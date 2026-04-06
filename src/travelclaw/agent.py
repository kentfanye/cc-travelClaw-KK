"""
Agent基类 — 类似OpenClaw中每个Agent的运行时抽象。
每个Agent从自己的SOUL.md加载身份，拥有独立的会话记忆。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import anthropic


@dataclass
class Message:
    """Agent间通信的消息协议"""
    role: str          # "user" | "assistant" | "system"
    content: str
    sender: str = ""   # 发送者agent_id
    receiver: str = "" # 接收者agent_id


@dataclass
class TaskResult:
    """专家Agent返回给总规划师的结果"""
    agent_id: str
    domain: str        # 吃/住/行/游/购/娱
    content: str
    success: bool = True


@dataclass
class Agent:
    """
    Agent基类 — 对应OpenClaw中一个独立的AI Agent。
    每个Agent有自己的SOUL.md(身份)、工作空间、会话历史。
    """
    agent_id: str
    workspace: Path
    model: str = "claude-sonnet-4-6"
    history: list[dict] = field(default_factory=list)
    _soul: str = ""
    _client: anthropic.Anthropic | None = None

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
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    def chat(self, user_message: str) -> str:
        """
        向Agent发送消息并获取回复。
        SOUL.md内容作为system prompt注入。
        """
        self.history.append({"role": "user", "content": user_message})

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=self.soul,
            messages=self.history,
        )

        reply = response.content[0].text
        self.history.append({"role": "assistant", "content": reply})
        return reply

    def execute_task(self, instruction: str) -> TaskResult:
        """
        执行一个由总规划师分配的子任务，返回结构化结果。
        """
        reply = self.chat(instruction)
        return TaskResult(
            agent_id=self.agent_id,
            domain=self.agent_id,
            content=reply,
        )

    def reset(self):
        """清空会话历史"""
        self.history.clear()
