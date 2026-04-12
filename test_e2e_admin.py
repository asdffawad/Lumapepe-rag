#!/usr/bin/env python3
"""
端到端测试脚本：在 Docker 容器内运行
直接设置 app.state 后通过 ASGITransport 调用 admin API
"""
import sys
import asyncio
sys.path.insert(0, "/app/backend")

from httpx import AsyncClient, ASGITransport


async def main():
    # ── 0. 重置 EmbeddingService 为 Mock（避免无 API key 时调用 DashScope 阻塞）──
    from app.services import embedding_service
    from app.services.embedding_service import MockEmbedding
    embedding_service.EmbeddingService._instance = MockEmbedding(dim=1024)
    print("EmbeddingService 重置为 MockEmbedding(1024)")

    # ── 1. 初始化 app.state（注入 db_pool 和 milvus_client）───────────────
    from app.main import app
    from app.database import connection as db_conn
    from app.vectorstore.milvus_client import MilvusClient
    from app.config import get_config

    config = get_config()
    print("初始化 MySQL 连接池...")
    pool = await db_conn.create_db_pool(config.db)
    app.state.db_pool = pool

    print("初始化 Milvus 连接...")
    milvus = MilvusClient(config.milvus)
    await milvus.connect()
    app.state.milvus_client = milvus

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:

        # ── 2. GET /admin/documents（列表）──────────────────────────────────
        print("\n[TEST 1] GET /admin/documents")
        r = await client.get("/api/v1/admin/documents")
        assert r.status_code == 200, f"FAIL: {r.text}"
        data = r.json()
        print(f"  PASS: total={data['total']} done={data['done']} failed={data['failed']}")
        doc_count = data["total"]

        # ── 3. GET /admin/documents/1（单条）────────────────────────────────
        print("\n[TEST 2] GET /admin/documents/1")
        r = await client.get("/api/v1/admin/documents/1")
        assert r.status_code == 200, f"FAIL: {r.text}"
        d = r.json()
        assert d["id"] == 1
        print(f"  PASS: doc id={d['id']} title={d.get('title','')[:30]}")

        # ── 4. GET /admin/documents/9999（不存在）────────────────────────────
        print("\n[TEST 3] GET /admin/documents/9999 (not found)")
        r = await client.get("/api/v1/admin/documents/9999")
        assert r.status_code == 404, f"FAIL: {r.status_code}"
        print(f"  PASS: 404 as expected")

        # ── 5. POST /admin/documents（创建）─────────────────────────────────
        print("\n[TEST 4] POST /admin/documents (create)")
        r = await client.post("/api/v1/admin/documents", json={
            "title": "pytest_e2e测试文档",
            "doc_type": "faq",
            "content": {"category": "测试", "content": "这是端到端测试创建的文档内容"},
            "source": "e2e_test",
        })
        assert r.status_code == 200, f"FAIL: {r.status_code} {r.text}"
        data = r.json()
        assert data["status"] == "success"
        new_id = data["doc_id"]
        print(f"  PASS: doc_id={new_id} chunks={data['chunks']}")

        # ── 6. POST /admin/documents/{id}/reindex（重索引）──────────────────
        print(f"\n[TEST 5] POST /admin/documents/{new_id}/reindex")
        r = await client.post(f"/api/v1/admin/documents/{new_id}/reindex", json={})
        assert r.status_code == 200, f"FAIL: {r.status_code} {r.text}"
        data = r.json()
        assert data["status"] == "success"
        print(f"  PASS: new_chunks={data['new_chunks']}")

        # ── 7. PUT /admin/documents/{id}（更新）────────────────────────────
        print(f"\n[TEST 6] PUT /admin/documents/{new_id} (update)")
        r = await client.put(f"/api/v1/admin/documents/{new_id}", json={
            "title": "pytest_e2e_已更新",
            "content": {"category": "测试", "content": "更新后的文档内容"},
        })
        assert r.status_code == 200, f"FAIL: {r.status_code} {r.text}"
        data = r.json()
        assert data["status"] == "success"
        print(f"  PASS: new_chunks={data['new_chunks']}")

        # ── 8. DELETE /admin/documents/{id}（删除）────────────────────────
        print(f"\n[TEST 7] DELETE /admin/documents/{new_id}")
        r = await client.delete(f"/api/v1/admin/documents/{new_id}")
        assert r.status_code == 200, f"FAIL: {r.status_code} {r.text}"
        data = r.json()
        assert data["status"] == "success"
        print(f"  PASS: deleted_chunks={data['deleted_chunks']}")

        # ── 9. 确认删除后该文档不存在（404）───────────────────────────────
        print(f"\n[TEST 8] 验证删除：GET /admin/documents/{new_id}")
        r = await client.get(f"/api/v1/admin/documents/{new_id}")
        assert r.status_code == 404
        print(f"  PASS: 404 after delete")

        # ── 10. Milvus delete_by_doc_id 独立验证 ───────────────────────────
        print("\n[TEST 9] MilvusClient.delete_by_doc_id 独立测试")
        test_doc_id = 8888
        await milvus.insert(
            vectors=[[0.01] * 1024, [0.02] * 1024],
            contents=["test chunk 1", "test chunk 2"],
            metadata=[{}, {}],
            doc_id=test_doc_id,
            source="e2e_test",
            doc_type="faq",
        )
        count = await milvus.delete_by_doc_id(test_doc_id)
        assert count == 2, f"FAIL: expected 2 deleted, got {count}"
        print(f"  PASS: deleted {count} chunks")

        # ── 11. Milvus search with metadata filter ──────────────────────────
        print("\n[TEST 10] MilvusClient.search with metadata filter")
        results = await milvus.search(
            query_vector=[0.0] * 1024,
            top_k=3,
            filters={"doc_type": "product"},
        )
        print(f"  PASS: filtered search returned {len(results)} results")

    # ── 清理 ───────────────────────────────────────────────────────────────
    await db_conn.close_db_pool()
    milvus.close()

    print("\n" + "=" * 50)
    print("ALL TESTS PASSED")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
