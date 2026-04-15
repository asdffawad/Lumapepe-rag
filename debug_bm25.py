#!/usr/bin/env python3
"""调试BM25检索问题"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import asyncio
from app.config import get_config
from app.vectorstore.milvus_client import MilvusClient
from app.services.bm25_retriever import BM25Retriever

async def main():
    print("=== BM25 检索调试 ===\n")

    config = get_config()
    client = MilvusClient(config.milvus)
    await client.connect()

    # 1. 查询Milvus中的数据
    coll = await client.collection
    results = coll.query(expr="id != ''", output_fields=["id", "content", "source"], limit=5)
    print(f"1. Milvus查询样本 (共{await client.num_entities()}条):")
    for r in results[:3]:
        content_preview = r.get("content", "")[:60]
        print(f"   ID: {r.get('id', '')[:30]}")
        print(f"   Content: {content_preview}...")
        print(f"   Source: {r.get('source', '')}")
        print()

    # 2. 构建BM25索引
    print("2. 构建BM25索引...")
    bm25_retriever = BM25Retriever(client)
    await bm25_retriever._ensure_index()
    print(f"   索引文档数: {bm25_retriever.num_indexed}")

    # 3. 测试BM25检索
    print("\n3. BM25检索测试 (查询: '退款政策'):")
    bm25_results = await bm25_retriever.retrieve("退款政策", top_k=10)
    print(f"   召回结果数: {len(bm25_results)}")
    if bm25_results:
        for i, doc in enumerate(bm25_results[:3], 1):
            print(f"   [{i}] {doc.content[:50]}... score={doc.score:.4f}")
    else:
        print("   ⚠️  BM25召回为空！检查索引内容...")
        # 检查索引内部的corpus
        if bm25_retriever._indexer and bm25_retriever._indexer._corpus:
            print(f"   索引corpus大小: {len(bm25_retriever._indexer._corpus)}")
            print(f"   第一条corpus: {bm25_retriever._indexer._corpus[0][:60] if bm25_retriever._indexer._corpus else 'empty'}")

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
