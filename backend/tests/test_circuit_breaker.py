"""
熔断器测试
"""
import pytest
import time
from app.services.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry, CircuitState


class TestCircuitBreaker:
    """熔断器单元测试"""

    def setup_method(self):
        self.cb = CircuitBreaker(
            name="test_service",
            failure_threshold=3,
            success_threshold=2,
            cooldown_seconds=0.5,
        )

    def test_initial_state_is_closed(self):
        """初始状态应为 CLOSED"""
        assert self.cb.state == CircuitState.CLOSED
        assert self.cb.can_execute() is True

    def test_failures_below_threshold_stay_closed(self):
        """未达到阈值的失败不应触发熔断"""
        self.cb.record_failure()
        self.cb.record_failure()
        assert self.cb.state == CircuitState.CLOSED
        assert self.cb.can_execute() is True

    def test_failures_at_threshold_open_circuit(self):
        """达到阈值的失败应触发熔断"""
        for _ in range(3):
            self.cb.record_failure()
        assert self.cb.state == CircuitState.OPEN
        assert self.cb.can_execute() is False

    def test_success_resets_failure_count(self):
        """成功后应重置失败计数"""
        self.cb.record_failure()
        self.cb.record_failure()
        self.cb.record_success()
        assert self.cb.state == CircuitState.CLOSED
        assert self.cb.failure_count == 0

    def test_half_open_after_cooldown(self):
        """冷却期过后应进入 HALF_OPEN 状态"""
        for _ in range(3):
            self.cb.record_failure()
        assert self.cb.state == CircuitState.OPEN
        assert self.cb.can_execute() is False

        # 等待冷却期
        time.sleep(0.6)
        assert self.cb.can_execute() is True
        assert self.cb.state == CircuitState.HALF_OPEN

    def test_half_open_success_closes_circuit(self):
        """半开状态下的成功应关闭熔断器"""
        for _ in range(3):
            self.cb.record_failure()
        time.sleep(0.6)
        self.cb.can_execute()  # 进入 HALF_OPEN

        self.cb.record_success()
        self.cb.record_success()  # 达到 success_threshold
        assert self.cb.state == CircuitState.CLOSED

    def test_half_open_failure_reopens(self):
        """半开状态下的失败应重新打开熔断器"""
        for _ in range(3):
            self.cb.record_failure()
        time.sleep(0.6)
        self.cb.can_execute()  # 进入 HALF_OPEN
        self.cb.record_failure()
        assert self.cb.state == CircuitState.OPEN

    def test_get_state_returns_dict(self):
        """get_state() 应返回正确的字典"""
        state = self.cb.get_state()
        assert isinstance(state, dict)
        assert state["name"] == "test_service"
        assert state["state"] == "closed"
        assert "failure_count" in state

    def test_custom_thresholds(self):
        """自定义阈值应正确生效"""
        cb = CircuitBreaker(name="custom", failure_threshold=5)
        for _ in range(4):
            cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()  # 第5次
        assert cb.state == CircuitState.OPEN

    def test_repr_contains_name_and_state(self):
        """__repr__ 应包含名称和状态"""
        repr_str = repr(self.cb)
        assert "test_service" in repr_str
        assert "closed" in repr_str


class TestCircuitBreakerRegistry:
    """熔断器注册表测试"""

    def setup_method(self):
        CircuitBreakerRegistry._breakers.clear()

    def test_get_creates_new_breaker(self):
        """get() 应创建新的熔断器"""
        cb = CircuitBreakerRegistry.get("new_service")
        assert isinstance(cb, CircuitBreaker)
        assert cb.name == "new_service"

    def test_get_returns_same_instance(self):
        """get() 应返回同一实例"""
        cb1 = CircuitBreakerRegistry.get("singleton")
        cb2 = CircuitBreakerRegistry.get("singleton")
        assert cb1 is cb2

    def test_reset_clears_state(self):
        """reset() 应重置指定熔断器状态"""
        cb = CircuitBreakerRegistry.get("resetable", failure_threshold=1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        CircuitBreakerRegistry.reset("resetable")
        assert cb.state == CircuitState.CLOSED

    def test_reset_all(self):
        """reset_all() 应重置所有熔断器"""
        cb1 = CircuitBreakerRegistry.get("service1", failure_threshold=1)
        cb2 = CircuitBreakerRegistry.get("service2", failure_threshold=1)
        cb1.record_failure()
        cb2.record_failure()

        CircuitBreakerRegistry.reset_all()
        assert cb1.state == CircuitState.CLOSED
        assert cb2.state == CircuitState.CLOSED

    def test_get_all_states(self):
        """get_all_states() 应返回所有状态"""
        CircuitBreakerRegistry.get("svc1")
        CircuitBreakerRegistry.get("svc2")
        states = CircuitBreakerRegistry.get_all_states()
        assert "svc1" in states
        assert "svc2" in states
