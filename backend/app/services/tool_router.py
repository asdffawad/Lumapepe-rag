"""
Tool Router - Unified tool dispatch with circuit breaker protection
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from app.services.tools.base import TOOL_REGISTRY, ToolBase, ToolResult
from app.services.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry


TOOL_FALLBACK_MESSAGES = {
    "order_query": "抱歉，暂时无法查询订单，请稍后重试或拨打客服热线 400-123-4567",
    "logistics_query": "物流查询服务暂时不可用，请稍后再试",
    "refund_query": "退款服务暂时繁忙，请稍后在我的订单中自助申请",
    "product_query": "商品查询服务暂时不可用，请稍后再试",
}


class ToolRouter:
    """Tool router with circuit breaker protection per tool"""

    def __init__(self):
        self._tools: dict[str, ToolBase] = {}
        self._register_all_tools()

    def _register_all_tools(self):
        """Instantiate all registered tools at startup"""
        from app.services import tools as _  # noqa: F401

        for name, tool_cls in TOOL_REGISTRY.items():
            self._tools[name] = tool_cls()
            CircuitBreakerRegistry.get(name, failure_threshold=3, cooldown_seconds=30.0)

    async def execute(self, tool_name: str, params: dict, request_id: str) -> ToolResult:
        """Execute tool with circuit breaker protection"""
        if tool_name not in self._tools:
            return ToolResult(success=False, error=f"未知工具: {tool_name}")

        tool = self._tools[tool_name]
        cb = CircuitBreakerRegistry.get(tool_name)

        if not cb.can_execute():
            return ToolResult(
                success=False,
                error=TOOL_FALLBACK_MESSAGES.get(tool_name, "服务暂时繁忙，请稍后再试"),
                source=tool.source,
            )

        try:
            result = await asyncio.wait_for(
                tool.execute(params),
                timeout=tool.get_timeout(),
            )
            cb.record_success()
            return result
        except asyncio.TimeoutError:
            cb.record_failure()
            return ToolResult(
                success=False,
                error="服务响应超时，请稍后再试",
                source=tool.source,
            )
        except Exception as e:
            cb.record_failure()
            return ToolResult(
                success=False,
                error="服务异常，请联系客服",
                source=tool.source,
            )
