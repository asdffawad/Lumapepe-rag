#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""快速检查商品数据库中的数据"""
import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "backend"))

async def main():
    from app.database.connection import get_db_pool
    from app.database.product import ProductRepository
    from app.services.product_info_parser import parse_product_info

    print("=" * 60)
    print("商品数据库查询工具")
    print("=" * 60)

    try:
        pool = await get_db_pool()
        repo = ProductRepository(pool)

        # 查询SPU统计
        spus = await repo.get_all_spu()
        print(f"\n[数据库状态]")
        print(f"  SPU总数: {len(spus)}")
        print(f"  SKU总数: {len(await repo.get_all_sku())}")

        if spus:
            # 显示前3个SPU
            print(f"\n[可用的SPU示例]")
            for i, spu in enumerate(spus[:3], 1):
                print(f"  {i}. ID: {spu.id}, 名称: {spu.name[:50]}")
                print(f"     价格: ${spu.price if spu.price else 'N/A'}, 库存: {spu.stock}")

        # 查询SKU统计
        skus = await repo.get_all_sku()
        if skus:
            print(f"\n[可用的SKU示例]")
            for i, sku in enumerate(skus[:3], 1):
                print(f"  {i}. SKU ID: {sku.id}, SPU ID: {sku.spu_id}, 价格: ${sku.price if sku.price else 'N/A'}")

        # 测试SPU上下文获取
        if spus:
            test_spu_id = spus[0].id
            print(f"\n[测试] 获取SPU上下文 (ID={test_spu_id})")
            print("-" * 40)
            from app.services.product_context_service import ProductContextService
            svc = ProductContextService(pool)
            ctx = await svc.get_product_context(spu_id=test_spu_id)
            if ctx:
                print(f"  商品名称: {ctx.get('product_name')}")
                print(f"  商品类型: {ctx.get('product_type')}")
                print(f"  价格: ${ctx.get('price')}" if ctx.get('price') else "  价格: N/A")
                print(f"  库存: {ctx.get('stock')}" if ctx.get('stock') is not None else "  库存: N/A")
                print(f"  简介长度: {len(ctx.get('introduction', ''))}")
                print(f"  详情长度: {len(ctx.get('description', ''))}")
            else:
                print("  [失败] 无法获取商品上下文")

        # 测试SKU上下文获取
        if skus:
            test_sku = skus[0]
            test_sku_id = test_sku.id
            print(f"\n[测试] 获取SKU上下文 (ID={test_sku_id})")
            print("-" * 40)
            ctx = await svc.get_product_context(sku_id=test_sku_id)
            if ctx:
                print(f"  商品名称: {ctx.get('product_name')}")
                print(f"  商品类型: {ctx.get('product_type')}")
                print(f"  SKU价格: ${ctx.get('sku_price')}" if ctx.get('sku_price') else "  SKU价格: N/A")
                print(f"  条形码: {ctx.get('bar_code', 'N/A')}")
                print(f"  规格属性: {ctx.get('properties', 'N/A')}")
            else:
                print("  [失败] 无法获取SKU上下文")

        pool.close()
        await pool.wait_closed()

        print("\n" + "=" * 60)
        print("测试完成！")
        print("=" * 60)

    except Exception as e:
        print(f"[错误] {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
