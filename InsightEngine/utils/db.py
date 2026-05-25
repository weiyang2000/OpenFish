"""
通用数据库工具（异步）

此模块提供基于 SQLAlchemy 2.x 异步引擎的数据库访问封装，支持 MySQL 与 PostgreSQL。
数据模型定义位置：
- 无（本模块仅提供连接与查询工具，不定义数据模型）
"""

from __future__ import annotations
import asyncio
from typing import Any, Dict, Iterable, List, Optional, Union

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy import text
from InsightEngine.utils.config import settings
from utils.runtime_database import load_runtime_database_config

__all__ = [
    "get_async_engine",
    "fetch_all",
]


_engine: Optional[AsyncEngine] = None
_engine_url: Optional[str] = None


def _build_database_url() -> str:
    return load_runtime_database_config(settings).async_sqlalchemy_url()


def get_async_engine() -> AsyncEngine:
    global _engine, _engine_url
    database_url: str = _build_database_url()
    if _engine is None or _engine_url != database_url:
        _engine = create_async_engine(
            database_url,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
        _engine_url = database_url
    return _engine


async def fetch_all(query: str, params: Optional[Union[Iterable[Any], Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """
    执行只读查询并返回字典列表。
    """
    engine: AsyncEngine = get_async_engine()
    async with engine.connect() as conn:
        result = await conn.execute(text(query), params or {})
        rows = result.mappings().all()
        # 将 RowMapping 转换为普通字典
        return [dict(row) for row in rows]

