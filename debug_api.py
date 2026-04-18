#!/usr/bin/env python
"""调试SKU API"""
import urllib.request
import json

def test_api(url):
    print(f"\n测试: {url}")
    try:
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            print(f"状态: {resp.status}")
            print(f"完整响应: {json.dumps(data, ensure_ascii=False)[:500]}")
    except urllib.error.HTTPError as e:
        print(f"HTTP错误: {e.code} {e.reason}")
        try:
            error_data = json.loads(e.read().decode())
            print(f"错误详情: {error_data}")
        except:
            print(f"原始错误: {e.read()[:200]}")
    except Exception as e:
        print(f"请求失败: {e}")

if __name__ == '__main__':
    test_api("http://localhost:8000/products/context?sku_id=209")
    test_api("http://localhost:8000/products/context?spu_id=753")
