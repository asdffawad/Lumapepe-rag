"""
RAG Orchestrator 测试
"""
import pytest
from app.services.rag_orchestrator import RAGOrchestrator
from app.services.query_rewriter import MockQueryRewriter
from app.services.vector_retriever import MockVectorRetriever
from app.services.reranker import MockReranker
from app.services.prompt_builder import MockPromptBuilder
from app.services.llm_generator import MockLLMGenerator
from app.config import RAGConfig


@pytest.fixture
def config():
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
def orchestrator(config):
    return RAGOrchestrator(
        rewriter=MockQueryRewriter(),
        retriever=MockVectorRetriever(),
        reranker=MockReranker(),
        prompt_builder=MockPromptBuilder(),
        generator=MockLLMGenerator(),
        config=config,
    )


@pytest.mark.asyncio
async def test_orchestrator_run(orchestrator):
    result = await orchestrator.run(
        query="iPhone 16 价格多少？",
        history=[],
        intent="product_qa",
        request_id="test-001",
    )
    assert result.content is not None
    assert result.intent == "product_qa"
    assert isinstance(result.sources, list)
    assert result.tokens_used >= 0
    assert result.latency_ms >= 0


@pytest.mark.asyncio
async def test_orchestrator_stream(orchestrator):
    chunks = []
    async for chunk in orchestrator.run_stream(
        query="MacBook 支持多大内存？",
        history=[],
        intent="product_qa",
        request_id="test-002",
    ):
        chunks.append(chunk)
    full = "".join(c.content if hasattr(c, "content") else str(c) for c in chunks)
    assert len(full) > 0


@pytest.mark.asyncio
async def test_orchestrator_with_history(orchestrator):
    history = [
        {"role": "user", "content": "我想买一个手机"},
        {"role": "assistant", "content": "好的，请问您有什么品牌偏好吗？"},
    ]
    result = await orchestrator.run(
        query="这个有没有5G版本？",
        history=history,
        intent="product_qa",
    )
    assert result.content is not None
