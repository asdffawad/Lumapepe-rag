"""
物流查询工具
"""
from app.services.tools.base import ToolBase, ToolResult, DatabaseMixin, register_tool


@register_tool(
    name="logistics_query",
    description="根据物流单号查询物流进度和配送状态",
    param_schema={
        "type": "object",
        "properties": {
            "logistics_no": {"type": "string", "description": "物流单号"},
        },
        "required": ["logistics_no"],
    },
)
class LogisticsTool(DatabaseMixin, ToolBase):
    _source = "物流系统"
    default_timeout = 3.0

    async def execute(self, params: dict) -> ToolResult:
        logistics_no = params.get("logistics_no")
        if not logistics_no:
            return self._error_result("logistics_no 不能为空")

        return await self._execute_db("查询物流", self._do_query, logistics_no)

    async def _do_query(self, repo, logistics_no: str) -> ToolResult:
        order = await repo.find_by_logistics(logistics_no)
        if order is None:
            return self._error_result(f"未找到物流单号 {logistics_no}，请确认单号是否正确")

        events = self._build_events(order)
        return self._success_result({
            "order_id": order.order_id,
            "product_name": order.product_name,
            "logistics_no": order.logistics_no,
            "logistics_company": order.logistics_company,
            "current_status": order.status_text,
            "address": order.address,
            "events": events,
        })

    def _build_events(self, order) -> list[dict]:
        events = []
        if order.shipped_at:
            events.append({
                "time": order.shipped_at.isoformat(),
                "location": "商家已发货",
                "status": "已发货",
            })
        if order.status == "shipped":
            events.append({
                "time": order.created_at.isoformat(),
                "location": f"{order.logistics_company} {order.logistics_no}",
                "status": "配送中",
            })
        if order.delivered_at:
            events.append({
                "time": order.delivered_at.isoformat(),
                "location": order.address or "客户收货地址",
                "status": "已签收",
            })
        return events
