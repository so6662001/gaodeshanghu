"""数据模型。

围绕「采集 -> 清洗分层 -> 合规触达 -> 归因转化」四段链路组织，
商户生命周期状态机借鉴美团 BD 体系：
    new(新采集) -> reached(已触达) -> engaged(有互动) -> intent(有意向)
    -> registered(已注册) -> activated(已下单) -> dormant(沉默) / invalid(无效)
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# ---------------------------------------------------------------------------
# 商户库
# ---------------------------------------------------------------------------

LIFECYCLE_STAGES = [
    "new",
    "reached",
    "engaged",
    "intent",
    "registered",
    "activated",
    "dormant",
    "invalid",
]


class Merchant(Base, TimestampMixin):
    """商户主档，一条记录对应一个高德 POI。"""

    __tablename__ = "merchants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    amap_poi_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)

    name: Mapped[str] = mapped_column(String(255), index=True)
    address: Mapped[str | None] = mapped_column(String(512))
    province: Mapped[str | None] = mapped_column(String(64), index=True)
    city: Mapped[str | None] = mapped_column(String(64), index=True)
    district: Mapped[str | None] = mapped_column(String(64), index=True)
    adcode: Mapped[str | None] = mapped_column(String(16), index=True)
    business_area: Mapped[str | None] = mapped_column(String(128), index=True)

    typecode: Mapped[str | None] = mapped_column(String(32), index=True)
    type_name: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(64), index=True)

    lng: Mapped[float | None] = mapped_column(Float)
    lat: Mapped[float | None] = mapped_column(Float)

    rating: Mapped[float | None] = mapped_column(Float)
    cost: Mapped[float | None] = mapped_column(Float)
    open_time: Mapped[str | None] = mapped_column(String(255))
    tel_raw: Mapped[str | None] = mapped_column(String(255))

    # 运营分层
    score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    grade: Mapped[str] = mapped_column(String(4), default="C", index=True)  # S/A/B/C
    lifecycle: Mapped[str] = mapped_column(String(24), default="new", index=True)
    tags: Mapped[list | None] = mapped_column(JSON, default=list)
    owner: Mapped[str | None] = mapped_column(String(64), index=True)  # 归属 BD
    note: Mapped[str | None] = mapped_column(Text)

    # 触达统计
    touch_count: Mapped[int] = mapped_column(Integer, default=0)
    last_touch_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    last_click_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_reply_at: Mapped[datetime | None] = mapped_column(DateTime)

    source_keyword: Mapped[str | None] = mapped_column(String(128), index=True)
    source_task_id: Mapped[int | None] = mapped_column(Integer, index=True)
    raw: Mapped[dict | None] = mapped_column(JSON)

    phones: Mapped[list["MerchantPhone"]] = relationship(
        back_populates="merchant", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_merchant_city_category", "city", "category"),
        Index("ix_merchant_grade_lifecycle", "grade", "lifecycle"),
    )

    @property
    def primary_phone(self) -> str | None:
        mobiles = [p for p in self.phones if p.is_valid and p.phone_type == "mobile"]
        if mobiles:
            return sorted(mobiles, key=lambda p: (not p.is_primary, p.id))[0].phone
        valid = [p for p in self.phones if p.is_valid]
        return valid[0].phone if valid else None


class MerchantPhone(Base, TimestampMixin):
    """一个 POI 往往带多个号码（前台座机 + 老板手机），拆开存便于精准触达。"""

    __tablename__ = "merchant_phones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), index=True
    )
    phone: Mapped[str] = mapped_column(String(32), index=True)
    phone_type: Mapped[str] = mapped_column(String(16), default="mobile")  # mobile/landline/other
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    is_valid: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    carrier: Mapped[str | None] = mapped_column(String(16))

    merchant: Mapped[Merchant] = relationship(back_populates="phones")

    __table_args__ = (UniqueConstraint("merchant_id", "phone", name="uq_merchant_phone"),)


# ---------------------------------------------------------------------------
# 采集
# ---------------------------------------------------------------------------


class CollectTask(Base, TimestampMixin):
    """高德采集任务。支持关键字、周边、多边形网格三种模式。"""

    __tablename__ = "collect_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    mode: Mapped[str] = mapped_column(String(16), default="text")  # text/around/grid
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    region: Mapped[str | None] = mapped_column(String(64))
    types: Mapped[str | None] = mapped_column(String(255))
    params: Mapped[dict | None] = mapped_column(JSON, default=dict)

    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    total_fetched: Mapped[int] = mapped_column(Integer, default=0)
    total_saved: Mapped[int] = mapped_column(Integer, default=0)
    total_duplicated: Mapped[int] = mapped_column(Integer, default=0)
    total_with_phone: Mapped[int] = mapped_column(Integer, default=0)
    total_mobile: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text)

    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


# ---------------------------------------------------------------------------
# 触达
# ---------------------------------------------------------------------------


class SmsTemplate(Base, TimestampMixin):
    """短信模板。变量写作 {商家名} 这种中文占位，运营同学好理解。"""

    __tablename__ = "sms_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    sign: Mapped[str | None] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    scene: Mapped[str] = mapped_column(String(32), default="first_touch", index=True)
    status: Mapped[str] = mapped_column(String(16), default="enabled", index=True)
    with_link: Mapped[bool] = mapped_column(Boolean, default=True)
    landing_url: Mapped[str | None] = mapped_column(String(512))
    remark: Mapped[str | None] = mapped_column(Text)


class Campaign(Base, TimestampMixin):
    """触达活动。一个活动可挂多个模板做 A/B。"""

    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    goal: Mapped[str | None] = mapped_column(String(255))
    audience: Mapped[dict] = mapped_column(JSON, default=dict)
    variants: Mapped[list] = mapped_column(JSON, default=list)  # [{template_id, weight, label}]
    landing_url: Mapped[str | None] = mapped_column(String(512))

    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    # draft -> ready -> running -> paused -> finished
    daily_limit: Mapped[int] = mapped_column(Integer, default=2000)
    follow_up: Mapped[dict | None] = mapped_column(JSON, default=dict)
    # {"enabled":true,"rounds":[{"after_days":3,"template_id":2,"only_untouched":true}]}

    total_targets: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


SMS_STATUS = [
    "pending",
    "sending",
    "sent",
    "delivered",
    "failed",
    "skipped",
]


class SmsMessage(Base, TimestampMixin):
    """发送流水，同时充当发送队列。"""

    __tablename__ = "sms_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="SET NULL"), index=True
    )
    merchant_id: Mapped[int | None] = mapped_column(
        ForeignKey("merchants.id", ondelete="SET NULL"), index=True
    )
    template_id: Mapped[int | None] = mapped_column(Integer, index=True)
    variant: Mapped[str | None] = mapped_column(String(32), index=True)
    round_no: Mapped[int] = mapped_column(Integer, default=1)

    phone: Mapped[str] = mapped_column(String(32), index=True)
    content: Mapped[str] = mapped_column(Text)
    # 变量短信用：与模板占位符顺序一一对应的取值，便于一次 API 调用群发个性化内容
    vars: Mapped[list | None] = mapped_column(JSON, default=list)
    link_code: Mapped[str | None] = mapped_column(String(16), index=True)

    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    skip_reason: Mapped[str | None] = mapped_column(String(64), index=True)
    # 发送批次租约：多进程部署时用来原子抢占待发消息，避免同一条被重复发出
    lease: Mapped[str | None] = mapped_column(String(32), index=True)
    leased_at: Mapped[datetime | None] = mapped_column(DateTime)
    provider_msg_id: Mapped[str | None] = mapped_column(String(64), index=True)
    provider_code: Mapped[str | None] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    fee_count: Mapped[int] = mapped_column(Integer, default=1)  # 计费条数

    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)
    clicked_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        Index("ix_sms_campaign_status", "campaign_id", "status"),
        Index("ix_sms_phone_sent", "phone", "sent_at"),
    )


class SmsReply(Base, TimestampMixin):
    """上行回复。退订要秒级生效，商机线索要马上流转给 BD。"""

    __tablename__ = "sms_replies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(32), index=True)
    content: Mapped[str] = mapped_column(Text)
    merchant_id: Mapped[int | None] = mapped_column(Integer, index=True)
    campaign_id: Mapped[int | None] = mapped_column(Integer, index=True)
    intent: Mapped[str] = mapped_column(String(24), default="unknown", index=True)
    # unsubscribe / interested / complaint / unknown
    handled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Blacklist(Base, TimestampMixin):
    """黑名单：退订、投诉、空号、竞对，一律不再触达。"""

    __tablename__ = "blacklist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    reason: Mapped[str] = mapped_column(String(64), default="unsubscribe")
    source: Mapped[str | None] = mapped_column(String(64))
    operator: Mapped[str | None] = mapped_column(String(64))


# ---------------------------------------------------------------------------
# 归因
# ---------------------------------------------------------------------------


class ShortLink(Base, TimestampMixin):
    """一人一码短链，点击即可归因到具体商户与活动。"""

    __tablename__ = "short_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    target_url: Mapped[str] = mapped_column(String(1024))
    merchant_id: Mapped[int | None] = mapped_column(Integer, index=True)
    campaign_id: Mapped[int | None] = mapped_column(Integer, index=True)
    message_id: Mapped[int | None] = mapped_column(Integer, index=True)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    first_click_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_click_at: Mapped[datetime | None] = mapped_column(DateTime)


class ClickEvent(Base, TimestampMixin):
    __tablename__ = "click_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    merchant_id: Mapped[int | None] = mapped_column(Integer, index=True)
    campaign_id: Mapped[int | None] = mapped_column(Integer, index=True)
    ip: Mapped[str | None] = mapped_column(String(64))
    ua: Mapped[str | None] = mapped_column(String(512))


class ConversionEvent(Base, TimestampMixin):
    """注册、开店、首单等业务转化，由货袋子主站回传。"""

    __tablename__ = "conversion_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    merchant_id: Mapped[int | None] = mapped_column(Integer, index=True)
    phone: Mapped[str | None] = mapped_column(String(32), index=True)
    campaign_id: Mapped[int | None] = mapped_column(Integer, index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    # register / open_shop / first_order / repeat_order
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    payload: Mapped[dict | None] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AuditLog(Base, TimestampMixin):
    """关键动作留痕，短信是强监管业务，出问题要能追溯到人。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str | None] = mapped_column(String(128))
    operator: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict | None] = mapped_column(JSON)
