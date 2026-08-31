"""Official IPCA (IBGE/BCB SGS series 433) cache."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.macro_index import IpcaObservation


SOURCE = "BCB SGS 433"
URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.433/dados"


async def refresh_ipca(
    session: AsyncSession,
    start: date,
    end: date,
    *,
    client: httpx.AsyncClient | None = None,
) -> int:
    """Fetch and upsert official monthly rates; returns rows written.

    A caller may provide a client in tests. No financial data is sent to the
    provider, only the requested date range.
    """
    params = {"formato": "json", "dataInicial": start.strftime("%d/%m/%Y"), "dataFinal": end.strftime("%d/%m/%Y")}
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=20.0)
    try:
        response = await client.get(URL, params=params)
        response.raise_for_status()
        payload = response.json()
    finally:
        if owns_client:
            await client.aclose()

    written = 0
    now = datetime.now(timezone.utc)
    for row in payload if isinstance(payload, list) else []:
        raw_date = row.get("data")
        raw_value = row.get("valor")
        if not raw_date or raw_value in (None, ""):
            continue
        try:
            observed = datetime.strptime(raw_date, "%d/%m/%Y").date()
            period = observed.replace(day=1)
            rate = Decimal(str(raw_value).replace(",", ".")) / Decimal("100")
        except (ValueError, ArithmeticError):
            continue
        existing = await session.scalar(
            select(IpcaObservation).where(
                IpcaObservation.period == period,
                IpcaObservation.source == SOURCE,
            )
        )
        if existing is None:
            session.add(IpcaObservation(period=period, rate=rate, source=SOURCE, retrieved_at=now))
            written += 1
        else:
            existing.rate = rate
            existing.retrieved_at = now
    await session.commit()
    return written


async def cumulative_ipca(session: AsyncSession, start: date, end: date) -> tuple[Decimal | None, str | None]:
    rows = (
        await session.execute(
            select(IpcaObservation).where(
                IpcaObservation.period >= start.replace(day=1),
                IpcaObservation.period <= end.replace(day=1),
                IpcaObservation.source == SOURCE,
            ).order_by(IpcaObservation.period)
        )
    ).scalars().all()
    if not rows:
        return None, None
    expected_months = (end.year - start.year) * 12 + end.month - start.month + 1
    if len(rows) < expected_months:
        return None, SOURCE
    value = Decimal("1")
    for row in rows:
        value *= Decimal("1") + row.rate
    return value - Decimal("1"), SOURCE
