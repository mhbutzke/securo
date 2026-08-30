from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class CreditCardExposure(BaseModel):
    account_id: str
    as_of: date
    currency: str
    closed_bill_unpaid: Decimal
    open_bill: Decimal
    committed_debt: Decimal
    after_current_bill: Decimal
    known_future_installments: Decimal
    unbilled_authorized: Decimal
    payments_credits_refunds: Decimal
    credit_limit: Optional[Decimal] = None
    available_credit: Optional[Decimal] = None
    current_bill_due_date: Optional[date] = None
    source: str
    basis: str
    confidence: str
