"""
Phase 2 端到端验证 — FastAPI REST + WebSocket。
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from travelclaw.api.app import create_app
from travelclaw.models import EventType, PlanEvent, PlanResponse, TaskResult, TravelRequest
from travelclaw.team import TravelTeam

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = str(PROJECT_ROOT / "config.yaml")


@pytest.fixture
def app():
    """Create test app with team pre-initialized (bypass lifespan)."""
    app = create_app(config_path=CONFIG_PATH)
    # Manually init team since lifespan doesn't run with ASGITransport
    app.state.team = TravelTeam(Path(CONFIG_PATH))
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── Health ────────────────────────────────────────────────

class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client):
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.1.0"
        assert data["agents_count"] == 7


# ── Agents ────────────────────────────────────────────────

class TestAgentsEndpoint:
    @pytest.mark.asyncio
    async def test_list_agents(self, client):
        resp = await client.get("/api/v1/agents")
        assert resp.status_code == 200
        agents = resp.json()["agents"]
        assert len(agents) == 7
        types = [a["type"] for a in agents]
        assert "orchestrator" in types
        ids = [a["id"] for a in agents]
        for expected in ["food", "hotel", "transport", "attraction", "shopping", "entertainment"]:
            assert expected in ids


# ── Plan ──────────────────────────────────────────────────

class TestPlanEndpoint:
    @pytest.mark.asyncio
    async def test_plan_empty_request_returns_422(self, client):
        resp = await client.post("/api/v1/plan", json={"request": ""})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_plan_missing_body_returns_422(self, client):
        resp = await client.post("/api/v1/plan")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_plan_with_mock(self, app):
        """完整plan端点 — mock aplan。"""
        mock_response = PlanResponse(
            request=TravelRequest(destination="东京", days=3),
            plan="测试行程: Day1 浅草寺...",
            agents_used=["food", "hotel"],
        )
        app.state.team.aplan = AsyncMock(return_value=mock_response)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/plan", json={"request": "3天东京游"})
            assert resp.status_code == 200
            data = resp.json()
            assert "测试行程" in data["plan"]
            assert data["request"]["destination"] == "东京"
            assert len(data["agents_used"]) == 2
            assert data["plan_id"]


# ── WebSocket ─────────────────────────────────────────────

class TestWebSocketStream:
    def test_websocket_stream_with_mock(self, app):
        """WebSocket流式测试 — mock aplan_stream。"""

        async def mock_stream(request):
            yield PlanEvent(type=EventType.PLAN_START, plan_id="test123")
            yield PlanEvent(type=EventType.DECOMPOSE_DONE, plan_id="test123",
                            data={"destination": "东京", "days": 3, "task_count": 2})
            yield PlanEvent(type=EventType.AGENT_DONE, plan_id="test123", agent_id="food",
                            data={"duration_ms": 1500})
            yield PlanEvent(type=EventType.PLAN_COMPLETE, plan_id="test123",
                            data={"plan": "完整行程", "duration_ms": 5000, "agents_used": ["food"]})

        app.state.team.aplan_stream = mock_stream

        test_client = TestClient(app)
        with test_client.websocket_connect("/api/v1/plan/stream") as ws:
            ws.send_json({"request": "3天东京游"})

            events = []
            while True:
                try:
                    data = ws.receive_json()
                    events.append(data)
                    if data["type"] in ("plan_complete", "plan_error", "error"):
                        break
                except Exception:
                    break

            event_types = [e["type"] for e in events]
            assert "plan_start" in event_types
            assert "plan_complete" in event_types
            assert events[-1]["type"] == "plan_complete"
            assert events[-1]["data"]["plan"] == "完整行程"
