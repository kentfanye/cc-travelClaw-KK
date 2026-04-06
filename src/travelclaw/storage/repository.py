"""
数据访问层 — Trip和UserPreference的CRUD操作。
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AgentResultRecord, Trip, UserPreference
from ..models import PlanResponse


class TripRepository:
    """行程数据操作"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_trip(self, request_text: str, user_id: str = "anonymous") -> Trip:
        trip = Trip(request_text=request_text, user_id=user_id, status="planning")
        self.session.add(trip)
        await self.session.commit()
        await self.session.refresh(trip)
        return trip

    async def get_trip(self, trip_id: str) -> Trip | None:
        result = await self.session.execute(select(Trip).where(Trip.id == trip_id))
        return result.scalar_one_or_none()

    async def list_trips(self, user_id: str = "anonymous", limit: int = 20) -> list[Trip]:
        result = await self.session.execute(
            select(Trip)
            .where(Trip.user_id == user_id)
            .order_by(Trip.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def save_plan_response(
        self, plan_response: PlanResponse, user_id: str = "anonymous"
    ) -> Trip:
        """从PlanResponse保存完整的行程记录"""
        trip = Trip(
            id=plan_response.plan_id,
            user_id=user_id,
            request_text=plan_response.request.prompt,
            destination=plan_response.request.destination,
            days=plan_response.request.days,
            budget=plan_response.request.budget,
            travelers=plan_response.request.travelers,
            final_plan=plan_response.plan,
            status="completed",
            duration_ms=plan_response.duration_ms,
            agents_used=json.dumps(plan_response.agents_used),
        )
        self.session.add(trip)

        # 保存各Agent结果
        for result in plan_response.agent_results:
            record = AgentResultRecord(
                trip_id=plan_response.plan_id,
                agent_id=result.agent_id,
                domain=result.domain,
                content=result.content,
                success=result.success,
                duration_ms=result.duration_ms,
            )
            self.session.add(record)

        await self.session.commit()
        await self.session.refresh(trip)
        return trip

    async def update_trip_status(self, trip_id: str, status: str, final_plan: str = ""):
        trip = await self.get_trip(trip_id)
        if trip:
            trip.status = status
            if final_plan:
                trip.final_plan = final_plan
            trip.updated_at = datetime.now()
            await self.session.commit()


class PreferenceRepository:
    """用户偏好操作"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_preferences(self, user_id: str) -> dict[str, str]:
        result = await self.session.execute(
            select(UserPreference).where(UserPreference.user_id == user_id)
        )
        prefs = result.scalars().all()
        return {p.key: p.value for p in prefs}

    async def set_preference(self, user_id: str, key: str, value: str):
        result = await self.session.execute(
            select(UserPreference).where(
                UserPreference.user_id == user_id, UserPreference.key == key
            )
        )
        pref = result.scalar_one_or_none()
        if pref:
            pref.value = value
            pref.updated_at = datetime.now()
        else:
            pref = UserPreference(user_id=user_id, key=key, value=value)
            self.session.add(pref)
        await self.session.commit()

    async def delete_preference(self, user_id: str, key: str):
        result = await self.session.execute(
            select(UserPreference).where(
                UserPreference.user_id == user_id, UserPreference.key == key
            )
        )
        pref = result.scalar_one_or_none()
        if pref:
            await self.session.delete(pref)
            await self.session.commit()
