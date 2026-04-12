"""
Redis 连接管理（单例）
所有 Redis 操作通过此模块获取连接
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import redis.asyncio as redis

if TYPE_CHECKING:
    from app.config import RedisConfig

# 模块级单例状态
_redis_pool: redis.ConnectionPool | None = None


async def create_redis_pool(url: str = "redis://localhost:6379") -> redis.ConnectionPool:
    """创建 Redis 连接池"""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.ConnectionPool.from_url(url, decode_responses=True)
    return _redis_pool


async def close_redis_pool():
    """关闭 Redis 连接池"""
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.disconnect()
        _redis_pool = None


def get_redis_pool() -> redis.ConnectionPool:
    """获取当前连接池"""
    if _redis_pool is None:
        raise RuntimeError("Redis not connected. Call create_redis_pool() first.")
    return _redis_pool


def get_redis_client() -> redis.Redis:
    """获取 Redis 客户端（异步）"""
    if _redis_pool is None:
        raise RuntimeError("Redis not connected.")
    return redis.Redis(connection_pool=_redis_pool)


# ---------------------------------------------------------------------------
# 同步 Redis 客户端（供中间件使用，避免异步上下文问题）
# ---------------------------------------------------------------------------
_redis_client_sync: "redis.Redis | None" = None


def get_redis_client_sync() -> "redis.Redis":
    """获取同步 Redis 客户端（供中间件使用）"""
    global _redis_client_sync
    if _redis_client_sync is None:
        _redis_client_sync = redis.Redis(connection_pool=_redis_pool)
    return _redis_client_sync
