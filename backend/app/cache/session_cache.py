"""
会话上下文缓存（Redis Hash + List）
Write-Through 模式：写 MySQL 时同步写 Redis
"""
from __future__ import annotations

import orjson
import redis.asyncio as redis


class SessionCache:
    _SESSION_TTL = 1800  # 30 min
    _HISTORY_TTL = 1800  # 30 min

    def __init__(self, redis_client: redis.Redis):
        self._r = redis_client

    async def get(self, session_id: str) -> dict | None:
        key = f"session:{session_id}"
        data = await self._r.get(key)
        return orjson.loads(data) if data else None

    async def set(self, session_id: str, session_data: dict, ttl: int = _SESSION_TTL):
        key = f"session:{session_id}"
        await self._r.set(key, orjson.dumps(session_data), ex=ttl)

    async def delete(self, session_id: str):
        key = f"session:{session_id}"
        await self._r.delete(key)

    async def append_message(self, session_id: str, message: dict, ttl: int = _HISTORY_TTL):
        key = f"session:{session_id}:history"
        pipe = self._r.pipeline()
        pipe.rpush(key, orjson.dumps(message))
        pipe.expire(key, ttl)
        await pipe.execute()

    async def get_history(self, session_id: str) -> list[dict]:
        key = f"session:{session_id}:history"
        raw = await self._r.lrange(key, 0, -1)
        return [orjson.loads(item) for item in raw]

    async def clear_history(self, session_id: str):
        key = f"session:{session_id}:history"
        await self._r.delete(key)
