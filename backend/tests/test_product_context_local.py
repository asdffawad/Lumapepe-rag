# -*- coding: utf-8 -*-
"""本地测试商品上下文服务"""
import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "backend"))


async def test_product_context_service():
    """测试商品上下文服务"""
    print("=" * 60)
    print("[TEST] 商品上下文服务测试")
    print("=" * 60)

    try:
        from app.services.product_context_service import ProductContextService
        from app.database.connection import get_db_pool

        # 创建服务实例
        pool = await get_db_pool()
        service = ProductContextService(pool)

        print("\n[TEST 1] 通过SPU ID查询")
        print("-" * 40)
        spu_context = await service.get_product_context(spu_id=753)
        if spu_context:
            print(f"  [OK] 找到商品:")
            print(f"    名称: {spu_context.get('name', 'N/A')}")
            print(f"    类型: {spu_context.get('product_type', 'N/A')}")
            print(f"    价格: ${spu_context.get('price', 'N/A')}" if spu_context.get('price') else f"    价格: N/A")
            print(f"    库存: {spu_context.get('stock', 'N/A')}")
            print(f"    关键词: {spu_context.get('keyword', 'N/A')}")
            print(f"    简介: {spu_context.get('introduction', 'N/A')[:100]}..." if spu_context.get('introduction') else f"    简介: N/A")
        else:
            print(f"  [FAIL] 未找到商品")

        print("\n[TEST 2] 系统提示格式化")
        print("-" * 40)
        if spu_context:
            system_prompt = service.format_system_prompt(spu_context)
            print(f"  [格式化结果]")
            print("  " + "-" * 38)
            for line in system_prompt.split("\n"):
                print(f"  {line}")
            print("  " + "-" * 38)

        print("\n[TEST 3] 通过SKU ID查询")
        print("-" * 40)
        # 查询第一个SKU
        from app.database.product import ProductRepository
        repo = ProductRepository(pool)
        skus = await repo.get_all_sku()
        if skus:
            first_sku = skus[0]
            print(f"  测试SKU ID: {first_sku.id}, SPU ID: {first_sku.spu_id}")
            sku_context = await service.get_product_context(sku_id=first_sku.id)
            if sku_context:
                print(f"  [OK] 找到SKU商品:")
                print(f"    名称: {sku_context.get('name', 'N/A')}")
                print(f"    SKU价格: ${sku_context.get('sku_price', 'N/A')}" if sku_context.get('sku_price') else f"    SKU价格: N/A")
                print(f"    条形码: {sku_context.get('bar_code', 'N/A')}")
                print(f"    规格属性: {sku_context.get('properties', 'N/A')}")
            else:
                print(f"  [FAIL] 未找到SKU商品")
        else:
            print(f"  [SKIP] 数据库中没有SKU数据")

        pool.close()
        await pool.wait_closed()

        print("\n" + "=" * 60)
        print("[OK] 商品上下文服务测试完成!")
        print("=" * 60)

    except Exception as e:
        print(f"\n[ERROR] 测试失败: {e}")
        import traceback
        traceback.print_exc()


async def main():
    print("\n" + "=" * 60)
    print("商品上下文传递功能 - 本地测试")
    print("=" * 60)

    await test_product_context_service()

    print("\n" + "=" * 60)
    print("前端集成说明")
    print("=" * 60)
    print("""
当用户从商品详情页点击AI客服时，前端应：

1. 构建WebSocket URL，携带商品ID参数:
   ws://localhost:8000/ws/chat?token=<JWT>&sku_id=753
   或
   ws://localhost:8000/ws/chat?token=<JWT>&spu_id=753

2. 连接成功后，系统会自动：
   - 在'connected'消息中返回商品基本信息
   - 在对话开始时注入商品详情作为系统提示

3. 用户提问时，AI已了解商品背景:
   - 商品名称、价格、库存
   - 商品简介、详情描述
   - SKU规格属性（如果有）

4. 示例响应:
   'connected' 消息:
   {
     "type": "connected",
     "session_id": "...",
     "product_context": {
       "product_name": "Large 70.87in Cat Tree...",
       "product_type": "SKU",
       "sku_id": 753,
       "spu_id": 123,
       "price": 80.88
     }
   }
""")


if __name__ == "__main__":
    asyncio.run(main())
