"""The files an invoice gathers, and which one of them *is* the invoice.

An invoice is not always a document. It is often a folder: the supplier's
PDF arrives by email, the fiscal document arrives days later from a
government portal, a payment receipt arrives after that, and the contract
that started it predates all three. The debt is one thing; the paper that
proves it accumulates.

Two consequences shape this table:

  - **`kind` is a role, not a file type.** What matters is what a file
    proves, not that it is a PDF. The roles are the ones every
    jurisdiction has — the bill, the fiscal document, the receipt, the
    agreement — and each pack names them in its own vocabulary. Nota
    fiscal, facture, Rechnung and invoice all land on `fiscal` here.

  - **`is_primary` marks the document itself.** When we wrote the invoice,
    our render is the document and nothing here outranks it. When we
    received it, the file the supplier sent *is* the document, and
    redrawing it from our own fields produces a page that looks official
    and is not. The primary attachment is what `/pdf` serves and what the
    screen shows, precisely so the product stops reconstructing something
    it was handed.
"""
import uuid
from datetime import date as _date, datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.invoice import Invoice


#: What a file proves. Roles, deliberately — a jurisdiction pack supplies
#: the words a user sees, and no value here names a country's paperwork.
#:
#:   bill     — the demand for payment, as issued or as received
#:   fiscal   — the tax document (nota fiscal, facture, Rechnung, invoice)
#:   receipt  — proof the money moved
#:   contract — the agreement or order the work came from
#:   other    — anything else worth keeping next to the debt
ATTACHMENT_KINDS = ("bill", "fiscal", "receipt", "contract", "other")


class InvoiceAttachment(Base):
    """One file gathered under an invoice."""

    __tablename__ = "invoice_attachments"
    __table_args__ = (
        Index("ix_invoice_attachments_invoice", "invoice_id"),
        # At most one file can be *the* document: a partial unique index
        # is the only way to say "unique among the true ones". Declared
        # for both dialects on purpose — `postgresql_where` alone is
        # silently dropped elsewhere, and the index then degrades into
        # "one attachment per invoice", the opposite of a table whose
        # whole point is gathering several.
        Index(
            "uq_invoice_attachments_primary",
            "invoice_id",
            unique=True,
            postgresql_where=text("is_primary"),
            sqlite_where=text("is_primary"),
        ),
        CheckConstraint(
            "kind IN ('bill', 'fiscal', 'receipt', 'contract', 'other')",
            name="ck_invoice_attachments_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    # Who uploaded it. Not an owner: the file belongs to the workspace.
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    kind: Mapped[str] = mapped_column(String(20), default="other", server_default="other")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # The number the document carries on its face — an NF-e key, a
    # facture number, a receipt reference. Text, because it is a name
    # given elsewhere and nothing here may assume it counts.
    document_number: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    # The date on the document, which is rarely the date it was uploaded.
    issued_at: Mapped[Optional[_date]] = mapped_column(Date, nullable=True)

    filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(BigInteger)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    invoice: Mapped["Invoice"] = relationship(back_populates="attachments")
