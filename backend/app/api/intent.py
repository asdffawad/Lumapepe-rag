"""
Intent API 路由
POST /api/v1/intent - 意图分类接口

用途：给定用户消息和可选的对话历史，返回用户意图分类结果。

支持的意图类型：
- product_qa      : 商品咨询（查询规格、价格、库存等）
- order_status    : 订单状态查询（发货、物流、收货等）
- refund_exchange : 退换货咨询
- payment_issue   : 支付问题
- price_protect   : 价格保护
- complaint       : 投诉建议
- chitchat        : 闲聊（不在知识库范围内）
- unknown         : 未知意图

使用示例：
    curl -X POST http://localhost:8000/api/v1/intent \\
      -H "Authorization: Bearer <token>" \\
      -H "Content-Type: application/json" \\
      -d '{"message": "我的订单什么时候能到？"}'
"""
from fastapi import APIRouter, Depends

from app.services.intent_service import IntentClassifier
from app.models.request import IntentRequest
from app.models.response import IntentResponse

router = APIRouter(prefix="/api/v1", tags=["intent"])


def get_intent_classifier() -> IntentClassifier:
    from app.main import app as _app
    return _app.state.intent_classifier


@router.post("/intent", response_model=IntentResponse)
async def classify_intent(
    request: IntentRequest,
    classifier: IntentClassifier = Depends(get_intent_classifier),
):
    result = await classifier.classify(request.message, request.history)
    return IntentResponse(
        intent=result.intent,
        confidence=result.confidence,
        message=result.message,
    )
