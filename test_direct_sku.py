#!/usr/bin/env python
"""在Docker容器中测试get_sku_with_spu"""
import sys
import asyncio
from aiomysql import create_pool

async def test():
    try:
        pool = await create_pool(
            host='localhost',
            port=3307,
            user='root',
            password='rag2026',
            db='rag_customer_service',
            autocommit=True
        )
        
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT
                        s.id as sku_id, s.spu_id, s.bar_code, s.properties,
                        s.price as sku_price, s.stock as sku_stock, s.pic_url,
                        s.created_at as sku_created_at, s.updated_at as sku_updated_at,
                        p.id as spu_id, p.name, p.keyword, p.introduction,
                        p.description, p.category_id, p.brand_id,
                        p.price as spu_price, p.stock as spu_stock,
                        p.created_at as spu_created_at, p.updated_at as spu_updated_at
                    FROM product_sku s
                    LEFT JOIN product_spu p ON s.spu_id = p.id
                    WHERE s.id = %s
                """, (209,))
                row = await cur.fetchone()
                print(f"查询结果: {row}")
                
                if row:
                    print(f"SKU ID: {row[0]}, SPU ID: {row[1]}, SPU Name: {row[8]}")
                else:
                    print("没有找到记录")
        
        pool.close()
        await pool.wait_closed()
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(test())
