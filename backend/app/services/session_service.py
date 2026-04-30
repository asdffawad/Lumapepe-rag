"""
会话管理服务
- MySQL 持久化 + Redis 缓存（双写）
- 上下文窗口: 最近 N 轮对话摘要
- 去重: 相同问题不重复 RAG 检索
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Optional

import redis.asyncio as redis

from app.database.message import MessageRepository
from app.database.session import SessionRecord, SessionRepository
from app.cache.session_cache import SessionCache
from app.logging_config import get_logger

_logger = get_logger("session.service")


@dataclass
class SessionContext:
    session_id: str
    user_id: str
    intent: Optional[str]
    history: list[dict]
    last_query_hash: Optional[str]
    product_context: Optional[dict] = None  # 商品上下文 {sku_id, spu_id, product_info}
    member_user_id: Optional[int] = None
    tenant_id: int = 0


class SessionService:
    def __init__(self, db_pool: "aiomysql.Pool", redis_client: redis.Redis):
        # 直接在内部创建 SessionCache，不再依赖外部传入
        self._cache = SessionCache(redis_client)
        self._session_repo = SessionRepository(db_pool)
        self._message_repo = MessageRepository(db_pool)

    async def get_or_create_session(
        self,
        session_id: Optional[str],
        user_id: str,
        sku_id: Optional[int] = None,
        spu_id: Optional[int] = None,
    ) -> SessionContext:
        """获取或创建会话，返回 SessionContext。

        安全验证：session_id 已存在时，必须属于同一个 user_id，防止跨用户访问。

        商品上下文：如果提供了 sku_id 或 spu_id，会查询并注入商品信息到会话上下文。
        """
        product_context = None

        # 如果提供了商品ID，查询商品上下文
        if sku_id or spu_id:
            from app.services.product_context_service import get_product_context_service
            product_svc = get_product_context_service()
            product_context = await product_svc.get_product_context(
                sku_id=sku_id, spu_id=spu_id
            )
            _logger.info("Product context loaded", extra={"sku_id": sku_id, "spu_id": spu_id})

        if session_id:
            # 尝试从缓存读取
            cached = await self._cache.get(session_id)
            if cached:
                # 会话隔离验证：跨用户访问检测
                if cached["user_id"] != user_id:
                    raise PermissionError(
                        f"Session {session_id} does not belong to user {user_id}"
                    )
                history = await self._cache.get_history(session_id)
                return SessionContext(
                    session_id=session_id,
                    user_id=cached["user_id"],
                    intent=cached.get("intent"),
                    history=history,
                    last_query_hash=cached.get("last_query_hash"),
                    product_context=product_context,
                    member_user_id=cached.get("member_user_id"),
                    tenant_id=cached.get("tenant_id", 0),
                )
            # 尝试从 DB 读取
            db_session = await self._session_repo.find_by_id(session_id)
            if db_session:
                # 会话隔离验证：跨用户访问检测
                if db_session.user_id != user_id:
                    raise PermissionError(
                        f"Session {session_id} does not belong to user {user_id}"
                    )
                history = await self._message_repo.list_by_session(session_id)
                history_dicts = [
                    {"role": m.role, "content": m.content, "intent": m.intent}
                    for m in history
                ]
                await self._cache.set(session_id, {
                    "user_id": db_session.user_id,
                    "intent": db_session.intent,
                    "last_query_hash": db_session.summary,
                    "member_user_id": db_session.member_user_id,
                    "tenant_id": db_session.tenant_id,
                })
                return SessionContext(
                    session_id=session_id,
                    user_id=db_session.user_id,
                    intent=db_session.intent,
                    history=history_dicts,
                    last_query_hash=db_session.summary,
                    product_context=product_context,
                    member_user_id=db_session.member_user_id,
                    tenant_id=db_session.tenant_id,
                )

        # 创建新会话
        member_user_id = int(user_id) if str(user_id).isdigit() else None
        new_session = await self._session_repo.create(
            user_id,
            member_user_id=member_user_id,
            tenant_id=0,
        )
        await self._cache.set(new_session.id, {
            "user_id": user_id,
            "intent": None,
            "last_query_hash": None,
            "member_user_id": new_session.member_user_id,
            "tenant_id": new_session.tenant_id,
        })
        return SessionContext(
            session_id=new_session.id,
            user_id=user_id,
            intent=None,
            history=[],
            last_query_hash=None,
            product_context=product_context,
            member_user_id=new_session.member_user_id,
            tenant_id=new_session.tenant_id,
        )

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        intent: Optional[str] = None,
        sources: Optional[dict] = None,
        tokens: Optional[int] = None,
        latency_ms: Optional[int] = None,
    ):
        # 写入 MySQL
        msg = await self._message_repo.create(
            session_id=session_id,
            role=role,
            content=content,
            intent=intent,
            sources=sources,
            tokens=tokens,
            latency_ms=latency_ms,
        )
        # 写入 Redis
        await self._cache.append_message(session_id, {
            "role": role,
            "content": content,
            "intent": intent,
            "sources": sources,
        })
        return msg

    async def update_session(self, session_id: str, intent: Optional[str] = None):
        if intent:
            await self._session_repo.update_intent(session_id, intent)
        cached = await self._cache.get(session_id)
        if cached:
            if intent:
                cached["intent"] = intent
            await self._cache.set(session_id, cached)

    async def delete_message(self, session_id: str, message_id: int, user_id: str) -> bool:
        """删除指定消息（仅删除用户自己的消息）"""
        # 验证会话归属
        owned = await self.verify_session_ownership(session_id, user_id)
        if not owned:
            raise PermissionError("无权删除该会话的消息")

        # 删除消息
        result = await self._message_repo.delete(message_id, session_id)
        if result:
            # 清除历史缓存，下次会从数据库重新加载
            await self._cache.clear_history(session_id)
        return result

    async def mark_query_processed(self, session_id: str, query: str):
        h = hashlib.md5(query.encode()).hexdigest()
        await self._session_repo.update_summary(session_id, h)
        cached = await self._cache.get(session_id)
        if cached:
            cached["last_query_hash"] = h
            await self._cache.set(session_id, cached)

    async def get_history(self, session_id: str, limit: int = 50) -> list[dict]:
        # 优先从 Redis 读取
        history = await self._cache.get_history(session_id)
        if history:
            return history[-limit:]
        # 回退到 MySQL
        msgs = await self._message_repo.list_by_session(session_id, limit)
        return [
            {"id": m.id, "role": m.role, "content": m.content, "intent": m.intent}
            for m in msgs
        ]

    async def verify_session_ownership(self, session_id: str, user_id: str) -> bool:
        """
        验证 session 是否属于指定用户。

        Returns:
            True - session 属于该用户
            False - session 不存在或不属于该用户

        注意：不会创建新 session。
        """
        # 先查缓存
        cached = await self._cache.get(session_id)
        if cached:
            return cached["user_id"] == user_id
        # 再查数据库
        db_session = await self._session_repo.find_by_id(session_id)
        if db_session:
            return db_session.user_id == user_id
        return False
