"""API 请求 / 响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# 采集
# ---------------------------------------------------------------------------
class CollectTaskCreate(BaseModel):
    name: str = Field(..., max_length=128, description="任务名，如「杭州餐饮首批」")
    mode: Literal["text", "around", "grid"] = "text"
    keywords: list[str] = Field(..., description="关键词列表，如 [\"火锅\", \"烧烤\"]")
    region: str | None = Field(None, description="城市名或 adcode，如「杭州」")
    types: str | None = Field(None, description="高德 POI 类型编码，多个用 | 分隔")
    grid: int = Field(4, ge=2, le=10, description="网格模式下的切分密度，4 表示 4×4")
    location: str | None = Field(None, description="周边模式的中心点 lng,lat")
    radius: int = Field(5000, ge=100, le=50000)
    city_limit: bool = True

    @field_validator("keywords")
    @classmethod
    def _clean_keywords(cls, value: list[str]) -> list[str]:
        cleaned = [k.strip() for k in value if k and k.strip()]
        if not cleaned:
            raise ValueError("关键词不能为空")
        return cleaned


class CollectTaskOut(BaseModel):
    id: int
    name: str
    mode: str
    keywords: list[str]
    region: str | None
    status: str
    progress: int
    total_fetched: int
    total_saved: int
    total_duplicated: int
    total_with_phone: int
    total_mobile: int
    message: str | None
    created_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class AmapPreviewRequest(BaseModel):
    keyword: str
    region: str | None = None
    types: str | None = None
    limit: int = Field(10, ge=1, le=25)


# ---------------------------------------------------------------------------
# 商户
# ---------------------------------------------------------------------------
class PhoneOut(BaseModel):
    phone: str
    phone_type: str
    carrier: str | None = None
    is_primary: bool
    is_valid: bool

    model_config = {"from_attributes": True}


class MerchantOut(BaseModel):
    id: int
    name: str
    address: str | None
    city: str | None
    district: str | None
    business_area: str | None
    category: str | None
    type_name: str | None
    rating: float | None
    cost: float | None
    score: int
    grade: str
    lifecycle: str
    tags: list[str] | None
    owner: str | None
    touch_count: int
    last_touch_at: datetime | None
    source_keyword: str | None
    created_at: datetime
    phones: list[PhoneOut] = []

    model_config = {"from_attributes": True}


class MerchantUpdate(BaseModel):
    lifecycle: str | None = None
    owner: str | None = None
    note: str | None = None
    tags: list[str] | None = None
    grade: str | None = None


class PageOut(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[Any]


# ---------------------------------------------------------------------------
# 模板
# ---------------------------------------------------------------------------
class TemplateIn(BaseModel):
    name: str
    content: str
    sign: str | None = None
    scene: str = "first_touch"
    with_link: bool = True
    landing_url: str | None = None
    remark: str | None = None
    status: str = "enabled"


class TemplateOut(TemplateIn):
    id: int
    created_at: datetime
    variables: list[str] = []
    preview: str = ""
    billing_count: int = 1

    model_config = {"from_attributes": True}


class TemplatePreviewRequest(BaseModel):
    content: str
    sign: str | None = None
    merchant_id: int | None = None


# ---------------------------------------------------------------------------
# 活动
# ---------------------------------------------------------------------------
class VariantIn(BaseModel):
    template_id: int
    weight: int = Field(1, ge=0, le=100)
    label: str | None = None


class AudienceIn(BaseModel):
    city: str | None = None
    district: str | None = None
    business_area: str | None = None
    category: str | None = None
    grades: list[str] | None = None
    lifecycles: list[str] | None = None
    min_score: int | None = None
    max_score: int | None = None
    keyword: str | None = None
    owner: str | None = None
    never_touched: bool = False
    merchant_ids: list[int] | None = None
    exclude_merchant_ids: list[int] | None = None


class FollowUpRound(BaseModel):
    after_days: int = Field(3, ge=1, le=60)
    template_id: int


class FollowUpIn(BaseModel):
    enabled: bool = False
    rounds: list[FollowUpRound] = []


class CampaignIn(BaseModel):
    name: str
    goal: str | None = None
    audience: AudienceIn = AudienceIn()
    variants: list[VariantIn]
    landing_url: str | None = None
    daily_limit: int = Field(2000, ge=1, le=200000)
    follow_up: FollowUpIn = FollowUpIn()


class CampaignOut(BaseModel):
    id: int
    name: str
    goal: str | None
    status: str
    audience: dict
    variants: list
    landing_url: str | None
    daily_limit: int
    follow_up: dict | None
    total_targets: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# 短信
# ---------------------------------------------------------------------------
class TestSendRequest(BaseModel):
    phone: str
    content: str
    sign: str | None = None


class BlacklistIn(BaseModel):
    phones: list[str]
    reason: str = "manual"
    operator: str | None = None


class ConversionIn(BaseModel):
    """货袋子主站回传转化事件。"""

    phone: str | None = None
    merchant_id: int | None = None
    event_type: Literal["register", "open_shop", "first_order", "repeat_order"]
    amount: float = 0.0
    payload: dict | None = None
