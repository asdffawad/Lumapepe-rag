"""
WebSocket 连接管理器测试
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.api.ws_manager import (
    ConnectionManager,
    SlidingWindowLimiter,
    get_connection_manager,
    reset_connection_manager,
)


@pytest.fixture(autouse=True)
def clean_ws_manager():
    """每个测试前后重置 WebSocket 连接管理器"""
    # 重置模块级单例
    reset_connection_manager()
    # 重置类级单例
    ConnectionManager._instance = None
    yield
    # 测试后清理
    reset_connection_manager()
    ConnectionManager._instance = None


class TestSlidingWindowLimiter:
    """测试滑动窗口限流器"""

    def test_memory_limiter_allows(self):
        """测试内存限流器允许请求"""
        limiter = SlidingWindowLimiter(max_requests=10, window_seconds=60)
        allowed, remaining = limiter._memory_fallback("test-key", 1000.0, 940.0)
        assert allowed is True
        assert remaining == 9
        assert len(limiter._memory["test-key"]) == 1

    def test_memory_limiter_blocks(self):
        """测试内存回退超限时拒绝"""
        limiter = SlidingWindowLimiter(max_requests=2, window_seconds=60)
        
        # 添加 2 个请求
        allowed1, remaining1 = limiter._memory_fallback("test-key", 1000.0, 940.0)
        allowed2, remaining2 = limiter._memory_fallback("test-key", 1001.0, 940.0)
        
        assert allowed1 is True
        assert remaining1 == 1
        assert allowed2 is True
        assert remaining2 == 0
        
        # 第 3 个请求应被拒绝
        allowed3, remaining3 = limiter._memory_fallback("test-key", 1002.0, 940.0)
        assert allowed3 is False
        assert remaining3 == 0

    def test_memory_fallback_cleans_old_timestamps(self):
        """测试内存回退清理过期时间戳"""
        limiter = SlidingWindowLimiter(max_requests=10, window_seconds=60)
        
        # 添加旧时间戳
        limiter._memory["test-key"] = [800.0, 850.0, 900.0]  # 全部在 window_start 之前
        allowed, remaining = limiter._memory_fallback("test-key", 1000.0, 940.0)
        
        # 只有当前请求会被添加
        assert allowed is True
        assert remaining == 9
        assert len(limiter._memory["test-key"]) == 1


class TestConnectionManager:
    """测试连接管理器"""

    @pytest.fixture
    def mock_config(self):
        class MockWSConfig:
            max_per_user = 3
            max_global = 100
            heartbeat_interval = 30
            heartbeat_timeout = 60
            rate_limit_per_user = 30
            rate_limit_per_conn = 20
            rate_limit_window = 60
            max_message_size = 65536
        return MockWSConfig()

    @pytest.mark.asyncio
    async def test_check_connection_limits(self, mock_config):
        """测试连接限制允许新连接"""
        manager = get_connection_manager(mock_config)

        # 应该允许
        allowed, code, msg = await manager.check_connection_limits("user-1")
        assert allowed is True
        assert code == ""
        assert msg == ""

    @pytest.mark.asyncio
    async def test_user_connection_limit(self, mock_config):
        """测试单用户连接数限制"""
        # 先修改 mock_config
        mock_config.max_per_user = 2
        # 重置单例，这样会使用新的 config 创建 manager
        ConnectionManager._instance = None
        reset_connection_manager()
        
        manager = get_connection_manager(mock_config)

        # 模拟建立连接
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.close = AsyncMock()
        
        # 连接 2 个
        await manager.connect(mock_ws, "session-1", "user-1")
        mock_ws2 = AsyncMock()
        mock_ws2.accept = AsyncMock()
        mock_ws2.close = AsyncMock()
        await manager.connect(mock_ws2, "session-2", "user-1")

        # 第三个应该被拒绝
        allowed, code, msg = await manager.check_connection_limits("user-1")
        assert allowed is False
        assert code == "TOO_MANY_CONNECTIONS"

        # 清理
        await manager.disconnect("session-1")
        await manager.disconnect("session-2")

    @pytest.mark.asyncio
    async def test_global_connection_limit(self, mock_config):
        """测试全局连接数限制"""
        # 先修改 mock_config
        mock_config.max_global = 2
        # 重置单例
        ConnectionManager._instance = None
        reset_connection_manager()
        
        manager = get_connection_manager(mock_config)

        # 建立 2 个连接
        mock_ws1 = AsyncMock()
        mock_ws1.accept = AsyncMock()
        mock_ws1.close = AsyncMock()
        await manager.connect(mock_ws1, "session-1", "user-1")
        
        mock_ws2 = AsyncMock()
        mock_ws2.accept = AsyncMock()
        mock_ws2.close = AsyncMock()
        await manager.connect(mock_ws2, "session-2", "user-2")

        # 第三个应该被拒绝
        allowed, code, msg = await manager.check_connection_limits("user-3")
        assert allowed is False
        assert code == "SERVER_FULL"

        # 清理
        await manager.disconnect("session-1")
        await manager.disconnect("session-2")

    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self, mock_config):
        """测试连接和断开"""
        manager = get_connection_manager(mock_config)

        # 模拟 WebSocket
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.close = AsyncMock()

        # 连接
        success, code, msg = await manager.connect(mock_ws, "session-1", "user-1")
        assert success is True
        assert manager.get_active_count() == 1
        assert manager.get_user_count("user-1") == 1

        # 断开
        await manager.disconnect("session-1")
        assert manager.get_active_count() == 0
        assert manager.get_user_count("user-1") == 0

    @pytest.mark.asyncio
    async def test_send_json(self, mock_config):
        """测试发送 JSON 消息成功"""
        manager = get_connection_manager(mock_config)

        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.send_json = AsyncMock()
        mock_ws.close = AsyncMock()

        await manager.connect(mock_ws, "session-1", "user-1")

        result = await manager.send_json("session-1", {"type": "test"})
        assert result is True
        mock_ws.send_json.assert_called_once_with({"type": "test"})

        await manager.disconnect("session-1")

    @pytest.mark.asyncio
    async def test_send_json_nonexistent_session(self, mock_config):
        """测试向不存在的会话发送消息"""
        manager = get_connection_manager(mock_config)
        result = await manager.send_json("nonexistent", {"type": "test"})
        assert result is False

    @pytest.mark.asyncio
    async def test_broadcast(self, mock_config):
        """测试向用户广播消息"""
        manager = get_connection_manager(mock_config)

        mock_ws1 = AsyncMock()
        mock_ws1.accept = AsyncMock()
        mock_ws1.send_json = AsyncMock()
        mock_ws1.close = AsyncMock()

        mock_ws2 = AsyncMock()
        mock_ws2.accept = AsyncMock()
        mock_ws2.send_json = AsyncMock()
        mock_ws2.close = AsyncMock()

        await manager.connect(mock_ws1, "session-1", "user-1")
        await manager.connect(mock_ws2, "session-2", "user-1")

        count = await manager.broadcast("user-1", {"type": "broadcast"})
        assert count == 2

        # 清理
        await manager.disconnect("session-1")
        await manager.disconnect("session-2")

    @pytest.mark.asyncio
    async def test_broadcast_no_connections(self, mock_config):
        """测试向无连接用户广播"""
        manager = get_connection_manager(mock_config)
        count = await manager.broadcast("user-1", {"type": "broadcast"})
        assert count == 0

    @pytest.mark.asyncio
    async def test_is_connected(self, mock_config):
        """测试检查连接状态"""
        manager = get_connection_manager(mock_config)

        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.close = AsyncMock()

        assert manager.is_connected("session-1") is False

        await manager.connect(mock_ws, "session-1", "user-1")
        assert manager.is_connected("session-1") is True

        await manager.disconnect("session-1")
        assert manager.is_connected("session-1") is False

    @pytest.mark.asyncio
    async def test_get_user_sessions(self, mock_config):
        """测试获取用户会话列表"""
        manager = get_connection_manager(mock_config)

        mock_ws1 = AsyncMock()
        mock_ws1.accept = AsyncMock()
        mock_ws1.close = AsyncMock()

        mock_ws2 = AsyncMock()
        mock_ws2.accept = AsyncMock()
        mock_ws2.close = AsyncMock()

        await manager.connect(mock_ws1, "session-1", "user-1")
        await manager.connect(mock_ws2, "session-2", "user-1")

        sessions = manager.get_user_sessions("user-1")
        assert len(sessions) == 2
        assert "session-1" in sessions
        assert "session-2" in sessions

        # 清理
        await manager.disconnect("session-1")
        await manager.disconnect("session-2")

    @pytest.mark.asyncio
    async def test_rate_limit(self, mock_config):
        """测试限流检查：前2个请求通过，第3个被限流"""
        # 修改限流配置为小值以便测试限流
        mock_config.rate_limit_per_user = 2
        ConnectionManager._instance = None
        reset_connection_manager()
        manager = get_connection_manager(mock_config)

        # 第1个请求应该通过
        allowed1, user_remaining1, conn_remaining1 = await manager.check_rate_limit("user-1", "session-1")
        assert allowed1 is True

        # 第2个请求应该通过
        allowed2, user_remaining2, conn_remaining2 = await manager.check_rate_limit("user-1", "session-2")
        assert allowed2 is True

        # 第3个请求应该被限流
        allowed3, user_remaining3, conn_remaining3 = await manager.check_rate_limit("user-1", "session-3")
        assert allowed3 is False