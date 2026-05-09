import asyncio
from copy import deepcopy
from datetime import datetime

class FakeRepo:
    def __init__(self):
        self.sessions = {
            "sess-1": {
                "id": "sess-1", "user_id": "288", "kefu_conversation_id": 100,
                "handoff_status": 3, "metadata": {}, "summary": "test"
            }
        }
        self.members = {288: {"id": 288, "tenant_id": 0}}
        self.conversations = {
            100: {"id": 100, "user_id": 288, "status": 2, "assigned_admin_id": None}
        }
        self.next_cid = 101

    async def get_session(self, sid):
        return deepcopy(self.sessions.get(sid))
    async def resolve_member_user(self, uid):
        return deepcopy(self.members.get(int(uid)))
    async def get_conversation(self, cid):
        return deepcopy(self.conversations.get(cid))
    async def create_conversation(self, member, session, content):
        c = {"id": self.next_cid, "user_id": member["id"], "status": 0, "assigned_admin_id": None}
        self.conversations[self.next_cid] = c
        self.next_cid += 1
        return deepcopy(c)
    async def update_session_handoff(self, sid, **kw):
        s = self.sessions[sid]
        for k, v in kw.items():
            if v is not None and k != "metadata_updates":
                s[k] = v
        if kw.get("metadata_updates"):
            s["metadata"].update(kw["metadata_updates"])
        return deepcopy(s)

fr = FakeRepo()

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
from app.services import handoff_service as hs_mod

class FakeHS(hs_mod.HandoffService):
    def __init__(self, pool, repo):
        self.pool = pool
        self._repo = repo

    async def _get_session(self, sid): return await self._repo.get_session(sid)
    async def _resolve_member_user(self, uid): return await self._repo.resolve_member_user(uid)
    async def _get_conversation(self, cid): return await self._repo.get_conversation(cid)
    async def _create_conversation(self, m, s, c): return await self._repo.create_conversation(m, s, c)
    async def _update_session_handoff(self, sid, **kw): return await self._repo.update_session_handoff(sid, **kw)
    async def _link_session_messages(self, sid, **kw): pass
    async def _insert_kefu_message(self, **kw): return {"id": 1}

svc = FakeHS(None, fr)

async def test():
    result = await svc.request_handoff("sess-1", "288", trigger_source="manual_button")
    print(f"conversation_id: {result.kefu_conversation_id}")
    print(f"created: {result.created}")
    print(f"handoff_status: {result.handoff_status}")
    sess_cid = fr.sessions["sess-1"]["kefu_conversation_id"]
    print(f"session kefu_conversation_id: {sess_cid}")
    print(f"new conv exists: {101 in fr.conversations}")
    print(f"old conv status: {fr.conversations[100]['status']}")
    print(f"new conv status: {fr.conversations[101]['status']}")
    print(f"handoff metadata: {fr.sessions['sess-1']['metadata']}")

asyncio.run(test())
