"""
WebSocket 端到端集成测试
"""
import pytest
import pytest_asyncio
import starlette.websockets
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.middleware.auth import create_token
from app.api import chat
from app.services.rag_orchestrator import RAGOrchestrator
from app.services.intent_service import RuleBasedIntentClassifier
from app.services.query_rewriter import MockQueryRewriter
from app.services.vector_retriever import MockVectorRetriever
from app.services.reranker import MockReranker
from app.services.prompt_builder import MockPromptBuilder
from app.services.llm_generator import MockLLMGenerator
from app.config import RAGConfig


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


_inmemory_svc = InMemorySessionService()


@pytest.fixture
def sync_client():
    """同步 TestClient 用于 WebSocket 测试"""
    # 初始化 app.state
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

    # 用 dependency_overrides 替换 SessionService
    async def mock_session_svc_factory():
        return _inmemory_svc

    app.dependency_overrides[chat.get_session_service] = mock_session_svc_factory

    client = TestClient(app)
    yield client

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def async_client():
    """异步 AsyncClient 用于 REST API 测试"""
    # 初始化 app.state
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

    # 用 dependency_overrides 替换 SessionService
    async def mock_session_svc_factory():
        return _inmemory_svc

    app.dependency_overrides[chat.get_session_service] = mock_session_svc_factory

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


class TestWebSocketEndpoint:
    """测试 WebSocket 端点"""

    def test_ws_missing_token(self, sync_client):
        """测试缺少 token 的连接 - 服务器应拒绝连接"""
        with pytest.raises(starlette.websockets.WebSocketDisconnect):
            with sync_client.websocket_connect("/ws/chat") as websocket:
                websocket.receive_json()

    def test_ws_invalid_token(self, sync_client):
        """测试无效 token 的连接"""
        with pytest.raises(starlette.websockets.WebSocketDisconnect):
            with sync_client.websocket_connect("/ws/chat?token=invalid") as websocket:
                websocket.receive_json()


@pytest.mark.asyncio
class TestDeprecatedEndpoints:
    """测试废弃的 REST 接口"""

    async def test_ws_deprecated_endpoints(self, async_client):
        """测试废弃的 REST 接口返回 410"""
        token = create_token(user_id="test-user")

        # POST /api/v1/chat 应该返回 410
        response = await async_client.post(
            "/api/v1/chat",
            json={"message": "test", "user_id": "test-user"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 410
        assert response.json()["error"]["code"] == "ENDPOINT_DEPRECATED"

        # POST /api/v1/chat/stream 应该返回 410
        response = await async_client.post(
            "/api/v1/chat/stream",
            json={"message": "test", "user_id": "test-user"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 410
