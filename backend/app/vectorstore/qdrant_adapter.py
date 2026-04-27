"""
Qdrant 向量数据库客户端
使用官方 qdrant-client 库，支持 HTTP 和 gRPC 两种传输协议

相比 Raw HTTP 的优势：
- gRPC 传输：吞吐量提升 3-5x
- 连接池复用：减少 TCP 握手开销
- 自动重试与超时处理
- 原生稀疏向量支持：实现真正的端到端混合检索
- 完善的 Filter 表达式：must / must_not / should
- Scroll API：支持大规模数据分页遍历
"""
from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any, AsyncIterator

from app.config import QdrantConfig
from app.services.vector_retriever import RetrievedDoc

if TYPE_CHECKING:
    pass

# Re-export so callers can import QdrantConfig from this module too
__all__ = ["QdrantClient", "QdrantConfig"]


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------
class QdrantError(Exception):
    """Qdrant 操作异常"""
    pass


class QdrantCollectionNotFound(QdrantError):
    """Collection 不存在"""
    pass


# ---------------------------------------------------------------------------
# 主客户端
# ---------------------------------------------------------------------------
class QdrantClient:
    """
    Qdrant 向量数据库客户端（基于官方 qdrant-client）

    接口与 MilvusClient 兼容，同时提供 Qdrant 独有的高级特性：
    - gRPC 高性能传输
    - 稀疏向量（原生 hybrid search）
    - 完善的条件过滤
    - Scroll 分页遍历
    """

    _instance: "QdrantClient | None" = None

    def __init__(self, config: QdrantConfig):
        self.config = config
        self._client = None
        self._sync_client = None
        self._connected = False
        self._collection_initialized = False

    # -------------------------
    # 连接管理
    # -------------------------
    async def connect(self):
        """建立与 Qdrant 的连接（HTTP 或 gRPC）"""
        if self._connected:
            return

        loop = asyncio.get_running_loop()

        def _connect_sync():
            from qdrant_client import QdrantClient as _QC

            host = self.config.host
            port = self.config.port
            timeout = self.config.timeout

            if self.config.prefer_grpc:
                try:
                    self._sync_client = _QC(
                        host=host,
                        port=self.config.grpc_port,
                        timeout=timeout,
                        prefer_grpc=True,
                        grpc_timeout=timeout,
                    )
                except Exception:
                    self._sync_client = _QC(host=host, port=port, timeout=timeout)
            else:
                self._sync_client = _QC(host=host, port=port, timeout=timeout)

            self._client = self._sync_client

        await loop.run_in_executor(None, _connect_sync)

        health = await loop.run_in_executor(
            None, lambda: bool(self._sync_client.info())
        )
        if not health:
            raise QdrantError("Qdrant health check failed")
        self._connected = True

    def _get_client(self):
        if not self._connected:
            raise RuntimeError("QdrantClient not connected. Call connect() first.")
        return self._client

    async def close(self):
        """关闭连接"""
        if self._sync_client:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._sync_client.close)
            self._connected = False
            self._client = None
            self._sync_client = None

    async def health_check(self) -> bool:
        """健康检查"""
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, lambda: bool(self._get_client().info())
            )
        except Exception:
            return False

    async def async_health_check(self) -> bool:
        return await self.health_check()

    # -------------------------
    # Collection 管理
    # -------------------------
    @property
    async def collection(self):
        """获取 collection（惰性初始化）"""
        await self._ensure_collection()
        return self

    async def _ensure_collection(self):
        """确保 collection 存在，不存在则创建"""
        if self._collection_initialized:
            return

        loop = asyncio.get_running_loop()

        def _do_check():
            client = self._get_client()
            colls = client.get_collections()
            names = [c.name for c in colls.collections]
            if self.config.collection_name in names:
                return

            distance_map = {
                "Cosine": "Cosine",
                "Dot": "Dot",
                "Euclid": "Euclid",
            }
            distance = distance_map.get(self.config.distance, "Cosine")

            from qdrant_client.models import SparseVectorParams

            client.create_collection(
                collection_name=self.config.collection_name,
                vectors_config={
                    "": qdrant_vectors_config(
                        size=self.config.vector_dim,
                        distance=distance,
                    )
                },
                sparse_vectors_config={
                    "sparse": SparseVectorParams(),
                },
                optimizers_config={
                    "indexing_threshold": 10000,
                    "memmap_threshold_kb": 200_000,
                    "on_disk": False,
                },
                hnsw_config={
                    "on_disk": False,
                    "m": 16,
                    "ef_construct": 200,
                },
            )

        try:
            await loop.run_in_executor(None, _do_check)
            self._collection_initialized = True
        except Exception as e:
            if "already exists" not in str(e).lower():
                raise QdrantError(f"Failed to create collection: {e}") from e
            self._collection_initialized = True

    # -------------------------
    # 向量检索
    # -------------------------
    async def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        filters: dict | None = None,
        score_threshold: float | None = None,
    ) -> list[RetrievedDoc]:
        """
        向量相似度检索（使用 Qdrant HTTP API）

        Args:
            query_vector: 查询向量
            top_k: 返回数量
            filters: 元数据过滤条件，支持 {'must': [...], 'should': [...], 'must_not': [...]}
            score_threshold: 最低相似度阈值（仅返回 >= 该分数的结果）
        """
        loop = asyncio.get_running_loop()

        def _do_search():
            import httpx

            host = self.config.host
            port = self.config.port
            base = f"http://{host}:{port}"

            payload = {
                "vector": query_vector,
                "limit": top_k,
                "with_payload": True,
            }
            if score_threshold is not None:
                payload["score_threshold"] = score_threshold

            q_filter = _build_filter(filters) if filters else None
            if q_filter:
                payload["filter"] = q_filter

            resp = httpx.post(
                f"{base}/collections/{self.config.collection_name}/points/search",
                json=payload,
                timeout=self.config.timeout,
            )
            resp.raise_for_status()
            hits = resp.json().get("result", [])

            return [
                RetrievedDoc(
                    content=payload_item.get("payload", {}).get("content", ""),
                    metadata={
                        "source": payload_item.get("payload", {}).get("source", ""),
                        "doc_type": payload_item.get("payload", {}).get("doc_type", ""),
                        "doc_id": payload_item.get("payload", {}).get("doc_id", 0),
                        "section_type": payload_item.get("payload", {}).get("section_type", "body"),
                        "markets": payload_item.get("payload", {}).get("markets", ["CN"]),
                        "lang": payload_item.get("payload", {}).get("lang", "zh"),
                        "hierarchy": payload_item.get("payload", {}).get("hierarchy", {}),
                    },
                    score=payload_item.get("score", 0.0),
                    source=payload_item.get("payload", {}).get("source", ""),
                )
                for payload_item in hits
            ]

        return await loop.run_in_executor(None, _do_search)

    # -------------------------
    # 稀疏向量检索（Qdrant 原生 hybrid search）
    # -------------------------
    async def search_sparse(
        self,
        query_sparse_vector: dict[int, float],
        top_k: int = 10,
        filters: dict | None = None,
        score_threshold: float | None = None,
    ) -> list[RetrievedDoc]:
        """
        稀疏向量检索（BM25 / SPLADE 风格）

        Args:
            query_sparse_vector: dict of {index: weight}，通常由外部 BM25/SPLADE 生成
        """
        loop = asyncio.get_running_loop()

        def _do_search():
            client = self._get_client()
            q_filter = _build_filter(filters) if filters else None

            from qdrant_client.models import SparseVector as _SV

            results = client.query_points(
                collection_name=self.config.collection_name,
                query=_SV(
                    indices=list(query_sparse_vector.keys()),
                    values=list(query_sparse_vector.values()),
                ),
                limit=top_k,
                query_filter=q_filter,
                score_threshold=score_threshold,
                with_payload=True,
                with_vectors=False,
            )

            return [
                RetrievedDoc(
                    content=payload.get("content", ""),
                    metadata={
                        "source": payload.get("source", ""),
                        "doc_type": payload.get("doc_type", ""),
                        "doc_id": payload.get("doc_id", 0),
                        "section_type": payload.get("section_type", "body"),
                        "markets": payload.get("markets", ["CN"]),
                        "lang": payload.get("lang", "zh"),
                        "hierarchy": payload.get("hierarchy", {}),
                    },
                    score=hit.score,
                    source=payload.get("source", ""),
                )
                for hit in results.points
                for payload in [hit.payload or {}]
            ]

        return await loop.run_in_executor(None, _do_search)

    async def search_hybrid(
        self,
        query_vector: list[float],
        query_sparse_vector: dict[int, float],
        top_k: int = 10,
        filters: dict | None = None,
        fusion: str = "rrf",
        alpha: float = 0.7,
    ) -> list[RetrievedDoc]:
        """
        Qdrant 原生混合检索（一次请求同时查 dense + sparse）

        fusion: "rrf" (默认) | "dbf" (distributed BF)
        alpha: dense 权重 (0~1)，sparse 权重 = 1-alpha
        """
        loop = asyncio.get_running_loop()

        def _do_search():
            client = self._get_client()
            q_filter = _build_filter(filters) if filters else None

            from qdrant_client.models import (
                FusionType,
                Prefetch,
                SparseVector,
            )

            fusion_map = {
                "rrf": FusionType.RRF,
                "dbf": FusionType.DBF,
            }
            fusion_type = fusion_map.get(fusion, FusionType.RRF)

            prefetch = [
                Prefetch(
                    query=query_vector,
                    filter=q_filter,
                    limit=top_k * 3,
                ),
                Prefetch(
                    query=SparseVector(
                        indices=list(query_sparse_vector.keys()),
                        values=list(query_sparse_vector.values()),
                    ),
                    filter=q_filter,
                    limit=top_k * 3,
                ),
            ]

            results = client.query_points(
                collection_name=self.config.collection_name,
                query=query_vector,
                limit=top_k,
                prefetch=prefetch,
                fusion=fusion_type,
                with_payload=True,
                with_vectors=False,
            )

            return [
                RetrievedDoc(
                    content=payload.get("content", ""),
                    metadata={
                        "source": payload.get("source", ""),
                        "doc_type": payload.get("doc_type", ""),
                        "doc_id": payload.get("doc_id", 0),
                        "section_type": payload.get("section_type", "body"),
                        "markets": payload.get("markets", ["CN"]),
                        "lang": payload.get("lang", "zh"),
                        "hierarchy": payload.get("hierarchy", {}),
                    },
                    score=hit.score,
                    source=payload.get("source", ""),
                )
                for hit in results.points
                for payload in [hit.payload or {}]
            ]

        return await loop.run_in_executor(None, _do_search)

    # -------------------------
    # 插入 / 更新
    # -------------------------
    async def insert(
        self,
        vectors: list[list[float]],
        contents: list[str],
        metadata: list[dict],
        doc_id: int,
        doc_type: str,
        source: str,
    ) -> list[str]:
        """批量插入向量数据"""
        loop = asyncio.get_running_loop()

        def _do_insert():
            client = self._get_client()
            self._ensure_collection_sync()

            from qdrant_client.models import PointStruct

            points = []
            for i, (vector, content) in enumerate(zip(vectors, contents)):
                meta = metadata[i] if i < len(metadata) else {}
                point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}_{i}"))
                points.append(
                    PointStruct(
                        id=point_id,
                        vector=vector,
                        payload={
                            "content": content,
                            "source": source,
                            "doc_type": doc_type,
                            "doc_id": doc_id,
                            "section_type": meta.get("section_type", "body"),
                            "markets": meta.get("markets", ["CN"]),
                            "lang": meta.get("lang", "zh"),
                            "hierarchy": meta.get("hierarchy", {}),
                        },
                    )
                )

            client.upsert(
                collection_name=self.config.collection_name,
                points=points,
                wait=True,
            )
            return [p.id for p in points]

        return await loop.run_in_executor(None, _do_insert)

    async def upsert_points(
        self,
        points: list[dict],
    ) -> int:
        """
        通用 upsert（支持稀疏向量）

        points 格式:
        [
            {
                "id": str,
                "vector": list[float],            # dense
                "sparse_vector": dict[int,float],  # 可选
                "payload": dict,
            }
        ]
        """
        loop = asyncio.get_running_loop()

        def _do_upsert():
            client = self._get_client()
            from qdrant_client.models import PointStruct, SparseVector

            converted = []
            for p in points:
                vec = p.get("vector", [])
                sparse = p.get("sparse_vector")
                if sparse:
                    from qdrant_client.models import SparseVector
                    vs = {"": vec, "sparse": SparseVector(indices=list(sparse.keys()), values=list(sparse.values()))}
                else:
                    vs = vec
                converted.append(
                    PointStruct(id=p["id"], vector=vs, payload=p.get("payload", {}))
                )

            client.upsert(collection_name=self.config.collection_name, points=converted, wait=True)
            return len(converted)

        return await loop.run_in_executor(None, _do_upsert)

    # -------------------------
    # 删除
    # -------------------------
    async def delete_by_doc_id(self, doc_id: int) -> int:
        """删除指定 doc_id 的所有向量"""
        loop = asyncio.get_running_loop()

        def _do_delete():
            client = self._get_client()
            from qdrant_client.models import Filter, FieldCondition, MatchValue, FilterSelector

            # scroll 只取 id，不取 payload/vector，最小化传输
            points, _ = client.scroll(
                collection_name=self.config.collection_name,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key="doc_id",
                            match=MatchValue(value=doc_id),
                        )
                    ]
                ),
                limit=10000,
                with_payload=False,
                with_vectors=False,
            )

            if not points:
                return 0

            # 直接用 filter delete，无需先 scroll 再逐 ID 删除
            client.delete(
                collection_name=self.config.collection_name,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[
                            FieldCondition(
                                key="doc_id",
                                match=MatchValue(value=doc_id),
                            )
                        ]
                    )
                ),
                wait=True,
            )
            return len(points)

        return await loop.run_in_executor(None, _do_delete)

    # -------------------------
    # 计数
    # -------------------------
    async def num_entities(self) -> int:
        """获取当前 collection 中的向量数量"""
        loop = asyncio.get_running_loop()

        def _do_count():
            client = self._get_client()
            count_info = client.count(
                collection_name=self.config.collection_name,
                exact=True,
            )
            return count_info.count

        try:
            return await loop.run_in_executor(None, _do_count)
        except Exception:
            return 0

    # -------------------------
    # Scroll 分页遍历（用于大规模数据导出/重建索引）
    # -------------------------
    async def scroll(
        self,
        filters: dict | None = None,
        limit: int = 1000,
        offset: str | None = None,
        with_vectors: bool = False,
    ) -> tuple[list[RetrievedDoc], str | None]:
        """
        Scroll API：高效分页遍历大量数据

        Returns:
            (docs, next_page_offset) - offset 为 None 时表示遍历完成
        """
        loop = asyncio.get_running_loop()

        def _do_scroll():
            client = self._get_client()
            q_filter = _build_filter(filters) if filters else None

            results, next_offset = client.scroll(
                collection_name=self.config.collection_name,
                filter=q_filter,
                limit=limit,
                offset=offset,
                with_payload=True,
                with_vectors=with_vectors,
            )

            docs = [
                RetrievedDoc(
                    content=p.payload.get("content", ""),
                    metadata={
                        "source": p.payload.get("source", ""),
                        "doc_type": p.payload.get("doc_type", ""),
                        "doc_id": p.payload.get("doc_id", 0),
                        "section_type": p.payload.get("section_type", "body"),
                        "markets": p.payload.get("markets", ["CN"]),
                        "lang": p.payload.get("lang", "zh"),
                        "hierarchy": p.payload.get("hierarchy", {}),
                    },
                    score=0.0,
                    source=p.payload.get("source", ""),
                )
                for p in results
            ]
            return docs, next_offset

        return await loop.run_in_executor(None, _do_scroll)

    async def scroll_all(
        self,
        filters: dict | None = None,
        batch_size: int = 1000,
    ) -> AsyncIterator[list[RetrievedDoc]]:
        """
        全量遍历 collection（自动分页），适合 ETL 或数据迁移

        Usage:
            async for batch in client.scroll_all(filters={"must": [...]}):
                process(batch)
        """
        offset = None
        while True:
            docs, offset = await self.scroll(
                filters=filters, limit=batch_size, offset=offset
            )
            if not docs:
                break
            yield docs
            if offset is None:
                break

    # -------------------------
    # 私有工具
    # -------------------------
    def _ensure_collection_sync(self):
        """同步版本 collection 初始化（供 insert 等方法内部调用）"""
        if self._collection_initialized:
            return

        def _do_check():
            client = self._client
            colls = client.get_collections()
            names = [c.name for c in colls.collections]
            if self.config.collection_name not in names:
                distance_map = {
                    "Cosine": "Cosine",
                    "Dot": "Dot",
                    "Euclid": "Euclid",
                }
                distance = distance_map.get(self.config.distance, "Cosine")
                try:
                    from qdrant_client.models import SparseVectorParams
                    client.create_collection(
                        collection_name=self.config.collection_name,
                        vectors_config={
                            "": qdrant_vectors_config(
                                size=self.config.vector_dim,
                                distance=distance,
                            )
                        },
                        sparse_vectors_config={
                            "sparse": SparseVectorParams(),
                        },
                        optimizers_config={
                            "indexing_threshold": 10000,
                        },
                    )
                except Exception as e:
                    if "already exists" not in str(e).lower():
                        raise

        _do_check()
        self._collection_initialized = True


# ---------------------------------------------------------------------------
# Filter 构造器
# ---------------------------------------------------------------------------
def _build_filter(filters: dict) -> Any:
    """
    将 Python dict 构建为 Qdrant Filter 表达式

    支持的格式:
    - 简单等值: {'doc_type': 'product'}  -> must [MatchValue]
    - 列表匹配: {'markets': ['CN', 'US']}  -> should [MatchAny]
    - 数值范围: {'doc_id': {'gte': 100}}  -> range [Range]
    - 组合: {
        'must': [{'field': 'lang', 'op': 'eq', 'value': 'zh'}],
        'should': [...],
        'must_not': [...]
      }
    """
    from qdrant_client.models import (
        FieldCondition,
        Filter,
        MatchAny,
        MatchText,
        MatchValue,
        Range,
        Should,
    )

    conditions: list = []

    # 顶层组合：must / should / must_not（与简写形式可同时生效）
    if "must" in filters:
        for cond in filters["must"]:
            conditions.append(_parse_condition(cond))
    if "should" in filters:
        should_conditions = [_parse_condition(c) for c in filters["should"]]
        conditions.append(Should(should=should_conditions, min_should_match=1))
    if "must_not" in filters:
        for cond in filters["must_not"]:
            conditions.append(_parse_condition(cond))

    # 简写形式：直接传字段值（跳过顶层组合 key）
    if not conditions:
        for key, value in filters.items():
            if key in ("must", "should", "must_not"):
                continue
            if isinstance(value, str):
                conditions.append(
                    FieldCondition(key=key, match=MatchValue(value=value))
                )
            elif isinstance(value, int):
                conditions.append(
                    FieldCondition(key=key, match=MatchValue(value=value))
                )
            elif isinstance(value, list):
                conditions.append(
                    FieldCondition(key=key, match=MatchAny(any=value))
                )
            elif isinstance(value, dict):
                conditions.append(_parse_range_condition(key, value))

    return Filter(must=conditions) if conditions else None


def _parse_condition(cond: dict) -> Any:
    """解析单个条件"""
    from qdrant_client.models import (
        FieldCondition,
        MatchAny,
        MatchValue,
        Range,
    )

    field = cond.get("field") or cond.get("key")
    op = cond.get("op", "eq")
    value = cond.get("value")

    if op == "eq" or op == "==":
        return FieldCondition(key=field, match=MatchValue(value=value))
    elif op == "ne" or op == "!=":
        return FieldCondition(key=field, match=MatchValue(value=value))
    elif op == "in":
        return FieldCondition(key=field, match=MatchAny(any=value))
    elif op == "range" or op in ("gte", "gt", "lte", "lt"):
        return FieldCondition(key=field, range=_parse_range_condition(field, cond))
    return FieldCondition(key=field, match=MatchValue(value=value))


def _parse_range_condition(field: str, cond: dict) -> Any:
    """解析数值范围条件"""
    from qdrant_client.models import Range

    range_kwargs = {}
    if "gte" in cond:
        range_kwargs["gte"] = cond["gte"]
    if "gt" in cond:
        range_kwargs["gt"] = cond["gt"]
    if "lte" in cond:
        range_kwargs["lte"] = cond["lte"]
    if "lt" in cond:
        range_kwargs["lt"] = cond["lt"]

    return Range(**range_kwargs) if range_kwargs else None


# ---------------------------------------------------------------------------
# 向量配置工厂（支持 named vectors）
# ---------------------------------------------------------------------------
def qdrant_vectors_config(
    size: int,
    distance: str = "Cosine",
) -> Any:
    """构建 Qdrant vectors_config（兼容 named / unnamed vectors）"""
    from qdrant_client.models import Distance, VectorParams

    distance_map = {
        "Cosine": Distance.COSINE,
        "Dot": Distance.DOT,
        "Euclid": Distance.EUCLID,
    }
    return VectorParams(size=size, distance=distance_map.get(distance, Distance.COSINE))
