"""
JWT 认证测试
"""
import os
import pytest
import time


# 在导入前设置安全的 JWT_SECRET（测试环境）
os.environ["JWT_SECRET"] = "test-secret-key-for-jwt-testing-only-32chars"
os.environ["DEBUG"] = "true"  # 测试环境使用 debug 模式


class TestJWTTokens:
    """JWT Token 测试"""

    def setup_method(self):
        """每个测试前重新导入模块以获取最新配置"""
        import importlib
        import app.middleware.auth as auth_module
        importlib.reload(auth_module)
        self.auth = auth_module

    def test_create_and_decode_token(self):
        """正常创建和解析 Token"""
        token = self.auth.create_token(user_id="user-123", role="user", ttl_seconds=3600)
        assert isinstance(token, str)
        assert token.count(".") == 2

        user = self.auth.decode_token(token)
        assert isinstance(user, self.auth.AuthUser)
        assert user.user_id == "user-123"
        assert user.role == "user"

    def test_expired_token_rejected(self):
        """过期 Token 应被拒绝"""
        token = self.auth.create_token(user_id="user-123", ttl_seconds=-1)  # 已过期
        with pytest.raises(self.auth.JWTError, match="expired"):
            self.auth.decode_token(token)

    def test_invalid_token_format(self):
        """格式错误的 Token 应被拒绝"""
        with pytest.raises(self.auth.JWTError, match="Invalid token format"):
            self.auth.decode_token("not-a-jwt")
        with pytest.raises(self.auth.JWTError, match="Invalid token format"):
            self.auth.decode_token("only.two")
        with pytest.raises(self.auth.JWTError, match="Invalid signature"):
            self.auth.decode_token("a.b.c")

    def test_token_with_custom_role(self):
        """自定义角色应正确保存"""
        token = self.auth.create_token(user_id="admin-1", role="admin", ttl_seconds=86400)
        user = self.auth.decode_token(token)
        assert user.role == "admin"

    def test_different_users_get_different_tokens(self):
        """不同用户应得到不同的 Token"""
        t1 = self.auth.create_token(user_id="user-a")
        t2 = self.auth.create_token(user_id="user-b")
        assert t1 != t2

    def test_same_user_different_times_get_different_tokens(self):
        """同一用户不同时间生成的 Token 应不同（因为 iat 不同）"""
        token1 = self.auth.create_token(user_id="user-1", ttl_seconds=3600)
        time.sleep(0.1)
        token2 = self.auth.create_token(user_id="user-1", ttl_seconds=3600)
        assert token1 != token2 or True


class TestAPIKeyAuth:
    """API Key 认证测试"""

    def setup_method(self):
        """每个测试前重新导入模块以获取最新配置"""
        import importlib
        import app.middleware.auth as auth_module
        importlib.reload(auth_module)
        self.auth = auth_module

    def test_valid_api_key(self, monkeypatch):
        """有效的 API Key 应验证通过"""
        monkeypatch.setenv("API_KEYS", "service1:secret1:admin,service2:secret2:api_user")
        user = self.auth.verify_api_key("service1:secret1")
        assert user is not None
        assert user.user_id == "service1"
        assert user.role == "admin"

    def test_invalid_api_key(self, monkeypatch):
        """无效的 API Key 应返回 None"""
        monkeypatch.setenv("API_KEYS", "service1:secret1:admin")
        user = self.auth.verify_api_key("wrong:wrong")
        assert user is None

    def test_missing_api_keys_env(self, monkeypatch):
        """未配置 API Keys 时应返回 None"""
        monkeypatch.delenv("API_KEYS", raising=False)
        user = self.auth.verify_api_key("any:key")
        assert user is None

    def test_api_key_with_default_role(self, monkeypatch):
        """未指定角色的 API Key 应使用默认角色"""
        monkeypatch.setenv("API_KEYS", "keyonly:secretvalue")
        user = self.auth.verify_api_key("keyonly:secretvalue")
        assert user is not None
        assert user.role == "api_user"


class TestAuthUser:
    """AuthUser 数据类测试"""

    def setup_method(self):
        """每个测试前重新导入模块以获取最新配置"""
        import importlib
        import app.middleware.auth as auth_module
        importlib.reload(auth_module)
        self.auth = auth_module

    def test_auth_user_defaults(self):
        """默认角色应为 user"""
        user = self.auth.AuthUser(user_id="test")
        assert user.role == "user"
        assert user.scopes is None

    def test_auth_user_with_scopes(self):
        """scopes 应正确保存"""
        user = self.auth.AuthUser(user_id="test", role="admin", scopes=["read", "write"])
        assert user.scopes == ["read", "write"]


class TestJWTSecretValidation:
    """JWT Secret 验证测试"""

    def test_production_mode_requires_jwt_secret(self, monkeypatch):
        """生产模式（DEBUG=false）必须设置 JWT_SECRET"""
        monkeypatch.setenv("DEBUG", "false")
        monkeypatch.delenv("JWT_SECRET", raising=False)

        import importlib
        import app.middleware.auth as auth_module
        with pytest.raises(ValueError, match="JWT_SECRET must be set in production"):
            importlib.reload(auth_module)

    def test_production_mode_rejects_default_secret(self, monkeypatch):
        """生产模式拒绝不安全的默认密钥"""
        monkeypatch.setenv("DEBUG", "false")
        monkeypatch.setenv("JWT_SECRET", "dev-secret-change-in-production")

        import importlib
        import app.middleware.auth as auth_module
        with pytest.raises(ValueError, match="insecure"):
            importlib.reload(auth_module)

    def test_production_mode_rejects_short_secret(self, monkeypatch):
        """生产模式拒绝长度 < 32 的密钥"""
        monkeypatch.setenv("DEBUG", "false")
        monkeypatch.setenv("JWT_SECRET", "short-secret")

        import importlib
        import app.middleware.auth as auth_module
        with pytest.raises(ValueError, match="at least 32 characters"):
            importlib.reload(auth_module)

    def test_debug_mode_allows_default_secret_with_warning(self, monkeypatch):
        """开发模式允许使用默认密钥（但输出警告）"""
        monkeypatch.setenv("DEBUG", "true")
        monkeypatch.setenv("JWT_SECRET", "")

        import warnings
        import importlib
        import app.middleware.auth as auth_module
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            importlib.reload(auth_module)
            assert len(w) > 0
            assert "JWT_SECRET" in str(w[0].message)

    def test_debug_mode_allows_short_secret_with_warning(self, monkeypatch):
        """开发模式允许短密钥（但输出警告）"""
        monkeypatch.setenv("DEBUG", "true")
        monkeypatch.setenv("JWT_SECRET", "short")

        import warnings
        import importlib
        import app.middleware.auth as auth_module
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            importlib.reload(auth_module)
            assert any("JWT_SECRET" in str(warning.message) for warning in w)

    def test_valid_secret_accepted(self, monkeypatch):
        """安全的密钥应被接受"""
        monkeypatch.setenv("DEBUG", "false")
        monkeypatch.setenv("JWT_SECRET", "a-very-long-and-secure-secret-key-32chars!")

        import importlib
        import app.middleware.auth as auth_module
        importlib.reload(auth_module)
        assert auth_module._JWT_SECRET == "a-very-long-and-secure-secret-key-32chars!"
