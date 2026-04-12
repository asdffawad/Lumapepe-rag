"""
增量摄取 CLI 脚本
提供命令行接口调用 incremental_ingest_core 的增量摄取功能

用法示例:
    python scripts/incremental_ingest.py                     # 执行增量摄取
    python scripts/incremental_ingest.py --dry-run           # 预览变更
    python scripts/incremental_ingest.py --no-delete         # 不自动删除缺失文件
    python scripts/incremental_ingest.py --data-dir ./data   # 指定数据目录
"""
from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# ============================================================================
# 路径设置（支持从 scripts/ 目录运行）
# ============================================================================
# 获取脚本所在目录
SCRIPT_DIR = Path(__file__).parent.resolve()
# BACKEND_DIR = f:\Myprogram\cursor\work\RAG\backend
BACKEND_DIR = SCRIPT_DIR.parent / "backend"
# PROJECT_DIR = f:\Myprogram\cursor\work\RAG
PROJECT_DIR = BACKEND_DIR.parent

# 添加到 sys.path 以便导入 app 模块
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(PROJECT_DIR))

# ============================================================================
# 导入核心模块
# ============================================================================
from app.services.incremental_ingest_core import run_incremental_ingestion

# ============================================================================
# 依赖库导入
# ============================================================================
import aiomysql
from pymilvus import connections, Collection


# ============================================================================
# 配置构建函数
# ============================================================================

@dataclass
class MySQLConfig:
    """MySQL 连接配置"""
    host: str
    port: int
    user: str
    password: str
    database: str
    pool_size: int = 5
    pool_recycle: int = 3600


@dataclass
class MilvusConfig:
    """Milvus 连接配置"""
    host: str
    port: int
    collection_name: str = "knowledge_base"
    alias: str = "default"


def build_mysql_config() -> MySQLConfig:
    """
    从环境变量构建 MySQL 配置
    
    环境变量:
        MYSQL_HOST: 数据库主机（默认 127.0.0.1）
        MYSQL_PORT: 端口（默认 3306）
        MYSQL_USER: 用户名（默认 root）
        MYSQL_PASSWORD: 密码（默认空）
        MYSQL_DATABASE: 数据库名（默认 knowledge_rag）
    
    Returns:
        MySQLConfig 配置对象
    """
    return MySQLConfig(
        host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ.get("MYSQL_USER", "root"),
        password=os.environ.get("MYSQL_PASSWORD", ""),
        database=os.environ.get("MYSQL_DATABASE", "knowledge_rag"),
        pool_size=int(os.environ.get("MYSQL_POOL_SIZE", "5")),
        pool_recycle=int(os.environ.get("MYSQL_POOL_RECYCLE", "3600")),
    )


def build_milvus_config() -> MilvusConfig:
    """
    从环境变量构建 Milvus 配置
    
    环境变量:
        MILVUS_HOST: Milvus 主机（默认 127.0.0.1）
        MILVUS_PORT: 端口（默认 19530）
    
    Returns:
        MilvusConfig 配置对象
    """
    return MilvusConfig(
        host=os.environ.get("MILVUS_HOST", "127.0.0.1"),
        port=int(os.environ.get("MILVUS_PORT", "19530")),
        collection_name=os.environ.get("MILVUS_COLLECTION", "knowledge_base"),
        alias="default",
    )


def build_ingest_config(data_dir_override: Path | None = None) -> dict:
    """
    从环境变量构建增量摄取配置
    
    环境变量:
        DATA_DIR: 数据目录（默认 PROJECT_DIR / "data"）
        CHUNK_SIZE: 分块大小（默认 512）
        CHUNK_OVERLAP: 重叠大小（默认 50）
        INGEST_BATCH_SIZE: 批处理大小（默认 10）
        DASHSCOPE_API_KEY: DashScope API 密钥
        LLM_BASE_URL: LLM API 地址
    
    Args:
        data_dir_override: CLI 指定的 data_dir，覆盖环境变量
    
    Returns:
        配置字典，供 run_incremental_ingestion 使用
    """
    return {
        "data_dir": str(data_dir_override) if data_dir_override else os.environ.get(
            "DATA_DIR", str(PROJECT_DIR / "data")
        ),
        "chunk_size": int(os.environ.get("CHUNK_SIZE", "512")),
        "overlap": int(os.environ.get("CHUNK_OVERLAP", "50")),
        "batch_size": int(os.environ.get("INGEST_BATCH_SIZE", "10")),
        "dashscope_api_key": os.environ.get("DASHSCOPE_API_KEY", ""),
        "llm_base_url": os.environ.get("LLM_BASE_URL", "https://dashscope.aliyuncs.com"),
    }


# ============================================================================
# CLI 参数解析
# ============================================================================

def parse_args():
    """
    解析命令行参数
    
    Returns:
        tuple: (data_dir_override, dry_run, delete_missing)
    """
    import argparse
    
    parser = argparse.ArgumentParser(
        description="增量摄取 CLI - 执行增量文档摄取到向量数据库",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/incremental_ingest.py                     # 执行增量摄取
  python scripts/incremental_ingest.py --dry-run           # 预览模式
  python scripts/incremental_ingest.py --no-delete         # 跳过删除
  python scripts/incremental_ingest.py --data-dir ./mydata  # 自定义数据目录
        """
    )
    
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="预览变更，不实际写入数据库"
    )
    
    parser.add_argument(
        "--no-delete",
        action="store_true",
        help="跳过自动删除缺失文件"
    )
    
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="覆盖数据目录路径（默认从 DATA_DIR 环境变量或 project/data 获取）"
    )
    
    args = parser.parse_args()
    
    # 转换 data_dir
    data_dir = Path(args.data_dir) if args.data_dir else None
    
    return data_dir, args.dry_run, not args.no_delete


# ============================================================================
# 结果输出格式化
# ============================================================================

def print_result(result) -> None:
    """
    打印增量摄取结果
    
    Args:
        result: IncrementalIngestResult 结果对象
    """
    # 计算总处理数
    total = result.added + result.updated + result.unchanged + result.deleted + result.errors
    
    print()
    print("========== 增量摄取结果 ==========")
    print(f"  新增: {result.added}")
    print(f"  更新: {result.updated}")
    print(f"  无变化: {result.unchanged}")
    print(f"  删除: {result.deleted}")
    print(f"  错误: {result.errors}")
    print()
    print("  详细清单:")
    
    # 按操作类型排序输出
    if not result.details:
        print("    (无)")
    else:
        for detail in result.details:
            source_path = detail.get("source_path", "unknown")
            action = detail.get("action", "unknown")
            message = detail.get("message", "")
            doc_id = detail.get("doc_id", None)
            
            if action == "created":
                chunks = detail.get("chunks", "")
                print(f"    [CREATED] {source_path}")
            elif action == "updated":
                print(f"    [UPDATED] {source_path}")
            elif action == "unchanged":
                print(f"    [UNCHANGED] {source_path}")
            elif action == "deleted":
                print(f"    [DELETED] {source_path}")
            elif action == "error":
                print(f"    [ERROR] {source_path}: {message}")
            else:
                print(f"    [{action.upper()}] {source_path}")
    
    print()
    print(f"总计处理: {total} 项")


# ============================================================================
# Milvus 连接管理
# ============================================================================

def connect_milvus(config: MilvusConfig) -> Collection | None:
    """
    连接到 Milvus 并返回 Collection 对象
    
    Args:
        config: MilvusConfig 配置对象
    
    Returns:
        Collection 对象，连接失败返回 None
    """
    try:
        # 连接到 Milvus
        connections.connect(
            alias=config.alias,
            host=config.host,
            port=config.port,
        )
        
        # 获取 Collection
        collection = Collection(config.collection_name)
        collection.load()
        
        print(f"[INFO] 已连接到 Milvus: {config.host}:{config.port}/{config.collection_name}")
        return collection
        
    except Exception as e:
        print(f"[WARN] Milvus 连接失败: {e}")
        print("[INFO] 将跳过 Milvus 操作，仅更新 MySQL")
        return None


def disconnect_milvus(config: MilvusConfig) -> None:
    """
    断开 Milvus 连接
    
    Args:
        config: MilvusConfig 配置对象
    """
    try:
        connections.disconnect(alias=config.alias)
    except Exception as e:
        print(f"[WARN] Milvus 断开连接失败: {e}")


# ============================================================================
# 主函数
# ============================================================================

async def main() -> None:
    """CLI 主函数"""
    # -------------------------------------------------------------------------
    # 1. 解析 CLI 参数
    # -------------------------------------------------------------------------
    data_dir_override, dry_run, delete_missing = parse_args()
    
    if dry_run:
        print("[INFO] 模式: DRY-RUN (预览模式，不实际写入)")
    else:
        print("[INFO] 模式: 正式执行")
    
    if not delete_missing:
        print("[INFO] 删除模式: 跳过自动删除缺失文件")
    
    # -------------------------------------------------------------------------
    # 2. 构建配置
    # -------------------------------------------------------------------------
    mysql_config = build_mysql_config()
    milvus_config = build_milvus_config()
    ingest_config = build_ingest_config(data_dir_override)
    
    print(f"[INFO] 数据目录: {ingest_config['data_dir']}")
    print(f"[INFO] MySQL: {mysql_config.host}:{mysql_config.port}/{mysql_config.database}")
    print(f"[INFO] Milvus: {milvus_config.host}:{milvus_config.port}")
    
    # -------------------------------------------------------------------------
    # 3. 创建 MySQL 连接池
    # -------------------------------------------------------------------------
    mysql_pool = None
    milvus_collection = None
    
    try:
        print("[INFO] 正在创建 MySQL 连接池...")
        mysql_pool = await aiomysql.create_pool(
            host=mysql_config.host,
            port=mysql_config.port,
            user=mysql_config.user,
            password=mysql_config.password,
            db=mysql_config.database,
            minsize=1,
            maxsize=mysql_config.pool_size,
            pool_recycle=mysql_config.pool_recycle,
            autocommit=True,
            charset="utf8mb4",
        )
        print("[INFO] MySQL 连接池创建成功")
        
        # ---------------------------------------------------------------------
        # 4. 连接 Milvus
        # ---------------------------------------------------------------------
        milvus_collection = connect_milvus(milvus_config)
        
        # ---------------------------------------------------------------------
        # 5. 调用核心函数
        # ---------------------------------------------------------------------
        print("[INFO] 开始执行增量摄取...")
        result = await run_incremental_ingestion(
            mysql_pool=mysql_pool,
            milvus_collection=milvus_collection,
            config=ingest_config,
            dry_run=dry_run,
            delete_missing=delete_missing,
        )
        
        # ---------------------------------------------------------------------
        # 6. 打印结果
        # ---------------------------------------------------------------------
        print_result(result)
        
    except Exception as e:
        print(f"[FATAL] CLI 执行失败: {e}")
        raise
        
    finally:
        # ---------------------------------------------------------------------
        # 7. 清理连接
        # ---------------------------------------------------------------------
        if mysql_pool is not None:
            try:
                mysql_pool.close()
                await asyncio.sleep(0.5)  # 等待连接池关闭
                mysql_pool.wait_closed()
                print("[INFO] MySQL 连接池已关闭")
            except Exception as e:
                print(f"[WARN] MySQL 连接池关闭失败: {e}")
        
        disconnect_milvus(milvus_config)


# ============================================================================
# 入口点
# ============================================================================

if __name__ == "__main__":
    asyncio.run(main())
