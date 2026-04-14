# WebSocket 企业级改造设计方案

> **日期:** 2026-04-13  
> **版本:** 1.0  
> **状态:** 已批准实施

---

## 1. 概述

### 1.1 目标

将项目从 REST API 通信升级为纯 WebSocket 通信方式，实现企业级的实时聊天功能。

### 1.2 核心原则

- **Bearer Token 认证** - 标准 Header 方式，安全可靠
- **纯 WebSocket 化** - 统一通信入口，移除 REST 聊天接口
- **企业级规范** - 连接数限制、限流、错误码标准化

---

## 2. 认证与安全

### 2.1 WebSocket 连接

```
URL: wss://api.example.com/ws/chat
Headers:
  Authorization: Bearer <JWT>
Query 参数:
  - session_id: 会话ID (可选，不提供则自动创建)
```

**握手流程:**
1. 客户端发起 WebSocket 连接，设置 `Authorization: Bearer xxx` header
2. 服务端在 `connect` 事件中读取 header
3. 验证 JWT 签名和有效期
4. 成功：接受连接，返回 `{"type": "connected", "session_id": "xxx"}`
5. 失败：拒绝连接

**备用方案（客户端不支持自定义 header 时）:**
```
Sec-WebSocket-Protocol: bearer, <JWT>
```

### 2.2 连接数限制

| 维度 | 上限 | 超出处理 |
|------|------|----------|
| 单用户 | 5 个并发连接 | 返回 `TOO_MANY_CONNECTIONS` |
| 全局 | 10000 个连接 | 返回 `SERVER_FULL` |

### 2.3 消息安全

| 限制项 | 值 | 超出处理 |
|--------|-----|----------|
| 单条消息大小 | ≤ 64KB | 返回 `MESSAGE_TOO_LARGE` |
| 用户消息频率 | 30 条/分钟 | 返回 `RATE_LIMIT_EXCEEDED` |
| 连接消息频率 | 20 条/分钟 | 返回 `RATE_LIMIT_EXCEEDED` |

---

## 3. 消息协议格式

### 3.1 客户端 → 服务端

```json
// 发送消息
{"type": "message", "content": "我想查订单", "session_id": "可选"}

// 获取历史
{"type": "history", "limit": 20}

// 心跳
{"type": "ping"}

// 删除消息
{"type": "delete", "message_id": 123}
```

### 3.2 服务端 → 客户端

```json
// 连接成功
{"type": "connected", "session_id": "xxx", "user_id": "xxx"}

// 消息已接收（确认）
{"type": "message_received", "session_id": "xxx", "intent": "order_query"}

// 流式内容块
{"type": "chunk", "content": "您好", "done": false}

// 流结束
{"type": "done", "session_id": "xxx", "intent": "xxx", "sources": [], "tokens_used": 100, "latency_ms": 500}

// 错误
{"type": "error", "code": "RATE_LIMIT_EXCEEDED", "message": "请求过于频繁", "retry_after": 5}

// 心跳响应
{"type": "pong"}

// 历史记录
{"type": "history", "session_id": "xxx", "messages": [...]}

// 优雅关闭通知
{"type": "shutdown", "message": "服务即将重启，请重新连接"}
```

---

## 4. 错误码规范

| 错误码 | 说明 | HTTP状态 | 处理建议 |
|--------|------|----------|----------|
| `UNAUTHORIZED` | Token 无效或缺失 | 401 | 重新登录获取 token |
| `TOKEN_EXPIRED` | Token 已过期 | 401 | 刷新 token |
| `TOO_MANY_CONNECTIONS` | 用户连接数超限 | 429 | 关闭其他连接后重试 |
| `SERVER_FULL` | 服务器连接数满 | 503 | 稍后重试 |
| `RATE_LIMIT_EXCEEDED` | 消息限流 | 429 | 等待 `retry_after` 秒后重试 |
| `MESSAGE_TOO_LARGE` | 消息超出 64KB | 413 | 缩短消息 |
| `SESSION_DENIED` | 无权访问该会话 | 403 | 检查 session_id |
| `SESSION_NOT_FOUND` | 会话不存在 | 404 | 创建新会话 |
| `INVALID_MESSAGE_TYPE` | 未知消��类型 | 400 | 检查 type 字段 |
| `INTERNAL_ERROR` | 服务器内部错误 | 500 | 联系支持 |

---

## 5. 心跳与生命周期

### 5.1 心跳机制

- **客户端发送:** 每 30 秒发送 `{"type": "ping"}`
- **服务端响应:** 返回 `{"type": "pong"}`
- **超时处理:** 60 秒无响应则断开连接

### 5.2 优雅断线

- 服务端通知: `{"type": "shutdown", "message": "服务即将重启"}`
- 客户端自动重连: 延迟 2 秒、5 秒、10 秒（指数退避）

---

## 6. REST 接口处理

### 6.1 废弃的接口（标记为 deprecated）

| 接口 | 处理方式 |
|------|----------|
| `POST /api/v1/chat` | 标记为 deprecated，返回 410 Gone |
| `POST /api/v1/chat/stream` | 标记为 deprecated，返回 410 Gone |

### 6.2 保留的接口

| 接口 | 说明 |
|------|------|
| `GET /api/v1/health` | 健康检查 |
| `POST /api/v1/intent` | 意图识别 |
| `GET /api/v1/admin/*` | 管理接口 |

---

## 7. 文件修改清单

| 文件 | 操作 | 职责 |
|------|------|------|
| `app/api/websocket.py` | 修改 | 核心 WS 逻辑 + Bearer Token 认证 |
| `app/api/ws_manager.py` | 修改 | 连接管理 + 连接数限制 + 限流 |
| `app/api/chat.py` | 修改 | 标记废弃接口 |
| `app/middleware/auth.py` | 修改 | 添加 WS 认证函数 |
| `app/services/llm_generator.py` | 修改 | StreamChunk 定义 |
| `app/models/request.py` | 可能修改 | 请求模型扩展 |
| `tests/test_websocket.py` | 新建 | WS 单元测试 |
| `tests/test_ws_manager.py` | 新建 | 连接管理测试 |

---

## 8. 技术实现要点

### 8.1 WebSocket 认证（FastAPI/Starlette）

```python
async def websocket_endpoint(websocket: WebSocket):
    # 方式1: Authorization Header
    token = websocket.headers.get("authorization", "").replace("Bearer ", "")
    
    # 方式2: Sec-WebSocket-Protocol
    if not token:
        protocol = websocket.headers.get("sec-websocket-protocol", "")
        if protocol.startswith("bearer,"):
            token = protocol.replace("bearer,", "").strip()
    
    # 验证 token...
```

### 8.2 连接管理器

```python
class WSConnectionManager:
    # 每用户连接数追踪
    # 全局连接数追踪
    # Token 验证
    # 心跳追踪
    # 消息限流（滑动窗口）
```

### 8.3 流式响应

- 使用 `aiokafka` 异步发送消息块
- 每块包含 `done: false`，最后一块 `done: true`
- 包含 `intent`、`sources`、`tokens_used`、`latency_ms` 等元数据

---

## 9.向后兼容

- 旧客户端（使用 query 参数传递 token）提供过渡期支持
- 过渡期结束后移除 query 参数兼容

---

## 10. 附录

### 10.1 消息类型枚举

| type 值 | 方向 | 说明 |
|---------|------|------|
| `message` | C→S | 发送消息 |
| `message_received` | S→C | 消息已接收确认 |
| `chunk` | S→C | 流式内容块 |
| `done` | S→C | 流结束 |
| `error` | S→C | 错误 |
| `ping` | C→S | 心跳 |
| `pong` | S→C | 心跳响应 |
| `history` | C→S/S→C | 历史记录请求/响应 |
| `delete` | C→S | 删除消息 |
| `connected` | S→C | 连接成功 |
| `shutdown` | S→C | 优雅关闭 |

### 10.2 环境变量配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `WS_MAX_PER_USER` | 5 | 单用户最大连接数 |
| `WS_MAX_GLOBAL` | 10000 | 全局最大连接数 |
| `WS_HEARTBEAT_INTERVAL` | 30 | 心跳间隔（秒） |
| `WS_HEARTBEAT_TIMEOUT` | 60 | 心跳超时（秒） |
| `WS_RATE_LIMIT_PER_USER` | 30 | 用户消息限流（条/分钟） |
| `WS_MAX_MESSAGE_SIZE` | 65536 | 单条消息最大字节数 |
