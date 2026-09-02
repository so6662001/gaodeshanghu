from app.utils import phone as p


def test_normalize_strips_noise():
    assert p.normalize("+86 138-0013-8000") == "13800138000"
    assert p.normalize("0571-88888888") == "057188888888"
    assert p.normalize("(0571)88888888转8001") == "057188888888"
    assert p.normalize("") == ""


def test_classify():
    assert p.classify("13800138000") == "mobile"
    assert p.classify("057188888888") == "landline"
    assert p.classify("4008008888") == "service"
    assert p.classify("12345") == "invalid"


def test_carrier():
    assert p.carrier_of("13800138000") == "china_mobile"
    assert p.carrier_of("13100131000") == "china_unicom"
    assert p.carrier_of("18900189000") == "china_telecom"
    assert p.carrier_of("057188888888") is None


def test_parse_tel_field_splits_multiple_numbers():
    """高德 tel 常见的座机 + 手机混排。"""
    result = p.parse_tel_field("0571-88888888;13800138000")
    assert [r["phone"] for r in result] == ["057188888888", "13800138000"]
    assert [r["phone_type"] for r in result] == ["landline", "mobile"]


def test_parse_tel_field_dedupes_and_filters_invalid():
    result = p.parse_tel_field("13800138000,13800138000,abc,123")
    assert len(result) == 1
    assert result[0]["phone"] == "13800138000"


def test_parse_tel_field_handles_empty():
    assert p.parse_tel_field(None) == []
    assert p.parse_tel_field("") == []


def test_mask():
    assert p.mask("13800138000") == "138****8000"
    assert p.mask("123") == "****"
