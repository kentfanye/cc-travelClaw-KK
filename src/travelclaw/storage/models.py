"""
数据库模型 — SQLModel (SQLAlchemy + Pydantic)。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel


def _uuid() -> str:
    return uuid.uuid4().hex[:12]


class Trip(SQLModel, table=True):
    """旅行行程记录"""

    __tablename__ = "trips"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(default="anonymous", index=True)
    request_text: str = ""
    destination: str = ""
    days: int = 0
    budget: str = ""
    travelers: str = ""
    final_plan: str = ""
    status: str = Field(default="pending")  # pending / planning / completed / failed
    duration_ms: int = 0
    agents_used: str = ""  # JSON array string
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class AgentResultRecord(SQLModel, table=True):
    """Agent执行结果记录"""

    __tablename__ = "agent_results"

    id: str = Field(default_factory=_uuid, primary_key=True)
    trip_id: str = Field(index=True)
    agent_id: str = ""
    domain: str = ""
    content: str = ""
    success: bool = True
    duration_ms: int = 0
    created_at: datetime = Field(default_factory=datetime.now)


class ConversationMessage(SQLModel, table=True):
    """Agent会话消息记录"""

    __tablename__ = "conversation_messages"

    id: str = Field(default_factory=_uuid, primary_key=True)
    trip_id: str = Field(default="", index=True)
    agent_id: str = Field(default="", index=True)
    role: str = ""  # user / assistant
    content: str = ""
    created_at: datetime = Field(default_factory=datetime.now)


class UserPreference(SQLModel, table=True):
    """用户偏好"""

    __tablename__ = "user_preferences"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(index=True)
    key: str = ""
    value: str = ""
    updated_at: datetime = Field(default_factory=datetime.now)
