"""Shared conservative cutoff policy for period-based financial views."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank_connection import BankConnection
from app.models.user import User
from app.models.workspace import Workspace


CutoffSource = Literal["period_end", "today", "last_sync", "no_sync"]


@dataclass(frozen=True)
class WorkspaceCutoff:
    cutoff_date: date
    source: CutoffSource
    latest_sync_at: datetime | None
    sync_is_stale: bool


async def _workspace_timezone(session: AsyncSession, workspace_id: uuid.UUID) -> ZoneInfo:
    """Use the workspace creator's display timezone for calendar cutoffs."""

    row = (
        await session.execute(
            select(Workspace.locale, Workspace.tax_jurisdiction, User.preferences)
            .outerjoin(User, Workspace.created_by_user_id == User.id)
            .where(Workspace.id == workspace_id)
        )
    ).one_or_none()
    locale, tax_jurisdiction, preferences = row if row is not None else (None, None, None)
    timezone_name = (preferences or {}).get("timezone")
    if not timezone_name and (locale == "pt-BR" or tax_jurisdiction == "BR"):
        timezone_name = "America/Sao_Paulo"
    try:
        return ZoneInfo(timezone_name or "UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _local_sync_date(value: datetime, local_timezone: ZoneInfo) -> date:
    """Convert provider timestamps safely, including naive legacy values."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(local_timezone).date()


async def resolve_workspace_cutoff(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    requested_to_date: date,
) -> WorkspaceCutoff:
    """Return a cutoff that never claims data newer than the stalest link."""

    local_timezone = await _workspace_timezone(session, workspace_id)
    reference_today = datetime.now(local_timezone).date()
    rows = await session.execute(
        select(BankConnection.status, BankConnection.last_sync_at).where(
            BankConnection.workspace_id == workspace_id,
        )
    )
    connections = rows.all()
    gated = [row for row in connections if row.status not in {"removed", "deleted"}]
    sync_dates = [
        _local_sync_date(row.last_sync_at, local_timezone)
        for row in gated
        if row.last_sync_at is not None
    ]
    latest_sync_at = max(
        (row.last_sync_at for row in gated if row.last_sync_at is not None),
        default=None,
    )
    if not gated:
        cutoff = min(requested_to_date, reference_today)
        source: CutoffSource = "today" if requested_to_date > reference_today else "period_end"
        return WorkspaceCutoff(cutoff, source, latest_sync_at, False)

    sync_cutoff = min(sync_dates) if sync_dates else reference_today
    cutoff = min(requested_to_date, sync_cutoff)
    missing_sync = len(sync_dates) < len(gated)
    sync_is_stale = missing_sync or sync_cutoff < requested_to_date
    if not sync_dates:
        source = "no_sync"
    elif sync_is_stale:
        source = "last_sync"
    else:
        source = "period_end"
    return WorkspaceCutoff(cutoff, source, latest_sync_at, sync_is_stale)
