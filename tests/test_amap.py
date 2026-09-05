from app.services.amap import BoundingBox, parse_poi
from app.services.scoring import grade_of, is_chain_store, resolve_category, score_poi


def test_parse_poi_v5_structure():
    """v5 把 tel/rating 收在 business 子对象里。"""
    poi = parse_poi(
        {
            "id": "B001",
            "name": "老张火锅（西湖店）",
            "location": "120.15,30.28",
            "pname": "浙江省",
            "cityname": "杭州市",
            "adname": "西湖区",
            "typecode": "050101",
            "type": "餐饮服务;中餐厅;火锅店",
            "address": "文三路100号",
            "business": {
                "tel": "0571-88888888;13800138000",
                "rating": "4.6",
                "cost": "88",
                "business_area": "文三路",
                "opentime_today": "10:00-22:00",
            },
        }
    )
    assert poi.name == "老张火锅（西湖店）"
    assert poi.lng == 120.15 and poi.lat == 30.28
    assert poi.tel == "0571-88888888;13800138000"
    assert poi.rating == 4.6
    assert poi.business_area == "文三路"


def test_parse_poi_v3_flat_structure():
    poi = parse_poi(
        {"id": "B002", "name": "便利店", "tel": "13900139000", "typecode": "060400"}
    )
    assert poi.tel == "13900139000"
    assert poi.typecode == "060400"


def test_parse_poi_handles_empty_list_values():
    """高德对空值有时返回 [] 而不是 null。"""
    poi = parse_poi({"id": "B003", "name": "某店", "address": [], "business": {"tel": []}})
    assert poi.address is None
    assert poi.tel is None


def test_parse_poi_skips_nameless():
    assert parse_poi({"id": "x", "name": ""}) is None


def test_bounding_box_split_and_polygon():
    box = BoundingBox(120.0, 30.0, 120.4, 30.4)
    cells = box.split(2, 2)
    assert len(cells) == 4
    # 矩形写法是「左上角|右下角」
    assert cells[0].as_polygon() == "120.000000,30.200000|120.200000,30.000000"


def test_resolve_category():
    assert resolve_category("050101", None)[0] == "餐饮"
    assert resolve_category("060400", None)[0] == "零售"
    assert resolve_category(None, None)[0] == "其他"


def test_is_chain_store():
    assert is_chain_store("老乡鸡（西湖店）")
    assert not is_chain_store("老乡鸡")


def test_score_prioritises_reachable_merchants():
    """有手机号的商户必须排在只有座机的前面 —— 短信增长的前提是能触达。"""
    with_mobile = parse_poi(
        {"id": "1", "name": "A火锅", "typecode": "050101",
         "business": {"tel": "13800138000", "rating": "4.6", "cost": "88"}}
    )
    landline_only = parse_poi(
        {"id": "2", "name": "B火锅", "typecode": "050101",
         "business": {"tel": "0571-88888888", "rating": "4.6", "cost": "88"}}
    )
    assert score_poi(with_mobile)[0] > score_poi(landline_only)[0]


def test_score_no_contact_is_low():
    poi = parse_poi({"id": "3", "name": "无号商户", "typecode": "120000"})
    score, grade, tags = score_poi(poi)
    assert grade == "C"
    assert "无联系方式" in tags


def test_grade_thresholds():
    assert grade_of(85) == "S"
    assert grade_of(70) == "A"
    assert grade_of(50) == "B"
    assert grade_of(10) == "C"
