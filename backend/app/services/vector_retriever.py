"""
向量检索模块
- 多路召回: 商品 + FAQ + 政策文档
- 混合检索: dense vector + sparse keyword (BM25)
- 分组检索: 按文档类型分组
- 过滤条件: metadata 过滤
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.vectorstore.milvus_client import MilvusClient


@dataclass
class RetrievedDoc:
    content: str
    metadata: dict
    score: float
    source: str


@dataclass
class RetrievalResult:
    docs: list[RetrievedDoc]
    query: str
    total: int


class RetrievalError(Exception):
    pass


class VectorRetriever(ABC):
    @abstractmethod
    async def retrieve(
        self, query: str, top_k: int, filters: dict | None
    ) -> list[RetrievedDoc]:
        """
        输入: query 文本 + top_k + metadata 过滤条件
        输出: list[RetrievedDoc] { content, metadata, score, source }
        """
        pass


class SimpleVectorRetriever(VectorRetriever):
    """基于 Milvus 的向量检索实现"""

    def __init__(self, milvus_client: "MilvusClient"):
        from app.services.embedding_service import EmbeddingService
        self._milvus = milvus_client
        self._embed = EmbeddingService.get_instance()

    async def retrieve(
        self, query: str, top_k: int = 10, filters: dict | None = None
    ) -> list[RetrievedDoc]:
        # 1. Query 向量化
        vectors = await self._embed.embed([query])
        query_vector = vectors[0]

        # 2. Milvus 向量搜索
        docs = await self._milvus.search(
            query_vector=query_vector,
            top_k=top_k,
            filters=filters,
        )
        return docs


class MockVectorRetriever(VectorRetriever):
    """测试用 Mock 实现"""

    async def retrieve(
        self, query: str, top_k: int = 10, filters: dict | None = None
    ) -> list[RetrievedDoc]:
        await asyncio.sleep(0.02)
        return [
            RetrievedDoc(
                content=f"[Mock检索结果{i}] {query}",
                metadata={"doc_type": "product", "id": i},
                score=1.0 - i * 0.1,
                source=f"mock_source_{i}",
            )
            for i in range(min(top_k, 3))
        ]


class HybridRetriever(VectorRetriever):
    """
    Dense(ANN) + Sparse(BM25) 混合检索
    RRF 融合公式: score = Σ 1/(k + rank_i)
    """

    def __init__(
        self,
        milvus_client: "MilvusClient",
        embed_service=None,
        k1: float = 1.5,
        b: float = 0.75,
        rrf_k: int = 60,
    ):
        from app.services.embedding_service import EmbeddingService
        self._milvus = milvus_client
        self._embed = embed_service or EmbeddingService.get_instance()
        self._k1 = k1
        self._b = b
        self._rrf_k = rrf_k
        self._tokenizer = None

    def _get_tokenizer(self):
        if self._tokenizer is None:
            from app.services.reranker import get_tokenizer
            self._tokenizer = get_tokenizer()
        return self._tokenizer

    async def retrieve(
        self, query: str, top_k: int = 10, filters: dict | None = None
    ) -> list[RetrievedDoc]:
        # 1. Dense: ANN 搜索（取 top_k*2 以便 RRF 融合时有足够候选）
        vectors = await self._embed.embed([query])
        dense_docs = await self._milvus.search(
            query_vector=vectors[0],
            top_k=top_k * 2,
            filters=filters,
        )
        if not dense_docs:
            return []

        # 2. Sparse: in-memory BM25 对 dense_docs 重打分
        bm25_scores = self._bm25_score(query, [d.content for d in dense_docs])

        # 3. RRF 融合
        fused = self._rrf_fusion(dense_docs, bm25_scores, top_k)

        # 4. 注入 BM25 分到 metadata（供 reranker 使用）
        bm25_map = {id(d): s for d, s in zip(dense_docs, bm25_scores)}
        for doc in fused:
            doc.metadata["bm25_score"] = bm25_map.get(id(doc), 0.0)

        return fused

    def _bm25_score(self, query: str, docs: list[str]) -> list[float]:
        """
        简化 BM25：对 dense 候选 docs 计算词匹配得分。
        不做全量 collection 扫描，适合 top_k~20 的候选重打分。
        """
        tokenizer = self._get_tokenizer()
        query_terms = tokenizer.tokenize(query)
        if not query_terms:
            return [0.0] * len(docs)

        doc_count = len(docs)
        # 收集词频
        word_doc_freq: dict[str, int] = {}
        docs_bow: list[dict[str, int]] = []
        for doc_text in docs:
            bow: dict[str, int] = {}
            for word in tokenizer.tokenize(doc_text):
                bow[word] = bow.get(word, 0) + 1
                word_doc_freq[word] = word_doc_freq.get(word, 0) + 1
            docs_bow.append(bow)

        avgdl = sum(len(bow) for bow in docs_bow) / max(doc_count, 1)
        scores = []
        for bow in docs_bow:
            score = 0.0
            doc_len = len(bow)
            for term in query_terms:
                if term in bow:
                    df = word_doc_freq.get(term, 0)
                    idf = max(0, (doc_count - df + 0.5) / (df + 0.5))
                    tf = bow[term]
                    score += idf * (tf * (self._k1 + 1)) / (
                        tf + self._k1 * (1 - self._b + self._b * doc_len / max(avgdl, 1))
                    )
            scores.append(score)

        return scores

    def _rrf_fusion(
        self,
        docs: list[RetrievedDoc],
        bm25_scores: list[float],
        top_k: int,
    ) -> list[RetrievedDoc]:
        """Reciprocal Rank Fusion 融合 dense 和 bm25 排名"""
        # 建立索引→位置映射
        dense_ranked = sorted(range(len(docs)), key=lambda i: docs[i].score, reverse=True)
        bm25_ranked = sorted(range(len(docs)), key=lambda i: bm25_scores[i], reverse=True)

        rrf: dict[int, float] = {}
        for rank, idx in enumerate(dense_ranked):
            rrf[idx] = rrf.get(idx, 0) + 1.0 / (self._rrf_k + rank)
        for rank, idx in enumerate(bm25_ranked):
            rrf[idx] = rrf.get(idx, 0) + 1.0 / (self._rrf_k + rank)

        fused_indices = sorted(rrf.keys(), key=lambda i: rrf[i], reverse=True)[:top_k]
        return [docs[i] for i in fused_indices]
