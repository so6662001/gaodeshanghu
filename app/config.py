"""全局配置。所有敏感信息统一走环境变量，不落代码库。"""

from __future__ import annotations

from datetime import time
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


def _parse_hhmm(value: str, fallback: time) -> time:
    try:
        hour, minute = value.strip().split(":")
        return time(hour=int(hour), minute=int(minute))
    except (ValueError, AttributeError):
        return fallback


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "货袋子增长运营平台"
    app_env: str = "dev"
    admin_token: str = "change-me-please"
    public_base_url: str = "http://127.0.0.1:8000"

    database_url: str = "sqlite:///./data/huodaizi.db"

    amap_key: str = ""
    amap_sig_secret: str = ""
    amap_qps: float = 3.0
    amap_max_results_per_query: int = 1000

    chuanglan_account: str = ""
    chuanglan_password: str = ""
    chuanglan_base_url: str = "https://smssh1.253.com"
    sms_sign: str = "【货袋子】"
    sms_qps: float = 20.0
    sms_batch_size: int = 500
    sms_dry_run: bool = True

    sms_send_window_start: str = "09:00"
    sms_send_window_end: str = "20:00"
    sms_min_interval_days: int = 7
    sms_max_touch_per_month: int = 3
    sms_unsubscribe_keywords: str = "TD,T,N,退订,拒收,QX"

    @property
    def send_window(self) -> tuple[time, time]:
        return (
            _parse_hhmm(self.sms_send_window_start, time(9, 0)),
            _parse_hhmm(self.sms_send_window_end, time(20, 0)),
        )

    @property
    def unsubscribe_keywords(self) -> list[str]:
        return [k.strip().upper() for k in self.sms_unsubscribe_keywords.split(",") if k.strip()]

    @property
    def amap_ready(self) -> bool:
        return bool(self.amap_key)

    @property
    def sms_ready(self) -> bool:
        return bool(self.chuanglan_account and self.chuanglan_password)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.database_url.startswith("sqlite"):
        (BASE_DIR / "data").mkdir(parents=True, exist_ok=True)
    return settings


settings = get_settings()
