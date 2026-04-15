#!/usr/bin/env python3
"""测试混合检索效果"""
import asyncio
import sys
from pathlib import Path

# 添加路径
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.config import get_config
from app.vectorstore.milvus_client import MilvusClient
from app.services.bm25_retriever import BM25Retriever
from app.services.vector_retriever import HybridRetriever

async def main():
    print("=" * 60)
    print("混合检索测试")
    print("=" * 60)

    # 初始化配置和客户端
    config = get_config()
    client = MilvusClient(config.milvus)
    await client.connect()

    # 检查Milvus文档数
    total = await client.num_entities()
    print(f"\n1. Milvus知识库状态:")
    print(f"   总向量数: {total}")

    # 测试BM25检索（惰性加载）
    print(f"\n2. BM25 稀疏检索测试:")
    bm25_retriever = BM25Retriever(client)
    bm25_docs = await bm25_retriever.retrieve("退款政策", top_k=10)
    print(f"   BM25召回数: {len(bm25_docs)}")
    if bm25_docs:
        print(f"   最高分文档: {bm25_docs[0].content[:80]}")
        print(f"   最高分: {bm25_docs[0].score:.4f}")

    # 测试Hybrid检索
    print(f"\n3. Hybrid混合检索测试 (Dense top_k*2=20 + BM25 top_k*2=20):")
    hybrid = HybridRetriever(client)
    hybrid_docs = await hybrid.retrieve("退款政策", top_k=10, filters=None)
    print(f"   最终召回数: {len(hybrid_docs)}")
    if hybrid_docs:
        print(f"\n   前3条结果:")
        for i, doc in enumerate(hybrid_docs[:3], 1):
            print(f"   [{i}] {doc.content[:60]}...")
            print(f"       来源: {doc.source}")
            print(f"       RRF分数: {doc.score:.4f}")
            print(f"       Dense分: {doc.metadata.get('dense_score', 0):.4f}")
            print(f"       BM25分: {doc.metadata.get('bm25_score', 0):.4f}")

    await client.disconnect()
    print("\n✅ 测试完成")

if __name__ == "__main__":
    asyncio.run(main())
