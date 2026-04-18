"""
退款查询工具
"""
from app.services.tools.base import ToolBase, ToolResult, DatabaseMixin, register_tool


@register_tool(
    name="refund_query",
    description="查询退款进度或申请退换货，包含退款政策说明",
    param_schema={
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "用户ID（必填）"},
            "order_id": {"type": "string", "description": "订单号（必填）"},
            "reason": {"type": "string", "description": "退款原因（可选）"},
        },
        "required": ["user_id", "order_id"],
    },
)
class RefundTool(DatabaseMixin, ToolBase):
    _source = "退款系统"
    default_timeout = 3.0

    POLICY = {
        "eligible_statuses": ["delivered", "shipped"],
        "steps": [
            "1. 在订单详情页点击「申请退款」，填写退款原因",
            "2. 等待客服审核（1-2个工作日内完成）",
            "3. 审核通过后，货款原路返回",
            "4. 银行卡/信用卡退款 3-5 个工作日到账",
            "5. 退款成功后短信通知",
        ],
        "notes": [
            "已发货但未签收商品，需先收货再申请退款",
            "定制商品、生鲜食品、内衣等特殊商品不支持七天无理由",
            "因质量问题退换货，运费由商家承担",
        ],
        "support_contact": "客服热线：400-888-8888",
    }

    async def execute(self, params: dict) -> ToolResult:
        order_id = params.get("order_id")
        user_id = params.get("user_id")
        reason = params.get("reason", "用户主动取消")

        if not order_id:
            return self._error_result("order_id 不能为空")
        if not user_id:
            return self._error_result("user_id 不能为空")

        return await self._execute_db("查询退款信息", self._do_query, order_id, user_id, reason)

    async def _do_query(self, repo, order_id: str, user_id: str, reason: str) -> ToolResult:
        order = await repo.find_by_order_id(order_id)
        if order is None:
            return self._error_result(f"未找到订单号 {order_id}")
        if order.user_id != user_id:
            return self._error_result("无权访问该订单")

        return self._success_result({
            "order_id": order.order_id,
            "product_name": order.product_name,
            "current_status": order.status,
            "status_text": order.status_text,
            "total_price": order.total_price,
            "can_refund": order.status in self.POLICY["eligible_statuses"],
            "reason": reason,
            "policy": self.POLICY,
        })
