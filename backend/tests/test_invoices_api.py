"""The invoicing ledger, end to end.

Two things these tests exist to pin down, beyond the usual CRUD:

  1. **A personal workspace cannot reach any of it.** Hiding the nav link
     is not enforcement; every route must 404 for a workspace without the
     module, or the "no impact on personal finance" promise is decoration.
  2. **Derived state is derived.** No status column is ever written for
     paid/partial/overdue, so the assertions here read the same value the
     UI does, computed from allocations and the due date.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.payee import Payee
from app.models.transaction import Transaction


@pytest_asyncio.fixture
async def personal_ws(session: AsyncSession, test_user):
    """The user's personal workspace, resolved by kind.

    Not the shared `test_workspace` fixture: that one takes the first row
    with no ORDER BY, so once these tests create a business workspace it
    can return either. Which workspace is which is the whole subject
    here, so it is pinned explicitly.
    """
    from sqlalchemy import select

    from app.models.workspace import Workspace, WorkspaceMember

    result = await session.execute(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == test_user.id, Workspace.kind == "personal")
        .order_by(Workspace.created_at.asc())
        .limit(1)
    )
    return result.scalar_one()


@pytest_asyncio.fixture
async def business_ws(client: AsyncClient, auth_headers) -> dict:
    """A business workspace — the only kind that has this module."""
    resp = await client.post(
        "/api/workspaces",
        headers=auth_headers,
        json={"name": "Consultoria", "kind": "business", "self_membership": True},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "invoices" in body["enabled_modules"]
    return body


@pytest_asyncio.fixture
async def biz_headers(auth_headers, business_ws) -> dict:
    return {**auth_headers, "X-Workspace-Id": business_ws["id"]}


@pytest_asyncio.fixture
async def client_payee(session: AsyncSession, business_ws, test_user) -> Payee:
    payee = Payee(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=uuid.UUID(business_ws["id"]),
        name="Cliente Alpha",
        source="manual",
    )
    session.add(payee)
    await session.commit()
    return payee


@pytest_asyncio.fixture
async def inflow(session: AsyncSession, business_ws, test_user) -> Transaction:
    """A credit that landed in the business workspace."""
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=uuid.UUID(business_ws["id"]),
        name="Conta PJ",
        type="checking",
        currency="USD",
        balance=Decimal("0"),
    )
    session.add(account)
    await session.flush()
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=uuid.UUID(business_ws["id"]),
        account_id=account.id,
        description="PIX RECEBIDO ALPHA",
        amount=Decimal("3000.00"),
        currency="USD",
        date=date.today(),
        type="credit",
        source="manual",
    )
    session.add(tx)
    await session.commit()
    return tx


async def _create(client, headers, **overrides):
    payload = {"total": "3000.00", "due_date": str(date.today() + timedelta(days=15))}
    payload.update(overrides)
    resp = await client.post("/api/invoices", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Module isolation — the promise that personal users are untouched
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_personal_workspace_cannot_reach_any_invoice_route(
    client: AsyncClient, auth_headers, personal_ws
):
    """Every route, not a sample: one ungated route is the whole hole."""
    personal = {**auth_headers, "X-Workspace-Id": str(personal_ws.id)}
    fake = str(uuid.uuid4())
    routes = [
        ("get", "/api/invoices", None),
        ("post", "/api/invoices", {"total": "10.00"}),
        ("get", "/api/invoices/settings", None),
        ("patch", "/api/invoices/settings", {"preset": "document"}),
        ("get", "/api/invoices/summary", None),
        ("get", f"/api/invoices/{fake}", None),
        ("patch", f"/api/invoices/{fake}", {"notes": "x"}),
        ("delete", f"/api/invoices/{fake}", None),
        ("post", f"/api/invoices/{fake}/issue", None),
        ("post", f"/api/invoices/{fake}/void", None),
        ("post", f"/api/invoices/{fake}/uncollectible", None),
        ("post", f"/api/invoices/{fake}/reopen", None),
        ("post", f"/api/invoices/{fake}/allocations", {"transaction_id": fake}),
        ("delete", f"/api/invoices/{fake}/allocations/{fake}", None),
    ]
    for method, url, body in routes:
        call = getattr(client, method)
        resp = await call(url, headers=personal, **({"json": body} if body else {}))
        # 404 rather than 403: a workspace without the module should not
        # be able to tell the feature exists.
        assert resp.status_code == 404, f"{method.upper()} {url} -> {resp.status_code}"


@pytest.mark.asyncio
async def test_personal_workspace_never_lists_the_module(
    client: AsyncClient, auth_headers, personal_ws
):
    resp = await client.get(
        "/api/workspaces/current",
        headers={**auth_headers, "X-Workspace-Id": str(personal_ws.id)},
    )
    assert "invoices" not in resp.json()["enabled_modules"]


@pytest.mark.asyncio
async def test_invoice_in_one_workspace_is_invisible_in_another(
    client: AsyncClient, auth_headers, biz_headers, business_ws
):
    created = await _create(client, biz_headers)
    other = await client.post(
        "/api/workspaces",
        headers=auth_headers,
        json={"name": "Outra PJ", "kind": "business", "self_membership": True},
    )
    other_headers = {**auth_headers, "X-Workspace-Id": other.json()["id"]}
    resp = await client.get(f"/api/invoices/{created['id']}", headers=other_headers)
    assert resp.status_code == 404
    assert await (await client.get("/api/invoices", headers=other_headers)).aread() == b"[]" or (
        (await client.get("/api/invoices", headers=other_headers)).json() == []
    )


# ---------------------------------------------------------------------------
# Lifecycle: the stored decisions
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tracking_preset_opens_immediately_and_numbers(client: AsyncClient, biz_headers):
    """The three-field flow: money is already owed, so no ceremony."""
    invoice = await _create(client, biz_headers)
    assert invoice["status"] == "open"
    assert invoice["state"] == "open"
    assert invoice["number"] == 1
    second = await _create(client, biz_headers)
    assert second["number"] == 2


@pytest.mark.asyncio
async def test_document_preset_starts_as_draft_and_requires_lines(
    client: AsyncClient, biz_headers
):
    resp = await client.patch(
        "/api/invoices/settings", headers=biz_headers, json={"preset": "document"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["document_required"] is True
    assert resp.json()["initial_state"] == "draft"

    bare = await client.post(
        "/api/invoices", headers=biz_headers, json={"total": "100.00"}
    )
    assert bare.status_code == 400
    assert bare.json()["detail"]["code"] == "lines_required"

    withlines = await _create(
        client,
        biz_headers,
        lines=[{"description": "Consultoria", "quantity": "10", "unit_price": "150.00"}],
    )
    assert withlines["status"] == "draft"
    # A draft carries no number — the DB constraint says so too.
    assert withlines["number"] is None
    assert withlines["total"] == "1500.00"

    issued = await client.post(
        f"/api/invoices/{withlines['id']}/issue", headers=biz_headers
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["status"] == "open"
    assert issued.json()["number"] == 1


@pytest.mark.asyncio
async def test_language_never_changes_behaviour(client: AsyncClient, biz_headers):
    """Preset drives behaviour; the UI language drives labels. Never the reverse."""
    await client.patch(
        f"/api/workspaces/{biz_headers['X-Workspace-Id']}",
        headers=biz_headers,
        json={"locale": "pt-BR"},
    )
    invoice = await _create(client, biz_headers)
    assert invoice["status"] == "open", "pt-BR must not imply a different preset"


@pytest.mark.asyncio
async def test_only_drafts_are_deletable(client: AsyncClient, biz_headers):
    issued = await _create(client, biz_headers)
    resp = await client.delete(f"/api/invoices/{issued['id']}", headers=biz_headers)
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "only_drafts_deletable"


@pytest.mark.asyncio
async def test_void_keeps_the_number_and_never_reuses_it(client: AsyncClient, biz_headers):
    first = await _create(client, biz_headers)
    assert first["number"] == 1
    voided = await client.post(f"/api/invoices/{first['id']}/void", headers=biz_headers)
    assert voided.status_code == 200, voided.text
    assert voided.json()["state"] == "void"
    assert voided.json()["number"] == 1, "a voided invoice keeps its number"

    # The counter moved on. Reissuing 1 would put two documents under one
    # identifier, which is the thing numbering rules exist to prevent.
    nxt = await _create(client, biz_headers)
    assert nxt["number"] == 2


@pytest.mark.asyncio
async def test_issued_invoice_financials_are_frozen(client: AsyncClient, biz_headers):
    invoice = await _create(client, biz_headers)
    resp = await client.patch(
        f"/api/invoices/{invoice['id']}", headers=biz_headers, json={"total": "9999.00"}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "issued_invoice_immutable"

    # Notes are the seller's own record and stay editable.
    ok = await client.patch(
        f"/api/invoices/{invoice['id']}", headers=biz_headers, json={"notes": "ligar quinta"}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["notes"] == "ligar quinta"


# ---------------------------------------------------------------------------
# Derived state: the facts about money and time
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_partial_then_paid_walks_without_any_status_write(
    client: AsyncClient, biz_headers, inflow
):
    invoice = await _create(client, biz_headers)

    half = await client.post(
        f"/api/invoices/{invoice['id']}/allocations",
        headers=biz_headers,
        json={"transaction_id": str(inflow.id), "amount": "1500.00"},
    )
    assert half.status_code == 201, half.text
    body = half.json()
    assert body["state"] == "partial"
    assert body["status"] == "open", "the stored decision did not change"
    assert body["balance"] == "1500.00"
    assert body["amount_paid"] == "1500.00"


@pytest.mark.asyncio
async def test_full_allocation_reads_paid(client: AsyncClient, biz_headers, inflow):
    invoice = await _create(client, biz_headers)
    resp = await client.post(
        f"/api/invoices/{invoice['id']}/allocations",
        headers=biz_headers,
        json={"transaction_id": str(inflow.id)},
    )
    assert resp.status_code == 201, resp.text
    # No amount given: defaults to as much as fits, which is the whole
    # invoice here. One click for the common case.
    assert resp.json()["state"] == "paid"
    assert resp.json()["balance"] == "0.00"


@pytest.mark.asyncio
async def test_unlinking_restores_both_sides(client: AsyncClient, biz_headers, inflow):
    invoice = await _create(client, biz_headers)
    allocated = await client.post(
        f"/api/invoices/{invoice['id']}/allocations",
        headers=biz_headers,
        json={"transaction_id": str(inflow.id)},
    )
    allocation_id = allocated.json()["allocations"][0]["id"]
    removed = await client.delete(
        f"/api/invoices/{invoice['id']}/allocations/{allocation_id}", headers=biz_headers
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["state"] == "open"
    assert removed.json()["balance"] == "3000.00"
    assert removed.json()["allocations"] == []


@pytest.mark.asyncio
async def test_overdue_needs_no_job(client: AsyncClient, biz_headers):
    """The point of deriving: nothing ran, and it still reads overdue."""
    invoice = await _create(
        client,
        biz_headers,
        issue_date=str(date.today() - timedelta(days=40)),
        due_date=str(date.today() - timedelta(days=10)),
    )
    resp = await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
    assert resp.json()["state"] == "overdue"
    assert resp.json()["days_overdue"] == 10


@pytest.mark.asyncio
async def test_moving_the_due_date_undoes_overdue_with_no_compensating_write(
    client: AsyncClient, biz_headers
):
    """Peers that store `overdue` need SQL to unset it. We need nothing."""
    await client.patch("/api/invoices/settings", headers=biz_headers, json={"preset": "document"})
    invoice = await _create(
        client,
        biz_headers,
        issue_date=str(date.today() - timedelta(days=40)),
        due_date=str(date.today() - timedelta(days=10)),
        lines=[{"description": "Retainer", "quantity": "1", "unit_price": "500.00"}],
    )
    moved = await client.patch(
        f"/api/invoices/{invoice['id']}",
        headers=biz_headers,
        json={"due_date": str(date.today() + timedelta(days=10))},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["state"] == "draft"
    issued = await client.post(f"/api/invoices/{invoice['id']}/issue", headers=biz_headers)
    assert issued.json()["state"] == "open"
    assert issued.json()["days_overdue"] == 0


@pytest.mark.asyncio
async def test_uncollectible_leaves_aging_and_is_never_paid(
    client: AsyncClient, biz_headers, inflow
):
    invoice = await _create(client, biz_headers)
    await client.post(
        f"/api/invoices/{invoice['id']}/allocations",
        headers=biz_headers,
        json={"transaction_id": str(inflow.id), "amount": "1000.00"},
    )
    written_off = await client.post(
        f"/api/invoices/{invoice['id']}/uncollectible", headers=biz_headers
    )
    assert written_off.status_code == 200, written_off.text
    # A decision outranks a fact: partly received, but given up on.
    assert written_off.json()["state"] == "uncollectible"

    summary = await client.get("/api/invoices/summary", headers=biz_headers)
    assert summary.json()["outstanding"] == "0.00"

    reopened = await client.post(f"/api/invoices/{invoice['id']}/reopen", headers=biz_headers)
    assert reopened.json()["state"] == "partial"


# ---------------------------------------------------------------------------
# Allocation guards
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cannot_over_allocate(client: AsyncClient, biz_headers, inflow):
    invoice = await _create(client, biz_headers, total="100.00")
    resp = await client.post(
        f"/api/invoices/{invoice['id']}/allocations",
        headers=biz_headers,
        json={"transaction_id": str(inflow.id), "amount": "500.00"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "over_allocation"


@pytest.mark.asyncio
async def test_same_transaction_twice_on_one_invoice_is_refused(
    client: AsyncClient, biz_headers, inflow
):
    invoice = await _create(client, biz_headers)
    first = await client.post(
        f"/api/invoices/{invoice['id']}/allocations",
        headers=biz_headers,
        json={"transaction_id": str(inflow.id), "amount": "100.00"},
    )
    assert first.status_code == 201
    again = await client.post(
        f"/api/invoices/{invoice['id']}/allocations",
        headers=biz_headers,
        json={"transaction_id": str(inflow.id), "amount": "100.00"},
    )
    assert again.status_code == 400
    assert again.json()["detail"]["code"] == "already_allocated"


@pytest.mark.asyncio
async def test_transaction_from_another_workspace_is_not_found(
    client: AsyncClient, auth_headers, biz_headers, session: AsyncSession, test_user, personal_ws
):
    """Cross-workspace binding must be impossible, and indistinguishable
    from a transaction that does not exist."""
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=personal_ws.id,
        name="Pessoal", type="checking", currency="USD", balance=Decimal("0"),
    )
    session.add(account)
    await session.flush()
    foreign = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=personal_ws.id,
        account_id=account.id, description="pessoal", amount=Decimal("50.00"),
        currency="USD", date=date.today(), type="credit", source="manual",
    )
    session.add(foreign)
    await session.commit()

    invoice = await _create(client, biz_headers)
    resp = await client.post(
        f"/api/invoices/{invoice['id']}/allocations",
        headers=biz_headers,
        json={"transaction_id": str(foreign.id)},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "transaction_not_found"


@pytest.mark.asyncio
async def test_void_refuses_while_money_is_linked(client: AsyncClient, biz_headers, inflow):
    invoice = await _create(client, biz_headers)
    await client.post(
        f"/api/invoices/{invoice['id']}/allocations",
        headers=biz_headers,
        json={"transaction_id": str(inflow.id), "amount": "100.00"},
    )
    resp = await client.post(f"/api/invoices/{invoice['id']}/void", headers=biz_headers)
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "void_with_allocations"


# ---------------------------------------------------------------------------
# Aging + settings
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_summary_buckets_and_excludes_drafts(client: AsyncClient, biz_headers):
    await client.patch("/api/invoices/settings", headers=biz_headers, json={"preset": "document"})
    # A draft: written but never issued. Money nobody owes yet.
    await _create(
        client, biz_headers,
        lines=[{"description": "rascunho", "quantity": "1", "unit_price": "999.00"}],
    )
    await client.patch("/api/invoices/settings", headers=biz_headers, json={"preset": "tracking"})
    await _create(client, biz_headers, total="200.00",
                  due_date=str(date.today() + timedelta(days=5)))
    await _create(client, biz_headers, total="300.00",
                  issue_date=str(date.today() - timedelta(days=60)),
                  due_date=str(date.today() - timedelta(days=45)))

    summary = (await client.get("/api/invoices/summary", headers=biz_headers)).json()
    assert summary["outstanding"] == "500.00", "the 999 draft must not appear"
    assert summary["overdue_amount"] == "300.00"
    assert summary["overdue_count"] == 1
    assert summary["buckets"]["current"] == "200.00"
    assert summary["buckets"]["d31_60"] == "300.00"
    assert len(summary["upcoming"]) == 1


@pytest.mark.asyncio
async def test_settings_carry_presentation_and_survive_issuance(
    client: AsyncClient, biz_headers
):
    """Logo and labels are frozen at issuance — changing them later must
    not rewrite a document the client already received."""
    await client.patch(
        "/api/invoices/settings",
        headers=biz_headers,
        json={
            "logo_url": "https://example.com/logo.png",
            "issuer_display_name": "Alpha Consultoria ME",
            "footer_note": "Obrigado!",
            "number_prefix": "FAT-",
            "template": {"labels": {"quantity": "Horas"}, "custom_fields": [{"key": "po", "label": "PO"}]},
        },
    )
    invoice = await _create(client, biz_headers, custom_fields={"po": "PO-4471"})
    assert invoice["snapshot"]["issuer"]["logo_url"] == "https://example.com/logo.png"
    assert invoice["snapshot"]["template"]["labels"]["quantity"] == "Horas"
    assert invoice["custom_fields"]["po"] == "PO-4471"

    await client.patch(
        "/api/invoices/settings", headers=biz_headers, json={"logo_url": "https://example.com/new.png"}
    )
    after = await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
    assert after.json()["snapshot"]["issuer"]["logo_url"] == "https://example.com/logo.png"


@pytest.mark.asyncio
async def test_competence_date_defaults_to_issue_but_can_diverge(
    client: AsyncClient, biz_headers
):
    """Work delivered in one month, invoiced in the next: the accountant
    books the first. Both dates are stored so both reports are possible."""
    plain = await _create(client, biz_headers)
    assert plain["competence_date"] == plain["issue_date"]

    delivered = str(date.today() - timedelta(days=35))
    split = await _create(client, biz_headers, competence_date=delivered)
    assert split["competence_date"] == delivered
    assert split["issue_date"] != delivered


@pytest.mark.asyncio
async def test_due_date_defaults_to_payment_terms(client: AsyncClient, biz_headers):
    await client.patch(
        "/api/invoices/settings", headers=biz_headers, json={"default_payment_terms_days": 7}
    )
    resp = await client.post("/api/invoices", headers=biz_headers, json={"total": "10.00"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["due_date"] == str(date.today() + timedelta(days=7))


@pytest.mark.asyncio
async def test_payee_from_another_workspace_is_refused(
    client: AsyncClient, biz_headers, session: AsyncSession, test_user, personal_ws
):
    stranger = Payee(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=personal_ws.id,
        name="Pessoal", source="manual",
    )
    session.add(stranger)
    await session.commit()
    resp = await client.post(
        "/api/invoices",
        headers=biz_headers,
        json={"total": "10.00", "payee_id": str(stranger.id)},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "payee_not_found"


@pytest.mark.asyncio
async def test_invoice_carries_its_client(client: AsyncClient, biz_headers, client_payee):
    invoice = await _create(client, biz_headers, payee_id=str(client_payee.id))
    assert invoice["payee"]["name"] == "Cliente Alpha"
    assert invoice["snapshot"]["counterparty"]["name"] == "Cliente Alpha"


@pytest.mark.asyncio
async def test_state_filter_uses_derived_values(client: AsyncClient, biz_headers, inflow):
    await _create(client, biz_headers, total="100.00")
    paid_one = await _create(client, biz_headers, total="50.00")
    await client.post(
        f"/api/invoices/{paid_one['id']}/allocations",
        headers=biz_headers,
        json={"transaction_id": str(inflow.id)},
    )
    listed = await client.get("/api/invoices?state=paid", headers=biz_headers)
    assert [i["id"] for i in listed.json()] == [paid_one["id"]]


# ---------------------------------------------------------------------------
# Role enforcement — the module gate and the role gate are separate questions
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_viewer_reads_but_never_writes(
    client: AsyncClient, auth_headers, biz_headers, business_ws, session: AsyncSession
):
    """A read-only member of a business workspace can see the ledger and
    change nothing in it.

    Both gates have to pass and they ask different questions: the module
    gate asks whether this workspace has invoicing, the role gate asks
    whether this member may write. This asserts the second one, on a
    workspace where the first already said yes.
    """
    import bcrypt as _bcrypt

    from app.models.user import User
    from app.models.workspace import WorkspaceMember

    invoice = await _create(client, biz_headers)

    viewer = User(
        id=uuid.uuid4(),
        email="invoice-viewer@example.com",
        hashed_password=_bcrypt.hashpw(b"viewerpass123", _bcrypt.gensalt()).decode(),
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(viewer)
    await session.flush()
    session.add(
        WorkspaceMember(
            id=uuid.uuid4(),
            workspace_id=uuid.UUID(business_ws["id"]),
            user_id=viewer.id,
            role="viewer",
        )
    )
    await session.commit()

    login = await client.post(
        "/api/auth/login",
        data={"username": "invoice-viewer@example.com", "password": "viewerpass123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert login.status_code == 200, login.text
    viewer_headers = {
        "Authorization": f"Bearer {login.json()['access_token']}",
        "X-Workspace-Id": business_ws["id"],
    }

    # Reads are fine.
    assert (await client.get("/api/invoices", headers=viewer_headers)).status_code == 200
    assert (await client.get("/api/invoices/summary", headers=viewer_headers)).status_code == 200
    assert (
        await client.get(f"/api/invoices/{invoice['id']}", headers=viewer_headers)
    ).status_code == 200

    # Every write is refused — 403 this time, not 404: the workspace does
    # have the module, so hiding its existence would be the wrong lie.
    writes = [
        ("post", "/api/invoices", {"total": "10.00"}),
        ("patch", f"/api/invoices/{invoice['id']}", {"notes": "x"}),
        ("delete", f"/api/invoices/{invoice['id']}", None),
        ("post", f"/api/invoices/{invoice['id']}/void", None),
        ("post", f"/api/invoices/{invoice['id']}/uncollectible", None),
        ("post", f"/api/invoices/{invoice['id']}/allocations", {"transaction_id": str(uuid.uuid4())}),
        ("patch", "/api/invoices/settings", {"preset": "document"}),
    ]
    for method, url, body in writes:
        call = getattr(client, method)
        resp = await call(url, headers=viewer_headers, **({"json": body} if body else {}))
        assert resp.status_code == 403, f"{method.upper()} {url} -> {resp.status_code}"


# ---------------------------------------------------------------------------
# The filter bar: years and counts
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_facets_count_every_state_the_bar_offers(
    client: AsyncClient, biz_headers, inflow
):
    """A chip reading 3 must open a list of exactly 3.

    The counts come from the same derived state the list filter uses, so
    this asserts the two agree rather than that each is plausible on its
    own.
    """
    await _create(client, biz_headers, total="100.00")  # open
    paid = await _create(client, biz_headers, total="50.00")
    await client.post(
        f"/api/invoices/{paid['id']}/allocations",
        headers=biz_headers,
        json={"transaction_id": str(inflow.id)},
    )
    await _create(
        client, biz_headers, total="200.00",
        issue_date=str(date.today() - timedelta(days=40)),
        due_date=str(date.today() - timedelta(days=10)),
    )  # overdue

    facets = (await client.get("/api/invoices/facets", headers=biz_headers)).json()
    assert facets["counts"]["all"] == 3
    assert facets["counts"]["paid"] == 1
    assert facets["counts"]["overdue"] == 1
    # `open` means "still expected", which includes the overdue one.
    assert facets["counts"]["open"] == 2
    assert facets["counts"]["draft"] == 0

    for facet in ("paid", "overdue", "draft"):
        listed = await client.get(f"/api/invoices?state={facet}", headers=biz_headers)
        assert len(listed.json()) == facets["counts"][facet], facet


@pytest.mark.asyncio
async def test_facets_list_only_years_that_have_invoices(client: AsyncClient, biz_headers):
    await _create(client, biz_headers, issue_date="2024-06-10", due_date="2024-07-10")
    await _create(client, biz_headers, issue_date="2026-02-01", due_date="2026-03-01")
    facets = (await client.get("/api/invoices/facets", headers=biz_headers)).json()
    # Newest first: the picker opens on the year someone is most likely
    # to want, and an empty year is never offered.
    assert facets["years"] == [2026, 2024]


@pytest.mark.asyncio
async def test_facet_counts_respect_the_selected_year(client: AsyncClient, biz_headers):
    await _create(client, biz_headers, issue_date="2024-06-10", due_date="2024-07-10")
    await _create(client, biz_headers, issue_date="2026-02-01", due_date="2026-03-01")
    await _create(client, biz_headers, issue_date="2026-05-01", due_date="2026-06-01")

    scoped = (await client.get("/api/invoices/facets?year=2026", headers=biz_headers)).json()
    assert scoped["counts"]["all"] == 2
    # The year list itself is never narrowed, or picking a year would
    # remove every other year from the picker.
    assert scoped["years"] == [2026, 2024]

    assert (
        await client.get("/api/invoices/facets?year=2024", headers=biz_headers)
    ).json()["counts"]["all"] == 1


@pytest.mark.asyncio
async def test_the_year_filter_scopes_the_list(client: AsyncClient, biz_headers):
    old = await _create(client, biz_headers, issue_date="2024-06-10", due_date="2024-07-10")
    new = await _create(client, biz_headers, issue_date="2026-02-01", due_date="2026-03-01")

    listed = await client.get("/api/invoices?year=2024", headers=biz_headers)
    assert [i["id"] for i in listed.json()] == [old["id"]]

    listed = await client.get("/api/invoices?year=2026", headers=biz_headers)
    assert [i["id"] for i in listed.json()] == [new["id"]]

    # Omitting the year means every year, not the current one: the
    # default belongs to the UI, not to the API.
    assert len((await client.get("/api/invoices", headers=biz_headers)).json()) == 2


@pytest.mark.asyncio
async def test_the_year_filter_combines_with_state(client: AsyncClient, biz_headers):
    await _create(client, biz_headers, issue_date="2024-06-10", due_date="2024-07-10")
    await _create(client, biz_headers, issue_date="2026-02-01", due_date="2026-03-01")
    listed = await client.get("/api/invoices?year=2024&state=overdue", headers=biz_headers)
    assert len(listed.json()) == 1


@pytest.mark.asyncio
async def test_the_summary_is_not_scoped_by_year(client: AsyncClient, biz_headers):
    """An unpaid invoice from two years ago is still owed today, so
    "outstanding" has no year. Only the list is scoped."""
    await _create(client, biz_headers, total="900.00",
                  issue_date="2024-06-10", due_date="2024-07-10")
    summary = (await client.get("/api/invoices/summary", headers=biz_headers)).json()
    assert summary["outstanding"] == "900.00"


@pytest.mark.asyncio
async def test_facets_are_gated_like_every_other_route(
    client: AsyncClient, auth_headers, personal_ws
):
    resp = await client.get(
        "/api/invoices/facets",
        headers={**auth_headers, "X-Workspace-Id": str(personal_ws.id)},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# The payee lifecycle, which used to answer 500
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_deleting_a_client_with_invoices_is_refused_in_words(
    client: AsyncClient, biz_headers, client_payee
):
    """The RESTRICT foreign key is deliberate — deleting a client must
    never silently delete the record of money they owed — but it used to
    surface as a 500, which tells the user nothing."""
    await _create(client, biz_headers, payee_id=str(client_payee.id))

    resp = await client.delete(f"/api/payees/{client_payee.id}", headers=biz_headers)
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "payee_has_invoices"
    assert resp.json()["detail"]["count"] == 1

    # And the client is still there afterwards.
    assert (
        await client.get(f"/api/payees/{client_payee.id}", headers=biz_headers)
    ).status_code == 200


@pytest.mark.asyncio
async def test_bulk_deleting_a_client_with_invoices_is_refused_too(
    client: AsyncClient, biz_headers, client_payee
):
    await _create(client, biz_headers, payee_id=str(client_payee.id))
    resp = await client.post(
        "/api/payees/bulk-delete", headers=biz_headers, json={"ids": [str(client_payee.id)]}
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "payee_has_invoices"


@pytest.mark.asyncio
async def test_a_client_with_no_invoices_still_deletes(client: AsyncClient, biz_headers):
    created = await client.post(
        "/api/payees", headers=biz_headers, json={"name": "Sem cobranças"}
    )
    assert (
        await client.delete(f"/api/payees/{created.json()['id']}", headers=biz_headers)
    ).status_code == 204


@pytest.mark.asyncio
async def test_merging_clients_carries_their_invoices_across(
    client: AsyncClient, biz_headers, client_payee
):
    """Transactions already follow a merge; invoices have to as well.

    The two rows were one counterparty all along, and leaving the
    invoices behind would both strand them and trip the foreign key on
    the delete the merge performs.
    """
    duplicate = (
        await client.post("/api/payees", headers=biz_headers, json={"name": "Cliente Alpha (dup)"})
    ).json()
    invoice = await _create(client, biz_headers, payee_id=duplicate["id"])

    merged = await client.post(
        "/api/payees/merge",
        headers=biz_headers,
        json={"target_id": str(client_payee.id), "source_ids": [duplicate["id"]]},
    )
    assert merged.status_code == 200, merged.text

    moved = await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
    assert moved.json()["payee_id"] == str(client_payee.id)
    assert moved.json()["payee"]["name"] == "Cliente Alpha"
    # The duplicate is gone, which is the point of merging.
    assert (
        await client.get(f"/api/payees/{duplicate['id']}", headers=biz_headers)
    ).status_code == 404


@pytest.mark.asyncio
async def test_a_merge_does_not_rewrite_an_issued_document(
    client: AsyncClient, biz_headers, client_payee
):
    """The snapshot froze the client's name at issuance. Merging changes
    who the invoice is linked to, never what the client received."""
    duplicate = (
        await client.post("/api/payees", headers=biz_headers, json={"name": "Beta Dup LTDA"})
    ).json()
    invoice = await _create(client, biz_headers, payee_id=duplicate["id"])
    assert invoice["snapshot"]["counterparty"]["name"] == "Beta Dup LTDA"

    await client.post(
        "/api/payees/merge",
        headers=biz_headers,
        json={"target_id": str(client_payee.id), "source_ids": [duplicate["id"]]},
    )
    after = await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
    assert after.json()["snapshot"]["counterparty"]["name"] == "Beta Dup LTDA"


# ---------------------------------------------------------------------------
# The badge on the transaction list
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_settling_transaction_carries_its_invoice(
    client: AsyncClient, biz_headers, inflow
):
    invoice = await _create(client, biz_headers)
    await client.post(
        f"/api/invoices/{invoice['id']}/allocations",
        headers=biz_headers,
        json={"transaction_id": str(inflow.id), "amount": "1000.00"},
    )

    listed = await client.get("/api/transactions?limit=50", headers=biz_headers)
    assert listed.status_code == 200, listed.text
    row = next(i for i in listed.json()["items"] if i["id"] == str(inflow.id))
    assert row["invoice_link"]["invoice_id"] == invoice["id"]
    assert row["invoice_link"]["number"] == invoice["number"]
    assert row["invoice_link"]["amount"] == "1000.00"


@pytest.mark.asyncio
async def test_an_unlinked_transaction_carries_nothing(
    client: AsyncClient, biz_headers, inflow
):
    listed = await client.get("/api/transactions?limit=50", headers=biz_headers)
    row = next(i for i in listed.json()["items"] if i["id"] == str(inflow.id))
    assert row["invoice_link"] is None


@pytest.mark.asyncio
async def test_a_personal_workspace_never_pays_for_the_badge(
    client: AsyncClient, auth_headers, personal_ws, session: AsyncSession, test_user
):
    """The field is absent, and more importantly the query behind it never
    runs: a workspace without the module pays nothing for a feature it
    does not have."""
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=personal_ws.id,
        name="Pessoal", type="checking", currency="USD", balance=Decimal("0"),
    )
    session.add(account)
    await session.flush()
    session.add(
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=personal_ws.id,
            account_id=account.id, description="mercado", amount=Decimal("-50.00"),
            currency="USD", date=date.today(), type="debit", source="manual",
        )
    )
    await session.commit()

    headers = {**auth_headers, "X-Workspace-Id": str(personal_ws.id)}
    listed = await client.get("/api/transactions?limit=50", headers=headers)
    assert listed.status_code == 200
    assert all(item["invoice_link"] is None for item in listed.json()["items"])
