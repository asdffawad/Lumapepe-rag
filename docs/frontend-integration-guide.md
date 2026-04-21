# RAG 前端联调与测试指南

本文档介绍如何有效使用增强后的前端页面进行开发调试和测试。

## 目录

1. [网络请求调试面板](#1-网络请求调试面板)
2. [快捷测试场景](#2-快捷测试场景)
3. [WebSocket 心跳排查](#3-websocket-心跳排查)
4. [常见问题排查](#4-常见问题排查)

---

## 1. 网络请求调试面板

### 1.1 打开调试面板

**客服聊天页 (index.html)**
- 点击导航栏右侧的「网络日志」按钮

**管理后台 (admin.html)**
- 点击顶部导航右侧的「网络日志」按钮

### 1.2 查看请求详情

面板分为左右两部分：

| 左侧列表 | 右侧详情 |
|---------|---------|
| 显示最近 20 次请求 | 点击列表项查看完整请求/响应 |
| Method、URL、状态码、耗时 | Request Headers/Body、Response Headers/Body |

### 1.3 状态码颜色说明

| 颜色 | 状态码范围 | 含义 |
|------|-----------|------|
| 绿色 | 200-299 | 请求成功 |
| 红色 | 400+ / 0 | 请求失败或网络错误 |
| 黄色 | 进行中... | 请求尚未完成 |

### 1.4 过滤不同类型的请求

```javascript
// 在浏览器 Console 中过滤
RequestLogger.logs.filter(l => l.method === 'WS')  // 仅看 WebSocket
RequestLogger.logs.filter(l => l.status >= 400)   // 仅看错误
RequestLogger.logs.filter(l => l.duration > 1000) // 仅看慢请求 (>1s)
```

---

## 2. 快捷测试场景

### 2.1 客服聊天页

快捷按钮会自动填充问题并发送：

| 按钮 | 问题内容 |
|------|---------|
| 📦 查询订单 | 查询我的订单状态 |
| 🛍️ 促销 | 有哪些促销商品？ |
| ↩️ 退换货 | 如何申请退换货？ |
| 💰 优惠 | 有什么优惠政策？ |

### 2.2 管理后台测试面板

预置了 5 个测试场景按钮：

| 场景 | 说明 | 连续发送的问题 |
|------|------|---------------|
| 商品咨询 | 测试商品问答 | 颜色/续航/推荐等 |
| 订单查询 | 测试订单意图 | 物流/地址/订单号 |
| 退换货 | 测试售后意图 | 退货/退款/运费 |
| 通用问答 | 测试通用知识 | 支付/配送/会员 |
| 多轮对话 | 测试上下文 | 连续 3 轮对话 |

### 2.3 自定义测试

1. 在输入框输入问题
2. 按 Enter 或点击「发送」
3. 结果自动显示在下方
4. 同时自动打开调试面板查看请求详情

---

## 3. WebSocket 心跳排查

### 3.1 心跳监控指标

侧边栏显示实时心跳状态：

| 指标 | 说明 |
|------|------|
| 发送 Ping | 客户端主动发送的心跳次数 |
| 收到 Pong | 服务器响应的次数 |
| 已发送 | 通过 WebSocket 发送的消息数 |
| 已接收 | 从服务器接收的消息数 |

### 3.2 正常心跳流程

```
客户端 ──(ping)──> 服务器
客户端 <──(pong)── 服务器
```

### 3.3 心跳异常排查

**Ping 发送但未收到 Pong**

可能原因：
1. 网络不稳定
2. 服务器过载无法响应
3. 反向代理（如 Nginx）超时配置过短

解决方案：
- 检查 Nginx 配置中的 `proxy_read_timeout`（建议 ≥ 60s）
- 检查后端 WebSocket 心跳处理逻辑
- 查看浏览器 Console 的 `[WS]` 日志

**连接频繁断开**

可能原因：
- 客户端配置的 `WS_HEARTBEAT_INTERVAL` 过短
- 服务器心跳超时设置过短

默认配置（前端）：
```javascript
const CONFIG = {
    WS_HEARTBEAT_INTERVAL: 30000,  // 30秒发送一次 Ping
    WS_HEARTBEAT_TIMEOUT: 60000,   // 60秒未收到 Pong 则断开
};
```

### 3.4 查看 WebSocket 日志

所有 WebSocket 消息都会被记录到调试面板：

1. 打开调试面板
2. 筛选 Method 为 `WS` 的记录
3. 点击查看消息详情

```javascript
// Console 中查看最近的消息
console.table(RequestLogger.logs.filter(l => l.method === 'WS'))
```

---

## 4. 常见问题排查

### 4.1 认证失败 (401)

**症状**：状态显示"认证失败"

**排查步骤**：
1. 打开调试面板
2. 查看 `/api/v1/debug/auth/token` 请求
3. 检查 Response 中是否返回了 token

**可能原因**：
- 后端认证服务未启动
- Token 过期时间设置过短
- 跨域问题

### 4.2 WebSocket 连接失败

**症状**：状态持续显示"连接中..."或"连接错误"

**排查步骤**：
1. 检查浏览器 Console 的 `[WS]` 日志
2. 查看网络面板中 WebSocket 连接请求的状态

**可能原因**：
| 原因 | 解决方案 |
|------|---------|
| Nginx 未启动 | `docker-compose up -d nginx` |
| Nginx WebSocket 配置缺失 | 检查 `nginx.conf` 中是否有 `proxy_set_header Upgrade` |
| Nginx 超时过短 | `proxy_read_timeout` 至少 300s |
| 跨域问题 | 检查后端 CORS 配置 |

### 4.3 消息发送无响应

**症状**：消息发送成功但没有回复

**排查步骤**：
1. 确认 WebSocket 状态为"已连接"
2. 查看「已发送」和「已接收」计数是否增加
3. 检查调试面板中是否有 `type: done` 的消息

**可能原因**：
- RAG 检索超时
- LLM 服务未启动
- 知识库为空

### 4.4 检索调试无结果

**症状**：检索调试执行后显示"无结果"

**排查步骤**：
1. 确认 Qdrant 服务运行正常：`docker ps | grep qdrant`
2. 检查知识库是否有数据：`curl http://localhost:6333/dashboard`
3. 查看检索调试的详细阶段（点击展开卡片）

**参数调优建议**：

| 参数 | 默认值 | 调整建议 |
|------|-------|---------|
| Top-K | 10 | 召回少时增大 |
| Rerank | 5 | 精排结果少时增大 |
| BM25 Weight | 0.30 | 关键词强时增大 |
| Hybrid Search | 开 | 建议保持开启 |

### 4.5 增量摄取失败

**症状**：执行后显示错误

**排查步骤**：
1. 查看操作日志中的详细错误信息
2. 检查 `data/` 目录中的文件格式
3. 确认支持的文件类型：
   - `products.json`
   - `knowledge_base.json`
   - `orders.json`

**JSON 格式示例**：

```json
{
  "title": "商品标题",
  "content": "商品描述内容...",
  "type": "product",
  "section_type": "introduction",
  "lang": "zh",
  "markets": ["VN"]
}
```

---

## 附录：API 端点参考

详见代码中的 `API_CONFIG` 配置对象。

### 客服聊天相关

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/debug/auth/token` | POST | 获取认证 Token |
| `/ws/chat` | WebSocket | 聊天 WebSocket |
| `/api/v1/products/context` | GET | 获取商品上下文 |

### 管理后台相关

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/health/detailed` | GET | 组件健康状态 |
| `/api/v1/health/alerts` | GET | 告警信息 |
| `/api/v1/admin/documents` | GET | 文档列表 |
| `/api/v1/admin/documents` | POST | 创建文档 |
| `/api/v1/admin/documents/{id}` | POST | 删除文档 |
| `/api/v1/admin/documents/{id}/reindex` | POST | 重建索引 |
| `/api/v1/admin/ingest/incremental` | POST | 增量摄取 |
| `/api/v1/debug/retrieve` | POST | 检索调试 |

---

## 获取帮助

如遇到本文未覆盖的问题：

1. 查看浏览器 Console 的 `[API]` 和 `[WS]` 日志
2. 检查 Docker 容器日志：`docker logs <container-name>`
3. 检查后端服务日志
