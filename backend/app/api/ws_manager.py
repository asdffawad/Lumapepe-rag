"""
WebSocket 连接管理器（企业级）
负责管理活跃连接、会话隔离、心跳保活、连接数限制、消息限流
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Set, Optional

from fastapi import WebSocket
from app.logging_config import get_logger
from app.middleware.auth import AuthUser

_logger = get_logger("ws.manager")


@dataclass
class ConnectionInfo:
    """连接信息"""
    websocket: WebSocket
    user_id: str
    session_id: str
    connected_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)


class SlidingWindowLimiter:
    """
    滑动窗口限流器
    使用 Redis 或内存实现滑动窗口计数
    """

    def __init__(
        self,
        max_requests: int,
        window_seconds: int,
        redis_client=None,
    ):
        self._max = max_requests
        self._window = window_seconds
        self._redis = redis_client
        # 内存回退：{key: [(timestamp, count), ...]}
        self._memory: Dict[str, list] = defaultdict(list)

    async def is_allowed(self, key: str) -> tuple[bool, int]:
        """
        检查是否允许请求

        Returns:
            (是否允许, 剩余请求数)
        """
        now = time.time()
        window_start = now - self._window

        if self._redis is not None:
            try:
                r = self._redis.client()
                redis_key = f"ws:ratelimit:{key}"
                unique_id = f"{now}:{id(self)}"

                # 使用同步 Redis 操作（更可靠）
                pipe = r.pipeline()
                pipe.zremrangebyscore(redis_key, 0, window_start)
                pipe.zadd(redis_key, {unique_id: now})
                pipe.zcard(redis_key)
                pipe.expire(redis_key, self._window + 1)
                results = await pipe.execute()

                count = results[2]  # zcard 的结果在索引 2
                allowed = count <= self._max
                remaining = max(0, self._max - count)
                return allowed, remaining
            except Exception as e:
                _logger.warning(f"Redis rate limit failed, using memory fallback: {e}")

        # 内存回退
        return self._memory_fallback(key, now, window_start)

    def _memory_fallback(self, key: str, now: float, window_start: float) -> tuple[bool, int]:
        """内存滑动窗口回退"""
        timestamps = self._memory[key]
        # 清理过期时间戳
        self._memory[key] = [ts for ts in timestamps if ts > window_start]
        # 添加当前请求
        self._memory[key].append(now)
        count = len(self._memory[key])
        allowed = count <= self._max
        remaining = max(0, self._max - count)
        return allowed, remaining


class ConnectionManager:
    """
    WebSocket 连接管理器（企业级）

    功能:
    - Bearer Token 认证
    - 连接数限制（单用户/全局）
    - 会话隔离验证
    - 心跳保活
    - 消息限流
    """

    _instance: Optional[ConnectionManager] = None

    def __new__(cls, config=None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, config=None):
        if self._initialized:
            return
        self._initialized = True

        # 配置
        from app.config import get_config
        cfg = config or get_config().websocket
        self._max_per_user = cfg.max_per_user
        self._max_global = cfg.max_global
        self._heartbeat_interval = cfg.heartbeat_interval
        self._heartbeat_timeout = cfg.heartbeat_timeout
        self._rate_limit_per_user = cfg.rate_limit_per_user
        self._rate_limit_per_conn = cfg.rate_limit_per_conn
        self._rate_limit_window = cfg.rate_limit_window
        self._max_message_size = cfg.max_message_size

        # 连接存储
        # session_id -> ConnectionInfo
        self._connections: Dict[str, ConnectionInfo] = {}
        # user_id -> set[session_id]
        self._user_sessions: Dict[str, Set[str]] = defaultdict(set)
        # session_id -> user_id (反向映射)
        self._session_users: Dict[str, str] = {}

        # 全局连接计数
        self._global_count: int = 0

        # 限流器
        self._user_limiter: Optional[SlidingWindowLimiter] = None
        self._conn_limiter: Optional[SlidingWindowLimiter] = None

        # 心跳任务
        self._heartbeat_task: Optional[asyncio.Task] = None

        # 锁
        self._lock = asyncio.Lock()

    def _get_limiter(self) -> tuple[SlidingWindowLimiter, SlidingWindowLimiter]:
        """获取限流器（延迟初始化）"""
        if self._user_limiter is None:
            from app.cache.redis_client import get_redis_client
            redis_client = None
            try:
                redis_client = get_redis_client()
            except Exception:
                pass
            self._user_limiter = SlidingWindowLimiter(
                self._rate_limit_per_user,
                self._rate_limit_window,
                redis_client,
            )
            self._conn_limiter = SlidingWindowLimiter(
                self._rate_limit_per_conn,
                self._rate_limit_window,
                redis_client,
            )
        return self._user_limiter, self._conn_limiter

    async def check_connection_limits(self, user_id: str) -> tuple[bool, str, int]:
        """
        检查连接数限制

        Returns:
            (是否允许, 错误码, 错误消息)
        """
        async with self._lock:
            # 检查全局限制
            if self._global_count >= self._max_global:
                return False, "SERVER_FULL", "服务器连接数已满，请稍后重试"

            # 检查单用户限制
            user_conn_count = len(self._user_sessions.get(user_id, set()))
            if user_conn_count >= self._max_per_user:
                return False, "TOO_MANY_CONNECTIONS", f"用户连接数超限（最大 {self._max_per_user} 个），请关闭其他连接后重试"

            return True, "", ""

    async def check_rate_limit(self, user_id: str, session_id: str) -> tuple[bool, int, int]:
        """
        检查消息限流

        Returns:
            (是否允许, 剩余用户配额, 剩余连接配额)
        """
        user_limiter, conn_limiter = self._get_limiter()

        user_allowed, user_remaining = await user_limiter.is_allowed(f"user:{user_id}")
        if not user_allowed:
            return False, 0, 0

        conn_allowed, conn_remaining = await conn_limiter.is_allowed(f"conn:{session_id}")
        if not conn_allowed:
            return False, user_remaining, 0

        return True, user_remaining, conn_remaining

    async def connect(self, websocket: WebSocket, session_id: str, user_id: str) -> tuple[bool, str, str]:
        """
        建立 WebSocket 连接

        Returns:
            (是否成功, 错误码, 错误消息)
        """
        # 先检查限制
        allowed, code, msg = await self.check_connection_limits(user_id)
        if not allowed:
            return False, code, msg

        # 在锁内完成 accept 和注册（防止竞态条件）
        async with self._lock:
            _logger.info("WS Manager connect called", extra={
                "session_id": session_id,
                "user_id": user_id,
                "global_count": self._global_count,
            })
            # 再次检查（乐观锁模式）
            if self._global_count >= self._max_global:
                return False, "SERVER_FULL", "服务器连接数已满，请稍后重试"
            user_conn_count = len(self._user_sessions.get(user_id, set()))
            if user_conn_count >= self._max_per_user:
                return False, "TOO_MANY_CONNECTIONS", f"用户连接数超限（最大 {self._max_per_user} 个），请关闭其他连接后重试"

            # 注意: websocket.accept() 已在 websocket.py 中调用，这里不需要重复调用
            # 重复调用 accept() 会导致 500 错误

            info = ConnectionInfo(
                websocket=websocket,
                user_id=user_id,
                session_id=session_id,
            )
            self._connections[session_id] = info
            self._session_users[session_id] = user_id
            self._user_sessions[user_id].add(session_id)
            self._global_count += 1
            _logger.info(f"Connection registered: {session_id}, initial last_activity={info.last_activity}")

        _logger.info(
            "WebSocket connected",
            extra={
                "session_id": session_id,
                "user_id": user_id,
                "global_count": self._global_count,
                "user_count": len(self._user_sessions.get(user_id, set())),
            }
        )

        # 启动心跳
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        return True, "", ""

    async def disconnect(self, session_id: str) -> None:
        """断开 WebSocket 连接"""
        async with self._lock:
            info = self._connections.pop(session_id, None)
            user_id = self._session_users.pop(session_id, None)

            if user_id and user_id in self._user_sessions:
                self._user_sessions[user_id].discard(session_id)
                if not self._user_sessions[user_id]:
                    del self._user_sessions[user_id]

            if info:
                self._global_count = max(0, self._global_count - 1)

        if info:
            try:
                await info.websocket.close()
            except Exception:
                pass

            _logger.info(
                "WebSocket disconnected",
                extra={
                    "session_id": session_id,
                    "user_id": user_id,
                    "global_count": self._global_count,
                }
            )

    async def send_json(self, session_id: str, data: dict) -> bool:
        """发送 JSON 消息到指定会话"""
        info = self._connections.get(session_id)
        if info is None:
            return False

        try:
            await info.websocket.send_json(data)
            info.last_activity = time.time()
            return True
        except Exception as e:
            _logger.warning(
                "Failed to send message",
                extra={"session_id": session_id, "error": str(e)}
            )
            await self.disconnect(session_id)
            return False

    async def broadcast(self, user_id: str, data: dict) -> int:
        """向指定用户的所有会话广播消息"""
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
        return self._user_sessions.get(user_id, set()).copy()

    def get_active_count(self) -> int:
        """获取全局活跃连接数"""
        return self._global_count

    def get_user_count(self, user_id: str) -> int:
        """获取用户的活跃连接数"""
        return len(self._user_sessions.get(user_id, set()))

    async def notify_shutdown(self, message: str = "服务即将重启，请重新连接") -> None:
        """通知所有连接优雅关闭"""
        shutdown_msg = {"type": "shutdown", "message": message}
        for session_id in list(self._connections.keys()):
            await self.send_json(session_id, shutdown_msg)

    async def update_activity(self, session_id: str) -> bool:
        """
        更新连接的最后活动时间

        Args:
            session_id: 会话 ID

        Returns:
            是否成功更新
        """
        async with self._lock:
            info = self._connections.get(session_id)
            if info:
                old_time = info.last_activity
                info.last_activity = time.time()
                _logger.debug(f"Activity updated for {session_id}: {old_time:.3f} -> {info.last_activity:.3f}")
                return True
            _logger.warning(f"update_activity failed: session {session_id} not found")
            return False

    async def _heartbeat_loop(self) -> None:
        """心跳保活循环"""
        _logger.info(f"Heartbeat loop started, interval={self._heartbeat_interval}s")
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            now = time.time()
            dead_sessions = []

            async with self._lock:
                active_count = len(self._connections)
                _logger.info(f"Heartbeat tick: checking {active_count} connections, timeout={self._heartbeat_timeout}s")

                for session_id, info in list(self._connections.items()):
                    elapsed = now - info.last_activity
                    _logger.debug(f"Session {session_id}: last_activity={info.last_activity}, elapsed={elapsed:.1f}s")
                    if elapsed > self._heartbeat_timeout:
                        _logger.info(f"Session timeout: {session_id}, elapsed={elapsed:.1f}s > {self._heartbeat_timeout}s")
                        dead_sessions.append(session_id)
                    else:
                        try:
                            await info.websocket.send_json({"type": "ping"})
                            # 发送 ping 后更新最后活动时间，防止被误判为死连接
                            info.last_activity = now
                            _logger.debug(f"Sent heartbeat ping to {session_id}, updated last_activity to {now}")
                        except Exception as e:
                            _logger.warning(f"Heartbeat send failed for {session_id}: {e}")
                            dead_sessions.append(session_id)

            for session_id in dead_sessions:
                await self.disconnect(session_id)

            if not self._connections:
                _logger.info("All connections gone, heartbeat loop ending")
                break


# 全局单例（使用配置初始化）
_manager: Optional[ConnectionManager] = None


def get_connection_manager(config=None) -> ConnectionManager:
    """获取 ConnectionManager 单例"""
    global _manager
    if _manager is None:
        _manager = ConnectionManager(config)
    return _manager


def reset_connection_manager() -> None:
    """重置连接管理器（用于测试）"""
    global _manager
    _manager = None
