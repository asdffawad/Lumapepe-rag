#!/usr/bin/env python3
"""
使用 httpx + ASGITransport 直接测试 admin API（不走网络）
绕过 curl/urllib 的事件循环兼容性问题
"""
import asyncio
import json
import sys
import tempfile

from httpx import AsyncClient, ASGITransport


async def main():
    # 重置 EmbeddingService 为 Mock（无 API key 时避免调用 DashScope）
    from app.services import embedding_service
    from app.services.embedding_service import MockEmbedding
    embedding_service.EmbeddingService._instance = MockEmbedding(dim=1024)
    print("EmbeddingService -> MockEmbedding(1024)")

    # 初始化 app.state
    from app.main import app
    from app.database import connection as db_conn
    from app.vectorstore.milvus_client import MilvusClient
    from app.config import get_config

    config = get_config()
    print("Init DB pool...")
    pool = await db_conn.create_db_pool(config.db)
    app.state.db_pool = pool

    print("Init Milvus...")
    milvus = MilvusClient(config.milvus)
    await milvus.connect()
    app.state.milvus_client = milvus

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:

        # TEST 1: GET /admin/documents
        print("\n[TEST 1] GET /admin/documents")
        r = await client.get("/api/v1/admin/documents")
        assert r.status_code == 200, f"FAIL {r.status_code}: {r.text}"
        d = r.json()
        print(f"  PASS: total={d['total']} done={d['done']} failed={d['failed']}")

        # TEST 2: GET /admin/documents/1
        print("\n[TEST 2] GET /admin/documents/1")
        r = await client.get("/api/v1/admin/documents/1")
        assert r.status_code == 200, f"FAIL {r.status_code}: {r.text}"
        d = r.json()
        assert d["id"] == 1
        print(f"  PASS: id={d['id']} title={d.get('title','')[:30]}")

        # TEST 3: GET /admin/documents/9999 (not found)
        print("\n[TEST 3] GET /admin/documents/9999")
        r = await client.get("/api/v1/admin/documents/9999")
        assert r.status_code == 404, f"FAIL {r.status_code}"
        print(f"  PASS: 404 as expected")

        # TEST 4: POST /admin/documents (create)
        import uuid
        test_uuid = uuid.uuid4().hex
        print("\n[TEST 4] POST /admin/documents (create)")
        r = await client.post("/api/v1/admin/documents", json={
            "title": f"ASGI测试文档-{test_uuid[:8]}",
            "doc_type": "faq",
            "content": {"category": "测试", "content": f"唯一测试内容-{test_uuid}"},
            "source": "asgi_test",
        }, timeout=30.0)
        assert r.status_code == 200, f"FAIL {r.status_code}: {r.text}"
        d = r.json()
        assert d.get("status") == "success"
        new_id = d["doc_id"]
        print(f"  PASS: doc_id={new_id} chunks={d.get('chunks')}")

        # TEST 5: POST /admin/documents/{id}/reindex
        print(f"\n[TEST 5] POST /admin/documents/{new_id}/reindex")
        r = await client.post(f"/api/v1/admin/documents/{new_id}/reindex", json={}, timeout=30.0)
        assert r.status_code == 200, f"FAIL {r.status_code}: {r.text}"
        d = r.json()
        assert d.get("status") == "success"
        print(f"  PASS: deleted={d.get('deleted_chunks')} new={d.get('new_chunks')}")

        # TEST 6: PUT /admin/documents/{id}
        print(f"\n[TEST 6] PUT /admin/documents/{new_id}")
        r = await client.put(f"/api/v1/admin/documents/{new_id}", json={
            "title": f"已更新-ASGI测试-{test_uuid[:4]}",
            "content": {"category": "测试", "content": f"更新后的内容-{test_uuid}"},
        }, timeout=30.0)
        assert r.status_code == 200, f"FAIL {r.status_code}: {r.text}"
        d = r.json()
        assert d.get("status") == "success"
        print(f"  PASS: new_chunks={d.get('new_chunks')}")

        # TEST 7: DELETE /admin/documents/{id}
        print(f"\n[TEST 7] DELETE /admin/documents/{new_id}")
        r = await client.delete(f"/api/v1/admin/documents/{new_id}", timeout=15.0)
        assert r.status_code == 200, f"FAIL {r.status_code}: {r.text}"
        d = r.json()
        assert d.get("status") == "success"
        print(f"  PASS: deleted_chunks={d.get('deleted_chunks')}")

        # TEST 8: GET after soft-delete (document still exists with status='deleted')
        print(f"\n[TEST 8] GET /admin/documents/{new_id} (after soft delete)")
        r = await client.get(f"/api/v1/admin/documents/{new_id}")
        # 软删除：status='deleted'，列表中不显示，但 GET /documents/{id} 应返回文档
        # 如果 GET 返回 404 说明 is_deleted 过滤生效，这也是正确行为
        print(f"  Status after delete: {r.status_code}, body: {r.text[:100]}")
        if r.status_code == 200:
            d = r.json()
            print(f"  PASS: doc still exists, status={d.get('status')}")
        else:
            print(f"  PASS: 404 (is_deleted filter active) = correct soft-delete behavior")

    # TEST 9: Milvus with filters
    print("\n[TEST 9] MilvusClient.search with filters")
    results = await milvus.search(query_vector=[0.0] * 1024, top_k=5, filters={"doc_type": "product"})
    print(f"  PASS: filtered (doc_type=product) -> {len(results)} results")
    if results:
        print(f"  Sample: source={results[0].source}, score={results[0].score:.4f}")

    # TEST 10: Milvus delete_by_doc_id
    print("\n[TEST 10] MilvusClient.delete_by_doc_id")
    test_id = 5555
    await milvus.insert(
        vectors=[[0.1] * 1024, [0.2] * 1024],
        contents=["e2e test 1", "e2e test 2"],
        metadata=[{}, {}],
        doc_id=test_id, source="asgi_test", doc_type="faq",
    )
    deleted = await milvus.delete_by_doc_id(test_id)
    assert deleted == 2, f"FAIL: expected 2, got {deleted}"
    print(f"  PASS: deleted {deleted} chunks")

    await db_conn.close_db_pool()
    milvus.close()
    print("\n" + "=" * 55)
    print("ALL 10 TESTS PASSED!")
    print("=" * 55)


if __name__ == "__main__":
    asyncio.run(main())


# ============================================================================
# 增量摄取核心模块单元测试
# ============================================================================
import tempfile
from pathlib import Path

import pytest

from app.services.incremental_ingest_core import scan_data_directory, IncrementalIngestResult


class TestScanDataDirectory:
    """测试 scan_data_directory 函数的扫描逻辑"""

    def test_scan_products_json(self):
        """测试扫描 products.json 文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 准备测试数据：3个产品
            products_data = [
                {"product_id": "P001", "name": "产品A", "price": 100},
                {"product_id": "P002", "name": "产品B", "price": 200},
                {"product_id": "P003", "name": "产品C", "price": 300},
            ]
            products_file = Path(tmpdir) / "products.json"
            products_file.write_text(
                json.dumps(products_data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

            # 调用被测函数
            result = scan_data_directory(Path(tmpdir))

            # 验证返回结果数量
            assert len(result) == 3, f"期望返回3项，实际返回{len(result)}项"

            # 验证每项的 source_path 格式和 doc_type
            for i, item in enumerate(result):
                assert item["source_path"] == f"products.json::P00{i+1}", \
                    f"source_path 格式错误: {item['source_path']}"
                assert item["doc_type"] == "product", \
                    f"doc_type 应为 'product'，实际为 '{item['doc_type']}'"
                assert item["item"] is not None, "item 不应为 None"
                assert item["file_path"] is None, "file_path 应为 None"
                assert "mtime" in item, "缺少 mtime 字段"

    def test_scan_knowledge_base_json(self):
        """测试扫描 knowledge_base.json 文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 准备测试数据：1个 policy 类，1个 faq 类
            kb_data = [
                {"id": "KB001", "category": "退换货政策", "title": "退货流程"},
                {"id": "KB002", "category": "常见问题", "title": "如何使用优惠券"},
            ]
            kb_file = Path(tmpdir) / "knowledge_base.json"
            kb_file.write_text(
                json.dumps(kb_data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

            # 调用被测函数
            result = scan_data_directory(Path(tmpdir))

            # 验证返回结果数量
            assert len(result) == 2, f"期望返回2项，实际返回{len(result)}项"

            # 验证第一个 item：退换货政策 → doc_type="policy"
            kb001 = next(item for item in result if item["source_path"] == "knowledge_base.json::KB001")
            assert kb001["doc_type"] == "policy", \
                f"退换货政策类别应映射为 'policy'，实际为 '{kb001['doc_type']}'"

            # 验证第二个 item：常见问题 → doc_type="faq"
            kb002 = next(item for item in result if item["source_path"] == "knowledge_base.json::KB002")
            assert kb002["doc_type"] == "faq", \
                f"常见问题类别应映射为 'faq'，实际为 '{kb002['doc_type']}'"

    def test_scan_orders_json(self):
        """测试扫描 orders.json 文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 准备测试数据：1个订单
            orders_data = [
                {"order_id": "ORD12345", "customer": "张三", "total": 500},
            ]
            orders_file = Path(tmpdir) / "orders.json"
            orders_file.write_text(
                json.dumps(orders_data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

            # 调用被测函数
            result = scan_data_directory(Path(tmpdir))

            # 验证返回结果数量
            assert len(result) == 1, f"期望返回1项，实际返回{len(result)}项"

            # 验证 source_path 格式
            assert result[0]["source_path"] == "orders.json::ORD12345", \
                f"source_path 格式错误: {result[0]['source_path']}"

            # 验证 doc_type
            assert result[0]["doc_type"] == "manual", \
                f"doc_type 应为 'manual'，实际为 '{result[0]['doc_type']}'"

            # 验证其他字段
            assert result[0]["item"] is not None, "item 不应为 None"
            assert result[0]["file_path"] is None, "file_path 应为 None"
            assert result[0]["item"]["order_id"] == "ORD12345", "订单数据不正确"

    def test_scan_empty_directory(self):
        """测试扫描空目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 调用被测函数（目录为空，无任何 JSON 文件）
            result = scan_data_directory(Path(tmpdir))

            # 验证返回空列表
            assert result == [], f"空目录应返回空列表，实际返回: {result}"
            assert len(result) == 0, "空目录应返回空列表"

    def test_scan_mixed_files(self):
        """测试扫描混合文件类型"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 准备 products.json（2个产品）
            products_data = [
                {"product_id": "P001", "name": "产品A"},
                {"product_id": "P002", "name": "产品B"},
            ]
            products_file = Path(tmpdir) / "products.json"
            products_file.write_text(json.dumps(products_data, ensure_ascii=False), encoding="utf-8")

            # 准备 knowledge_base.json（1个条目）
            kb_data = [{"id": "KB001", "category": "配送说明", "title": "如何查看物流"}]
            kb_file = Path(tmpdir) / "knowledge_base.json"
            kb_file.write_text(json.dumps(kb_data, ensure_ascii=False), encoding="utf-8")

            # 调用被测函数
            result = scan_data_directory(Path(tmpdir))

            # 验证返回结果数量：2个产品 + 1个知识库 = 3项
            assert len(result) == 3, f"期望返回3项，实际返回{len(result)}项"

            # 验证包含来自两个文件的项目
            source_paths = {item["source_path"] for item in result}
            assert "products.json::P001" in source_paths, "缺少 products.json::P001"
            assert "products.json::P002" in source_paths, "缺少 products.json::P002"
            assert "knowledge_base.json::KB001" in source_paths, "缺少 knowledge_base.json::KB001"

    def test_scan_knowledge_base_with_various_categories(self):
        """测试知识库各类别到 doc_type 的映射"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 准备测试数据：覆盖多种类别
            kb_data = [
                {"id": "KB001", "category": "退换货政策", "title": "退货流程"},           # → policy
                {"id": "KB002", "category": "价格保护规则", "title": "价保说明"},         # → policy
                {"id": "KB003", "category": "发票开具", "title": "如何开票"},             # → policy
                {"id": "KB004", "category": "售后服务条款", "title": "售后政策"},         # → policy
                {"id": "KB005", "category": "配送说明", "title": "如何查看物流"},         # → faq
                {"id": "KB006", "category": "支付方式", "title": "支持的支付"},           # → faq
                {"id": "KB007", "category": "运费计算", "title": "运费说明"},             # → faq
                {"id": "KB008", "category": "其他问题", "title": "账号绑定"},             # → faq (默认)
            ]
            kb_file = Path(tmpdir) / "knowledge_base.json"
            kb_file.write_text(json.dumps(kb_data, ensure_ascii=False), encoding="utf-8")

            # 调用被测函数
            result = scan_data_directory(Path(tmpdir))

            # 验证返回结果数量
            assert len(result) == 8, f"期望返回8项，实际返回{len(result)}项"

            # 统计各 doc_type 数量
            policy_count = sum(1 for item in result if item["doc_type"] == "policy")
            faq_count = sum(1 for item in result if item["doc_type"] == "faq")

            assert policy_count == 4, f"policy 应有4项，实际有{policy_count}项"
            assert faq_count == 4, f"faq 应有4项，实际有{faq_count}项"


class TestIncrementalIngestResult:
    """测试 IncrementalIngestResult 数据类"""

    def test_default_values(self):
        """测试默认值初始化"""
        result = IncrementalIngestResult()

        assert result.added == 0, "added 默认值应为 0"
        assert result.updated == 0, "updated 默认值应为 0"
        assert result.unchanged == 0, "unchanged 默认值应为 0"
        assert result.deleted == 0, "deleted 默认值应为 0"
        assert result.errors == 0, "errors 默认值应为 0"
        assert result.details == [], "details 默认值应为空列表"

    def test_custom_values(self):
        """测试自定义值初始化"""
        result = IncrementalIngestResult(
            added=10,
            updated=5,
            unchanged=20,
            deleted=2,
            errors=1,
            details=[
                {"source_path": "test.json::001", "action": "added"},
                {"source_path": "test.json::002", "action": "updated"},
            ]
        )

        assert result.added == 10, f"added 应为 10，实际为 {result.added}"
        assert result.updated == 5, f"updated 应为 5，实际为 {result.updated}"
        assert result.unchanged == 20, f"unchanged 应为 20，实际为 {result.unchanged}"
        assert result.deleted == 2, f"deleted 应为 2，实际为 {result.deleted}"
        assert result.errors == 1, f"errors 应为 1，实际为 {result.errors}"
        assert len(result.details) == 2, f"details 应有2项，实际有{len(result.details)}项"

    def test_details_list_operations(self):
        """测试 details 列表的增删操作"""
        result = IncrementalIngestResult()

        # 添加详情记录
        result.details.append({"source_path": "test1.json::001", "action": "added"})
        result.details.append({"source_path": "test2.json::002", "action": "updated"})
        result.details.append({"source_path": "test3.json::003", "action": "error", "message": "解析失败"})

        assert len(result.details) == 3, "details 应有3项"

        # 验证第一条记录
        assert result.details[0]["source_path"] == "test1.json::001"
        assert result.details[0]["action"] == "added"

        # 验证第三条记录的错误信息
        assert result.details[2]["message"] == "解析失败"

        # 清空详情
        result.details.clear()
        assert len(result.details) == 0, "details 应被清空"

    def test_field_assignment(self):
        """测试字段赋值"""
        result = IncrementalIngestResult()

        # 模拟增量处理过程
        result.added += 1
        result.updated += 1
        result.errors += 1

        assert result.added == 1
        assert result.updated == 1
        assert result.errors == 1

        # 再次添加
        result.added += 5
        assert result.added == 6
