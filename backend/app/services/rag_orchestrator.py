"""
RAG Orchestrator - RAG 管道唯一入口
严格遵循 rag-orchestrator-architecture.mdc 规范

调度流程: Query改写 -> 向量检索 -> 重排 -> Prompt构建 -> LLM生成
各子模块职责单一、可独立测试和替换
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncGenerator

from app.config import RAGConfig
from app.services.query_rewriter import QueryRewriter, RewrittenQuery
from app.services.vector_retriever import VectorRetriever, RetrievedDoc
from app.services.reranker import Reranker, RerankedDoc
from app.services.prompt_builder import PromptBuilder, Prompt
from app.services.llm_generator import LLMGenerator, GenerationResult, StreamChunk

if TYPE_CHECKING:
    from app.services.tools.base import ToolResult


@dataclass
class RAGResult:
    content: str
    intent: str
    sources: list[str]
    citations: list[str]
    tokens_used: int
    latency_ms: int
    rewritten_query: str
    tool_result: "ToolResult | None" = None


class RAGError(Exception):
    pass


class RAGOrchestrator:
    """
    RAG 管道唯一入口，所有子模块通过构造器注入
    """

    def __init__(
        self,
        rewriter: QueryRewriter,
        retriever: VectorRetriever,
        reranker: Reranker,
        prompt_builder: PromptBuilder,
        generator: LLMGenerator,
        config: RAGConfig,
    ):
        self._rewriter = rewriter
        self._retriever = retriever
        self._reranker = reranker
        self._prompt_builder = prompt_builder
        self._generator = generator
        self._config = config

    async def run(
        self,
        query: str,
        history: list[dict],
        intent: str,
        request_id: str | None = None,
        user_id: str | None = None,
    ) -> RAGResult:
        """
        同步模式：完整执行 RAG 管道，返回 GenerationResult
        """
        start_time = time.monotonic()
        total_latency = {"value": 0}

        try:
            # Step 1: Query 改写
            rewritten = await self._timed_rewrite(query, history, total_latency)

            # Step 2: 向量检索
            candidates = await self._timed_retrieve(rewritten.rewritten, total_latency)

            # Step 3: 重排
            reranked = await self._timed_rerank(
                rewritten.rewritten, candidates, total_latency, intent=intent
            )

            # Step 3.5: 工具分流（order_query / refund_return / logistics）
            tool_result: "ToolResult | None" = None
            if intent in ("order_query", "refund_return", "logistics_query") and user_id:
                tool_result = await self._call_tool(
                    intent,
                    {"user_id": user_id, "_query": query},
                    request_id,
                )

            # Step 4: Prompt 构建
            prompt = self._prompt_builder.build(
                query=rewritten.rewritten,
                context=reranked,
                intent=intent,
                history=history,
                tool_result=tool_result,
            )

            # Step 5: LLM 生成
            gen_result = await self._timed_generate(
                prompt, request_id, total_latency
            )

            total_latency["value"] = int((time.monotonic() - start_time) * 1000)

            return RAGResult(
                content=gen_result.content,
                intent=intent,
                sources=[d.source for d in reranked],
                # 引用改为从检索文档来源提取（而非从 LLM 回复中无依据地提取）
                citations=[d.source for d in reranked[:5]],
                tokens_used=gen_result.tokens_used,
                latency_ms=total_latency["value"],
                rewritten_query=rewritten.rewritten,
                tool_result=tool_result,
            )

        except asyncio.TimeoutError:
            raise RAGError("RAG pipeline timeout")
        except Exception as e:
            raise RAGError(f"RAG pipeline error: {e}")

    async def run_stream(
        self,
        query: str,
        history: list[dict],
        intent: str,
        request_id: str | None = None,
        user_id: str | None = None,
    ) -> AsyncGenerator["StreamChunk | str", None]:
        """
        流式模式：返回 AsyncGenerator，逐字产出 StreamChunk
        兼容字符串输出（用于错误消息）
        """
        try:
            # 前4步同同步模式
            rewritten = await asyncio.wait_for(
                self._rewriter.rewrite(query, history),
                timeout=self._config.rewrite_timeout_ms / 1000,
            )

            candidates = await asyncio.wait_for(
                self._retriever.retrieve(
                    rewritten.rewritten,
                    top_k=self._config.top_k,
                    filters=None,
                ),
                timeout=self._config.retrieve_timeout_ms / 1000,
            )

            reranked = await asyncio.wait_for(
                self._reranker.rerank(
                    rewritten.rewritten,
                    candidates,
                    self._config.rerank_top_k,
                    intent=intent,
                ),
                timeout=self._config.rerank_timeout_ms / 1000,
            )

            # 工具分流（仅在有 user_id 时调用）
            tool_result = None
            if intent in ("order_query", "refund_return", "logistics_query") and user_id:
                tool_result = await self._call_tool(
                    intent,
                    {"user_id": user_id, "_query": rewritten.rewritten},
                    request_id,
                )

            prompt = self._prompt_builder.build(
                query=rewritten.rewritten,
                context=reranked,
                intent=intent,
                history=history,
                tool_result=tool_result,
            )

            # 流式生成（传入真实检索来源，而非从 LLM 回复无依据提取）
            async for chunk in self._generator.stream(
                prompt, "", request_id,
                sources=[d.source for d in reranked[:5]],
            ):
                yield chunk

        except asyncio.TimeoutError:
            yield "[错误] RAG pipeline 超时，请稍后再试"
        except Exception as e:
            yield f"[错误] {str(e)}"

    async def _timed_rewrite(
        self, query: str, history: list[dict], latency: dict
    ) -> RewrittenQuery:
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self._rewriter.rewrite(query, history),
                timeout=self._config.rewrite_timeout_ms / 1000,
            )
            latency["value"] += int((time.monotonic() - start) * 1000)
            return result
        except asyncio.TimeoutError:
            return RewrittenQuery(
                rewritten=query,
                expanded_terms=[],
                intent="product_qa",
                method="timeout_fallback",
                latency_ms=0,
            )

    async def _timed_retrieve(
        self, query: str, latency: dict
    ) -> list[RetrievedDoc]:
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self._retriever.retrieve(query, top_k=self._config.top_k, filters=None),
                timeout=self._config.retrieve_timeout_ms / 1000,
            )
            latency["value"] += int((time.monotonic() - start) * 1000)
            return result
        except asyncio.TimeoutError:
            return []

    async def _timed_rerank(
        self, query: str, candidates: list[RetrievedDoc], latency: dict, intent: str = "product_qa"
    ) -> list[RerankedDoc]:
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self._reranker.rerank(query, candidates, self._config.rerank_top_k, intent=intent),
                timeout=self._config.rerank_timeout_ms / 1000,
            )
            latency["value"] += int((time.monotonic() - start) * 1000)
            return result
        except asyncio.TimeoutError:
            # 跳过重排，直接返回候选
            return [
                RerankedDoc(
                    content=d.content,
                    metadata=d.metadata,
                    rerank_score=d.score,
                    source=d.source,
                )
                for d in candidates[: self._config.rerank_top_k]
            ]

    async def _timed_generate(
        self, prompt: Prompt, request_id: str | None, latency: dict
    ) -> GenerationResult:
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self._generator.generate(prompt, "", request_id),
                timeout=self._config.llm_timeout_ms / 1000,
            )
            latency["value"] += int((time.monotonic() - start) * 1000)
            return result
        except asyncio.TimeoutError:
            # LLM 超时降级：返回基于 Prompt 内容的 fallback 回答
            latency["value"] += self._config.llm_timeout_ms
            _gen_logger.warning("LLM generate timeout, using fallback response")
            from app.services.llm_generator import GenerationResult
            return GenerationResult(
                content="抱歉，AI 回答生成超时。请稍后重试，或尝试简化您的问题。",
                citations=[],
                tokens_used=0,
                model="fallback",
                latency_ms=self._config.llm_timeout_ms,
                finish_reason="timeout",
            )
        except Exception as e:
            # LLM 调用失败降级：返回友好提示而非抛出异常
            latency["value"] += int((time.monotonic() - start) * 1000)
            _gen_logger.warning(f"LLM generate failed, using fallback: {e}")
            from app.services.llm_generator import GenerationResult
            return GenerationResult(
                content="抱歉，AI 服务暂时不可用，请稍后重试。",
                citations=[],
                tokens_used=0,
                model="fallback",
                latency_ms=latency["value"],
                finish_reason="error",
            )

    async def _call_tool(
        self, intent: str, params: dict, request_id: str | None
    ) -> "ToolResult | None":
        """根据意图调用对应的工具，带超时和熔断器保护"""
        from app.services.tool_router import ToolRouter
        from app.services.tools.base import ToolResult

        tool_name_map = {
            "order_query": "order_query",
            "refund_return": "refund_query",
            "logistics_query": "logistics_query",
        }
        tool_name = tool_name_map.get(intent)
        if not tool_name:
            return None

        # 从 query 中提取 order_id（如用户提及了具体订单号）
        query = params.get("_query", "")
        extracted_order_id = None
        if query:
            import re

            # 优先匹配：以订单号格式开头（独立行或开头无其他词）
            # 例如：ORD20260001、SF1234567890
            m = re.search(r"\b([A-Z]{2,4}[0-9]{6,})\b", query, re.IGNORECASE)
            if not m:
                # 兜底：从"订单"或"单号"关键词附近贪婪匹配
                # 处理 "订单ORD20260001" 或 "订单ORD20260001发货了吗" 等各种格式
                m = re.search(
                    r"订单.*?([A-Z]{2,4}[0-9]{6,})",
                    query, re.IGNORECASE | re.DOTALL
                )
            if not m:
                # 纯订单号模式：独立数字字母组合（8-32位）
                m = re.search(r"\b([A-Z0-9]{8,32})\b", query)
            if m:
                extracted_order_id = m.group(1).upper()

        # 构建工具参数（优先用提取的 order_id）
        tool_params = {
            "user_id": params.get("user_id", ""),
        }
        if extracted_order_id:
            tool_params["order_id"] = extracted_order_id

        try:
            router = ToolRouter()
            return await asyncio.wait_for(
                router.execute(tool_name, tool_params, request_id or ""),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            return ToolResult(
                success=False,
                error="工具调用超时，请稍后重试",
                source="工具系统",
            )
        except Exception:
            return None
