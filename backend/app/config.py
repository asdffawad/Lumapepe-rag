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
    # Token 限制（安全限制）
    max_input_tokens: int = 4096
    max_output_tokens: int = 2048


@dataclass
class EmbeddingConfig:
    provider: str = "dashscope"
    model_name: str = "text-embedding-v3"
    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dim: int = 1024


@dataclass
class DBConfig:
    host: str = "localhost"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = "lumapepe"
    pool_size: int = 10
    max_overflow: int = 20
    pool_recycle: int = 3600
    pool_timeout: int = 30


@dataclass
class RedisConfig:
    url: str = "redis://localhost:6379"
    db: int = 0
    pool_size: int = 10
    socket_timeout: int = 5
    socket_connect_timeout: int = 5
    required: bool = False


@dataclass
class MilvusConfig:
    host: str = "localhost"
    port: int = 19530
    collection_name: str = "knowledge_base"
    entity_count_hint: int = 1_000_000  # 启动时估计向量数量，<100万用 HNSW，否则用 IVF_FLAT


@dataclass
class QdrantConfig:
    """Qdrant 向量数据库配置"""
    host: str = "localhost"
    port: int = 6333
    collection_name: str = "knowledge_base"
    timeout: int = 30
    enabled: bool = False  # 是否启用 Qdrant（替代 Milvus）
    prefer_grpc: bool = True      # gRPC 默认更快
    vector_dim: int = 1024       # embedding 向量维度
    distance: str = "Cosine"      # "Cosine" | "Dot" | "Euclid"
    grpc_port: int = 6334         # gRPC 端口（默认 6334）


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
class WebSocketConfig:
    max_per_user: int = 5           # 单用户最大连接数
    max_global: int = 10000         # 全局最大连接数
    heartbeat_interval: int = 30    # 心跳间隔（秒）
    heartbeat_timeout: int = 60     # 心跳超时（秒）
    ping_timeout: int = 5           # ping 响应超时（秒）
    rate_limit_per_user: int = 30   # 用户消息限流（条/分钟）
    rate_limit_per_conn: int = 20   # 连接消息限流（条/分钟）
    rate_limit_window: int = 60     # 限流窗口（秒）
    max_message_size: int = 1048576  # 单条消息最大字节数（1MB）


@dataclass
class IngestConfig:
    auto_ingest: bool = False
    data_dir: str = "data"
    chunk_size: int = 512          # tokens（token-based 分块，中英文公平）
    chunk_overlap: int = 50        # 重叠 tokens 数
    chunk_unit: str = "token"      # "token" | "char"，默认 token
    batch_size: int = 10


@dataclass
class ProductConfig:
    dict_path: str = "data/product_dict.json"      # 字典文件路径
    reload_interval_seconds: int = 3600            # 自动重载间隔（秒）
    fuzzy_threshold: int = 80                      # 模糊匹配阈值（0-100）
    max_batch_size: int = 100                      # 批量查询最大数量
    enabled: bool = True                           # 是否启用商品检索模块


@dataclass
class STTConfig:
    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com/api-v1/services/asr"
    model: str = "paraformer-realtime-v2"
    sample_rate: int = 16000
    format: str = "wav"
    timeout: int = 30


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    db: DBConfig = field(default_factory=DBConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    milvus: MilvusConfig = field(default_factory=MilvusConfig)
    qdrant: QdrantConfig = field(default_factory=QdrantConfig)
    celery: CeleryConfig = field(default_factory=CeleryConfig)
    rag: RAGConfig = field(default_factory=RAGConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    websocket: WebSocketConfig = field(default_factory=WebSocketConfig)
    ingest: IngestConfig = field(default_factory=IngestConfig)
    product: ProductConfig = field(default_factory=ProductConfig)  # 商品检索配置
    stt: STTConfig = field(default_factory=STTConfig)  # STT 语音识别配置
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    debug: bool = False


def _str_to_bool(val: str | None) -> bool:
    if val is None:
        return False
    return val.lower() in ("1", "true", "yes")


@lru_cache
def get_config() -> AppConfig:
    llm_provider = os.getenv("LLM_PROVIDER", "mock")
    if llm_provider == "openai":
        llm_model_name = os.getenv("OPENAI_MODEL", os.getenv("LLM_MODEL_NAME", "gpt-4.1-mini"))
        llm_fallback_models = os.getenv("OPENAI_FALLBACK_MODELS", os.getenv("LLM_FALLBACK_MODELS", ""))
        llm_api_key = os.getenv("OPENAI_API_KEY", os.getenv("DASHSCOPE_API_KEY", ""))
        llm_base_url = os.getenv("OPENAI_BASE_URL", os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"))
    else:
        llm_model_name = os.getenv("LLM_MODEL_NAME", "qwen-max")
        llm_fallback_models = os.getenv("LLM_FALLBACK_MODELS", "qwen-plus,qwen-turbo")
        llm_api_key = os.getenv("DASHSCOPE_API_KEY", "")
        llm_base_url = os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com")

    return AppConfig(
        llm=LLMConfig(
            provider=llm_provider,
            model_name=llm_model_name,
            fallback_models=[m for m in llm_fallback_models.split(",") if m],
            api_key=llm_api_key,
            base_url=llm_base_url,
            timeout=int(os.getenv("LLM_TIMEOUT", "60")),
            max_retries=int(os.getenv("LLM_MAX_RETRIES", "3")),
            default_temperature=float(os.getenv("LLM_TEMPERATURE", "0.7")),
            default_max_tokens=int(os.getenv("LLM_MAX_TOKENS", "2048")),
            max_input_tokens=int(os.getenv("LLM_MAX_INPUT_TOKENS", "4096")),
            max_output_tokens=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "2048")),
        ),
        embedding=EmbeddingConfig(
            provider=os.getenv("EMBEDDING_PROVIDER", "dashscope"),
            model_name=os.getenv("EMBEDDING_MODEL_NAME", "text-embedding-v3"),
            api_key=os.getenv("EMBEDDING_API_KEY", os.getenv("DASHSCOPE_API_KEY", "")),
            base_url=os.getenv("EMBEDDING_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            dim=int(os.getenv("EMBEDDING_DIM", "1024")),
        ),
        db=DBConfig(
            host=os.getenv("MYSQL_HOST", "localhost"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=os.getenv("MYSQL_DATABASE", "lumapepe"),
            pool_size=int(os.getenv("MYSQL_POOL_SIZE", "10")),
            max_overflow=int(os.getenv("MYSQL_MAX_OVERFLOW", "20")),
            pool_recycle=int(os.getenv("MYSQL_POOL_RECYCLE", "3600")),
            pool_timeout=int(os.getenv("MYSQL_POOL_TIMEOUT", "30")),
        ),
        redis=RedisConfig(
            url=os.getenv("REDIS_URL", "redis://localhost:6379"),
            db=int(os.getenv("REDIS_DB", "0")),
            pool_size=int(os.getenv("REDIS_POOL_SIZE", "10")),
            socket_timeout=int(os.getenv("REDIS_SOCKET_TIMEOUT", "5")),
            socket_connect_timeout=int(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", "5")),
            required=_str_to_bool(os.getenv("REDIS_REQUIRED", "false")),
        ),
        milvus=MilvusConfig(
            host=os.getenv("MILVUS_HOST", "localhost") or "localhost",
            port=int(os.getenv("MILVUS_PORT") or "19530"),
            collection_name=os.getenv("MILVUS_COLLECTION", "knowledge_base"),
            entity_count_hint=int(os.getenv("MILVUS_ENTITY_COUNT_HINT", "1000000")),
        ),
        qdrant=QdrantConfig(
            host=os.getenv("QDRANT_HOST", "localhost") or "localhost",
            port=int(os.getenv("QDRANT_PORT") or "6333"),
            collection_name=os.getenv("QDRANT_COLLECTION", "knowledge_base"),
            timeout=int(os.getenv("QDRANT_TIMEOUT") or "30"),
            enabled=_str_to_bool(os.getenv("QDRANT_ENABLED", "false")),
            prefer_grpc=_str_to_bool(os.getenv("QDRANT_PREFER_GRPC", "true")),
            vector_dim=int(os.getenv("QDRANT_VECTOR_DIM", "1024")),
            distance=os.getenv("QDRANT_DISTANCE", "Cosine"),
            grpc_port=int(os.getenv("QDRANT_GRPC_PORT") or "6334"),
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
        websocket=WebSocketConfig(
            max_per_user=int(os.getenv("WS_MAX_PER_USER", "5")),
            max_global=int(os.getenv("WS_MAX_GLOBAL", "10000")),
            heartbeat_interval=int(os.getenv("WS_HEARTBEAT_INTERVAL", "30")),
            heartbeat_timeout=int(os.getenv("WS_HEARTBEAT_TIMEOUT", "60")),
            ping_timeout=int(os.getenv("WS_PING_TIMEOUT", "5")),
            rate_limit_per_user=int(os.getenv("WS_RATE_LIMIT_PER_USER", "30")),
            rate_limit_per_conn=int(os.getenv("WS_RATE_LIMIT_PER_CONN", "20")),
            rate_limit_window=int(os.getenv("WS_RATE_LIMIT_WINDOW", "60")),
            max_message_size=int(os.getenv("WS_MAX_MESSAGE_SIZE", "1048576")),
        ),
        ingest=IngestConfig(
            auto_ingest=_str_to_bool(os.getenv("AUTO_INGEST", "false")),
            data_dir=os.getenv("DATA_DIR", "data"),
            chunk_size=int(os.getenv("CHUNK_SIZE", "512")),
            chunk_overlap=int(os.getenv("CHUNK_OVERLAP", "50")),
            chunk_unit=os.getenv("CHUNK_UNIT", "token"),
            batch_size=int(os.getenv("INGEST_BATCH_SIZE", "10")),
        ),
        product=ProductConfig(
            dict_path=os.getenv("PRODUCT_DICT_PATH", "data/product_dict.json"),
            reload_interval_seconds=int(os.getenv("PRODUCT_RELOAD_INTERVAL", "3600")),
            fuzzy_threshold=int(os.getenv("PRODUCT_FUZZY_THRESHOLD", "80")),
            max_batch_size=int(os.getenv("PRODUCT_MAX_BATCH_SIZE", "100")),
            enabled=_str_to_bool(os.getenv("PRODUCT_ENABLED", "true")),
        ),
        stt=STTConfig(
            api_key=os.getenv("DASHSCOPE_API_KEY", ""),
            base_url=os.getenv("STT_BASE_URL", "https://dashscope.aliyuncs.com/api-v1/services/asr"),
            model=os.getenv("STT_MODEL", "paraformer-realtime-v2"),
            timeout=int(os.getenv("STT_TIMEOUT", "30")),
        ),
        cors_origins=os.getenv("CORS_ORIGINS", "").split(","),
        debug=_str_to_bool(os.getenv("DEBUG")),
    )
