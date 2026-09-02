from app.models import Merchant
from app.services.campaign import pick_variant, to_variable_template
from app.services.render import brand_of, extract_variables, render, render_for_merchant
from app.services.sms_chuanglan import (
    ChuanglanClient,
    billing_count,
    build_content,
    describe_code,
)


def test_extract_and_render_variables():
    content = "{商家名}老板您好，{城市}的同行都在用，{链接}"
    assert extract_variables(content) == ["商家名", "城市", "链接"]
    rendered = render(content, {"商家名": "老张火锅", "城市": "杭州", "链接": "https://a.cn/s/x"})
    assert rendered == "老张火锅老板您好，杭州的同行都在用，https://a.cn/s/x"


def test_render_keeps_unknown_placeholder():
    """运营写错变量名时保留原样，不要渲染成空白导致文案缺字。"""
    assert render("你好{不存在}", {}) == "你好{不存在}"


def test_brand_of_strips_store_suffix():
    assert brand_of("老乡鸡（西湖店）") == "老乡鸡"
    assert brand_of("老乡鸡") == "老乡鸡"


def test_render_for_merchant():
    merchant = Merchant(name="老张火锅（西湖店）", city="杭州", district="西湖区", category="餐饮")
    result = render_for_merchant("{简称}在{城市}{区县}做{品类}", merchant)
    assert result == "老张火锅在杭州西湖区做餐饮"


def test_build_content_adds_sign_and_unsubscribe():
    content = build_content("测试内容", sign="【货袋子】")
    assert content.startswith("【货袋子】")
    assert "回T退订" in content


def test_build_content_does_not_duplicate():
    once = build_content("测试", sign="【货袋子】")
    twice = build_content(once, sign="【货袋子】")
    assert once == twice


def test_build_content_respects_existing_sign():
    content = build_content("【其他签名】内容", sign="【货袋子】")
    assert content.startswith("【其他签名】")


def test_billing_count():
    assert billing_count("") == 0
    assert billing_count("短" * 70) == 1
    assert billing_count("短" * 71) == 2
    assert billing_count("短" * 134) == 2
    assert billing_count("短" * 135) == 3


def test_describe_code():
    assert describe_code("0")[0] == "提交成功"
    assert describe_code("103")[1] is True   # 提交过快，可重试
    assert describe_code("102")[1] is False  # 密码错误，重试无用


def test_to_variable_template():
    """转成创蓝变量格式后，占位符顺序必须与取值顺序严格对应。"""
    template, names = to_variable_template("{商家名}老板，{城市}上新，{链接}")
    assert template == "{$var}老板，{$var}上新，{$var}"
    assert names == ["商家名", "城市", "链接"]


def test_to_variable_template_repeated_placeholder():
    template, names = to_variable_template("{简称}好，{简称}加油")
    assert template == "{$var}好，{$var}加油"
    assert names == ["简称", "简称"]


def test_pick_variant_is_stable():
    """同一商户必须永远落在同一版文案，否则二次触达文案会跳变。"""
    variants = [{"template_id": 1, "weight": 1, "label": "A"},
                {"template_id": 2, "weight": 1, "label": "B"}]
    assert pick_variant(variants, 42) == pick_variant(variants, 42)


def test_pick_variant_respects_weight():
    variants = [{"template_id": 1, "weight": 9, "label": "A"},
                {"template_id": 2, "weight": 1, "label": "B"}]
    labels = [pick_variant(variants, i)["label"] for i in range(300)]
    assert labels.count("A") > labels.count("B") * 3


def test_dry_run_never_calls_provider():
    """演练模式下必须完全不碰外部接口。"""
    client = ChuanglanClient(account="", password="", dry_run=True)
    result = client.send(["13800138000"], "测试")
    assert result.ok and result.dry_run


def test_send_with_empty_phones():
    client = ChuanglanClient(dry_run=True)
    assert not client.send([], "内容").ok
