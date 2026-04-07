"""
Phase 1 端到端验证 — 验证核心健壮性改造的完整性。
不调用真实API，使用mock验证整个流程。
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from travelclaw.models import (
    EventType,
    PlanEvent,
    PlanResponse,
    TaskDecomposition,
    TaskResult,
    TravelRequest,
)
from travelclaw.errors import (
    AgentError,
    DecompositionError,
    IntegrationError,
    TravelClawError,
)
from travelclaw.agent import Agent
from travelclaw.orchestrator import Orchestrator, _parse_decomposition_json
from travelclaw.team import TravelTeam
from travelclaw.logging_config import setup_logging


# ── 配置路径 ──────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
PLANNER_WORKSPACE = PROJECT_ROOT / "agents" / "planner"
FOOD_WORKSPACE = PROJECT_ROOT / "agents" / "food"


def _mock_openai_response(text: str) -> MagicMock:
    """创建OpenAI兼容的mock响应"""
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=text))]
    return resp


def _mock_stream_chunks(text: str):
    """创建模拟流式响应的chunk列表（同步迭代器）"""
    chunks = []
    for char in text:
        chunk = MagicMock()
        delta = MagicMock()
        delta.content = char
        delta.reasoning_content = None
        chunk.choices = [MagicMock(delta=delta)]
        chunks.append(chunk)
    return chunks


class _AsyncChunkIterator:
    """模拟异步流式响应"""

    def __init__(self, text: str):
        self._chunks = _mock_stream_chunks(text)
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk


# ── 1. Pydantic模型校验 ──────────────────────────────────


class TestModels:
    def test_travel_request_valid(self):
        req = TravelRequest(destination="东京", days=5, budget="2万", travelers="情侣")
        assert req.destination == "东京"
        assert req.days == 5

    def test_travel_request_invalid_days(self):
        with pytest.raises(Exception):  # ValidationError
            TravelRequest(destination="东京", days=0)

    def test_travel_request_invalid_destination(self):
        with pytest.raises(Exception):
            TravelRequest(destination="", days=5)

    def test_task_decomposition_validation(self):
        td = TaskDecomposition(
            destination="东京",
            days=5,
            budget="2万",
            travelers="2人",
            tasks=[{"agent": "food", "instruction": "推荐美食"}],
        )
        assert len(td.tasks) == 1
        assert td.tasks[0].agent == "food"

    def test_task_result(self):
        tr = TaskResult(agent_id="food", domain="food", content="推荐拉面", duration_ms=1500)
        assert tr.success is True
        assert tr.duration_ms == 1500

    def test_plan_response_has_plan_id(self):
        pr = PlanResponse(
            request=TravelRequest(destination="东京", days=3),
            plan="测试行程",
        )
        assert len(pr.plan_id) == 12
        assert pr.created_at is not None

    def test_plan_event(self):
        event = PlanEvent(type=EventType.PLAN_START, plan_id="abc123")
        assert event.type == EventType.PLAN_START


# ── 2. 错误层级 ──────────────────────────────────────────


class TestErrors:
    def test_error_hierarchy(self):
        assert issubclass(AgentError, TravelClawError)
        assert issubclass(DecompositionError, TravelClawError)
        assert issubclass(IntegrationError, TravelClawError)

    def test_agent_error_contains_id(self):
        err = AgentError("food", "API调用失败")
        assert "food" in str(err)
        assert "API调用失败" in str(err)


# ── 3. JSON解析工具 ──────────────────────────────────────


class TestJsonParsing:
    def test_parse_code_fence_json(self):
        raw = '一些文字\n```json\n{"destination":"东京","days":3,"tasks":[]}\n```\n更多文字'
        data = _parse_decomposition_json(raw)
        assert data["destination"] == "东京"

    def test_parse_bare_json(self):
        raw = '{"destination":"大阪","days":2,"tasks":[]}'
        data = _parse_decomposition_json(raw)
        assert data["destination"] == "大阪"

    def test_parse_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_decomposition_json("这不是JSON")


# ── 4. Agent基本功能 ─────────────────────────────────────


class TestAgent:
    def test_soul_loading(self):
        agent = Agent(agent_id="food", workspace=FOOD_WORKSPACE)
        assert "美食顾问" in agent.soul

    def test_soul_fallback(self, tmp_path):
        agent = Agent(agent_id="test", workspace=tmp_path)
        assert "test" in agent.soul

    def test_reset_clears_history(self):
        agent = Agent(agent_id="food", workspace=FOOD_WORKSPACE)
        agent.history.append({"role": "user", "content": "test"})
        agent.reset()
        assert len(agent.history) == 0

    def test_chat_with_mock(self):
        agent = Agent(agent_id="food", workspace=FOOD_WORKSPACE)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response("推荐寿司")
        agent._client = mock_client

        reply = agent.chat("推荐东京美食")
        assert reply == "推荐寿司"
        assert len(agent.history) == 2

    def test_execute_task_with_mock(self):
        agent = Agent(agent_id="food", workspace=FOOD_WORKSPACE)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response("拉面推荐")
        agent._client = mock_client

        result = agent.execute_task("推荐拉面")
        assert result.success is True
        assert result.agent_id == "food"
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_async_execute_task_with_mock(self):
        agent = Agent(agent_id="hotel", workspace=PROJECT_ROOT / "agents" / "hotel")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response("推荐新宿酒店")
        agent._async_client = mock_client

        result = await agent.aexecute_task("推荐酒店")
        assert result.success is True
        assert "新宿" in result.content


# ── 5. Orchestrator流程 ──────────────────────────────────


class TestOrchestrator:
    def _make_orchestrator(self):
        return Orchestrator(workspace=PLANNER_WORKSPACE)

    def _make_mock_agents(self, *agent_ids):
        """创建mock agents，不需要真的调用API"""
        agents = {}
        for aid in agent_ids:
            agents[aid] = Agent(
                agent_id=aid, workspace=PROJECT_ROOT / "agents" / aid
            )
        return agents

    def test_decompose_with_mock(self):
        """向后兼容：decompose_tasks仍可用"""
        orch = self._make_orchestrator()
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response(
            json.dumps(
                {
                    "destination": "东京",
                    "days": 5,
                    "budget": "2万",
                    "travelers": "2人",
                    "tasks": [
                        {"agent": "food", "instruction": "推荐东京美食"},
                        {"agent": "hotel", "instruction": "推荐酒店"},
                    ],
                }
            )
        )
        orch._client = mock_client

        decomp = orch.decompose_tasks("5天东京游")
        assert decomp.destination == "东京"
        assert len(decomp.tasks) == 2

    def test_dispatch_with_mock_agents(self):
        """向后兼容：dispatch_tasks仍可用"""
        orch = self._make_orchestrator()
        decomp = TaskDecomposition(
            destination="东京",
            days=5,
            tasks=[
                {"agent": "food", "instruction": "推荐美食"},
                {"agent": "missing_agent", "instruction": "不存在的"},
            ],
        )
        food_agent = Agent(agent_id="food", workspace=FOOD_WORKSPACE)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response("寿司推荐")
        food_agent._client = mock_client

        results = orch.dispatch_tasks(decomp, {"food": food_agent})
        assert len(results) == 2
        assert results[0].success is True
        assert results[1].success is False  # missing_agent

    def test_integrate_with_mock(self):
        """向后兼容：integrate_results仍可用"""
        orch = self._make_orchestrator()
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response(
            "Day 1: 浅草寺..."
        )
        orch._client = mock_client

        results = [
            TaskResult(agent_id="food", domain="food", content="寿司推荐"),
            TaskResult(agent_id="hotel", domain="hotel", content="新宿酒店"),
        ]
        plan = orch.integrate_results(results)
        assert "Day 1" in plan

    def test_integrate_all_failed_raises(self):
        orch = self._make_orchestrator()
        results = [
            TaskResult(agent_id="food", domain="food", content="失败", success=False),
        ]
        with pytest.raises(IntegrationError):
            orch.integrate_results(results)

    def test_unified_plan_sync(self):
        """核心测试：单次调用模式的完整规划（流式）"""
        orch = self._make_orchestrator()
        plan_text = "完整行程方案: Day1 浅草寺 → 寿司大 → 新宿酒店..."
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_stream_chunks(plan_text)
        orch._client = mock_client

        agents = self._make_mock_agents("food", "hotel")
        response = orch.plan("3天东京游", agents)

        assert isinstance(response, PlanResponse)
        assert response.plan_id
        assert "完整行程方案" in response.plan
        assert set(response.agents_used) == {"food", "hotel"}
        assert response.duration_ms >= 0
        assert mock_client.chat.completions.create.call_count == 1

    @pytest.mark.asyncio
    async def test_unified_plan_async(self):
        """核心测试：异步单次调用（流式）"""
        orch = self._make_orchestrator()
        plan_text = "大阪3日行程: Day1 道顿堀..."
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _AsyncChunkIterator(plan_text)
        orch._async_client = mock_client

        agents = self._make_mock_agents("food", "attraction")
        response = await orch.aplan("3天大阪游", agents)

        assert isinstance(response, PlanResponse)
        assert "大阪3日行程" in response.plan
        assert set(response.agents_used) == {"food", "attraction"}
        assert mock_client.chat.completions.create.call_count == 1


# ── 6. Team组装 ──────────────────────────────────────────


class TestTeam:
    def test_team_loads_from_config(self):
        team = TravelTeam(CONFIG_PATH)
        assert team.orchestrator is not None
        assert len(team.agents) == 6

    def test_team_list_agents(self):
        team = TravelTeam(CONFIG_PATH)
        members = team.list_agents()
        assert len(members) == 7  # 1 orchestrator + 6 specialists
        types = [m["type"] for m in members]
        assert types.count("orchestrator") == 1
        assert types.count("specialist") == 6

    def test_team_agent_ids(self):
        team = TravelTeam(CONFIG_PATH)
        expected = {"food", "hotel", "transport", "attraction", "shopping", "entertainment"}
        assert set(team.agents.keys()) == expected


# ── 7. 日志配置 ──────────────────────────────────────────


class TestLogging:
    def test_setup_logging_no_crash(self):
        setup_logging(level="DEBUG")
        setup_logging(level="INFO")  # 第二次调用不应重复handler

    def test_setup_logging_json(self):
        setup_logging(level="INFO", json_format=True)


# ── 8. CLI入口 ───────────────────────────────────────────


class TestCLI:
    def test_list_agents_cli(self):
        import subprocess

        result = subprocess.run(
            ["python", "-m", "travelclaw.main", "--config", str(CONFIG_PATH), "--list-agents"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
        )
        assert result.returncode == 0
        assert "planner" in result.stdout
        assert "food" in result.stdout
