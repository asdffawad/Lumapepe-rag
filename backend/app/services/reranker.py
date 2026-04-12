"""
Reranker module（企业级）
- MMR (Maximal Marginal Relevance) 重排
- 业务规则加权（新品、促销等）
- 意图关键词匹配加权
- LLM 重排（可选，用于复杂场景）
- 中文分词改进（基于 jieba 或字符级 n-gram）
- MMR λ 参数可配置化
"""
from __future__ import annotations

import asyncio
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from app.services.llm_service import LLMBase, ChatMessage, LLMError
from app.services.vector_retriever import RetrievedDoc
from app.logging_config import get_logger
from app import metrics as _metrics

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
_rerank_logger = get_logger("reranker")

# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class RerankedDoc:
    content: str
    metadata: dict
    rerank_score: float
    source: str


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------
class RerankError(Exception):
    pass


# ---------------------------------------------------------------------------
# 中文分词工具（企业级）
# ---------------------------------------------------------------------------
class ChineseTokenizer:
    """
    企业级中文分词器
    - 优先使用 jieba（精确模式）
    - Fallback 到字符级 n-gram + 数字/英文词合并
    """

    def __init__(self):
        self._tokenizer = None
        self._jieba_loaded = False
        self._init_jieba()

    def _init_jieba(self):
        try:
            import jieba
            jieba.setLogLevel(20)  # 关闭 jieba 日志
            self._tokenizer = jieba
            self._jieba_loaded = True
            _rerank_logger.info("Chinese tokenizer initialized with jieba")
        except ImportError:
            _rerank_logger.warning(
                "jieba not installed, using char-level tokenization fallback"
            )
            self._tokenizer = None

    def tokenize(self, text: str) -> set[str]:
        """分词，返回词集合（小写）"""
        if self._jieba_loaded and self._tokenizer:
            words = set()
            for word in self._tokenizer.lcut(text):
                cleaned = word.strip().lower()
                if cleaned and len(cleaned) > 0:
                    words.add(cleaned)
            return words
        return self._char_level_tokenize(text)

    def _char_level_tokenize(self, text: str) -> set[str]:
        """
        字符级分词 fallback
        - 中文：按标点和空格分词
        - 英文/数字：合并成词（连续字母/数字视为一个词）
        """
        import re
        # 合并英文单词
        text = re.sub(r"[a-zA-Z]+", lambda m: m.group(0), text)
        text = re.sub(r"\d+", lambda m: m.group(0), text)
        # 按标点和空格分割
        tokens = re.split(r"[，。！？、：；,.!?:()（）\[\]【】\"\'''\"\n\r\t]+", text)
        words = set()
        for token in tokens:
            cleaned = token.strip().lower()
            if cleaned and len(cleaned) > 1:  # 过滤单字符
                words.add(cleaned)
        return words


_tokenizer_cache: ChineseTokenizer | None = None


def get_tokenizer() -> ChineseTokenizer:
    global _tokenizer_cache
    if _tokenizer_cache is None:
        _tokenizer_cache = ChineseTokenizer()
    return _tokenizer_cache


# ---------------------------------------------------------------------------
# 相似度计算
# ---------------------------------------------------------------------------
def word_jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """词级 Jaccard 相似度"""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------
class Reranker(ABC):
    @abstractmethod
    async def rerank(
        self, query: str, candidates: list[RetrievedDoc], top_k: int
    ) -> list[RerankedDoc]:
        """
        Input: query + top-k candidate docs + final return count
        Output: list[RerankedDoc] { content, metadata, rerank_score, source }
        """
        pass


# ---------------------------------------------------------------------------
# MMR Reranker（企业级）
# ---------------------------------------------------------------------------
class MMRReranker(Reranker):
    """
    MMR (Maximal Marginal Relevance) Reranker（企业级）

    改进点：
    - 可配置 λ 参数
    - 改进的中文分词（jieba）
    - 业务加权 + 意图加权
    - 可选 LLM 重排（用于语义重排）
    """

    _BUSINESS_BOOST_KEYWORDS: dict[str, float] = {
        # 促销活动
        "新品": 0.15,
        "热销": 0.12,
        "活动": 0.10,
        "特价": 0.12,
        "爆款": 0.15,
        "推荐": 0.10,
        "促销": 0.10,
        "打折": 0.10,
        "限时": 0.08,
        "爆款推荐": 0.12,
        "今日特惠": 0.10,
        "爆款热卖": 0.12,
        # 商品属性
        "正品": 0.05,
        "包邮": 0.05,
        "急速发货": 0.05,
    }

    _INTENT_KEYWORD_SCORES: dict[str, list[str]] = {
        "product_qa": ["参数", "配置", "功能", "规格", "价格", "颜色", "内存", "续航", "像素", "屏幕"],
        "order_query": ["订单", "发货", "物流", "配送", "快递", "收货", "签收", "运单", "催单"],
        "refund_return": ["退款", "退货", "售后", "退换", "七天", "质量", "破损", "换货"],
        "product_comparison": ["对比", "区别", "哪个好", "比较", "差异", "优缺点"],
        "promotion_inquiry": ["优惠", "折扣", "满减", "优惠券", "活动", "特价", "降价"],
        "account_info": ["账号", "登录", "密码", "地址", "支付", "会员"],
        "technical_support": ["打不开", "闪退", "无法", "故障", "异常", "失败", "错误"],
        "feedback_complaint": ["投诉", "差评", "不满", "失望", "延迟"],
    }

    def __init__(
        self,
        llm: Optional[LLMBase] = None,
        lambda_mult: float = 0.5,
        use_llm_rerank: bool = False,
        use_llm_threshold: float = 0.6,
        business_boost: bool = True,
        bm25_weight: float = 0.3,
    ):
        """
        Args:
            llm: 可选 LLM，用于语义重排
            lambda_mult: MMR 平衡因子 (0=只看相关性, 1=只看多样性)
            use_llm_rerank: 是否启用 LLM 重排（当规则得分低于阈值时触发）
            use_llm_threshold: 触发 LLM 重排的得分阈值
            business_boost: 是否启用业务加权（新品、促销等）
            bm25_weight: BM25 在融合分数中的权重 (0~1)，由 HybridRetriever 注入 bm25_score
        """
        self._llm = llm
        self._lambda = lambda_mult
        self._use_llm_rerank = use_llm_rerank
        self._use_llm_threshold = use_llm_threshold
        self._business_boost = business_boost
        self._bm25_weight = bm25_weight
        self._tokenizer = get_tokenizer()

    def _tokenize(self, text: str) -> set[str]:
        """中文分词"""
        return self._tokenizer.tokenize(text)

    def _content_similarity(self, a: str, b: str) -> float:
        """基于分词的文档相似度"""
        set_a = self._tokenize(a)
        set_b = self._tokenize(b)
        return word_jaccard_similarity(set_a, set_b)

    def _rule_score(self, query: str, doc: RetrievedDoc, intent: str = "product_qa") -> float:
        """
        基于规则的语义相关度打分
        - 原始向量相似度 (0~1)
        - BM25 融合（由 HybridRetriever 注入）
        - 业务加权（新品、促销）
        - 意图关键词匹配
        - section_type / markets / lang 字段加权
        """
        import math

        score = doc.score  # dense 相似度 0~1（IP 内积）

        # BM25 分数融合（归一化后加权）
        bm25_raw = doc.metadata.get("bm25_score", 0.0)
        bm25_norm = math.log1p(bm25_raw) / 10.0  # 压缩到 ~0.4 量级
        dense_weight = 1.0 - self._bm25_weight
        score = dense_weight * score + self._bm25_weight * bm25_norm

        if self._business_boost:
            content_lower = doc.content.lower()
            for kw, boost in self._BUSINESS_BOOST_KEYWORDS.items():
                if kw in content_lower:
                    score += boost

        # 意图关键词加权
        if intent in self._INTENT_KEYWORD_SCORES:
            intent_kws = self._INTENT_KEYWORD_SCORES[intent]
            query_lower = query.lower()
            content_lower = doc.content.lower()
            for kw in intent_kws:
                if kw in query_lower and kw in content_lower:
                    score += 0.05
                    break

        # section_type 加权：bullet/ordered 信息密度高于普通 body
        _section_boost = {
            "bullet": 0.05,
            "ordered": 0.05,
            "heading": 0.03,
            "note": 0.02,
        }
        stype = doc.metadata.get("section_type", "body")
        score += _section_boost.get(stype, 0.0)

        # markets 加权：根据 query 语言偏好对应市场
        doc_markets = doc.metadata.get("markets", [])
        if not isinstance(doc_markets, list):
            doc_markets = [doc_markets]
        if "vi" in query.lower() and "VN" in doc_markets:
            score += 0.05
        elif "en" in query.lower() and "US" in doc_markets:
            score += 0.05
        elif "zh" not in query.lower() and "vi" not in query.lower() and "en" not in query.lower():
            # 默认中文优先
            if "CN" in doc_markets:
                score += 0.03

        return min(score, 1.0)

    def _mmr_selection(
        self,
        candidates: list[tuple[float, RetrievedDoc]],
        top_k: int,
    ) -> list[RerankedDoc]:
        """
        MMR 多样性选择算法
        λ=0.5: 相关性和多样性各占一半
        λ=0.7: 更注相关性
        λ=0.3: 更注多样性

        额外奖励：同一 hierarchy 路径的文档轻微加权（上下文连贯性）。
        """
        result: list[RerankedDoc] = []
        seen_bow: list[set[str]] = []
        seen_hierarchy_paths: set[str] = set()

        for score, doc in candidates:
            if len(result) >= top_k:
                break

            bow = self._tokenize(doc.content)

            # 计算与已选文档的最大相似度（多样性惩罚）
            max_sim = 0.0
            for seen_bow_item in seen_bow:
                sim = word_jaccard_similarity(bow, seen_bow_item)
                max_sim = max(max_sim, sim)

            # MMR = λ * score - (1-λ) * max_sim
            mmr_score = self._lambda * score - (1 - self._lambda) * max_sim

            # hierarchy 上下文连贯性加分
            hierarchy = doc.metadata.get("hierarchy", {})
            hierarchy_paths = [k for k in hierarchy if k != "_flat_index"]
            if hierarchy_paths:
                path_key = hierarchy_paths[0]
                if path_key in seen_hierarchy_paths:
                    mmr_score += 0.02
                seen_hierarchy_paths.add(path_key)

            result.append(RerankedDoc(
                content=doc.content,
                metadata=doc.metadata,
                rerank_score=mmr_score,
                source=doc.source,
            ))
            seen_bow.append(bow)

        return result

    async def rerank(
        self, query: str, candidates: list[RetrievedDoc], top_k: int = 5, intent: str = "product_qa"
    ) -> list[RerankedDoc]:
        start = time.monotonic()

        if not candidates:
            return []

        # Step 1: 规则打分
        scored = []
        for doc in candidates:
            score = self._rule_score(query, doc, intent)
            scored.append((score, doc))

        # Step 2: 排序
        scored.sort(key=lambda x: x[0], reverse=True)

        # Step 3: 检查是否需要 LLM 重排
        if (
            self._use_llm_rerank
            and self._llm is not None
            and scored[0][0] < self._use_llm_threshold
            and len(candidates) >= 2
        ):
            _rerank_logger.info(
                "Triggering LLM rerank",
                extra={"top_score": scored[0][0], "threshold": self._use_llm_threshold}
            )
            scored = await self._llm_rerank(query, scored, intent)

        # Step 4: MMR 多样性选择
        result = self._mmr_selection(scored, top_k)

        latency_ms = int((time.monotonic() - start) * 1000)
        try:
            _metrics.rag_rerank_duration_seconds.observe(latency_ms / 1000.0)
        except Exception:
            pass

        _rerank_logger.info(
            "Reranking completed",
            extra={
                "query": query[:50],
                "candidates": len(candidates),
                "top_k": top_k,
                "top_score": result[0].rerank_score if result else 0,
                "latency_ms": latency_ms,
                "method": "llm" if self._use_llm_rerank and scored[0][0] < self._use_llm_threshold else "rule",
            }
        )

        return result

    async def _llm_rerank(
        self,
        query: str,
        candidates: list[tuple[float, RetrievedDoc]],
        intent: str,
    ) -> list[tuple[float, RetrievedDoc]]:
        """
        LLM 语义重排
        将 query + doc 并行发送给 LLM 评分
        """
        if self._llm is None:
            return candidates

        prompt_template = """你是一个文档相关性评分系统。请评估以下文档与查询的相关程度。

查询：{query}
文档：{doc_content}

请从以下角度评分（满分1分）：
- 语义相关性（0-0.5）
- 关键词覆盖度（0-0.3）
- 信息完整性（0-0.2）

请只输出一个 0 到 1 之间的小数分数，不要任何解释。

分数："""

        tasks = []
        for score, doc in candidates[:10]:  # 最多重排 10 个
            prompt = prompt_template.format(
                query=query,
                doc_content=doc.content[:500],  # 限制长度
            )
            tasks.append(self._llm.chat([ChatMessage(role="user", content=prompt)]))

        try:
            responses = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=10.0,
            )

            llm_scores = []
            for i, (score, doc), response in enumerate(zip(candidates, responses)):
                if isinstance(response, Exception):
                    _rerank_logger.warning(
                        f"LLM rerank failed for doc {i}",
                        extra={"error": str(response)[:100]}
                    )
                    llm_scores.append((score, doc))
                else:
                    try:
                        llm_score = float(response.content.strip())
                        llm_score = max(0.0, min(1.0, llm_score))
                        # 与规则得分加权
                        combined = 0.4 * score + 0.6 * llm_score
                        llm_scores.append((combined, doc))
                    except ValueError:
                        llm_scores.append((score, doc))

            llm_scores.sort(key=lambda x: x[0], reverse=True)
            return llm_scores

        except asyncio.TimeoutError:
            _rerank_logger.warning("LLM rerank timeout, using rule-based scores")
            return candidates


# ---------------------------------------------------------------------------
# BM25 Reranker（备选）
# ---------------------------------------------------------------------------
class BM25Reranker(Reranker):
    """
    BM25 重排器（传统信息检索方法）
    作为 MMR 的备选或补充方案
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self._k1 = k1
        self._b = b
        self._tokenizer = get_tokenizer()

    async def rerank(
        self, query: str, candidates: list[RetrievedDoc], top_k: int = 5
    ) -> list[RerankedDoc]:
        if not candidates:
            return []

        # 计算 IDF
        docs_bow = [self._tokenizer.tokenize(d.content) for d in candidates]
        doc_count = len(docs_bow)
        word_doc_freq: dict[str, int] = {}
        for bow in docs_bow:
            for word in set(bow):
                word_doc_freq[word] = word_doc_freq.get(word, 0) + 1

        query_words = self._tokenizer.tokenize(query)
        avgdl = sum(len(bow) for bow in docs_bow) / max(doc_count, 1)

        scores = []
        for doc, bow in zip(candidates, docs_bow):
            score = 0.0
            doc_len = len(bow)
            for word in query_words:
                if word in bow:
                    df = word_doc_freq.get(word, 0)
                    idf = max(0, (doc_count - df + 0.5) / (df + 0.5))
                    tf = bow.count(word)
                    score += idf * (tf * (self._k1 + 1)) / (
                        tf + self._k1 * (1 - self._b + self._b * doc_len / avgdl)
                    )
            scores.append((score + doc.score * 0.3, doc))  # 与向量分加权

        scores.sort(key=lambda x: x[0], reverse=True)
        return [
            RerankedDoc(
                content=doc.content,
                metadata=doc.metadata,
                rerank_score=score,
                source=doc.source,
            )
            for score, doc in scores[:top_k]
        ]


# ---------------------------------------------------------------------------
# Mock 实现
# ---------------------------------------------------------------------------
class MockReranker(Reranker):
    async def rerank(
        self, query: str, candidates: list[RetrievedDoc], top_k: int = 5, intent: str = "product_qa"
    ) -> list[RerankedDoc]:
        await asyncio.sleep(0.01)
        return [
            RerankedDoc(
                content=d.content,
                metadata=d.metadata,
                rerank_score=d.score,
                source=d.source,
            )
            for d in candidates[:top_k]
        ]
