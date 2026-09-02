"""接口鉴权。

商户手机号属于个人信息，接口必须鉴权。这里用最轻的 Token 方案，
生产环境建议换成公司统一 SSO，并对导出接口单独做审批与水印。
"""

from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.config import settings


def require_token(x_api_token: str | None = Header(default=None)) -> str:
    if not settings.admin_token:
        return "anonymous"
    if x_api_token != settings.admin_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="鉴权失败，请检查 X-Api-Token"
        )
    return "admin"
