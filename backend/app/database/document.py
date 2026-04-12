"""
documents 表 CRUD 操作（aiomysql 版本）
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import aiomysql


@dataclass
class DocumentRecord:
    id: int
    doc_type: str
    title: str
    file_path: Optional[str]
    source_path: Optional[str]
    file_mtime: Optional[float]
    file_hash: Optional[str]
    chunk_count: int
    status: str
    created_at: datetime
    indexed_at: Optional[datetime]
    content_raw: Optional[dict] = None


@dataclass
class LogRecord:
    id: int
    level: str
    service: str
    action: str
    request_id: Optional[str]
    detail: dict
    created_at: datetime


class DocumentRepository:
    def __init__(self, pool: aiomysql.Pool):
        self.pool = pool

    async def create(self, title: str, doc_type: str, file_path: str | None = None, file_hash: str | None = None) -> DocumentRecord:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "INSERT INTO documents (title, doc_type, file_path, file_hash, status) VALUES (%s, %s, %s, %s, 'pending')",
                    (title, doc_type, file_path, file_hash),
                )
                last_id = cur.lastrowid
                await cur.execute("SELECT * FROM documents WHERE id = %s", (last_id,))
                row = await cur.fetchone()
                return self._row_to_record(row)

    async def find_by_id(self, doc_id: int) -> Optional[DocumentRecord]:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT * FROM documents WHERE id = %s", (doc_id,))
                row = await cur.fetchone()
                return self._row_to_record(row) if row else None

    async def mark_indexed(self, doc_id: int, chunk_count: int):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE documents SET status = 'done', indexed_at = NOW(), chunk_count = %s WHERE id = %s",
                    (chunk_count, doc_id),
                )

    async def mark_failed(self, doc_id: int):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("UPDATE documents SET status = 'failed' WHERE id = %s", (doc_id,))

    async def upsert_by_source(
        self,
        source_path: str,
        title: str,
        doc_type: str,
        file_hash: str | None,
        file_mtime: float | None,
    ) -> tuple[int, str]:
        """
        Insert or update document record by source_path.
        Returns (doc_id, action) where action is "created" | "updated" | "unchanged"
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                # 查询是否存在该 source_path 的记录
                await cur.execute(
                    "SELECT id, file_hash FROM documents WHERE source_path = %s AND status != 'deleted'",
                    (source_path,),
                )
                existing = await cur.fetchone()

                if existing is None:
                    # 记录不存在，插入新记录
                    await cur.execute(
                        "INSERT INTO documents (title, doc_type, source_path, file_path, file_hash, file_mtime, status) "
                        "VALUES (%s, %s, %s, %s, %s, %s, 'pending')",
                        (title, doc_type, source_path, source_path, file_hash, file_mtime),
                    )
                    return (cur.lastrowid, "created")

                # 记录存在，比较 file_hash 判断是否需要更新
                if existing["file_hash"] != file_hash:
                    await cur.execute(
                        "UPDATE documents SET title = %s, file_hash = %s, file_mtime = %s, "
                        "status = 'pending', indexed_at = NULL, chunk_count = 0 "
                        "WHERE id = %s",
                        (title, file_hash, file_mtime, existing["id"]),
                    )
                    return (existing["id"], "updated")

                # 内容未变化
                return (existing["id"], "unchanged")

    async def mark_deleted(self, doc_id: int):
        """Soft delete document (status='deleted')"""
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE documents SET status = 'deleted' WHERE id = %s",
                    (doc_id,),
                )

    async def list_source_paths(self) -> list[tuple[int, str]]:
        """Return all non-deleted records as (id, source_path)"""
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id, source_path FROM documents WHERE status != 'deleted' AND source_path IS NOT NULL"
                )
                rows = await cur.fetchall()
                return [(row[0], row[1]) for row in rows]

    async def get_by_source_path(self, source_path: str) -> Optional[DocumentRecord]:
        """Find record by source_path"""
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT * FROM documents WHERE source_path = %s AND status != 'deleted'",
                    (source_path,),
                )
                row = await cur.fetchone()
                return self._row_to_record(row) if row else None

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
            source_path=row.get("source_path"),
            file_mtime=row.get("file_mtime"),
            file_hash=row.get("file_hash"),
            chunk_count=row.get("chunk_count") or 0,
            status=row["status"],
            created_at=row["created_at"],
            indexed_at=row.get("indexed_at"),
            content_raw=content_raw,
        )
