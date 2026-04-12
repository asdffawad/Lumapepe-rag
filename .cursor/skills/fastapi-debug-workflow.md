---
name: fastapi-debug-workflow
description: FastAPI 调试和问题排查工作流。用于排查 API 错误、链路追踪、Redis 缓存问题、MySQL 连接问题、Celery 任务卡住、Docker Compose 调试。包括常见错误码速查、日志分析、性能瓶颈定位。
---

# FastAPI 调试工作流

## 常见错误码速查

| HTTP 状态码 | 含义 | 快速定位 | 解决方案 |
|-----------|------|---------|---------|
| 422 | 请求参数校验失败 | 查看 `detail` 字段 | 检查 Pydantic 模型约束 |
| 429 | 限流触发 | `rate:user:{id}` Redis Key | 等待 1 分钟或调高阈值 |
| 500 | 内部服务错误 | 查看 FastAPI 日志 traceback | 检查 LLM 超时 / Milvus 连接 |
| 502 | 上游服务不可用 | 检查 Milvus/MySQL/Redis | 重启相关容器 |
| 503 | 服务过载 | Celery 队列积压 | 扩展 worker 或检查任务死循环 |
| 504 | 网关超时 | LLM 响应 > 60s | 调高 uvicorn 超时配置 |

## 请求链路追踪

每个请求在 `X-Request-ID` header 中透传请求 ID，用 grep 追踪全链路日志：

```bash
# 查看某个请求的完整日志链路
grep "req-abc123-xyz" backend/logs/*.log

# 日志格式规范（structlog）
# 2026-04-04 12:00:00 [orchestrator] request_started request_id=req-abc123-xyz ...
# 2026-04-04 12:00:00 [retriever] milvus_search started request_id=req-abc123-xyz ...
# 2026-04-04 12:00:01 [retriever] milvus_search completed duration_ms=234 request_id=req-abc123-xyz ...
# 2026-04-04 12:00:01 [orchestrator] request_completed total_ms=1234 request_id=req-abc123-xyz ...
```

### 链路各环节耗时基准

| 环节 | 正常耗时 | 异常阈值 |
|------|---------|---------|
| Redis 缓存读写 | < 5ms | > 50ms |
| MySQL 查询 | < 20ms | > 200ms |
| Milvus 检索 | < 100ms | > 500ms |
| Embedding | < 200ms | > 1s |
| LLM 生成 | < 3s | > 15s |

## Redis 问题排查

### 缓存穿透

现象：大量请求 Miss，MySQL 压力增大。

排查：

```bash
# 检查 embedding 缓存命中率
redis-cli INFO stats | grep keyspace_hits
redis-cli INFO stats | grep keyspace_misses

# 命中率 < 50% 需优化
```

解决方案：增加 Embedding 缓存 TTL，或预热热点 query 缓存。

### 热点 Key 雪崩

现象：大量 session Key 同时过期，瞬间大量请求打到 MySQL。

排查：

```bash
# 检查 Redis CPU 使用率
redis-cli INFO cpu

# 查看是否有大量 Key 同时过期
redis-cli --scan --pattern "session:*" | wc -l
```

解决方案：给 Key 过期时间加随机偏移（±10%），或用 Redis Hash 替代 String。

### 连接池耗尽

现象：`ConnectionPool Exhausted` 错误。

```python
# 检查当前连接数
redis-cli INFO clients
# 检查 blocked_clients（阻塞中的客户端）
```

解决方案：增加 Redis 最大连接数，或排查是否有连接泄漏（未正确关闭 async with）。

## MySQL 问题排查

### 连接池耗尽

排查：

```python
# 查看当前活跃连接数
await db.pool.fetchval("SELECT count(*) FROM pg_stat_activity WHERE datname='rag_customer_service'")
# 或在 asyncpg 中
active = db.pool.get_size()
```

常见原因：
- 异步函数中使用了同步 `psycopg2` 而非 `asyncpg`
- 连接未正确释放（缺少 `async with pool.acquire() as conn`）
- 慢查询占用连接

解决方案：

```python
# 确保使用 async with 释放连接
async with db.pool.acquire() as conn:
    result = await conn.fetch("SELECT * FROM sessions WHERE id = $1", session_id)
# 连接自动归还池中
```

### 慢查询

```sql
-- 查看最近慢查询（MySQL）
SHOW VARIABLES LIKE 'slow_query_log';
SHOW VARIABLES LIKE 'long_query_time';
SHOW FULL PROCESSLIST;
```

添加索引优化：

```sql
-- sessions 表常用查询添加索引
CREATE INDEX idx_sessions_user_id ON sessions(user_id);
CREATE INDEX idx_sessions_created_at ON sessions(created_at);
CREATE INDEX idx_messages_session_id ON messages(session_id);
CREATE INDEX idx_messages_created_at ON messages(created_at);
```

## Celery 任务卡住

排查步骤：

```bash
# 1. 查看任务状态
celery -A celery_app inspect active
celery -A celery_app inspect scheduled
celery -A celery_app inspect reserved

# 2. 查看 worker 日志
docker logs celery_worker_1

# 3. 查看队列积压情况
docker exec redis redis-cli LLEN celery

# 4. 强制终止卡住的任务
celery -A celery_app revoke <task_id> --terminate
```

常见原因及解决：

| 原因 | 现象 | 解决 |
|------|------|------|
| Worker 挂掉 | `celery inspect active` 显示无任务但队列有积压 | `docker-compose restart celery_worker` |
| 任务死循环 | 单个任务运行时间 > 1h | 检查任务代码，增加超时 `time_limit` |
| 内存爆炸 | Worker OOM，被 K8s/Docker 杀掉 | 添加 `max_memory_per_child` 限制 |
| Broker 断开 | `celery inspect` 超时 | 重启 Redis 或检查网络 |

## Docker Compose 调试

```bash
# 查看所有容器状态
docker-compose ps

# 查看后端日志（实时）
docker-compose logs -f backend

# 查看指定容器日志
docker-compose logs -f --tail=100 celery_worker

# 重启单个服务
docker-compose restart backend

# 进入容器调试
docker-compose exec backend bash
docker-compose exec mysql mysql -u root -p

# 检查容器健康状态
docker inspect milvus | grep -A 5 "Health"
docker inspect mysql | grep -A 5 "Health"

# 查看资源使用
docker stats

# 完全重建（清除缓存）
docker-compose down -v
docker-compose up --build -d
```

## 性能调优

### uvicorn 配置

```bash
# 启动命令（生产环境）
uvicorn main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers 4 \
  --loop uvloop \
  --http httptools \
  --limit-concurrency 100 \
  --limit-max-requests 1000 \
  --timeout-keep-alive 30
```

### Milvus 索引调优

```python
# 查询参数优化
search_params = {
    "metric_type": "IP",
    "params": {
        "ef": 128  # 搜索精度，值越大精度越高但越慢
    }
}
results = milvus.search(
    collection_name="products",
    data=[query_embedding],
    limit=10,
    search_params=search_params,
    consistency_level="Eventually"  # 最终一致性，读性能更高
)
```

### Redis 连接池

```python
# 增大连接池
redis_client = await RedisClient.connect(
    url="redis://localhost:6379",
    max_connections=50  # 默认 50
)
```
