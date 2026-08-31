from datetime import date
from decimal import Decimal
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.credit_card_bill import CreditCardBill
from app.models.transaction import Transaction
from app.services.period_cutoff import resolve_workspace_cutoff


async def get_exposure(session: AsyncSession, workspace_id: uuid.UUID, account_id: uuid.UUID):
    account = await session.scalar(select(Account).where(Account.id == account_id, Account.workspace_id == workspace_id))
    if account is None:
        return None
    if account.type != "credit_card":
        return None
    cutoff_info = await resolve_workspace_cutoff(session, workspace_id, date.today())
    as_of = cutoff_info.cutoff_date
    bills = list((await session.execute(select(CreditCardBill).where(CreditCardBill.account_id == account_id).order_by(CreditCardBill.due_date))).scalars().all())
    closed = [b for b in bills if b.due_date <= as_of]
    open_bill = next((b for b in bills if b.due_date > as_of), None)
    # The issuer balance is the authoritative committed debt when present.
    committed = max(-Decimal(str(account.balance)), Decimal("0"))
    closed_total = sum((Decimal(str(b.total_amount)) for b in closed), Decimal("0"))
    open_total = Decimal(str(open_bill.total_amount)) if open_bill else Decimal("0")
    after_current = max(committed - closed_total - open_total, Decimal("0"))
    future_installments = await session.scalar(
        select(func.coalesce(func.sum(func.abs(Transaction.amount)), 0)).where(
            Transaction.account_id == account_id,
            Transaction.workspace_id == workspace_id,
            Transaction.total_installments.is_not(None),
            Transaction.installment_number < Transaction.total_installments,
            Transaction.type == "debit",
            Transaction.status != "cancelled",
            # Only parcels whose cash-flow date is still ahead are future
            # exposure. Historical installments remain part of the issuer
            # balance/bill reconciliation and must not be counted twice.
            func.coalesce(Transaction.effective_date, Transaction.date) > as_of,
        )
    )
    unbilled = await session.scalar(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.account_id == account_id,
            Transaction.workspace_id == workspace_id,
            Transaction.bill_id.is_(None),
            Transaction.type == "debit",
            Transaction.status == "pending",
        )
    )
    credits = await session.scalar(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.account_id == account_id,
            Transaction.workspace_id == workspace_id,
            Transaction.type == "credit",
        )
    )
    return {
        "account_id": str(account_id), "as_of": as_of, "currency": account.currency,
        "closed_bill_unpaid": closed_total, "open_bill": open_total,
        "committed_debt": committed, "after_current_bill": after_current,
        "known_future_installments": Decimal(str(future_installments or 0)),
        "unbilled_authorized": Decimal(str(unbilled or 0)),
        "payments_credits_refunds": Decimal(str(credits or 0)),
        "credit_limit": account.credit_limit, "available_credit": (
            account.credit_limit - committed if account.credit_limit is not None else None
        ),
        "current_bill_due_date": open_bill.due_date if open_bill else None,
        "source": "issuer_balance+bills+ledger", "basis": "account balance and linked bills",
        "confidence": "high" if bills else "medium",
    }
