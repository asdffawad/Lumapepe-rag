"""
用户限流（Redis INCR + EXPIRE）
滑动窗口计数器
"""
from __future__ import annotations

import time

from app.cache.redis_client import RedisClient


class RateLimiter:
    def __init__(
        self, redis_client: RedisClient, max_requests: int = 60, window: int = 60
    ):
        self._r = redis_client
        self._max_requests = max_requests
        self._window = window  # seconds

    async def is_allowed(self, user_id: str) -> bool:
        key = f"rate:user:{user_id}:{int(time.time() // self._window)}"
        client = self._r.client()
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, self._window)
        return count <= self._max_requests
