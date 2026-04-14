"""
认证中间件
支持两种认证方式：
1. JWT Bearer Token（适用于前端用户）
2. API Key（适用于内部系统 / B2B 场景）
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Callable

from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware

# 预计算的 JWT secret（通过环境变量覆盖）
_JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-in-production")
_JWT_ALGORITHM = "HS256"


@dataclass
class AuthUser:
    """认证用户对象"""
    user_id: str
    role: str = "user"
    scopes: list[str] = None


class JWTError(Exception):
    pass


def create_token(user_id: str, role: str = "user", ttl_seconds: int = 86400) -> str:
    """
    创建 JWT Token

    Args:
        user_id: 用户ID
        role: 用户角色
        ttl_seconds: Token 有效期（默认 24 小时）

    Returns:
        JWT Token 字符串
    """
    import struct

    header = {"alg": _JWT_ALGORITHM, "typ": "JWT"}
    payload = {
        "sub": user_id,
        "role": role,
        "iat": int(time.time()),
        "exp": int(time.time()) + ttl_seconds,
    }

    def _b64(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    header_b64 = _b64(header)
    payload_b64 = _b64(payload)
    sign_input = f"{header_b64}.{payload_b64}"

    signature = hmac.new(
        _JWT_SECRET.encode(),
        sign_input.encode(),
        hashlib.sha256,
    ).digest()
    sig_b64 = base64.urlsafe_b64encode(signature).decode().rstrip("=")

    return f"{sign_input}.{sig_b64}"


def decode_token(token: str) -> AuthUser:
    """
    解析并验证 JWT Token

    Raises:
        JWTError: Token 无效或已过期
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise JWTError("Invalid token format")

    header_b64, payload_b64, sig_b64 = parts

    # 验证签名
    sign_input = f"{header_b64}.{payload_b64}"
    expected_sig = hmac.new(
        _JWT_SECRET.encode(),
        sign_input.encode(),
        hashlib.sha256,
    ).digest()
    expected_sig_b64 = base64.urlsafe_b64encode(expected_sig).decode().rstrip("=")

    if not hmac.compare_digest(sig_b64, expected_sig_b64):
        raise JWTError("Invalid signature")

    # 解析 payload
    padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded.encode()))

    # 检查过期
    if payload.get("exp", 0) < time.time():
        raise JWTError("Token expired")

    return AuthUser(
        user_id=payload.get("sub", ""),
        role=payload.get("role", "user"),
        scopes=payload.get("scopes", []),
    )


def verify_api_key(api_key: str) -> AuthUser | None:
    """
    验证 API Key

    API Key 格式: {key_id}:{secret}
    环境变量 API_KEYS 格式: key_id:secret:role,key_id2:secret2:role2
    """
    allowed_keys = os.getenv("API_KEYS", "")
    if not allowed_keys:
        return None

    for entry in allowed_keys.split(","):
        parts = entry.strip().split(":")
        if len(parts) < 2:
            continue
        key_id, secret = parts[0], parts[1]
        role = parts[2] if len(parts) > 2 else "api_user"

        expected = f"{key_id}:{secret}"
        if hmac.compare_digest(api_key, expected):
            return AuthUser(user_id=key_id, role=role)

    return None


def _extract_bearer_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def _extract_api_key(request: Request) -> str | None:
    return request.headers.get("X-API-Key") or request.headers.get("x-api-key")


async def get_current_user(request: Request) -> AuthUser:
    """
    FastAPI 依赖：获取当前认证用户

    优先级：
    1. JWT Bearer Token (Authorization: Bearer <token>)
    2. API Key (X-API-Key: <key>)

    Raises:
        HTTPException 401: 未提供认证信息
        HTTPException 403: 认证信息无效
    """
    # 跳过认证的路径
    skip_paths = {"/", "/docs", "/redoc", "/openapi.json", "/api/v1/health"}
    if request.url.path in skip_paths or request.url.path.startswith("/docs"):
        return AuthUser(user_id="anonymous", role="guest")

    # 跳过认证的路径（精确匹配）
    skip_exact = {
        "/api/v1/health",
        "/api/v1/admin/ingest",
        "/api/v1/admin/documents",
    }
    if request.url.path in skip_exact:
        return AuthUser(user_id="admin", role="admin")

    # 1. 尝试 JWT
    token = _extract_bearer_token(request)
    if token:
        try:
            return decode_token(token)
        except JWTError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": str(e)},
                headers={"WWW-Authenticate": "Bearer"},
            )

    # 2. 尝试 API Key
    api_key = _extract_api_key(request)
    if api_key:
        user = verify_api_key(api_key)
        if user:
            return user
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_API_KEY", "message": "无效的 API Key"},
        )

    # 未提供认证
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "UNAUTHORIZED", "message": "请提供 Authorization: Bearer <token> 或 X-API-Key"},
        headers={"WWW-Authenticate": "Bearer"},
    )


# ---------------------------------------------------------------------------
# 限流中间件（应用级）
# ---------------------------------------------------------------------------


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    基于 Redis 的滑动窗口限流中间件（同步版）

    限流维度：
    - 全局限流：按 IP
    - 用户限流：按 user_id（从认证信息获取）
    """

    def __init__(
        self,
        app,
        redis_pool,
        global_limit: int = 200,
        user_limit: int = 60,
        window: int = 60,
    ):
        super().__init__(app)
        self._redis_pool = redis_pool
        self._global_limit = global_limit
        self._user_limit = user_limit
        self._window = window

    def _get_redis(self):
        """获取同步 Redis 客户端"""
        import redis as sync_redis
        return sync_redis.Redis(connection_pool=self._redis_pool)

    async def dispatch(self, request: Request, call_next):
        import time

        # 跳过限流的路径
        skip_paths = {"/", "/docs", "/redoc", "/openapi.json", "/health", "/metrics"}
        if request.url.path.startswith("/docs") or request.url.path in skip_paths:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now_bucket = int(time.time() // self._window)

        try:
            r = self._get_redis()

            # 1. 全局限流（按 IP）
            global_key = f"rate:global:{client_ip}:{now_bucket}"
            count = r.incr(global_key)
            if count == 1:
                r.expire(global_key, self._window)

            if count > self._global_limit:
                return __import__("fastapi").responses.JSONResponse(
                    status_code=429,
                    content={
                        "error": {
                            "code": "RATE_LIMIT_EXCEEDED",
                            "message": f"全局限流：每 {self._window}s 最多 {self._global_limit} 请求",
                            "retry_after": self._window - (time.time() % self._window),
                        }
                    },
                    headers={"Retry-After": str(self._window)},
                )

            # 2. 用户限流（从请求体获取 user_id，简化版）
            user_id = getattr(request.state, "user_id", None)
            if user_id:
                user_key = f"rate:user:{user_id}:{now_bucket}"
                user_count = r.incr(user_key)
                if user_count == 1:
                    r.expire(user_key, self._window)

                if user_count > self._user_limit:
                    return __import__("fastapi").responses.JSONResponse(
                        status_code=429,
                        content={
                            "error": {
                                "code": "RATE_LIMIT_EXCEEDED",
                                "message": f"用户限流：每 {self._window}s 最多 {self._user_limit} 请求",
                                "retry_after": self._window - (time.time() % self._window),
                            }
                        },
                        headers={"Retry-After": str(self._window)},
                    )

            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(self._global_limit)
            response.headers["X-RateLimit-Remaining"] = str(max(0, self._global_limit - count))
            return response
        except Exception:
            # Redis 不可用时，放行请求（不阻断服务）
            return await call_next(request)


# ---------------------------------------------------------------------------
# WebSocket 认证
# ---------------------------------------------------------------------------


def extract_bearer_from_headers(headers: dict) -> str | None:
    """
    从 WebSocket headers 中提取 Bearer Token

    支持两种方式：
    1. Authorization: Bearer <token>
    2. Sec-WebSocket-Protocol: bearer, <token>

    Returns:
        Token 字符串，如果不存在返回 None
    """
    # 方式1: Authorization Header
    auth = headers.get("authorization", "") or headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    if auth.startswith("bearer "):
        return auth[7:].strip()

    # 方式2: Sec-WebSocket-Protocol
    protocol = headers.get("sec-websocket-protocol", "") or headers.get("Sec-WebSocket-Protocol", "")
    if protocol.startswith("bearer,"):
        return protocol[7:].strip()
    if protocol.startswith("bearer "):
        return protocol[7:].strip()

    return None


async def verify_ws_token(headers: dict) -> AuthUser:
    """
    验证 WebSocket 连接的 Bearer Token

    Raises:
        JWTError: Token 无效或已过期
    """
    token = extract_bearer_from_headers(headers)
    if not token:
        raise JWTError("Missing authorization token")

    return decode_token(token)
