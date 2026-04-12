#!/usr/bin/env python3
import sys
sys.path.insert(0, '/app')

from backend.app.config import get_config
from backend.app.vectorstore.milvus_client import MilvusClient
from backend.app.services.vector_retriever import SimpleVectorRetriever
from backend.app.services.query_rewriter import EcommerceQueryRewriter
import asyncio

async def main():
    config = get_config()
    mc = MilvusClient(config.milvus)
    await mc.connect()
    
    retriever = SimpleVectorRetriever(mc)
    rewriter = EcommerceQueryRewriter()
    
    queries = [
        "美国退货政策",
        "美国退货",
        "美国退换货",
        "美国 退款",
        "lumapepe",
        "Luma Pepe是什么",
    ]
    
    for query in queries:
        print(f"\n{'='*60}")
        print(f"Query: {query}")
        
        # Query rewriting
        rewritten_query = await rewriter.rewrite(query, [])
        rewritten = rewritten_query.rewritten
        print(f"Intent: {rewritten_query.intent}, Rewritten: {rewritten}")
        print(f"Expanded: {rewritten_query.expanded_terms}")
        
        # Retrieval
        chunks = await retriever.retrieve(rewritten, top_k=15)
        print(f"Retrieved {len(chunks)} chunks:")
        for i, chunk in enumerate(chunks[:10]):
            print(f"  [{i+1}] score={chunk.score:.4f} | {chunk.content[:120]}")

asyncio.run(main())
