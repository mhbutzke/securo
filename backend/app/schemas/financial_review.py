"""Schemas for the read-only financial review queue."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ReviewQueueName = Literal[
    "all",
    "pending",
    "uncategorized",
    "third_party_transfers",
    "high_value",
    "ignored",
    "rule_managed",
]


class FinancialReviewSummary(BaseModel):
    count: int = Field(ge=0)
    total_amount: Decimal


class FinancialReviewItem(BaseModel):
    id: uuid.UUID
    date: date
    amount: Decimal
    currency: str
    type: str
    description: str
    account_id: uuid.UUID
    category_id: uuid.UUID | None = None
    category_origin: str | None = None
    reason: Literal[
        "pending",
        "uncategorized",
        "third_party_transfers",
        "high_value",
        "ignored",
        "rule_managed",
    ]

    model_config = ConfigDict(from_attributes=True)


class FinancialReviewQueueResponse(BaseModel):
    workspace_id: uuid.UUID
    queue: ReviewQueueName
    from_date: date
    requested_to_date: date
    cutoff_date: date
    cutoff_source: Literal["period_end", "today", "last_sync", "no_sync"]
    latest_sync_at: datetime | None = None
    sync_is_stale: bool
    limit: int
    offset: int
    total_count: int = Field(ge=0)
    total_amount: Decimal
    summaries: dict[str, FinancialReviewSummary]
    coverage_notes: dict[str, str]
    items: list[FinancialReviewItem]
