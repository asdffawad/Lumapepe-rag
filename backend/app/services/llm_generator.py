"""
LLM 生成器
- 流式输出: AsyncGenerator，支持打字机效果
- 引用标注: 生成内容中标注引用来源
- 流式引用提取: 流式输出过程中实时提取引用
- 安全过滤: 敏感词检测（可选）
- 结构化日志 + Metrics
"""
from __future__ import annotations

import asyncio
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncGenerator, Literal

from app.services.llm_service import ChatMessage, ChatResponse, LLMBase
from app.services.prompt_builder import Prompt
from app.logging_config import get_logger
from app import metrics as _metrics

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
_gen_logger = get_logger("llm.generator")

# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class GenerationResult:
    content: str
    citations: list[str]
    tokens_used: int
    latency_ms: int
    model: str
    finish_reason: str


@dataclass
class StreamChunk:
    """流式输出块"""
    content: str  # 增量内容
    citations: list[str]  # 到目前为止提取的引用
    is_final: bool  # 是否为最后一块


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------
class LLMGenerator(ABC):
    @abstractmethod
    async def generate(
        self,
        prompt: Prompt,
        session_id: str,
        request_id: str | None = None,
    ) -> GenerationResult:
        """
        输入: Prompt 对象 + session_id + request_id（用于链路追踪）
        输出: GenerationResult { content, citations, tokens_used, latency_ms, model, finish_reason }
        """
        pass

    @abstractmethod
    async def stream(
        self,
        prompt: Prompt,
        session_id: str,
        request_id: str | None = None,
        sources: list[str] | None = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        流式输出，返回 AsyncGenerator[StreamChunk]
        每个 chunk 包含增量内容和真实检索来源（由 orchestrator 注入）
        """
        pass


# ---------------------------------------------------------------------------
# 标准 LLM 生成器（企业级）
# ---------------------------------------------------------------------------
class StandardLLMGenerator(LLMGenerator):
    """
    基于 LLMBase 的标准实现（企业级）

    改进：
    - 流式输出中实时提取引用
    - session_id 和 request_id 透传到日志
    - token 使用埋点
    - 结构化日志
    """

    # 引用标注正则（支持多种格式）
    _CITATION_PATTERNS = [
        re.compile(r"【([^】]+)】"),       # 【商品详情】
        re.compile(r"\[([^\]]+)\]"),        # [来源]
        re.compile(r"来源[:：]\s*([^\s，,。]+)"),  # 来源: xxx
    ]

    def __init__(self, llm: LLMBase):
        self._llm = llm

    async def generate(
        self,
        prompt: Prompt,
        session_id: str,
        request_id: str | None = None,
    ) -> GenerationResult:
        start = time.monotonic()

        messages = [
            ChatMessage(role="system", content=prompt.system),
            ChatMessage(role="user", content=prompt.user),
        ]

        response = await self._llm.chat(
            messages,
            request_id=request_id,
        )

        citations = self._extract_citations(response.content)
        latency_ms = int((time.monotonic() - start) * 1000)

        # Metrics 埋点
        try:
            _metrics.llm_tokens_used.labels(model=response.model, type="output").inc(response.tokens_used)
            _metrics.rag_generate_duration_seconds.observe(latency_ms / 1000.0)
        except Exception:
            pass

        _gen_logger.info(
            "LLM generation completed",
            extra={
                "request_id": request_id or "",
                "session_id": session_id,
                "model": response.model,
                "tokens_used": response.tokens_used,
                "citations_count": len(citations),
                "latency_ms": latency_ms,
                "finish_reason": response.finish_reason,
            }
        )

        return GenerationResult(
            content=response.content,
            citations=citations,
            tokens_used=response.tokens_used,
            latency_ms=latency_ms,
            model=response.model,
            finish_reason=response.finish_reason,
        )

    async def stream(
        self,
        prompt: Prompt,
        session_id: str,
        request_id: str | None = None,
        sources: list[str] | None = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        messages = [
            ChatMessage(role="system", content=prompt.system),
            ChatMessage(role="user", content=prompt.user),
        ]

        accumulated = ""
        start = time.monotonic()
        # 引用来源由 orchestrator 注入真实检索文档来源，不再从 LLM 回复无依据地提取
        verified_sources = sources or []

        try:
            async for chunk_text in self._llm.chat_stream(
                messages,
                request_id=request_id,
            ):
                accumulated += chunk_text
                yield StreamChunk(
                    content=chunk_text,
                    citations=verified_sources,
                    is_final=False,
                )

            # 流式结束，发送最终 chunk
            yield StreamChunk(
                content="",
                citations=verified_sources,
                is_final=True,
            )

            latency_ms = int((time.monotonic() - start) * 1000)

            _gen_logger.info(
                "LLM stream completed",
                extra={
                    "request_id": request_id or "",
                    "session_id": session_id,
                    "model": self._llm.get_model_name(),
                    "total_chars": len(accumulated),
                    "citations_count": len(verified_sources),
                    "latency_ms": latency_ms,
                }
            )

        except Exception as e:
            _gen_logger.error(
                "LLM stream error",
                extra={
                    "request_id": request_id or "",
                    "session_id": session_id,
                    "error": str(e)[:200],
                }
            )
            raise

    def _extract_citations(self, text: str) -> list[str]:
        """提取内容中的引用来源"""
        sources: set[str] = set()
        for pattern in self._CITATION_PATTERNS:
            for match in pattern.finditer(text):
                source = match.group(1).strip()
                if source:
                    sources.add(source)
        return list(sources)


# ---------------------------------------------------------------------------
# Mock 生成器
# ---------------------------------------------------------------------------
class MockLLMGenerator(LLMGenerator):
    """测试用 Mock 实现"""

    async def generate(
        self,
        prompt: Prompt,
        session_id: str,
        request_id: str | None = None,
    ) -> GenerationResult:
        await asyncio.sleep(0.05)
        content = f"[Mock回答] 已收到您的问题：{prompt.user[:50]}..."
        return GenerationResult(
            content=content,
            citations=["mock_source"],
            tokens_used=100,
            latency_ms=50,
            model="mock-gpt-3.5",
            finish_reason="stop",
        )

    async def stream(
        self,
        prompt: Prompt,
        session_id: str,
        request_id: str | None = None,
        sources: list[str] | None = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        text = f"[Mock流式回答] 已收到您的问题：{prompt.user[:30]}..."
        verified_sources = sources or ["mock_source"]
        for ch in text:
            yield StreamChunk(content=ch, citations=verified_sources, is_final=False)
            await asyncio.sleep(0.01)
        yield StreamChunk(content="", citations=verified_sources, is_final=True)


# ---------------------------------------------------------------------------
# 流式引用提取工具
# ---------------------------------------------------------------------------
def extract_citations_from_chunk(chunk_text: str) -> list[str]:
    """
    工具函数：从流式 chunk 中提取引用
    可被外部调用（如 chat.py 中的 SSE 事件处理）
    """
    patterns = [
        re.compile(r"【([^】]+)】"),
        re.compile(r"\[([^\]]+)\]"),
        re.compile(r"来源[:：]\s*([^\s，,。]+)"),
    ]
    sources: set[str] = set()
    for pattern in patterns:
        for match in pattern.finditer(chunk_text):
            source = match.group(1).strip()
            if source:
                sources.add(source)
    return list(sources)