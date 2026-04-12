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
async def test_chat_endpoint(client):
    response = await client.post(
        "/api/v1/chat",
        json={
            "message": "iPhone 16 手机价格多少？",
            "user_id": "test-user",
            "session_id": None,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data
    assert "message" in data
    assert "intent" in data
    assert data["intent"] == "product_qa"


@pytest.mark.asyncio
async def test_chat_with_session(client):
    # First chat
    r1 = await client.post(
        "/api/v1/chat",
        json={"message": "我想买手机", "user_id": "test-user"},
    )
    session_id = r1.json()["session_id"]

    # Second chat with session
    r2 = await client.post(
        "/api/v1/chat",
        json={
            "message": "这个有没有5G功能？",
            "user_id": "test-user",
            "session_id": session_id,
        },
    )
    assert r2.status_code == 200
    assert r2.json()["session_id"] == session_id


@pytest.mark.asyncio
async def test_chat_history(client):
    # Create session and add messages
    r1 = await client.post(
        "/api/v1/chat",
        json={"message": "MacBook 价格", "user_id": "test-user"},
    )
    session_id = r1.json()["session_id"]

    # Get history
    r2 = await client.get(f"/api/v1/chat/history/{session_id}")
    assert r2.status_code == 200
    data = r2.json()
    assert data["session_id"] == session_id
    assert "history" in data


@pytest.mark.asyncio
async def test_chat_message_validation(client):
    """测试消息长度验证"""
    response = await client.post(
        "/api/v1/chat",
        json={"message": "", "user_id": "test-user"},
    )
    assert response.status_code == 422  # Validation error
