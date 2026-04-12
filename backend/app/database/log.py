"""
logs 表 CRUD 操作（aiomysql 版本）
"""
from __future__ import annotations

import json as json_module
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import aiomysql


@dataclass
class LogRecord:
    id: int
    level: str
    service: str
    action: str
    request_id: Optional[str]
    detail: dict
    created_at: datetime


class LogRepository:
    def __init__(self, pool: aiomysql.Pool):
        self.pool = pool

    async def create(
        self,
        level: str,
        service: str,
        action: str,
        request_id: str | None = None,
        detail: dict | None = None,
    ) -> LogRecord:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    """INSERT INTO logs (level, service, action, request_id, detail)
                    VALUES (%s, %s, %s, %s, %s)""",
                    (level, service, action, request_id, json_module.dumps(detail or {})),
                )
                last_id = cur.lastrowid
                await cur.execute("SELECT * FROM logs WHERE id = %s", (last_id,))
                row = await cur.fetchone()
                return self._row_to_record(row)

    async def delete_older_than(self, days: int):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM logs WHERE created_at < DATE_SUB(NOW(), INTERVAL %s DAY)", (days,)
                )

    def _row_to_record(self, row: dict) -> LogRecord:
        detail = row.get("detail")
        if isinstance(detail, str):
            detail = json_module.loads(detail)
        return LogRecord(
            id=row["id"],
            level=row["level"],
            service=row["service"],
            action=row["action"],
            request_id=row.get("request_id"),
            detail=detail or {},
            created_at=row["created_at"],
        )
