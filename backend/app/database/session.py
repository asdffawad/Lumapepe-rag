"""
ragz_sessions 表 CRUD 操作（aiomysql 版本）
"""
from __future__ import annotations

import uuid
import json as json_module
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import aiomysql


@dataclass
class SessionRecord:
    id: str
    user_id: str
    intent: Optional[str]
    status: str
    created_at: datetime
    updated_at: datetime
    summary: Optional[str]
    metadata: dict


class SessionRepository:
    def __init__(self, pool: aiomysql.Pool):
        self.pool = pool

    async def create(self, user_id: str, metadata: dict | None = None) -> SessionRecord:
        sid = str(uuid.uuid4())
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "INSERT INTO ragz_sessions (id, user_id, status, metadata) VALUES (%s, %s, 'active', %s)",
                    (sid, user_id, json_module.dumps(metadata or {})),
                )
                await cur.execute("SELECT * FROM ragz_sessions WHERE id = %s", (sid,))
                row = await cur.fetchone()
                return self._row_to_record(row)

    async def find_by_id(self, session_id: str) -> Optional[SessionRecord]:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT * FROM ragz_sessions WHERE id = %s", (session_id,))
                row = await cur.fetchone()
                return self._row_to_record(row) if row else None

    async def update_intent(self, session_id: str, intent: str):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE ragz_sessions SET intent = %s WHERE id = %s", (intent, session_id)
                )

    async def update_summary(self, session_id: str, summary: str):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE ragz_sessions SET summary = %s WHERE id = %s", (summary, session_id)
                )

    async def update_metadata(self, session_id: str, metadata: dict):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE ragz_sessions SET metadata = %s WHERE id = %s",
                    (json_module.dumps(metadata, ensure_ascii=False), session_id),
                )

    async def update_status(self, session_id: str, status: str):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE ragz_sessions SET status = %s WHERE id = %s", (status, session_id)
                )

    def _row_to_record(self, row: dict) -> SessionRecord:
        metadata = row.get("metadata")
        if isinstance(metadata, str):
            metadata = json_module.loads(metadata)
        return SessionRecord(
            id=row["id"],
            user_id=row["user_id"],
            intent=row.get("intent"),
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            summary=row.get("summary"),
            metadata=metadata or {},
        )
