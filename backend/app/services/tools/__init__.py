"""
企业级工具实现（MySQL 真实数据源）
"""
from app.services.tools.base import ToolBase, ToolResult, register_tool


def _get_db_pool():
    from app.database.connection import get_db_pool
    return get_db_pool()


# ---------------------------------------------------------------------------
# 订单查询工具
# ---------------------------------------------------------------------------
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
class OrderTool(ToolBase):
    def get_timeout(self) -> float:
        return 3.0

    async def execute(self, params: dict) -> ToolResult:
        user_id = params.get("user_id")
        order_id = params.get("order_id")

        if not user_id:
            return ToolResult(
                success=False,
                error="user_id 不能为空",
                source="订单系统",
            )

        try:
            from app.database.order import OrderRepository

            pool = _get_db_pool()
            repo = OrderRepository(pool)

            if order_id:
                order = await repo.find_by_order_id(order_id)
                if order is None:
                    return ToolResult(
                        success=False,
                        error=f"未找到订单号 {order_id}",
                        source="订单系统",
                    )
                if order.user_id != user_id:
                    return ToolResult(
                        success=False,
                        error="无权访问该订单",
                        source="订单系统",
                    )
                return ToolResult(
                    success=True,
                    data=order.to_dict(),
                    source="订单系统",
                )
            else:
                orders = await repo.find_by_user(user_id, limit=20)
                if not orders:
                    return ToolResult(
                        success=False,
                        error=f"用户 {user_id} 暂无订单记录",
                        source="订单系统",
                    )
                return ToolResult(
                    success=True,
                    data={
                        "orders": [o.to_dict() for o in orders],
                        "total": len(orders),
                    },
                    source="订单系统",
                )

        except RuntimeError:
            return ToolResult(
                success=False,
                error="数据库连接未初始化，请稍后重试",
                source="订单系统",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"查询订单失败: {e}",
                source="订单系统",
            )


# ---------------------------------------------------------------------------
# 物流查询工具
# ---------------------------------------------------------------------------
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
class LogisticsTool(ToolBase):
    def get_timeout(self) -> float:
        return 3.0

    async def execute(self, params: dict) -> ToolResult:
        logistics_no = params.get("logistics_no")
        if not logistics_no:
            return ToolResult(
                success=False,
                error="logistics_no 不能为空",
                source="物流系统",
            )

        try:
            from app.database.order import OrderRepository

            pool = _get_db_pool()
            repo = OrderRepository(pool)

            order = await repo.find_by_logistics(logistics_no)
            if order is None:
                return ToolResult(
                    success=False,
                    error=f"未找到物流单号 {logistics_no}，请确认单号是否正确",
                    source="物流系统",
                )

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

            return ToolResult(
                success=True,
                data={
                    "order_id": order.order_id,
                    "product_name": order.product_name,
                    "logistics_no": order.logistics_no,
                    "logistics_company": order.logistics_company,
                    "current_status": order.status_text,
                    "address": order.address,
                    "events": events,
                },
                source="物流系统",
            )

        except RuntimeError:
            return ToolResult(
                success=False,
                error="数据库连接未初始化，请稍后重试",
                source="物流系统",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"查询物流失败: {e}",
                source="物流系统",
            )


# ---------------------------------------------------------------------------
# 退款查询工具
# ---------------------------------------------------------------------------
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
class RefundTool(ToolBase):
    def get_timeout(self) -> float:
        return 3.0

    REFUND_POLICY = {
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
            return ToolResult(
                success=False,
                error="order_id 不能为空",
                source="退款系统",
            )
        if not user_id:
            return ToolResult(
                success=False,
                error="user_id 不能为空",
                source="退款系统",
            )

        try:
            from app.database.order import OrderRepository

            pool = _get_db_pool()
            repo = OrderRepository(pool)

            order = await repo.find_by_order_id(order_id)
            if order is None:
                return ToolResult(
                    success=False,
                    error=f"未找到订单号 {order_id}",
                    source="退款系统",
                )
            if order.user_id != user_id:
                return ToolResult(
                    success=False,
                    error="无权访问该订单",
                    source="退款系统",
                )

            eligible = order.status in self.REFUND_POLICY["eligible_statuses"]

            return ToolResult(
                success=True,
                data={
                    "order_id": order.order_id,
                    "product_name": order.product_name,
                    "current_status": order.status,
                    "status_text": order.status_text,
                    "total_price": order.total_price,
                    "can_refund": eligible,
                    "reason": reason,
                    "policy": self.REFUND_POLICY,
                },
                source="退款系统",
            )

        except RuntimeError:
            return ToolResult(
                success=False,
                error="数据库连接未初始化，请稍后重试",
                source="退款系统",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"查询退款信息失败: {e}",
                source="退款系统",
            )
