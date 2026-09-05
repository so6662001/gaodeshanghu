"""看板数据接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_token
from app.db import get_db
from app.services import stats as stats_service

router = APIRouter(prefix="/api/stats", tags=["看板"], dependencies=[Depends(require_token)])


@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> dict:
    return stats_service.overview(db)


@router.get("/funnel")
def funnel(campaign_id: int | None = None, db: Session = Depends(get_db)) -> list[dict]:
    return stats_service.funnel(db, campaign_id)


@router.get("/trend")
def trend(days: int = Query(14, ge=3, le=90), db: Session = Depends(get_db)) -> list[dict]:
    return stats_service.trend(db, days)


@router.get("/distribution")
def distribution(
    dimension: str = Query("city", pattern="^(city|category|grade|lifecycle|district)$"),
    limit: int = Query(12, ge=1, le=50),
    db: Session = Depends(get_db),
) -> list[dict]:
    return stats_service.distribution(db, dimension, limit)
