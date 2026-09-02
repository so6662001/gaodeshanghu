"""商户分层与打分。

借鉴美团 BD 的「商户分级」思路：不是所有商户都值得同等力度触达。
把有限的短信预算和 BD 人力压在高价值池上，ROI 会有数量级差异。

打分维度（满分 100）：
    可触达性 35  —— 有手机号才谈得上短信增长，座机只能转电销
    品类匹配 25  —— 货袋子是食材/货源供应链，餐饮与零售门店才是真买家
    经营体量 20  —— 人均消费、连锁与否，直接决定采购规模
    活跃质量 20  —— 评分、营业时间、信息完整度，反映门店是否在正常经营

分级：S >= 80，A >= 65，B >= 45，其余 C。
"""

from __future__ import annotations

from app.services.amap import Poi
from app.utils import phone as phone_utils

# 高德 typecode 大类 -> (一级类目, 与货袋子的品类匹配分)
CATEGORY_MAP: dict[str, tuple[str, int]] = {
    "05": ("餐饮", 25),
    "06": ("零售", 22),
    "10": ("住宿", 16),
    "07": ("生活服务", 12),
    "08": ("休闲娱乐", 12),
    "09": ("医疗", 6),
    "14": ("科教文化", 8),
    "17": ("企业", 8),
    "12": ("商务住宅", 4),
    "13": ("机构团体", 6),
}

# 细分品类加权：这些是食材采购频次最高的业态
HIGH_VALUE_KEYWORDS = (
    "火锅", "烧烤", "中餐", "快餐", "自助餐", "食堂", "餐厅", "酒楼", "饭店",
    "生鲜", "果蔬", "水果", "菜市场", "超市", "便利店", "食品", "农贸",
    "面馆", "小吃", "烘焙", "蛋糕", "茶饮", "咖啡", "酒店",
)

CHAIN_HINTS = ("店", "分店", "连锁")


def resolve_category(typecode: str | None, type_name: str | None) -> tuple[str, int]:
    """把高德 typecode 归一到平台一级类目。"""
    if typecode:
        prefix = typecode.strip()[:2]
        if prefix in CATEGORY_MAP:
            return CATEGORY_MAP[prefix]
    if type_name:
        for key, (name, weight) in CATEGORY_MAP.items():  # noqa: B007
            if name in type_name:
                return name, weight
    return "其他", 5


def is_chain_store(name: str) -> bool:
    """带括号门店后缀的通常是连锁分店，如「老乡鸡（西湖店）」。"""
    if "(" in name or "（" in name:
        tail = name.replace("（", "(").split("(")[-1]
        return any(hint in tail for hint in CHAIN_HINTS)
    return False


def score_poi(poi: Poi, phones: list[dict] | None = None) -> tuple[int, str, list[str]]:
    """返回 (分数, 等级, 标签)。"""
    phones = phones if phones is not None else phone_utils.parse_tel_field(poi.tel)
    tags: list[str] = []
    score = 0

    # 1) 可触达性
    has_mobile = any(p["phone_type"] == "mobile" for p in phones)
    has_landline = any(p["phone_type"] in ("landline", "service") for p in phones)
    if has_mobile:
        score += 35
        tags.append("可短信触达")
    elif has_landline:
        score += 12
        tags.append("仅座机")
    else:
        tags.append("无联系方式")

    # 2) 品类匹配
    category, category_score = resolve_category(poi.typecode, poi.type_name)
    score += category_score
    tags.append(category)
    haystack = f"{poi.name}{poi.type_name or ''}"
    if any(word in haystack for word in HIGH_VALUE_KEYWORDS):
        score += 6
        tags.append("核心业态")

    # 3) 经营体量
    if poi.cost:
        if poi.cost >= 100:
            score += 12
        elif poi.cost >= 50:
            score += 9
        elif poi.cost >= 20:
            score += 6
        else:
            score += 3
    if is_chain_store(poi.name):
        score += 8
        tags.append("连锁门店")
    if poi.business_area:
        score += 3

    # 4) 活跃质量
    if poi.rating:
        if poi.rating >= 4.5:
            score += 12
        elif poi.rating >= 4.0:
            score += 9
        elif poi.rating >= 3.5:
            score += 5
        else:
            score += 2
        tags.append(f"评分{poi.rating}")
    if poi.open_time:
        score += 4
    if poi.address:
        score += 2
    if poi.lng and poi.lat:
        score += 2

    score = max(0, min(score, 100))
    return score, grade_of(score), tags


def grade_of(score: int) -> str:
    if score >= 80:
        return "S"
    if score >= 65:
        return "A"
    if score >= 45:
        return "B"
    return "C"
