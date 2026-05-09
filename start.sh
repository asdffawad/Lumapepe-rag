#!/bin/bash
# ==============================================================================
# RAG 智能客服 - 启动脚本
# ==============================================================================
# 功能：
# 1. 检查 .env 文件是否存在
# 2. 启动所有服务
# 3. 显示服务状态
# ==============================================================================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  RAG 智能客服 - Docker 启动脚本${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# 检查 .env 文件
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}警告：.env 文件不存在${NC}"
    echo -e "${YELLOW}正在从 .env.example 创建...${NC}"
    cp .env.example .env
    echo -e "${RED}请编辑 .env 文件并修改以下必需配置：${NC}"
    echo "  - JWT_SECRET"
    echo "  - ADMIN_API_KEYS"
    echo "  - REDIS_PASSWORD"
    echo "  - DB_HOST"
    echo "  - DB_USER"
    echo "  - DB_PASSWORD"
    echo "  - DASHSCOPE_API_KEY"
    echo "  - CORS_ORIGINS"
    echo ""
    echo -e "${YELLOW}编辑完成后重新运行此脚本${NC}"
    exit 1
fi

# 检查必需的环境变量
source .env

if [ "$JWT_SECRET" == "CHANGE_ME_generate_with_openssl_rand_hex_32" ]; then
    echo -e "${RED}错误：请修改 .env 中的 JWT_SECRET${NC}"
    exit 1
fi

if [ "$REDIS_PASSWORD" == "CHANGE_ME_generate_with_openssl_rand_hex_16" ]; then
    echo -e "${RED}错误：请修改 .env 中的 REDIS_PASSWORD${NC}"
    exit 1
fi

if [ -z "$DB_HOST" ] || [ "$DB_HOST" == "CHANGE_ME_mysql_server_b_ip" ]; then
    echo -e "${RED}错误：请修改 .env 中的 DB_HOST 为服务器 B 的 MySQL IP${NC}"
    exit 1
fi

if [ -z "$DB_USER" ] || [ "$DB_USER" == "CHANGE_ME_mysql_user" ]; then
    echo -e "${RED}错误：请修改 .env 中的 DB_USER${NC}"
    exit 1
fi

if [ -z "$DB_PASSWORD" ] || [ "$DB_PASSWORD" == "CHANGE_ME_mysql_password" ]; then
    echo -e "${RED}错误：请修改 .env 中的 DB_PASSWORD${NC}"
    exit 1
fi

echo -e "${GREEN}配置检查通过${NC}"
echo ""

# 拉取最新镜像
echo -e "${YELLOW}正在拉取最新镜像...${NC}"
docker-compose pull

# 构建镜像
echo -e "${YELLOW}正在构建镜像...${NC}"
docker-compose build --no-cache

# 启动服务
echo -e "${YELLOW}正在启动服务...${NC}"
docker-compose up -d

# 等待服务启动
echo -e "${YELLOW}等待服务就绪...${NC}"
sleep 10

# 显示状态
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  服务状态${NC}"
echo -e "${GREEN}========================================${NC}"
docker-compose ps

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  访问地址${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "  API 服务:     http://localhost:8000"
echo "  API 文档:     http://localhost:8000/docs"
echo "  健康检查:     http://localhost:8000/api/v1/health"
echo "  WebSocket:    ws://localhost:8000/ws"
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  常用命令${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "  查看日志:     docker-compose logs -f backend"
echo "  停止服务:     docker-compose down"
echo "  重启服务:     docker-compose restart"
echo "  进入容器:     docker-compose exec backend bash"
echo ""
