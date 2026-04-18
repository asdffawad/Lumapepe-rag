#!/usr/bin/env python
"""测试商品上下文API"""
import urllib.request
import json

def test_api(url):
    print(f"\n测试: {url}")
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            print(f"状态: {resp.status}")
            if data.get('success'):
                ctx = data['data']
                print(f"商品名称: {ctx.get('product_name')}")
                print(f"商品类型: {ctx.get('product_type')}")
                print(f"价格: {ctx.get('sku_price') or ctx.get('price')}")
                print(f"库存: {ctx.get('sku_stock') or ctx.get('stock')}")
                print(f"包含图片: {ctx.get('has_images')}")
                print(f"包含HTML: {ctx.get('has_html')}")
                print(f"简介片段数: {len(ctx.get('parsed_introduction', []))}")
                print(f"详情片段数: {len(ctx.get('parsed_description', []))}")
            else:
                print(f"错误: {data.get('error')}")
    except Exception as e:
        print(f"请求失败: {e}")

if __name__ == '__main__':
    base = 'http://localhost:8000/products/context'
    test_api(f"{base}?sku_id=209")
    test_api(f"{base}?spu_id=753")
    test_api(f"{base}?sku_id=999")  # 不存在的ID
