# RAG 电商智能客服系统 - 生产部署指南

## 目录

1. [部署前检查清单](#1-部署前检查清单)
2. [环境配置](#2-环境配置)
3. [Docker Compose 部署](#3-docker-compose-部署)
4. [Kubernetes 部署](#4-kubernetes-部署)
5. [监控配置](#5-监控配置)
6. [告警配置](#6-告警配置)
7. [故障排查](#7-故障排查)
8. [性能调优](#8-性能调优)
9. [安全加固](#9-安全加固)
10. [备份与恢复](#10-备份与恢复)

---

## 1. 部署前检查清单

### 环境要求

| 组件 | 最低配置 | 推荐配置 |
|------|---------|---------|
| CPU | 4 核 | 8 核 |
| 内存 | 8 GB | 16 GB |
| 磁盘 | 50 GB SSD | 200 GB SSD |
| Docker | 20.10+ | 24.0+ |
| Docker Compose | 2.0+ | 2.20+ |

### 依赖检查

- [ ] Docker 已安装并运行
- [ ] Docker Compose 已安装
- [ ] 端口未被占用：80, 443, 3306, 6379, 8000, 19530, 9091
- [ ] 已获取 DashScope API Key（通义千问）
- [ ] 防火墙/安全组已配置

### 数据准备

- [ ] 已准备商品数据 `data/products.json`
- [ ] 已准备知识库 `data/knowledge_base.json`
- [ ] 已准备订单数据 `data/orders.json`
- [ ] MySQL 数据库已初始化（运行 `scripts/init_mysql.sql`）

---

## 2. 环境配置

### 2.1 创建 `.env` 配置文件

```bash
cp .env.example .env
```

编辑 `.env`：

```env
# === LLM 配置（必填）===
LLM_PROVIDER=qwen
LLM_MODEL_NAME=qwen-max
LLM_FALLBACK_MODELS=qwen-plus,qwen-turbo
DASHSCOPE_API_KEY=sk-your-actual-key-here
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_TIMEOUT=60
LLM_MAX_RETRIES=3
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=2048

# === 安全配置（必填）===
JWT_SECRET=your-random-secret-here-32chars
# 生产环境：JWT_SECRET=$(openssl rand -hex 32)
CORS_ORIGINS=https://your-domain.com,https://admin.your-domain.com

# === MySQL 配置 ===
MYSQL_HOST=mysql
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your-strong-password-here
MYSQL_DATABASE=rag_customer_service
MYSQL_POOL_SIZE=10

# === Redis 配置 ===
REDIS_URL=redis://redis:6379/0

# === Milvus 配置 ===
MILVUS_HOST=milvus
MILVUS_PORT=19530
MILVUS_COLLECTION=knowledge_base

# === 限流配置 ===
RATE_LIMIT_ENABLED=true
RATE_LIMIT_GLOBAL=200
RATE_LIMIT_USER=60
RATE_LIMIT_WINDOW=60

# === RAG 配置 ===
RAG_TOP_K=10
RAG_RERANK_TOP_K=5

# === 知识库自动摄取 ===
AUTO_INGEST=true

# === 监控配置 ===
LOG_LEVEL=INFO
DEBUG=false
```

### 2.2 生成安全的 JWT Secret

```bash
# Linux/macOS
openssl rand -hex 32

# Windows PowerShell
powershell -Command "[Convert]::ToBase64String((1..64 | ForEach-Object { Get-Random -Maximum 256 }) )"
```

---

## 3. Docker Compose 部署

### 3.1 启动服务

```bash
# 一键启动（推荐）
bash scripts/startup.sh

# 或手动启动
docker-compose up -d

# 查看服务状态
docker-compose ps

# 查看日志
docker-compose logs -f backend
```

### 3.2 验证部署

```bash
# 1. 检查健康状态
curl http://localhost:8000/api/v1/health

# 2. 检查详细状态
curl http://localhost:8000/api/v1/health/detailed

# 3. 检查告警状态
curl http://localhost:8000/api/v1/health/alerts

# 4. 检查 Prometheus 指标
curl http://localhost:8000/metrics

# 5. 检查知识库文档数量
curl http://localhost:8000/api/v1/admin/documents \
  -H "Authorization: Bearer <admin-token>"
```

### 3.3 停止服务

```bash
# 停止所有服务（保留数据）
docker-compose down

# 停止并清理数据卷（慎用）
docker-compose down -v

# 重启单个服务
docker-compose restart backend
```

### 3.4 服务访问地址

| 服务 | 地址 | 说明 |
|------|------|------|
| 前端界面 | http://localhost:80 | 聊天界面 |
| API 文档 | http://localhost:8000/docs | Swagger UI |
| 健康检查 | http://localhost:8000/api/v1/health | 服务存活 |
| Prometheus | http://localhost:8000/metrics | 监控指标 |

---

## 4. Kubernetes 部署

> TODO: K8s 部署配置待补充

### 4.1 Helm Chart

```bash
# 待实现
helm install rag-customer-service ./charts/rag-customer-service
```

### 4.2 K8s 资源清单

主要 K8s 资源：
- Deployment: backend, celery-worker
- Service: backend, mysql, redis, milvus
- PersistentVolumeClaim: mysql, redis, milvus
- Ingress: 外部访问
- ConfigMap/Secret: 环境变量

---

## 5. 监控配置

### 5.1 Prometheus 配置

将以下配置添加到 `prometheus.yml`：

```yaml
scrape_configs:
  - job_name: 'rag-customer-service'
    static_configs:
      - targets: ['backend:8000']
    metrics_path: '/metrics'
    scrape_interval: 15s
```

### 5.2 Grafana 仪表板

推荐导入的仪表板：
- **Grafana Dashboard ID 9628**: Prometheus 2.0 Overview
- 自定义仪表板（见下方 JSON）

```json
{
  "dashboard": {
    "title": "RAG 智能客服监控",
    "panels": [
      {
        "title": "QPS",
        "targets": [{"expr": "rate(chat_requests_total[5m])"}]
      },
      {
        "title": "P99 延迟",
        "targets": [{"expr": "histogram_quantile(0.99, rate(chat_request_duration_seconds_bucket[5m]))"}]
      },
      {
        "title": "LLM 调用成功率",
        "targets": [{"expr": "sum(rate(llm_calls_total{status='success'}[5m])) / sum(rate(llm_calls_total[5m]))"}]
      },
      {
        "title": "Embedding 缓存命中率",
        "targets": [{"expr": "embedding_cache_hits_total / (embedding_cache_hits_total + embedding_cache_misses_total)"}]
      },
      {
        "title": "活跃会话数",
        "targets": [{"expr": "active_sessions"}]
      }
    ]
  }
}
```

### 5.3 关键监控指标

| 指标 | 说明 | 告警阈值 |
|------|------|---------|
| `chat_requests_total` | 聊天请求总数 | - |
| `chat_request_duration_seconds` | 请求延迟 | P99 > 30s |
| `llm_calls_total{status="error"}` | LLM 调用失败数 | 连续 > 5 次 |
| `vector_search_duration_seconds` | 向量检索延迟 | P99 > 1s |
| `embedding_cache_hit_ratio` | Embedding 缓存命中率 | < 60% |
| `active_sessions` | 活跃会话数 | > 1000 |

---

## 6. 告警配置

### 6.1 告警规则（Prometheus AlertManager）

```yaml
groups:
  - name: rag-alerts
    rules:
      - alert: RAGHighLatency
        expr: histogram_quantile(0.99, rate(chat_request_duration_seconds_bucket[5m])) > 30
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "RAG 请求 P99 延迟超过 30 秒"

      - alert: RAGLLMFailure
        expr: increase(llm_calls_total{status="error"}[5m]) > 5
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "LLM 调用连续失败"

      - alert: RAGVectorSearchFailure
        expr: increase(vector_search_errors_total[5m]) > 10
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "向量检索失败次数过多"

      - alert: RAGLowCacheHitRatio
        expr: embedding_cache_hits_total / (embedding_cache_hits_total + embedding_cache_misses_total) < 0.6
        for: 10m
        labels:
          severity: info
        annotations:
          summary: "Embedding 缓存命中率低于 60%"

      - alert: RAGHighMemoryUsage
        expr: (container_memory_usage_bytes / container_spec_memory_limit_bytes) > 0.8
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "容器内存使用率超过 80%"
```

### 6.2 告警通知配置

支持的通知方式：
- [x] Slack / Discord Webhook
- [x] Email (SMTP)
- [x] 企业微信 / 钉钉 Webhook
- [x] PagerDuty
- [x] 自定义 Webhook

### 6.3 本地告警状态查询

```bash
curl http://localhost:8000/api/v1/health/alerts
```

响应示例：

```json
{
  "monitoring": true,
  "alerts": {
    "llm": {
      "consecutive_failures": 0,
      "threshold": 5,
      "triggered": false
    },
    "vector": {
      "consecutive_failures": 0,
      "threshold": 10,
      "triggered": false
    },
    "latency": {
      "p99_ms": 850,
      "threshold_ms": 30000,
      "samples": 150
    }
  }
}
```

---

## 7. 故障排查

### 7.1 常见问题

#### Q1: Backend 无法启动

```bash
# 检查 backend 日志
docker-compose logs backend

# 常见原因：
# 1. MySQL 未就绪 - 等待健康检查
# 2. 端口被占用 - 检查 8000 端口
docker ps | grep 8000

# 3. 环境变量缺失
docker-compose config  # 验证配置
```

#### Q2: LLM 调用失败

```bash
# 检查 DashScope API Key
grep DASHSCOPE_API_KEY .env

# 测试 API 连通性
curl https://dashscope.aliyuncs.com/compatible-mode/v1/models \
  -H "Authorization: Bearer $DASHSCOPE_API_KEY"

# 检查熔断器状态
curl http://localhost:8000/api/v1/health/detailed
```

#### Q3: 向量检索返回空结果

```bash
# 检查 Milvus 是否有数据
docker exec rag-milvus milvus-cli show collection -c knowledge_base

# 检查 documents 表
curl http://localhost:8000/api/v1/admin/documents \
  -H "Authorization: Bearer <admin-token>"

# 重新触发知识库摄取
curl -X POST http://localhost:8000/api/v1/admin/ingest?reset=true \
  -H "Authorization: Bearer <admin-token>"
```

#### Q4: 会话历史丢失

```bash
# 检查 Redis 连接
docker-compose logs redis | grep -i error

# 检查 Redis 内存
redis-cli -h localhost -p 6379 info memory

# 检查 session 表
docker exec -it rag-mysql mysql -u root -p -e "SELECT COUNT(*) FROM rag_customer_service.sessions;"
```

#### Q5: 限流误触

```bash
# 检查限流配置
docker-compose config | grep RATE_LIMIT

# 临时关闭限流（仅测试用）
docker-compose exec backend python -c "
import os
os.environ['RATE_LIMIT_ENABLED'] = 'false'
"
```

### 7.2 日志分析

```bash
# 查看 backend 所有日志
docker-compose logs -f backend

# 查看带时间戳的日志
docker-compose logs -f -t backend

# 搜索错误日志
docker-compose logs backend | grep -i error

# 导出最近 1 小时的日志
docker-compose logs --since 1h backend > logs/backend.log

# 查看 MySQL 慢查询日志
docker-compose logs mysql | grep -i "slow"
```

### 7.3 健康检查

```bash
# 完整健康检查
curl -s http://localhost:8000/api/v1/health/detailed | jq .

# 单独检查每个组件
curl -s http://localhost:8000/api/v1/health/detailed | jq '.components'
```

---

## 8. 性能调优

### 8.1 Milvus 调优

```yaml
# docker-compose.yml 中 Milvus 配置
environment:
  - COMMON_STORAGYPE=local
# 索引参数调优
# IVF_FLAT: nlist=1024, nprobe=16 (平衡精度和速度)
# HNSW: M=16, efConstruction=200 (高精度，高内存)
```

### 8.2 Redis 调优

```bash
# docker-compose.yml 中 Redis 配置
command: >
  redis-server
  --maxmemory 512mb
  --maxmemory-policy allkeys-lru
  --save ""
  --appendonly no
```

### 8.3 MySQL 调优

```bash
# docker-compose.yml 中 MySQL 配置
command: >
  --character-set-server=utf8mb4
  --collation-server=utf8mb4_unicode_ci
  --max_connections=200
  --innodb_buffer_pool_size=256M
  --innodb_log_file_size=64M
  --slow_query_log=1
  --long_query_time=2
```

### 8.4 RAG 参数调优

```env
# 调高 top_k 提升召回率（但增加延迟）
RAG_TOP_K=20

# 降低 rerank_top_k 减少 LLM token 消耗
RAG_RERANK_TOP_K=3
```

### 8.5 资源规划

| 组件 | CPU | 内存 | 磁盘 |
|------|-----|------|------|
| backend | 2 核 | 2 GB | - |
| celery-worker | 1 核 | 512 MB | - |
| mysql | 1 核 | 512 MB | 10 GB |
| redis | 0.5 核 | 256 MB | 1 GB |
| milvus | 2 核 | 4 GB | 50 GB |

---

## 9. 安全加固

### 9.1 网络安全

```yaml
# docker-compose.yml networks 配置
networks:
  rag-network:
    driver: bridge
    driver_opts:
      com.docker.network.bridge.name: rag-br0
      com.docker.network.bridge.enable_icc: "true"
```

### 9.2 密钥管理

```bash
# 生产环境使用 Docker Secrets 或 K8s Secrets
echo "your-secret" | docker secret create dashscope_api_key -
```

### 9.3 HTTPS 配置

```nginx
# frontend/nginx.conf (生产环境)
server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate /etc/nginx/ssl/cert.pem;
    ssl_certificate_key /etc/nginx/ssl/key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # 安全头
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy "no-referrer-when-downgrade" always;

    # ... 其他配置
}
```

### 9.4 CORS 配置

```python
# 生产环境 CORS 配置
CORS_ORIGINS=https://your-domain.com,https://admin.your-domain.com
```

### 9.5 速率限制

```env
# 生产环境建议
RATE_LIMIT_GLOBAL=100
RATE_LIMIT_USER=30
RATE_LIMIT_WINDOW=60
```

---

## 10. 备份与恢复

### 10.1 MySQL 备份

```bash
# 备份脚本
docker exec rag-mysql mysqldump -u root -p$MYSQL_PASSWORD rag_customer_service \
  > backup_$(date +%Y%m%d_%H%M%S).sql

# 自动备份（每天凌晨 2 点）
0 2 * * * docker exec rag-mysql mysqldump -u root -p$MYSQL_PASSWORD \
  rag_customer_service > /backup/mysql_$(date +\%Y\%m\%d).sql
```

### 10.2 Redis 备份

```bash
# 手动备份
docker exec rag-redis redis-cli SAVE

# 复制备份文件
docker cp rag-redis:/data/dump.rdb ./backup/redis_$(date +%Y%m%d).rdb
```

### 10.3 Milvus 备份

```bash
# Milvus 数据卷备份
docker volume backup rag_milvus_data > milvus_backup_$(date +%Y%m%d).tar

# 或使用 Milvus 内置备份工具
docker exec rag-milvus milvus-backup backup -n backup_name
```

### 10.4 恢复流程

```bash
# 1. 停止服务
docker-compose down

# 2. 恢复 MySQL
docker exec -i rag-mysql mysql -u root -p$MYSQL_PASSWORD \
  rag_customer_service < backup_20260101.sql

# 3. 恢复 Redis
docker cp ./backup/redis_20260101.rdb rag-redis:/data/dump.rdb
docker restart rag-redis

# 4. 恢复 Milvus
docker volume restore rag_milvus_data < milvus_backup_20260101.tar

# 5. 重启所有服务
docker-compose up -d
```

---

## 附录 A：API 快速参考

### 聊天接口

```bash
# 发送消息
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "iPhone 16 Pro Max 有哪些颜色？",
    "user_id": "user-001",
    "session_id": ""
  }'
```

### 意图分类

```bash
curl -X POST http://localhost:8000/api/v1/intent \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "我的订单到哪了？"
  }'
```

### 获取会话历史

```bash
curl http://localhost:8000/api/v1/chat/history/{session_id}?user_id=user-001 \
  -H "Authorization: Bearer <token>"
```

---

## 附录 B：环境变量完整参考

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `LLM_PROVIDER` | qwen | LLM 提供商 (qwen/mock) |
| `LLM_MODEL_NAME` | qwen-max | 主模型名称 |
| `LLM_FALLBACK_MODELS` | qwen-plus,qwen-turbo | 降级模型列表 |
| `DASHSCOPE_API_KEY` | - | DashScope API Key（必填） |
| `JWT_SECRET` | - | JWT 签名密钥（必填） |
| `CORS_ORIGINS` | * | 允许的 CORS 域名 |
| `RATE_LIMIT_ENABLED` | true | 是否启用限流 |
| `RATE_LIMIT_GLOBAL` | 200 | 全局限流（每分钟） |
| `RATE_LIMIT_USER` | 60 | 用户限流（每分钟） |
| `AUTO_INGEST` | false | 启动时自动摄取知识库 |
| `RAG_TOP_K` | 10 | RAG 检索返回数量 |
| `RAG_RERANK_TOP_K` | 5 | Rerank 后返回数量 |
| `LOG_LEVEL` | INFO | 日志级别 (DEBUG/INFO/WARNING/ERROR) |
| `DEBUG` | false | 调试模式 |

---

*文档版本：1.0.0 | 最后更新：2026-04-04*
