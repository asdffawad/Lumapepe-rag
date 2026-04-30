"""
In-memory WebSocket connection manager for human kefu conversations.

This MVP stores live connections in the current process only. Multi-instance
deployments should replace the fan-out layer with Redis Pub/Sub or a message
queue so events are delivered across backend replicas.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import WebSocket


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

    async def connect_admin(self, websocket: WebSocket, conversation_id: int) -> None:
        cid = int(conversation_id)
        if websocket not in self.admin_connections[cid]:
            self.admin_connections[cid].append(websocket)
        self._connection_index[websocket] = ("admin", cid)

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
        await self._send_many(self.user_connections.get(int(conversation_id), []), payload)

    async def send_to_admin(self, conversation_id: int, payload: dict[str, Any]) -> None:
        await self._send_many(self.admin_connections.get(int(conversation_id), []), payload)

    async def broadcast_conversation(self, conversation_id: int, payload: dict[str, Any]) -> None:
        cid = int(conversation_id)
        await self._send_many(self.user_connections.get(cid, []), payload)
        await self._send_many(self.admin_connections.get(cid, []), payload)

    async def _send_many(self, websockets: list[WebSocket], payload: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for websocket in list(websockets):
            try:
                await websocket.send_json(payload)
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            self.disconnect(websocket)


_kefu_connection_manager = KefuConnectionManager()


def get_kefu_connection_manager() -> KefuConnectionManager:
    return _kefu_connection_manager
