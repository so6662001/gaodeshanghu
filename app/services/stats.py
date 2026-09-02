"""运营数据统计。

只看「发了多少条」是没有意义的，必须看到完整漏斗：
    采集商户 -> 可触达 -> 已发送 -> 送达 -> 点击 -> 注册 -> 首单
每一层的衰减率告诉你问题出在哪：
    可触达率低  -> 采集策略要换，或该品类高德不挂手机号，得靠 BD 扫街
    送达率低    -> 号码质量差或通道有问题，检查运营商分布
    点击率低    -> 文案没打中痛点，换 A/B 版本
    注册率低    -> 落地页或承接流程有问题，不是短信的锅
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import case, func, select

from app.models import (
    Blacklist,
    Campaign,
    ClickEvent,
    ConversionEvent,
    Merchant,
    MerchantPhone,
    SmsMessage,
    SmsReply,
    utcnow,
)


def _count(db, stmt) -> int:
    return db.scalar(stmt) or 0


def overview(db) -> dict[str, Any]:
    now = utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    total_merchants = _count(db, select(func.count(Merchant.id)))
    reachable = _count(
        db,
        select(func.count(func.distinct(MerchantPhone.merchant_id))).where(
            MerchantPhone.phone_type == "mobile", MerchantPhone.is_valid.is_(True)
        ),
    )
    today_collected = _count(
        db, select(func.count(Merchant.id)).where(Merchant.created_at >= today)
    )
    sent = _count(
        db,
        select(func.count(SmsMessage.id)).where(
            SmsMessage.status.in_(("sent", "delivered"))
        ),
    )
    today_sent = _count(
        db,
        select(func.count(SmsMessage.id)).where(
            SmsMessage.status.in_(("sent", "delivered")), SmsMessage.sent_at >= today
        ),
    )
    delivered = _count(
        db, select(func.count(SmsMessage.id)).where(SmsMessage.status == "delivered")
    )
    failed = _count(
        db, select(func.count(SmsMessage.id)).where(SmsMessage.status == "failed")
    )
    pending = _count(
        db, select(func.count(SmsMessage.id)).where(SmsMessage.status == "pending")
    )
    clicked = _count(
        db, select(func.count(SmsMessage.id)).where(SmsMessage.clicked_at.isnot(None))
    )
    replies = _count(db, select(func.count(SmsReply.id)))
    interested = _count(
        db, select(func.count(SmsReply.id)).where(SmsReply.intent == "interested")
    )
    registered = _count(
        db,
        select(func.count(func.distinct(ConversionEvent.merchant_id))).where(
            ConversionEvent.event_type == "register"
        ),
    )
    ordered = _count(
        db,
        select(func.count(func.distinct(ConversionEvent.merchant_id))).where(
            ConversionEvent.event_type == "first_order"
        ),
    )
    gmv = db.scalar(select(func.sum(ConversionEvent.amount))) or 0.0
    billing = db.scalar(
        select(func.sum(SmsMessage.fee_count)).where(
            SmsMessage.status.in_(("sent", "delivered"))
        )
    ) or 0

    return {
        "merchants": total_merchants,
        "reachable": reachable,
        "reachable_rate": _rate(reachable, total_merchants),
        "today_collected": today_collected,
        "sent": sent,
        "today_sent": today_sent,
        "pending": pending,
        "delivered": delivered,
        "failed": failed,
        "delivery_rate": _rate(delivered, sent),
        "clicked": clicked,
        "click_rate": _rate(clicked, sent),
        "replies": replies,
        "interested": interested,
        "registered": registered,
        "ordered": ordered,
        "register_rate": _rate(registered, sent),
        "gmv": round(float(gmv), 2),
        "billing_count": int(billing),
        "blacklist": _count(db, select(func.count(Blacklist.id))),
        "running_campaigns": _count(
            db, select(func.count(Campaign.id)).where(Campaign.status == "running")
        ),
    }


def _rate(numerator: int, denominator: int) -> float:
    if not denominator:
        return 0.0
    return round(numerator / denominator * 100, 2)


def funnel(db, campaign_id: int | None = None) -> list[dict[str, Any]]:
    conditions = []
    if campaign_id:
        conditions.append(SmsMessage.campaign_id == campaign_id)

    def sms_count(*extra) -> int:
        stmt = select(func.count(SmsMessage.id))
        for cond in [*conditions, *extra]:
            stmt = stmt.where(cond)
        return _count(db, stmt)

    if campaign_id:
        merchants = sms_count()
        reachable = sms_count(SmsMessage.status != "skipped")
    else:
        merchants = _count(db, select(func.count(Merchant.id)))
        reachable = _count(
            db,
            select(func.count(func.distinct(MerchantPhone.merchant_id))).where(
                MerchantPhone.phone_type == "mobile", MerchantPhone.is_valid.is_(True)
            ),
        )

    sent = sms_count(SmsMessage.status.in_(("sent", "delivered")))
    delivered = sms_count(SmsMessage.status == "delivered")
    clicked = sms_count(SmsMessage.clicked_at.isnot(None))

    conv_stmt = select(
        ConversionEvent.event_type, func.count(func.distinct(ConversionEvent.merchant_id))
    ).group_by(ConversionEvent.event_type)
    if campaign_id:
        conv_stmt = conv_stmt.where(ConversionEvent.campaign_id == campaign_id)
    conversions = dict(db.execute(conv_stmt).all())

    stages = [
        ("采集商户", merchants),
        ("可短信触达", reachable),
        ("已发送", sent),
        ("已送达", delivered if delivered else sent),
        ("点击落地页", clicked),
        ("注册开户", conversions.get("register", 0)),
        ("完成首单", conversions.get("first_order", 0)),
    ]

    result = []
    previous = None
    for name, value in stages:
        result.append(
            {
                "stage": name,
                "value": value,
                "rate_of_top": _rate(value, stages[0][1]),
                "rate_of_prev": _rate(value, previous) if previous else 100.0,
            }
        )
        previous = value
    return result


def campaign_report(db, campaign_id: int) -> dict[str, Any]:
    """A/B 对比：哪版文案值得放量，看点击率和意向回复率。"""
    rows = db.execute(
        select(
            SmsMessage.variant,
            func.count(SmsMessage.id),
            func.sum(case((SmsMessage.status.in_(("sent", "delivered")), 1), else_=0)),
            func.sum(case((SmsMessage.status == "delivered", 1), else_=0)),
            func.sum(case((SmsMessage.status == "failed", 1), else_=0)),
            func.sum(case((SmsMessage.clicked_at.isnot(None), 1), else_=0)),
            func.sum(SmsMessage.fee_count),
        )
        .where(SmsMessage.campaign_id == campaign_id)
        .group_by(SmsMessage.variant)
    ).all()

    variants = []
    for variant, total, sent, delivered, failed, clicked, fee in rows:
        sent = int(sent or 0)
        variants.append(
            {
                "variant": variant or "未分组",
                "total": int(total or 0),
                "sent": sent,
                "delivered": int(delivered or 0),
                "failed": int(failed or 0),
                "clicked": int(clicked or 0),
                "click_rate": _rate(int(clicked or 0), sent),
                "billing_count": int(fee or 0),
            }
        )
    variants.sort(key=lambda x: x["click_rate"], reverse=True)

    skip_rows = db.execute(
        select(SmsMessage.skip_reason, func.count(SmsMessage.id))
        .where(SmsMessage.campaign_id == campaign_id, SmsMessage.status == "skipped")
        .group_by(SmsMessage.skip_reason)
    ).all()

    return {
        "campaign_id": campaign_id,
        "variants": variants,
        "skipped": {reason or "unknown": count for reason, count in skip_rows},
        "funnel": funnel(db, campaign_id),
        "winner": variants[0]["variant"] if variants and variants[0]["sent"] >= 100 else None,
    }


def trend(db, days: int = 14) -> list[dict[str, Any]]:
    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=days - 1
    )

    def daily(column, *conditions) -> dict[str, int]:
        stmt = select(func.date(column), func.count()).where(column >= start)
        for cond in conditions:
            stmt = stmt.where(cond)
        return {str(day): count for day, count in db.execute(stmt.group_by(func.date(column))).all()}

    collected = daily(Merchant.created_at)
    sent = daily(SmsMessage.sent_at, SmsMessage.status.in_(("sent", "delivered")))
    clicks = daily(ClickEvent.created_at)

    result = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).date().isoformat()
        result.append(
            {
                "date": day,
                "collected": collected.get(day, 0),
                "sent": sent.get(day, 0),
                "clicked": clicks.get(day, 0),
            }
        )
    return result


def distribution(db, dimension: str = "city", limit: int = 12) -> list[dict[str, Any]]:
    column = {
        "city": Merchant.city,
        "category": Merchant.category,
        "grade": Merchant.grade,
        "lifecycle": Merchant.lifecycle,
        "district": Merchant.district,
    }.get(dimension, Merchant.city)

    rows = db.execute(
        select(column, func.count(Merchant.id))
        .where(column.isnot(None), column != "")
        .group_by(column)
        .order_by(func.count(Merchant.id).desc())
        .limit(limit)
    ).all()
    return [{"name": name, "value": count} for name, count in rows]
