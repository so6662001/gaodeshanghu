"""合规与频控中心。

营销短信是强监管业务，做增长可以激进，但这几条不能碰：
    1. 退订即止 —— 用户回复 T/TD/退订后必须立刻停发，且永久停发
    2. 时间窗   —— 不得在夜间打扰，默认只在 09:00-20:00 发送
    3. 频次控制 —— 同一号码 N 天内不重复触达，30 天内不超过 M 次
    4. 必带退订 —— 每条营销短信结尾必须给退订方式
    5. 号码校验 —— 只发手机号，座机/服务号一律转电销工单

投诉率一旦超标，运营商会直接封通道，整个增长链路瘫痪。
所以宁可少发，也不能越线。所有发送前都必须经过 check_sendable。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.config import settings
from app.models import Blacklist, SmsMessage, utcnow
from app.utils import phone as phone_utils

CN_TZ = ZoneInfo("Asia/Shanghai")

SKIP_REASONS = {
    "blacklist": "已退订/黑名单",
    "not_mobile": "非手机号，无法短信触达",
    "too_frequent": "触达间隔不足，频控拦截",
    "monthly_cap": "本月触达次数已达上限",
    "duplicate_in_campaign": "同一活动内号码重复",
}


def to_cn(dt: datetime) -> datetime:
    """库里存的是 UTC naive，展示与时间窗判断都要转成北京时间。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(CN_TZ)


def in_send_window(now: datetime | None = None) -> bool:
    now_cn = to_cn(now or utcnow())
    start, end = settings.send_window
    return start <= now_cn.time() <= end


def next_window_start(now: datetime | None = None) -> datetime:
    """返回下一个可发送时刻（UTC naive），用于把任务顺延到明早。"""
    now = now or utcnow()
    now_cn = to_cn(now)
    start, end = settings.send_window

    if now_cn.time() < start:
        target = now_cn.replace(
            hour=start.hour, minute=start.minute, second=0, microsecond=0
        )
    elif now_cn.time() > end:
        target = (now_cn + timedelta(days=1)).replace(
            hour=start.hour, minute=start.minute, second=0, microsecond=0
        )
    else:
        return now
    return target.astimezone(timezone.utc).replace(tzinfo=None)


def is_blacklisted(db, phone: str) -> bool:
    return db.scalar(select(Blacklist.id).where(Blacklist.phone == phone)) is not None


def add_to_blacklist(
    db, phone: str, reason: str = "unsubscribe", source: str | None = None,
    operator: str | None = None,
) -> Blacklist | None:
    phone = phone_utils.normalize(phone)
    if not phone:
        return None
    existing = db.scalar(select(Blacklist).where(Blacklist.phone == phone))
    if existing:
        return existing
    record = Blacklist(phone=phone, reason=reason, source=source, operator=operator)
    db.add(record)
    # 立即 flush：同一批回调里可能有多条相同号码的退订，
    # 不 flush 的话后续查询看不到未提交的记录，会撞唯一索引导致整批失败
    db.flush()
    return record


def check_sendable(
    db,
    phone: str,
    now: datetime | None = None,
    min_interval_days: int | None = None,
    max_per_month: int | None = None,
) -> tuple[bool, str | None]:
    """发送前置校验。返回 (是否可发, 拦截原因)。"""
    now = now or utcnow()
    phone = phone_utils.normalize(phone)

    if not phone_utils.is_mobile(phone):
        return False, "not_mobile"
    if is_blacklisted(db, phone):
        return False, "blacklist"

    interval = (
        settings.sms_min_interval_days if min_interval_days is None else min_interval_days
    )
    if interval > 0:
        since = now - timedelta(days=interval)
        recent = db.scalar(
            select(SmsMessage.id)
            .where(
                SmsMessage.phone == phone,
                SmsMessage.status.in_(("sent", "delivered")),
                SmsMessage.sent_at >= since,
            )
            .limit(1)
        )
        if recent is not None:
            return False, "too_frequent"

    cap = settings.sms_max_touch_per_month if max_per_month is None else max_per_month
    if cap > 0:
        month_ago = now - timedelta(days=30)
        count = db.scalar(
            select(func.count(SmsMessage.id)).where(
                SmsMessage.phone == phone,
                SmsMessage.status.in_(("sent", "delivered")),
                SmsMessage.sent_at >= month_ago,
            )
        )
        if (count or 0) >= cap:
            return False, "monthly_cap"

    return True, None


def classify_reply(content: str) -> str:
    """判断上行回复意图，退订必须优先命中。"""
    text = (content or "").strip().upper()
    if not text:
        return "unknown"

    for keyword in settings.unsubscribe_keywords:
        if text == keyword or text.startswith(keyword) or keyword in text:
            return "unsubscribe"

    if any(word in text for word in ("投诉", "举报", "骚扰", "报警")):
        return "complaint"
    if any(
        word in text
        for word in ("怎么", "多少", "价格", "报价", "有兴趣", "咨询", "了解", "合作", "联系", "要", "想")
    ):
        return "interested"
    return "unknown"
