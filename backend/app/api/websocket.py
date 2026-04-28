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
from app.middleware.auth import decode_token

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
async def websocket_chat(
    websocket: WebSocket,
    session_id: Optional[str] = Query(None),
    sku_id: Optional[int] = Query(None),
    spu_id: Optional[int] = Query(None),
):
    """
    WebSocket 聊天端点

    认证：使用 Authorization Header 或 Sec-WebSocket-Protocol
    Headers:
      Authorization: Bearer <JWT>
      或
      Sec-WebSocket-Protocol: bearer, <JWT>

    Query 参数:
      - session_id: 会话ID (可选，不提供则自动创建)
      - sku_id: 商品SKU ID (可选，从商品详情页进入时传递)
      - spu_id: 商品SPU ID (可选，从商品详情页进入时传递)
    """
    from app.middleware.auth import JWTError, verify_ws_token
    from app.logging_config import get_logger
    _ws_logger = get_logger("websocket.debug")

    # 1. 认证（仅从 Authorization header 获取，URL query string 不再支持）
    headers = dict(websocket.headers)
    auth_header = headers.get("Authorization") or headers.get("authorization") or ""
    _ws_logger.info("WebSocket handshake start", extra={
        "path": str(websocket.url),
        "has_auth_header": bool(auth_header),
        "auth_header_preview": auth_header[:30] if auth_header else "none",
    })
    try:
        auth_user = None
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        else:
            token = None
        _ws_logger.info("Token extraction from headers", extra={
            "token_present": bool(token),
            "token_preview": token[:30] + "..." if token else "none",
        })
        if token:
            auth_user = decode_token(token)
        if not auth_user:
            raise JWTError("Missing authorization token")
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

    # 5. 发送连接成功消息（包含商品上下文信息）
    connected_msg = {
        "type": "connected",
        "session_id": session_id,
        "user_id": user_id,
    }

    # 如果传入了商品ID，预先查询并返回商品信息
    if sku_id or spu_id:
        from app.services.product_context_service import get_product_context_service
        product_svc = get_product_context_service()
        product_ctx = await product_svc.get_product_context(sku_id=sku_id, spu_id=spu_id)
        if product_ctx:
            # 构建前端可用的商品上下文（包含解析后的信息）
            parsed_introduction = product_ctx.get("parsed_introduction", [])
            parsed_description = product_ctx.get("parsed_description", [])
            
            connected_msg["product_context"] = {
                "product_name": product_ctx.get("product_name"),
                "product_type": product_ctx.get("product_type"),
                "sku_id": product_ctx.get("sku_id"),
                "spu_id": product_ctx.get("spu_id"),
                "price": product_ctx.get("sku_price") or product_ctx.get("price"),
                "stock": product_ctx.get("sku_stock") if product_ctx.get("sku_stock") is not None 
                        else product_ctx.get("stock"),
                "parsed_introduction": parsed_introduction,
                "parsed_description": parsed_description,
                "has_images": product_ctx.get("has_images", False),
                "has_html": product_ctx.get("has_html", False),
            }

    await websocket.send_json(connected_msg)
    _ws_logger.info("Connection established and 'connected' message sent",
                    extra={"session_id": session_id, "user_id": user_id, "has_product_context": "product_context" in connected_msg})

    try:
        while True:
            _ws_logger.info("Waiting for message...")
            try:
                data = await websocket.receive_json()
            except Exception as e:
                _ws_logger.error(f"receive_json EXCEPTION: {type(e).__name__}: {e}", exc_info=True)
                raise
            msg_type = data.get("type")

            # 调试日志
            _ws_logger.info(f"Received message: type={msg_type}, data={data}")

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
                    sku_id=websocket.query_params.get("sku_id") and int(websocket.query_params.get("sku_id")) or None,
                    spu_id=websocket.query_params.get("spu_id") and int(websocket.query_params.get("spu_id")) or None,
                )

            elif msg_type == "history":
                await handle_history(
                    websocket=websocket,
                    session_id=session_id,
                    user_id=user_id,
                    limit=data.get("limit", 20),
                )

            elif msg_type == "ping":
                import time
                client_timestamp = data.get("timestamp")
                server_timestamp = int(time.time() * 1000)
                _ws_logger.debug(f"PING received: client_timestamp={client_timestamp}")
                await websocket.send_json({
                    "type": "pong",
                    "timestamp": client_timestamp,  # 回显客户端时间戳
                    "server_time": server_timestamp  # 服务器当前时间
                })
                _ws_logger.debug(f"PONG sent: server_time={server_timestamp}")

            elif msg_type == "pong":
                # 收到客户端的 pong 响应，更新最后活动时间
                _ws_logger.debug(f"PONG received from client")
                # 通过 ConnectionManager 更新该连接的最后活动时间
                try:
                    await manager.update_activity(session_id)
                except Exception as e:
                    _ws_logger.warning(f"Failed to update activity: {e}")

            elif msg_type == "delete":
                await handle_delete_message(
                    websocket=websocket,
                    session_id=session_id,
                    user_id=user_id,
                    message_id=data.get("message_id"),
                )

            elif msg_type == "voice_record":
                await handle_voice_record(
                    websocket=websocket,
                    session_id=session_id,
                    user_id=user_id,
                    audio_data=data.get("audio"),
                    format=data.get("format", "wav"),
                    sample_rate=data.get("sampleRate"),
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
    sku_id: Optional[int] = None,
    spu_id: Optional[int] = None,
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
            sku_id=sku_id,
            spu_id=spu_id,
        )
        history = ctx.history[-5:] if ctx.history else []

        # 注入商品上下文到历史记录（作为系统消息）
        if ctx.product_context:
            from app.services.product_context_service import get_product_context_service
            product_svc = get_product_context_service()
            system_msg = product_svc.format_system_prompt(ctx.product_context)
            if system_msg:
                # 插入到历史记录最前面
                history.insert(0, {
                    "role": "system",
                    "content": system_msg,
                    "intent": "product_context"
                })
                _logger.info("Product context injected into conversation", extra={"product": ctx.product_context.get("product_name")})

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
            sku_id=sku_id,
            spu_id=spu_id,
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
        
        # 先保存用户消息
        try:
            await session_svc.add_message(
                session_id=ctx.session_id,
                role="user",
                content=content,
                intent=intent,
            )
        except Exception as e:
            _logger.error(f"Failed to save user message: {e}", extra={"session_id": session_id})
            # 用户消息保存失败仍可继续，AI回答仍然有效

        # 保存 AI 回复（带补偿机制）
        save_success = True
        try:
            await session_svc.add_message(
                session_id=ctx.session_id,
                role="assistant",
                content=accumulated,
                intent=intent,
                sources={"sources": list(set(final_citations))} if final_citations else None,
            )
        except Exception as e:
            _logger.error(f"Failed to save assistant message: {e}", extra={
                "session_id": session_id,
                "content_length": len(accumulated),
                "user_id": user_id,
            })
            save_success = False
            # 记录补偿任务（可由后台 worker 恢复）
            await _queue_compensation(
                session_id=ctx.session_id,
                role="assistant",
                content=accumulated,
                intent=intent,
                citations=list(set(final_citations)),
            )
        
        # 更新会话
        try:
            await session_svc.update_session(ctx.session_id, intent=intent)
        except Exception as e:
            _logger.error(f"Failed to update session: {e}", extra={"session_id": session_id})
        
        # 发送完成消息（即使保存失败，AI回答也已发送给用户）
        await websocket.send_json({
            "type": "done",
            "session_id": ctx.session_id,
            "intent": intent,
            "sources": list(set(final_citations)),
            "content_length": len(accumulated),
            "save_status": "success" if save_success else "partial",
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


async def _queue_compensation(
    session_id: str,
    role: str,
    content: str,
    intent: str | None,
    citations: list[str],
) -> None:
    """
    将保存失败的消息加入补偿队列。

    后续由后台 worker 从 Redis 队列中取出并重试保存。
    """
    try:
        redis_client = get_redis_client()
        import json
        compensation = {
            "session_id": session_id,
            "role": role,
            "content": content,
            "intent": intent,
            "citations": citations,
            "created_at": __import__("time").time(),
        }
        key = "compensation:messages"
        await redis_client.rpush(key, json.dumps(compensation))
        _logger.info("Message queued for compensation", extra={
            "session_id": session_id,
            "role": role,
            "content_length": len(content),
        })
    except Exception as e:
        _logger.error(f"Failed to queue compensation: {e}", extra={
            "session_id": session_id,
            "role": role,
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


async def handle_voice_record(
    websocket: WebSocket,
    session_id: str,
    user_id: str,
    audio_data: str | None,
    format: str = "pcm",
    sample_rate: int | None = None,
) -> None:
    """
    Handle voice recording submission:
    1. Receive base64-encoded audio
    2. Decode and transcribe via DashScope STT
    3. Return transcribed text to client for confirmation
    4. Client then sends a normal 'chat' message with the confirmed text
    """
    from app.services.stt_service import STTFactory, STTConfig, STTError
    import base64
    import os

    if not audio_data:
        await websocket.send_json({
            "type": "voice_error",
            "code": "MISSING_AUDIO",
            "message": "No audio data provided",
        })
        return

    try:
        audio_bytes = base64.b64decode(audio_data)
    except Exception:
        await websocket.send_json({
            "type": "voice_error",
            "code": "INVALID_AUDIO",
            "message": "Invalid base64 audio data",
        })
        return

    if len(audio_bytes) > 10 * 1024 * 1024:  # 10MB limit
        await websocket.send_json({
            "type": "voice_error",
            "code": "AUDIO_TOO_LARGE",
            "message": "Audio exceeds 10MB limit",
        })
        return

    _logger.info("Transcribing voice recording", extra={
        "user_id": user_id,
        "session_id": session_id,
        "audio_size_bytes": len(audio_bytes),
        "format": format,
    })

    try:
        api_key = os.getenv("DASHSCOPE_API_KEY", "")
        if not api_key:
            raise STTError("DASHSCOPE_API_KEY not configured")

        sample_rate = sample_rate if sample_rate else 16000
        stt_cfg = STTConfig(
            api_key=api_key,
            format=format,
            sample_rate=sample_rate,
        )
        stt = STTFactory.create("dashscope", stt_cfg)

        text = await stt.transcribe(audio_bytes)

        await websocket.send_json({
            "type": "voice_result",
            "text": text,
            "session_id": session_id,
        })

        _logger.info("Voice transcription completed", extra={
            "user_id": user_id,
            "session_id": session_id,
            "text_length": len(text),
            "text_preview": text[:50] if text else "",
        })

    except STTError as e:
        _logger.error("STT transcription failed", extra={"error": str(e), "user_id": user_id})
        await websocket.send_json({
            "type": "voice_error",
            "code": "STT_ERROR",
            "message": f"Transcription failed: {e}",
        })
    except Exception as e:
        import traceback
        _logger.error("Voice record handling error", extra={
            "error": str(e), "traceback": traceback.format_exc()[:500]
        })
        await websocket.send_json({
            "type": "voice_error",
            "code": "INTERNAL_ERROR",
            "message": "Transcription service unavailable",
        })
