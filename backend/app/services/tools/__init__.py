"""
企业级工具集（MySQL 真实数据源）

使用方式：
    from app.services.tools import list_tools, get_tool

    # 获取所有工具
    tools = list_tools()

    # 获取指定工具
    tool = get_tool("order_query")
"""
from app.services.tools.base import (
    ToolBase,
    ToolResult,
    DatabaseMixin,
    register_tool,
    get_tool,
    list_tools,
)

# 导入各工具模块，触发 @register_tool 装饰器执行
from app.services.tools import order_tool
from app.services.tools import logistics_tool
from app.services.tools import refund_tool
from app.services.tools import product_tool

__all__ = [
    "ToolBase",
    "ToolResult",
    "DatabaseMixin",
    "register_tool",
    "get_tool",
    "list_tools",
]
