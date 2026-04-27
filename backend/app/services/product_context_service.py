"""
商品上下文服务
当用户从商品详情页进入AI客服时，提供商品信息上下文
"""
from typing import Optional
from app.database.product import ProductRepository
from app.database.connection import get_db_pool
from app.logging_config import get_logger
from app.services.product_info_parser import parse_product_info

_logger = get_logger("product.context")


class ProductContextService:
    """商品上下文服务"""

    def __init__(self, db_pool=None):
        self._db_pool = db_pool
        self._repo: Optional[ProductRepository] = None

    def _get_repo(self) -> Optional[ProductRepository]:
        """获取ProductRepository实例"""
        if self._repo is None and self._db_pool:
            self._repo = ProductRepository(self._db_pool)
        return self._repo

    async def get_product_context(
        self,
        sku_id: Optional[int] = None,
        spu_id: Optional[int] = None,
    ) -> Optional[dict]:
        """
        获取商品上下文信息

        Args:
            sku_id: SKU ID (优先)
            spu_id: SPU ID

        Returns:
            商品上下文字典，包含：
            - product_name: 商品名称
            - product_info: 商品详细信息
            - source: 来源 (sku/spu)
            - sku_id, spu_id
        """
        repo = self._get_repo()
        if not repo:
            _logger.warning("ProductRepository not available")
            return None

        try:
            product_info = {}

            # 优先使用SKU ID查询
            if sku_id:
                sku_detail, spu = await repo.get_sku_with_spu(sku_id)
                if sku_detail:
                    product_info = self._format_sku_context(sku_detail, spu)
                    product_info["source"] = "sku"
                    product_info["sku_id"] = sku_id
                    product_info["spu_id"] = sku_detail.spu_id
                    return product_info

            # 使用SPU ID查询
            if spu_id:
                spu = await repo.get_spu_by_id(spu_id)
                if spu:
                    product_info = self._format_spu_context(spu)
                    product_info["source"] = "spu"
                    product_info["spu_id"] = spu_id
                    return product_info

            return None

        except Exception as e:
            _logger.error(f"Failed to get product context: {e}")
            return None

    def _format_sku_context(self, sku, spu) -> dict:
        """格式化SKU商品上下文"""
        info = {
            "product_name": spu.name if spu else "Unknown",
            "product_type": "SKU",
        }

        if spu:
            # 使用商品信息解析器处理简介和详情
            parsed = parse_product_info(
                introduction=spu.introduction or "",
                description=spu.description or ""
            )

            info.update({
                "name": spu.name,
                "keyword": spu.keyword,
                "introduction": spu.introduction,
                "description": spu.description,
                "parsed_introduction": [self._segment_to_dict(s) for s in parsed.parsed_introduction],
                "parsed_description": [self._segment_to_dict(s) for s in parsed.parsed_description],
                "has_images": parsed.has_images,
                "has_html": parsed.has_html,
                "category_id": spu.category_id,
                "brand_id": spu.brand_id,
                "spu_price": float(spu.price) if spu.price else None,
                "spu_stock": spu.stock,
                "spu_pic_url": spu.pic_url,
                "status": spu.status,
            })

        info.update({
            "sku_id": sku.id,
            "sku_price": float(sku.price) if sku.price else None,
            "sku_stock": sku.stock,
            "sku_pic_url": sku.pic_url,
            "bar_code": sku.bar_code,
            "supply_external_sku": sku.supply_external_sku,
            "properties": sku.properties,
            "weight": sku.weight,
            "volume": sku.volume,
        })

        return info

    def _segment_to_dict(self, seg) -> dict:
        """将 ParsedTextSegment 或字典转换为字典"""
        if isinstance(seg, dict):
            return seg
        return {
            "type": seg.type,
            "content": seg.content,
            "original": seg.original,
        }

    def _format_spu_context(self, spu) -> dict:
        """格式化SPU商品上下文"""
        # 使用商品信息解析器处理简介和详情
        parsed = parse_product_info(
            introduction=spu.introduction or "",
            description=spu.description or ""
        )

        return {
            "product_name": spu.name,
            "product_type": "SPU",
            "name": spu.name,
            "keyword": spu.keyword,
            "introduction": spu.introduction,
            "description": spu.description,
            "parsed_introduction": [self._segment_to_dict(s) for s in parsed.parsed_introduction],
            "parsed_description": [self._segment_to_dict(s) for s in parsed.parsed_description],
            "has_images": parsed.has_images,
            "has_html": parsed.has_html,
            "category_id": spu.category_id,
            "brand_id": spu.brand_id,
            "price": float(spu.price) if spu.price else None,
            "market_price": float(spu.market_price) if spu.market_price else None,
            "stock": spu.stock,
            "sales_count": spu.sales_count,
            "pic_url": spu.pic_url,
            "slider_pic_urls": spu.slider_pic_urls,
            "status": spu.status,
        }

    def format_system_prompt(self, product_context: dict) -> str:
        """
        将商品上下文格式化为系统提示

        Args:
            product_context: 商品上下文字典

        Returns:
            系统提示字符串
        """
        if not product_context:
            return ""

        product_type = product_context.get("product_type", "SPU")
        name = product_context.get("product_name", "Unknown")

        prompt_parts = [
            f"商品信息：",
            f"商品名称: {name}",
        ]

        # SKU特有信息
        if product_type == "SKU":
            if product_context.get("sku_price"):
                prompt_parts.append(f"价格: ${product_context['sku_price']:.2f}")
            if product_context.get("sku_stock") is not None:
                prompt_parts.append(f"库存: {product_context['sku_stock']}")
            if product_context.get("bar_code"):
                prompt_parts.append(f"条形码: {product_context['bar_code']}")
            if product_context.get("properties"):
                prompt_parts.append(f"规格属性: {product_context['properties']}")

        # SPU信息
        if product_context.get("price"):
            prompt_parts.append(f"商品价格: ${product_context['price']:.2f}")
        if product_context.get("stock") is not None:
            prompt_parts.append(f"库存: {product_context['stock']}")

        # 使用解析后的商品简介（纯文本）
        if product_context.get("parsed_introduction"):
            intro_segments = product_context["parsed_introduction"]
            intro_texts = [seg["content"] for seg in intro_segments if seg.get("type") == "text"]
            if intro_texts:
                prompt_parts.append(' '.join(intro_texts))

        # 使用解析后的商品详情（纯文本）
        if product_context.get("parsed_description"):
            desc_segments = product_context["parsed_description"]
            desc_texts = [seg["content"] for seg in desc_segments if seg.get("type") == "text"]
            if desc_texts:
                desc = ' '.join(desc_texts)
                if len(desc) > 500:
                    desc = desc[:500] + "..."
                prompt_parts.append(desc)

        return "\n".join(prompt_parts)


# 全局实例
_product_context_service: Optional[ProductContextService] = None


def get_product_context_service() -> ProductContextService:
    """获取商品上下文服务实例"""
    global _product_context_service
    if _product_context_service is None:
        try:
            pool = get_db_pool()
            _product_context_service = ProductContextService(pool)
        except Exception:
            _product_context_service = ProductContextService()
    return _product_context_service
