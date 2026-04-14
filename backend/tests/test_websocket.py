"""
WebSocket 端到端集成测试
"""
import pytest
from app.middleware.auth import create_token


class TestWebSocketEndpoint:
    """测试 WebSocket 端点"""

    @pytest.mark.asyncio
    async def test_ws_missing_token(self, client):
        """测试缺少 token 的连接"""
        with client.websocket_connect("/ws/chat") as websocket:
            # 应该收到关闭消息
            with pytest.raises(Exception):
                websocket.receive_json()

    @pytest.mark.asyncio
    async def test_ws_invalid_token(self, client):
        """测试无效 token 的连接"""
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/chat?token=invalid") as websocket:
                websocket.receive_json()

    @pytest.mark.asyncio
    async def test_ws_deprecated_endpoints(self, client):
        """测试废弃的 REST 接口返回 410"""
        token = create_token(user_id="test-user")

        # POST /api/v1/chat 应该返回 410
        response = await client.post(
            "/api/v1/chat",
            json={"message": "test", "user_id": "test-user"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 410
        assert response.json()["error"]["code"] == "ENDPOINT_DEPRECATED"

        # POST /api/v1/chat/stream 应该返回 410
        response = await client.post(
            "/api/v1/chat/stream",
            json={"message": "test", "user_id": "test-user"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 410
