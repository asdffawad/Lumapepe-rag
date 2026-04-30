"""
ragz_messages 表 CRUD 操作（aiomysql 版本）
"""
from __future__ import annotations

import json as json_module
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import aiomysql


@dataclass
class MessageRecord:
    id: int
    session_id: str
    role: str
    content: str
    intent: Optional[str]
    sources: dict
    tokens: Optional[int]
    latency_ms: Optional[int]
    created_at: datetime


class MessageRepository:
    def __init__(self, pool: aiomysql.Pool):
        self.pool = pool

    async def create(
        self,
        session_id: str,
        role: str,
        content: str,
        intent: str | None = None,
        sources: dict | None = None,
        tokens: int | None = None,
        latency_ms: int | None = None,
    ) -> MessageRecord:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    """INSERT INTO ragz_messages
                    (session_id, role, content, intent, sources, tokens, latency_ms)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (session_id, role, content, intent, json_module.dumps(sources or {}), tokens, latency_ms),
                )
                last_id = cur.lastrowid
                await cur.execute("SELECT * FROM ragz_messages WHERE id = %s", (last_id,))
                row = await cur.fetchone()
                return self._row_to_record(row)

    async def list_by_session(self, session_id: str, limit: int = 50) -> list[MessageRecord]:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT * FROM ragz_messages WHERE session_id = %s ORDER BY created_at DESC LIMIT %s",
                    (session_id, limit),
                )
                rows = await cur.fetchall()
                return [self._row_to_record(r) for r in reversed(rows)]

    async def get_recent_queries(self, session_id: str, limit: int = 5) -> list[str]:
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """SELECT content FROM ragz_messages
                    WHERE session_id = %s AND role = 'user'
                    ORDER BY created_at DESC LIMIT %s""",
                    (session_id, limit),
                )
                rows = await cur.fetchall()
                return [r[0] for r in reversed(rows)]

    async def delete(self, message_id: int, session_id: str) -> bool:
        """删除指定消息（软删除）"""
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                # 先获取消息确认归属
                await cur.execute(
                    "SELECT * FROM ragz_messages WHERE id = %s AND session_id = %s",
                    (message_id, session_id)
                )
                row = await cur.fetchone()
                if not row:
                    return False

                # 删除消息
                await cur.execute(
                    "DELETE FROM ragz_messages WHERE id = %s",
                    (message_id,)
                )
                return cur.rowcount > 0

    def _row_to_record(self, row: dict) -> MessageRecord:
        sources = row.get("sources")
        if isinstance(sources, str):
            sources = json_module.loads(sources)
        return MessageRecord(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            intent=row.get("intent"),
            sources=sources or {},
            tokens=row.get("tokens"),
            latency_ms=row.get("latency_ms"),
            created_at=row["created_at"],
        )
