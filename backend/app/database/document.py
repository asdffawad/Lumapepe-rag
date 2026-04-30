"""
ragz_documents 表 CRUD 操作（aiomysql 版本）
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import aiomysql
import orjson
import json


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
                    "INSERT INTO ragz_documents (title, doc_type, file_path, file_hash, status) VALUES (%s, %s, %s, %s, 'pending')",
                    (title, doc_type, file_path, file_hash),
                )
                last_id = cur.lastrowid
                await cur.execute("SELECT * FROM ragz_documents WHERE id = %s", (last_id,))
                row = await cur.fetchone()
                return self._row_to_record(row)

    async def find_by_id(self, doc_id: int) -> Optional[DocumentRecord]:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT * FROM ragz_documents WHERE id = %s", (doc_id,))
                row = await cur.fetchone()
                return self._row_to_record(row) if row else None

    async def mark_indexed(self, doc_id: int, chunk_count: int):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE ragz_documents SET status = 'done', indexed_at = NOW(), chunk_count = %s WHERE id = %s",
                    (chunk_count, doc_id),
                )

    async def mark_failed(self, doc_id: int):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("UPDATE ragz_documents SET status = 'failed' WHERE id = %s", (doc_id,))

    async def upsert_by_source(
        self,
        source_path: str,
        title: str,
        doc_type: str,
        file_hash: str | None,
        file_mtime: float | None,
        dry_run: bool = False,
        content_raw: dict | None = None,
    ) -> tuple[int, str]:
        """
        Insert or update document record by source_path.
        Returns (doc_id, action) where action is "created" | "updated" | "unchanged" | "previewed".

        State machine:
        - dry_run=True, hash matches, status != dry_run_previewed  → mark dry_run_previewed, return "previewed"
        - dry_run=True, hash matches, status == dry_run_previewed  → return "unchanged" (already previewed)
        - dry_run=True, hash matches, status == done                 → mark dry_run_previewed, return "previewed"
        - dry_run=False, hash matches, status == dry_run_previewed  → set status=pending, return "updated" (enter ingestion pipeline)
        - dry_run=False, hash matches, status == done               → return "unchanged" (fully indexed, skip)
        - hash changed → upsert to pending, return "created" or "updated"
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT id, file_hash, status FROM ragz_documents WHERE source_path = %s AND status != 'deleted' ORDER BY id DESC LIMIT 1",
                    (source_path,),
                )
                existing = await cur.fetchone()

                if existing is None:
                    initial_status = "dry_run_previewed" if dry_run else "pending"
                    raw_json = json.dumps(content_raw, ensure_ascii=False) if content_raw else None
                    await cur.execute(
                        "INSERT INTO ragz_documents (title, doc_type, source_path, file_path, file_hash, file_mtime, status, content_raw) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (title, doc_type, source_path, source_path, file_hash, file_mtime, initial_status, raw_json),
                    )
                    return (cur.lastrowid, "previewed" if dry_run else "created")

                existing_id = existing["id"]
                existing_hash = existing["file_hash"]
                existing_status = existing["status"]

                if existing_hash != file_hash:
                    raw_json = json.dumps(content_raw, ensure_ascii=False) if content_raw else None
                    await cur.execute(
                        "UPDATE ragz_documents SET title = %s, file_hash = %s, file_mtime = %s, "
                        "status = 'pending', indexed_at = NULL, chunk_count = 0, content_raw = %s "
                        "WHERE id = %s",
                        (title, file_hash, file_mtime, raw_json, existing_id),
                    )
                    return (existing_id, "updated")

                # hash unchanged — consult status
                if existing_status == "dry_run_previewed" and not dry_run:
                    await cur.execute(
                        "UPDATE ragz_documents SET status = 'pending', indexed_at = NULL, chunk_count = 0 WHERE id = %s",
                        (existing_id,),
                    )
                    return (existing_id, "updated")

                if dry_run:
                    if existing_status != "dry_run_previewed":
                        await cur.execute(
                            "UPDATE ragz_documents SET status = 'dry_run_previewed' WHERE id = %s",
                            (existing_id,),
                        )
                    return (existing_id, "previewed")

                if existing_status == "done":
                    return (existing_id, "unchanged")

                return (existing_id, "unchanged")

    async def mark_deleted(self, doc_id: int):
        """Soft delete document (status='deleted')"""
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE ragz_documents SET status = 'deleted' WHERE id = %s",
                    (doc_id,),
                )

    async def hard_delete(self, doc_id: int):
        """Hard delete document (remove from MySQL)"""
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM ragz_documents WHERE id = %s",
                    (doc_id,),
                )

    async def list_source_paths(self) -> list[tuple[int, str]]:
        """Return all non-deleted records as (id, source_path)"""
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id, source_path FROM ragz_documents WHERE status != 'deleted' AND source_path IS NOT NULL"
                )
                rows = await cur.fetchall()
                return [(row[0], row[1]) for row in rows]

    async def get_by_source_path(self, source_path: str) -> Optional[DocumentRecord]:
        """Find record by source_path"""
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT * FROM ragz_documents WHERE source_path = %s AND status != 'deleted'",
                    (source_path,),
                )
                row = await cur.fetchone()
                return self._row_to_record(row) if row else None

    def _row_to_record(self, row: dict) -> DocumentRecord:
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
