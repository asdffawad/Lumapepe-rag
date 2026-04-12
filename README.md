# RAG 电商智能客服系统

基于 RAG（检索增强生成）的电商智能客服系统，支持商品问答、订单查询、退换货辅助、多轮对话。

## 快速开始

### 1. 启动全部服务（推荐）

```bash
# 复制环境变量配置
cp .env.example .env

# 启动所有容器（mock 模式，不需要真实 API Key）
docker compose up -d

# 查看服务状态
docker compose ps

# 查看日志
docker compose logs -f backend
```

### 2. 访问服务

| 服务 | 地址 |
|------|------|
| 后端 API | http://localhost:8000 |
| Swagger 文档 | http://localhost:8000/docs |
| 前端页面 | http://localhost:80 |
| Prometheus 监控 | http://localhost:8000/metrics |
| Redis | localhost:6380 |
| MySQL | localhost:3307 |
| Milvus | localhost:19531（API）、localhost:9092（控制台） |

> **注意**：本地端口与 Docker 内部端口不同（MySQL 3306→3307、Redis 6379→6380、Milvus 9091→9092），如遇端口冲突请修改 `docker-compose.yml` 中的映射。

### 3. 使用真实 LLM（可选）

编辑 `.env` 文件：

```env
LLM_PROVIDER=qwen
LLM_MODEL_NAME=qwen-max
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxx
```

然后重启：

```bash
docker compose up -d --force-recreate backend
```

## 服务说明

| 容器 | 说明 |
|------|------|
| `rag-backend` | FastAPI 后端服务（Uvicorn） |
| `rag-celery-worker` | Celery 异步任务 Worker |
| `rag-celery-beat` | Celery 定时任务调度器（RedBeat） |
| `rag-mysql` | MySQL 8.0 数据库 |
| `rag-redis` | Redis 7 缓存（兼 Celery Broker） |
| `rag-milvus` | Milvus v2.4.14 向量数据库（Standalone 模式） |
| `rag-frontend` | 前端页面（Nginx 反向代理） |

## 开发测试

### 本地运行

```bash
# 激活 conda 环境（rag-test）
conda activate rag-test

# 运行后端
uvicorn backend.app.main:app --reload --port 8000
```

### 运行单元测试

```bash
# 在容器内运行
docker exec rag-backend python -m pytest tests/ -v

# 本地运行
python -m pytest backend/tests/ -v
```

**当前测试状态：75 passed**

### WebSocket 实时聊天（2026-04-12）

本次更新添加 WebSocket 双向通信支持，实现流式响应和消息撤回功能。

#### 新增功能

| 功能 | 说明 |
|------|------|
| WebSocket 端点 | `ws://localhost:8000/ws/chat?user_id=xxx&session_id=xxx` |
| 流式响应 | 消息分片实时推送，无需等待完整响应 |
| 自动重连 | 连接断开后指数退避重连（最大 30s） |
| 心跳保活 | 30s 间隔 ping/pong 保持连接 |
| 消息撤回 | 支持删除最后一条消息 |

#### WebSocket 消息协议

```json
// 客户端发送消息
{"type": "message", "content": "查询订单", "session_id": "可选"}

// 服务端响应
{"type": "chunk", "content": "您", "done": false}  // 流式片段
{"type": "chunk", "content": "好", "done": false}
...
{"type": "done", "session_id": "...", "intent": "order_query", "sources": [...]}

// 撤回最后一条消息
{"type": "delete", "message_id": 0}  // 0 表示删除最后一条

// 撤回响应
{"type": "message_deleted", "message_id": 123, "success": true}
```

#### 核心模块

| 文件 | 说明 |
|------|------|
| `app/api/ws_manager.py` | 连接管理器（单例模式，session/user 映射） |
| `app/api/websocket.py` | WebSocket 路由（消息处理/历史/撤回） |
| `database/message.py` | 新增 `delete()` 消息删除方法 |
| `services/session_service.py` | 新增 `delete_message()` 会话消息删除 |

#### 架构图

```
                    ┌─────────────────┐
                    │  WebSocket     │
                    │  Connection    │
                    └───────┬────────┘
                            │
         ┌──────────────────┼──────────────────┐
         ▼                  ▼                  ▼
   ┌───────────┐    ┌─────────────┐   ┌──────────┐
   │ Message   │    │   History   │   │  Delete  │
   │ (流式)    │    │  (查询)      │   │ (撤回)   │
   └─────┬─────┘    └──────┬──────┘   └────┬─────┘
         │                 │               │
         ▼                 ▼               ▼
   ┌─────────────────────────────────────────┐
   │        RAGOrchestrator / SessionService │
   └─────────────────────────────────────────┘
```

### 订单查询功能集成（2026-04-06）

本次更新将订单查询工具从 Mock 数据升级为 MySQL 真实数据源，实现企业级 RAG 系统订单服务能力。

#### 新增功能

| 工具 | 数据源 | 参数 | 说明 |
|------|--------|------|------|
| `order_query` | MySQL `orders` 表 | `user_id`（必填）, `order_id`（可选） | 优先精确查询，未提供则返回用户全部订单 |
| `logistics_query` | MySQL `orders` 表 | `logistics_no` | 查询物流进度和配送状态 |
| `refund_query` | MySQL `orders` 表 | `order_id`, `user_id` | 查询退款政策及退款资格 |

#### 架构变更

```
用户 Query → Intent Classifier → RAG Orchestrator
                                    │
                          ┌────────┴────────┐
                          │  tool_router   │
                          │  (熔断器保护)   │
                          └────────┬────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    ▼              ▼              ▼
             OrderRepository  LogisticsTool  RefundTool
                    │              │              │
                    └──────────────┴──────────────┘
                                   │
                                   ▼
                          MySQL `orders` 表
```

#### 核心模块

| 文件 | 说明 |
|------|------|
| `database/order.py` | `OrderRepository` 数据访问层（aiomysql） |
| `services/tools/__init__.py` | 三个企业工具（从 Mock 改为真实 MySQL 查询） |
| `services/rag_orchestrator.py` | 工具分流（Step 3.5），`tool_result` 注入 Prompt |
| `models/response.py` | `ChatResponse.tool_result` 字段，传递工具结果到前端 |
| `scripts/init_mysql.sql` | `orders` 表 DDL + 测试数据（`test-user` 4条订单） |

#### 测试验证

```bash
# 订单工具测试
python -m pytest backend/tests/test_order_tools.py -v

# 端到端测试（MySQL 真实数据）
# POST /api/v1/chat — "查询我的订单" → 返回真实订单列表
```

### 前端测试

访问管理后台的「测试」标签页（`/admin/`），或直接在聊天页面进行交互测试：

```bash
# 访问前端测试页面
http://localhost:80
```

**测试功能：**
- **聊天交互测试**：发送商品咨询、订单查询、退换货等各类问题，验证 RAG 回复质量
- **意图分类测试**：观察不同问题触发的意图标签（product_inquiry / order_query / refund / general）
- **多轮对话测试**：连续对话验证会话上下文保持
- **流式响应测试**：验证 SSE 流式输出是否正常
- **管理后台测试**：监控系统组件状态、告警阈值、知识库摄取流程

### 知识库 CRUD 测试

在管理后台的「知识库」标签页可直接进行文档增删改查测试，无需 API 工具：

| 操作 | 说明 | API |
|------|------|-----|
| 新建文档 | 填写标题、类型、JSON 内容，自动分块索引 | `POST /api/v1/admin/documents` |
| 查看详情 | 查看文档完整字段，含 content_raw JSON | `GET /api/v1/admin/documents/{id}` |
| 编辑文档 | 修改内容后自动删除旧向量并重建索引 | `PUT /api/v1/admin/documents/{id}` |
| 重建索引 | 删除指定文档旧向量，重新向量化入库 | `POST /api/v1/admin/documents/{id}/reindex` |
| 删除文档 | 软删除 MySQL 记录 + 删除 Milvus 关联向量 | `DELETE /api/v1/admin/documents/{id}` |
| 增量摄取 | 从 data/ 目录增量扫描文件并索引 | `POST /api/v1/admin/ingest` |
| 全量重建 | 删除现有全部向量数据后重新摄取 | `POST /api/v1/admin/ingest?reset=true` |

支持的文档类型：`product`（商品）、`policy`（政策）、`faq`（常见问题）、`manual`（手册）。

内容以 JSON 格式存储，支持多字段结构，分块器自动提取各节点文本分别向量化。

### 代码质量检查

```bash
# 语法检查
python -m py_compile backend/app/services/alerting.py

# Lint（需安装 ruff）
ruff check backend/app/
```

## 项目结构

```
RAG/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI 入口 + 生命周期管理
│   │   ├── config.py        # 配置管理（dataclasses + env）
│   │   ├── models/          # Pydantic 请求/响应模型
│   │   ├── api/             # API 路由层（chat, intent, health, admin, websocket）
│   │   ├── services/        # 业务逻辑层
│   │   │   ├── rag_orchestrator.py  # RAG 管道编排（单入口）
│   │   │   ├── session_service.py   # 会话管理（MySQL + Redis 双写）
│   │   │   ├── intent_service.py    # 意图分类（规则/LLM/混合）
│   │   │   ├── vector_retriever.py # 向量检索（Milvus 多路召回）
│   │   │   ├── reranker.py          # 重排（MMR + 词级 Jaccard 去重）
│   │   │   ├── query_rewriter.py    # 查询改写（同义词/指代消解）
│   │   │   ├── prompt_builder.py   # 动态 Prompt 构建
│   │   │   ├── llm_generator.py    # LLM 生成（流式 + Citation）
│   │   │   ├── llm_service.py       # LLM 抽象层（Factory 模式）
│   │   │   ├── embedding_service.py # 文本向量化（Cache-Aside 模式）
│   │   │   ├── tool_router.py       # 工具路由（熔断器保护）
│   │   │   ├── circuit_breaker.py   # 熔断器（CLOSED/HALF_OPEN/OPEN）
│   │   │   ├── alerting.py           # 业务告警（LLM/向量/延迟/内存）
│   │   │   └── tools/               # 工具实现（订单/物流/退款）
│   │   ├── database/        # MySQL 数据访问层（aiomysql）
│   │   │                     # session.py / message.py / document.py / log.py / **order.py**
│   │   ├── cache/           # Redis 缓存层（SessionCache / EmbedCache）
│   │   ├── vectorstore/     # Milvus 向量层（全异步 API）
│   │   ├── middleware/      # 中间件（限流、JWT、API-Key）
│   │   ├── logging_config.py # 结构化日志（structlog）
│   │   └── metrics.py        # Prometheus 监控指标
│   ├── celery_app/           # Celery 异步任务
│   └── tests/                # 单元测试（pytest-asyncio）
├── data/                      # 模拟数据（商品/订单/知识库）
├── scripts/                   # 初始化脚本（MySQL / Milvus / 数据摄取）
├── frontend/                  # 前端页面（Nginx）
├── docs/                     # 部署文档
├── docker-compose.yml        # 容器编排
└── Dockerfile                # 后端镜像构建
```

## API 文档

启动后访问 http://localhost:8000/docs 查看交互式 Swagger 文档。

### 核心接口

```
POST /api/v1/chat              # 发送消息（RAG + 会话持久化）
POST /api/v1/chat/stream       # 流式响应（SSE）
GET  /api/v1/chat/history/{session_id}  # 会话历史
POST /api/v1/intent            # 意图分类
GET  /api/v1/health            # 健康检查
GET  /api/v1/health/detailed   # 详细状态（熔断器/告警）
GET  /api/v1/health/alerts      # 告警状态
GET  /metrics                  # Prometheus 指标

# WebSocket 实时通信
WS   /ws/chat                 # WebSocket 聊天（流式响应 + 撤回）

# 知识库管理（admin）
GET    /api/v1/admin/documents          # 文档列表 + 统计
GET    /api/v1/admin/documents/{id}     # 文档详情（含 content_raw）
POST   /api/v1/admin/documents         # 新建文档 + 自动索引
PUT    /api/v1/admin/documents/{id}     # 更新文档 + 重索引
DELETE /api/v1/admin/documents/{id}     # 软删除文档 + 删除向量
POST   /api/v1/admin/documents/{id}/reindex  # 重建指定文档索引
POST   /api/v1/admin/ingest            # 触发知识库摄取
```

#### WebSocket 测试

```bash
# 使用 websockets 库测试
python -c "import websockets, json, asyncio
async def test():
    async with websockets.connect('ws://localhost:8000/ws/chat?user_id=test') as ws:
        await ws.send(json.dumps({'type': 'message', 'content': '你好'}))
        async for msg in ws:
            print(msg)
            if json.loads(msg).get('type') == 'done': break
asyncio.run(test())
"
```

## 架构特性

### RAG 管道

```
用户 Query
    │
    ▼
┌─────────────────┐
│  Query Rewriter  │ 同义词扩展 / 指代消解 / 关键词增强
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Intent Classifier│ 意图分类（规则/LLM/混合）
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Vector Retriever │ Milvus 多路召回（dense + metadata 过滤）
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│    Reranker     │ MMR 去重 + 词级 Jaccard + 业务规则加权
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Prompt Builder  │ 动态组装 + Token 预算控制
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  LLM Generator  │ 流式输出 + Citation 提取
└────────┬────────┘
         │
         ▼
     LLM Response
```

### 质量保障

- **熔断器**：LLM 调用连续失败 5 次自动熔断
- **降级策略**：Milvus 不可用时自动降级到 MockVectorRetriever
- **会话一致性**：Redis 缓存（TTL 30min）+ MySQL 持久化
- **幂等写入**：用户消息和助手消息成对写入，避免孤立记录
- **业务告警**：LLM/向量/延迟/内存四维度监控，连续异常触发告警

## 关闭服务

```bash
# 停止所有服务（保留数据卷）
docker compose down

# 停止并删除数据卷（清空数据，慎用）
docker compose down -v

# 重启单个服务
docker compose restart backend
```

## 近期修复（2026-04-05）

本次更新修复了以下企业级 RAG 系统关键问题：

### 本次测试发现并修复的 Bug

| 文件 | 问题 | 修复 |
|------|------|------|
| `intent_service.py` | `confidence_threshold=0.6` 过高，归一化分母错误（≈50），单次命中 confidence ≈ 0.02 → 所有短查询均 fallback 到 `general_chat` | 修正归一化分母为匹配意图自身关键词数量；阈值调至 0.05；单次命中 confidence ≈ 0.5 |
| `rag_orchestrator.py` | `_timed_rewrite` 超时降级 `RewrittenQuery` 缺少 `method` 和 `latency_ms` 参数 | 添加完整参数 `method="timeout_fallback", latency_ms=0` |
| `rag_orchestrator.py` | `_timed_generate` 超时（30s）直接抛出 `TimeoutError` → `RAGError` → 500 Internal Server Error | 捕获 LLM 超时，返回降级 `GenerationResult`（提示用户稍后重试），业务继续正常响应 |
| `test_rag.py` | `test_orchestrator_stream` 期望字符串，`"".join(chunks)` 对 `StreamChunk` 对象抛出 `TypeError` | 提取 `chunk.content` 属性 |

### 历史修复

| 类别 | 问题 | 修复 |
|------|------|------|
| Bug | 重复 `cache.set` 导致无效写操作 | 删除冗余写入 |
| Bug | `MilvusClient` 同步 `_connect()` 阻塞异步事件循环 | 改为 `asyncio.to_thread()` 异步调用 |
| Bug | Reranker 相似度使用字符级 Jaccard（中文不准确） | 改为基于标点/空格分割的词级实现 |
| Bug | 用户消息在 RAG 执行前写入，产生孤立消息 | RAG 成功后再成对写入消息 |
| Bug | 告警模块 `asyncio.run()` 在已有循环中创建新循环 | 改为 `ensure_future` + 线程池兜底 |
| Bug | Embedding 缓存命中率指标定义后未记录 | 集成 `_metrics.embedding_cache_hits/misses` |
| Bug | `tool_router.py` 重复定义 `CircuitBreriter` | 统一使用独立的 `circuit_breaker.py` |
| Bug | `RetrievedDoc` 在 `milvus_client` 和 `vector_retriever` 重复定义 | 合并为单一来源 |
| 改进 | 端口冲突：本地 MySQL/Redis 占用标准端口 | Docker 映射改为 3307/6380/9092 |
| 改进 | 测试覆盖：75 个单元测试全部通过 | pytest-asyncio + mock 依赖 |

### 测试验证

```bash
# Conda 环境（rag-test）
E:\conda_base\envs\rag-test\python.exe -m pytest backend/tests/ -v
# 结果：75 passed in 45.23s

# Docker 容器内
docker exec rag-backend python -m pytest tests/ -v
# 结果：75 passed in 4.42s

# Admin 集成测试（MySQL + Milvus 真实连接）
docker exec rag-backend python /app/backend/tests/test_admin_integration.py
# 结果：ALL 10 TESTS PASSED（CRUD + 向量检索 + 过滤搜索 + 删除）

# 端到端 API 测试（qwen-max 真实 LLM）
# POST /api/v1/chat — product_qa / order_query / refund_return / intent
# 结果：4/4 PASSED

# 订单工具测试（MySQL 真实数据）
docker exec rag-backend python -m pytest /app/backend/tests/test_order_tools.py -v
# OrderRepository / OrderTool / LogisticsTool / RefundTool / 端到端集成
```
