"""号码解析与清洗。

高德返回的 tel 字段格式很杂，常见形态：
    "0571-88888888"                          纯座机
    "0571-88888888;13800138000"              座机 + 手机
    "13800138000,13900139000"                多个手机
    "400-800-8888"                           全国服务号
    "(0571)88888888 转 8001"                 带分机
只有手机号能被短信触达，座机需要转给电销/BD，因此必须拆分并打标。
"""

from __future__ import annotations

import re

SPLIT_PATTERN = re.compile(r"[;,；，、\s/|]+")
MOBILE_PATTERN = re.compile(r"^1[3-9]\d{9}$")
SERVICE_PATTERN = re.compile(r"^(400|800)\d{7}$")
LANDLINE_PATTERN = re.compile(r"^0\d{9,11}$")

# 号段 -> 运营商，用于估算送达率与分渠道通道
_CARRIER_PREFIX = {
    "china_mobile": {
        "134", "135", "136", "137", "138", "139", "147", "148", "150", "151",
        "152", "157", "158", "159", "165", "172", "178", "182", "183", "184",
        "187", "188", "195", "197", "198",
    },
    "china_unicom": {
        "130", "131", "132", "145", "146", "155", "156", "166", "167", "175",
        "176", "185", "186", "196",
    },
    "china_telecom": {
        "133", "149", "153", "162", "173", "174", "177", "180", "181", "189",
        "190", "191", "193", "199",
    },
    "virtual": {"170", "171"},
}
_PREFIX_TO_CARRIER = {
    prefix: carrier for carrier, prefixes in _CARRIER_PREFIX.items() for prefix in prefixes
}


def normalize(raw: str) -> str:
    """去掉国际区号、分隔符、分机等噪声，得到纯数字号码。"""
    if not raw:
        return ""
    text = str(raw).strip()
    # 去掉分机部分："88888888转8001" / "88888888-8001"
    text = re.split(r"转|ext|EXT", text)[0]
    text = re.sub(r"[^\d+]", "", text)
    if text.startswith("+86"):
        text = text[3:]
    elif text.startswith("0086"):
        text = text[4:]
    elif text.startswith("86") and len(text) == 13:
        text = text[2:]
    return text.lstrip("+")


def classify(phone: str) -> str:
    """返回 mobile / landline / service / invalid。"""
    if MOBILE_PATTERN.match(phone):
        return "mobile"
    if SERVICE_PATTERN.match(phone.replace("-", "")):
        return "service"
    if LANDLINE_PATTERN.match(phone):
        return "landline"
    # 不带区号的本地座机，7~8 位
    if phone.isdigit() and 7 <= len(phone) <= 8:
        return "landline"
    return "invalid"


def carrier_of(phone: str) -> str | None:
    if not MOBILE_PATTERN.match(phone):
        return None
    return _PREFIX_TO_CARRIER.get(phone[:3])


def is_mobile(phone: str) -> bool:
    return bool(MOBILE_PATTERN.match(normalize(phone)))


def parse_tel_field(raw: str | None) -> list[dict]:
    """把高德 tel 原始串拆成结构化号码列表，保持出现顺序并去重。

    返回 [{"phone": "13800138000", "phone_type": "mobile", "carrier": "china_mobile"}, ...]
    """
    if not raw:
        return []

    results: list[dict] = []
    seen: set[str] = set()
    for chunk in SPLIT_PATTERN.split(str(raw)):
        if not chunk:
            continue
        # 形如 0571-88888888 的整体保留后再归一
        phone = normalize(chunk)
        if not phone or phone in seen:
            continue
        kind = classify(phone)
        if kind == "invalid":
            continue
        seen.add(phone)
        results.append(
            {"phone": phone, "phone_type": kind, "carrier": carrier_of(phone)}
        )
    return results


def mask(phone: str) -> str:
    """导出与日志展示用的脱敏格式。"""
    if len(phone) == 11:
        return f"{phone[:3]}****{phone[7:]}"
    if len(phone) > 6:
        return f"{phone[:3]}****{phone[-2:]}"
    return "****"
