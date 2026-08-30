import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PositionCreate(BaseModel):
    side: Literal["receivable", "liability"]
    name: str = Field(min_length=1, max_length=255)
    counterparty: Optional[str] = Field(default=None, max_length=255)
    currency: str = Field(default="BRL", min_length=3, max_length=3)
    original_principal: Decimal = Field(gt=0)
    start_date: date
    due_date: Optional[date] = None
    interest_rate: Optional[Decimal] = None
    liquidity: str = "illiquid"
    status: str = "open"
    valuation_date: Optional[date] = None
    valuation_source: Optional[str] = None
    valuation_confidence: Optional[str] = None
    group_id: Optional[uuid.UUID] = None


class PositionUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    counterparty: Optional[str] = Field(default=None, max_length=255)
    due_date: Optional[date] = None
    interest_rate: Optional[Decimal] = None
    liquidity: Optional[str] = None
    status: Optional[str] = None
    valuation_date: Optional[date] = None
    valuation_source: Optional[str] = None
    valuation_confidence: Optional[str] = None
    group_id: Optional[uuid.UUID] = None
    is_archived: Optional[bool] = None


class PositionMovementCreate(BaseModel):
    kind: Literal["opening", "increase", "decrease", "writeoff"]
    principal_amount: Decimal = Field(gt=0)
    cash_amount: Optional[Decimal] = None
    interest_amount: Decimal = Decimal("0")
    fee_amount: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    fx_rate: Optional[Decimal] = None
    effective_date: date
    idempotency_key: str = Field(min_length=1, max_length=128)
    transaction_id: Optional[uuid.UUID] = None


class PositionMovementRead(PositionMovementCreate):
    id: uuid.UUID
    position_id: uuid.UUID
    reversed_at: Optional[datetime] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class PositionRead(PositionCreate):
    id: uuid.UUID
    user_id: uuid.UUID
    workspace_id: uuid.UUID
    is_archived: bool
    created_at: datetime
    balance: Decimal = Decimal("0")
    movements: list[PositionMovementRead] = []
    model_config = ConfigDict(from_attributes=True)


class PositionTransactionLinkCreate(BaseModel):
    transaction_id: uuid.UUID
    kind: Literal["opening", "increase", "decrease", "writeoff"]
    principal_amount: Decimal = Field(gt=0)
    effective_date: date
    cash_amount: Optional[Decimal] = None
    idempotency_key: str = Field(min_length=1, max_length=128)
    interest_amount: Decimal = Decimal("0")
    fee_amount: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    fx_rate: Optional[Decimal] = None
