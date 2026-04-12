"""
重排模块测试
"""
import pytest
from app.services.reranker import MockReranker, MMRReranker
from app.vectorstore.milvus_client import RetrievedDoc


class TestMockReranker:
    @pytest.fixture
    def reranker(self):
        return MockReranker()

    @pytest.fixture
    def candidates(self):
        return [
            RetrievedDoc(
                content="iPhone 16 参数配置介绍",
                metadata={"doc_type": "product"},
                score=0.95,
                source="products",
            ),
            RetrievedDoc(
                content="退换货政策说明",
                metadata={"doc_type": "policy"},
                score=0.80,
                source="policy",
            ),
            RetrievedDoc(
                content="iPhone 16 价格信息",
                metadata={"doc_type": "product"},
                score=0.90,
                source="products",
            ),
        ]

    @pytest.mark.asyncio
    async def test_reranker_returns_results(self, reranker, candidates):
        result = await reranker.rerank("iPhone 16 多少钱？", candidates, top_k=3)
        assert len(result) > 0
        assert len(result) <= 3
        assert all(hasattr(d, "rerank_score") for d in result)

    @pytest.mark.asyncio
    async def test_reranker_empty_input(self, reranker):
        result = await reranker.rerank("手机", [], top_k=5)
        assert result == []

    @pytest.mark.asyncio
    async def test_reranker_top_k_limit(self, reranker, candidates):
        result = await reranker.rerank("手机", candidates, top_k=1)
        assert len(result) == 1


class TestMMRReranker:
    @pytest.fixture
    def reranker(self):
        return MMRReranker(lambda_mult=0.5)

    @pytest.fixture
    def candidates(self):
        return [
            RetrievedDoc(
                content="iPhone 16 Pro Max 参数介绍",
                metadata={"doc_type": "product"},
                score=0.95,
                source="products",
            ),
            RetrievedDoc(
                content="iPhone 16 Pro 512GB 优惠活动",
                metadata={"doc_type": "product"},
                score=0.92,
                source="products",
            ),
            RetrievedDoc(
                content="MacBook Pro 14寸 配置表",
                metadata={"doc_type": "product"},
                score=0.70,
                source="products",
            ),
        ]

    @pytest.mark.asyncio
    async def test_mmr_deduplication(self, reranker, candidates):
        """MMR 应该去重，返回相关性高且多样的结果"""
        result = await reranker.rerank("iPhone 16", candidates, top_k=2)
        # 两条结果应该来自不同内容
        assert len(result) <= 2
        # 由于前两条内容相似度高，MMR 应该倾向保留不同内容
        contents = [d.content for d in result]
        # 至少不应该有完全相同的内容
        assert len(set(contents)) == len(contents)
