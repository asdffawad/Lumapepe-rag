---
name: llm-provider-switching
description: LLM Provider 切换和扩展指南。用于切换大模型（Qwen/GPT/Claude/Ollama）、新增 Provider 实现、Prompt 格式迁移、成本控制、Mock 测试。涉及 llm_service.py、LLMBase、Provider 注册、Token 预算计算。
---

# LLM Provider 切换指南

## Provider 矩阵

| Provider | 模型 | 流式 | Function Call | Vision | Context | 备注 |
|----------|------|------|--------------|--------|---------|------|
| Qwen | qwen-max | 支持 | 支持 | 支持 | 32k | 国内合规，数据出境 |
| OpenAI | GPT-4o | 支持 | 支持 | 支持 | 128k | API 稳定性高 |
| Anthropic | Claude-3 | 支持 | 支持 | 支持 | 200k | 推理能力强 |
| Ollama | Llama3/Mistral | 支持 | 不支持 | 不支持 | 8k | 本地部署，零成本 |
| vLLM | Qwen2/Mistral | 支持 | 支持 | 不支持 | 32k | 高性能本地推理 |

## 新增 Provider 步骤

### Step 1: 继承 LLMBase

在 `services/providers/` 下创建新文件，如 `ollama_provider.py`：

```python
from services.providers.base import LLMBase, ChatMessage, ChatResponse
from services.providers.base import register_provider

@register_provider("ollama")
class OllamaProvider(LLMBase):
    def __init__(self, config: LLMConfig):
        self.base_url = config.base_url   # 如 "http://localhost:11434"
        self.model_name = config.model_name  # 如 "llama3"
        self.timeout = config.timeout

    async def chat(self, messages, temperature, max_tokens, request_id) -> ChatResponse:
        import httpx
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model_name,
                    "messages": [{"role": m.role, "content": m.content} for m in messages],
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        **({"num_predict": max_tokens} if max_tokens else {}),
                    }
                }
            )
            data = response.json()
            return ChatResponse(
                content=data["message"]["content"],
                model=self.model_name,
                tokens_used=data.get("eval_count", 0),
                latency_ms=data.get("total_duration", 0) // 1_000_000,
                finish_reason=data["done_reason"] if "done_reason" in data else "stop",
            )

    async def chat_stream(self, messages, temperature, max_tokens, request_id):
        import httpx
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model_name,
                    "messages": [{"role": m.role, "content": m.content} for m in messages],
                    "stream": True,
                }
            ) as resp:
                async for line in resp.aiter_lines():
                    if line:
                        chunk = json.loads(line)
                        if "message" in chunk:
                            yield chunk["message"]["content"]

    def count_tokens(self, text: str) -> int:
        # Ollama 可用 /api/tokenize 接口，或用估算
        return len(text) // 4  # 粗略估算

    def get_model_name(self) -> str:
        return self.model_name

    def get_max_context_window(self) -> int:
        return 8192  # Llama3 默认上下文窗口
```

### Step 2: 注册 Provider

`@register_provider("ollama")` 装饰器已在 `__init__.py` 的 `register_provider()` 中注册。确保 `services/providers/__init__.py` 导入该模块：

```python
# services/providers/__init__.py
from services.providers.qwen_provider import QwenProvider
from services.providers.ollama_provider import OllamaProvider  # 新增
```

### Step 3: 配置切换

在 `config.py` 中添加配置选项：

```python
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "qwen")  # 切换项

@dataclass
class LLMConfig:
    provider: str = LLM_PROVIDER
    model_name: str = os.getenv("LLM_MODEL", "qwen-max")
    # ...
```

### Step 4: 验证

```python
llm = LLMFactory.create("ollama", config)
response = await llm.chat([ChatMessage(role="user", content="你好")])
print(response.content)
```

## Prompt 格式迁移检查清单

不同模型的 system prompt 格式可能需要调整：

| 差异点 | Qwen | GPT-4 | Claude |
|--------|------|-------|--------|
| System prompt | `{role: system, content: xxx}` | 同左 | 同左 |
| 中文理解 | 优化 | 一般 | 优秀 |
| JSON 输出 | 可能有多余空格 | 稳定 | 稳定 |
| 思维链 | 支持 `reasoning` | 支持 `thinking` | 支持扩展思考 |

**迁移检查**：
- [ ] System prompt 是否需要调整以适配新模型的指令遵循能力
- [ ] Few-shot 示例在新模型上是否仍然有效
- [ ] JSON 输出格式是否需要后处理（如 `orjson.loads(response.content.strip())`）
- [ ] Token 预算是否需要重新计算

## 成本控制

### Token 预算计算

```python
def calculate_token_budget(
    query: str,
    history: list[ChatMessage],
    context: list[str],
    model_max_window: int,
    reserved_for_response: int = 2048,
) -> int:
    query_tokens = count_tokens(query)
    history_tokens = sum(count_tokens(m.content) for m in history)
    available_for_context = model_max_window - query_tokens - history_tokens - reserved_for_response
    return max(0, available_for_context)
```

### 分级降级策略

当 LLM 响应过慢或出错时，自动降级：

```python
class LLMFallbackChain:
    def __init__(self, providers: list[tuple[str, LLMConfig]]):
        self.chain = [(name, LLMFactory.create(name, cfg)) for name, cfg in providers]
        # providers = [("qwen-max", cfg), ("qwen-plus", cfg), ("ollama", cfg)]

    async def chat(self, messages) -> ChatResponse:
        errors = []
        for name, provider in self.chain:
            try:
                return await asyncio.wait_for(provider.chat(messages), timeout=10)
            except Exception as e:
                errors.append(f"{name}: {e}")
                continue
        raise LLMError(f"All providers failed: {errors}")
```

## Mock 测试

本地开发和 CI 使用 Mock Provider，不依赖真实 API：

```python
# 切换为 Mock
config.provider = "mock"
llm = LLMFactory.create("mock", config)

# MockProvider 返回固定响应
@register_provider("mock")
class MockProvider(LLMBase):
    async def chat(self, messages, temperature, max_tokens, request_id):
        return ChatResponse(
            content=f"[Mock] 处理了消息: {messages[-1].content[:50]}...",
            model="mock-gpt-3.5",
            tokens_used=50,
            latency_ms=10,
            finish_reason="stop",
        )

    async def chat_stream(self, messages, temperature, max_tokens, request_id):
        content = f"[Mock Stream] {messages[-1].content}"
        for char in content:
            yield char
            await asyncio.sleep(0.01)  # 模拟打字机效果

    def count_tokens(self, text: str) -> int:
        return len(text) // 4

    def get_model_name(self) -> str:
        return "mock-gpt-3.5"

    def get_max_context_window(self) -> int:
        return 4096
```

## 常见问题

**Q: 流式和非流式如何共存？**
A: `LLMBase.chat_stream()` 返回 `AsyncGenerator`，调用方通过 `async for` 消费。Orchestrator 的 `run_stream()` 透传 Generator，不做聚合。

**Q: 模型不支持 Function Calling 时如何处理？**
A: `LLMBase` 中不定义 `function_call()` 接口，Function Calling 由业务层（如 IntentService）通过 prompt engineering 实现，不强依赖模型能力。

**Q: 如何测试 Prompt 在不同模型上的效果差异？**
A: 用 `LLMFallbackChain` 依次调用所有 Provider，对比输出质量和响应速度，记录到测试报告中。
