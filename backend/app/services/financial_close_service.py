"""Deterministic close snapshot used by the UI and the MCP narrator."""

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
import math
import uuid

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.category import Category
from app.models.collection import Collection
from app.models.position import Position
from app.services.ipca_service import cumulative_ipca
from app.models.transaction import Transaction
from app.services.dashboard_service import _account_balance_at
from app.services.period_cutoff import resolve_workspace_cutoff

INVESTIBLE_COLLECTION_NAME = "carteira investível"


def _is_investible_collection(collection: Collection) -> bool:
    return collection.name.strip().casefold() == INVESTIBLE_COLLECTION_NAME


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


async def _load_collection(
    session: AsyncSession, workspace_id: uuid.UUID, collection_id: uuid.UUID
) -> Collection:
    """Load a reporting lens and reject cross-workspace identifiers."""
    result = await session.execute(
        select(Collection)
        .options(
            selectinload(Collection.accounts),
            selectinload(Collection.asset_groups),
            selectinload(Collection.positions),
        )
        .where(Collection.id == collection_id, Collection.workspace_id == workspace_id)
    )
    collection = result.scalar_one_or_none()
    if collection is None:
        raise LookupError("Collection not found")
    return collection


async def _resolve_investible_collection(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    collection_id: uuid.UUID | None,
) -> Collection | None:
    """Resolve the named lens, rejecting arbitrary reporting collections."""
    if collection_id is not None:
        collection = await _load_collection(session, workspace_id, collection_id)
        if not _is_investible_collection(collection):
            raise ValueError("collection_id must reference the Carteira investível collection")
        return collection
    result = await session.execute(select(Collection).where(Collection.workspace_id == workspace_id))
    named = [collection for collection in result.scalars().all() if _is_investible_collection(collection)]
    if len(named) != 1:
        return None
    return await _load_collection(session, workspace_id, named[0].id)


async def _collection_withdrawal_net(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    collection_account_ids: set[uuid.UUID],
    period_pair_ids: set[uuid.UUID] | None,
    cutoff: date,
    start: date,
) -> Decimal:
    """Sum paired cash legs leaving/entering the selected account lens."""
    if not collection_account_ids:
        return Decimal("0")
    if period_pair_ids is None:
        pair_rows = await session.execute(
            select(Transaction.transfer_pair_id).where(
                Transaction.workspace_id == workspace_id,
                Transaction.date >= start,
                Transaction.date <= cutoff,
                Transaction.transfer_pair_id.is_not(None),
            ).distinct()
        )
        period_pair_ids = {row[0] for row in pair_rows.all() if row[0] is not None}
    if not period_pair_ids:
        return Decimal("0")
    result = await session.execute(
        select(Transaction).where(
            Transaction.workspace_id == workspace_id,
            Transaction.transfer_pair_id.in_(period_pair_ids),
            Transaction.date >= start,
            Transaction.date <= cutoff,
        )
    )
    pairs: dict[uuid.UUID, list[Transaction]] = {}
    for transaction in result.scalars().all():
        if transaction.transfer_pair_id is not None:
            pairs.setdefault(transaction.transfer_pair_id, []).append(transaction)
    total = Decimal("0")
    for legs in pairs.values():
        if not any(leg.account_id not in collection_account_ids for leg in legs):
            continue
        for leg in legs:
            if leg.account_id not in collection_account_ids:
                continue
            amount = abs(Decimal(str(leg.amount or 0)))
            total += amount if leg.type == "debit" else -amount
    return total


def _position_value_at(position: Position, cutoff: date) -> Decimal:
    candidates = sorted(
        (v for v in position.valuations if v.valuation_date <= cutoff and v.reversed_at is None),
        key=lambda v: (v.valuation_date, v.created_at, str(v.id)),
        reverse=True,
    )
    for valuation in candidates:
        if valuation.base_amount is not None:
            return Decimal(str(valuation.base_amount))
        if valuation.currency == valuation.base_currency:
            return Decimal(str(valuation.amount))
        if valuation.fx_rate is not None:
            return Decimal(str(valuation.amount)) * Decimal(str(valuation.fx_rate))
    return sum((movement_delta for movement_delta in (
        (m.principal_amount if m.kind in ("opening", "increase") else -m.principal_amount)
        for m in position.movements
        if m.effective_date <= cutoff and (m.reversed_at is None or m.reversed_at.date() > cutoff)
    )), Decimal("0"))


def _xirr(cashflows: list[tuple[date, Decimal]]) -> Decimal | None:
    if len(cashflows) < 2 or not any(v < 0 for _, v in cashflows) or not any(v > 0 for _, v in cashflows):
        return None
    origin = cashflows[0][0]

    def npv(rate: float) -> float:
        return sum(float(value) / ((1.0 + rate) ** ((when - origin).days / 365.0)) for when, value in cashflows)

    rate = 0.1
    for _ in range(80):
        value = npv(rate)
        derivative = sum(
            -((when - origin).days / 365.0) * float(value_amount)
            / ((1.0 + rate) ** (((when - origin).days / 365.0) + 1))
            for when, value_amount in cashflows
        )
        if abs(derivative) < 1e-12:
            return None
        next_rate = rate - value / derivative
        if next_rate <= -0.9999 or not math.isfinite(next_rate):
            return None
        if abs(next_rate - rate) < 1e-8:
            return Decimal(str(next_rate))
        rate = next_rate
    return None


async def _portfolio_value_at(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    collection: Collection,
    cutoff: date,
) -> Decimal:
    total = Decimal("0")
    for account in collection.accounts:
        if account.is_closed and account.closed_at is not None and account.closed_at.date() <= cutoff:
            continue
        total += await _snapshot_account_balance(session, account, cutoff)
    group_ids = {group.id for group in collection.asset_groups}
    assets = await session.scalars(select(Asset).where(
        Asset.workspace_id == workspace_id,
        Asset.is_archived.is_(False),
        Asset.group_id.in_(group_ids) if group_ids else Asset.id.is_(None),
    ))
    for asset in assets.all():
        value = await session.scalar(
            select(AssetValue.amount).where(
                AssetValue.asset_id == asset.id, AssetValue.date <= cutoff,
            ).order_by(desc(AssetValue.date), desc(AssetValue.id)).limit(1)
        )
        total += Decimal(str(value if value is not None else (asset.purchase_price or 0)))
    position_ids = {position.id for position in collection.positions}
    positions = await session.scalars(select(Position).options(
        selectinload(Position.movements), selectinload(Position.valuations)
    ).where(
        Position.workspace_id == workspace_id,
        Position.is_archived.is_(False),
        Position.id.in_(position_ids) if position_ids else Position.id.is_(None),
    ))
    for position in positions.unique().all():
        value = _position_value_at(position, cutoff)
        total += value if position.side == "receivable" else -value
    return total


async def build_snapshot(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    period: str,
    collection_id: uuid.UUID | None = None,
) -> dict:
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
    collection = await _resolve_investible_collection(session, workspace_id, collection_id)
    collection_account_ids = {account.id for account in collection.accounts} if collection else set()
    collection_wallet_ids = {wallet.id for wallet in collection.asset_groups} if collection else set()
    collection_position_ids = {position.id for position in collection.positions} if collection else set()
    positions = await session.scalars(
        select(Position).options(selectinload(Position.movements), selectinload(Position.valuations)).where(
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
        select(Transaction, Category.treat_as_transfer, Category.is_ignored, Category.accounting_role, Category.is_essential)
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(Transaction.workspace_id == workspace_id, Transaction.date >= start, Transaction.date <= cutoff)
        .order_by(Transaction.date, Transaction.id)
    )
    income = Decimal("0")
    consumption = Decimal("0")
    transfers = Decimal("0")
    ignored = Decimal("0")
    tx_rows = tx_result.all()
    period_transfer_pair_ids = {
        tx.transfer_pair_id for tx, *_ in tx_rows if tx.transfer_pair_id is not None
    }
    for tx, as_transfer, category_ignored, accounting_role, _is_essential in tx_rows:
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
        if accounting_role == "ignored" or category_ignored:
            ignored += amount
            continue
        if accounting_role == "patrimonial" or tx.transfer_pair_id is not None or as_transfer:
            transfers += amount
            continue
        if tx.type == "credit":
            income += amount
        elif tx.type == "debit":
            if accounting_role == "financial_cost":
                consumption += amount
            elif accounting_role == "capitalizable":
                transfers += amount
            else:
                consumption += amount

    account_rows = await session.execute(
        select(Account).where(Account.workspace_id == workspace_id)
    )
    accounts = list(account_rows.scalars().all())
    account_balance = Decimal("0")
    portfolio_account_balance = Decimal("0")
    for account in accounts:
        # A close made after the requested cutoff still belongs in the
        # historical net worth. Accounts closed on or before the cutoff do not.
        if account.is_closed and account.closed_at is not None and account.closed_at.date() <= cutoff:
            continue
        balance = await _snapshot_account_balance(session, account, cutoff)
        account_balance += balance
        if account.id in collection_account_ids:
            portfolio_account_balance += balance

    asset_result = await session.scalars(
        select(Asset).where(Asset.workspace_id == workspace_id, Asset.is_archived == False)
    )
    assets = list(asset_result.all())
    asset_total = Decimal("0")
    portfolio_asset_total = Decimal("0")
    for asset in assets:
        value = await session.scalar(
            select(AssetValue.amount)
            .where(AssetValue.asset_id == asset.id, AssetValue.date <= cutoff)
            .order_by(desc(AssetValue.date), desc(AssetValue.id)).limit(1)
        )
        if value is None:
            value = asset.purchase_price if asset.purchase_date is None or asset.purchase_date <= cutoff else Decimal("0")
        value = Decimal(str(value or 0))
        asset_total += value
        if asset.group_id in collection_wallet_ids:
            portfolio_asset_total += value

    receivables = Decimal("0")
    portfolio_receivables = Decimal("0")
    liabilities = Decimal("0")
    portfolio_liabilities = Decimal("0")
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
        value = _position_value_at(position, cutoff)
        side = position.side
        if side == "receivable":
            principal = Decimal(str(principal or 0))
            receivables += value
            if position.id in collection_position_ids:
                portfolio_receivables += value
        else:
            principal = Decimal(str(principal or 0))
            liabilities += value
            if position.id in collection_position_ids:
                portfolio_liabilities += value

    # A withdrawal only has meaning when the workspace has an explicit
    # investible-portfolio lens. Inferring it from account or asset names would
    # silently misclassify ordinary transfers, so fail closed until that lens
    # is configured.
    portfolio_withdrawals = None
    if collection is not None:
        portfolio_withdrawals = await _collection_withdrawal_net(
            session, workspace_id, collection_account_ids, period_transfer_pair_ids, cutoff, start
        )
    savings_rate = None if income <= 0 else (income - consumption) / income
    # Until the user explicitly configures the investible-portfolio lens, this
    # is a broad proxy over all open accounts/assets. Keep the number available
    # for continuity, but label it so downstream UI and narrators cannot pass
    # it off as a true investible-portfolio balance.
    if collection is not None:
        financial_portfolio_net = (
            portfolio_account_balance
            + portfolio_asset_total
            + portfolio_receivables
            - portfolio_liabilities
        )
        financial_portfolio_quality = {
            "status": "available",
            "reason": "Calculated from accounts, AssetGroups and Positions in the explicitly selected Collection lens.",
            "code": "investible_portfolio_collection",
        }
    else:
        financial_portfolio_net = account_balance + asset_total - liabilities
        financial_portfolio_quality = {
            "status": "provisional",
            "reason": "Investible-portfolio lens is not configured; the current value is a broad proxy over accounts and structural assets.",
            "code": "investible_portfolio_lens_required",
        }

    monthly_values: list[tuple[date, Decimal]] = []
    withdrawal_rate_12m = None
    twr = None
    xirr = None
    if collection is not None:
        cursor_year, cursor_month = year, month
        for _ in range(13):
            month_end = date(cursor_year, cursor_month, monthrange(cursor_year, cursor_month)[1])
            if month_end <= cutoff:
                monthly_values.append((month_end, await _portfolio_value_at(session, workspace_id, collection, month_end)))
            cursor_month -= 1
            if cursor_month == 0:
                cursor_year -= 1
                cursor_month = 12
        monthly_values.sort(key=lambda item: item[0])
        if len(monthly_values) == 13:
            average_13 = sum((value for _, value in monthly_values), Decimal("0")) / Decimal("13")
            twelve_month_start = monthly_values[1][0] + timedelta(days=1)
            twelve_month_withdrawal = await _collection_withdrawal_net(
                session, workspace_id, collection_account_ids, None, cutoff, twelve_month_start
            )
            if average_13 != 0:
                withdrawal_rate_12m = twelve_month_withdrawal / average_13
            period_returns: list[Decimal] = []
            for index in range(1, len(monthly_values)):
                start_value = monthly_values[index - 1][1]
                end_value = monthly_values[index][1]
                month_start = monthly_values[index - 1][0] + timedelta(days=1)
                month_withdrawal = await _collection_withdrawal_net(
                    session, workspace_id, collection_account_ids, None, monthly_values[index][0], month_start
                )
                if start_value != 0:
                    period_returns.append((end_value + month_withdrawal) / start_value - Decimal("1"))
            if len(period_returns) == 12:
                twr_value = Decimal("1")
                for period_return in period_returns:
                    twr_value *= Decimal("1") + period_return
                twr = twr_value - Decimal("1")
            cashflows: list[tuple[date, Decimal]] = [(monthly_values[0][0], -monthly_values[0][1])]
            for index in range(1, len(monthly_values)):
                month_withdrawal = await _collection_withdrawal_net(
                    session, workspace_id, collection_account_ids, None,
                    monthly_values[index][0], monthly_values[index - 1][0],
                )
                cashflows.append((monthly_values[index][0], -month_withdrawal))
            cashflows[-1] = (cashflows[-1][0], cashflows[-1][1] + monthly_values[-1][1])
            xirr = _xirr(cashflows)

    # Reserve coverage is deliberately conservative: only explicitly marked
    # D+0/D+1 assets and non-credit collection accounts count.
    liquidity_coverage = None
    essential_average = None
    if collection is not None:
        recent_months = monthly_values[-3:] if len(monthly_values) >= 3 else monthly_values
        if recent_months:
            recent_start = recent_months[0][0].replace(day=1)
            essential_result = await session.execute(
                select(Transaction, Category.is_essential, Category.accounting_role).join(
                    Category, Transaction.category_id == Category.id
                ).where(
                    Transaction.workspace_id == workspace_id,
                    Transaction.date >= recent_start,
                    Transaction.date <= cutoff,
                    Transaction.type == "debit",
                    Transaction.is_ignored.is_(False),
                    Category.is_essential.is_(True),
                    Category.accounting_role == "consumption",
                )
            )
            essential_total = sum((abs(Decimal(str(tx.amount or 0))) for tx, _, _ in essential_result.all()), Decimal("0"))
            essential_average = essential_total / Decimal(str(max(len(recent_months), 1)))
        eligible_assets = await session.scalars(select(Asset).where(
            Asset.workspace_id == workspace_id,
            Asset.is_archived.is_(False),
            Asset.reserve_eligible.is_(True),
            Asset.liquidity_days <= 1,
            Asset.group_id.in_(collection_wallet_ids) if collection_wallet_ids else Asset.id.is_(None),
        ))
        eligible_value = Decimal("0")
        for asset in eligible_assets.all():
            latest = await session.scalar(select(AssetValue.amount).where(
                AssetValue.asset_id == asset.id, AssetValue.date <= cutoff,
            ).order_by(desc(AssetValue.date), desc(AssetValue.id)).limit(1))
            eligible_value += Decimal(str(latest if latest is not None else (asset.purchase_price or 0)))
        for account in collection.accounts:
            if account.type in {"checking", "savings", "wallet", "cash"}:
                eligible_value += await _snapshot_account_balance(session, account, cutoff)
        if essential_average and essential_average > 0:
            liquidity_coverage = eligible_value / essential_average
    ipca_return, ipca_source = await cumulative_ipca(session, start, cutoff)
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
        "financial_portfolio_collection_id": str(collection.id) if collection else None,
        "financial_portfolio_collection_name": collection.name if collection else None,
        "withdrawal_rate_12m": withdrawal_rate_12m,
        "liquidity_coverage": liquidity_coverage,
        "twr": twr,
        "xirr": xirr,
        "ipca_return": ipca_return,
        "ipca_source": ipca_source,
        "essential_cost_average": essential_average,
        "metric_quality": {
            "portfolio_withdrawal_net": {
                "status": "available" if collection is not None else "unavailable",
                "reason": "Calculated from paired transfers involving the selected Collection accounts."
                if collection is not None
                else "Configure the investible-portfolio lens before classifying withdrawals.",
                "code": "collection_transfer_pairs" if collection is not None else "investible_portfolio_lens_required",
            },
            "withdrawal_rate_12m": {
                "status": "available" if withdrawal_rate_12m is not None else "unavailable",
                "reason": "Calculated from 13 monthly closing values and paired portfolio cash flows."
                if withdrawal_rate_12m is not None else "Requires an investible-portfolio lens and 13 monthly closing values.",
                "code": "monthly_closing_history" if withdrawal_rate_12m is not None else "monthly_closing_history_required",
            },
            "liquidity_coverage": {
                "status": "available" if liquidity_coverage is not None else "unavailable",
                "reason": "Uses essential consumption and explicitly eligible D+0/D+1 balances."
                if liquidity_coverage is not None else "Requires essential categories and eligible D+0/D+1 assets.",
            },
            "twr": {"status": "available" if twr is not None else "unavailable", "reason": "Time-weighted return from monthly closes and external cash flows."},
            "xirr": {"status": "available" if xirr is not None else "unavailable", "reason": "Money-weighted return requires both inflows and outflows."},
            "ipca_return": {
                "status": "available" if ipca_return is not None else "unavailable",
                "reason": "Cumulative official IPCA over the requested period." if ipca_return is not None else "Official IPCA observations have not been loaded for this period.",
                "source": ipca_source,
            },
            "financial_portfolio_net": financial_portfolio_quality,
        },
        "methodology": {
            "source": "Securo ledger, account balances, asset valuations and Position ledger",
            "period_policy": "Only transactions dated inside the requested month and available by the cutoff; future rows are excluded",
            "cutoff_policy": "The latest workspace synchronization limits the effective cutoff",
            "savings_rate": "null when economic income is not positive",
            "principal_withdrawals": "excluded from economic income; linked cash legs are patrimonial transfers",
            "position_result": "interest increases/lowers result by side; fees and taxes are costs",
            "financial_portfolio_net": "calculated from the selected investible-portfolio Collection"
            if collection is not None
            else "provisional proxy until the investible-portfolio Collection is configured",
        },
    }
