"""Real-time voice transcription WebSocket endpoint."""
from __future__ import annotations

import asyncio
import base64
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from app.api.ws_manager import get_connection_manager
from app.services.stt_service import STTFactory, STTConfig, STTError
from app.middleware.auth import decode_token, JWTError
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
    token: str = Query(None),
):
    """
    Real-time voice transcription WebSocket.

    Protocol:
    1. Client connects with ?token=<jwt>
    2. Server sends: {"type": "connected"}
    3. Client sends: {"type": "audio", "data": "<base64>", "format": "wav"}
    4. Server sends: {"type": "stream_text", "text": "<partial transcript>"}
    5. Client sends: {"type": "stop"}
    6. Server sends: {"type": "done", "text": "<final transcript>"}
    """
    # 1. Auth
    auth_header = dict(websocket.headers).get("Authorization") or ""
    ws_token = token
    if not ws_token and auth_header.startswith("Bearer "):
        ws_token = auth_header[7:]

    try:
        auth_user = decode_token(ws_token) if ws_token else None
    except JWTError:
        await websocket.close(code=4001, reason="Invalid token")
        return

    user_id = auth_user.user_id if auth_user else "anonymous"
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

        # Collect audio chunks
        audio_chunks: list[bytes] = []

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

                audio_chunks.append(chunk_bytes)

            elif msg_type == "stop":
                if not audio_chunks:
                    await websocket.send_json({
                        "type": "done",
                        "text": "",
                        "session_id": session_id,
                    })
                    continue

                try:
                    stt = _get_stt_provider()
                    full_audio = b"".join(audio_chunks)
                    text = await stt.transcribe(full_audio)

                    await websocket.send_json({
                        "type": "done",
                        "text": text,
                        "session_id": session_id,
                    })
                    _logger.info("Transcription completed", extra={
                        "user_id": user_id,
                        "session_id": session_id,
                        "text_length": len(text),
                    })

                except STTError as e:
                    _logger.error("STT error", extra={"error": str(e), "user_id": user_id})
                    await websocket.send_json({
                        "type": "error",
                        "code": "STT_ERROR",
                        "message": f"Transcription failed: {e}",
                    })
                except Exception as e:
                    _logger.error("Unexpected error during transcription", extra={
                        "error": str(e), "user_id": user_id
                    })
                    await websocket.send_json({
                        "type": "error",
                        "code": "INTERNAL_ERROR",
                        "message": "Internal transcription error",
                    })

                audio_chunks.clear()

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
            await manager.disconnect(session_id)
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
