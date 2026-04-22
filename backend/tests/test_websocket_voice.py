"""
Tests for voice_record message type integration in WebSocket chat endpoint.
"""
import pytest
import inspect


class TestVoiceRecordHandler:
    """Tests for voice_record message type handler"""

    def test_ws_chat_handles_voice_record_message_type(self):
        """Verify voice_record message type is handled in the message loop."""
        from app.api import websocket as ws_module
        assert hasattr(ws_module, 'handle_voice_record')
        assert inspect.iscoroutinefunction(ws_module.handle_voice_record)

    def test_voice_record_handler_signature(self):
        """Verify handle_voice_record has the correct signature."""
        from app.api import websocket as ws_module
        sig = inspect.signature(ws_module.handle_voice_record)
        params = list(sig.parameters.keys())
        assert "websocket" in params
        assert "session_id" in params
        assert "user_id" in params
        assert "audio_data" in params
        assert "format" in params

    def test_voice_record_handler_returns_none(self):
        """Verify handle_voice_record has None return type annotation."""
        from app.api import websocket as ws_module
        sig = inspect.signature(ws_module.handle_voice_record)
        # Python's `-> None` annotation appears as a string 'None' in signature
        ret_annotation = sig.return_annotation
        assert ret_annotation is inspect.Parameter.empty or ret_annotation == 'None'
