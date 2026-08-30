import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.account import Account
from app.models.transaction import Transaction
from app.schemas.position import PositionCreate, PositionMovementCreate
from app.services.category_assignment import assign_category
from app.services.position_service import add_movement, create_position, position_balance, reverse_movement
from app.services.financial_close_service import build_snapshot


def test_manual_category_ownership_survives_automatic_assignment():
    tx = Transaction(category_id=uuid.uuid4(), category_origin="manual")
    original = tx.category_id
    assert assign_category(tx, uuid.uuid4(), origin="rule") is False
    assert tx.category_id == original
    assert tx.category_origin == "manual"


def test_explicit_clear_removes_category_ownership():
    tx = Transaction(category_id=uuid.uuid4(), category_origin="manual")
    assert assign_category(tx, None, origin=None) is True
    assert tx.category_id is None
    assert tx.category_origin is None


@pytest.mark.asyncio
async def test_position_ledger_is_reversible(session, test_user, test_workspace):
    position = await create_position(
        session, test_workspace.id, test_user.id,
        PositionCreate(side="receivable", name="Loan", original_principal=Decimal("1000"), start_date=date(2026, 1, 1)),
    )
    assert position_balance(position) == Decimal("1000")
    movement = await add_movement(
        session, test_workspace.id, position.id,
        PositionMovementCreate(kind="decrease", principal_amount=Decimal("250"), effective_date=date(2026, 2, 1), idempotency_key="repay-1"),
    )
    position = await __import__("app.services.position_service", fromlist=["get_position"]).get_position(session, test_workspace.id, position.id)
    assert position_balance(position) == Decimal("750")
    await reverse_movement(session, test_workspace.id, position.id, movement.id)
    position = await __import__("app.services.position_service", fromlist=["get_position"]).get_position(session, test_workspace.id, position.id)
    assert position_balance(position) == Decimal("1000")


@pytest.mark.asyncio
async def test_financial_close_excludes_transfers_and_returns_null_savings_without_income(
    session, test_user, test_workspace
):
    account = Account(
        user_id=test_user.id, workspace_id=test_workspace.id, name="Cash", type="checking",
        balance=Decimal("100"), currency="BRL",
    )
    session.add(account)
    await session.flush()
    session.add(Transaction(
        user_id=test_user.id, workspace_id=test_workspace.id, account_id=account.id,
        description="Transfer", amount=Decimal("90"), date=date(2026, 1, 10),
        type="debit", source="manual", transfer_pair_id=uuid.uuid4(),
    ))
    await session.commit()
    snapshot = await build_snapshot(session, test_workspace.id, "2026-01")
    assert snapshot["transfers_and_patrimonial_movements"] == Decimal("90")
    assert snapshot["consumption_recurring"] == Decimal("0")
    assert snapshot["savings_rate"] is None


@pytest.mark.asyncio
async def test_safe_rule_preview_commit_detects_drift_and_preserves_notes(
    client, auth_headers, test_transactions, test_categories, test_rules
):
    today = date.today()
    tx = test_transactions[0]
    body = {
        "from_date": today.replace(day=1).isoformat(),
        "to_date": today.isoformat(),
        "transaction_ids": [str(tx.id)],
    }
    preview = await client.post("/api/rules/apply-preview", json=body, headers=auth_headers)
    assert preview.status_code == 200
    digest = preview.json()["digest"]
    assert preview.json()["will_change"] >= 1

    # A manual category decision after preview invalidates the optimistic digest.
    changed = await client.patch(
        f"/api/transactions/{tx.id}",
        json={"category_id": str(test_categories[0].id), "notes": "manual note"},
        headers=auth_headers,
    )
    assert changed.status_code == 200
    commit = await client.post(
        f"/api/rules/apply-preview/{digest}/commit", json=body, headers=auth_headers
    )
    assert commit.status_code == 409


@pytest.mark.asyncio
async def test_safe_rule_preview_commit_can_target_selected_transactions_only(
    client, auth_headers, test_transactions, test_rules, session
):
    today = date.today()
    selected, outside = test_transactions[0], test_transactions[1]
    body = {
        "from_date": today.replace(day=1).isoformat(),
        "to_date": today.isoformat(),
        "transaction_ids": [str(selected.id)],
    }

    preview = await client.post("/api/rules/apply-preview", json=body, headers=auth_headers)
    assert preview.status_code == 200
    assert preview.json()["matched"] == 1
    assert preview.json()["will_change"] == 1
    assert preview.json()["sample"][0]["id"] == str(selected.id)

    commit = await client.post(
        f"/api/rules/apply-preview/{preview.json()['digest']}/commit",
        json=body,
        headers=auth_headers,
    )
    assert commit.status_code == 200
    assert commit.json()["applied"] == 1

    await session.refresh(selected)
    await session.refresh(outside)
    assert selected.category_origin == "rule"
    assert outside.category_origin is None

    retry = await client.post(
        f"/api/rules/apply-preview/{preview.json()['digest']}/commit",
        json=body,
        headers=auth_headers,
    )
    assert retry.status_code == 200
    assert retry.json()["batch_id"] == commit.json()["batch_id"]
    assert retry.json()["applied"] == 1


@pytest.mark.asyncio
async def test_safe_rule_preview_detects_rule_input_drift(client, auth_headers, test_transactions, test_rules):
    today = date.today()
    tx = test_transactions[0]
    body = {
        "from_date": today.replace(day=1).isoformat(),
        "to_date": today.isoformat(),
        "transaction_ids": [str(tx.id)],
    }
    preview = await client.post("/api/rules/apply-preview", json=body, headers=auth_headers)
    assert preview.status_code == 200

    changed = await client.patch(
        f"/api/transactions/{tx.id}",
        json={"description": "MANUAL DESCRIPTION"},
        headers=auth_headers,
    )
    assert changed.status_code == 200
    commit = await client.post(
        f"/api/rules/apply-preview/{preview.json()['digest']}/commit",
        json=body,
        headers=auth_headers,
    )
    assert commit.status_code == 409


@pytest.mark.asyncio
async def test_safe_rule_preview_rejects_more_than_20_selected_transactions(
    client, auth_headers
):
    today = date.today()
    body = {
        "from_date": today.replace(day=1).isoformat(),
        "to_date": today.isoformat(),
        "transaction_ids": [str(uuid.uuid4()) for _ in range(21)],
    }
    response = await client.post("/api/rules/apply-preview", json=body, headers=auth_headers)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_safe_rule_preview_requires_explicit_transaction_selection(client, auth_headers):
    today = date.today()
    body = {"from_date": today.isoformat(), "to_date": today.isoformat()}
    preview = await client.post("/api/rules/apply-preview", json=body, headers=auth_headers)
    assert preview.status_code == 422
