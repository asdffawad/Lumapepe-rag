#!/usr/bin/env python3
"""
Milvus 初始化脚本
创建 knowledge_base collection 和索引
"""
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from pymilvus import connections, Collection, CollectionSchema, DataType, FieldSchema, utility


def init_milvus(host: str = "localhost", port: int = 19530, collection_name: str = "knowledge_base"):
    """初始化 Milvus collection"""
    print(f"[Milvus Init] Connecting to {host}:{port}...")

    alias = "default"
    connections.connect(alias=alias, host=host, port=port,
        channel_kwargs={"grpc.max_receive_message_length": 32*1024*1024})

    name = collection_name

    # 如果已存在，删除重建（可选，这里改为跳过已存在）
    if utility.has_collection(name):
        collection = Collection(name)
        stats = collection.num_entities
        print(f"[Milvus Init] Collection '{name}' already exists with {stats} entities.")
        connections.disconnect(alias=alias)
        return

    print(f"[Milvus Init] Creating collection '{name}'...")

    fields = [
        FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=128, is_primary=True),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=1024),
        FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=4096),
        FieldSchema(name="doc_id", dtype=DataType.INT64),
        FieldSchema(name="chunk_index", dtype=DataType.INT64),
        FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=256),
        FieldSchema(name="doc_type", dtype=DataType.VARCHAR, max_length=64),
    ]

    schema = CollectionSchema(fields=fields, description="RAG Knowledge Base - Production Ready")

    collection = Collection(name=name, schema=schema)
    print(f"[Milvus Init] Collection '{name}' created.")

    # 创建向量索引
    print(f"[Milvus Init] Creating IVF_FLAT index on 'vector' field...")
    index_params = {"index_type": "IVF_FLAT", "metric_type": "IP", "params": {"nlist": 128}}
    collection.create_index(field_name="vector", index_params=index_params)

    # 加载 collection 到内存
    print(f"[Milvus Init] Loading collection into memory...")
    collection.load()

    print(f"[Milvus Init] Done. Collection '{name}' is ready.")
    connections.disconnect(alias=alias)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Initialize Milvus collection")
    parser.add_argument("--host", default=os.getenv("MILVUS_HOST", "localhost"), help="Milvus host")
    parser.add_argument("--port", type=int, default=int(os.getenv("MILVUS_PORT", "19530")), help="Milvus port")
    parser.add_argument("--collection", default=os.getenv("MILVUS_COLLECTION", "knowledge_base"), help="Collection name")

    args = parser.parse_args()

    try:
        init_milvus(host=args.host, port=args.port, collection_name=args.collection)
    except Exception as e:
        print(f"[Milvus Init] ERROR: {e}", file=sys.stderr)
        sys.exit(1)
