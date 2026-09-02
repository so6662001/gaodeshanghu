"""短信发送、黑名单与通道运维接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_token
from app.config import settings
from app.db import get_db
from app.models import AuditLog, Blacklist, SmsMessage, SmsReply, utcnow
from app.schemas import BlacklistIn, PageOut, TestSendRequest
from app.services.compliance import (
    SKIP_REASONS,
    add_to_blacklist,
    in_send_window,
    next_window_start,
)
from app.services.dispatcher import dispatcher
from app.services.sms_chuanglan import ChuanglanClient, billing_count, build_content
from app.utils import phone as phone_utils

router = APIRouter(prefix="/api/sms", tags=["短信"], dependencies=[Depends(require_token)])


@router.get("/channel")
def channel_status() -> dict:
    """通道体检：账号是否配好、余额多少、是否演练模式、当前能不能发。"""
    with ChuanglanClient() as client:
        try:
            balance = client.balance()
        except Exception as exc:  # noqa: BLE001
            balance = {"code": "-1", "errorMsg": str(exc)}

    start, end = settings.send_window
    return {
        "configured": settings.sms_ready,
        "dry_run": settings.sms_dry_run,
        "sign": settings.sms_sign,
        "balance": balance,
        "send_window": f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}",
        "in_window": in_send_window(),
        "next_window_start": next_window_start(),
        "min_interval_days": settings.sms_min_interval_days,
        "max_touch_per_month": settings.sms_max_touch_per_month,
        "dispatcher_running": dispatcher.running,
        "dispatcher_last_tick": dispatcher.last_tick_at,
        "dispatcher_error": dispatcher.last_error,
    }


@router.post("/test-send")
def test_send(payload: TestSendRequest, db: Session = Depends(get_db)) -> dict:
    """发一条真实短信到自己手机，上线前必做。"""
    phone = phone_utils.normalize(payload.phone)
    if not phone_utils.is_mobile(phone):
        raise HTTPException(status_code=400, detail="请填写正确的手机号")

    content = build_content(payload.content, payload.sign)
    message = SmsMessage(
        phone=phone,
        content=content,
        status="sending",
        variant="test",
        fee_count=billing_count(content),
    )
    db.add(message)
    db.flush()

    with ChuanglanClient() as client:
        result = client.send([phone], content)

    if result.ok:
        message.status = "sent"
        message.sent_at = utcnow()
        message.provider_msg_id = result.msg_id
    else:
        message.status = "failed"
        message.error = result.error
    message.provider_code = result.code

    db.add(
        AuditLog(
            action="sms.test_send",
            target=phone_utils.mask(phone),
            detail={"code": result.code, "dry_run": result.dry_run},
        )
    )
    db.commit()

    return {
        "ok": result.ok,
        "dry_run": result.dry_run,
        "code": result.code,
        "error": result.error,
        "content": content,
        "billing_count": message.fee_count,
    }


@router.get("/messages", response_model=PageOut)
def list_messages(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    campaign_id: int | None = None,
    status: str | None = None,
    phone: str | None = None,
    unmask: bool = False,
    db: Session = Depends(get_db),
) -> PageOut:
    stmt = select(SmsMessage)
    count_stmt = select(func.count(SmsMessage.id))
    for condition in [
        SmsMessage.campaign_id == campaign_id if campaign_id else None,
        SmsMessage.status == status if status else None,
        SmsMessage.phone == phone_utils.normalize(phone) if phone else None,
    ]:
        if condition is not None:
            stmt = stmt.where(condition)
            count_stmt = count_stmt.where(condition)

    total = db.scalar(count_stmt) or 0
    messages = db.scalars(
        stmt.order_by(SmsMessage.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()

    return PageOut(
        total=total,
        page=page,
        page_size=page_size,
        items=[
            {
                "id": m.id,
                "campaign_id": m.campaign_id,
                "merchant_id": m.merchant_id,
                "phone": m.phone if unmask else phone_utils.mask(m.phone),
                "content": m.content,
                "status": m.status,
                "skip_reason": SKIP_REASONS.get(m.skip_reason or "", m.skip_reason),
                "variant": m.variant,
                "round_no": m.round_no,
                "fee_count": m.fee_count,
                "error": m.error,
                "sent_at": m.sent_at,
                "delivered_at": m.delivered_at,
                "clicked_at": m.clicked_at,
            }
            for m in messages
        ],
    )


@router.get("/replies", response_model=PageOut)
def list_replies(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    intent: str | None = None,
    db: Session = Depends(get_db),
) -> PageOut:
    stmt = select(SmsReply)
    count_stmt = select(func.count(SmsReply.id))
    if intent:
        stmt = stmt.where(SmsReply.intent == intent)
        count_stmt = count_stmt.where(SmsReply.intent == intent)

    total = db.scalar(count_stmt) or 0
    replies = db.scalars(
        stmt.order_by(SmsReply.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return PageOut(
        total=total,
        page=page,
        page_size=page_size,
        items=[
            {
                "id": r.id,
                "phone": phone_utils.mask(r.phone),
                "content": r.content,
                "intent": r.intent,
                "merchant_id": r.merchant_id,
                "handled": r.handled,
                "received_at": r.received_at,
            }
            for r in replies
        ],
    )


@router.post("/replies/{reply_id}/handle")
def handle_reply(reply_id: int, db: Session = Depends(get_db)) -> dict:
    reply = db.get(SmsReply, reply_id)
    if reply is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    reply.handled = True
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# 黑名单
# ---------------------------------------------------------------------------
@router.get("/blacklist", response_model=PageOut)
def list_blacklist(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> PageOut:
    total = db.scalar(select(func.count(Blacklist.id))) or 0
    rows = db.scalars(
        select(Blacklist)
        .order_by(Blacklist.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return PageOut(
        total=total,
        page=page,
        page_size=page_size,
        items=[
            {
                "id": b.id,
                "phone": phone_utils.mask(b.phone),
                "reason": b.reason,
                "source": b.source,
                "created_at": b.created_at,
            }
            for b in rows
        ],
    )


@router.post("/blacklist")
def add_blacklist(payload: BlacklistIn, db: Session = Depends(get_db)) -> dict:
    added = 0
    for raw in payload.phones:
        if add_to_blacklist(
            db, raw, reason=payload.reason, source="manual", operator=payload.operator
        ):
            added += 1
    db.add(
        AuditLog(
            action="blacklist.add",
            operator=payload.operator,
            detail={"count": added, "reason": payload.reason},
        )
    )
    db.commit()
    return {"ok": True, "added": added}


@router.delete("/blacklist/{blacklist_id}")
def remove_blacklist(blacklist_id: int, db: Session = Depends(get_db)) -> dict:
    record = db.get(Blacklist, blacklist_id)
    if record is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if record.reason == "unsubscribe":
        raise HTTPException(status_code=400, detail="用户主动退订的号码不允许移出黑名单")
    db.delete(record)
    db.commit()
    return {"ok": True}
