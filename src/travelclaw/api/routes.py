"""
REST API 路由。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import TravelClawError
from ..models import PlanResponse
from ..storage.database import get_engine
from ..storage.repository import PreferenceRepository, TripRepository

logger = logging.getLogger("travelclaw.api")

router = APIRouter()


class PlanRequest(BaseModel):
    """API请求体"""

    request: str = Field(..., min_length=1, description="旅行规划需求的自然语言描述")
    user_id: str = Field(default="anonymous", description="用户ID")


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
    agents_count: int = 0
    database: str = "unknown"


class PreferenceBody(BaseModel):
    preferences: dict[str, str]


async def get_db_session():
    """获取异步数据库会话"""
    from sqlalchemy.orm import sessionmaker

    engine = get_engine()
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session


# ── Health & Probes ───────────────────────────────────��───


@router.get("/health", response_model=HealthResponse)
async def health(request: Request):
    """综合健康检查"""
    team = request.app.state.team
    # ���查DB连通性
    db_status = "unknown"
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception:
        db_status = "disconnected"
    return HealthResponse(
        agents_count=len(team.agents) + (1 if team.orchestrator else 0),
        database=db_status,
    )


@router.get("/health/live")
async def liveness():
    """存活探针 (Kubernetes liveness probe)"""
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness(request: Request):
    """就绪探针 (Kubernetes readiness probe)"""
    team = getattr(request.app.state, "team", None)
    if not team or not team.orchestrator:
        raise HTTPException(status_code=503, detail="Team not initialized")
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="Database not ready")
    return {"status": "ready"}


@router.get("/agents")
async def list_agents(request: Request):
    team = request.app.state.team
    return {"agents": team.list_agents()}


# ── Plan CRUD ─────────────────────────────────────────────


@router.post("/plan", response_model=PlanResponse)
async def create_plan(
    body: PlanRequest, request: Request, session: AsyncSession = Depends(get_db_session)
):
    """提交旅行规划需求，异步并行调度，自动持久化结果。"""
    team = request.app.state.team
    try:
        response = await team.aplan(body.request)
        # 持久化
        repo = TripRepository(session)
        await repo.save_plan_response(response, user_id=body.user_id)
        logger.info("行程已保存: plan_id=%s", response.plan_id)
        return response
    except TravelClawError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/plans")
async def list_plans(user_id: str = "anonymous", session: AsyncSession = Depends(get_db_session)):
    """查询用户的行程历史。"""
    repo = TripRepository(session)
    trips = await repo.list_trips(user_id=user_id)
    return {
        "trips": [
            {
                "id": t.id,
                "destination": t.destination,
                "days": t.days,
                "status": t.status,
                "duration_ms": t.duration_ms,
                "created_at": t.created_at.isoformat(),
            }
            for t in trips
        ]
    }


@router.get("/plan/{plan_id}")
async def get_plan(plan_id: str, session: AsyncSession = Depends(get_db_session)):
    """查询单个行程详情。"""
    repo = TripRepository(session)
    trip = await repo.get_trip(plan_id)
    if not trip:
        raise HTTPException(status_code=404, detail="行程不存在")
    return {
        "id": trip.id,
        "destination": trip.destination,
        "days": trip.days,
        "budget": trip.budget,
        "travelers": trip.travelers,
        "plan": trip.final_plan,
        "status": trip.status,
        "duration_ms": trip.duration_ms,
        "agents_used": trip.agents_used,
        "created_at": trip.created_at.isoformat(),
    }


# ── User Preferences ─────────────────────────────────────


@router.get("/user/{user_id}/preferences")
async def get_preferences(user_id: str, session: AsyncSession = Depends(get_db_session)):
    repo = PreferenceRepository(session)
    prefs = await repo.get_preferences(user_id)
    return {"user_id": user_id, "preferences": prefs}


@router.put("/user/{user_id}/preferences")
async def set_preferences(
    user_id: str, body: PreferenceBody, session: AsyncSession = Depends(get_db_session)
):
    repo = PreferenceRepository(session)
    for key, value in body.preferences.items():
        await repo.set_preference(user_id, key, value)
    return {"user_id": user_id, "preferences": body.preferences}
