"""Lay out an `InvoiceDocument` as a PDF.

This module decides nothing about content. Everything it prints came
from `invoice_document.build_document`, which is the same structure the
web view renders — so the two cannot drift into disagreeing about what
the invoice says.

ReportLab rather than an HTML-to-PDF engine: WeasyPrint and its peers
need pango, cairo and gdk-pixbuf present in the image, and this is a
self-hosted product where every native library is a cost paid by the
person running the server. ReportLab ships pure-Python wheels.

The layout is deliberately plain — one column, generous whitespace, the
sender's accent colour used once for the header rule and once for the
balance. A document that shouts is not a document a client trusts.
"""
import io
from decimal import Decimal
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import Paragraph, Table, TableStyle

from app.services.invoice_document import DEFAULT_ACCENT, InvoiceDocument

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 18 * mm
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN

INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#6b7280")
RULE = colors.HexColor("#e5e7eb")


def _accent(value: Optional[str]) -> colors.Color:
    """The sender's colour, or the neutral ink when it is unusable.

    The value is user input reaching a PDF generator, so a malformed one
    must degrade rather than raise: an invoice that will not render is
    worse than an invoice in the wrong colour.
    """
    try:
        return colors.HexColor(value or DEFAULT_ACCENT)
    except Exception:
        return colors.HexColor(DEFAULT_ACCENT)


def _money(amount: Decimal, currency: str) -> str:
    """Amount with its currency code.

    Deliberately not locale-formatted: the server has no reliable locale
    for the *reader*, who may be in a different country from the issuer,
    and a misplaced thousands separator on an invoice is worse than a
    plain one. The code is always shown so the number is unambiguous.
    """
    quantised = Decimal(amount).quantize(Decimal("0.01"))
    return f"{currency} {quantised:,.2f}"


def _para(text: str, size: float = 9.5, color: colors.Color = INK, bold: bool = False,
          leading: Optional[float] = None, align: int = 0) -> Paragraph:
    style = ParagraphStyle(
        "cell",
        fontName="Helvetica-Bold" if bold else "Helvetica",
        fontSize=size,
        leading=leading or size * 1.35,
        textColor=color,
        alignment=align,
    )
    return Paragraph(text.replace("\n", "<br/>"), style)


def _party_block(title: str, name: Optional[str], legal_name: Optional[str],
                 address: Optional[str], email: Optional[str],
                 tax_ids: list[tuple[str, str]]) -> list:
    rows = [_para(title.upper(), size=7.5, color=MUTED, bold=True)]
    if name:
        rows.append(_para(name, size=10.5, bold=True))
    # Only when it differs: printing "Alpha ME / Alpha ME" reads as a bug.
    if legal_name and legal_name != name:
        rows.append(_para(legal_name, size=9, color=MUTED))
    for label, value in tax_ids:
        rows.append(_para(f"{label} {value}", size=9, color=MUTED))
    if address:
        rows.append(_para(address, size=9, color=MUTED))
    if email:
        rows.append(_para(email, size=9, color=MUTED))
    return rows


def _draw_flowables(canvas, flowables: list, x: float, y: float, width: float) -> float:
    """Draw a stack of flowables downward from `y`, returning the new y."""
    for flowable in flowables:
        _, height = flowable.wrap(width, PAGE_HEIGHT)
        y -= height
        flowable.drawOn(canvas, x, y)
    return y


def render_pdf(document: InvoiceDocument, logo_bytes: Optional[bytes] = None) -> bytes:
    """The document as PDF bytes.

    `logo_bytes` is passed in rather than fetched here: this function does
    no I/O, which keeps it trivially testable and keeps a slow or hostile
    URL from being reachable from inside a renderer.
    """
    accent = _accent(document.accent_color)
    buffer = io.BytesIO()
    canvas = pdf_canvas.Canvas(buffer, pagesize=A4)
    canvas.setTitle(f"{document.labels['invoice']} {document.number or ''}".strip())

    y = PAGE_HEIGHT - MARGIN

    # --- Header -----------------------------------------------------------
    if logo_bytes:
        try:
            from reportlab.lib.utils import ImageReader

            image = ImageReader(io.BytesIO(logo_bytes))
            iw, ih = image.getSize()
            height = 14 * mm
            width = height * (iw / ih) if ih else height
            # Never let a wide logo run into the invoice title.
            width = min(width, 45 * mm)
            canvas.drawImage(
                image, MARGIN, y - height, width=width, height=height,
                mask="auto", preserveAspectRatio=True, anchor="sw",
            )
            y -= height + 4 * mm
        except Exception:
            # A broken image must not cost the whole document.
            pass

    canvas.setFont("Helvetica-Bold", 20)
    canvas.setFillColor(INK)
    canvas.drawString(MARGIN, y - 7 * mm, document.labels["invoice"])

    if document.number:
        canvas.setFont("Helvetica-Bold", 13)
        canvas.setFillColor(accent)
        canvas.drawRightString(PAGE_WIDTH - MARGIN, y - 7 * mm, document.number)

    y -= 12 * mm
    canvas.setStrokeColor(accent)
    canvas.setLineWidth(1.6)
    canvas.line(MARGIN, y, PAGE_WIDTH - MARGIN, y)
    y -= 8 * mm

    # --- Parties, side by side -------------------------------------------
    column = (CONTENT_WIDTH - 10 * mm) / 2
    issuer_block = _party_block(
        document.labels["from"], document.issuer.name, document.issuer.legal_name,
        document.issuer.address, None, document.issuer.tax_ids,
    )
    client_block = _party_block(
        document.labels["billTo"], document.client.name, None,
        document.client.address, document.client.email, document.client.tax_ids,
    )
    left_y = _draw_flowables(canvas, issuer_block, MARGIN, y, column)
    right_y = _draw_flowables(canvas, client_block, MARGIN + column + 10 * mm, y, column)
    y = min(left_y, right_y) - 8 * mm

    # --- Dates and any declared custom fields ----------------------------
    meta: list[tuple[str, str]] = [
        (document.labels["issueDate"], document.issue_date.isoformat()),
        (document.labels["dueDate"], document.due_date.isoformat()),
        *document.custom_fields,
    ]
    meta_table = Table(
        [[_para(label.upper(), size=7.5, color=MUTED, bold=True) for label, _ in meta],
         [_para(value, size=9.5) for _, value in meta]],
        colWidths=[CONTENT_WIDTH / len(meta)] * len(meta),
    )
    meta_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    _, meta_height = meta_table.wrap(CONTENT_WIDTH, PAGE_HEIGHT)
    y -= meta_height
    meta_table.drawOn(canvas, MARGIN, y)
    y -= 10 * mm

    # --- Line items -------------------------------------------------------
    if document.lines:
        header = [
            _para(document.labels["description"], size=7.5, color=MUTED, bold=True),
            _para(document.labels["quantity"], size=7.5, color=MUTED, bold=True, align=TA_RIGHT),
            _para(document.labels["unitPrice"], size=7.5, color=MUTED, bold=True, align=TA_RIGHT),
            _para(document.labels["amount"], size=7.5, color=MUTED, bold=True, align=TA_RIGHT),
        ]
        rows = [header]
        for line in document.lines:
            quantity = Decimal(line.quantity).normalize()
            rows.append([
                _para(line.description),
                _para(f"{quantity:f}", align=TA_RIGHT),
                _para(_money(line.unit_price, document.currency), align=TA_RIGHT),
                _para(_money(line.total, document.currency), align=TA_RIGHT),
            ])
        widths = [CONTENT_WIDTH * 0.46, CONTENT_WIDTH * 0.12, CONTENT_WIDTH * 0.21,
                  CONTENT_WIDTH * 0.21]
        table = Table(rows, colWidths=widths, repeatRows=1)
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
            ("LINEBELOW", (0, 1), (-1, -2), 0.4, RULE),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        _, table_height = table.wrap(CONTENT_WIDTH, PAGE_HEIGHT)
        y -= table_height
        table.drawOn(canvas, MARGIN, y)
        y -= 8 * mm

    # --- Totals, right-aligned -------------------------------------------
    totals: list[tuple[str, str, bool]] = []
    if document.lines:
        totals.append((document.labels["subtotal"], _money(document.subtotal, document.currency), False))
    if document.discount and document.discount > 0:
        totals.append((document.labels["discount"], f"-{_money(document.discount, document.currency)}", False))
    if document.tax_total and document.tax_total > 0:
        totals.append((document.labels["tax"], _money(document.tax_total, document.currency), False))
    totals.append((document.labels["total"], _money(document.total, document.currency), True))
    # Paid and balance only once money has moved: on an untouched invoice
    # they restate the total twice and add nothing.
    if document.amount_paid and document.amount_paid > 0:
        totals.append((document.labels["paid"], _money(document.amount_paid, document.currency), False))
        totals.append((document.labels["balance"], _money(document.balance, document.currency), True))

    totals_width = 72 * mm
    totals_rows = [
        [_para(label, size=10 if strong else 9.5, bold=strong,
               color=INK if strong else MUTED),
         _para(value, size=11 if strong else 9.5, bold=strong,
               color=accent if strong else INK, align=TA_RIGHT)]
        for label, value, strong in totals
    ]
    totals_table = Table(totals_rows, colWidths=[totals_width * 0.5, totals_width * 0.5])
    totals_table.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("LINEABOVE", (0, len(totals_rows) - 1), (-1, len(totals_rows) - 1), 0.6, RULE),
    ]))
    _, totals_height = totals_table.wrap(totals_width, PAGE_HEIGHT)
    y -= totals_height
    totals_table.drawOn(canvas, PAGE_WIDTH - MARGIN - totals_width, y)
    y -= 10 * mm

    # --- Payment details and notes ---------------------------------------
    for title, body in (
        (document.labels["paymentDetails"], document.payment_details),
        (document.labels["notes"], document.notes),
    ):
        if not body:
            continue
        block = [
            _para(title.upper(), size=7.5, color=MUTED, bold=True),
            _para(body, size=9.5),
        ]
        y = _draw_flowables(canvas, block, MARGIN, y, CONTENT_WIDTH) - 6 * mm

    # --- Footer -----------------------------------------------------------
    if document.footer_note:
        footer = _para(document.footer_note, size=8.5, color=MUTED)
        _, height = footer.wrap(CONTENT_WIDTH, PAGE_HEIGHT)
        footer.drawOn(canvas, MARGIN, MARGIN - height + 6 * mm)

    canvas.showPage()
    canvas.save()
    return buffer.getvalue()
