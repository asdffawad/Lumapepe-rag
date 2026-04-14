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
