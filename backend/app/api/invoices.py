"""Invoicing ledger routes.

Every route is gated twice: `require_module(INVOICES)` asks whether this
workspace has the module at all, and the write variant additionally asks
whether the member's role may write. A personal workspace gets a 404
from all of them — the same answer it would get for a URL that does not
exist, which is the honest thing to say to a client that should not know
the feature is there.
"""
import uuid
from datetime import date as _date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.module_gate import require_module, require_module_write
from app.core.workspace_context import WorkspaceContext
from app.schemas.invoice import (
    AllocationCreate,
    InvoiceCreate,
    InvoiceRead,
    InvoiceSettingsRead,
    InvoiceSettingsUpdate,
    InvoiceSummary,
    InvoiceUpdate,
)
from app.services import invoice_service
from app.services.invoice_service import InvoiceError
from app.services.module_service import ModuleId

router = APIRouter(prefix="/api/invoices", tags=["invoices"])

read_ctx = require_module(ModuleId.INVOICES)
write_ctx = require_module_write(ModuleId.INVOICES)


def _serialize(invoice, today: Optional[_date] = None) -> InvoiceRead:
    """Attach the derived answers to the stored row.

    Computed here, once, from the single definition in the service — so
    the API can expose `state`, `balance` and `days_overdue` without any
    of them ever being written to a column that could drift.
    """
    payload = InvoiceRead.model_validate(invoice, from_attributes=True)
    payload.state = invoice_service.derive_state(invoice, today)
    payload.amount_paid = invoice_service.allocated_total(invoice)
    payload.balance = invoice_service.balance(invoice)
    payload.days_overdue = invoice_service.days_overdue(invoice, today)
    return payload


async def _load(session: AsyncSession, invoice_id: uuid.UUID, workspace_id: uuid.UUID):
    invoice = await invoice_service.get_invoice(session, invoice_id, workspace_id)
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    return invoice


def _http(error: InvoiceError) -> HTTPException:
    """Map a ledger rule to a response the frontend can translate."""
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    )


# ---------------------------------------------------------------------------
# Settings — before /{invoice_id} so the literal path wins the match
# ---------------------------------------------------------------------------
@router.get("/settings", response_model=InvoiceSettingsRead)
async def read_settings(
    ctx: WorkspaceContext = Depends(read_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    return await invoice_service.get_settings(session, ctx.workspace.id)


@router.patch("/settings", response_model=InvoiceSettingsRead)
async def write_settings(
    payload: InvoiceSettingsUpdate,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    settings = await invoice_service.update_settings(
        session, ctx.workspace.id, payload.model_dump(exclude_unset=True)
    )
    await session.commit()
    return settings


@router.get("/summary", response_model=InvoiceSummary)
async def read_summary(
    ctx: WorkspaceContext = Depends(read_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    data = await invoice_service.aging_summary(session, ctx.workspace.id)
    data["upcoming"] = [_serialize(inv) for inv in data["upcoming"]]
    return data


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------
@router.get("", response_model=list[InvoiceRead])
async def list_invoices(
    state: Optional[str] = Query(None, description="Filter by derived state"),
    payee_id: Optional[uuid.UUID] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    ctx: WorkspaceContext = Depends(read_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    invoices = await invoice_service.list_invoices(
        session, ctx.workspace.id, state=state, payee_id=payee_id, q=q, limit=limit, offset=offset
    )
    return [_serialize(inv) for inv in invoices]


@router.post("", response_model=InvoiceRead, status_code=status.HTTP_201_CREATED)
async def create_invoice(
    payload: InvoiceCreate,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    data = payload.model_dump(exclude_unset=True)
    if data.get("lines") is not None:
        data["lines"] = [dict(line) for line in data["lines"]]
    try:
        invoice = await invoice_service.create_invoice(
            session, ctx.workspace.id, ctx.user_id, data
        )
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    invoice = await _load(session, invoice.id, ctx.workspace.id)
    return _serialize(invoice)


@router.get("/{invoice_id}", response_model=InvoiceRead)
async def get_invoice(
    invoice_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(read_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    return _serialize(await _load(session, invoice_id, ctx.workspace.id))


@router.patch("/{invoice_id}", response_model=InvoiceRead)
async def update_invoice(
    invoice_id: uuid.UUID,
    payload: InvoiceUpdate,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    invoice = await _load(session, invoice_id, ctx.workspace.id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("lines") is not None:
        data["lines"] = [dict(line) for line in data["lines"]]
    try:
        invoice = await invoice_service.update_invoice(session, invoice, data)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return _serialize(await _load(session, invoice_id, ctx.workspace.id))


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invoice(
    invoice_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    invoice = await _load(session, invoice_id, ctx.workspace.id)
    try:
        await invoice_service.delete_invoice(session, invoice)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()


# ---------------------------------------------------------------------------
# The decisions. Each is its own route, because a status that changed
# always has a reason, and a PATCH of a string never records one.
# ---------------------------------------------------------------------------
@router.post("/{invoice_id}/issue", response_model=InvoiceRead)
async def issue_invoice(
    invoice_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    invoice = await _load(session, invoice_id, ctx.workspace.id)
    try:
        await invoice_service.issue_invoice(session, invoice)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return _serialize(await _load(session, invoice_id, ctx.workspace.id))


@router.post("/{invoice_id}/void", response_model=InvoiceRead)
async def void_invoice(
    invoice_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    invoice = await _load(session, invoice_id, ctx.workspace.id)
    try:
        await invoice_service.void_invoice(session, invoice)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return _serialize(await _load(session, invoice_id, ctx.workspace.id))


@router.post("/{invoice_id}/uncollectible", response_model=InvoiceRead)
async def write_off_invoice(
    invoice_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    invoice = await _load(session, invoice_id, ctx.workspace.id)
    try:
        await invoice_service.mark_uncollectible(session, invoice)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return _serialize(await _load(session, invoice_id, ctx.workspace.id))


@router.post("/{invoice_id}/reopen", response_model=InvoiceRead)
async def reopen_invoice(
    invoice_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    invoice = await _load(session, invoice_id, ctx.workspace.id)
    try:
        await invoice_service.reopen_invoice(session, invoice)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return _serialize(await _load(session, invoice_id, ctx.workspace.id))


# ---------------------------------------------------------------------------
# Allocations — money bound to debt
# ---------------------------------------------------------------------------
@router.post("/{invoice_id}/allocations", response_model=InvoiceRead, status_code=status.HTTP_201_CREATED)
async def create_allocation(
    invoice_id: uuid.UUID,
    payload: AllocationCreate,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    invoice = await _load(session, invoice_id, ctx.workspace.id)
    try:
        await invoice_service.allocate(session, invoice, payload.transaction_id, payload.amount)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return _serialize(await _load(session, invoice_id, ctx.workspace.id))


@router.delete("/{invoice_id}/allocations/{allocation_id}", response_model=InvoiceRead)
async def remove_allocation(
    invoice_id: uuid.UUID,
    allocation_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    invoice = await _load(session, invoice_id, ctx.workspace.id)
    try:
        await invoice_service.unallocate(session, invoice, allocation_id)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return _serialize(await _load(session, invoice_id, ctx.workspace.id))
