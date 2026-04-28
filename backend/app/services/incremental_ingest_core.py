"""
增量摄取核心模块
提供目录增量扫描和增量摄取功能，供 CLI 脚本和 Celery 任务共同使用

功能说明：
1. scan_data_directory: 扫描数据目录，返回文件列表（含 JSON 项和富文本文件）
2. run_incremental_ingestion: 执行增量摄取主流程
3. _embed_texts / _mock_vectors: 向量化辅助函数
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx

# ============================================================================
# 修复 sys.path：确保 /app 在路径中，这样才能导入 scripts 模块
# ============================================================================
import sys as _sys
_app_idx = next((i for i, p in enumerate(_sys.path) if p == '/app' or p.rstrip('/') == '/app'), None)
if _app_idx is None:
    _sys.path.insert(0, '/app')

# ============================================================================
# _parse_file_safe 导入（支持 scripts.ingest_data 和 ingest_data 两种路径）
# ============================================================================
try:
    from scripts.ingest_data import _parse_file_safe
except ImportError:
    try:
        from ingest_data import _parse_file_safe
    except ImportError:
        # 定义空实现作为 fallback
        def _parse_file_safe(file_path: str):
            """Fallback 空实现"""
            return None


# ============================================================================
# 数据结构定义
# ============================================================================

@dataclass
class IncrementalIngestResult:
    """增量摄取结果汇总"""
    added: int = 0          # 新增文档数
    updated: int = 0        # 更新文档数
    unchanged: int = 0      # 未变化文档数
    previewed: int = 0     # dry_run 预览文档数
    deleted: int = 0         # 删除文档数
    errors: int = 0          # 错误数
    details: list[dict] = field(default_factory=list)  # 详细操作记录


# ============================================================================
# 目录扫描函数
# ============================================================================

def scan_data_directory(data_dir: Path) -> list[dict]:
    result: list[dict] = []
    data_path = Path(data_dir)

    # -------------------------------------------------------------------------
    # 1. 扫描 products.json
    # -------------------------------------------------------------------------
    products_file = data_path / "products.json"
    if products_file.exists():
        try:
            with open(products_file, encoding="utf-8") as f:
                products = json.load(f)
            mtime = os.path.getmtime(products_file)
            for item in products:
                product_id = item.get("product_id") or item.get("id") or ""
                result.append({
                    "source_path": f"products.json::{product_id}",
                    "mtime": datetime.fromtimestamp(mtime),
                    "item": item,
                    "doc_type": "product",
                    "file_path": None,
                })
        except Exception as e:
            print(f"[WARN] 读取 products.json 失败: {e}")

    # -------------------------------------------------------------------------
    # 2. 扫描 knowledge_base.json
    # -------------------------------------------------------------------------
    kb_file = data_path / "knowledge_base.json"
    if kb_file.exists():
        try:
            with open(kb_file, encoding="utf-8") as f:
                kb_items = json.load(f)
            mtime = datetime.fromtimestamp(os.path.getmtime(kb_file))
            for item in kb_items:
                item_id = item.get("id", "")
                category = item.get("category", "")

                # 根据 category 关键词判断 doc_type
                if any(kw in category for kw in ["退换货", "退货", "换货", "价格保护", "发票", "售后"]):
                    doc_type = "policy"
                elif any(kw in category for kw in ["配送", "支付", "物流", "运费"]):
                    doc_type = "faq"
                else:
                    doc_type = "faq"

                result.append({
                    "source_path": f"knowledge_base.json::{item_id}",
                    "mtime": mtime,
                    "item": item,
                    "doc_type": doc_type,
                    "file_path": None,
                })
        except Exception as e:
            print(f"[WARN] 读取 knowledge_base.json 失败: {e}")

    # -------------------------------------------------------------------------
    # 3. 扫描 orders.json
    # -------------------------------------------------------------------------
    orders_file = data_path / "orders.json"
    if orders_file.exists():
        try:
            with open(orders_file, encoding="utf-8") as f:
                orders = json.load(f)
            mtime = datetime.fromtimestamp(os.path.getmtime(orders_file))
            for item in orders:
                order_id = item.get("order_id", "")
                result.append({
                    "source_path": f"orders.json::{order_id}",
                    "mtime": mtime,
                    "item": item,
                    "doc_type": "manual",
                    "file_path": None,
                })
        except Exception as e:
            print(f"[WARN] 读取 orders.json 失败: {e}")

    # -------------------------------------------------------------------------
    # 4. 扫描富文本文件 (.pdf/.docx/.html/.htm)
    # -------------------------------------------------------------------------
    supported_exts = {".pdf", ".docx", ".html", ".htm"}
    for ext in supported_exts:
        for file_path in data_path.rglob(f"*{ext}"):
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
                # 计算相对于 data_dir 的路径作为 source_path
                rel_path = file_path.relative_to(data_path)
                result.append({
                    "source_path": str(rel_path),
                    "mtime": mtime,
                    "item": None,
                    "doc_type": "policy",
                    "file_path": str(file_path),
                })
            except Exception as e:
                print(f"[WARN] 扫描文件 {file_path} 失败: {e}")

    return result


# ============================================================================
# 向量化辅助函数
# ============================================================================

async def _embed_texts(texts: list[str], config: dict[str, Any]) -> list[list[float]]:
    """
    调用 DashScope text-embedding-v3 API 向量化文本

    Args:
        texts: 待向量化的文本列表
        config: 配置字典，需包含 dashscope_api_key 和 llm_base_url

    Returns:
        向量列表，每个向量 1024 维
    """
    api_key = config.get("dashscope_api_key", "")
    base_url = config.get("llm_base_url", "https://dashscope.aliyuncs.com")

    if not api_key:
        return _mock_vectors(texts)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"model": "text-embedding-v3", "input": texts}

    url = f"{base_url.rstrip('/')}/embeddings"

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return [e["embedding"] for e in data["data"]]
    except Exception as e:
        print(f"[WARN] DashScope API 调用失败: {e}，使用 Mock 向量")
        return _mock_vectors(texts)


def _mock_vectors(texts: list[str]) -> list[list[float]]:
    """
    生成确定性伪随机向量（相同文本产生相同向量，便于调试）

    Args:
        texts: 文本列表

    Returns:
        向量列表，每个向量 1024 维
    """
    result = []
    for i, t in enumerate(texts):
        vec = [
            (hashlib.md5(f"{t}_{i}_{j}".encode()).digest()[j % 16] / 255.0) * 2 - 1
            for j in range(1024)
        ]
        result.append(vec)
    return result


# ============================================================================
# 层级字段截断辅助函数
# ============================================================================

def _truncate_hierarchy(hierarchy: dict, max_key_len: int = 20, max_val_len: int = 15, max_total: int = 256) -> str:
    """
    截断 hierarchy 字典以满足 Milvus 存储约束

    Args:
        hierarchy: 原始层级字典
        max_key_len: 键的最大长度
        max_val_len: 值的最大长度
        max_total: 最终字符串的最大长度

    Returns:
        JSON 序列化的字符串（已截断）
    """
    # 过滤掉 _flat_index，截断键值
    safe_hierarchy = {
        k[:max_key_len]: v[:max_val_len]
        for k, v in hierarchy.items()
        if k != "_flat_index"
    }
    hierarchy_str = json.dumps(safe_hierarchy, ensure_ascii=False)
    if len(hierarchy_str) > max_total:
        hierarchy_str = hierarchy_str[:max_total]
    return hierarchy_str


# ============================================================================
# 增量摄取主函数
# ============================================================================

async def run_incremental_ingestion(
    mysql_pool: Any,
    qdrant_client: Any,
    config: dict[str, Any],
    dry_run: bool = False,
    delete_missing: bool = False,
) -> IncrementalIngestResult:
    """
    执行增量摄取主流程

    Args:
        mysql_pool: aiomysql 连接池
        qdrant_client: QdrantClient 对象（可为 None）
        config: 配置字典，需包含:
            - data_dir: 数据目录路径
            - chunk_size: 分块大小
            - overlap: 重叠大小
            - dashscope_api_key: DashScope API 密钥
            - llm_base_url: LLM API 地址
            - batch_size: 向量化批处理大小
        dry_run: True 则只扫描不实际写入
        delete_missing: True 则删除数据库中存在但扫描结果中不存在的文档

    Returns:
        IncrementalIngestResult 结果汇总
    """
    # 延迟导入避免模块级依赖
    import aiomysql

    from app.database.document import DocumentRepository
    from app.utils.chunker import TextChunker

    result = IncrementalIngestResult()

    try:
        # =========================================================================
        # 1. 扫描数据目录
        # =========================================================================
        data_dir = Path(config.get("data_dir", "./data"))
        scanned_items = scan_data_directory(data_dir)
        scanned_paths = {item["source_path"] for item in scanned_items}

        # =========================================================================
        # 2. 初始化分块器和仓储
        # =========================================================================
        chunk_size = config.get("chunk_size", 512)
        overlap = config.get("overlap", 50)
        chunker = TextChunker(chunk_size=chunk_size, overlap=overlap)
        repo = DocumentRepository(mysql_pool)

        # =========================================================================
        # 3. 处理每个扫描到的项
        # =========================================================================
        for item in scanned_items:
            source_path = item["source_path"]
            mtime = item["mtime"]
            doc_type = item["doc_type"]
            file_item = item["item"]
            file_path = item["file_path"]

            try:
                # -------------------------------------------------------------------------
                # 3a. JSON 项处理
                # -------------------------------------------------------------------------
                if file_item is not None:
                    # 计算内容 hash
                    content_str = json.dumps(file_item, ensure_ascii=False, sort_keys=True)
                    file_hash = hashlib.sha256(content_str.encode()).hexdigest()

                    # 提取标题
                    title = (
                        file_item.get("title")
                        or file_item.get("name")
                        or file_item.get("product_id")
                        or file_item.get("id")
                        or source_path
                    )

                    # upsert 到 MySQL
                    doc_id, action = await repo.upsert_by_source(
                        source_path=source_path,
                        title=str(title),
                        doc_type=doc_type,
                        file_hash=file_hash,
                        file_mtime=mtime,
                        dry_run=dry_run,
                    )

                    if action == "previewed":
                        result.previewed += 1
                        result.details.append({
                            "source_path": source_path,
                            "action": "previewed",
                            "title": file_item.get("title", source_path),
                            "message": "dry_run 预览",
                        })
                        continue

                    if action == "unchanged":
                        result.unchanged += 1
                        result.details.append({
                            "source_path": source_path,
                            "action": "unchanged",
                            "message": "内容未变化，跳过",
                        })
                        continue

                    if action == "created":
                        result.added += 1
                    else:  # updated
                        result.updated += 1

                    # -------------------------------------------------------------------------
                    # 3b. 删除旧向量（如果是更新）
                    # -------------------------------------------------------------------------
                    if qdrant_client is not None and action == "updated":
                        try:
                            await qdrant_client.delete_by_doc_id(doc_id)
                        except Exception as e:
                            print(f"[WARN] 删除向量失败 doc_id={doc_id}: {e}")

                    # -------------------------------------------------------------------------
                    # 3c. 分块
                    # -------------------------------------------------------------------------
                    markets = file_item.get("_markets", [])
                    lang = file_item.get("_lang", "")
                    chunks = chunker.chunk_structured(
                        data=file_item,
                        doc_id=doc_id,
                        source=source_path,
                        doc_type=doc_type,
                        markets=markets,
                        lang=lang,
                    )

                    result.details.append({
                        "source_path": source_path,
                        "action": action,
                        "doc_id": doc_id,
                        "title": file_item.get("title", source_path),
                        "chunks": len(chunks),
                    })

                # -------------------------------------------------------------------------
                # 3d. 富文本文件处理
                # -------------------------------------------------------------------------
                else:
                    if file_path is None:
                        continue

                    # 解析富文本文件
                    try:
                        parsed = _parse_file_safe(file_path)
                    except Exception as parse_exc:
                        result.errors += 1
                        result.details.append({
                            "source_path": source_path,
                            "action": "error",
                            "message": f"解析异常: {parse_exc}",
                        })
                        continue

                    if parsed is None:
                        result.errors += 1
                        result.details.append({
                            "source_path": source_path,
                            "action": "error",
                            "message": "文件解析失败",
                        })
                        continue

                    # 计算内容 hash
                    content_str = parsed.content
                    file_hash = hashlib.sha256(content_str.encode()).hexdigest()

                    # upsert 到 MySQL
                    doc_id, action = await repo.upsert_by_source(
                        source_path=source_path,
                        title=parsed.title,
                        doc_type=doc_type,
                        file_hash=file_hash,
                        file_mtime=mtime,
                        dry_run=dry_run,
                    )

                    if action == "previewed":
                        result.previewed += 1
                        result.details.append({
                            "source_path": source_path,
                            "action": "previewed",
                            "title": parsed.title,
                            "message": "dry_run 预览",
                        })
                        continue

                    if action == "unchanged":
                        result.unchanged += 1
                        result.details.append({
                            "source_path": source_path,
                            "action": "unchanged",
                            "message": "内容未变化，跳过",
                        })
                        continue

                    if action == "created":
                        result.added += 1
                    else:  # updated
                        result.updated += 1

                    # 删除旧向量
                    if qdrant_client is not None and action == "updated":
                        try:
                            await qdrant_client.delete_by_doc_id(doc_id)
                        except Exception as e:
                            print(f"[WARN] 删除向量失败 doc_id={doc_id}: {e}")

                    # 分块
                    markets = parsed.markets
                    lang = parsed.lang
                    chunks = chunker.chunk_by_paragraph(
                        text=parsed.content,
                        doc_id=doc_id,
                        source=source_path,
                        doc_type=doc_type,
                        markets=markets,
                        lang=lang,
                    )

                    result.details.append({
                        "source_path": source_path,
                        "action": action,
                        "doc_id": doc_id,
                        "title": parsed.title,
                        "chunks": len(chunks),
                    })

                # =========================================================================
                # 4. 向量化和 Milvus 入库
                # =========================================================================
                if not chunks:
                    await repo.mark_indexed(doc_id, 0)
                    continue

                # 准备数据
                all_ids: list[str] = []
                all_contents: list[str] = []
                all_doc_ids: list[int] = []
                all_indices: list[int] = []
                all_sources: list[str] = []
                all_doc_types: list[str] = []
                all_section_types: list[str] = []
                all_markets: list[str] = []
                all_langs: list[str] = []
                all_hierarchies: list[str] = []

                for chunk in chunks:
                    all_ids.append(f"{chunk.doc_id}_{chunk.chunk_index}")
                    all_contents.append(chunk.content)
                    all_doc_ids.append(chunk.doc_id)
                    all_indices.append(chunk.chunk_index)
                    all_sources.append(chunk.source)
                    all_doc_types.append(chunk.doc_type)
                    all_section_types.append(chunk.section_type)
                    all_markets.append(json.dumps(chunk.markets, ensure_ascii=False))
                    all_langs.append(chunk.lang)
                    all_hierarchies.append(_truncate_hierarchy(chunk.hierarchy))

                # 批量向量化
                batch_size = config.get("batch_size", 10)
                all_vectors: list[list[float]] = []
                for i in range(0, len(chunks), batch_size):
                    batch_texts = all_contents[i : i + batch_size]
                    vectors = await _embed_texts(batch_texts, config)
                    all_vectors.extend(vectors)

                # 写入向量数据库
                if qdrant_client is not None:
                    try:
                        # 准备 metadata
                        all_metadata = []
                        for i in range(len(chunks)):
                            all_metadata.append({
                                "section_type": all_section_types[i],
                                "markets": json.loads(all_markets[i]) if all_markets[i] else ["CN"],
                                "lang": all_langs[i],
                                "hierarchy": json.loads(all_hierarchies[i]) if all_hierarchies[i] else {},
                            })
                        
                        await qdrant_client.insert(
                            vectors=all_vectors,
                            contents=all_contents,
                            metadata=all_metadata,
                            doc_id=doc_id,
                            doc_type=doc_type,
                            source=source_path,
                        )
                    except Exception as e:
                        await repo.mark_failed(doc_id)
                        result.errors += 1
                        result.details.append({
                            "source_path": source_path,
                            "action": "error",
                            "message": f"向量写入失败: {e}",
                        })
                        continue

                # 更新 MySQL 状态
                await repo.mark_indexed(doc_id, len(chunks))

            except Exception as e:
                result.errors += 1
                result.details.append({
                    "source_path": source_path,
                    "action": "error",
                    "message": str(e),
                })
                print(f"[ERROR] 处理 {source_path} 失败: {e}")

        # =========================================================================
        # 5. 处理缺失文档（硬删除 MySQL + Milvus）
        # =========================================================================
        if delete_missing and not dry_run:
            try:
                db_records = await repo.list_source_paths()
                db_paths = {path for _, path in db_records}

                # 找出数据库中有但扫描结果中没有的文档
                missing_paths = db_paths - scanned_paths

                for doc_id, path in db_records:
                    if path in missing_paths:
                        # 删除向量
                        if qdrant_client is not None:
                            try:
                                await qdrant_client.delete_by_doc_id(doc_id)
                            except Exception as e:
                                print(f"[WARN] 删除向量失败 doc_id={doc_id}: {e}")

                        # 硬删除 MySQL 记录
                        await repo.hard_delete(doc_id)
                        result.deleted += 1
                        result.details.append({
                            "source_path": path,
                            "action": "deleted",
                            "doc_id": doc_id,
                        })

            except Exception as e:
                print(f"[ERROR] 处理缺失文档失败: {e}")

    except Exception as e:
        print(f"[FATAL] 增量摄取流程失败: {e}")
        result.errors += 1
        result.details.append({
            "source_path": "GLOBAL",
            "action": "error",
            "message": f"流程失败: {e}",
        })

    return result
