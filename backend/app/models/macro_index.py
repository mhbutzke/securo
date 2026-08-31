"""Cached official macroeconomic observations used by financial reports."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Date, DateTime, Index, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IpcaObservation(Base):
    __tablename__ = "ipca_observations"
    __table_args__ = (
        UniqueConstraint("period", "source", name="uq_ipca_observations_period_source"),
        Index("ix_ipca_observations_period", "period"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # First day of the reference month.
    period: Mapped[date] = mapped_column(Date, nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(precision=12, scale=8), nullable=False)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
