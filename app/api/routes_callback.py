"""外部回调：创蓝状态报告、上行回复，以及货袋子主站的转化回传。

这三个回调接口不走 Token 鉴权（对方系统无法带我们的头），
生产环境请在网关层用 IP 白名单保护，创蓝控制台可以查到推送源 IP。
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ConversionEvent, Merchant, MerchantPhone, SmsMessage, SmsReply, utcnow
from app.schemas import ConversionIn
from app.services.compliance import add_to_blacklist, classify_reply
from app.utils import phone as phone_utils

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/callback", tags=["回调"])

DELIVERED_CODES = {"DELIVRD", "0", "100"}


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


@router.post("/sms/report")
async def sms_report(request: Request, db: Session = Depends(get_db)) -> dict:
    """创蓝状态报告推送。

    送达率是短信质量的核心指标，低于 90% 就要排查号码来源与通道。
    空号会在这里暴露出来，直接标记号码无效，避免持续浪费额度。
    """
    payload = await request.json()
    reports = payload if isinstance(payload, list) else payload.get("reports") or [payload]

    updated = 0
    for item in reports:
        if not isinstance(item, dict):
            continue
        phone = phone_utils.normalize(str(item.get("mobile") or item.get("phone") or ""))
        msg_id = str(item.get("msgId") or item.get("msgid") or "")
        status_code = str(item.get("statusCode") or item.get("status") or "").upper()
        if not phone:
            continue

        stmt = select(SmsMessage).where(SmsMessage.phone == phone)
        if msg_id:
            stmt = stmt.where(SmsMessage.provider_msg_id == msg_id)
        message = db.scalars(stmt.order_by(SmsMessage.id.desc()).limit(1)).first()
        if message is None:
            continue

        if status_code in DELIVERED_CODES:
            message.status = "delivered"
            message.delivered_at = _parse_time(item.get("reportTime")) or utcnow()
        else:
            message.status = "failed"
            message.error = f"运营商回执 {status_code}"
            # 空号/停机类回执直接把号码作废，后续活动不再圈进来
            if status_code in {"UNDELIV", "MBBLK", "REJECTD", "ABSENT", "UNKNOWN"}:
                db.query(MerchantPhone).filter(MerchantPhone.phone == phone).update(
                    {MerchantPhone.is_valid: False}, synchronize_session=False
                )
        message.provider_code = status_code
        updated += 1

    db.commit()
    logger.info("处理状态报告 %s 条", updated)
    return {"result": "success", "updated": updated}


@router.post("/sms/reply")
async def sms_reply(request: Request, db: Session = Depends(get_db)) -> dict:
    """上行回复推送。

    退订必须立刻生效，这是合规红线；
    有意向的回复要马上变成 BD 线索，短信触达真正的价值就在这批人身上。
    """
    payload = await request.json()
    replies = payload if isinstance(payload, list) else payload.get("replies") or [payload]

    handled = 0
    unsubscribed: set[str] = set()
    for item in replies:
        if not isinstance(item, dict):
            continue
        phone = phone_utils.normalize(str(item.get("mobile") or item.get("phone") or ""))
        content = str(item.get("msg") or item.get("content") or "")
        if not phone:
            continue

        intent = classify_reply(content)
        merchant_id = db.scalar(
            select(MerchantPhone.merchant_id).where(MerchantPhone.phone == phone).limit(1)
        )
        last_message = db.scalars(
            select(SmsMessage)
            .where(SmsMessage.phone == phone)
            .order_by(SmsMessage.id.desc())
            .limit(1)
        ).first()

        db.add(
            SmsReply(
                phone=phone,
                content=content,
                intent=intent,
                merchant_id=merchant_id,
                campaign_id=last_message.campaign_id if last_message else None,
                received_at=_parse_time(item.get("replyTime") or item.get("time")) or utcnow(),
            )
        )

        if intent == "unsubscribe":
            add_to_blacklist(db, phone, reason="unsubscribe", source="sms_reply")
            unsubscribed.add(phone)
        elif intent == "complaint":
            add_to_blacklist(db, phone, reason="complaint", source="sms_reply")

        if merchant_id:
            merchant = db.get(Merchant, merchant_id)
            if merchant:
                merchant.last_reply_at = utcnow()
                if intent == "interested":
                    merchant.lifecycle = "intent"
                elif intent in ("unsubscribe", "complaint"):
                    merchant.lifecycle = "invalid"
                elif merchant.lifecycle in ("new", "reached"):
                    merchant.lifecycle = "engaged"
        handled += 1

    db.commit()
    logger.info("处理上行回复 %s 条，退订号码 %s 个", handled, len(unsubscribed))
    return {"result": "success", "handled": handled, "unsubscribed": len(unsubscribed)}


@router.post("/conversion")
def conversion(payload: ConversionIn, db: Session = Depends(get_db)) -> dict:
    """货袋子主站回传注册/开店/首单，闭合归因漏斗。"""
    merchant_id = payload.merchant_id
    phone = phone_utils.normalize(payload.phone or "")
    if merchant_id is None and phone:
        merchant_id = db.scalar(
            select(MerchantPhone.merchant_id).where(MerchantPhone.phone == phone).limit(1)
        )

    campaign_id = None
    if phone:
        last_message = db.scalars(
            select(SmsMessage)
            .where(SmsMessage.phone == phone, SmsMessage.status.in_(("sent", "delivered")))
            .order_by(SmsMessage.id.desc())
            .limit(1)
        ).first()
        if last_message:
            campaign_id = last_message.campaign_id

    db.add(
        ConversionEvent(
            merchant_id=merchant_id,
            phone=phone or None,
            campaign_id=campaign_id,
            event_type=payload.event_type,
            amount=payload.amount,
            payload=payload.payload,
        )
    )

    if merchant_id:
        merchant = db.get(Merchant, merchant_id)
        if merchant:
            stage = {
                "register": "registered",
                "open_shop": "registered",
                "first_order": "activated",
                "repeat_order": "activated",
            }[payload.event_type]
            merchant.lifecycle = stage

    db.commit()
    return {"ok": True, "merchant_id": merchant_id, "campaign_id": campaign_id}
