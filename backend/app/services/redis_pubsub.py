"""Redis Pub/Sub fan-out for kefu WebSocket events."""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import redis.asyncio as redis

from app.api.kefu_ws_manager import KefuConnectionManager, get_kefu_connection_manager
from app.logging_config import get_logger


KEFU_EVENTS_CHANNEL = "kefu:events"
_logger = get_logger("redis-pubsub")


def mask_redis_url(redis_url: str) -> str:
    try:
        parsed = urlsplit(redis_url)
        if "@" not in parsed.netloc:
            return redis_url
        userinfo, hostinfo = parsed.netloc.rsplit("@", 1)
        if ":" in userinfo:
            username, _password = userinfo.split(":", 1)
            userinfo = f"{username}:***"
        else:
            userinfo = "***"
        return urlunsplit((parsed.scheme, f"{userinfo}@{hostinfo}", parsed.path, parsed.query, parsed.fragment))
    except Exception:
        return "redis://***"


class RedisPubSubManager:
    def __init__(
        self,
        redis_url: str,
        *,
        channel: str = KEFU_EVENTS_CHANNEL,
        local_manager: KefuConnectionManager | None = None,
        redis_client: redis.Redis | None = None,
        instance_id: str | None = None,
        redis_required: bool = False,
    ):
        self.redis_url = redis_url
        self.channel = channel
        self.local_manager = local_manager or get_kefu_connection_manager()
        self.redis_required = redis_required
        self.instance_id = instance_id or f"{uuid.uuid4()}:{os.getpid()}"
        self._redis = redis_client
        self._subscriber_redis: redis.Redis | None = None
        self._connected = redis_client is not None
        self._socket_timeout = float(os.getenv("REDIS_PUBSUB_SOCKET_TIMEOUT", "0.5"))
        self._socket_connect_timeout = float(os.getenv("REDIS_PUBSUB_CONNECT_TIMEOUT", "0.5"))
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self._pubsub = None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def subscriber_running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def sanitized_redis_url(self) -> str:
        return mask_redis_url(self.redis_url)

    async def connect(self) -> bool:
        try:
            if self._redis is None:
                _logger.info("[redis-pubsub] connecting redis_url=%s", self.sanitized_redis_url)
                self._redis = self._create_redis_client()
            await self._redis.ping()
            self._connected = True
            self._stopping.clear()
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self.subscribe_loop())
            _logger.info(
                "[redis-pubsub] connected",
                extra={"redis_url": self.sanitized_redis_url, "instance_id": self.instance_id},
            )
            return True
        except Exception as exc:
            self._connected = False
            _logger.warning(
                "[redis-pubsub] connect failed",
                extra={"redis_url": self.sanitized_redis_url, "instance_id": self.instance_id, "error": str(exc)},
            )
            if self.redis_required:
                raise
            return False

    async def disconnect(self) -> None:
        self._stopping.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        if self._pubsub is not None:
            try:
                await self._pubsub.close()
            except Exception:
                pass
            self._pubsub = None
        if self._redis is not None:
            try:
                await self._redis.close()
            except Exception:
                pass
            self._redis = None
        if self._subscriber_redis is not None:
            try:
                await self._subscriber_redis.close()
            except Exception:
                pass
            self._subscriber_redis = None
        self._connected = False

    async def publish_event(self, event: dict[str, Any]) -> bool:
        try:
            if self._redis is None:
                self._redis = self._create_redis_client()
            payload = dict(event)
            payload.setdefault("target", "both")
            payload["source"] = "redis_pubsub"
            payload["source_instance_id"] = self.instance_id
            payload["created_at"] = datetime.now(timezone.utc).isoformat()
            await self._redis.publish(self.channel, json.dumps(payload, ensure_ascii=False, default=str))
            _logger.info(
                "[redis-pubsub] publish event=%s conversation_id=%s target=%s",
                payload.get("event"),
                payload.get("conversation_id"),
                payload.get("target"),
                extra={
                    "instance_id": self.instance_id,
                    "channel": self.channel,
                    "event": payload.get("event"),
                    "conversation_id": payload.get("conversation_id"),
                    "target": payload.get("target"),
                },
            )
            return True
        except Exception as exc:
            self._connected = False
            _logger.warning(
                "[redis-pubsub] publish failed",
                extra={
                    "instance_id": self.instance_id,
                    "channel": self.channel,
                    "event": event.get("event"),
                    "conversation_id": event.get("conversation_id"),
                    "target": event.get("target"),
                    "error": str(exc),
                },
            )
            return False

    async def subscribe_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                if self._subscriber_redis is None:
                    self._subscriber_redis = self._create_subscriber_client()
                self._pubsub = self._subscriber_redis.pubsub()
                await self._pubsub.subscribe(self.channel)
                self._connected = True
                _logger.info(
                    "[redis-pubsub] subscribed channel=%s",
                    self.channel,
                    extra={"instance_id": self.instance_id, "channel": self.channel},
                )
                async for message in self._pubsub.listen():
                    if self._stopping.is_set():
                        break
                    if not isinstance(message, dict) or message.get("type") != "message":
                        continue
                    raw = message.get("data")
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    try:
                        event = json.loads(raw)
                    except Exception as exc:
                        _logger.warning("[redis-pubsub] invalid json event", extra={"error": str(exc)})
                        continue
                    await self.handle_event(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._connected = False
                _logger.warning(
                    "[redis-pubsub] redis disconnected, retrying...",
                    extra={"instance_id": self.instance_id, "error": str(exc)},
                )
                try:
                    if self._pubsub is not None:
                        await self._pubsub.close()
                except Exception:
                    pass
                self._pubsub = None
                try:
                    if self._subscriber_redis is not None:
                        await self._subscriber_redis.close()
                except Exception:
                    pass
                self._subscriber_redis = None
                await asyncio.sleep(2)

    async def handle_event(self, event: dict[str, Any]) -> dict[str, int]:
        _logger.info(
            "[redis-pubsub] received event=%s conversation_id=%s target=%s",
            event.get("event"),
            event.get("conversation_id"),
            event.get("target"),
            extra={
                "instance_id": self.instance_id,
                "source_instance_id": event.get("source_instance_id"),
                "conversation_id": event.get("conversation_id"),
                "target": event.get("target"),
            },
        )
        result = await self.local_manager.dispatch_event(event)
        _logger.info(
            "[redis-pubsub] local push done user_count=%s admin_count=%s",
            result.get("user_count", 0),
            result.get("admin_count", 0),
            extra={"instance_id": self.instance_id, "conversation_id": event.get("conversation_id")},
        )
        return result

    def _create_redis_client(self) -> redis.Redis:
        return redis.Redis.from_url(
            self.redis_url,
            decode_responses=True,
            socket_timeout=self._socket_timeout,
            socket_connect_timeout=self._socket_connect_timeout,
        )

    def _create_subscriber_client(self) -> redis.Redis:
        return redis.Redis.from_url(
            self.redis_url,
            decode_responses=True,
            socket_connect_timeout=self._socket_connect_timeout,
            health_check_interval=30,
        )


_redis_pubsub_manager: RedisPubSubManager | None = None


def get_redis_pubsub_manager(
    redis_url: str | None = None,
    *,
    redis_required: bool | None = None,
) -> RedisPubSubManager:
    global _redis_pubsub_manager
    if _redis_pubsub_manager is None:
        _redis_pubsub_manager = RedisPubSubManager(
            redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            redis_required=(os.getenv("REDIS_REQUIRED", "false").lower() in ("1", "true", "yes"))
            if redis_required is None
            else redis_required,
        )
    return _redis_pubsub_manager


async def publish_kefu_event(event: dict[str, Any]) -> bool:
    manager = get_redis_pubsub_manager()
    published = await manager.publish_event(event)
    if published and manager.subscriber_running:
        return True
    await manager.local_manager.dispatch_event(event)
    return published
