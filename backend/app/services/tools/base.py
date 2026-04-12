"""
Tool 基类和注册装饰器
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


TOOL_REGISTRY: dict[str, type["ToolBase"]] = {}


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

    @abstractmethod
    async def execute(self, params: dict) -> ToolResult:
        pass

    def get_timeout(self) -> float:
        return 5.0


def register_tool(name: str, description: str, param_schema: dict):
    def decorator(cls: type[ToolBase]):
        cls.name = name
        cls.description = description
        cls.param_schema = param_schema
        TOOL_REGISTRY[name] = cls
        return cls
    return decorator
