"""创蓝 253 短信客户端。

支持三种调用：
    单发/群发   POST /msg/v1/send/json      同一内容发给一批号码，最省接口调用
    变量短信    POST /msg/variable/json     每个号码带自己的变量，做个性化必用
    余额查询    POST /msg/balance/json      发大批量前先看余额，别发一半断了

营销短信有几条硬规矩，都在这里落实：
    - 必须带签名，签名要在内容最前面
    - 必须带退订后缀（回T退订），否则运营商会拦截且投诉风险极高
    - 计费按 70 字一条，超长按 67 字/条拆分，预算要按计费条数算而不是号码数
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Iterable

import httpx

from app.config import settings
from app.utils.ratelimit import RateLimiter

logger = logging.getLogger(__name__)

UNSUBSCRIBE_SUFFIX = "回T退订"

# 创蓝返回码 -> 中文说明与是否可重试
ERROR_CODES: dict[str, tuple[str, bool]] = {
    "0": ("提交成功", False),
    "101": ("无此用户，检查 CHUANGLAN_ACCOUNT", False),
    "102": ("密码错误，注意 API 密码不是登录密码", False),
    "103": ("提交过快，超过流速限制", True),
    "104": ("系统忙，稍后重试", True),
    "105": ("敏感短信，内容命中运营商敏感词", False),
    "106": ("消息长度错误", False),
    "107": ("频率限制", True),
    "108": ("号码内容为空", False),
    "109": ("账号冻结", False),
    "110": ("禁止群发", False),
    "111": ("系统错误", True),
    "112": ("号码格式错误", False),
    "113": ("扩展码格式错误", False),
    "114": ("可用参数组合错误", False),
    "115": ("定时时间格式错误", False),
    "116": ("签名不合法或未带签名", False),
    "117": ("IP 地址认证失败，需在控制台加白名单", False),
    "118": ("用户未开通此产品", False),
    "119": ("用户已申请退订", False),
    "120": ("系统升级中", True),
}


@dataclass
class SendResult:
    ok: bool
    code: str
    msg_id: str | None = None
    error: str | None = None
    retriable: bool = False
    dry_run: bool = False


def describe_code(code: str) -> tuple[str, bool]:
    return ERROR_CODES.get(str(code), (f"未知返回码 {code}", False))


def billing_count(content: str) -> int:
    """计费条数：<=70 字算 1 条，超出按 67 字/条切分。"""
    length = len(content or "")
    if length == 0:
        return 0
    if length <= 70:
        return 1
    return -(-length // 67)


def build_content(body: str, sign: str | None = None, with_unsubscribe: bool = True) -> str:
    """拼装最终下发内容：签名 + 正文 + 退订后缀，且不重复添加。"""
    sign = (sign or settings.sms_sign or "").strip()
    content = (body or "").strip()
    if sign and not content.startswith(sign):
        # 内容里若已自带其他【】签名则不再叠加
        if not content.startswith("【"):
            content = f"{sign}{content}"
    if with_unsubscribe and "退订" not in content:
        content = f"{content} {UNSUBSCRIBE_SUFFIX}"
    return content


class ChuanglanClient:
    def __init__(
        self,
        account: str | None = None,
        password: str | None = None,
        base_url: str | None = None,
        qps: float | None = None,
        dry_run: bool | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.account = account or settings.chuanglan_account
        self.password = password or settings.chuanglan_password
        self.base_url = (base_url or settings.chuanglan_base_url).rstrip("/")
        self.dry_run = settings.sms_dry_run if dry_run is None else dry_run
        self.limiter = RateLimiter(qps or settings.sms_qps)
        self._client = httpx.Client(
            timeout=timeout, headers={"Content-Type": "application/json;charset=utf-8"}
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ChuanglanClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @property
    def configured(self) -> bool:
        return bool(self.account and self.password)

    def _post(self, path: str, payload: dict[str, Any], retries: int = 3) -> dict[str, Any]:
        body = {"account": self.account, "password": self.password, **payload}
        last_error: Exception | None = None
        for attempt in range(retries):
            self.limiter.acquire()
            try:
                resp = self._client.post(f"{self.base_url}{path}", json=body)
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                logger.warning("创蓝请求失败(%s/%s)：%s", attempt + 1, retries, exc)
                time.sleep(2**attempt)
        raise RuntimeError(f"调用创蓝接口失败：{last_error}")

    # -- 业务接口 ---------------------------------------------------------
    def send(
        self,
        phones: Iterable[str],
        content: str,
        extend: str | None = None,
        uid: str | None = None,
        send_time: str | None = None,
    ) -> SendResult:
        """同内容群发。phone 参数逗号分隔，单次建议不超过 1000 个号码。"""
        numbers = [p for p in phones if p]
        if not numbers:
            return SendResult(ok=False, code="108", error="号码为空")

        if self.dry_run or not self.configured:
            reason = "演练模式" if self.dry_run else "未配置创蓝账号，自动降级为演练"
            logger.info("[%s] 拟发送 %s 条：%s", reason, len(numbers), content[:40])
            return SendResult(
                ok=True, code="0", msg_id=f"dry-{int(time.time()*1000)}", dry_run=True
            )

        data = self._post(
            "/msg/v1/send/json",
            {
                "msg": content,
                "phone": ",".join(numbers),
                "report": "true",
                "extend": extend or "",
                "uid": uid or "",
                "sendtime": send_time or "",
            },
        )
        return self._to_result(data)

    def send_variable(
        self,
        template: str,
        rows: list[tuple[str, list[str]]],
        report: bool = True,
    ) -> SendResult:
        """变量短信。

        template 内用 {$var} 占位，rows 为 [(手机号, [变量1, 变量2]), ...]，
        params 拼成 "手机号,变量1,变量2;手机号,变量1,变量2"。
        """
        if not rows:
            return SendResult(ok=False, code="108", error="号码为空")

        params = ";".join(
            ",".join([phone, *[str(v).replace(",", "，").replace(";", "；") for v in values]])
            for phone, values in rows
        )

        if self.dry_run or not self.configured:
            logger.info("[演练模式] 拟变量群发 %s 条", len(rows))
            return SendResult(
                ok=True, code="0", msg_id=f"dry-{int(time.time()*1000)}", dry_run=True
            )

        data = self._post(
            "/msg/variable/json",
            {"msg": template, "params": params, "report": "true" if report else "false"},
        )
        return self._to_result(data)

    def balance(self) -> dict[str, Any]:
        if self.dry_run or not self.configured:
            return {"code": "0", "balance": "演练模式", "dry_run": True}
        return self._post("/msg/balance/json", {})

    @staticmethod
    def _to_result(data: dict[str, Any]) -> SendResult:
        code = str(data.get("code", ""))
        message, retriable = describe_code(code)
        if code == "0":
            return SendResult(ok=True, code=code, msg_id=str(data.get("msgId") or ""))
        return SendResult(
            ok=False,
            code=code,
            error=data.get("errorMsg") or message,
            retriable=retriable,
        )
