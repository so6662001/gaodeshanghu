"""灌入演示数据，用于在没有高德 Key / 创蓝账号时先跑通全流程。

用法：
    python3 scripts/seed_demo.py

会生成 120 家模拟商户、一个跑完的触达活动，以及点击与转化事件，
这样打开看板就能看到完整的漏斗形态。
"""

from __future__ import annotations

import random
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db, session_scope  # noqa: E402
from app.models import (  # noqa: E402
    Campaign,
    ClickEvent,
    ConversionEvent,
    Merchant,
    SmsMessage,
    SmsReply,
    SmsTemplate,
    utcnow,
)
from app.services.amap import parse_poi  # noqa: E402
from app.services.collector import upsert_poi  # noqa: E402
from app.services.render import build_context, render  # noqa: E402
from app.services.shortlink import create_link, link_url  # noqa: E402
from app.services.sms_chuanglan import billing_count, build_content  # noqa: E402

random.seed(20260305)

BRANDS = [
    ("老张火锅", "050101", "餐饮", 88),
    ("蜀香川菜馆", "050102", "餐饮", 65),
    ("阿宽烧烤", "050300", "餐饮", 55),
    ("鲜果多水果店", "060400", "零售", 0),
    ("邻里生鲜超市", "060401", "零售", 0),
    ("晨光早餐铺", "050200", "餐饮", 15),
    ("味知香快餐", "050200", "餐饮", 25),
    ("云顶酒楼", "050101", "餐饮", 180),
    ("好味道面馆", "050200", "餐饮", 20),
    ("百家便利店", "060400", "零售", 0),
    ("食堂承包中心", "050000", "餐饮", 18),
    ("甜心烘焙坊", "050500", "餐饮", 35),
]
CITIES = [
    ("杭州市", "西湖区", "文三路", "330106"),
    ("杭州市", "拱墅区", "武林商圈", "330105"),
    ("杭州市", "余杭区", "未来科技城", "330110"),
    ("宁波市", "海曙区", "天一广场", "330203"),
    ("温州市", "鹿城区", "五马街", "330302"),
]


def make_merchants(db, count: int = 120) -> None:
    for i in range(count):
        brand, typecode, _category, cost = random.choice(BRANDS)
        city, district, area, adcode = random.choice(CITIES)
        # 七成商户挂手机号，接近真实的高德数据情况
        if random.random() < 0.7:
            tel = f"1{random.choice('35789')}{random.randint(10**8, 10**9 - 1)}"
            if random.random() < 0.3:
                tel = f"0571-8{random.randint(1000000, 9999999)};{tel}"
        else:
            tel = f"0571-8{random.randint(1000000, 9999999)}"

        poi = parse_poi(
            {
                "id": f"DEMO{i:04d}",
                "name": f"{brand}（{area}{i % 9 + 1}店）" if i % 3 == 0 else f"{brand}{i}",
                "location": f"{120.0 + random.random():.6f},{30.0 + random.random():.6f}",
                "pname": "浙江省",
                "cityname": city,
                "adname": district,
                "adcode": adcode,
                "typecode": typecode,
                "type": "餐饮服务;中餐厅",
                "address": f"{district}{area}{random.randint(1, 300)}号",
                "business": {
                    "tel": tel,
                    "rating": f"{random.uniform(3.2, 4.9):.1f}",
                    "cost": str(cost) if cost else "",
                    "business_area": area,
                    "opentime_today": "09:00-21:00",
                },
            }
        )
        upsert_poi(db, poi, task_id=None, keyword=brand[:2])


def make_campaign(db) -> None:
    templates = db.query(SmsTemplate).limit(2).all()
    if len(templates) < 2:
        return

    campaign = Campaign(
        name="杭州餐饮首轮拉新（演示）",
        goal="验证文案与通道，跑通首批 80 家商户注册转化",
        audience={"city": "杭州市", "grades": ["S", "A"], "never_touched": True},
        variants=[
            {"template_id": templates[0].id, "weight": 1, "label": "A-省成本"},
            {"template_id": templates[1].id, "weight": 1, "label": "B-首单立减"},
        ],
        landing_url="https://m.huodaizi.com/reg",
        daily_limit=2000,
        status="finished",
        started_at=utcnow() - timedelta(days=6),
        finished_at=utcnow() - timedelta(days=5),
    )
    db.add(campaign)
    db.flush()

    merchants = (
        db.query(Merchant)
        .filter(Merchant.city == "杭州市", Merchant.grade.in_(("S", "A")))
        .limit(80)
        .all()
    )

    sent_count = 0
    for index, merchant in enumerate(merchants):
        phone = merchant.primary_phone
        if not phone:
            continue

        variant = campaign.variants[index % 2]
        template = next(t for t in templates if t.id == variant["template_id"])
        sent_at = utcnow() - timedelta(days=6, minutes=index * 3)

        message = SmsMessage(
            campaign_id=campaign.id,
            merchant_id=merchant.id,
            template_id=template.id,
            variant=variant["label"],
            phone=phone,
            content="",
            status="delivered" if random.random() > 0.06 else "failed",
            round_no=1,
            scheduled_at=sent_at,
            sent_at=sent_at,
        )
        db.add(message)
        db.flush()

        link = create_link(
            db,
            target_url=campaign.landing_url,
            merchant_id=merchant.id,
            campaign_id=campaign.id,
            message_id=message.id,
        )
        message.link_code = link.code
        raw = build_content(template.content, template.sign)
        message.content = render(raw, build_context(merchant, link=link_url(link.code)))
        message.fee_count = billing_count(message.content)

        if message.status == "delivered":
            message.delivered_at = sent_at + timedelta(minutes=1)
            sent_count += 1
            merchant.touch_count = 1
            merchant.last_touch_at = sent_at
            merchant.lifecycle = "reached"

            # A 版文案点击率略高，让 A/B 报表能看出差异
            click_rate = 0.14 if variant["label"].startswith("A") else 0.09
            if random.random() < click_rate:
                clicked_at = sent_at + timedelta(hours=random.randint(1, 20))
                message.clicked_at = clicked_at
                link.clicks = 1
                link.first_click_at = link.last_click_at = clicked_at
                merchant.lifecycle = "engaged"
                merchant.last_click_at = clicked_at
                db.add(
                    ClickEvent(
                        code=link.code,
                        merchant_id=merchant.id,
                        campaign_id=campaign.id,
                        ip="127.0.0.1",
                        ua="Mozilla/5.0 (iPhone)",
                        created_at=clicked_at,
                    )
                )

                if random.random() < 0.45:
                    merchant.lifecycle = "registered"
                    db.add(
                        ConversionEvent(
                            merchant_id=merchant.id,
                            phone=phone,
                            campaign_id=campaign.id,
                            event_type="register",
                            occurred_at=clicked_at + timedelta(minutes=20),
                        )
                    )
                    if random.random() < 0.5:
                        merchant.lifecycle = "activated"
                        db.add(
                            ConversionEvent(
                                merchant_id=merchant.id,
                                phone=phone,
                                campaign_id=campaign.id,
                                event_type="first_order",
                                amount=round(random.uniform(800, 6500), 2),
                                occurred_at=clicked_at + timedelta(days=1),
                            )
                        )

            # 少量回复：退订与意向
            roll = random.random()
            if roll < 0.03:
                db.add(SmsReply(phone=phone, content="TD", intent="unsubscribe",
                                merchant_id=merchant.id, campaign_id=campaign.id))
            elif roll < 0.07:
                db.add(SmsReply(phone=phone, content="米面油怎么报价", intent="interested",
                                merchant_id=merchant.id, campaign_id=campaign.id))
                merchant.lifecycle = "intent"

    campaign.total_targets = sent_count
    print(f"演示活动已生成，发送 {sent_count} 条")


def main() -> None:
    init_db()
    from app.main import seed_default_templates

    seed_default_templates()

    with session_scope() as db:
        if db.query(Merchant).count():
            print("商户库已有数据，跳过灌入。如需重来请删除 data/huodaizi.db")
            return
        make_merchants(db)
        db.flush()
        print(f"已生成 {db.query(Merchant).count()} 家演示商户")
        make_campaign(db)

    print("完成。启动服务后打开 http://127.0.0.1:8000 查看")


if __name__ == "__main__":
    main()
