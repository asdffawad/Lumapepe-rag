"""
Chat API 路由
POST /api/v1/chat - 发送消息
GET /api/v1/chat/history/{session_id} - 获取会话历史

认证说明：
- 所有聊天接口均需认证
- 支持 JWT Bearer Token (Authorization: Bearer <token>)
- 支持 API Key (X-API-Key: <key>)
- 会话隔离：每个 session 只能被其所属 user_id 访问

认证示例：
    curl -X POST http://localhost:8000/api/v1/chat \\
      -H "Authorization: Bearer <token>" \\
      -H "Content-Type: application/json" \\
      -d '{"message": "iPhone 有哪些颜色？", "user_id": "user-001"}'
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.database.connection import get_db_pool
from app.cache.redis_client import get_redis_client
from app.database.session import SessionRecord, SessionRepository
from app.database.message import MessageRepository
from app.cache.session_cache import SessionCache
from app.services.rag_orchestrator import RAGOrchestrator
from app.services.intent_service import IntentClassifier
from app.services.session_service import SessionService
from app.services.llm_generator import StreamChunk
from app.models.request import ChatRequest
from app.models.response import ChatResponse
from app.logging_config import get_logger
from app.middleware.auth import AuthUser, get_current_user

router = APIRouter(prefix="/api/v1", tags=["chat"])
sessions_router = APIRouter(prefix="/api", tags=["sessions"])
_logger = get_logger("chat.api")


async def get_db_pool_dep():
    return get_db_pool()


async def get_redis():
    return get_redis_client()


def get_rag_orchestrator() -> RAGOrchestrator:
    from app.main import app as _app
    return _app.state.orchestrator


def get_intent_classifier() -> IntentClassifier:
    from app.main import app as _app
    return _app.state.intent_classifier


async def get_session_service(
    db_pool=Depends(get_db_pool_dep),
    redis=Depends(get_redis),
) -> SessionService:
    return SessionService(db_pool, redis)


async def get_session_repo(db_pool=Depends(get_db_pool_dep)) -> SessionRepository:
    return SessionRepository(db_pool)


async def get_message_repo(db_pool=Depends(get_db_pool_dep)) -> MessageRepository:
    return MessageRepository(db_pool)


def _dt(value):
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return value


def _get_value(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _session_owned_by_user(session: SessionRecord, user_id: str) -> bool:
    if session.user_id == user_id:
        return True
    return session.member_user_id is not None and str(session.member_user_id) == str(user_id)


def _session_to_dict(session: SessionRecord) -> dict:
    return {
        "id": session.id,
        "user_id": session.user_id,
        "member_user_id": session.member_user_id,
        "summary": session.summary,
        "intent": session.intent,
        "status": session.status,
        "handoff_status": session.handoff_status,
        "kefu_conversation_id": session.kefu_conversation_id,
        "tenant_id": session.tenant_id,
        "handoff_at": _dt(session.handoff_at),
        "created_at": _dt(session.created_at),
        "updated_at": _dt(session.updated_at),
    }


def _history_row_to_dict(row) -> dict:
    summary = _get_value(row, "summary")
    last_message = _get_value(row, "last_message")
    title = _get_value(row, "title") or summary or last_message or _get_value(row, "id")
    if title and len(str(title)) > 60:
        title = str(title)[:57] + "..."
    return {
        "id": _get_value(row, "id"),
        "user_id": _get_value(row, "user_id"),
        "member_user_id": _get_value(row, "member_user_id"),
        "title": title,
        "summary": summary,
        "intent": _get_value(row, "intent"),
        "status": _get_value(row, "status"),
        "handoff_status": _get_value(row, "handoff_status") or 0,
        "kefu_conversation_id": _get_value(row, "kefu_conversation_id"),
        "message_count": int(_get_value(row, "message_count") or 0),
        "last_message": last_message,
        "last_message_role": _get_value(row, "last_message_role"),
        "last_message_at": _dt(_get_value(row, "last_message_at")),
        "created_at": _dt(_get_value(row, "created_at")),
        "updated_at": _dt(_get_value(row, "updated_at")),
        "pinned": _get_value(row, "pinned") or 0,
        "archived": _get_value(row, "archived") or 0,
    }


def _message_to_dict(message) -> dict:
    return {
        "id": message.id,
        "session_id": message.session_id,
        "role": message.role,
        "content": message.content,
        "intent": message.intent,
        "sources": message.sources,
        "tokens": message.tokens,
        "latency_ms": message.latency_ms,
        "created_at": _dt(message.created_at),
        "member_user_id": message.member_user_id,
        "kefu_conversation_id": message.kefu_conversation_id,
        "kefu_message_id": message.kefu_message_id,
        "sender_type": message.sender_type,
        "tenant_id": message.tenant_id,
    }


@router.post("/chat", response_model=ChatResponse, deprecated=True)
async def chat(
    request: ChatRequest,
    orchestrator: RAGOrchestrator = Depends(get_rag_orchestrator),
    intent_classifier: IntentClassifier = Depends(get_intent_classifier),
    session_svc: SessionService = Depends(get_session_service),
):
    """
    [DEPRECATED] 此接口已废弃，请使用 WebSocket /ws/chat

    2026-04-13 起，此接口不再维护。
    WebSocket 端点支持所有聊天功能，并提供更好的实时体验。
    """
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=410,
        content={
            "error": {
                "code": "ENDPOINT_DEPRECATED",
                "message": "此接口已废弃，请使用 WebSocket /ws/chat",
                "migration_guide": "请参考 docs/websocket-migration.md",
            }
        },
    )


@router.post("/chat/stream", deprecated=True)
async def chat_stream(
    request: ChatRequest,
    orchestrator: RAGOrchestrator = Depends(get_rag_orchestrator),
    intent_classifier: IntentClassifier = Depends(get_intent_classifier),
    session_svc: SessionService = Depends(get_session_service),
):
    """
    [DEPRECATED] 此接口已废弃，请使用 WebSocket /ws/chat

    2026-04-13 起，此接口不再维护。
    """
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=410,
        content={
            "error": {
                "code": "ENDPOINT_DEPRECATED",
                "message": "此接口已废弃，请使用 WebSocket /ws/chat",
                "migration_guide": "请参考 docs/websocket-migration.md",
            }
        },
    )


@router.get("/chat/history/{session_id}")
async def get_chat_history(
    session_id: str,
    session_svc: SessionService = Depends(get_session_service),
    current_user: AuthUser = Depends(get_current_user),
    limit: int = 20,
):
    """
    获取指定会话的聊天历史。

    会话隔离：验证 session 是否属于当前认证用户。
    """
    owned = await session_svc.verify_session_ownership(session_id, current_user.user_id)
    if not owned:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "SESSION_ACCESS_DENIED", "message": "您无权访问该会话"},
        )
    history = await session_svc.get_history(session_id, limit=limit)
    return {"session_id": session_id, "history": history}


@router.get("/sessions/history")
@sessions_router.get("/sessions/history")
async def get_session_history_list(
    user_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session_repo: SessionRepository = Depends(get_session_repo),
    current_user: AuthUser = Depends(get_current_user),
):
    if user_id is not None and str(user_id) != str(current_user.user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "USER_ACCESS_DENIED", "message": "您只能查看自己的历史会话"},
        )
    rows = await session_repo.list_history_for_user(
        current_user.user_id,
        limit=limit,
        offset=offset,
    )
    total = await session_repo.count_history_for_user(current_user.user_id)
    return {
        "user_id": current_user.user_id,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(rows) < total,
        "items": [_history_row_to_dict(row) for row in rows],
    }


@router.get("/sessions/{session_id}/messages")
@sessions_router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    session_repo: SessionRepository = Depends(get_session_repo),
    message_repo: MessageRepository = Depends(get_message_repo),
    current_user: AuthUser = Depends(get_current_user),
):
    session = await session_repo.find_by_id(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "会话不存在"},
        )
    if not _session_owned_by_user(session, current_user.user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "SESSION_ACCESS_DENIED", "message": "您无权访问该会话"},
        )
    messages = await message_repo.list_by_session(session_id, limit=limit)
    return {
        "session": _session_to_dict(session),
        "messages": [_message_to_dict(message) for message in messages],
    }
