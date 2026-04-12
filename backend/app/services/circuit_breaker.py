"""
独立熔断器组件
支持对任意服务（LLM、Milvus、外部 API）进行熔断保护
不依赖 ToolRouter，可独立使用
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class CircuitState(Enum):
    CLOSED = "closed"      # 正常，允许请求通过
    OPEN = "open"          # 熔断，拒绝所有请求
    HALF_OPEN = "half_open"  # 半开，放行一个请求试探


@dataclass
class CircuitBreaker:
    """
    熔断器实现

    状态转换：
      closed → (failure_count >= threshold) → open
      open → (cooldown 过后) → half_open
      half_open → (成功) → closed
      half_open → (失败) → open
    """
    name: str
    failure_threshold: int = 3      # 触发熔断的连续失败次数
    success_threshold: int = 2      # 半开状态下需要连续成功次数才关闭
    cooldown_seconds: float = 30.0  # 熔断持续时间
    half_open_max_calls: int = 3    # 半开状态下最大并发试探数

    failure_count: int = 0
    success_count: int = 0
    half_open_calls: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    state: CircuitState = field(default=CircuitState.CLOSED)

    def record_success(self):
        """记录一次成功调用"""
        now = time.monotonic()
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            self.last_success_time = now
            if self.success_count >= self.success_threshold:
                self._reset()
        elif self.state == CircuitState.CLOSED:
            # 成功后重置失败计数
            self.failure_count = 0

    def record_failure(self):
        """记录一次失败调用"""
        now = time.monotonic()
        self.last_failure_time = now
        self.failure_count += 1

        if self.state == CircuitState.CLOSED:
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
        elif self.state == CircuitState.HALF_OPEN:
            # 半开状态下任何失败都重新打开
            self.state = CircuitState.OPEN
            self.half_open_calls = 0
            self.success_count = 0

    def can_execute(self) -> bool:
        """检查是否允许执行请求"""
        now = time.monotonic()

        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            if now - self.last_failure_time >= self.cooldown_seconds:
                self.state = CircuitState.HALF_OPEN
                self.half_open_calls = 0
                self.success_count = 0
                return True
            return False

        if self.state == CircuitState.HALF_OPEN:
            if self.half_open_calls < self.half_open_max_calls:
                self.half_open_calls += 1
                return True
            return False

        return True

    def _reset(self):
        """重置为关闭状态"""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.half_open_calls = 0

    def get_state(self) -> dict:
        """获取当前状态（用于监控）"""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "half_open_calls": self.half_open_calls,
            "last_failure_time": self.last_failure_time,
        }

    def __repr__(self) -> str:
        return (
            f"CircuitBreaker(name={self.name}, state={self.state.value}, "
            f"failures={self.failure_count}, successes={self.success_count})"
        )


class CircuitBreakerRegistry:
    """
    全局限路器注册表
    提供统一的熔断器管理和获取接口
    """
    _breakers: dict[str, CircuitBreaker] = {}

    @classmethod
    def get(cls, name: str, **kwargs) -> CircuitBreaker:
        """获取或创建指定名称的熔断器"""
        if name not in cls._breakers:
            cls._breakers[name] = CircuitBreaker(name=name, **kwargs)
        return cls._breakers[name]

    @classmethod
    def get_all_states(cls) -> dict[str, dict]:
        """获取所有熔断器状态"""
        return {name: cb.get_state() for name, cb in cls._breakers.items()}

    @classmethod
    def reset(cls, name: str):
        """重置指定熔断器"""
        if name in cls._breakers:
            cls._breakers[name]._reset()

    @classmethod
    def reset_all(cls):
        """重置所有熔断器"""
        for cb in cls._breakers.values():
            cb._reset()
