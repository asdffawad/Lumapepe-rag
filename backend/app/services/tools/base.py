"""
Tool 基类和注册装饰器
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
import logging


TOOL_REGISTRY: dict[str, type["ToolBase"]] = {}
_logger = logging.getLogger("tools")


@dataclass
class ToolResult:
    success: bool
    data: Any | None = None
    error: str | None = None
    source: str = ""


class ToolBase(ABC):
    name: str = ""
    description: str = ""
    param_schema: dict = {}
    default_timeout: float = 5.0

    def get_timeout(self) -> float:
        return self.default_timeout

    @abstractmethod
    async def execute(self, params: dict) -> ToolResult:
        pass

    def _log_info(self, message: str, **kwargs):
        _logger.info(f"[{self.name}] {message}", extra=kwargs)

    def _log_error(self, message: str, **kwargs):
        _logger.error(f"[{self.name}] {message}", extra=kwargs)


class DatabaseMixin:
    """数据库访问混入类"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pool = None
        self._repo = None

    @property
    def source(self) -> str:
        """工具来源标识，由子类覆盖"""
        return getattr(self, "_source", "系统")

    def _get_db_pool(self):
        if self._pool is None:
            from app.database.connection import get_db_pool
            self._pool = get_db_pool()
        return self._pool

    def _get_order_repo(self):
        if self._repo is None:
            from app.database.order import OrderRepository
            self._repo = OrderRepository(self._pool)
        return self._repo

    def _success_result(self, data) -> ToolResult:
        """构建成功结果"""
        return ToolResult(success=True, data=data, source=self.source)

    def _error_result(self, error: str) -> ToolResult:
        """构建错误结果"""
        return ToolResult(success=False, error=error, source=self.source)

    async def _execute_db(self, operation_name: str, operation, *args):
        """执行数据库操作，自动处理异常"""
        try:
            from app.database.order import OrderRepository
            pool = self._get_db_pool()
            repo = OrderRepository(pool)
            result = await operation(repo, *args)
            return result
        except RuntimeError:
            return ToolResult(
                success=False,
                error="数据库连接未初始化，请稍后重试",
                source=self.source,
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"{operation_name}失败: {e}",
                source=self.source,
            )


def register_tool(name: str, description: str, param_schema: dict):
    def decorator(cls: type[ToolBase]):
        cls.name = name
        cls.description = description
        cls.param_schema = param_schema
        TOOL_REGISTRY[name] = cls
        return cls
    return decorator


def get_tool(name: str) -> ToolBase | None:
    """获取指定名称的工具实例"""
    cls = TOOL_REGISTRY.get(name)
    return cls() if cls else None


def list_tools() -> list[dict]:
    """获取所有已注册工具的元信息"""
    return [
        {
            "name": cls.name,
            "description": cls.description,
            "param_schema": cls.param_schema,
        }
        for cls in TOOL_REGISTRY.values()
    ]
