"""
WebSocket 连接管理器
负责管理活跃连接、会话隔离、心跳保活
"""
from __future__ import annotations

import asyncio
from typing import Dict, Set, Optional
from fastapi import WebSocket
from app.logging_config import get_logger

_logger = get_logger("ws.manager")


class ConnectionManager:
    """
    单例模式的 WebSocket 连接管理器
    
    功能:
    - session_id -> WebSocket 映射
    - user_id -> session_ids 映射（用于清理）
    - 会话隔离验证
    - 心跳保活
    """
    
    _instance: Optional[ConnectionManager] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        # session_id -> WebSocket
        self._connections: Dict[str, WebSocket] = {}
        # user_id -> set[session_id]
        self._user_sessions: Dict[str, Set[str]] = {}
        # session_id -> user_id (反向映射)
        self._session_users: Dict[str, str] = {}
        # 心跳任务
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._heartbeat_interval = 30  # 秒
    
    async def connect(self, websocket: WebSocket, session_id: str, user_id: str) -> None:
        """
        建立 WebSocket 连接
        
        Args:
            websocket: WebSocket 连接
            session_id: 会话 ID
            user_id: 用户 ID
        """
        await websocket.accept()
        self._connections[session_id] = websocket
        self._session_users[session_id] = user_id
        
        if user_id not in self._user_sessions:
            self._user_sessions[user_id] = set()
        self._user_sessions[user_id].add(session_id)
        
        _logger.info(
            "WebSocket connected",
            extra={"session_id": session_id, "user_id": user_id}
        )
        
        # 启动心跳
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
    
    async def disconnect(self, session_id: str) -> None:
        """
        断开 WebSocket 连接
        
        Args:
            session_id: 会话 ID
        """
        websocket = self._connections.pop(session_id, None)
        user_id = self._session_users.pop(session_id, None)
        
        if user_id and user_id in self._user_sessions:
            self._user_sessions[user_id].discard(session_id)
            if not self._user_sessions[user_id]:
                del self._user_sessions[user_id]
        
        if websocket:
            try:
                await websocket.close()
            except Exception:
                pass
        
        _logger.info(
            "WebSocket disconnected",
            extra={"session_id": session_id, "user_id": user_id}
        )
    
    async def send_json(self, session_id: str, data: dict) -> bool:
        """
        发送 JSON 消息到指定会话
        
        Args:
            session_id: 会话 ID
            data: 要发送的数据
            
        Returns:
            bool: 发送是否成功
        """
        websocket = self._connections.get(session_id)
        if websocket is None:
            return False
        
        try:
            await websocket.send_json(data)
            return True
        except Exception as e:
            _logger.warning(
                "Failed to send message",
                extra={"session_id": session_id, "error": str(e)}
            )
            await self.disconnect(session_id)
            return False
    
    async def broadcast(self, user_id: str, data: dict) -> int:
        """
        向指定用户的所有会话广播消息
        
        Args:
            user_id: 用户 ID
            data: 要发送的数据
            
        Returns:
            int: 成功发送的数量
        """
        session_ids = self._user_sessions.get(user_id, set())
        count = 0
        for session_id in list(session_ids):
            if await self.send_json(session_id, data):
                count += 1
        return count
    
    def is_connected(self, session_id: str) -> bool:
        """检查会话是否已连接"""
        return session_id in self._connections
    
    def get_user_sessions(self, user_id: str) -> Set[str]:
        """获取用户的所有会话 ID"""
        return self._user_sessions.get(user_id, set())
    
    def get_active_count(self) -> int:
        """获取活跃连接数"""
        return len(self._connections)
    
    async def _heartbeat_loop(self) -> None:
        """心跳保活循环"""
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            # 发送心跳并清理断开连接
            dead_sessions = []
            for session_id, websocket in list(self._connections.items()):
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    dead_sessions.append(session_id)
            
            for session_id in dead_sessions:
                await self.disconnect(session_id)
            
            if not self._connections:
                break


# 全局单例
_manager: Optional[ConnectionManager] = None


def get_connection_manager() -> ConnectionManager:
    """获取 ConnectionManager 单例"""
    global _manager
    if _manager is None:
        _manager = ConnectionManager()
    return _manager
