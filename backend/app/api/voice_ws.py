"""Real-time voice transcription WebSocket endpoint."""
from __future__ import annotations

import asyncio
import base64
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.ws_manager import get_connection_manager
from app.services.stt_service import STTFactory, STTConfig, STTError
from app.middleware.auth import JWTError, verify_ws_token
from app.logging_config import get_logger

router = APIRouter(tags=["voice"])
_logger = get_logger("voice_ws")

MAX_AUDIO_CHUNK_BYTES = 512 * 1024  # 512KB per chunk


def _get_stt_provider():
    from app.main import app as _app
    stt = getattr(_app.state, "stt_provider", None)
    if stt is None:
        import os
        from app.config import get_config
        cfg = get_config()
        stt_cfg = STTConfig(
            api_key=cfg.stt.api_key or os.getenv("DASHSCOPE_API_KEY", ""),
            base_url=cfg.stt.base_url,
            model=cfg.stt.model,
            sample_rate=cfg.stt.sample_rate,
            format=cfg.stt.format,
            timeout=cfg.stt.timeout,
        )
        stt = STTFactory.create("dashscope", stt_cfg)
    return stt


@router.websocket("/ws/voice")
async def websocket_voice(
    websocket: WebSocket,
):
    """
    Real-time voice transcription WebSocket with true streaming support.

    认证：使用 Authorization Header 或 Sec-WebSocket-Protocol
    Headers:
      Authorization: Bearer <JWT>
      或
      Sec-WebSocket-Protocol: bearer, <JWT>

    Protocol:
    1. Client connects (token via Sec-WebSocket-Protocol or Authorization Header)
    2. Server sends: {"type": "connected", "session_id": "..."}
    3. Client sends: {"type": "audio", "data": "<base64>"} (repeated)
    4. Server sends: {"type": "stream_text", "text": "<partial transcript>"} (repeated, in real-time)
    5. Client sends: {"type": "stop"}
    6. Server sends: {"type": "done", "text": "<final transcript>"}
    """
    # 1. Auth（统一使用 verify_ws_token，支持 Sec-WebSocket-Protocol 和 Authorization Header）
    try:
        auth_user = await verify_ws_token(dict(websocket.headers))
        user_id = auth_user.user_id
    except JWTError as e:
        await websocket.close(code=4001, reason=str(e))
        return
    session_id = str(uuid.uuid4())

    await websocket.accept()
    _logger.info("Voice WS connected", extra={"user_id": user_id, "session_id": session_id})

    manager = get_connection_manager()
    success, error_code, error_msg = await manager.connect(websocket, session_id, user_id)
    if not success:
        await websocket.close(code=4002 if error_code == "TOO_MANY_CONNECTIONS" else 503, reason=error_msg)
        return

    try:
        # Send connected ack
        await websocket.send_json({
            "type": "connected",
            "session_id": session_id,
        })

        # Async queue for buffering audio chunks between receiver and transcriber
        audio_queue: asyncio.Queue = asyncio.Queue()
        accumulated_text = ""

        async def _queue_to_async_iter():
            """Convert queue to async iterable."""
            while True:
                chunk = await audio_queue.get()
                if chunk is None:
                    break
                yield chunk

        async def _transcribe_and_forward():
            """Consume audio from queue, stream to DashScope, forward results to client."""
            nonlocal accumulated_text
            try:
                stt = _get_stt_provider()
                async for text in stt.transcribe_stream(_queue_to_async_iter()):
                    accumulated_text += text
                    await websocket.send_json({
                        "type": "stream_text",
                        "text": text,
                    })
            except STTError as e:
                _logger.error("STT streaming error", extra={"error": str(e), "user_id": user_id})
                await websocket.send_json({
                    "type": "error",
                    "code": "STT_ERROR",
                    "message": f"Transcription failed: {e}",
                })
            except Exception as e:
                _logger.error("Unexpected transcription error", extra={"error": str(e), "user_id": user_id})
                await websocket.send_json({
                    "type": "error",
                    "code": "INTERNAL_ERROR",
                    "message": "Transcription service unavailable",
                })

        transcriber_task = asyncio.create_task(_transcribe_and_forward())

        while True:
            try:
                data = await websocket.receive_json()
            except Exception:
                break

            msg_type = data.get("type")

            if msg_type == "audio":
                b64_data = data.get("data", "")
                if not b64_data:
                    continue

                try:
                    chunk_bytes = base64.b64decode(b64_data)
                except Exception:
                    await websocket.send_json({
                        "type": "error",
                        "code": "INVALID_AUDIO",
                        "message": "Invalid base64 audio data",
                    })
                    continue

                if len(chunk_bytes) > MAX_AUDIO_CHUNK_BYTES:
                    await websocket.send_json({
                        "type": "error",
                        "code": "CHUNK_TOO_LARGE",
                        "message": f"Audio chunk exceeds {MAX_AUDIO_CHUNK_BYTES} bytes",
                    })
                    continue

                await audio_queue.put(chunk_bytes)

            elif msg_type == "stop":
                # Signal end of audio input
                await audio_queue.put(None)

                # Wait for transcriber to finish
                try:
                    await asyncio.wait_for(transcriber_task, timeout=30.0)
                except asyncio.TimeoutError:
                    _logger.warning("Transcription timed out", extra={"user_id": user_id})
                    transcriber_task.cancel()

                await websocket.send_json({
                    "type": "done",
                    "text": accumulated_text,
                    "session_id": session_id,
                })
                accumulated_text = ""

                # Reset: start a new transcriber for next recording
                transcriber_task = asyncio.create_task(_transcribe_and_forward())

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            else:
                await websocket.send_json({
                    "type": "error",
                    "code": "INVALID_MESSAGE_TYPE",
                    "message": f"Unknown message type: {msg_type}",
                })

    except WebSocketDisconnect:
        _logger.info("Voice WS disconnected", extra={"user_id": user_id, "session_id": session_id})
    except Exception as e:
        _logger.error("Voice WS error", extra={"error": str(e), "user_id": user_id})
    finally:
        try:
            transcriber_task.cancel()
            await asyncio.shield(transcriber_task)
        except (asyncio.CancelledError, Exception):
            pass
        try:
            await manager.disconnect(session_id)
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
