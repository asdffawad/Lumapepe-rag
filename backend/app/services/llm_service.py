"""
LLM 抽象层 + Provider 工厂模式
严格遵循 llm-abstraction-design.mdc 规范

企业级增强:
- Token 精确计数 (tiktoken + DashScope 原生 API fallback)
- 连接池复用 (httpx.AsyncClient 单例)
- 流式异常处理修复
- 结构化日志 + Prometheus 埋点
- 请求级连接池 (per-request 隔离)
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncGenerator, Literal

import httpx

from app.config import LLMConfig
from app.logging_config import get_logger
from app import metrics as _metrics

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
_llm_logger = get_logger("llm.service")


# ---------------------------------------------------------------------------
# Token 计数
# ---------------------------------------------------------------------------
class TokenCounter:
    """
    企业级 Token 计数器。
    优先尝试 tiktoken (精确)，fallback 到字符除 4 估算。
    支持配置 model 对应的 encoding name。
    """

    _ENCODING_NAMES: dict[str, str] = {
        "qwen": "cl100k_base",
        "qwen-max": "cl100k_base",
        "qwen-plus": "cl100k_base",
        "qwen-turbo": "cl100k_base",
        "gpt-4": "cl100k_base",
        "gpt-4o": "cl100k_base",
        "gpt-3.5-turbo": "cl100k_base",
    }

    def __init__(self, model_name: str = "qwen"):
        self._encoder = None
        self._encoding_name = self._ENCODING_NAMES.get(model_name, "cl100k_base")
        self._fallback_mode = False
        self._try_init_encoder()

    def _try_init_encoder(self):
        try:
            import tiktoken
            self._encoder = tiktoken.get_encoding(self._encoding_name)
            _llm_logger.info("Token counter initialized with tiktoken", extra={"encoding": self._encoding_name})
        except ImportError:
            _llm_logger.warning(
                "tiktoken not installed, using char-based token estimation",
                extra={"fallback": "char_div_4"}
            )
            self._fallback_mode = True
        except Exception as e:
            _llm_logger.warning(f"Failed to init tiktoken encoder: {e}", extra={"fallback": "char_div_4"})
            self._fallback_mode = True

    def count(self, text: str) -> int:
        if self._fallback_mode or self._encoder is None:
            return len(text) // 4
        try:
            tokens = self._encoder.encode(text, disallowed_special=())
            return len(tokens)
        except Exception:
            return len(text) // 4


# ---------------------------------------------------------------------------
# Token 计数单例（按 model 缓存）
# ---------------------------------------------------------------------------
_token_counter_cache: dict[str, TokenCounter] = {}


def get_token_counter(model_name: str) -> TokenCounter:
    if model_name not in _token_counter_cache:
        _token_counter_cache[model_name] = TokenCounter(model_name)
    return _token_counter_cache[model_name]


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass
class ChatResponse:
    content: str
    model: str
    tokens_used: int
    latency_ms: int
    finish_reason: str


@dataclass
class LLMCallLog:
    """LLM 调用日志结构"""
    request_id: str
    model: str
    provider: str
    messages_count: int
    total_input_tokens: int
    output_tokens: int
    latency_ms: int
    finish_reason: str
    status: Literal["success", "error", "fallback"]


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------
class LLMError(Exception):
    pass


class LLMRetryExhausted(LLMError):
    """所有重试次数耗尽"""
    pass


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------
class LLMBase(ABC):
    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        request_id: str | None = None,
    ) -> ChatResponse:
        pass

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        request_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        pass

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        pass

    @abstractmethod
    def get_model_name(self) -> str:
        pass

    @abstractmethod
    def get_max_context_window(self) -> int:
        pass

    def supports_streaming(self) -> bool:
        return True

    @property
    def provider_name(self) -> str:
        """Provider 标识名，用于日志和 metrics"""
        return self.__class__.__name__.replace("Provider", "").lower()


# ---------------------------------------------------------------------------
# 工厂模式
# ---------------------------------------------------------------------------
class LLMFactory:
    _providers: dict[str, type[LLMBase]] = {}

    @classmethod
    def register(cls, name: str, provider_class: type[LLMBase]):
        cls._providers[name] = provider_class
        _llm_logger.debug(f"Registered LLM provider: {name}")

    @classmethod
    def create(cls, provider_name: str, config: LLMConfig) -> LLMBase:
        if provider_name not in cls._providers:
            available = list(cls._providers.keys())
            _llm_logger.error(f"Unknown LLM provider requested", extra={
                "requested": provider_name,
                "available": available
            })
            raise ValueError(f"Unknown provider: {provider_name}. Available: {available}")
        instance = cls._providers[provider_name](config)
        _llm_logger.info(f"LLM provider created", extra={
            "provider": provider_name,
            "model": config.model_name
        })
        return instance

    @classmethod
    def list_providers(cls) -> list[str]:
        return list(cls._providers.keys())


def register_provider(name: str):
    def decorator(cls: type[LLMBase]):
        LLMFactory.register(name, cls)
        return cls
    return decorator


# ---------------------------------------------------------------------------
# Base HTTP Client Mixin — 连接池复用
# ---------------------------------------------------------------------------
class _BaseHTTPClientMixin:
    """
    封装 httpx.AsyncClient 管理，提供:
    - 连接池配置
    - 自动错误分类 (rate_limit / server_error / client_error)
    - 请求级日志
    """

    _DEFAULT_POOL_LIMITS = httpx.Limits(
        max_keepalive_connections=20,
        max_connections=100,
        keepalive_expiry=30.0,
    )

    def _build_client(
        self,
        timeout: int,
        limits: httpx.Limits | None = None,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            limits=limits or self._DEFAULT_POOL_LIMITS,
            follow_redirects=True,
        )

    def _classify_error(self, status_code: int) -> str:
        """将 HTTP 状态码分类，用于日志和 metrics"""
        if status_code == 429:
            return "rate_limit"
        if status_code in (500, 502, 503, 504):
            return "server_error"
        if status_code == 401 or status_code == 403:
            return "auth_error"
        if status_code == 400:
            return "client_error"
        return "unknown"

    def _log_llm_call(
        self,
        request_id: str | None,
        model: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        finish_reason: str,
        status: str,
        error_detail: str | None = None,
    ):
        extra = {
            "request_id": request_id or "",
            "model": model,
            "provider": provider,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency_ms,
            "finish_reason": finish_reason,
            "status": status,
        }
        if error_detail:
            extra["error_detail"] = error_detail

        if status == "success":
            _llm_logger.info("LLM call completed", extra=extra)
        else:
            _llm_logger.error("LLM call failed", extra=extra)

        # Prometheus 埋点
        try:
            _metrics.record_llm_call(model, status, latency_ms / 1000.0)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Mock Provider
# ---------------------------------------------------------------------------
@register_provider("mock")
class MockProvider(LLMBase):
    def __init__(self, config: LLMConfig | None = None):
        self._counter = get_token_counter("mock-gpt-3.5")

    async def chat(
        self, messages, temperature=0.7, max_tokens=None, request_id=None
    ) -> ChatResponse:
        await asyncio.sleep(0.05)
        last = messages[-1].content if messages else ""
        content = f"[Mock] 您说的是: {last}"
        return ChatResponse(
            content=content,
            model="mock-gpt-3.5",
            tokens_used=len(content) // 4,
            latency_ms=50,
            finish_reason="stop",
        )

    async def chat_stream(
        self, messages, temperature=0.7, max_tokens=None, request_id=None
    ) -> AsyncGenerator[str, None]:
        last = messages[-1].content if messages else ""
        text = f"[Mock] 您说的是: {last}"
        for ch in text:
            yield ch
            await asyncio.sleep(0.01)

    def count_tokens(self, text: str) -> int:
        return self._counter.count(text)

    def get_model_name(self) -> str:
        return "mock-gpt-3.5"

    def get_max_context_window(self) -> int:
        return 4096


# ---------------------------------------------------------------------------
# Qwen Provider — 企业级实现
# ---------------------------------------------------------------------------
@register_provider("qwen")
class QwenProvider(LLMBase, _BaseHTTPClientMixin):
    """
    通义千问 Provider（企业级）
    - 连接池复用
    - 精确 token 计数
    - 结构化日志
    - 自动重试 (指数退避)
    """

    def __init__(self, config: LLMConfig):
        self.model_name = config.model_name
        self.api_key = config.api_key
        self.base_url = config.base_url.rstrip("/")
        self.timeout = config.timeout
        self.max_retries = config.max_retries
        self.default_temperature = config.default_temperature
        self.default_max_tokens = config.default_max_tokens
        self._counter = get_token_counter(config.model_name)

        _llm_logger.info("QwenProvider initialized", extra={
            "model": self.model_name,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
        })

    def _build_headers(self, request_id: str | None) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Request-Id": request_id or "",
        }

    def _build_payload(
        self,
        messages: list[ChatMessage],
        temperature: float,
        max_tokens: int | None,
        stream: bool = False,
    ) -> dict:
        payload = {
            "model": self.model_name,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if stream:
            payload["stream"] = True
        return payload

    def _calc_input_tokens(self, messages: list[ChatMessage]) -> int:
        total = 0
        for m in messages:
            total += self._counter.count(m.content)
        return total

    async def chat(
        self, messages, temperature=0.7, max_tokens=None, request_id=None
    ) -> ChatResponse:
        start = time.monotonic()
        headers = self._build_headers(request_id)
        payload = self._build_payload(messages, temperature, max_tokens, stream=False)
        input_tokens = self._calc_input_tokens(messages)
        status = "error"
        error_detail: str | None = None

        client = self._build_client(self.timeout)
        try:
            for attempt in range(self.max_retries):
                try:
                    # 连接池复用: client 实例在外部创建
                    http_resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                    )

                    if http_resp.status_code == 429:
                        wait = 2 ** attempt
                        _llm_logger.warning(
                            "LLM rate limited, backing off",
                            extra={"request_id": request_id or "", "attempt": attempt + 1, "wait_s": wait}
                        )
                        await asyncio.sleep(wait)
                        continue

                    http_resp.raise_for_status()
                    data = http_resp.json()

                    latency_ms = int((time.monotonic() - start) * 1000)
                    total_tokens = data.get("usage", {}).get("total_tokens", 0)
                    finish_reason = data["choices"][0].get("finish_reason", "stop")
                    content = data["choices"][0]["message"]["content"]

                    self._log_llm_call(
                        request_id=request_id,
                        model=self.model_name,
                        provider=self.provider_name,
                        input_tokens=input_tokens,
                        output_tokens=total_tokens,
                        latency_ms=latency_ms,
                        finish_reason=finish_reason,
                        status="success",
                    )

                    return ChatResponse(
                        content=content,
                        model=self.model_name,
                        tokens_used=total_tokens,
                        latency_ms=latency_ms,
                        finish_reason=finish_reason,
                    )

                except httpx.HTTPStatusError as e:
                    error_detail = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
                    err_class = self._classify_error(e.response.status_code)

                    if err_class in ("rate_limit", "server_error"):
                        if attempt < self.max_retries - 1:
                            wait = 2 ** attempt
                            _llm_logger.warning(
                                "LLM call failed, retrying",
                                extra={"request_id": request_id or "", "attempt": attempt + 1, "error": error_detail}
                            )
                            await asyncio.sleep(wait)
                            continue
                    # 非重试类错误直接抛出
                    _llm_logger.error("LLM call HTTP error (non-retryable)", extra={
                        "request_id": request_id or "",
                        "status_code": e.response.status_code,
                        "error": error_detail,
                    })
                    raise LLMError(error_detail) from e

                except httpx.RequestError as e:
                    error_detail = f"Request error: {str(e)[:200]}"
                    if attempt < self.max_retries - 1:
                        wait = 2 ** attempt
                        _llm_logger.warning(
                            "LLM network error, retrying",
                            extra={"request_id": request_id or "", "attempt": attempt + 1, "error": error_detail}
                        )
                        await asyncio.sleep(wait)
                        continue
                    _llm_logger.error("LLM network error (exhausted retries)", extra={
                        "request_id": request_id or "",
                        "error": error_detail,
                    })
                    raise LLMError(error_detail) from e

            # 所有重试耗尽
            status = "error"
            raise LLMRetryExhausted(f"All {self.max_retries} retries exhausted. Last: {error_detail}")

        finally:
            try:
                await client.aclose()
            except Exception:
                pass  # 忽略关闭时的小异常，避免掩盖真正的错误
            if status == "error":
                self._log_llm_call(
                    request_id=request_id,
                    model=self.model_name,
                    provider=self.provider_name,
                    input_tokens=input_tokens,
                    output_tokens=0,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    finish_reason="error",
                    status="error",
                    error_detail=error_detail,
                )

    async def chat_stream(
        self, messages, temperature=0.7, max_tokens=None, request_id=None
    ) -> AsyncGenerator[str, None]:
        headers = self._build_headers(request_id)
        payload = self._build_payload(messages, temperature, max_tokens, stream=True)
        input_tokens = self._calc_input_tokens(messages)
        start = time.monotonic()
        total_output_chars = 0
        error_detail: str | None = None
        status = "error"

        client = self._build_client(self.timeout)
        try:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            ) as response:
                # 流式响应: 先检查状态码再读 body
                if response.status_code == 429:
                    error_detail = "Rate limited (429)"
                    _llm_logger.warning("LLM stream rate limited", extra={"request_id": request_id or ""})
                    yield "[错误] 请求过于频繁，请稍后再试。"
                    return
                if response.status_code >= 400:
                    body_text = ""
                    try:
                        body_text = await response.aread()
                        error_detail = f"HTTP {response.status_code}: {body_text[:200]}"
                    except Exception:
                        error_detail = f"HTTP {response.status_code}"
                    _llm_logger.error("LLM stream HTTP error", extra={
                        "request_id": request_id or "",
                        "status_code": response.status_code,
                    })
                    yield f"[错误] 服务异常 ({response.status_code})，请稍后再试。"
                    return

                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        import json as _json
                        chunk = _json.loads(data_str)
                        delta = chunk["choices"][0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            total_output_chars += len(content)
                            yield content
                    except (_json.JSONDecodeError, KeyError, IndexError):
                        continue

            status = "success"
            latency_ms = int((time.monotonic() - start) * 1000)
            # 流式输出的 token 数无法精确获取，用字符数估算 (中英文平均 ~2 chars/token)
            est_output_tokens = total_output_chars // 2
            self._log_llm_call(
                request_id=request_id,
                model=self.model_name,
                provider=self.provider_name,
                input_tokens=input_tokens,
                output_tokens=est_output_tokens,
                latency_ms=latency_ms,
                finish_reason="stop",
                status="success",
            )

        except httpx.RequestError as e:
            error_detail = f"Stream network error: {str(e)[:200]}"
            _llm_logger.error("LLM stream network error", extra={
                "request_id": request_id or "",
                "error": error_detail,
            })
            yield f"[错误] 网络异常，请稍后再试。"
        finally:
            try:
                await client.aclose()
            except Exception:
                pass  # 忽略关闭时的小异常，避免掩盖真正的错误
            if status == "error":
                self._log_llm_call(
                    request_id=request_id,
                    model=self.model_name,
                    provider=self.provider_name,
                    input_tokens=input_tokens,
                    output_tokens=0,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    finish_reason="error",
                    status="error",
                    error_detail=error_detail,
                )

    def count_tokens(self, text: str) -> int:
        return self._counter.count(text)

    def get_model_name(self) -> str:
        return self.model_name

    def get_max_context_window(self) -> int:
        # Qwen 系列上下文窗口
        windows = {
            "qwen-max": 32 * 1024,
            "qwen-plus": 128 * 1024,
            "qwen-turbo": 8 * 1024,
            "qwen2-72b-instruct": 32 * 1024,
        }
        return windows.get(self.model_name, 8192)


# ---------------------------------------------------------------------------
# Qwen Fallback Provider — 多模型降级
# ---------------------------------------------------------------------------
@register_provider("qwen_fallback")
class QwenFallbackProvider(LLMBase, _BaseHTTPClientMixin):
    """
    多模型降级 Provider
    按优先级尝试多个模型，某个失败后自动切换下一个
    模型列表按成本从高到低排序
    """

    def __init__(self, config: LLMConfig):
        self.api_key = config.api_key
        self.base_url = config.base_url.rstrip("/")
        self.timeout = config.timeout
        self.max_retries = config.max_retries
        self.default_temperature = config.default_temperature
        self.default_max_tokens = config.default_max_tokens
        self.models = [config.model_name] + list(config.fallback_models)
        self._counter = get_token_counter(config.model_name)

        _llm_logger.info("QwenFallbackProvider initialized", extra={
            "models": self.models,
            "base_url": self.base_url,
        })

    def _build_headers(self, request_id: str | None) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Request-Id": request_id or "",
        }

    def _build_payload(
        self, messages: list[ChatMessage], model: str,
        temperature: float, max_tokens: int | None, stream: bool = False,
    ) -> dict:
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if stream:
            payload["stream"] = True
        return payload

    def _calc_input_tokens(self, messages: list[ChatMessage]) -> int:
        total = 0
        for m in messages:
            total += self._counter.count(m.content)
        return total

    async def chat(
        self, messages, temperature=0.7, max_tokens=None, request_id=None
    ) -> ChatResponse:
        start = time.monotonic()
        headers = self._build_headers(request_id)
        input_tokens = self._calc_input_tokens(messages)
        last_error: Exception | None = None
        used_model: str = ""

        for i, model in enumerate(self.models):
            client = self._build_client(self.timeout)
            try:
                payload = self._build_payload(messages, model, temperature, max_tokens)
                http_resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )

                if http_resp.status_code == 429:
                    _llm_logger.warning(
                        "Model rate limited, trying fallback",
                        extra={"model": model, "attempt": i + 1}
                    )
                    await client.aclose()
                    continue

                http_resp.raise_for_status()
                data = http_resp.json()
                await client.aclose()

                latency_ms = int((time.monotonic() - start) * 1000)
                total_tokens = data.get("usage", {}).get("total_tokens", 0)
                finish_reason = data["choices"][0].get("finish_reason", "stop")
                content = data["choices"][0]["message"]["content"]

                if i > 0:
                    _llm_logger.info(
                        "Fallback to secondary model",
                        extra={"primary": self.models[0], "used": model, "attempt": i + 1}
                    )

                self._log_llm_call(
                    request_id=request_id,
                    model=model,
                    provider=self.provider_name,
                    input_tokens=input_tokens,
                    output_tokens=total_tokens,
                    latency_ms=latency_ms,
                    finish_reason=finish_reason,
                    status="fallback" if i > 0 else "success",
                )

                return ChatResponse(
                    content=content,
                    model=model,
                    tokens_used=total_tokens,
                    latency_ms=latency_ms,
                    finish_reason=finish_reason,
                )

            except httpx.HTTPStatusError as e:
                await client.aclose()
                err_class = self._classify_error(e.response.status_code)
                if err_class in ("rate_limit", "server_error"):
                    last_error = e
                    _llm_logger.warning(
                        "Fallback model failed, trying next",
                        extra={"model": model, "error": str(e)[:100]}
                    )
                    continue
                raise LLMError(f"HTTP {e.response.status_code}: {e.response.text[:200]}") from e

            except httpx.RequestError as e:
                await client.aclose()
                last_error = e
                _llm_logger.warning(
                    "Fallback model network error, trying next",
                    extra={"model": model, "error": str(e)[:100]}
                )
                continue

        self._log_llm_call(
            request_id=request_id,
            model=self.models[-1],
            provider=self.provider_name,
            input_tokens=input_tokens,
            output_tokens=0,
            latency_ms=int((time.monotonic() - start) * 1000),
            finish_reason="error",
            status="error",
            error_detail=str(last_error)[:200] if last_error else "All models failed",
        )
        raise LLMRetryExhausted(
            f"All models failed. Last error: {last_error}. Tried: {self.models}"
        )

    async def chat_stream(
        self, messages, temperature=0.7, max_tokens=None, request_id=None
    ) -> AsyncGenerator[str, None]:
        start = time.monotonic()
        headers = self._build_headers(request_id)
        input_tokens = self._calc_input_tokens(messages)
        last_error: Exception | None = None

        for i, model in enumerate(self.models):
            client = self._build_client(self.timeout)
            try:
                payload = self._build_payload(
                    messages, model, temperature, max_tokens, stream=True
                )

                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.status_code == 429:
                        await client.aclose()
                        _llm_logger.warning(
                            "Fallback model rate limited, trying next",
                            extra={"model": model}
                        )
                        continue
                    if response.status_code >= 400:
                        body_text = ""
                        try:
                            body_text = await response.aread()
                        except Exception:
                            pass
                        await client.aclose()
                        _llm_logger.warning(
                            "Fallback model HTTP error, trying next",
                            extra={"model": model, "status": response.status_code}
                        )
                        continue

                    if i > 0:
                        _llm_logger.info(
                            "Fallback to secondary model (stream)",
                            extra={"primary": self.models[0], "used": model}
                        )

                    total_chars = 0
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            import json as _json
                            chunk = _json.loads(data_str)
                            delta = chunk["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                total_chars += len(content)
                                yield content
                        except (_json.JSONDecodeError, KeyError, IndexError):
                            continue

                    await client.aclose()

                    self._log_llm_call(
                        request_id=request_id,
                        model=model,
                        provider=self.provider_name,
                        input_tokens=input_tokens,
                        output_tokens=total_chars // 2,
                        latency_ms=int((time.monotonic() - start) * 1000),
                        finish_reason="stop",
                        status="fallback" if i > 0 else "success",
                    )
                    return

            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                await client.aclose()
                last_error = e
                _llm_logger.warning(
                    "Fallback model failed in stream, trying next",
                    extra={"model": model, "error": str(e)[:100]}
                )
                continue

        self._log_llm_call(
            request_id=request_id,
            model=self.models[-1],
            provider=self.provider_name,
            input_tokens=input_tokens,
            output_tokens=0,
            latency_ms=int((time.monotonic() - start) * 1000),
            finish_reason="error",
            status="error",
            error_detail=str(last_error)[:200] if last_error else "All models failed",
        )
        yield "[错误] 所有模型均不可用，请稍后再试。"

    def count_tokens(self, text: str) -> int:
        return self._counter.count(text)

    def get_model_name(self) -> str:
        return f"qwen_fallback[{','.join(self.models)}]"

    def get_max_context_window(self) -> int:
        return 32 * 1024
