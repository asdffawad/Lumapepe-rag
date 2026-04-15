"""
BM25 Sparse Retriever（独立多路召回）
- 从 Milvus 全量加载文档，构建内存 BM25 索引
- 支持中英文分词（jieba）
- 惰性加载：首次查询时构建索引
"""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.logging_config import get_logger

if TYPE_CHECKING:
    from app.vectorstore.milvus_client import MilvusClient

_bm25_logger = get_logger("bm25_retriever")


@dataclass
class BM25Doc:
    """BM25 检索结果"""
    id: str
    content: str
    metadata: dict
    score: float


class BM25Indexer:
    """
    内存 BM25 索引（基于 rank_bm25）
    支持中英文混合分词
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self._k1 = k1
        self._b = b
        self._corpus: list[str] = []
        self._doc_ids: list[str] = []
        self._metadatas: list[dict] = []
        self._index = None
        self._tokenizer = None

    def _get_tokenizer(self):
        if self._tokenizer is None:
            from app.services.reranker import get_tokenizer
            self._tokenizer = get_tokenizer()
        return self._tokenizer

    def build(self, doc_ids: list[str], contents: list[str], metadatas: list[dict]):
        """构建 BM25 索引"""
        from rank_bm25 import BM25Okapi

        self._doc_ids = doc_ids
        self._corpus = contents
        self._metadatas = metadatas

        tokenizer = self._get_tokenizer()
        tokenized_corpus = [tokenizer.tokenize(doc) for doc in contents]
        self._index = BM25Okapi(tokenized_corpus, k1=self._k1, b=self._b)

        _bm25_logger.info(
            "BM25 index built",
            extra={"num_docs": len(contents), "k1": self._k1, "b": self._b}
        )

    def search(self, query: str, top_k: int = 20) -> list[BM25Doc]:
        """搜索 BM25 相关文档"""
        if self._index is None:
            return []

        tokenizer = self._get_tokenizer()
        query_tokens = tokenizer.tokenize(query)

        if not query_tokens:
            return []

        scores = self._index.get_scores(query_tokens)
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]

        return [
            BM25Doc(
                id=self._doc_ids[i],
                content=self._corpus[i],
                metadata=self._metadatas[i] if i < len(self._metadatas) else {},
                score=scores[i],
            )
            for i in top_indices
        ]

    @property
    def num_docs(self) -> int:
        return len(self._corpus)


class BM25Retriever:
    """
    独立 BM25 召回器
    - 从 Milvus 全量加载文档（按需批量加载）
    - 构建内存 BM25 索引
    - 支持增量更新
    """

    def __init__(
        self,
        milvus_client: "MilvusClient",
        k1: float = 1.5,
        b: float = 0.75,
        batch_size: int = 1000,
    ):
        self._milvus = milvus_client
        self._k1 = k1
        self._b = b
        self._batch_size = batch_size
        self._indexer: BM25Indexer | None = None
        self._index_built = False

    async def _ensure_index(self):
        """惰性构建 BM25 索引"""
        if self._index_built:
            return

        if self._indexer is None:
            self._indexer = BM25Indexer(k1=self._k1, b=self._b)

        _bm25_logger.info("Building BM25 index from Milvus...")

        all_ids: list[str] = []
        all_contents: list[str] = []
        all_metadatas: list[dict] = []

        batch_offset = 0
        while True:
            docs = await self._fetch_batch(batch_offset, self._batch_size)
            if not docs:
                break

            for doc in docs:
                all_ids.append(doc.metadata.get("source", "") or f"doc_{batch_offset}")
                all_contents.append(doc.content)
                all_metadatas.append(doc.metadata)

            batch_offset += len(docs)

            if len(docs) < self._batch_size:
                break

        if all_contents:
            self._indexer.build(all_ids, all_contents, all_metadatas)
            self._index_built = True
            _bm25_logger.info(f"BM25 index ready: {len(all_contents)} documents loaded")
        else:
            _bm25_logger.warning("No documents found in Milvus for BM25 indexing")

    async def _fetch_batch(self, offset: int, limit: int) -> list:
        """批量从 Milvus 获取文档"""
        import asyncio

        async def _query():
            coll = await self._milvus.collection
            import json

            def _sync_query():
                results = coll.query(
                    expr="id != ''",
                    output_fields=[
                        "id", "content", "source", "doc_type",
                        "section_type", "markets", "lang", "hierarchy",
                    ],
                    limit=limit,
                    offset=offset,
                )
                docs = []
                for fields in results:
                    markets_raw = fields.get("markets", "[]")
                    hierarchy_raw = fields.get("hierarchy", "{}")
                    try:
                        markets = json.loads(markets_raw) if markets_raw else ["CN"]
                        hierarchy = json.loads(hierarchy_raw) if hierarchy_raw else {}
                    except Exception:
                        markets = ["CN"]
                        hierarchy = {}

                    from app.services.vector_retriever import RetrievedDoc
                    docs.append(RetrievedDoc(
                        content=fields.get("content", ""),
                        metadata={
                            "source": fields.get("source", ""),
                            "doc_type": fields.get("doc_type", ""),
                            "section_type": fields.get("section_type", "body"),
                            "markets": markets,
                            "lang": fields.get("lang", "zh"),
                            "hierarchy": hierarchy,
                        },
                        score=0.0,
                        source=fields.get("source", ""),
                    ))
                return docs

            return await asyncio.to_thread(_sync_query)

        return await _query()

    async def retrieve(self, query: str, top_k: int = 20) -> list:
        """BM25 检索"""
        await self._ensure_index()

        if self._indexer is None:
            return []

        results = self._indexer.search(query, top_k=top_k)

        _bm25_logger.debug(
            "BM25 search completed",
            extra={"query": query[:50], "top_k": top_k, "num_results": len(results)}
        )

        return results

    def invalidate(self):
        """使索引失效（下次查询时重建）"""
        self._index_built = False
        self._indexer = None

    @property
    def is_ready(self) -> bool:
        return self._index_built and self._indexer is not None

    @property
    def num_indexed(self) -> int:
        return self._indexer.num_docs if self._indexer else 0
