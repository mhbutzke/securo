"""Normalize credit-card bill states without changing monetary totals."""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.core.database import async_session_maker
from app.models import CreditCardBill, Workspace


TODAY = date(2026, 8, 31)


async def main():
    async with async_session_maker() as session:
        workspace = await session.scalar(select(Workspace).where(Workspace.name == "Pessoal"))
        bills = list(
            (
                await session.execute(
                    select(CreditCardBill).where(CreditCardBill.workspace_id == workspace.id)
                )
            ).scalars().all()
        ) if workspace else []
        changed = 0
        for bill in bills:
            paid = Decimal(str(bill.paid_amount or 0))
            total = Decimal(str(bill.total_amount or 0))
            if paid >= total and total > 0:
                state = "paid"
            elif bill.due_date < TODAY:
                state = "overdue"
            elif bill.closed_at is not None or bill.due_date <= TODAY:
                state = "closed"
            else:
                state = "open"
            if bill.status != state:
                bill.status = state
                changed += 1
        await session.commit()
        print({"workspace": workspace.name if workspace else None, "bills": len(bills), "normalized": changed})


if __name__ == "__main__":
    asyncio.run(main())
