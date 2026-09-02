"""采集任务执行器：调高德 -> 清洗 -> 去重 -> 打分 -> 入库。

去重是采集环节最容易被低估的一环。同一家店在关键词「火锅」和「川菜」下都会
出现，网格化搜索的相邻格子边界上也会重复返回，不去重会导致同一个老板收到
多条相同短信 —— 这是投诉和退订的主要来源。

三层去重：
    1. amap_poi_id 精确命中（同一 POI）
    2. 归一化门店名 + 地址（POI 被高德重建过 id 会变）
    3. 手机号命中已有商户（同老板多门店，短信按号码维度再做一次频控）
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime

from sqlalchemy import select

from app.db import session_scope
from app.models import CollectTask, Merchant, MerchantPhone, utcnow
from app.services.amap import AmapClient, AmapError, Poi
from app.services.scoring import resolve_category, score_poi
from app.utils import phone as phone_utils

logger = logging.getLogger(__name__)

_NOISE = re.compile(r"[\s()（）\[\]【】·、,，.。\-—_/]+")


def normalize_name(name: str) -> str:
    return _NOISE.sub("", name or "").lower()


def dedupe_key(poi: Poi) -> str:
    return f"{normalize_name(poi.name)}|{normalize_name(poi.address or '')[:24]}"


class CollectResult:
    def __init__(self) -> None:
        self.fetched = 0
        self.saved = 0
        self.duplicated = 0
        self.with_phone = 0
        self.with_mobile = 0


def upsert_poi(db, poi: Poi, task_id: int | None, keyword: str | None) -> str:
    """写入或更新一个 POI，返回 saved / duplicated / updated。"""
    phones = phone_utils.parse_tel_field(poi.tel)
    score, grade, tags = score_poi(poi, phones)
    category, _ = resolve_category(poi.typecode, poi.type_name)

    merchant: Merchant | None = None
    if poi.poi_id:
        merchant = db.scalar(select(Merchant).where(Merchant.amap_poi_id == poi.poi_id))

    if merchant is None:
        # 名称 + 地址兜底去重
        candidates = db.scalars(
            select(Merchant).where(Merchant.name == poi.name, Merchant.city == poi.city)
        ).all()
        key = dedupe_key(poi)
        for candidate in candidates:
            candidate_poi = Poi(
                poi_id=candidate.amap_poi_id or "",
                name=candidate.name,
                address=candidate.address,
            )
            if dedupe_key(candidate_poi) == key:
                merchant = candidate
                break

    if merchant is not None:
        changed = False
        # 已有商户只补齐缺失字段，不覆盖运营侧已经维护的数据
        for attr, value in (
            ("address", poi.address),
            ("business_area", poi.business_area),
            ("rating", poi.rating),
            ("cost", poi.cost),
            ("open_time", poi.open_time),
            ("tel_raw", poi.tel),
        ):
            if value and not getattr(merchant, attr):
                setattr(merchant, attr, value)
                changed = True
        if merchant.amap_poi_id is None and poi.poi_id:
            merchant.amap_poi_id = poi.poi_id
            changed = True
        added = _sync_phones(db, merchant, phones)
        if added or changed:
            merchant.score, merchant.grade = score, grade
            merchant.updated_at = utcnow()
        return "duplicated"

    merchant = Merchant(
        amap_poi_id=poi.poi_id or None,
        name=poi.name,
        address=poi.address,
        province=poi.province,
        city=poi.city,
        district=poi.district,
        adcode=poi.adcode,
        business_area=poi.business_area,
        typecode=poi.typecode,
        type_name=poi.type_name,
        category=category,
        lng=poi.lng,
        lat=poi.lat,
        rating=poi.rating,
        cost=poi.cost,
        open_time=poi.open_time,
        tel_raw=poi.tel,
        score=score,
        grade=grade,
        tags=tags,
        lifecycle="new",
        source_keyword=keyword,
        source_task_id=task_id,
        raw=poi.raw,
    )
    db.add(merchant)
    db.flush()
    _sync_phones(db, merchant, phones)
    return "saved"


def _sync_phones(db, merchant: Merchant, phones: list[dict]) -> bool:
    existing = {p.phone for p in merchant.phones}
    added = False
    has_primary = any(p.is_primary for p in merchant.phones)
    for item in phones:
        if item["phone"] in existing:
            continue
        is_primary = not has_primary and item["phone_type"] == "mobile"
        db.add(
            MerchantPhone(
                merchant_id=merchant.id,
                phone=item["phone"],
                phone_type=item["phone_type"],
                carrier=item.get("carrier"),
                is_primary=is_primary,
                is_valid=item["phone_type"] in ("mobile", "landline", "service"),
            )
        )
        existing.add(item["phone"])
        has_primary = has_primary or is_primary
        added = True
    return added


def run_collect_task(task_id: int) -> None:
    """在后台线程里执行采集任务。"""
    with session_scope() as db:
        task = db.get(CollectTask, task_id)
        if task is None:
            return
        task.status = "running"
        task.started_at = utcnow()
        task.message = None
        mode = task.mode
        keywords = list(task.keywords or [])
        region = task.region
        types = task.types
        params = dict(task.params or {})

    result = CollectResult()
    error_message: str | None = None

    try:
        with AmapClient() as client:
            total_units = max(len(keywords), 1)
            for index, keyword in enumerate(keywords or [None]):
                if mode == "grid":
                    grid = int(params.get("grid", 4))

                    def on_cell(cell_no: int, cell_total: int, _i=index) -> None:
                        done = (_i + cell_no / max(cell_total, 1)) / total_units
                        _update_progress(task_id, int(done * 100))

                    stream = client.search_grid(
                        keyword or "", region or "", types=types, grid=grid, on_cell=on_cell
                    )
                elif mode == "around":
                    stream = client.search_around(
                        location=params.get("location", ""),
                        keywords=keyword,
                        types=types,
                        radius=int(params.get("radius", 5000)),
                    )
                else:
                    stream = client.search_text(
                        keyword or "",
                        region=region,
                        types=types,
                        city_limit=bool(params.get("city_limit", True)),
                    )

                _consume(stream, task_id, keyword, result)
                _update_progress(task_id, int((index + 1) / total_units * 100), result)
    except AmapError as exc:
        error_message = str(exc)
        logger.error("采集任务 %s 失败：%s", task_id, exc)
    except Exception as exc:  # noqa: BLE001
        error_message = f"采集异常：{exc}"
        logger.exception("采集任务 %s 异常", task_id)

    with session_scope() as db:
        task = db.get(CollectTask, task_id)
        if task is None:
            return
        task.status = "failed" if error_message else "finished"
        task.message = error_message or (
            f"共抓取 {result.fetched} 条，新增 {result.saved} 家，"
            f"去重 {result.duplicated} 条，其中 {result.with_mobile} 家可短信触达"
        )
        task.total_fetched = result.fetched
        task.total_saved = result.saved
        task.total_duplicated = result.duplicated
        task.total_with_phone = result.with_phone
        task.total_mobile = result.with_mobile
        task.progress = 100
        task.finished_at = utcnow()


def _consume(stream, task_id: int, keyword: str | None, result: CollectResult) -> None:
    """按批提交，避免长事务把 SQLite 锁住。"""
    batch: list[Poi] = []
    seen_keys: set[str] = set()

    def flush() -> None:
        if not batch:
            return
        with session_scope() as db:
            for poi in batch:
                outcome = upsert_poi(db, poi, task_id, keyword)
                if outcome == "saved":
                    result.saved += 1
                else:
                    result.duplicated += 1
        batch.clear()

    for poi in stream:
        result.fetched += 1
        key = poi.poi_id or dedupe_key(poi)
        if key in seen_keys:
            result.duplicated += 1
            continue
        seen_keys.add(key)

        phones = phone_utils.parse_tel_field(poi.tel)
        if phones:
            result.with_phone += 1
        if any(p["phone_type"] == "mobile" for p in phones):
            result.with_mobile += 1

        batch.append(poi)
        if len(batch) >= 50:
            flush()
    flush()


def _update_progress(task_id: int, progress: int, result: CollectResult | None = None) -> None:
    with session_scope() as db:
        task = db.get(CollectTask, task_id)
        if task is None:
            return
        task.progress = max(0, min(progress, 99))
        if result:
            task.total_fetched = result.fetched
            task.total_saved = result.saved
            task.total_duplicated = result.duplicated
            task.total_with_phone = result.with_phone
            task.total_mobile = result.with_mobile


def start_collect_task(task_id: int) -> None:
    thread = threading.Thread(
        target=run_collect_task, args=(task_id,), name=f"collect-{task_id}", daemon=True
    )
    thread.start()


def summarize_started(task: CollectTask) -> datetime | None:
    return task.started_at
