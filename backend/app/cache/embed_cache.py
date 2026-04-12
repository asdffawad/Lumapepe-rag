"""
Embedding 结果缓存（Redis String）
Cache-Aside 模式：先查 Redis，miss 后向量化并回填
"""
from __future__ import annotations

import hashlib

import orjson
from app.cache.redis_client import RedisClient


class EmbedCache:
    _TTL = 7 * 24 * 3600  # 7 days

    def __init__(self, redis_client: RedisClient, ttl: int = _TTL):
        self._r = redis_client
        self._ttl = ttl

    def _hash(self, text: str) -> str:
        return hashlib.md5(text.encode()).hexdigest()

    async def get(self, text: str) -> list[float] | None:
        key = f"embed:cache:{self._hash(text)}"
        data = await self._r.client().get(key)
        return orjson.loads(data) if data else None

    async def set(self, text: str, embedding: list[float]):
        key = f"embed:cache:{self._hash(text)}"
        await self._r.client().set(key, orjson.dumps(embedding), ex=self._ttl)
