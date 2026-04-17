"""
商品检索扩展接口（预留）

这些接口用于未来扩展，当前版本仅声明协议，不实现具体逻辑
"""
from __future__ import annotations

from typing import Protocol, List, Dict, Any


class ESSearchService(Protocol):
    """ES搜索服务接口（预留）"""

    async def search(
        self,
        query: str,
        top_k: int = 10,
        filters: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        """
        使用Elasticsearch进行商品检索

        Args:
            query: 查询文本
            top_k: 返回结果数量
            filters: 过滤条件（如category_id, brand_id等）

        Returns:
            匹配的商品ID列表
        """
        ...


class VectorSearchService(Protocol):
    """向量搜索服务接口（预留）"""

    async def search(
        self,
        query: str,
        top_k: int = 10,
        filters: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        """
        使用向量数据库进行商品检索

        Args:
            query: 查询文本
            top_k: 返回结果数量
            filters: 过滤条件

        Returns:
            匹配的商品ID列表及相似度分数
        """
        ...


class HybridSearchService(Protocol):
    """混合搜索服务接口（预留）"""

    async def search(
        self,
        query: str,
        top_k: int = 10,
        strategy: str = "rrf",  # "rrf" | "weighted"
    ) -> List[Dict[str, Any]]:
        """
        混合搜索：结合文本匹配 + 向量搜索

        Args:
            query: 查询文本
            top_k: 返回结果数量
            strategy: 融合策略

        Returns:
            融合后的排序结果
        """
        ...