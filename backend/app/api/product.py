"""
商品检索 API 路由

提供以下端点：
- POST /products/search       - 商品检索（解析 + 查询）
- POST /products/resolve      - 仅解析
- GET  /products/sku/{sku_id} - 获取SKU详情
- GET  /products/spu/{spu_id} - 获取SPU详情
- POST /products/dict/reload  - 手动触发字典重载
- GET  /products/dict/stats   - 获取字典统计信息
"""
from __future__ import annotations

from typing import Optional
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field

from app.services.product_service import ProductService
from app.models.product import ResolveResult, ProductDetail, ProductSearchResult
from app.logging_config import get_logger

_logger = get_logger("api.product")

# ==================== 请求/响应模型 ====================

class ProductSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="商品查询文本")
    use_extended_search: bool = Field(default=True, description="匹配失败时是否尝试扩展搜索")

    model_config = {
        "json_schema_extra": {
            "example": {
                "query": "iPhone 16 Pro 256GB",
                "use_extended_search": True,
            }
        }
    }


class ProductResolveRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="商品查询文本")


@dataclass
class ProductSearchResponse:
    success: bool
    data: Optional[ProductSearchResult] = None
    error: Optional[str] = None


class ResolveResponse(BaseModel):
    sku_id: Optional[int] = None
    spu_id: Optional[int] = None
    confidence: float = 0.0
    match_type: str = "none"


class DictStatsResponse(BaseModel):
    version: Optional[str] = None
    stats: Optional[dict] = None
    last_loaded_at: Optional[str] = None


class DictRebuildRequest(BaseModel):
    output_path: Optional[str] = None


class APIResponse(BaseModel):
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None
    message: Optional[str] = None


# ==================== 路由定义 ====================

router = APIRouter(prefix="/products", tags=["商品检索"])


# 依赖注入：获取ProductService实例
# 注意：实际项目中应使用Depends或单例模式，这里简化处理
_product_service: Optional[ProductService] = None


def get_product_service() -> ProductService:
    """获取ProductService实例"""
    global _product_service
    if _product_service is None:
        raise RuntimeError("ProductService not initialized")
    return _product_service


def set_product_service(service: ProductService) -> None:
    """设置ProductService实例（由main.py在启动时调用）"""
    global _product_service
    _product_service = service


# ==================== API 端点 ====================

@router.post("/search", response_model=dict)
async def search_product(
    request: ProductSearchRequest,
    service: ProductService = Depends(get_product_service),
) -> dict:
    """
    商品检索主入口

    输入查询文本，返回匹配的商品详情

    Example:
        POST /products/search
        {"query": "iPhone 16 Pro 256GB"}
    """
    try:
        result = await service.search_product(
            query=request.query,
            use_extended_search=request.use_extended_search,
        )

        if result is None:
            return {
                "success": False,
                "error": "商品未匹配",
                "data": None,
            }

        return {
            "success": True,
            "data": result.to_dict(),
        }

    except Exception as e:
        _logger.error("Product search API failed", extra={"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"检索失败: {str(e)}",
        )


@router.post("/resolve", response_model=dict)
async def resolve_product(
    request: ProductResolveRequest,
    service: ProductService = Depends(get_product_service),
) -> dict:
    """
    仅解析商品（不查询数据库）

    返回解析结果：SKU/SPU ID 和置信度

    Example:
        POST /products/resolve
        {"query": "苹果手机"}
    """
    try:
        result = await service.resolve_only(request.query)

        return {
            "success": True,
            "data": {
                "sku_id": result.sku_id,
                "spu_id": result.spu_id,
                "confidence": result.confidence,
                "match_type": result.match_type.value,
            },
        }

    except Exception as e:
        _logger.error("Product resolve API failed", extra={"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"解析失败: {str(e)}",
        )


@router.get("/sku/{sku_id}", response_model=dict)
async def get_sku_detail(
    sku_id: int,
    service: ProductService = Depends(get_product_service),
) -> dict:
    """
    获取SKU详情

    Example:
        GET /products/sku/123
    """
    try:
        detail = await service.get_sku_detail(sku_id)

        if detail is None or detail.sku is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"SKU不存在: {sku_id}",
            )

        return {
            "success": True,
            "data": detail.to_dict(),
        }

    except HTTPException:
        raise
    except Exception as e:
        _logger.error("Get SKU detail API failed", extra={"sku_id": sku_id, "error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询失败: {str(e)}",
        )


@router.get("/spu/{spu_id}", response_model=dict)
async def get_spu_detail(
    spu_id: int,
    service: ProductService = Depends(get_product_service),
) -> dict:
    """
    获取SPU详情

    Example:
        GET /products/spu/456
    """
    try:
        detail = await service.get_spu_detail(spu_id)

        if detail is None or detail.spu is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"SPU不存在: {spu_id}",
            )

        return {
            "success": True,
            "data": detail.to_dict(),
        }

    except HTTPException:
        raise
    except Exception as e:
        _logger.error("Get SPU detail API failed", extra={"spu_id": spu_id, "error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询失败: {str(e)}",
        )


@router.post("/dict/reload", response_model=dict)
async def reload_dict(
    force: bool = Query(default=False, description="是否强制重载"),
    service: ProductService = Depends(get_product_service),
) -> dict:
    """
    手动触发字典重载

    Example:
        POST /products/dict/reload?force=true
    """
    try:
        success = await service.reload_dict(force=force)

        if success:
            return {
                "success": True,
                "message": "字典重载成功",
                "data": {
                    "version": service.dict_version,
                    "stats": service.dict_stats,
                },
            }
        else:
            return {
                "success": False,
                "error": "字典重载失败",
            }

    except Exception as e:
        _logger.error("Dict reload API failed", extra={"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"重载失败: {str(e)}",
        )


@router.post("/dict/rebuild", response_model=dict)
async def rebuild_dict(
    request: DictRebuildRequest = None,
    service: ProductService = Depends(get_product_service),
) -> dict:
    """
    重新构建字典（从数据库拉取）

    Example:
        POST /products/dict/rebuild
        {"output_path": "data/product_dict.json"}
    """
    try:
        output_path = request.output_path if request else None
        success = await service.rebuild_dict(output_path=output_path)

        if success:
            return {
                "success": True,
                "message": "字典重建成功",
                "data": {
                    "version": service.dict_version,
                    "stats": service.dict_stats,
                },
            }
        else:
            return {
                "success": False,
                "error": "字典重建失败",
            }

    except Exception as e:
        _logger.error("Dict rebuild API failed", extra={"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"重建失败: {str(e)}",
        )


@router.get("/dict/stats", response_model=dict)
async def get_dict_stats(
    service: ProductService = Depends(get_product_service),
) -> dict:
    """
    获取字典统计信息

    Example:
        GET /products/dict/stats
    """
    return {
        "success": True,
        "data": {
            "version": service.dict_version,
            "stats": service.dict_stats,
            "is_ready": service.is_ready,
        },
    }