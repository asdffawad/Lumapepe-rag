# WebSocket 聊天改造实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留现有 HTTP API 的基础上，添加 WebSocket 实时聊天支持，实现双向流式通信。

**Architecture:** 创建一个独立的 ConnectionManager 管理 WebSocket 连接，添加 WebSocket 路由复用现有 SessionService 和 RAGOrchestrator，前端使用原生 WebSocket API 替换 HTTP 请求。

**Tech Stack:** FastAPI WebSocket, 原生 WebSocket API, asyncio

---

## 文件结构

```
backend/app/api/
├── __init__.py          # 修改：导出 websocket router
├── chat.py              # 不变：保留 HTTP API
├── ws_manager.py        # 新建：ConnectionManager
└── websocket.py         # 新建：WebSocket 路由

frontend/
├── index.html           # 修改：WebSocket 客户端
└── test.html            # 修改：WebSocket 客户端

backend/app/
└── main.py              # 修改：注册 WebSocket 路由
```

---

## Task 1: 创建 ConnectionManager

**Files:**
- Create: `backend/app/api/ws_manager.py`

- [ ] **Step 1: 创建 ws_manager.py 文件**

```python
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
```

---

## Task 2: 创建 WebSocket 路由

**Files:**
- Create: `backend/app/api/websocket.py`

- [ ] **Step 1: 创建 websocket.py 文件**

```python
"""
WebSocket 聊天路由
处理连接、消息路由、流式响应、心跳
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from app.api.ws_manager import get_connection_manager
from app.database.connection import get_db_pool
from app.cache.redis_client import get_redis_client
from app.services.rag_orchestrator import RAGOrchestrator
from app.services.intent_service import IntentClassifier
from app.services.session_service import SessionService
from app.services.llm_generator import StreamChunk
from app.logging_config import get_logger

router = APIRouter(tags=["websocket"])
_logger = get_logger("websocket")


def get_rag_orchestrator() -> RAGOrchestrator:
    from app.main import app as _app
    return _app.state.orchestrator


def get_intent_classifier() -> IntentClassifier:
    from app.main import app as _app
    return _app.state.intent_classifier


async def get_session_service() -> SessionService:
    pool = get_db_pool()
    redis = get_redis_client()
    return SessionService(pool, redis)


@router.websocket("/ws/chat")
async def websocket_chat(
    websocket: WebSocket,
    session_id: Optional[str] = Query(None, description="会话ID，不提供则自动创建"),
    user_id: str = Query(..., description="用户ID（必需）"),
):
    """
    WebSocket 聊天端点
    
    连接后可以发送以下消息:
    - {"type": "message", "content": "...", "session_id": "可选"}
    - {"type": "history", "limit": 20}
    - {"type": "ping"}
    """
    manager = get_connection_manager()
    session_id = session_id or str(uuid.uuid4())
    
    await manager.connect(websocket, session_id, user_id)
    request_id = str(uuid.uuid4())
    
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            
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
            
            else:
                await websocket.send_json({
                    "type": "error",
                    "code": "UNKNOWN_MESSAGE_TYPE",
                    "message": f"未知的消息类型: {msg_type}"
                })
    
    except WebSocketDisconnect:
        _logger.info("Client disconnected", extra={"session_id": session_id})
    except Exception as e:
        _logger.error(
            "WebSocket error",
            extra={"session_id": session_id, "error": str(e)}
        )
        try:
            await websocket.send_json({
                "type": "error",
                "code": "INTERNAL_ERROR",
                "message": str(e)
            })
        except Exception:
            pass
    finally:
        await manager.disconnect(session_id)


async def handle_message(
    websocket: WebSocket,
    session_id: str,
    user_id: str,
    content: str,
    provided_session_id: Optional[str],
    request_id: str,
) -> None:
    """处理聊天消息，流式响应"""
    orchestrator = get_rag_orchestrator()
    intent_classifier = get_intent_classifier()
    session_svc = await get_session_service()
    
    # 如果提供了 session_id，验证归属
    target_session_id = provided_session_id or session_id
    if provided_session_id:
        owned = await session_svc.verify_session_ownership(provided_session_id, user_id)
        if not owned:
            await websocket.send_json({
                "type": "error",
                "code": "SESSION_DENIED",
                "message": "无权访问该会话"
            })
            return
    
    try:
        ctx = await session_svc.get_or_create_session(
            session_id=target_session_id,
            user_id=user_id,
        )
        history = ctx.history[-5:] if ctx.history else []
        
        # 意图分类
        intent_result = await intent_classifier.classify(content, history)
        intent = intent_result.intent
        
        # 发送用户消息确认
        await websocket.send_json({
            "type": "message_received",
            "session_id": ctx.session_id,
            "intent": intent,
        })
        
        # 流式生成
        accumulated = ""
        final_citations = []
        
        async for chunk_obj in orchestrator.run_stream(
            query=content,
            history=history,
            intent=intent,
            request_id=request_id,
            user_id=user_id,
        ):
            if isinstance(chunk_obj, StreamChunk):
                if chunk_obj.content:
                    await websocket.send_json({
                        "type": "chunk",
                        "content": chunk_obj.content,
                        "done": False,
                    })
                    accumulated += chunk_obj.content
                if chunk_obj.citations:
                    final_citations = chunk_obj.citations
                if chunk_obj.is_final:
                    break
            elif isinstance(chunk_obj, str):
                if chunk_obj:
                    await websocket.send_json({
                        "type": "chunk",
                        "content": chunk_obj,
                        "done": False,
                    })
                    accumulated += chunk_obj
        
        # 流结束后，保存消息
        await session_svc.add_message(
            session_id=ctx.session_id,
            role="user",
            content=content,
            intent=intent,
        )
        await session_svc.add_message(
            session_id=ctx.session_id,
            role="assistant",
            content=accumulated,
            intent=intent,
            sources={"sources": list(set(final_citations))} if final_citations else None,
        )
        await session_svc.update_session(ctx.session_id, intent=intent)
        
        # 发送完成消息
        await websocket.send_json({
            "type": "done",
            "session_id": ctx.session_id,
            "intent": intent,
            "sources": list(set(final_citations)),
            "content_length": len(accumulated),
        })
    
    except PermissionError as e:
        await websocket.send_json({
            "type": "error",
            "code": "SESSION_DENIED",
            "message": str(e)
        })
    except Exception as e:
        _logger.error("Message handling error", extra={"error": str(e)})
        await websocket.send_json({
            "type": "error",
            "code": "MESSAGE_ERROR",
            "message": str(e)
        })


async def handle_history(
    websocket: WebSocket,
    session_id: str,
    user_id: str,
    limit: int,
) -> None:
    """处理历史记录请求"""
    session_svc = await get_session_service()
    
    owned = await session_svc.verify_session_ownership(session_id, user_id)
    if not owned:
        await websocket.send_json({
            "type": "error",
            "code": "SESSION_DENIED",
            "message": "无权访问该会话"
        })
        return
    
    history = await session_svc.get_history(session_id, limit=limit)
    await websocket.send_json({
        "type": "history",
        "session_id": session_id,
        "messages": history,
    })
```

---

## Task 3: 注册 WebSocket 路由

**Files:**
- Modify: `backend/app/main.py:241-245`
- Modify: `backend/app/api/__init__.py`

- [ ] **Step 1: 在 main.py 中添加 WebSocket 路由**

在 `backend/app/main.py` 第241行附近，找到:
```python
    # 注册路由
    app.include_router(chat.router)
    app.include_router(intent.router)
    app.include_router(health.router)
    app.include_router(admin.router)
    app.include_router(debug.router)
```

添加 WebSocket 路由:
```python
    # 注册路由
    app.include_router(chat.router)
    app.include_router(intent.router)
    app.include_router(health.router)
    app.include_router(admin.router)
    app.include_router(debug.router)
    app.include_router(websocket.router)
```

- [ ] **Step 2: 更新 __init__.py 导出**

在 `backend/app/api/__init__.py` 添加:
```python
from app.api import chat, intent, health, admin, debug, websocket
```

---

## Task 4: 改造前端 index.html

**Files:**
- Modify: `frontend/index.html:130-195`

- [ ] **Step 1: 添加 WebSocket 客户端代码**

在 `frontend/index.html` 中，将现有的 `handleSend()` 函数替换为 WebSocket 实现:

找到现有的 handleSend 函数 (约130-185行)，替换为:

```javascript
// WebSocket 配置
let ws = null;
let wsConnected = false;
const wsReconnectDelay = 1000;
let wsReconnectTimer = null;
let accumulatedResponse = "";
let currentPlaceholder = null;
let currentSessionId = null;

// 初始化 WebSocket 连接
function initWebSocket() {
    if (ws && ws.readyState === WebSocket.OPEN) return;
    
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/chat?user_id=guest${currentSessionId ? '&session_id=' + currentSessionId : ''}`;
    
    ws = new WebSocket(wsUrl);
    
    ws.onopen = () => {
        console.log('WebSocket connected');
        wsConnected = true;
        document.getElementById('connectionStatus').textContent = '● 已连接';
        document.getElementById('connectionStatus').style.color = '#4caf50';
        
        // 清除重连定时器
        if (wsReconnectTimer) {
            clearTimeout(wsReconnectTimer);
            wsReconnectTimer = null;
        }
    };
    
    ws.onclose = () => {
        console.log('WebSocket disconnected');
        wsConnected = false;
        document.getElementById('connectionStatus').textContent = '○ 已断开';
        document.getElementById('connectionStatus').style.color = '#999';
        
        // 指数退避重连
        if (!wsReconnectTimer) {
            wsReconnectTimer = setTimeout(() => {
                wsReconnectTimer = null;
                initWebSocket();
            }, Math.min(wsReconnectDelay * 2, 30000));
        }
    };
    
    ws.onerror = (err) => {
        console.error('WebSocket error:', err);
    };
    
    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleWsMessage(data);
        } catch (e) {
            console.error('Failed to parse message:', e);
        }
    };
}

// 处理 WebSocket 消息
function handleWsMessage(data) {
    switch (data.type) {
        case 'chunk':
            // 流式片段
            if (currentPlaceholder) {
                accumulatedResponse += data.content;
                currentPlaceholder.innerHTML = accumulatedResponse + '▌';
            }
            break;
            
        case 'done':
            // 完成
            if (currentPlaceholder) {
                currentPlaceholder.innerHTML = accumulatedResponse || '（无内容）';
                // 添加元信息
                let meta = '';
                if (data.intent) {
                    meta += `<div class="intent-tag">意图: ${data.intent}</div>`;
                }
                if (data.sources && data.sources.length > 0) {
                    meta += `<div class="sources">来源: ${data.sources.join(', ')}</div>`;
                }
                if (meta) {
                    currentPlaceholder.innerHTML += meta;
                }
            }
            currentSessionId = data.session_id;
            currentPlaceholder = null;
            accumulatedResponse = "";
            document.getElementById('sendBtn').disabled = false;
            break;
            
        case 'message_received':
            // 消息已接收，开始流式输出
            currentSessionId = data.session_id;
            accumulatedResponse = "";
            break;
            
        case 'error':
            // 错误
            if (currentPlaceholder) {
                currentPlaceholder.innerHTML = `<div class="error">错误: ${data.message}</div>`;
            }
            currentPlaceholder = null;
            accumulatedResponse = "";
            document.getElementById('sendBtn').disabled = false;
            break;
            
        case 'pong':
            // 心跳响应
            break;
            
        case 'history':
            // 历史记录
            console.log('History received:', data.messages);
            break;
    }
}

// 发送消息
async function handleSend() {
    const input = document.getElementById('userInput');
    const sendBtn = document.getElementById('sendBtn');
    const message = input.value.trim();
    
    if (!message) return;
    if (!wsConnected) {
        alert('WebSocket 未连接，请等待...');
        return;
    }
    
    input.value = '';
    sendBtn.disabled = true;
    
    // 用户消息
    addMessage('user', message);
    
    // 助手占位符
    currentPlaceholder = addMessage('assistant', '正在思考中...', true);
    accumulatedResponse = "";
    
    // 发送 WebSocket 消息
    ws.send(JSON.stringify({
        type: 'message',
        content: message,
        session_id: currentSessionId || undefined
    }));
}

// 添加连接状态显示
function addConnectionStatus() {
    const statusDiv = document.createElement('div');
    statusDiv.id = 'connectionStatus';
    statusDiv.style.cssText = 'position:fixed;top:10px;right:10px;padding:5px 10px;background:#f5f5f5;border-radius:4px;font-size:12px;color:#999;';
    statusDiv.textContent = '○ 连接中...';
    document.body.appendChild(statusDiv);
}

// 页面加载时初始化
document.addEventListener('DOMContentLoaded', () => {
    addConnectionStatus();
    initWebSocket();
});

// 页面卸载时关闭
window.addEventListener('beforeunload', () => {
    if (ws) {
        ws.close();
    }
});
```

- [ ] **Step 2: 确认 handleSend 函数已替换**

确保 `frontend/index.html` 中不再有旧的 `fetch` 调用。

---

## Task 5: 改造前端 test.html

**Files:**
- Modify: `frontend/test.html:1060-1110`

- [ ] **Step 1: 应用类似的 WebSocket 改造**

在 `frontend/test.html` 中找到现有的聊天发送逻辑，应用与 `index.html` 相同的改造模式。

---

## Task 6: 测试验证

**Files:**
- 无新增文件

- [ ] **Step 1: 重启后端服务**

```bash
docker compose restart backend
```

- [ ] **Step 2: 测试 WebSocket 连接**

使用浏览器开发者工具或 WebSocket 测试工具连接:
```
ws://localhost:8000/ws/chat?user_id=test&session_id=abc123
```

- [ ] **Step 3: 测试发送消息**

发送:
```json
{"type": "message", "content": "你好"}
```

预期响应:
```json
{"type": "chunk", "content": "你", "done": false}
{"type": "chunk", "content": "好", "done": false}
...
{"type": "done", "session_id": "...", "intent": "...", "sources": [...]}
```

- [ ] **Step 4: 测试心跳**

发送:
```json
{"type": "ping"}
```

预期响应:
```json
{"type": "pong"}
```

- [ ] **Step 5: 测试前端**

1. 打开 http://localhost/
2. 确认连接状态显示 "● 已连接"
3. 发送消息，确认流式输出正常

---

## 自检清单

- [ ] 所有消息类型都有对应处理
- [ ] 错误处理完善（断开连接、会话拒绝等）
- [ ] 前端重连机制正常
- [ ] 心跳保活工作正常
- [ ] HTTP API 仍然可用
- [ ] 流式输出无丢失

---

## 执行选项

**Plan complete and saved to `docs/superpowers/plans/2026-04-12-websocket-chat-plan.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
