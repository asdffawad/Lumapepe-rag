# WebSocket 聊天改造设计方案

> **日期:** 2026-04-12  
> **目标:** 在保留现有 HTTP API 的基础上，添加 WebSocket 实时聊天支持

---

## 1. 架构概览

```
┌─────────────┐     HTTP POST      ┌─────────────┐
│   前端A     │ ───────────────►   │   FastAPI   │
└─────────────┘   /api/v1/chat     └─────────────┘
                                      │
              ┌───────────────────────┼───────────────────────┐
              │                       │                       │
              ▼                       ▼                       ▼
     ┌─────────────┐          ┌─────────────┐         ┌─────────────┐
     │  WebSocket  │          │ SessionSvc   │         │ RAGOrch     │
     │  /ws/chat   │          │ (复用)      │         │ (复用)      │
     └─────────────┘          └─────────────┘         └─────────────┘
```

## 2. WebSocket 端点

**连接 URL:**
```
ws://localhost:8000/ws/chat?session_id={可选}&user_id={必需}
```

**示例:**
```
ws://localhost:8000/ws/chat?user_id=user-001
ws://localhost:8000/ws/chat?session_id=sess-abc123&user_id=user-001
```

## 3. 消息协议

### 3.1 客户端 → 服务端

```json
// 发送聊天消息
{"type": "message", "content": "iPhone有优惠吗？", "session_id": "可选"}

 // 获取历史记录
{"type": "history", "limit": 20}

 // 心跳检测
{"type": "ping"}
```

### 3.2 服务端 → 客户端

```json
// 流式片段
{"type": "chunk", "content": "iPhone", "done": false}

// 完成
{
  "type": "done",
  "session_id": "xxx",
  "intent": "product_inquiry",
  "sources": ["商品详情"],
  "tokens": 150,
  "latency_ms": 1200
}

// 历史记录响应
{"type": "history", "session_id": "xxx", "messages": [...]}

// 心跳响应
{"type": "pong"}

// 错误
{"type": "error", "code": "SESSION_DENIED", "message": "无权访问"}
```

## 4. 核心组件

### 4.1 ConnectionManager (`app/api/ws_manager.py`)

**职责:**
- 管理活跃 WebSocket 连接
- session_id → WebSocket 映射
- user_id → session_ids 映射（用于清理）
- 会话隔离验证

**核心方法:**
```python
class ConnectionManager:
    async def connect(websocket, session_id, user_id)
    async def disconnect(session_id, user_id)
    async def send_json(session_id, data)
    async def broadcast(user_id, data)  # 群发支持
```

### 4.2 WebSocket 路由 (`app/api/websocket.py`)

**职责:**
- 处理连接握手
- 路由消息类型
- 调用 RAGOrchestrator 流式处理
- 心跳管理

### 4.3 前端 (`frontend/index.html`)

**改动:**
- 使用原生 WebSocket API
- 自动重连机制（指数退避）
- 流式文本累积渲染
- 心跳保活

## 5. 关键特性

| 特性 | 实现方式 |
|------|----------|
| 会话隔离 | user_id 必须提供，验证 session 归属 |
| 心跳保活 | 30秒 ping/pong |
| 断线重连 | 前端指数退避重连 |
| 向后兼容 | HTTP API 保持不变 |

## 6. 文件变更清单

### 新建文件
- `backend/app/api/ws_manager.py` - 连接管理器
- `backend/app/api/websocket.py` - WebSocket 路由

### 修改文件
- `backend/app/main.py` - 注册 WebSocket 路由
- `frontend/index.html` - WebSocket 客户端
- `frontend/test.html` - WebSocket 客户端
- `frontend/admin.html` - 保持 HTTP（管理功能）

### 保留不变
- `backend/app/api/chat.py` - HTTP API 保持不变
- `backend/app/services/session_service.py` - 复用
- `backend/app/services/rag_orchestrator.py` - 复用

## 7. 错误处理

| 错误场景 | 响应 |
|----------|------|
| session 不属于该 user | `{"type": "error", "code": "SESSION_DENIED"}` |
| LLM 调用失败 | `{"type": "error", "code": "LLM_ERROR", "message": "..."}` |
| WebSocket 断开 | 前端自动重连 |

## 8. 安全考虑

1. **认证**: 支持通过 query 参数传入 token，验证 JWT
2. **CORS**: WebSocket 不受 HTTP CORS 限制，但需验证 Origin
3. **限流**: 复用现有 RateLimitMiddleware
