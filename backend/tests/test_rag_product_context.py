"""
Tests for product context integration in RAG pipeline.

These tests verify the bug fix for: when a user enters the chat from a product
detail page (e.g. ?spu_id=756), the product context should be retrieved and
injected into the RAG pipeline so the LLM can answer questions about that product.

Root cause: product_service was not injected into RAGOrchestrator in main.py,
and handle_message had sku_id/spu_id from URL params but never passed them
to orchestrator.run_stream().
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.rag_orchestrator import RAGOrchestrator, ProductContext
from app.services.query_rewriter import MockQueryRewriter
from app.services.vector_retriever import MockVectorRetriever, RetrievedDoc
from app.services.reranker import MockReranker
from app.services.prompt_builder import MockPromptBuilder
from app.services.llm_generator import MockLLMGenerator
from app.config import RAGConfig


class TestOrchestratorProductContextInjection:
    """Tests for product context injection into RAG pipeline."""

    @pytest.fixture
    def config(self):
        return RAGConfig(
            top_k=5,
            rerank_top_k=3,
            rewrite_timeout_ms=500,
            retrieve_timeout_ms=1000,
            rerank_timeout_ms=1000,
            llm_timeout_ms=30000,
            max_context_tokens=3000,
        )

    @pytest.fixture
    def mock_product_service(self):
        """Mock product service that returns product info for a given ID."""
        mock = AsyncMock()
        # Text search result
        result = MagicMock()
        result.detail = MagicMock()
        result.detail.spu = MagicMock()
        result.detail.spu.name = "便携式发电机"
        result.detail.spu.price = 2999.00  # 美元
        result.detail.spu.properties = '{"功率": "3000W", "电压": "220V"}'
        result.resolve_result = MagicMock()
        result.resolve_result.match_type = MagicMock()
        result.resolve_result.match_type.value = "text_search"
        result.resolve_result.confidence = 0.8
        mock.search_product.return_value = result
        # SKU detail result
        sku_detail = MagicMock()
        sku_detail.spu = MagicMock()
        sku_detail.spu.name = "便携式发电机"
        sku_detail.spu.price = 2999.00
        sku_detail.spu.properties = '{"功率": "3000W", "电压": "220V"}'
        mock.get_sku_detail.return_value = sku_detail
        # SPU detail result
        spu_detail = MagicMock()
        spu_detail.spu = MagicMock()
        spu_detail.spu.name = "便携式发电机"
        spu_detail.spu.price = 2999.00
        spu_detail.spu.properties = '{"功率": "3000W", "电压": "220V"}'
        mock.get_spu_detail.return_value = spu_detail
        return mock

    @pytest.mark.asyncio
    async def test_orchestrator_run_stream_accepts_sku_id_parameter(
        self, config, mock_product_service
    ):
        """run_stream should accept sku_id and spu_id parameters."""
        orchestrator = RAGOrchestrator(
            rewriter=MockQueryRewriter(),
            retriever=MockVectorRetriever(),
            reranker=MockReranker(),
            prompt_builder=MockPromptBuilder(),
            generator=MockLLMGenerator(),
            config=config,
            product_service=mock_product_service,
        )
        chunks = []
        async for chunk in orchestrator.run_stream(
            query="这个商品有哪些特点？",
            history=[],
            intent="product_qa",
            request_id="test-001",
            user_id="test-user",
            sku_id=753,
            spu_id=None,
        ):
            chunks.append(chunk)
        # Should not raise TypeError about unexpected keyword argument
        assert len(chunks) >= 0

    @pytest.mark.asyncio
    async def test_orchestrator_run_stream_accepts_spu_id_parameter(
        self, config, mock_product_service
    ):
        """run_stream should accept spu_id parameter."""
        orchestrator = RAGOrchestrator(
            rewriter=MockQueryRewriter(),
            retriever=MockVectorRetriever(),
            reranker=MockReranker(),
            prompt_builder=MockPromptBuilder(),
            generator=MockLLMGenerator(),
            config=config,
            product_service=mock_product_service,
        )
        chunks = []
        async for chunk in orchestrator.run_stream(
            query="介绍一下这件商品",
            history=[],
            intent="product_qa",
            request_id="test-002",
            user_id="test-user",
            sku_id=None,
            spu_id=756,
        ):
            chunks.append(chunk)
        assert len(chunks) >= 0

    @pytest.mark.asyncio
    async def test_orchestrator_injects_product_service(
        self, config, mock_product_service
    ):
        """RAGOrchestrator should accept and use product_service via constructor."""
        orchestrator = RAGOrchestrator(
            rewriter=MockQueryRewriter(),
            retriever=MockVectorRetriever(),
            reranker=MockReranker(),
            prompt_builder=MockPromptBuilder(),
            generator=MockLLMGenerator(),
            config=config,
            product_service=mock_product_service,
        )
        assert orchestrator._product_service is mock_product_service

    @pytest.mark.asyncio
    async def test_product_context_retrieved_when_spu_id_provided(
        self, config, mock_product_service
    ):
        """When spu_id is provided, product_service should be called to fetch context."""
        orchestrator = RAGOrchestrator(
            rewriter=MockQueryRewriter(),
            retriever=MockVectorRetriever(),
            reranker=MockReranker(),
            prompt_builder=MockPromptBuilder(),
            generator=MockLLMGenerator(),
            config=config,
            product_service=mock_product_service,
        )

        chunks = []
        async for chunk in orchestrator.run_stream(
            query="这个商品有什么特点",
            history=[],
            intent="product_qa",
            request_id="test-003",
            user_id="test-user",
            sku_id=None,
            spu_id=756,
        ):
            chunks.append(chunk)

        # Verify product_service was called to fetch by spu_id
        mock_product_service.get_spu_detail.assert_called()

    @pytest.mark.asyncio
    async def test_product_context_retrieved_when_sku_id_provided(
        self, config, mock_product_service
    ):
        """When sku_id is provided, product_service should be called to fetch context."""
        orchestrator = RAGOrchestrator(
            rewriter=MockQueryRewriter(),
            retriever=MockVectorRetriever(),
            reranker=MockReranker(),
            prompt_builder=MockPromptBuilder(),
            generator=MockLLMGenerator(),
            config=config,
            product_service=mock_product_service,
        )

        chunks = []
        async for chunk in orchestrator.run_stream(
            query="价格是多少",
            history=[],
            intent="product_qa",
            request_id="test-004",
            user_id="test-user",
            sku_id=753,
            spu_id=None,
        ):
            chunks.append(chunk)

        # Verify product_service was called to fetch by sku_id
        mock_product_service.get_sku_detail.assert_called()

    @pytest.mark.asyncio
    async def test_product_context_not_retrieved_for_non_product_qa_intent(
        self, config, mock_product_service
    ):
        """For non-product_qa intents, product_service should not be called."""
        orchestrator = RAGOrchestrator(
            rewriter=MockQueryRewriter(),
            retriever=MockVectorRetriever(),
            reranker=MockReranker(),
            prompt_builder=MockPromptBuilder(),
            generator=MockLLMGenerator(),
            config=config,
            product_service=mock_product_service,
        )

        chunks = []
        async for chunk in orchestrator.run_stream(
            query="查询我的订单状态",
            history=[],
            intent="order_query",
            request_id="test-005",
            user_id="test-user",
            sku_id=753,
            spu_id=None,
        ):
            chunks.append(chunk)

        # order_query should not trigger product search
        mock_product_service.search_product.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_run_accepts_sku_id_spu_id(self, config, mock_product_service):
        """Sync run() method should also accept sku_id and spu_id parameters."""
        orchestrator = RAGOrchestrator(
            rewriter=MockQueryRewriter(),
            retriever=MockVectorRetriever(),
            reranker=MockReranker(),
            prompt_builder=MockPromptBuilder(),
            generator=MockLLMGenerator(),
            config=config,
            product_service=mock_product_service,
        )
        result = await orchestrator.run(
            query="这件商品支持退货吗",
            history=[],
            intent="product_qa",
            request_id="test-006",
            user_id="test-user",
            sku_id=753,
            spu_id=None,
        )
        assert result.content is not None
        mock_product_service.get_sku_detail.assert_called()


class TestPriceFormattingBug:
    """Tests for the price unit (USD) in product context.

    Database stores prices in USD. The RAG pipeline should display
    prices as-is with USD symbol $ without any conversion.
    """

    @pytest.fixture
    def config(self):
        return RAGConfig(
            top_k=5,
            rerank_top_k=3,
            rewrite_timeout_ms=500,
            retrieve_timeout_ms=1000,
            rerank_timeout_ms=1000,
            llm_timeout_ms=30000,
            max_context_tokens=3000,
        )

    @pytest.fixture
    def mock_product_service_with_price(self):
        """Mock product service with a known price value in USD."""
        mock = AsyncMock()
        result = MagicMock()
        result.detail = MagicMock()
        result.detail.spu = MagicMock()
        result.detail.spu.name = "便携式发电机"
        result.detail.spu.price = 2999.00  # 价格单位：美元
        result.detail.spu.properties = '{"功率": "3000W"}'
        mock.search_product.return_value = result

        spu_detail = MagicMock()
        spu_detail.spu = MagicMock()
        spu_detail.spu.name = "便携式发电机"
        spu_detail.spu.price = 2999.00
        spu_detail.spu.properties = '{"功率": "3000W"}'
        mock.get_spu_detail.return_value = spu_detail

        sku_detail = MagicMock()
        sku_detail.spu = MagicMock()
        sku_detail.spu.name = "便携式发电机"
        sku_detail.spu.price = 2999.00
        sku_detail.spu.properties = '{"功率": "3000W"}'
        mock.get_sku_detail.return_value = sku_detail
        return mock

    @pytest.mark.asyncio
    async def test_product_detail_price_shown_as_usd(self, config, mock_product_service_with_price):
        """价格直接以美元显示，无需换算。

        数据库存 2999.00 美元，product_detail 应显示为 "$2999.00"。
        """
        orchestrator = RAGOrchestrator(
            rewriter=MockQueryRewriter(),
            retriever=MockVectorRetriever(),
            reranker=MockReranker(),
            prompt_builder=MockPromptBuilder(),
            generator=MockLLMGenerator(),
            config=config,
            product_service=mock_product_service_with_price,
        )

        pc = await orchestrator._timed_product_search(
            query="这个发电机价格",
            latency={"value": 0},
            sku_id=None,
            spu_id=756,
        )

        assert pc is not None
        assert "$2999.00" in pc.product_detail, (
            f"Price should show as USD $2999.00! Found: {pc.product_detail}"
        )

    @pytest.mark.asyncio
    async def test_sku_price_shown_as_usd(self, config, mock_product_service_with_price):
        """SKU价格同样以美元显示。"""
        orchestrator = RAGOrchestrator(
            rewriter=MockQueryRewriter(),
            retriever=MockVectorRetriever(),
            reranker=MockReranker(),
            prompt_builder=MockPromptBuilder(),
            generator=MockLLMGenerator(),
            config=config,
            product_service=mock_product_service_with_price,
        )

        pc = await orchestrator._timed_product_search(
            query="这个商品价格",
            latency={"value": 0},
            sku_id=753,
            spu_id=None,
        )

        assert pc is not None
        assert "$2999.00" in pc.product_detail, (
            f"SKU price should show as USD $2999.00! Found: {pc.product_detail}"
        )

    @pytest.mark.asyncio
    async def test_product_detail_uses_usd_symbol(self, config, mock_product_service_with_price):
        """商品价格必须使用美元符号 $ 而不是 ¥。"""
        orchestrator = RAGOrchestrator(
            rewriter=MockQueryRewriter(),
            retriever=MockVectorRetriever(),
            reranker=MockReranker(),
            prompt_builder=MockPromptBuilder(),
            generator=MockLLMGenerator(),
            config=config,
            product_service=mock_product_service_with_price,
        )

        pc = await orchestrator._timed_product_search(
            query="这个发电机价格",
            latency={"value": 0},
            sku_id=None,
            spu_id=756,
        )

        assert pc is not None
        assert "$" in pc.product_detail, (
            f"Price should use USD symbol $! Found: {pc.product_detail}"
        )
        assert "¥" not in pc.product_detail, (
            f"Price should NOT use ¥ symbol! Found: {pc.product_detail}"
        )

