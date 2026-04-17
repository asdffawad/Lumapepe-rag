"""
商品服务层（ProductService）

协调Resolver、Repository、DictLoader，提供统一的商品检索接口

职责：
- 协调商品解析和数据库查询
- 封装业务逻辑
- 提供字典热重载能力
- 预留扩展接口调用点
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional, List

from app.database.product import ProductRepository
from app.database.connection import get_db_pool
from app.etl.dict_loader import ProductDictLoader
from app.etl.dict_builder import ProductDict, build_product_dict, save_dict
from app.models.product import (
    ResolveResult,
    ProductDetail,
    ProductSearchResult,
    ProductDictStats,
    SkuInfo,
    SpuInfo,
)
from app.services.resolver.product_resolver import ProductResolver
from app.services.resolver.extensions import ESSearchService, VectorSearchService
from app.logging_config import get_logger
from app import metrics as _metrics

_logger = get_logger("product.service")


class ProductService:
    """
    商品服务层

    设计要点：
    - 协调 Resolver 和 Repository
    - Service层不直接访问数据库，只通过Repository
    - 支持字典热重载
    - 预留扩展接口（ES/向量搜索）
    - 全链路metrics埋点
    """

    def __init__(
        self,
        dict_loader: ProductDictLoader,
        repo: Optional[ProductRepository] = None,
        es_service: Optional[ESSearchService] = None,
        vector_service: Optional[VectorSearchService] = None,
        max_batch_size: int = 100,
    ):
        """
        Args:
            dict_loader: 字典加载器
            repo: 商品Repository（可选，默认从连接池创建）
            es_service: ES搜索服务（预留）
            vector_service: 向量搜索服务（预留）
            max_batch_size: 批量查询最大数量
        """
        self._dict_loader = dict_loader
        self._repo = repo
        self._es_service = es_service
        self._vector_service = vector_service
        self._max_batch_size = max_batch_size

        # Resolver实例（延迟创建）
        self._resolver: Optional[ProductResolver] = None

        _logger.info("ProductService initialized")

    @property
    def _product_repo(self) -> ProductRepository:
        """延迟创建Repository（当需要访问数据库时）"""
        if self._repo is None:
            pool = get_db_pool()
            self._repo = ProductRepository(pool)
        return self._repo

    async def _get_resolver(self) -> ProductResolver:
        """获取或创建Resolver实例"""
        if self._resolver is None:
            product_dict = await self._dict_loader.get()
            self._resolver = ProductResolver(product_dict)
        return self._resolver

    # ==================== 核心检索方法 ====================

    async def search_product(
        self,
        query: str,
        use_extended_search: bool = True,
    ) -> Optional[ProductSearchResult]:
        """
        商品检索主入口

        流程：
        1. 解析查询 → Resolver
        2. 查询详情 → Repository
        3. 组装结果

        Args:
            query: 用户查询文本
            use_extended_search: 当字典匹配失败时，是否尝试扩展搜索（ES/向量）

        Returns:
            ProductSearchResult 或 None
        """
        start = time.monotonic()

        try:
            # Step 1: 解析查询
            resolver = await self._get_resolver()
            resolve_result = await resolver.resolve(query)

            if not resolve_result.is_resolved:
                _logger.info("Product not resolved", extra={"query": query})
                if use_extended_search:
                    # TODO: 调用扩展搜索
                    _logger.debug("Extended search not implemented")
                # 记录失败
                _metrics.product_search_requests.labels(
                    match_type=resolve_result.match_type.value,
                    status="not_found"
                ).inc()
                return None

            # Step 2: 查询详情
            detail = await self._fetch_detail(resolve_result)

            # Step 3: 组装结果
            result = ProductSearchResult(
                detail=detail,
                resolve_result=resolve_result,
            )

            elapsed = (time.monotonic() - start) * 1000
            # 记录成功
            _metrics.product_search_requests.labels(
                match_type=resolve_result.match_type.value,
                status="success"
            ).inc()
            _metrics.product_search_latency_seconds.observe(elapsed / 1000)
            _metrics.product_search_success.labels(match_type=resolve_result.match_type.value).inc()

            _logger.info(
                "Product search completed",
                extra={
                    "query": query,
                    "match_type": resolve_result.match_type.value,
                    "sku_id": resolve_result.sku_id,
                    "spu_id": resolve_result.spu_id,
                    "confidence": resolve_result.confidence,
                    "latency_ms": elapsed,
                }
            )

            return result

        except Exception as e:
            _metrics.product_search_errors.labels(error_type=type(e).__name__).inc()
            _logger.error("Product search failed", extra={"query": query, "error": str(e)})
            raise

    async def resolve_only(self, query: str) -> ResolveResult:
        """
        仅解析（不查询数据库）

        用于：意图分类后判断是否需要查询商品详情

        Args:
            query: 查询文本

        Returns:
            ResolveResult
        """
        resolver = await self._get_resolver()
        return await resolver.resolve(query)

    async def get_sku_detail(self, sku_id: int) -> Optional[ProductDetail]:
        """
        获取SKU详情

        Args:
            sku_id: SKU ID

        Returns:
            ProductDetail 或 None
        """
        sku, spu = await self._product_repo.get_sku_with_spu(sku_id)
        if not sku:
            return None

        return ProductDetail(sku=sku, spu=spu)

    async def get_spu_detail(self, spu_id: int) -> Optional[ProductDetail]:
        """
        获取SPU详情（不包含具体SKU）

        Args:
            spu_id: SPU ID

        Returns:
            ProductDetail 或 None
        """
        spu = await self._product_repo.get_spu_by_id(spu_id)
        if not spu:
            return None

        return ProductDetail(sku=None, spu=spu)

    async def batch_get_products(
        self,
        sku_ids: List[int],
    ) -> List[ProductDetail]:
        """
        批量获取商品详情（优化N+1问题）

        Args:
            sku_ids: SKU ID列表

        Returns:
            ProductDetail 列表（只返回存在的记录）
        """
        if not sku_ids:
            return []

        # 限制批量大小
        sku_ids = sku_ids[:self._max_batch_size]

        # 批量查询SKU
        skus = await self._product_repo.batch_get_sku(sku_ids)
        if not skus:
            return []

        # 收集所有SPU ID
        spu_ids = list({sku.spu_id for sku in skus})
        spu_map = {spu.id: spu for spu in await self._product_repo.batch_get_spu(spu_ids)}

        # 组装结果
        results = []
        for sku in skus:
            spu = spu_map.get(sku.spu_id)
            results.append(ProductDetail(sku=sku, spu=spu))

        return results

    # ==================== 字典管理方法 ====================

    async def reload_dict(self, force: bool = False) -> bool:
        """
        手动触发字典重载

        Args:
            force: 是否强制重载

        Returns:
            是否重载成功
        """
        try:
            _logger.info("Manual dictionary reload triggered", extra={"force": force})

            # 触发字典重载
            await self._dict_loader.reload(force=force)

            # 重建Resolver
            self._resolver = None
            await self._get_resolver()

            _logger.info("Dictionary reload completed successfully")
            return True

        except Exception as e:
            _logger.error("Dictionary reload failed", extra={"error": str(e)})
            return False

    async def rebuild_dict(
        self,
        output_path: Optional[str] = None,
    ) -> bool:
        """
        重新构建字典（从数据库拉取数据）

        Args:
            output_path: 输出路径（默认使用loader的路径）

        Returns:
            是否构建成功
        """
        try:
            _logger.info("Dictionary rebuild started")

            # 从数据库拉取并构建
            pool = get_db_pool()
            product_dict = await build_product_dict(
                pool,
                output_path=output_path or str(self._dict_loader._dict_path),
                save=True,
            )

            # 触发重载
            await self._dict_loader.reload(force=True)
            self._resolver = None

            _logger.info(
                "Dictionary rebuild completed",
                extra={"stats": product_dict.get("stats")}
            )
            return True

        except Exception as e:
            _logger.error("Dictionary rebuild failed", extra={"error": str(e)})
            return False

    # ==================== 辅助方法 ====================

    async def _fetch_detail(
        self,
        resolve_result: ResolveResult,
    ) -> ProductDetail:
        """
        根据解析结果查询商品详情

        Args:
            resolve_result: 解析结果

        Returns:
            ProductDetail
        """
        if resolve_result.sku_id:
            return await self.get_sku_detail(resolve_result.sku_id) or ProductDetail()
        elif resolve_result.spu_id:
            return await self.get_spu_detail(resolve_result.spu_id) or ProductDetail()
        else:
            return ProductDetail()

    @property
    def dict_stats(self) -> Optional[dict]:
        """获取字典统计信息"""
        return self._dict_loader.stats

    @property
    def dict_version(self) -> Optional[str]:
        """获取字典版本"""
        return self._dict_loader.version

    @property
    def is_ready(self) -> bool:
        """服务是否就绪"""
        return self._dict_loader.is_loaded
