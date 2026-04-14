"""
pytest 配置文件
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport


pytest_plugins = ["pytest_asyncio"]


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


class InMemorySessionService:
    """测试用内存会话服务，不依赖 MySQL/Redis"""

    def __init__(self):
        self._sessions = {}
        self._messages = {}

    async def get_or_create_session(self, session_id, user_id):
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        import uuid
        sid = session_id or str(uuid.uuid4())
        ctx = type("obj", (object,), {
            "session_id": sid,
            "user_id": user_id,
            "intent": None,
            "history": [],
            "last_query_hash": None,
        })()
        self._sessions[sid] = ctx
        self._messages.setdefault(sid, [])
        return ctx

    async def update_session(self, session_id, intent=None, query_hash=None):
        if session_id in self._sessions:
            ctx = self._sessions[session_id]
            if intent:
                ctx.intent = intent
            if query_hash:
                ctx.last_query_hash = query_hash

    async def add_message(self, session_id, role, content, intent=None, sources=None, tokens=None, latency_ms=None):
        self._messages.setdefault(session_id, []).append({
            "role": role, "content": content, "intent": intent
        })

    async def get_history(self, session_id, limit=10):
        msgs = self._messages.get(session_id, [])
        return msgs[-limit:]

    async def is_duplicate_query(self, session_id, query):
        import hashlib
        h = hashlib.md5(query.encode()).hexdigest()
        return getattr(self._sessions.get(session_id), "last_query_hash", None) == h

    async def mark_query_processed(self, session_id, query):
        import hashlib
        h = hashlib.md5(query.encode()).hexdigest()
        await self.update_session(session_id, query_hash=h)


# 全局实例
_inmemory_svc = InMemorySessionService()


@pytest.fixture
def reset_ws_manager_fixture():
    """每个测试前后重置 WebSocket 连接管理器（用于其他测试模块）"""
    from app.api.ws_manager import reset_connection_manager
    reset_connection_manager()
    yield
    reset_connection_manager()


@pytest_asyncio.fixture
async def client():
    from app.main import app
    from app.api import chat
    from app.services.rag_orchestrator import RAGOrchestrator
    from app.services.intent_service import RuleBasedIntentClassifier
    from app.services.query_rewriter import MockQueryRewriter
    from app.services.vector_retriever import MockVectorRetriever
    from app.services.reranker import MockReranker
    from app.services.prompt_builder import MockPromptBuilder
    from app.services.llm_generator import MockLLMGenerator
    from app.config import RAGConfig

    # 初始化 app.state（lifespan 不在测试中运行）
    if not hasattr(app.state, "orchestrator"):
        config = RAGConfig()
        app.state.orchestrator = RAGOrchestrator(
            rewriter=MockQueryRewriter(),
            retriever=MockVectorRetriever(),
            reranker=MockReranker(),
            prompt_builder=MockPromptBuilder(),
            generator=MockLLMGenerator(),
            config=config,
        )
    if not hasattr(app.state, "intent_classifier"):
        app.state.intent_classifier = RuleBasedIntentClassifier()

    # 用 FastAPI dependency_overrides 替换 SessionService
    async def mock_session_svc_factory():
        return _inmemory_svc

    app.dependency_overrides[chat.get_session_service] = mock_session_svc_factory

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    # 清理
    app.dependency_overrides.clear()
