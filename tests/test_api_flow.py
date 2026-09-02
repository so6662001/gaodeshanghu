"""端到端跑一遍主链路：入库商户 -> 建模板 -> 建活动 -> 圈人 -> 发送 -> 点击 -> 转化。"""

from app.db import SessionLocal
from app.models import Merchant, MerchantPhone, SmsMessage
from app.services.amap import parse_poi
from app.services.collector import upsert_poi


def seed_merchants(count: int = 5) -> None:
    db = SessionLocal()
    try:
        for i in range(count):
            poi = parse_poi(
                {
                    "id": f"POI{i}",
                    "name": f"测试火锅{i}",
                    "location": "120.15,30.28",
                    "cityname": "杭州市",
                    "adname": "西湖区",
                    "typecode": "050101",
                    "type": "餐饮服务;火锅店",
                    "address": f"测试路{i}号",
                    "business": {"tel": f"1380013800{i}", "rating": "4.6", "cost": "88"},
                }
            )
            upsert_poi(db, poi, task_id=None, keyword="火锅")
        db.commit()
    finally:
        db.close()


def test_health(client):
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["dry_run"] is True


def test_auth_required(client):
    resp = client.get("/api/stats/overview", headers={"X-Api-Token": "wrong"})
    assert resp.status_code == 401


def test_upsert_dedupes_by_poi_id(client):
    seed_merchants(3)
    seed_merchants(3)
    db = SessionLocal()
    try:
        assert db.query(Merchant).count() == 3
        assert db.query(MerchantPhone).count() == 3
    finally:
        db.close()


def test_merchant_list_and_filters(client):
    seed_merchants(3)
    body = client.get("/api/merchants?has_mobile=true").json()
    assert body["total"] == 3
    assert body["items"][0]["grade"] in ("S", "A", "B", "C")

    options = client.get("/api/merchants/filters").json()
    assert "杭州市" in options["cities"]


def test_template_preview_warns_about_compliance(client):
    body = client.post(
        "/api/templates/preview", json={"content": "老板您好，免费领取", "sign": "【货袋子】"}
    ).json()
    assert "回T退订" in body["preview"]
    assert any("敏感词" in w for w in body["warnings"])


def test_full_campaign_flow(client):
    seed_merchants(5)

    template = client.post(
        "/api/templates",
        json={
            "name": "测试首触",
            "content": "{商家名}老板您好，货袋子{城市}仓已上线，看报价：{链接}",
            "sign": "【货袋子】",
            "with_link": True,
            "landing_url": "https://m.huodaizi.com/reg",
        },
    ).json()

    campaign = client.post(
        "/api/campaigns",
        json={
            "name": "杭州餐饮首轮",
            "goal": "拉新",
            "audience": {"city": "杭州市", "never_touched": True},
            "variants": [{"template_id": template["id"], "weight": 1, "label": "A"}],
            "landing_url": "https://m.huodaizi.com/reg",
            "daily_limit": 100,
        },
    ).json()

    prepared = client.post(f"/api/campaigns/{campaign['id']}/prepare").json()
    assert prepared["created"] == 5
    assert prepared["billing_count"] >= 5

    started = client.post(f"/api/campaigns/{campaign['id']}/start").json()
    assert started["pending"] == 5

    sent = client.post(f"/api/campaigns/{campaign['id']}/dispatch?batch=10").json()
    assert sent["sent"] == 5

    db = SessionLocal()
    try:
        message = db.query(SmsMessage).filter(SmsMessage.status == "sent").first()
        assert message is not None
        # 内容必须已渲染成真实门店名，且带上专属短链
        assert "测试火锅" in message.content
        assert message.link_code
        assert "{" not in message.content
        merchant = db.get(Merchant, message.merchant_id)
        assert merchant.lifecycle == "reached"
        assert merchant.touch_count == 1
        link_code = message.link_code
        message_id = message.id
    finally:
        db.close()

    # 商户点开短链：应完成归因并推进生命周期
    resp = client.get(f"/s/{link_code}", follow_redirects=False)
    assert resp.status_code == 302
    assert "hdz_src=sms" in resp.headers["location"]

    db = SessionLocal()
    try:
        message = db.get(SmsMessage, message_id)
        assert message.clicked_at is not None
        assert db.get(Merchant, message.merchant_id).lifecycle == "engaged"
        phone = message.phone
    finally:
        db.close()

    # 主站回传注册转化
    conversion = client.post(
        "/callback/conversion",
        json={"phone": phone, "event_type": "register", "amount": 0},
    ).json()
    assert conversion["ok"] is True

    report = client.get(f"/api/campaigns/{campaign['id']}/report").json()
    assert report["variants"][0]["sent"] == 5
    stages = {s["stage"]: s["value"] for s in report["funnel"]}
    assert stages["已发送"] == 5
    assert stages["点击落地页"] == 1
    assert stages["注册开户"] == 1


def test_frequency_control_blocks_second_campaign(client):
    """同一批商户短时间内再建活动，必须被频控全部拦下。"""
    seed_merchants(3)
    template = client.post(
        "/api/templates", json={"name": "T", "content": "{商家名}你好", "with_link": False}
    ).json()

    def make_campaign(name):
        campaign = client.post(
            "/api/campaigns",
            json={"name": name, "audience": {"city": "杭州市"},
                  "variants": [{"template_id": template["id"], "weight": 1, "label": "A"}]},
        ).json()
        client.post(f"/api/campaigns/{campaign['id']}/prepare")
        client.post(f"/api/campaigns/{campaign['id']}/start")
        client.post(f"/api/campaigns/{campaign['id']}/dispatch?batch=10")
        return campaign

    make_campaign("第一轮")
    second = client.post(
        "/api/campaigns",
        json={"name": "第二轮", "audience": {"city": "杭州市"},
              "variants": [{"template_id": template["id"], "weight": 1, "label": "A"}]},
    ).json()
    prepared = client.post(f"/api/campaigns/{second['id']}/prepare").json()
    assert prepared["created"] == 0
    assert prepared["skip_detail"].get("too_frequent") == 3


def test_unsubscribe_callback_blocks_future_sends(client):
    """回复 TD 后必须立刻进黑名单，后续活动一条都发不出去。"""
    seed_merchants(2)
    client.post(
        "/callback/sms/reply",
        json=[{"mobile": "13800138000", "msg": "TD", "time": "2026-03-05 10:00:00"}],
    )
    blacklist = client.get("/api/sms/blacklist").json()
    assert blacklist["total"] == 1

    template = client.post(
        "/api/templates", json={"name": "T2", "content": "{商家名}你好", "with_link": False}
    ).json()
    campaign = client.post(
        "/api/campaigns",
        json={"name": "退订验证", "audience": {"city": "杭州市"},
              "variants": [{"template_id": template["id"], "weight": 1, "label": "A"}]},
    ).json()
    prepared = client.post(f"/api/campaigns/{campaign['id']}/prepare").json()
    assert prepared["skip_detail"].get("blacklist") == 1
    assert prepared["created"] == 1


def test_delivery_report_marks_invalid_number(client):
    """空号回执要把号码作废，避免后续活动继续烧额度。"""
    seed_merchants(1)
    template = client.post(
        "/api/templates", json={"name": "T3", "content": "{商家名}你好", "with_link": False}
    ).json()
    campaign = client.post(
        "/api/campaigns",
        json={"name": "回执验证", "audience": {"city": "杭州市"},
              "variants": [{"template_id": template["id"], "weight": 1, "label": "A"}]},
    ).json()
    client.post(f"/api/campaigns/{campaign['id']}/prepare")
    client.post(f"/api/campaigns/{campaign['id']}/start")
    client.post(f"/api/campaigns/{campaign['id']}/dispatch?batch=10")

    client.post(
        "/callback/sms/report",
        json=[{"mobile": "13800138000", "statusCode": "UNDELIV",
               "reportTime": "2026-03-05 10:01:00"}],
    )

    db = SessionLocal()
    try:
        phone = db.query(MerchantPhone).filter(MerchantPhone.phone == "13800138000").first()
        assert phone.is_valid is False
    finally:
        db.close()


def test_test_send_endpoint(client):
    body = client.post(
        "/api/sms/test-send", json={"phone": "13800138000", "content": "测试"}
    ).json()
    assert body["ok"] and body["dry_run"]
    assert body["content"].endswith("回T退订")


def test_stats_overview(client):
    seed_merchants(4)
    body = client.get("/api/stats/overview").json()
    assert body["merchants"] == 4
    assert body["reachable"] == 4
    assert body["reachable_rate"] == 100.0


def test_duplicate_unsubscribe_in_same_batch(client):
    """同一批回调里同号码多条退订：不能报错，且只算一个退订号码。"""
    seed_merchants(2)
    body = client.post(
        "/callback/sms/reply",
        json=[
            {"mobile": "13800138000", "msg": "TD"},
            {"mobile": "13800138000", "msg": "退订"},
            {"mobile": "13800138001", "msg": "米面油怎么报价"},
        ],
    ).json()
    assert body["handled"] == 3
    assert body["unsubscribed"] == 1
    assert client.get("/api/sms/blacklist").json()["total"] == 1

    replies = client.get("/api/sms/replies?intent=interested").json()
    assert replies["total"] == 1
