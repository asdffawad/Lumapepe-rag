"""
WebSocket 聊天路由
处理连接、消息路由、流式响应、心跳
"""
from __future__ import annotations

import json
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

# WebSocket 消息最大大小 (1MB)
MAX_MESSAGE_SIZE = 1024 * 1024


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
async def websocket_chat(websocket: WebSocket, session_id: Optional[str] = Query(None)):
    """
    WebSocket 聊天端点（企业级）

    认证：使用 Authorization Header 或 Sec-WebSocket-Protocol
    Headers:
      Authorization: Bearer <JWT>
      或
      Sec-WebSocket-Protocol: bearer, <JWT>

    Query 参数:
      - session_id: 会话ID (可选，不提供则自动创建)
    """
    from app.middleware.auth import JWTError, verify_ws_token
    from app.logging_config import get_logger
    _ws_logger = get_logger("websocket.debug")

    # 1. 认证
    headers = dict(websocket.headers)
    auth_header = headers.get("Authorization") or headers.get("authorization") or ""
    _ws_logger.info("WebSocket handshake start", extra={
        "path": str(websocket.url),
        "has_auth_header": bool(auth_header),
        "auth_header_preview": auth_header[:30] if auth_header else "none",
    })
    try:
        auth_user = await verify_ws_token(headers)
        user_id = auth_user.user_id
        _ws_logger.info("Token verified successfully", extra={"user_id": user_id})
    except JWTError as e:
        _ws_logger.error("Token verification failed", extra={
            "error": str(e),
            "auth_header_preview": auth_header[:30] if auth_header else "none",
        })
        await websocket.close(code=4001, reason=str(e))
        return

    # 2. 生成或使用 session_id
    session_id = session_id or str(uuid.uuid4())

    # 3. 连接管理
    manager = get_connection_manager()

    _ws_logger.info("Attempting to accept WebSocket")
    # WebSocket.accept() 是 async 函数，必须 await
    await websocket.accept()
    _ws_logger.info("WebSocket accepted successfully")

    # 4. 建立连接（检查连接数限制）
    success, error_code, error_msg = await manager.connect(websocket, session_id, user_id)
    if not success:
        await websocket.close(code=4002 if error_code == "TOO_MANY_CONNECTIONS" else 503, reason=error_msg)
        return

    # 5. 发送连接成功消息
    await websocket.send_json({
        "type": "connected",
        "session_id": session_id,
        "user_id": user_id,
    })

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            # 每次处理消息时生成新的 request_id
            request_id = str(uuid.uuid4())

            # 消息大小检查
            data_str = json.dumps(data) if isinstance(data, dict) else str(data)
            if len(data_str.encode()) > MAX_MESSAGE_SIZE:
                await websocket.send_json({
                    "type": "error",
                    "code": "MESSAGE_TOO_LARGE",
                    "message": f"消息超出 {MAX_MESSAGE_SIZE} 字节限制",
                    "retry_after": 0,
                })
                continue

            # 限流检查
            allowed, user_remaining, conn_remaining = await manager.check_rate_limit(user_id, session_id)
            if not allowed:
                await websocket.send_json({
                    "type": "error",
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": "请求过于频繁，请稍后重试",
                    "retry_after": 5,
                    "user_remaining": user_remaining,
                    "conn_remaining": conn_remaining,
                })
                continue

            # 兼容 "chat" 和 "message" 两种消息类型
            if msg_type in ("message", "chat"):
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
                    "code": "INVALID_MESSAGE_TYPE",
                    "message": f"未知的消息类型: {msg_type}"
                })

    except WebSocketDisconnect:
        _logger.info("Client disconnected", extra={"session_id": session_id, "user_id": user_id})
    except Exception as e:
        _logger.error(
            "WebSocket error",
            extra={"session_id": session_id, "user_id": user_id, "error": str(e)}
        )
        try:
            await websocket.send_json({
                "type": "error",
                "code": "INTERNAL_ERROR",
                "message": "服务器内部错误"
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
        try:
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
        except Exception as e:
            _logger.error(f"Failed to save message: {e}", extra={"session_id": session_id})
            await websocket.send_json({
                "type": "error",
                "code": "SAVE_ERROR",
                "message": "保存消息失败"
            })
            return  # 提前返回，不再发送 done 消息

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
        import traceback
        _logger.error("Message handling error", extra={"error": str(e), "traceback": traceback.format_exc()[:500]})
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
