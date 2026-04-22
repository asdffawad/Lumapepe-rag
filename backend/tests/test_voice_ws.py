import pytest


def test_voice_ws_route_registered():
    from app.main import app
    routes = [r.path for r in app.routes]
    assert "/ws/voice" in routes


def test_stt_config_from_env(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key-123")
    from importlib import reload
    import app.config
    reload(app.config)
    cfg = app.config.get_config()
    assert cfg.stt.api_key == "test-key-123"
    assert cfg.stt.model == "paraformer-realtime-v2"
