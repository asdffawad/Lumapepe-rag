#!/usr/bin/env python3
"""Docker 容器内 API 测试脚本"""
import requests, json

BASE = "http://localhost:8000/api/v1/admin"

# 1. 测试创建文档
print("=== 1. POST /admin/documents (create) ===")
r = requests.post(f"{BASE}/documents", json={
    "title": "测试文档新增API",
    "doc_type": "faq",
    "content": {"category": "测试", "content": "这是一个通过API新增的文档"},
    "source": "API-TEST"
})
print("Status:", r.status_code)
print(json.dumps(r.json(), ensure_ascii=False, indent=2))
new_doc_id = r.json().get("doc_id") if r.status_code == 200 else None

# 2. 测试获取文档列表
print()
print("=== 2. GET /admin/documents ===")
r = requests.get(f"{BASE}/documents")
data = r.json()
print("Status:", r.status_code)
print(f"共 {data['total']} 条文档, done={data['done']}, failed={data['failed']}, pending={data['pending']}")

# 3. 测试获取单文档
print()
print("=== 3. GET /admin/documents/1 ===")
r = requests.get(f"{BASE}/documents/1")
print("Status:", r.status_code)
print(json.dumps(r.json(), ensure_ascii=False, indent=2))

# 4. 测试更新文档
if new_doc_id:
    print()
    print("=== 4. PUT /admin/documents/{id} (update) ===")
    r = requests.put(f"{BASE}/documents/{new_doc_id}", json={
        "title": "测试文档-已更新",
        "content": {"category": "测试", "content": "这是更新后的文档内容"}
    })
    print("Status:", r.status_code)
    print(json.dumps(r.json(), ensure_ascii=False, indent=2))

    # 5. 测试删除文档
    print()
    print("=== 5. DELETE /admin/documents/{id} (soft delete) ===")
    r = requests.delete(f"{BASE}/documents/{new_doc_id}")
    print("Status:", r.status_code)
    print(json.dumps(r.json(), ensure_ascii=False, indent=2))

    # 验证删除后再查列表
    print()
    print("=== 6. 验证删除后的列表 ===")
    r = requests.get(f"{BASE}/documents")
    data = r.json()
    print("Status:", r.status_code)
    print(f"共 {data['total']} 条文档, done={data['done']}, failed={data['failed']}, pending={data['pending']}")
    deleted_docs = [d for d in data["documents"] if d["id"] == new_doc_id]
    print(f"id={new_doc_id} 的文档: {[d['status'] for d in deleted_docs]}")

# 7. 测试不存在文档
print()
print("=== 7. GET /admin/documents/9999 (not found) ===")
r = requests.get(f"{BASE}/documents/9999")
print("Status:", r.status_code, r.json())

# 8. 测试重索引
print()
print("=== 8. POST /admin/documents/1/reindex ===")
r = requests.post(f"{BASE}/documents/1/reindex")
print("Status:", r.status_code)
print(json.dumps(r.json(), ensure_ascii=False, indent=2))

print()
print("=== All tests completed ===")
