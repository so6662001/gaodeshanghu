from datetime import timedelta

from app.db import SessionLocal
from app.models import Campaign, SmsMessage, SmsTemplate, utcnow
from app.services.dispatcher import dispatch_campaign, reclaim_stale_leases


def _setup_campaign(db, count: int = 4) -> int:
    template = SmsTemplate(name="T", content="{商家名}你好", sign="【货袋子】", with_link=False)
    db.add(template)
    db.flush()
    campaign = Campaign(
        name="并发验证",
        variants=[{"template_id": template.id, "weight": 1, "label": "A"}],
        status="running",
        daily_limit=1000,
    )
    db.add(campaign)
    db.flush()
    for i in range(count):
        db.add(
            SmsMessage(
                campaign_id=campaign.id,
                template_id=template.id,
                variant="A",
                phone=f"1380013800{i}",
                content=f"【货袋子】商户{i}你好 回T退订",
                vars=[f"商户{i}"],
                status="pending",
                scheduled_at=utcnow(),
            )
        )
    db.commit()
    return campaign.id


def test_dispatch_sends_pending_messages(db):
    campaign_id = _setup_campaign(db, 4)
    assert dispatch_campaign(campaign_id, batch_limit=10) == 4

    session = SessionLocal()
    try:
        assert session.query(SmsMessage).filter(SmsMessage.status == "sent").count() == 4
        # 租约必须在发送完成后释放
        assert session.query(SmsMessage).filter(SmsMessage.lease.isnot(None)).count() == 0
    finally:
        session.close()


def test_dispatch_is_idempotent(db):
    """第二次调度不应重复发送已发出的消息。"""
    campaign_id = _setup_campaign(db, 3)
    assert dispatch_campaign(campaign_id, batch_limit=10) == 3
    assert dispatch_campaign(campaign_id, batch_limit=10) == 0


def test_dispatch_respects_daily_limit(db):
    campaign_id = _setup_campaign(db, 5)
    session = SessionLocal()
    try:
        campaign = session.get(Campaign, campaign_id)
        campaign.daily_limit = 2
        session.commit()
    finally:
        session.close()

    assert dispatch_campaign(campaign_id, batch_limit=10) == 2
    assert dispatch_campaign(campaign_id, batch_limit=10) == 0


def test_campaign_auto_finishes_when_queue_empty(db):
    campaign_id = _setup_campaign(db, 2)
    dispatch_campaign(campaign_id, batch_limit=10)
    dispatch_campaign(campaign_id, batch_limit=10)

    session = SessionLocal()
    try:
        assert session.get(Campaign, campaign_id).status == "finished"
    finally:
        session.close()


def test_reclaim_stale_lease(db):
    """进程崩溃导致消息卡在 sending，超时后必须能回收重发。"""
    campaign_id = _setup_campaign(db, 2)
    session = SessionLocal()
    try:
        session.query(SmsMessage).update(
            {
                SmsMessage.status: "sending",
                SmsMessage.lease: "dead-worker",
                SmsMessage.leased_at: utcnow() - timedelta(minutes=30),
            },
            synchronize_session=False,
        )
        session.commit()
    finally:
        session.close()

    assert reclaim_stale_leases() == 2
    assert dispatch_campaign(campaign_id, batch_limit=10) == 2


def test_fresh_lease_is_not_reclaimed(db):
    """刚抢占的租约不能被误回收，否则会造成重复发送。"""
    _setup_campaign(db, 2)
    session = SessionLocal()
    try:
        session.query(SmsMessage).update(
            {
                SmsMessage.status: "sending",
                SmsMessage.lease: "live-worker",
                SmsMessage.leased_at: utcnow(),
            },
            synchronize_session=False,
        )
        session.commit()
    finally:
        session.close()

    assert reclaim_stale_leases() == 0
