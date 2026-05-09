#!/usr/bin/env python3
"""
Health API 路由（增强版）
GET /api/v1/health - 健康检查（仅返回服务状态）
GET /api/v1/health/detailed - 详细健康检查（含依赖服务状态）
"""
from __future__ import annotations

from fastapi import APIRouter, Response

import time

from app.logging_config import get_logger

router = APIRouter(prefix="/api/v1", tags=["health"])
public_router = APIRouter(prefix="/api", tags=["health"])
logger = get_logger("health")

REQUIRED_BUSINESS_TABLES = [
    "ragz_sessions",
    "ragz_messages",
    "ragz_documents",
    "member_user",
    "promotion_kefu_conversation",
    "promotion_kefu_message",
    "system_users",
]


def _db_config_payload() -> dict:
    from app.config import get_config

    config = get_config()
    return {
        "host": config.db.host,
        "port": config.db.port,
        "database": config.db.database,
        "user": config.db.user,
    }


def _row_value(row, key: str, index: int = 0):
    if isinstance(row, dict):
        return row.get(key)
    if row is None:
        return None
    return row[index]


def _sanitize_db_error(message: str) -> str:
    from app.config import get_config

    password = get_config().db.password or ""
    if password:
        return message.replace(password, "***")
    return message


async def _db_health_payload() -> dict:
    configured = _db_config_payload()
    start = time.time()
    try:
        from app.database.connection import get_db_pool

        pool = get_db_pool()
        import aiomysql
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT DATABASE() AS database_name")
                db_row = await cur.fetchone()
                selected_database = _row_value(db_row, "database_name")

                placeholders = ", ".join(["%s"] * len(REQUIRED_BUSINESS_TABLES))
                await cur.execute(
                    "SELECT TABLE_NAME FROM information_schema.TABLES "
                    f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
                    REQUIRED_BUSINESS_TABLES,
                )
                rows = await cur.fetchall()

        present_set = {_row_value(row, "TABLE_NAME") for row in rows}
        present = [table for table in REQUIRED_BUSINESS_TABLES if table in present_set]
        missing = [table for table in REQUIRED_BUSINESS_TABLES if table not in present_set]
        latency_ms = round((time.time() - start) * 1000)
        connected_to_expected_db = selected_database == configured["database"] == "lumapepe"
        schema_ok = not missing
        return {
            "ok": connected_to_expected_db,
            "status": "up" if connected_to_expected_db and schema_ok else "degraded",
            "configured": configured,
            "selected_database": selected_database,
            "latency_ms": latency_ms,
            "required_tables": {
                "ok": schema_ok,
                "present": present,
                "missing": missing,
            },
        }
    except Exception as exc:
        latency_ms = round((time.time() - start) * 1000)
        error_message = _sanitize_db_error(str(exc))
        logger.error(
            "MySQL health check failed",
            extra={
                "db_host": configured["host"],
                "db_port": configured["port"],
                "db_name": configured["database"],
                "db_user": configured["user"],
                "error": f"{type(exc).__name__}: {error_message}",
            },
        )
        return {
            "ok": False,
            "status": "down",
            "configured": configured,
            "selected_database": None,
            "latency_ms": latency_ms,
            "required_tables": {
                "ok": False,
                "present": [],
                "missing": REQUIRED_BUSINESS_TABLES,
            },
            "error": {
                "type": type(exc).__name__,
                "message": error_message,
            },
        }


def _db_health_response(payload: dict, response: Response) -> dict:
    if payload.get("status") == "down":
        response.status_code = 503
    return payload


async def _check_mysql() -> dict:
    """检查 MySQL 连接"""
    db = await _db_health_payload()
    result = {
        "status": db["status"],
        "latency_ms": db["latency_ms"],
        "configured": db["configured"],
        "selected_database": db["selected_database"],
        "required_tables": db["required_tables"],
    }
    if db.get("error"):
        result["error"] = db["error"]
    return result


async def _check_redis() -> dict:
    """检查 Redis 连接"""
    try:
        from app.cache.redis_client import get_redis_client
        r = get_redis_client()
        start = time.time()
        pong = await r.ping()
        latency_ms = round((time.time() - start) * 1000)
        return {"status": "up" if pong else "down", "latency_ms": latency_ms}
    except Exception:
        return {"status": "down", "latency_ms": 0, "error": "connection_failed"}


async def _check_qdrant() -> dict:
    """检查 Qdrant 连接"""
    try:
        from app.main import app as _app
        if hasattr(_app.state, "milvus_client"):
            client = _app.state.milvus_client
            if hasattr(client, "health_check"):
                result = await client.health_check()
                if result:
                    return {"status": "up", "entities": 0}
                return {"status": "degraded", "entities": 0}
        return {"status": "degraded", "entities": 0, "reason": "using_mock_retriever"}
    except Exception:
        return {"status": "down", "error": "connection_failed"}


async def _check_llm() -> dict:
    """检查 LLM 服务可用性（轻量级 ping）"""
    try:
        from app.main import app as _app
        if hasattr(_app.state, "orchestrator"):
            return {"status": "available", "reason": "llm_configured"}
        return {"status": "unknown"}
    except Exception:
        return {"status": "down", "error": "connection_failed"}


@router.get("/health")
async def health_check():
    """
    基础健康检查（Kubernetes liveness probe）
    仅返回服务是否存活
    """
    return {
        "status": "healthy",
        "service": "RAG Customer Service API",
        "version": "1.0.0",
    }


@router.get("/health/detailed")
async def health_check_detailed():
    """
    详细健康检查（Kubernetes readiness probe）
    检查所有依赖服务状态
    """
    mysql = await _check_mysql()
    redis = await _check_redis()
    qdrant = await _check_qdrant()
    llm = await _check_llm()

    components = {
        "mysql": mysql,
        "redis": redis,
        "qdrant": qdrant,
        "llm": llm,
    }

    # 判断整体状态
    down_components = [k for k, v in components.items() if v.get("status") == "down"]
    degraded_components = [k for k, v in components.items() if v.get("status") == "degraded"]

    if down_components:
        overall_status = "unhealthy"
    elif degraded_components:
        overall_status = "degraded"
    else:
        overall_status = "healthy"

    return {
        "status": overall_status,
        "service": "RAG Customer Service API",
        "version": "1.0.0",
        "components": components,
    }


@router.get("/health/alerts")
async def health_alerts():
    """
    告警监控系统状态

    返回各监控指标的当前状态和告警阈值。
    """
    from app.services.alerting import get_monitor
    monitor = get_monitor()
    status = monitor.get_status()
    return {
        "monitoring": status["running"],
        "alerts": {
            "llm": {
                "consecutive_failures": status["llm_failures"],
                "threshold": monitor.llm_failure_threshold,
                "triggered": status["llm_failures"] >= monitor.llm_failure_threshold,
            },
            "vector": {
                "consecutive_failures": status["vector_failures"],
                "threshold": monitor.vector_failure_threshold,
                "triggered": status["vector_failures"] >= monitor.vector_failure_threshold,
            },
            "latency": {
                "p99_ms": status["latency_p99"],
                "threshold_ms": monitor.latency_threshold_ms,
                "samples": status["latency_samples"],
            },
        },
    }


async def _redis_health_payload() -> dict:
    from app.config import get_config
    from app.services.redis_pubsub import get_redis_pubsub_manager, mask_redis_url

    config = get_config()
    manager = get_redis_pubsub_manager(config.redis.url, redis_required=config.redis.required)
    redis_url = mask_redis_url(config.redis.url)
    try:
        redis_client = getattr(manager, "_redis", None)
        if redis_client is None:
            from app.cache.redis_client import get_redis_client
            redis_client = get_redis_client()
        ping = await redis_client.ping()
        return {
            "ok": bool(ping),
            "redis_url": redis_url,
            "ping": "PONG" if ping else None,
            "pubsub_connected": manager.connected,
            "subscriber_running": manager.subscriber_running,
            "channel": manager.channel,
            "instance_id": manager.instance_id,
        }
    except Exception as exc:
        return {
            "ok": False,
            "redis_url": redis_url,
            "error": str(exc),
            "pubsub_connected": manager.connected,
            "subscriber_running": manager.subscriber_running,
            "channel": manager.channel,
            "instance_id": manager.instance_id,
        }


@router.get("/health/redis")
async def redis_health_v1():
    return await _redis_health_payload()


@public_router.get("/health/redis")
async def redis_health():
    return await _redis_health_payload()


@router.get("/health/db")
async def db_health_v1(response: Response):
    return _db_health_response(await _db_health_payload(), response)


@public_router.get("/health/db")
async def db_health(response: Response):
    return _db_health_response(await _db_health_payload(), response)
