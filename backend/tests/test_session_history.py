from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.middleware.auth import create_token


class FakeSessionRepo:
    def __init__(self):
        self.sessions = {
            "sess-1": SimpleNamespace(
                id="sess-1",
                user_id="288",
                member_user_id=288,
                intent="support",
                status="active",
                summary="用户咨询了物流进度",
                metadata={},
                handoff_status=2,
                kefu_conversation_id=100,
                tenant_id=0,
                handoff_at=datetime(2026, 5, 1, 10, 2, 0),
                created_at=datetime(2026, 5, 1, 10, 0, 0),
                updated_at=datetime(2026, 5, 1, 10, 5, 0),
            ),
            "sess-2": SimpleNamespace(
                id="sess-2",
                user_id="288",
                member_user_id=288,
                intent="after_sales",
                status="active",
                summary="用户咨询退换货政策",
                metadata={},
                handoff_status=0,
                kefu_conversation_id=None,
                tenant_id=0,
                handoff_at=None,
                created_at=datetime(2026, 5, 1, 8, 0, 0),
                updated_at=datetime(2026, 5, 1, 8, 15, 0),
            ),
            "sess-other": SimpleNamespace(
                id="sess-other",
                user_id="999",
                member_user_id=999,
                intent=None,
                status="active",
                summary="其他用户会话",
                metadata={},
                handoff_status=0,
                kefu_conversation_id=None,
                tenant_id=0,
                handoff_at=None,
                created_at=datetime(2026, 5, 1, 9, 0, 0),
                updated_at=datetime(2026, 5, 1, 9, 1, 0),
            ),
        }

    async def list_history_for_user(self, user_id, *, limit=20, offset=0):
        rows = []
        for session in self.sessions.values():
            if session.user_id != user_id and str(session.member_user_id) != str(user_id):
                continue
            rows.append({
                "id": session.id,
                "user_id": session.user_id,
                "member_user_id": session.member_user_id,
                "summary": session.summary,
                "intent": session.intent,
                "status": session.status,
                "handoff_status": session.handoff_status,
                "kefu_conversation_id": session.kefu_conversation_id,
                "title": "物流问题咨询" if session.id == "sess-1" else "售后政策咨询",
                "message_count": 6 if session.id == "sess-1" else 4,
                "last_message": "请帮我查一下物流" if session.id == "sess-1" else "怎么退货？",
                "last_message_role": "user",
                "last_message_at": datetime(2026, 5, 1, 10, 4, 0) if session.id == "sess-1" else datetime(2026, 5, 1, 8, 14, 0),
                "created_at": session.created_at,
                "updated_at": session.updated_at,
            })
        rows.sort(key=lambda row: row["last_message_at"], reverse=True)
        return rows[offset:offset + limit]

    async def count_history_for_user(self, user_id):
        return sum(
            1 for session in self.sessions.values()
            if session.user_id == user_id or str(session.member_user_id) == str(user_id)
        )

    async def find_by_id(self, session_id):
        return self.sessions.get(session_id)


class FakeMessageRepo:
    async def list_by_session(self, session_id, limit=50):
        if session_id != "sess-1":
            return []
        return [
            SimpleNamespace(
                id=1,
                session_id=session_id,
                role="user",
                content="请帮我查一下物流",
                intent="support",
                sources={},
                tokens=None,
                latency_ms=None,
                created_at=datetime(2026, 5, 1, 10, 0, 1),
                member_user_id=288,
                kefu_conversation_id=100,
                kefu_message_id=None,
                sender_type=1,
                tenant_id=0,
            ),
            SimpleNamespace(
                id=2,
                session_id=session_id,
                role="assistant",
                content="我先帮您查询订单物流信息。",
                intent="support",
                sources={"citations": ["order-policy"]},
                tokens=32,
                latency_ms=120,
                created_at=datetime(2026, 5, 1, 10, 0, 2),
                member_user_id=288,
                kefu_conversation_id=100,
                kefu_message_id=None,
                sender_type=3,
                tenant_id=0,
            ),
        ][-limit:]


@pytest.fixture
async def session_history_client():
    from app.api import chat
    from app.main import app

    async def override_session_repo():
        return FakeSessionRepo()

    async def override_message_repo():
        return FakeMessageRepo()

    app.dependency_overrides[chat.get_session_repo] = override_session_repo
    app.dependency_overrides[chat.get_message_repo] = override_message_repo
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def user_headers(user_id="288"):
    return {"Authorization": "Bearer " + create_token(user_id, role="user")}


@pytest.mark.asyncio
async def test_user_can_list_own_session_history(session_history_client):
    response = await session_history_client.get(
        "/api/sessions/history?user_id=288&limit=1",
        headers=user_headers("288"),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == "288"
    assert data["total"] == 2
    assert data["has_more"] is True
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["id"] == "sess-1"
    assert item["title"] == "物流问题咨询"
    assert item["summary"] == "用户咨询了物流进度"
    assert item["handoff_status"] == 2
    assert item["kefu_conversation_id"] == 100
    assert item["message_count"] == 6
    assert item["last_message"] == "请帮我查一下物流"
    assert item["last_message_role"] == "user"
    assert item["last_message_at"] == "2026-05-01 10:04:00"


@pytest.mark.asyncio
async def test_user_id_query_must_match_authenticated_user(session_history_client):
    response = await session_history_client.get(
        "/api/sessions/history?user_id=999",
        headers=user_headers("288"),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_user_can_load_messages_for_own_history_session(session_history_client):
    response = await session_history_client.get(
        "/api/sessions/sess-1/messages",
        headers=user_headers("288"),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["session"]["id"] == "sess-1"
    assert data["session"]["handoff_status"] == 2
    assert data["session"]["kefu_conversation_id"] == 100
    assert [m["role"] for m in data["messages"]] == ["user", "assistant"]
    assert data["messages"][1]["sources"] == {"citations": ["order-policy"]}


@pytest.mark.asyncio
async def test_user_cannot_load_other_users_history_session(session_history_client):
    response = await session_history_client.get(
        "/api/sessions/sess-other/messages",
        headers=user_headers("288"),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_legacy_v1_session_history_path_still_works(session_history_client):
    response = await session_history_client.get(
        "/api/v1/sessions/history?user_id=288",
        headers=user_headers("288"),
    )

    assert response.status_code == 200
    assert response.json()["total"] == 2


class _MessageCursor:
    def __init__(self):
        self.executions = []
        self.lastrowid = 10

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, sql, params=None):
        self.executions.append((sql, params))

    async def fetchone(self):
        return {
            "id": 10,
            "session_id": "sess-1",
            "role": "assistant",
            "content": "您好，请问还需要什么帮助？",
            "intent": "support",
            "sources": "{}",
            "tokens": 8,
            "latency_ms": 80,
            "created_at": datetime(2026, 5, 1, 11, 0, 0),
        }


class _MessageListCursor:
    def __init__(self):
        self.executions = []
        self.same_time = datetime(2026, 5, 1, 11, 0, 0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, sql, params=None):
        self.executions.append((sql, params))

    async def fetchall(self):
        return [
            {
                "id": 1,
                "session_id": "sess-1",
                "role": "user",
                "content": "你好",
                "intent": "general_chat",
                "sources": "{}",
                "tokens": None,
                "latency_ms": None,
                "created_at": self.same_time,
            },
            {
                "id": 2,
                "session_id": "sess-1",
                "role": "assistant",
                "content": "你好！有什么可以帮助您的吗？",
                "intent": "general_chat",
                "sources": "{}",
                "tokens": 12,
                "latency_ms": 80,
                "created_at": self.same_time,
            },
        ]


class _MessageConn:
    def __init__(self, cursor):
        self.cursor_obj = cursor

    def cursor(self, *args, **kwargs):
        return self.cursor_obj


class _MessageAcquire:
    def __init__(self, cursor):
        self.cursor = cursor

    async def __aenter__(self):
        return _MessageConn(self.cursor)

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _MessagePool:
    def __init__(self, cursor):
        self.cursor = cursor

    def acquire(self):
        return _MessageAcquire(self.cursor)


@pytest.mark.asyncio
async def test_message_write_updates_session_history_columns():
    from app.database.message import MessageRepository

    cursor = _MessageCursor()
    repo = MessageRepository(_MessagePool(cursor))

    await repo.create(
        session_id="sess-1",
        role="assistant",
        content="您好，请问还需要什么帮助？",
        intent="support",
        tokens=8,
        latency_ms=80,
    )

    update_calls = [
        (sql, params) for sql, params in cursor.executions
        if "UPDATE ragz_sessions" in sql
    ]
    assert update_calls, "writing ragz_messages should update ragz_sessions list fields"
    _, params = update_calls[0]
    assert params[0] == "您好，请问还需要什么帮助？"
    assert params[1] == "assistant"
    assert params[-1] == "sess-1"


@pytest.mark.asyncio
async def test_session_messages_keep_chronological_order_when_created_at_ties():
    from app.database.message import MessageRepository

    cursor = _MessageListCursor()
    repo = MessageRepository(_MessagePool(cursor))

    messages = await repo.list_by_session("sess-1", limit=50)

    assert [message.role for message in messages] == ["user", "assistant"]
    assert [message.content for message in messages] == [
        "你好",
        "你好！有什么可以帮助您的吗？",
    ]
