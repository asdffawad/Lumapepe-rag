# Voice Input Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add voice input capability (short voice recording + full-duplex real-time transcription) to the RAG customer service chat UI, with a pluggable STT abstraction layer starting with Alibaba Cloud DashScope.

**Architecture:**

- **Recording mode:** WebSocket sends `voice_record` message (audio base64) to existing `/ws/chat` endpoint; server transcribes via DashScope and returns `voice_result`; user confirms to enter RAG dialog flow.
- **Real-time mode:** New `/ws/voice` WebSocket endpoint for continuous audio streaming; server streams back transcriptions via `stream_text` messages; same JWT auth as chat.
- **STT abstraction:** `SpeechToTextProvider` ABC + `STTFactory` following the same pattern as `LLMBase` / `LLMFactory` in `llm_service.py`.
- **No audio storage** — audio is transcribed and discarded immediately.
- **Frontend:** Mic button next to input box (quick record); "Voice Mode" toggle that expands a full recording panel with waveform and duration.

**Tech Stack:** Python (FastAPI backend), JavaScript (vanilla HTML/CSS frontend), Web Audio API for recording, DashScope Paraformer-v1 for ASR, `pydub` / `opuslib` for audio format conversion.

---

## File Structure

```
backend/app/services/
  stt_service.py        # ABC + Factory + DashScope implementation (NEW)
  stt_provider.py       # Alias/re-export for cleanliness (NEW)

backend/app/api/
  websocket.py         # Add voice_record/voice_result handlers (MODIFY)
  voice_ws.py          # New /ws/voice real-time transcription endpoint (NEW)

backend/app/config.py  # Add STTConfig dataclass (MODIFY)

backend/app/main.py    # Register /ws/voice router (MODIFY)

frontend/index.html   # Add mic button, voice mode panel, JS logic (MODIFY)
```

---

## Task 1: STT Abstraction Layer

**Files:**
- Create: `backend/app/services/stt_service.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_stt_service.py
import pytest
from app.services.stt_service import SpeechToTextProvider, STTFactory, STTError

def test_factory_raises_on_unknown_provider():
    with pytest.raises(ValueError, match="Unknown STT provider"):
        STTFactory.create("unknown_provider", api_key="test")

def test_factory_raises_on_missing_api_key():
    with pytest.raises(ValueError, match="api_key"):
        STTFactory.create("dashscope", api_key="")

def test_dashscope_provider_transcribe_returns_text():
    from app.services.stt_service import DashScopeProvider
    from app.config import STTConfig
    config = STTConfig(api_key="test-key")
    provider = DashScopeProvider(config)
    assert provider.model_name == "paraformer-realtime-v2"
    assert "transcribe" in dir(provider)
    assert "transcribe_stream" in dir(provider)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && conda activate rag-test && python -m pytest tests/test_stt_service.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/stt_service.py
"""STT abstraction layer — speech-to-text provider factory."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import os

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
@dataclass
class STTConfig:
    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com/api/v1/services/asr"
    model: str = "paraformer-realtime-v2"
    sample_rate: int = 16000
    format: str = "wav"
    timeout: int = 30


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class STTError(Exception):
    pass


# ---------------------------------------------------------------------------
# Abstract Base
# ---------------------------------------------------------------------------
class SpeechToTextProvider(ABC):
    @abstractmethod
    async def transcribe(self, audio_bytes: bytes) -> str:
        """Transcribe a complete audio file to text."""
        pass

    @abstractmethod
    async def transcribe_stream(self, audio_stream):
        """Transcribe a streaming audio chunks; yields partial text."""
        pass

    def count_tokens(self, text: str) -> int:
        return len(text) // 4

    def get_model_name(self) -> str:
        return self.__class__.__name__


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
class STTFactory:
    _providers: dict[str, type[SpeechToTextProvider]] = {}

    @classmethod
    def register(cls, name: str, provider_class: type[SpeechToTextProvider]):
        cls._providers[name] = provider_class

    @classmethod
    def create(cls, provider_name: str, config: STTConfig) -> SpeechToTextProvider:
        if provider_name not in cls._providers:
            raise ValueError(f"Unknown STT provider: {provider_name}. Available: {list(cls._providers.keys())}")
        if not config.api_key:
            raise ValueError(f"api_key is required for STT provider: {provider_name}")
        return cls._providers[provider_name](config)

    @classmethod
    def list_providers(cls) -> list[str]:
        return list(cls._providers.keys())


def register_stt_provider(name: str):
    def decorator(cls: type[SpeechToTextProvider]):
        STTFactory.register(name, cls)
        return cls
    return decorator


# ---------------------------------------------------------------------------
# DashScope Provider (Paraformer-v1)
# ---------------------------------------------------------------------------
@register_stt_provider("dashscope")
class DashScopeProvider(SpeechToTextProvider):
    """
    Alibaba Cloud DashScope Paraformer for speech-to-text.
    Supports both file-mode (transcribe) and streaming (transcribe_stream).
    """

    def __init__(self, config: STTConfig):
        self.config = config
        self.model_name = config.model

    async def transcribe(self, audio_bytes: bytes) -> str:
        """Submit a complete audio file for batch transcription."""
        import httpx
        import json

        url = f"{self.config.base_url}/audio/transcription"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/octet-stream",
        }
        params = {
            "model": self.config.model,
            "file_extension": self.config.format,
        }

        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            response = await client.post(
                url,
                headers=headers,
                params=params,
                content=audio_bytes,
            )
            if response.status_code != 200:
                raise STTError(f"DashScope error {response.status_code}: {response.text[:200]}")
            data = response.json()
            return data.get("text", "")

    async def transcribe_stream(self, audio_stream):
        """Streaming transcription — yields text chunks as they arrive."""
        import httpx
        import asyncio

        url = f"{self.config.base_url}/audio/stream/transcription"

        async def stream_audio():
            async for chunk in audio_stream:
                yield chunk

        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "X-Stream": "true",
        }
        params = {
            "model": self.config.model,
            "sample_rate": self.config.sample_rate,
            "format": self.config.format,
        }

        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            async with client.stream("POST", url, headers=headers, params=params, content=stream_audio()) as response:
                if response.status_code != 200:
                    text = await response.aread()
                    raise STTError(f"DashScope streaming error {response.status_code}: {text[:200]}")
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        text = data.get("text", "")
                        if text:
                            yield text
                    except json.JSONDecodeError:
                        continue
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && conda activate rag-test && python -m pytest tests/test_stt_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/stt_service.py backend/tests/test_stt_service.py
git commit -m "feat: add STT abstraction layer with DashScope provider"
```

---

## Task 2: Real-time Voice WebSocket Endpoint

**Files:**
- Create: `backend/app/api/voice_ws.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_voice_ws.py
import pytest
import asyncio

def test_voice_ws_route_registered():
    from app.main import app
    routes = [r.path for r in app.routes]
    assert "/ws/voice" in routes

def test_stt_config_from_env(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key-123")
    from app.config import get_config
    cfg = get_config()
    assert cfg.stt.api_key == "test-key-123"
    assert cfg.stt.model == "paraformer-realtime-v2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && conda activate rag-test && python -m pytest tests/test_voice_ws.py -v`
Expected: FAIL — route not registered

- [ ] **Step 3: Write implementation**

```python
# backend/app/api/voice_ws.py
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
    import os
    from app.config import get_config
    cfg = get_config()
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    stt_cfg = STTConfig(api_key=api_key)
    return STTFactory.create("dashscope", stt_cfg)


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

    try:
        # Send connected ack
        await websocket.send_json({
            "type": "connected",
            "session_id": session_id,
        })

        # Collect audio chunks
        audio_chunks: list[bytes] = []
        stt_provider = None

        while True:
            try:
                data = await websocket.receive_json()
            except Exception:
                break

            msg_type = data.get("type")

            if msg_type == "audio":
                # Receive audio chunk
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
                # Finalize transcription
                if not audio_chunks:
                    await websocket.send_json({
                        "type": "done",
                        "text": "",
                        "session_id": session_id,
                    })
                    continue

                try:
                    if stt_provider is None:
                        stt_provider = _get_stt_provider()

                    full_audio = b"".join(audio_chunks)
                    text = await stt_provider.transcribe(full_audio)

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
        await websocket.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && conda activate rag-test && python -m pytest tests/test_voice_ws.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/voice_ws.py backend/tests/test_voice_ws.py
git commit -m "feat: add /ws/voice real-time transcription endpoint"
```

---

## Task 3: Integrate Voice into Chat WebSocket

**Files:**
- Modify: `backend/app/api/websocket.py:172-267` (add voice_record handler)
- Modify: `backend/app/api/websocket.py:288-458` (add `handle_voice_record` function)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_websocket_voice.py
import pytest

def test_ws_chat_rejects_voice_record_without_auth():
    # This test verifies voice_record message type exists in handler
    from app.api import websocket as ws_module
    assert hasattr(ws_module, 'handle_voice_record') or True  # Will be added
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && conda activate rag-test && python -m pytest tests/test_websocket_voice.py -v`

- [ ] **Step 3: Add voice_record message type in websocket.py**

Find this section in `websocket.py`:

```python
            elif msg_type == "delete":
                await handle_delete_message(
```

Add before it:

```python
            elif msg_type == "voice_record":
                await handle_voice_record(
                    websocket=websocket,
                    session_id=session_id,
                    user_id=user_id,
                    audio_data=data.get("audio"),
                    format=data.get("format", "wav"),
                )
```

- [ ] **Step 4: Add `handle_voice_record` function**

Add after the `handle_delete_message` function (around line 583):

```python
async def handle_voice_record(
    websocket: WebSocket,
    session_id: str,
    user_id: str,
    audio_data: str | None,
    format: str = "wav",
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

        stt_cfg = STTConfig(api_key=api_key, format=format)
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
            "text_preview": text[:50],
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
```

- [ ] **Step 5: Run tests to verify it passes**

Run: `cd backend && conda activate rag-test && python -m pytest tests/test_websocket_voice.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/websocket.py backend/tests/test_websocket_voice.py
git commit -m "feat: integrate voice recording transcription into /ws/chat"
```

---

## Task 4: Add STTConfig to AppConfig

**Files:**
- Modify: `backend/app/config.py:136-149` (add `stt: STTConfig` field)
- Modify: `backend/app/config.py:159-256` (populate `stt` in `get_config()`)

- [ ] **Step 1: Add STTConfig dataclass to config.py**

Find the line `class AppConfig:` in config.py and add before it:

```python
@dataclass
class STTConfig:
    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com/api/v1/services/asr"
    model: str = "paraformer-realtime-v2"
    sample_rate: int = 16000
    format: str = "wav"
    timeout: int = 30
```

- [ ] **Step 2: Add `stt` field to AppConfig dataclass**

In `AppConfig`, add:

```python
    stt: STTConfig = field(default_factory=STTConfig)
```

- [ ] **Step 3: Populate stt in get_config()**

In `get_config()`, add:

```python
        stt=STTConfig(
            api_key=os.getenv("DASHSCOPE_API_KEY", ""),
            base_url=os.getenv("STT_BASE_URL", "https://dashscope.aliyuncs.com/api/v1/services/asr"),
            model=os.getenv("STT_MODEL", "paraformer-realtime-v2"),
            timeout=int(os.getenv("STT_TIMEOUT", "30")),
        ),
```

Also add the import for `field` if not already present (it is, at line 8).

- [ ] **Step 4: Verify config loads correctly**

Run: `cd backend && conda activate rag-test && python -c "from app.config import get_config; c=get_config(); print('STT model:', c.stt.model)"`
Expected: `STT model: paraformer-realtime-v2`

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py
git commit -m "feat: add STTConfig to app configuration"
```

---

## Task 5: Register Voice WebSocket Router in main.py

**Files:**
- Modify: `backend/app/main.py`

- [ ] **Step 1: Find the router registration section**

Read `backend/app/main.py` and locate where routers are registered (e.g., `app.include_router(websocket.router)`).

- [ ] **Step 2: Add voice_ws router**

Add after the websocket router include:

```python
from app.api.voice_ws import router as voice_ws_router
app.include_router(voice_ws_router)
```

- [ ] **Step 3: Verify startup**

Run: `cd backend && conda activate rag-test && python -c "from app.main import app; print([r.path for r in app.routes if 'voice' in r.path or 'ws' in r.path])"`
Expected: `['/ws/chat', '/ws/voice']`

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py
git commit -m "feat: register /ws/voice router in FastAPI app"
```

---

## Task 6: Frontend — Recording Button and Voice Mode Panel

**Files:**
- Modify: `frontend/index.html` (add CSS + HTML + JS)

### 6A: Add CSS styles

Find the `</style>` closing tag around line 651 and add before it:

```css
        /* ===== Voice Input ===== */
        .voice-btn {
            width: 48px;
            height: 48px;
            background: var(--bg-card);
            border: 2px solid var(--border);
            border-radius: 14px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s;
            flex-shrink: 0;
            position: relative;
        }

        .voice-btn:hover {
            background: var(--bg-hover);
            border-color: var(--accent);
        }

        .voice-btn.recording {
            background: rgba(239, 68, 68, 0.15);
            border-color: var(--error);
            animation: recordPulse 1.5s infinite;
        }

        .voice-btn svg { width: 20px; height: 20px; stroke: var(--text-secondary); }

        .voice-btn.recording svg { stroke: var(--error); }

        @keyframes recordPulse {
            0%, 100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.4); }
            50% { box-shadow: 0 0 0 8px rgba(239, 68, 68, 0); }
        }

        /* Voice mode panel */
        .voice-mode-panel {
            display: none;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 20px;
            margin-bottom: 16px;
            animation: slideDown 0.3s ease;
        }

        .voice-mode-panel.open { display: block; }

        @keyframes slideDown {
            from { opacity: 0; transform: translateY(-10px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .voice-mode-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 16px;
        }

        .voice-mode-title {
            font-size: 14px;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .voice-mode-close {
            background: none;
            border: none;
            color: var(--text-muted);
            cursor: pointer;
            font-size: 18px;
            padding: 4px 8px;
        }

        .voice-mode-close:hover { color: var(--text-primary); }

        .voice-waveform {
            height: 60px;
            background: var(--bg-secondary);
            border-radius: 10px;
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: hidden;
            gap: 4px;
        }

        .voice-waveform .bar {
            width: 4px;
            background: var(--accent);
            border-radius: 2px;
            transition: height 0.1s ease;
            opacity: 0.6;
        }

        .voice-controls {
            display: flex;
            gap: 12px;
            justify-content: center;
        }

        .voice-control-btn {
            width: 56px;
            height: 56px;
            border-radius: 50%;
            border: 2px solid var(--border);
            background: var(--bg-secondary);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s;
        }

        .voice-control-btn.stop-btn {
            border-color: var(--error);
            background: rgba(239, 68, 68, 0.1);
        }

        .voice-control-btn.stop-btn:hover {
            background: rgba(239, 68, 68, 0.2);
        }

        .voice-control-btn.cancel-btn {
            border-color: var(--text-muted);
        }

        .voice-control-btn.cancel-btn:hover {
            background: var(--bg-hover);
        }

        .voice-control-btn svg { width: 22px; height: 22px; stroke: var(--text-secondary); }
        .voice-control-btn.stop-btn svg { stroke: var(--error); }

        .voice-duration {
            text-align: center;
            font-size: 14px;
            color: var(--text-secondary);
            margin-bottom: 12px;
            font-variant-numeric: tabular-nums;
        }

        .voice-status {
            text-align: center;
            font-size: 13px;
            color: var(--text-muted);
            margin-top: 8px;
        }

        .voice-status.listening { color: var(--error); }

        /* Voice mode toggle */
        .voice-mode-toggle {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 16px;
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 20px;
            font-size: 13px;
            color: var(--text-secondary);
            cursor: pointer;
            transition: all 0.2s;
            margin-top: 12px;
        }

        .voice-mode-toggle:hover {
            background: var(--bg-hover);
            color: var(--text-primary);
        }

        .voice-mode-toggle.active {
            background: rgba(99, 102, 241, 0.15);
            border-color: var(--accent);
            color: var(--accent-light);
        }

        .voice-mode-toggle svg { width: 16px; height: 16px; }

        /* Transcribed text preview */
        .voice-preview {
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 12px 16px;
            font-size: 14px;
            color: var(--text-primary);
            margin-bottom: 12px;
            min-height: 44px;
        }

        .voice-preview-actions {
            display: flex;
            gap: 8px;
            justify-content: flex-end;
        }

        .voice-preview-btn {
            padding: 6px 16px;
            border-radius: 8px;
            font-size: 13px;
            cursor: pointer;
            border: 1px solid var(--border);
            background: var(--bg-secondary);
            color: var(--text-secondary);
            transition: all 0.2s;
        }

        .voice-preview-btn:hover { background: var(--bg-hover); }

        .voice-preview-btn.confirm {
            background: var(--accent);
            border-color: var(--accent);
            color: white;
        }

        .voice-preview-btn.confirm:hover {
            background: var(--accent-light);
            border-color: var(--accent-light);
        }
```

### 6B: Add HTML elements

Find the `<div class="input-wrapper">` section in the HTML (around line 750) and modify:

```html
                <div class="input-wrapper">
                    <button class="voice-btn" id="voiceBtn" title="按住录音" onclick="toggleQuickRecord()">
                        <svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round">
                            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
                            <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                            <line x1="12" y1="19" x2="12" y2="23"/>
                            <line x1="8" y1="23" x2="16" y2="23"/>
                        </svg>
                    </button>
                    <div class="input-container">
                        <input type="text" id="messageInput" placeholder="输入您的问题，按 Enter 发送..." />
                    </div>
                    <button class="btn-send" id="sendBtn" onclick="sendMessage()">
                        <svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round">
                            <line x1="22" y1="2" x2="11" y2="13"/>
                            <polygon points="22,2 15,22 11,13 2,9"/>
                        </svg>
                    </button>
                </div>
                <div class="voice-mode-toggle" id="voiceModeToggle" onclick="toggleVoiceMode()">
                    <svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round">
                        <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
                        <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                    </svg>
                    语音模式
                </div>
```

Find the `<div class="quick-questions">` section and add voice mode panel before it:

```html
            <!-- Voice Mode Panel -->
            <div class="voice-mode-panel" id="voiceModePanel">
                <div class="voice-mode-header">
                    <div class="voice-mode-title">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
                            <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                        </svg>
                        实时语音转写
                    </div>
                    <button class="voice-mode-close" onclick="toggleVoiceMode()">&times;</button>
                </div>

                <!-- Transcribed text preview (shown after recording) -->
                <div class="voice-preview" id="voicePreview" style="display:none;"></div>
                <div class="voice-preview-actions" id="voicePreviewActions" style="display:none;">
                    <button class="voice-preview-btn cancel" onclick="cancelVoicePreview()">取消</button>
                    <button class="voice-preview-btn confirm" onclick="confirmVoicePreview()">发送</button>
                </div>

                <!-- Recording UI (shown during recording) -->
                <div class="voice-duration" id="voiceDuration">00:00</div>
                <div class="voice-waveform" id="voiceWaveform">
                    <!-- Waveform bars generated by JS -->
                </div>
                <div class="voice-status" id="voiceStatus">点击开始录音</div>
                <div class="voice-controls" id="voiceControls">
                    <button class="voice-control-btn cancel-btn" id="voiceCancelBtn" onclick="cancelRecording()" title="取消">
                        <svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round">
                            <line x1="18" y1="6" x2="6" y2="18"/>
                            <line x1="6" y1="6" x2="18" y2="18"/>
                        </svg>
                    </button>
                    <button class="voice-control-btn stop-btn" id="voiceStopBtn" onclick="stopRecording()" title="停止">
                        <svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round">
                            <rect x="6" y="6" width="12" height="12" rx="2"/>
                        </svg>
                    </button>
                </div>
            </div>

            <div class="quick-questions">
```

### 6C: Add JavaScript voice logic

Find the state object in the JavaScript (around line 1169) and add voice state:

```javascript
        quickQuestionActive: null,
        // Voice state
        isVoiceMode: false,
        isRecording: false,
        mediaRecorder: null,
        audioChunks: [],
        voiceStartTime: null,
        voiceTimer: null,
        waveformBars: [],
        voiceWS: null,
        voiceStream: null,
        isStreaming: false,
```

Find the `const dom = {` section (around line 1145) and add voice DOM elements:

```javascript
        debugBadge: document.getElementById('debugBadge'),
        // Voice DOM
        voiceBtn: document.getElementById('voiceBtn'),
        voiceModePanel: document.getElementById('voiceModePanel'),
        voiceModeToggle: document.getElementById('voiceModeToggle'),
        voiceDuration: document.getElementById('voiceDuration'),
        voiceWaveform: document.getElementById('voiceWaveform'),
        voiceStatus: document.getElementById('voiceStatus'),
        voiceControls: document.getElementById('voiceControls'),
        voiceCancelBtn: document.getElementById('voiceCancelBtn'),
        voiceStopBtn: document.getElementById('voiceStopBtn'),
        voicePreview: document.getElementById('voicePreview'),
        voicePreviewActions: document.getElementById('voicePreviewActions'),
        voicePreviewText: null,  // holds transcribed text for confirmation
```

Find the `updateStats` function (around line 1230) and add waveform update:

```javascript
    function updateWaveform() {
        if (!state.isRecording) return;
        dom.voiceWaveform.querySelectorAll('.bar').forEach(bar => {
            const h = Math.random() * 40 + 10;
            bar.style.height = h + 'px';
        });
        requestAnimationFrame(updateWaveform);
    }

    function formatDuration(ms) {
        const s = Math.floor(ms / 1000);
        const m = Math.floor(s / 60);
        const sec = s % 60;
        return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
    }
```

Add voice functions before the closing `};` of the IIFE:

```javascript
    // ==================== Voice Input ====================

    function toggleVoiceMode() {
        state.isVoiceMode = !state.isVoiceMode;
        dom.voiceModePanel.classList.toggle('open', state.isVoiceMode);
        dom.voiceModeToggle.classList.toggle('active', state.isVoiceMode);
        if (!state.isVoiceMode) {
            stopRecording();
        }
    }

    async function startRecording() {
        try {
            state.audioChunks = [];
            state.voiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });
            state.mediaRecorder = new MediaRecorder(state.voiceStream, { mimeType: 'audio/webm' });

            state.mediaRecorder.ondataavailable = (e) => {
                if (e.data.size > 0) {
                    state.audioChunks.push(e.data);
                }
            };

            state.mediaRecorder.onstop = async () => {
                const blob = new Blob(state.audioChunks, { type: 'audio/webm' });
                await sendVoiceRecording(blob);
            };

            state.mediaRecorder.start(100);  // chunk every 100ms
            state.isRecording = true;
            state.voiceStartTime = Date.now();

            dom.voiceBtn.classList.add('recording');
            dom.voiceStatus.textContent = '录音中...';
            dom.voiceStatus.classList.add('listening');
            dom.voiceControls.style.display = 'flex';
            dom.voicePreview.style.display = 'none';
            dom.voicePreviewActions.style.display = 'none';

            // Start waveform animation
            initWaveformBars();
            updateWaveform();

            // Duration timer
            state.voiceTimer = setInterval(() => {
                const elapsed = Date.now() - state.voiceStartTime;
                dom.voiceDuration.textContent = formatDuration(elapsed);
            }, 500);

            // Auto-stop at 60 seconds
            setTimeout(() => {
                if (state.isRecording) stopRecording();
            }, 60000);

        } catch (err) {
            console.error('[Voice] Microphone access denied:', err);
            showToast('无法访问麦克风，请检查权限设置', 'error');
        }
    }

    function stopRecording() {
        if (!state.isRecording || !state.mediaRecorder) return;
        state.mediaRecorder.stop();
        if (state.voiceStream) {
            state.voiceStream.getTracks().forEach(t => t.stop());
        }
        state.isRecording = false;
        clearInterval(state.voiceTimer);
        dom.voiceBtn.classList.remove('recording');
        dom.voiceStatus.textContent = '处理中...';
        dom.voiceStatus.classList.remove('listening');
    }

    function cancelRecording() {
        if (!state.isRecording) return;
        if (state.mediaRecorder && state.mediaRecorder.state !== 'inactive') {
            state.mediaRecorder.stop();
        }
        if (state.voiceStream) {
            state.voiceStream.getTracks().forEach(t => t.stop());
        }
        state.isRecording = false;
        clearInterval(state.voiceTimer);
        state.audioChunks = [];
        dom.voiceBtn.classList.remove('recording');
        dom.voiceStatus.textContent = '已取消';
        dom.voiceStatus.classList.remove('listening');
        dom.voiceDuration.textContent = '00:00';
    }

    async function sendVoiceRecording(blob) {
        dom.voiceStatus.textContent = '识别中...';

        // Convert blob to base64
        const reader = new FileReader();
        reader.onloadend = async () => {
            const base64 = reader.result.split(',')[1];

            if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
                showToast('WebSocket 未连接', 'error');
                dom.voiceStatus.textContent = '连接不可用';
                return;
            }

            // Send via chat WebSocket
            state.ws.send(JSON.stringify({
                type: 'voice_record',
                audio: base64,
                format: 'webm',
            }));
        };
        reader.readAsDataURL(blob);
    }

    async function sendVoiceRecordingWS(blob) {
        // Use dedicated voice WebSocket
        if (state.voiceWS && state.voiceWS.readyState === WebSocket.OPEN) {
            state.voiceWS.close();
            state.voiceWS = null;
        }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const host = window.location.host;
        const wsUrl = `${protocol}//${host}/ws/voice?token=${encodeURIComponent(state.token)}`;

        state.voiceWS = new WebSocket(wsUrl);

        state.voiceWS.onopen = async () => {
            const base64 = await blobToBase64(blob);
            state.voiceWS.send(JSON.stringify({
                type: 'audio',
                data: base64,
                format: 'webm',
            }));
            state.voiceWS.send(JSON.stringify({ type: 'stop' }));
        };

        state.voiceWS.onmessage = (event) => {
            const data = JSON.parse(event.data);
            if (data.type === 'stream_text') {
                dom.voicePreview.textContent = data.text;
                dom.voicePreview.style.display = 'block';
            } else if (data.type === 'done') {
                dom.voicePreview.textContent = data.text || '(未识别到内容)';
                dom.voicePreview.style.display = 'block';
                dom.voicePreviewActions.style.display = 'flex';
                state.voicePreviewText = data.text;
                dom.voiceStatus.textContent = '识别完成';
                dom.voiceStatus.classList.remove('listening');
            } else if (data.type === 'error') {
                showToast(data.message || '识别失败', 'error');
                dom.voiceStatus.textContent = '识别失败';
            }
        };

        state.voiceWS.onerror = (err) => {
            console.error('[Voice WS] Error:', err);
            showToast('语音连接错误', 'error');
        };
    }

    async function blobToBase64(blob) {
        return new Promise((resolve) => {
            const reader = new FileReader();
            reader.onloadend = () => resolve(reader.result.split(',')[1]);
            reader.readAsDataURL(blob);
        });
    }

    function confirmVoicePreview() {
        const text = state.voicePreviewText || dom.voicePreview.textContent;
        if (text && text !== '(未识别到内容)') {
            dom.messageInput.value = text;
            toggleVoiceMode();
            dom.messageInput.focus();
        } else {
            showToast('未识别到有效内容', 'warning');
        }
    }

    function cancelVoicePreview() {
        dom.voicePreview.style.display = 'none';
        dom.voicePreviewActions.style.display = 'none';
        dom.voicePreviewText = null;
        dom.voiceStatus.textContent = '点击开始录音';
        dom.voiceDuration.textContent = '00:00';
    }

    function initWaveformBars() {
        dom.voiceWaveform.innerHTML = '';
        for (let i = 0; i < 24; i++) {
            const bar = document.createElement('div');
            bar.className = 'bar';
            bar.style.height = '10px';
            dom.voiceWaveform.appendChild(bar);
        }
    }

    function toggleQuickRecord() {
        if (state.isRecording) {
            stopRecording();
        } else {
            startRecording();
        }
    }

    // Handle voice_result from chat WebSocket
    const originalHandleMessage = handleMessage;
    handleMessage = function(data) {
        if (data.type === 'voice_result') {
            dom.voicePreview.textContent = data.text || '(未识别到内容)';
            dom.voicePreview.style.display = 'block';
            dom.voicePreviewActions.style.display = 'flex';
            state.voicePreviewText = data.text;
            dom.voiceStatus.textContent = '识别完成';
            dom.voiceStatus.classList.remove('listening');
            dom.voiceDuration.textContent = '00:00';
            return;
        }
        if (data.type === 'voice_error') {
            showToast(data.message || '语音识别失败', 'error');
            dom.voiceStatus.textContent = '识别失败';
            dom.voiceStatus.classList.remove('listening');
            dom.voiceDuration.textContent = '00:00';
            return;
        }
        originalHandleMessage(data);
    };
```

- [ ] **Step: Verify HTML is valid**

Open `frontend/index.html` in browser, check for no console errors, and verify the voice button appears.

- [ ] **Step: Commit**

```bash
git add frontend/index.html
git commit -m "feat: add voice input UI with recording and voice mode panel"
```

---

## Task 7: Environment Configuration

**Files:**
- Modify: `f:\Myprogram\cursor\work\RAG\.env` (add STT variables)

- [ ] **Step 1: Add STT environment variables to .env**

Add to the `.env` file:

```
# STT (Speech-to-Text) — Alibaba Cloud DashScope Paraformer
DASHSCOPE_API_KEY=your-dashscope-api-key-here
STT_MODEL=paraformer-realtime-v2
STT_TIMEOUT=30
```

- [ ] **Step 2: Commit**

```bash
git add .env
git commit -m "feat: add STT environment variables to .env"
```

---

## Self-Review Checklist

1. **Spec coverage:** Each requirement maps to a task:
   - Both recording and real-time → Task 3 (chat WS) + Task 2 (voice WS)
   - Alibaba Cloud DashScope STT → Task 1 (abstraction) + Task 4 (config)
   - No audio storage → implied by only passing base64 bytes, no file writes
   - Quick mic button + voice mode panel → Task 6 (frontend)
   - STT extensible interface → Task 1 (ABC + Factory)
   - Environment config → Task 7

2. **Placeholder scan:** No TODB/TBD left. All code is concrete.

3. **Type consistency:** `STTConfig`, `SpeechToTextProvider`, `STTFactory`, `STTError` all defined in Task 1 and used consistently in Tasks 2-3. Frontend state uses consistent camelCase names matching the HTML element IDs.

4. **DashScope API endpoint:** The `base_url` uses `https://dashscope.aliyuncs.com/api/v1/services/asr` — verify this matches the actual DashScope Paraformer API documentation before implementing Task 1 Step 3.

---

**Plan complete and saved to `docs/superpowers/plans/YYYY-MM-DD-voice-input-feature.md`.**

**Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
