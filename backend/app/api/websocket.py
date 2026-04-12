"""
WebSocket 聊天路由
处理连接、消息路由、流式响应、心跳
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from app.api.ws_manager import get_connection_manager
from app.database.connection import get_db_pool
from app.cache.redis_client import get_redis_client
from app.services.rag_orchestrator import RAGOrchestrator
from app.services.intent_service import IntentClassifier
from app.services.session_service import SessionService
from app.services.llm_generator import StreamChunk
from app.logging_config import get_logger

router = APIRouter(tags=["websocket"])
_logger = get_logger("websocket")


def get_rag_orchestrator() -> RAGOrchestrator:
    from app.main import app as _app
    return _app.state.orchestrator


def get_intent_classifier() -> IntentClassifier:
    from app.main import app as _app
    return _app.state.intent_classifier


async def get_session_service() -> SessionService | None:
    """获取会话服务，数据库不可用时返回 None"""
    try:
        pool = get_db_pool()
        redis = get_redis_client()
        return SessionService(pool, redis)
    except Exception:
        return None


@router.websocket("/ws/chat")
async def websocket_chat(
    websocket: WebSocket,
    session_id: Optional[str] = Query(None, description="会话ID，不提供则自动创建"),
    user_id: str = Query(..., description="用户ID（必需）"),
):
    """
    WebSocket 聊天端点
    
    连接后可以发送以下消息:
    - {"type": "message", "content": "...", "session_id": "可选"}
    - {"type": "history", "limit": 20}
    - {"type": "ping"}
    """
    manager = get_connection_manager()
    session_id = session_id or str(uuid.uuid4())
    
    await manager.connect(websocket, session_id, user_id)
    request_id = str(uuid.uuid4())
    
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            
            if msg_type == "message":
                await handle_message(
                    websocket=websocket,
                    session_id=session_id,
                    user_id=user_id,
                    content=data.get("content", ""),
                    provided_session_id=data.get("session_id"),
                    request_id=request_id,
                )
            
            elif msg_type == "history":
                await handle_history(
                    websocket=websocket,
                    session_id=session_id,
                    user_id=user_id,
                    limit=data.get("limit", 20),
                )
            
            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            elif msg_type == "delete":
                await handle_delete_message(
                    websocket=websocket,
                    session_id=session_id,
                    user_id=user_id,
                    message_id=data.get("message_id"),
                )
            
            else:
                await websocket.send_json({
                    "type": "error",
                    "code": "UNKNOWN_MESSAGE_TYPE",
                    "message": f"未知的消息类型: {msg_type}"
                })
    
    except WebSocketDisconnect:
        _logger.info("Client disconnected", extra={"session_id": session_id})
    except Exception as e:
        _logger.error(
            "WebSocket error",
            extra={"session_id": session_id, "error": str(e)}
        )
        try:
            await websocket.send_json({
                "type": "error",
                "code": "INTERNAL_ERROR",
                "message": str(e)
            })
        except Exception:
            pass
    finally:
        await manager.disconnect(session_id)


async def handle_message(
    websocket: WebSocket,
    session_id: str,
    user_id: str,
    content: str,
    provided_session_id: Optional[str],
    request_id: str,
) -> None:
    """处理聊天消息，流式响应"""
    orchestrator = get_rag_orchestrator()
    intent_classifier = get_intent_classifier()
    session_svc = await get_session_service()
    
    # 检查数据库是否可用
    if session_svc is None:
        await websocket.send_json({
            "type": "error",
            "code": "SERVICE_UNAVAILABLE",
            "message": "会话服务不可用（数据库未连接）"
        })
        return
    
    # 如果提供了 session_id，验证归属
    target_session_id = provided_session_id or session_id
    if provided_session_id:
        owned = await session_svc.verify_session_ownership(provided_session_id, user_id)
        if not owned:
            await websocket.send_json({
                "type": "error",
                "code": "SESSION_DENIED",
                "message": "无权访问该会话"
            })
            return
    
    try:
        ctx = await session_svc.get_or_create_session(
            session_id=target_session_id,
            user_id=user_id,
        )
        history = ctx.history[-5:] if ctx.history else []
        
        # 意图分类
        intent_result = await intent_classifier.classify(content, history)
        intent = intent_result.intent
        
        # 发送用户消息确认
        await websocket.send_json({
            "type": "message_received",
            "session_id": ctx.session_id,
            "intent": intent,
        })
        
        # 流式生成
        accumulated = ""
        final_citations = []
        
        async for chunk_obj in orchestrator.run_stream(
            query=content,
            history=history,
            intent=intent,
            request_id=request_id,
            user_id=user_id,
        ):
            if isinstance(chunk_obj, StreamChunk):
                if chunk_obj.content:
                    await websocket.send_json({
                        "type": "chunk",
                        "content": chunk_obj.content,
                        "done": False,
                    })
                    accumulated += chunk_obj.content
                if chunk_obj.citations:
                    final_citations = chunk_obj.citations
                if chunk_obj.is_final:
                    break
            elif isinstance(chunk_obj, str):
                if chunk_obj:
                    await websocket.send_json({
                        "type": "chunk",
                        "content": chunk_obj,
                        "done": False,
                    })
                    accumulated += chunk_obj
        
        # 流结束后，保存消息
        await session_svc.add_message(
            session_id=ctx.session_id,
            role="user",
            content=content,
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
        
        # 发送完成消息
        await websocket.send_json({
            "type": "done",
            "session_id": ctx.session_id,
            "intent": intent,
            "sources": list(set(final_citations)),
            "content_length": len(accumulated),
        })
    
    except PermissionError as e:
        await websocket.send_json({
            "type": "error",
            "code": "SESSION_DENIED",
            "message": str(e)
        })
    except Exception as e:
        _logger.error("Message handling error", extra={"error": str(e)})
        await websocket.send_json({
            "type": "error",
            "code": "MESSAGE_ERROR",
            "message": str(e)
        })


async def handle_history(
    websocket: WebSocket,
    session_id: str,
    user_id: str,
    limit: int,
) -> None:
    """处理历史记录请求"""
    session_svc = await get_session_service()

    if session_svc is None:
        await websocket.send_json({
            "type": "error",
            "code": "SERVICE_UNAVAILABLE",
            "message": "会话服务不可用（数据库未连接）"
        })
        return

    owned = await session_svc.verify_session_ownership(session_id, user_id)
    if not owned:
        await websocket.send_json({
            "type": "error",
            "code": "SESSION_DENIED",
            "message": "无权访问该会话"
        })
        return

    history = await session_svc.get_history(session_id, limit=limit)
    await websocket.send_json({
        "type": "history",
        "session_id": session_id,
        "messages": history,
    })


async def handle_delete_message(
    websocket: WebSocket,
    session_id: str,
    user_id: str,
    message_id: int,
) -> None:
    """处理删除消息请求"""
    session_svc = await get_session_service()
    if session_svc is None:
        await websocket.send_json({
            "type": "error",
            "code": "SERVICE_UNAVAILABLE",
            "message": "会话服务不可用"
        })
        return

    try:
        # message_id=0 表示删除最后一条用户消息
        if message_id == 0:
            history = await session_svc.get_history(session_id, limit=1)
            if history and len(history) > 0:
                # 找到最后一条用户消息
                for msg in reversed(history):
                    if msg.get('role') == 'user':
                        message_id = msg.get('id')
                        break
            if message_id == 0:
                await websocket.send_json({
                    "type": "message_deleted",
                    "message_id": 0,
                    "success": False,
                    "message": "没有可删除的消息"
                })
                return

        result = await session_svc.delete_message(session_id, message_id, user_id)
        await websocket.send_json({
            "type": "message_deleted",
            "message_id": message_id,
            "success": result,
        })
    except PermissionError as e:
        await websocket.send_json({
            "type": "error",
            "code": "SESSION_DENIED",
            "message": str(e)
        })
    except Exception as e:
        await websocket.send_json({
            "type": "error",
            "code": "DELETE_ERROR",
            "message": str(e)
        })
