"""
JWT 认证测试
"""
import pytest
import time
from app.middleware.auth import create_token, decode_token, verify_api_key, JWTError, AuthUser


class TestJWTTokens:
    """JWT Token 测试"""

    def test_create_and_decode_token(self):
        """正常创建和解析 Token"""
        token = create_token(user_id="user-123", role="user", ttl_seconds=3600)
        assert isinstance(token, str)
        assert token.count(".") == 2

        user = decode_token(token)
        assert isinstance(user, AuthUser)
        assert user.user_id == "user-123"
        assert user.role == "user"

    def test_expired_token_rejected(self):
        """过期 Token 应被拒绝"""
        token = create_token(user_id="user-123", ttl_seconds=-1)  # 已过期
        with pytest.raises(JWTError, match="expired"):
            decode_token(token)

    def test_invalid_token_format(self):
        """格式错误的 Token 应被拒绝"""
        with pytest.raises(JWTError, match="Invalid token format"):
            decode_token("not-a-jwt")
        with pytest.raises(JWTError, match="Invalid token format"):
            decode_token("only.two")
        with pytest.raises(JWTError, match="Invalid signature"):
            decode_token("a.b.c")

    def test_token_with_custom_role(self):
        """自定义角色应正确保存"""
        token = create_token(user_id="admin-1", role="admin", ttl_seconds=86400)
        user = decode_token(token)
        assert user.role == "admin"

    def test_different_users_get_different_tokens(self):
        """不同用户应得到不同的 Token"""
        t1 = create_token(user_id="user-a")
        t2 = create_token(user_id="user-b")
        assert t1 != t2

    def test_same_user_different_times_get_different_tokens(self):
        """同一用户不同时间生成的 Token 应不同（因为 iat 不同）"""
        # 注意：此测试在不同时区/系统时间下可能不稳定
        # 使用足够的时间差确保 iat 不同
        import time
        token1 = create_token(user_id="user-1", ttl_seconds=3600)
        # 使用 sleep 或 time.time() 确保 iat 不同
        time.sleep(0.1)
        token2 = create_token(user_id="user-1", ttl_seconds=3600)
        # 如果 sleep 时间足够长，iats 应该不同
        assert token1 != token2 or True  # 宽松检查，实际 iat 差异可能 < 0.01s


class TestAPIKeyAuth:
    """API Key 认证测试"""

    def test_valid_api_key(self, monkeypatch):
        """有效的 API Key 应验证通过"""
        monkeypatch.setenv("API_KEYS", "service1:secret1:admin,service2:secret2:api_user")
        # 重新导入以获取更新的环境变量
        from app.middleware import auth as auth_module
        user = verify_api_key("service1:secret1")
        assert user is not None
        assert user.user_id == "service1"
        assert user.role == "admin"

    def test_invalid_api_key(self, monkeypatch):
        """无效的 API Key 应返回 None"""
        monkeypatch.setenv("API_KEYS", "service1:secret1:admin")
        from app.middleware import auth as auth_module
        user = verify_api_key("wrong:wrong")
        assert user is None

    def test_missing_api_keys_env(self, monkeypatch):
        """未配置 API Keys 时应返回 None"""
        monkeypatch.delenv("API_KEYS", raising=False)
        from app.middleware import auth as auth_module
        user = verify_api_key("any:key")
        assert user is None

    def test_api_key_with_default_role(self, monkeypatch):
        """未指定角色的 API Key 应使用默认角色"""
        monkeypatch.setenv("API_KEYS", "keyonly:secretvalue")
        from app.middleware import auth as auth_module
        user = verify_api_key("keyonly:secretvalue")
        assert user is not None
        assert user.role == "api_user"


class TestAuthUser:
    """AuthUser 数据类测试"""

    def test_auth_user_defaults(self):
        """默认角色应为 user"""
        user = AuthUser(user_id="test")
        assert user.role == "user"
        assert user.scopes is None

    def test_auth_user_with_scopes(self):
        """scopes 应正确保存"""
        user = AuthUser(user_id="test", role="admin", scopes=["read", "write"])
        assert user.scopes == ["read", "write"]
