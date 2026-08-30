"""Read-only prioritization of transactions that still need review.

This service intentionally does not suggest or apply categories. It gives the
UI a bounded, aggregate-first queue so a user can review ten to twenty facts
at a time without exposing raw provider payloads or mutating the ledger.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction
from app.schemas.financial_review import (
    FinancialReviewItem,
    FinancialReviewQueueResponse,
    FinancialReviewSummary,
    ReviewQueueName,
)
from app.services.period_cutoff import resolve_workspace_cutoff


_QUEUE_NAMES: tuple[Literal["pending", "uncategorized", "third_party_transfers", "high_value", "ignored", "rule_managed"], ...] = (
    "pending",
    "uncategorized",
    "third_party_transfers",
    "high_value",
    "ignored",
    "rule_managed",
)
_PRIORITY: tuple[str, ...] = (
    "high_value",
    "pending",
    "uncategorized",
    "third_party_transfers",
    "rule_managed",
    "ignored",
)
_HIGH_VALUE_THRESHOLD = Decimal("1000")
QueuePredicate = ColumnElement[bool]
QueueLabel = ColumnElement[str]


def _queue_expressions() -> dict[str, QueuePredicate]:
    # Thresholds are expressed in the workspace's primary currency. BRL is
    # the configured currency for the Brazilian workspace; foreign rows only
    # qualify when the provider/import has populated amount_primary.
    primary_amount = func.coalesce(
        Transaction.amount_primary,
        case((Transaction.currency == "BRL", Transaction.amount), else_=None),
    )
    high_value = func.abs(primary_amount) >= _HIGH_VALUE_THRESHOLD
    return {
        "pending": Transaction.status == "pending",
        "uncategorized": Transaction.category_id.is_(None),
        "third_party_transfers": (
            Category.treat_as_transfer.is_(True) & Transaction.transfer_pair_id.is_(None)
        ),
        "high_value": high_value,
        "ignored": or_(
            Transaction.is_ignored.is_(True),
            Category.is_ignored.is_(True),
        ),
        # All rule-owned rows are surfaced so broad rules can be reviewed as a
        # batch. The queue deliberately does not rewrite or disable a rule.
        "rule_managed": Transaction.category_origin == "rule",
    }


def _base_filters(
    workspace_id: uuid.UUID,
    from_date: date,
    cutoff: date,
) -> tuple[QueuePredicate, ...]:
    return (
        Transaction.workspace_id == workspace_id,
        Transaction.date >= from_date,
        Transaction.date <= cutoff,
        Transaction.source != "opening_balance",
    )


async def _build_summaries(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    filters: tuple[QueuePredicate, ...],
    expressions: dict[str, QueuePredicate],
) -> dict[str, FinancialReviewSummary]:
    summaries: dict[str, FinancialReviewSummary] = {}
    for name in _QUEUE_NAMES:
        result = await session.execute(
            select(
                func.count(Transaction.id),
                func.coalesce(func.sum(func.abs(Transaction.amount)), 0),
            )
            .select_from(Transaction)
            .join(
                Account,
                (Account.id == Transaction.account_id)
                & (Account.workspace_id == workspace_id),
            )
            .outerjoin(Category, Category.id == Transaction.category_id)
            .where(*filters, expressions[name])
        )
        count, total = result.one()
        summaries[name] = FinancialReviewSummary(
            count=int(count or 0),
            total_amount=Decimal(str(total or 0)),
        )
    return summaries


async def _select_queue(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    filters: tuple[QueuePredicate, ...],
    expressions: dict[str, QueuePredicate],
    summaries: dict[str, FinancialReviewSummary],
    queue: ReviewQueueName,
) -> tuple[QueuePredicate, QueueLabel, int, Decimal]:
    if queue == "all":
        selected_expression = or_(*(expressions[name] for name in _QUEUE_NAMES))
        # The queues overlap (for example, an ignored high-value transaction),
        # so the all-queue total must be a distinct transaction aggregate.
        total_result = await session.execute(
            select(
                func.count(Transaction.id),
                func.coalesce(func.sum(func.abs(Transaction.amount)), 0),
            )
            .select_from(Transaction)
            .join(
                Account,
                (Account.id == Transaction.account_id)
                & (Account.workspace_id == workspace_id),
            )
            .outerjoin(Category, Category.id == Transaction.category_id)
            .where(*filters, selected_expression)
        )
        distinct_count, distinct_total = total_result.one()
        item_reason = case(
            (expressions["high_value"], "high_value"),
            (expressions["pending"], "pending"),
            (expressions["uncategorized"], "uncategorized"),
            (expressions["third_party_transfers"], "third_party_transfers"),
            (expressions["rule_managed"], "rule_managed"),
            (expressions["ignored"], "ignored"),
            else_="uncategorized",
        )
        return (
            selected_expression,
            item_reason,
            int(distinct_count or 0),
            Decimal(str(distinct_total or 0)),
        )

    if queue not in expressions:
        raise ValueError(f"Unsupported review queue: {queue}")
    selected_expression = expressions[queue]
    summary = summaries[queue]
    item_reason = case((selected_expression, queue), else_=queue)
    return selected_expression, item_reason, summary.count, summary.total_amount


async def _build_items(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    filters: tuple[QueuePredicate, ...],
    selected_expression: QueuePredicate,
    item_reason: QueueLabel,
    limit: int,
    offset: int,
) -> list[FinancialReviewItem]:
    rows = await session.execute(
        select(
            Transaction.id,
            Transaction.date,
            Transaction.amount,
            Transaction.currency,
            Transaction.type,
            Transaction.description,
            Transaction.account_id,
            Transaction.category_id,
            Transaction.category_origin,
            item_reason.label("reason"),
        )
        .select_from(Transaction)
        .join(
            Account,
            (Account.id == Transaction.account_id)
            & (Account.workspace_id == workspace_id),
        )
        .outerjoin(Category, Category.id == Transaction.category_id)
        .where(*filters, selected_expression)
        .order_by(func.abs(Transaction.amount).desc(), Transaction.date, Transaction.id)
        .offset(offset)
        .limit(limit)
    )
    return [FinancialReviewItem(**dict(row._mapping)) for row in rows]


async def build_review_queue(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    from_date: date,
    to_date: date,
    queue: ReviewQueueName = "all",
    limit: int = 20,
    offset: int = 0,
) -> FinancialReviewQueueResponse:
    """Build a bounded review queue without changing any row."""

    if from_date > to_date:
        raise ValueError("from_date must be on or before to_date")
    cutoff_info = await resolve_workspace_cutoff(session, workspace_id, to_date)
    cutoff = cutoff_info.cutoff_date
    cutoff_source = cutoff_info.source
    latest_sync_at = cutoff_info.latest_sync_at
    sync_is_stale = cutoff_info.sync_is_stale
    expressions = _queue_expressions()
    filters = _base_filters(workspace_id, from_date, cutoff)
    summaries = await _build_summaries(session, workspace_id, filters, expressions)
    selected_expression, item_reason, total_count, total_amount = await _select_queue(
        session, workspace_id, filters, expressions, summaries, queue
    )
    items = await _build_items(
        session, workspace_id, filters, selected_expression, item_reason, limit, offset
    )
    return FinancialReviewQueueResponse(
        workspace_id=workspace_id,
        queue=queue,
        from_date=from_date,
        requested_to_date=to_date,
        cutoff_date=cutoff,
        cutoff_source=cutoff_source,
        latest_sync_at=latest_sync_at,
        sync_is_stale=sync_is_stale,
        limit=limit,
        offset=offset,
        total_count=total_count,
        total_amount=total_amount,
        summaries=summaries,
        coverage_notes={
            "pending": "All rows marked pending in the selected period.",
            "high_value": "Uses amount_primary when available; BRL native amounts otherwise.",
            "third_party_transfers": (
                "Candidate queue based on transfer categories without a pair; confirm the recipient manually."
            ),
            "rule_managed": "All rule-owned categories are surfaced so broad rules can be reviewed.",
            "uncategorized": "Rows without a category, including pending and ignored rows when applicable.",
            "ignored": "Rows explicitly ignored by the transaction or category flag.",
        },
        items=items,
    )
