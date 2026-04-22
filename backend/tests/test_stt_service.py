import pytest
from app.services.stt_service import SpeechToTextProvider, STTFactory, STTError, STTConfig


def test_factory_raises_on_unknown_provider():
    """Test that creating an unknown provider raises ValueError."""
    config = STTConfig(api_key="test-key")
    with pytest.raises(ValueError, match="Unknown STT provider"):
        STTFactory.create("unknown_provider", config)


def test_factory_raises_on_missing_api_key():
    """Test that creating a provider without api_key raises ValueError."""
    config = STTConfig(api_key="")
    with pytest.raises(ValueError, match="api_key"):
        STTFactory.create("dashscope", config)


def test_dashscope_provider_has_correct_model():
    """Test that DashScope provider has the correct default model."""
    from app.services.stt_service import DashScopeProvider
    config = STTConfig(api_key="test-key")
    provider = DashScopeProvider(config)
    assert provider.model_name == "paraformer-realtime-v2"


def test_dashscope_provider_has_required_methods():
    """Test that DashScope provider has the required transcribe methods."""
    from app.services.stt_service import DashScopeProvider
    from app.services.stt_service import SpeechToTextProvider
    config = STTConfig(api_key="test-key")
    provider = DashScopeProvider(config)
    
    # Check it's a SpeechToTextProvider
    assert isinstance(provider, SpeechToTextProvider)
    
    # Check required methods exist
    assert hasattr(provider, 'transcribe')
    assert hasattr(provider, 'transcribe_stream')
    assert hasattr(provider, 'count_tokens')
    assert hasattr(provider, 'get_model_name')


def test_stt_config_defaults():
    """Test STTConfig default values."""
    config = STTConfig()
    assert config.api_key == ""
    assert config.base_url == "https://dashscope.aliyuncs.com"
    assert config.model == "paraformer-realtime-v2"
    assert config.sample_rate == 16000
    assert config.format == "pcm"
    assert config.timeout == 30


def test_stt_config_custom_values():
    """Test STTConfig with custom values."""
    config = STTConfig(
        api_key="custom-key",
        base_url="https://custom.example.com",
        model="custom-model",
        sample_rate=8000,
        format="wav",
        timeout=60
    )
    assert config.api_key == "custom-key"
    assert config.base_url == "https://custom.example.com"
    assert config.model == "custom-model"
    assert config.sample_rate == 8000
    assert config.format == "wav"
    assert config.timeout == 60


def test_stt_error_is_exception():
    """Test that STTError inherits from Exception."""
    error = STTError("Test error message")
    assert isinstance(error, Exception)
    assert str(error) == "Test error message"


def test_provider_count_tokens():
    """Test that providers can count tokens."""
    from app.services.stt_service import DashScopeProvider
    config = STTConfig(api_key="test-key")
    provider = DashScopeProvider(config)
    
    # Token estimation: len(text) // 4
    tokens = provider.count_tokens("你好世界")
    assert tokens == 1  # 4 characters // 4 = 1


def test_provider_get_model_name():
    """Test that providers return their model name from config."""
    from app.services.stt_service import DashScopeProvider
    config = STTConfig(api_key="test-key", model="paraformer-v2")
    provider = DashScopeProvider(config)
    
    # get_model_name() should return the model from config
    assert provider.get_model_name() == "paraformer-v2"


def test_provider_get_model_name_uses_default():
    """Test that get_model_name() returns default model when not specified."""
    from app.services.stt_service import DashScopeProvider
    config = STTConfig(api_key="test-key")  # Uses default model
    provider = DashScopeProvider(config)
    
    # Default model is paraformer-realtime-v2
    assert provider.get_model_name() == "paraformer-realtime-v2"
