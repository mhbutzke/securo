"""Scheduled refresh of the official Brazilian IPCA cache."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.services.ipca_service import refresh_ipca
from app.worker import celery_app

logger = logging.getLogger(__name__)


def _month_offset(year: int, month: int, offset: int) -> date:
    index = year * 12 + (month - 1) + offset
    return date(index // 12, index % 12 + 1, 1)


async def _refresh() -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    today = date.today()
    start = _month_offset(today.year, today.month, -36)
    try:
        async with session_maker() as session:
            return await refresh_ipca(session, start, today)
    finally:
        await engine.dispose()


@celery_app.task(name="app.tasks.ipca_tasks.refresh_ipca_cache")
def refresh_ipca_cache() -> dict:
    """Refresh the last 36 months; disabled in isolated staging."""
    if os.getenv("IPCA_SYNC_ENABLED", "true").lower() not in {"1", "true", "yes", "on"}:
        logger.debug("IPCA refresh skipped (IPCA_SYNC_ENABLED is false)")
        return {"skipped": True, "reason": "IPCA sync disabled"}
    count = asyncio.run(_refresh())
    logger.info("IPCA refresh complete: %d observations written", count)
    return {"synced": count}
