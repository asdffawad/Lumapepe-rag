"""
订单查询工具集成测试
覆盖: OrderRecord, OrderRepository, OrderTool/RefundTool,
     RAGOrchestrator order_query 分流, API 层 tool_result 响应
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

from app.database.order import OrderRecord, OrderRepository
from app.services.tools.base import ToolResult
from app.services.tools import OrderTool, LogisticsTool, RefundTool
from app.services.tool_router import ToolRouter
from app.services.rag_orchestrator import RAGOrchestrator
from app.services.query_rewriter import MockQueryRewriter
from app.services.vector_retriever import MockVectorRetriever
from app.services.reranker import MockReranker
from app.services.prompt_builder import MockPromptBuilder
from app.services.llm_generator import MockLLMGenerator
from app.config import RAGConfig


# ---------------------------------------------------------------------------
# 测试辅助: 构建兼容 aiomysql 0.3.x 异步调用链的 mock
# ---------------------------------------------------------------------------
def _make_mock_pool(row_or_rows):
    """构建与 aiomysql 0.3.x 兼容的 pool mock。

    aiomysql.pool.Pool.acquire() 是同步方法，返回 _PoolAcquireContextManager 对象
    （有 async __aenter__/__aexit__）。
    conn.cursor() 返回 cursor 对象（需支持 async with）。
    """
    rows = row_or_rows if isinstance(row_or_rows, list) else ([row_or_rows] if row_or_rows else [])
    _fetchone_data = row_or_rows if not isinstance(row_or_rows, list) else (rows[0] if rows else None)

    class _Cursor:
        async def execute(self, q, a=None): pass
        async def fetchone(self): return _fetchone_data
        async def fetchall(self): return rows

        def cursor(self, cursor_cls=None):
            return _Cursor()

        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass

    class _PoolAcquireCM:
        async def __aenter__(self): return _Cursor()
        async def __aexit__(self, *args): return None

    class _Pool:
        def acquire(self): return _PoolAcquireCM()

    return _Pool()


# ---------------------------------------------------------------------------
# OrderRecord 单元测试
# ---------------------------------------------------------------------------
class TestOrderRecord:
    def test_to_dict(self):
        record = OrderRecord(
            order_id="ORD001",
            user_id="test-user",
            product_name="iPhone 16",
            product_id="P001",
            quantity=1,
            total_price=9999.0,
            status="shipped",
            payment_method="支付宝",
            address="北京市朝阳区",
            logistics_no="SF123",
            logistics_company="顺丰速运",
            created_at=datetime(2026, 4, 1, 10, 0, 0),
            paid_at=datetime(2026, 4, 1, 10, 5, 0),
            shipped_at=datetime(2026, 4, 2, 14, 0, 0),
            delivered_at=None,
        )
        d = record.to_dict()
        assert d["order_id"] == "ORD001"
        assert d["product_name"] == "iPhone 16"
        assert d["status"] == "shipped"
        assert d["created_at"] == "2026-04-01T10:00:00"

    def test_status_text_mapping(self):
        record = OrderRecord(
            order_id="ORD001", user_id="u", product_name="p", product_id=None,
            quantity=1, total_price=100.0, status="shipped",
            payment_method=None, address=None, logistics_no=None,
            logistics_company=None, created_at=datetime.now(),
            paid_at=None, shipped_at=None, delivered_at=None,
        )
        assert record.status_text == "配送中"
        record.status = "pending"
        assert record.status_text == "待支付"
        record.status = "delivered"
        assert record.status_text == "已完成"
        record.status = "unknown_status"
        assert record.status_text == "unknown_status"


# ---------------------------------------------------------------------------
# OrderRepository 单元测试（Mock DB Pool）
# ---------------------------------------------------------------------------
class TestOrderRepository:
    _REAL_DICT = {
        "order_id": "ORD001", "user_id": "test-user",
        "product_name": "iPhone 16", "product_id": "P001",
        "quantity": 1, "total_price": 9999.0,
        "status": "shipped", "payment_method": "支付宝",
        "address": "北京", "logistics_no": "SF123",
        "logistics_company": "顺丰",
        "created_at": datetime(2026, 4, 1, 10, 0, 0),
        "paid_at": None, "shipped_at": None, "delivered_at": None,
    }
    _REAL_DICT_DELIVERED = dict(_REAL_DICT, order_id="ORD002",
        product_name="MacBook", total_price=16999.0, status="delivered",
        delivered_at=datetime(2026, 4, 3, 18, 0, 0))
    _REAL_DICT_LOGISTICS = dict(_REAL_DICT, order_id="ORD003",
        logistics_no="SF789", logistics_company="顺丰")

    @pytest.mark.asyncio
    async def test_find_by_user_returns_orders(self):
        repo = OrderRepository(_make_mock_pool(self._REAL_DICT))
        results = await repo.find_by_user("test-user", limit=5)
        assert len(results) == 1
        assert results[0].order_id == "ORD001"
        assert results[0].status == "shipped"
        assert results[0].total_price == 9999.0

    @pytest.mark.asyncio
    async def test_find_by_order_id_found(self):
        repo = OrderRepository(_make_mock_pool(self._REAL_DICT_DELIVERED))
        result = await repo.find_by_order_id("ORD002")
        assert result is not None
        assert result.order_id == "ORD002"
        assert result.status == "delivered"

    @pytest.mark.asyncio
    async def test_find_by_order_id_not_found(self):
        repo = OrderRepository(_make_mock_pool(None))
        result = await repo.find_by_order_id("NOTEXIST")
        assert result is None

    @pytest.mark.asyncio
    async def test_find_by_logistics(self):
        repo = OrderRepository(_make_mock_pool(self._REAL_DICT_LOGISTICS))
        result = await repo.find_by_logistics("SF789")
        assert result is not None
        assert result.logistics_no == "SF789"


# ---------------------------------------------------------------------------
# 工具单元测试（Mock DB Pool）
# ---------------------------------------------------------------------------
class TestOrderTool:
    @pytest.mark.asyncio
    async def test_order_tool_user_id_required(self):
        tool = OrderTool()
        result = await tool.execute({})
        assert result.success is False
        assert "user_id" in result.error

    @pytest.mark.asyncio
    async def test_order_tool_returns_user_orders(self):
        tool = OrderTool()
        pool = _make_mock_pool({
            "order_id": "ORD001", "user_id": "test-user",
            "product_name": "iPhone 16", "product_id": "P001",
            "quantity": 1, "total_price": 9999.0,
            "status": "shipped", "payment_method": "支付宝",
            "address": "北京", "logistics_no": "SF123",
            "logistics_company": "顺丰",
            "created_at": datetime(2026, 4, 1),
            "paid_at": None, "shipped_at": None, "delivered_at": None,
        })
        with patch("app.services.tools._get_db_pool", return_value=pool):
            result = await tool.execute({"user_id": "test-user"})
        assert result.success is True
        assert result.source == "订单系统"
        assert "orders" in result.data
        assert result.data["total"] == 1

    @pytest.mark.asyncio
    async def test_order_tool_db_not_init(self):
        tool = OrderTool()
        with patch("app.services.tools._get_db_pool", side_effect=RuntimeError("not init")):
            result = await tool.execute({"user_id": "test-user"})
        assert result.success is False
        assert "未初始化" in result.error


class TestRefundTool:
    @pytest.mark.asyncio
    async def test_refund_tool_user_id_required(self):
        tool = RefundTool()
        result = await tool.execute({"order_id": "ORD001"})
        assert result.success is False
        assert "user_id" in result.error

    @pytest.mark.asyncio
    async def test_refund_tool_returns_policy(self):
        tool = RefundTool()
        pool = _make_mock_pool({
            "order_id": "ORD001", "user_id": "test-user",
            "product_name": "MacBook", "product_id": "P002",
            "quantity": 1, "total_price": 16999.0,
            "status": "delivered", "payment_method": "微信",
            "address": "上海", "logistics_no": None,
            "logistics_company": None,
            "created_at": datetime(2026, 4, 1),
            "paid_at": None, "shipped_at": None,
            "delivered_at": datetime(2026, 4, 3),
        })
        with patch("app.services.tools._get_db_pool", return_value=pool):
            result = await tool.execute({"user_id": "test-user", "order_id": "ORD001"})
        assert result.success is True
        assert result.data["can_refund"] is True
        assert "policy" in result.data


# ---------------------------------------------------------------------------
# ToolRouter 集成测试
# ---------------------------------------------------------------------------
class TestToolRouter:
    @pytest.mark.asyncio
    async def test_tool_router_unknown_tool(self):
        router = ToolRouter()
        result = await router.execute("unknown_tool", {}, "req-001")
        assert result.success is False
        assert "未知工具" in result.error

    @pytest.mark.asyncio
    async def test_tool_router_order_query(self):
        router = ToolRouter()
        # 工具会因为 pool 未初始化而返回错误，这是预期行为
        result = await router.execute("order_query", {"user_id": "test-user"}, "req-002")
        assert result.source == "订单系统"


# ---------------------------------------------------------------------------
# RAGOrchestrator 工具分流测试
# ---------------------------------------------------------------------------
class TestRAGOrchestratorToolRouting:
    @pytest.fixture
    def config(self):
        return RAGConfig(
            top_k=5, rerank_top_k=3,
            rewrite_timeout_ms=500, retrieve_timeout_ms=1000,
            rerank_timeout_ms=1000, llm_timeout_ms=30000,
            max_context_tokens=3000,
        )

    @pytest.fixture
    def orchestrator(self, config):
        return RAGOrchestrator(
            rewriter=MockQueryRewriter(),
            retriever=MockVectorRetriever(),
            reranker=MockReranker(),
            prompt_builder=MockPromptBuilder(),
            generator=MockLLMGenerator(),
            config=config,
        )

    @pytest.mark.asyncio
    async def test_run_with_order_query_calls_tool(self, orchestrator):
        """order_query 意图应调用 ToolRouter"""
        mock_result = ToolResult(
            success=True,
            data={"order_id": "ORD001", "product_name": "iPhone 16", "status": "配送中"},
            source="订单系统",
        )
        with patch.object(orchestrator, "_call_tool", return_value=mock_result) as mock_call:
            result = await orchestrator.run(
                query="我的订单到哪了？",
                history=[],
                intent="order_query",
                request_id="test-001",
                user_id="test-user",
            )
            mock_call.assert_called_once_with(
                "order_query",
                {"user_id": "test-user"},
                "test-001",
            )
            assert result.tool_result is not None
            assert result.tool_result.success is True

    @pytest.mark.asyncio
    async def test_run_without_user_id_skips_tool(self, orchestrator):
        """无 user_id 时跳过工具调用"""
        with patch.object(orchestrator, "_call_tool") as mock_call:
            result = await orchestrator.run(
                query="我的订单到哪了？",
                history=[],
                intent="order_query",
                request_id="test-002",
                user_id=None,
            )
            mock_call.assert_not_called()
            assert result.tool_result is None

    @pytest.mark.asyncio
    async def test_run_product_qa_skips_tool(self, orchestrator):
        """product_qa 意图不应调用工具"""
        with patch.object(orchestrator, "_call_tool") as mock_call:
            result = await orchestrator.run(
                query="iPhone 16 支持5G吗？",
                history=[],
                intent="product_qa",
                request_id="test-003",
                user_id="test-user",
            )
            mock_call.assert_not_called()
            assert result.tool_result is None
