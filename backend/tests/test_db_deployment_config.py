import pytest
from httpx import ASGITransport, AsyncClient


def test_db_config_prefers_db_env_vars(monkeypatch):
    from app.config import get_config

    get_config.cache_clear()
    monkeypatch.setenv("DB_HOST", "10.20.30.40")
    monkeypatch.setenv("DB_PORT", "3307")
    monkeypatch.setenv("DB_NAME", "lumapepe")
    monkeypatch.setenv("DB_USER", "remote_app")
    monkeypatch.setenv("DB_PASSWORD", "remote-secret")
    monkeypatch.setenv("MYSQL_HOST", "host.docker.internal")
    monkeypatch.setenv("MYSQL_PORT", "3306")
    monkeypatch.setenv("MYSQL_DATABASE", "wrong_db")
    monkeypatch.setenv("MYSQL_USER", "root")
    monkeypatch.setenv("MYSQL_PASSWORD", "old-secret")

    config = get_config()

    assert config.db.host == "10.20.30.40"
    assert config.db.port == 3307
    assert config.db.database == "lumapepe"
    assert config.db.user == "remote_app"
    assert config.db.password == "remote-secret"
    get_config.cache_clear()


def test_db_config_reads_remote_connection_retry_env(monkeypatch):
    from app.config import get_config

    get_config.cache_clear()
    monkeypatch.setenv("DB_HOST", "10.20.30.40")
    monkeypatch.setenv("DB_USER", "remote_app")
    monkeypatch.setenv("DB_PASSWORD", "remote-secret")
    monkeypatch.setenv("DB_CONNECT_RETRIES", "12")
    monkeypatch.setenv("DB_CONNECT_RETRY_DELAY", "3")
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", "15")

    config = get_config()

    assert config.db.connect_retries == 12
    assert config.db.connect_retry_delay == 3
    assert config.db.connect_timeout == 15
    get_config.cache_clear()


@pytest.mark.asyncio
async def test_wait_for_mysql_failure_reports_target_without_password(monkeypatch):
    from app.config import DBConfig
    from app.database import connection

    async def fail_connect(**kwargs):
        raise OSError("connection timed out")

    monkeypatch.setattr(connection.aiomysql, "connect", fail_connect)
    config = DBConfig(
        host="10.20.30.40",
        port=3307,
        user="remote_app",
        password="remote-secret",
        database="lumapepe",
    )

    with pytest.raises(RuntimeError) as exc_info:
        await connection._wait_for_mysql(config, max_retries=1, delay=0)

    message = str(exc_info.value)
    assert "10.20.30.40:3307" in message
    assert "lumapepe" in message
    assert "remote_app" in message
    assert "connection timed out" in message
    assert "remote-secret" not in message


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeCursor:
    def __init__(self, table_names):
        self.table_names = table_names
        self._result = None

    async def execute(self, sql, params=None):
        if "SELECT DATABASE()" in sql:
            self._result = {"database_name": "lumapepe"}
        elif "information_schema.TABLES" in sql:
            self._result = [{"TABLE_NAME": table_name} for table_name in self.table_names]
        else:
            self._result = {"ok": 1}

    async def fetchone(self):
        return self._result

    async def fetchall(self):
        return self._result


class _FakeConnection:
    def __init__(self, table_names):
        self.table_names = table_names

    def cursor(self, *args, **kwargs):
        return _AsyncContext(_FakeCursor(self.table_names))


class _FakePool:
    def __init__(self, table_names):
        self.table_names = table_names

    def acquire(self):
        return _AsyncContext(_FakeConnection(self.table_names))


@pytest.mark.asyncio
async def test_health_db_reports_configured_and_selected_database(monkeypatch):
    from app.config import get_config
    from app.api import health
    from app.database import connection
    from fastapi import FastAPI

    required_tables = [
        "ragz_sessions",
        "ragz_messages",
        "ragz_documents",
        "member_user",
        "promotion_kefu_conversation",
        "promotion_kefu_message",
        "system_users",
    ]
    get_config.cache_clear()
    monkeypatch.setenv("DB_HOST", "10.20.30.40")
    monkeypatch.setenv("DB_PORT", "3307")
    monkeypatch.setenv("DB_NAME", "lumapepe")
    monkeypatch.setenv("DB_USER", "remote_app")
    monkeypatch.setenv("DB_PASSWORD", "remote-secret")
    monkeypatch.setattr(connection, "get_db_pool", lambda: _FakePool(required_tables))

    app = FastAPI()
    app.include_router(health.public_router)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/health/db")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["configured"]["host"] == "10.20.30.40"
    assert data["configured"]["port"] == 3307
    assert data["configured"]["database"] == "lumapepe"
    assert data["configured"]["user"] == "remote_app"
    assert "password" not in data["configured"]
    assert data["selected_database"] == "lumapepe"
    assert data["required_tables"]["missing"] == []
    assert data["required_tables"]["present"] == required_tables
    get_config.cache_clear()


@pytest.mark.asyncio
async def test_health_db_missing_rag_tables_keeps_connection_healthy(monkeypatch):
    from app.config import get_config
    from app.api import health
    from app.database import connection
    from fastapi import FastAPI

    existing_remote_tables = [
        "member_user",
        "promotion_kefu_conversation",
        "promotion_kefu_message",
        "system_users",
    ]
    get_config.cache_clear()
    monkeypatch.setenv("DB_HOST", "10.20.30.40")
    monkeypatch.setenv("DB_PORT", "3307")
    monkeypatch.setenv("DB_NAME", "lumapepe")
    monkeypatch.setenv("DB_USER", "remote_app")
    monkeypatch.setenv("DB_PASSWORD", "remote-secret")
    monkeypatch.setattr(connection, "get_db_pool", lambda: _FakePool(existing_remote_tables))

    app = FastAPI()
    app.include_router(health.public_router)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/health/db")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["status"] == "degraded"
    assert data["selected_database"] == "lumapepe"
    assert data["required_tables"]["ok"] is False
    assert data["required_tables"]["missing"] == [
        "ragz_sessions",
        "ragz_messages",
        "ragz_documents",
    ]
    get_config.cache_clear()


@pytest.mark.asyncio
async def test_health_db_failure_returns_503_without_password(monkeypatch):
    from app.config import get_config
    from app.api import health
    from app.database import connection
    from fastapi import FastAPI

    get_config.cache_clear()
    monkeypatch.setenv("DB_HOST", "10.20.30.40")
    monkeypatch.setenv("DB_PORT", "3307")
    monkeypatch.setenv("DB_NAME", "lumapepe")
    monkeypatch.setenv("DB_USER", "remote_app")
    monkeypatch.setenv("DB_PASSWORD", "remote-secret")

    def fail_get_pool():
        raise RuntimeError("cannot connect with remote-secret")

    monkeypatch.setattr(connection, "get_db_pool", fail_get_pool)

    app = FastAPI()
    app.include_router(health.public_router)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/health/db")

    assert response.status_code == 503
    data = response.json()
    assert data["ok"] is False
    assert data["configured"]["host"] == "10.20.30.40"
    assert "password" not in data["configured"]
    assert "remote-secret" not in data["error"]["message"]
    get_config.cache_clear()
