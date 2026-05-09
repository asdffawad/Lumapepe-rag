"""
转人工客服服务

封装转人工请求的核心业务逻辑，供 HTTP 接口和 WebSocket 处理器共用。
避免代码重复，确保人工客服请求的创建和状态更新逻辑一致。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import aiomysql

from app.database.connection import get_db_pool
from app.logging_config import get_logger
from app.services.redis_pubsub import publish_kefu_event

_logger = get_logger("handoff.service")


@dataclass
class HandoffResult:
    """转人工结果"""
    session_id: str
    kefu_conversation_id: int
    handoff_status: int
    conversation_status: int
    assigned_admin_id: Optional[int] = None
    handoff_at: Optional[datetime] = None
    created: bool = False
    already_exists: bool = False


class HandoffService:
    """转人工服务"""

    # 状态常量（与 kefu.py 保持一致）
    HANDOFF_AI = 0
    HANDOFF_PENDING = 1
    HANDOFF_ACCEPTED = 2
    HANDOFF_CLOSED = 3

    # 消息发送者类型
    SENDER_USER = 1
    SENDER_ADMIN = 2
    SENDER_AI = 3
    SENDER_SYSTEM = 4

    # 消息内容类型
    CONTENT_TEXT = 1
    KEFU_LOCAL_NOW_SQL = "DATE_ADD(UTC_TIMESTAMP(), INTERVAL 8 HOUR)"

    def __init__(self, pool: aiomysql.Pool, _overrides: Optional[dict] = None):
        self.pool = pool
        self._overrides = _overrides or {}

    async def _get_session(self, session_id: str):
        if "_get_session" in self._overrides:
            return await self._overrides["_get_session"](session_id)
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT * FROM ragz_sessions WHERE id = %s",
                    (session_id,),
                )
                row = await cur.fetchone()
        if row:
            row["metadata"] = self._parse_json(row.get("metadata"))
        return row

    async def _resolve_member_user(self, user_subject: str):
        if "_resolve_member_user" in self._overrides:
            return await self._overrides["_resolve_member_user"](user_subject)
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                if str(user_subject).isdigit():
                    await cur.execute(
                        """
                        SELECT id, mobile, email, nickname, avatar, status, tenant_id, wallet_address, deleted
                        FROM member_user
                        WHERE id = %s
                        LIMIT 1
                        """,
                        (int(user_subject),),
                    )
                else:
                    await cur.execute(
                        """
                        SELECT id, mobile, email, nickname, avatar, status, tenant_id, wallet_address, deleted
                        FROM member_user
                        WHERE email = %s OR mobile = %s OR nickname = %s OR wallet_address = %s
                        ORDER BY deleted ASC, id ASC
                        LIMIT 1
                        """,
                        (user_subject, user_subject, user_subject, user_subject),
                    )
                return await cur.fetchone()

    async def _get_conversation(self, conversation_id: int):
        if "_get_conversation" in self._overrides:
            return await self._overrides["_get_conversation"](conversation_id)
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT * FROM promotion_kefu_conversation WHERE id = %s AND deleted = b'0'",
                    (conversation_id,),
                )
                return await cur.fetchone()

    async def _create_conversation(
        self,
        member_user: dict,
        session: dict,
        last_message_content: str,
    ):
        if "_create_conversation" in self._overrides:
            return await self._overrides["_create_conversation"](member_user, session, last_message_content)
        tenant_id = member_user.get("tenant_id") or session.get("tenant_id") or 0
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    f"""
                    INSERT INTO promotion_kefu_conversation
                    (user_id, last_message_time, last_message_content, last_message_content_type,
                     admin_unread_message_count, status, last_user_message_time, creator, tenant_id,
                     create_time, update_time)
                    VALUES (%s, {self.KEFU_LOCAL_NOW_SQL}, %s, %s, 1, 0, {self.KEFU_LOCAL_NOW_SQL},
                            'rag_handoff', %s, {self.KEFU_LOCAL_NOW_SQL}, {self.KEFU_LOCAL_NOW_SQL})
                    """,
                    (
                        member_user["id"],
                        self._truncate(last_message_content),
                        self.CONTENT_TEXT,
                        tenant_id,
                    ),
                )
                conversation_id = cur.lastrowid
                await cur.execute(
                    "SELECT * FROM promotion_kefu_conversation WHERE id = %s",
                    (conversation_id,),
                )
                return await cur.fetchone()

    async def _update_session_handoff(
        self,
        session_id: str,
        *,
        member_user_id: Optional[int] = None,
        kefu_conversation_id: Optional[int] = None,
        handoff_status: Optional[int] = None,
        metadata_updates: Optional[dict] = None,
        set_handoff_at: bool = False,
        current_metadata: Optional[dict] = None,
    ):
        if "_update_session_handoff" in self._overrides:
            return await self._overrides["_update_session_handoff"](
                session_id, member_user_id=member_user_id, kefu_conversation_id=kefu_conversation_id,
                handoff_status=handoff_status, metadata_updates=metadata_updates, set_handoff_at=set_handoff_at,
                current_metadata=current_metadata,
            )
        session = await self._get_session(session_id)
        if not session:
            raise ValueError("SESSION_NOT_FOUND")

        assignments: list[str] = []
        params: list[Any] = []
        if member_user_id is not None:
            assignments.append("member_user_id = %s")
            params.append(member_user_id)
        if kefu_conversation_id is not None:
            assignments.append("kefu_conversation_id = %s")
            params.append(kefu_conversation_id)
        if handoff_status is not None:
            assignments.append("handoff_status = %s")
            params.append(handoff_status)
        if set_handoff_at:
            assignments.append("handoff_at = NOW()")
        if metadata_updates is not None:
            base_metadata = current_metadata if current_metadata is not None else (session.get("metadata") or {})
            merged_metadata = self._merge_dict(base_metadata, metadata_updates)
            assignments.append("metadata = %s")
            params.append(json.dumps(merged_metadata, ensure_ascii=False))
        if not assignments:
            return session

        params.append(session_id)
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"UPDATE ragz_sessions SET {', '.join(assignments)} WHERE id = %s",
                    tuple(params),
                )
        updated = await self._get_session(session_id)
        return updated or session

    async def _link_session_messages(
        self,
        session_id: str,
        *,
        member_user_id: int,
        kefu_conversation_id: int,
    ):
        if "_link_session_messages" in self._overrides:
            return await self._overrides["_link_session_messages"](
                session_id, member_user_id=member_user_id, kefu_conversation_id=kefu_conversation_id,
            )
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE ragz_messages
                    SET member_user_id = COALESCE(member_user_id, %s),
                        kefu_conversation_id = COALESCE(kefu_conversation_id, %s),
                        sender_type = COALESCE(
                            sender_type,
                            CASE role
                                WHEN 'user' THEN 1
                                WHEN 'assistant' THEN 3
                                ELSE 4
                            END
                        )
                    WHERE session_id = %s
                    """,
                    (member_user_id, kefu_conversation_id, session_id),
                )

    async def _insert_kefu_message(
        self,
        *,
        conversation_id: int,
        sender_id: int,
        sender_type: int,
        content: str,
        receiver_id: Optional[int] = None,
        receiver_type: Optional[int] = None,
        tenant_id: int = 0,
    ):
        if "_insert_kefu_message" in self._overrides:
            return await self._overrides["_insert_kefu_message"](
                conversation_id=conversation_id, sender_id=sender_id, sender_type=sender_type,
                content=content, receiver_id=receiver_id, receiver_type=receiver_type, tenant_id=tenant_id,
            )
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    """
                    INSERT INTO promotion_kefu_message
                    (conversation_id, sender_id, sender_type, receiver_id, receiver_type,
                     content_type, content, creator, tenant_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'rag_handoff', %s)
                    """,
                    (
                        conversation_id,
                        sender_id,
                        sender_type,
                        receiver_id,
                        receiver_type,
                        self.CONTENT_TEXT,
                        self._truncate(content),
                        tenant_id,
                    ),
                )
                message_id = cur.lastrowid
                try:
                    await conn.commit()
                except Exception:
                    pass
                await cur.execute(
                    "SELECT * FROM promotion_kefu_message WHERE id = %s",
                    (message_id,),
                )
                return await cur.fetchone()

    def _truncate(self, text: Optional[str], limit: int = 2048) -> str:
        """截断文本"""
        value = (text or "").strip()
        if not value:
            return "用户请求转人工客服"
        return value[:limit]

    def _parse_json(self, value: Any) -> dict:
        """解析 JSON 字段"""
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        if isinstance(value, str) and value:
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    def _merge_dict(self, base: dict, updates: dict) -> dict:
        """递归合并字典"""
        merged = dict(base or {})
        for key, value in (updates or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._merge_dict(merged[key], value)
            else:
                merged[key] = value
        return merged

    def _conversation_status_value(self, conversation: Optional[dict]) -> Optional[int]:
        if not conversation:
            return None
        try:
            return int(conversation.get("status"))
        except (TypeError, ValueError):
            return None

    def _build_handoff_metadata(
        self,
        *,
        conversation_id: int,
        member_user_id: int,
        handoff_status: int,
        trigger_source: str = "manual_button",
        reason: Optional[str] = None,
        extra: Optional[dict] = None,
        handoff_history: Optional[list[dict]] = None,
    ) -> dict:
        """构建 handoff 元数据"""
        handoff = {
            "kefu_conversation_id": conversation_id,
            "current_conversation_id": conversation_id,
            "member_user_id": member_user_id,
            "status": handoff_status,
            "trigger_source": trigger_source,
            "updated_at": datetime.now().isoformat(),
        }
        if handoff_status == self.HANDOFF_PENDING:
            handoff["requested_at"] = datetime.now().isoformat()
        if reason:
            handoff["reason"] = reason
        if extra:
            handoff.update(extra)
        updates = {"handoff": handoff}
        if handoff_history is not None:
            updates["handoff_history"] = handoff_history
        return updates

    async def _get_session(self, session_id: str) -> Optional[dict]:
        """获取会话信息"""
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT * FROM ragz_sessions WHERE id = %s",
                    (session_id,),
                )
                row = await cur.fetchone()
        if row:
            row["metadata"] = self._parse_json(row.get("metadata"))
        return row

    async def _resolve_member_user(self, user_subject: str) -> Optional[dict]:
        """解析会员用户"""
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                if str(user_subject).isdigit():
                    await cur.execute(
                        """
                        SELECT id, mobile, email, nickname, avatar, status, tenant_id, wallet_address, deleted
                        FROM member_user
                        WHERE id = %s
                        LIMIT 1
                        """,
                        (int(user_subject),),
                    )
                else:
                    await cur.execute(
                        """
                        SELECT id, mobile, email, nickname, avatar, status, tenant_id, wallet_address, deleted
                        FROM member_user
                        WHERE email = %s OR mobile = %s OR nickname = %s OR wallet_address = %s
                        ORDER BY deleted ASC, id ASC
                        LIMIT 1
                        """,
                        (user_subject, user_subject, user_subject, user_subject),
                    )
                return await cur.fetchone()

    async def _get_conversation(self, conversation_id: int) -> Optional[dict]:
        """获取客服会话"""
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT * FROM promotion_kefu_conversation WHERE id = %s AND deleted = b'0'",
                    (conversation_id,),
                )
                return await cur.fetchone()

    async def _create_conversation(
        self,
        member_user: dict,
        session: dict,
        last_message_content: str,
    ) -> dict:
        """创建客服会话"""
        tenant_id = member_user.get("tenant_id") or session.get("tenant_id") or 0
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    f"""
                    INSERT INTO promotion_kefu_conversation
                    (user_id, last_message_time, last_message_content, last_message_content_type,
                     admin_unread_message_count, status, last_user_message_time, creator, tenant_id,
                     create_time, update_time)
                    VALUES (%s, {self.KEFU_LOCAL_NOW_SQL}, %s, %s, 1, 0, {self.KEFU_LOCAL_NOW_SQL},
                            'rag_handoff', %s, {self.KEFU_LOCAL_NOW_SQL}, {self.KEFU_LOCAL_NOW_SQL})
                    """,
                    (
                        member_user["id"],
                        self._truncate(last_message_content),
                        self.CONTENT_TEXT,
                        tenant_id,
                    ),
                )
                conversation_id = cur.lastrowid
                await cur.execute(
                    "SELECT * FROM promotion_kefu_conversation WHERE id = %s",
                    (conversation_id,),
                )
                return await cur.fetchone()

    async def _update_session_handoff(
        self,
        session_id: str,
        *,
        member_user_id: Optional[int] = None,
        kefu_conversation_id: Optional[int] = None,
        handoff_status: Optional[int] = None,
        metadata_updates: Optional[dict] = None,
        set_handoff_at: bool = False,
        current_metadata: Optional[dict] = None,
    ) -> dict:
        """更新会话的转人工状态"""
        session = await self._get_session(session_id)
        if not session:
            raise ValueError("SESSION_NOT_FOUND")

        assignments: list[str] = []
        params: list[Any] = []
        if member_user_id is not None:
            assignments.append("member_user_id = %s")
            params.append(member_user_id)
        if kefu_conversation_id is not None:
            assignments.append("kefu_conversation_id = %s")
            params.append(kefu_conversation_id)
        if handoff_status is not None:
            assignments.append("handoff_status = %s")
            params.append(handoff_status)
        if set_handoff_at:
            assignments.append("handoff_at = NOW()")
        if metadata_updates is not None:
            base_metadata = current_metadata if current_metadata is not None else (session.get("metadata") or {})
            merged_metadata = self._merge_dict(base_metadata, metadata_updates)
            assignments.append("metadata = %s")
            params.append(json.dumps(merged_metadata, ensure_ascii=False))
        if not assignments:
            return session

        params.append(session_id)
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"UPDATE ragz_sessions SET {', '.join(assignments)} WHERE id = %s",
                    tuple(params),
                )
        updated = await self._get_session(session_id)
        return updated or session

    async def _link_session_messages(
        self,
        session_id: str,
        *,
        member_user_id: int,
        kefu_conversation_id: int,
    ) -> None:
        """关联会话消息"""
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE ragz_messages
                    SET member_user_id = COALESCE(member_user_id, %s),
                        kefu_conversation_id = COALESCE(kefu_conversation_id, %s),
                        sender_type = COALESCE(
                            sender_type,
                            CASE role
                                WHEN 'user' THEN 1
                                WHEN 'assistant' THEN 3
                                ELSE 4
                            END
                        )
                    WHERE session_id = %s
                    """,
                    (member_user_id, kefu_conversation_id, session_id),
                )

    async def _insert_kefu_message(
        self,
        *,
        conversation_id: int,
        sender_id: int,
        sender_type: int,
        content: str,
        receiver_id: Optional[int] = None,
        receiver_type: Optional[int] = None,
        tenant_id: int = 0,
    ) -> dict:
        """插入客服消息"""
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    """
                    INSERT INTO promotion_kefu_message
                    (conversation_id, sender_id, sender_type, receiver_id, receiver_type,
                     content_type, content, creator, tenant_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'rag_handoff', %s)
                    """,
                    (
                        conversation_id,
                        sender_id,
                        sender_type,
                        receiver_id,
                        receiver_type,
                        self.CONTENT_TEXT,
                        self._truncate(content),
                        tenant_id,
                    ),
                )
                message_id = cur.lastrowid
                try:
                    await conn.commit()
                except Exception:
                    pass
                await cur.execute(
                    "SELECT * FROM promotion_kefu_message WHERE id = %s",
                    (message_id,),
                )
                return await cur.fetchone()

    def _handoff_status_from_conversation(self, conversation: Optional[dict]) -> int:
        """从会话状态获取 handoff_status"""
        if not conversation:
            return self.HANDOFF_AI
        status_val = self._conversation_status_value(conversation)
        if status_val == 0:
            return self.HANDOFF_PENDING
        if status_val == 1:
            return self.HANDOFF_ACCEPTED
        if status_val == 2:
            return self.HANDOFF_CLOSED
        return self.HANDOFF_PENDING

    def _message_event(self, conversation_id: int, message: dict, target: str = "both") -> dict:
        """构建消息事件"""
        def _jsonable(value: Any) -> Any:
            if isinstance(value, datetime):
                return value.strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(value, (bytes, bytearray)):
                raw = bytes(value)
                if len(raw) == 1:
                    return int.from_bytes(raw, "big")
                try:
                    return raw.decode("utf-8")
                except UnicodeDecodeError:
                    return raw.hex()
            if isinstance(value, dict):
                return {key: _jsonable(val) for key, val in value.items()}
            if isinstance(value, list):
                return [_jsonable(item) for item in value]
            return value

        return {
            "event": "message.new",
            "conversation_id": int(conversation_id),
            "target": target,
            "message": _jsonable(message),
        }

    async def request_handoff(
        self,
        session_id: str,
        user_id: str,
        reason: Optional[str] = None,
        trigger_source: str = "manual_button",
    ) -> HandoffResult:
        """
        请求转人工客服。

        共同逻辑：
        1. 校验 session 属于当前用户
        2. 查询 ragz_sessions
        3. 如果已有未结束 kefu_conversation_id，复用待接入/处理中会话
        4. 如果没有或旧会话已结束，创建新的 promotion_kefu_conversation
        5. 更新 ragz_sessions：
           - handoff_status = 1
           - handoff_at = NOW()
           - kefu_conversation_id
           - merge metadata
        6. 写入 promotion_kefu_message 系统记录
        7. 返回结果

        Args:
            session_id: 会话ID
            user_id: 用户ID (subject)
            reason: 转人工原因/用户原始输入
            trigger_source: 触发来源 ("manual_button" 或 "ai_detected_intent")

        Returns:
            HandoffResult 对象

        Raises:
            ValueError: session 不存在
            PermissionError: 无权访问 session
        """
        # 获取会话
        session = await self._get_session(session_id)
        if not session:
            raise ValueError("SESSION_NOT_FOUND")

        # 验证会话归属
        if session.get("user_id") != user_id:
            raise PermissionError("SESSION_ACCESS_DENIED")

        # 解析会员用户
        member_user = await self._resolve_member_user(user_id)
        if not member_user:
            raise ValueError("MEMBER_USER_NOT_FOUND")

        # 检查是否已有客服会话
        conversation = None
        created = False
        old_closed_conversation = None
        if session.get("kefu_conversation_id"):
            conversation = await self._get_conversation(int(session["kefu_conversation_id"]))

        # 如果已有会话但已关闭(status=2)，必须创建新的
        if conversation and self._conversation_status_value(conversation) == 2:
            old_closed_conversation = conversation
            conversation = None

        if conversation and self._conversation_status_value(conversation) not in (0, 1):
            conversation = None

        if not conversation:
            # 构建最后一条消息内容
            metadata = session.get("metadata") or {}
            recent_messages = metadata.get("recent_messages") or []
            last_content = ""
            if recent_messages:
                last_content = str(recent_messages[-1].get("content") or "")
            last_content = last_content or session.get("summary") or reason or "用户请求转人工客服"

            # 创建新的客服会话
            conversation = await self._create_conversation(member_user, session, last_content)
            created = True

        # 获取 handoff_status
        handoff_status = self._handoff_status_from_conversation(conversation)

        # 构建 metadata_updates（包含 handoff_history 记录）
        metadata = session.get("metadata") or {}
        requested_at = datetime.now().isoformat()
        effective_reason = reason or ("用户重新发起转人工" if old_closed_conversation else None)
        raw_history = metadata.get("handoff_history")
        handoff_history = [dict(item) for item in raw_history if isinstance(item, dict)] if isinstance(raw_history, list) else []
        history_records = []
        if old_closed_conversation:
            history_records.append({
                "conversation_id": int(old_closed_conversation["id"]),
                "status": "closed",
                "closed_at": requested_at,
            })
        if created:
            record = {
                "conversation_id": int(conversation["id"]),
                "status": "requested",
                "requested_at": requested_at,
                "trigger_source": trigger_source,
            }
            if effective_reason:
                record["reason"] = effective_reason
            history_records.append(record)
        for record in history_records:
            if not any(
                item.get("conversation_id") == record.get("conversation_id")
                and item.get("status") == record.get("status")
                for item in handoff_history
            ):
                handoff_history.append(record)

        metadata_updates = self._build_handoff_metadata(
            conversation_id=conversation["id"],
            member_user_id=member_user["id"],
            handoff_status=handoff_status,
            trigger_source=trigger_source,
            reason=effective_reason,
            handoff_history=handoff_history if history_records else None,
        )

        # 更新会话状态
        updated_session = await self._update_session_handoff(
            session["id"],
            member_user_id=member_user["id"],
            kefu_conversation_id=conversation["id"],
            handoff_status=handoff_status,
            metadata_updates=metadata_updates,
            set_handoff_at=created,
            current_metadata=session.get("metadata"),
        )

        # 关联历史消息
        await self._link_session_messages(
            session["id"],
            member_user_id=member_user["id"],
            kefu_conversation_id=conversation["id"],
        )

        # 如果是新创建，发送系统消息和事件
        if created:
            summary = session.get("summary") or "暂无摘要"
            system_message_content = f"用户请求转人工。AI 会话摘要：{summary}"
            if trigger_source == "ai_detected_intent":
                system_message_content = f"用户请求转人工（AI 自动识别）。AI 会话摘要：{summary}"
                if effective_reason:
                    system_message_content += f"\n用户原话：{effective_reason}"

            system_message = await self._insert_kefu_message(
                conversation_id=conversation["id"],
                sender_id=0,
                sender_type=self.SENDER_SYSTEM,
                receiver_id=member_user["id"],
                receiver_type=self.SENDER_USER,
                content=system_message_content,
                tenant_id=member_user.get("tenant_id") or 0,
            )

            # 发布事件通知管理员
            await publish_kefu_event({
                "event": "handoff.requested",
                "conversation_id": conversation["id"],
                "target": "all_admins",
                "conversation_status": conversation.get("status"),
                "handoff_status": handoff_status,
                "session_id": session["id"],
                "member_user_id": member_user["id"],
                "trigger_source": trigger_source,
                "reason": effective_reason,
            })
            await publish_kefu_event(self._message_event(conversation["id"], system_message, target="all_admins"))

        _logger.info(
            "[handoff] request_handoff completed",
            extra={
                "session_id": session_id,
                "kefu_conversation_id": conversation["id"],
                "handoff_status": handoff_status,
                "trigger_source": trigger_source,
                "is_created": created,
            },
        )

        return HandoffResult(
            session_id=session["id"],
            kefu_conversation_id=conversation["id"],
            handoff_status=handoff_status,
            conversation_status=conversation.get("status") or 0,
            assigned_admin_id=conversation.get("assigned_admin_id"),
            handoff_at=updated_session.get("handoff_at"),
            created=created,
            already_exists=not created,
        )


async def get_handoff_service() -> HandoffService:
    """获取 HandoffService 实例"""
    pool = get_db_pool()
    return HandoffService(pool)
