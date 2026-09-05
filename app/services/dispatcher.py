"""发送调度器。

一个常驻后台线程，每隔几秒扫一次待发队列，做四件事：
    1. 时间窗守门：不在 09:00-20:00 内一律不发，队列自动顺延到次日
    2. 日限额守门：按活动 daily_limit 控制节奏，避免一天打完所有额度
    3. 批量提交：同一模板的消息合并成一次变量短信调用，几百条一个请求
    4. 失败分流：可重试错误退回队列，不可重试直接置失败并记录原因

不用 Celery 这类重型队列，是因为短信发送本身就是低频批量作业，
数据库队列 + 单线程消费足够，运维成本低得多，且天然可审计。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, or_, select

from app.config import settings
from app.db import session_scope
from app.models import Campaign, Merchant, SmsMessage, SmsTemplate, utcnow
from app.services.campaign import prepare_campaign, to_variable_template
from app.services.compliance import check_sendable, in_send_window, next_window_start
from app.services.sms_chuanglan import ChuanglanClient, build_content

logger = logging.getLogger(__name__)

MAX_RETRY = 3
TICK_SECONDS = 5
# 进程崩溃会让消息卡在 sending，超过这个时长就回收重发
LEASE_TIMEOUT_MINUTES = 10


class Dispatcher:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_tick_at = None
        self.last_error: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="sms-dispatcher", daemon=True)
        self._thread.start()
        logger.info("短信发送调度器已启动")

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        follow_up_checked_at = 0.0
        while not self._stop.is_set():
            try:
                self.tick()
                self.last_tick_at = utcnow()
                self.last_error = None
                # 跟进轮次一小时评估一次即可
                if time.time() - follow_up_checked_at > 3600:
                    schedule_follow_ups()
                    follow_up_checked_at = time.time()
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                logger.exception("调度器执行异常")
            self._stop.wait(TICK_SECONDS)

    def tick(self) -> int:
        reclaim_stale_leases()
        if not in_send_window():
            _postpone_pending()
            return 0

        with session_scope() as db:
            campaign_ids = db.scalars(
                select(Campaign.id).where(Campaign.status == "running")
            ).all()

        sent = 0
        for campaign_id in campaign_ids:
            sent += dispatch_campaign(campaign_id)
        return sent


def reclaim_stale_leases() -> int:
    """回收僵死的租约。

    进程被 kill 或机器重启时，消息会永远停在 sending 状态。
    超时后退回 pending 重新排队 —— 宁可极小概率重发一条，
    也不能让整批消息静默卡死。
    """
    cutoff = utcnow() - timedelta(minutes=LEASE_TIMEOUT_MINUTES)
    with session_scope() as db:
        return (
            db.query(SmsMessage)
            .filter(
                SmsMessage.status == "sending",
                or_(SmsMessage.leased_at.is_(None), SmsMessage.leased_at <= cutoff),
            )
            .update(
                {SmsMessage.status: "pending", SmsMessage.lease: None},
                synchronize_session=False,
            )
        )


def _postpone_pending() -> None:
    """非发送时段：把已到期的待发消息顺延到下一个窗口开始。"""
    now = utcnow()
    target = next_window_start(now)
    if target <= now:
        return
    with session_scope() as db:
        db.query(SmsMessage).filter(
            SmsMessage.status == "pending",
            SmsMessage.scheduled_at <= now,
        ).update({SmsMessage.scheduled_at: target}, synchronize_session=False)


def _today_sent_count(db, campaign_id: int) -> int:
    start_of_day = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.scalar(
            select(func.count(SmsMessage.id)).where(
                SmsMessage.campaign_id == campaign_id,
                SmsMessage.status.in_(("sent", "delivered")),
                SmsMessage.sent_at >= start_of_day,
            )
        )
        or 0
    )


def dispatch_campaign(campaign_id: int, batch_limit: int | None = None) -> int:
    """发送一个活动的一批消息，返回本次提交成功的条数。"""
    batch_limit = batch_limit or settings.sms_batch_size
    now = utcnow()

    with session_scope() as db:
        campaign = db.get(Campaign, campaign_id)
        if campaign is None or campaign.status != "running":
            return 0

        remaining_today = campaign.daily_limit - _today_sent_count(db, campaign_id)
        if remaining_today <= 0:
            return 0

        candidate_ids = (
            select(SmsMessage.id)
            .where(
                SmsMessage.campaign_id == campaign_id,
                SmsMessage.status == "pending",
                SmsMessage.scheduled_at <= now,
            )
            .order_by(SmsMessage.id.asc())
            .limit(min(batch_limit, remaining_today))
            .scalar_subquery()
        )

        # 用租约原子抢占：WHERE 里带 status == pending，
        # 并发进程只有一个能命中，另一个 update 影响 0 行，天然不会重复发送
        lease = uuid4().hex
        db.query(SmsMessage).filter(
            SmsMessage.id.in_(candidate_ids), SmsMessage.status == "pending"
        ).update(
            {SmsMessage.status: "sending", SmsMessage.lease: lease, SmsMessage.leased_at: now},
            synchronize_session=False,
        )
        db.flush()

        messages = db.scalars(
            select(SmsMessage).where(SmsMessage.lease == lease).order_by(SmsMessage.id.asc())
        ).all()

        if not messages:
            pending_left = db.scalar(
                select(func.count(SmsMessage.id)).where(
                    SmsMessage.campaign_id == campaign_id,
                    SmsMessage.status.in_(("pending", "sending")),
                )
            )
            if not pending_left:
                campaign.status = "finished"
                campaign.finished_at = now
                logger.info("活动 %s 队列已清空，自动结束", campaign_id)
            return 0

        groups: dict[int | None, list[SmsMessage]] = defaultdict(list)
        for message in messages:
            groups[message.template_id].append(message)

        templates = {
            t.id: t
            for t in db.scalars(
                select(SmsTemplate).where(SmsTemplate.id.in_([k for k in groups if k]))
            ).all()
        }
        payloads = []
        for template_id, group in groups.items():
            template = templates.get(template_id)
            if template is not None:
                raw = build_content(template.content, template.sign)
                variable_template, _ = to_variable_template(raw)
            else:
                variable_template = None
            payloads.append(
                (
                    variable_template,
                    [
                        {
                            "id": m.id,
                            "phone": m.phone,
                            "content": m.content,
                            "vars": list(m.vars or []),
                        }
                        for m in group
                    ],
                )
            )

    succeeded = 0
    with ChuanglanClient() as client:
        for variable_template, items in payloads:
            succeeded += _send_group(client, variable_template, items)
    return succeeded


def _send_group(client: ChuanglanClient, variable_template: str | None, items: list[dict]) -> int:
    """优先走变量短信，一次调用发一整组个性化内容。"""
    if not items:
        return 0

    use_variable = bool(variable_template) and all(item["vars"] for item in items)
    if use_variable:
        rows = [(item["phone"], item["vars"]) for item in items]
        result = client.send_variable(variable_template, rows)
    elif len(items) == 1:
        result = client.send([items[0]["phone"]], items[0]["content"])
    else:
        # 内容不一致又没有变量信息时只能逐条发，慢但不会串内容
        total = 0
        for item in items:
            total += _send_group(client, None, [item])
        return total

    _apply_result(items, result)
    return len(items) if result.ok else 0


def _apply_result(items: list[dict], result) -> None:
    now = utcnow()
    ids = [item["id"] for item in items]
    with session_scope() as db:
        messages = db.scalars(select(SmsMessage).where(SmsMessage.id.in_(ids))).all()
        for message in messages:
            message.lease = None
            if result.ok:
                message.status = "sent"
                message.sent_at = now
                message.provider_msg_id = result.msg_id
                message.provider_code = result.code
                message.error = None
                _mark_merchant_touched(db, message, now)
            else:
                message.provider_code = result.code
                message.error = result.error
                if result.retriable and message.retry_count < MAX_RETRY:
                    message.retry_count += 1
                    message.status = "pending"
                    # 指数退避，避免流速超限时反复撞墙
                    message.scheduled_at = now + timedelta(minutes=2**message.retry_count)
                else:
                    message.status = "failed"


def _mark_merchant_touched(db, message: SmsMessage, now) -> None:
    if not message.merchant_id:
        return
    merchant = db.get(Merchant, message.merchant_id)
    if merchant is None:
        return
    merchant.touch_count = (merchant.touch_count or 0) + 1
    merchant.last_touch_at = now
    if merchant.lifecycle == "new":
        merchant.lifecycle = "reached"


# ---------------------------------------------------------------------------
# 多轮跟进
# ---------------------------------------------------------------------------


def schedule_follow_ups() -> int:
    """按活动配置生成下一轮触达。

    节奏参考美团 BD 的三次触达法：首触讲价值，二触给具体让利，三触制造紧迫感。
    只对「已送达但没点击没回复」的商户做跟进，已经有互动的交给 BD 跟人，
    不再用短信打扰。
    """
    created = 0
    now = utcnow()
    with session_scope() as db:
        campaigns = db.scalars(
            select(Campaign).where(Campaign.status.in_(("running", "ready")))
        ).all()

        for campaign in campaigns:
            config = campaign.follow_up or {}
            if not config.get("enabled"):
                continue
            for index, rule in enumerate(config.get("rounds") or [], start=2):
                after_days = int(rule.get("after_days", 3))
                template_id = rule.get("template_id")
                if not template_id:
                    continue

                cutoff = now - timedelta(days=after_days)
                candidates = db.scalars(
                    select(SmsMessage).where(
                        SmsMessage.campaign_id == campaign.id,
                        SmsMessage.round_no == index - 1,
                        SmsMessage.status.in_(("sent", "delivered")),
                        SmsMessage.sent_at <= cutoff,
                        SmsMessage.clicked_at.is_(None),
                    )
                ).all()

                already = set(
                    db.scalars(
                        select(SmsMessage.phone).where(
                            SmsMessage.campaign_id == campaign.id,
                            SmsMessage.round_no == index,
                        )
                    ).all()
                )

                for previous in candidates:
                    if previous.phone in already:
                        continue
                    ok, reason = check_sendable(db, previous.phone, now=now)
                    if not ok:
                        continue
                    merchant = (
                        db.get(Merchant, previous.merchant_id)
                        if previous.merchant_id
                        else None
                    )
                    if merchant and merchant.lifecycle in ("intent", "registered", "activated"):
                        continue
                    message = _build_follow_up_message(
                        db, campaign, merchant, previous.phone, template_id, index, now
                    )
                    if message is not None:
                        already.add(previous.phone)
                        created += 1
    if created:
        logger.info("生成跟进短信 %s 条", created)
    return created


def _build_follow_up_message(
    db, campaign: Campaign, merchant, phone: str, template_id: int, round_no: int, now
) -> SmsMessage | None:
    from app.services import shortlink
    from app.services.render import build_context, render
    from app.services.sms_chuanglan import billing_count

    template = db.get(SmsTemplate, template_id)
    if template is None or template.status != "enabled":
        return None

    message = SmsMessage(
        campaign_id=campaign.id,
        merchant_id=merchant.id if merchant else None,
        template_id=template.id,
        variant=f"R{round_no}",
        phone=phone,
        content="",
        status="pending",
        round_no=round_no,
        scheduled_at=now,
    )
    db.add(message)
    db.flush()

    link = None
    target_url = template.landing_url or campaign.landing_url
    if template.with_link and target_url:
        short = shortlink.create_link(
            db,
            target_url=target_url,
            merchant_id=message.merchant_id,
            campaign_id=campaign.id,
            message_id=message.id,
        )
        message.link_code = short.code
        link = shortlink.link_url(short.code)

    raw = build_content(template.content, template.sign)
    context = build_context(merchant, link=link, now=now)
    _, ordered = to_variable_template(raw)
    message.content = render(raw, context)
    message.vars = [str(context.get(name, f"{{{name}}}")) for name in ordered]
    message.fee_count = billing_count(message.content)
    return message


dispatcher = Dispatcher()


def ensure_prepared(db, campaign: Campaign) -> dict:
    """启动活动前若队列为空则自动圈人。"""
    pending = db.scalar(
        select(func.count(SmsMessage.id)).where(
            SmsMessage.campaign_id == campaign.id, SmsMessage.status == "pending"
        )
    )
    if pending:
        return {"created": 0, "pending": pending}
    return prepare_campaign(db, campaign)
