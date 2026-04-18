# -*- coding: utf-8 -*-
"""测试本地MySQL连接和商品字典构建"""
import asyncio
import aiomysql
import sys

async def test_connection():
    print("=" * 50)
    print("1. 测试MySQL连接")
    print("=" * 50)
    
    try:
        pool = await aiomysql.create_pool(
            host='localhost',
            port=3306,
            user='root',
            password='123456',
            db='lumapepe',
            minsize=1,
            maxsize=5,
            charset='utf8mb4'
        )
        print(" MySQL连接成功!")
        
        # 测试查询
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # 查询SPU数量
                await cur.execute("SELECT COUNT(*) as cnt FROM product_spu")
                spu_count = await cur.fetchone()
                print(f"   SPU数量: {spu_count[0]}")
                
                # 查询SKU数量
                await cur.execute("SELECT COUNT(*) as cnt FROM product_sku")
                sku_count = await cur.fetchone()
                print(f"   SKU数量: {sku_count[0]}")
                
                # 查询有条形码的SKU
                await cur.execute("SELECT COUNT(*) as cnt FROM product_sku WHERE bar_code IS NOT NULL AND bar_code != ''")
                sku_with_barcode = await cur.fetchone()
                print(f"   有条形码的SKU: {sku_with_barcode[0]}")
                
                # 样例数据
                await cur.execute("SELECT id, name, price, stock FROM product_spu LIMIT 3")
                samples = await cur.fetchall()
                print(f"\n   SPU样例:")
                for s in samples:
                    print(f"   - ID: {s[0]}, 名称: {s[1][:30]}..., 价格: {s[2]}, 库存: {s[3]}")
        
        pool.close()
        await pool.wait_closed()
        print("\n 数据库连接测试完成!")
        return True
        
    except Exception as e:
        print(f" MySQL连接失败: {e}")
        return False


async def build_dict():
    print("\n" + "=" * 50)
    print("2. 构建商品字典")
    print("=" * 50)
    
    try:
        from app.database.product import ProductRepository
        from app.etl.dict_builder import build_product_dict, save_dict
        from pathlib import Path
        
        pool = await aiomysql.create_pool(
            host='localhost',
            port=3306,
            user='root',
            password='123456',
            db='lumapepe',
            minsize=2,
            maxsize=5,
            charset='utf8mb4'
        )
        
        # 构建字典
        product_dict = await build_product_dict(
            pool,
            output_path="data/product_dict.json",
            save=True,
        )
        
        print(f" 字典构建成功!")
        print(f"   版本: {product_dict['version']}")
        print(f"   SKU数量: {product_dict['stats']['sku_count']}")
        print(f"   SPU数量: {product_dict['stats']['spu_count']}")
        print(f"   别名数量: {product_dict['stats']['alias_count']}")
        print(f"   SKU字典大小: {len(product_dict['sku_dict'])}")
        print(f"   SPU字典大小: {len(product_dict['spu_dict'])}")
        print(f"   别名字典大小: {len(product_dict['alias_dict'])}")
        
        pool.close()
        await pool.wait_closed()
        return True
        
    except Exception as e:
        import traceback
        print(f" 字典构建失败: {e}")
        traceback.print_exc()
        return False


async def test_product_query():
    print("\n" + "=" * 50)
    print("3. 测试商品查询工具")
    print("=" * 50)
    
    try:
        from app.database.product import ProductRepository
        from app.etl.dict_loader import ProductDictLoader
        from app.services.resolver.product_resolver import ProductResolver
        
        # 加载字典
        loader = ProductDictLoader("data/product_dict.json")
        await loader.load()
        product_dict = await loader.get()
        print(f" 字典加载成功")
        
        # 创建解析器
        resolver = ProductResolver(product_dict)
        print(f" 解析器创建成功")
        
        # 测试查询
        test_queries = [
            "cat tree",
            "dog ramp",
            "hammock",
            "pet bed",
        ]
        
        for query in test_queries:
            result = await resolver.resolve(query)
            if result.is_resolved:
                print(f"\n   查询 '{query}':")
                print(f"    匹配成功! SPU ID: {result.spu_id}, 类型: {result.match_type.value}, 置信度: {result.confidence:.2f}")
            else:
                print(f"\n   查询 '{query}':")
                print(f"    未匹配")
        
        return True
        
    except Exception as e:
        import traceback
        print(f" 商品查询测试失败: {e}")
        traceback.print_exc()
        return False


async def main():
    print("\n" + "=" * 60)
    print("[TEST] 商品查询功能本地测试")
    print("=" * 60)
    
    # 1. 测试连接
    if not await test_connection():
        print("\n数据库连接失败，测试终止。")
        return
    
    # 2. 构建字典
    if not await build_dict():
        print("\n字典构建失败，测试终止。")
        return
    
    # 3. 测试查询
    await test_product_query()
    
    print("\n" + "=" * 60)
    print("[OK] 所有测试完成!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
