import asyncio
from backend.tests.test_kefu_handoff import FakeKefuRepository, FakeHandoffService
from datetime import datetime

async def test():
    fake_repo = FakeKefuRepository()

    # Set up closed state
    fake_repo.conversations[100] = {
        "id": 100, "user_id": 288, "status": 2, "assigned_admin_id": None,
        "last_message_content": "上次会话", "last_message_content_type": 1,
        "admin_unread_message_count": 0, "tenant_id": 0,
        "create_time": datetime(2026, 4, 30, 10, 1, 0), "update_time": datetime(2026, 4, 30, 10, 1, 0),
    }
    fake_repo.sessions["sess-1"]["handoff_status"] = 3
    fake_repo.sessions["sess-1"]["kefu_conversation_id"] = 100
    fake_repo.sessions["sess-1"]["metadata"] = {}

    # Create a HandoffService using fake_repo
    class TestHS(FakeHandoffService):
        pass

    hs = TestHS(None)
    hs._get_session = fake_repo.get_session
    hs._get_conversation = fake_repo.get_conversation
    hs._create_conversation = fake_repo.create_conversation
    hs._update_session_handoff = fake_repo.update_session_handoff
    hs._link_session_messages = fake_repo.link_session_messages
    hs._insert_kefu_message = fake_repo.insert_kefu_message
    hs._resolve_member_user = fake_repo.resolve_member_user

    print("Before request_handoff:")
    print(f"  session handoff_status: {fake_repo.sessions['sess-1']['handoff_status']}")
    print(f"  session kefu_conversation_id: {fake_repo.sessions['sess-1']['kefu_conversation_id']}")
    print(f"  conversation 100 status: {fake_repo.conversations.get(100)}")

    result = await hs.request_handoff("sess-1", "288")
    print(f"\nAfter request_handoff:")
    print(f"  result.kefu_conversation_id: {result.kefu_conversation_id}")
    print(f"  result.handoff_status: {result.handoff_status}")
    print(f"  session kefu_conversation_id: {fake_repo.sessions['sess-1']['kefu_conversation_id']}")
    print(f"  session handoff_status: {fake_repo.sessions['sess-1']['handoff_status']}")
    print(f"  conversations: {list(fake_repo.conversations.keys())}")
    print(f"  metadata: {fake_repo.sessions['sess-1']['metadata']}")

asyncio.run(test())
