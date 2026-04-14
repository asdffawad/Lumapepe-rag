"""
API 层测试
"""
import pytest


@pytest.mark.asyncio
async def test_health_check(client):
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data


@pytest.mark.asyncio
async def test_intent_classification(client):
    response = await client.post(
        "/api/v1/intent",
        json={"message": "iPhone 16 手机支持5G吗？", "history": []},
    )
    assert response.status_code == 200
    data = response.json()
    assert "intent" in data
    assert data["intent"] == "product_qa"
    assert "confidence" in data


@pytest.mark.asyncio
async def test_intent_order_query(client):
    response = await client.post(
        "/api/v1/intent",
        json={"message": "我的订单到哪了？", "history": []},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "order_query"


@pytest.mark.asyncio
async def test_chat_endpoint_deprecated(client):
    """REST /api/v1/chat 已废弃，应返回 410 Gone"""
    response = await client.post(
        "/api/v1/chat",
        json={
            "message": "iPhone 16 手机价格多少？",
            "user_id": "test-user",
            "session_id": None,
        },
    )
    assert response.status_code == 410
    data = response.json()
    assert data["error"]["code"] == "ENDPOINT_DEPRECATED"


@pytest.mark.asyncio
async def test_chat_with_session_deprecated(client):
    """REST /api/v1/chat 已废弃，应返回 410 Gone"""
    response = await client.post(
        "/api/v1/chat",
        json={"message": "我想买手机", "user_id": "test-user"},
    )
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "ENDPOINT_DEPRECATED"


@pytest.mark.asyncio
async def test_chat_history(client):
    """REST /api/v1/chat 已废弃，但 /api/v1/chat/history 仍然可用"""
    # 先通过 WebSocket 创建会话（这里无法测试），直接测试 history 端点
    import uuid
    fake_session_id = str(uuid.uuid4())
    response = await client.get(f"/api/v1/chat/history/{fake_session_id}")
    # 存在会话返回历史，不存在返回空列表
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == fake_session_id
    assert "history" in data


@pytest.mark.asyncio
async def test_chat_message_validation(client):
    """测试消息长度验证"""
    response = await client.post(
        "/api/v1/chat",
        json={"message": "", "user_id": "test-user"},
    )
    assert response.status_code == 422  # Validation error
