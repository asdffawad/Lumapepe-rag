"""
订单查询工具
"""
from app.services.tools.base import ToolBase, ToolResult, DatabaseMixin, register_tool


@register_tool(
    name="order_query",
    description="查询用户订单详情，包括订单状态、商品信息、收货地址。优先使用 order_id 精确查询，未提供则返回用户全部订单。",
    param_schema={
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "用户ID（必填）"},
            "order_id": {"type": "string", "description": "订单号（可选，不提供则返回全部订单）"},
        },
        "required": ["user_id"],
    },
)
class OrderTool(DatabaseMixin, ToolBase):
    _source = "订单系统"
    default_timeout = 3.0

    async def execute(self, params: dict) -> ToolResult:
        user_id = params.get("user_id")
        order_id = params.get("order_id")

        if not user_id:
            return self._error_result("user_id 不能为空")

        return await self._execute_db("查询订单", self._do_query, user_id, order_id)

    async def _do_query(self, repo, user_id: str, order_id: str | None) -> ToolResult:
        if order_id:
            order = await repo.find_by_order_id(order_id)
            if order is None:
                return self._error_result(f"未找到订单号 {order_id}")
            if order.user_id != user_id:
                return self._error_result("无权访问该订单")
            return self._success_result(order.to_dict())
        else:
            orders = await repo.find_by_user(user_id, limit=20)
            if not orders:
                return self._error_result(f"用户 {user_id} 暂无订单记录")
            return self._success_result({
                "orders": [o.to_dict() for o in orders],
                "total": len(orders),
            })
