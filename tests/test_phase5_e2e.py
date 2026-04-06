"""
Phase 5 端到端验证 — 环境配置 + 健康探针 + 中间件 + Docker文件。
"""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from travelclaw.api.app import create_app
from travelclaw.api.routes import get_db_session
from travelclaw.config import Settings, get_settings, reset_settings
from travelclaw.team import TravelTeam

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = str(PROJECT_ROOT / "config.yaml")


# ── 1. Settings配置 ──────────────────────────────────────


class TestSettings:
    def setup_method(self):
        reset_settings()

    def test_default_settings(self):
        s = Settings()
        assert s.database_url == "sqlite+aiosqlite:///travelclaw.db"
        assert s.log_level == "INFO"
        assert s.max_concurrent_agents == 3
        assert s.port == 8000

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("TRAVELCLAW_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("TRAVELCLAW_PORT", "9000")
        monkeypatch.setenv("TRAVELCLAW_MAX_CONCURRENT_AGENTS", "5")
        s = Settings()
        assert s.log_level == "DEBUG"
        assert s.port == 9000
        assert s.max_concurrent_agents == 5

    def test_get_settings_singleton(self):
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2


# ── 2. 健康探针 ──────────────────────────────────────────


@pytest.fixture
async def app_with_db():
    app = create_app(config_path=CONFIG_PATH)
    app.state.team = TravelTeam(Path(CONFIG_PATH))

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async def override_get_session():
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_get_session

    # Store engine in storage module so health checks can use it
    from travelclaw.storage import database

    database._engine = engine
    yield app
    database.reset_engine()


class TestHealthProbes:
    @pytest.mark.asyncio
    async def test_liveness(self, app_with_db):
        transport = ASGITransport(app=app_with_db)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/health/live")
            assert resp.status_code == 200
            assert resp.json()["status"] == "alive"

    @pytest.mark.asyncio
    async def test_readiness(self, app_with_db):
        transport = ASGITransport(app=app_with_db)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/health/ready")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ready"

    @pytest.mark.asyncio
    async def test_health_with_db_status(self, app_with_db):
        transport = ASGITransport(app=app_with_db)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["database"] == "connected"
            assert data["agents_count"] == 7


# ── 3. 中间件 ────────────────────────────────────────────


class TestMiddleware:
    @pytest.mark.asyncio
    async def test_request_id_header(self, app_with_db):
        transport = ASGITransport(app=app_with_db)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/health/live")
            assert "x-request-id" in resp.headers
            assert "x-response-time" in resp.headers
            assert resp.headers["x-response-time"].endswith("ms")


# ── 4. Docker文件验证 ────────────────────────────────────


class TestDockerFiles:
    def test_dockerfile_exists(self):
        assert (PROJECT_ROOT / "Dockerfile").exists()

    def test_docker_compose_exists(self):
        assert (PROJECT_ROOT / "docker-compose.yml").exists()

    def test_dockerignore_exists(self):
        assert (PROJECT_ROOT / ".dockerignore").exists()

    def test_dockerfile_has_healthcheck(self):
        content = (PROJECT_ROOT / "Dockerfile").read_text()
        assert "HEALTHCHECK" in content
        assert "EXPOSE 8000" in content
        assert "appuser" in content  # non-root user

    def test_docker_compose_has_env(self):
        content = (PROJECT_ROOT / "docker-compose.yml").read_text()
        assert "TRAVELCLAW_ANTHROPIC_API_KEY" in content
        assert "TRAVELCLAW_DATABASE_URL" in content

    def test_ci_workflow_exists(self):
        assert (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").exists()
