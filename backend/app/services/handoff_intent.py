"""
转人工意图识别模块

提供基于规则的转人工意图识别功能，用于自动检测用户消息中是否包含转人工客服的意图。
"""
from __future__ import annotations

import re
from typing import Optional


# 强触发短语：直接触发转人工
STRONG_TRIGGERS = [
    "转人工",
    "我要找人工",
    "帮我转人工",
    "联系客服",
    "真人客服",
    "找人处理",
    "我要人工",
    "帮我找人工",
    "请求人工",
    "需要人工",
]

# 弱触发词：需要结合上下文判断
WEAK_TRIGGERS = [
    "人工",
    "客服",
    "投诉",
    "退款",
    "售后",
    "订单异常",
    "人工处理",
    "人工介入",
]

# 排除模式：问句形式（咨询性质，不自动转人工）
EXCLUDE_PATTERNS = [
    r"什么是.*人工",
    r"怎么.*人工",
    r"如何.*人工",
    r"有没有.*人工",
    r"有人工.*吗",
    r"人工.*怎么",
    r"人工.*如何",
    r"人工.*设置",
    r"人工.*开通",
    r"人工.*开通",
    r"人工.*收费",
    r"人工.*价格",
    r"人工.*要钱",
    r"人工.*免费",
    r"人工.*联系",
    r"人工.*咨询",
    r"人工.*客服.*流程",
    r"人工.*服务.*流程",
    r"开通.*人工",
    r"申请.*人工",
    r"人工.*申请",
]


def _normalize(text: str) -> str:
    """文本归一化处理"""
    if not text:
        return ""
    # strip, lower, 去除多余空格
    normalized = text.strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _is_question_form(text: str) -> bool:
    """判断是否为问句形式（咨询性质）"""
    for pattern in EXCLUDE_PATTERNS:
        if re.search(pattern, text):
            return True
    return False


def is_handoff_intent(text: str) -> bool:
    """
    判断用户消息是否包含转人工意图。

    规则：
    1. 文本为空时返回 False
    2. 进行文本归一化
    3. 命中强触发短语直接返回 True
    4. 命中弱触发词但不是问句形式时返回 True
    5. 排除模式（问句形式）返回 False

    Args:
        text: 用户输入的原始文本

    Returns:
        True 如果用户消息表达转人工意图，否则 False
    """
    if not text:
        return False

    normalized = _normalize(text)

    # 排除问句形式
    if _is_question_form(normalized):
        return False

    # 强触发短语检查
    for trigger in STRONG_TRIGGERS:
        if trigger in normalized:
            return True

    # 弱触发词检查（需要排除问句形式）
    for trigger in WEAK_TRIGGERS:
        if trigger in normalized:
            # 额外检查：如果包含"人工客服"且前面有问号，可能是问句
            if trigger == "人工" and "人工客服" in normalized:
                # "人工客服" + 问句词 = 大概率是咨询
                if any(q in normalized for q in ["吗", "呢", "怎么", "如何", "什么"]):
                    continue
            return True

    return False


def get_intent_keywords() -> dict:
    """
    返回当前意图识别的关键词配置（用于调试和文档）。

    Returns:
        包含 strong_triggers, weak_triggers, exclude_patterns 的字典
    """
    return {
        "strong_triggers": STRONG_TRIGGERS,
        "weak_triggers": WEAK_TRIGGERS,
        "exclude_patterns": EXCLUDE_PATTERNS,
    }
