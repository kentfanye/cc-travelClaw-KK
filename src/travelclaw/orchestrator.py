"""
Orchestrator — 总规划师调度引擎。
负责：需求分析 → 子任务拆解 → 并行分发 → 结果整合 → 质量把控。
类似OpenClaw中Orchestrator Agent的路由和调度逻辑。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

from .agent import Agent, TaskResult


TASK_DECOMPOSE_PROMPT = """\
你是旅行规划总规划师。用户提出了一个旅行需求，你需要将其拆解为子任务分发给专家团队。

可用的专家Agent：
- food: 美食顾问（餐饮推荐）
- hotel: 住宿顾问（酒店民宿）
- transport: 交通顾问（出行路线）
- attraction: 景点顾问（游览推荐）
- shopping: 购物顾问（特产伴手礼）
- entertainment: 娱乐顾问（休闲体验）

请分析用户需求，为每个相关的专家生成具体的任务指令。
务必严格按以下JSON格式输出，不要包含其他内容：

```json
{
  "destination": "目的地",
  "days": 天数,
  "budget": "预算描述",
  "travelers": "出行人描述",
  "tasks": [
    {"agent": "agent_id", "instruction": "给该专家的具体任务指令，包含目的地、天数、预算、偏好等上下文"}
  ]
}
```
"""

INTEGRATE_PROMPT = """\
你是旅行规划总规划师。各专家已完成子任务，请整合他们的建议为一份完整、连贯的行程方案。

要求：
1. 按天组织行程，每天有清晰的时间线
2. 确保景点、餐厅、交通之间的衔接合理
3. 预算汇总要清晰
4. 如果发现冲突（时间/地点不合理），主动调整
5. 输出一份用户可以直接使用的行程单

以下是各专家的建议：

{expert_results}

请整合为最终行程方案。
"""


class Orchestrator:
    """
    总规划师 — 旅行规划团队的调度核心。

    工作流：
    1. 接收用户需求
    2. 调用LLM拆解子任务（生成JSON分发指令）
    3. 将子任务分发给对应的专家Agent
    4. 收集所有专家的结果
    5. 调用LLM整合为最终行程
    """

    def __init__(self, workspace: Path, model: str = "claude-sonnet-4-6"):
        self.workspace = workspace
        self.model = model
        self.client = anthropic.Anthropic()
        # 从SOUL.md加载总规划师的身份
        soul_path = workspace / "SOUL.md"
        self.soul = soul_path.read_text(encoding="utf-8") if soul_path.exists() else ""

    def decompose_tasks(self, user_request: str) -> dict:
        """
        第一步：将用户需求拆解为子任务JSON。
        总规划师分析需求，生成分发给各专家的具体指令。
        """
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=self.soul + "\n\n" + TASK_DECOMPOSE_PROMPT,
            messages=[{"role": "user", "content": user_request}],
        )

        raw = response.content[0].text
        # 提取JSON块
        json_match = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1))
        # 尝试直接解析
        return json.loads(raw)

    def dispatch_tasks(
        self, task_plan: dict, agents: dict[str, Agent]
    ) -> list[TaskResult]:
        """
        第二步：将子任务分发给对应的专家Agent并收集结果。
        同步版本 — 依次执行各专家任务。
        """
        results = []
        for task in task_plan.get("tasks", []):
            agent_id = task["agent"]
            instruction = task["instruction"]

            if agent_id not in agents:
                results.append(TaskResult(
                    agent_id=agent_id,
                    domain=agent_id,
                    content=f"[跳过] 未找到Agent: {agent_id}",
                    success=False,
                ))
                continue

            print(f"  → 分发任务给 [{agent_id}] ...")
            result = agents[agent_id].execute_task(instruction)
            print(f"  ✓ [{agent_id}] 完成")
            results.append(result)

        return results

    def integrate_results(self, results: list[TaskResult]) -> str:
        """
        第三步：整合各专家结果为最终行程方案。
        总规划师负责质量把控和冲突调解。
        """
        expert_results = "\n\n".join(
            f"=== {r.agent_id}（{r.domain}）===\n{r.content}"
            for r in results
            if r.success
        )

        prompt = INTEGRATE_PROMPT.format(expert_results=expert_results)

        response = self.client.messages.create(
            model=self.model,
            max_tokens=8192,
            system=self.soul,
            messages=[{"role": "user", "content": prompt}],
        )

        return response.content[0].text

    def plan(self, user_request: str, agents: dict[str, Agent]) -> str:
        """
        完整的规划流程：拆解 → 分发 → 整合。
        """
        print("\n[1/3] 总规划师正在分析需求、拆解子任务...")
        task_plan = self.decompose_tasks(user_request)

        destination = task_plan.get("destination", "未知")
        days = task_plan.get("days", "?")
        print(f"  目的地: {destination} | 天数: {days}")
        print(f"  拆解出 {len(task_plan.get('tasks', []))} 个子任务\n")

        print("[2/3] 分发子任务给专家团队...")
        results = self.dispatch_tasks(task_plan, agents)
        print()

        print("[3/3] 总规划师正在整合行程方案...")
        final_plan = self.integrate_results(results)
        print("  ✓ 行程规划完成！\n")

        return final_plan
