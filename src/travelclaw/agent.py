"""
Agent基类 — 类似OpenClaw中每个Agent的运行时抽象。
每个Agent从自己的SOUL.md加载身份，拥有独立的会话记忆。
支持同步和异步两种调用方式。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import openai

from .errors import AgentError
from .models import TaskResult
from .retry import with_async_retry, with_retry

logger = logging.getLogger("travelclaw.agent")

# GLM-5.1 (智谱AI) OpenAI兼容API
_DEFAULT_API_BASE = "https://api.z.ai/api/coding/paas/v4"
_DEFAULT_API_KEY = "f1993fa5335b49398522d4d3004adaa6.RqMo0C8kAI2Ji44X"


def _extract_reply(message) -> str:
    """从GLM-5.1响应中提取回复内容。

    GLM-5.1是推理模型，可能把内容放在reasoning_content而非content中。
    优先取content，若为空则取reasoning_content。
    """
    content = message.content or ""
    if not content:
        content = getattr(message, "reasoning_content", "") or ""
    return content


class Agent:
    """
    Agent基类 — 对应OpenClaw中一个独立的AI Agent。
    每个Agent有自己的SOUL.md(身份)、工作空间、会话历史。
    """

    def __init__(
        self,
        agent_id: str,
        workspace: Path,
        model: str = "glm-5.1",
        api_key: str | None = None,
        api_base: str | None = None,
    ):
        self.agent_id = agent_id
        self.workspace = workspace
        self.model = model
        self.api_key = api_key or _DEFAULT_API_KEY
        self.api_base = api_base or _DEFAULT_API_BASE
        self.history: list[dict] = []
        self._soul: str = ""
        self._client: openai.OpenAI | None = None
        self._async_client: openai.AsyncOpenAI | None = None

    @property
    def soul(self) -> str:
        """从SOUL.md加载Agent身份（惰性加载）"""
        if not self._soul:
            soul_path = self.workspace / "SOUL.md"
            if soul_path.exists():
                self._soul = soul_path.read_text(encoding="utf-8")
            else:
                self._soul = f"你是 {self.agent_id} Agent。"
        return self._soul

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

    @with_retry(max_attempts=3)
    def chat(self, user_message: str) -> str:
        """
        向Agent发送消息并获取回复（同步）。
        SOUL.md内容作为system prompt注入。
        """
        self.history.append({"role": "user", "content": user_message})
        try:
            messages = [{"role": "system", "content": self.soul}] + self.history
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=4096,
                messages=messages,
            )
            reply = _extract_reply(response.choices[0].message)
            self.history.append({"role": "assistant", "content": reply})
            logger.debug("[%s] chat完成, 回复长度=%d", self.agent_id, len(reply))
            return reply
        except Exception as e:
            # 回滚history中的user消息
            self.history.pop()
            if isinstance(
                e,
                (
                    openai.RateLimitError,
                    openai.APIConnectionError,
                    openai.InternalServerError,
                    openai.APITimeoutError,
                ),
            ):
                raise  # 让retry装饰器处理
            raise AgentError(self.agent_id, f"chat失败: {e}", cause=e) from e

    @with_async_retry(max_attempts=3)
    async def achat(self, user_message: str) -> str:
        """
        向Agent发送消息并获取回复（异步）。
        """
        self.history.append({"role": "user", "content": user_message})
        try:
            messages = [{"role": "system", "content": self.soul}] + self.history
            response = await self.async_client.chat.completions.create(
                model=self.model,
                max_tokens=4096,
                messages=messages,
            )
            reply = _extract_reply(response.choices[0].message)
            self.history.append({"role": "assistant", "content": reply})
            logger.debug("[%s] achat完成, 回复长度=%d", self.agent_id, len(reply))
            return reply
        except Exception as e:
            self.history.pop()
            if isinstance(
                e,
                (
                    openai.RateLimitError,
                    openai.APIConnectionError,
                    openai.InternalServerError,
                    openai.APITimeoutError,
                ),
            ):
                raise
            raise AgentError(self.agent_id, f"achat失败: {e}", cause=e) from e

    def execute_task(self, instruction: str) -> TaskResult:
        """执行子任务（同步），返回结构化结果。"""
        start = time.monotonic()
        try:
            reply = self.chat(instruction)
            duration = int((time.monotonic() - start) * 1000)
            return TaskResult(
                agent_id=self.agent_id,
                domain=self.agent_id,
                content=reply,
                duration_ms=duration,
            )
        except Exception as e:
            duration = int((time.monotonic() - start) * 1000)
            logger.error("[%s] 任务执行失败: %s", self.agent_id, e)
            return TaskResult(
                agent_id=self.agent_id,
                domain=self.agent_id,
                content=f"[错误] {e}",
                success=False,
                duration_ms=duration,
            )

    async def aexecute_task(self, instruction: str) -> TaskResult:
        """执行子任务（异步），返回结构化结果。"""
        start = time.monotonic()
        try:
            reply = await self.achat(instruction)
            duration = int((time.monotonic() - start) * 1000)
            return TaskResult(
                agent_id=self.agent_id,
                domain=self.agent_id,
                content=reply,
                duration_ms=duration,
            )
        except Exception as e:
            duration = int((time.monotonic() - start) * 1000)
            logger.error("[%s] 异步任务执行失败: %s", self.agent_id, e)
            return TaskResult(
                agent_id=self.agent_id,
                domain=self.agent_id,
                content=f"[错误] {e}",
                success=False,
                duration_ms=duration,
            )

    def reset(self):
        """清空会话历史"""
        self.history.clear()
