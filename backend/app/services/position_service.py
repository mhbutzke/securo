import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.asset_group import AssetGroup
from app.models.position import Position, PositionMovement, PositionValuation
from app.models.transaction import Transaction
from app.schemas.position import PositionCreate, PositionMovementCreate, PositionUpdate, PositionValuationCreate


async def _validate_group(session: AsyncSession, workspace_id: uuid.UUID, group_id):
    if group_id is None:
        return
    group = await session.scalar(select(AssetGroup.id).where(AssetGroup.id == group_id, AssetGroup.workspace_id == workspace_id))
    if group is None:
        raise ValueError("Asset group not found")


def movement_delta(movement: PositionMovement) -> Decimal:
    if movement.reversed_at is not None:
        return Decimal("0")
    return movement.principal_amount if movement.kind in ("opening", "increase") else -movement.principal_amount


def position_balance(position: Position) -> Decimal:
    return sum((movement_delta(m) for m in position.movements), Decimal("0"))


async def list_positions(session: AsyncSession, workspace_id: uuid.UUID, include_archived: bool = False):
    query = select(Position).options(selectinload(Position.movements), selectinload(Position.valuations)).where(Position.workspace_id == workspace_id)
    if not include_archived:
        query = query.where(Position.is_archived == False)
    result = await session.execute(query.order_by(Position.created_at, Position.id))
    return list(result.scalars().unique().all())


async def get_position(session: AsyncSession, workspace_id: uuid.UUID, position_id: uuid.UUID):
    result = await session.execute(
        select(Position).options(selectinload(Position.movements), selectinload(Position.valuations)).where(
            Position.id == position_id, Position.workspace_id == workspace_id
        ).execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def create_position(session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, data: PositionCreate):
    await _validate_group(session, workspace_id, data.group_id)
    position = Position(workspace_id=workspace_id, user_id=user_id, **data.model_dump())
    session.add(position)
    await session.flush()
    opening = PositionMovement(
        position_id=position.id, kind="opening", principal_amount=data.original_principal,
        effective_date=data.start_date, idempotency_key=f"opening:{position.id}",
    )
    session.add(opening)
    await session.commit()
    return await get_position(session, workspace_id, position.id)


async def update_position(session: AsyncSession, workspace_id: uuid.UUID, position_id: uuid.UUID, data: PositionUpdate):
    position = await get_position(session, workspace_id, position_id)
    if position is None:
        return None
    values = data.model_dump(exclude_unset=True)
    if "group_id" in values:
        await _validate_group(session, workspace_id, values["group_id"])
    for key, value in values.items():
        setattr(position, key, value)
    await session.commit()
    return await get_position(session, workspace_id, position_id)


async def add_movement(session: AsyncSession, workspace_id: uuid.UUID, position_id: uuid.UUID, data: PositionMovementCreate):
    position = await get_position(session, workspace_id, position_id)
    if position is None:
        return None
    if data.transaction_id is not None:
        tx = await session.scalar(select(Transaction.id).where(Transaction.id == data.transaction_id, Transaction.workspace_id == workspace_id))
        if tx is None:
            raise ValueError("Transaction not found")
        linked = await session.scalar(
            select(PositionMovement).where(
                PositionMovement.transaction_id == data.transaction_id,
                PositionMovement.reversed_at.is_(None),
            )
        )
        if linked is not None and linked.position_id != position_id:
            raise ValueError("Transaction is already linked to another position")
    existing = await session.scalar(select(PositionMovement).where(PositionMovement.position_id == position_id, PositionMovement.idempotency_key == data.idempotency_key))
    if existing is not None:
        return existing
    movement = PositionMovement(position_id=position_id, **data.model_dump())
    session.add(movement)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(select(PositionMovement).where(PositionMovement.position_id == position_id, PositionMovement.idempotency_key == data.idempotency_key))
        if existing is not None:
            return existing
        raise
    return movement


async def reverse_movement(session: AsyncSession, workspace_id: uuid.UUID, position_id: uuid.UUID, movement_id: uuid.UUID):
    position = await get_position(session, workspace_id, position_id)
    if position is None:
        return None
    movement = next((m for m in position.movements if m.id == movement_id), None)
    if movement is None:
        return False
    if movement.reversed_at is None:
        movement.reversed_at = datetime.now(timezone.utc)
        await session.commit()
    return movement


async def add_valuation(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    position_id: uuid.UUID,
    data: PositionValuationCreate,
):
    position = await get_position(session, workspace_id, position_id)
    if position is None:
        return None
    existing = await session.scalar(
        select(PositionValuation).where(
            PositionValuation.position_id == position_id,
            PositionValuation.idempotency_key == data.idempotency_key,
        )
    )
    if existing is not None:
        return existing
    if data.currency != position.currency and data.base_amount is None and data.fx_rate is None:
        raise ValueError("base_amount or fx_rate is required for a foreign-currency valuation")
    valuation = PositionValuation(position_id=position_id, **data.model_dump())
    session.add(valuation)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(
            select(PositionValuation).where(
                PositionValuation.position_id == position_id,
                PositionValuation.idempotency_key == data.idempotency_key,
            )
        )
        if existing is not None:
            return existing
        raise
    return valuation


async def reverse_valuation(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    position_id: uuid.UUID,
    valuation_id: uuid.UUID,
):
    position = await get_position(session, workspace_id, position_id)
    if position is None:
        return None
    valuation = next((v for v in position.valuations if v.id == valuation_id), None)
    if valuation is None:
        return False
    if valuation.reversed_at is None:
        valuation.reversed_at = datetime.now(timezone.utc)
        await session.commit()
    return valuation
