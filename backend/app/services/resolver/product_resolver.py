"""
商品解析器（ProductResolver）

核心功能：
- 基于内存字典的多级商品匹配
- 支持条形码、别名、SKU名称、SPU关键词匹配
- 使用 rapidfuzz 进行模糊搜索
- 返回解析结果和置信度

匹配优先级：
1. barcode 精确匹配（置信度 1.0）
2. alias 模糊匹配（置信度 0.9）
3. sku_name 模糊匹配（置信度 0.85）
4. spu_keyword 匹配（置信度 0.8）
5. spu_name 匹配（置信度 0.75）
6. none（无匹配）
"""
from __future__ import annotations

import re
import time
from typing import Optional, List, Tuple
from dataclasses import dataclass

from rapidfuzz import process, fuzz

# 默认使用 partial_ratio：支持子串匹配，短查询更容易命中
# 其他选项：token_set_ratio（分词后匹配）、token_sort_ratio（分词排序后匹配）
DEFAULT_SCORER = fuzz.partial_ratio

from app.etl.dict_builder import ProductDict, normalize_text
from app.models.product import ResolveResult, MatchType
from app.logging_config import get_logger

_logger = get_logger("product.resolver")


@dataclass
class MatchCandidate:
    """匹配候选结果"""
    key: str
    value: int  # sku_id 或 spu_id
    score: float  # 相似度分数 0-100
    match_type: MatchType


class ProductResolver:
    """
    商品解析器

    基于内存字典的多级匹配策略，支持高并发查询（无锁读）

    设计要点：
    - 字典只读，无需锁
    - 模糊搜索使用 rapidfuzz（C++优化，速度快）
    - 支持自定义匹配阈值
    - 结构化日志记录匹配过程
    """

    # 匹配类型权重（用于置信度计算）
    MATCH_WEIGHTS = {
        MatchType.BARCODE: 1.0,
        MatchType.ALIAS: 0.9,
        MatchType.SKU_NAME: 0.85,
        MatchType.SPU_KEYWORD: 0.8,
        MatchType.SPU_NAME: 0.75,
    }

    def __init__(
        self,
        product_dict: ProductDict,
        fuzzy_threshold: int = 80,
        max_candidates: int = 5,
    ):
        """
        Args:
            product_dict: 商品字典对象（从JSON加载）
            fuzzy_threshold: 模糊匹配阈值（0-100），默认80
            max_candidates: 模糊搜索返回的最大���选数
        """
        self._dict = product_dict
        self._fuzzy_threshold = fuzzy_threshold
        self._max_candidates = max_candidates

        # 预编译正则表达式（性能优化）
        self._whitespace_re = re.compile(r'\s+')

        _logger.info(
            "ProductResolver initialized",
            extra={
                "sku_dict_size": len(product_dict.get("sku_dict", {})),
                "spu_dict_size": len(product_dict.get("spu_dict", {})),
                "alias_dict_size": len(product_dict.get("alias_dict", {})),
                "fuzzy_threshold": fuzzy_threshold,
            }
        )

    async def resolve(self, query: str) -> Optional[ResolveResult]:
        """
        解析用户查询，返回匹配的商品信息

        匹配流程（按优先级）：
        1. barcode 精确匹配（sku_dict 中的 key）
        2. alias 模糊匹配（alias_dict）
        3. SKU 名称模糊匹配（需要通过反向映射获取，这里简化处理）
        4. SPU keyword 匹配（spu_dict）
        5. SPU name 匹配（spu_dict）

        Args:
            query: 用户输入的查询文本

        Returns:
            ResolveResult（匹配成功）或 None（无匹配）
        """
        start = time.monotonic()
        query_orig = query.strip()

        if not query_orig:
            return None

        # 标准化查询文本
        query_norm = normalize_text(query_orig)
        _logger.debug(
            "Resolving query",
            extra={"query": query_orig, "normalized": query_norm}
        )

        # === Step 1: barcode 精确匹配 ===
        sku_dict = self._dict.get("sku_dict", {})
        if query_norm in sku_dict:
            sku_id = sku_dict[query_norm]
            _logger.info(
                "Matched by barcode",
                extra={"query": query_orig, "sku_id": sku_id}
            )
            return ResolveResult(
                sku_id=sku_id,
                spu_id=None,  # 后续查询SPU
                confidence=1.0,
                match_type=MatchType.BARCODE,
            )

        # === Step 2: alias 模糊匹配 ===
        alias_dict = self._dict.get("alias_dict", {})
        if alias_dict:
            alias_candidates = self._fuzzy_search(
                query_norm,
                alias_dict,
                self._fuzzy_threshold
            )
            if alias_candidates:
                best_key, best_score = alias_candidates[0]
                spu_id = alias_dict[best_key]
                _logger.info(
                    "Matched by alias",
                    extra={
                        "query": query_orig,
                        "alias": best_key,
                        "score": best_score,
                        "spu_id": spu_id,
                    }
                )
                return ResolveResult(
                    sku_id=None,
                    spu_id=spu_id,
                    confidence=self._score_to_confidence(best_score, MatchType.ALIAS),
                    match_type=MatchType.ALIAS,
                )

        # === Step 3: SKU名称匹配 ===
        # 注意：SKU名称存储在 product_sku.properties 中（JSON字符串）
        # 但字典中只存了 barcode -> sku_id，没有 sku_name -> sku_id
        # 这里有两种策略：
        #   a) 字典中额外建立 sku_name -> sku_id 映射（ETL阶段构建）
        #   b) 查询时去数据库查（性能差）
        # 我们选择方案a：在ETL阶段将 SKU的properties.name加入字典
        # 但当前字典结构没有 sku_dict 的 name 映射，所以这一步暂时跳过
        # TODO: 后续在ETL中增强，将SKU名称也加入sku_dict

        # === Step 4: SPU keyword 匹配 ===
        spu_dict = self._dict.get("spu_dict", {})
        if spu_dict:
            spu_candidates = self._fuzzy_search(
                query_norm,
                spu_dict,
                self._fuzzy_threshold
            )
            if spu_candidates:
                best_key, best_score = spu_candidates[0]
                spu_id = spu_dict[best_key]
                _logger.info(
                    "Matched by spu (keyword/name)",
                    extra={
                        "query": query_orig,
                        "matched_key": best_key,
                        "score": best_score,
                        "spu_id": spu_id,
                    }
                )
                return ResolveResult(
                    sku_id=None,
                    spu_id=spu_id,
                    confidence=self._score_to_confidence(best_score, MatchType.SPU_KEYWORD),
                    match_type=MatchType.SPU_KEYWORD,
                )

        # === Step 5: 无匹配 ===
        _logger.debug(
            "No product matched",
            extra={"query": query_orig, "normalized": query_norm}
        )
        return ResolveResult(
            sku_id=None,
            spu_id=None,
            confidence=0.0,
            match_type=MatchType.NONE,
        )

    def _fuzzy_search(
        self,
        query: str,
        mapping: dict[str, int],
        threshold: int,
    ) -> List[Tuple[str, float]]:
        """
        模糊搜索（使用rapidfuzz）

        Args:
            query: 查询文本（已normalize）
            mapping: 字典 {key -> value}
            threshold: 最小相似度阈值（0-100）

        Returns:
            候选列表 [(key, score)]，按分数降序
        """
        if not mapping:
            return []

        # rapidfuzz.process.extract 返回 [(key, score, index), ...]
        # 使用 partial_ratio 支持子串匹配，短查询更容易命中长关键词
        # 其他选项：token_set_ratio（分词后匹配）、token_sort_ratio（分词排序后匹配）
        results = process.extract(
            query,
            mapping.keys(),
            scorer=DEFAULT_SCORER,
            limit=self._max_candidates,
        )

        # 过滤低于阈值的候选
        filtered = [(key, score) for key, score, _ in results if score >= threshold]
        return filtered

    def _score_to_confidence(
        self,
        score: float,
        match_type: MatchType,
    ) -> float:
        """
        将模糊匹配分数转换为置信度

        公式：
        base_confidence = score / 100.0
        final = base_confidence * weight[match_type]

        Args:
            score: rapidfuzz返回的分数（0-100）
            match_type: 匹配类型

        Returns:
            置信度（0.0-1.0）
        """
        base = score / 100.0
        weight = self.MATCH_WEIGHTS.get(match_type, 0.5)
        return base * weight

    @property
    def dict_version(self) -> Optional[str]:
        """字典版本"""
        return self._dict.get("version")

    @property
    def dict_stats(self) -> Optional[dict]:
        """字典统计"""
        return self._dict.get("stats")
