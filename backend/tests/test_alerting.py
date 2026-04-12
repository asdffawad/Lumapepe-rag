"""
Business alerting module tests.
"""
import pytest
import asyncio
from app.services.alerting import (
    AlertMonitor,
    AlertLevel,
    Alert,
    _default_alert_handler,
    _schedule_alert,
)


class TestAlertMonitor:
    def setup_method(self):
        self.monitor = AlertMonitor(
            llm_failure_threshold=3,
            vector_failure_threshold=2,
            latency_threshold_ms=5000,
            memory_threshold_pct=90,
            check_interval_seconds=60,
        )

    def test_initial_state(self):
        status = self.monitor.get_status()
        assert status["llm_failures"] == 0
        assert status["vector_failures"] == 0
        assert status["running"] is False

    def test_llm_failure_triggers_alert(self):
        """LLM consecutive failures reach threshold -> alert emitted."""
        call_count = [0]

        async def capture_handler(alert):
            call_count[0] += 1

        self.monitor._handlers = [capture_handler]

        self.monitor.record_llm_failure("timeout")
        assert self.monitor._llm_failures == 1

        self.monitor.record_llm_failure("timeout")
        assert self.monitor._llm_failures == 2

        self.monitor.record_llm_failure("timeout")
        assert self.monitor._llm_failures == 3
        assert call_count[0] == 1

    def test_llm_success_resets_counter(self):
        self.monitor._llm_failures = 2
        self.monitor.record_llm_success()
        assert self.monitor._llm_failures == 0

    def test_vector_failure_triggers_alert(self):
        """Vector retrieval failures reach threshold -> alert emitted."""
        call_count = [0]

        async def capture_handler(alert):
            call_count[0] += 1

        self.monitor._handlers = [capture_handler]
        self.monitor.vector_failure_threshold = 2

        self.monitor.record_vector_failure("connection error")
        assert self.monitor._vector_failures == 1
        assert call_count[0] == 0

        self.monitor.record_vector_failure("timeout")
        assert self.monitor._vector_failures == 2
        assert call_count[0] == 1
        assert self.monitor._handlers[0] is capture_handler

    def test_vector_success_resets_counter(self):
        self.monitor._vector_failures = 2
        self.monitor.record_vector_success()
        assert self.monitor._vector_failures == 0

    def test_latency_p99_triggers_alert(self):
        """P99 latency exceeds threshold -> alert emitted."""
        call_count = [0]

        async def capture_handler(alert):
            call_count[0] += 1

        self.monitor._handlers = [capture_handler]
        self.monitor.latency_threshold_ms = 100

        for _ in range(100):
            self.monitor.record_latency(50.0)
        assert call_count[0] == 0

        self.monitor.record_latency(500.0)
        assert call_count[0] == 1

    def test_get_status_returns_dict(self):
        status = self.monitor.get_status()
        assert isinstance(status, dict)
        assert "running" in status
        assert "llm_failures" in status
        assert "vector_failures" in status
        assert "latency_samples" in status


class TestAlertDataclass:
    def test_alert_creation(self):
        alert = Alert(
            level=AlertLevel.CRITICAL,
            source="llm",
            message="LLM failed",
            metric="failure_count",
            value=5.0,
            threshold=3.0,
        )
        assert alert.level == AlertLevel.CRITICAL
        assert alert.source == "llm"
        assert alert.value == 5.0
        assert alert.timestamp > 0

    def test_alert_levels(self):
        assert AlertLevel.INFO.value == "info"
        assert AlertLevel.WARNING.value == "warning"
        assert AlertLevel.CRITICAL.value == "critical"


class TestDefaultAlertHandler:
    @pytest.mark.asyncio
    async def test_default_handler_does_not_raise(self):
        alert = Alert(
            level=AlertLevel.WARNING,
            source="test",
            message="Test alert",
            metric="test_metric",
            value=100.0,
            threshold=50.0,
        )
        await _default_alert_handler(alert)
