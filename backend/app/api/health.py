#!/usr/bin/env python3
"""
Health API 路由（增强版）
GET /api/v1/health - 健康检查（仅返回服务状态）
GET /api/v1/health/detailed - 详细健康检查（含依赖服务状态）
"""
from __future__ import annotations

from fastapi import APIRouter, Response

import time

router = APIRouter(prefix="/api/v1", tags=["health"])


async def _check_mysql() -> dict:
    """检查 MySQL 连接"""
    try:
        from app.database.connection import get_db_pool
        pool = get_db_pool()
        if pool is None:
            return {"status": "down", "latency_ms": 0, "error": "not_connected"}
        import aiomysql
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                start = time.time()
                await cur.execute("SELECT 1")
                await cur.fetchone()
                latency_ms = round((time.time() - start) * 1000)
        return {"status": "up", "latency_ms": latency_ms}
    except Exception:
        return {"status": "down", "latency_ms": 0, "error": "connection_failed"}


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
