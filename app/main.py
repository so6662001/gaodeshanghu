"""应用入口。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import (
    routes_callback,
    routes_collect,
    routes_marketing,
    routes_merchants,
    routes_sms,
    routes_stats,
    routes_track,
)
from app.config import settings
from app.db import init_db, session_scope
from app.services.dispatcher import dispatcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def seed_default_templates() -> None:
    """首次启动灌入三轮触达的默认文案，运营可直接改。"""
    from sqlalchemy import func, select

    from app.models import SmsTemplate

    with session_scope() as db:
        if db.scalar(select(func.count(SmsTemplate.id))):
            return

        defaults = [
            {
                "name": "首触-源头直采省成本",
                "scene": "first_touch",
                "content": "{商家名}老板您好，货袋子{品类}产地直采平台已在{城市}上线，"
                "同行门店平均降低采购成本15%，次日达免配送费。看看您店里的常用品报价：{链接}",
            },
            {
                "name": "二触-新客首单立减",
                "scene": "follow_up",
                "content": "{简称}老板，货袋子{城市}仓新客首单立减200元，米面粮油今日特价，"
                "支持先货后款。{日期}前下单还送一次免费配送：{链接}",
            },
            {
                "name": "三触-同商圈案例",
                "scene": "follow_up",
                "content": "{简称}老板，{商圈}已有37家同行在货袋子直采，"
                "月均省下6800元采购成本。名额有限，点开看您门店的专属报价：{链接}",
            },
            {
                "name": "唤醒-沉默商户召回",
                "scene": "reactivate",
                "content": "{简称}老板，好久没见您下单了。货袋子本月上新一批{品类}货源，"
                "老客回归专享9折。看看有没有您需要的：{链接}",
            },
        ]
        for item in defaults:
            db.add(SmsTemplate(sign=settings.sms_sign, with_link=True, **item))
        logger.info("已初始化 %s 个默认短信模板", len(defaults))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    seed_default_templates()
    dispatcher.start()
    if settings.sms_dry_run:
        logger.warning("当前为短信演练模式（SMS_DRY_RUN=true），不会真实下发")
    if not settings.amap_ready:
        logger.warning("未配置 AMAP_KEY，采集功能不可用")
    yield
    dispatcher.stop()


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description="高德关键字获客 + 创蓝短信批量触达的商户增长运营平台",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_collect.router)
app.include_router(routes_merchants.router)
app.include_router(routes_marketing.router)
app.include_router(routes_sms.router)
app.include_router(routes_stats.router)
app.include_router(routes_callback.router)
app.include_router(routes_track.router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health", tags=["系统"])
def health() -> dict:
    return {
        "ok": True,
        "version": __version__,
        "amap_ready": settings.amap_ready,
        "sms_ready": settings.sms_ready,
        "dry_run": settings.sms_dry_run,
        "dispatcher_running": dispatcher.running,
    }


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))
