"""
Pydantic v2 数据模型 — 替代原有dataclass，统一校验层。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


# ── 用户输入 ──────────────────────────────────────────────


class TravelRequest(BaseModel):
    """用户的旅行规划请求"""

    destination: str = Field(..., min_length=1, description="目的地")
    days: int = Field(..., gt=0, le=30, description="天数")
    budget: str = Field(default="不限", description="预算描述")
    travelers: str = Field(default="1人", description="出行人描述")
    preferences: str = Field(default="", description="偏好和特殊要求")

    @property
    def prompt(self) -> str:
        """将请求转为自然语言prompt"""
        return (
            f"目的地：{self.destination}，{self.days}天，"
            f"预算：{self.budget}，出行人：{self.travelers}。"
            f"{self.preferences}"
        )


# ── Orchestrator调度协议 ──────────────────────────────────


class TaskAssignment(BaseModel):
    """单个子任务分配"""

    agent: str = Field(..., description="目标agent_id")
    instruction: str = Field(..., min_length=1, description="给该专家的具体指令")


class TaskDecomposition(BaseModel):
    """总规划师拆解出的子任务集"""

    destination: str = ""
    days: int = 0
    budget: str = ""
    travelers: str = ""
    tasks: list[TaskAssignment] = Field(default_factory=list)


# ── Agent执行结果 ─────────────────────────────────────────


class TaskResult(BaseModel):
    """专家Agent返回的结果"""

    agent_id: str
    domain: str
    content: str
    success: bool = True
    duration_ms: int = 0


# ── 流式进度事件 ──────────────────────────────────────────


class EventType(str, Enum):
    PLAN_START = "plan_start"
    DECOMPOSE_START = "decompose_start"
    DECOMPOSE_DONE = "decompose_done"
    AGENT_START = "agent_start"
    AGENT_DONE = "agent_done"
    AGENT_ERROR = "agent_error"
    INTEGRATE_START = "integrate_start"
    PLAN_COMPLETE = "plan_complete"
    PLAN_ERROR = "plan_error"


class PlanEvent(BaseModel):
    """规划流程中的进度事件"""

    type: EventType
    plan_id: str = ""
    agent_id: str = ""
    data: dict | str = ""
    timestamp: datetime = Field(default_factory=datetime.now)


# ── 最终输出 ──────────────────────────────────────────────


class PlanResponse(BaseModel):
    """完整的行程规划响应"""

    plan_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    request: TravelRequest
    plan: str = ""
    agent_results: list[TaskResult] = Field(default_factory=list)
    duration_ms: int = 0
    agents_used: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
