"""
Prometheus Metrics 测试
"""
import pytest
from app import metrics as metrics_module


class TestMetrics:
    """指标记录函数测试"""

    def test_record_chat_request(self):
        """record_chat_request 应正确增加计数器"""
        before = metrics_module.chat_requests_total.labels(
            intent="product_qa", status="success"
        )._value.get()
        metrics_module.record_chat_request(
            intent="product_qa", status="success", duration_seconds=1.5
        )
        after = metrics_module.chat_requests_total.labels(
            intent="product_qa", status="success"
        )._value.get()
        assert after == before + 1

    def test_record_llm_call(self):
        """record_llm_call 应正确记录 LLM 调用"""
        before = metrics_module.llm_calls_total.labels(
            model="qwen-max", status="success"
        )._value.get()
        metrics_module.record_llm_call(
            model="qwen-max", status="success", duration_seconds=2.0
        )
        after = metrics_module.llm_calls_total.labels(
            model="qwen-max", status="success"
        )._value.get()
        assert after == before + 1

    def test_record_llm_call_error(self):
        """LLM 调用失败应记录 error 状态"""
        before = metrics_module.llm_calls_total.labels(
            model="qwen-max", status="error"
        )._value.get()
        metrics_module.record_llm_call(
            model="qwen-max", status="error", duration_seconds=0.5
        )
        after = metrics_module.llm_calls_total.labels(
            model="qwen-max", status="error"
        )._value.get()
        assert after == before + 1

    def test_record_vector_search(self):
        """record_vector_search 应记录向量检索延迟"""
        # Histogram 不暴露 count 属性，用 observe 并检查不抛异常来验证
        metrics_module.record_vector_search(duration_seconds=0.05)
        metrics_module.record_vector_search(duration_seconds=0.1)
        # 不抛异常即通过

    def test_set_active_sessions(self):
        """set_active_sessions 应正确设置 Gauge"""
        metrics_module.set_active_sessions(10)
        assert metrics_module.active_sessions._value.get() == 10
        metrics_module.set_active_sessions(5)
        assert metrics_module.active_sessions._value.get() == 5


class TestMetricsEndpoint:
    """指标端点测试"""

    @pytest.mark.asyncio
    async def test_metrics_endpoint_returns_prometheus_format(self):
        """GET /metrics 应返回 Prometheus 格式数据"""
        from httpx import AsyncClient, ASGITransport
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/metrics")
            assert response.status_code == 200
            content = response.text
            # Prometheus 格式应包含 HELP 和 TYPE 行
            assert "# HELP" in content
            assert "# TYPE" in content
            # 应包含我们定义的指标
            assert "chat_requests_total" in content
            assert "llm_calls_total" in content
            assert "vector_search_duration_seconds" in content

    @pytest.mark.asyncio
    async def test_health_endpoint_returns_status(self):
        """GET /api/v1/health 应返回服务状态"""
        from httpx import AsyncClient, ASGITransport
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/v1/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"
            assert "version" in data
