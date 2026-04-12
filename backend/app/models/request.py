from pydantic import BaseModel, Field
from typing import Optional


class ChatRequest(BaseModel):
    session_id: Optional[str] = Field(None, description="会话ID，首次为空自动生成")
    message: str = Field(..., min_length=1, max_length=2000)
    user_id: str = Field(default="guest")

    model_config = {
        "json_schema_extra": {
            "example": {
                "session_id": "sess-abc123",
                "message": "我想查一下我的订单",
                "user_id": "user-001",
            }
        }
    }


class IntentRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    history: list[dict] = Field(default_factory=list)

    model_config = {
        "json_schema_extra": {
            "example": {
                "message": "这个手机支持5G吗？",
                "history": [],
            }
        }
    }


class DocumentCreateRequest(BaseModel):
    """创建文档并自动摄取"""
    title: str = Field(..., min_length=1, max_length=256, description="文档标题")
    doc_type: str = Field(..., description="文档类型: product / policy / faq / manual")
    content: dict = Field(..., description="文档内容（JSON 对象）")
    source: str = Field(default="manual", description="来源标识")

    model_config = {
        "json_schema_extra": {
            "example": {
                "title": "iPhone 16 Pro 退货政策",
                "doc_type": "policy",
                "content": {
                    "category": "退换货政策",
                    "content": "自收货之日起7天内可无理由退货...",
                },
                "source": "POLICY001",
            }
        }
    }


class DocumentUpdateRequest(BaseModel):
    """更新文档内容并触发重索引"""
    title: str | None = Field(None, max_length=256)
    content: dict | None = Field(None, description="新的文档内容（JSON 对象）")

    model_config = {
        "json_schema_extra": {
            "example": {
                "title": "iPhone 16 Pro 退货政策（修订版）",
                "content": {
                    "category": "退换货政策",
                    "content": "自收货之日起15天内可无理由退货...",
                },
            }
        }
    }
