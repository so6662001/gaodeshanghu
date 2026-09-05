"""短链跳转与埋点。"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import ClickEvent, Merchant, ShortLink, SmsMessage, utcnow

router = APIRouter(tags=["追踪"])


@router.get("/s/{code}")
def redirect(code: str, request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    """商户点开短链的瞬间，这里完成三件事：计数、归因、生命周期推进。"""
    link = db.scalar(select(ShortLink).where(ShortLink.code == code))
    if link is None:
        return RedirectResponse(url=settings.public_base_url, status_code=302)

    now = utcnow()
    link.clicks = (link.clicks or 0) + 1
    link.first_click_at = link.first_click_at or now
    link.last_click_at = now

    db.add(
        ClickEvent(
            code=code,
            merchant_id=link.merchant_id,
            campaign_id=link.campaign_id,
            ip=request.client.host if request.client else None,
            ua=request.headers.get("user-agent", "")[:500],
        )
    )

    if link.message_id:
        message = db.get(SmsMessage, link.message_id)
        if message and message.clicked_at is None:
            message.clicked_at = now

    if link.merchant_id:
        merchant = db.get(Merchant, link.merchant_id)
        if merchant:
            merchant.last_click_at = now
            # 点击说明有兴趣，直接推进到 engaged，交给 BD 优先跟进
            if merchant.lifecycle in ("new", "reached"):
                merchant.lifecycle = "engaged"

    db.commit()

    # 把归因参数带到落地页，方便主站侧继续追踪注册转化
    target = link.target_url
    separator = "&" if "?" in target else "?"
    target = (
        f"{target}{separator}hdz_src=sms&hdz_code={quote(code)}"
        f"&hdz_cid={link.campaign_id or ''}&hdz_mid={link.merchant_id or ''}"
    )
    return RedirectResponse(url=target, status_code=302)
