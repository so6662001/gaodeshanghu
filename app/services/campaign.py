"""活动受众圈选与目标生成。

「发给谁」比「发什么」更决定结果。这里做三件事：
    1. 按城市/品类/评级/生命周期等条件圈人，天然只圈可短信触达的手机号
    2. A/B 分流：同一批人按权重分到不同文案，用数据决定主推哪版
    3. 发送前预检：黑名单、频控、重复号码在生成阶段就打成 skipped，
       不进发送队列，避免运行时才发现浪费额度
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from sqlalchemy import Select, or_, select

from app.models import Campaign, Merchant, MerchantPhone, SmsMessage, SmsTemplate, utcnow
from app.services import shortlink
from app.services.compliance import check_sendable
from app.services.render import VARIABLE_PATTERN, build_context, render
from app.services.sms_chuanglan import billing_count, build_content

logger = logging.getLogger(__name__)


def build_audience_query(audience: dict[str, Any]) -> Select:
    """把前端传的筛选条件翻译成查询。只返回有有效手机号的商户。"""
    query = (
        select(Merchant)
        .join(MerchantPhone, MerchantPhone.merchant_id == Merchant.id)
        .where(
            MerchantPhone.phone_type == "mobile",
            MerchantPhone.is_valid.is_(True),
        )
        .distinct()
    )

    audience = audience or {}
    if city := audience.get("city"):
        query = query.where(Merchant.city == city)
    if district := audience.get("district"):
        query = query.where(Merchant.district == district)
    if business_area := audience.get("business_area"):
        query = query.where(Merchant.business_area == business_area)
    if category := audience.get("category"):
        query = query.where(Merchant.category == category)
    if grades := audience.get("grades"):
        query = query.where(Merchant.grade.in_(grades))
    if lifecycles := audience.get("lifecycles"):
        query = query.where(Merchant.lifecycle.in_(lifecycles))
    if (min_score := audience.get("min_score")) is not None:
        query = query.where(Merchant.score >= int(min_score))
    if (max_score := audience.get("max_score")) is not None:
        query = query.where(Merchant.score <= int(max_score))
    if keyword := audience.get("keyword"):
        like = f"%{keyword}%"
        query = query.where(
            or_(Merchant.name.like(like), Merchant.type_name.like(like))
        )
    if owner := audience.get("owner"):
        query = query.where(Merchant.owner == owner)
    if audience.get("never_touched"):
        query = query.where(
            or_(Merchant.touch_count == 0, Merchant.last_touch_at.is_(None))
        )
    if exclude_ids := audience.get("exclude_merchant_ids"):
        query = query.where(Merchant.id.notin_(exclude_ids))
    if merchant_ids := audience.get("merchant_ids"):
        query = query.where(Merchant.id.in_(merchant_ids))

    # 高分优先，预算有限时先打穿最有价值的那批
    return query.order_by(Merchant.score.desc(), Merchant.id.asc())


def count_audience(db, audience: dict[str, Any]) -> int:
    return len(db.scalars(build_audience_query(audience)).all())


def pick_variant(variants: list[dict], merchant_id: int) -> dict:
    """按权重稳定分流：同一商户永远落在同一版文案，避免二次触达文案跳变。"""
    usable = [v for v in variants if v.get("template_id")]
    if not usable:
        return {}
    total = sum(max(int(v.get("weight", 1)), 0) for v in usable) or len(usable)
    digest = hashlib.md5(str(merchant_id).encode()).hexdigest()
    point = int(digest[:8], 16) % total
    cursor = 0
    for variant in usable:
        cursor += max(int(variant.get("weight", 1)), 0)
        if point < cursor:
            return variant
    return usable[-1]


def to_variable_template(content: str) -> tuple[str, list[str]]:
    """把中文占位模板转成创蓝变量短信格式。

    「{商家名}老板您好，{链接}」-> ("{$var}老板您好，{$var}", ["商家名", "链接"])
    这样同一批 500 条个性化短信只需要一次 API 调用。
    返回的占位符列表按出现顺序排列，同名占位符出现多次会重复列出，
    与 params 中的取值顺序严格对应。
    """
    ordered = VARIABLE_PATTERN.findall(content or "")
    variable_content = VARIABLE_PATTERN.sub("{$var}", content or "")
    return variable_content, ordered


def prepare_campaign(
    db, campaign: Campaign, round_no: int = 1, limit: int | None = None
) -> dict[str, Any]:
    """生成待发队列。返回各类统计，运营可据此决定是否启动。"""
    audience = dict(campaign.audience or {})
    merchants = db.scalars(build_audience_query(audience)).all()
    if limit:
        merchants = merchants[:limit]

    templates: dict[int, SmsTemplate] = {}
    for variant in campaign.variants or []:
        template_id = variant.get("template_id")
        if template_id and template_id not in templates:
            template = db.get(SmsTemplate, template_id)
            if template:
                templates[template_id] = template

    if not templates:
        return {"error": "活动未绑定任何可用模板", "created": 0}

    # 同一活动内已存在的号码，避免重复排队
    existing_phones = set(
        db.scalars(
            select(SmsMessage.phone).where(SmsMessage.campaign_id == campaign.id)
        ).all()
    )

    stats = {
        "audience": len(merchants),
        "created": 0,
        "skipped": 0,
        "skip_detail": {},
        "billing_count": 0,
    }
    now = utcnow()
    landing_base = campaign.landing_url

    for merchant in merchants:
        phone = merchant.primary_phone
        if not phone:
            _bump(stats, "no_phone")
            continue
        if phone in existing_phones:
            _bump(stats, "duplicate_in_campaign")
            continue

        ok, reason = check_sendable(db, phone, now=now)
        if not ok:
            existing_phones.add(phone)
            db.add(
                SmsMessage(
                    campaign_id=campaign.id,
                    merchant_id=merchant.id,
                    phone=phone,
                    content="",
                    status="skipped",
                    skip_reason=reason,
                    round_no=round_no,
                )
            )
            _bump(stats, reason or "unknown")
            continue

        variant = pick_variant(campaign.variants or [], merchant.id)
        template = templates.get(variant.get("template_id"))
        if template is None:
            _bump(stats, "no_template")
            continue

        message = SmsMessage(
            campaign_id=campaign.id,
            merchant_id=merchant.id,
            template_id=template.id,
            variant=variant.get("label") or f"T{template.id}",
            phone=phone,
            content="",
            status="pending",
            round_no=round_no,
            scheduled_at=now,
        )
        db.add(message)
        db.flush()

        link = None
        target_url = template.landing_url or landing_base
        if template.with_link and target_url:
            short = shortlink.create_link(
                db,
                target_url=target_url,
                merchant_id=merchant.id,
                campaign_id=campaign.id,
                message_id=message.id,
            )
            message.link_code = short.code
            link = shortlink.link_url(short.code)

        raw_content = build_content(template.content, template.sign)
        context = build_context(merchant, link=link, now=now)
        _, ordered_names = to_variable_template(raw_content)
        message.content = render(raw_content, context)
        message.vars = [str(context.get(name, f"{{{name}}}")) for name in ordered_names]
        message.fee_count = billing_count(message.content)

        existing_phones.add(phone)
        stats["created"] += 1
        stats["billing_count"] += message.fee_count

    campaign.total_targets = (campaign.total_targets or 0) + stats["created"]
    if campaign.status == "draft":
        campaign.status = "ready"
    return stats


def _bump(stats: dict[str, Any], reason: str) -> None:
    stats["skipped"] += 1
    stats["skip_detail"][reason] = stats["skip_detail"].get(reason, 0) + 1
