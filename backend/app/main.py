"""
FastAPI 应用主入口
路由注册、中间件配置、依赖注入
严格遵循 fastapi-best-practices.mdc 规范
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status as http_status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_config
from app.database.connection import create_db_pool, close_db_pool, get_db_pool
from app.cache.redis_client import create_redis_pool, close_redis_pool, get_redis_client
from app.vectorstore.milvus_client import MilvusClient
from app.services.llm_service import LLMFactory
from app.services.embedding_service import EmbeddingService
from app.services.rag_orchestrator import RAGOrchestrator
from app.services.intent_service import (
    IntentClassifier,
    HybridIntentClassifier,
    RuleBasedIntentClassifier,
)
from app.services.query_rewriter import MockQueryRewriter, EcommerceQueryRewriter
from app.services.vector_retriever import MockVectorRetriever, SimpleVectorRetriever
from app.services.reranker import MockReranker, MMRReranker
from app.services.prompt_builder import MockPromptBuilder, EcommercePromptBuilder
from app.services.llm_generator import MockLLMGenerator, StandardLLMGenerator
from app.services.stt_service import STTFactory, STTConfig as STTServiceConfig
from app.api import chat, intent, health, admin, debug, websocket, product, voice_ws, kefu
from app import metrics
from app.logging_config import get_logger as _get_logger

# ---------------------------------------------------------------------------
# 在导入时创建同步 Redis 池（供限流中间件在应用启动前使用）
# ---------------------------------------------------------------------------
import os as _os

_sync_redis_pool = None


def _get_sync_redis_pool():
    """获取同步 Redis 连接池（用于限流中间件）"""
    global _sync_redis_pool
    if _sync_redis_pool is None:
        import redis as _sync_redis
        redis_url = _os.getenv("REDIS_URL", "redis://localhost:6379")
        _sync_redis_pool = _sync_redis.ConnectionPool.from_url(redis_url, decode_responses=False)
    return _sync_redis_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    config = get_config()

    # 连接 MySQL
    logger = _get_logger("app.startup")
    db_log_context = {
        "db_host": config.db.host,
        "db_port": config.db.port,
        "db_name": config.db.database,
        "db_user": config.db.user,
        "db_connect_retries": config.db.connect_retries,
        "db_connect_timeout": config.db.connect_timeout,
    }
    logger.info("MySQL config loaded", extra=db_log_context)
    try:
        pool = await create_db_pool(config.db)
        app.state.db_pool = pool
        logger.info("MySQL connected", extra=db_log_context)
    except Exception as e:
        error_message = str(e).replace(config.db.password, "***") if config.db.password else str(e)
        logger.error(
            "MySQL connection failed",
            extra={
                **db_log_context,
                "error": f"{type(e).__name__}: {error_message}",
            },
        )
        raise

    # 连接 Redis（异步客户端）
    app.state.redis_pool = None
    app.state.redis_client = None
    try:
        pool = await create_redis_pool(config.redis.url)
        app.state.redis_pool = pool
        app.state.redis_client = get_redis_client()
        from app.services.redis_pubsub import mask_redis_url
        logger.info("Redis connected", extra={"url": mask_redis_url(config.redis.url)})
    except Exception as e:
        logger.error("Redis connection failed", extra={"error": str(e)})

    # 启动人工客服 Redis Pub/Sub。Redis 不可用时不阻断基础 HTTP/DB 功能，
    # REDIS_REQUIRED=true 时才让启动失败。
    try:
        from app.services.redis_pubsub import get_redis_pubsub_manager
        kefu_pubsub = get_redis_pubsub_manager(config.redis.url, redis_required=config.redis.required)
        app.state.kefu_pubsub_manager = kefu_pubsub
        connected = await kefu_pubsub.connect()
        logger.info(
            "Kefu Redis Pub/Sub initialized",
            extra={
                "connected": connected,
                "channel": kefu_pubsub.channel,
                "instance_id": kefu_pubsub.instance_id,
            },
        )
    except Exception as e:
        logger.warning("Kefu Redis Pub/Sub init failed", extra={"error": str(e)})
        if config.redis.required:
            raise

    # 初始化 Embedding 服务
    EmbeddingService.init(config, redis_client=app.state.redis_client)

    # 初始化 LLM
    llm = LLMFactory.create(config.llm.provider, config.llm)

    # 初始化商品检索模块（需在 Orchestrator 之前创建）
    from app.etl.dict_loader import ProductDictLoader
    from app.services.product_service import ProductService

    dict_loader = ProductDictLoader(
        dict_path=config.product.dict_path,
        reload_interval_seconds=config.product.reload_interval_seconds,
    )

    product_service = ProductService(
        dict_loader=dict_loader,
        max_batch_size=config.product.max_batch_size,
    )

    # 初始化向量数据库（Milvus 或 Qdrant）
    vector_client = None
    vector_type = None

    # 优先尝试 Qdrant（如果启用）
    if config.qdrant.enabled:
        try:
            from app.vectorstore.qdrant_adapter import QdrantClient
            qdrant_client = QdrantClient(config.qdrant)
            await qdrant_client.connect()
            vector_client = qdrant_client
            vector_type = "qdrant"
            app.state.milvus_client = qdrant_client  # 保持兼容
            logger.info("Qdrant connected", extra={"host": config.qdrant.host, "port": config.qdrant.port})
        except Exception as e:
            import traceback as _tb
            logger.warning(f"Qdrant connection failed: {type(e).__name__}: {e}")

    # 尝试 Milvus（如果 Qdrant 未启用或连接失败）
    if vector_client is None:
        try:
            milvus_client = MilvusClient(config.milvus)
            await milvus_client.connect()
            vector_client = milvus_client
            vector_type = "milvus"
            app.state.milvus_client = milvus_client
            logger.info("Milvus connected", extra={"host": config.milvus.host, "port": config.milvus.port})
        except Exception as e:
            import traceback as _tb
            logger.warning(
                f"Milvus connection failed: {type(e).__name__}: {e}"
            )

    # 初始化 RAG 子模块（优先用 Mock，方便本地测试）
    if config.llm.provider == "mock":
        rewriter = MockQueryRewriter()
        retriever = MockVectorRetriever()
        reranker = MockReranker()
        prompt_builder = MockPromptBuilder()
        generator = MockLLMGenerator()
    else:
        # 启用 LLM 增强改写（query_rewriter 会自动判断场景是否需要）
        rewriter = EcommerceQueryRewriter(llm, use_llm_rewrite=True)
        if vector_client is not None:
            retriever = SimpleVectorRetriever(vector_client)
        else:
            retriever = MockVectorRetriever()
        reranker = MMRReranker(llm)
        prompt_builder = EcommercePromptBuilder(llm, config.rag.max_context_tokens)
        generator = StandardLLMGenerator(llm)

    # 初始化 RAG Orchestrator
    orchestrator = RAGOrchestrator(
        rewriter=rewriter,
        retriever=retriever,
        reranker=reranker,
        prompt_builder=prompt_builder,
        generator=generator,
        config=config.rag,
        product_service=product_service,
    )
    app.state.orchestrator = orchestrator

    # 初始化意图分类器
    if config.llm.provider == "mock":
        intent_classifier: IntentClassifier = RuleBasedIntentClassifier()
    else:
        intent_classifier = HybridIntentClassifier(llm)
    app.state.intent_classifier = intent_classifier

    app.state.product_service = product_service

    # 注册API路由
    from app.api.product import set_product_service
    set_product_service(product_service)
    app.include_router(product.router)

    # 尝试加载字典（非阻塞）
    try:
        if Path(config.product.dict_path).exists():
            await dict_loader.load()
            logger.info("Product dictionary loaded", extra={
                "path": config.product.dict_path,
                "version": dict_loader.version,
            })
            # 启动定时重载
            if config.product.enabled:
                await dict_loader.start_auto_reload()
        else:
            logger.warning(
                "Product dictionary not found, run ETL first",
                extra={"path": config.product.dict_path}
            )
    except Exception as e:
        logger.error("Failed to load product dictionary", extra={"error": str(e)})

    # 检查是否需要自动知识库摄取
    if config.ingest.auto_ingest:
        try:
            entity_count = await vector_client.num_entities()
            if entity_count == 0:
                logger.info("Knowledge base is empty, triggering auto-ingestion...")
                # 延迟导入避免循环依赖
                import subprocess, sys
                project_root = Path(__file__).resolve().parent.parent.parent
                script_path = project_root / "scripts" / "ingest_data.py"
                result = subprocess.run(
                    [sys.executable, str(script_path)],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    env={**_os.environ, "PYTHONPATH": str(project_root / "backend")},
                )
                if result.returncode == 0:
                    logger.info("Auto-ingestion completed successfully")
                else:
                    logger.warning(f"Auto-ingestion failed: {result.stderr[-500:]}")
            else:
                logger.info(f"Knowledge base already indexed: {entity_count} entities")
        except Exception as e:
            logger.warning(f"Auto-ingestion check failed: {e}")

    # 启动告警监控系统
    from app.services.alerting import get_monitor
    monitor = get_monitor()
    monitor.start()
    app.state.alert_monitor = monitor

    # 初始化 STT 语音识别服务
    stt_cfg = STTServiceConfig(
        api_key=config.stt.api_key,
        base_url=config.stt.base_url,
        model=config.stt.model,
        sample_rate=config.stt.sample_rate,
        format=config.stt.format,
        timeout=config.stt.timeout,
    )
    app.state.stt_provider = STTFactory.create("dashscope", stt_cfg)
    logger.info("STT provider initialized", extra={"model": config.stt.model})

    yield

    # Shutdown
    try:
        await monitor.stop()
    except Exception:
        pass
    try:
        if hasattr(app.state, "kefu_pubsub_manager") and app.state.kefu_pubsub_manager:
            await app.state.kefu_pubsub_manager.disconnect()
    except Exception:
        pass
    try:
        # 停止商品字典自动重载
        if hasattr(app.state, 'product_service') and app.state.product_service:
            try:
                await app.state.product_service._dict_loader.stop_auto_reload()
            except Exception:
                pass
    except Exception:
        pass
    try:
        await close_db_pool()
    except Exception:
        pass
    try:
        await close_redis_pool()
    except Exception:
        pass


def create_app() -> FastAPI:
    config = get_config()

    app = FastAPI(
        title="RAG 电商智能客服 API",
        description="基于 RAG 的电商智能客服系统，支持商品问答、订单查询、退换货辅助、多轮对话",
        version="1.0.0",
        lifespan=lifespan,
        debug=config.debug,
    )

    # 限流中间件（需在应用启动前注册，使用同步 Redis 池）
    if config.rate_limit.enabled:
        try:
            from app.middleware.auth import RateLimitMiddleware
            sync_pool = _get_sync_redis_pool()
            app.add_middleware(
                RateLimitMiddleware,
                redis_pool=sync_pool,
                global_limit=config.rate_limit.global_limit,
                user_limit=config.rate_limit.user_limit,
                window=config.rate_limit.window,
            )
        except Exception as e:
            logger.warning("RateLimitMiddleware init failed, skipping", extra={"error": str(e)})

    # CORS 中间件（生产环境应配置具体域名，不使用通配符）
    # 安全检查：不允许同时使用通配符和 credentials
    cors_origins = config.cors_origins
    allow_creds = True
    if "*" in cors_origins:
        if config.debug:
            import warnings
            warnings.warn(
                "[WARNING] CORS 配置不安全：allow_origins=['*'] + allow_credentials=True 会导致浏览器拒绝请求。"
                "生产环境请设置具体的 CORS_ORIGINS 域名列表。",
                UserWarning,
            )
            allow_creds = False
        else:
            raise ValueError(
                "CORS 配置不安全：生产环境禁止使用 allow_origins=['*']。"
                "请通过环境变量 CORS_ORIGINS 设置具体的域名列表，格式：https://example.com,https://app.example.com"
            )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=allow_creds,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID", "X-HTTP-Method-Override"],
    )

    # Request ID 中间件
    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    # HTTP Method Override 中间件（支持 _method 字段模拟 DELETE/PUT）
    @app.middleware("http")
    async def method_override_middleware(request: Request, call_next):
        if request.method == "POST":
            try:
                body = await request.body()
                if body:
                    import json
                    data = json.loads(body)
                    override = data.get("_method", "").upper()
                    if override in ("DELETE", "PUT", "PATCH"):
                        # 替换方法并重新构建请求
                        scope = request.scope
                        scope["method"] = override
                        request = Request(scope, request.receive)
            except Exception:
                pass
        response = await call_next(request)
        return response

    # 注册路由
    app.include_router(chat.router)
    app.include_router(chat.sessions_router)
    app.include_router(intent.router)
    app.include_router(health.router)
    app.include_router(health.public_router)
    app.include_router(admin.router)
    app.include_router(kefu.router)
    app.include_router(debug.router)
    app.include_router(websocket.router)
    app.include_router(voice_ws.router)

    # 注册 metrics 路由（Prometheus 采集）
    app.include_router(metrics.router)

    # 全局异常处理（不泄露内部错误信息到客户端）
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        import traceback
        from app.logging_config import get_logger
        exc_logger = get_logger("app.exception")
        exc_logger.error(
            "Unhandled exception",
            extra={
                "request_id": getattr(request.state, "request_id", None),
                "path": str(request.url.path),
                "method": request.method,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        request_id = getattr(request.state, "request_id", None)
        exc_msg = str(exc)
        if "CHAT_ERROR" in exc_msg:
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "CHAT_ERROR",
                        "message": "聊天服务错误，请稍后重试",
                        "request_id": request_id,
                    }
                },
            )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "服务器内部错误，请稍后重试",
                    "request_id": request_id,
                }
            },
        )

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
