"""
热点问题缓存（Redis ZSet）
每小时刷新，每日清理
"""
from __future__ import annotations

from datetime import date

from app.cache.redis_client import RedisClient


class HotQuestionCache:
    _TTL = 86400  # 24h

    def __init__(self, redis_client: RedisClient):
        self._r = redis_client

    async def record(self, query: str):
        key = f"hot:questions:day:{date.today()}"
        await self._r.client().zincrby(key, 1, query)

    async def get_top(self, limit: int = 10) -> list[tuple[str, float]]:
        key = f"hot:questions:day:{date.today()}"
        return await self._r.client().zrevrange(key, 0, limit - 1, withscores=True)
