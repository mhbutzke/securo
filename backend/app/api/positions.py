import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace, current_writable_workspace
from app.schemas.position import (
    PositionCreate, PositionMovementCreate, PositionMovementRead, PositionRead,
    PositionTransactionLinkCreate, PositionUpdate, PositionValuationCreate, PositionValuationRead,
)
from app.services import position_service

router = APIRouter(prefix="/api/positions", tags=["positions"])


def _read(position):
    payload = PositionRead.model_validate(position)
    payload.balance = position_service.position_balance(position)
    return payload


@router.get("", response_model=list[PositionRead])
async def list_positions(include_archived: bool = False, ctx: WorkspaceContext = Depends(current_workspace), session: AsyncSession = Depends(get_async_session)):
    return [_read(p) for p in await position_service.list_positions(session, ctx.workspace.id, include_archived)]


@router.post("", response_model=PositionRead, status_code=status.HTTP_201_CREATED)
async def create_position(data: PositionCreate, ctx: WorkspaceContext = Depends(current_writable_workspace), session: AsyncSession = Depends(get_async_session)):
    try:
        return _read(await position_service.create_position(session, ctx.workspace.id, ctx.user_id, data))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{position_id}", response_model=PositionRead)
async def get_position(position_id: uuid.UUID, ctx: WorkspaceContext = Depends(current_workspace), session: AsyncSession = Depends(get_async_session)):
    position = await position_service.get_position(session, ctx.workspace.id, position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return _read(position)


@router.patch("/{position_id}", response_model=PositionRead)
async def update_position(position_id: uuid.UUID, data: PositionUpdate, ctx: WorkspaceContext = Depends(current_writable_workspace), session: AsyncSession = Depends(get_async_session)):
    try:
        position = await position_service.update_position(session, ctx.workspace.id, position_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return _read(position)


@router.post("/{position_id}/movements", response_model=PositionMovementRead, status_code=status.HTTP_201_CREATED)
async def create_movement(position_id: uuid.UUID, data: PositionMovementCreate, ctx: WorkspaceContext = Depends(current_writable_workspace), session: AsyncSession = Depends(get_async_session)):
    try:
        movement = await position_service.add_movement(session, ctx.workspace.id, position_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if movement is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return movement


@router.post("/{position_id}/transaction-links", response_model=PositionMovementRead, status_code=status.HTTP_201_CREATED)
async def link_transaction(position_id: uuid.UUID, data: PositionTransactionLinkCreate, ctx: WorkspaceContext = Depends(current_writable_workspace), session: AsyncSession = Depends(get_async_session)):
    movement_data = PositionMovementCreate(**data.model_dump())
    try:
        movement = await position_service.add_movement(session, ctx.workspace.id, position_id, movement_data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if movement is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return movement


@router.post("/{position_id}/movements/{movement_id}/reverse", response_model=PositionMovementRead)
async def reverse_movement(position_id: uuid.UUID, movement_id: uuid.UUID, ctx: WorkspaceContext = Depends(current_writable_workspace), session: AsyncSession = Depends(get_async_session)):
    movement = await position_service.reverse_movement(session, ctx.workspace.id, position_id, movement_id)
    if movement is None:
        raise HTTPException(status_code=404, detail="Position not found")
    if movement is False:
        raise HTTPException(status_code=404, detail="Movement not found")
    return movement


@router.post("/{position_id}/valuations", response_model=PositionValuationRead, status_code=status.HTTP_201_CREATED)
async def create_valuation(
    position_id: uuid.UUID,
    data: PositionValuationCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        valuation = await position_service.add_valuation(session, ctx.workspace.id, position_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if valuation is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return valuation


@router.post("/{position_id}/valuations/{valuation_id}/reverse", response_model=PositionValuationRead)
async def reverse_valuation(
    position_id: uuid.UUID,
    valuation_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    valuation = await position_service.reverse_valuation(session, ctx.workspace.id, position_id, valuation_id)
    if valuation is None:
        raise HTTPException(status_code=404, detail="Position not found")
    if valuation is False:
        raise HTTPException(status_code=404, detail="Valuation not found")
    return valuation
