"""模板与活动接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_token
from app.db import get_db
from app.models import Campaign, Merchant, SmsMessage, SmsTemplate, utcnow
from app.schemas import (
    CampaignIn,
    CampaignOut,
    PageOut,
    TemplateIn,
    TemplateOut,
    TemplatePreviewRequest,
)
from app.services import stats as stats_service
from app.services.campaign import count_audience, prepare_campaign
from app.services.dispatcher import dispatch_campaign, schedule_follow_ups
from app.services.render import SUPPORTED_VARIABLES, extract_variables, render_for_merchant
from app.services.sms_chuanglan import billing_count, build_content

router = APIRouter(prefix="/api", tags=["运营"], dependencies=[Depends(require_token)])


# ---------------------------------------------------------------------------
# 模板
# ---------------------------------------------------------------------------
def _template_out(db: Session, template: SmsTemplate) -> TemplateOut:
    content = build_content(template.content, template.sign)
    preview = render_for_merchant(content, None, link="https://hdz.cn/s/aB3xY7z")
    data = TemplateOut.model_validate(template)
    data.variables = extract_variables(template.content)
    data.preview = preview
    data.billing_count = billing_count(preview)
    return data


@router.get("/templates/variables")
def template_variables() -> dict:
    return {"variables": SUPPORTED_VARIABLES}


@router.get("/templates")
def list_templates(scene: str | None = None, db: Session = Depends(get_db)) -> list[TemplateOut]:
    stmt = select(SmsTemplate).order_by(SmsTemplate.id.desc())
    if scene:
        stmt = stmt.where(SmsTemplate.scene == scene)
    return [_template_out(db, t) for t in db.scalars(stmt).all()]


@router.post("/templates", response_model=TemplateOut)
def create_template(payload: TemplateIn, db: Session = Depends(get_db)) -> TemplateOut:
    template = SmsTemplate(**payload.model_dump())
    db.add(template)
    db.commit()
    db.refresh(template)
    return _template_out(db, template)


@router.put("/templates/{template_id}", response_model=TemplateOut)
def update_template(
    template_id: int, payload: TemplateIn, db: Session = Depends(get_db)
) -> TemplateOut:
    template = db.get(SmsTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    for field, value in payload.model_dump().items():
        setattr(template, field, value)
    db.commit()
    db.refresh(template)
    return _template_out(db, template)


@router.delete("/templates/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db)) -> dict:
    template = db.get(SmsTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    in_use = db.scalar(
        select(func.count(SmsMessage.id)).where(SmsMessage.template_id == template_id)
    )
    if in_use:
        template.status = "disabled"
        db.commit()
        return {"ok": True, "message": "模板已被活动使用，改为停用而非删除"}
    db.delete(template)
    db.commit()
    return {"ok": True}


@router.post("/templates/preview")
def preview_template(payload: TemplatePreviewRequest, db: Session = Depends(get_db)) -> dict:
    merchant = db.get(Merchant, payload.merchant_id) if payload.merchant_id else None
    content = build_content(payload.content, payload.sign)
    rendered = render_for_merchant(content, merchant, link="https://hdz.cn/s/aB3xY7z")
    return {
        "preview": rendered,
        "length": len(rendered),
        "billing_count": billing_count(rendered),
        "variables": extract_variables(payload.content),
        "warnings": _content_warnings(rendered),
    }


def _content_warnings(content: str) -> list[str]:
    """把常见的被拦截原因提前提示出来，别等发失败了才知道。"""
    warnings = []
    if not content.startswith("【"):
        warnings.append("内容未以签名开头，运营商可能拦截")
    if "退订" not in content:
        warnings.append("缺少退订方式，营销短信必须提供")
    if len(content) > 70:
        warnings.append(f"内容 {len(content)} 字，将按 {billing_count(content)} 条计费")
    for word in ("免费", "第一", "最低", "国家级", "点击链接", "中奖", "贷款", "发票"):
        if word in content:
            warnings.append(f"含敏感词「{word}」，易被运营商拦截")
    return warnings


# ---------------------------------------------------------------------------
# 活动
# ---------------------------------------------------------------------------
@router.post("/campaigns/audience/count")
def audience_count(payload: dict, db: Session = Depends(get_db)) -> dict:
    """圈人预估。发之前先看清楚要打多少人、花多少钱。"""
    total = count_audience(db, payload or {})
    return {"total": total, "estimated_billing": total}


@router.get("/campaigns")
def list_campaigns(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> PageOut:
    total = db.scalar(select(func.count(Campaign.id))) or 0
    campaigns = db.scalars(
        select(Campaign)
        .order_by(Campaign.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    items = []
    for campaign in campaigns:
        data = CampaignOut.model_validate(campaign).model_dump()
        counts = dict(
            db.execute(
                select(SmsMessage.status, func.count(SmsMessage.id))
                .where(SmsMessage.campaign_id == campaign.id)
                .group_by(SmsMessage.status)
            ).all()
        )
        data["counts"] = counts
        items.append(data)
    return PageOut(total=total, page=page, page_size=page_size, items=items)


@router.post("/campaigns", response_model=CampaignOut)
def create_campaign(payload: CampaignIn, db: Session = Depends(get_db)) -> Campaign:
    for variant in payload.variants:
        if db.get(SmsTemplate, variant.template_id) is None:
            raise HTTPException(status_code=400, detail=f"模板 {variant.template_id} 不存在")

    campaign = Campaign(
        name=payload.name,
        goal=payload.goal,
        audience=payload.audience.model_dump(exclude_none=True),
        variants=[v.model_dump() for v in payload.variants],
        landing_url=payload.landing_url,
        daily_limit=payload.daily_limit,
        follow_up=payload.follow_up.model_dump(),
        status="draft",
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return campaign


@router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: int, db: Session = Depends(get_db)) -> dict:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="活动不存在")
    return {
        "campaign": CampaignOut.model_validate(campaign),
        "report": stats_service.campaign_report(db, campaign_id),
    }


@router.post("/campaigns/{campaign_id}/prepare")
def prepare(
    campaign_id: int,
    limit: int | None = Query(None, ge=1, le=100000, description="试跑时可只圈一小批"),
    db: Session = Depends(get_db),
) -> dict:
    """圈人并生成待发队列，此时不会真的发送。"""
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="活动不存在")
    if campaign.status == "running":
        raise HTTPException(status_code=400, detail="活动运行中，请先暂停再重新圈人")

    result = prepare_campaign(db, campaign, limit=limit)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    db.commit()
    return result


@router.post("/campaigns/{campaign_id}/start")
def start_campaign(campaign_id: int, db: Session = Depends(get_db)) -> dict:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="活动不存在")

    pending = db.scalar(
        select(func.count(SmsMessage.id)).where(
            SmsMessage.campaign_id == campaign_id, SmsMessage.status == "pending"
        )
    )
    if not pending:
        raise HTTPException(status_code=400, detail="待发队列为空，请先执行圈人")

    campaign.status = "running"
    campaign.started_at = campaign.started_at or utcnow()
    db.commit()
    return {"ok": True, "pending": pending, "message": "已启动，调度器将在发送时段内按节奏发出"}


@router.post("/campaigns/{campaign_id}/pause")
def pause_campaign(campaign_id: int, db: Session = Depends(get_db)) -> dict:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="活动不存在")
    campaign.status = "paused"
    db.commit()
    return {"ok": True}


@router.post("/campaigns/{campaign_id}/dispatch")
def dispatch_now(
    campaign_id: int,
    batch: int = Query(50, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict:
    """手动触发一批发送，用于小流量灰度验证。"""
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="活动不存在")
    if campaign.status != "running":
        raise HTTPException(status_code=400, detail="请先启动活动")
    sent = dispatch_campaign(campaign_id, batch_limit=batch)
    return {"ok": True, "sent": sent}


@router.post("/campaigns/follow-ups/run")
def run_follow_ups() -> dict:
    created = schedule_follow_ups()
    return {"ok": True, "created": created}


@router.get("/campaigns/{campaign_id}/report")
def campaign_report(campaign_id: int, db: Session = Depends(get_db)) -> dict:
    return stats_service.campaign_report(db, campaign_id)
