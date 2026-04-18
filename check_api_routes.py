#!/usr/bin/env python
"""检查Docker容器中的API路由"""
import urllib.request
import json

# 获取OpenAPI规范
try:
    with urllib.request.urlopen('http://localhost:8000/openapi.json', timeout=5) as resp:
        spec = json.loads(resp.read().decode())
        paths = list(spec.get('paths', {}).keys())
        print("可用的API端点:")
        for path in sorted(paths):
            print(f"  {path}")
except Exception as e:
    print(f"获取OpenAPI规范失败: {e}")
