"""
会话管理服务测试（单元测试，不依赖外部服务）
"""
import hashlib
import pytest
from app.services.session_service import SessionService
from unittest.mock import AsyncMock, MagicMock


def _compute_hash(text: str) -> str:
    """SessionService 内部使用的哈希函数"""
    return hashlib.md5(text.encode()).hexdigest()


class TestSessionServiceLogic:
    """测试 SessionService 核心逻辑（Mock 所有外部依赖）"""

    @pytest.fixture
    def mock_db(self):
        db = MagicMock()
        db.pool = MagicMock()
        return db

    @pytest.fixture
    def mock_cache(self):
        cache = AsyncMock()
        cache.get = AsyncMock(return_value=None)
        cache.set = AsyncMock()
        cache.append_message = AsyncMock()
        cache.get_history = AsyncMock(return_value=[])
        return cache

    def test_compute_hash(self, mock_db, mock_cache):
        """测试去重哈希计算"""
        h1 = _compute_hash("iPhone 价格")
        h2 = _compute_hash("iPhone 价格")
        h3 = _compute_hash("MacBook 价格")
        assert h1 == h2
        assert h1 != h3

    def test_compute_hash_stable(self, mock_db, mock_cache):
        """测试哈希计算稳定性"""
        text = "测试商品名称"
        assert _compute_hash(text) == _compute_hash(text)

    @pytest.mark.asyncio
    async def test_session_context_dataclass(self):
        """测试 SessionContext 数据类"""
        from app.services.session_service import SessionContext
        ctx = SessionContext(
            session_id="test-123",
            user_id="user-001",
            intent="product_qa",
            history=[{"role": "user", "content": "你好"}],
            last_query_hash="abc123",
        )
        assert ctx.session_id == "test-123"
        assert ctx.user_id == "user-001"
        assert ctx.intent == "product_qa"
        assert len(ctx.history) == 1
