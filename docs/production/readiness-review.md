# RAG 智能客服 - 生产环境上线审查报告

> 审查日期：2026-04-20
> 审查范围：架构 / 性能 / 稳定性 / 安全 / RAG效果 / 可观测性 / 工程规范
> 审查结论：暂缓上线，需修复 P0 阻断问题

---

## 1. 项目概述

### 1.1 系统架构

```
[客户端] <--WebSocket/HTTP--> [FastAPI 网关]
                                   |
                    +--------------+---------------+
                    |              |               |
              [JWT 鉴权]    [限流中间件]    [RAG Orchestrator]
                                              |
              +-------------+------------------+-------------+
              |             |                  |             |
        [意图分类]    [Query改写]        [向量检索]    [LLM生成]
              |             |                  |             |
        [工具分流]    [商品检索]        [Milvus]      [通义千问]
              |                              |
        [订单/物流]                     [Embedding缓存]
```

### 1.2 技术栈

| 层级 | 技术选型 |
|------|----------|
| API 框架 | FastAPI + Uvicorn |
| 实时通信 | WebSocket |
| 向量数据库 | Milvus Lite（单机） |
| 关系数据库 | MySQL (aiomysql) |
| 缓存 | Redis |
| LLM | 通义千问（DashScope API） |
| Embedding | text-embedding-v3 |
| 消息队列 | Celery + Redis |
| 监控 | Prometheus + AlertMonitor |

### 1.3 核心模块

- `app/api/websocket.py` - WebSocket 聊天入口
- `app/services/rag_orchestrator.py` - RAG 管道编排
- `app/services/llm_service.py` - LLM Provider（Qwen）
- `app/api/ws_manager.py` - WebSocket 连接管理
- `app/middleware/auth.py` - JWT 鉴权 + 限流

---

## 2. 审查结论

### 2.1 总体评估

| 维度 | 评级 | 说明 |
|------|------|------|
| 架构设计 | B+ | 设计合理，但单机组件限制扩展性 |
| 性能 | B- | 基础具备，但流式输出缺重试，延迟需优化 |
| 稳定性 | C+ | 有超时/熔断，但消息保存存在一致性问题 |
| 安全性 | D | JWT 默认密钥 + 管理员 API 无认证，必须修复 |
| RAG 效果 | B | 管道完善，但缺量化评估 |
| 可观测性 | B | 日志/Metrics 完善，但缺分布式追踪 |
| 工程规范 | A- | 配置规范，依赖注入良好 |

### 2.2 上线决策

**结论：暂缓上线（Block）**

- P0 阻断问题：**8 个**（含 2 个安全漏洞）
- P1 重要风险：**11 个**
- P2 可选改进：**7 个**

### 2.3 核心风险摘要

> ⚠️ **安全红线**：JWT 默认密钥可被伪造令牌，管理员 API 可被未授权访问
> ⚠️ **可用性红线**：多 worker 部署时 WebSocket 连接管理失效
> ⚠️ **数据一致性红线**：AI 回答已发送但数据库保存失败

---

## 3. P0 阻断问题（必须修复）

> 以下问题为上线阻断项，必须在部署前全部修复。

### 3.1 【P0-01】JWT 默认密钥不安全

**文件位置**：`app/middleware/auth.py:22`

```python
_JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-in-production")
```

**问题描述**：

生产环境如果未设置 `JWT_SECRET`，系统使用硬编码的默认值 "dev-secret-change-in-production"。攻击者可利用此密钥伪造任意用户的 JWT token，完全接管用户身份。

**影响范围**：所有使用 JWT 认证的接口和 WebSocket 连接

**风险等级**：**P0 - 严重安全漏洞**

**修复建议**：

```python
# 启动时强制检查
_JWT_SECRET = os.getenv("JWT_SECRET")
if not _JWT_SECRET or len(_JWT_SECRET) < 32:
    raise ValueError(
        "JWT_SECRET must be set in production with at least 32 characters. "
        "Generate with: openssl rand -hex 32"
    )
```

**验收标准**：
- [ ] 启动时检测到未设置 `JWT_SECRET` 抛出异常
- [ ] `JWT_SECRET` 长度 < 32 字符时抛出异常

---

### 3.2 【P0-02】管理员 API 无认证

**文件位置**：`app/middleware/auth.py:176-177`

```python
skip_exact = {
    "/api/v1/admin/ingest",
    "/api/v1/admin/documents",
}
if request.url.path in skip_exact:
    return AuthUser(user_id="admin", role="admin")
```

**问题描述**：

`/api/v1/admin/ingest`（知识库导入）和 `/api/v1/admin/documents`（文档管理）端点完全跳过认证，返回 admin 权限。任何人可调用这些接口修改知识库内容或触发数据导入。

**影响范围**：知识库内容可被未授权篡改

**风险等级**：**P0 - 严重安全漏洞**

**修复建议**：

```python
# 方案A：使用 API Key 认证
skip_exact = {
    "/api/v1/admin/ingest",
    "/api/v1/admin/documents",
}
if request.url.path in skip_exact:
    api_key = request.headers.get("X-Admin-Key")
    if not api_key or not verify_admin_key(api_key):
        raise HTTPException(status_code=401, detail="Admin authentication required")
    return AuthUser(user_id="admin", role="admin")
```

**验收标准**：
- [ ] 管理员 API 请求无 API Key 返回 401
- [ ] 使用错误 API Key 返回 403
- [ ] 使用正确 API Key 正常访问

---

### 3.3 【P0-03】LLM 流式输出无重试机制

**文件位置**：`app/services/llm_service.py:518-574`

**问题描述**：

`QwenProvider.chat_stream()` 在流式响应中途失败时（如网络抖动），没有任何重试逻辑。对比非流式的 `chat()` 方法有完整的重试机制，但流式输出缺失。

```python
# chat() 有重试
for attempt in range(self.max_retries):
    try:
        http_resp = await client.post(...)
        # ...
    except httpx.RequestError:
        await asyncio.sleep(wait)
        continue

# chat_stream() 无重试
async with client.stream(...) as response:
    async for line in response.aiter_lines():
        yield content
```

**影响范围**：网络不稳定时用户收到不完整回答，无法区分"输出完成"和"连接断开"

**风险等级**：**P0 - 可用性**

**修复建议**：

```python
async def chat_stream(self, ...):
    for attempt in range(self.max_retries):
        try:
            async with client.stream(...) as response:
                # ... 处理流式响应 ...
                return  # 成功后返回
        except httpx.RequestError as e:
            if attempt < self.max_retries - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            yield "[错误] 网络异常，请稍后重试。"
            return
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (429, 500, 502, 503):
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
            yield f"[错误] 服务异常 ({e.response.status_code})，请稍后重试。"
            return
```

**验收标准**：
- [ ] 流式输出中途网络错误时自动重试（最多 3 次）
- [ ] 重试耗尽后发送友好错误消息
- [ ] 重试期间日志记录 attempt 次数

---

### 3.4 【P0-04】消息保存失败但回答已发送

**文件位置**：`app/api/websocket.py:390-413`

**问题描述**：

AI 回答通过 WebSocket 发送后，再保存到数据库。如果 `add_message()` 失败，AI 回答已发送给用户但数据库中丢失。用户刷新页面或切换设备后，该对话消失。

```python
async for chunk in orchestrator.run_stream(...):
    await websocket.send_json({"type": "chunk", ...})  # 用户已看到

try:
    await session_svc.add_message(...)  # AI回答保存
except Exception as e:
    # 用户已看到回答，但保存失败
    await websocket.send_json({"type": "error", "code": "SAVE_ERROR"})
    return
```

**影响范围**：会话历史数据不一致，可能丢失 AI 回复

**风险等级**：**P0 - 数据一致性**

**修复建议**：

```python
# 方案A：消息暂存 + 异步补偿
class PendingMessages:
    """待确认的消息队列"""
    def __init__(self, redis_client):
        self._r = redis_client
    
    async def save_pending(self, session_id: str, role: str, content: str):
        key = f"pending:{session_id}"
        await self._r.rpush(key, json.dumps({"role": role, "content": content}))
    
    async def confirm(self, session_id: str):
        key = f"pending:{session_id}"
        messages = await self._r.lrange(key, 0, -1)
        await self._r.delete(key)
        return [json.loads(m) for m in messages]

# 使用
accumulated = ""
async for chunk in orchestrator.run_stream(...):
    await websocket.send_json({"type": "chunk", ...})
    accumulated += chunk.content

# 先保存到 Redis，再异步落库
await pending.save_pending(session_id, "assistant", accumulated)
try:
    await session_svc.add_message(...)
    await pending.confirm(session_id)  # 从 Redis 移除
except Exception:
    pass  # Redis 中的数据由后台 worker 补偿

await websocket.send_json({"type": "done", ...})
```

**验收标准**：
- [ ] AI 回答先暂存 Redis，再发送 done 消息
- [ ] 保存失败时消息不丢失（可由后台 worker 补偿）
- [ ] 添加 `save_error_total` 指标监控

---

### 3.5 【P0-05】多 worker 部署时连接管理失效

**文件位置**：`app/api/ws_manager.py:95-151`

**问题描述**：

`ConnectionManager` 是进程内单例，使用 `asyncio.Lock()` 保护状态。部署多 worker（`uvicorn workers > 1`）时，每个进程有独立的 ConnectionManager 实例，用户可能在进程 A 建立连接，但请求被路由到进程 B，无法找到该连接。

```python
class ConnectionManager:
    _instance: Optional[ConnectionManager] = None  # 进程内单例
    
    def __init__(self, config=None):
        self._connections: Dict[str, ConnectionInfo] = {}  # 进程内存储
        self._lock = asyncio.Lock()  # 进程内锁
```

**影响范围**：多 worker 部署时 WebSocket 连接完全不可用

**风险等级**：**P0 - 可用性**

**修复建议**：

```python
# 方案A：使用 Redis 分布式连接管理
class DistributedConnectionManager:
    def __init__(self, redis_client):
        self._r = redis_client
    
    async def connect(self, session_id: str, user_id: str, ws_url: str):
        key = f"ws:conn:{session_id}"
        await self._r.hset(key, mapping={
            "user_id": user_id,
            "ws_url": ws_url,
            "connected_at": time.time(),
        })
        await self._r.expire(key, 3600)
    
    async def is_connected(self, session_id: str) -> bool:
        return await self._r.exists(f"ws:conn:{session_id}")

# 方案B：使用 Sticky Session（反向代理层）
# Nginx 配置：
# upstream backend {
#     ip_hash;  # 保持同一 IP 的请求到同一后端
#     server 127.0.0.1:8000;
#     server 127.0.0.1:8001;
# }

# 方案C：切换到支持分布式的 WebSocket 框架
# 如：FastAPI-WebSocket + Redis Pub/Sub
```

**验收标准**：
- [ ] 单 worker 部署正常工作
- [ ] 多 worker 部署（workers=2）连接正常
- [ ] 全局连接数限制（max_global）在多 worker 下生效

---

### 3.6 【P0-06】milvus-lite 无法水平扩展

**文件位置**：`app/vectorstore/milvus_client.py:1-20`

**问题描述**：

`MilvusClient` 底层使用 milvus-lite（嵌入式本地向量数据库），数据存储在本地文件。多实例部署时各实例数据不同步，无法做副本扩展。

```python
class MilvusClient:
    """
    milvus-lite 是 pymilvus 内置的本地向量数据库：
    - 无需外部服务（etcd/minio）
    - 数据存储在本地文件
    - 适合开发测试和单机部署
    """
```

**影响范围**：1000+ 并发时向量检索成为瓶颈，无法做高可用

**风险等级**：**P0 - 可扩展性**

**修复建议**：

```python
# 方案A：切换到 Milvus 分布式集群（推荐）
# docker-compose.yml 添加：
# milvus-etcd:
#   image: quay.io/coreos/etcd:v3.5.5
# milvus-minio:
#   image: minio/minio:latest
# milvus-standalone:
#   image: milvusdb/milvus:v2.3.x
#   environment:
#     ETCD_ENDPOINTS: milvus-etcd:2379
#     MINIO_ADDRESS: milvus-minio:9000

# 方案B：切换到 Qdrant（Rust 实现，性能更好）
class QdrantClient:
    """分布式向量数据库"""
    def __init__(self, url: str, collection: str):
        self.client = qdrant_client.QdrantClient(url=url)
        self.collection = collection

# 方案C：使用云服务（如果预算允许）
# - Azure AI Search
# - Pinecone
# - Weaviate Cloud
```

**验收标准**：
- [ ] 上线前完成向量检索基准压测（目标：P99 < 100ms）
- [ ] 多副本部署时数据同步正常
- [ ] 监控 `vector_search_duration_seconds` P99

---

### 3.7 【P0-07】心跳机制可能误判正常连接

**文件位置**：`app/api/ws_manager.py:385-392`

**问题描述**：

心跳发送使用同步 `send_json()`，如果网络慢导致发送超时（即使后续成功），会触发异常并被判定为死连接。实际测试中发现，心跳发送耗时超过一定阈值时可能导致连接被误杀。

```python
try:
    await info.websocket.send_json({"type": "ping"})  # 可能阻塞
    info.last_activity = now
except Exception as e:
    dead_sessions.append(session_id)  # 可能误判
```

**影响范围**：正常用户连接被意外断开

**风险等级**：**P0 - 可用性**

**修复建议**：

```python
async def _send_ping_with_timeout(self, websocket: WebSocket, timeout: float = 5.0):
    """带超时的 ping 发送，避免阻塞"""
    try:
        await asyncio.wait_for(
            websocket.send_json({"type": "ping"}),
            timeout=timeout
        )
        return True
    except asyncio.TimeoutError:
        return False  # 超时不视为失败，等待下次重试

# 心跳循环中使用
for session_id, info in list(self._connections.items()):
    success = await self._send_ping_with_timeout(info.websocket, timeout=5.0)
    if success:
        info.last_activity = time.time()
    else:
        # 连续失败才断开
        info.consecutive_failures = getattr(info, 'consecutive_failures', 0) + 1
        if info.consecutive_failures >= 3:
            dead_sessions.append(session_id)
```

**验收标准**：
- [ ] 网络慢时心跳发送带 5s 超时
- [ ] 单次 ping 失败不立即断开（连续 3 次才断开）
- [ ] 心跳日志包含 `success` 字段

---

### 3.8 【P0-08】CORS 允许通配符生产警告

**文件位置**：`app/main.py:263-271`

**问题描述**：

虽然代码检测到 `CORS_ORIGINS=*` 时会设置 `allow_credentials=False`，但 `allow_origins=["*"]` 仍然允许所有来源。生产环境配置 `*` 会带来 CSRF 风险。

```python
if "*" in cors_origins:
    warnings.warn("CORS 配置不安全...")
    allow_creds = False
# allow_origins 仍为 ["*"]
```

**影响范围**：CSRF 攻击风险

**风险等级**：**P0 - 安全配置**

**修复建议**：

```python
# 方案A：启动时拒绝
if "*" in cors_origins and not config.debug:
    raise ValueError(
        "CORS_ORIGINS cannot be '*' in production. "
        "Set specific domains: CORS_ORIGINS=https://example.com"
    )

# 方案B：开发/生产自动切换
cors_origins = config.cors_origins
if "*" in cors_origins and not config.debug:
    logger.warning("CORS is set to '*' in production mode, this is insecure!")
    # 自动限制为已知域名（从 JWT 或其他途径获取）
```

**验收标准**：
- [ ] 生产环境（debug=False）配置 `CORS_ORIGINS=*` 启动时警告或拒绝
- [ ] 开发环境配置 `*` 正常可用

---

## 4. P1 优化建议

> 以下问题影响系统可维护性和长期稳定性，建议在上线后短期内（1-2 周内）修复。

### 4.1 【P1-01】RAG Pipeline 串行执行延迟高

**文件**：`app/services/rag_orchestrator.py:182-205`

**问题**：Query改写、向量检索、Rerank 串行执行，总延迟 = rewrite(10s) + retrieve(1s) + rerank(1s) + LLM(60s)

**建议**：rewrite 和 intent classification 可并行；非 product_qa 意图跳过商品检索

---

### 4.2 【P1-02】JWT 无防重放机制

**文件**：`app/middleware/auth.py:77-114`

**问题**：JWT 一旦签发，过期前可被任何持有者使用

**建议**：在 payload 添加 `jti`（JWT ID），Redis 存储黑名单

---

### 4.3 【P1-03】Token 计数不精确

**文件**：`app/services/llm_service.py:576-577`

**问题**：流式输出用 `total_output_chars // 2` 估算 token 数

**建议**：使用 tiktoken 本地精确计算，或接受 ±20% 误差

---

### 4.4 【P1-04】缺少分布式追踪

**文件**：全局

**问题**：没有 OpenTelemetry/Jaeger，无法跨服务追踪请求

**建议**：添加 OpenTelemetry SDK，日志中包含 trace_id

---

### 4.5 【P1-05】Alert 无外发机制

**文件**：`app/services/alerting.py:46-61`

**问题**：告警只有 `logger.warning()`，无通知通道

**建议**：添加钉钉/企微 WebHook 通知

---

### 4.6 【P1-06】RAG 检索质量未量化

**文件**：全局

**问题**：没有 ground truth 测试集，无法评估幻觉率

**建议**：建立 100-500 个 Q&A benchmark，定期评估

---

### 4.7 【P1-07】无低置信度 Fallback

**文件**：`app/services/rag_orchestrator.py`

**问题**：检索结果差时 LLM 仍尝试回答，可能产生幻觉

**建议**：rerank 分数 < 0.3 时提示转人工

---

### 4.8 【P1-08】Redis 连接池未配置上限

**文件**：`app/cache/redis_client.py:22`

**问题**：未设置 `max_connections`，高并发可能耗尽连接

**建议**：`max_connections=50`（根据预估并发调整）

---

### 4.9 【P1-09】milvus-lite 性能上限未知

**文件**：`app/vectorstore/milvus_client.py`

**问题**：无压测数据，容量规划缺失

**建议**：上线前完成基准压测，记录 QPS 上限

---

### 4.10 【P1-10】商品检索可并行化

**文件**：`app/services/rag_orchestrator.py:179-180`

**问题**：product_qa 意图时商品检索和 RAG 检索串行

**建议**：评估是否可并行，节省约 100-300ms

---

### 4.11 【P1-11】配置默认值可能不适配生产

**文件**：`app/config.py`

**问题**：`MYSQL_POOL_SIZE=10`、`WS_MAX_GLOBAL=10000` 可能不合理

**建议**：添加启动时配置校验（最小值/最大值限制）

---

## 5. P2 改进项

| ID | 问题 | 建议 | 优先级 |
|----|------|------|--------|
| P2-01 | 缺少幂等性设计 | 客户端生成 `message_id`，服务端去重 | P2 |
| P2-02 | API 版本控制缺失 | 添加 `/api/v2/` 路径 | P2 |
| P2-03 | Metrics 维度不完整 | 添加 `error_type`、`intent` 维度 | P2 |
| P2-04 | 未使用 Pydantic v2 语法 | 迁移到 `model_config` | P2 |
| P2-05 | 缺少健康检查详细状态 | `/health` 返回各组件状态 | P2 |
| P2-06 | 日志未区分请求边界 | 使用 `trace_id` 聚合单次请求 | P2 |
| P2-07 | 文档未说明降级策略 | 添加降级流程文档 | P2 |

---

## 6. 上线检查清单

> **执行要求**：上线前必须逐项确认，可使用 CI/CD 自动检查。

### 6.1 安全检查

- [ ] **JWT_SECRET 已配置**，长度 >= 32 字符，且非默认值
- [ ] **管理员 API 已添加认证**（API Key 或其他方式）
- [ ] **CORS_ORIGINS 已配置具体域名**，非 `*`
- [ ] 所有内部服务（MySQL、Redis、Milvus）端口不对外暴露
- [ ] 敏感环境变量（API Key、密码）不写入代码仓库
- [ ] 已设置 `DEBUG=False` 或移除 debug 路由

### 6.2 部署检查

- [ ] **milvus-lite 已升级为 Milvus 分布式集群**（或已验证单机 QPS 满足需求）
- [ ] **WebSocket 连接管理已支持多 worker**（Redis 存储或 Sticky Session）
- [ ] MySQL 连接池大小 `MYSQL_POOL_SIZE` 已根据并发调整（建议 >= 20）
- [ ] Redis 连接池 `max_connections` 已配置
- [ ] `WS_MAX_GLOBAL` 已根据实际容量设置

### 6.3 稳定性检查

- [ ] **LLM 流式输出已添加重试机制**（最多 3 次）
- [ ] **消息保存失败已添加补偿机制**（Redis 暂存或补偿队列）
- [ ] **心跳机制已优化**（带超时 + 连续失败计数）
- [ ] 所有外部 API 调用（LLM、DashScope）有熔断器保护
- [ ] 启动时各依赖服务（MySQL、Redis、Milvus）有健康检查
- [ ] 进程异常退出时有优雅关闭机制

### 6.4 监控检查

- [ ] Prometheus `/metrics` 端点可访问
- [ ] 关键指标已配置告警规则：
  - [ ] LLM 错误率 > 5%
  - [ ] 向量检索延迟 P99 > 500ms
  - [ ] 内存使用 > 80%
  - [ ] WebSocket 连接数 > 8000
- [ ] 告警通知已配置（钉钉/企微/邮件）
- [ ] 日志聚合已配置（ELK/Loki）
- [ ] 已设置 `trace_id` 透传

### 6.5 数据检查

- [ ] 知识库已导入并验证数据质量
- [ ] 商品字典已生成并验证覆盖率
- [ ] 测试用例已覆盖主要场景（订单查询、退换货、物流）
- [ ] RAG 检索效果已验证（幻觉率、可回答率）

### 6.6 文档检查

- [ ] API 文档已更新（/docs 可访问）
- [ ] 部署文档已包含所有环境变量说明
- [ ] 故障排查文档已编写
- [ ] 降级策略已文档化

---

## 7. 后续优化路线图

### 7.1 第一阶段：上线后 1 周内（P1 修复）

```
┌─────────────────────────────────────────────────────────────┐
│  P1-01: RAG Pipeline 并行化                                  │
│  P1-02: JWT 防重放机制                                       │
│  P1-04: 分布式追踪 (OpenTelemetry)                          │
│  P1-05: Alert 外发通知                                       │
│  P1-07: 低置信度 Fallback                                    │
│  P1-08: Redis 连接池配置                                      │
│  P1-09: 向量检索压测                                          │
└─────────────────────────────────────────────────────────────┘
```

### 7.2 第二阶段：上线后 1 个月内（P2 + 改进）

```
┌─────────────────────────────────────────────────────────────┐
│  P2-01: 幂等性设计                                          │
│  P2-03: Metrics 维度完善                                    │
│  P2-05: 健康检查详细状态                                     │
│  P1-10: 商品检索并行化                                        │
│  P1-11: 配置值校验                                          │
│  性能: 向量检索优化 / 缓存策略调优                           │
│  质量: RAG 评估数据集建立                                    │
└─────────────────────────────────────────────────────────────┘
```

### 7.3 第三阶段：稳定运营期（长期）

```
┌─────────────────────────────────────────────────────────────┐
│  扩展: 多语言支持                                            │
│  扩展: 多租户隔离                                            │
│  监控: 业务指标仪表盘                                        │
│  安全: SOC2 / 等保合规                                       │
│  成本: LLM 调用成本优化                                      │
│  质量: A/B 测试框架                                          │
└─────────────────────────────────────────────────────────────┘
```

---

## 附录

### A. 相关文件清单

| 文件 | 说明 |
|------|------|
| `backend/app/api/websocket.py` | WebSocket 入口 |
| `backend/app/services/rag_orchestrator.py` | RAG 管道编排 |
| `backend/app/services/llm_service.py` | LLM Provider |
| `backend/app/api/ws_manager.py` | 连接管理 |
| `backend/app/middleware/auth.py` | JWT 鉴权 |
| `backend/app/config.py` | 配置管理 |
| `backend/app/vectorstore/milvus_client.py` | 向量数据库 |
| `backend/app/services/alerting.py` | 告警监控 |

### B. 环境变量清单

| 变量 | 说明 | 是否必填 |
|------|------|----------|
| `JWT_SECRET` | JWT 签名密钥（>=32字符） | **是** |
| `DASHSCOPE_API_KEY` | 通义千问 API Key | **是** |
| `CORS_ORIGINS` | 允许的域名列表 | **是（生产）** |
| `MYSQL_PASSWORD` | 数据库密码 | **是** |
| `DEBUG` | 调试模式 | **否（生产必 False）** |

### C. 监控指标清单

| 指标 | 说明 | 告警阈值 |
|------|------|----------|
| `chat_requests_total` | 聊天请求数 | - |
| `chat_request_duration_seconds` | 请求延迟 | P99 > 30s |
| `llm_calls_total` | LLM 调用数 | 错误率 > 5% |
| `llm_call_duration_seconds` | LLM 延迟 | P99 > 60s |
| `vector_search_duration_seconds` | 向量检索延迟 | P99 > 500ms |
| `active_sessions` | 活跃会话数 | > 8000 |
| `embedding_cache_hits_total` | Embedding 缓存命中 | 命中率 < 60% |

---

> **文档版本**：v1.0
> **维护者**：技术团队
> **下次审查**：P0 问题修复后
