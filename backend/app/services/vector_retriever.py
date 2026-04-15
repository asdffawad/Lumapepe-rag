"""
向量检索模块
- 多路召回: 商品 + FAQ + 政策文档
- 混合检索: dense vector + sparse keyword (BM25)
- 分组检索: 按文档类型分组
- 过滤条件: metadata 过滤
"""
from __future__ import annotations

import asyncio
import hashlib
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
    Dense(ANN) + Sparse(BM25) 真正多路召回
    - Dense: 从 Milvus 语义召回
    - Sparse: 从独立 BM25 索引关键词召回
    - RRF 融合公式: score = Σ 1/(k + rank_i)
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
        self._bm25_retriever: "BM25Retriever | None" = None

    def _get_bm25_retriever(self) -> "BM25Retriever":
        if self._bm25_retriever is None:
            from app.services.bm25_retriever import BM25Retriever
            self._bm25_retriever = BM25Retriever(
                milvus_client=self._milvus,
                k1=self._k1,
                b=self._b,
            )
        return self._bm25_retriever

    async def retrieve(
        self, query: str, top_k: int = 10, filters: dict | None = None
    ) -> list[RetrievedDoc]:
        # 并行执行 Dense 和 Sparse 检索
        dense_task = self._dense_search(query, top_k * 2, filters)
        bm25_task = self._bm25_search(query, top_k * 2)

        dense_results, bm25_results = await asyncio.gather(dense_task, bm25_task)

        if not dense_results and not bm25_results:
            return []

        # RRF 融合
        fused = self._rrf_fusion(dense_results, bm25_results, top_k)

        return fused[:top_k]

    async def _dense_search(
        self, query: str, top_k: int, filters: dict | None
    ) -> list[RetrievedDoc]:
        """Dense 语义检索"""
        vectors = await self._embed.embed([query])
        docs = await self._milvus.search(
            query_vector=vectors[0],
            top_k=top_k,
            filters=filters,
        )
        return docs

    async def _bm25_search(
        self, query: str, top_k: int
    ) -> list[RetrievedDoc]:
        """BM25 关键词检索（全量文档）"""
        bm25_retriever = self._get_bm25_retriever()
        bm25_docs = await bm25_retriever.retrieve(query, top_k=top_k)

        # 转换为 RetrievedDoc 格式
        return [
            RetrievedDoc(
                content=doc.content,
                metadata=doc.metadata,
                score=doc.score,
                source=doc.metadata.get("source", ""),
            )
            for doc in bm25_docs
        ]

    def _rrf_fusion(
        self,
        dense_docs: list[RetrievedDoc],
        bm25_docs: list[RetrievedDoc],
        top_k: int,
    ) -> list[RetrievedDoc]:
        """
        RRF 融合 Dense 和 BM25 结果
        基于 content 哈希对齐文档
        """
        # 建立 content 哈希 -> (dense_score, dense_rank) 映射
        dense_map: dict[str, tuple[float, int]] = {}
        for rank, doc in enumerate(dense_docs):
            key = self._make_doc_key(doc)
            dense_map[key] = (doc.score, rank)

        # 建立 content 哈希 -> (bm25_score, bm25_rank) 映射
        bm25_map: dict[str, tuple[float, int]] = {}
        for rank, doc in enumerate(bm25_docs):
            key = self._make_doc_key(doc)
            bm25_map[key] = (doc.score, rank)

        # 收集所有唯一文档
        all_keys: set[str] = set(dense_map.keys()) | set(bm25_map.keys())

        # 计算 RRF 分数
        rrf_scores: dict[str, float] = {}
        for key in all_keys:
            dense_rank = dense_map[key][1] if key in dense_map else len(dense_docs)
            bm25_rank = bm25_map[key][1] if key in bm25_map else len(bm25_docs)
            rrf_scores[key] = (
                1.0 / (self._rrf_k + dense_rank) +
                1.0 / (self._rrf_k + bm25_rank)
            )

        # 按 RRF 分数排序
        sorted_keys = sorted(rrf_scores.keys(), key=lambda k: rrf_scores[k], reverse=True)

        # 构建结果（合并 metadata）
        result: list[RetrievedDoc] = []
        for key in sorted_keys[:top_k * 2]:
            # 优先使用 Dense 结果（包含完整 metadata）
            doc = None
            for d in dense_docs:
                if self._make_doc_key(d) == key:
                    doc = d
                    break
            if doc is None:
                for d in bm25_docs:
                    if self._make_doc_key(d) == key:
                        doc = d
                        break

            if doc is None:
                continue

            new_doc = RetrievedDoc(
                content=doc.content,
                metadata={
                    **doc.metadata,
                    "bm25_score": bm25_map.get(key, (0.0, 0))[0],
                    "dense_score": dense_map.get(key, (0.0, 0))[0],
                    "dense_rank": dense_map.get(key, (0.0, len(dense_docs)))[1],
                    "bm25_rank": bm25_map.get(key, (0.0, len(bm25_docs)))[1],
                },
                score=rrf_scores[key],
                source=doc.source,
            )
            result.append(new_doc)

        return result

    def _make_doc_key(self, doc: RetrievedDoc) -> str:
        """生成文档唯一标识（用于跨通道对齐）"""
        content = doc.content.strip()
        key_content = content[:200] if content else ""
        return hashlib.md5(key_content.encode()).hexdigest()
