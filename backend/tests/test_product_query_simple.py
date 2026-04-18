# -*- coding: utf-8 -*-
"""测试本地商品查询功能"""
import asyncio
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "backend"))

async def main():
    print("=" * 60)
    print("[TEST] 商品查询功能本地测试")
    print("=" * 60)
    
    # 测试字典解析
    try:
        from app.etl.dict_loader import ProductDictLoader
        from app.services.resolver.product_resolver import ProductResolver
        
        # 加载字典
        loader = ProductDictLoader("data/product_dict.json")
        await loader.load()
        product_dict = await loader.get()
        print("[OK] 字典加载成功")
        print(f"   版本: {product_dict['version']}")
        print(f"   SPU数量: {len(product_dict['spu_dict'])}")
        print(f"   SKU数量: {len(product_dict['sku_dict'])}")
        
        # 创建解析器
        resolver = ProductResolver(product_dict)
        print("[OK] 解析器创建成功")
        
        # 测试查询
        test_cases = [
            ("cat tree", "应该匹配到SPU 753"),
            ("dog ramp", "应该匹配到SPU 754"),
            ("hammock", "应该匹配到SPU 753"),
        ]
        
        print("\n[查询结果]")
        for query, expected in test_cases:
            result = await resolver.resolve(query)
            if result.is_resolved:
                print(f"  [OK] '{query}': SPU ID={result.spu_id}, 类型={result.match_type.value}, 置信度={result.confidence:.2f}")
            else:
                print(f"  [FAIL] '{query}': 未匹配 - {expected}")
        
        print("\n" + "=" * 60)
        print("[完成] 商品查询功能测试完成!")
        print("=" * 60)
        
    except Exception as e:
        import traceback
        print(f"[ERROR] 测试失败: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
