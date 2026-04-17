"""
商品数据访问层（ProductRepository）
使用 aiomysql 异步驱动，支持参数化查询和批量查询优化
"""
from __future__ import annotations

from typing import Optional, NamedTuple
from dataclasses import dataclass

import aiomysql
from aiomysql import DictCursor

from app.models.product import SkuInfo, SpuInfo
from app.logging_config import get_logger

_logger = get_logger("product.repository")


class ProductTuple(NamedTuple):
    """商品查询结果元组（用于避免DictCursor序列化开销）"""
    sku_id: int
    spu_id: int
    bar_code: Optional[str]
    properties: str
    price: float
    stock: int
    pic_url: Optional[str]
    sku_created_at: Optional[str]
    sku_updated_at: Optional[str]


class ProductRepository:
    """
    商品数据访问层

    设计原则：
    - 使用参数化查询，防止SQL注入
    - 批量查询使用 IN 语句，避免 N+1 问题
    - 所有查询走连接池，高并发友好
    """

    def __init__(self, pool: aiomysql.Pool):
        self._pool = pool

    async def get_sku_by_id(self, sku_id: int) -> Optional[SkuInfo]:
        """
        根据SKU ID查询SKU详情

        Args:
            sku_id: SKU主键

        Returns:
            SkuInfo 或 None（不存在时）
        """
        async with self._pool.acquire() as conn:
            async with conn.cursor(DictCursor) as cur:
                await cur.execute(
                    "SELECT * FROM product_sku WHERE id = %s",
                    (sku_id,)
                )
                row = await cur.fetchone()

                if not row:
                    _logger.debug("SKU not found", extra={"sku_id": sku_id})
                    return None

                return self._row_to_sku(row)

    async def get_spu_by_id(self, spu_id: int) -> Optional[SpuInfo]:
        """
        根据SPU ID查询SPU详情

        Args:
            spu_id: SPU主键

        Returns:
            SpuInfo 或 None（不存在时）
        """
        async with self._pool.acquire() as conn:
            async with conn.cursor(DictCursor) as cur:
                await cur.execute(
                    "SELECT * FROM product_spu WHERE id = %s",
                    (spu_id,)
                )
                row = await cur.fetchone()

                if not row:
                    _logger.debug("SPU not found", extra={"spu_id": spu_id})
                    return None

                return self._row_to_spu(row)

    async def batch_get_sku(self, sku_ids: list[int]) -> list[SkuInfo]:
        """
        批量查询SKU（优化N+1问题）

        Args:
            sku_ids: SKU ID列表

        Returns:
            SkuInfo 列表（只返回存在的记录）
        """
        if not sku_ids:
            return []

        placeholders = ",".join(["%s"] * len(sku_ids))
        sql = f"SELECT * FROM product_sku WHERE id IN ({placeholders})"

        async with self._pool.acquire() as conn:
            async with conn.cursor(DictCursor) as cur:
                await cur.execute(sql, tuple(sku_ids))
                rows = await cur.fetchall()

                _logger.debug(
                    "Batch get SKU",
                    extra={"requested": len(sku_ids), "found": len(rows)}
                )

                return [self._row_to_sku(row) for row in rows]

    async def batch_get_spu(self, spu_ids: list[int]) -> list[SpuInfo]:
        """
        批量查询SPU（优化N+1问题）

        Args:
            spu_ids: SPU ID列表

        Returns:
            SpuInfo 列表（只返回存在的记录）
        """
        if not spu_ids:
            return []

        placeholders = ",".join(["%s"] * len(spu_ids))
        sql = f"SELECT * FROM product_spu WHERE id IN ({placeholders})"

        async with self._pool.acquire() as conn:
            async with conn.cursor(DictCursor) as cur:
                await cur.execute(sql, tuple(spu_ids))
                rows = await cur.fetchall()

                _logger.debug(
                    "Batch get SPU",
                    extra={"requested": len(spu_ids), "found": len(rows)}
                )

                return [self._row_to_spu(row) for row in rows]

    async def get_all_sku(self) -> list[SkuInfo]:
        """
        获取所有SKU（ETL用，全量拉取）

        Returns:
            全部 SkuInfo 列表
        """
        async with self._pool.acquire() as conn:
            async with conn.cursor(DictCursor) as cur:
                await cur.execute("SELECT * FROM product_sku WHERE bar_code IS NOT NULL")
                rows = await cur.fetchall()

                _logger.info("Fetch all SKU for ETL", extra={"count": len(rows)})
                return [self._row_to_sku(row) for row in rows]

    async def get_all_spu(self) -> list[SpuInfo]:
        """
        获取所有SPU（ETL用，全量拉取）

        Returns:
            全部 SpuInfo 列表
        """
        async with self._pool.acquire() as conn:
            async with conn.cursor(DictCursor) as cur:
                await cur.execute("SELECT * FROM product_spu")
                rows = await cur.fetchall()

                _logger.info("Fetch all SPU for ETL", extra={"count": len(rows)})
                return [self._row_to_spu(row) for row in rows]

    async def get_all_aliases(self) -> list[dict]:
        """
        获取所有商品别名（ETL用）

        Returns:
            别名字典列表 [{"spu_id": 1, "alias_name": "xxx"}, ...]
        """
        async with self._pool.acquire() as conn:
            async with conn.cursor(DictCursor) as cur:
                try:
                    await cur.execute("SELECT spu_id, alias_name FROM product_alias")
                    rows = await cur.fetchall()
                    _logger.info("Fetch all aliases for ETL", extra={"count": len(rows)})
                    return [{"spu_id": row["spu_id"], "alias_name": row["alias_name"]} for row in rows]
                except Exception as e:
                    if "product_alias" in str(e) and "doesn't exist" in str(e):
                        _logger.warning("product_alias table does not exist, skipping")
                        return []
                    raise

    async def get_sku_with_spu(self, sku_id: int) -> tuple[Optional[SkuInfo], Optional[SpuInfo]]:
        """
        一次性获取SKU及其关联的SPU（减少数据库往返）

        Args:
            sku_id: SKU主键

        Returns:
            (SkuInfo, SpuInfo) 元组
        """
        async with self._pool.acquire() as conn:
            async with conn.cursor(DictCursor) as cur:
                await cur.execute(
                    """
                    SELECT
                        s.id as sku_id, s.spu_id, s.bar_code, s.properties,
                        s.price as sku_price, s.stock as sku_stock, s.pic_url,
                        s.created_at as sku_created_at, s.updated_at as sku_updated_at,
                        p.id as spu_id, p.name, p.keyword, p.introduction,
                        p.description, p.category_id, p.brand_id,
                        p.price as spu_price, p.stock as spu_stock,
                        p.created_at as spu_created_at, p.updated_at as spu_updated_at
                    FROM product_sku s
                    LEFT JOIN product_spu p ON s.spu_id = p.id
                    WHERE s.id = %s
                    """,
                    (sku_id,)
                )
                row = await cur.fetchone()

                if not row:
                    return None, None

                sku = SkuInfo(
                    id=row["sku_id"],
                    spu_id=row["spu_id"],
                    bar_code=row["bar_code"],
                    properties=row["properties"],
                    price=row["sku_price"],
                    stock=row["sku_stock"],
                    pic_url=row["pic_url"],
                    created_at=row["sku_created_at"],
                    updated_at=row["sku_updated_at"],
                )

                spu = None
                if row["spu_id"]:
                    spu = SpuInfo(
                        id=row["spu_id"],
                        name=row["name"],
                        keyword=row["keyword"],
                        introduction=row["introduction"],
                        description=row["description"],
                        category_id=row["category_id"],
                        brand_id=row["brand_id"],
                        price=row["spu_price"],
                        stock=row["spu_stock"],
                        created_at=row["spu_created_at"],
                        updated_at=row["spu_updated_at"],
                    )

                return sku, spu

    def _row_to_sku(self, row: dict) -> SkuInfo:
        """将数据库行转换为 SkuInfo 对象"""
        return SkuInfo(
            id=row["id"],
            spu_id=row["spu_id"],
            bar_code=row.get("bar_code"),
            properties=row.get("properties", "{}"),
            price=float(row.get("price", 0) or 0),
            stock=int(row.get("stock", 0) or 0),
            pic_url=row.get("pic_url"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    def _row_to_spu(self, row: dict) -> SpuInfo:
        """将数据库行转换为 SpuInfo 对象"""
        return SpuInfo(
            id=row["id"],
            name=row["name"],
            keyword=row.get("keyword"),
            introduction=row.get("introduction"),
            description=row.get("description"),
            category_id=row.get("category_id"),
            brand_id=row.get("brand_id"),
            price=float(row["price"]) if row.get("price") else None,
            stock=int(row["stock"]) if row.get("stock") else None,
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )
