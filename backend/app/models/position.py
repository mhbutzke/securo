import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.asset_group import AssetGroup
    from app.models.transaction import Transaction


class Position(Base):
    """A receivable or liability tracked independently from positive assets."""

    __tablename__ = "positions"
    __table_args__ = (Index("ix_positions_workspace_active", "workspace_id", "is_archived"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    side: Mapped[str] = mapped_column(String(12))  # receivable | liability
    name: Mapped[str] = mapped_column(String(255))
    counterparty: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="BRL")
    original_principal: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=2))
    start_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    interest_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=12, scale=6), nullable=True)
    liquidity: Mapped[str] = mapped_column(String(20), default="illiquid")
    status: Mapped[str] = mapped_column(String(20), default="open")
    valuation_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    valuation_source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    valuation_confidence: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    group_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("asset_groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_archived: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    movements: Mapped[list["PositionMovement"]] = relationship(
        back_populates="position", cascade="all, delete-orphan", order_by="PositionMovement.effective_date"
    )


class PositionMovement(Base):
    __tablename__ = "position_movements"
    __table_args__ = (
        Index("ux_position_movements_idempotency", "position_id", "idempotency_key", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    position_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("positions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(12))  # opening | increase | decrease | writeoff
    transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    principal_amount: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=2))
    cash_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=18, scale=2), nullable=True)
    interest_amount: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=2), default=Decimal("0"))
    fee_amount: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=2), default=Decimal("0"))
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=2), default=Decimal("0"))
    fx_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=20, scale=10), nullable=True)
    effective_date: Mapped[date] = mapped_column(Date)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    reversed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    position: Mapped[Position] = relationship(back_populates="movements")

