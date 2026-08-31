"""Create a non-binding next-month budget from three clean months."""

from __future__ import annotations

import asyncio
from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select

from app.core.database import async_session_maker
from app.models import Budget, Category, Transaction, Workspace


CUTOFF = date(2026, 8, 31)


async def main():
    async with async_session_maker() as session:
        workspace = await session.scalar(select(Workspace).where(Workspace.name == "Pessoal"))
        if workspace is None:
            raise RuntimeError("workspace not found")
        categories = list(
            (
                await session.execute(
                    select(Category).where(
                        Category.workspace_id == workspace.id,
                        Category.accounting_role == "consumption",
                        Category.is_hidden.is_(False),
                    )
                )
            ).scalars().all()
        )
        months = []
        year, month = CUTOFF.year, CUTOFF.month
        for _ in range(3):
            month -= 1
            if month == 0:
                year -= 1
                month = 12
            months.append(date(year, month, 1))
        target = date(CUTOFF.year + (1 if CUTOFF.month == 12 else 0), 1 if CUTOFF.month == 12 else CUTOFF.month + 1, 1)
        created = 0
        for category in categories:
            values = []
            for month_start in months:
                month_end = date(month_start.year, month_start.month, monthrange(month_start.year, month_start.month)[1])
                rows = (
                    await session.execute(
                        select(Transaction.amount).where(
                            Transaction.workspace_id == workspace.id,
                            Transaction.category_id == category.id,
                            Transaction.type == "debit",
                            Transaction.is_ignored.is_(False),
                            Transaction.date >= month_start,
                            Transaction.date <= month_end,
                        )
                    )
                ).scalars().all()
                values.append(sum((abs(Decimal(str(value or 0))) for value in rows), Decimal("0")))
            average = (sum(values, Decimal("0")) / Decimal("3")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if average <= 0:
                continue
            existing = await session.scalar(
                select(Budget).where(
                    Budget.workspace_id == workspace.id,
                    Budget.category_id == category.id,
                    Budget.month == target,
                    Budget.is_recurring.is_(True),
                )
            )
            if existing is None:
                session.add(
                    Budget(
                        user_id=workspace.created_by_user_id,
                        workspace_id=workspace.id,
                        category_id=category.id,
                        amount=average,
                        month=target,
                        is_recurring=True,
                        is_draft=True,
                    )
                )
                created += 1
        await session.commit()
        print({"workspace": workspace.name, "period": target.isoformat(), "draft_budgets_created": created})


if __name__ == "__main__":
    asyncio.run(main())
