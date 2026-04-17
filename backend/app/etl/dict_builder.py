"""
商品字典构建器
负责从数据库拉取数据，构建三字典结构，并持久化到JSON
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, TypedDict
from dataclasses import dataclass, asdict

import aiomysql

from app.database.product import ProductRepository
from app.models.product import SkuInfo, SpuInfo
from app.logging_config import get_logger

_logger = get_logger("etl.dict_builder")


# ============== 类型定义 ==============

class ProductDict(TypedDict):
    """商品字典数据结构（对应JSON文件格式）"""
    version: str
    built_at: str
    stats: dict
    sku_dict: dict[str, int]      # barcode_normalized -> sku_id
    spu_dict: dict[str, int]      # name/keyword_normalized -> spu_id
    alias_dict: dict[str, int]    # alias_normalized -> spu_id


@dataclass
class DictStats:
    """字典构建统计"""
    sku_count: int = 0
    spu_count: int = 0
    alias_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ============== 文本标准化工具 ==============

def normalize_text(text: str, for_alias: bool = False) -> str:
    """
    文本标准化：去除空格、符号，转小写

    Args:
        text: 原始文本
        for_alias: 是否为别名模式（保持中文字符，去除所有非字母数字汉字字符）

    Returns:
        标准化后的文本
    """
    if not text:
        return ""

    # 去除首尾空格，转小写
    text = text.strip().lower()

    # 去除所有空格、换行符
    text = re.sub(r'\s+', '', text)

    # 去除常见符号：-_./*等
    text = re.sub(r'[-_./*+\\|~!@#$%^&*()=+\[\]{};\'",.<>?/`]+', '', text)

    if for_alias:
        # 别名模式：保留中文字符、字母、数字
        text = re.sub(r'[^a-z0-9\u4e00-\u9fa5]', '', text)
    else:
        # 商品名/关键词：保留中英文、数字，去除其他符号
        text = re.sub(r'[^a-z0-9\u4e00-\u9fa5]', '', text)

    return text


# ============== 字典构建器 ==============

class ProductDictBuilder:
    """
    商品字典构建器

    职责：
    - 从MySQL全量拉取商品数据
    - 构建三字典结构（sku_dict / spu_dict / alias_dict）
    - 执行normalize统一化
    - 持久化到JSON文件
    """

    def __init__(
        self,
        repo: ProductRepository,
        fuzzy_match_threshold: int = 80,
    ):
        """
        Args:
            repo: 商品Repository实例
            fuzzy_match_threshold: 模糊匹配阈值（用于别名去重）
        """
        self._repo = repo
        self._fuzzy_threshold = fuzzy_match_threshold

    async def build(self) -> ProductDict:
        """
        构建字典主流程

        Returns:
            构建完成的字典对象
        """
        _logger.info("Starting product dictionary build")

        # Step 1: 并行拉取数据
        start = time.monotonic()
        sku_data, spu_data, alias_data = await self._fetch_all_data()
        _logger.info(
            "Data fetched",
            extra={"sku": len(sku_data), "spu": len(spu_data), "alias": len(alias_data)}
        )

        # Step 2: 构建字典
        sku_dict, spu_dict, alias_dict = self._build_dicts(sku_data, spu_data, alias_data)
        _logger.info(
            "Dicts built",
            extra={"sku_dict": len(sku_dict), "spu_dict": len(spu_dict), "alias_dict": len(alias_dict)}
        )

        # Step 3: 生成版本信息
        version = datetime.now().strftime("%Y%m%d_%H%M%S")
        built_at = datetime.now().isoformat()

        product_dict: ProductDict = {
            "version": version,
            "built_at": built_at,
            "stats": {
                "sku_count": len(sku_data),
                "spu_count": len(spu_data),
                "alias_count": len(alias_data),
            },
            "sku_dict": sku_dict,
            "spu_dict": spu_dict,
            "alias_dict": alias_dict,
        }

        elapsed = time.monotonic() - start
        _logger.info("Dictionary build complete", extra={"elapsed_seconds": elapsed})

        return product_dict

    async def _fetch_all_data(
        self
    ) -> tuple[list[SkuInfo], list[SpuInfo], list[dict]]:
        """
        并行拉取所有数据（SKU/SPU/别名）

        Returns:
            (sku列表, spu列表, 别名列表)
        """
        # 并发查询：使用 gather 并行执行
        skus_task = self._repo.get_all_sku()
        spus_task = self._repo.get_all_spu()
        aliases_task = self._repo.get_all_aliases()

        skus, spus, aliases = await asyncio.gather(skus_task, spus_task, aliases_task)

        return skus, spus, aliases

    def _build_dicts(
        self,
        skus: list[SkuInfo],
        spus: list[SpuInfo],
        aliases: list[dict]
    ) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
        """
        构建三个字典

        逻辑：
        1. sku_dict: bar_code -> sku_id (必须存在)
        2. spu_dict: name/keyword -> spu_id
        3. alias_dict: alias -> spu_id

        Returns:
            (sku_dict, spu_dict, alias_dict)
        """
        # ---- SKU 字典 ----
        sku_dict: dict[str, int] = {}
        for sku in skus:
            if sku.bar_code:
                norm = normalize_text(sku.bar_code)
                if norm:  # 非空才插入
                    sku_dict[norm] = sku.id

        # ---- SPU 字典 ----
        spu_dict: dict[str, int] = {}
        for spu in spus:
            # 名称
            if spu.name:
                norm = normalize_text(spu.name)
                if norm:
                    spu_dict[norm] = spu.id

            # 关键词
            if spu.keyword:
                # keyword 可能是逗号分隔的多个词
                keywords = [kw.strip() for kw in spu.keyword.split(",")]
                for kw in keywords:
                    norm_kw = normalize_text(kw)
                    if norm_kw and norm_kw not in spu_dict:
                        spu_dict[norm_kw] = spu.id

        # ---- Alias 字典 ----
        alias_dict: dict[str, int] = {}
        seen: dict[str, int] = {}  # 用于去重

        for alias in aliases:
            name = alias.get("alias_name", "")
            spu_id = alias.get("spu_id")

            if not name or not spu_id:
                continue

            norm = normalize_text(name, for_alias=True)
            if not norm:
                continue

            # 去重：同名别名取第一个（或者可以合并为列表，但解析时需处理）
            if norm not in seen:
                alias_dict[norm] = spu_id
                seen[norm] = spu_id

        return sku_dict, spu_dict, alias_dict


# ============== 持久化工具 ==============

def save_dict(
    product_dict: ProductDict,
    path: str | Path,
    indent: int = 2,
    ensure_ascii: bool = False,
) -> None:
    """
    保存字典到JSON文件

    Args:
        product_dict: 字典对象
        path: 文件路径
        indent: JSON缩进（生产环境可以设为0减小体积）
        ensure_ascii: 是否转义非ASCII字符
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(product_dict, f, ensure_ascii=ensure_ascii, indent=indent)

    _logger.info("Dictionary saved", extra={"path": str(path), "size_bytes": path.stat().st_size})


def load_dict(path: str | Path) -> ProductDict:
    """
    从JSON文件加载字典

    Args:
        path: 文件路径

    Returns:
        字典对象

    Raises:
        FileNotFoundError: 文件不存在
        json.JSONDecodeError: JSON格式错误
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    _logger.info("Dictionary loaded", extra={"path": str(path)})
    return data


# ============== 便捷函数 ==============

async def build_product_dict(
    pool: aiomysql.Pool,
    output_path: str | Path = "data/product_dict.json",
    fuzzy_threshold: int = 80,
    save: bool = True,
) -> ProductDict:
    """
    便捷函数：构建并保存商品字典（单次ETL入口）

    Args:
        pool: 数据库连接池
        output_path: 输出JSON文件路径
        fuzzy_threshold: 模糊匹配阈值
        save: 是否保存到文件

    Returns:
        构建完成的字典对象
    """
    repo = ProductRepository(pool)
    builder = ProductDictBuilder(repo, fuzzy_threshold=fuzzy_threshold)

    product_dict = await builder.build()

    if save:
        save_dict(product_dict, output_path)

    return product_dict
