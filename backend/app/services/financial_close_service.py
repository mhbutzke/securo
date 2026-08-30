"""Deterministic close snapshot used by the UI and the MCP narrator."""

from calendar import monthrange
from datetime import date
from decimal import Decimal
import uuid

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.category import Category
from app.models.position import Position
from app.models.transaction import Transaction


async def build_snapshot(session: AsyncSession, workspace_id: uuid.UUID, period: str) -> dict:
    try:
        year, month = (int(v) for v in period.split("-", 1))
        start = date(year, month, 1)
    except (ValueError, TypeError):
        raise ValueError("period must be YYYY-MM")
    end = date(year, month, monthrange(year, month)[1])
    tx_result = await session.execute(
        select(Transaction, Category.treat_as_transfer, Category.is_ignored)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(Transaction.workspace_id == workspace_id, Transaction.date >= start, Transaction.date <= end)
        .order_by(Transaction.date, Transaction.id)
    )
    income = Decimal("0")
    consumption = Decimal("0")
    transfers = Decimal("0")
    ignored = Decimal("0")
    for tx, as_transfer, category_ignored in tx_result.all():
        amount = abs(Decimal(str(tx.amount or 0)))
        if tx.is_ignored or category_ignored:
            ignored += amount
            continue
        if tx.transfer_pair_id is not None or as_transfer:
            transfers += amount
            continue
        if tx.type == "credit":
            income += amount
        elif tx.type == "debit":
            consumption += amount

    account_rows = await session.execute(
        select(Account.currency, Account.balance).where(Account.workspace_id == workspace_id, Account.is_closed == False)
    )
    account_balance = sum((Decimal(str(row.balance or 0)) for row in account_rows.all()), Decimal("0"))

    assets = await session.scalars(select(Asset).where(Asset.workspace_id == workspace_id, Asset.is_archived == False))
    asset_total = Decimal("0")
    for asset in assets:
        value = await session.scalar(
            select(AssetValue.amount)
            .where(AssetValue.asset_id == asset.id, AssetValue.date <= end)
            .order_by(desc(AssetValue.date), desc(AssetValue.id)).limit(1)
        )
        if value is None:
            value = asset.purchase_price if asset.purchase_date is None or asset.purchase_date <= end else Decimal("0")
        asset_total += Decimal(str(value or 0))

    positions = await session.scalars(
        select(Position).options(selectinload(Position.movements)).where(
            Position.workspace_id == workspace_id, Position.is_archived == False
        )
    )
    receivables = Decimal("0")
    liabilities = Decimal("0")
    for position in positions:
        principal = sum(
            (m.principal_amount if m.kind in ("opening", "increase") else -m.principal_amount
             for m in position.movements if m.reversed_at is None), Decimal("0")
        )
        side = position.side
        if side == "receivable":
            receivables += Decimal(str(principal or 0))
        else:
            liabilities += Decimal(str(principal or 0))

    portfolio_withdrawals = Decimal("0")
    savings_rate = None if income <= 0 else (income - consumption) / income
    return {
        "period": period,
        "as_of": end.isoformat(),
        "income_economic": income,
        "consumption_recurring": consumption,
        "transfers_and_patrimonial_movements": transfers,
        "ignored_amount": ignored,
        "portfolio_withdrawal_net": portfolio_withdrawals,
        "savings_rate": savings_rate,
        "account_balance": account_balance,
        "asset_value": asset_total,
        "receivables": receivables,
        "liabilities": liabilities,
        "net_worth_consolidated": account_balance + asset_total + receivables - liabilities,
        "financial_portfolio_net": account_balance + asset_total - liabilities,
        "methodology": {
            "source": "Securo ledger, account balances, asset valuations and Position ledger",
            "period_policy": "Only transactions dated inside the requested month; future rows are excluded",
            "savings_rate": "null when economic income is not positive",
            "principal_withdrawals": "excluded from economic income",
        },
    }
