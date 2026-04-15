#!/usr/bin/env python3
import sys
sys.path.insert(0, "/app/backend")
import asyncio
import aiohttp

async def test_api():
    url = "http://localhost:8000/api/v1/debug/retrieve"
    payload = {
        "query": "退款政策",
        "top_k": 10,
        "rerank_top_k": 5,
        "hybrid_search": True,
        "bm25_weight": 0.3,
        "use_llm_rerank": False,
        "intent": "product_qa"
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            result = await resp.json()
            stages = result.get("stages", {})

            print("=" * 60)
            print("混合检索API测试结果")
            print("=" * 60)
            print(f"\n1. Dense检索: {stages.get('dense', {}).get('count', 0)} 条")
            print(f"2. BM25检索: {stages.get('bm25', {}).get('count', 0)} 条")
            print(f"3. RRF融合: {stages.get('rrf', {}).get('count', 0)} 条")
            print(f"4. Rerank后: {stages.get('rerank', {}).get('count', 0)} 条")

            print("\nTop 5 Rerank结果:")
            for i, item in enumerate(stages.get("rerank", {}).get("items", [])[:5], 1):
                print(f"{i}. {item.get('content', '')[:60]}...")
                print(f"   分数: {item.get('rerank_score', 0):.4f}")

            print(f"\n总耗时: {result.get('total_latency_ms', 0):.1f}ms")

asyncio.run(test_api())
