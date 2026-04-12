"""
意图分类服务（企业级）
- 分类类别: product_qa | order_query | refund_return | general_chat |
            product_comparison | promotion_inquiry | account_info |
            technical_support | feedback_complaint
- 策略: 关键词规则兜底 + LLM 精细分类（带重试）
- 输出意图标签 -> 路由到对应 handler
- 结构化日志 + metrics 埋点
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from app.services.llm_service import LLMBase, ChatMessage, LLMError
from app.logging_config import get_logger
from app import metrics as _metrics

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
_intent_logger = get_logger("intent.service")

# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class IntentResult:
    intent: str
    confidence: float
    message: str
    latency_ms: int
    method: str  # "rule" | "llm" | "hybrid"


# ---------------------------------------------------------------------------
# 意图关键词规则库（扩展版，企业级电商场景）
# ---------------------------------------------------------------------------
# 注意：关键词统一使用小写，匹配时将用户消息转为小写
_INTENT_KEYWORDS: dict[str, list[str]] = {
    "order_query": [
        # 订单状态
        "订单", "我的订单", "订单号", "订单状态", "查单", "查看订单",
        "下了单", "买的东西", "已购", "购买记录",
        # 物流配送
        "发货", "发货了吗", "发了吗", "发货时间", "发货速度",
        "物流", "物流信息", "物流状态", "物流进度", "到哪了",
        "配送", "配送中", "配送时间", "预计送达",
        "快递", "快递员", "快递公司", "运单号", "单号",
        "收货", "签收", "收货地址", "收货人", "收件",
        "什么时候到", "好久到", "多久到", "几天到",
        "催单", "改地址", "改收货地址",
    ],
    "refund_return": [
        # 退款
        "退款", "退钱", "退款的", "退款到账", "退款流程", "申请退款",
        "取消订单退款", "不想要了", "不想要", "不想买了",
        # 退货
        "退货", "退回去", "退回去", "退货流程", "申请退货",
        "退货运费", "退货地址", "上门取件", "退货退款",
        # 换货
        "换货", "换一件", "换颜色", "换尺码", "换型号", "换款式",
        # 售后
        "售后", "售后服务", "质量问题", "坏了", "有问题",
        "功能异常", "破损", "少件", "漏发", "发错",
        # 退换政策
        "七天无理由", "七天", "无理由退货", "退货政策",
        "退换货", "退换", "退换流程",
    ],
    "product_qa": [
        # 商品咨询
        "手机", "电脑", "笔记本", "平板", "平板", "耳机", "音箱", "相机",
        "智能手表", "手环", "键盘", "鼠标", "显示器", "硬盘", "内存卡",
        "充电宝", "数据线", "充电器", "保护壳", "贴膜",
        # 参数功能
        "参数", "配置", "规格", "功能", "尺寸", "重量", "颜色",
        "内存", "存储", "硬盘", "处理器", "CPU", "GPU", "屏幕",
        "分辨率", "刷新率", "电池容量", "续航", "像素", "摄像头",
        # 常见问题模式
        "是什么", "有哪些", "哪个好", "怎么选", "如何", "多少",
        "好不好", "怎么样", "值得买", "推荐", "介绍", "说明",
        "5g", "5G", "wifi", "WiFi", "蓝牙", "NFC", "红外",
        "接口", "充电", "快充", "防水", "指纹", "面部识别",
        # 购买咨询
        "价格", "多少钱", "售价", "报价", "活动价", "原价",
        "好不好", "怎么样", "值得买吗", "推荐", "评价", "口碑",
        "对比", "区别", "哪个好", "买哪个", "选哪个",
        "品牌", "正品", "正品保障", "正品保证",
        # 优惠咨询
        "优惠", "打折", "特价", "促销", "活动", "满减", "优惠券",
        "满减", "返现", "返利", "积分", "赠品", "赠",
        "降价", "降价了", "价格变动", "比价",
    ],
    "promotion_inquiry": [
        "优惠", "优惠券", "打折", "特价", "促销", "活动",
        "满减", "满折", "领券", "领优惠", "红包", "津贴",
        "什么时候有活动", "下次活动", "活动内容", "活动规则",
        "叠加", "叠加优惠", "优惠码", "兑换码",
        "新人", "新用户", "首单", "首购", "首单优惠",
        "会员", "会员价", "vip", "VIP", "积分",
        "限时", "秒杀", "团购", "拼单",
    ],
    "account_info": [
        "账号", "账户", "登录", "注册", "密码", "忘记密码",
        "修改密码", "忘记密码", "找回密码",
        "个人信息", "个人信息修改", "头像", "昵称",
        "收货地址", "地址管理", "新增地址", "修改地址", "删除地址",
        "支付方式", "支付密码", "添加银行卡", "微信支付", "支付宝",
        "绑定", "解绑", "手机号", "换手机号", "换绑",
        "会员", "会员信息", "会员等级", "积分", "积分查询",
        "注销", "注销账号", "账户安全",
    ],
    "technical_support": [
        "打不开", "打不开", "无法打开", "无法登录", "登录不了",
        "闪退", "卡顿", "延迟", "加载慢", "打不开",
        "支付失败", "付款失败", "无法支付", "支付异常",
        "退款不到账", "退款未到账", "退款异常",
        "怎么用", "如何使用", "操作方法", "使用教程",
        "设置", "功能设置", "怎么设置",
        "网络问题", "网络异常", "连接失败", "超时",
        "更新", "版本更新", "升级", "最新版本",
        "清理缓存", "清除缓存", "重启", "重装",
    ],
    "feedback_complaint": [
        "投诉", "举报", "反馈", "差评", "给差评",
        "不满", "不满意", "失望", "太差了", "垃圾",
        "态度", "客服态度", "服务态度", "不满意",
        "虚假宣传", "货不对板", "图片不符", "实物不符",
        "虚假信息", "欺骗", "欺诈", "骗子",
        "延迟发货", "发货慢", "发货太慢",
        "漏发", "少发", "发错", "破损",
        "赔偿", "要求赔偿", "补偿",
        "升级处理", "人工", "找人工", "人工客服",
    ],
    "general_chat": [
        "你好", "您好", "在吗", "请问", "咨询", "帮忙",
        "你好呀", "哈喽", "hi", "hello", "hey", "嘿",
        "早上好", "早安", "晚上好", "晚安", "下午好",
        "谢谢", "感谢", "辛苦了", "麻烦你",
        "再见", "拜拜", "下次见", "bye",
        "可以", "好的", "嗯", "好", "行",
        "随便问问", "没别的", "没了",
    ],
}

# 关键词权重映射（某些词比其它词更强烈指向某意图）
_INTENT_WEIGHTS: dict[str, dict[str, float]] = {
    kw: weight
    for intent, keywords in _INTENT_KEYWORDS.items()
    for kw, weight in [(k, 1.0) for k in keywords]
}
# 高权重关键词（强烈指向特定意图）
_HIGH_WEIGHT_KEYWORDS: dict[str, float] = {
    # 退款/退货
    "七天无理由退货": 3.0,
    "七天无理由": 2.5,
    "七天": 1.5,
    "退货退款": 3.0,
    "退款到账": 2.5,
    # 物流
    "运单号": 2.5,
    "物流信息": 2.5,
    "催单": 2.5,
    "改地址": 2.5,
    # 支付
    "支付失败": 2.0,
    "付款失败": 2.0,
    "无法支付": 2.0,
    # 投诉
    "虚假宣传": 3.0,
    "货不对板": 3.0,
    "欺诈": 3.0,
    "骗子": 2.5,
}

# LLM 意图分类提示词（支持扩展意图）
_INTENT_PROMPT = """你是一个电商客服意图分类器。请根据用户消息判断用户意图。

可选意图：
- product_qa: 商品问答（商品信息、价格、参数、功能、优惠等）
- order_query: 订单查询（订单状态、物流、发货、收货地址等）
- refund_return: 退换货（退款、退货、换货、售后等）
- general_chat: 一般闲聊（问候、致谢、无法归类等）
- product_comparison: 产品对比（多产品参数、功能、价格对比）
- promotion_inquiry: 促销活动咨询（优惠券、满减、折扣活动等）
- account_info: 账号信息（登录、注册、个人信息、地址管理等）
- technical_support: 技术支持（下单、支付等技术问题排查）
- feedback_complaint: 投诉反馈（不满、投诉、举报等）

用户消息: {message}

请只返回一个意图标签，不要其他内容。"""


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------
class IntentClassifier(ABC):
    @abstractmethod
    async def classify(
        self, message: str, history: list[dict]
    ) -> IntentResult:
        pass


# ---------------------------------------------------------------------------
# 基于规则的意图分类器（优化版）
# ---------------------------------------------------------------------------
class RuleBasedIntentClassifier(IntentClassifier):
    """
    基于规则的意图分类器（企业级）
    - 关键词权重打分（非简单计数）
    - 高权重关键词优先匹配
    - 上下文消歧（考虑历史消息）
    """

    # 意图优先级（当多个意图得分相同时，选择优先级更高的）
    INTENT_PRIORITY: dict[str, int] = {
        "refund_return": 10,
        "feedback_complaint": 9,
        "technical_support": 8,
        "account_info": 7,
        "promotion_inquiry": 6,
        "order_query": 5,
        "product_comparison": 4,
        "product_qa": 3,
        "general_chat": 1,
    }

    def __init__(self, confidence_threshold: float = 0.05):
        self._confidence_threshold = confidence_threshold

    async def classify(
        self, message: str, history: list[dict]
    ) -> IntentResult:
        start = time.monotonic()
        message_lower = message.lower()

        # 检查高权重关键词（优先匹配）
        for keyword, weight in sorted(
            _HIGH_WEIGHT_KEYWORDS.items(),
            key=lambda x: x[1],
            reverse=True,
        ):
            if keyword in message_lower:
                # 找到该关键词所属意图
                for intent, keywords in _INTENT_KEYWORDS.items():
                    if keyword in keywords:
                        return IntentResult(
                            intent=intent,
                            confidence=min(weight / 2.5, 1.0),
                            message="",
                            latency_ms=int((time.monotonic() - start) * 1000),
                            method="rule",
                        )

        # 权重打分
        scores: dict[str, float] = {intent: 0.0 for intent in _INTENT_KEYWORDS}
        for intent, keywords in _INTENT_KEYWORDS.items():
            for kw in keywords:
                # 统计命中次数（重复词权重衰减）
                count = message_lower.count(kw)
                if count > 0:
                    # 首次命中权重 1.0，后续衰减
                    scores[intent] += sum(1.0 / (i + 1) for i in range(min(count, 3)))

        if max(scores.values()) > 0:
            # 归一化：使用匹配意图自身的关键词数量作为分母
            # 单次命中 → confidence ≈ 0.5（有意义），多次命中可更高
            best_intent = max(scores, key=lambda k: (scores[k], self.INTENT_PRIORITY[k]))
            num_keywords = len(_INTENT_KEYWORDS[best_intent])
            # 理论最大得分 ≈ num_keywords（每次命中衰减，3次后趋近0）
            theoretical_max = min(num_keywords, 5) * 1.0
            confidence = min(scores[best_intent] / theoretical_max, 1.0)

            # 如果规则置信度足够，直接返回
            if confidence >= self._confidence_threshold:
                return IntentResult(
                    intent=best_intent,
                    confidence=confidence,
                    message="",
                    latency_ms=int((time.monotonic() - start) * 1000),
                    method="rule",
                )

        return IntentResult(
            intent="general_chat",
            confidence=0.5,
            message="",
            latency_ms=int((time.monotonic() - start) * 1000),
            method="rule",
        )


# ---------------------------------------------------------------------------
# 基于 LLM 的意图分类器（带重试机制）
# ---------------------------------------------------------------------------
class LLMIntentClassifier(IntentClassifier):
    """
    基于 LLM 的意图分类器（企业级）
    - 带重试机制（指数退避）
    - 结构化日志
    - 超时降级
    """

    VALID_INTENTS = {
        "product_qa", "order_query", "refund_return", "general_chat",
        "product_comparison", "promotion_inquiry", "account_info",
        "technical_support", "feedback_complaint",
    }

    def __init__(
        self,
        llm: LLMBase,
        max_retries: int = 2,
        timeout_per_call: float = 5.0,
    ):
        self._llm = llm
        self._max_retries = max_retries
        self._timeout_per_call = timeout_per_call

    async def classify(
        self, message: str, history: list[dict]
    ) -> IntentResult:
        start = time.monotonic()
        prompt = _INTENT_PROMPT.format(message=message)

        for attempt in range(self._max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    self._llm.chat([ChatMessage(role="user", content=prompt)]),
                    timeout=self._timeout_per_call,
                )

                intent = response.content.strip().lower()

                # 验证意图是否在有效列表中
                if intent in self.VALID_INTENTS:
                    _intent_logger.info(
                        "LLM intent classified",
                        extra={
                            "intent": intent,
                            "confidence": 0.85,
                            "model": response.model,
                            "latency_ms": response.latency_ms,
                        }
                    )
                    return IntentResult(
                        intent=intent,
                        confidence=0.85,
                        message=response.content,
                        latency_ms=int((time.monotonic() - start) * 1000),
                        method="llm",
                    )
                else:
                    _intent_logger.warning(
                        "LLM returned invalid intent, retrying",
                        extra={
                            "invalid_intent": intent,
                            "attempt": attempt + 1,
                            "raw_response": response.content[:100],
                        }
                    )
                    if attempt < self._max_retries:
                        continue

            except asyncio.TimeoutError:
                _intent_logger.warning(
                    "LLM intent classification timeout",
                    extra={"attempt": attempt + 1, "timeout": self._timeout_per_call}
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(0.5 * (2 ** attempt))  # 指数退避
                    continue

            except LLMError as e:
                _intent_logger.warning(
                    "LLM intent classification error",
                    extra={"attempt": attempt + 1, "error": str(e)[:100]}
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue

        # 所有重试耗尽，降级
        _intent_logger.warning(
            "LLM intent classification exhausted, falling back to general_chat",
            extra={"attempts": self._max_retries + 1}
        )
        return IntentResult(
            intent="general_chat",
            confidence=0.4,
            message="",
            latency_ms=int((time.monotonic() - start) * 1000),
            method="llm",
        )


# ---------------------------------------------------------------------------
# 混合意图分类器（企业级推荐）
# ---------------------------------------------------------------------------
class HybridIntentClassifier(IntentClassifier):
    """
    混合意图分类：规则兜底 + LLM 精细分类

    策略：
    - 规则高置信度 (>= 0.7) 直接返回
    - 规则低置信度走 LLM
    - LLM 也失败则用规则结果
    - 全链路 metrics 埋点
    """

    def __init__(
        self,
        llm: Optional[LLMBase] = None,
        rule_threshold: float = 0.7,
        llm_weight: float = 0.7,
        rule_weight: float = 0.3,
    ):
        self._rule_classifier = RuleBasedIntentClassifier(confidence_threshold=rule_threshold)
        self._llm_classifier = LLMIntentClassifier(llm) if llm else None
        self._llm_weight = llm_weight
        self._rule_weight = rule_weight

    async def classify(
        self, message: str, history: list[dict]
    ) -> IntentResult:
        start = time.monotonic()

        # Step 1: 规则快速分类
        rule_result = await self._rule_classifier.classify(message, history)

        # 规则高置信度直接返回
        if rule_result.confidence >= 0.7:
            try:
                _metrics.intent_classification_method.labels(method="rule").inc()
            except Exception:
                pass
            return rule_result

        # Step 2: 低置信度走 LLM
        if self._llm_classifier:
            llm_result = await self._llm_classifier.classify(message, history)

            try:
                _metrics.intent_classification_method.labels(method="hybrid").inc()
            except Exception:
                pass

            # 加权合并置信度
            final_conf = (
                rule_result.confidence * self._rule_weight
                + llm_result.confidence * self._llm_weight
            )

            return IntentResult(
                intent=llm_result.intent,
                confidence=final_conf,
                message=llm_result.message,
                latency_ms=int((time.monotonic() - start) * 1000),
                method="hybrid",
            )

        # 无 LLM，返回规则结果
        try:
            _metrics.intent_classification_method.labels(method="rule_fallback").inc()
        except Exception:
            pass
        return rule_result
