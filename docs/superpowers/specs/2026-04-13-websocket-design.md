# WebSocket 企业级改造设计方案

> 日期: 2026-04-13
> 目标: 将项目改造为纯 WebSocket 通信方式

---

## 1. 概述

### 1.1 改造目标

- 将聊天功能从 REST API 迁移到 WebSocket
- 采用企业级安全标准（Header 认证）
- 统一通信协议，简化架构

### 1.2 设计决策

| 决策项 | 选择 | 原因 |
|--------|------|------|
| 通信模式 | 纯 WebSocket 化 | 统一入口，简化架构 |
| 认证方式 | Bearer Token (Header) | 安全、合规、符合企业标准 |
| REST 处理 | 废弃聊天接口 | 保持 admin/health/intent |
| 连接数限制 | 单用户 5 个，全局 10000 | 平衡多设备需求与资源控制 |

---

## 2. 认证与安全

### 2.1 WebSocket 连接

**URL:** `wss://api.example.com/ws/chat`

**请求头:**
```
Authorization: Bearer <JWT>
```

**Query 参数 (可选):**
```
?session_id=xxx  -- 指定会话ID，不提供则自动创建
```

### 2.2 握手流程

```
1. 客户端: wss://host/ws/chat (带 Authorization header)
2. 服务端: 在 WebSocket connect 事件中读取 Authorization header
3. 服务端: 解析 JWT，验证签名和有效期
4. 成功: 接受连接，返回 {"type": "connected", "session_id": "xxx"}
5. 失败: 拒绝连接 (401/403)
```

### 2.3 连接数限制

| 维度 | 限制 | 超出处理 |
|------|------|----------|
| 单用户 | 5 个并发连接 | 返回 `TOO_MANY_CONNECTIONS` |
| 全局 | 10000 个连接 | 返回 `SERVER_FULL` |

### 2.4 消息安全

- 单条消息最大: **64KB**
- 超出返回: `MESSAGE_TOO_LARGE`
- 所有敏感信息不记录到日志

---

## 3. 消息协议格式

### 3.1 客户端 → 服务端

```json
// 发送聊天消息
{
  "type": "message",
  "content": "我想查订单",
  "session_id": "可选，不提供则使用当前会话"
}

// 获取历史记录
{
  "type": "history",
  "session_id": "可选",
  "limit": 20
}

// 发送心跳
{
  "type": "ping"
}

// 删除消息
{
  "type": "delete",
  "message_id": 123,
  "session_id": "可选"
}
```

### 3.2 服务端 → 客户端

```json
// 连接成功
{
  "type": "connected",
  "session_id": "xxx",
  "user_id": "xxx"
}

// 消息已接收确认
{
  "type": "message_received",
  "session_id": "xxx",
  "intent": "order_query"
}

// 流式内容块
{
  "type": "chunk",
  "content": "您好",
  "done": false
}

// 流结束
{
  "type": "done",
  "session_id": "xxx",
  "intent": "xxx",
  "sources": [],
  "tokens_used": 100,
  "latency_ms": 500
}

// 错误
{
  "type": "error",
  "code": "RATE_LIMIT_EXCEEDED",
  "message": "请求过于频繁",
  "retry_after": 60
}

// 心跳响应
{
  "type": "pong"
}

// 历史记录
{
  "type": "history",
  "session_id": "xxx",
  "messages": [...]
}

// 优雅关闭通知
{
  "type": "shutdown",
  "message": "服务即将重启，请重新连接"
}
```

---

## 4. 错误码规范

| 错误码 | HTTP状态 | 说明 | 处理建议 |
|--------|----------|------|----------|
| `UNAUTHORIZED` | 401 | Token 无效或缺失 | 重新登录获取 token |
| `TOKEN_EXPIRED` | 401 | Token 已过期 | 刷新 token |
| `TOO_MANY_CONNECTIONS` | 429 | 用户连接数超限 | 关闭其他连接后重试 |
| `SERVER_FULL` | 503 | 服务器连接数满 | 稍后重试 |
| `RATE_LIMIT_EXCEEDED` | 429 | 消息限流 | 等待 retry_after 秒 |
| `MESSAGE_TOO_LARGE` | 413 | 消息超出 64KB | 缩短消息 |
| `SESSION_DENIED` | 403 | 无权访问该会话 | 检查 session_id |
| `SESSION_NOT_FOUND` | 404 | 会话不存在 | 创建新会话 |
| `INVALID_MESSAGE_TYPE` | 400 | 未知消息类型 | 检查 type 字段 |
| `INTERNAL_ERROR` | 500 | 服务器内部错误 | 联系支持 |

---

## 5. 限流策略

| 维度 | 限制 | 窗口类型 |
|------|------|----------|
| 每用户消息频率 | 30 条/分钟 | 滑动窗口 |
| 每连接消息频率 | 20 条/分钟 | 滑动窗口 |

超出限制返回 `RATE_LIMIT_EXCEEDED`，包含 `retry_after` 字段。

---

## 6. 心跳与连接保活

- 客户端每 **30 秒** 发送 `ping`
- 服务端 **15 秒** 内未收到任何消息则发送 `ping`
- 服务端 **60 秒** 内无响应则断开连接
- 心跳失败不计入限流

---

## 7. 文件修改清单

| 文件 | 操作 | 职责 |
|------|------|------|
| `app/api/websocket.py` | 修改 | 核心 WS 逻辑 + Header 认证 |
| `app/api/ws_manager.py` | 修改 | 连接管理 + 连接数限制 |
| `app/api/chat.py` | 修改 | 标记废弃接口 |
| `app/middleware/auth.py` | 修改 | 添加 WS 认证函数 |
| `app/services/llm_generator.py` | 修改 | StreamChunk 定义 |
| `app/models/request.py` | 可能修改 | 请求模型 |
| `tests/test_websocket.py` | 新建 | WS 测试 |

---

## 8. REST 接口处理

### 8.1 废弃接口 (标记为 deprecated)

- `POST /api/v1/chat` - 聊天 (废弃)
- `POST /api/v1/chat/stream` - 流式聊天 (废弃)

### 8.2 保留接口

- `GET /api/v1/health` - 健康检查
- `POST /api/v1/admin/*` - 管理接口
- `POST /api/v1/intent/*` - 意图识别

---

## 9. 客户端 SDK 设计 (参考)

```javascript
// 连接
const ws = new WebSocket('wss://api.example.com/ws/chat', 'bearer', {
  headers: {
    'Authorization': 'Bearer <JWT>'
  }
});

// 发送消息
ws.send(JSON.stringify({
  type: 'message',
  content: '你好'
}));

// 接收消息
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  // 处理不同类型消息
};
```

---

## 10. 部署注意事项

1. 反向代理需支持 WebSocket 升级
2. Nginx 配置 `proxy_http_version 1.1` 和 `proxy_set_header Upgrade $http_upgrade`
3. 考虑使用 Redis 进行分布式连接管理
4. 监控连接数和消息量