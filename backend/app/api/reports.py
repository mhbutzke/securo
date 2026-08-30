import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
from app.schemas.financial_review import FinancialReviewQueueResponse, ReviewQueueName
from app.schemas.report import ReportResponse
from app.services import report_service
from app.services import financial_close_service
from app.services import financial_review_service

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/financial-close")
async def financial_close(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        return await financial_close_service.build_snapshot(session, ctx.workspace.id, period)
    except ValueError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/financial-review-queue", response_model=FinancialReviewQueueResponse)
async def financial_review_queue(
    from_date: date = Query(...),
    to_date: date = Query(...),
    queue: ReviewQueueName = Query("all"),
    limit: int = Query(20, ge=1, le=20),
    offset: int = Query(0, ge=0, le=10_000),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        return await financial_review_service.build_review_queue(
            session,
            ctx.workspace.id,
            from_date,
            to_date,
            queue=queue,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/net-worth", response_model=ReportResponse)
async def get_net_worth(
    months: int = Query(12, ge=1, le=24),
    interval: str = Query("monthly", pattern="^(daily|weekly|monthly|yearly)$"),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    asset_group_ids: Optional[list[uuid.UUID]] = Query(None),
    position_ids: Optional[list[uuid.UUID]] = Query(None),
    period: str | None = Query(None, pattern="^ytd$"),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await report_service.get_net_worth_report(
        session, ctx.workspace.id, ctx.user_id, months, interval, ctx.user.primary_currency,
        account_ids=account_ids, asset_group_ids=asset_group_ids, period=period,
        position_ids=position_ids,
    )


@router.get("/income-expenses", response_model=ReportResponse)
async def get_income_expenses(
    months: int = Query(12, ge=1, le=24),
    interval: str = Query("monthly", pattern="^(daily|weekly|monthly|yearly)$"),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    period: str | None = Query(None, pattern="^ytd$"),
    days: Optional[int] = Query(None, ge=1, le=730),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """`days` overrides `months` with an exact rolling window ending today."""
    return await report_service.get_income_expenses_report(
        session, ctx.workspace.id, ctx.user_id, months, interval, ctx.user.primary_currency,
        account_ids=account_ids, period=period, days=days,
    )


@router.get("/cash-flow", response_model=ReportResponse)
async def get_cash_flow(
    months: int = Query(6, ge=1, le=12),
    interval: str = Query("daily", pattern="^(daily|weekly|monthly)$"),
    baseline: bool = Query(False),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await report_service.get_cash_flow_report(
        session, ctx.workspace.id, ctx.user_id, months, interval, ctx.user.primary_currency,
        baseline=baseline, account_ids=account_ids,
    )
