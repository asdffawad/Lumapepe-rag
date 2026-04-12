"""
Milvus 向量数据库客户端（适配 milvus-lite 本地模式）
当 MILVUS_HOST 不可用时自动降级到 MockVectorRetriever
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import MilvusConfig
from app.services.vector_retriever import RetrievedDoc


class MilvusClient:
    """
    milvus-lite 是 pymilvus 内置的本地向量数据库：
    - 无需外部服务（etcd/minio）
    - 数据存储在本地文件
    - 适合开发测试和单机部署
    - 端口 19530 由 milvus-lite 服务进程监听
    """

    _instance: "MilvusClient | None" = None

    def __init__(self, config: MilvusConfig):
        self.config = config
        self._alias = "default"
        self._collection = None
        self._search_params: dict | None = None  # 动态索引参数（HNSW/IVF_FLAT）

    async def connect(self):
        """异步连接 Milvus（在 FastAPI startup 事件中调用）"""
        import asyncio

        def _sync_connect():
            from pymilvus import connections
            channel_kwargs = {
                "grpc.max_receive_message_length": 32 * 1024 * 1024,  # 32MB
                "grpc.max_send_message_length": 32 * 1024 * 1024,
            }
            connections.connect(
                alias=self._alias,
                host=self.config.host,
                port=self.config.port,
                channel_kwargs=channel_kwargs,
            )
        await asyncio.to_thread(_sync_connect)

    @property
    async def collection(self):
        if self._collection is None:
            await self._ensure_collection()
        return self._collection

    async def _ensure_collection(self):
        import asyncio

        def _sync_ensure():
            from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, utility
            name = self.config.collection_name
            if utility.has_collection(name):
                self._collection = Collection(name)
                # 复用已有 collection 时，从索引信息推断 search_params
                indexes = self._collection.indexes
                if indexes:
                    idx = indexes[0]
                    if idx.params.get("index_type") == "HNSW":
                        self._search_params = {"metric_type": "IP", "params": {"ef": 128}}
                    else:
                        self._search_params = {"metric_type": "IP", "params": {"nprobe": 32}}
                else:
                    self._search_params = {"metric_type": "IP", "params": {"nprobe": 32}}
                return

            fields = [
                FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=128, is_primary=True),
                FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=1024),
                FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=4096),
                FieldSchema(name="doc_id", dtype=DataType.INT64),
                FieldSchema(name="chunk_index", dtype=DataType.INT64),
                FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=256),
                FieldSchema(name="doc_type", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="section_type", dtype=DataType.VARCHAR, max_length=32),
                FieldSchema(name="markets", dtype=DataType.VARCHAR, max_length=128),
                FieldSchema(name="lang", dtype=DataType.VARCHAR, max_length=8),
                FieldSchema(name="hierarchy", dtype=DataType.VARCHAR, max_length=256),
            ]
            schema = CollectionSchema(fields=fields, description="RAG Knowledge Base - v2")
            collection = Collection(name=name, schema=schema)

            # 自动选择索引类型：HNSW（<100万向量，高召回）/ IVF_FLAT（超大规模）
            entity_hint = getattr(self.config, "entity_count_hint", 1_000_000)
            if entity_hint < 1_000_000:
                index_params = {
                    "index_type": "HNSW",
                    "metric_type": "IP",
                    "params": {"M": 16, "efConstruction": 200},
                }
                self._search_params = {"metric_type": "IP", "params": {"ef": 128}}
            else:
                index_params = {
                    "index_type": "IVF_FLAT",
                    "metric_type": "IP",
                    "params": {"nlist": 1024},
                }
                self._search_params = {"metric_type": "IP", "params": {"nprobe": 32}}

            collection.create_index(field_name="vector", index_params=index_params)
            collection.load()
            self._collection = collection

        await asyncio.to_thread(_sync_ensure)

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        filters: dict | None = None,
    ) -> list[RetrievedDoc]:
        """向量检索，支持 metadata 字段过滤。"""
        import asyncio

        async def _search():
            coll = await self.collection

            def _sync_search():
                import json
                expr = self._build_filter_expr(filters)
                sp = self._search_params or {"metric_type": "IP", "params": {"nprobe": 32}}
                results = coll.search(
                    data=[query_vector],
                    anns_field="vector",
                    param=sp,
                    limit=top_k,
                    expr=expr,
                    output_fields=[
                        "content", "source", "doc_type", "doc_id",
                        "section_type", "markets", "lang", "hierarchy",
                    ],
                )
                docs = []
                for hits in results:
                    for hit in hits:
                        markets_raw = hit.fields.get("markets", "[]")
                        hierarchy_raw = hit.fields.get("hierarchy", "{}")
                        try:
                            markets = json.loads(markets_raw) if markets_raw else ["CN"]
                            hierarchy = json.loads(hierarchy_raw) if hierarchy_raw else {}
                        except Exception:
                            markets = ["CN"]
                            hierarchy = {}
                        docs.append(
                            RetrievedDoc(
                                content=hit.fields.get("content", ""),
                                metadata={
                                    "source": hit.fields.get("source", ""),
                                    "doc_type": hit.fields.get("doc_type", ""),
                                    "doc_id": hit.fields.get("doc_id", 0),
                                    "section_type": hit.fields.get("section_type", "body"),
                                    "markets": markets,
                                    "lang": hit.fields.get("lang", "zh"),
                                    "hierarchy": hierarchy,
                                },
                                score=hit.score,
                                source=hit.fields.get("source", ""),
                            )
                        )
                return docs

            return await asyncio.to_thread(_sync_search)

        return await _search()

    def _build_filter_expr(self, filters: dict | None) -> str | None:
        """将 filters dict 构建为 Milvus 表达式，支持 section_type / lang / markets."""
        if not filters:
            return None

        expr_parts = []
        for key, value in filters.items():
            if isinstance(value, str):
                if key == "section_type":
                    expr_parts.append(f'(section_type == "{value}")')
                elif key == "lang":
                    expr_parts.append(f'(lang == "{value}")')
                elif key == "doc_type":
                    expr_parts.append(f'(doc_type == "{value}")')
                else:
                    expr_parts.append(f'(doc_type == "{value}")')
            elif isinstance(value, int):
                expr_parts.append(f'(doc_id == {value})')
            elif isinstance(value, list):
                if key == "markets":
                    for m in value:
                        expr_parts.append(f'(markets like "%{m}%")')
                elif key == "doc_id":
                    items = ", ".join(str(v) for v in value)
                    expr_parts.append(f'(doc_id in [{items}])')
                else:
                    items = ", ".join(f'"{v}"' for v in value)
                    expr_parts.append(f'(doc_type in [{items}])')
        return " and ".join(expr_parts) if expr_parts else None

    async def insert(
        self,
        vectors: list[list[float]],
        contents: list[str],
        metadata: list[dict],
        doc_id: int,
        doc_type: str,
        source: str,
    ) -> list[str]:
        import asyncio
        import json

        async def _insert():
            coll = await self.collection

            def _sync_insert():
                ids = [f"{doc_id}_{i}" for i in range(len(vectors))]
                section_types = []
                markets_list = []
                langs = []
                hierarchies = []
                for m in metadata:
                    section_types.append(m.get("section_type", "body") if isinstance(m, dict) else "body")
                    markets_list.append(json.dumps(m.get("markets", ["CN"])) if isinstance(m, dict) else '["CN"]')
                    langs.append(m.get("lang", "zh") if isinstance(m, dict) else "zh")
                    hierarchies.append(json.dumps(m.get("hierarchy", {})) if isinstance(m, dict) else "{}")
                data = [
                    ids,
                    vectors,
                    contents,
                    [doc_id] * len(vectors),
                    list(range(len(vectors))),
                    [source] * len(vectors),
                    [doc_type] * len(vectors),
                    section_types,
                    markets_list,
                    langs,
                    hierarchies,
                ]
                coll.insert(data)
                coll.flush()
                return ids

            return await asyncio.to_thread(_sync_insert)

        return await _insert()

    def health_check(self) -> bool:
        """健康检查：测试 Milvus 连接是否可用"""
        try:
            from pymilvus import connections
            return connections.has_connection(self._alias)
        except Exception:
            return False

    async def async_health_check(self) -> bool:
        """异步健康检查（避免阻塞事件循环）"""
        import asyncio
        try:
            from pymilvus import connections
            def _check():
                return connections.has_connection(self._alias)
            return await asyncio.to_thread(_check)
        except Exception:
            return False

    async def auto_reconnect(self):
        """异步重连 Milvus"""
        self.close()
        self._collection = None
        await self.connect()

    def close(self):
        from pymilvus import connections
        connections.disconnect(alias=self._alias)

    async def delete_by_doc_id(self, doc_id: int) -> int:
        """
        删除指定 doc_id 关联的所有向量 chunk。

        Returns:
            删除的向量数量
        """
        import asyncio

        async def _delete():
            coll = await self.collection

            def _sync_delete():
                # Milvus VARCHAR 主键格式为 "{doc_id}_{chunk_index}"
                # 使用前缀匹配删除所有该 doc_id 的 chunks
                prefix = f"{doc_id}_"
                # 先查询要删除的 ID 列表
                results = coll.query(
                    expr=f'id like "{prefix}%"',
                    output_fields=["id"],
                )
                if not results:
                    return 0
                ids_to_delete = [r["id"] for r in results]
                coll.delete(expr=f'id in {ids_to_delete}')
                coll.flush()
                return len(ids_to_delete)

            return await asyncio.to_thread(_sync_delete)

        return await _delete()

    async def num_entities(self) -> int:
        """获取当前 collection 中的向量数量"""
        import asyncio

        async def _get():
            coll = await self.collection
            return coll.num_entities

        try:
            return await _get()
        except Exception:
            return 0
