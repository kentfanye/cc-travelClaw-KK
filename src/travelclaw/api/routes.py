"""
REST API 路由。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..errors import TravelClawError
from ..models import PlanResponse

router = APIRouter()


class PlanRequest(BaseModel):
    """API请求体"""
    request: str = Field(..., min_length=1, description="旅行规划需求的自然语言描述")


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
    agents_count: int = 0


@router.get("/health", response_model=HealthResponse)
async def health(request: Request):
    """健康检查"""
    team = request.app.state.team
    return HealthResponse(agents_count=len(team.agents) + (1 if team.orchestrator else 0))


@router.get("/agents")
async def list_agents(request: Request):
    """列出团队所有成员"""
    team = request.app.state.team
    return {"agents": team.list_agents()}


@router.post("/plan", response_model=PlanResponse)
async def create_plan(body: PlanRequest, request: Request):
    """
    提交旅行规划需求 — 异步并行调度专家团队。
    返回完整的PlanResponse。
    """
    team = request.app.state.team
    try:
        response = await team.aplan(body.request)
        return response
    except TravelClawError as e:
        raise HTTPException(status_code=500, detail=str(e))
