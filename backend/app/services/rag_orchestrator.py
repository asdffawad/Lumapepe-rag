"""
RAG Orchestrator - RAG 管道唯一入口
严格遵循 rag-orchestrator-architecture.mdc 规范

调度流程: 意图分类 -> 商品检索(仅product_qa) -> Query改写 -> 向量检索 -> 重排 -> Prompt构建 -> LLM生成
各子模块职责单一、可独立测试和替换
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, AsyncGenerator

from app.config import RAGConfig
from app.logging_config import get_logger
from app.services.query_rewriter import QueryRewriter, RewrittenQuery
from app.services.vector_retriever import VectorRetriever, RetrievedDoc
from app.services.reranker import Reranker, RerankedDoc
from app.services.prompt_builder import PromptBuilder, Prompt
from app.services.llm_generator import LLMGenerator, GenerationResult, StreamChunk

if TYPE_CHECKING:
    from app.services.product_service import ProductService
    from app.services.tools.base import ToolResult

_gen_logger = get_logger("rag.orchestrator")


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
    product_info: "dict | None" = None  # 商品检索结果


@dataclass
class ProductContext:
    """商品检索上下文"""
    product_name: str = ""
    product_detail: str = ""
    match_type: str = ""
    confidence: float = 0.0


class RAGError(Exception):
    pass


class RAGOrchestrator:
    """
    RAG 管道唯一入口，所有子模块通过构造器注入

    支持的意图及处理策略：
    - product_qa: 商品检索 + 向量检索 + LLM 生成
    - order_query/refund_return/logistics_query: 工具调用 + LLM 生成
    - 其他意图: 仅向量检索 + LLM 生成
    """

    def __init__(
        self,
        rewriter: QueryRewriter,
        retriever: VectorRetriever,
        reranker: Reranker,
        prompt_builder: PromptBuilder,
        generator: LLMGenerator,
        config: RAGConfig,
        product_service: "ProductService | None" = None,
    ):
        self._rewriter = rewriter
        self._retriever = retriever
        self._reranker = reranker
        self._prompt_builder = prompt_builder
        self._generator = generator
        self._config = config
        self._product_service = product_service

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
        product_context: "ProductContext | None" = None

        try:
            # Step 0: 商品检索（仅 product_qa 意图）
            if intent == "product_qa" and self._product_service:
                product_context = await self._timed_product_search(query, total_latency)

            # Step 1: Query 改写
            rewritten = await self._timed_rewrite(query, history, total_latency)

            # Step 2: 向量检索
            candidates = await self._timed_retrieve(rewritten.rewritten, total_latency)

            # Step 3: 重排
            reranked = await self._timed_rerank(
                rewritten.rewritten, candidates, total_latency, intent=intent
            )

            # Step 3.5: 工具分流（order_query / refund_return / logistics / product_query）
            tool_result: "ToolResult | None" = None
            if intent in ("order_query", "refund_return", "logistics_query", "product_query") and (user_id or intent == "product_query"):
                tool_result = await self._call_tool(
                    intent,
                    {"user_id": user_id if user_id else "", "_query": query},
                    request_id,
                )

            # Step 4: Prompt 构建
            prompt = self._prompt_builder.build(
                query=rewritten.rewritten,
                context=reranked,
                intent=intent,
                history=history,
                tool_result=tool_result,
                product_context=product_context,
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
                citations=[d.source for d in reranked[:5]],
                tokens_used=gen_result.tokens_used,
                latency_ms=total_latency["value"],
                rewritten_query=rewritten.rewritten,
                tool_result=tool_result,
                product_info={
                    "product_name": product_context.product_name if product_context else "",
                    "product_detail": product_context.product_detail if product_context else "",
                    "match_type": product_context.match_type if product_context else "",
                    "confidence": product_context.confidence if product_context else 0.0,
                } if product_context else None,
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
            product_context: "ProductContext | None" = None

            # Step 0: 商品检索（仅 product_qa 意图）
            if intent == "product_qa" and self._product_service:
                product_context = await self._timed_product_search(query, {"value": 0})

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

            # 工具分流（仅在有 user_id 时调用，或 product_query）
            tool_result = None
            if intent in ("order_query", "refund_return", "logistics_query", "product_query") and (user_id or intent == "product_query"):
                tool_result = await self._call_tool(
                    intent,
                    {"user_id": user_id if user_id else "", "_query": rewritten.rewritten},
                    request_id,
                )

            prompt = self._prompt_builder.build(
                query=rewritten.rewritten,
                context=reranked,
                intent=intent,
                history=history,
                tool_result=tool_result,
                product_context=product_context,
            )

            # 流式生成（传入真实检索来源，而非从 LLM 回复无依据提取）
            async with asyncio.timeout(self._config.llm_timeout_ms / 1000):
                async for chunk in self._generator.stream(
                    prompt, "", request_id,
                    sources=[d.source for d in reranked[:5]],
                ):
                    yield chunk

        except asyncio.TimeoutError:
            _gen_logger.error("RAG stream timeout", extra={
                "query": query[:100],
                "intent": intent,
            })
            yield "[错误] RAG pipeline 超时，请稍后再试"
        except Exception as e:
            _gen_logger.error("RAG stream error", extra={
                "query": query[:100],
                "error": str(e),
            })
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

    async def _timed_product_search(
        self, query: str, latency: dict
    ) -> "ProductContext | None":
        """商品检索（带超时保护）"""
        start = time.monotonic()
        try:
            if not self._product_service:
                return None

            result = await asyncio.wait_for(
                self._product_service.search_product(query),
                timeout=self._config.retrieve_timeout_ms / 1000,
            )
            latency["value"] += int((time.monotonic() - start) * 1000)

            if result and result.detail:
                detail = result.detail
                product_name = ""
                product_detail = ""

                if detail.spu:
                    product_name = detail.spu.name or ""
                    # 组装商品详情
                    props = {}
                    if detail.spu.properties:
                        import json
                        try:
                            props = json.loads(detail.spu.properties) if isinstance(detail.spu.properties, str) else detail.spu.properties
                        except Exception:
                            pass
                    product_detail = f"{detail.spu.name or ''} | 价格: ¥{detail.spu.price or 0:.2f}"
                    if props:
                        key_props = [f"{k}: {v}" for k, v in props.items() if k in ("规格", "颜色", "内存", "存储")]
                        if key_props:
                            product_detail += f" | {' | '.join(key_props)}"

                return ProductContext(
                    product_name=product_name,
                    product_detail=product_detail,
                    match_type=result.resolve_result.match_type.value if result.resolve_result else "",
                    confidence=result.resolve_result.confidence if result.resolve_result else 0.0,
                )
            return None
        except asyncio.TimeoutError:
            latency["value"] += int((time.monotonic() - start) * 1000)
            _gen_logger.warning("Product search timeout", extra={"query": query[:50]})
            return None
        except Exception as e:
            latency["value"] += int((time.monotonic() - start) * 1000)
            _gen_logger.error("Product search failed", extra={"query": query[:50], "error": str(e)})
            return None

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
            "product_query": "product_query",
        }
        tool_name = tool_name_map.get(intent)
        if not tool_name:
            return None

        # 从 query 中提取相关信息
        query = params.get("_query", "")
        user_id = params.get("user_id", "")
        
        # 构建工具参数（基础参数）
        tool_params = {}
        
        # === 商品查询参数提取 ===
        if intent == "product_query":
            # 商品查询不需要user_id
            # 尝试从查询中提取商品相关信息
            import re
            
            # 1. 提取条形码（通常是数字，长度8-20位）
            barcode_match = re.search(r'条形码[：:]\s*(\d{8,20})', query)
            if not barcode_match:
                barcode_match = re.search(r'\b(\d{8,20})\b', query)
            if barcode_match:
                tool_params["barcode"] = barcode_match.group(1)
            
            # 2. 提取商品ID（通常是数字）
            product_id_match = re.search(r'商品ID[：:]\s*(\d+)', query)
            if not product_id_match:
                product_id_match = re.search(r'\b商品(?:号|ID)?[：:\s]*(\d+)\b', query)
            if product_id_match:
                tool_params["product_id"] = int(product_id_match.group(1))
            
            # 3. 商品名称/关键词：直接传递整个查询（让工具内部处理）
            # 如果查询中包含明确的商品名称短语，可以提取
            # 这里简化处理：传递原始查询，让工具层进一步解析
            if not tool_params:
                # 没有提取到具体ID/条形码，传递整个查询作为商品名称
                tool_params["product_name"] = query.strip()
        
        # === 订单相关工具参数提取 ===
        else:
            # 需要user_id
            tool_params["user_id"] = user_id
            
            # 提取订单号
            extracted_order_id = None
            if query:
                # 优先匹配：以订单号格式开头（独立行或开头无其他词）
                m = re.search(r"\b([A-Z]{2,4}[0-9]{6,})\b", query, re.IGNORECASE)
                if not m:
                    # 兜底：从"订单"或"单号"关键词附近贪婪匹配
                    m = re.search(
                        r"订单.*?([A-Z]{2,4}[0-9]{6,})",
                        query, re.IGNORECASE | re.DOTALL
                    )
                if not m:
                    # 纯订单号模式：独立数字字母组合（8-32位）
                    m = re.search(r"\b([A-Z0-9]{8,32})\b", query)
                if m:
                    extracted_order_id = m.group(1).upper()
            
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
