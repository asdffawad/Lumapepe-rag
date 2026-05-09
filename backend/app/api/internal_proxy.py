"""
商城后端（lumapepe）专用：签发 WebSocket 代理 JWT，无需在商城进程内持有 JWT_SECRET。

部署：
- 设置环境变量 RAG_PROXY_INTERNAL_KEY（建议 openssl rand -hex 32）
- 仅允许内网访问该路径（防火墙 / Nginx allow）
- lumapepe 配置 lumapepe.kefu.rag.token-fetch-url = https://<rag-host>/internal/v1/rag-proxy/token
  + token-fetch-api-key = 与 RAG_PROXY_INTERNAL_KEY 相同
"""
from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.middleware.auth import create_token

router = APIRouter(prefix="/internal/v1", tags=["internal-proxy"])


class RagProxyTokenResponse(BaseModel):
    """与 lumapepe KeFuRagProxyServiceImpl.fetchTokenFromHttp 解析字段对齐"""

    token: str = Field(..., description="Bearer WebSocket JWT")
    expires_in: int = Field(..., description="有效期（秒）")
    token_type: str = Field(default="Bearer", description="固定 Bearer")


def _internal_key_configured() -> str:
    return (os.getenv("RAG_PROXY_INTERNAL_KEY") or "").strip()


def _verify_proxy_key(provided: str | None) -> None:
    expected = _internal_key_configured()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "RAG_PROXY_TOKEN_DISABLED",
                "message": "RAG_PROXY_INTERNAL_KEY is not set; internal token endpoint is disabled",
            },
        )
    if not provided:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "MISSING_KEY", "message": "Missing X-RAG-Proxy-Key header"},
        )
    # 长度不等时避免 compare_digest 抛错，仍做常量时间比较思路下的分支
    if len(provided) != len(expected):
        _reject_key()
    if not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        _reject_key()


def _reject_key() -> None:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "INVALID_KEY", "message": "Invalid X-RAG-Proxy-Key"},
    )


@router.post(
    "/rag-proxy/token",
    response_model=RagProxyTokenResponse,
    summary="签发商城 WebSocket 代理 JWT（仅内网）",
    description=(
        "供 lumapepe Java 代理拉取 JWT，用于连接 /ws/chat。"
        "请求头必须带 X-RAG-Proxy-Key，与 RAG 环境变量 RAG_PROXY_INTERNAL_KEY 一致。"
    ),
)
async def issue_rag_proxy_token(request: Request) -> Any:
    key = request.headers.get("x-rag-proxy-key") or request.headers.get("X-RAG-Proxy-Key")
    _verify_proxy_key(key)

    subject = os.getenv("RAG_PROXY_TOKEN_SUBJECT", "lumapepe-proxy").strip() or "lumapepe-proxy"
    try:
        ttl = int(os.getenv("RAG_PROXY_TOKEN_TTL_SECONDS", "604800"))
    except ValueError:
        ttl = 604800
    ttl = max(60, min(ttl, 2592000))  # 1 分钟 ~ 30 天

    role = os.getenv("RAG_PROXY_TOKEN_ROLE", "service").strip() or "service"
    token = create_token(subject, role=role, ttl_seconds=ttl)

    return RagProxyTokenResponse(
        token=token,
        expires_in=ttl,
        token_type="Bearer",
    )


@router.get("/rag-proxy/health")
async def rag_proxy_health() -> dict[str, str]:
    """运维探测：是否已配置内部换票（不泄露密钥）。"""
    ok = bool(_internal_key_configured())
    return {
        "internal_proxy_token": "enabled" if ok else "disabled",
    }
