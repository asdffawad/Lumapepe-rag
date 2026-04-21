# ==============================================================================
# RAG 智能客服 - 生产环境 Dockerfile
# ==============================================================================
# 多阶段构建：builder (依赖安装) + runtime (最终镜像)
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1: Builder - 安装依赖
# ------------------------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /app

# 安装构建依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    libffi-dev \
    libssl-dev \
    curl \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# 预安装核心依赖（利用 Docker 缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ------------------------------------------------------------------------------
# Stage 2: Runtime - 最终镜像
# ------------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# 安全：创建非 root 用户
RUN groupadd --gid 1000 appgroup \
    && useradd --uid 1000 --gid appgroup --shell /bin/bash --create-home appuser

WORKDIR /app

# 安装运行时依赖（轻量）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# 从 builder 复制已安装的 Python 包
COPY --from=builder /root/.local /home/appuser/.local

# 复制应用代码
COPY --chown=appuser:appgroup backend/ ./backend/
COPY --chown=appuser:appgroup data/ ./data/
COPY --chown=appuser:appgroup scripts/ ./scripts/

# 环境变量
ENV PYTHONPATH=/app/backend \
    PYTHONUNBUFFERED=1 \
    PATH=/home/appuser/.local/bin:$PATH

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

# 切换到非 root 用户
USER appuser

# 暴露端口
EXPOSE 8000

# 启动命令：gunicorn + uvicorn workers
# - workers: CPU 核心数 * 2 + 1
# - threads: 4（每个 worker 4 线程）
# - timeout: 300s（支持长连接和 RAG 生成）
# - keep-alive: 5s
CMD ["sh", "-c", "gunicorn app.main:app \
    --workers 4 \
    --threads 4 \
    --bind 0.0.0.0:8000 \
    --worker-class uvicorn.workers.UvicornWorker \
    --timeout 300 \
    --keep-alive 5 \
    --access-logfile - \
    --error-logfile - \
    --log-level info"]
