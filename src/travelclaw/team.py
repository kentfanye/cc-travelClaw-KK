"""
Team — 团队组装模块。
从config.yaml读取配置，创建Agent实例，组装团队。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from pathlib import Path

import yaml

from .agent import Agent
from .models import PlanEvent, PlanResponse
from .orchestrator import Orchestrator

logger = logging.getLogger("travelclaw.team")


class TravelTeam:
    """
    旅行规划AI团队 — 从配置文件自动组装。
    支持同步plan()和异步aplan()/aplan_stream()。
    """

    def __init__(self, config_path: str | Path = "config.yaml"):
        self.config_path = Path(config_path)
        self.project_root = self.config_path.parent
        self.config = self._load_config()

        self.orchestrator: Orchestrator | None = None
        self.agents: dict[str, Agent] = {}
        self._build_team()
        logger.info(
            "团队组装完成: orchestrator=%s, specialists=%s",
            self.orchestrator is not None,
            list(self.agents.keys()),
        )

    def _load_config(self) -> dict:
        with open(self.config_path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _build_team(self):
        """根据config.yaml构建团队"""
        model_config = self.config.get("model", {})
        default_model = model_config.get("default", "claude-sonnet-4-6")
        orchestrator_model = model_config.get("orchestrator", default_model)

        for agent_id, agent_conf in self.config.get("agents", {}).items():
            workspace = self.project_root / agent_conf["workspace"]

            if agent_conf.get("role") == "orchestrator":
                self.orchestrator = Orchestrator(
                    workspace=workspace,
                    model=orchestrator_model,
                )
            else:
                self.agents[agent_id] = Agent(
                    agent_id=agent_id,
                    workspace=workspace,
                    model=default_model,
                )

    def _ensure_orchestrator(self) -> Orchestrator:
        if not self.orchestrator:
            raise RuntimeError("团队中未找到总规划师(orchestrator)，请检查config.yaml")
        return self.orchestrator

    def plan(self, user_request: str) -> PlanResponse:
        """团队协作规划（同步）。"""
        return self._ensure_orchestrator().plan(user_request, self.agents)

    async def aplan(self, user_request: str) -> PlanResponse:
        """团队协作规划（异步，专家并行）。"""
        return await self._ensure_orchestrator().aplan(user_request, self.agents)

    async def aplan_stream(self, user_request: str) -> AsyncGenerator[PlanEvent, None]:
        """团队协作规划（异步流式，yield进度事件）。"""
        async for event in self._ensure_orchestrator().aplan_stream(user_request, self.agents):
            yield event

    def list_agents(self) -> list[dict]:
        """列出团队所有成员"""
        members = []
        if self.orchestrator:
            members.append(
                {
                    "id": "planner",
                    "role": "总规划师",
                    "type": "orchestrator",
                }
            )
        for agent_id in self.agents:
            members.append(
                {
                    "id": agent_id,
                    "role": self.config["agents"][agent_id].get("description", agent_id),
                    "type": "specialist",
                    "domain": self.config["agents"][agent_id].get("domain", ""),
                }
            )
        return members
