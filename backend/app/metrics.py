#!/usr/bin/env python3
"""
Prometheus Metrics 模块
暴露 /metrics 端点，提供关键业务指标
"""
from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    generate_latest,
    CONTENT_TYPE_LATEST,
)


# ---------------------------------------------------------------------------
# 指标定义
# ---------------------------------------------------------------------------

# 请求计数器（按 intent 分维）
chat_requests_total = Counter(
    "chat_requests_total",
    "Total chat requests",
    ["intent", "status"]
)

# 请求延迟直方图
chat_request_duration_seconds = Histogram(
    "chat_request_duration_seconds",
    "Chat request duration in seconds",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
)

# LLM 调用计数器
llm_calls_total = Counter(
    "llm_calls_total",
    "Total LLM API calls",
    ["model", "status"]
)

# LLM 延迟直方图
llm_call_duration_seconds = Histogram(
    "llm_call_duration_seconds",
    "LLM call duration in seconds",
    ["model"],
    buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0)
)

# 向量检索延迟
vector_search_duration_seconds = Histogram(
    "vector_search_duration_seconds",
    "Vector search duration in seconds",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)
)

# 活跃会话数
active_sessions = Gauge(
    "active_sessions",
    "Number of active chat sessions"
)

# Embedding 缓存命中率
embedding_cache_hits = Counter(
    "embedding_cache_hits_total",
    "Total embedding cache hits"
)
embedding_cache_misses = Counter(
    "embedding_cache_misses_total",
    "Total embedding cache misses"
)

# 系统指标
requests_in_flight = Gauge(
    "requests_in_flight",
    "Number of requests currently being processed"
)

# 意图分类方法分布
intent_classification_method = Counter(
    "intent_classification_method_total",
    "Intent classification method distribution",
    ["method"]  # rule / llm / hybrid / rule_fallback
)

# Token 使用量
llm_tokens_used = Counter(
    "llm_tokens_used_total",
    "Total LLM tokens used",
    ["model", "type"]  # type: input / output
)

# RAG 各阶段延迟
rag_rewrite_duration_seconds = Histogram(
    "rag_rewrite_duration_seconds",
    "RAG query rewrite duration",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0)
)
rag_retrieve_duration_seconds = Histogram(
    "rag_retrieve_duration_seconds",
    "RAG vector retrieval duration",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)
)
rag_rerank_duration_seconds = Histogram(
    "rag_rerank_duration_seconds",
    "RAG rerank duration",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5)
)
rag_generate_duration_seconds = Histogram(
    "rag_generate_duration_seconds",
    "RAG LLM generation duration",
    buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0)
)

# 商品检索指标
product_search_requests = Counter(
    "product_search_requests_total",
    "Total product search requests",
    ["match_type", "status"]  # match_type: barcode/alias/spu_keyword/none
)

product_search_latency_seconds = Histogram(
    "product_search_duration_seconds",
    "Product search duration in seconds",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
)

product_search_success = Counter(
    "product_search_success_total",
    "Total successful product searches",
    ["match_type"]
)

product_search_errors = Counter(
    "product_search_errors_total",
    "Total failed product searches",
    ["error_type"]
)

product_resolve_latency_seconds = Histogram(
    "product_resolve_duration_seconds",
    "Product resolve (parsing) duration in seconds",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25)
)

# 字典加载指标
dict_reloads_total = Counter(
    "dict_reloads_total",
    "Total dictionary reload operations",
    ["success"]  # "success" | "failure"
)

dict_load_duration_seconds = Histogram(
    "dict_load_duration_seconds",
    "Dictionary load duration",
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0)
)


# ---------------------------------------------------------------------------
# API 路由
# ---------------------------------------------------------------------------
router = APIRouter(tags=["monitoring"])


@router.get("/metrics")
async def metrics():
    """
    Prometheus /metrics 端点
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------------------------------------------------------------------------
# 辅助函数（供业务代码调用）
# ---------------------------------------------------------------------------
def record_chat_request(intent: str, status: str, duration_seconds: float):
    """记录聊天请求"""
    chat_requests_total.labels(intent=intent, status=status).inc()
    chat_request_duration_seconds.observe(duration_seconds)


def record_llm_call(model: str, status: str, duration_seconds: float):
    """记录 LLM 调用"""
    llm_calls_total.labels(model=model, status=status).inc()
    llm_call_duration_seconds.labels(model=model).observe(duration_seconds)


def record_vector_search(duration_seconds: float):
    """记录向量检索"""
    vector_search_duration_seconds.observe(duration_seconds)


def set_active_sessions(count: int):
    """设置活跃会话数"""
    active_sessions.set(count)


def record_product_search(match_type: str, status: str, duration_seconds: float):
    """记录商品检索"""
    product_search_requests.labels(match_type=match_type, status=status).inc()
    product_search_latency_seconds.observe(duration_seconds)
    if status == "success":
        product_search_success.labels(match_type=match_type).inc()


def record_product_search_error(error_type: str):
    """记录商品检索错误"""
    product_search_errors.labels(error_type=error_type).inc()


def record_resolve_duration(duration_seconds: float):
    """记录解析耗时"""
    product_resolve_latency_seconds.observe(duration_seconds)


def record_dict_reload(success: bool, duration_seconds: float):
    """记录字典重载"""
    status_label = "success" if success else "failure"
    dict_reloads_total.labels(success=status_label).inc()
    dict_load_duration_seconds.observe(duration_seconds)

