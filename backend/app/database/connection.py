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


def _db_target(config: "DBConfig") -> str:
    return f"{config.host}:{config.port}/{config.database} user={config.user}"


async def _wait_for_mysql(
    config: "DBConfig",
    max_retries: int | None = None,
    delay: float | None = None,
) -> None:
    """等待 MySQL 完全就绪后再创建连接池"""
    retries = max_retries if max_retries is not None else config.connect_retries
    retry_delay = delay if delay is not None else config.connect_retry_delay
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            test_conn = await aiomysql.connect(
                host=config.host,
                port=config.port,
                user=config.user,
                password=config.password,
                db=config.database,
                charset="utf8mb4",
                connect_timeout=config.connect_timeout,
            )
            test_conn.close()
            return
        except Exception as e:
            last_error = e
            if attempt < retries:
                await asyncio.sleep(retry_delay)
    raise RuntimeError(
        f"MySQL not available after {retries} attempts "
        f"for {_db_target(config)}: {last_error}"
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

    if not config.host:
        raise RuntimeError("MySQL DB_HOST is not configured; set DB_HOST to the remote MySQL server IP")
    if not config.user:
        raise RuntimeError("MySQL DB_USER is not configured")

    # 等待 MySQL 完全就绪（避免容器启动后网络层尚未稳定的瞬时错误）
    await _wait_for_mysql(config)

    _db_pool = await aiomysql.create_pool(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        db=config.database,
        minsize=5,
        maxsize=config.pool_size,
        autocommit=True,
        pool_recycle=config.pool_recycle,
        connect_timeout=config.connect_timeout,
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
        try:
            from app.config import get_config
            target = _db_target(get_config().db)
        except Exception:
            target = "unknown target"
        raise RuntimeError(
            f"Database not connected for {target}. "
            "Startup connection failed or create_db_pool() was not called."
        )
    return _db_pool
