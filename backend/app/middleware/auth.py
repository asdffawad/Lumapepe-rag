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

# JWT Secret 验证
_JWT_SECRET = os.getenv("JWT_SECRET", "")

# 开发模式标志（设置 DEBUG=true 时允许使用弱密钥）
_IS_DEBUG = os.getenv("DEBUG", "").lower() in ("true", "1", "yes")

# 不安全的默认密钥列表
_UNSAFE_SECRETS = {
    "dev-secret-change-in-production",
    "changeme",
    "secret",
    "password",
    "your-secret-here",
    "",
}


def _validate_jwt_secret() -> str:
    """
    验证 JWT_SECRET 配置。

    生产模式：必须设置 >= 32 字符的安全密钥
    开发模式：允许使用默认值但输出警告
    """
    secret = os.getenv("JWT_SECRET", "")

    if not secret:
        if _IS_DEBUG:
            import warnings
            warnings.warn(
                "[WARNING] JWT_SECRET is not set. Using insecure default key for development only. "
                "DO NOT use in production. Generate with: openssl rand -hex 32",
                UserWarning,
                stacklevel=2,
            )
            return "dev-insecure-key-do-not-use-in-production"
        else:
            raise ValueError(
                "JWT_SECRET must be set in production environment. "
                "Generate with: openssl rand -hex 32"
            )

    if secret in _UNSAFE_SECRETS:
        if _IS_DEBUG:
            import warnings
            warnings.warn(
                f"[WARNING] JWT_SECRET uses insecure value '{secret}'. "
                "DO NOT use in production. Generate with: openssl rand -hex 32",
                UserWarning,
                stacklevel=2,
            )
        else:
            raise ValueError(
                f"JWT_SECRET uses insecure value '{secret}'. "
                "Generate with: openssl rand -hex 32"
            )

    if len(secret) < 32:
        if _IS_DEBUG:
            import warnings
            warnings.warn(
                f"[WARNING] JWT_SECRET length ({len(secret)}) is less than 32 characters. "
                "This is insecure for production. Generate with: openssl rand -hex 32",
                UserWarning,
                stacklevel=2,
            )
        else:
            raise ValueError(
                f"JWT_SECRET length ({len(secret)}) must be at least 32 characters. "
                "Generate with: openssl rand -hex 32"
            )

    return secret


# 初始化时验证
_JWT_SECRET = _validate_jwt_secret()
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


def verify_admin_key(api_key: str) -> bool:
    """
    验证管理员 API Key

    环境变量 ADMIN_API_KEYS 格式: key_id:secret,key_id2:secret2

    Returns:
        True 如果验证通过，否则 False
    """
    if not api_key:
        return False

    allowed_keys = os.getenv("ADMIN_API_KEYS", "")
    if not allowed_keys:
        return False

    for entry in allowed_keys.split(","):
        parts = entry.strip().split(":")
        if len(parts) < 2:
            continue
        key_id, secret = parts[0], parts[1]
        expected = f"{key_id}:{secret}"
        if hmac.compare_digest(api_key, expected):
            return True

    return False


def _extract_admin_key(request: Request) -> str | None:
    """从请求中提取管理员 API Key"""
    # 支持多种 Header 名称
    return (
        request.headers.get("X-Admin-Key")
        or request.headers.get("x-admin-key")
        or request.headers.get("X-AdminKey")
    )


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


async def get_admin_user(request: Request) -> AuthUser:
    """
    FastAPI 依赖：admin 专属鉴权，同时支持 JWT Bearer Token 和 Admin API Key。

    支持的认证方式（优先级依次）：
    1. X-Admin-Key: <key> — 验证管理员 API Key
    2. Authorization: Bearer <token> — 验证 JWT，role 必须为 "admin"

    Raises:
        HTTPException 401: 未提供认证信息或 Token 无效
        HTTPException 403: Token 有效但非 admin 角色
    """
    # 1. 尝试 Admin API Key
    admin_key = _extract_admin_key(request)
    if admin_key and verify_admin_key(admin_key):
        return AuthUser(user_id="admin", role="admin")

    # 2. 尝试 JWT Bearer Token
    token = _extract_bearer_token(request)
    if token:
        try:
            user = decode_token(token)
        except JWTError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": str(e)},
                headers={"WWW-Authenticate": "Bearer"},
            )

        if user.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "ADMIN_REQUIRED",
                    "message": "此接口需要 admin 权限"
                },
            )
        return user

    # 3. 均未提供
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": "UNAUTHORIZED",
            "message": "请提供 Authorization: Bearer <token> 或 X-Admin-Key"
        },
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

        # 跳过限流的路径（含商城代理换票，避免刷 JWT 触发全局限流）
        skip_paths = {
            "/",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/health",
            "/metrics",
            "/internal/v1/rag-proxy/token",
            "/internal/v1/rag-proxy/health",
        }
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

    # 方式2: Sec-WebSocket-Protocol: bearer, <token>
    # 浏览器 new WebSocket(url, ['bearer', token]) 发送: Sec-WebSocket-Protocol: bearer, <token>
    # Starlette 可能将其解析为列表 ['bearer', token]，也可能是字符串 'bearer, <token>'
    protocol = headers.get("sec-websocket-protocol", "") or headers.get("Sec-WebSocket-Protocol", "")
    if isinstance(protocol, list):
        # Starlette 解析为列表格式
        vals = protocol
    else:
        # 字符串格式，按逗号+空格分割
        vals = [v.strip() for v in protocol.split(",")]
    # 找 'bearer' 子协议，其后紧跟的元素即为 token
    try:
        idx = vals.index("bearer")
        if idx + 1 < len(vals):
            return vals[idx + 1]
    except (ValueError, AttributeError):
        pass

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
