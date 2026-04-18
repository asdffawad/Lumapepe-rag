#!/usr/bin/env python
"""调试SKU查询"""
import asyncio
import sys
sys.path.insert(0, 'backend')

from app.database.connection import create_db_pool, close_db_pool
from app.services.product_context_service import ProductContextService

async def test():
    try:
        pool = await create_db_pool({
            'host': 'localhost',
            'port': 3307,
            'user': 'root',
            'password': 'rag2026',
            'database': 'rag_customer_service',
        })
        
        svc = ProductContextService(pool)
        
        # 测试SKU查询
        print("测试 SKU 209:")
        result = await svc.get_product_context(sku_id=209)
        print(f"结果: {result}")
        
        # 测试SPU查询
        print("\n测试 SPU 753:")
        result = await svc.get_product_context(spu_id=753)
        print(f"结果: {result is not None}")
        if result:
            print(f"商品名称: {result.get('product_name')}")
            print(f"has_images: {result.get('has_images')}")
            print(f"has_html: {result.get('has_html')}")
        
        await close_db_pool()
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(test())
