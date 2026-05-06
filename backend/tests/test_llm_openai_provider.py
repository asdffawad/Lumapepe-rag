from app.config import get_config
from app.services.embedding_service import DashScopeEmbedding, EmbeddingService
from app.services.llm_service import ChatMessage, LLMFactory


def test_openai_provider_is_registered_and_uses_openai_config():
    cfg = get_config()
    cfg.llm.provider = "openai"
    cfg.llm.model_name = "gpt-4.1-mini"
    cfg.llm.api_key = "test-openai-key"
    cfg.llm.base_url = "https://api.openai.com/v1"

    provider = LLMFactory.create("openai", cfg.llm)

    assert provider.get_model_name() == "gpt-4.1-mini"
    assert provider.get_max_context_window() == 1_047_576
    assert provider.provider_name == "openai"
    assert "openai" in LLMFactory.list_providers()


def test_openai_provider_uses_max_completion_tokens_payload_key():
    cfg = get_config()
    cfg.llm.provider = "openai"
    cfg.llm.model_name = "gpt-4.1-mini"
    cfg.llm.api_key = "test-openai-key"
    cfg.llm.base_url = "https://api.openai.com/v1"
    provider = LLMFactory.create("openai", cfg.llm)

    payload = provider._build_payload(
        [ChatMessage(role="user", content="hello")],
        temperature=0.2,
        max_tokens=128,
    )

    assert payload["max_completion_tokens"] == 128
    assert "max_tokens" not in payload


def test_get_config_reads_openai_environment(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("OPENAI_FALLBACK_MODELS", "gpt-4.1,gpt-4o-mini")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL_NAME", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_FALLBACK_MODELS", raising=False)
    get_config.cache_clear()

    try:
        cfg = get_config()
        assert cfg.llm.provider == "openai"
        assert cfg.llm.api_key == "test-openai-key"
        assert cfg.llm.base_url == "https://api.openai.com/v1"
        assert cfg.llm.model_name == "gpt-4.1-mini"
        assert cfg.llm.fallback_models == ["gpt-4.1", "gpt-4o-mini"]
    finally:
        get_config.cache_clear()


def test_openai_llm_keeps_embedding_config_separate(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-dashscope-key")
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    get_config.cache_clear()

    try:
        cfg = get_config()
        assert cfg.llm.api_key == "test-openai-key"
        assert cfg.llm.base_url == "https://api.openai.com/v1"
        assert cfg.embedding.provider == "dashscope"
        assert cfg.embedding.api_key == "test-dashscope-key"
        assert cfg.embedding.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    finally:
        get_config.cache_clear()


def test_embedding_service_uses_embedding_config_for_openai_llm(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-dashscope-key")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://dashscope.example/v1")
    get_config.cache_clear()

    try:
        cfg = get_config()
        EmbeddingService.init(cfg, redis_client=None)
        provider = EmbeddingService.get_instance()

        assert isinstance(provider, DashScopeEmbedding)
        assert provider.api_key == "test-dashscope-key"
        assert provider.base_url == "https://dashscope.example/v1"
    finally:
        EmbeddingService._instance = None
        get_config.cache_clear()
