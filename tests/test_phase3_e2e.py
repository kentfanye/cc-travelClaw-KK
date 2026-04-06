"""
Phase 3 端到端验证 — 持久化与记忆。
使用内存SQLite测试数据库round-trip。
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from travelclaw.api.app import create_app
from travelclaw.models import PlanResponse, TaskResult, TravelRequest
from travelclaw.storage.models import AgentResultRecord, ConversationMessage, Trip, UserPreference
from travelclaw.storage.repository import PreferenceRepository, TripRepository
from travelclaw.team import TravelTeam

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = str(PROJECT_ROOT / "config.yaml")


# ── Fixtures ──────────────────────────────────────────────

@pytest.fixture
async def db_engine():
    """创建内存SQLite引擎"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    """创建数据库会话"""
    async_session = sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session


# ── 1. 数据库模型和表创建 ─────────────────────────────────

class TestDatabaseModels:
    @pytest.mark.asyncio
    async def test_tables_created(self, db_engine):
        """验证所有表都被创建"""
        from sqlalchemy import inspect
        async with db_engine.connect() as conn:
            tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
        assert "trips" in tables
        assert "agent_results" in tables
        assert "conversation_messages" in tables
        assert "user_preferences" in tables


# ── 2. TripRepository CRUD ───────────────────────────────

class TestTripRepository:
    @pytest.mark.asyncio
    async def test_create_and_get_trip(self, db_session):
        repo = TripRepository(db_session)
        trip = await repo.create_trip("5天东京游", user_id="user1")
        assert trip.id
        assert trip.status == "planning"

        fetched = await repo.get_trip(trip.id)
        assert fetched is not None
        assert fetched.request_text == "5天东京游"
        assert fetched.user_id == "user1"

    @pytest.mark.asyncio
    async def test_list_trips(self, db_session):
        repo = TripRepository(db_session)
        await repo.create_trip("东京游", user_id="user1")
        await repo.create_trip("大阪游", user_id="user1")
        await repo.create_trip("京都游", user_id="user2")

        user1_trips = await repo.list_trips(user_id="user1")
        assert len(user1_trips) == 2

        user2_trips = await repo.list_trips(user_id="user2")
        assert len(user2_trips) == 1

    @pytest.mark.asyncio
    async def test_save_plan_response(self, db_session):
        repo = TripRepository(db_session)
        response = PlanResponse(
            plan_id="test123",
            request=TravelRequest(destination="东京", days=5, budget="2万"),
            plan="Day 1: 浅草寺...",
            agent_results=[
                TaskResult(agent_id="food", domain="food", content="拉面推荐", duration_ms=1500),
                TaskResult(agent_id="hotel", domain="hotel", content="新宿酒店", duration_ms=2000),
            ],
            duration_ms=5000,
            agents_used=["food", "hotel"],
        )

        trip = await repo.save_plan_response(response, user_id="user1")
        assert trip.id == "test123"
        assert trip.destination == "东京"
        assert trip.status == "completed"

        # 验证Agent结果也被保存
        from sqlalchemy import select
        result = await db_session.execute(
            select(AgentResultRecord).where(AgentResultRecord.trip_id == "test123")
        )
        agent_results = list(result.scalars().all())
        assert len(agent_results) == 2

    @pytest.mark.asyncio
    async def test_update_trip_status(self, db_session):
        repo = TripRepository(db_session)
        trip = await repo.create_trip("东京游")
        await repo.update_trip_status(trip.id, "completed", "最终行程")

        updated = await repo.get_trip(trip.id)
        assert updated.status == "completed"
        assert updated.final_plan == "最终行程"

    @pytest.mark.asyncio
    async def test_get_nonexistent_trip(self, db_session):
        repo = TripRepository(db_session)
        trip = await repo.get_trip("nonexistent")
        assert trip is None


# ── 3. PreferenceRepository ──────────────────────────────

class TestPreferenceRepository:
    @pytest.mark.asyncio
    async def test_set_and_get_preferences(self, db_session):
        repo = PreferenceRepository(db_session)
        await repo.set_preference("user1", "cuisine", "日料")
        await repo.set_preference("user1", "budget_style", "经济型")

        prefs = await repo.get_preferences("user1")
        assert prefs["cuisine"] == "日料"
        assert prefs["budget_style"] == "经济型"

    @pytest.mark.asyncio
    async def test_update_preference(self, db_session):
        repo = PreferenceRepository(db_session)
        await repo.set_preference("user1", "cuisine", "日料")
        await repo.set_preference("user1", "cuisine", "中餐")

        prefs = await repo.get_preferences("user1")
        assert prefs["cuisine"] == "中餐"

    @pytest.mark.asyncio
    async def test_delete_preference(self, db_session):
        repo = PreferenceRepository(db_session)
        await repo.set_preference("user1", "cuisine", "日料")
        await repo.delete_preference("user1", "cuisine")

        prefs = await repo.get_preferences("user1")
        assert "cuisine" not in prefs

    @pytest.mark.asyncio
    async def test_empty_preferences(self, db_session):
        repo = PreferenceRepository(db_session)
        prefs = await repo.get_preferences("no_such_user")
        assert prefs == {}


# ── 4. API端点集成持久化 ─────────────────────────────────

class TestAPIWithPersistence:
    @pytest.fixture
    def app(self, db_engine):
        """带内存数据库的测试App"""
        app = create_app(config_path=CONFIG_PATH)
        app.state.team = TravelTeam(Path(CONFIG_PATH))

        # Override db session dependency
        async def override_get_session():
            async_session = sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
            async with async_session() as session:
                yield session

        from travelclaw.api.routes import get_db_session
        app.dependency_overrides[get_db_session] = override_get_session
        return app

    @pytest.mark.asyncio
    async def test_plan_and_retrieve(self, app):
        """POST /plan后能通过GET /plan/{id}查回"""
        mock_response = PlanResponse(
            plan_id="api_test_1",
            request=TravelRequest(destination="东京", days=3),
            plan="API测试行程",
            agent_results=[TaskResult(agent_id="food", domain="food", content="寿司")],
            agents_used=["food"],
            duration_ms=1000,
        )
        app.state.team.aplan = AsyncMock(return_value=mock_response)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 创建行程
            resp = await client.post("/api/v1/plan", json={"request": "3天东京游"})
            assert resp.status_code == 200
            plan_id = resp.json()["plan_id"]

            # 查询行程
            resp = await client.get(f"/api/v1/plan/{plan_id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["destination"] == "东京"
            assert data["plan"] == "API测试行程"

    @pytest.mark.asyncio
    async def test_list_plans(self, app):
        mock_response = PlanResponse(
            request=TravelRequest(destination="大阪", days=2),
            plan="大阪行程",
            agents_used=["food"],
        )
        app.state.team.aplan = AsyncMock(return_value=mock_response)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/v1/plan", json={"request": "大阪游", "user_id": "test_user"})

            resp = await client.get("/api/v1/plans?user_id=test_user")
            assert resp.status_code == 200
            assert len(resp.json()["trips"]) >= 1

    @pytest.mark.asyncio
    async def test_plan_not_found(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/plan/nonexistent")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_preferences_crud(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Set preferences
            resp = await client.put(
                "/api/v1/user/test_user/preferences",
                json={"preferences": {"cuisine": "日料", "style": "休闲"}},
            )
            assert resp.status_code == 200

            # Get preferences
            resp = await client.get("/api/v1/user/test_user/preferences")
            assert resp.status_code == 200
            data = resp.json()
            assert data["preferences"]["cuisine"] == "日料"
            assert data["preferences"]["style"] == "休闲"
