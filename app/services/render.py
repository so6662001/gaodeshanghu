"""短信内容渲染。

模板用中文占位符，运营同学不用学语法：
    【货袋子】{商家名}老板您好，{城市}{品类}同行都在用货袋子直采...{链接}

个性化不是花架子。短信第一句带上门店真名，打开率和回复率会明显高于通用文案，
这也是美团 BD 触达一直强调的「让商户觉得这条信息是给他一个人发的」。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.models import Merchant
from app.services.compliance import to_cn
from app.models import utcnow

VARIABLE_PATTERN = re.compile(r"\{([^{}]+)\}")

# 占位符 -> 说明，前端做模板编辑提示用
SUPPORTED_VARIABLES = {
    "商家名": "商户名称，如「老张火锅」",
    "简称": "去掉括号门店后缀的品牌名，如「老张火锅（西湖店）」-> 「老张火锅」",
    "城市": "所在城市",
    "区县": "所在区县",
    "商圈": "所在商圈，无数据时回退为区县",
    "品类": "一级类目，如餐饮/零售",
    "链接": "专属追踪短链，点击可归因到该商户",
    "日期": "当天日期，如 3月5日",
}


def brand_of(name: str) -> str:
    """「老张火锅（西湖店）」-> 「老张火锅」，短信里叫品牌名更自然。"""
    return re.split(r"[（(]", name or "")[0].strip() or name


def extract_variables(content: str) -> list[str]:
    return list(dict.fromkeys(VARIABLE_PATTERN.findall(content or "")))


def build_context(
    merchant: Merchant | None, link: str | None = None, now: datetime | None = None
) -> dict[str, str]:
    now_cn = to_cn(now or utcnow())
    if merchant is None:
        return {
            "商家名": "示例商户", "简称": "示例", "城市": "杭州", "区县": "西湖区",
            "商圈": "文三路", "品类": "餐饮", "链接": link or "", 
            "日期": f"{now_cn.month}月{now_cn.day}日",
        }
    return {
        "商家名": merchant.name or "",
        "简称": brand_of(merchant.name or ""),
        "城市": merchant.city or "",
        "区县": merchant.district or "",
        "商圈": merchant.business_area or merchant.district or "",
        "品类": merchant.category or "",
        "链接": link or "",
        "日期": f"{now_cn.month}月{now_cn.day}日",
    }


def render(content: str, context: dict[str, Any]) -> str:
    """未知占位符原样保留，避免运营写错字直接把内容渲染成空白。"""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        if key in context:
            return str(context[key] or "")
        return match.group(0)

    return VARIABLE_PATTERN.sub(replace, content or "")


def render_for_merchant(
    content: str, merchant: Merchant | None, link: str | None = None
) -> str:
    return render(content, build_context(merchant, link))
