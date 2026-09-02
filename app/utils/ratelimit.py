"""线程安全的令牌桶。

高德 Web 服务 Key 有 QPS 上限（个人版通常 3），创蓝也有提交频率限制，
超限会直接被拒绝甚至封 Key，所有外部调用都必须过这一层。
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, rate: float, capacity: float | None = None) -> None:
        self.rate = max(float(rate), 0.1)
        self.capacity = capacity if capacity is not None else max(self.rate, 1.0)
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                wait = (tokens - self._tokens) / self.rate
            time.sleep(min(wait, 1.0))
