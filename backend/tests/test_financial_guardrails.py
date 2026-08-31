import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.category import Category
from app.models.credit_card_bill import CreditCardBill
from app.models.position import Position, PositionMovement
from app.models.transaction import Transaction
from app.schemas.position import PositionCreate, PositionMovementCreate
from app.services.category_assignment import assign_category
from app.services.position_service import add_movement, create_position, position_balance, reverse_movement
from app.services.financial_close_service import build_snapshot
from app.services.credit_card_exposure_service import get_exposure
from app.services.period_cutoff import resolve_workspace_cutoff


@pytest.mark.asyncio
async def test_financial_close_route_requires_auth_and_returns_contract(
    client, auth_headers, test_workspace
):
    unauthenticated = await client.get(
        "/api/reports/financial-close", params={"period": "2026-01"}
    )
    assert unauthenticated.status_code == 401

    response = await client.get(
        "/api/reports/financial-close",
        params={"period": "2026-01"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["period"] == "2026-01"
    assert payload["cutoff_date"]
    assert payload["requested_period_end"] == "2026-01-31"
    assert payload["metric_quality"]["withdrawal_rate_12m"]["status"] == "unavailable"
    assert payload["metric_quality"]["financial_portfolio_net"]["status"] == "provisional"
    assert "period_policy" in payload["methodology"]


@pytest.mark.asyncio
async def test_financial_review_queue_is_aggregate_first_and_read_only(
    client, auth_headers, session, test_user, test_workspace, test_account
):
    transfer_category = Category(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Aplicações",
        treat_as_transfer=True,
    )
    session.add(transfer_category)
    await session.flush()
    before = test_account.balance
    session.add_all([
        Transaction(
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=test_account.id,
            description="High value pending",
            amount=Decimal("5000"),
            currency="BRL",
            date=date(2026, 1, 5),
            type="debit",
            source="manual",
        ),
        Transaction(
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=test_account.id,
            description="Small pending",
            amount=Decimal("50"),
            date=date(2026, 1, 6),
            type="debit",
            source="manual",
        ),
        Transaction(
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=test_account.id,
            category_id=transfer_category.id,
            category_origin="rule",
            description="Investment application",
            amount=Decimal("300"),
            date=date(2026, 1, 7),
            type="debit",
            source="manual",
        ),
        Transaction(
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=test_account.id,
            category_id=transfer_category.id,
            description="Pending categorized charge",
            amount=Decimal("20"),
            date=date(2026, 1, 10),
            type="debit",
            status="pending",
            source="manual",
        ),
        Transaction(
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=test_account.id,
            category_id=transfer_category.id,
            transfer_pair_id=uuid.uuid4(),
            description="Paired own transfer",
            amount=Decimal("700"),
            date=date(2026, 1, 8),
            type="debit",
            source="manual",
        ),
        Transaction(
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=test_account.id,
            is_ignored=True,
            description="Ignored item",
            amount=Decimal("80"),
            date=date(2026, 1, 9),
            type="debit",
            source="manual",
        ),
        Transaction(
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=test_account.id,
            description="Future forecast",
            amount=Decimal("9999"),
            date=date.today() + timedelta(days=1),
            type="debit",
            source="manual",
        ),
        Transaction(
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=test_account.id,
            description="Opening balance",
            amount=Decimal("10000"),
            date=date(2026, 1, 1),
            type="credit",
            source="opening_balance",
        ),
    ])
    await session.commit()

    local_today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    response = await client.get(
        "/api/reports/financial-review-queue",
        params={"from_date": "2026-01-01", "to_date": (local_today + timedelta(days=30)).isoformat()},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["cutoff_date"] == local_today.isoformat()
    assert payload["total_count"] == 5
    assert payload["summaries"]["high_value"]["count"] == 1
    assert payload["summaries"]["uncategorized"]["count"] == 3
    assert payload["summaries"]["third_party_transfers"]["count"] == 2
    assert payload["summaries"]["ignored"]["count"] == 1
    assert payload["summaries"]["pending"]["count"] == 1
    assert payload["summaries"]["rule_managed"]["count"] == 1
    assert payload["items"][0]["description"] == "High value pending"
    assert all(item["description"] != "Future forecast" for item in payload["items"])
    assert all(item["description"] != "Opening balance" for item in payload["items"])
    assert test_account.balance == before

    limited = await client.get(
        "/api/reports/financial-review-queue",
        params={
            "from_date": "2026-01-01",
            "to_date": local_today.isoformat(),
            "queue": "high_value",
            "limit": 20,
        },
        headers=auth_headers,
    )
    assert limited.status_code == 200
    assert len(limited.json()["items"]) == 1

    pending = await client.get(
        "/api/reports/financial-review-queue",
        params={
            "from_date": "2026-01-01",
            "to_date": local_today.isoformat(),
            "queue": "pending",
        },
        headers=auth_headers,
    )
    assert pending.status_code == 200
    assert pending.json()["total_count"] == 1


@pytest.mark.asyncio
async def test_financial_review_queue_is_workspace_scoped(
    client, auth_headers, session, test_user, test_workspace
):
    from app.models.workspace import Workspace

    other_workspace = Workspace(
        id=uuid.uuid4(),
        name="Outra casa",
        kind="personal",
        created_by_user_id=test_user.id,
        default_currency="BRL",
        locale="pt-BR",
    )
    session.add(other_workspace)
    await session.flush()
    other_account = Account(
        user_id=test_user.id,
        workspace_id=other_workspace.id,
        name="Outra conta",
        type="checking",
        balance=Decimal("0"),
        currency="BRL",
    )
    session.add(other_account)
    await session.flush()
    session.add(Transaction(
        user_id=test_user.id,
        workspace_id=other_workspace.id,
        account_id=other_account.id,
        description="Other workspace",
        amount=Decimal("10000"),
        date=date(2026, 1, 2),
        type="debit",
        source="manual",
    ))
    await session.commit()

    response = await client.get(
        "/api/reports/financial-review-queue",
        params={"from_date": "2026-01-01", "to_date": "2026-01-31"},
        headers={**auth_headers, "X-Workspace-Id": str(test_workspace.id)},
    )
    assert response.status_code == 200
    assert response.json()["total_count"] == 0


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
    assert snapshot["portfolio_withdrawal_net"] is None
    assert snapshot["metric_quality"]["portfolio_withdrawal_net"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_financial_close_uses_latest_sync_as_cutoff(session, test_user, test_workspace):
    account = Account(
        user_id=test_user.id, workspace_id=test_workspace.id, name="Cash", type="checking",
        balance=Decimal("0"), currency="BRL",
    )
    session.add(account)
    session.add(BankConnection(
        user_id=test_user.id, workspace_id=test_workspace.id, provider="pluggy",
        external_id="item-1", institution_name="Test bank",
        last_sync_at=datetime(2026, 1, 15, 12, tzinfo=timezone.utc),
    ))
    account.is_closed = True
    account.closed_at = datetime(2026, 1, 20, tzinfo=timezone.utc)
    await session.flush()
    session.add_all([
        Transaction(
            user_id=test_user.id, workspace_id=test_workspace.id, account_id=account.id,
            description="Before cutoff", amount=Decimal("10"), date=date(2026, 1, 10),
            type="debit", source="manual",
        ),
        Transaction(
            user_id=test_user.id, workspace_id=test_workspace.id, account_id=account.id,
            description="After cutoff", amount=Decimal("100"), date=date(2026, 1, 20),
            type="debit", source="manual",
        ),
    ])
    await session.commit()

    snapshot = await build_snapshot(session, test_workspace.id, "2026-01")

    assert snapshot["cutoff_date"] == "2026-01-15"
    assert snapshot["cutoff_source"] == "last_sync"
    assert snapshot["sync_is_stale"] is True
    assert snapshot["consumption_recurring"] == Decimal("10")
    assert snapshot["account_balance"] == Decimal("-10")


@pytest.mark.asyncio
async def test_cutoff_uses_workspace_timezone_at_utc_midnight(session, test_user, test_workspace):
    session.add(BankConnection(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="pluggy",
        external_id="item-timezone",
        institution_name="Test bank",
        last_sync_at=datetime(2026, 2, 1, 2, 30, tzinfo=timezone.utc),
    ))
    await session.commit()

    cutoff = await resolve_workspace_cutoff(session, test_workspace.id, date(2026, 2, 1))

    assert cutoff.cutoff_date == date(2026, 1, 31)
    assert cutoff.source == "last_sync"


@pytest.mark.asyncio
async def test_financial_close_excludes_position_movements_after_cutoff(session, test_user, test_workspace):
    position = Position(
        user_id=test_user.id, workspace_id=test_workspace.id, side="receivable",
        name="Receivable", currency="BRL", original_principal=Decimal("100"),
        start_date=date(2026, 1, 1), liquidity="illiquid", status="open",
    )
    session.add(position)
    await session.flush()
    session.add_all([
        PositionMovement(
            position_id=position.id, kind="opening", principal_amount=Decimal("100"),
            effective_date=date(2026, 1, 1), idempotency_key="opening",
        ),
        PositionMovement(
            position_id=position.id, kind="increase", principal_amount=Decimal("50"),
            effective_date=date(2026, 2, 1), idempotency_key="increase",
        ),
    ])
    await session.commit()

    snapshot = await build_snapshot(session, test_workspace.id, "2026-01")

    assert snapshot["receivables"] == Decimal("100")


@pytest.mark.asyncio
async def test_financial_close_keeps_position_until_reversal_date(session, test_user, test_workspace):
    position = Position(
        user_id=test_user.id, workspace_id=test_workspace.id, side="receivable",
        name="Receivable", currency="BRL", original_principal=Decimal("100"),
        start_date=date(2026, 1, 1), liquidity="illiquid", status="open",
    )
    session.add(position)
    await session.flush()
    session.add(PositionMovement(
        position_id=position.id, kind="opening", principal_amount=Decimal("100"),
        effective_date=date(2026, 1, 1), idempotency_key="opening",
        reversed_at=datetime(2026, 2, 2, tzinfo=timezone.utc),
    ))
    await session.commit()

    january = await build_snapshot(session, test_workspace.id, "2026-01")
    february = await build_snapshot(session, test_workspace.id, "2026-02")

    assert january["receivables"] == Decimal("100")
    assert february["receivables"] == Decimal("0")


@pytest.mark.asyncio
async def test_financial_close_keeps_card_refund_as_income(session, test_user, test_workspace):
    account = Account(
        user_id=test_user.id, workspace_id=test_workspace.id, name="Card", type="credit_card",
        balance=Decimal("0"), currency="BRL",
    )
    session.add(account)
    await session.flush()
    bill = CreditCardBill(
        user_id=test_user.id, workspace_id=test_workspace.id, account_id=account.id,
        external_id="bill-1", due_date=date(2026, 1, 31), total_amount=Decimal("20"),
    )
    session.add(bill)
    await session.flush()
    session.add(Transaction(
        user_id=test_user.id, workspace_id=test_workspace.id, account_id=account.id,
        bill_id=bill.id, description="Bill credit", amount=Decimal("20"),
        date=date(2026, 1, 20), type="credit", source="pluggy",
    ))
    await session.commit()

    snapshot = await build_snapshot(session, test_workspace.id, "2026-01")

    assert snapshot["income_economic"] == Decimal("20")
    assert snapshot["transfers_and_patrimonial_movements"] == Decimal("0")


@pytest.mark.asyncio
async def test_credit_card_exposure_separates_current_and_future_commitments(
    session, test_user, test_workspace
):
    today = date.today()
    account = Account(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Exposure card",
        type="credit_card",
        balance=Decimal("-500.00"),
        currency="BRL",
        credit_limit=Decimal("1000.00"),
    )
    session.add(account)
    await session.flush()
    session.add_all([
        CreditCardBill(
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=account.id,
            external_id="exposure-closed",
            due_date=today - timedelta(days=10),
            total_amount=Decimal("200.00"),
        ),
        CreditCardBill(
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=account.id,
            external_id="exposure-open",
            due_date=today + timedelta(days=10),
            total_amount=Decimal("100.00"),
        ),
        Transaction(
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=account.id,
            description="Future installment",
            amount=Decimal("50.00"),
            currency="BRL",
            date=today,
            effective_date=today + timedelta(days=1),
            type="debit",
            source="pluggy",
            installment_number=1,
            total_installments=3,
        ),
        Transaction(
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=account.id,
            description="Unbilled authorization",
            amount=Decimal("30.00"),
            currency="BRL",
            date=today,
            effective_date=today,
            type="debit",
            source="pluggy",
            status="pending",
        ),
        Transaction(
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=account.id,
            description="Refund",
            amount=Decimal("20.00"),
            currency="BRL",
            date=today,
            effective_date=today,
            type="credit",
            source="pluggy",
        ),
    ])
    await session.commit()

    exposure = await get_exposure(session, test_workspace.id, account.id)

    assert exposure is not None
    assert exposure["closed_bill_unpaid"] == Decimal("200.00")
    assert exposure["open_bill"] == Decimal("100.00")
    assert exposure["committed_debt"] == Decimal("500.00")
    assert exposure["after_current_bill"] == Decimal("200.00")
    assert exposure["known_future_installments"] == Decimal("50.00")
    assert exposure["unbilled_authorized"] == Decimal("30.00")
    assert exposure["payments_credits_refunds"] == Decimal("20.00")
    assert exposure["available_credit"] == Decimal("500.00")


@pytest.mark.asyncio
async def test_credit_card_exposure_uses_conservative_sync_cutoff(
    session, test_user, test_workspace
):
    connection = BankConnection(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="pluggy",
        external_id="stale-card-item",
        institution_name="Stale bank",
        status="active",
        # Midday UTC keeps the calendar date stable in the workspace's
        # America/Sao_Paulo timezone.
        last_sync_at=datetime(2026, 1, 10, 15, tzinfo=timezone.utc),
    )
    session.add(connection)
    await session.flush()
    account = Account(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=connection.id,
        name="Stale exposure card",
        type="credit_card",
        balance=Decimal("-100.00"),
        currency="BRL",
    )
    session.add(account)
    await session.flush()
    session.add_all([
        CreditCardBill(
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=account.id,
            external_id="stale-closed",
            due_date=date(2026, 1, 5),
            total_amount=Decimal("40.00"),
        ),
        CreditCardBill(
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=account.id,
            external_id="stale-open",
            due_date=date(2026, 1, 15),
            total_amount=Decimal("60.00"),
        ),
    ])
    await session.commit()

    exposure = await get_exposure(session, test_workspace.id, account.id)

    assert exposure is not None
    assert exposure["as_of"] == date(2026, 1, 10)
    assert exposure["closed_bill_unpaid"] == Decimal("40.00")
    assert exposure["open_bill"] == Decimal("60.00")


@pytest.mark.asyncio
async def test_financial_close_clamps_future_period_without_sync(session, test_user, test_workspace):
    account = Account(
        user_id=test_user.id, workspace_id=test_workspace.id, name="Cash", type="checking",
        balance=Decimal("0"), currency="BRL",
    )
    session.add(account)
    session.add(BankConnection(
        user_id=test_user.id, workspace_id=test_workspace.id, provider="pluggy",
        external_id="item-no-sync", institution_name="Test bank", status="active",
    ))
    await session.flush()
    future_date = date.today() + timedelta(days=30)
    session.add(Transaction(
        user_id=test_user.id, workspace_id=test_workspace.id, account_id=account.id,
        description="Future", amount=Decimal("100"), date=future_date,
        type="debit", source="manual",
    ))
    await session.commit()

    snapshot = await build_snapshot(session, test_workspace.id, future_date.strftime("%Y-%m"))

    assert snapshot["cutoff_source"] == "no_sync"
    assert snapshot["sync_is_stale"] is True
    assert snapshot["consumption_recurring"] == Decimal("0")


@pytest.mark.asyncio
async def test_financial_close_separates_position_principal_and_result(session, test_user, test_workspace):
    account = Account(
        user_id=test_user.id, workspace_id=test_workspace.id, name="Cash", type="checking",
        balance=Decimal("0"), currency="BRL",
    )
    session.add(account)
    await session.flush()
    tx = Transaction(
        user_id=test_user.id, workspace_id=test_workspace.id, account_id=account.id,
        description="Loan advance", amount=Decimal("105"), date=date(2026, 1, 10),
        type="debit", source="manual",
    )
    session.add(tx)
    await session.flush()
    position = Position(
        user_id=test_user.id, workspace_id=test_workspace.id, side="receivable",
        name="Loan", currency="BRL", original_principal=Decimal("100"),
        start_date=date(2026, 1, 10), liquidity="illiquid", status="open",
    )
    session.add(position)
    await session.flush()
    session.add(PositionMovement(
        position_id=position.id, kind="opening", principal_amount=Decimal("100"),
        cash_amount=Decimal("105"), interest_amount=Decimal("5"),
        effective_date=date(2026, 1, 10), idempotency_key="loan-advance", transaction_id=tx.id,
    ))
    await session.commit()

    snapshot = await build_snapshot(session, test_workspace.id, "2026-01")

    assert snapshot["transfers_and_patrimonial_movements"] == Decimal("105")
    assert snapshot["income_economic"] == Decimal("5")
    assert snapshot["position_interest_income"] == Decimal("5")
    assert snapshot["consumption_recurring"] == Decimal("0")

    following_month = await build_snapshot(session, test_workspace.id, "2026-02")
    assert following_month["position_interest_income"] == Decimal("0")


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
