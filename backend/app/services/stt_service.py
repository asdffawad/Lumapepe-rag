"""
STT Abstraction Layer - Speech-to-Text Provider Factory

Supports multiple STT providers with a consistent interface for:
- File transcription (batch mode)
- Streaming transcription (real-time mode)

Based on DashScope Paraformer API research:
- REST API (file): https://dashscope.aliyuncs.com - requires publicly accessible URLs
- WebSocket API (streaming): wss://dashscope.aliyuncs.com/api-ws/v1/inference
"""
from __future__ import annotations

import asyncio
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from app.logging_config import get_logger
from app import metrics as _metrics


_logger = get_logger("stt.service")

# STT metrics (extend as needed)
_stt_calls_total = _metrics.llm_calls_total  # Reuse LLM metric pattern
_stt_duration_seconds = _metrics.llm_call_duration_seconds


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
@dataclass
class STTConfig:
    """Configuration for STT providers."""
    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com"
    model: str = "paraformer-realtime-v2"
    sample_rate: int = 16000
    format: str = "pcm"
    timeout: int = 30


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class STTError(Exception):
    """Base exception for STT-related errors."""
    pass


# ---------------------------------------------------------------------------
# Abstract Base
# ---------------------------------------------------------------------------
class SpeechToTextProvider(ABC):
    """
    Abstract base class for speech-to-text providers.

    Implementations must provide:
    - transcribe(): Batch transcription of complete audio
    - transcribe_stream(): Real-time streaming transcription
    """

    def __init__(self, config: STTConfig):
        self.config = config

    @abstractmethod
    async def transcribe(self, audio_source: str | bytes) -> str:
        """
        Transcribe a complete audio file to text.

        Args:
            audio_source: Either a publicly accessible URL (str) or
                        audio bytes for streaming providers

        Returns:
            Transcribed text string
        """
        pass

    @abstractmethod
    async def transcribe_stream(self, audio_stream):
        """
        Transcribe a streaming audio source; yields partial text results.

        Args:
            audio_stream: Async iterable of audio chunks (bytes)

        Yields:
            Partial transcription results as text arrives
        """
        pass

    def count_tokens(self, text: str) -> int:
        """Estimate token count using character-based method."""
        return len(text) // 4

    def get_model_name(self) -> str:
        """Return the model name for this provider."""
        return self.config.model


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
class STTFactory:
    """
    Factory for creating STT provider instances.

    Usage:
        config = STTConfig(api_key="your-key")
        provider = STTFactory.create("dashscope", config)
    """
    _providers: dict[str, type[SpeechToTextProvider]] = {}

    @classmethod
    def register(cls, name: str, provider_class: type[SpeechToTextProvider]):
        """Register a provider class with a name."""
        cls._providers[name] = provider_class

    @classmethod
    def create(cls, provider_name: str, config: STTConfig) -> SpeechToTextProvider:
        """
        Create an STT provider instance.

        Args:
            provider_name: Name of the provider to create
            config: STT configuration

        Returns:
            Configured SpeechToTextProvider instance

        Raises:
            ValueError: If provider unknown or config invalid
        """
        if provider_name not in cls._providers:
            available = list(cls._providers.keys())
            raise ValueError(
                f"Unknown STT provider: {provider_name}. "
                f"Available: {available}"
            )
        if not config.api_key:
            raise ValueError(f"api_key is required for STT provider: {provider_name}")
        return cls._providers[provider_name](config)

    @classmethod
    def list_providers(cls) -> list[str]:
        """List all registered provider names."""
        return list(cls._providers.keys())


def register_stt_provider(name: str):
    """Decorator to register a provider class."""
    def decorator(cls: type[SpeechToTextProvider]):
        STTFactory.register(name, cls)
        return cls
    return decorator


# ---------------------------------------------------------------------------
# DashScope Provider (Paraformer Realtime)
# ---------------------------------------------------------------------------
@register_stt_provider("dashscope")
class DashScopeProvider(SpeechToTextProvider):
    """
    Alibaba Cloud DashScope Paraformer for speech-to-text.

    Supports:
    - File transcription via REST API (requires public URL)
    - Streaming transcription via WebSocket API

    API Documentation:
    - REST API: https://www.alibabacloud.com/help/en/model-studio/paraformer-recorded-speech-recognition-python-sdk
    - WebSocket API: https://help.aliyun.com/zh/model-studio/websocket-for-paraformer-real-time-service
    """

    def __init__(self, config: STTConfig):
        super().__init__(config)
        self.model_name = config.model

    async def transcribe(self, audio_source: str | bytes) -> str:
        """
        Submit audio for batch transcription.

        Note: REST API requires publicly accessible URLs. For local files,
        use transcribe_stream() instead which accepts raw audio via WebSocket.

        Args:
            audio_source: Public URL string or audio bytes

        Returns:
            Transcribed text
        """
        import httpx

        if isinstance(audio_source, str):
            api_url = f"{self.config.base_url}/api/v1/services/asr/audio/transcription"
            headers = {
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.config.model,
                "file_urls": [audio_source],
            }

            async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                response = await client.post(api_url, headers=headers, json=payload)

                if response.status_code != 200:
                    raise STTError(
                        f"DashScope error {response.status_code}: {response.text[:500]}"
                    )

                data = response.json()
                task_id = data.get("output", {}).get("task_id")
                if not task_id:
                    raise STTError(f"No task_id in response: {data}")

                result_url = await self._poll_task_result(task_id, client, headers)
                return await self._fetch_transcription_result(result_url)
        else:
            results = []
            async for text in self._stream_audio([audio_source]):
                results.append(text)
            return "".join(results)

    async def _poll_task_result(self, task_id: str, client: httpx.AsyncClient, headers: dict) -> str:
        """Poll for task completion and return transcription URL."""
        fetch_url = f"{self.config.base_url}/api/v1/services/asr/audio/transcription"

        for _ in range(60):
            await asyncio.sleep(5)

            response = await client.get(
                fetch_url,
                headers=headers,
                params={"task_id": task_id}
            )

            if response.status_code != 200:
                continue

            data = response.json()
            task_status = data.get("output", {}).get("task_status")

            if task_status == "SUCCEEDED":
                results = data.get("output", {}).get("results", [])
                if results:
                    return results[0].get("transcription_url")
                raise STTError("No transcription results found")

            elif task_status == "FAILED":
                error = data.get("output", {}).get("message", "Unknown error")
                raise STTError(f"Transcription failed: {error}")

        raise STTError("Transcription timed out")

    async def _fetch_transcription_result(self, result_url: str) -> str:
        """Download and parse transcription result from result URL."""
        import httpx

        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            response = await client.get(result_url)

            if response.status_code != 200:
                raise STTError(f"Failed to fetch result: {response.status_code}")

            data = response.json()

            transcripts = data.get("transcripts", [])
            if not transcripts:
                return ""

            texts = []
            for transcript in transcripts:
                text = transcript.get("text", "")
                if text:
                    texts.append(text)

            return " ".join(texts)

    async def transcribe_stream(self, audio_stream):
        """
        Stream audio for real-time transcription via WebSocket.

        Args:
            audio_stream: Async iterable of audio chunks (bytes)

        Yields:
            Transcription text as it arrives
        """
        async for text in self._stream_audio(audio_stream):
            yield text

    async def _stream_audio(self, audio_stream):
        """
        Internal streaming via DashScope WebSocket protocol using aiohttp.

        Protocol flow (per official DashScope docs):
        1. Connect to wss://dashscope.aliyuncs.com/api-ws/v1/inference
        2. Collect audio and detect format (WAV vs raw PCM)
        3. Send run-task JSON command with detected format/sample_rate
        4. Wait for task-started event (status 200)
        5. Stream binary audio chunks
        6. Receive result-generated events with transcription text
        7. Send finish-task command
        8. Close connection
        """
        import aiohttp

        task_id = uuid.uuid4().hex[:32]
        ws_url = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"

        _logger.info(
            "DashScope streaming started",
            extra={"task_id": task_id, "model": self.config.model}
        )

        start_time = time.monotonic()
        status = "success"

        async def _send_audio(ws_conn, chunks):
            """Send audio chunks to the WebSocket."""
            for i, chunk in enumerate(chunks):
                if ws_conn.closed:
                    _logger.warning(
                        "WebSocket closed while sending audio",
                        extra={"task_id": task_id, "chunks_sent": i}
                    )
                    return
                await ws_conn.send_bytes(chunk)
                await asyncio.sleep(0.05)

        try:
            headers = {"Authorization": f"Bearer {self.config.api_key}"}
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(ws_url, headers=headers) as ws:
                    # Step 3: Collect audio and detect format BEFORE sending run-task
                    audio_chunks = [chunk async for chunk in self._iter_audio(audio_stream)]
                    audio_bytes = b''.join(audio_chunks)
                    total_bytes = len(audio_bytes)

                    # Detect WAV: if first 4 bytes are RIFF, use raw WAV bytes with detected sample rate
                    audio_format = self.config.format
                    effective_sample_rate = self.config.sample_rate
                    if audio_bytes[:4] == b'RIFF':
                        audio_format = "wav"
                        try:
                            _, detected_sr = self._parse_wav(audio_bytes)
                            effective_sample_rate = detected_sr
                            _logger.info(
                                "Detected WAV audio, will send raw WAV bytes",
                                extra={
                                    "task_id": task_id,
                                    "detected_sample_rate": detected_sr,
                                    "original_bytes": total_bytes,
                                }
                            )
                        except ValueError as e:
                            _logger.warning(
                                "Failed to parse WAV header, using default sample rate",
                                extra={"task_id": task_id, "error": str(e)},
                            )
                    else:
                        _logger.debug(
                            "Audio chunks collected",
                            extra={
                                "task_id": task_id,
                                "chunk_count": len(audio_chunks),
                                "total_bytes": total_bytes,
                            }
                        )

                    # Step 4: Send run-task with actual detected format and sample rate
                    run_task_msg = {
                        "header": {
                            "action": "run-task",
                            "task_id": task_id,
                            "streaming": "duplex"
                        },
                        "payload": {
                            "task_group": "audio",
                            "task": "asr",
                            "function": "recognition",
                            "model": self.config.model,
                            "parameters": {
                                "format": audio_format,
                                "sample_rate": effective_sample_rate,
                                "language_hints": ["zh"],
                            },
                            "input": {}
                        }
                    }

                    _logger.debug("Sending run-task message", extra={"task_id": task_id})
                    await ws.send_json(run_task_msg)

                    # Step 5: Wait for task-started (status 200)
                    resp = await ws.receive_json()
                    _logger.debug("DashScope response", extra={"response": resp})
                    resp_header = resp.get("header", {})
                    event_type = resp_header.get("event", "")
                    if event_type != "task-started":
                        error_code = resp_header.get("error_code", "UNKNOWN")
                        error_msg = resp_header.get("error_message", "Unknown error")
                        _logger.error(
                            "DashScope task start failed",
                            extra={
                                "task_id": task_id,
                                "event": event_type,
                                "error_code": error_code,
                                "error_message": error_msg,
                                "full_response": resp,
                            }
                        )
                        raise STTError(f"DashScope task start failed: {error_code} - {error_msg}")

                    _logger.debug("Task started successfully", extra={"task_id": task_id})

                    # Step 6: Send audio chunks in background while receiving results
                    send_task = asyncio.create_task(_send_audio(ws, audio_chunks))

                    # Step 7: Receive transcription results while audio is being sent
                    while True:
                        try:
                            ws_msg = await asyncio.wait_for(ws.receive(), timeout=10.0)

                            # DashScope may send binary frames (audio acknowledgment)
                            # alongside JSON text frames. Only process TEXT frames.
                            if ws_msg.type == aiohttp.WSMsgType.BINARY:
                                _logger.debug(
                                    "Received binary frame from DashScope, skipping",
                                    extra={"task_id": task_id, "data_len": len(ws_msg.data)}
                                )
                                continue
                            if ws_msg.type != aiohttp.WSMsgType.TEXT:
                                if ws_msg.type == aiohttp.WSMsgType.CLOSE:
                                    _logger.debug("Connection closed by DashScope", extra={"task_id": task_id})
                                    send_task.cancel()
                                    break
                                # Handle ping/pong from DashScope server
                                if ws_msg.type == aiohttp.WSMsgType.PING:
                                    _logger.debug("Received PING from DashScope, responding with PONG", extra={"task_id": task_id})
                                    await ws.pong()
                                    continue
                                if ws_msg.type == aiohttp.WSMsgType.PONG:
                                    _logger.debug("Received PONG from DashScope", extra={"task_id": task_id})
                                    continue
                                _logger.warning(
                                    "Unexpected WebSocket frame type",
                                    extra={"task_id": task_id, "type": ws_msg.type}
                                )
                                continue

                            msg = ws_msg.json()
                            header = msg.get("header", {})
                            event_type = header.get("event", "")

                            if event_type == "task-finished":
                                _logger.debug("Received task-finished from server", extra={"task_id": task_id})
                                break
                            if event_type == "task-failed":
                                send_task.cancel()
                                error_msg = header.get("error_message", "Unknown error")
                                raise STTError(f"DashScope task failed: {error_msg}")

                            # Handle result-generated events
                            if event_type == "result-generated":
                                sentence = msg.get("payload", {}).get("output", {}).get("sentence", {})
                                # Skip heartbeat events - they are just keep-alive pings, not recognition results
                                if sentence.get("heartbeat"):
                                    _logger.debug("Received heartbeat event, skipping", extra={"task_id": task_id})
                                    continue
                                text = sentence.get("text", "")
                                if text:
                                    _logger.info(
                                        "DashScope transcription result",
                                        extra={"text_length": len(text), "task_id": task_id, "end_time": sentence.get("end_time")}
                                    )
                                    yield text

                        except asyncio.TimeoutError:
                            if send_task.done():
                                _logger.debug(
                                    "Audio sent and no more transcription results (timeout)",
                                    extra={"task_id": task_id}
                                )
                                break
                            _logger.debug("Audio still sending, waiting for results", extra={"task_id": task_id})

                    # Step 8: Send finish-task command only if connection is still open
                    if not ws.closed:
                        _logger.debug("Sending finish-task command", extra={"task_id": task_id})
                        try:
                            await ws.send_json({
                                "header": {
                                    "action": "finish-task",
                                    "task_id": task_id,
                                    "streaming": "duplex"
                                },
                                "payload": {"input": {}}
                            })
                        except Exception as e:
                            _logger.warning(
                                "Failed to send finish-task (connection may already be closed)",
                                extra={"task_id": task_id, "error": str(e)}
                            )
                    else:
                        _logger.debug("WebSocket already closed, skipping finish-task", extra={"task_id": task_id})

        except STTError:
            raise
        except Exception as e:
            status = "error"
            _logger.error(
                "DashScope streaming error",
                extra={"error": str(e), "task_id": task_id}
            )
            raise STTError(f"WebSocket error: {e}")
        finally:
            duration = time.monotonic() - start_time
            _stt_calls_total.labels(model=self.config.model, status=status).inc()
            _stt_duration_seconds.labels(model=self.config.model).observe(duration)

    async def _iter_audio(self, audio_stream):
        """Convert audio source to async iterable of chunks."""
        if hasattr(audio_stream, '__aiter__'):
            async for chunk in audio_stream:
                yield chunk
        else:
            for chunk in audio_stream:
                yield chunk

    def _parse_wav(self, audio_bytes: bytes) -> tuple[bytes, int]:
        """
        Parse WAV audio bytes and extract raw PCM data.

        Returns (pcm_data, sample_rate).
        Raises ValueError if not a valid WAV file.
        """
        if len(audio_bytes) < 44:
            raise ValueError(f"Audio data too short for WAV header: {len(audio_bytes)} bytes")

        riff = audio_bytes[:4]
        wave = audio_bytes[8:12]
        if riff != b'RIFF' or wave != b'WAVE':
            raise ValueError(f"Not a valid WAV file: riff={riff!r}, wave={wave!r}")

        offset = 12
        sample_rate = self.config.sample_rate
        num_channels = 1
        bits_per_sample = 16

        while offset < len(audio_bytes) - 8:
            chunk_id = audio_bytes[offset:offset + 4]
            chunk_size = int.from_bytes(audio_bytes[offset + 4:offset + 8], 'little')

            if chunk_id == b'fmt ':
                fmt_data = audio_bytes[offset + 8:offset + 8 + chunk_size]
                if len(fmt_data) >= 16:
                    audio_format = int.from_bytes(fmt_data[0:2], 'little')
                    num_channels = int.from_bytes(fmt_data[2:4], 'little')
                    sample_rate = int.from_bytes(fmt_data[4:8], 'little')
                    block_align = int.from_bytes(fmt_data[12:14], 'little')
                    bits_per_sample = int.from_bytes(fmt_data[14:16], 'little')
                    if audio_format != 1:
                        raise ValueError(
                            f"WAV audio format {audio_format} not supported "
                            f"(only PCM format 1 is supported)"
                        )
                offset += 8 + chunk_size
            elif chunk_id == b'data':
                pcm_data = audio_bytes[offset + 8:offset + 8 + chunk_size]
                _logger.debug(
                    "WAV parsed",
                    extra={
                        "sample_rate": sample_rate,
                        "channels": num_channels,
                        "bits_per_sample": bits_per_sample,
                        "pcm_bytes": len(pcm_data),
                    }
                )
                return pcm_data, sample_rate
            else:
                offset += 8 + chunk_size

        raise ValueError("WAV file has no data chunk")
