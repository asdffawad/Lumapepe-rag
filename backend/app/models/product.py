"""
商品检索模块数据模型定义
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from dataclasses import dataclass
from enum import Enum


class MatchType(str, Enum):
    """匹配类型枚举"""
    BARCODE = "barcode"              # 条形码精确匹配
    SKU_CODE = "sku_code"           # 外部SKU编码匹配 (supply_external_sku)
    ALIAS = "alias"                 # 别名匹配
    SPU_KEYWORD = "spu_keyword"     # SPU关键词匹配
    SPU_NAME = "spu_name"           # SPU名称匹配
    NONE = "none"                   # 无匹配


@dataclass
class ResolveResult:
    """商品解析结果"""
    sku_id: Optional[int] = None
    spu_id: Optional[int] = None
    confidence: float = 0.0
    match_type: MatchType = MatchType.NONE

    @property
    def is_resolved(self) -> bool:
        """是否成功解析到商品"""
        return self.sku_id is not None or self.spu_id is not None

    def to_dict(self) -> dict:
        return {
            "sku_id": self.sku_id,
            "spu_id": self.spu_id,
            "confidence": self.confidence,
            "match_type": self.match_type.value,
        }


@dataclass
class SkuInfo:
    """SKU信息（数据库原始数据）"""
    id: int
    spu_id: int
    properties: str  # JSON字符串
    price: float
    stock: int
    bar_code: Optional[str] = None
    supply_external_sku: Optional[str] = None  # 外部SKU编码
    pic_url: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "spu_id": self.spu_id,
            "properties": self.properties,
            "price": self.price,
            "stock": self.stock,
            "bar_code": self.bar_code,
            "supply_external_sku": self.supply_external_sku,
            "pic_url": self.pic_url,
        }


@dataclass
class SpuInfo:
    """SPU信息（数据库原始数据）"""
    id: int
    name: str
    keyword: Optional[str] = None
    introduction: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[int] = None
    brand_id: Optional[int] = None
    price: Optional[float] = None
    stock: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "keyword": self.keyword,
            "introduction": self.introduction,
            "description": self.description,
            "category_id": self.category_id,
            "brand_id": self.brand_id,
            "price": self.price,
            "stock": self.stock,
        }


@dataclass
class ProductDetail:
    """商品详情（合并SKU和SPU信息）"""
    sku: Optional[SkuInfo] = None
    spu: Optional[SpuInfo] = None
    match_info: Optional[ResolveResult] = None

    def to_dict(self) -> dict:
        return {
            "sku": self.sku.to_dict() if self.sku else None,
            "spu": self.spu.to_dict() if self.spu else None,
            "match": self.match_info.to_dict() if self.match_info else None,
        }


@dataclass
class ProductSearchResult:
    """商品检索结果"""
    detail: ProductDetail
    resolve_result: ResolveResult

    def to_dict(self) -> dict:
        return {
            "detail": self.detail.to_dict(),
            "resolve_result": self.resolve_result.to_dict(),
        }


@dataclass
class ProductDictStats:
    """商品字典统计信息"""
    sku_count: int = 0
    spu_count: int = 0
    alias_count: int = 0
    built_at: Optional[datetime] = None
    version: str = ""

    def to_dict(self) -> dict:
        return {
            "sku_count": self.sku_count,
            "spu_count": self.spu_count,
            "alias_count": self.alias_count,
            "built_at": self.built_at.isoformat() if self.built_at else None,
            "version": self.version,
        }
