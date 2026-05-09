"""
Celery 增量摄取任务
每日凌晨执行增量扫描 + 可选删除检测
"""
import asyncio
import os
from celery_app import celery_app
from pathlib import Path

# Compute paths for sys.path adjustment
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent   # project root


@celery_app.task(name="celery_app.tasks.incremental_ingest.daily_incremental_ingest")
def daily_incremental_ingest(dry_run: bool = False, delete_missing: bool = True) -> dict:
    """
    每日凌晨执行增量摄取。

    参数:
        dry_run: True=仅预览不执行，默认 False
        delete_missing: True=自动删除缺失文件，默认 True
    """
    import sys
    # Adjust sys.path so we can import app modules
    backend_path = str(PROJECT_DIR / "backend")
    project_path = str(PROJECT_DIR)
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)
    if project_path not in sys.path:
        sys.path.insert(0, project_path)

    import aiomysql
    from pymilvus import Collection, connections, utility

    mysql_config = {
        "host": os.getenv("DB_HOST") or os.getenv("MYSQL_HOST", ""),
        "port": int(os.getenv("DB_PORT") or os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("DB_USER") or os.getenv("MYSQL_USER", ""),
        "password": os.getenv("DB_PASSWORD") or os.getenv("MYSQL_PASSWORD", ""),
        "database": os.getenv("DB_NAME") or os.getenv("MYSQL_DATABASE", "lumapepe"),
    }
    milvus_config = {
        "host": os.getenv("MILVUS_HOST", "localhost"),
        "port": int(os.getenv("MILVUS_PORT", "19530")),
    }
    ingest_config = {
        "data_dir": os.getenv("DATA_DIR", str(PROJECT_DIR / "data")),
        "chunk_size": int(os.getenv("CHUNK_SIZE", "512")),
        "chunk_overlap": int(os.getenv("CHUNK_OVERLAP", "50")),
        "batch_size": int(os.getenv("INGEST_BATCH_SIZE", "10")),
        "dashscope_api_key": os.getenv("DASHSCOPE_API_KEY", ""),
        "llm_base_url": os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com"),
    }

    async def _run():
        pool = await aiomysql.create_pool(
            host=mysql_config["host"],
            port=mysql_config["port"],
            user=mysql_config["user"],
            password=mysql_config["password"],
            db=mysql_config["database"],
            charset="utf8mb4",
            minsize=1, maxsize=5,
            autocommit=True,
        )
        alias = "default"
        connections.connect(
            alias=alias, 
            host=milvus_config["host"], 
            port=milvus_config["port"],
            channel_kwargs={"grpc.max_receive_message_length": 32*1024*1024}
        )
        collection_name = "knowledge_base"
        milvus_collection = Collection(collection_name) if utility.has_collection(collection_name) else None

        try:
            from app.services.incremental_ingest_core import run_incremental_ingestion
            result = await run_incremental_ingestion(
                pool, milvus_collection, ingest_config,
                dry_run=dry_run, delete_missing=delete_missing
            )
            return {
                "added": result.added,
                "updated": result.updated,
                "unchanged": result.unchanged,
                "previewed": result.previewed,
                "deleted": result.deleted,
                "errors": result.errors,
                "details_count": len(result.details),
            }
        finally:
            pool.close()
            await pool.wait_closed()
            connections.disconnect(alias=alias)

    return asyncio.run(_run())
