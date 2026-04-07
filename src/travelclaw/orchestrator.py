"""
Orchestrator — 总规划师调度引擎。
采用OpenClaw式的prompt组合架构：将所有专家Agent的SOUL.md知识
编排进一个统一prompt，通过单次LLM调用产出完整行程方案。

多Agent ≠ 多次API调用。多Agent = 多角色知识的组织方式。
"""

from __future__ import annotations

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


def _build_team_system_prompt(orchestrator_soul: str, agents: dict[str, Agent]) -> str:
    """
    将总规划师和所有专家Agent的SOUL.md组合成一个统一的system prompt。
    这是OpenClaw config-first架构的核心：多角色知识在prompt层面融合。
    """
    parts = [orchestrator_soul.strip()]
    parts.append("\n\n---\n\n你拥有以下专家团队的全部知识，请以他们各自的专业视角综合规划：\n")
    for agent_id, agent in agents.items():
        parts.append(f"\n### 【{agent_id}】专家知识\n{agent.soul.strip()}\n")
    return "\n".join(parts)


UNIFIED_PLAN_PROMPT = """\
请根据用户的旅行需求，综合运用你所有专家团队的知识，产出一份完整的行程规划方案。

要求：
1. 先分析需求，提取目的地、天数、预算、出行人信息
2. 以总规划师的视角统筹，融合吃、住、行、游、购、娱六大维度
3. 按天组织行程，每天有清晰的时间线
4. 每个推荐都要体现对应专家的专业水准（具体餐厅名、酒店名、景点名、交通方式、费用估算）
5. 确保景点、餐厅、交通之间的衔接合理
6. 预算汇总要清晰
7. 输出一份用户可以直接使用的行程单
"""


def _parse_decomposition_json(raw: str) -> dict:
    """从LLM输出中提取JSON，支持code fence包裹和裸JSON。"""
    json_match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
    text = json_match.group(1) if json_match else raw
    return json.loads(text)


def _extract_request_info(plan_text: str, user_request: str) -> TravelRequest:
    """从规划结果中尝试提取结构化信息，兜底使用用户原始请求。"""
    # 简单启发式提取
    dest = ""
    days = 1

    # 尝试从用户请求中提取天数
    day_match = re.search(r"(\d+)\s*[天日]", user_request)
    if day_match:
        days = int(day_match.group(1))

    # 目的地：取用户请求的前面部分作为描述
    dest = user_request[:20].strip()

    return TravelRequest(destination=dest or "未指定", days=max(days, 1))


class Orchestrator:
    """
    总规划师 — 旅行规划团队的调度核心。

    采用prompt组合模式：将所有specialist的SOUL.md知识融合进一个prompt，
    通过单次LLM调用完成完整规划，避免多次API调用带来的限流和延迟问题。
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
                api_key=self.api_key, base_url=self.api_base, timeout=180.0
            )
        return self._client

    @property
    def async_client(self) -> openai.AsyncOpenAI:
        if self._async_client is None:
            self._async_client = openai.AsyncOpenAI(
                api_key=self.api_key, base_url=self.api_base, timeout=180.0
            )
        return self._async_client

    # ── 核心：流式调用规划（保持连接活跃） ────────────────

    @with_retry(max_attempts=5)
    def unified_plan_call(self, user_request: str, agents: dict[str, Agent]) -> str:
        """单次LLM调用完成完整规划（同步，流式接收）。"""
        system_prompt = _build_team_system_prompt(self.soul, agents)
        stream = self.client.chat.completions.create(
            model=self.model,
            max_tokens=8192,
            stream=True,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": UNIFIED_PLAN_PROMPT + "\n\n用户需求：" + user_request},
            ],
        )
        # 流式收集：每个chunk都有数据流动，防止idle连接被杀
        content_parts = []
        reasoning_parts = []
        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta:
                if delta.content:
                    content_parts.append(delta.content)
                # GLM-5.1推理模型的reasoning_content
                rc = getattr(delta, "reasoning_content", None)
                if rc:
                    reasoning_parts.append(rc)
        result = "".join(content_parts)
        if not result and reasoning_parts:
            result = "".join(reasoning_parts)
        return result

    @with_async_retry(max_attempts=5)
    async def aunified_plan_call(self, user_request: str, agents: dict[str, Agent]) -> str:
        """单次LLM调用完成完整规划（异步，流式接收）。"""
        system_prompt = _build_team_system_prompt(self.soul, agents)
        stream = await self.async_client.chat.completions.create(
            model=self.model,
            max_tokens=8192,
            stream=True,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": UNIFIED_PLAN_PROMPT + "\n\n用户需求：" + user_request},
            ],
        )
        # 异步流式收集：保持连接活跃
        content_parts = []
        reasoning_parts = []
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta:
                if delta.content:
                    content_parts.append(delta.content)
                rc = getattr(delta, "reasoning_content", None)
                if rc:
                    reasoning_parts.append(rc)
        result = "".join(content_parts)
        if not result and reasoning_parts:
            result = "".join(reasoning_parts)
        return result

    # ── 完整规划流程 ───────────────────────────────────────

    def plan(self, user_request: str, agents: dict[str, Agent]) -> PlanResponse:
        """完整规划流程（同步）：单次调用，融合所有专家知识。"""
        plan_id = uuid.uuid4().hex[:12]
        start = time.monotonic()
        logger.info("[%s] 开始规划（单次调用模式）", plan_id)
        logger.info("[%s] 融合 %d 位专家知识...", plan_id, len(agents))

        final_plan = self.unified_plan_call(user_request, agents)

        duration = int((time.monotonic() - start) * 1000)
        logger.info("[%s] 规划完成, 总耗时%dms", plan_id, duration)

        request_info = _extract_request_info(final_plan, user_request)
        return PlanResponse(
            plan_id=plan_id,
            request=request_info,
            plan=final_plan,
            agent_results=[
                TaskResult(
                    agent_id=aid,
                    domain=aid,
                    content="（知识已融合进统一规划）",
                )
                for aid in agents
            ],
            duration_ms=duration,
            agents_used=list(agents.keys()),
        )

    async def aplan(self, user_request: str, agents: dict[str, Agent]) -> PlanResponse:
        """完整规划流程（异步）：单次调用，融合所有专家知识。"""
        plan_id = uuid.uuid4().hex[:12]
        start = time.monotonic()
        logger.info("[%s] 开始异步规划（单次调用模式）", plan_id)
        logger.info("[%s] 融合 %d 位专家知识...", plan_id, len(agents))

        final_plan = await self.aunified_plan_call(user_request, agents)

        duration = int((time.monotonic() - start) * 1000)
        logger.info("[%s] 异步规划完成, 总耗时%dms", plan_id, duration)

        request_info = _extract_request_info(final_plan, user_request)
        return PlanResponse(
            plan_id=plan_id,
            request=request_info,
            plan=final_plan,
            agent_results=[
                TaskResult(
                    agent_id=aid,
                    domain=aid,
                    content="（知识已融合进统一规划）",
                )
                for aid in agents
            ],
            duration_ms=duration,
            agents_used=list(agents.keys()),
        )

    async def aplan_stream(
        self, user_request: str, agents: dict[str, Agent]
    ) -> AsyncGenerator[PlanEvent, None]:
        """
        流式规划流程（async generator）。
        单次调用模式下简化事件序列。
        """
        plan_id = uuid.uuid4().hex[:12]
        start = time.monotonic()

        yield PlanEvent(type=EventType.PLAN_START, plan_id=plan_id)

        # 通知正在融合所有专家知识
        yield PlanEvent(
            type=EventType.DECOMPOSE_START,
            plan_id=plan_id,
            data={"message": "融合所有专家知识，开始规划..."},
        )

        for agent_id in agents:
            yield PlanEvent(type=EventType.AGENT_START, plan_id=plan_id, agent_id=agent_id)

        # 单次调用
        try:
            final_plan = await self.aunified_plan_call(user_request, agents)
        except Exception as e:
            yield PlanEvent(type=EventType.PLAN_ERROR, plan_id=plan_id, data=str(e))
            return

        for agent_id in agents:
            yield PlanEvent(
                type=EventType.AGENT_DONE,
                plan_id=plan_id,
                agent_id=agent_id,
                data={"duration_ms": 0},
            )

        duration = int((time.monotonic() - start) * 1000)
        yield PlanEvent(
            type=EventType.PLAN_COMPLETE,
            plan_id=plan_id,
            data={
                "plan": final_plan,
                "duration_ms": duration,
                "agents_used": list(agents.keys()),
            },
        )

    # ── 向后兼容（测试用） ──────────────────────────────────

    @with_retry(max_attempts=5)
    def decompose_tasks(self, user_request: str) -> TaskDecomposition:
        """将用户需求拆解为子任务（同步，向后兼容）。"""
        TASK_DECOMPOSE_PROMPT = (
            "你是旅行规划总规划师。请将用户需求拆解为子任务。\n"
            "严格按JSON格式输出：\n"
            '```json\n{"destination":"目的地","days":天数,"budget":"预算","travelers":"出行人","tasks":[{"agent":"agent_id","instruction":"任务指令"}]}\n```'
        )
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
            raise DecompositionError(
                f"子任务拆解JSON解析失败: {e}\n原始输出: {raw[:500]}"
            ) from e

    def dispatch_tasks(
        self, decomposition: TaskDecomposition, agents: dict[str, Agent]
    ) -> list[TaskResult]:
        """将子任务分发给专家Agent（同步，向后兼容）。"""
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
            result = agents[task.agent].execute_task(task.instruction)
            results.append(result)
        return results

    @with_retry(max_attempts=5)
    def integrate_results(self, results: list[TaskResult]) -> str:
        """整合各专家结果为最终行程（同步，向后兼容）。"""
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
                {"role": "user", "content": f"请整合以下专家建议为最终行程：\n\n{expert_results}"},
            ],
        )
        return _extract_reply(response.choices[0].message)
