"""
WebSocket 认证测试
"""
import pytest
from app.middleware.auth import (
    extract_bearer_from_headers,
    create_token,
    decode_token,
    JWTError,
    verify_ws_token,
)


class TestExtractBearerFromHeaders:
    """测试从 headers 提取 Bearer Token"""

    def test_authorization_bearer(self):
        headers = {"authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.test"}
        token = extract_bearer_from_headers(headers)
        assert token == "eyJhbGciOiJIUzI1NiJ9.test"

    def test_authorization_lowercase(self):
        headers = {"Authorization": "Bearer test-token-123"}
        token = extract_bearer_from_headers(headers)
        assert token == "test-token-123"

    def test_websocket_protocol(self):
        headers = {"sec-websocket-protocol": "bearer, ws-token-456"}
        token = extract_bearer_from_headers(headers)
        assert token == "ws-token-456"

    def test_missing_token(self):
        headers = {}
        token = extract_bearer_from_headers(headers)
        assert token is None

    def test_invalid_format(self):
        headers = {"authorization": "Basic abc123"}
        token = extract_bearer_from_headers(headers)
        assert token is None


class TestCreateAndDecodeToken:
    """测试 JWT 创建和解析"""

    def test_create_and_decode(self):
        token = create_token(user_id="user-123", role="user")
        user = decode_token(token)
        assert user.user_id == "user-123"
        assert user.role == "user"

    def test_expired_token(self):
        token = create_token(user_id="user-123", ttl_seconds=-1)
        with pytest.raises(JWTError, match="expired"):
            decode_token(token)

    def test_invalid_token(self):
        with pytest.raises(JWTError, match="Invalid token format"):
            decode_token("not.a.valid.token")


@pytest.mark.asyncio
class TestVerifyWsToken:
    """测试 WebSocket Token 验证"""

    async def test_valid_token(self):
        token = create_token(user_id="ws-user", role="user")
        headers = {"authorization": f"Bearer {token}"}
        user = await verify_ws_token(headers)
        assert user.user_id == "ws-user"

    async def test_missing_token(self):
        headers = {}
        with pytest.raises(JWTError, match="Missing"):
            await verify_ws_token(headers)

    async def test_invalid_token(self):
        headers = {"authorization": "Bearer invalid.token"}
        with pytest.raises(JWTError):
            await verify_ws_token(headers)
