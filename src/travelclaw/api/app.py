"""
FastAPI应用工厂。
Lifespan中初始化TravelTeam + 数据库。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from ..config import get_settings
from ..logging_config import setup_logging
from ..middleware import RequestTimingMiddleware
from ..storage.database import init_db
from ..team import TravelTeam
from .routes import router
from .ws import ws_router


def create_app(config_path: str | None = None) -> FastAPI:
    """应用工厂 — 创建并配置FastAPI实例。"""
    settings = get_settings()
    resolved_config = config_path or settings.config_path

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging(level=settings.log_level, json_format=(settings.log_format == "json"))
        app.state.team = TravelTeam(Path(resolved_config))
        app.state.settings = settings
        await init_db(settings.database_url)
        yield

    app = FastAPI(
        title="TravelClaw",
        description="AI旅行规划团队 API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(RequestTimingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins.split(","),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router, prefix="/api/v1")
    app.include_router(ws_router, prefix="/api/v1")

    # Serve frontend UI at root
    _index_path = Path(__file__).resolve().parent.parent / "static" / "index.html"
    _index_html = _index_path.read_text(encoding="utf-8") if _index_path.exists() else ""

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index():
        return _index_html

    return app
