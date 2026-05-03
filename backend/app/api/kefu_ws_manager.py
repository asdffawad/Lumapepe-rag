"""Process-local WebSocket connection manager for human kefu conversations."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import WebSocket

from app.logging_config import get_logger

_logger = get_logger("kefu.ws")


class KefuConnectionManager:
    def __init__(self):
        self.user_connections: dict[int, list[WebSocket]] = defaultdict(list)
        self.admin_connections: dict[int, list[WebSocket]] = defaultdict(list)
        self._connection_index: dict[WebSocket, tuple[str, int]] = {}

    async def connect_user(self, websocket: WebSocket, conversation_id: int) -> None:
        cid = int(conversation_id)
        if websocket not in self.user_connections[cid]:
            self.user_connections[cid].append(websocket)
        self._connection_index[websocket] = ("user", cid)
        _logger.info(
            "[kefu] user websocket connected to room",
            extra={"conversation_id": cid, "user_connection_count": len(self.user_connections[cid])},
        )

    async def connect_admin(self, websocket: WebSocket, conversation_id: int) -> None:
        cid = int(conversation_id)
        if websocket not in self.admin_connections[cid]:
            self.admin_connections[cid].append(websocket)
        self._connection_index[websocket] = ("admin", cid)
        _logger.info(
            "[kefu] admin websocket connected to room",
            extra={"conversation_id": cid, "admin_connection_count": len(self.admin_connections[cid])},
        )

    def disconnect(self, websocket: WebSocket) -> None:
        indexed = self._connection_index.pop(websocket, None)
        if not indexed:
            return
        role, conversation_id = indexed
        bucket = self.user_connections if role == "user" else self.admin_connections
        connections = bucket.get(conversation_id, [])
        if websocket in connections:
            connections.remove(websocket)
        if not connections and conversation_id in bucket:
            bucket.pop(conversation_id, None)

    async def send_to_user(self, conversation_id: int, payload: dict[str, Any]) -> None:
        cid = int(conversation_id)
        await self._send_many("user", cid, self.user_connections.get(cid, []), payload)

    async def send_to_admin(self, conversation_id: int, payload: dict[str, Any]) -> None:
        cid = int(conversation_id)
        await self._send_many("admin", cid, self.admin_connections.get(cid, []), payload)

    async def broadcast_conversation(self, conversation_id: int, payload: dict[str, Any]) -> None:
        cid = int(conversation_id)
        await self._send_many("user", cid, self.user_connections.get(cid, []), payload)
        await self._send_many("admin", cid, self.admin_connections.get(cid, []), payload)

    async def dispatch_event(self, event: dict[str, Any]) -> dict[str, int]:
        cid = int(event.get("conversation_id") or 0)
        target = str(event.get("target") or "both")
        payload = self._frontend_payload(event)
        user_count = 0
        admin_count = 0

        if target in ("user", "both"):
            user_count = await self._send_many("user", cid, self.user_connections.get(cid, []), payload)
        if target in ("admin", "both"):
            admin_count = await self._send_many("admin", cid, self.admin_connections.get(cid, []), payload)
        if target == "all_admins":
            admin_count = await self._send_many("admin", cid, self._all_admin_connections(), payload)

        _logger.info(
            "[kefu] local dispatch done",
            extra={
                "conversation_id": cid,
                "event": event.get("event"),
                "target": target,
                "user_count": user_count,
                "admin_count": admin_count,
            },
        )
        return {"user_count": user_count, "admin_count": admin_count}

    def local_counts(self, conversation_id: int | None = None) -> dict[str, int]:
        if conversation_id is not None:
            cid = int(conversation_id)
            return {
                "user_connections": len(self.user_connections.get(cid, [])),
                "admin_connections": len(self.admin_connections.get(cid, [])),
            }
        return {
            "user_connections": sum(len(items) for items in self.user_connections.values()),
            "admin_connections": sum(len(items) for items in self.admin_connections.values()),
        }

    def _frontend_payload(self, event: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in event.items()
            if key not in {"target", "source", "source_instance_id", "created_at"}
        }

    def _all_admin_connections(self) -> list[WebSocket]:
        seen: set[WebSocket] = set()
        result: list[WebSocket] = []
        for connections in self.admin_connections.values():
            for websocket in connections:
                if websocket not in seen:
                    seen.add(websocket)
                    result.append(websocket)
        return result

    async def _send_many(self, role: str, conversation_id: int, websockets: list[WebSocket], payload: dict[str, Any]) -> int:
        stale: list[WebSocket] = []
        sent = 0
        event = payload.get("event") if isinstance(payload, dict) else None
        _logger.info(
            "[kefu] websocket room send start",
            extra={"conversation_id": conversation_id, "target_role": role, "target_count": len(websockets), "event": event},
        )
        for websocket in list(websockets):
            try:
                await websocket.send_json(payload)
                sent += 1
            except Exception as exc:
                _logger.warning(
                    "[kefu] websocket room send failed",
                    extra={
                        "conversation_id": conversation_id,
                        "target_role": role,
                        "event": event,
                        "error": str(exc),
                    },
                )
                stale.append(websocket)
        for websocket in stale:
            self.disconnect(websocket)
        _logger.info(
            "[kefu] websocket room send done",
            extra={
                "conversation_id": conversation_id,
                "target_role": role,
                "target_count": len(websockets),
                "sent_count": sent,
                "stale_count": len(stale),
                "event": event,
            },
        )
        return sent


_kefu_connection_manager = KefuConnectionManager()


def get_kefu_connection_manager() -> KefuConnectionManager:
    return _kefu_connection_manager
