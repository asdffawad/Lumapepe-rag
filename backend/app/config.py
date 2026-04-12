"""
配置管理：统一管理 MySQL / Redis / Milvus / Celery / LLM 连接参数
所有配置从环境变量读取，零硬编码
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional


@dataclass
class LLMConfig:
    provider: str = "qwen"
    model_name: str = "qwen-max"
    fallback_models: list[str] = field(default_factory=lambda: ["qwen-plus", "qwen-turbo"])
    api_key: str = ""          # 必填，从环境变量读取，不再有默认值
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    timeout: int = 60
    max_retries: int = 3
    default_temperature: float = 0.7
    default_max_tokens: int = 2048


@dataclass
class DBConfig:
    host: str = "localhost"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = "rag_customer_service"
    pool_size: int = 10


@dataclass
class RedisConfig:
    url: str = "redis://localhost:6379"
    db: int = 0


@dataclass
class MilvusConfig:
    host: str = "localhost"
    port: int = 19530
    collection_name: str = "knowledge_base"
    entity_count_hint: int = 1_000_000  # 启动时估计向量数量，<100万用 HNSW，否则用 IVF_FLAT


@dataclass
class CeleryConfig:
    broker_url: str = "redis://localhost:6379/1"
    result_backend: str = "redis://localhost:6379/2"


@dataclass
class RAGConfig:
    top_k: int = 10
    rerank_top_k: int = 5
    rewrite_timeout_ms: int = 10000   # 10s，history 增长时需要更长时间
    retrieve_timeout_ms: int = 1000
    rerank_timeout_ms: int = 1000
    llm_timeout_ms: int = int(os.getenv("RAG_LLM_TIMEOUT_MS", "60000"))
    max_context_tokens: int = 3000
    # 检索调优参数
    hybrid_search: bool = True      # 启用 Dense+BM25 混合检索
    hnsw_auto: bool = True          # 自动选择 HNSW/IVF_FLAT 索引
    rrf_k: int = 60                 # RRF 融合参数
    bm25_weight: float = 0.3        # BM25 在融合中的权重


@dataclass
class RateLimitConfig:
    enabled: bool = True
    global_limit: int = 200   # 全局每秒最大请求数（按 IP）
    user_limit: int = 60      # 用户每秒最大请求数（按 user_id）
    window: int = 60           # 滑动窗口秒数


@dataclass
class IngestConfig:
    auto_ingest: bool = False
    data_dir: str = "data"
    chunk_size: int = 512          # tokens（token-based 分块，中英文公平）
    chunk_overlap: int = 50        # 重叠 tokens 数
    chunk_unit: str = "token"      # "token" | "char"，默认 token
    batch_size: int = 10


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    db: DBConfig = field(default_factory=DBConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    milvus: MilvusConfig = field(default_factory=MilvusConfig)
    celery: CeleryConfig = field(default_factory=CeleryConfig)
    rag: RAGConfig = field(default_factory=RAGConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    ingest: IngestConfig = field(default_factory=IngestConfig)
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    debug: bool = False


def _str_to_bool(val: str | None) -> bool:
    if val is None:
        return False
    return val.lower() in ("1", "true", "yes")


@lru_cache
def get_config() -> AppConfig:
    return AppConfig(
        llm=LLMConfig(
            provider=os.getenv("LLM_PROVIDER", "mock"),
            model_name=os.getenv("LLM_MODEL_NAME", "qwen-max"),
            fallback_models=os.getenv("LLM_FALLBACK_MODELS", "qwen-plus,qwen-turbo").split(","),
            api_key=os.getenv("DASHSCOPE_API_KEY", ""),
            base_url=os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com"),
            timeout=int(os.getenv("LLM_TIMEOUT", "60")),
            max_retries=int(os.getenv("LLM_MAX_RETRIES", "3")),
            default_temperature=float(os.getenv("LLM_TEMPERATURE", "0.7")),
            default_max_tokens=int(os.getenv("LLM_MAX_TOKENS", "2048")),
        ),
        db=DBConfig(
            host=os.getenv("MYSQL_HOST", "localhost"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=os.getenv("MYSQL_DATABASE", "rag_customer_service"),
            pool_size=int(os.getenv("MYSQL_POOL_SIZE", "10")),
        ),
        redis=RedisConfig(
            url=os.getenv("REDIS_URL", "redis://localhost:6379"),
            db=int(os.getenv("REDIS_DB", "0")),
        ),
        milvus=MilvusConfig(
            host=os.getenv("MILVUS_HOST", "localhost"),
            port=int(os.getenv("MILVUS_PORT", "19530")),
            collection_name=os.getenv("MILVUS_COLLECTION", "knowledge_base"),
            entity_count_hint=int(os.getenv("MILVUS_ENTITY_COUNT_HINT", "1000000")),
        ),
        celery=CeleryConfig(
            broker_url=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/1"),
            result_backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/2"),
        ),
        rag=RAGConfig(
            top_k=int(os.getenv("RAG_TOP_K", "10")),
            rerank_top_k=int(os.getenv("RAG_RERANK_TOP_K", "5")),
            hybrid_search=_str_to_bool(os.getenv("RAG_HYBRID_SEARCH", "true")),
            hnsw_auto=_str_to_bool(os.getenv("RAG_HNSW_AUTO", "true")),
            rrf_k=int(os.getenv("RAG_RRF_K", "60")),
            bm25_weight=float(os.getenv("RAG_BM25_WEIGHT", "0.3")),
        ),
        rate_limit=RateLimitConfig(
            enabled=_str_to_bool(os.getenv("RATE_LIMIT_ENABLED", "true")),
            global_limit=int(os.getenv("RATE_LIMIT_GLOBAL", "200")),
            user_limit=int(os.getenv("RATE_LIMIT_USER", "60")),
            window=int(os.getenv("RATE_LIMIT_WINDOW", "60")),
        ),
        ingest=IngestConfig(
            auto_ingest=_str_to_bool(os.getenv("AUTO_INGEST", "false")),
            data_dir=os.getenv("DATA_DIR", "data"),
            chunk_size=int(os.getenv("CHUNK_SIZE", "512")),
            chunk_overlap=int(os.getenv("CHUNK_OVERLAP", "50")),
            chunk_unit=os.getenv("CHUNK_UNIT", "token"),
            batch_size=int(os.getenv("INGEST_BATCH_SIZE", "10")),
        ),
        cors_origins=os.getenv("CORS_ORIGINS", "*").split(","),
        debug=_str_to_bool(os.getenv("DEBUG")),
    )
