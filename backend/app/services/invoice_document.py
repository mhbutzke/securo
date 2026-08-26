"""The rendered invoice: one resolved document, two renderers.

The screen and the PDF must agree, forever. The way they do that here is
that neither of them decides anything: `build_document` resolves the
invoice, its snapshot and the workspace's identity into one plain
structure, and both renderers only lay it out. A field that appears on
the PDF but not on screen is a bug in one renderer, never a difference
of opinion about what the document says.

What is resolved, and in what order, is the whole subject:

  - **The snapshot wins.** It is what the document said when it was
    issued. A logo changed in September must not reach August's invoice,
    and a client renamed after the fact must not rename itself on a
    document they already hold.
  - **Live settings fill only what the snapshot never captured** — an
    invoice issued before a field existed, or a draft that has not been
    issued at all and therefore has no snapshot yet.

Nothing here is jurisdiction-specific. Whether the document reads as a
fiscal instrument or as a commercial request for payment is a matter of
what the workspace configured and what the reader's law says — the same
bytes serve a French auto-entrepreneur and a Brazilian MEI whose NFS-e
was issued at the prefeitura.
"""
from dataclasses import dataclass, field
from datetime import date as _date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.fiscal.registry import TaxIdKind, format_for_display, spec_for
from app.models.invoice import Invoice, InvoiceSettings
from app.models.workspace import Workspace, WorkspaceTaxId

#: Labels a template may override. Anything not listed is not overridable,
#: which keeps a stray key in the jsonb from silently becoming UI.
#:
#: English is the fallback, not the default: `default_labels()` picks the
#: pack for the *issuer's* locale first. The document is written in the
#: sender's language, and a Brazilian shipping "Invoice / Bill to / Qty"
#: until they rename all eighteen by hand is a bad first run.
DEFAULT_LABELS: dict[str, str] = {
    "invoice": "Invoice",
    "billTo": "Bill to",
    "from": "From",
    "number": "Number",
    "issueDate": "Issue date",
    "dueDate": "Due date",
    "description": "Description",
    "quantity": "Qty",
    "unitPrice": "Unit price",
    "amount": "Amount",
    "subtotal": "Subtotal",
    "discount": "Discount",
    "tax": "Tax",
    "total": "Total",
    "paid": "Paid",
    "balance": "Balance due",
    "paymentDetails": "Payment details",
    "notes": "Notes",
}

#: Shipped label packs, by language. Deliberately few: this is the set
#: the product is actually sold in, and a half-translated document reads
#: worse than an English one. A locale with no pack falls back to
#: `DEFAULT_LABELS`, and every label stays individually overridable
#: afterwards regardless.
#:
#: These are *defaults for the sender*, never a translation for the
#: reader: the document keeps the words its issuer chose, whoever opens
#: it. That is why they are resolved once, at issuance, into the snapshot.
LABEL_PACKS: dict[str, dict[str, str]] = {
    "pt": {
        "invoice": "Fatura",
        "billTo": "Cliente",
        "from": "Emitente",
        "number": "Número",
        "issueDate": "Emissão",
        "dueDate": "Vencimento",
        "description": "Descrição",
        "quantity": "Qtd",
        "unitPrice": "Valor unit.",
        "amount": "Valor",
        "subtotal": "Subtotal",
        "discount": "Desconto",
        "tax": "Impostos",
        "total": "Total",
        "paid": "Recebido",
        "balance": "Saldo devedor",
        "paymentDetails": "Dados para pagamento",
        "notes": "Observações",
    },
    "es": {
        "invoice": "Factura",
        "billTo": "Cliente",
        "from": "Emisor",
        "number": "Número",
        "issueDate": "Emisión",
        "dueDate": "Vencimiento",
        "description": "Descripción",
        "quantity": "Cant.",
        "unitPrice": "Precio unit.",
        "amount": "Importe",
        "subtotal": "Subtotal",
        "discount": "Descuento",
        "tax": "Impuestos",
        "total": "Total",
        "paid": "Cobrado",
        "balance": "Saldo pendiente",
        "paymentDetails": "Datos de pago",
        "notes": "Notas",
    },
    "fr": {
        "invoice": "Facture",
        "billTo": "Client",
        "from": "Émetteur",
        "number": "Numéro",
        "issueDate": "Date d'émission",
        "dueDate": "Échéance",
        "description": "Désignation",
        "quantity": "Qté",
        "unitPrice": "Prix unit.",
        "amount": "Montant",
        "subtotal": "Sous-total",
        "discount": "Remise",
        "tax": "TVA",
        "total": "Total",
        "paid": "Réglé",
        "balance": "Reste à payer",
        "paymentDetails": "Coordonnées de paiement",
        "notes": "Notes",
    },
    "de": {
        "invoice": "Rechnung",
        "billTo": "Rechnungsempfänger",
        "from": "Rechnungssteller",
        "number": "Nummer",
        "issueDate": "Rechnungsdatum",
        "dueDate": "Fällig am",
        "description": "Bezeichnung",
        "quantity": "Menge",
        "unitPrice": "Einzelpreis",
        "amount": "Betrag",
        "subtotal": "Zwischensumme",
        "discount": "Rabatt",
        "tax": "USt.",
        "total": "Gesamt",
        "paid": "Bezahlt",
        "balance": "Offener Betrag",
        "paymentDetails": "Zahlungsinformationen",
        "notes": "Hinweise",
    },
    "it": {
        "invoice": "Fattura",
        "billTo": "Cliente",
        "from": "Emittente",
        "number": "Numero",
        "issueDate": "Data emissione",
        "dueDate": "Scadenza",
        "description": "Descrizione",
        "quantity": "Qtà",
        "unitPrice": "Prezzo unit.",
        "amount": "Importo",
        "subtotal": "Subtotale",
        "discount": "Sconto",
        "tax": "IVA",
        "total": "Totale",
        "paid": "Incassato",
        "balance": "Saldo dovuto",
        "paymentDetails": "Dati per il pagamento",
        "notes": "Note",
    },
}


def default_labels(locale: Optional[str]) -> dict[str, str]:
    """The label pack for the issuer's language, English if none ships.

    Keyed on the language subtag, so `pt-BR` and `pt-PT` share a pack —
    the two differ in vocabulary a translator would care about and not in
    the eighteen words on an invoice.
    """
    if not locale:
        return dict(DEFAULT_LABELS)
    language = locale.replace("_", "-").split("-")[0].lower()
    return dict(LABEL_PACKS.get(language, DEFAULT_LABELS))


#: The default when a workspace has picked no colour. Deliberately a
#: neutral ink rather than the product's own brand: the document belongs
#: to the sender, not to the tool that printed it.
DEFAULT_ACCENT = "#111827"


@dataclass
class DocumentParty:
    """One side of the document. Every field optional, because a workspace
    that has filled nothing in still gets a usable page."""

    name: Optional[str] = None
    legal_name: Optional[str] = None
    address: Optional[str] = None
    email: Optional[str] = None
    #: Rendered `(label, value)` pairs — already resolved through the
    #: fiscal pack, so no renderer needs to know what a CNPJ is.
    tax_ids: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class DocumentLine:
    description: str
    quantity: Decimal
    unit_price: Decimal
    total: Decimal
    tax_rate: Optional[Decimal] = None


@dataclass
class InvoiceDocument:
    """Everything a renderer needs, and nothing it has to look up."""

    number: Optional[str]
    status: str
    state: str
    issue_date: _date
    due_date: _date
    currency: str
    subtotal: Decimal
    discount: Decimal
    tax_total: Decimal
    total: Decimal
    amount_paid: Decimal
    balance: Decimal
    issuer: DocumentParty
    client: DocumentParty
    lines: list[DocumentLine]
    labels: dict[str, str]
    accent_color: str
    logo_url: Optional[str]
    payment_details: Optional[str]
    notes: Optional[str]
    footer_note: Optional[str]
    custom_fields: list[tuple[str, str]]
    #: True when the invoice carries enough to be worth rendering as a
    #: document at all. An amount with no lines is a perfectly good
    #: receivable and a poor-looking fatura, so the UI asks first.
    has_line_items: bool


def _label_map(
    template: Optional[dict[str, Any]], locale: Optional[str] = None
) -> dict[str, str]:
    """The issuer's language pack, overridden only by keys that exist.

    A template is free-form jsonb by design, so it can hold anything a
    hand edit put there; unknown keys are ignored rather than rendered.
    """
    labels = default_labels(locale)
    overrides = (template or {}).get("labels")
    if isinstance(overrides, dict):
        for key, value in overrides.items():
            if key in DEFAULT_LABELS and isinstance(value, str) and value.strip():
                labels[key] = value.strip()
    return labels


def _custom_field_pairs(
    template: Optional[dict[str, Any]], values: Optional[dict[str, Any]]
) -> list[tuple[str, str]]:
    """Only fields the workspace declared, in the order it declared them.

    Driven by the definitions rather than by the stored values, so a field
    removed from settings stops printing without anyone editing invoices.
    """
    defs = (template or {}).get("custom_fields")
    if not isinstance(defs, list) or not values:
        return []
    pairs: list[tuple[str, str]] = []
    for definition in defs:
        if not isinstance(definition, dict):
            continue
        key = definition.get("key")
        if not isinstance(key, str):
            continue
        value = values.get(key)
        if value in (None, ""):
            continue
        pairs.append((str(definition.get("label") or key), str(value)))
    return pairs


def _format_tax_id(kind_value: str, value: str) -> tuple[str, str]:
    """A document as a person reads it, resolved through the pack.

    An unrecognised kind still renders — a stored value must never become
    unprintable because a pack changed underneath it.
    """
    try:
        kind = TaxIdKind(kind_value)
    except ValueError:
        return kind_value.upper(), value
    spec = spec_for(kind)
    # `label_key` is an i18n key; its last segment is a usable fallback for
    # a server-rendered PDF, which has no translator.
    label = spec.label_key.rsplit(".", 1)[-1].upper()
    # Masked here rather than by the reader: this structure feeds a PDF as
    # well as a screen, and the PDF has nobody to ask.
    return label, format_for_display(kind, value)


async def build_document(
    session: AsyncSession,
    invoice: Invoice,
    settings: InvoiceSettings,
    workspace: Workspace,
) -> InvoiceDocument:
    from app.services import invoice_service

    snapshot = invoice.snapshot or {}
    snap_issuer = snapshot.get("issuer") or {}
    snap_client = snapshot.get("counterparty") or {}

    # Live settings are the fallback, never the override: an issued
    # document keeps what it captured.
    def issued_or_live(key: str, live: Any) -> Any:
        if snapshot and key in snap_issuer:
            return snap_issuer.get(key)
        return live

    tax_rows = await session.execute(
        select(WorkspaceTaxId).where(WorkspaceTaxId.workspace_id == workspace.id)
    )
    issuer_tax_ids = [_format_tax_id(row.kind, row.value) for row in tax_rows.scalars().all()]

    issuer = DocumentParty(
        name=issued_or_live("display_name", settings.issuer_display_name) or workspace.name,
        legal_name=issued_or_live("legal_name", workspace.legal_name),
        address=issued_or_live("address", workspace.address),
        tax_ids=issuer_tax_ids,
    )

    payee = invoice.payee
    client = DocumentParty(
        name=snap_client.get("name") if snapshot else (payee.name if payee else None),
        address=snap_client.get("address") if snapshot else (payee.address if payee else None),
        email=snap_client.get("email") if snapshot else (payee.email if payee else None),
        tax_ids=[
            _format_tax_id(t["kind"], t["value"])
            for t in (snap_client.get("tax_ids") or [])
            if isinstance(t, dict) and t.get("kind") and t.get("value")
        ]
        if snapshot
        else [_format_tax_id(t.kind, t.value) for t in (payee.tax_ids if payee else [])],
    )

    template = snapshot.get("template") if snapshot else settings.template

    return InvoiceDocument(
        number=(
            f"{(snapshot.get('number_prefix') if snapshot else settings.number_prefix) or ''}"
            f"{invoice.number}"
            if invoice.number is not None
            else None
        ),
        status=invoice.status,
        state=invoice_service.derive_state(invoice),
        issue_date=invoice.issue_date,
        due_date=invoice.due_date,
        currency=invoice.currency,
        subtotal=invoice.subtotal or Decimal("0"),
        discount=invoice.discount or Decimal("0"),
        tax_total=invoice.tax_total or Decimal("0"),
        total=invoice.total or Decimal("0"),
        amount_paid=invoice_service.allocated_total(invoice),
        balance=invoice_service.balance(invoice),
        issuer=issuer,
        client=client,
        lines=[
            DocumentLine(
                description=line.description,
                quantity=line.quantity,
                unit_price=line.unit_price,
                total=line.total,
                tax_rate=line.tax_rate,
            )
            for line in invoice.lines
        ],
        # The workspace's own language, because the sender writes the
        # document. Once issued, the snapshot carries the resolved labels
        # and this is never consulted again — switching the interface to
        # English must not retitle a document already in a client's hands.
        labels=_label_map(template, snapshot.get("locale") if snapshot else workspace.locale),
        accent_color=(
            issued_or_live("accent_color", settings.accent_color) or DEFAULT_ACCENT
        ),
        logo_url=issued_or_live("logo_url", settings.logo_url),
        payment_details=issued_or_live("payment_details", settings.payment_details),
        notes=invoice.notes,
        footer_note=issued_or_live("footer_note", settings.footer_note),
        custom_fields=_custom_field_pairs(template, invoice.custom_fields),
        has_line_items=bool(invoice.lines),
    )


def document_payload(document: InvoiceDocument) -> dict[str, Any]:
    """The document as JSON, for the on-screen renderer and the share page.

    Decimals become strings rather than floats: the frontend formats them
    with the workspace's locale, and a float would have already lost the
    cents by the time it got there.
    """
    return {
        "number": document.number,
        "status": document.status,
        "state": document.state,
        "issue_date": document.issue_date.isoformat(),
        "due_date": document.due_date.isoformat(),
        "currency": document.currency,
        "subtotal": str(document.subtotal),
        "discount": str(document.discount),
        "tax_total": str(document.tax_total),
        "total": str(document.total),
        "amount_paid": str(document.amount_paid),
        "balance": str(document.balance),
        "issuer": {
            "name": document.issuer.name,
            "legal_name": document.issuer.legal_name,
            "address": document.issuer.address,
            "tax_ids": [{"label": k, "value": v} for k, v in document.issuer.tax_ids],
        },
        "client": {
            "name": document.client.name,
            "address": document.client.address,
            "email": document.client.email,
            "tax_ids": [{"label": k, "value": v} for k, v in document.client.tax_ids],
        },
        "lines": [
            {
                "description": line.description,
                "quantity": str(line.quantity),
                "unit_price": str(line.unit_price),
                "total": str(line.total),
                "tax_rate": str(line.tax_rate) if line.tax_rate is not None else None,
            }
            for line in document.lines
        ],
        "labels": document.labels,
        "accent_color": document.accent_color,
        "logo_url": document.logo_url,
        "payment_details": document.payment_details,
        "notes": document.notes,
        "footer_note": document.footer_note,
        "custom_fields": [{"label": k, "value": v} for k, v in document.custom_fields],
        "has_line_items": document.has_line_items,
    }
