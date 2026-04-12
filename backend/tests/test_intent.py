"""
意图分类服务测试
"""
import pytest
from app.services.intent_service import (
    RuleBasedIntentClassifier,
    HybridIntentClassifier,
    IntentResult,
)


class TestRuleBasedIntentClassifier:
    @pytest.fixture
    def classifier(self):
        return RuleBasedIntentClassifier()

    @pytest.mark.asyncio
    async def test_order_query_intent(self, classifier):
        result = await classifier.classify("我想查一下我的订单", [])
        assert result.intent == "order_query"
        assert result.confidence > 0

    @pytest.mark.asyncio
    async def test_refund_intent(self, classifier):
        result = await classifier.classify("这个商品我想退款", [])
        assert result.intent == "refund_return"

    @pytest.mark.asyncio
    async def test_product_qa_intent(self, classifier):
        result = await classifier.classify("iPhone 16 手机支持5G吗？", [])
        assert result.intent == "product_qa"

    @pytest.mark.asyncio
    async def test_general_chat_intent(self, classifier):
        result = await classifier.classify("你好，请问有什么可以帮助的？", [])
        assert result.intent == "general_chat"

    @pytest.mark.asyncio
    async def test_confidence_normalized(self, classifier):
        # 单关键词
        r1 = await classifier.classify("手机", [])
        # 多关键词
        r2 = await classifier.classify("iPhone 16 手机 5G 价格 多少钱", [])
        assert 0 <= r1.confidence <= 1.0
        assert 0 <= r2.confidence <= 1.0
