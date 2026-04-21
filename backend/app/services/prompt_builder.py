"""
Prompt 动态构建器
- 动态模板: 根据意图类型选择不同 prompt 模板
- 上下文注入: 会话历史摘要 + 召回文档 + 用户画像
- Token 控制: 基于 token 预算动态截断（按句子边界截断）
- Few-shot 示例: 为复杂场景注入示例提升准确率
- Prompt 版本管理: 支持 A/B 测试
- Token 精确计数: 集成 tiktoken
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from app.services.reranker import RerankedDoc

if TYPE_CHECKING:
    from app.services.tools.base import ToolResult


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class Prompt:
    system: str
    user: str
    system_tokens: int
    user_tokens: int
    version: str = "v1"


# ---------------------------------------------------------------------------
# System Prompt 模板（按意图分类）
# ---------------------------------------------------------------------------
SYSTEM_TEMPLATES: dict[str, str] = {
    "product_qa": """你是一位专业的电商商品顾问，请根据提供的商品知识库内容，准确回答用户关于商品的问题。

回答规范：
- 只基于知识库内容回答，不要编造信息
- 如知识库中没有相关信息，请明确告知用户"暂无该信息，建议您联系在线客服"
- 回答要专业、友好、简洁
- 涉及价格时，注意单位（元/人民币）""",

    "order_query": """你是一位电商订单客服，请根据订单信息准确回答用户关于订单状态、物流进度、收货地址等问题。

【重要规则 - 必须遵守】
1. 【订单系统查询结果】中的数据是用户的**真实数据**，必须以此为准
2. **禁止**编造任何订单信息（商品名称、价格、状态、数量等）
3. **禁止**预测送达时间、预计到达时间
4. 如物流信息显示"已发货"，仅告知"已发货"，不要补充任何时间预测
5. 如物流信息显示"已签收"，如实告知，不要预测

回答规范：
- 严格使用【订单系统查询结果】中的数据，不要猜测或补充不存在的订单
- 如查询结果显示"暂无订单"，如实告知用户
- 如查询结果包含物流信息，按实际状态回答，不添加任何时间预测
- 如用户未提供具体订单号，先引导用户提供，或展示全部订单供用户选择
- 回答要专业、及时、友好""",

    "refund_return": """你是一位电商售后客服，请根据退换货政策帮助用户解决退换货问题。

回答规范：
- 只基于知识库内容回答，不要编造退换货条件、操作步骤、时效天数、联系方式
- 如知识库中某项信息未明确记载，明确告知用户"暂无该信息"
- 如知识库中没有相关政策，告知用户"暂无相关信息，建议联系在线客服"
- 回答要耐心、细致、同理心
- 涉及时限（如 7 天无理由），明确说明起算日期""",

    "general_chat": """你是一位友善的电商客服，请与用户进行自然友好的对话。

回答规范：
- 友好、热情、专业
- 适当引导用户了解商品或服务
- 不要过度营销，保持中立
- 若用户表达不满，优先安抚情绪
- 遇到超出知识库范围的问题，不要编造内容，明确告知用户建议通过相关功能或联系在线客服""",

    # 企业级扩展意图
    "product_comparison": """你是一位专业的电商产品对比顾问。请根据提供的商品信息，客观对比多个产品的参数、功能、价格等信息。

回答规范：
- 客观列出各产品优缺点
- 用表格或对比格式呈现关键差异
- 不偏袒特定品牌或产品
- 基于事实数据，不做主观评价""",

    "promotion_inquiry": """你是一位电商促销活动咨询顾问。请根据当前活动和促销信息，准确回答用户关于优惠、折扣、满减、赠品等问题。

回答规范：
- 只基于知识库中明确记载的优惠信息回答，不要编造任何优惠内容
- 如知识库中没有当前促销活动的相关信息，明确告知用户"暂无相关优惠信息"，并可建议用户关注官网公告或联系在线客服
- 涉及优惠有效期、适用范围时，基于已有信息回答，不要添加不存在的条件""",

    "account_info": """你是一位电商账号客服。请帮助用户查询或修改账号信息，包括个人信息、地址管理、支付方式等。

回答规范：
- 查询类操作需核实用户身份
- 修改类操作需二次确认
- 涉及敏感信息，引导用户到官方 App/网页操作
- 保持专业和耐心""",

    "technical_support": """你是一位电商技术支持客服。请帮助用户解决下单、支付、物流追踪等技术问题。

回答规范：
- 先确认问题现象，再逐一排查原因
- 提供清晰的解决步骤
- 如无法远程解决，引导用户联系人工客服
- 涉及退款金额需与用户确认""",

    "feedback_complaint": """你是一位电商客服，负责处理用户反馈和投诉。

回答规范：
- 先倾听并认可用户情绪，不要急于辩解
- 记录问题细节，承诺跟进时间
- 无法立即解决时，说明处理流程和预期时间
- 跟进处理结果""",
}

DEFAULT_SYSTEM = SYSTEM_TEMPLATES["general_chat"]

# Prompt 版本映射（支持 A/B 测试）
_PROMPT_VERSIONS: dict[str, dict[str, str]] = {
    "v1": SYSTEM_TEMPLATES,
}


# ---------------------------------------------------------------------------
# User Prompt 模板
# ---------------------------------------------------------------------------
USER_TEMPLATE = """【用户问题】
{query}

【相关知识】
{context}

【对话历史】
{history}

请根据以上信息回答用户问题。"""


# ---------------------------------------------------------------------------
# Tool Result 格式化（注入 Prompt）
# ---------------------------------------------------------------------------
def format_tool_result_for_prompt(tool_result: "ToolResult") -> str:
    """将 ToolResult 结构化为可读文本，注入到 Prompt 上下文中"""
    from app.services.tools.base import ToolResult

    if not tool_result.success:
        return f"【{tool_result.source} 查询失败】\n{tool_result.error or '未知错误'}"

    data = tool_result.data
    source = tool_result.source

    if source == "订单系统" and isinstance(data, dict):
        if "orders" in data:
            lines = [f"【{source}】查询到用户共 {data.get('total', 0)} 条订单：\n"]
            for order in data["orders"]:
                lines.append(
                    f"  订单号: {order['order_id']} | "
                    f"商品: {order['product_name']} | "
                    f"金额: ¥{order['total_price']:.2f} | "
                    f"状态: {order.get('status_text', order['status'])}"
                )
            return '\n'.join(lines)
        else:
            return (
                f"【{source}】\n"
                f"  订单号: {data['order_id']} | "
                f"商品: {data['product_name']} | "
                f"金额: ¥{data['total_price']:.2f} | "
                f"状态: {data.get('status_text', data['status'])}\n"
                f"  收货地址: {data.get('address', '暂无')}\n"
                f"  物流: {data.get('logistics_company', '')} {data.get('logistics_no', '')}"
            )

    elif source == "物流系统" and isinstance(data, dict):
        lines = [f"【{source}】物流进度查询结果：\n"]
        lines.append(f"  快递: {data.get('logistics_company', '')} {data.get('logistics_no', '')}")
        lines.append(f"  当前状态: {data.get('current_status', '未知')}")
        lines.append(f"  收货地址: {data.get('address', '暂无')}")
        if data.get("events"):
            lines.append("  物流轨迹:")
            for ev in data["events"]:
                lines.append(f"    [{ev['time']}] {ev['location']} - {ev['status']}")
        return '\n'.join(lines)

    elif source == "退款系统" and isinstance(data, dict):
        lines = [f"【{source}】退款信息：\n"]
        lines.append(f"  订单号: {data['order_id']} | 商品: {data['product_name']}")
        lines.append(f"  当前状态: {data['status_text']} | 可退款: {'是' if data.get('can_refund') else '否'}")
        policy = data.get("policy", {})
        if policy.get("steps"):
            lines.append("  退款流程:")
            for step in policy["steps"]:
                lines.append(f"    {step}")
        return '\n'.join(lines)

    return f"【{source}】{data}"

# 简短模板（用于 token 紧张时）
USER_TEMPLATE_CONCISE = """问题: {query}
背景: {context}
历史: {history}
回答:"""


# ---------------------------------------------------------------------------
# Few-shot 示例
# ---------------------------------------------------------------------------
FEW_SHOT_EXAMPLES: dict[str, list[dict[str, str]]] = {
    "product_qa": [
        {
            "user": "iPhone 15 有哪些颜色可选？",
            "assistant": "iPhone 15 提供以下颜色：【商品详情】黑色、蓝色、绿色、黄色、粉色。请告诉我您偏好哪种颜色，我来帮您查询库存情况。"
        },
        {
            "user": "这款笔记本的续航时间是多少？",
            "assistant": "根据商品详情，该笔记本的续航时间为 12 小时（本地视频播放）。【商品详情】实际使用时间会因使用场景和网络环境有所差异。"
        },
    ],
    "order_query": [
        {
            "user": "我的订单什么时候发货？",
            "assistant": "我需要您的订单号来查询发货时间。请提供订单号，我会立即为您查看物流状态。【订单查询】"
        },
        {
            "user": "订单号 20240101 的物流到哪了？",
            "assistant": "您的订单正在配送中，预计 2 天内送达。【物流信息】快递员会在送达前联系您，请保持手机畅通。"
        },
    ],
    "refund_return": [
        {
            "user": "我买的衣服不合适可以退吗？",
            "assistant": "您好，该商品支持 7 天无理由退换货。【退换货政策】请问您是需要退货还是换货呢？我可以帮您办理相关手续。"
        },
    ],
}


# ---------------------------------------------------------------------------
# 句子边界截断工具
# ---------------------------------------------------------------------------
_SENTENCE_DELIMITERS = re.compile(r'(?<=[。！？.!?])\s+')
_SENTENCE_BOUNDARY_RE = re.compile(r'[。！？.!?]')


def _split_into_sentences(text: str) -> list[str]:
    """按句子边界拆分，保留分隔符"""
    parts = _SENTENCE_DELIMITERS.split(text.strip())
    result = []
    for part in parts:
        if part:
            # 如果段落末尾没有句子结束符，补上
            if part[-1] not in '。！？.!?':
                result.append(part + '。')
            else:
                result.append(part)
    return result


def _truncate_by_sentences(
    sentences: list[str],
    max_tokens: int,
    count_fn: callable,
) -> str:
    """按句子截断，确保不超过 token 预算（优先保留完整句子）"""
    result = []
    current_tokens = 0

    for sentence in sentences:
        sentence_tokens = count_fn(sentence)
        if current_tokens + sentence_tokens > max_tokens:
            # 检查是否可以容纳单个短句子
            if sentence_tokens <= max_tokens and not result:
                return sentence
            break
        result.append(sentence)
        current_tokens += sentence_tokens

    return '\n\n'.join(result)


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------
class PromptBuilder(ABC):
    @abstractmethod
    def build(
        self, query: str, context: list[RerankedDoc], intent: str, history: list[dict]
    ) -> Prompt:
        """
        输入: query + 重排后文档 + 意图 + 会话历史
        输出: Prompt { system, user, system_tokens, user_tokens, version }
        """
        pass


# ---------------------------------------------------------------------------
# 企业级 Prompt 构建器
# ---------------------------------------------------------------------------
class EcommercePromptBuilder(PromptBuilder):
    """
    企业级 Prompt 构建器
    - 意图路由选择对应 system prompt
    - 按句子边界截断上下文（不截断句子中间）
    - 可配置历史轮数、max_context_tokens
    - 支持 few-shot 示例注入
    - Token 精确计数（集成 tiktoken）
    """

    def __init__(
        self,
        llm,
        max_context_tokens: int = 3000,
        max_history_turns: int = 5,
        enable_few_shot: bool = True,
        prompt_version: str = "v1",
    ):
        self._llm = llm
        self._max_context_tokens = max_context_tokens
        self._max_history_turns = max_history_turns
        self._enable_few_shot = enable_few_shot
        self._prompt_version = prompt_version
        self._system_templates = _PROMPT_VERSIONS.get(prompt_version, SYSTEM_TEMPLATES)

    def _count_tokens(self, text: str) -> int:
        if self._llm and hasattr(self._llm, 'count_tokens'):
            return self._llm.count_tokens(text)
        return len(text) // 4

    def _get_system_prompt(self, intent: str) -> str:
        return self._system_templates.get(intent, DEFAULT_SYSTEM)

    def _build_few_shot_section(self, intent: str) -> str:
        """构建 few-shot 示例部分"""
        if not self._enable_few_shot:
            return ""
        examples = FEW_SHOT_EXAMPLES.get(intent, [])
        if not examples:
            return ""
        lines = ["\n\n【示例】"]
        for ex in examples[:2]:  # 最多 2 个示例
            lines.append(f"用户: {ex['user']}")
            lines.append(f"助手: {ex['assistant']}")
        return '\n'.join(lines)

    def _build_context(self, context: list[RerankedDoc]) -> str:
        if not context:
            return "（知识库中暂无相关信息）"
        parts = []
        for i, doc in enumerate(context, 1):
            source = doc.source or "未知来源"
            parts.append(f"[{i}] 来源: {source}\n{doc.content}")
        return '\n\n'.join(parts)

    def _build_history(self, history: list[dict]) -> str:
        if not history:
            return "（首次对话）"
        turns = []
        for msg in history[-self._max_history_turns:]:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            label = "用户" if role == "user" else "助手"
            turns.append(f"{label}: {content}")
        return '\n'.join(turns)

    def build(
        self,
        query: str,
        context: list[RerankedDoc],
        intent: str,
        history: list[dict],
        tool_result: "ToolResult | None" = None,
        product_context: "dict | None" = None,
    ) -> Prompt:
        system_prompt = self._get_system_prompt(intent)

        # 计算 system prompt tokens（包含 few-shot）
        few_shot_section = self._build_few_shot_section(intent)
        full_system = system_prompt + few_shot_section
        system_tokens = self._count_tokens(full_system)

        # Token 预算：预留 200 给响应 + 100 给历史
        reserved_tokens = 300
        available = self._max_context_tokens - system_tokens - reserved_tokens

        # 构建上下文（先构建知识库内容）
        context_raw = self._build_context(context)
        if self._count_tokens(context_raw) > available:
            sentences = _split_into_sentences(context_raw)
            context_truncated = _truncate_by_sentences(
                sentences, available, self._count_tokens
            )
            context_text = context_truncated + "\n\n（上下文已按句子截断）"
        else:
            context_text = context_raw

        # Bug Fix: tool_result 注入必须在 context 构建完成之后
        # 否则第 375 行注入后，第 392 行又被覆盖，工具数据从未进入 Prompt
        if tool_result is not None:
            tool_context = format_tool_result_for_prompt(tool_result)
            context_text = f"{tool_context}\n\n{'='*40}\n\n{context_text}"

        # 商品上下文注入（如果存在）
        if product_context and product_context.get("product_name"):
            product_section = f"【商品检索结果】\n"
            product_section += f"商品名称: {product_context.get('product_name', '未知')}\n"
            if product_context.get("product_detail"):
                product_section += f"商品详情: {product_context.get('product_detail')}\n"
            product_section += f"匹配方式: {product_context.get('match_type', '未知')}\n"
            product_section += f"置信度: {product_context.get('confidence', 0):.2f}"
            context_text = f"{product_section}\n\n{'='*40}\n\n{context_text}"

        # 构建历史
        history_text = self._build_history(history)

        # 构建 user prompt
        user_prompt = USER_TEMPLATE.format(
            query=query,
            context=context_text,
            history=history_text,
        )

        # 最终 token 计数
        user_tokens = self._count_tokens(user_prompt)

        return Prompt(
            system=full_system,
            user=user_prompt,
            system_tokens=system_tokens,
            user_tokens=user_tokens,
            version=self._prompt_version,
        )


# ---------------------------------------------------------------------------
# 简洁版 Prompt 构建器（用于超长 context 场景）
# ---------------------------------------------------------------------------
class ConcisePromptBuilder(PromptBuilder):
    """
    简洁版 Prompt 构建器 — token 受限时使用
    移除 few-shot，简化模板，优先保证核心信息
    """

    def __init__(self, llm, max_context_tokens: int = 2000, max_history_turns: int = 3):
        self._llm = llm
        self._max_context_tokens = max_context_tokens
        self._max_history_turns = max_history_turns

    def _count_tokens(self, text: str) -> int:
        if self._llm and hasattr(self._llm, 'count_tokens'):
            return self._llm.count_tokens(text)
        return len(text) // 4

    def build(
        self,
        query: str,
        context: list[RerankedDoc],
        intent: str,
        history: list[dict],
        tool_result: "ToolResult | None" = None,
        product_context: "dict | None" = None,
    ) -> Prompt:
        system_prompt = SYSTEM_TEMPLATES.get(intent, DEFAULT_SYSTEM)
        system_tokens = self._count_tokens(system_prompt)

        reserved = 200
        available = self._max_context_tokens - system_tokens - reserved

        # 精简上下文
        context_parts = []
        for doc in context:
            src = doc.source or "未知"
            context_parts.append(f"【{src}】{doc.content}")
        context_raw = '\n'.join(context_parts)

        if self._count_tokens(context_raw) > available:
            sentences = _split_into_sentences(context_raw)
            context_text = _truncate_by_sentences(
                sentences, available, self._count_tokens
            )
        else:
            context_text = context_raw

        # 工具结果注入
        if tool_result is not None:
            tool_context = format_tool_result_for_prompt(tool_result)
            context_text = f"{tool_context}\n\n{'='*40}\n\n{context_text}"

        # 商品上下文注入
        if product_context and product_context.get("product_name"):
            product_section = f"【商品检索结果】\n"
            product_section += f"商品名称: {product_context.get('product_name', '未知')}\n"
            if product_context.get("product_detail"):
                product_section += f"商品详情: {product_context.get('product_detail')}\n"
            product_section += f"匹配方式: {product_context.get('match_type', '未知')}\n"
            product_section += f"置信度: {product_context.get('confidence', 0):.2f}"
            context_text = f"{product_section}\n\n{'='*40}\n\n{context_text}"

        # 精简历史
        hist_turns = []
        for msg in history[-self._max_history_turns:]:
            role = msg.get("role", "user")
            hist_turns.append(f"{role}: {msg.get('content', '')}")
        history_text = '\n'.join(hist_turns) or "（首次对话）"

        user_prompt = USER_TEMPLATE_CONCISE.format(
            query=query,
            context=context_text,
            history=history_text,
        )
        user_tokens = self._count_tokens(user_prompt)

        return Prompt(
            system=system_prompt,
            user=user_prompt,
            system_tokens=system_tokens,
            user_tokens=user_tokens,
            version="concise",
        )


# ---------------------------------------------------------------------------
# Mock 实现
# ---------------------------------------------------------------------------
class MockPromptBuilder(PromptBuilder):
    def build(
        self,
        query: str,
        context: list[RerankedDoc],
        intent: str,
        history: list[dict],
        tool_result: "ToolResult | None" = None,
        product_context: "dict | None" = None,
    ) -> Prompt:
        tool_info = ""
        if tool_result is not None:
            tool_info = f"\n[Tool: {tool_result.source}]"
        product_info = ""
        if product_context and product_context.get("product_name"):
            product_info = f"\n[Product: {product_context.get('product_name', '')}]"
        return Prompt(
            system=DEFAULT_SYSTEM,
            user=f"Query: {query}\nContext: {len(context)} docs{tool_info}{product_info}",
            system_tokens=50,
            user_tokens=50,
            version="mock",
        )