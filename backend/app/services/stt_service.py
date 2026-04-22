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
import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

import websockets


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
        Internal streaming via WebSocket protocol.

        Protocol flow:
        1. Connect to wss://dashscope.aliyuncs.com/api-ws/v1/inference
        2. Send run-task JSON command
        3. Wait for task-started event
        4. Stream binary audio chunks
        5. Send finish-task command
        6. Yield result-generated events until task-finished
        """
        task_id = uuid.uuid4().hex[:32]
        ws_url = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
        headers = {"Authorization": f"Bearer {self.config.api_key}"}

        try:
            async with websockets.connect(ws_url, extra_headers=headers) as ws:
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
                            "format": self.config.format,
                            "sample_rate": self.config.sample_rate,
                            "disfluency_removal_enabled": False,
                        },
                        "input": {}
                    }
                }

                await ws.send(json.dumps(run_task_msg))

                task_started = False
                while True:
                    response = await ws.recv()
                    event = json.loads(response)
                    event_type = event.get("header", {}).get("event")

                    if event_type == "task-started":
                        task_started = True
                        break
                    elif event_type == "task-failed":
                        error_msg = event.get("header", {}).get("error_message", "Unknown error")
                        raise STTError(f"Task failed: {error_msg}")

                if not task_started:
                    raise STTError("Did not receive task-started event")

                async for chunk in self._iter_audio(audio_stream):
                    await ws.send(chunk)
                    await asyncio.sleep(0.1)

                finish_task_msg = {
                    "header": {
                        "action": "finish-task",
                        "task_id": task_id,
                        "streaming": "duplex"
                    },
                    "payload": {
                        "input": {}
                    }
                }
                await ws.send(json.dumps(finish_task_msg))

                full_text = []
                while True:
                    try:
                        response = await asyncio.wait_for(ws.recv(), timeout=60.0)
                        event = json.loads(response)
                        event_type = event.get("header", {}).get("event")

                        if event_type == "result-generated":
                            sentence = event.get("payload", {}).get("output", {}).get("sentence", {})
                            text = sentence.get("text", "")
                            if text:
                                full_text.append(text)
                                yield text

                        elif event_type == "task-finished":
                            break

                        elif event_type == "task-failed":
                            error_msg = event.get("header", {}).get("error_message", "Unknown error")
                            raise STTError(f"Task failed: {error_msg}")

                    except asyncio.TimeoutError:
                        raise STTError("Timeout waiting for transcription results")

        except websockets.exceptions.WebSocketException as e:
            raise STTError(f"WebSocket error: {e}")

    async def _iter_audio(self, audio_stream):
        """Convert audio source to async iterable of chunks."""
        if hasattr(audio_stream, '__aiter__'):
            async for chunk in audio_stream:
                yield chunk
        else:
            for chunk in audio_stream:
                yield chunk
