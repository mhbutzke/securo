import { useTranslation } from 'react-i18next'
import type { InvoiceDocumentPayload } from '@/types'

/**
 * The invoice as a document, on screen.
 *
 * A deliberate mirror of `services/invoice_pdf.py`: same blocks, same
 * order, same labels. It recomputes nothing — every value here was
 * resolved by the server into one structure that both renderers read, so
 * "what the client will receive" and "what I am looking at" cannot drift
 * apart into two opinions.
 *
 * Labels come from the document rather than from i18n. That reads
 * backwards until you remember whose document it is: the sender chose
 * these words, possibly in their client's language, and translating them
 * into the *viewer's* language would rewrite someone else's invoice.
 * Page chrome around it stays translated; the document does not.
 */

function money(amount: string, currency: string): string {
  const value = Number(amount)
  if (!Number.isFinite(value)) return `${currency} ${amount}`
  return `${currency} ${value.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

function Party({
  title,
  name,
  legalName,
  address,
  email,
  taxIds,
}: {
  title: string
  name: string | null
  legalName?: string | null
  address: string | null
  email?: string | null
  taxIds: { label: string; value: string }[]
}) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        {title}
      </div>
      {name && <div className="mt-1.5 font-semibold text-[15px] break-words">{name}</div>}
      {/* Only when it differs — printing the same string twice reads as a bug. */}
      {legalName && legalName !== name && (
        <div className="text-sm text-muted-foreground break-words">{legalName}</div>
      )}
      {taxIds.map((doc) => (
        <div key={`${doc.label}-${doc.value}`} className="text-sm text-muted-foreground">
          {doc.label} {doc.value}
        </div>
      ))}
      {address && (
        <div className="text-sm text-muted-foreground whitespace-pre-line break-words">
          {address}
        </div>
      )}
      {email && <div className="text-sm text-muted-foreground break-all">{email}</div>}
    </div>
  )
}

export function InvoiceDocumentView({
  document,
  className = '',
}: {
  document: InvoiceDocumentPayload
  className?: string
}) {
  const { t } = useTranslation()
  const L = document.labels
  const accent = document.accent_color
  const currency = document.currency
  const hasPaid = Number(document.amount_paid) > 0

  const totals: { label: string; value: string; strong?: boolean }[] = []
  if (document.lines.length > 0) {
    totals.push({ label: L.subtotal, value: money(document.subtotal, currency) })
  }
  if (Number(document.discount) > 0) {
    totals.push({ label: L.discount, value: `-${money(document.discount, currency)}` })
  }
  if (Number(document.tax_total) > 0) {
    totals.push({ label: L.tax, value: money(document.tax_total, currency) })
  }
  totals.push({ label: L.total, value: money(document.total, currency), strong: true })
  // Paid and balance only once money has moved: on an untouched invoice
  // they restate the total twice and add nothing.
  if (hasPaid) {
    totals.push({ label: L.paid, value: money(document.amount_paid, currency) })
    totals.push({ label: L.balance, value: money(document.balance, currency), strong: true })
  }

  return (
    <div
      data-testid="invoice-document"
      className={`bg-white text-neutral-900 rounded-xl border shadow-sm p-8 sm:p-10 ${className}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          {document.logo_url && (
            <img
              src={document.logo_url}
              alt=""
              className="h-10 w-auto max-w-[140px] object-contain"
              data-testid="document-logo"
            />
          )}
          <h2 className="text-2xl font-bold tracking-tight">{L.invoice}</h2>
        </div>
        {document.number && (
          <div
            className="text-lg font-bold tabular-nums"
            style={{ color: accent }}
            data-testid="document-number"
          >
            {document.number}
          </div>
        )}
      </div>

      <div className="mt-3 h-[2px] rounded-full" style={{ backgroundColor: accent }} />

      <div className="mt-6 grid gap-6 sm:grid-cols-2">
        <Party
          title={L.from}
          name={document.issuer.name}
          legalName={document.issuer.legal_name}
          address={document.issuer.address}
          taxIds={document.issuer.tax_ids}
        />
        <Party
          title={L.billTo}
          name={document.client.name}
          address={document.client.address}
          email={document.client.email}
          taxIds={document.client.tax_ids}
        />
      </div>

      <div className="mt-6 flex flex-wrap gap-x-10 gap-y-3">
        {[
          { label: L.issueDate, value: document.issue_date },
          { label: L.dueDate, value: document.due_date },
          ...document.custom_fields,
        ].map((field) => (
          <div key={`${field.label}-${field.value}`}>
            <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
              {field.label}
            </div>
            <div className="text-sm tabular-nums">{field.value}</div>
          </div>
        ))}
      </div>

      {document.lines.length > 0 ? (
        <div className="mt-7 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-200">
                <th className="py-2 text-left text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                  {L.description}
                </th>
                <th className="py-2 text-right text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                  {L.quantity}
                </th>
                <th className="py-2 text-right text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                  {L.unitPrice}
                </th>
                <th className="py-2 text-right text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                  {L.amount}
                </th>
              </tr>
            </thead>
            <tbody>
              {document.lines.map((line, index) => (
                <tr key={index} className="border-b border-neutral-100 last:border-0">
                  <td className="py-2.5 pr-4">{line.description}</td>
                  <td className="py-2.5 text-right tabular-nums">{Number(line.quantity)}</td>
                  <td className="py-2.5 text-right tabular-nums">
                    {money(line.unit_price, currency)}
                  </td>
                  <td className="py-2.5 text-right tabular-nums">{money(line.total, currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        // Not an error state: an invoice with no lines is the normal case
        // where the fiscal document was issued somewhere else and this is
        // only tracking the money.
        <p className="mt-7 text-sm text-muted-foreground" data-testid="document-no-lines">
          {t('invoices.document.noLines')}
        </p>
      )}

      <div className="mt-6 flex justify-end">
        <dl className="w-full max-w-[280px] space-y-1.5">
          {totals.map((row) => (
            <div
              key={row.label}
              className={`flex items-baseline justify-between gap-6 ${
                row.strong ? 'border-t border-neutral-200 pt-1.5' : ''
              }`}
            >
              <dt
                className={row.strong ? 'text-sm font-semibold' : 'text-sm text-muted-foreground'}
              >
                {row.label}
              </dt>
              <dd
                className="text-sm tabular-nums font-medium"
                style={row.strong ? { color: accent, fontWeight: 700 } : undefined}
              >
                {row.value}
              </dd>
            </div>
          ))}
        </dl>
      </div>

      {(document.payment_details || document.notes) && (
        <div className="mt-7 space-y-4">
          {document.payment_details && (
            <div>
              <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                {L.paymentDetails}
              </div>
              <p className="mt-1 text-sm whitespace-pre-line">{document.payment_details}</p>
            </div>
          )}
          {document.notes && (
            <div>
              <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                {L.notes}
              </div>
              <p className="mt-1 text-sm whitespace-pre-line">{document.notes}</p>
            </div>
          )}
        </div>
      )}

      {document.footer_note && (
        <p className="mt-8 border-t border-neutral-100 pt-4 text-xs text-muted-foreground">
          {document.footer_note}
        </p>
      )}
    </div>
  )
}
