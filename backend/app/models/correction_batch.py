import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class CorrectionBatch(Base):
    """Immutable audit envelope for a reversible correction operation."""

    __tablename__ = "correction_batches"
    __table_args__ = (
        UniqueConstraint("workspace_id", "operation", "digest", name="uq_correction_batch_digest"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    operation: Mapped[str] = mapped_column(String(50), default="rule_category_apply")
    digest: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(20), default="committed")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    undone_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    items: Mapped[list["CorrectionBatchItem"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class CorrectionBatchItem(Base):
    __tablename__ = "correction_batch_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("correction_batches.id", ondelete="CASCADE"), index=True
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="RESTRICT"), index=True
    )
    before_state: Mapped[dict] = mapped_column(JSON)
    after_state: Mapped[dict] = mapped_column(JSON)
    batch: Mapped[CorrectionBatch] = relationship(back_populates="items")
