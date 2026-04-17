"""
商品字典ETL构建脚本

用法：
    python -m app.etl.etl_build_dict [--output OUTPUT] [--force]
    python -m app.etl.etl_build_dict --help

功能：
    - 从MySQL全量拉取商品数据（SPU/SKU/别名）
    - 构建三字典结构（sku_dict / spu_dict / alias_dict）
    - 保存为JSON文件（供内存加载使用）
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import aiomysql

from app.config import get_config
from app.database.product import ProductRepository
from app.etl.dict_builder import build_product_dict, save_dict, ProductDict
from app.etl.dict_loader import ProductDictLoader
from app.logging_config import get_logger

_logger = get_logger("etl.main")


async def main_async(args):
    """主异步入口"""
    config = get_config()

    # 创建数据库连接池
    _logger.info("Connecting to MySQL", extra={"host": config.db.host})
    pool = await aiomysql.create_pool(
        host=config.db.host,
        port=config.db.port,
        user=config.db.user,
        password=config.db.password,
        db=config.db.database,
        minsize=2,
        maxsize=10,
        autocommit=True,
        pool_recycle=3600,
        charset="utf8mb4",
    )

    try:
        # 创建Repository
        repo = ProductRepository(pool)

        # 构建字典
        _logger.info("Building product dictionary...")
        product_dict: ProductDict = await build_product_dict(
            repo,
            output_path=args.output,
            fuzzy_threshold=args.threshold,
            save=False,  # 我们手动保存
        )

        # 保存到文件
        output_path = Path(args.output)
        save_dict(product_dict, output_path, indent=2 if args.pretty else 0)

        _logger.info(
            "Product dictionary built successfully",
            extra={
                "output": str(output_path),
                "version": product_dict["version"],
                "stats": product_dict["stats"],
            }
        )

        # 验证字典加载
        if args.validate:
            _logger.info("Validating dictionary...")
            loader = ProductDictLoader(output_path)
            loaded_dict = await loader.load()
            _logger.info(
                "Dictionary validated",
                extra={
                    "sku_keys": len(loaded_dict.get("sku_dict", {})),
                    "spu_keys": len(loaded_dict.get("spu_dict", {})),
                    "alias_keys": len(loaded_dict.get("alias_dict", {})),
                }
            )

        return 0

    except Exception as e:
        _logger.error("Failed to build dictionary", extra={"error": str(e)}, exc_info=True)
        return 1
    finally:
        pool.close()
        await pool.wait_closed()


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="Build product dictionary for RAG system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                                    # 构建到默认路径 data/product_dict.json
  %(prog)s --output ./data/my_dict.json      # 指定输出路径
  %(prog)s --force --pretty                  # 强制覆盖，美化输出
  %(prog)s --validate                        # 构建后验证字典可加载性
        """,
    )

    parser.add_argument(
        "-o", "--output",
        type=str,
        default="data/product_dict.json",
        help="输出JSON文件路径（默认: data/product_dict.json）"
    )
    parser.add_argument(
        "-t", "--threshold",
        type=int,
        default=80,
        help="模糊匹配阈值（默认: 80）"
    )
    parser.add_argument(
        "-f", "--force",
        action="store_true",
        default=False,
        help="强制覆盖已存在的文件"
    )
    parser.add_argument(
        "-p", "--pretty",
        action="store_true",
        default=False,
        help="美化JSON输出（缩进2空格，生产环境建议关闭以减小体积）"
    )
    parser.add_argument(
        "-v", "--validate",
        action="store_true",
        default=False,
        help="构建后验证字典可加载性"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="仅显示统计信息，不保存文件"
    )

    args = parser.parse_args()

    # 检查输出路径是否已存在
    output_path = Path(args.output)
    if output_path.exists() and not args.force:
        print(f"Error: Output file already exists: {output_path}", file=sys.stderr)
        print("Use --force to overwrite", file=sys.stderr)
        return 1

    # 运行异步主函数
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
