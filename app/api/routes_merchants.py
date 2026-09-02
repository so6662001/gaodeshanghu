"""商户库接口。"""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import distinct, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_token
from app.db import get_db
from app.models import LIFECYCLE_STAGES, Merchant, MerchantPhone, SmsMessage
from app.schemas import MerchantOut, MerchantUpdate, PageOut
from app.utils import phone as phone_utils

router = APIRouter(prefix="/api/merchants", tags=["商户"], dependencies=[Depends(require_token)])


def _apply_filters(stmt, **filters):
    if city := filters.get("city"):
        stmt = stmt.where(Merchant.city == city)
    if district := filters.get("district"):
        stmt = stmt.where(Merchant.district == district)
    if category := filters.get("category"):
        stmt = stmt.where(Merchant.category == category)
    if grade := filters.get("grade"):
        stmt = stmt.where(Merchant.grade == grade)
    if lifecycle := filters.get("lifecycle"):
        stmt = stmt.where(Merchant.lifecycle == lifecycle)
    if keyword := filters.get("keyword"):
        like = f"%{keyword}%"
        stmt = stmt.where(or_(Merchant.name.like(like), Merchant.address.like(like)))
    if filters.get("has_mobile"):
        stmt = stmt.where(
            Merchant.id.in_(
                select(MerchantPhone.merchant_id).where(
                    MerchantPhone.phone_type == "mobile", MerchantPhone.is_valid.is_(True)
                )
            )
        )
    if (min_score := filters.get("min_score")) is not None:
        stmt = stmt.where(Merchant.score >= min_score)
    return stmt


@router.get("", response_model=PageOut)
def list_merchants(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    city: str | None = None,
    district: str | None = None,
    category: str | None = None,
    grade: str | None = None,
    lifecycle: str | None = None,
    keyword: str | None = None,
    has_mobile: bool = False,
    min_score: int | None = None,
    order_by: str = Query("score", pattern="^(score|created_at|touch_count|rating)$"),
    db: Session = Depends(get_db),
) -> PageOut:
    filters = dict(
        city=city, district=district, category=category, grade=grade,
        lifecycle=lifecycle, keyword=keyword, has_mobile=has_mobile, min_score=min_score,
    )
    count_stmt = _apply_filters(select(func.count(Merchant.id)), **filters)
    total = db.scalar(count_stmt) or 0

    order_column = {
        "score": Merchant.score,
        "created_at": Merchant.created_at,
        "touch_count": Merchant.touch_count,
        "rating": Merchant.rating,
    }[order_by]

    stmt = _apply_filters(select(Merchant), **filters)
    merchants = db.scalars(
        stmt.order_by(order_column.desc(), Merchant.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    return PageOut(
        total=total,
        page=page,
        page_size=page_size,
        items=[MerchantOut.model_validate(m) for m in merchants],
    )


@router.get("/filters")
def filter_options(db: Session = Depends(get_db)) -> dict:
    """给前端下拉框用。"""

    def options(column) -> list[str]:
        rows = db.scalars(
            select(distinct(column)).where(column.isnot(None), column != "").limit(200)
        ).all()
        return sorted(str(r) for r in rows)

    return {
        "cities": options(Merchant.city),
        "districts": options(Merchant.district),
        "categories": options(Merchant.category),
        "grades": ["S", "A", "B", "C"],
        "lifecycles": LIFECYCLE_STAGES,
    }


@router.get("/{merchant_id}")
def get_merchant(merchant_id: int, db: Session = Depends(get_db)) -> dict:
    merchant = db.get(Merchant, merchant_id)
    if merchant is None:
        raise HTTPException(status_code=404, detail="商户不存在")

    history = db.scalars(
        select(SmsMessage)
        .where(SmsMessage.merchant_id == merchant_id)
        .order_by(SmsMessage.id.desc())
        .limit(20)
    ).all()

    return {
        "merchant": MerchantOut.model_validate(merchant),
        "note": merchant.note,
        "lng": merchant.lng,
        "lat": merchant.lat,
        "touch_history": [
            {
                "id": m.id,
                "campaign_id": m.campaign_id,
                "variant": m.variant,
                "round_no": m.round_no,
                "phone": phone_utils.mask(m.phone),
                "content": m.content,
                "status": m.status,
                "skip_reason": m.skip_reason,
                "sent_at": m.sent_at,
                "clicked_at": m.clicked_at,
            }
            for m in history
        ],
    }


@router.patch("/{merchant_id}", response_model=MerchantOut)
def update_merchant(
    merchant_id: int, payload: MerchantUpdate, db: Session = Depends(get_db)
) -> Merchant:
    merchant = db.get(Merchant, merchant_id)
    if merchant is None:
        raise HTTPException(status_code=404, detail="商户不存在")
    if payload.lifecycle and payload.lifecycle not in LIFECYCLE_STAGES:
        raise HTTPException(status_code=400, detail=f"生命周期取值须为 {LIFECYCLE_STAGES}")

    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(merchant, field, value)
    db.commit()
    db.refresh(merchant)
    return merchant


@router.get("/export/csv")
def export_csv(
    city: str | None = None,
    category: str | None = None,
    grade: str | None = None,
    lifecycle: str | None = None,
    has_mobile: bool = True,
    unmask: bool = Query(False, description="是否导出完整号码，默认脱敏"),
    limit: int = Query(5000, ge=1, le=50000),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """导出给 BD 做地推名单。默认脱敏，完整号码导出会记审计日志。"""
    stmt = _apply_filters(
        select(Merchant),
        city=city, category=category, grade=grade, lifecycle=lifecycle, has_mobile=has_mobile,
    )
    merchants = db.scalars(stmt.order_by(Merchant.score.desc()).limit(limit)).all()

    buffer = io.StringIO()
    buffer.write("\ufeff")  # BOM，Excel 打开不乱码
    writer = csv.writer(buffer)
    writer.writerow(
        ["商户名", "评级", "分数", "品类", "城市", "区县", "商圈", "地址",
         "手机号", "座机", "评分", "人均", "生命周期", "触达次数", "来源关键词"]
    )
    for m in merchants:
        mobiles = [p.phone for p in m.phones if p.phone_type == "mobile"]
        landlines = [p.phone for p in m.phones if p.phone_type != "mobile"]
        if not unmask:
            mobiles = [phone_utils.mask(p) for p in mobiles]
            landlines = [phone_utils.mask(p) for p in landlines]
        writer.writerow(
            [m.name, m.grade, m.score, m.category, m.city, m.district, m.business_area,
             m.address, "/".join(mobiles), "/".join(landlines), m.rating, m.cost,
             m.lifecycle, m.touch_count, m.source_keyword]
        )

    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="merchants.csv"'},
    )
