import { useTranslation } from 'react-i18next'
import { Plus, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { linesTotal } from '@/lib/invoice-utils'
import type { InvoiceLineInput } from '@/types'

/**
 * Line items on a draft.
 *
 * Optional by design, and the empty state says so: under the tracking
 * preset the fiscal document was issued elsewhere and an invoice with no
 * lines is the normal case, not an unfinished one. Adding lines is what
 * turns a receivable into something worth printing.
 *
 * The running total here is a convenience while typing; the server
 * recomputes on save from the same quantities and prices, and its answer
 * is the one that gets stored.
 */
const BLANK: InvoiceLineInput = { description: '', quantity: '1', unit_price: '0' }

export function InvoiceLineEditor({
  lines,
  onChange,
  currency,
  showTax,
  required = false,
}: {
  lines: InvoiceLineInput[]
  onChange: (lines: InvoiceLineInput[]) => void
  currency: string
  showTax: boolean
  /** True when the workspace's preset makes the document mandatory. Shows
   *  one empty row so the requirement is visible before the submit, not
   *  after it. */
  required?: boolean
}) {
  const { t } = useTranslation()

  // A render decision, not state: seeding the parent's array from an
  // effect would fight the parent for ownership of it.
  const rows = lines.length === 0 && required ? [BLANK] : lines

  const update = (index: number, patch: Partial<InvoiceLineInput>) => {
    onChange(rows.map((line, i) => (i === index ? { ...line, ...patch } : line)))
  }

  const add = () => onChange([...rows, { ...BLANK }])

  const total = linesTotal(rows)

  return (
    <div className="space-y-3" data-testid="invoice-line-editor">
      <div className="flex items-center justify-between">
        <Label>{t('invoices.field.lines')}</Label>
        <Button size="sm" variant="ghost" onClick={add} data-testid="invoice-add-line">
          <Plus className="h-3.5 w-3.5 mr-1" />
          {t('invoices.field.addLine')}
        </Button>
      </div>

      {rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">{t('invoices.field.linesOptional')}</p>
      ) : (
        <div className="space-y-2">
          {rows.map((line, index) => (
            <div key={index} className="flex items-start gap-2" data-testid="invoice-line-row">
              <Input
                className="flex-1"
                placeholder={t('invoices.field.lineDescription')}
                value={line.description}
                onChange={(e) => update(index, { description: e.target.value })}
                data-testid={`invoice-line-description-${index}`}
              />
              <Input
                className="w-16 text-right"
                inputMode="decimal"
                value={line.quantity}
                onChange={(e) => update(index, { quantity: e.target.value })}
                data-testid={`invoice-line-quantity-${index}`}
                aria-label={t('invoices.column.quantity', 'Quantity')}
              />
              <Input
                className="w-24 text-right"
                inputMode="decimal"
                value={line.unit_price}
                onChange={(e) => update(index, { unit_price: e.target.value })}
                data-testid={`invoice-line-price-${index}`}
                aria-label={t('invoices.field.unitPrice', 'Unit price')}
              />
              {/* Only when the workspace shows tax at all — a rate field on
                  a tracking-preset invoice is a question nobody asked. */}
              {showTax && (
                <Input
                  className="w-16 text-right"
                  inputMode="decimal"
                  placeholder="%"
                  value={line.tax_rate ?? ''}
                  onChange={(e) => update(index, { tax_rate: e.target.value || null })}
                  data-testid={`invoice-line-tax-${index}`}
                  aria-label={t('invoices.field.taxRate', 'Tax rate')}
                />
              )}
              <Button
                size="sm"
                variant="ghost"
                className="shrink-0"
                onClick={() => onChange(rows.filter((_, i) => i !== index))}
                data-testid={`invoice-remove-line-${index}`}
                aria-label={t('common.delete')}
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          ))}

          <div className="flex justify-end pt-1 text-sm">
            <span className="text-muted-foreground mr-3">{t('invoices.column.total')}</span>
            <span className="font-medium tabular-nums" data-testid="invoice-lines-total">
              {currency} {total.toFixed(2)}
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
