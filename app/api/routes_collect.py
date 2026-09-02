"""采集相关接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_token
from app.config import settings
from app.db import get_db
from app.models import CollectTask
from app.schemas import AmapPreviewRequest, CollectTaskCreate, CollectTaskOut, PageOut
from app.services.amap import AmapClient, AmapError
from app.services.collector import start_collect_task
from app.services.scoring import score_poi
from app.utils import phone as phone_utils

router = APIRouter(prefix="/api/collect", tags=["采集"], dependencies=[Depends(require_token)])


@router.get("/health")
def amap_health() -> dict:
    """探活：确认 AMAP_KEY 可用，避免建了任务才发现 Key 没配好。"""
    if not settings.amap_ready:
        return {"ok": False, "message": "未配置 AMAP_KEY，请在 .env 中填写"}
    try:
        with AmapClient() as client:
            info = client.district("杭州", with_boundary=False)
        return {"ok": True, "message": "高德接口连通正常", "sample": info.get("name") if info else None}
    except AmapError as exc:
        return {"ok": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"连通失败：{exc}"}


@router.post("/preview")
def preview(payload: AmapPreviewRequest) -> dict:
    """试搜：看看关键词能捞到什么、手机号覆盖率如何，再决定要不要跑全量。

    这一步很重要 —— 不同品类在高德挂手机号的比例差异极大，
    先试搜 25 条估算可触达率，能避免把配额浪费在挂不到号码的品类上。
    """
    if not settings.amap_ready:
        raise HTTPException(status_code=400, detail="未配置 AMAP_KEY")
    try:
        with AmapClient() as client:
            pois = list(
                client.search_text(
                    payload.keyword,
                    region=payload.region,
                    types=payload.types,
                    max_results=payload.limit,
                )
            )
    except AmapError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    items = []
    mobile_hit = 0
    for poi in pois:
        phones = phone_utils.parse_tel_field(poi.tel)
        has_mobile = any(p["phone_type"] == "mobile" for p in phones)
        mobile_hit += 1 if has_mobile else 0
        score, grade, tags = score_poi(poi, phones)
        items.append(
            {
                "name": poi.name,
                "address": poi.address,
                "city": poi.city,
                "district": poi.district,
                "type_name": poi.type_name,
                "rating": poi.rating,
                "cost": poi.cost,
                "phones": [
                    {**p, "masked": phone_utils.mask(p["phone"])} for p in phones
                ],
                "has_mobile": has_mobile,
                "score": score,
                "grade": grade,
                "tags": tags,
            }
        )

    return {
        "total": len(items),
        "mobile_rate": round(mobile_hit / len(items) * 100, 1) if items else 0.0,
        "items": items,
    }


@router.post("/tasks", response_model=CollectTaskOut)
def create_task(payload: CollectTaskCreate, db: Session = Depends(get_db)) -> CollectTask:
    if not settings.amap_ready:
        raise HTTPException(status_code=400, detail="未配置 AMAP_KEY")
    if payload.mode == "grid" and not payload.region:
        raise HTTPException(status_code=400, detail="网格模式必须指定城市")
    if payload.mode == "around" and not payload.location:
        raise HTTPException(status_code=400, detail="周边模式必须指定中心点坐标")

    task = CollectTask(
        name=payload.name,
        mode=payload.mode,
        keywords=payload.keywords,
        region=payload.region,
        types=payload.types,
        params={
            "grid": payload.grid,
            "location": payload.location,
            "radius": payload.radius,
            "city_limit": payload.city_limit,
        },
        status="pending",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    start_collect_task(task.id)
    return task


@router.get("/tasks", response_model=PageOut)
def list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> PageOut:
    total = db.scalar(select(func.count(CollectTask.id))) or 0
    tasks = db.scalars(
        select(CollectTask)
        .order_by(CollectTask.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return PageOut(
        total=total,
        page=page,
        page_size=page_size,
        items=[CollectTaskOut.model_validate(t) for t in tasks],
    )


@router.get("/tasks/{task_id}", response_model=CollectTaskOut)
def get_task(task_id: int, db: Session = Depends(get_db)) -> CollectTask:
    task = db.get(CollectTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.post("/tasks/{task_id}/retry", response_model=CollectTaskOut)
def retry_task(task_id: int, db: Session = Depends(get_db)) -> CollectTask:
    task = db.get(CollectTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status == "running":
        raise HTTPException(status_code=400, detail="任务正在执行中")
    task.status = "pending"
    task.progress = 0
    task.message = None
    db.commit()
    db.refresh(task)
    start_collect_task(task.id)
    return task
