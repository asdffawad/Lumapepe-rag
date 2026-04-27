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
    price: Optional[float] = None
    market_price: Optional[float] = None
    cost_price: Optional[float] = None
    suggest_sell_price: Optional[float] = None
    bar_code: Optional[str] = None
    supply_external_sku: Optional[str] = None  # 供应链SKU
    pic_url: Optional[str] = None
    stock: Optional[int] = None
    weight: Optional[float] = None  # 重量 kg
    volume: Optional[float] = None  # 体积 m^3
    sales_count: Optional[int] = None
    create_time: Optional[datetime] = None
    update_time: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "spu_id": self.spu_id,
            "properties": self.properties,
            "price": self.price,
            "market_price": self.market_price,
            "cost_price": self.cost_price,
            "suggest_sell_price": self.suggest_sell_price,
            "bar_code": self.bar_code,
            "supply_external_sku": self.supply_external_sku,
            "pic_url": self.pic_url,
            "stock": self.stock,
            "weight": self.weight,
            "volume": self.volume,
            "sales_count": self.sales_count,
            "create_time": self.create_time.isoformat() if self.create_time else None,
            "update_time": self.update_time.isoformat() if self.update_time else None,
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
    pic_url: Optional[str] = None
    slider_pic_urls: Optional[str] = None  # 轮播图，逗号分隔
    status: Optional[int] = None  # 1上架 0下架
    spec_type: Optional[bool] = None  # 0单规格 1多规格
    price: Optional[float] = None
    market_price: Optional[float] = None  # 单位：美元
    cost_price: Optional[float] = None
    stock: Optional[int] = None
    sales_count: Optional[int] = None
    virtual_sales_count: Optional[int] = None
    browse_count: Optional[int] = None
    sort: Optional[int] = None
    create_time: Optional[datetime] = None
    update_time: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "keyword": self.keyword,
            "introduction": self.introduction,
            "description": self.description,
            "category_id": self.category_id,
            "brand_id": self.brand_id,
            "pic_url": self.pic_url,
            "slider_pic_urls": self.slider_pic_urls,
            "status": self.status,
            "spec_type": self.spec_type,
            "price": self.price,
            "market_price": self.market_price,
            "cost_price": self.cost_price,
            "stock": self.stock,
            "sales_count": self.sales_count,
            "virtual_sales_count": self.virtual_sales_count,
            "browse_count": self.browse_count,
            "sort": self.sort,
            "create_time": self.create_time.isoformat() if self.create_time else None,
            "update_time": self.update_time.isoformat() if self.update_time else None,
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
