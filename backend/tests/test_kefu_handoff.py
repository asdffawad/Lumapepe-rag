"""
Kefu handoff MVP tests.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime

import pytest

from app.middleware.auth import create_token


class FakeKefuRepository:
    def __init__(self):
        self.sessions = {
            "sess-1": {
                "id": "sess-1",
                "user_id": "288",
                "member_user_id": None,
                "kefu_conversation_id": None,
                "tenant_id": 0,
                "handoff_status": 0,
                "handoff_at": None,
                "summary": "用户咨询退货政策",
                "metadata": {
                    "message_count": 2,
                    "last_citations": ["policy/refund"],
                    "recent_messages": [
                        {"role": "user", "content": "怎么退货？"},
                        {"role": "assistant", "content": "可以在 7 天内申请。"},
                    ],
                },
                "updated_at": None,
            },
            "sess-other": {
                "id": "sess-other",
                "user_id": "999",
                "member_user_id": None,
                "kefu_conversation_id": None,
                "tenant_id": 0,
                "handoff_status": 0,
                "handoff_at": None,
                "summary": "",
                "metadata": {},
                "updated_at": None,
            },
            "sess-missing-member": {
                "id": "sess-missing-member",
                "user_id": "404",
                "member_user_id": None,
                "kefu_conversation_id": None,
                "tenant_id": 0,
                "handoff_status": 0,
                "handoff_at": None,
                "summary": "",
                "metadata": {},
                "updated_at": None,
            },
        }
        self.members = {
            288: {
                "id": 288,
                "nickname": "Alice",
                "email": "alice@example.com",
                "mobile": "13800000000",
                "avatar": "",
                "tenant_id": 0,
            }
        }
        self.admins = {
            1: {
                "id": 1,
                "username": "admin",
                "nickname": "客服一号",
                "email": "admin@example.com",
                "mobile": "",
            }
        }
        self.rag_messages = [
            {
                "id": 1,
                "session_id": "sess-1",
                "role": "user",
                "content": "怎么退货？",
                "intent": "refund_return",
                "sources": {},
                "created_at": datetime(2026, 4, 30, 10, 0, 0),
            },
            {
                "id": 2,
                "session_id": "sess-1",
                "role": "assistant",
                "content": "可以在 7 天内申请。",
                "intent": "refund_return",
                "sources": {"sources": ["policy/refund"]},
                "created_at": datetime(2026, 4, 30, 10, 0, 1),
            },
        ]
        self.conversations = {}
        self.kefu_messages = []
        self.next_conversation_id = 100
        self.next_kefu_message_id = 500

    async def get_session(self, session_id):
        return deepcopy(self.sessions.get(session_id))

    async def resolve_member_user(self, user_subject):
        if str(user_subject).isdigit():
            return deepcopy(self.members.get(int(user_subject)))
        for member in self.members.values():
            if user_subject in (
                member.get("email"),
                member.get("mobile"),
                member.get("nickname"),
                member.get("wallet_address"),
            ):
                return deepcopy(member)
        return None

    async def get_conversation(self, conversation_id):
        return deepcopy(self.conversations.get(int(conversation_id)))

    async def create_conversation(self, member_user, session, last_message_content):
        cid = self.next_conversation_id
        self.next_conversation_id += 1
        conversation = {
            "id": cid,
            "user_id": member_user["id"],
            "status": 0,
            "assigned_admin_id": None,
            "last_message_content": last_message_content,
            "last_message_content_type": 1,
            "admin_unread_message_count": 1,
            "tenant_id": member_user.get("tenant_id", 0),
            "create_time": datetime(2026, 4, 30, 10, 1, 0),
            "update_time": datetime(2026, 4, 30, 10, 1, 0),
        }
        self.conversations[cid] = conversation
        return deepcopy(conversation)

    async def update_session_handoff(self, session_id, *, member_user_id=None, kefu_conversation_id=None, handoff_status=None, metadata_updates=None, set_handoff_at=False):
        session = self.sessions[session_id]
        if member_user_id is not None:
            session["member_user_id"] = member_user_id
        if kefu_conversation_id is not None:
            session["kefu_conversation_id"] = kefu_conversation_id
        if handoff_status is not None:
            session["handoff_status"] = handoff_status
        if set_handoff_at:
            session["handoff_at"] = datetime(2026, 4, 30, 10, 1, 0)
        if metadata_updates:
            metadata = session.get("metadata") or {}
            metadata.update(metadata_updates)
            session["metadata"] = metadata
        session["updated_at"] = datetime(2026, 4, 30, 10, 1, 1)
        return deepcopy(session)

    async def link_session_messages(self, session_id, *, member_user_id, kefu_conversation_id):
        for message in self.rag_messages:
            if message["session_id"] == session_id:
                message["member_user_id"] = member_user_id
                message["kefu_conversation_id"] = kefu_conversation_id
                message["sender_type"] = 1 if message["role"] == "user" else 3

    async def insert_kefu_message(self, *, conversation_id, sender_id, sender_type, content, receiver_id=None, receiver_type=None, tenant_id=0):
        row = {
            "id": self.next_kefu_message_id,
            "conversation_id": conversation_id,
            "sender_id": sender_id,
            "sender_type": sender_type,
            "receiver_id": receiver_id,
            "receiver_type": receiver_type,
            "content_type": 1,
            "content": content,
            "tenant_id": tenant_id,
            "create_time": datetime(2026, 4, 30, 10, 2, 0),
        }
        self.next_kefu_message_id += 1
        self.kefu_messages.append(row)
        return deepcopy(row)

    async def list_handoff_requests(self, limit=50):
        rows = []
        for session in self.sessions.values():
            cid = session.get("kefu_conversation_id")
            if not cid or session.get("handoff_status") not in (1, 2):
                continue
            conversation = self.conversations[cid]
            member = self.members[conversation["user_id"]]
            rows.append({
                "kefu_conversation_id": cid,
                "ragz_session_id": session["id"],
                "member_user": deepcopy(member),
                "status": conversation["status"],
                "handoff_status": session["handoff_status"],
                "last_message_content": conversation["last_message_content"],
                "handoff_at": session["handoff_at"],
                "summary": session["summary"],
            })
        return rows[:limit]

    async def get_handoff_detail(self, conversation_id, message_limit=20):
        conversation = self.conversations.get(int(conversation_id))
        if not conversation:
            return None
        session = next(
            s for s in self.sessions.values()
            if s.get("kefu_conversation_id") == int(conversation_id)
        )
        return {
            "conversation": deepcopy(conversation),
            "member_user": deepcopy(self.members[conversation["user_id"]]),
            "assigned_admin": deepcopy(self.admins.get(conversation.get("assigned_admin_id"))),
            "ragz_session": deepcopy(session),
            "ragz_messages": [
                deepcopy(m) for m in self.rag_messages
                if m["session_id"] == session["id"]
            ][-message_limit:],
            "kefu_messages": [
                deepcopy(m) for m in self.kefu_messages
                if m["conversation_id"] == int(conversation_id)
            ],
        }

    async def resolve_admin_user(self, admin_subject):
        if str(admin_subject).isdigit():
            return deepcopy(self.admins.get(int(admin_subject)))
        for admin in self.admins.values():
            if admin_subject in (admin.get("username"), admin.get("email"), admin.get("mobile")):
                return deepcopy(admin)
        return deepcopy(next(iter(self.admins.values())))

    async def accept_conversation(self, conversation_id, admin_id):
        conversation = self.conversations[int(conversation_id)]
        conversation["status"] = 1
        conversation["assigned_admin_id"] = admin_id
        conversation["assigned_at"] = datetime(2026, 4, 30, 10, 3, 0)
        return deepcopy(conversation)

    async def close_conversation(self, conversation_id, close_reason):
        conversation = self.conversations[int(conversation_id)]
        conversation["status"] = 2
        conversation["disconnect_reason"] = close_reason
        return deepcopy(conversation)


class _FakeCursor:
    def __init__(self, row):
        self.row = row
        self.executed_sql = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, sql, params=None):
        self.executed_sql = sql

    async def fetchone(self):
        if "deleted = b'0'" in self.executed_sql:
            return None
        return self.row


class _FakeConn:
    def __init__(self, row):
        self.row = row

    def cursor(self, *args, **kwargs):
        return _FakeCursor(self.row)


class _FakeAcquire:
    def __init__(self, row):
        self.row = row

    async def __aenter__(self):
        return _FakeConn(self.row)

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def __init__(self, row):
        self.row = row

    def acquire(self):
        return _FakeAcquire(self.row)


@pytest.fixture
def fake_repo():
    return FakeKefuRepository()


@pytest.fixture
async def kefu_client(fake_repo):
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.api import kefu

    async def override_repo():
        return fake_repo

    app.dependency_overrides[kefu.get_kefu_repo] = override_repo
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def user_headers(user_id="288"):
    return {"Authorization": "Bearer " + create_token(user_id, role="user")}


def admin_headers(admin_id="1"):
    return {"Authorization": "Bearer " + create_token(admin_id, role="admin")}


async def request_handoff(client, session_id="sess-1"):
    return await client.post(
        "/api/kefu/handoff/request",
        json={"session_id": session_id},
        headers=user_headers(),
    )


@pytest.mark.asyncio
async def test_user_can_request_handoff(kefu_client, fake_repo):
    response = await request_handoff(kefu_client)

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == "sess-1"
    assert data["handoff_status"] == 1
    assert data["conversation_status"] == 0
    assert data["kefu_conversation_id"] == 100
    assert fake_repo.sessions["sess-1"]["handoff_status"] == 1
    assert fake_repo.conversations[100]["status"] == 0


@pytest.mark.asyncio
async def test_repository_resolves_existing_numeric_member_even_if_soft_deleted():
    from app.api.kefu import KefuRepository

    repo = KefuRepository(_FakePool({
        "id": 288,
        "nickname": "Wallet_DLHoTMSY",
        "email": None,
        "mobile": None,
        "avatar": "",
        "status": 0,
        "tenant_id": 1,
        "wallet_address": None,
        "deleted": 1,
    }))

    member = await repo.resolve_member_user("288")

    assert member["id"] == 288


@pytest.mark.asyncio
async def test_repeated_handoff_reuses_existing_conversation(kefu_client, fake_repo):
    first = await request_handoff(kefu_client)
    second = await request_handoff(kefu_client)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["kefu_conversation_id"] == second.json()["kefu_conversation_id"]
    assert len(fake_repo.conversations) == 1


@pytest.mark.asyncio
async def test_user_cannot_request_handoff_for_other_session(kefu_client):
    response = await kefu_client.post(
        "/api/kefu/handoff/request",
        json={"session_id": "sess-other"},
        headers=user_headers("288"),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_handoff_status_returns_current_state(kefu_client):
    await request_handoff(kefu_client)

    response = await kefu_client.get(
        "/api/kefu/handoff/status?session_id=sess-1",
        headers=user_headers(),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["handoff_status"] == 1
    assert data["conversation_status"] == 0
    assert data["assigned_admin_id"] is None


@pytest.mark.asyncio
async def test_admin_lists_handoff_requests(kefu_client):
    await request_handoff(kefu_client)

    response = await kefu_client.get(
        "/api/admin/kefu/handoff/requests",
        headers=admin_headers(),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["member_user"]["nickname"] == "Alice"
    assert data["items"][0]["summary"] == "用户咨询退货政策"


@pytest.mark.asyncio
async def test_admin_can_view_handoff_detail(kefu_client):
    await request_handoff(kefu_client)

    response = await kefu_client.get(
        "/api/admin/kefu/handoff/requests/100",
        headers=admin_headers(),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ragz_session"]["summary"] == "用户咨询退货政策"
    assert data["ragz_session"]["metadata"]["last_citations"] == ["policy/refund"]
    assert len(data["ragz_messages"]) == 2
    assert data["kefu_messages"][0]["sender_type"] == 4


@pytest.mark.asyncio
async def test_admin_accept_updates_conversation_and_rag_session(kefu_client, fake_repo):
    await request_handoff(kefu_client)

    response = await kefu_client.post(
        "/api/admin/kefu/handoff/requests/100/accept",
        headers=admin_headers("1"),
    )

    assert response.status_code == 200
    assert response.json()["conversation_status"] == 1
    assert fake_repo.conversations[100]["status"] == 1
    assert fake_repo.sessions["sess-1"]["handoff_status"] == 2


@pytest.mark.asyncio
async def test_admin_close_updates_conversation_and_rag_session(kefu_client, fake_repo):
    await request_handoff(kefu_client)
    await kefu_client.post("/api/admin/kefu/handoff/requests/100/accept", headers=admin_headers("1"))

    response = await kefu_client.post(
        "/api/admin/kefu/handoff/requests/100/close",
        json={"close_reason": "resolved", "resolution_note": "已解释退货流程"},
        headers=admin_headers("1"),
    )

    assert response.status_code == 200
    assert response.json()["conversation_status"] == 2
    assert fake_repo.conversations[100]["status"] == 2
    assert fake_repo.sessions["sess-1"]["handoff_status"] == 3
    assert fake_repo.sessions["sess-1"]["metadata"]["handoff"]["close_reason"] == "resolved"


@pytest.mark.asyncio
async def test_member_user_must_exist(kefu_client):
    response = await kefu_client.post(
        "/api/kefu/handoff/request",
        json={"session_id": "sess-missing-member"},
        headers=user_headers("404"),
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_existing_websocket_route_remains_registered(kefu_client):
    from app.main import app

    websocket_paths = [route.path for route in app.routes if getattr(route, "path", None)]
    assert "/ws/chat" in websocket_paths
