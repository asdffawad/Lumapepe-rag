"""
RAG 转人工客服 MVP 接口。

设计原则：
- promotion_kefu_conversation 是人工客服业务主会话。
- ragz_sessions 是 AI 上下文子会话。
- 两套消息物理分表，通过软关联字段建立映射。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import aiomysql
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.database.connection import get_db_pool
from app.middleware.auth import AuthUser, get_admin_user, get_current_user


router = APIRouter(tags=["kefu"])

HANDOFF_AI = 0
HANDOFF_PENDING = 1
HANDOFF_ACCEPTED = 2
HANDOFF_CLOSED = 3

SENDER_USER = 1
SENDER_ADMIN = 2
SENDER_AI = 3
SENDER_SYSTEM = 4

CONTENT_TEXT = 1


class HandoffRequest(BaseModel):
    session_id: str = Field(..., min_length=1)


class HandoffCloseRequest(BaseModel):
    close_reason: str | None = Field(None, max_length=64)
    resolution_note: str | None = Field(None, max_length=1000)


def _parse_json(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _merge_dict(base: dict, updates: dict) -> dict:
    merged = dict(base or {})
    for key, value in (updates or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _truncate(text: str | None, limit: int = 2048) -> str:
    value = (text or "").strip()
    if not value:
        return "用户请求转人工客服"
    return value[:limit]


def _handoff_status_from_conversation(conversation: dict | None) -> int:
    if not conversation:
        return HANDOFF_AI
    status_val = conversation.get("status")
    if status_val == 0:
        return HANDOFF_PENDING
    if status_val == 1:
        return HANDOFF_ACCEPTED
    if status_val == 2:
        return HANDOFF_CLOSED
    return HANDOFF_PENDING


def _sender_type_from_role(role: str) -> int:
    if role == "user":
        return SENDER_USER
    if role == "assistant":
        return SENDER_AI
    return SENDER_SYSTEM


class KefuRepository:
    def __init__(self, pool: aiomysql.Pool):
        self.pool = pool

    async def get_session(self, session_id: str) -> dict | None:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT * FROM ragz_sessions WHERE id = %s", (session_id,))
                row = await cur.fetchone()
        if row:
            row["metadata"] = _parse_json(row.get("metadata"))
        return row

    async def resolve_member_user(self, user_subject: str) -> dict | None:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                if str(user_subject).isdigit():
                    await cur.execute(
                        """
                        SELECT id, mobile, email, nickname, avatar, status, tenant_id, wallet_address, deleted
                        FROM member_user
                        WHERE id = %s
                        LIMIT 1
                        """,
                        (int(user_subject),),
                    )
                else:
                    await cur.execute(
                        """
                        SELECT id, mobile, email, nickname, avatar, status, tenant_id, wallet_address, deleted
                        FROM member_user
                        WHERE email = %s OR mobile = %s OR nickname = %s OR wallet_address = %s
                        ORDER BY deleted ASC, id ASC
                        LIMIT 1
                        """,
                        (user_subject, user_subject, user_subject, user_subject),
                    )
                return await cur.fetchone()

    async def resolve_admin_user(self, admin_subject: str) -> dict | None:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                if str(admin_subject).isdigit():
                    await cur.execute(
                        """
                        SELECT id, username, nickname, email, mobile, tenant_id
                        FROM system_users
                        WHERE id = %s AND deleted = b'0'
                        LIMIT 1
                        """,
                        (int(admin_subject),),
                    )
                    row = await cur.fetchone()
                    if row:
                        return row
                else:
                    await cur.execute(
                        """
                        SELECT id, username, nickname, email, mobile, tenant_id
                        FROM system_users
                        WHERE deleted = b'0'
                          AND (username = %s OR email = %s OR mobile = %s)
                        LIMIT 1
                        """,
                        (admin_subject, admin_subject, admin_subject),
                    )
                    row = await cur.fetchone()
                    if row:
                        return row

                await cur.execute(
                    """
                    SELECT id, username, nickname, email, mobile, tenant_id
                    FROM system_users
                    WHERE deleted = b'0' AND status = 0
                    ORDER BY id ASC
                    LIMIT 1
                    """
                )
                return await cur.fetchone()

    async def get_conversation(self, conversation_id: int) -> dict | None:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT * FROM promotion_kefu_conversation WHERE id = %s AND deleted = b'0'",
                    (conversation_id,),
                )
                return await cur.fetchone()

    async def create_conversation(
        self,
        member_user: dict,
        session: dict,
        last_message_content: str,
    ) -> dict:
        tenant_id = member_user.get("tenant_id") or session.get("tenant_id") or 0
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    """
                    INSERT INTO promotion_kefu_conversation
                    (user_id, last_message_time, last_message_content, last_message_content_type,
                     admin_unread_message_count, status, last_user_message_time, creator, tenant_id)
                    VALUES (%s, NOW(), %s, %s, 1, 0, NOW(), 'rag_handoff', %s)
                    """,
                    (
                        member_user["id"],
                        _truncate(last_message_content),
                        CONTENT_TEXT,
                        tenant_id,
                    ),
                )
                conversation_id = cur.lastrowid
                await cur.execute(
                    "SELECT * FROM promotion_kefu_conversation WHERE id = %s",
                    (conversation_id,),
                )
                return await cur.fetchone()

    async def update_session_handoff(
        self,
        session_id: str,
        *,
        member_user_id: int | None = None,
        kefu_conversation_id: int | None = None,
        handoff_status: int | None = None,
        metadata_updates: dict | None = None,
        set_handoff_at: bool = False,
    ) -> dict:
        session = await self.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail={"code": "SESSION_NOT_FOUND"})

        assignments: list[str] = []
        params: list[Any] = []
        if member_user_id is not None:
            assignments.append("member_user_id = %s")
            params.append(member_user_id)
        if kefu_conversation_id is not None:
            assignments.append("kefu_conversation_id = %s")
            params.append(kefu_conversation_id)
        if handoff_status is not None:
            assignments.append("handoff_status = %s")
            params.append(handoff_status)
        if set_handoff_at:
            assignments.append("handoff_at = COALESCE(handoff_at, NOW())")
        if metadata_updates is not None:
            merged_metadata = _merge_dict(session.get("metadata") or {}, metadata_updates)
            assignments.append("metadata = %s")
            params.append(json.dumps(merged_metadata, ensure_ascii=False))
        if not assignments:
            return session

        params.append(session_id)
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"UPDATE ragz_sessions SET {', '.join(assignments)} WHERE id = %s",
                    tuple(params),
                )
        updated = await self.get_session(session_id)
        return updated or session

    async def link_session_messages(
        self,
        session_id: str,
        *,
        member_user_id: int,
        kefu_conversation_id: int,
    ) -> None:
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE ragz_messages
                    SET member_user_id = COALESCE(member_user_id, %s),
                        kefu_conversation_id = COALESCE(kefu_conversation_id, %s),
                        sender_type = COALESCE(
                            sender_type,
                            CASE role
                                WHEN 'user' THEN 1
                                WHEN 'assistant' THEN 3
                                ELSE 4
                            END
                        )
                    WHERE session_id = %s
                    """,
                    (member_user_id, kefu_conversation_id, session_id),
                )

    async def insert_kefu_message(
        self,
        *,
        conversation_id: int,
        sender_id: int,
        sender_type: int,
        content: str,
        receiver_id: int | None = None,
        receiver_type: int | None = None,
        tenant_id: int = 0,
    ) -> dict:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    """
                    INSERT INTO promotion_kefu_message
                    (conversation_id, sender_id, sender_type, receiver_id, receiver_type,
                     content_type, content, creator, tenant_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'rag_handoff', %s)
                    """,
                    (
                        conversation_id,
                        sender_id,
                        sender_type,
                        receiver_id,
                        receiver_type,
                        CONTENT_TEXT,
                        _truncate(content),
                        tenant_id,
                    ),
                )
                message_id = cur.lastrowid
                await cur.execute(
                    "SELECT * FROM promotion_kefu_message WHERE id = %s",
                    (message_id,),
                )
                return await cur.fetchone()

    async def list_handoff_requests(self, limit: int = 50) -> list[dict]:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    """
                    SELECT
                        c.id AS kefu_conversation_id,
                        s.id AS ragz_session_id,
                        c.status,
                        s.handoff_status,
                        c.last_message_content,
                        s.handoff_at,
                        s.summary,
                        mu.id AS member_id,
                        mu.nickname AS member_nickname,
                        mu.email AS member_email,
                        mu.mobile AS member_mobile,
                        mu.avatar AS member_avatar,
                        mu.tenant_id AS member_tenant_id
                    FROM ragz_sessions s
                    JOIN promotion_kefu_conversation c ON c.id = s.kefu_conversation_id
                    LEFT JOIN member_user mu ON mu.id = c.user_id
                    WHERE s.handoff_status IN (1, 2)
                      AND c.deleted = b'0'
                    ORDER BY COALESCE(s.handoff_at, c.create_time) DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = await cur.fetchall()

        items: list[dict] = []
        for row in rows:
            items.append(
                {
                    "kefu_conversation_id": row["kefu_conversation_id"],
                    "ragz_session_id": row["ragz_session_id"],
                    "member_user": {
                        "id": row.get("member_id"),
                        "nickname": row.get("member_nickname"),
                        "email": row.get("member_email"),
                        "mobile": row.get("member_mobile"),
                        "avatar": row.get("member_avatar"),
                        "tenant_id": row.get("member_tenant_id"),
                    },
                    "status": row["status"],
                    "handoff_status": row["handoff_status"],
                    "last_message_content": row.get("last_message_content"),
                    "handoff_at": row.get("handoff_at"),
                    "summary": row.get("summary"),
                }
            )
        return items

    async def get_handoff_detail(self, conversation_id: int, message_limit: int = 20) -> dict | None:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT * FROM promotion_kefu_conversation WHERE id = %s AND deleted = b'0'",
                    (conversation_id,),
                )
                conversation = await cur.fetchone()
                if not conversation:
                    return None

                await cur.execute(
                    "SELECT id, mobile, email, nickname, avatar, status, tenant_id, wallet_address "
                    "FROM member_user WHERE id = %s",
                    (conversation["user_id"],),
                )
                member_user = await cur.fetchone()

                assigned_admin = None
                if conversation.get("assigned_admin_id"):
                    await cur.execute(
                        "SELECT id, username, nickname, email, mobile, tenant_id "
                        "FROM system_users WHERE id = %s",
                        (conversation["assigned_admin_id"],),
                    )
                    assigned_admin = await cur.fetchone()

                await cur.execute(
                    "SELECT * FROM ragz_sessions WHERE kefu_conversation_id = %s ORDER BY handoff_at DESC LIMIT 1",
                    (conversation_id,),
                )
                ragz_session = await cur.fetchone()
                if ragz_session:
                    ragz_session["metadata"] = _parse_json(ragz_session.get("metadata"))

                ragz_messages: list[dict] = []
                if ragz_session:
                    await cur.execute(
                        """
                        SELECT id, session_id, role, content, intent, sources, tokens, latency_ms,
                               created_at, member_user_id, kefu_conversation_id, kefu_message_id, sender_type
                        FROM ragz_messages
                        WHERE session_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (ragz_session["id"], message_limit),
                    )
                    rows = await cur.fetchall()
                    ragz_messages = list(reversed(rows))
                    for message in ragz_messages:
                        message["sources"] = _parse_json(message.get("sources"))

                await cur.execute(
                    """
                    SELECT id, conversation_id, sender_id, sender_type, receiver_id, receiver_type,
                           content_type, content, read_status, create_time, update_time, tenant_id
                    FROM promotion_kefu_message
                    WHERE conversation_id = %s AND deleted = b'0'
                    ORDER BY create_time ASC, id ASC
                    LIMIT %s
                    """,
                    (conversation_id, message_limit),
                )
                kefu_messages = await cur.fetchall()

        return {
            "conversation": conversation,
            "member_user": member_user,
            "assigned_admin": assigned_admin,
            "ragz_session": ragz_session,
            "ragz_messages": ragz_messages,
            "kefu_messages": kefu_messages,
        }

    async def accept_conversation(self, conversation_id: int, admin_id: int) -> dict:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    """
                    UPDATE promotion_kefu_conversation
                    SET status = 1,
                        assigned_admin_id = %s,
                        assigned_at = COALESCE(assigned_at, NOW()),
                        updater = 'rag_handoff'
                    WHERE id = %s AND deleted = b'0'
                    """,
                    (admin_id, conversation_id),
                )
                await cur.execute(
                    "SELECT * FROM promotion_kefu_conversation WHERE id = %s",
                    (conversation_id,),
                )
                return await cur.fetchone()

    async def close_conversation(self, conversation_id: int, close_reason: str | None) -> dict:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    """
                    UPDATE promotion_kefu_conversation
                    SET status = 2,
                        disconnect_reason = %s,
                        updater = 'rag_handoff'
                    WHERE id = %s AND deleted = b'0'
                    """,
                    (close_reason, conversation_id),
                )
                await cur.execute(
                    "SELECT * FROM promotion_kefu_conversation WHERE id = %s",
                    (conversation_id,),
                )
                return await cur.fetchone()


async def get_kefu_repo() -> KefuRepository:
    return KefuRepository(get_db_pool())


def _require_owned_session(session: dict | None, current_user: AuthUser) -> dict:
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "会话不存在"},
        )
    if session.get("user_id") != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "SESSION_ACCESS_DENIED", "message": "无权访问该会话"},
        )
    return session


def _build_handoff_metadata(
    *,
    conversation_id: int,
    member_user_id: int,
    handoff_status: int,
    extra: dict | None = None,
) -> dict:
    handoff = {
        "kefu_conversation_id": conversation_id,
        "member_user_id": member_user_id,
        "status": handoff_status,
        "updated_at": datetime.now().isoformat(),
    }
    if handoff_status == HANDOFF_PENDING:
        handoff["requested_at"] = datetime.now().isoformat()
    if extra:
        handoff.update(extra)
    return {"handoff": handoff}


@router.post("/api/kefu/handoff/request")
async def request_handoff(
    request: HandoffRequest,
    current_user: AuthUser = Depends(get_current_user),
    repo: KefuRepository = Depends(get_kefu_repo),
):
    session = _require_owned_session(await repo.get_session(request.session_id), current_user)
    member_user = await repo.resolve_member_user(current_user.user_id)
    if not member_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "MEMBER_USER_NOT_FOUND",
                "message": "当前认证用户无法匹配 member_user，不能发起转人工",
            },
        )

    conversation = None
    created = False
    if session.get("kefu_conversation_id"):
        conversation = await repo.get_conversation(int(session["kefu_conversation_id"]))

    if not conversation:
        metadata = session.get("metadata") or {}
        recent_messages = metadata.get("recent_messages") or []
        last_content = ""
        if recent_messages:
            last_content = str(recent_messages[-1].get("content") or "")
        last_content = last_content or session.get("summary") or "用户请求转人工客服"
        conversation = await repo.create_conversation(member_user, session, last_content)
        created = True

    handoff_status = _handoff_status_from_conversation(conversation)
    updated_session = await repo.update_session_handoff(
        session["id"],
        member_user_id=member_user["id"],
        kefu_conversation_id=conversation["id"],
        handoff_status=handoff_status,
        metadata_updates=_build_handoff_metadata(
            conversation_id=conversation["id"],
            member_user_id=member_user["id"],
            handoff_status=handoff_status,
        ),
        set_handoff_at=True,
    )
    await repo.link_session_messages(
        session["id"],
        member_user_id=member_user["id"],
        kefu_conversation_id=conversation["id"],
    )

    if created:
        summary = session.get("summary") or "暂无摘要"
        await repo.insert_kefu_message(
            conversation_id=conversation["id"],
            sender_id=0,
            sender_type=SENDER_SYSTEM,
            receiver_id=member_user["id"],
            receiver_type=SENDER_USER,
            content=f"用户请求转人工。AI 会话摘要：{summary}",
            tenant_id=member_user.get("tenant_id") or 0,
        )

    return {
        "session_id": session["id"],
        "handoff_status": updated_session.get("handoff_status", handoff_status),
        "kefu_conversation_id": conversation["id"],
        "conversation_status": conversation.get("status"),
        "assigned_admin_id": conversation.get("assigned_admin_id"),
        "handoff_at": updated_session.get("handoff_at"),
        "updated_at": updated_session.get("updated_at"),
    }


@router.get("/api/kefu/handoff/status")
async def get_handoff_status(
    session_id: str = Query(..., min_length=1),
    current_user: AuthUser = Depends(get_current_user),
    repo: KefuRepository = Depends(get_kefu_repo),
):
    session = _require_owned_session(await repo.get_session(session_id), current_user)
    conversation = None
    if session.get("kefu_conversation_id"):
        conversation = await repo.get_conversation(int(session["kefu_conversation_id"]))

    return {
        "session_id": session["id"],
        "handoff_status": session.get("handoff_status") or HANDOFF_AI,
        "kefu_conversation_id": session.get("kefu_conversation_id"),
        "conversation_status": conversation.get("status") if conversation else None,
        "assigned_admin_id": conversation.get("assigned_admin_id") if conversation else None,
        "handoff_at": session.get("handoff_at"),
        "updated_at": session.get("updated_at"),
    }


@router.get("/api/admin/kefu/handoff/requests")
async def list_handoff_requests(
    limit: int = Query(50, ge=1, le=100),
    current_user: AuthUser = Depends(get_admin_user),
    repo: KefuRepository = Depends(get_kefu_repo),
):
    items = await repo.list_handoff_requests(limit=limit)
    return {"total": len(items), "items": items}


@router.get("/api/admin/kefu/handoff/requests/{conversation_id}")
async def get_handoff_request_detail(
    conversation_id: int,
    limit: int = Query(20, ge=1, le=100),
    current_user: AuthUser = Depends(get_admin_user),
    repo: KefuRepository = Depends(get_kefu_repo),
):
    detail = await repo.get_handoff_detail(conversation_id, message_limit=limit)
    if not detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "HANDOFF_NOT_FOUND", "message": "转人工请求不存在"},
        )
    return detail


@router.post("/api/admin/kefu/handoff/requests/{conversation_id}/accept")
async def accept_handoff_request(
    conversation_id: int,
    current_user: AuthUser = Depends(get_admin_user),
    repo: KefuRepository = Depends(get_kefu_repo),
):
    detail = await repo.get_handoff_detail(conversation_id, message_limit=1)
    if not detail or not detail.get("ragz_session"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "HANDOFF_NOT_FOUND", "message": "转人工请求不存在"},
        )
    admin_user = await repo.resolve_admin_user(current_user.user_id)
    if not admin_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "ADMIN_USER_NOT_FOUND", "message": "当前管理员无法匹配 system_users"},
        )

    conversation = await repo.accept_conversation(conversation_id, admin_user["id"])
    session = detail["ragz_session"]
    member_user = detail.get("member_user") or {}
    await repo.update_session_handoff(
        session["id"],
        handoff_status=HANDOFF_ACCEPTED,
        metadata_updates=_build_handoff_metadata(
            conversation_id=conversation_id,
            member_user_id=conversation["user_id"],
            handoff_status=HANDOFF_ACCEPTED,
            extra={"accepted_at": datetime.now().isoformat(), "admin_id": admin_user["id"]},
        ),
    )
    await repo.insert_kefu_message(
        conversation_id=conversation_id,
        sender_id=0,
        sender_type=SENDER_SYSTEM,
        receiver_id=member_user.get("id"),
        receiver_type=SENDER_USER,
        content=f"客服 {admin_user.get('nickname') or admin_user.get('username') or admin_user['id']} 已接入",
        tenant_id=conversation.get("tenant_id") or 0,
    )

    return {
        "kefu_conversation_id": conversation_id,
        "conversation_status": conversation.get("status"),
        "assigned_admin_id": conversation.get("assigned_admin_id"),
        "handoff_status": HANDOFF_ACCEPTED,
    }


@router.post("/api/admin/kefu/handoff/requests/{conversation_id}/close")
async def close_handoff_request(
    conversation_id: int,
    request: HandoffCloseRequest,
    current_user: AuthUser = Depends(get_admin_user),
    repo: KefuRepository = Depends(get_kefu_repo),
):
    detail = await repo.get_handoff_detail(conversation_id, message_limit=1)
    if not detail or not detail.get("ragz_session"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "HANDOFF_NOT_FOUND", "message": "转人工请求不存在"},
        )
    conversation = await repo.close_conversation(conversation_id, request.close_reason)
    session = detail["ragz_session"]
    member_user = detail.get("member_user") or {}
    closed_at = datetime.now().isoformat()
    await repo.update_session_handoff(
        session["id"],
        handoff_status=HANDOFF_CLOSED,
        metadata_updates=_build_handoff_metadata(
            conversation_id=conversation_id,
            member_user_id=conversation["user_id"],
            handoff_status=HANDOFF_CLOSED,
            extra={
                "close_reason": request.close_reason,
                "resolution_note": request.resolution_note,
                "closed_at": closed_at,
            },
        ),
    )
    note = request.resolution_note or ""
    await repo.insert_kefu_message(
        conversation_id=conversation_id,
        sender_id=0,
        sender_type=SENDER_SYSTEM,
        receiver_id=member_user.get("id"),
        receiver_type=SENDER_USER,
        content=f"客服请求已关闭。原因：{request.close_reason or '未填写'}。{note}",
        tenant_id=conversation.get("tenant_id") or 0,
    )

    return {
        "kefu_conversation_id": conversation_id,
        "conversation_status": conversation.get("status"),
        "handoff_status": HANDOFF_CLOSED,
        "closed_at": closed_at,
    }
