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
from app.database.session import SessionRepository
from app.database.message import MessageRepository
from app.cache.session_cache import SessionCache
from app.services.rag_orchestrator import RAGOrchestrator
from app.services.intent_service import IntentClassifier
from app.services.session_service import SessionService
from app.services.llm_generator import StreamChunk
from app.models.request import ChatRequest
from app.models.response import ChatResponse
from app.logging_config import get_logger

router = APIRouter(prefix="/api/v1", tags=["chat"])
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
    user_id: str = Query("", description="用户ID，用于会话隔离验证"),
    limit: int = 20,
):
    """
    获取指定会话的聊天历史。

    会话隔离：若传入了 user_id，则验证 session 是否属于该用户。
    """
    if user_id:
        owned = await session_svc.verify_session_ownership(session_id, user_id)
        if not owned:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "SESSION_ACCESS_DENIED", "message": "您无权访问该会话"},
            )
    history = await session_svc.get_history(session_id, limit=limit)
    return {"session_id": session_id, "history": history}
