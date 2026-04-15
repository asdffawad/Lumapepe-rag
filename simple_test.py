#!/usr/bin/env python3
import sys
sys.path.insert(0, "/app/backend")
import asyncio
from app.config import get_config
from app.vectorstore.milvus_client import MilvusClient
from app.services.bm25_retriever import BM25Retriever

async def test():
    config = get_config()
    client = MilvusClient(config.milvus)
    await client.connect()

    # 查询Milvus数据
    coll = await client.collection
    results = coll.query(expr="id != ''", output_fields=["id", "content", "source"], limit=5)
    print(f"Milvus entities: {await client.num_entities()}")
    print(f"Sample query results: {len(results)}")
    if results:
        print(f"First content: {results[0].get('content','')[:60]}")

    # BM25检索
    retriever = BM25Retriever(client)
    await retriever._ensure_index()
    print(f"BM25 indexed: {retriever.num_indexed}")

    bm25_results = await retriever.retrieve("退款政策", top_k=10)
    print(f"BM25 search results: {len(bm25_results)}")

    await client.disconnect()

asyncio.run(test())
