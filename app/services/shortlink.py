"""一人一码短链。

短信里不能带 UTM 长参数（占字数、还丑），但没有追踪就没法算转化。
做法是给每条短信生成唯一短码，落地页跳转时把「谁点了、哪个活动、哪个文案」
全部落库，构成 发送 -> 点击 -> 注册 -> 首单 的完整归因漏斗。
"""

from __future__ import annotations

import secrets

from sqlalchemy import select

from app.config import settings
from app.models import ShortLink

# 去掉容易看错的 0/O/1/l/I，商户可能是照着短信手动输入的
ALPHABET = "23456789abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ"


def generate_code(length: int = 7) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def create_link(
    db,
    target_url: str,
    merchant_id: int | None = None,
    campaign_id: int | None = None,
    message_id: int | None = None,
) -> ShortLink:
    for _ in range(6):
        code = generate_code()
        if db.scalar(select(ShortLink.id).where(ShortLink.code == code)) is None:
            break
    else:  # pragma: no cover - 概率极低
        code = generate_code(10)

    link = ShortLink(
        code=code,
        target_url=target_url,
        merchant_id=merchant_id,
        campaign_id=campaign_id,
        message_id=message_id,
    )
    db.add(link)
    db.flush()
    return link


def link_url(code: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/s/{code}"
