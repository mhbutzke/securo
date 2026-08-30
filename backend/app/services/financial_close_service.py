"""Deterministic close snapshot used by the UI and the MCP narrator."""

from calendar import monthrange
from datetime import date
from decimal import Decimal
import uuid

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.bank_connection import BankConnection
from app.models.category import Category
from app.models.position import Position
from app.models.transaction import Transaction
from app.services.dashboard_service import _account_balance_at


async def _snapshot_account_balance(
    session: AsyncSession, account: Account, cutoff: date
) -> Decimal:
    """Return an account balance that is consistent with the close cutoff.

    Connected accounts can be reconstructed from the provider's current
    balance and posted activity after the cutoff. Manual accounts without any
    ledger rows keep their explicitly entered balance; once rows exist, the
    ledger is authoritative for historical snapshots.
    """
    if account.connection_id:
        balance = await _account_balance_at(session, account, cutoff)
        return Decimal(str(balance))

    has_transactions = await session.scalar(
        select(Transaction.id)
        .where(Transaction.account_id == account.id)
        .limit(1)
    )
    if has_transactions is None:
        return Decimal(str(account.balance or 0))
    balance = await _account_balance_at(session, account, cutoff)
    return Decimal(str(balance))


async def build_snapshot(session: AsyncSession, workspace_id: uuid.UUID, period: str) -> dict:
    try:
        year, month = (int(v) for v in period.split("-", 1))
        start = date(year, month, 1)
    except (ValueError, TypeError):
        raise ValueError("period must be YYYY-MM")
    end = date(year, month, monthrange(year, month)[1])
    connection_rows = await session.execute(
        select(BankConnection.status, BankConnection.last_sync_at).where(
            BankConnection.workspace_id == workspace_id,
        )
    )
    connections = connection_rows.all()
    active_connections = [row for row in connections if row.status == "active"]
    sync_dates = [row.last_sync_at.date() for row in active_connections if row.last_sync_at is not None]
    latest_sync_at = max(
        (row.last_sync_at for row in active_connections if row.last_sync_at is not None),
        default=None,
    )
    # Use the oldest active connection's sync date so a fresh connection does
    # not make stale data from another account appear reconciled. A workspace
    # with no active connections is manual-only and needs no sync gate. An
    # active connection with no sync is clamped to today for future periods.
    if sync_dates:
        sync_cutoff = min(sync_dates)
    elif active_connections:
        sync_cutoff = date.today()
    else:
        sync_cutoff = None
    cutoff = min(end, sync_cutoff) if sync_cutoff is not None else end
    sync_is_stale = bool(active_connections) and (not sync_dates or min(sync_dates) < end)
    if not active_connections:
        cutoff_source = "period_end"
    elif not sync_dates:
        cutoff_source = "no_sync"
    elif min(sync_dates) < end:
        cutoff_source = "last_sync"
    else:
        cutoff_source = "period_end"
    tx_result = await session.execute(
        select(Transaction, Account.type, Category.treat_as_transfer, Category.is_ignored)
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(Transaction.workspace_id == workspace_id, Transaction.date >= start, Transaction.date <= cutoff)
        .order_by(Transaction.date, Transaction.id)
    )
    income = Decimal("0")
    consumption = Decimal("0")
    transfers = Decimal("0")
    ignored = Decimal("0")
    for tx, account_type, as_transfer, category_ignored in tx_result.all():
        amount = abs(Decimal(str(tx.amount or 0)))
        if tx.is_ignored or category_ignored:
            ignored += amount
            continue
        # A credit posted to a card bill is a settlement/credit adjustment,
        # never economic income. Keep it in the transfer bucket so paying the
        # bill cannot create a second income/expense event.
        is_card_settlement = (
            account_type == "credit_card"
            and tx.type == "credit"
            and tx.bill_id is not None
        )
        if tx.transfer_pair_id is not None or as_transfer or is_card_settlement:
            transfers += amount
            continue
        if tx.type == "credit":
            income += amount
        elif tx.type == "debit":
            consumption += amount

    account_rows = await session.execute(
        select(Account).where(Account.workspace_id == workspace_id, Account.is_closed == False)
    )
    account_balance = Decimal("0")
    for account in account_rows.scalars().all():
        account_balance += await _snapshot_account_balance(session, account, cutoff)

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
             if m.effective_date <= cutoff
             and (m.reversed_at is None or m.reversed_at.date() > cutoff)), Decimal("0")
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
