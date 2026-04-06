"""
FastAPI应用工厂。
Lifespan中初始化TravelTeam，存入app.state。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..logging_config import setup_logging
from ..team import TravelTeam
from .routes import router
from .ws import ws_router


def create_app(config_path: str = "config.yaml") -> FastAPI:
    """应用工厂 — 创建并配置FastAPI实例。"""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging(level="INFO")
        app.state.team = TravelTeam(Path(config_path))
        yield

    app = FastAPI(
        title="TravelClaw",
        description="AI旅行规划团队 API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router, prefix="/api/v1")
    app.include_router(ws_router, prefix="/api/v1")

    return app
