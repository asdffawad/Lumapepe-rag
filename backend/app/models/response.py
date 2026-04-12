from pydantic import BaseModel, Field
from typing import Optional


class ChatResponse(BaseModel):
    session_id: str
    message: str
    intent: str
    sources: list[str] = Field(default_factory=list)
    tokens_used: int = 0
    latency_ms: int = 0
    timestamp: str
    tool_result: Optional[dict] = Field(default=None, description="工具调用原始结果（仅 order_query/refund_return 意图时有效）")


class IntentResponse(BaseModel):
    intent: str
    confidence: float = 0.0
    message: str = ""


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: Optional[str] = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
