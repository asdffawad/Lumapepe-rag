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


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    orchestrator: RAGOrchestrator = Depends(get_rag_orchestrator),
    intent_classifier: IntentClassifier = Depends(get_intent_classifier),
    session_svc: SessionService = Depends(get_session_service),
):
    request_id = str(uuid.uuid4())

    try:
        ctx = await session_svc.get_or_create_session(
            session_id=request.session_id,
            user_id=request.user_id,
        )

        history = ctx.history[-5:] if ctx.history else []
        intent_result = await intent_classifier.classify(request.message, history)
        intent = intent_result.intent

        rag_result = await orchestrator.run(
            query=request.message,
            history=history,
            intent=intent,
            request_id=request_id,
            user_id=request.user_id,
        )

        # 先写 RAG 成功后再写两条消息，避免 RAG 失败时产生孤立消息
        await session_svc.add_message(
            session_id=ctx.session_id,
            role="user",
            content=request.message,
            intent=intent,
        )
        await session_svc.add_message(
            session_id=ctx.session_id,
            role="assistant",
            content=rag_result.content,
            intent=intent,
            sources={"sources": rag_result.sources},
            tokens=rag_result.tokens_used,
            latency_ms=rag_result.latency_ms,
        )

        await session_svc.update_session(ctx.session_id, intent=intent)
        await session_svc.mark_query_processed(ctx.session_id, request.message)

        # 序列化 tool_result 供前端展示
        tool_result_dict = None
        if rag_result.tool_result is not None:
            tr = rag_result.tool_result
            tool_result_dict = {
                "success": tr.success,
                "source": tr.source,
                "data": tr.data,
                "error": tr.error,
            }

        return ChatResponse(
            session_id=ctx.session_id,
            message=rag_result.content,
            intent=intent,
            sources=rag_result.sources,
            tokens_used=rag_result.tokens_used,
            latency_ms=rag_result.latency_ms,
            timestamp=datetime.utcnow().isoformat(),
            tool_result=tool_result_dict,
        )

    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "SESSION_ACCESS_DENIED", "message": "您无权访问该会话"},
        )
    except HTTPException:
        raise
    except Exception as e:
        import sys, traceback
        traceback.print_exc()
        print(f"CHAT ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "CHAT_ERROR", "message": str(e)},
        )


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    orchestrator: RAGOrchestrator = Depends(get_rag_orchestrator),
    intent_classifier: IntentClassifier = Depends(get_intent_classifier),
    session_svc: SessionService = Depends(get_session_service),
):
    request_id = str(uuid.uuid4())

    async def event_generator() -> AsyncGenerator[str, None]:
        ctx = None
        accumulated = ""
        final_citations: list[str] = []

        try:
            ctx = await session_svc.get_or_create_session(
                session_id=request.session_id,
                user_id=request.user_id,
            )
            history = ctx.history[-5:] if ctx.history else []
            intent_result = await intent_classifier.classify(request.message, history)
            intent = intent_result.intent

            # 流式输出
            async for chunk_obj in orchestrator.run_stream(
                query=request.message,
                history=history,
                intent=intent,
                request_id=request_id,
                user_id=request.user_id,
            ):
                if isinstance(chunk_obj, StreamChunk):
                    accumulated += chunk_obj.content
                    if chunk_obj.content:
                        yield f"data: {chunk_obj.content}\n\n"
                    if chunk_obj.citations:
                        final_citations = chunk_obj.citations
                    if chunk_obj.is_final:
                        break
                elif isinstance(chunk_obj, str):
                    # 兼容旧字符串格式
                    accumulated += chunk_obj
                    if chunk_obj:
                        yield f"data: {chunk_obj}\n\n"
                else:
                    content = getattr(chunk_obj, "content", str(chunk_obj))
                    accumulated += content
                    if content:
                        yield f"data: {content}\n\n"

            # 流结束后，写入会话消息
            if ctx is not None:
                await session_svc.add_message(
                    session_id=ctx.session_id,
                    role="user",
                    content=request.message,
                    intent=intent,
                )
                await session_svc.add_message(
                    session_id=ctx.session_id,
                    role="assistant",
                    content=accumulated,
                    intent=intent,
                    sources={"sources": list(set(final_citations))} if final_citations else None,
                )
                await session_svc.update_session(ctx.session_id, intent=intent)

            yield "data: [DONE]\n\n"

        except PermissionError as e:
            yield f"data: [ERROR] 会话访问被拒绝: {str(e)}\n\n"
        except Exception as e:
            _logger.error(
                "Stream chat error",
                extra={"request_id": request_id, "error": str(e)[:200]}
            )
            yield f"data: [ERROR] {str(e)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Request-ID": request_id,
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
