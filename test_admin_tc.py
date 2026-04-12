#!/usr/bin/env python3
"""使用 FastAPI TestClient 在容器内直接测试 admin API（不走 HTTP）"""
import sys, json
sys.path.insert(0, "/app/backend")

from httpx import ASGITransport, AsyncClient
from app.main import app
import asyncio


async def run_tests():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:

        # 1. 创建文档
        print("=== 1. POST /admin/documents (create) ===")
        r = await client.post("/api/v1/admin/documents", json={
            "title": "测试文档新增API",
            "doc_type": "faq",
            "content": {"category": "测试", "content": "这是一个通过API新增的文档"},
            "source": "API-TEST"
        })
        print("Status:", r.status_code)
        resp = r.json()
        if r.status_code < 500:
            print(json.dumps(resp, ensure_ascii=False, indent=2))
            new_id = resp.get("doc_id")
        else:
            print(resp)
            new_id = None

        # 2. 文档列表
        print("\n=== 2. GET /admin/documents ===")
        r = await client.get("/api/v1/admin/documents")
        print("Status:", r.status_code)
        d = r.json()
        print(f"total={d['total']} done={d['done']} failed={d['failed']} pending={d['pending']}")

        # 3. 单文档
        print("\n=== 3. GET /admin/documents/1 ===")
        r = await client.get("/api/v1/admin/documents/1")
        print("Status:", r.status_code, json.dumps(r.json(), ensure_ascii=False)[:200])

        if new_id:
            # 4. 更新
            print(f"\n=== 4. PUT /admin/documents/{new_id} ===")
            r = await client.put(f"/api/v1/admin/documents/{new_id}", json={
                "title": "已更新-测试文档",
                "content": {"category": "测试", "content": "更新后的内容"}
            })
            print("Status:", r.status_code)
            if r.status_code < 500:
                print(json.dumps(r.json(), ensure_ascii=False, indent=2))
            else:
                print(r.text[:300])

            # 5. 删除
            print(f"\n=== 5. DELETE /admin/documents/{new_id} ===")
            r = await client.delete(f"/api/v1/admin/documents/{new_id}")
            print("Status:", r.status_code, json.dumps(r.json(), ensure_ascii=False))

        # 6. 重索引
        print("\n=== 6. POST /admin/documents/1/reindex ===")
        r = await client.post("/api/v1/admin/documents/1/reindex", json={})
        print("Status:", r.status_code)
        if r.status_code < 500:
            print(json.dumps(r.json(), ensure_ascii=False, indent=2))
        else:
            print(r.text[:300])

        # 7. 不存在的文档
        print("\n=== 7. GET /admin/documents/9999 ===")
        r = await client.get("/api/v1/admin/documents/9999")
        print("Status:", r.status_code, r.json())

    print("\n=== All tests completed ===")


asyncio.run(run_tests())
