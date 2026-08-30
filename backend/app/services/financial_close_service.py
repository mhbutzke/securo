"""Deterministic close snapshot used by the UI and the MCP narrator."""

from calendar import monthrange
from datetime import date
from decimal import Decimal
import uuid

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.bank_connection import BankConnection
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
    latest_sync_at = await session.scalar(
        select(func.max(BankConnection.last_sync_at)).where(
            BankConnection.workspace_id == workspace_id,
        )
    )
    latest_sync_date = latest_sync_at.date() if latest_sync_at is not None else None
    cutoff = min(end, latest_sync_date) if latest_sync_date is not None else end
    sync_is_stale = latest_sync_date is None or latest_sync_date < end
    cutoff_source = "last_sync" if latest_sync_date is not None and latest_sync_date < end else "period_end"
    tx_result = await session.execute(
        select(Transaction, Category.treat_as_transfer, Category.is_ignored)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(Transaction.workspace_id == workspace_id, Transaction.date >= start, Transaction.date <= cutoff)
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
            .where(AssetValue.asset_id == asset.id, AssetValue.date <= cutoff)
            .order_by(desc(AssetValue.date), desc(AssetValue.id)).limit(1)
        )
        if value is None:
            value = asset.purchase_price if asset.purchase_date is None or asset.purchase_date <= cutoff else Decimal("0")
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
             for m in position.movements
             if m.reversed_at is None and m.effective_date <= cutoff), Decimal("0")
        )
        side = position.side
        if side == "receivable":
            receivables += Decimal(str(principal or 0))
        else:
            liabilities += Decimal(str(principal or 0))

    # A withdrawal only has meaning when the workspace has an explicit
    # investible-portfolio lens. Inferring it from account or asset names would
    # silently misclassify ordinary transfers, so fail closed until that lens
    # is configured.
    portfolio_withdrawals = None
    savings_rate = None if income <= 0 else (income - consumption) / income
    return {
        "period": period,
        "as_of": cutoff.isoformat(),
        "requested_period_end": end.isoformat(),
        "cutoff_date": cutoff.isoformat(),
        "cutoff_source": cutoff_source,
        "latest_sync_at": latest_sync_at.isoformat() if latest_sync_at is not None else None,
        "sync_is_stale": sync_is_stale,
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
        "withdrawal_rate_12m": None,
        "liquidity_coverage": None,
        "metric_quality": {
            "portfolio_withdrawal_net": {
                "status": "unavailable",
                "reason": "Configure the investible-portfolio lens before classifying withdrawals.",
            },
            "withdrawal_rate_12m": {
                "status": "unavailable",
                "reason": "Requires an investible-portfolio lens and 13 monthly closing values.",
            },
            "liquidity_coverage": {
                "status": "unavailable",
                "reason": "Requires essential-expense categories and eligible D+0/D+1 assets.",
            },
        },
        "methodology": {
            "source": "Securo ledger, account balances, asset valuations and Position ledger",
            "period_policy": "Only transactions dated inside the requested month and available by the cutoff; future rows are excluded",
            "cutoff_policy": "The latest workspace synchronization limits the effective cutoff",
            "savings_rate": "null when economic income is not positive",
            "principal_withdrawals": "excluded from economic income",
        },
    }
