"""高德开放平台 Web 服务客户端。

覆盖三种拿商户的方式：
1. 关键字搜索  /v5/place/text     —— 最常用，"关键词 + 城市"
2. 周边搜索    /v5/place/around   —— 圈定某个商圈/写字楼周边扫街
3. 多边形搜索  /v5/place/polygon  —— 网格化用，突破单次 1000 条上限

高德对单个查询条件最多只返回前 1000 条（page_num * page_size <= 1000）。
想把一个城市的某个品类扫全，必须做空间切分：先用行政区接口拿到城市外接矩形，
再切成 N×N 网格逐格搜索。这是把「关键词采集」做到规模化的关键。
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx

from app.config import settings
from app.utils.ratelimit import RateLimiter

logger = logging.getLogger(__name__)

AMAP_HOST = "https://restapi.amap.com"
PAGE_SIZE = 25
MAX_PAGE_NUM = 40  # 25 * 40 = 1000，高德硬上限
SHOW_FIELDS = "business,children,photos,indoor,navi"

# 高德错误码 -> 人话，运营同学看到能自己判断
INFOCODE_HINTS = {
    "10001": "AMAP_KEY 不正确或已过期",
    "10002": "没有权限使用该接口，请确认 Key 的服务平台选择了「Web服务」",
    "10003": "今日调用量已达上限，请次日再试或升级配额",
    "10004": "单位时间内访问过快，已自动降速重试",
    "10009": "请求 Key 与绑定平台不符（多为选成了 Web端(JS API)）",
    "10012": "权限不足，服务请求被拒绝",
    "10044": "账号维度日调用量超限",
    "20000": "请求参数非法",
    "20003": "查询坐标或区域异常",
}


class AmapError(RuntimeError):
    def __init__(self, infocode: str, info: str) -> None:
        hint = INFOCODE_HINTS.get(infocode, info)
        super().__init__(f"高德接口返回错误 [{infocode}] {info}；{hint}")
        self.infocode = infocode
        self.info = info


@dataclass
class BoundingBox:
    min_lng: float
    min_lat: float
    max_lng: float
    max_lat: float

    def split(self, cols: int, rows: int) -> list["BoundingBox"]:
        """切成 cols × rows 个子矩形。"""
        cells = []
        dw = (self.max_lng - self.min_lng) / cols
        dh = (self.max_lat - self.min_lat) / rows
        for i in range(cols):
            for j in range(rows):
                cells.append(
                    BoundingBox(
                        min_lng=self.min_lng + dw * i,
                        min_lat=self.min_lat + dh * j,
                        max_lng=self.min_lng + dw * (i + 1),
                        max_lat=self.min_lat + dh * (j + 1),
                    )
                )
        return cells

    def as_polygon(self) -> str:
        """高德矩形写法：左上角|右下角。"""
        return (
            f"{self.min_lng:.6f},{self.max_lat:.6f}|{self.max_lng:.6f},{self.min_lat:.6f}"
        )


@dataclass
class Poi:
    """从高德响应里抽出来的干净结构。"""

    poi_id: str
    name: str
    address: str | None = None
    province: str | None = None
    city: str | None = None
    district: str | None = None
    adcode: str | None = None
    typecode: str | None = None
    type_name: str | None = None
    lng: float | None = None
    lat: float | None = None
    tel: str | None = None
    business_area: str | None = None
    rating: float | None = None
    cost: float | None = None
    open_time: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, "", []):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_poi(item: dict[str, Any]) -> Poi | None:
    """兼容 v3 / v5 两种响应结构。

    v5 把 tel、rating、business_area 收进了 business 子对象，v3 是平铺的。
    高德对空值有时返回 [] 而不是 null，需要统一处理。
    """
    poi_id = str(item.get("id") or "").strip()
    name = str(item.get("name") or "").strip()
    if not name:
        return None

    business = item.get("business")
    business = business if isinstance(business, dict) else {}

    def pick(*keys: str) -> Any:
        for key in keys:
            for source in (business, item):
                value = source.get(key)
                if value not in (None, "", [], {}):
                    return value
        return None

    lng = lat = None
    location = item.get("location")
    if isinstance(location, str) and "," in location:
        parts = location.split(",")
        lng, lat = _to_float(parts[0]), _to_float(parts[1])

    type_name = item.get("type")
    if isinstance(type_name, list):
        type_name = ";".join(str(t) for t in type_name)

    tel = pick("tel")
    if isinstance(tel, list):
        tel = ";".join(str(t) for t in tel if t)

    return Poi(
        poi_id=poi_id,
        name=name,
        address=str(pick("address") or "") or None,
        province=str(item.get("pname") or "") or None,
        city=str(item.get("cityname") or item.get("city") or "") or None,
        district=str(item.get("adname") or "") or None,
        adcode=str(item.get("adcode") or "") or None,
        typecode=str(item.get("typecode") or "") or None,
        type_name=str(type_name or "") or None,
        lng=lng,
        lat=lat,
        tel=str(tel) if tel else None,
        business_area=str(pick("business_area") or "") or None,
        rating=_to_float(pick("rating")),
        cost=_to_float(pick("cost")),
        open_time=str(pick("opentime_today", "opentime_week", "open_time") or "") or None,
        raw=item,
    )


class AmapClient:
    def __init__(
        self,
        key: str | None = None,
        sig_secret: str | None = None,
        qps: float | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.key = key or settings.amap_key
        self.sig_secret = sig_secret if sig_secret is not None else settings.amap_sig_secret
        self.limiter = RateLimiter(qps or settings.amap_qps)
        self._client = httpx.Client(
            timeout=timeout, headers={"User-Agent": "huodaizi-growth/1.0"}
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AmapClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- 底层请求 ---------------------------------------------------------
    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self.sig_secret:
            return params
        joined = "&".join(f"{k}={params[k]}" for k in sorted(params))
        params["sig"] = hashlib.md5((joined + self.sig_secret).encode()).hexdigest()
        return params

    def _get(self, path: str, params: dict[str, Any], retries: int = 3) -> dict[str, Any]:
        if not self.key:
            raise AmapError("10001", "未配置 AMAP_KEY")

        payload = {k: v for k, v in params.items() if v not in (None, "")}
        payload["key"] = self.key
        payload = self._sign(payload)

        last_error: Exception | None = None
        for attempt in range(retries):
            self.limiter.acquire()
            try:
                resp = self._client.get(f"{AMAP_HOST}{path}", params=payload)
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                time.sleep(2**attempt)
                continue

            if str(data.get("status")) == "1":
                return data

            infocode = str(data.get("infocode", ""))
            info = str(data.get("info", ""))
            # 频率超限属于可恢复错误，退避后重试
            if infocode in {"10004", "10021", "10022", "10023", "10029"}:
                logger.warning("高德限流(%s)，第 %s 次退避重试", infocode, attempt + 1)
                time.sleep(2 ** (attempt + 1))
                last_error = AmapError(infocode, info)
                continue
            raise AmapError(infocode, info)

        if isinstance(last_error, AmapError):
            raise last_error
        raise AmapError("network", f"请求高德失败：{last_error}")

    # -- 业务接口 ---------------------------------------------------------
    def district(self, keyword: str, with_boundary: bool = True) -> dict[str, Any] | None:
        """行政区查询，用于拿 adcode、中心点与边界。"""
        data = self._get(
            "/v3/config/district",
            {
                "keywords": keyword,
                "subdistrict": 0,
                "extensions": "all" if with_boundary else "base",
            },
        )
        districts = data.get("districts") or []
        return districts[0] if districts else None

    def bounding_box(self, region: str) -> BoundingBox | None:
        """取城市外接矩形。边界数据缺失时退化成中心点 ±0.35°（约 ±35km）。"""
        info = self.district(region, with_boundary=True)
        if not info:
            return None

        polyline = info.get("polyline") or ""
        lngs: list[float] = []
        lats: list[float] = []
        for point in polyline.replace("|", ";").split(";"):
            if "," not in point:
                continue
            lng, lat = point.split(",")[:2]
            flng, flat = _to_float(lng), _to_float(lat)
            if flng is not None and flat is not None:
                lngs.append(flng)
                lats.append(flat)

        if lngs and lats:
            return BoundingBox(min(lngs), min(lats), max(lngs), max(lats))

        center = info.get("center") or ""
        if "," in center:
            clng, clat = (_to_float(x) for x in center.split(",")[:2])
            if clng is not None and clat is not None:
                return BoundingBox(clng - 0.35, clat - 0.3, clng + 0.35, clat + 0.3)
        return None

    def _paged_search(
        self, path: str, base_params: dict[str, Any], max_results: int
    ) -> Iterator[Poi]:
        yielded = 0
        for page in range(1, MAX_PAGE_NUM + 1):
            if yielded >= max_results:
                return
            params = dict(base_params)
            params.update(
                {"page_size": PAGE_SIZE, "page_num": page, "show_fields": SHOW_FIELDS}
            )
            data = self._get(path, params)
            pois = data.get("pois") or []
            for item in pois:
                poi = parse_poi(item)
                if poi is None:
                    continue
                yield poi
                yielded += 1
                if yielded >= max_results:
                    return
            if len(pois) < PAGE_SIZE:
                return

    def search_text(
        self,
        keywords: str,
        region: str | None = None,
        types: str | None = None,
        city_limit: bool = True,
        max_results: int | None = None,
    ) -> Iterator[Poi]:
        yield from self._paged_search(
            "/v5/place/text",
            {
                "keywords": keywords,
                "region": region,
                "types": types,
                "city_limit": "true" if city_limit else "false",
            },
            max_results or settings.amap_max_results_per_query,
        )

    def search_around(
        self,
        location: str,
        keywords: str | None = None,
        types: str | None = None,
        radius: int = 5000,
        max_results: int | None = None,
    ) -> Iterator[Poi]:
        yield from self._paged_search(
            "/v5/place/around",
            {
                "location": location,
                "keywords": keywords,
                "types": types,
                "radius": min(max(radius, 100), 50000),
                "sortrule": "weight",
            },
            max_results or settings.amap_max_results_per_query,
        )

    def search_polygon(
        self,
        polygon: str,
        keywords: str | None = None,
        types: str | None = None,
        max_results: int | None = None,
    ) -> Iterator[Poi]:
        yield from self._paged_search(
            "/v5/place/polygon",
            {"polygon": polygon, "keywords": keywords, "types": types},
            max_results or settings.amap_max_results_per_query,
        )

    def search_grid(
        self,
        keywords: str,
        region: str,
        types: str | None = None,
        grid: int = 4,
        on_cell: Any = None,
    ) -> Iterator[Poi]:
        """网格化采集：把城市切成 grid×grid 格逐格搜索，突破 1000 条上限。

        4×4 网格理论上限就是 16000 条，足以覆盖一个二线城市的单个品类。
        """
        box = self.bounding_box(region)
        if box is None:
            logger.warning("未取到 %s 的边界，退化为普通关键字搜索", region)
            yield from self.search_text(keywords, region=region, types=types)
            return

        cells = box.split(grid, grid)
        for index, cell in enumerate(cells, start=1):
            if on_cell:
                on_cell(index, len(cells))
            yield from self.search_polygon(cell.as_polygon(), keywords=keywords, types=types)
