"""Emit a non-PII before/after manifest for the financial close window."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select, text

from app.core.database import async_session_maker
from app.models import Account, Asset, Budget, Category, Collection, CreditCardBill, Position, Rule, Transaction, Workspace


async def main():
    async with async_session_maker() as session:
        workspace = await session.scalar(select(Workspace).where(Workspace.name == "Pessoal"))
        if workspace is None:
            raise RuntimeError("workspace not found")
        wid = workspace.id
        def count(model):
            return session.scalar(select(func.count()).select_from(model).where(model.workspace_id == wid))
        tx_count = await count(Transaction)
        tx_total = await session.scalar(select(func.coalesce(func.sum(func.abs(Transaction.amount)), 0)).where(Transaction.workspace_id == wid))
        pending = await session.scalar(select(func.count()).where(Transaction.workspace_id == wid, Transaction.status == "pending"))
        high_value = await session.scalar(select(func.count()).where(Transaction.workspace_id == wid, func.abs(Transaction.amount) >= 1000, Transaction.date >= date(2026, 1, 1)))
        origins = (await session.execute(select(Transaction.category_origin, func.count()).where(Transaction.workspace_id == wid).group_by(Transaction.category_origin))).all()
        schema = await session.scalar(text("select version_num from alembic_version"))
        result = {
            "workspace": workspace.name,
            "workspace_id": str(wid),
            "cutoff_policy": "latest successful bank synchronization",
            "latest_sync_at": str(await session.scalar(text("select max(last_sync_at) from bank_connections where workspace_id=:wid"), {"wid": wid})),
            "schema": schema,
            "git_revision": os.environ.get("SECURO_GIT_REVISION", "runtime-image"),
            "accounts": await count(Account),
            "transactions": tx_count,
            "transaction_abs_total": str(Decimal(str(tx_total or 0))),
            "pending_transactions": pending,
            "high_value_2026": high_value,
            "invoices": await count(CreditCardBill),
            "assets_active": await session.scalar(select(func.count()).select_from(Asset).where(Asset.workspace_id == wid, Asset.is_archived.is_(False))),
            "assets_archived": await session.scalar(select(func.count()).select_from(Asset).where(Asset.workspace_id == wid, Asset.is_archived.is_(True))),
            "positions": await count(Position),
            "rules": await count(Rule),
            "budgets": await count(Budget),
            "categories": await count(Category),
            "collections": await count(Collection),
            "category_origins": {str(origin): amount for origin, amount in origins},
        }
        print(json.dumps(result, sort_keys=True, default=str))


if __name__ == "__main__":
    asyncio.run(main())
