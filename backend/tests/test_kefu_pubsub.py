from __future__ import annotations

import json

import pytest


class _FakeWebSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, payload):
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_local_connection_manager_dispatches_by_event_target():
    from app.api.kefu_ws_manager import KefuConnectionManager

    manager = KefuConnectionManager()
    user_ws = _FakeWebSocket()
    admin_ws = _FakeWebSocket()
    await manager.connect_user(user_ws, 123)
    await manager.connect_admin(admin_ws, 123)

    result = await manager.dispatch_event(
        {
            "event": "message.new",
            "conversation_id": 123,
            "target": "user",
            "source": "redis_pubsub",
            "source_instance_id": "backend-a",
            "created_at": "2026-05-02T12:00:00",
            "message": {"id": 456, "content": "您好，我是人工客服"},
        }
    )

    assert result["user_count"] == 1
    assert result["admin_count"] == 0
    assert user_ws.sent == [
        {
            "event": "message.new",
            "conversation_id": 123,
            "message": {"id": 456, "content": "您好，我是人工客服"},
        }
    ]
    assert admin_ws.sent == []


class _FakeRedis:
    def __init__(self):
        self.published: list[tuple[str, str]] = []

    async def ping(self):
        return True

    async def publish(self, channel, payload):
        self.published.append((channel, payload))
        return 1


@pytest.mark.asyncio
async def test_redis_pubsub_publish_event_uses_global_channel_and_instance_id():
    from app.services.redis_pubsub import RedisPubSubManager

    fake_redis = _FakeRedis()
    manager = RedisPubSubManager(
        redis_url="redis://:secret@redis:6379/0",
        redis_client=fake_redis,
        instance_id="backend-test",
    )

    published = await manager.publish_event(
        {
            "event": "message.new",
            "conversation_id": 123,
            "target": "user",
            "message": {"id": 456},
        }
    )

    assert published is True
    assert fake_redis.published[0][0] == "kefu:events"
    payload = json.loads(fake_redis.published[0][1])
    assert payload["event"] == "message.new"
    assert payload["conversation_id"] == 123
    assert payload["target"] == "user"
    assert payload["source_instance_id"] == "backend-test"
    assert payload["source"] == "redis_pubsub"
    assert "created_at" in payload
