"""
MySQL 连接池管理（单例），使用 aiomysql 异步驱动
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import aiomysql

if TYPE_CHECKING:
    from app.config import DBConfig

# 模块级单例状态
_db_pool: aiomysql.Pool | None = None


async def _wait_for_mysql(config: "DBConfig", max_retries: int = 5, delay: float = 2.0) -> None:
    """等待 MySQL 完全就绪后再创建连接池"""
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            test_conn = await aiomysql.connect(
                host=config.host,
                port=config.port,
                user=config.user,
                password=config.password,
                db=config.database,
                charset="utf8mb4",
                connect_timeout=10,
            )
            test_conn.close()
            return
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                await asyncio.sleep(delay * attempt)
    raise RuntimeError(
        f"MySQL not available after {max_retries} attempts: {last_error}"
    ) from last_error


async def create_db_pool(config: "DBConfig") -> aiomysql.Pool:
    """创建数据库连接池"""
    global _db_pool
    if _db_pool is not None:
        try:
            async with _db_pool.acquire() as conn:
                pass
            return _db_pool
        except Exception:
            await close_db_pool()

    # 等待 MySQL 完全就绪（避免容器启动后网络层尚未稳定的瞬时错误）
    await _wait_for_mysql(config, max_retries=5, delay=2.0)

    _db_pool = await aiomysql.create_pool(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        db=config.database,
        minsize=5,
        maxsize=config.pool_size,
        autocommit=True,
        pool_recycle=3600,
        connect_timeout=30,
        charset="utf8mb4",
    )
    return _db_pool


async def close_db_pool():
    """关闭数据库连接池"""
    global _db_pool
    if _db_pool is not None:
        _db_pool.close()
        await _db_pool.wait_closed()
        _db_pool = None


def get_db_pool() -> aiomysql.Pool:
    """获取当前连接池"""
    if _db_pool is None:
        raise RuntimeError("Database not connected. Call create_db_pool() first.")
    return _db_pool
