"""
管理接口
手动触发知识库摄取、文档增删改查

认证说明：
- 所有管理接口需要 admin 角色认证
- 支持 JWT Bearer Token (Authorization: Bearer <token>)
- 支持 API Key (X-API-Key: <key>)

摄取 API 使用示例：
    # 触发全量摄取（增量模式，跳过已索引文档）
    curl -X POST http://localhost:8000/api/v1/admin/ingest \\
      -H "Authorization: Bearer <admin-token>" \\
      -H "Content-Type: application/json"

    # 强制重建（删除现有数据重新摄取）
    curl -X POST "http://localhost:8000/api/v1/admin/ingest?reset=true" \\
      -H "Authorization: Bearer <admin-token>"

查看文档列表示例：
    curl http://localhost:8000/api/v1/admin/documents \\
      -H "Authorization: Bearer <admin-token>"
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from app.models.request import DocumentCreateRequest, DocumentUpdateRequest

# 路径计算：动态检测脚本目录位置
# Docker 中 working_dir=/app/backend，__file__ → /app/backend/app/api/admin.py → /app/
# 本地中 working_dir=backend/，    __file__ → backend/app/api/admin.py → backend/
# 向上找到最上层包含 scripts/ 子目录的目录即为项目根目录
def _find_project_root(start: Path) -> Path:
    """向上搜索包含 scripts/ 子目录的最顶层目录作为项目根目录。

    在绑定挂载场景下 /app/backend 可能通过 symlink 指向 /app，导致 Path.resolve()
    将 /app/backend/app/api/admin.py 解析为 /app/app/api/admin.py。
    因此先用固定级数计算（admin.py → app/api → backend → 项目根，共 3 级），
    再用 scripts/ 存在性验证 fallback。
    """
    p = start.resolve()
    # admin.py 在 app/api/，项目根 = admin.py 的父.parent.parent
    root = p.parent.parent.parent
    # 验证 root 下是否有 scripts/（直接目录或 symlink 均通过 is_dir() 检查）
    if (root / "scripts").is_dir():
        return root
    # fallback：向上搜索最近的 scripts/ 所在目录
    for _ in range(10):
        if (p / "scripts").is_dir():
            return p
        if p.parent == p:
            break
        p = p.parent
    return start

_PROJECT_ROOT = _find_project_root(Path(__file__).resolve().parent)
SCRIPT_DIR = _PROJECT_ROOT / "scripts"
_BACKEND_DIR = _PROJECT_ROOT / "backend"


router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.post("/ingest")
async def trigger_ingest(reset: bool = False):
    script_path = SCRIPT_DIR / "ingest_data.py"

    cmd = [sys.executable, str(script_path)]
    if reset:
        cmd.append("--reset")

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{_PROJECT_ROOT}:{_BACKEND_DIR}"

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(_BACKEND_DIR),  # ingest_data.py 从 backend/ 目录运行，data/ 相对路径才正确
            env=env,
        )
        # // #region agent log
        print(f"[AGENT_DEBUG] subprocess returncode={result.returncode} stdout_len={len(result.stdout)} stderr_len={len(result.stderr)}", flush=True)
        if result.returncode != 0:
            print(f"[AGENT_DEBUG] stderr: {result.stderr[-500:]}", flush=True)
        # // #endregion

        output = result.stdout + result.stderr

        if result.returncode == 0:
            return {
                "status": "success",
                "message": "知识库摄取完成",
                "output": output[-2000:],  # 截取最后 2000 字符
            }
        else:
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "INGEST_ERROR",
                    "message": f"摄取失败 (exit {result.returncode})",
                    "output": output[-2000:],
                },
            )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail={"code": "INGEST_TIMEOUT", "message": "摄取超时（5分钟）"})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"code": "INGEST_ERROR", "message": str(e)})


@router.get("/documents")
async def list_documents():
    """
    查看所有已摄取的文档状态
    """
    from app.database.connection import get_db_pool

    pool = get_db_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail={"code": "DB_UNAVAILABLE", "message": "MySQL 未连接"})

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, doc_type, title, chunk_count, status, created_at, indexed_at "
                "FROM documents WHERE status != 'deleted' ORDER BY created_at DESC LIMIT 100"
            )
            rows = await cur.fetchall()
            columns = [col[0] for col in cur.description]

    docs = [dict(zip(columns, row)) for row in rows]

    # 统计
    total = len(docs)
    done = sum(1 for d in docs if d["status"] == "done")
    failed = sum(1 for d in docs if d["status"] == "failed")
    pending = sum(1 for d in docs if d["status"] in ("pending", "processing"))

    return {
        "total": total,
        "done": done,
        "failed": failed,
        "pending": pending,
        "documents": docs,
    }


@router.get("/documents/{doc_id}")
async def get_document(doc_id: int):
    """查看指定文档详情"""
    from app.database.connection import get_db_pool

    pool = get_db_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail={"code": "DB_UNAVAILABLE", "message": "MySQL 未连接"})

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, doc_type, title, file_hash, chunk_count, status, created_at, indexed_at "
                "FROM documents WHERE id = %s",
                (doc_id,),
            )
            row = await cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": f"文档 {doc_id} 不存在"})
            columns = [col[0] for col in cur.description]

    return dict(zip(columns, row))


@router.post("/documents/{doc_id}/reindex")
async def reindex_document(doc_id: int):
    """
    重新索引指定文档：
    1. 从 Milvus 删除该 doc_id 的所有向量 chunk
    2. 重新分块、向量化、入库
    3. 更新 MySQL documents 表状态
    """
    from app.database.connection import get_db_pool
    from app.utils.chunker import TextChunker
    from app.vectorstore.milvus_client import MilvusClient
    from app.services.embedding_service import EmbeddingService

    pool = get_db_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail={"code": "DB_UNAVAILABLE", "message": "MySQL 未连接"})

    # 1. 查询原文档内容
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM documents WHERE id = %s",
                (doc_id,),
            )
            row = await cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": f"文档 {doc_id} 不存在"})
            columns = [col[0] for col in cur.description]
            doc = dict(zip(columns, row))
            title = doc["title"]
            doc_type = doc["doc_type"]

    # 2. 获取 Milvus client
    from app.main import app as _app
    milvus: MilvusClient = getattr(_app.state, "milvus_client", None)
    if milvus is None or not await milvus.async_health_check():
        raise HTTPException(status_code=503, detail={"code": "MILVUS_UNAVAILABLE", "message": "Milvus 未连接"})

    # 3. 删除旧向量
    deleted = await milvus.delete_by_doc_id(doc_id)

    # 4. 重新摄取：从 documents 表中取出 content（存储在 JSON 字段中）
    #    MySQL JSON 字段返回 str，需要解析
    import json as _json
    content_raw_val = doc.get("content_raw")
    if isinstance(content_raw_val, str):
        content_raw: dict[str, Any] | None = _json.loads(content_raw_val)
    elif isinstance(content_raw_val, dict):
        content_raw = content_raw_val
    else:
        content_raw = None
    if not content_raw:
        content_raw = {"title": title, **doc}

    chunker = TextChunker(chunk_size=512, overlap=64)
    chunks = chunker.chunk_structured(content_raw, doc_id, str(doc.get("file_path") or doc_id), doc_type)

    # 5. 向量化并入库
    embed_service = EmbeddingService.get_instance()
    texts = [c.content for c in chunks]
    vectors = await embed_service.embed(texts)

    ids = [f"{c.doc_id}_{c.chunk_index}" for c in chunks]
    await milvus.insert(
        vectors=vectors,
        contents=texts,
        metadata=[{} for _ in chunks],
        doc_id=doc_id,
        source=str(doc.get("file_path") or doc_id),
        doc_type=doc_type,
    )

    # 6. 更新 MySQL 状态
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE documents SET status = 'done', indexed_at = NOW(), chunk_count = %s WHERE id = %s",
                (len(chunks), doc_id),
            )

    return {
        "status": "success",
        "doc_id": doc_id,
        "title": title,
        "deleted_chunks": deleted,
        "new_chunks": len(chunks),
    }


@router.post("/documents")
async def create_document(request: DocumentCreateRequest):
    """
    创建新文档并自动摄取到向量库。

    支持的 doc_type：product / policy / faq / manual
    """
    from app.database.connection import get_db_pool
    from app.utils.chunker import TextChunker
    from app.vectorstore.milvus_client import MilvusClient
    from app.services.embedding_service import EmbeddingService

    pool = get_db_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail={"code": "DB_UNAVAILABLE", "message": "MySQL 未连接"})

    # 1. 计算 content hash 用于去重
    content_str = json.dumps(request.content, ensure_ascii=False, sort_keys=True)
    file_hash = hashlib.sha256(content_str.encode()).hexdigest()

    # 2. 检查是否已存在
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, status FROM documents WHERE file_hash = %s",
                (file_hash,),
            )
            existing = await cur.fetchone()
            if existing:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "DUPLICATE", "message": f"文档已存在 (id={existing[0]})，请使用 PUT 更新"},
                )

    # 3. 获取 Milvus client
    from app.main import app as _app
    milvus: MilvusClient = getattr(_app.state, "milvus_client", None)
    if milvus is None or not await milvus.async_health_check():
        raise HTTPException(status_code=503, detail={"code": "MILVUS_UNAVAILABLE", "message": "Milvus 未连接"})

    # 4. 写入 MySQL（pending 状态）
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO documents (title, doc_type, file_hash, status, content_raw) "
                "VALUES (%s, %s, %s, 'processing', %s)",
                (request.title, request.doc_type, file_hash, json.dumps(request.content, ensure_ascii=False)),
            )
            doc_db_id = cur.lastrowid

    # 5. 分块 + 向量化 + 入库
    chunker = TextChunker(chunk_size=512, overlap=64)
    chunks = chunker.chunk_structured(request.content, doc_db_id, request.source, request.doc_type)
    texts = [c.content for c in chunks]

    embed_service = EmbeddingService.get_instance()
    vectors = await embed_service.embed(texts)

    await milvus.insert(
        vectors=vectors,
        contents=texts,
        metadata=[{} for _ in chunks],
        doc_id=doc_db_id,
        source=request.source,
        doc_type=request.doc_type,
    )

    # 6. 更新 MySQL 状态
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE documents SET status = 'done', indexed_at = NOW(), chunk_count = %s WHERE id = %s",
                (len(chunks), doc_db_id),
            )

    return {
        "status": "success",
        "doc_id": doc_db_id,
        "title": request.title,
        "doc_type": request.doc_type,
        "chunks": len(chunks),
    }


@router.put("/documents/{doc_id}")
async def update_document(doc_id: int, request: DocumentUpdateRequest):
    """
    更新文档内容并自动重索引。

    等效于：删除旧向量 + 插入新向量 + 更新 MySQL 元数据
    """
    from app.database.connection import get_db_pool
    from app.utils.chunker import TextChunker
    from app.vectorstore.milvus_client import MilvusClient
    from app.services.embedding_service import EmbeddingService

    pool = get_db_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail={"code": "DB_UNAVAILABLE", "message": "MySQL 未连接"})

    # 1. 查询原文档
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM documents WHERE id = %s", (doc_id,))
            row = await cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": f"文档 {doc_id} 不存在"})
            columns = [col[0] for col in cur.description]
            doc = dict(zip(columns, row))
            import json as _json
            cv = doc.get("content_raw")
            if isinstance(cv, str):
                doc["content_raw"] = _json.loads(cv)

    # 2. 获取 Milvus client
    from app.main import app as _app
    milvus: MilvusClient = getattr(_app.state, "milvus_client", None)
    if milvus is None or not await milvus.async_health_check():
        raise HTTPException(status_code=503, detail={"code": "MILVUS_UNAVAILABLE", "message": "Milvus 未连接"})

    # 3. 删除旧向量
    deleted = await milvus.delete_by_doc_id(doc_id)

    # 4. 合并新旧内容
    new_content = request.content or {}
    if request.title:
        new_content.setdefault("title", request.title)
    if not new_content.get("title"):
        new_content.setdefault("title", doc.get("title", ""))

    # 5. 重新分块向量化
    chunker = TextChunker(chunk_size=512, overlap=64)
    chunks = chunker.chunk_structured(
        new_content,
        doc_id,
        str(doc.get("file_path") or doc_id),
        doc.get("doc_type", "faq"),
    )
    texts = [c.content for c in chunks]

    embed_service = EmbeddingService.get_instance()
    vectors = await embed_service.embed(texts)

    await milvus.insert(
        vectors=vectors,
        contents=texts,
        metadata=[{} for _ in chunks],
        doc_id=doc_id,
        source=str(doc.get("file_path") or doc_id),
        doc_type=doc.get("doc_type", "faq"),
    )

    # 6. 更新 MySQL
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            new_hash = hashlib.sha256(json.dumps(new_content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            if request.title:
                await cur.execute(
                    "UPDATE documents SET title = %s, file_hash = %s, status = 'done', "
                    "indexed_at = NOW(), chunk_count = %s, content_raw = %s WHERE id = %s",
                    (request.title, new_hash, len(chunks), json.dumps(new_content, ensure_ascii=False), doc_id),
                )
            else:
                await cur.execute(
                    "UPDATE documents SET file_hash = %s, status = 'done', "
                    "indexed_at = NOW(), chunk_count = %s, content_raw = %s WHERE id = %s",
                    (new_hash, len(chunks), json.dumps(new_content, ensure_ascii=False), doc_id),
                )

    return {
        "status": "success",
        "doc_id": doc_id,
        "deleted_chunks": deleted,
        "new_chunks": len(chunks),
    }


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: int):
    """
    删除指定文档：删除 Milvus 向量 + MySQL 记录（软删除）。
    删除后可通过重新 POST 创建同名文档。
    """
    from app.database.connection import get_db_pool
    from app.vectorstore.milvus_client import MilvusClient

    pool = get_db_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail={"code": "DB_UNAVAILABLE", "message": "MySQL 未连接"})

    # 1. 检查文档是否存在
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id, title, status FROM documents WHERE id = %s", (doc_id,))
            row = await cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": f"文档 {doc_id} 不存在"})

    # 2. 删除 Milvus 向量
    from app.main import app as _app
    milvus: MilvusClient = getattr(_app.state, "milvus_client", None)
    deleted = 0
    if milvus and milvus.health_check():
        deleted = await milvus.delete_by_doc_id(doc_id)

    # 3. 软删除 MySQL 记录
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE documents SET status = 'deleted' WHERE id = %s",
                (doc_id,),
            )

    return {
        "status": "success",
        "doc_id": doc_id,
        "deleted_chunks": deleted,
        "message": "文档已删除",
    }


@router.post("/ingest/incremental")
async def trigger_incremental_ingest(dry_run: bool = False, delete_missing: bool = True):
    """
    增量摄取 API：
    - 扫描 data/ 目录，对比 MySQL records
    - 自动处理新增、更新、删除（删除可关闭）
    - 支持 dry_run 预览模式

    认证：同其他 admin 接口（JWT Bearer 或 API Key）

    返回示例：
    {
        "status": "success",
        "dry_run": false,
        "summary": {"added": 2, "updated": 1, "unchanged": 10, "deleted": 0, "errors": 0},
        "details": [
            {"source_path": "products.json::SKU001", "action": "updated", "doc_id": 5, "title": "...", "chunks": 8}
        ]
    }
    """
    import os
    import asyncio
    from pathlib import Path

    from pymilvus import Collection, connections, utility

    # 计算 data_dir（项目根目录下的 data/ 文件夹）
    data_dir = os.getenv("DATA_DIR", None)
    if data_dir is None:
        # __file__ = backend/app/api/admin.py
        # parent.parent.parent.parent = 项目根目录
        data_dir = str(Path(__file__).resolve().parent.parent.parent.parent / "data")

    mysql_config = {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "database": os.getenv("MYSQL_DATABASE", "rag_customer_service"),
    }
    milvus_config = {
        "host": os.getenv("MILVUS_HOST", "localhost"),
        "port": int(os.getenv("MILVUS_PORT", "19530")),
    }
    ingest_config = {
        "data_dir": data_dir,
        "chunk_size": int(os.getenv("CHUNK_SIZE", "512")),
        "chunk_overlap": int(os.getenv("CHUNK_OVERLAP", "50")),
        "batch_size": int(os.getenv("INGEST_BATCH_SIZE", "10")),
        "dashscope_api_key": os.getenv("DASHSCOPE_API_KEY", ""),
        "llm_base_url": os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com"),
    }

    import aiomysql
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
    # pymilvus connections.connect 是同步的，在线程池中运行避免阻塞事件循环
    await asyncio.to_thread(
        connections.connect,
        alias=alias,
        host=milvus_config["host"],
        port=milvus_config["port"],
        channel_kwargs={"grpc.max_receive_message_length": 32*1024*1024}
    )
    # 确保 Milvus collection 已加载或创建
    milvus_collection = None
    if utility.has_collection("knowledge_base"):
        milvus_collection = Collection("knowledge_base")
        milvus_collection.load()
    else:
        # 创建 collection
        from pymilvus import CollectionSchema, DataType, FieldSchema
        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=128, is_primary=True),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=1024),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=4096),
            FieldSchema(name="doc_id", dtype=DataType.INT64),
            FieldSchema(name="chunk_index", dtype=DataType.INT64),
            FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="doc_type", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="section_type", dtype=DataType.VARCHAR, max_length=32),
            FieldSchema(name="markets", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="lang", dtype=DataType.VARCHAR, max_length=8),
            FieldSchema(name="hierarchy", dtype=DataType.VARCHAR, max_length=256),
        ]
        schema = CollectionSchema(fields=fields, description="RAG Knowledge Base - v2")
        milvus_collection = Collection(name="knowledge_base", schema=schema)
        milvus_collection.create_index(field_name="vector", index_params={"index_type": "HNSW", "metric_type": "IP", "params": {"M": 16, "efConstruction": 200}})
        milvus_collection.load()

    try:
        from app.services.incremental_ingest_core import run_incremental_ingestion
        result = await run_incremental_ingestion(
            pool, milvus_collection, ingest_config,
            dry_run=dry_run, delete_missing=delete_missing
        )

        return {
            "status": "success",
            "dry_run": dry_run,
            "summary": {
                "added": result.added,
                "updated": result.updated,
                "unchanged": result.unchanged,
                "deleted": result.deleted,
                "errors": result.errors,
            },
            "details": result.details,
        }
    finally:
        pool.close()
        await pool.wait_closed()
        await asyncio.to_thread(connections.disconnect, alias=alias)
