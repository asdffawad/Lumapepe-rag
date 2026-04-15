#!/usr/bin/env python3
import sys
sys.path.insert(0, "/app/backend")
import asyncio
from app.config import get_config
from app.vectorstore.milvus_client import MilvusClient
from app.services.vector_retriever import HybridRetriever

async def test():
    config = get_config()
    client = MilvusClient(config.milvus)
    await client.connect()

    print("=" * 60)
    print("混合检索完整测试")
    print("=" * 60)
    print(f"Milvus实体总数: {await client.num_entities()}")

    hybrid = HybridRetriever(client)
    docs = await hybrid.retrieve('退款政策', top_k=10, filters=None)

    print(f"\n混合检索结果数: {len(docs)}")
    print(f"\n前5条结果详情:")
    for i, d in enumerate(docs[:5], 1):
        print(f"{i}. {d.content[:60]}...")
        print(f"   来源: {d.source}")
        print(f"   RRF分数: {d.score:.4f}")
        print(f"   Dense分: {d.metadata.get('dense_score',0):.4f} (rank={d.metadata.get('dense_rank',0)})")
        print(f"   BM25分: {d.metadata.get('bm25_score',0):.4f} (rank={d.metadata.get('bm25_rank',0)})")
        print()

    await client.disconnect()

asyncio.run(test())
