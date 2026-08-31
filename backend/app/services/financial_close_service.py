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
from app.models.category import Category
from app.models.position import Position
from app.models.transaction import Transaction
from app.services.dashboard_service import _account_balance_at
from app.services.period_cutoff import resolve_workspace_cutoff


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
    cutoff_info = await resolve_workspace_cutoff(session, workspace_id, end)
    cutoff = cutoff_info.cutoff_date
    cutoff_source = cutoff_info.source
    latest_sync_at = cutoff_info.latest_sync_at
    sync_is_stale = cutoff_info.sync_is_stale
    positions = await session.scalars(
        select(Position).options(selectinload(Position.movements)).where(
            Position.workspace_id == workspace_id, Position.is_archived == False
        )
    )
    positions = list(positions.unique().all())
    active_movements: list[tuple[Position, object]] = []
    linked_movement_ids: dict[uuid.UUID, object] = {}
    for position in positions:
        for movement in position.movements:
            if movement.effective_date > cutoff:
                continue
            if movement.reversed_at is not None and movement.reversed_at.date() <= cutoff:
                continue
            active_movements.append((position, movement))
            if movement.transaction_id is not None:
                linked_movement_ids[movement.transaction_id] = movement
    tx_result = await session.execute(
        select(Transaction, Category.treat_as_transfer, Category.is_ignored)
        .join(Account, Transaction.account_id == Account.id)
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
        linked_movement = linked_movement_ids.get(tx.id)
        if linked_movement is not None:
            # A transaction linked to a Position is a cash leg of a
            # patrimonial movement. Keep it out of P&L; any explicit interest,
            # fee or tax on the movement is added below as result.
            transfers += abs(Decimal(str(linked_movement.cash_amount or tx.amount or 0)))
            continue
        # A credit-card refund is a real credit even though it belongs to a
        # bill; only an explicit transfer pair/category marks a settlement.
        # This prevents refunds from being silently discarded as payments.
        if tx.transfer_pair_id is not None or as_transfer:
            transfers += amount
            continue
        if tx.type == "credit":
            income += amount
        elif tx.type == "debit":
            consumption += amount

    account_rows = await session.execute(
        select(Account).where(Account.workspace_id == workspace_id)
    )
    account_balance = Decimal("0")
    for account in account_rows.scalars().all():
        # A close made after the requested cutoff still belongs in the
        # historical net worth. Accounts closed on or before the cutoff do not.
        if account.is_closed and account.closed_at is not None and account.closed_at.date() <= cutoff:
            continue
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

    receivables = Decimal("0")
    liabilities = Decimal("0")
    position_interest_income = Decimal("0")
    position_costs = Decimal("0")
    for position, movement in active_movements:
        if movement.effective_date < start or movement.effective_date > cutoff:
            continue
        interest = Decimal(str(movement.interest_amount or 0))
        fees_and_taxes = Decimal(str(movement.fee_amount or 0)) + Decimal(str(movement.tax_amount or 0))
        if position.side == "receivable":
            position_interest_income += interest
        else:
            position_costs += interest
        position_costs += fees_and_taxes
    income += position_interest_income
    consumption += position_costs
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
    # Until the user explicitly configures the investible-portfolio lens, this
    # is a broad proxy over all open accounts/assets. Keep the number available
    # for continuity, but label it so downstream UI and narrators cannot pass
    # it off as a true investible-portfolio balance.
    financial_portfolio_net = account_balance + asset_total - liabilities
    return {
        "period": period,
        "as_of": cutoff.isoformat(),
        "requested_period_end": end.isoformat(),
        "cutoff_date": cutoff.isoformat(),
        "cutoff_source": cutoff_source,
        "latest_sync_at": latest_sync_at.isoformat() if latest_sync_at is not None else None,
        "sync_is_stale": sync_is_stale,
        "income_economic": income,
        "position_interest_income": position_interest_income,
        "position_costs": position_costs,
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
        "financial_portfolio_net": financial_portfolio_net,
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
            "financial_portfolio_net": {
                "status": "provisional",
                "reason": "Proxy amplo: inclui todas as contas e ativos estruturais. Configure a Collection Carteira investível para separar a carteira financeira.",
            },
        },
        "methodology": {
            "source": "Securo ledger, account balances, asset valuations and Position ledger",
            "period_policy": "Only transactions dated inside the requested month and available by the cutoff; future rows are excluded",
            "cutoff_policy": "The latest workspace synchronization limits the effective cutoff",
            "savings_rate": "null when economic income is not positive",
            "principal_withdrawals": "excluded from economic income; linked cash legs are patrimonial transfers",
            "position_result": "interest increases/lowers result by side; fees and taxes are costs",
            "financial_portfolio_net": "provisional proxy until the investible-portfolio Collection is configured",
        },
    }
