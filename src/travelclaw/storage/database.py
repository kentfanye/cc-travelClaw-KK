"""
数据库连接管理。
开发用SQLite，生产用PostgreSQL，通过DATABASE_URL切换。
"""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///travelclaw.db"


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


_engine = None


def get_engine(database_url: str | None = None):
    global _engine
    if _engine is None:
        url = database_url or get_database_url()
        connect_args = {}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        _engine = create_async_engine(url, echo=False, connect_args=connect_args)
    return _engine


def reset_engine():
    """重置engine（用于测试）"""
    global _engine
    _engine = None


async def init_db(database_url: str | None = None):
    """创建所有表"""
    engine = get_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def get_session() -> AsyncSession:
    """获取数据库会话"""
    engine = get_engine()
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
