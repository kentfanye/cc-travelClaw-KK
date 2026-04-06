"""
Team — 团队组装模块。
从config.yaml读取配置，创建Agent实例，组装团队。
类似OpenClaw从配置文件加载agent列表和路由规则。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .agent import Agent
from .orchestrator import Orchestrator


class TravelTeam:
    """
    旅行规划AI团队 — 从配置文件自动组装。

    对应OpenClaw的多Agent团队概念：
    - 一个Orchestrator（总规划师）
    - 多个Specialist Agent（专家顾问）
    - config.yaml定义团队组成和路由规则
    """

    def __init__(self, config_path: str | Path = "config.yaml"):
        self.config_path = Path(config_path)
        self.project_root = self.config_path.parent
        self.config = self._load_config()

        # 从配置创建团队
        self.orchestrator: Orchestrator | None = None
        self.agents: dict[str, Agent] = {}
        self._build_team()

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

    def plan(self, user_request: str) -> str:
        """
        团队协作规划旅行行程。
        入口点 → 总规划师 → 分发给专家 → 整合结果。
        """
        if not self.orchestrator:
            raise RuntimeError("团队中未找到总规划师(orchestrator)，请检查config.yaml")

        return self.orchestrator.plan(user_request, self.agents)

    def list_agents(self) -> list[dict]:
        """列出团队所有成员"""
        members = []
        if self.orchestrator:
            members.append({
                "id": "planner",
                "role": "总规划师",
                "type": "orchestrator",
            })
        for agent_id, agent in self.agents.items():
            members.append({
                "id": agent_id,
                "role": self.config["agents"][agent_id].get("description", agent_id),
                "type": "specialist",
                "domain": self.config["agents"][agent_id].get("domain", ""),
            })
        return members
