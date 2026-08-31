"""Idempotent, workspace-scoped financial foundation migration.

This script only creates/archives/annotates records; it never deletes financial
rows or starts provider transfers. It is intentionally safe to rerun after a
partial deployment.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import async_session_maker
from app.models import (
    Account,
    Asset,
    AssetGroup,
    BankConnection,
    Category,
    Collection,
    Position,
    PositionMovement,
    PositionValuation,
    Transaction,
    User,
    Workspace,
)


CUT_OFF = date(2026, 8, 30)
WORKSPACE_NAME = "Pessoal"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


async def _workspace(session):
    result = await session.execute(
        select(Workspace).where(Workspace.name == WORKSPACE_NAME, Workspace.is_archived.is_(False))
    )
    workspace = result.scalar_one_or_none()
    if workspace is None:
        raise RuntimeError(f"workspace {WORKSPACE_NAME!r} not found")
    user = await session.get(User, workspace.created_by_user_id)
    if user is None:
        raise RuntimeError("workspace owner not found")
    latest_sync = await session.scalar(
        select(BankConnection.last_sync_at)
        .where(BankConnection.workspace_id == workspace.id)
        .order_by(BankConnection.last_sync_at.desc())
        .limit(1)
    )
    cutoff = latest_sync.date() if latest_sync is not None else date.today()
    return workspace, user, cutoff


async def _asset_groups(session, workspace, user_id):
    names = {
        "BTG": "Investimentos financeiros",
        "MeuPluggy": "Imóveis",
        "MeuPluggy 2": "Uso pessoal",
    }
    result = await session.execute(select(AssetGroup).where(AssetGroup.workspace_id == workspace.id))
    groups = {group.name: group for group in result.scalars().all()}
    for old, new in names.items():
        group = groups.get(old)
        if group is not None:
            group.name = new
            groups[new] = group
            groups.pop(old, None)
    for name in ("Participações privadas", "Recebíveis e obrigações"):
        if name not in groups:
            group = AssetGroup(
                user_id=user_id,
                workspace_id=workspace.id,
                name=name,
                source="manual",
                icon="wallet",
                color="#64748B",
            )
            session.add(group)
            await session.flush()
            groups[name] = group
    return groups


async def _organise_assets(session, workspace, groups):
    result = await session.execute(
        select(Asset).where(Asset.workspace_id == workspace.id, Asset.is_archived.is_(False))
    )
    changed = 0
    for asset in result.scalars().all():
        if asset.type == "real_estate":
            target = groups["Imóveis"]
        elif asset.type == "vehicle":
            target = groups["Uso pessoal"]
        elif asset.name.casefold() == "reserva imob":
            target = groups["Participações privadas"]
        elif asset.type == "investment":
            target = groups["Investimentos financeiros"]
        else:
            continue
        if asset.group_id != target.id:
            asset.group_id = target.id
            changed += 1
    return changed


async def _position(
    session,
    workspace,
    user_id,
    group,
    *,
    name: str,
    principal: Decimal,
    currency: str,
    valuation: Decimal,
    valuation_currency: str,
    fx_rate: Decimal | None,
    start_date: date,
):
    result = await session.execute(
        select(Position).where(Position.workspace_id == workspace.id, Position.name == name)
    )
    position = result.scalar_one_or_none()
    if position is None:
        position = Position(
            user_id=user_id,
            workspace_id=workspace.id,
            side="receivable",
            name=name,
            counterparty=name,
            currency=currency,
            original_principal=principal,
            start_date=start_date,
            liquidity="illiquid",
            status="open",
            group_id=group.id,
        )
        session.add(position)
        await session.flush()
    else:
        position.group_id = group.id
        position.original_principal = principal
        position.currency = currency
        position.status = "open"

    movement_key = f"foundation-opening:{_slug(name)}:v1"
    movement = await session.scalar(
        select(PositionMovement).where(
            PositionMovement.position_id == position.id,
            PositionMovement.idempotency_key == movement_key,
        )
    )
    if movement is None:
        session.add(
            PositionMovement(
                position_id=position.id,
                kind="opening",
                principal_amount=principal,
                cash_amount=principal,
                effective_date=start_date,
                idempotency_key=movement_key,
            )
        )

    valuation_key = f"foundation-valuation:{_slug(name)}:{CUT_OFF.isoformat()}:v1"
    existing = await session.scalar(
        select(PositionValuation).where(
            PositionValuation.position_id == position.id,
            PositionValuation.idempotency_key == valuation_key,
        )
    )
    if existing is None:
        session.add(
            PositionValuation(
                position_id=position.id,
                amount=valuation,
                currency=valuation_currency,
                base_amount=valuation if valuation_currency == "BRL" else valuation * (fx_rate or 0),
                base_currency="BRL",
                fx_rate=fx_rate,
                valuation_date=CUT_OFF,
                basis="declared",
                source="user",
                confidence="low",
                idempotency_key=valuation_key,
            )
        )
    return position


async def _migrate_positions(session, workspace, user_id, groups):
    positions = [
        dict(
            name="Caução One Tower 3701",
            principal=Decimal("105000"),
            currency="BRL",
            valuation=Decimal("124199.33"),
            valuation_currency="BRL",
            fx_rate=None,
            start_date=date(2025, 11, 3),
        ),
        dict(
            name="Emp. Renan",
            principal=Decimal("150000"),
            currency="BRL",
            valuation=Decimal("229640.72"),
            valuation_currency="BRL",
            fx_rate=None,
            start_date=date(2025, 8, 1),
        ),
        dict(
            name="Empréstimo ao irmão",
            principal=Decimal("60000"),
            currency="USD",
            valuation=Decimal("60000"),
            valuation_currency="USD",
            fx_rate=Decimal("5.2005"),
            start_date=date(2025, 8, 1),
        ),
    ]
    created = 0
    for spec in positions:
        before = await session.scalar(
            select(Position.id).where(Position.workspace_id == workspace.id, Position.name == spec["name"])
        )
        await _position(session, workspace, user_id, groups["Recebíveis e obrigações"], **spec)
        created += int(before is None)

    # Archive the three legacy positive-asset placeholders only after the
    # ledger and valuation rows above exist. Archiving is reversible.
    legacy_names = {"Caução Aluguel One Tower", "Emp Renan", "USD"}
    result = await session.execute(
        select(Asset).where(Asset.workspace_id == workspace.id, Asset.name.in_(legacy_names))
    )
    archived = 0
    for asset in result.scalars().all():
        if not asset.is_archived:
            asset.is_archived = True
            archived += 1
    return created, archived


async def _collections(session, workspace, user_id, groups):
    result = await session.execute(
        select(Collection)
        .options(
            selectinload(Collection.accounts),
            selectinload(Collection.asset_groups),
            selectinload(Collection.positions),
        )
        .where(Collection.workspace_id == workspace.id)
    )
    collections = {c.name: c for c in result.scalars().all()}
    old_cards = collections.get("Cartões de Crédito")
    if old_cards is not None:
        old_cards.name = "Cartões"
        collections["Cartões"] = old_cards
        collections.pop("Cartões de Crédito", None)

    for name in ("Caixa familiar", "Carteira investível"):
        if name not in collections:
            collection = Collection(
                user_id=user_id,
                workspace_id=workspace.id,
                name=name,
                icon="folder",
                color="#0EA5E9" if name == "Caixa familiar" else "#8B5CF6",
            )
            # Initialize relationships explicitly for a newly-created row so
            # async SQLAlchemy never attempts an implicit lazy load.
            collection.accounts = []
            collection.asset_groups = []
            collection.positions = []
            session.add(collection)
            await session.flush()
            collections[name] = collection

    accounts = (
        await session.execute(select(Account).where(Account.workspace_id == workspace.id))
    ).scalars().all()
    def add_accounts(collection, predicate):
        present = {account.id for account in collection.accounts}
        for account in accounts:
            if predicate(account) and account.id not in present:
                collection.accounts.append(account)
                present.add(account.id)

    add_accounts(
        collections["Caixa familiar"],
        lambda account: account.type in {"checking", "savings", "wallet"}
        and any(token in account.name.casefold() for token in ("btg", "unicred")),
    )
    add_accounts(collections["Cartões"], lambda account: account.type == "credit_card")
    add_accounts(
        collections["Carteira investível"],
        lambda account: any(token in account.name.casefold() for token in ("invest", "eqi", "necton", "carteira")),
    )
    if groups["Investimentos financeiros"] not in collections["Carteira investível"].asset_groups:
        collections["Carteira investível"].asset_groups.append(groups["Investimentos financeiros"])
    return {name: len(collection.accounts) for name, collection in collections.items()}


async def _categories_and_tags(session, workspace, user_id):
    category_specs = {
        "Obra capitalizável": ("capitalizable", False),
        "Mobília": ("consumption", False),
        "Frete e mudança": ("consumption", False),
        "Serviços": ("consumption", False),
        "Despesas não capitalizáveis": ("consumption", False),
    }
    existing = {
        row.name: row
        for row in (
            await session.execute(select(Category).where(Category.workspace_id == workspace.id))
        ).scalars().all()
    }
    created = 0
    for name, (role, essential) in category_specs.items():
        category = existing.get(name)
        if category is None:
            category = Category(
                user_id=user_id,
                workspace_id=workspace.id,
                name=name,
                accounting_role=role,
                is_essential=essential,
            )
            session.add(category)
            created += 1
        else:
            category.accounting_role = role
            category.is_essential = essential

    # Tags are stored as hashtags in notes for backwards compatibility. The
    # append-only update preserves every existing note and is idempotent.
    result = await session.execute(
        select(Transaction).where(
            Transaction.workspace_id == workspace.id,
            Transaction.date >= date(2026, 1, 1),
            Transaction.date <= CUT_OFF,
            Transaction.description.ilike("%one tower%")
            | Transaction.description.ilike("%3401%")
            | Transaction.description.ilike("%mudan%")
            | Transaction.description.ilike("%mobili%")
            | Transaction.description.ilike("%frete%"),
        )
    )
    tagged = 0
    for tx in result.scalars().all():
        notes = tx.notes or ""
        if "#projeto-one-tower-3401" not in notes:
            tx.notes = f"{notes.rstrip()} #projeto-one-tower-3401".strip()
            tagged += 1
    return created, tagged


async def main():
    global CUT_OFF
    async with async_session_maker() as session:
        workspace, user, cutoff = await _workspace(session)
        CUT_OFF = cutoff
        groups = await _asset_groups(session, workspace, user.id)
        asset_changes = await _organise_assets(session, workspace, groups)
        positions_created, legacy_archived = await _migrate_positions(session, workspace, user.id, groups)
        collection_counts = await _collections(session, workspace, user.id, groups)
        categories_created, tagged = await _categories_and_tags(session, workspace, user.id)
        await session.commit()
        print(
            {
                "workspace": WORKSPACE_NAME,
                "cutoff": CUT_OFF.isoformat(),
                "asset_group_taxonomy": sorted(groups),
                "asset_group_assignments_changed": asset_changes,
                "positions_created": positions_created,
                "legacy_assets_archived": legacy_archived,
                "collection_account_counts": collection_counts,
                "one_tower_categories_created": categories_created,
                "one_tower_transactions_tagged": tagged,
            }
        )


if __name__ == "__main__":
    asyncio.run(main())
