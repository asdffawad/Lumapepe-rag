"""
Tests for STT workflow: sampleRate passthrough and streaming architecture.

Covers:
- handle_voice_record reads sampleRate from request and passes to STTConfig
- voice_ws uses asyncio.Queue for true streaming
- Protocol docstring reflects streaming capability
"""
import pytest
import inspect


class TestHandleVoiceRecordSampleRate:
    """Verify handle_voice_record passes sampleRate from request to STTConfig."""

    def test_handle_voice_record_reads_samplerate(self):
        """Verify handle_voice_record reads sampleRate from request data via parameter."""
        from app.api import websocket as ws_module

        source = inspect.getsource(ws_module.handle_voice_record)
        assert "sample_rate" in source, \
            "handle_voice_record should reference sample_rate"
        assert 'sample_rate if sample_rate else 16000' in source or \
               'sample_rate=sample_rate' in source, \
            "handle_voice_record should use sample_rate with a default of 16000"

    def test_voice_record_handler_accepts_sample_rate_param(self):
        """Verify handle_voice_record signature includes sample_rate parameter."""
        from app.api import websocket as ws_module

        sig = inspect.signature(ws_module.handle_voice_record)
        params = list(sig.parameters.keys())
        assert "sample_rate" in params, \
            "handle_voice_record should accept sample_rate parameter"

    def test_ws_chat_passes_sample_rate_to_handler(self):
        """Verify the WebSocket handler passes sampleRate from the incoming message."""
        from app.api import websocket as ws_module

        source = inspect.getsource(ws_module.websocket_chat)
        # The handler should extract sampleRate from data and pass it to handle_voice_record
        assert 'sampleRate' in source, \
            "ws_endpoint should extract sampleRate from voice_record message"
        assert 'sample_rate=' in source, \
            "ws_endpoint should pass sample_rate to handle_voice_record"


class TestVoiceWsStreaming:
    """Verify voice_ws.py implements true streaming with asyncio.Queue."""

    def test_queue_used_for_audio_buffering(self):
        """Verify asyncio.Queue is used for audio buffering in voice_ws."""
        from app.api import voice_ws as vws_module

        source = inspect.getsource(vws_module.websocket_voice)
        assert "asyncio.Queue" in source, \
            "voice_ws should use asyncio.Queue for audio buffering"

    def test_transcribe_stream_is_called(self):
        """Verify voice_ws uses transcribe_stream for real-time transcription."""
        from app.api import voice_ws as vws_module

        source = inspect.getsource(vws_module.websocket_voice)
        assert "transcribe_stream" in source, \
            "voice_ws should use transcribe_stream for real-time transcription"

    def test_stream_text_response_type(self):
        """Verify voice_ws sends stream_text response type."""
        from app.api import voice_ws as vws_module

        source = inspect.getsource(vws_module.websocket_voice)
        assert '"type": "stream_text"' in source or "'type': 'stream_text'" in source, \
            "voice_ws should send stream_text response type"

    def test_asyncio_imported(self):
        """Verify asyncio is imported in voice_ws for Queue support."""
        from app.api import voice_ws as vws_module

        module_source = inspect.getsource(vws_module)
        assert "import asyncio" in module_source, \
            "voice_ws should import asyncio for Queue support"

    def test_protocol_doc_updated(self):
        """Verify voice_ws docstring reflects streaming protocol."""
        from app.api import voice_ws as vws_module

        doc = inspect.getdoc(vws_module.websocket_voice)
        assert doc is not None, "websocket_voice should have a docstring"
        assert "stream_text" in doc, \
            "voice_ws docstring should mention stream_text"
        assert "streaming" in doc.lower() or "real-time" in doc.lower(), \
            "voice_ws docstring should mention streaming/real-time capability"
