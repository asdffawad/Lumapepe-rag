# 增量摄取（定时 + API）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 RAG 知识库增加增量摄取能力——通过每日凌晨 Celery Beat 定时任务和增量 API 双通道感知 data/ 目录变化，自动完成文档的新增、更新、删除同步。

**Architecture:** 增量摄取的核心是 **source_path + file_mtime** 双键索引：MySQL `documents` 表新增 `source_path` 和 `file_mtime` 字段，每次扫描 data/ 目录时对比文件系统 mtime 与数据库记录，对比 hash 判断内容是否真正变化。Celery Beat 定时任务在每日凌晨执行全量目录扫描；增量 API 则支持外部系统通过 HTTP 触发或 dry-run 预览。删除检测通过对比 MySQL 记录与文件系统差异实现，缺失文件触发软删除 + Milvus 向量清理。

**Tech Stack:** Python 3.11+ / aiomysql / pymilvus / FastAPI / Celery Beat / Redis

---

## 设计决策

| 问题 | 决策 |
|------|------|
| 增量检测基础 | `source_path` + `file_mtime` 双字段，mtime 变化触发 hash 再对比 |
| 文件删除处理 | 定时任务：软删除 MySQL + 删除 Milvus；API：不做自动删除 |
| 定时频率 | 每日凌晨（0:00 Asia/Shanghai），`schedule: crontab(hour=0, minute=0)` |
| API 响应形式 | 详细清单 + 可选 dry-run 预览 |

---

## 文件结构

```
backend/
  app/
    database/
      document.py          # 修改：新增 source_path/file_mtime 字段到 DocumentRecord dataclass
    services/
      incremental_ingest_core.py  # 新建：增量摄取核心逻辑（被 Celery 和 CLI 共用）
    api/
      admin.py             # 修改：新增 /api/v1/admin/ingest/incremental 端点
    celery_app/
      __init__.py         # 修改：新增 beat_schedule 条目
      tasks/
        incremental_ingest.py   # 新建：Celery 任务包装（调用核心逻辑）
        __init__.py       # 修改：导出新增任务
scripts/
  incremental_ingest.py         # 新建：CLI 脚本（直接调用核心逻辑）
```

**关键设计**：核心逻辑统一放在 `app/services/incremental_ingest_core.py`，避免 `scripts/` ↔ `celery_app/tasks/` 之间的循环导入。Celery 任务和 CLI 脚本都只导入核心模块。

---

## Task 1: MySQL Schema 迁移

**Files:**
- Modify: `backend/app/database/document.py:12-98`

- [ ] **Step 1: 修改 DocumentRecord dataclass，新增两个字段**

将 `source_path` 和 `file_mtime` 添加到 `DocumentRecord`：

```python
@dataclass
class DocumentRecord:
    id: int
    doc_type: str
    title: str
    file_path: Optional[str]
    source_path: Optional[str]   # 新增：文件系统原始路径（如 data/products.json::SKU001）
    file_mtime: Optional[float] # 新增：文件修改时间戳（秒，os.path.getmtime）
    file_hash: Optional[str]
    chunk_count: int
    status: str
    created_at: datetime
    indexed_at: Optional[datetime]
    content_raw: Optional[dict] = None
```

- [ ] **Step 2: 修改 `_row_to_record` 映射**

```python
def _row_to_record(self, row: dict) -> DocumentRecord:
    import orjson
    content_raw = None
    raw_val = row.get("content_raw")
    if raw_val:
        if isinstance(raw_val, str):
            content_raw = orjson.loads(raw_val)
        elif isinstance(raw_val, bytes):
            content_raw = orjson.loads(raw_val)
        else:
            content_raw = raw_val
    return DocumentRecord(
        id=row["id"],
        doc_type=row["doc_type"],
        title=row["title"],
        file_path=row.get("file_path"),
        source_path=row.get("source_path"),        # 新增
        file_mtime=row.get("file_mtime"),          # 新增
        file_hash=row.get("file_hash"),
        chunk_count=row.get("chunk_count") or 0,
        status=row["status"],
        created_at=row["created_at"],
        indexed_at=row.get("indexed_at"),
        content_raw=content_raw,
    )
```

- [ ] **Step 3: 新增 `DocumentRepository` 方法（insert / upsert 相关）**

在 `DocumentRepository` 类中新增以下方法：

```python
async def upsert_by_source(self, source_path: str, title: str, doc_type: str,
                           file_hash: str | None, file_mtime: float | None) -> tuple[int, str]:
    """
    依据 source_path 插入或更新文档记录。
    返回 (doc_id, action)，action 为 "created" | "updated" | "unchanged"
    """
    async with self.pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT id, file_hash, status FROM documents WHERE source_path = %s",
                (source_path,)
            )
            row = await cur.fetchone()
            if row:
                doc_id = row["id"]
                if row["file_hash"] == file_hash:
                    return doc_id, "unchanged"
                # 内容变了，更新
                await cur.execute(
                    "UPDATE documents SET title=%s, doc_type=%s, file_hash=%s, "
                    "file_mtime=%s, status='processing', content_raw=NULL "
                    "WHERE id=%s",
                    (title, doc_type, file_hash, file_mtime, doc_id)
                )
                return doc_id, "updated"
            else:
                # 新增
                await cur.execute(
                    "INSERT INTO documents (source_path, title, doc_type, file_hash, "
                    "file_mtime, status) VALUES (%s,%s,%s,%s,%s,'processing')",
                    (source_path, title, doc_type, file_hash, file_mtime)
                )
                return cur.lastrowid, "created"


async def mark_deleted(self, doc_id: int):
    """软删除文档（status='deleted'）"""
    async with self.pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE documents SET status='deleted' WHERE id=%s",
                (doc_id,)
            )


async def list_source_paths(self) -> list[tuple[int, str]]:
    """返回所有非 deleted 记录的 (id, source_path)"""
    async with self.pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, source_path FROM documents WHERE status != 'deleted' AND source_path IS NOT NULL"
            )
            rows = await cur.fetchall()
            return [(r[0], r[1]) for r in rows]


async def get_by_source_path(self, source_path: str) -> Optional[DocumentRecord]:
    """依据 source_path 查找记录"""
    async with self.pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM documents WHERE source_path = %s AND status != 'deleted'",
                (source_path,)
            )
            row = await cur.fetchone()
            return self._row_to_record(row) if row else None
```

- [ ] **Step 4: 补充 `from __future__ import annotations` 顶部导入**

确保文件顶部有：
```python
from __future__ import annotations
from dataclasses import dataclass
```
（`@dataclass` 装饰器在文件底部已有，但顶部 import 缺失，需补充）

---

## Task 2: 编写增量摄取核心逻辑（共享模块）

**Files:**
- Create: `backend/app/services/incremental_ingest_core.py`
- Create: `backend/app/services/__init__.py`（如果不存在）

- [ ] **Step 1: 编写 `IncrementalIngestResult` 数据类**

```python
@dataclass
class IncrementalIngestResult:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    errors: int = 0
    details: list[dict] = field(default_factory=list)
```

- [ ] **Step 2: 编写 `scan_data_directory` 函数**

扫描 data/ 目录，返回所有文件项（含 mtime 和 computed source_path）：

```python
def scan_data_directory(data_dir: Path) -> list[dict]:
    """
    扫描 data_dir，返回文件列表。
    JSON 文件中的每个条目 source_path = "products.json::product_id"
    独立文件 source_path = "data/xxx.pdf"（相对于项目根）
    """
    results: list[dict] = []
    data_path = Path(data_dir)

    # 扫描 products.json
    products_file = data_path / "products.json"
    if products_file.exists():
        try:
            with open(products_file, encoding="utf-8") as f:
                products = json.load(f)
            mtime = products_file.stat().st_mtime
            for item in products:
                source_id = item.get("product_id") or item.get("id") or ""
                results.append({
                    "source_path": f"products.json::{source_id}",
                    "mtime": mtime,
                    "item": item,
                    "doc_type": "product",
                })
        except Exception:
            pass

    # 扫描 knowledge_base.json
    kb_file = data_path / "knowledge_base.json"
    if kb_file.exists():
        try:
            with open(kb_file, encoding="utf-8") as f:
                kb_items = json.load(f)
            mtime = kb_file.stat().st_mtime
            for item in kb_items:
                doc_id = item.get("id", "")
                category = item.get("category", "")
                if "退换货" in category or "退货" in category or "换货" in category or "价格保护" in category or "发票" in category:
                    doc_type = "policy"
                else:
                    doc_type = "faq"
                results.append({
                    "source_path": f"knowledge_base.json::{doc_id}",
                    "mtime": mtime,
                    "item": item,
                    "doc_type": doc_type,
                })
        except Exception:
            pass

    # 扫描 orders.json
    orders_file = data_path / "orders.json"
    if orders_file.exists():
        try:
            with open(orders_file, encoding="utf-8") as f:
                orders = json.load(f)
            mtime = orders_file.stat().st_mtime
            for item in orders:
                order_id = item.get("order_id", "")
                results.append({
                    "source_path": f"orders.json::{order_id}",
                    "mtime": mtime,
                    "item": item,
                    "doc_type": "manual",
                })
        except Exception:
            pass

    # 扫描富文本文件（PDF/DOCX/HTML）
    for ext in [".pdf", ".docx", ".html", ".htm"]:
        for file_path in data_path.rglob(f"*{ext}"):
            try:
                mtime = file_path.stat().st_mtime
                results.append({
                    "source_path": str(file_path.relative_to(data_path.parent)),
                    "mtime": mtime,
                    "item": None,   # 富文本单独解析
                    "doc_type": "policy",
                    "file_path": str(file_path),
                })
            except Exception:
                pass

    return results
```

- [ ] **Step 3: 编写 `run_incremental_ingestion` 核心函数**

```python
async def run_incremental_ingestion(
    mysql_pool: aiomysql.Pool,
    milvus_collection,  # pymilvus Collection
    config: dict,
    dry_run: bool = False,
    delete_missing: bool = True,
) -> IncrementalIngestResult:
    """
    增量摄取核心逻辑。

    流程：
    1. scan_data_directory 扫描文件系统
    2. 对每个文件项：upsert_by_source（新增/更新/skip）
    3. 对比 MySQL source_paths 与扫描结果，找出缺失文件 → 软删除
    4. 对新增/更新项：删除旧向量 → 分块 → 向量化 → 入库
    """
    import hashlib
    from app.utils.chunker import TextChunker

    result = IncrementalIngestResult()
    data_dir = config.get("data_dir", "data")
    chunk_size = config.get("chunk_size", 512)
    chunk_overlap = config.get("chunk_overlap", 50)
    batch_size = config.get("batch_size", 10)

    chunker = TextChunker(chunk_size=chunk_size, overlap=chunk_overlap)

    # Step 1: 扫描文件系统
    file_items = scan_data_directory(Path(data_dir))
    scanned_paths = {item["source_path"] for item in file_items}

    # Step 2: 获取 Milvus client（已在外部传入）
    repo = DocumentRepository(mysql_pool)

    # Step 3: upsert 每个文件
    for item in file_items:
        source_path = item["source_path"]
        mtime = item["mtime"]
        doc_type = item["doc_type"]
        item_data = item.get("item")

        try:
            # 计算 hash
            if item_data is not None:
                content_str = json.dumps(item_data, ensure_ascii=False, sort_keys=True)
                file_hash = hashlib.sha256(content_str.encode()).hexdigest()
            else:
                # 富文本文件：读取内容计算 hash
                file_path = item.get("file_path", "")
                if not os.path.exists(file_path):
                    result.errors += 1
                    result.details.append({
                        "source_path": source_path,
                        "action": "error",
                        "reason": "file not found",
                    })
                    continue
                with open(file_path, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                file_hash = hashlib.sha256(content.encode()).hexdigest()
                mtime = os.path.getmtime(file_path)

            title = (
                item_data.get("title") or
                item_data.get("name") or
                item_data.get("product_id") or
                item_data.get("order_id") or
                source_path
            ) if item_data else source_path

            # 检查 mtime 是否变化（快速路径）
            existing = await repo.get_by_source_path(source_path)
            if existing:
                if existing.file_mtime is not None and abs(existing.file_mtime - mtime) < 0.1:
                    # mtime 没变，跳过
                    result.unchanged += 1
                    continue

            doc_id, action = await repo.upsert_by_source(
                source_path=source_path,
                title=title,
                doc_type=doc_type,
                file_hash=file_hash,
                file_mtime=mtime,
            )

            if action == "unchanged":
                result.unchanged += 1
                continue

            if dry_run:
                result.details.append({
                    "source_path": source_path,
                    "action": action,
                    "doc_id": doc_id,
                    "title": title,
                })
                continue

            # 实际执行：删除旧向量
            if milvus_collection is not None:
                expr = f"doc_id == {doc_id}"
                milvus_collection.delete(expr)

            # 分块
            if item_data is not None:
                markets = item_data.get("_markets", [])
                lang = item_data.get("_lang", "")
                chunks = chunker.chunk_structured(
                    item_data, doc_id, source_path, doc_type,
                    markets=markets, lang=lang
                )
            else:
                # 富文本
                # 富文本：复用 ingest_data.py 的安全解析器
            try:
                from scripts.ingest_data import _parse_file_safe
            except ImportError:
                from ingest_data import _parse_file_safe
            parsed = _parse_file_safe(item.get("file_path", ""))
                if parsed is None:
                    result.errors += 1
                    continue
                chunks = chunker.chunk_structured(
                    {"title": parsed.title, "content": parsed.content},
                    doc_id, source_path, doc_type,
                    markets=parsed.markets, lang=parsed.lang
                )

            if not chunks:
                await repo.mark_failed(doc_id)
                result.errors += 1
                continue

            # 向量化
            texts = [c.content for c in chunks]
            vectors = await _embed_texts(texts, config)

            # 入库 Milvus
            if milvus_collection is not None:
                ids = [f"{c.doc_id}_{c.chunk_index}" for c in chunks]
                all_doc_ids = [c.doc_id for c in chunks]
                all_indices = [c.chunk_index for c in chunks]
                all_sources = [c.source for c in chunks]
                all_doc_types = [c.doc_type for c in chunks]
                all_section_types = [c.section_type for c in chunks]
                all_markets = [json.dumps(c.markets, ensure_ascii=False) for c in chunks]
                all_langs = [c.lang for c in chunks]
                all_hierarchies = []
                for c in chunks:
                    safe_h = {k[:20]: v[:15] for k, v in c.hierarchy.items() if k != "_flat_index"}
                    hs = json.dumps(safe_h, ensure_ascii=False)
                    all_hierarchies.append(hs[:256] if len(hs) > 256 else hs)

                milvus_collection.insert([
                    ids, vectors, texts, all_doc_ids, all_indices,
                    all_sources, all_doc_types, all_section_types,
                    all_markets, all_langs, all_hierarchies,
                ])
                milvus_collection.flush()

            # 更新状态
            await repo.mark_indexed(doc_id, len(chunks))

            if action == "created":
                result.added += 1
            else:
                result.updated += 1

            result.details.append({
                "source_path": source_path,
                "action": action,
                "doc_id": doc_id,
                "title": title,
                "chunks": len(chunks),
            })

        except Exception as e:
            result.errors += 1
            result.details.append({
                "source_path": source_path,
                "action": "error",
                "reason": str(e),
            })

    # Step 4: 删除检测（只在 delete_missing=True 时执行）
    if delete_missing and not dry_run:
        try:
            db_paths = await repo.list_source_paths()
            for doc_id, source_path in db_paths:
                if source_path not in scanned_paths:
                    # 文件已删除 → 软删除 + 删除向量
                    if milvus_collection is not None:
                        expr = f"doc_id == {doc_id}"
                        milvus_collection.delete(expr)
                        milvus_collection.flush()
                    await repo.mark_deleted(doc_id)
                    result.deleted += 1
                    result.details.append({
                        "source_path": source_path,
                        "action": "deleted",
                        "doc_id": doc_id,
                    })
        except Exception as e:
            result.errors += 1
            result.details.append({
                "action": "delete_check_error",
                "reason": str(e),
            })

    return result
```

- [ ] **Step 4: 编写 `_embed_texts` 辅助函数**

```python
async def _embed_texts(texts: list[str], config: dict) -> list[list[float]]:
    """调用 DashScope API 向量化，失败时降级 Mock"""
    import hashlib
    dashscope_api_key = config.get("dashscope_api_key", "")
    base_url = config.get("llm_base_url", "https://dashscope.aliyuncs.com").rstrip("/")
    url = f"{base_url}/embeddings"

    if not dashscope_api_key:
        return _mock_vectors(texts)

    headers = {
        "Authorization": f"Bearer {dashscope_api_key}",
        "Content-Type": "application/json",
    }
    payload = {"model": "text-embedding-v3", "input": texts}

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return [e["embedding"] for e in data["data"]]
    except Exception:
        return _mock_vectors(texts)


def _mock_vectors(texts: list[str]) -> list[list[float]]:
    import hashlib
    result = []
    for i, t in enumerate(texts):
        vec = [
            (hashlib.md5(f"{t}_{i}_{j}".encode()).digest()[j % 16] / 255.0) * 2 - 1
            for j in range(1024)
        ]
        result.append(vec)
    return result
```

---

## Task 3: 新建 CLI 脚本

**Files:**
- Create: `scripts/incremental_ingest.py`

- [ ] **Step 1: 编写 CLI 脚本（复用核心逻辑）**

```python
#!/usr/bin/env python3
"""
增量摄取 CLI 脚本

使用方式:
    # 增量模式（默认）
    python scripts/incremental_ingest.py

    # 预览模式（dry-run）
    python scripts/incremental_ingest.py --dry-run

    # 不执行删除（安全模式）
    python scripts/incremental_ingest.py --no-delete
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent / "backend"
PROJECT_DIR = BACKEND_DIR.parent
for _p in [str(BACKEND_DIR), str(PROJECT_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def main():
    parser = argparse.ArgumentParser(description="增量摄取脚本")
    parser.add_argument("--dry-run", action="store_true", help="仅预览变更，不实际写入")
    parser.add_argument("--no-delete", action="store_true", help="不自动删除缺失文件")
    parser.add_argument("--data-dir", default=os.getenv("DATA_DIR", str(PROJECT_DIR / "data")))
    args = parser.parse_args()

    import aiomysql
    from pymilvus import Collection, connections, utility

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
        "data_dir": args.data_dir,
        "chunk_size": int(os.getenv("CHUNK_SIZE", "512")),
        "chunk_overlap": int(os.getenv("CHUNK_OVERLAP", "50")),
        "batch_size": int(os.getenv("INGEST_BATCH_SIZE", "10")),
        "dashscope_api_key": os.getenv("DASHSCOPE_API_KEY", ""),
        "llm_base_url": os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com"),
    }

    async def run():
        # MySQL
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

        # Milvus
        alias = "default"
        connections.connect(alias=alias, host=milvus_config["host"], port=milvus_config["port"],
            channel_kwargs={"grpc.max_receive_message_length": 32*1024*1024})
        collection_name = "knowledge_base"
        if utility.has_collection(collection_name):
            milvus_collection = Collection(collection_name)
        else:
            milvus_collection = None

        try:
            # 引用共享核心模块
            from app.services.incremental_ingest_core import run_incremental_ingestion
            result = await run_incremental_ingestion(
                pool, milvus_collection, ingest_config,
                dry_run=args.dry_run, delete_missing=not args.no_delete
            )

            print("\n========== 增量摄取结果 ==========")
            print(f"  新增: {result.added}")
            print(f"  更新: {result.updated}")
            print(f"  无变化: {result.unchanged}")
            print(f"  删除: {result.deleted}")
            print(f"  错误: {result.errors}")

            if result.details:
                print("\n  详细清单:")
                for d in result.details:
                    action = d.get("action", "")
                    sp = d.get("source_path", "")
                    chunks = d.get("chunks", "")
                    reason = d.get("reason", "")
                    if action == "error":
                        print(f"    [ERROR] {sp}: {reason}")
                    elif action == "deleted":
                        print(f"    [DELETE] {sp}")
                    elif args.dry_run:
                        print(f"    [{action.upper()}] {sp}" + (f" ({chunks} chunks)" if chunks else ""))
                    else:
                        print(f"    [{action.upper()}] {sp}" + (f" ({chunks} chunks)" if chunks else ""))

        finally:
            pool.close()
            await pool.wait_closed()
            connections.disconnect(alias=alias)

    asyncio.run(run())


if __name__ == "__main__":
    main()
```

---

## Task 4: 编写 Celery 任务

**Files:**
- Create: `backend/celery_app/tasks/incremental_ingest.py`
- Modify: `backend/celery_app/tasks/__init__.py`

- [ ] **Step 1: 编写 Celery 任务函数（引用核心逻辑）**

```python
"""
Celery 增量摄取任务
每日凌晨执行增量扫描 + 可选删除检测
"""
import asyncio
import os
from celery_app import celery_app
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent / "backend"
PROJECT_DIR = BACKEND_DIR.parent


@celery_app.task(name="celery_app.tasks.incremental_ingest.daily_incremental_ingest")
def daily_incremental_ingest(dry_run: bool = False, delete_missing: bool = True) -> dict:
    """
    每日凌晨执行增量摄取。

    参数:
        dry_run: True=仅预览不执行，默认 False
        delete_missing: True=自动删除缺失文件，默认 True
    """
    import sys
    for _p in [str(BACKEND_DIR), str(PROJECT_DIR)]:
        if _p not in sys.path:
            sys.path.insert(0, _p)

    import aiomysql
    from pymilvus import Collection, connections, utility

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
        connections.connect(alias=alias, host=milvus_config["host"], port=milvus_config["port"],
            channel_kwargs={"grpc.max_receive_message_length": 32*1024*1024})
        collection_name = "knowledge_base"
        milvus_collection = Collection(collection_name) if utility.has_collection(collection_name) else None

        try:
            # 引用共享核心模块（解决循环导入）
            from app.services.incremental_ingest_core import run_incremental_ingestion
            result = await run_incremental_ingestion(
                pool, milvus_collection, ingest_config,
                dry_run=dry_run, delete_missing=delete_missing
            )
            return {
                "added": result.added,
                "updated": result.updated,
                "unchanged": result.unchanged,
                "deleted": result.deleted,
                "errors": result.errors,
                "details_count": len(result.details),
            }
        finally:
            pool.close()
            await pool.wait_closed()
            connections.disconnect(alias=alias)

    return asyncio.run(_run())
```

- [ ] **Step 2: 修改 `backend/celery_app/tasks/__init__.py` 导出任务**

```python
"""
Celery 任务模块
"""
from celery_app import celery_app

__all__ = ["celery_app", "daily_incremental_ingest"]
```

- [ ] **Step 3: 修改 `backend/celery_app/__init__.py` 新增 beat_schedule**

在 `make_celery()` 函数的 `celery.conf.update(...)` 中，`beat_schedule` 字典添加：

```python
beat_schedule={
    "cleanup-old-logs": {
        "task": "celery_app.tasks.cleanup_old_logs",
        "schedule": 86400.0,
    },
    "refresh-hot-questions": {
        "task": "celery_app.tasks.refresh_hot_questions",
        "schedule": 3600.0,
    },
    # ---- 新增：每日凌晨增量摄取 ----
    "daily-incremental-ingest": {
        "task": "celery_app.tasks.incremental_ingest.daily_incremental_ingest",
        "schedule": crontab(hour=0, minute=0),   # 每日 00:00 执行
    },
},
```

需要确保文件顶部导入了 `crontab`：
```python
from celery.schedules import crontab
```

---

## Task 5: 新增增量摄取 API 端点

**Files:**
- Modify: `backend/app/api/admin.py`

- [ ] **Step 1: 在 admin.py 末尾新增 `/ingest/incremental` 端点**

```python
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
    import asyncio
    import os
    from pathlib import Path

    from pymilvus import Collection, connections, utility

    # 获取配置
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

    # 获取 data_dir
    data_dir = os.getenv("DATA_DIR", None)
    if data_dir is None:
        # 计算 backend/data（相对于 backend 目录）
        data_dir = str(Path(__file__).resolve().parent.parent.parent / "data")

    ingest_config = {
        "data_dir": data_dir,
        "chunk_size": int(os.getenv("CHUNK_SIZE", "512")),
        "chunk_overlap": int(os.getenv("CHUNK_OVERLAP", "50")),
        "batch_size": int(os.getenv("INGEST_BATCH_SIZE", "10")),
        "dashscope_api_key": os.getenv("DASHSCOPE_API_KEY", ""),
        "llm_base_url": os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com"),
    }

    async def _run():
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
        connections.connect(
            alias=alias, host=milvus_config["host"], port=milvus_config["port"],
            channel_kwargs={"grpc.max_receive_message_length": 32*1024*1024}
        )
        milvus_collection = None
        if utility.has_collection("knowledge_base"):
            milvus_collection = Collection("knowledge_base")

        try:
            # 复用共享核心模块（解决循环导入）
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
            connections.disconnect(alias=alias)

    return asyncio.run(_run())
```

**注意**：`app/services/incremental_ingest_core.py` 在 FastAPI worker 进程中被 import 时，Celery 的 `sys.path` 调整不适用。确保 `backend/app/main.py` 启动时 `app/services/` 已在 `sys.path` 中（通过 `PYTHONPATH=backend` 或 uvicorn 工作目录）。

---

## Task 6: MySQL Schema 升级脚本

**Files:**
- Create: `scripts/upgrade_incremental_ingest.sql`

- [ ] **Step 1: 编写 SQL 迁移脚本**

```sql
-- 增量摄取功能 Schema 升级
-- 执行: mysql -u root -p rag_customer_service < scripts/upgrade_incremental_ingest.sql

-- 新增字段：source_path（文件系统路径标识）、file_mtime（文件修改时间）
ALTER TABLE documents
    ADD COLUMN source_path VARCHAR(512) DEFAULT NULL AFTER file_path,
    ADD COLUMN file_mtime DOUBLE DEFAULT NULL AFTER source_path;

-- 为 source_path 建立索引，加速增量查询
CREATE INDEX ix_documents_source_path ON documents(source_path);

-- 为 status != 'deleted' 条件建立复合索引（如果查询频繁，可考虑）
-- CREATE INDEX ix_documents_status_source ON documents(status, source_path);
```

---

## Task 7: 验证

**Files:**
- Modify: `backend/tests/test_admin_integration.py`

- [ ] **Step 1: 编写增量摄取单元测试**

```python
import pytest
import asyncio
from pathlib import Path
import tempfile
import json
import os

from celery_app.tasks.incremental_ingest import (
    scan_data_directory,
    IncrementalIngestResult,
)


def test_scan_data_directory():
    """测试目录扫描功能"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建测试文件
        products = [{"product_id": "P001", "name": "Test Product"}]
        kb = [{"id": "K001", "category": "FAQ", "content": "test"}]

        products_file = Path(tmpdir) / "products.json"
        kb_file = Path(tmpdir) / "knowledge_base.json"

        with open(products_file, "w", encoding="utf-8") as f:
            json.dump(products, f)
        with open(kb_file, "w", encoding="utf-8") as f:
            json.dump(kb, f)

        items = scan_data_directory(Path(tmpdir))
        paths = [item["source_path"] for item in items]

        assert "products.json::P001" in paths
        assert "knowledge_base.json::K001" in paths
        assert len(items) == 2


def test_scan_empty_directory():
    """测试空目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        items = scan_data_directory(Path(tmpdir))
        assert items == []
```

- [ ] **Step 2: 运行测试验证**

```bash
cd f:\Myprogram\cursor\work\RAG\backend
python -m pytest tests/test_admin_integration.py -v -k incremental
```

---

## 执行顺序

1. **Task 6** (SQL Schema) → 2. **Task 1** (DocumentRecord) → 3. **Task 2** (核心逻辑) → 4. **Task 3** (CLI) → 5. **Task 4** (Celery) → 6. **Task 5** (API) → 7. **Task 7** (测试)
