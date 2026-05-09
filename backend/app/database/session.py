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
    member_user_id: Optional[int] = None
    kefu_conversation_id: Optional[int] = None
    tenant_id: int = 0
    handoff_status: int = 0
    handoff_at: Optional[datetime] = None
    title: Optional[str] = None
    last_message_content: Optional[str] = None
    last_message_role: Optional[str] = None
    last_message_at: Optional[datetime] = None
    message_count: int = 0
    pinned: int = 0
    archived: int = 0


class SessionRepository:
    def __init__(self, pool: aiomysql.Pool):
        self.pool = pool

    async def create(
        self,
        user_id: str,
        metadata: dict | None = None,
        member_user_id: int | None = None,
        tenant_id: int = 0,
    ) -> SessionRecord:
        sid = str(uuid.uuid4())
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                if member_user_id is not None:
                    try:
                        await cur.execute(
                            "INSERT INTO ragz_sessions "
                            "(id, user_id, status, metadata, member_user_id, tenant_id) "
                            "VALUES (%s, %s, 'active', %s, %s, %s)",
                            (
                                sid,
                                user_id,
                                json_module.dumps(metadata or {}, ensure_ascii=False),
                                member_user_id,
                                tenant_id,
                            ),
                        )
                    except Exception as e:
                        if "Unknown column" not in str(e):
                            raise
                        await cur.execute(
                            "INSERT INTO ragz_sessions (id, user_id, status, metadata) "
                            "VALUES (%s, %s, 'active', %s)",
                            (sid, user_id, json_module.dumps(metadata or {}, ensure_ascii=False)),
                        )
                else:
                    await cur.execute(
                        "INSERT INTO ragz_sessions (id, user_id, status, metadata) VALUES (%s, %s, 'active', %s)",
                        (sid, user_id, json_module.dumps(metadata or {}, ensure_ascii=False)),
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

    async def list_history_for_user(
        self,
        user_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict]:
        member_user_id = int(user_id) if str(user_id).isdigit() else None
        where_clause = "s.user_id = %s"
        params: list[object] = [user_id]
        if member_user_id is not None:
            where_clause = "(s.user_id = %s OR s.member_user_id = %s)"
            params.append(member_user_id)
        params.extend([limit, offset])

        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                try:
                    await cur.execute(
                        f"""
                        SELECT
                            s.id,
                            s.user_id,
                            s.member_user_id,
                            s.title,
                            s.summary,
                            s.intent,
                            s.status,
                            s.handoff_status,
                            s.kefu_conversation_id,
                            s.created_at,
                            s.updated_at,
                            COALESCE(NULLIF(s.last_message_content, ''), (
                                SELECT m.content
                                FROM ragz_messages m
                                WHERE m.session_id = s.id
                                ORDER BY m.created_at DESC, m.id DESC
                                LIMIT 1
                            )) AS last_message,
                            COALESCE(NULLIF(s.last_message_role, ''), (
                                SELECT m.role
                                FROM ragz_messages m
                                WHERE m.session_id = s.id
                                ORDER BY m.created_at DESC, m.id DESC
                                LIMIT 1
                            )) AS last_message_role,
                            COALESCE(s.last_message_at, (
                                SELECT m.created_at
                                FROM ragz_messages m
                                WHERE m.session_id = s.id
                                ORDER BY m.created_at DESC, m.id DESC
                                LIMIT 1
                            ), s.updated_at, s.created_at) AS last_message_at,
                            GREATEST(COALESCE(s.message_count, 0), (
                                SELECT COUNT(*)
                                FROM ragz_messages m
                                WHERE m.session_id = s.id
                            )) AS message_count,
                            COALESCE(s.pinned, 0) AS pinned,
                            COALESCE(s.archived, 0) AS archived
                        FROM ragz_sessions s
                        WHERE {where_clause}
                        ORDER BY
                            COALESCE(s.pinned, 0) DESC,
                            COALESCE(s.last_message_at, (
                                SELECT MAX(m.created_at)
                                FROM ragz_messages m
                                WHERE m.session_id = s.id
                            ), s.updated_at, s.created_at) DESC,
                            s.created_at DESC
                        LIMIT %s OFFSET %s
                        """,
                        tuple(params),
                    )
                except Exception as e:
                    if "Unknown column" not in str(e):
                        raise
                    await cur.execute(
                        f"""
                        SELECT
                            s.id,
                            s.user_id,
                            s.member_user_id,
                            NULL AS title,
                            s.summary,
                            s.intent,
                            s.status,
                            s.handoff_status,
                            s.kefu_conversation_id,
                            s.created_at,
                            s.updated_at,
                            (
                                SELECT m.content
                                FROM ragz_messages m
                                WHERE m.session_id = s.id
                                ORDER BY m.created_at DESC, m.id DESC
                                LIMIT 1
                            ) AS last_message,
                            (
                                SELECT m.role
                                FROM ragz_messages m
                                WHERE m.session_id = s.id
                                ORDER BY m.created_at DESC, m.id DESC
                                LIMIT 1
                            ) AS last_message_role,
                            COALESCE((
                                SELECT m.created_at
                                FROM ragz_messages m
                                WHERE m.session_id = s.id
                                ORDER BY m.created_at DESC, m.id DESC
                                LIMIT 1
                            ), s.updated_at, s.created_at) AS last_message_at,
                            (
                                SELECT COUNT(*)
                                FROM ragz_messages m
                                WHERE m.session_id = s.id
                            ) AS message_count,
                            0 AS pinned,
                            0 AS archived
                        FROM ragz_sessions s
                        WHERE {where_clause}
                        ORDER BY
                            COALESCE((
                                SELECT MAX(m.created_at)
                                FROM ragz_messages m
                                WHERE m.session_id = s.id
                            ), s.updated_at, s.created_at) DESC,
                            s.created_at DESC
                        LIMIT %s OFFSET %s
                        """,
                        tuple(params),
                    )
                return list(await cur.fetchall())

    async def count_history_for_user(self, user_id: str) -> int:
        member_user_id = int(user_id) if str(user_id).isdigit() else None
        where_clause = "user_id = %s"
        params: list[object] = [user_id]
        if member_user_id is not None:
            where_clause = "(user_id = %s OR member_user_id = %s)"
            params.append(member_user_id)
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT COUNT(*) FROM ragz_sessions WHERE {where_clause}",
                    tuple(params),
                )
                row = await cur.fetchone()
                return int(row[0] if row else 0)

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

    async def update_handoff_fields(
        self,
        session_id: str,
        *,
        member_user_id: int | None = None,
        kefu_conversation_id: int | None = None,
        handoff_status: int | None = None,
        handoff_at_now: bool = False,
    ):
        assignments: list[str] = []
        params: list[object] = []
        if member_user_id is not None:
            assignments.append("member_user_id = %s")
            params.append(member_user_id)
        if kefu_conversation_id is not None:
            assignments.append("kefu_conversation_id = %s")
            params.append(kefu_conversation_id)
        if handoff_status is not None:
            assignments.append("handoff_status = %s")
            params.append(handoff_status)
        if handoff_at_now:
            assignments.append("handoff_at = NOW()")
        if not assignments:
            return
        params.append(session_id)
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"UPDATE ragz_sessions SET {', '.join(assignments)} WHERE id = %s",
                    tuple(params),
                )

    def _row_to_record(self, row: dict) -> SessionRecord:
        metadata = row.get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json_module.loads(metadata) if metadata else {}
            except json_module.JSONDecodeError:
                metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        return SessionRecord(
            id=row["id"],
            user_id=row["user_id"],
            intent=row.get("intent"),
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            summary=row.get("summary"),
            metadata=metadata or {},
            member_user_id=row.get("member_user_id"),
            kefu_conversation_id=row.get("kefu_conversation_id"),
            tenant_id=row.get("tenant_id") or 0,
            handoff_status=row.get("handoff_status") or 0,
            handoff_at=row.get("handoff_at"),
            title=row.get("title"),
            last_message_content=row.get("last_message_content"),
            last_message_role=row.get("last_message_role"),
            last_message_at=row.get("last_message_at"),
            message_count=row.get("message_count") or 0,
            pinned=row.get("pinned") or 0,
            archived=row.get("archived") or 0,
        )
