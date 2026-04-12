"""
Query 改写模块（企业级）
- 多轮补全: 提取指代词还原完整 query
- 同义词扩展: 电商领域同义词扩展（大规模）
- Query 去噪: 过滤语气词、重复词
- 历史摘要: 结合上一轮对话对模糊问题补全
- LLM 改写: 可选的 LLM 增强改写（用于复杂多轮场景）
"""
from __future__ import annotations

import asyncio
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.services.llm_service import LLMBase, ChatMessage, LLMError
from app.logging_config import get_logger
from app import metrics as _metrics

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
_rewrite_logger = get_logger("query.rewriter")

# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class RewrittenQuery:
    rewritten: str
    expanded_terms: list[str]
    intent: str  # 初步意图（待 IntentService 确认）
    method: str  # "rule" | "llm" | "hybrid"
    latency_ms: int


# ---------------------------------------------------------------------------
# 电商领域同义词映射（大规模，修复重复）
# ---------------------------------------------------------------------------
_DOMAIN_SYNONYMS: dict[str, list[str]] = {
    # 3C 数码
    "手机": ["智能手机", "移动电话", "电话", "手持设备", "智能机"],
    "电脑": ["笔记本", "笔记本电脑", "台式机", "PC", "计算机"],
    "笔记本": ["笔记本电脑", "手提电脑", "轻薄本", "游戏本", "办公本"],
    "平板": ["平板电脑", "iPad", "安卓平板", "学习平板"],
    "耳机": ["耳麦", "头戴式耳机", "蓝牙耳机", "无线耳机", "降噪耳机", "入耳式", "挂耳式"],
    "音箱": ["音响", "蓝牙音箱", "桌面音箱", "智能音箱", "家用音箱"],
    "相机": ["数码相机", "单反", "微单", "卡片机", "运动相机"],
    "智能手表": ["手表", "运动手表", "儿童手表", "智能手环", "watch"],
    # 电脑配件
    "键盘": ["机械键盘", "薄膜键盘", "无线键盘", "游戏键盘"],
    "鼠标": ["无线鼠标", "游戏鼠标", "蓝牙鼠标", "光电鼠标"],
    "显示器": ["屏幕", "外接显示器", "电竞屏", "4K屏"],
    "硬盘": ["固态硬盘", "SSD", "机械硬盘", "HDD", "移动硬盘"],
    "内存": ["RAM", "运行内存", "物理内存"],
    # 网络设备
    "路由器": ["WiFi路由器", "无线路由器", "Mesh路由"],
    "移动电源": ["充电宝", "便携电源", "powerbank"],
    # 商品属性
    "降价": ["打折", "优惠", "促销", "特价", "折扣", "减价", "便宜"],
    "发货": ["寄送", "配送", "发出", "发货", "发出"],
    "退款": ["退货", "退钱", "返款", "取消退款"],
    "颜色": ["配色", "色彩", "外观颜色"],
    "价格": ["价钱", "售价", "报价", "价位", "多少钱"],
    "内存容量": ["存储空间", "ROM", "存储容量", "硬盘容量"],
    "屏幕尺寸": ["显示屏大小", "屏幕大小", "显示器尺寸"],
    "电池续航": ["续航时间", "电池使用时间", "待机时间"],
    # 服装鞋类
    "衣服": ["上衣", "外套", "T恤", "衬衫", "卫衣", "毛衣", "羽绒服"],
    "鞋子": ["运动鞋", "休闲鞋", "皮鞋", "帆布鞋", "拖鞋"],
    "裤子": ["牛仔裤", "休闲裤", "运动裤", "短裤", "长裤"],
    "包包": ["背包", "手提包", "单肩包", "双肩包", "挎包"],
    # 家用电器
    "电视": ["电视机", "智能电视", "液晶电视", "OLED电视"],
    "冰箱": ["电冰箱", "冷藏柜", "冷柜", "双开门冰箱"],
    "洗衣机": ["洗衣烘干机", "滚筒洗衣机", "波轮洗衣机"],
    "空调": ["冷气机", "空气调节器", "变频空调", "挂机", "柜机"],
    "吸尘器": ["扫地机", "洗地机", "无线吸尘器", "扫地机器人"],
    # 护肤美妆
    "化妆品": ["彩妆", "护肤品", "美妆", "护肤"],
    "洗面奶": ["洁面乳", "洁面霜", "洁面啫喱"],
    "面霜": ["保湿霜", "日霜", "晚霜", "护肤霜"],
    "口红": ["唇膏", "唇釉", "唇彩"],
    # 食品生鲜
    "水果": ["生鲜水果", "时令水果", "进口水果"],
    "零食": ["小吃", "休闲食品", "坚果", "糖果"],
}

# 电商指代词还原（去重）
_PRONOUN_MAP: dict[str, list[str]] = {
    "这个": ["这个商品", "这个产品", "这款商品"],
    "那个": ["那个商品", "那个产品", "那款商品"],
    "它": ["这个商品", "这个产品", "该商品"],
    "这款": ["这款商品", "这款产品", "这款"],
    "那款": ["那款商品", "那款产品", "那款"],
    "这件": ["这件商品", "这件产品", "这件"],
    "那双": ["那双鞋子", "那双鞋"],
    "那件": ["那件衣服", "那件商品"],
    "这双": ["这双鞋子", "这双鞋"],
    "这件": ["这件衣服", "这件商品"],
    "刚才": ["刚才那个", "刚才看的"],
    "之前": ["之前那个", "之前那款"],
    "上次": ["上次那个", "上次说的"],
    "有货吗": ["有库存吗", "还有货吗"],
}

# 语气词和噪音词
_NOISE_PATTERNS: list[re.Pattern] = [
    re.compile(r"[呀啊呗嘛呢哈哦呃嗯呐噢嘛啰喔哇嗨嗯呃]"),
    re.compile(r"[!！?？]{2,}"),  # 多个感叹号/问号
    re.compile(r"(\w)\1{2,}"),   # 重复字符
    re.compile(r"\s+"),            # 多余空格
    re.compile(r"^[的得地]+"),     # 开头语气词
]

# LLM 改写提示词
_LLM_REWRITE_PROMPT = """你是一个电商客服 Query 改写助手。请将用户的原始问题改写成完整、清晰、无歧义的查询语句。

要求：
1. 如果用户使用了代词（这个、那个、它等），根据对话历史还原指代内容
2. 如果用户问题模糊或简短，结合历史信息补充完整
3. 如果用户使用了同义词，改写时保留原词和同义词版本
4. 去除语气词、口头禅，保持问题核心
5. 不要回答问题，只改写问题本身

对话历史：
{history}

当前问题：{query}

请只输出改写后的查询语句，不要解释。"""


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------
class QueryRewriter(ABC):
    @abstractmethod
    async def rewrite(
        self, query: str, history: list[dict]
    ) -> RewrittenQuery:
        """
        输入: 原始 query + 会话历史
        输出: RewrittenQuery { rewritten, expanded_terms, intent, method, latency_ms }
        """
        pass


# ---------------------------------------------------------------------------
# 规则 Query 改写器
# ---------------------------------------------------------------------------
class EcommerceQueryRewriter(QueryRewriter):
    """
    电商 Query 改写器（规则版）
    - 去噪
    - 指代词还原
    - 同义词扩展
    - 初步意图判断
    """

    def __init__(
        self,
        llm: LLMBase | None = None,
        use_llm_rewrite: bool = False,
        llm_rewrite_threshold: int = 30,
    ):
        """
        Args:
            llm: 可选 LLM，用于复杂场景增强改写
            use_llm_rewrite: 是否启用 LLM 改写（复杂场景自动触发）
            llm_rewrite_threshold: query 长度阈值，短于此长度才考虑 LLM 改写
        """
        self._llm = llm
        self._use_llm_rewrite = use_llm_rewrite
        self._llm_rewrite_threshold = llm_rewrite_threshold
        self._rule_classifier = _SimpleIntentClassifier()

    async def rewrite(
        self, query: str, history: list[dict]
    ) -> RewrittenQuery:
        start = time.monotonic()

        # 1. 去噪
        cleaned = self._denoise(query)

        # 2. 指代词还原
        expanded = self._resolve_pronouns(cleaned, history)

        # 3. 判断是否需要 LLM 改写
        use_llm = (
            self._llm is not None
            and self._use_llm_rewrite
            and self._should_use_llm(expanded, history)
        )

        if use_llm:
            llm_result = await self._llm_rewrite(expanded, history)
            expanded_terms = self._expand_synonyms(llm_result)
            intent = self._rule_classifier.classify(llm_result)
            method = "llm"
        else:
            expanded_terms = self._expand_synonyms(expanded)
            intent = self._rule_classifier.classify(expanded)
            method = "rule"

        latency_ms = int((time.monotonic() - start) * 1000)

        _rewrite_logger.info(
            "Query rewritten",
            extra={
                "original": query,
                "rewritten": expanded,
                "method": method,
                "intent": intent,
                "latency_ms": latency_ms,
            }
        )

        return RewrittenQuery(
            rewritten=expanded,
            expanded_terms=expanded_terms,
            intent=intent,
            method=method,
            latency_ms=latency_ms,
        )

    def _denoise(self, query: str) -> str:
        """去除语气词、感叹词、重复"""
        result = query.strip()
        for pattern in _NOISE_PATTERNS:
            result = pattern.sub("", result)
        # 清理首尾标点和空格
        result = result.strip("。！？.!? \t\n")
        return result or query

    def _resolve_pronouns(self, query: str, history: list[dict]) -> str:
        """基于历史上下文还原指代词"""
        if not history:
            return query

        result = query
        last_content = history[-1].get("content", "")

        for pronoun, replacements in _PRONOUN_MAP.items():
            if pronoun in result:
                matched = False
                for rep in replacements:
                    if rep[:4] in last_content:
                        result = result.replace(pronoun, rep, 1)
                        matched = True
                        break
                if not matched:
                    result = result.replace(pronoun, replacements[0], 1)

        return result

    def _expand_synonyms(self, query: str) -> list[str]:
        """电商同义词扩展"""
        expanded = []
        for key, synonyms in _DOMAIN_SYNONYMS.items():
            if key in query:
                expanded.append(key)
                expanded.extend(synonyms)
        return list(set(expanded))

    def _should_use_llm(self, query: str, history: list[dict]) -> bool:
        """判断是否需要 LLM 增强改写"""
        # 短 query + 有历史 → 可能需要补全
        if len(query) < self._llm_rewrite_threshold and history:
            return True
        # 包含指代词但历史中没有明确匹配
        for pronoun in _PRONOUN_MAP:
            if pronoun in query:
                return True
        return False

    async def _llm_rewrite(
        self, query: str, history: list[dict]
    ) -> str:
        """使用 LLM 增强改写"""
        if self._llm is None:
            return query

        # 构建历史摘要（最多 3 轮）
        history_text = ""
        if history:
            turns = []
            for msg in history[-3:]:
                role = "用户" if msg.get("role") == "user" else "助手"
                turns.append(f"{role}: {msg.get('content', '')}")
            history_text = '\n'.join(turns)

        prompt = _LLM_REWRITE_PROMPT.format(
            query=query,
            history=history_text or "（无历史记录）",
        )

        try:
            response = await asyncio.wait_for(
                self._llm.chat([ChatMessage(role="user", content=prompt)]),
                timeout=3.0,
            )
            rewritten = response.content.strip()
            if rewritten:
                _rewrite_logger.debug(
                    "LLM rewrite completed",
                    extra={"original": query, "rewritten": rewritten}
                )
                return rewritten
        except (asyncio.TimeoutError, LLMError) as e:
            _rewrite_logger.warning(
                "LLM rewrite failed, falling back to rule-based",
                extra={"query": query, "error": str(e)[:100]}
            )
        return query


class _SimpleIntentClassifier:
    """简单的意图分类器（用于 QueryRewriter 内部）"""

    def __init__(self):
        self._keywords = {
            "product_qa": ["多少钱", "价格", "优惠", "降价", "打折", "参数", "功能", "颜色"],
            "order_query": ["订单", "买了", "购买", "发货", "物流", "快递"],
            "refund_return": ["退款", "退货", "换货", "售后", "退换", "质量"],
        }

    def classify(self, query: str) -> str:
        q = query.lower()
        for intent, keywords in self._keywords.items():
            if any(k in q for k in keywords):
                return intent
        return "product_qa"


# ---------------------------------------------------------------------------
# Mock 实现
# ---------------------------------------------------------------------------
class MockQueryRewriter(QueryRewriter):
    async def rewrite(self, query: str, history: list[dict]) -> RewrittenQuery:
        await asyncio.sleep(0.01)
        return RewrittenQuery(
            rewritten=query,
            expanded_terms=[],
            intent="product_qa",
            method="mock",
            latency_ms=10,
        )