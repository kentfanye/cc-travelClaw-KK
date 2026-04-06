"""
Orchestrator — 总规划师调度引擎。
负责：需求分析 → 子任务拆解 → 并行分发 → 结果整合 → 质量把控。
支持同步/异步两种模式，异步模式下专家Agent并行执行。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import openai

from .agent import Agent, _DEFAULT_API_BASE, _DEFAULT_API_KEY, _extract_reply
from .errors import DecompositionError, IntegrationError
from .models import (
    EventType,
    PlanEvent,
    PlanResponse,
    TaskDecomposition,
    TaskResult,
    TravelRequest,
)
from .retry import with_async_retry, with_retry

logger = logging.getLogger("travelclaw.orchestrator")


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


def _parse_decomposition_json(raw: str) -> dict:
    """从LLM输出中提取JSON，支持code fence包裹和裸JSON。"""
    json_match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
    text = json_match.group(1) if json_match else raw
    return json.loads(text)


class Orchestrator:
    """
    总规划师 — 旅行规划团队的调度核心。
    """

    def __init__(
        self,
        workspace: Path,
        model: str = "glm-5.1",
        max_concurrent: int = 1,
        api_key: str | None = None,
        api_base: str | None = None,
    ):
        self.workspace = workspace
        self.model = model
        self.max_concurrent = max_concurrent
        self.api_key = api_key or _DEFAULT_API_KEY
        self.api_base = api_base or _DEFAULT_API_BASE
        self._client: openai.OpenAI | None = None
        self._async_client: openai.AsyncOpenAI | None = None
        # 从SOUL.md加载总规划师的身份
        soul_path = workspace / "SOUL.md"
        self.soul = soul_path.read_text(encoding="utf-8") if soul_path.exists() else ""

    @property
    def client(self) -> openai.OpenAI:
        if self._client is None:
            self._client = openai.OpenAI(
                api_key=self.api_key, base_url=self.api_base, timeout=120.0
            )
        return self._client

    @property
    def async_client(self) -> openai.AsyncOpenAI:
        if self._async_client is None:
            self._async_client = openai.AsyncOpenAI(
                api_key=self.api_key, base_url=self.api_base, timeout=120.0
            )
        return self._async_client

    # ── 第一步：子任务拆解 ─────────────────────────────────

    @with_retry(max_attempts=3)
    def decompose_tasks(self, user_request: str) -> TaskDecomposition:
        """将用户需求拆解为子任务（同步）。"""
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=2048,
            messages=[
                {"role": "system", "content": self.soul + "\n\n" + TASK_DECOMPOSE_PROMPT},
                {"role": "user", "content": user_request},
            ],
        )
        raw = _extract_reply(response.choices[0].message)
        try:
            data = _parse_decomposition_json(raw)
            return TaskDecomposition.model_validate(data)
        except (json.JSONDecodeError, Exception) as e:
            raise DecompositionError(f"子任务拆解JSON解析失败: {e}\n原始输出: {raw[:500]}") from e

    @with_async_retry(max_attempts=3)
    async def adecompose_tasks(self, user_request: str) -> TaskDecomposition:
        """将用户需求拆解为子任务（异步）。"""
        response = await self.async_client.chat.completions.create(
            model=self.model,
            max_tokens=2048,
            messages=[
                {"role": "system", "content": self.soul + "\n\n" + TASK_DECOMPOSE_PROMPT},
                {"role": "user", "content": user_request},
            ],
        )
        raw = _extract_reply(response.choices[0].message)
        try:
            data = _parse_decomposition_json(raw)
            return TaskDecomposition.model_validate(data)
        except (json.JSONDecodeError, Exception) as e:
            raise DecompositionError(f"子任务拆解JSON解析失败: {e}\n原始输出: {raw[:500]}") from e

    # ── 第二步：子任务分发 ─────────────────────────────────

    def dispatch_tasks(
        self, decomposition: TaskDecomposition, agents: dict[str, Agent]
    ) -> list[TaskResult]:
        """将子任务分发给专家Agent（同步，串行）。"""
        results = []
        for task in decomposition.tasks:
            if task.agent not in agents:
                logger.warning("未找到Agent: %s, 跳过", task.agent)
                results.append(
                    TaskResult(
                        agent_id=task.agent,
                        domain=task.agent,
                        content=f"[跳过] 未找到Agent: {task.agent}",
                        success=False,
                    )
                )
                continue

            logger.info("分发任务给 [%s]", task.agent)
            result = agents[task.agent].execute_task(task.instruction)
            logger.info("[%s] 完成, 耗时%dms", task.agent, result.duration_ms)
            results.append(result)
        return results

    async def adispatch_tasks(
        self, decomposition: TaskDecomposition, agents: dict[str, Agent]
    ) -> list[TaskResult]:
        """将子任务分发给专家Agent（异步，并行，受Semaphore限流）。"""
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def _run(agent_id: str, instruction: str) -> TaskResult:
            async with semaphore:
                logger.info("分发任务给 [%s]", agent_id)
                result = await agents[agent_id].aexecute_task(instruction)
                logger.info("[%s] 完成, 耗时%dms", agent_id, result.duration_ms)
                return result

        tasks_to_run = []
        skip_results = []
        for task in decomposition.tasks:
            if task.agent not in agents:
                logger.warning("未找到Agent: %s, 跳过", task.agent)
                skip_results.append(
                    TaskResult(
                        agent_id=task.agent,
                        domain=task.agent,
                        content=f"[跳过] 未找到Agent: {task.agent}",
                        success=False,
                    )
                )
            else:
                tasks_to_run.append(_run(task.agent, task.instruction))

        parallel_results = await asyncio.gather(*tasks_to_run, return_exceptions=True)

        results = list(skip_results)
        for r in parallel_results:
            if isinstance(r, Exception):
                logger.error("并行任务异常: %s", r)
                results.append(
                    TaskResult(
                        agent_id="unknown",
                        domain="unknown",
                        content=f"[异常] {r}",
                        success=False,
                    )
                )
            else:
                results.append(r)
        return results

    # ── 第三步：结果整合 ───────────────────────────────────

    @with_retry(max_attempts=3)
    def integrate_results(self, results: list[TaskResult]) -> str:
        """整合各专家结果为最终行程（同步）。"""
        expert_results = "\n\n".join(
            f"=== {r.agent_id}（{r.domain}）===\n{r.content}" for r in results if r.success
        )
        if not expert_results:
            raise IntegrationError("所有专家Agent均执行失败，无法整合")

        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=8192,
            messages=[
                {"role": "system", "content": self.soul},
                {
                    "role": "user",
                    "content": INTEGRATE_PROMPT.format(expert_results=expert_results),
                },
            ],
        )
        return _extract_reply(response.choices[0].message)

    @with_async_retry(max_attempts=3)
    async def aintegrate_results(self, results: list[TaskResult]) -> str:
        """整合各专家结果为最终行程（异步）。"""
        expert_results = "\n\n".join(
            f"=== {r.agent_id}（{r.domain}）===\n{r.content}" for r in results if r.success
        )
        if not expert_results:
            raise IntegrationError("所有专家Agent均执行失败，无法整合")

        response = await self.async_client.chat.completions.create(
            model=self.model,
            max_tokens=8192,
            messages=[
                {"role": "system", "content": self.soul},
                {
                    "role": "user",
                    "content": INTEGRATE_PROMPT.format(expert_results=expert_results),
                },
            ],
        )
        return _extract_reply(response.choices[0].message)

    # ── 完整规划流程 ───────────────────────────────────────

    def plan(self, user_request: str, agents: dict[str, Agent]) -> PlanResponse:
        """完整规划流程（同步）：拆解 → 分发 → 整合。"""
        plan_id = uuid.uuid4().hex[:12]
        start = time.monotonic()
        logger.info("[%s] 开始规划", plan_id)

        logger.info("[%s] [1/3] 拆解子任务...", plan_id)
        decomposition = self.decompose_tasks(user_request)
        logger.info(
            "[%s] 目的地=%s 天数=%d 子任务数=%d",
            plan_id,
            decomposition.destination,
            decomposition.days,
            len(decomposition.tasks),
        )

        logger.info("[%s] [2/3] 分发给专家团队...", plan_id)
        results = self.dispatch_tasks(decomposition, agents)

        logger.info("[%s] [3/3] 整合行程方案...", plan_id)
        final_plan = self.integrate_results(results)

        duration = int((time.monotonic() - start) * 1000)
        logger.info("[%s] 规划完成, 总耗时%dms", plan_id, duration)

        return PlanResponse(
            plan_id=plan_id,
            request=TravelRequest(
                destination=decomposition.destination,
                days=decomposition.days or 1,
                budget=decomposition.budget,
                travelers=decomposition.travelers,
            ),
            plan=final_plan,
            agent_results=results,
            duration_ms=duration,
            agents_used=[r.agent_id for r in results if r.success],
        )

    async def aplan(self, user_request: str, agents: dict[str, Agent]) -> PlanResponse:
        """完整规划流程（异步，专家并行）：拆解 → 并行分发 → 整合。"""
        plan_id = uuid.uuid4().hex[:12]
        start = time.monotonic()
        logger.info("[%s] 开始异步规划", plan_id)

        logger.info("[%s] [1/3] 拆解子任务...", plan_id)
        decomposition = await self.adecompose_tasks(user_request)
        logger.info(
            "[%s] 目的地=%s 天数=%d 子任务数=%d",
            plan_id,
            decomposition.destination,
            decomposition.days,
            len(decomposition.tasks),
        )

        logger.info("[%s] [2/3] 并行分发给专家团队...", plan_id)
        results = await self.adispatch_tasks(decomposition, agents)

        logger.info("[%s] [3/3] 整合行程方案...", plan_id)
        final_plan = await self.aintegrate_results(results)

        duration = int((time.monotonic() - start) * 1000)
        logger.info("[%s] 异步规划完成, 总耗时%dms", plan_id, duration)

        return PlanResponse(
            plan_id=plan_id,
            request=TravelRequest(
                destination=decomposition.destination,
                days=decomposition.days or 1,
                budget=decomposition.budget,
                travelers=decomposition.travelers,
            ),
            plan=final_plan,
            agent_results=results,
            duration_ms=duration,
            agents_used=[r.agent_id for r in results if r.success],
        )

    async def aplan_stream(
        self, user_request: str, agents: dict[str, Agent]
    ) -> AsyncGenerator[PlanEvent, None]:
        """
        流式规划流程（async generator）。
        yield PlanEvent事件，供WebSocket/SSE/CLI消费。
        """
        plan_id = uuid.uuid4().hex[:12]
        start = time.monotonic()

        yield PlanEvent(type=EventType.PLAN_START, plan_id=plan_id)

        # 1. 拆解
        yield PlanEvent(type=EventType.DECOMPOSE_START, plan_id=plan_id)
        try:
            decomposition = await self.adecompose_tasks(user_request)
        except Exception as e:
            yield PlanEvent(type=EventType.PLAN_ERROR, plan_id=plan_id, data=str(e))
            return
        yield PlanEvent(
            type=EventType.DECOMPOSE_DONE,
            plan_id=plan_id,
            data={
                "destination": decomposition.destination,
                "days": decomposition.days,
                "task_count": len(decomposition.tasks),
            },
        )

        # 2. 并行分发（逐个Agent yield事件）
        semaphore = asyncio.Semaphore(self.max_concurrent)
        results: list[TaskResult] = []

        async def _run_and_collect(agent_id: str, instruction: str):
            async with semaphore:
                return await agents[agent_id].aexecute_task(instruction)

        pending_tasks = {}
        for task in decomposition.tasks:
            if task.agent in agents:
                yield PlanEvent(type=EventType.AGENT_START, plan_id=plan_id, agent_id=task.agent)
                coro = _run_and_collect(task.agent, task.instruction)
                pending_tasks[task.agent] = asyncio.create_task(coro)

        for agent_id, async_task in pending_tasks.items():
            try:
                result = await async_task
                results.append(result)
                yield PlanEvent(
                    type=EventType.AGENT_DONE,
                    plan_id=plan_id,
                    agent_id=agent_id,
                    data={"duration_ms": result.duration_ms},
                )
            except Exception as e:
                results.append(
                    TaskResult(
                        agent_id=agent_id,
                        domain=agent_id,
                        content=f"[错误] {e}",
                        success=False,
                    )
                )
                yield PlanEvent(
                    type=EventType.AGENT_ERROR, plan_id=plan_id, agent_id=agent_id, data=str(e)
                )

        # 3. 整合
        yield PlanEvent(type=EventType.INTEGRATE_START, plan_id=plan_id)
        try:
            final_plan = await self.aintegrate_results(results)
        except Exception as e:
            yield PlanEvent(type=EventType.PLAN_ERROR, plan_id=plan_id, data=str(e))
            return

        duration = int((time.monotonic() - start) * 1000)
        yield PlanEvent(
            type=EventType.PLAN_COMPLETE,
            plan_id=plan_id,
            data={
                "plan": final_plan,
                "duration_ms": duration,
                "agents_used": [r.agent_id for r in results if r.success],
            },
        )
