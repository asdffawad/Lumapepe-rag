# WebSocket 企业级改造实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将项目从 REST API 通信升级为纯 WebSocket 通信方式，实现企业级的 Bearer Token 认证、连接数限制、消息限流。

**Architecture:** 使用 FastAPI/Starlette 原生 WebSocket 支持，通过 ConnectionManager 单例管理所有连接。在 WebSocket 握手阶段通过 `Authorization` Header 验证 Bearer Token，连接时检查用户连接数和全局连接数上限，运行时使用滑动窗口限流。

**Tech Stack:** FastAPI, Starlette WebSocket, Redis (滑动窗口限流), asyncio

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `backend/app/middleware/auth.py` | 修改 | 添加 `verify_ws_token()` 函数 |
| `backend/app/config.py` | 修改 | 添加 WebSocket 配置项 |
| `backend/app/api/ws_manager.py` | 重写 | 连接管理 + 连接数限制 + 心跳 + 限流 |
| `backend/app/api/websocket.py` | 修改 | 使用新认证 + 新连接管理 + 限流 |
| `backend/app/api/chat.py` | 修改 | 标记废弃接口 |
| `backend/app/models/request.py` | 可能修改 | 请求模型扩展（如需要） |
| `backend/tests/test_ws_auth.py` | 新建 | WS 认证测试 |
| `backend/tests/test_ws_manager.py` | 新建 | 连接管理测试 |
| `backend/tests/test_websocket.py` | 新建 | WS 集成测试 |

---

## 详细任务

### Task 1: 添加 WebSocket 配置项

**Files:**
- Modify: `backend/app/config.py`

- [ ] **Step 1: 修改 AppConfig 添加 WebSocket 配置**

在 `backend/app/config.py` 的 `AppConfig` 类后添加 WebSocket 配置类：

```python
@dataclass
class WebSocketConfig:
    max_per_user: int = 5           # 单用户最大连接数
    max_global: int = 10000         # 全局最大连接数
    heartbeat_interval: int = 30    # 心跳间隔（秒）
    heartbeat_timeout: int = 60     # 心跳超时（秒）
    rate_limit_per_user: int = 30   # 用户消息限流（条/分钟）
    rate_limit_per_conn: int = 20   # 连接消息限流（条/分钟）
    rate_limit_window: int = 60     # 限流窗口（秒）
    max_message_size: int = 65536   # 单条消息最大字节数（64KB）
```

在 `AppConfig` 类中添加：
```python
websocket: WebSocketConfig = field(default_factory=WebSocketConfig)
```

在 `get_config()` 函数中添加：
```python
websocket=WebSocketConfig(
    max_per_user=int(os.getenv("WS_MAX_PER_USER", "5")),
    max_global=int(os.getenv("WS_MAX_GLOBAL", "10000")),
    heartbeat_interval=int(os.getenv("WS_HEARTBEAT_INTERVAL", "30")),
    heartbeat_timeout=int(os.getenv("WS_HEARTBEAT_TIMEOUT", "60")),
    rate_limit_per_user=int(os.getenv("WS_RATE_LIMIT_PER_USER", "30")),
    rate_limit_per_conn=int(os.getenv("WS_RATE_LIMIT_PER_CONN", "20")),
    rate_limit_window=int(os.getenv("WS_RATE_LIMIT_WINDOW", "60")),
    max_message_size=int(os.getenv("WS_MAX_MESSAGE_SIZE", "65536")),
),
```

- [ ] **Step 2: 验证配置**

Run: `cd backend && python -c "from app.config import get_config; c = get_config(); print(f'WS max_per_user: {c.websocket.max_per_user}, max_global: {c.websocket.max_global}')"`
Expected: 输出 `WS max_per_user: 5, max_global: 10000`

- [ ] **Step 3: Commit**

```bash
git add backend/app/config.py
git commit -m "feat(config): add WebSocket configuration options"
```

---

### Task 2: 添加 WebSocket 认证函数

**Files:**
- Modify: `backend/app/middleware/auth.py`

- [ ] **Step 1: 添加 WebSocket Token 验证函数**

在 `backend/app/middleware/auth.py` 文件末尾添加：

```python
# ---------------------------------------------------------------------------
# WebSocket 认证
# ---------------------------------------------------------------------------

def extract_bearer_from_headers(headers: dict) -> str | None:
    """
    从 WebSocket headers 中提取 Bearer Token

    支持两种方式：
    1. Authorization: Bearer <token>
    2. Sec-WebSocket-Protocol: bearer, <token>

    Returns:
        Token 字符串，如果不存在返回 None
    """
    # 方式1: Authorization Header
    auth = headers.get("authorization", "") or headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    if auth.startswith("bearer "):
        return auth[7:].strip()

    # 方式2: Sec-WebSocket-Protocol
    protocol = headers.get("sec-websocket-protocol", "") or headers.get("Sec-WebSocket-Protocol", "")
    if protocol.startswith("bearer,"):
        return protocol[7:].strip()
    if protocol.startswith("bearer "):
        return protocol[7:].strip()

    return None


async def verify_ws_token(headers: dict) -> AuthUser:
    """
    验证 WebSocket 连接的 Bearer Token

    Raises:
        JWTError: Token 无效或已过期
    """
    token = extract_bearer_from_headers(headers)
    if not token:
        raise JWTError("Missing authorization token")

    return decode_token(token)
```

- [ ] **Step 2: 运行现有测试确保没有破坏**

Run: `cd backend && python -m pytest tests/test_auth.py -v`
Expected: 所有测试通过

- [ ] **Step 3: Commit**

```bash
git add backend/app/middleware/auth.py
git commit -m "feat(auth): add WebSocket token verification functions"
```

---

### Task 3: 重写连接管理器 (ws_manager.py)

**Files:**
- Modify: `backend/app/api/ws_manager.py`

- [ ] **Step 1: 重写 ConnectionManager 类**

将 `backend/app/api/ws_manager.py` 完全重写为：

```python
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
                # ZADD score=timestamp, member=unique_id
                unique_id = f"{now}:{id(self)}"
                pipe = r.pipeline()
                pipe.zremrangebyscore(redis_key, 0, window_start)
                pipe.zadd(redis_key, {unique_id: now})
                pipe.zcard(redis_key)
                pipe.expire(redis_key, self._window + 1)
                results = await r.async_pipeline(pipe) if hasattr(r, 'async_pipeline') else self._redis_fallback(r, redis_key, now, window_start, unique_id)

                count = results[2] if len(results) >= 3 else self._memory_fallback(key, now, window_start)
                allowed = count <= self._max
                remaining = max(0, self._max - count)
                return allowed, remaining
            except Exception:
                pass

        # 内存回退
        return self._memory_fallback(key, now, window_start)

    def _redis_fallback(self, r, redis_key, now, window_start, unique_id):
        """Redis 操作回退"""
        r.zremrangebyscore(redis_key, 0, window_start)
        r.zadd(redis_key, {unique_id: now})
        r.zcard(redis_key)
        r.expire(redis_key, self._window + 1)
        return r.zcard(redis_key)

    def _memory_fallback(self, key: str, now: float, window_start: float) -> int:
        """内存滑动窗口回退"""
        timestamps = self._memory[key]
        # 清理过期时间戳
        self._memory[key] = [ts for ts in timestamps if ts > window_start]
        # 添加当前请求
        self._memory[key].append(now)
        return len(self._memory[key])


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
        # 检查限制
        allowed, code, msg = await self.check_connection_limits(user_id)
        if not allowed:
            return False, code, msg

        await websocket.accept()

        async with self._lock:
            info = ConnectionInfo(
                websocket=websocket,
                user_id=user_id,
                session_id=session_id,
            )
            self._connections[session_id] = info
            self._session_users[session_id] = user_id
            self._user_sessions[user_id].add(session_id)
            self._global_count += 1

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

    async def _heartbeat_loop(self) -> None:
        """心跳保活循环"""
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            now = time.time()
            dead_sessions = []

            async with self._lock:
                for session_id, info in list(self._connections.items()):
                    if now - info.last_activity > self._heartbeat_timeout:
                        dead_sessions.append(session_id)
                    else:
                        try:
                            await info.websocket.send_json({"type": "ping"})
                        except Exception:
                            dead_sessions.append(session_id)

            for session_id in dead_sessions:
                await self.disconnect(session_id)

            if not self._connections:
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
```

- [ ] **Step 2: 运行基本语法检查**

Run: `cd backend && python -c "from app.api.ws_manager import ConnectionManager, SlidingWindowLimiter; print('OK')"`
Expected: 输出 `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/ws_manager.py
git commit -m "feat(ws): rewrite connection manager with rate limiting and connection limits"
```

---

### Task 4: 修改 WebSocket 端点

**Files:**
- Modify: `backend/app/api/websocket.py`

- [ ] **Step 1: 修改 WebSocket 端点**

将 `backend/app/api/websocket.py` 中的 `websocket_chat` 函数修改为：

```python
@router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket, session_id: Optional[str] = Query(None)):
    """
    WebSocket 聊天端点（企业级）

    认证：使用 Authorization Header 或 Sec-WebSocket-Protocol
    Headers:
      Authorization: Bearer <JWT>
      或
      Sec-WebSocket-Protocol: bearer, <JWT>

    Query 参数:
      - session_id: 会话ID (可选，不提供则自动创建)
    """
    from app.middleware.auth import JWTError, verify_ws_token

    # 1. 认证
    headers = dict(websocket.headers)
    try:
        auth_user = await verify_ws_token(headers)
        user_id = auth_user.user_id
    except JWTError as e:
        await websocket.close(code=4001, reason=str(e))
        return

    # 2. 生成或使用 session_id
    session_id = session_id or str(uuid.uuid4())

    # 3. 连接管理
    manager = get_connection_manager()

    # 检查消息大小限制
    max_size = manager._max_message_size if hasattr(manager, '_max_message_size') else 65536
    websocket.accept(max_size=max_size)

    # 4. 建立连接（检查连接数限制）
    success, error_code, error_msg = await manager.connect(websocket, session_id, user_id)
    if not success:
        await websocket.close(code=4002 if error_code == "TOO_MANY_CONNECTIONS" else 503, reason=error_msg)
        return

    # 5. 发送连接成功消息
    await websocket.send_json({
        "type": "connected",
        "session_id": session_id,
        "user_id": user_id,
    })

    request_id = str(uuid.uuid4())

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            # 消息大小检查
            data_str = json.dumps(data) if isinstance(data, dict) else str(data)
            if len(data_str.encode()) > max_size:
                await websocket.send_json({
                    "type": "error",
                    "code": "MESSAGE_TOO_LARGE",
                    "message": f"消息超出 {max_size} 字节限制",
                    "retry_after": 0,
                })
                continue

            # 限流检查
            allowed, user_remaining, conn_remaining = await manager.check_rate_limit(user_id, session_id)
            if not allowed:
                await websocket.send_json({
                    "type": "error",
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": "请求过于频繁，请稍后重试",
                    "retry_after": 5,
                    "user_remaining": user_remaining,
                    "conn_remaining": conn_remaining,
                })
                continue

            if msg_type == "message":
                await handle_message(
                    websocket=websocket,
                    session_id=session_id,
                    user_id=user_id,
                    content=data.get("content", ""),
                    provided_session_id=data.get("session_id"),
                    request_id=request_id,
                )

            elif msg_type == "history":
                await handle_history(
                    websocket=websocket,
                    session_id=session_id,
                    user_id=user_id,
                    limit=data.get("limit", 20),
                )

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            elif msg_type == "delete":
                await handle_delete_message(
                    websocket=websocket,
                    session_id=session_id,
                    user_id=user_id,
                    message_id=data.get("message_id"),
                )

            else:
                await websocket.send_json({
                    "type": "error",
                    "code": "INVALID_MESSAGE_TYPE",
                    "message": f"未知的消息类型: {msg_type}"
                })

    except WebSocketDisconnect:
        _logger.info("Client disconnected", extra={"session_id": session_id, "user_id": user_id})
    except Exception as e:
        _logger.error(
            "WebSocket error",
            extra={"session_id": session_id, "user_id": user_id, "error": str(e)}
        )
        try:
            await websocket.send_json({
                "type": "error",
                "code": "INTERNAL_ERROR",
                "message": "服务器内部错误"
            })
        except Exception:
            pass
    finally:
        await manager.disconnect(session_id)
```

同时在文件顶部添加 `import json`。

- [ ] **Step 2: 运行语法检查**

Run: `cd backend && python -c "from app.api import websocket; print('OK')"`
Expected: 输出 `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/websocket.py
git commit -m "feat(websocket): add Bearer Token authentication and rate limiting"
```

---

### Task 5: 标记 REST 聊天接口为废弃

**Files:**
- Modify: `backend/app/api/chat.py`

- [ ] **Step 1: 标记废弃接口**

在 `backend/app/api/chat.py` 中，将 `chat` 和 `chat_stream` 函数修改为返回 410 Gone：

```python
@router.post("/chat", response_model=ChatResponse, deprecated=True)
async def chat(
    request: ChatRequest,
    orchestrator: RAGOrchestrator = Depends(get_rag_orchestrator),
    intent_classifier: IntentClassifier = Depends(get_intent_classifier),
    session_svc: SessionService = Depends(get_session_service),
):
    """
    [DEPRECATED] 此接口已废弃，请使用 WebSocket /ws/chat

    2026-04-13 起，此接口不再维护。
    WebSocket 端点支持所有聊天功能，并提供更好的实时体验。
    """
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=410,
        content={
            "error": {
                "code": "ENDPOINT_DEPRECATED",
                "message": "此接口已废弃，请使用 WebSocket /ws/chat",
                "migration_guide": "请参考 docs/websocket-migration.md",
            }
        },
    )


@router.post("/chat/stream", deprecated=True)
async def chat_stream(
    request: ChatRequest,
    orchestrator: RAGOrchestrator = Depends(get_rag_orchestrator),
    intent_classifier: IntentClassifier = Depends(get_intent_classifier),
    session_svc: SessionService = Depends(get_session_service),
):
    """
    [DEPRECATED] 此接口已废弃，请使用 WebSocket /ws/chat

    2026-04-13 起，此接口不再维护。
    """
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=410,
        content={
            "error": {
                "code": "ENDPOINT_DEPRECATED",
                "message": "此接口已废弃，请使用 WebSocket /ws/chat",
                "migration_guide": "请参考 docs/websocket-migration.md",
            }
        },
    )
```

- [ ] **Step 2: 运行语法检查**

Run: `cd backend && python -c "from app.api import chat; print('OK')"`
Expected: 输出 `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/chat.py
git commit -m "feat(chat): deprecate REST chat endpoints in favor of WebSocket"
```

---

### Task 6: 编写 WebSocket 认证测试

**Files:**
- Create: `backend/tests/test_ws_auth.py`

- [ ] **Step 1: 编写测试**

```python
"""
WebSocket 认证测试
"""
import pytest
from app.middleware.auth import (
    extract_bearer_from_headers,
    create_token,
    decode_token,
    JWTError,
    verify_ws_token,
)


class TestExtractBearerFromHeaders:
    """测试从 headers 提取 Bearer Token"""

    def test_authorization_bearer(self):
        headers = {"authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.test"}
        token = extract_bearer_from_headers(headers)
        assert token == "eyJhbGciOiJIUzI1NiJ9.test"

    def test_authorization_lowercase(self):
        headers = {"Authorization": "Bearer test-token-123"}
        token = extract_bearer_from_headers(headers)
        assert token == "test-token-123"

    def test_websocket_protocol(self):
        headers = {"sec-websocket-protocol": "bearer, ws-token-456"}
        token = extract_bearer_from_headers(headers)
        assert token == "ws-token-456"

    def test_missing_token(self):
        headers = {}
        token = extract_bearer_from_headers(headers)
        assert token is None

    def test_invalid_format(self):
        headers = {"authorization": "Basic abc123"}
        token = extract_bearer_from_headers(headers)
        assert token is None


class TestCreateAndDecodeToken:
    """测试 JWT 创建和解析"""

    def test_create_and_decode(self):
        token = create_token(user_id="user-123", role="user")
        user = decode_token(token)
        assert user.user_id == "user-123"
        assert user.role == "user"

    def test_expired_token(self):
        token = create_token(user_id="user-123", ttl_seconds=-1)
        with pytest.raises(JWTError, match="expired"):
            decode_token(token)

    def test_invalid_token(self):
        with pytest.raises(JWTError, match="Invalid token format"):
            decode_token("not.a.valid.token")


@pytest.mark.asyncio
class TestVerifyWsToken:
    """测试 WebSocket Token 验证"""

    async def test_valid_token(self):
        token = create_token(user_id="ws-user", role="user")
        headers = {"authorization": f"Bearer {token}"}
        user = await verify_ws_token(headers)
        assert user.user_id == "ws-user"

    async def test_missing_token(self):
        headers = {}
        with pytest.raises(JWTError, match="Missing"):
            await verify_ws_token(headers)

    async def test_invalid_token(self):
        headers = {"authorization": "Bearer invalid.token"}
        with pytest.raises(JWTError):
            await verify_ws_token(headers)
```

- [ ] **Step 2: 运行测试**

Run: `cd backend && python -m pytest tests/test_ws_auth.py -v`
Expected: 所有测试通过

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_ws_auth.py
git commit -m "test(ws): add WebSocket authentication tests"
```

---

### Task 7: 编写连接管理器测试

**Files:**
- Create: `backend/tests/test_ws_manager.py`

- [ ] **Step 1: 编写测试**

```python
"""
WebSocket 连接管理器测试
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.api.ws_manager import (
    ConnectionManager,
    SlidingWindowLimiter,
    get_connection_manager,
    reset_connection_manager,
)


class TestSlidingWindowLimiter:
    """测试滑动窗口限流器"""

    def test_memory_limiter_allows(self):
        limiter = SlidingWindowLimiter(max_requests=10, window_seconds=60)
        allowed, remaining = limiter._memory_fallback("test-key", 1000.0, 940.0)
        assert allowed is True
        assert remaining == 1

    def test_memory_limiter_blocks(self):
        limiter = SlidingWindowLimiter(max_requests=2, window_seconds=60)
        # 添加 2 个请求
        limiter._memory_fallback("test-key", 1000.0, 940.0)
        limiter._memory_fallback("test-key", 1001.0, 940.0)
        # 第三个请求
        allowed, remaining = limiter._memory_fallback("test-key", 1002.0, 940.0)
        assert allowed is False


class TestConnectionManager:
    """测试连接管理器"""

    @pytest.fixture(autouse=True)
    def reset_manager(self):
        reset_connection_manager()
        yield
        reset_connection_manager()

    @pytest.fixture
    def mock_config(self):
        class MockWSConfig:
            max_per_user = 3
            max_global = 100
            heartbeat_interval = 30
            heartbeat_timeout = 60
            rate_limit_per_user = 30
            rate_limit_per_conn = 20
            rate_limit_window = 60
            max_message_size = 65536
        return MockWSConfig()

    @pytest.mark.asyncio
    async def test_check_connection_limits(self, mock_config):
        manager = get_connection_manager(mock_config)

        # 应该允许
        allowed, code, msg = await manager.check_connection_limits("user-1")
        assert allowed is True
        assert code == ""
        assert msg == ""

    @pytest.mark.asyncio
    async def test_user_connection_limit(self, mock_config):
        manager = get_connection_manager(mock_config)
        mock_config.max_per_user = 2

        # 连接 2 个
        await manager.check_connection_limits("user-1")
        await manager.check_connection_limits("user-1")

        # 第三个应该被拒绝
        allowed, code, msg = await manager.check_connection_limits("user-1")
        assert allowed is False
        assert code == "TOO_MANY_CONNECTIONS"

    @pytest.mark.asyncio
    async def test_global_connection_limit(self, mock_config):
        manager = get_connection_manager(mock_config)
        mock_config.max_global = 2

        # 连接 2 个
        await manager.check_connection_limits("user-1")
        await manager.check_connection_limits("user-2")

        # 第三个应该被拒绝
        allowed, code, msg = await manager.check_connection_limits("user-3")
        assert allowed is False
        assert code == "SERVER_FULL"

    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self, mock_config):
        manager = get_connection_manager(mock_config)

        # 模拟 WebSocket
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.close = AsyncMock()

        # 连接
        success, code, msg = await manager.connect(mock_ws, "session-1", "user-1")
        assert success is True
        assert manager.get_active_count() == 1
        assert manager.get_user_count("user-1") == 1

        # 断开
        await manager.disconnect("session-1")
        assert manager.get_active_count() == 0
        assert manager.get_user_count("user-1") == 0

    @pytest.mark.asyncio
    async def test_send_json(self, mock_config):
        manager = get_connection_manager(mock_config)

        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.send_json = AsyncMock()
        mock_ws.close = AsyncMock()

        await manager.connect(mock_ws, "session-1", "user-1")

        result = await manager.send_json("session-1", {"type": "test"})
        assert result is True
        mock_ws.send_json.assert_called_once_with({"type": "test"})

        await manager.disconnect("session-1")

    @pytest.mark.asyncio
    async def test_broadcast(self, mock_config):
        manager = get_connection_manager(mock_config)

        mock_ws1 = AsyncMock()
        mock_ws1.accept = AsyncMock()
        mock_ws1.send_json = AsyncMock()
        mock_ws1.close = AsyncMock()

        mock_ws2 = AsyncMock()
        mock_ws2.accept = AsyncMock()
        mock_ws2.send_json = AsyncMock()
        mock_ws2.close = AsyncMock()

        await manager.connect(mock_ws1, "session-1", "user-1")
        await manager.connect(mock_ws2, "session-2", "user-1")

        count = await manager.broadcast("user-1", {"type": "broadcast"})
        assert count == 2

        await manager.disconnect("session-1")
        await manager.disconnect("session-2")

    @pytest.mark.asyncio
    async def test_rate_limit(self, mock_config):
        manager = get_connection_manager(mock_config)
        mock_config.rate_limit_per_user = 2
        mock_config.rate_limit_per_conn = 2

        # 前 2 个请求应该通过
        for i in range(2):
            allowed, _, _ = await manager.check_rate_limit("user-1", "session-1")
            assert allowed is True

        # 第 3 个请求应该被限流
        allowed, _, _ = await manager.check_rate_limit("user-1", "session-1")
        assert allowed is False
```

- [ ] **Step 2: 运行测试**

Run: `cd backend && python -m pytest tests/test_ws_manager.py -v`
Expected: 所有测试通过

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_ws_manager.py
git commit -m "test(ws): add connection manager tests"
```

---

### Task 8: 编写 WebSocket 集成测试

**Files:**
- Create: `backend/tests/test_websocket.py`

- [ ] **Step 1: 编写集成测试**

```python
"""
WebSocket 端到端集成测试
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


class TestWebSocketEndpoint:
    """测试 WebSocket 端点"""

    def test_ws_missing_token(self, client):
        """测试缺少 token 的连接"""
        with client.websocket_connect("/ws/chat") as websocket:
            # 应该收到关闭消息
            with pytest.raises(Exception):
                websocket.receive_json()

    def test_ws_invalid_token(self, client):
        """测试无效 token 的连接"""
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/chat?token=invalid") as websocket:
                websocket.receive_json()

    def test_ws_deprecated_endpoints(self, client):
        """测试废弃的 REST 接口返回 410"""
        from app.middleware.auth import create_token
        token = create_token(user_id="test-user")

        # POST /api/v1/chat 应该返回 410
        response = client.post(
            "/api/v1/chat",
            json={"message": "test", "user_id": "test-user"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 410
        assert response.json()["error"]["code"] == "ENDPOINT_DEPRECATED"

        # POST /api/v1/chat/stream 应该返回 410
        response = client.post(
            "/api/v1/chat/stream",
            json={"message": "test", "user_id": "test-user"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 410
```

- [ ] **Step 2: 运行集成测试**

Run: `cd backend && python -m pytest tests/test_websocket.py -v -k "test_ws_deprecated" --tb=short`
Expected: 测试通过（可能需要 mock 数据库连接）

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_websocket.py
git commit -m "test(ws): add WebSocket integration tests"
```

---

### Task 9: 更新配置和环境变量文档

**Files:**
- Create: `docs/websocket-migration.md`（可选迁移指南）

- [ ] **Step 1: 创建迁移指南文档**

```markdown
# WebSocket 迁移指南

## 从 REST API 迁移到 WebSocket

### 旧接口（已废弃）

```bash
# 同步聊天
POST /api/v1/chat
Body: {"message": "...", "user_id": "...", "session_id": "..."}

# 流式聊天
POST /api/v1/chat/stream
Body: {"message": "...", "user_id": "...", "session_id": "..."}
```

### 新接口（推荐）

```bash
# WebSocket 连接
wss://api.example.com/ws/chat?session_id=xxx
Headers:
  Authorization: Bearer <JWT>
```

### 客户端示例

#### JavaScript

```javascript
const token = "your-jwt-token";
const ws = new WebSocket(
  `wss://api.example.com/ws/chat?session_id=${sessionId}`,
  ["bearer", token]
);

ws.onopen = () => {
  ws.send(JSON.stringify({
    type: "message",
    content: "你好，我想查订单"
  }));
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  if (data.type === "chunk") {
    console.log("收到内容:", data.content);
  } else if (data.type === "done") {
    console.log("回答完成:", data);
  }
};
```

#### Python

```python
import asyncio
import websockets
import json

async def chat():
    token = "your-jwt-token"
    uri = "wss://api.example.com/ws/chat"

    async with websockets.connect(uri, extra_headers={"Authorization": f"Bearer {token}"}) as ws:
        # 发送消息
        await ws.send(json.dumps({
            "type": "message",
            "content": "你好，我想查订单"
        }))

        # 接收响应
        async for message in ws:
            data = json.loads(message)
            if data["type"] == "chunk":
                print(data["content"], end="")
            elif data["type"] == "done":
                print("\n回答完成")
                break
```

## 错误码

| 错误码 | 说明 |
|--------|------|
| `UNAUTHORIZED` | Token 无效 |
| `TOO_MANY_CONNECTIONS` | 用户连接数超限 |
| `SERVER_FULL` | 服务器连接数满 |
| `RATE_LIMIT_EXCEEDED` | 消息限流 |
| `MESSAGE_TOO_LARGE` | 消息超出 64KB |
| `SESSION_DENIED` | 无权访问该会话 |
| `INVALID_MESSAGE_TYPE` | 未知消息类型 |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `WS_MAX_PER_USER` | 5 | 单用户最大连接数 |
| `WS_MAX_GLOBAL` | 10000 | 全局最大连接数 |
| `WS_HEARTBEAT_INTERVAL` | 30 | 心跳间隔（秒） |
| `WS_HEARTBEAT_TIMEOUT` | 60 | 心跳超时（秒） |
| `WS_RATE_LIMIT_PER_USER` | 30 | 用户消息限流（条/分钟） |
| `WS_RATE_LIMIT_PER_CONN` | 20 | 连接消息限流（条/分钟） |
| `WS_MAX_MESSAGE_SIZE` | 65536 | 单条消息最大字节数 |
```

- [ ] **Step 2: Commit**

```bash
git add docs/websocket-migration.md
git commit -m "docs: add WebSocket migration guide"
```

---

## 自检清单

完成实施后，验证以下内容：

### 1. Spec 覆盖检查

- [ ] Bearer Token 认证 → Task 2, 4
- [ ] 连接数限制（单用户 5 个，全局 10000 个）→ Task 3, 4
- [ ] 消息限流（滑动窗口）→ Task 3, 4
- [ ] 错误码规范 → Task 3, 4
- [ ] REST 聊天接口废弃 → Task 5
- [ ] 心跳机制 → Task 3
- [ ] 优雅关闭 → Task 3
- [ ] 测试覆盖 → Task 6, 7, 8

### 2. Placeholder 扫描

- [ ] 无 "TBD"、"TODO" 等占位符
- [ ] 所有代码步骤都有实际代码
- [ ] 所有测试都有断言

### 3. 类型一致性

- [ ] `ConnectionManager` 方法名一致
- [ ] `SlidingWindowLimiter` 返回类型一致
- [ ] 错误码与设计文档一致
