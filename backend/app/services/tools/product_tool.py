"""
商品查询工具
支持按商品ID、商品名称、关键词、分类、品牌、条形码、供应链SKU查询商品信息
"""
from app.services.tools.base import ToolBase, ToolResult, register_tool
from typing import Optional, List, Dict, Any


@register_tool(
    name="product_query",
    description="""查询商品信息，支持多种查询方式：
1. 商品ID查询（SKU ID 或 SPU ID）
2. 商品名称/关键词查询
3. 分类查询（category_id）
4. 品牌查询（brand_id）
5. 条形码查询（bar_code）
6. 供应链SKU查询（supply_external_sku）

返回商品详情、价格、库存、图片等信息。""",
    param_schema={
        "type": "object",
        "properties": {
            "product_id": {
                "type": ["integer", "string"],
                "description": "商品ID（SKU ID 或 SPU ID，可选）"
            },
            "product_name": {
                "type": "string",
                "description": "商品名称或关键词（可选）"
            },
            "category_id": {
                "type": "integer",
                "description": "商品分类ID（可选）"
            },
            "brand_id": {
                "type": "integer",
                "description": "商品品牌ID（可选）"
            },
            "barcode": {
                "type": "string",
                "description": "条形码（可选）"
            },
            "supply_sku": {
                "type": "string",
                "description": "供应链SKU编号（可选）"
            },
            "limit": {
                "type": "integer",
                "description": "返回结果数量限制（默认10）"
            }
        },
        "additionalProperties": False
    },
)
class ProductTool(ToolBase):
    """
    商品查询工具
    
    功能：
    1. 根据商品ID精确查询（SKU/SPU）
    2. 根据商品名称/关键词模糊查询
    3. 根据分类/品牌查询
    4. 根据条形码/供应链SKU查询
    
    返回：商品基本信息（名称、价格、库存、图片等）
    """
    _source = "商品系统"
    default_timeout = 5.0  # 商品查询可能涉及更多数据，给予更长时间
    
    def __init__(self):
        super().__init__()
        self._product_service = None
        self._repo = None
    
    def _get_product_service(self):
        """获取ProductService实例"""
        if self._product_service is None:
            try:
                from app.main import app
                self._product_service = getattr(app.state, 'product_service', None)
            except Exception:
                self._product_service = None
        return self._product_service
    
    def _get_repo(self):
        """获取ProductRepository实例"""
        if self._repo is None:
            try:
                from app.database.connection import get_db_pool
                from app.database.product import ProductRepository
                pool = get_db_pool()
                self._repo = ProductRepository(pool)
            except Exception:
                self._repo = None
        return self._repo
    
    async def execute(self, params: dict) -> ToolResult:
        """
        执行商品查询
        
        Args:
            params: 查询参数
                - product_id: 商品ID (int/str)
                - product_name: 商品名称关键词 (str)
                - category_id: 分类ID (int)
                - brand_id: 品牌ID (int)
                - barcode: 条形码 (str)
                - supply_sku: 供应链SKU (str)
                - limit: 结果数量限制 (int)
        
        Returns:
            ToolResult: 查询结果
        """
        # 参数提取
        product_id = params.get("product_id")
        product_name = params.get("product_name", "").strip()
        category_id = params.get("category_id")
        brand_id = params.get("brand_id")
        barcode = params.get("barcode", "").strip()
        supply_sku = params.get("supply_sku", "").strip()
        limit = params.get("limit", 10)
        
        # 验证limit范围
        if not isinstance(limit, int) or limit <= 0 or limit > 100:
            limit = 10
        
        # 优先级：精确ID查询 > 条形码/供应链SKU > 分类/品牌 > 商品名称
        if product_id:
            return await self._query_by_id(product_id)
        elif barcode:
            return await self._query_by_barcode(barcode)
        elif supply_sku:
            return await self._query_by_supply_sku(supply_sku)
        elif category_id:
            return await self._query_by_category(category_id, limit)
        elif brand_id:
            return await self._query_by_brand(brand_id, limit)
        elif product_name:
            return await self._query_by_name(product_name, limit)
        else:
            return self._error_result(
                "请提供以下查询条件之一：商品ID、商品名称、分类ID、品牌ID、条形码或供应链SKU"
            )
    
    async def _query_by_id(self, product_id: Any) -> ToolResult:
        """根据商品ID查询（支持SKU或SPU）"""
        try:
            repo = self._get_repo()
            if not repo:
                return self._error_result("商品服务未初始化，请稍后重试")
            
            pid = int(product_id) if str(product_id).isdigit() else None
            if pid is None:
                return self._error_result(f"无效的商品ID: {product_id}")
            
            # 先尝试作为SKU ID查询
            sku_detail, spu_detail = await repo.get_sku_with_spu(pid)
            if sku_detail:
                return self._success_result({
                    "products": [self._format_sku_detail(sku_detail, spu_detail)],
                    "total": 1,
                    "query_type": "sku_id",
                    "query_value": pid
                })
            
            # 尝试作为SPU ID查询
            spu = await repo.get_spu_by_id(pid)
            if spu:
                return self._success_result({
                    "products": [self._format_spu_detail(spu)],
                    "total": 1,
                    "query_type": "spu_id",
                    "query_value": pid
                })
            
            return self._error_result(f"未找到ID为 {pid} 的商品")
            
        except Exception as e:
            return self._error_result(f"查询失败: {str(e)}")
    
    async def _query_by_barcode(self, barcode: str) -> ToolResult:
        """根据条形码查询"""
        try:
            repo = self._get_repo()
            if not repo:
                return self._error_result("商品服务未初始化，请稍后重试")
            
            sku = await repo.get_sku_by_barcode(barcode)
            if not sku:
                return self._error_result(f"未找到条形码为 {barcode} 的商品")
            
            sku_detail, spu_detail = await repo.get_sku_with_spu(sku.id)
            return self._success_result({
                "products": [self._format_sku_detail(sku_detail, spu_detail)],
                "total": 1,
                "query_type": "barcode",
                "query_value": barcode
            })
            
        except Exception as e:
            return self._error_result(f"查询失败: {str(e)}")
    
    async def _query_by_supply_sku(self, supply_sku: str) -> ToolResult:
        """根据供应链SKU查询"""
        try:
            repo = self._get_repo()
            if not repo:
                return self._error_result("商品服务未初始化，请稍后重试")
            
            skus = await repo.get_sku_by_supply_sku(supply_sku)
            if not skus:
                return self._error_result(f"未找到供应链SKU为 {supply_sku} 的商品")
            
            products = []
            for sku in skus[:10]:
                sku_detail, spu_detail = await repo.get_sku_with_spu(sku.id)
                if sku_detail:
                    products.append(self._format_sku_detail(sku_detail, spu_detail))
            
            return self._success_result({
                "products": products,
                "total": len(products),
                "query_type": "supply_sku",
                "query_value": supply_sku
            })
            
        except Exception as e:
            return self._error_result(f"查询失败: {str(e)}")
    
    async def _query_by_category(self, category_id: int, limit: int) -> ToolResult:
        """根据分类查询"""
        try:
            repo = self._get_repo()
            if not repo:
                return self._error_result("商品服务未初始化，请稍后重试")
            
            spus = await repo.get_spu_by_category(category_id, limit)
            if not spus:
                return self._error_result(f"分类ID {category_id} 下暂无商品")
            
            products = [self._format_spu_detail(spu) for spu in spus]
            return self._success_result({
                "products": products,
                "total": len(products),
                "query_type": "category",
                "query_value": category_id
            })
            
        except Exception as e:
            return self._error_result(f"查询失败: {str(e)}")
    
    async def _query_by_brand(self, brand_id: int, limit: int) -> ToolResult:
        """根据品牌查询"""
        try:
            repo = self._get_repo()
            if not repo:
                return self._error_result("商品服务未初始化，请稍后重试")
            
            spus = await repo.get_spu_by_brand(brand_id, limit)
            if not spus:
                return self._error_result(f"品牌ID {brand_id} 下暂无商品")
            
            products = [self._format_spu_detail(spu) for spu in spus]
            return self._success_result({
                "products": products,
                "total": len(products),
                "query_type": "brand",
                "query_value": brand_id
            })
            
        except Exception as e:
            return self._error_result(f"查询失败: {str(e)}")
    
    async def _query_by_name(self, product_name: str, limit: int) -> ToolResult:
        """根据商品名称/关键词查询"""
        try:
            product_service = self._get_product_service()
            
            if not product_service:
                return self._error_result("商品服务未初始化，请稍后重试")
            
            # 使用ProductService进行商品检索（基于字典匹配）
            result = await product_service.search_product(product_name)
            
            if not result or not result.detail:
                return self._error_result(f"未找到与 '{product_name}' 相关的商品")
            
            # 格式化结果
            detail = result.detail
            products = []
            
            # SPU信息
            if detail.spu:
                products.append(self._format_spu_detail(detail.spu))
            
            # SKU信息
            if detail.sku and detail.spu:
                products[0]["sku"] = self._format_sku_simple(detail.sku)
            
            return self._success_result({
                "products": products[:limit],
                "total": len(products),
                "match_info": {
                    "match_type": result.resolve_result.match_type.value if result.resolve_result else "unknown",
                    "confidence": result.resolve_result.confidence if result.resolve_result else 0.0,
                },
                "query_type": "name",
                "query_value": product_name
            })
            
        except Exception as e:
            return self._error_result(f"查询失败: {str(e)}")
    
    def _format_spu_detail(self, spu) -> Dict[str, Any]:
        """格式化SPU详情"""
        return {
            "type": "spu",
            "spu_id": spu.id,
            "name": spu.name,
            "keyword": spu.keyword,
            "introduction": spu.introduction,
            "description": spu.description,
            "category_id": spu.category_id,
            "brand_id": spu.brand_id,
            "price": float(spu.price) / 100 if spu.price else None,  # 分转元
            "market_price": float(spu.market_price) / 100 if spu.market_price else None,
            "stock": spu.stock,
            "sales_count": spu.sales_count,
            "pic_url": spu.pic_url,
            "slider_pic_urls": spu.slider_pic_urls,
            "status": spu.status,
            "status_text": "上架" if spu.status == 1 else "下架" if spu.status == 0 else "其他",
            "spec_type": "多规格" if spu.spec_type else "单规格",
        }
    
    def _format_sku_detail(self, sku, spu) -> Dict[str, Any]:
        """格式化SKU详情（含关联SPU）"""
        result = {
            "type": "sku",
            "sku_id": sku.id,
            "spu_id": sku.spu_id,
            "bar_code": sku.bar_code,
            "supply_external_sku": sku.supply_external_sku,
            "properties": sku.properties,
            "price": float(sku.price) if sku.price else None,  # 已是美元
            "market_price": float(sku.market_price) if sku.market_price else None,
            "stock": sku.stock,
            "pic_url": sku.pic_url,
            "weight": sku.weight,
            "volume": sku.volume,
        }
        
        if spu:
            result["spu_name"] = spu.name
            result["spu_keyword"] = spu.keyword
            result["spu_price"] = float(spu.price) / 100 if spu.price else None,
            result["spu_pic_url"] = spu.pic_url,
        
        return result
    
    def _format_sku_simple(self, sku) -> Dict[str, Any]:
        """格式化简单SKU信息"""
        return {
            "sku_id": sku.id,
            "spu_id": sku.spu_id,
            "bar_code": sku.bar_code,
            "properties": sku.properties,
            "price": float(sku.price) if sku.price else None,
            "stock": sku.stock,
            "pic_url": sku.pic_url,
        }
    
    def _success_result(self, data) -> ToolResult:
        """构建成功结果"""
        return ToolResult(success=True, data=data, source=self.source)
    
    def _error_result(self, error: str) -> ToolResult:
        """构建错误结果"""
        return ToolResult(success=False, error=error, source=self.source)
