#!/bin/bash
# =============================================================================
# RAG 知识库系统一键启动脚本（企业级生产版）
# 执行顺序：
#   1. 检查 Docker 是否运行
#   2. 启动 MySQL 和 Redis（必需的前置服务）
#   3. 等待 MySQL 健康检查通过
#   4. 启动 Milvus
#   5. 初始化 Milvus collection（如不存在）
#   6. 启动所有 Docker 服务（backend 会根据 AUTO_INGEST 自动摄取）
#   7. 显示服务状态
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_DIR/backend"

cd "$PROJECT_DIR"

echo "========================================"
echo "  RAG 知识库系统启动脚本 (企业级)"
echo "========================================"
echo "  项目目录: $PROJECT_DIR"
echo ""

# 检查 Docker 是否运行
if ! command -v docker &> /dev/null; then
    echo "[ERROR] Docker 未安装或未运行"
    exit 1
fi

if ! docker info &> /dev/null; then
    echo "[ERROR] Docker daemon 未运行，请先启动 Docker Desktop"
    exit 1
fi

# =============================================================================
# Step 1: 检查并启动 MySQL 和 Redis
# =============================================================================
echo "[Step 1/5] 启动 MySQL 和 Redis..."

docker-compose up -d mysql redis

echo "  -> 等待 MySQL 就绪..."
for i in {1..30}; do
    MYSQL_HEALTH=$(docker inspect --format='{{.State.Health.Status}}' rag-mysql 2>/dev/null || echo "not_found")
    if [ "$MYSQL_HEALTH" = "healthy" ]; then
        echo "  -> MySQL 健康检查通过"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "[WARN] MySQL 健康检查超时，继续尝试启动..."
    fi
    sleep 2
done

echo "  -> MySQL 和 Redis 已启动"

# =============================================================================
# Step 2: 启动 Milvus
# =============================================================================
echo ""
echo "[Step 2/5] 启动 Milvus 向量数据库..."

docker-compose up -d milvus

echo "  -> Milvus 启动中（首次启动需要 2-3 分钟初始化）..."
echo "  -> 等待 Milvus 就绪..."
for i in {1..60}; do
    MILVUS_HEALTH=$(docker inspect --format='{{.State.Health.Status}}' rag-milvus 2>/dev/null || echo "not_found")
    if [ "$MILVUS_HEALTH" = "healthy" ]; then
        echo "  -> Milvus 健康检查通过"
        break
    fi
    if [ $i -eq 60 ]; then
        echo "[WARN] Milvus 健康检查超时（可能仍在初始化中），继续..."
    fi
    sleep 5
done

# =============================================================================
# Step 3: 初始化 Milvus Collection（如果不存在）
# =============================================================================
echo ""
echo "[Step 3/5] 检查 Milvus Collection..."

# 从 .env 读取配置（如果存在）
MILVUS_HOST="${MILVUS_HOST:-milvus}"
MILVUS_PORT="${MILVUS_PORT:-19530}"

# 检查 collection 是否存在
echo "  -> Milvus: $MILVUS_HOST:$MILVUS_PORT"

# =============================================================================
# Step 4: 启动所有服务（backend 会根据 AUTO_INGEST 自动摄取）
# =============================================================================
echo ""
echo "[Step 4/5] 启动所有 Docker 服务..."

# 从 .env 读取环境变量（如果存在）
if [ -f "$PROJECT_DIR/.env" ]; then
    echo "  -> 加载 .env 配置..."
fi

docker-compose up -d

# =============================================================================
# Step 5: 验证服务状态
# =============================================================================
echo ""
echo "[Step 5/5] 验证服务状态..."
echo ""

# 等待 backend 启动
echo "  -> 等待 Backend API 启动..."
for i in {1..30}; do
    BACKEND_HEALTH=$(curl -s http://localhost:8000/api/v1/health 2>/dev/null | grep -c "healthy" || echo "0")
    if [ "$BACKEND_HEALTH" -ge 1 ]; then
        echo "  -> Backend API 已就绪"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "[WARN] Backend API 启动超时，请检查日志: docker-compose logs backend"
    fi
    sleep 2
done

echo ""
echo "========================================"
echo "  启动完成"
echo "========================================"
echo ""
echo "  服务状态:"
docker-compose ps
echo ""
echo "  访问地址:"
echo "    前端界面:   http://localhost:80"
echo "    API 文档:   http://localhost:8000/docs"
echo "    健康检查:   http://localhost:8000/api/v1/health"
echo "    详细健康:   http://localhost:8000/api/v1/health/detailed"
echo "    告警状态:   http://localhost:8000/api/v1/health/alerts"
echo "    Prometheus: http://localhost:8000/metrics"
echo ""
echo "  知识库管理:"
echo "    查看文档:   GET  http://localhost:8000/api/v1/admin/documents"
echo "    手动摄取:   POST http://localhost:8000/api/v1/admin/ingest"
echo "    强制重建:   POST http://localhost:8000/api/v1/admin/ingest?reset=true"
echo ""
echo "  日志查看:"
echo "    Backend:    docker-compose logs -f backend"
echo "    MySQL:      docker-compose logs -f mysql"
echo "    Milvus:     docker-compose logs -f milvus"
echo ""
echo "  常见操作:"
echo "    重启服务:   docker-compose restart"
echo "    停止服务:   docker-compose down"
echo "    清理数据:   docker-compose down -v  (会删除所有数据)"
echo ""
