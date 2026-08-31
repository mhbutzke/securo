"""Audit existing rules and disable unsafe definitions before previews."""

from __future__ import annotations

import asyncio
import re

from sqlalchemy import select

from app.core.database import async_session_maker
from app.models import Rule, Transaction, Workspace
from app.services.rule_engine import evaluate_conditions


UNSAFE_ACTIONS = {"set_payee", "set_description", "append_notes", "ignore"}
RESTRICTIVE_FIELDS = {"account_id", "payee_id"}


async def main():
    async with async_session_maker() as session:
        workspace = await session.scalar(select(Workspace).where(Workspace.name == "Pessoal"))
        if workspace is None:
            raise RuntimeError("workspace not found")
        transactions = list(
            (
                await session.execute(
                    select(Transaction).where(Transaction.workspace_id == workspace.id)
                )
            ).scalars().all()
        )
        rules = list(
            (
                await session.execute(select(Rule).where(Rule.workspace_id == workspace.id))
            ).scalars().all()
        )
        disabled: list[dict] = []
        for rule in rules:
            actions = rule.actions or []
            action_ops = {action.get("op") for action in actions if isinstance(action, dict)}
            leaves = []
            for node in rule.conditions or []:
                nested = node.get("conditions") if isinstance(node, dict) else None
                leaves.extend(nested if isinstance(nested, list) else [node])
            values = " ".join(str(node.get("value", "")) for node in leaves if isinstance(node, dict))
            fields = {node.get("field") for node in leaves if isinstance(node, dict)}
            hits = sum(
                1
                for tx in transactions
                if evaluate_conditions(rule.conditions_op, rule.conditions or [], tx)
            )
            broad = bool(transactions) and hits / len(transactions) > 0.05 and not fields.intersection(RESTRICTIVE_FIELDS)
            reasons = []
            if action_ops.intersection(UNSAFE_ACTIONS):
                reasons.append("non_category_action")
            if re.search(r"\b\d{11,14}\b", values):
                reasons.append("clear_identifier_condition")
            if broad:
                reasons.append("broad_gt_5_percent")
            if reasons and rule.is_active:
                rule.is_active = False
                disabled.append({"rule_id": str(rule.id), "reason": reasons, "hits": hits})
        await session.commit()
        print({"workspace": workspace.name, "rules_audited": len(rules), "rules_disabled": len(disabled), "disabled": disabled})


if __name__ == "__main__":
    asyncio.run(main())
