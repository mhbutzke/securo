import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Link2, Unlink, Ban, CheckCircle2, Trash2, Send, RotateCcw } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import { formatCurrency } from '@/lib/format'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useWorkspace } from '@/contexts/workspace-context'
import { invoices as invoicesApi, transactions as transactionsApi } from '@/lib/api'
import {
  STATE_TONE,
  availableActions,
  customFieldDefs,
  displayNumber,
  invoiceErrorKey,
  resolveTemplate,
} from '@/lib/invoice-utils'

/**
 * One invoice, and the money bound to it.
 *
 * The allocation panel is the reason this screen exists. Today a person
 * points at the transaction that paid; when automatic matching lands it
 * writes the same rows through the same table, and this view does not
 * change — only the `method` on the row does.
 */
export default function InvoiceDetailPage() {
  const { t } = useTranslation()
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { mask } = usePrivacyMode()
  const { current, canWrite } = useWorkspace()
  const locale = current?.locale ?? 'en'

  const [linkOpen, setLinkOpen] = useState(false)

  const { data: invoice, isLoading } = useQuery({
    queryKey: ['invoice', id],
    queryFn: () => invoicesApi.get(id),
    enabled: Boolean(id),
  })
  const { data: settings } = useQuery({
    queryKey: ['invoice-settings'],
    queryFn: invoicesApi.settings,
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['invoice', id] })
    void queryClient.invalidateQueries({ queryKey: ['invoices'] })
    void queryClient.invalidateQueries({ queryKey: ['invoice-summary'] })
  }

  const onError = (error: unknown) => {
    const key = invoiceErrorKey(error)
    toast.error(key ? t(key, t('invoices.errors.generic')) : t('invoices.errors.generic'))
  }

  // Each decision is its own mutation rather than one parameterised
  // hook: `useMutation` is a hook, so a factory that calls it would be
  // calling hooks from a helper — and the four differ only in a toast.
  const decision = (run: () => Promise<unknown>, successKey: string) => ({
    mutationFn: run,
    onSuccess: () => {
      toast.success(t(successKey))
      refresh()
    },
    onError,
  })

  const issueMutation = useMutation(decision(() => invoicesApi.issue(id), 'invoices.issued'))
  const voidMutation = useMutation(decision(() => invoicesApi.void(id), 'invoices.voided'))
  const writeOffMutation = useMutation(
    decision(() => invoicesApi.writeOff(id), 'invoices.writtenOff'),
  )
  const reopenMutation = useMutation(decision(() => invoicesApi.reopen(id), 'invoices.reopened'))
  const deleteMutation = useMutation({
    mutationFn: () => invoicesApi.remove(id),
    onSuccess: () => {
      toast.success(t('invoices.deleted'))
      refresh()
      navigate('/invoices')
    },
    onError,
  })
  const unlinkMutation = useMutation({
    mutationFn: (allocationId: string) => invoicesApi.unallocate(id, allocationId),
    onSuccess: () => {
      toast.success(t('invoices.unlinked'))
      refresh()
    },
    onError,
  })

  const money = (value: string | number | null | undefined, currency?: string) =>
    mask(formatCurrency(Number(value ?? 0), currency ?? invoice?.currency ?? 'USD', locale))

  if (isLoading || !invoice) {
    return <div className="container max-w-4xl py-10 text-sm text-muted-foreground">{t('common.loading')}</div>
  }

  const actions = availableActions(invoice)
  const template = resolveTemplate(invoice, settings?.template)
  const defs = customFieldDefs(template)
  const snapshotIssuer = invoice.snapshot?.issuer as
    | { display_name?: string; logo_url?: string; footer_note?: string }
    | undefined

  return (
    <div className="container max-w-4xl py-6 space-y-6">
      <button
        onClick={() => navigate('/invoices')}
        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" />
        {t('invoices.backToList')}
      </button>

      <div className="rounded-xl border bg-card p-6 space-y-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-1">
            <div className="flex items-center gap-3">
              {/* The logo the document carried when it was issued — read
                  from the snapshot, never from live settings. */}
              {snapshotIssuer?.logo_url && (
                <img
                  src={snapshotIssuer.logo_url}
                  alt=""
                  className="h-8 w-8 rounded object-contain"
                  data-testid="invoice-logo"
                />
              )}
              <h1 className="text-xl font-semibold tabular-nums" data-testid="invoice-number">
                {displayNumber(invoice, settings?.number_prefix) ?? t('invoices.draftTitle')}
              </h1>
              <span
                data-testid="invoice-detail-state"
                className={cn(
                  'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium',
                  STATE_TONE[invoice.state],
                )}
              >
                {t(`invoices.state.${invoice.state}`)}
              </span>
            </div>
            <p className="text-sm text-muted-foreground">
              {snapshotIssuer?.display_name && <span>{snapshotIssuer.display_name} · </span>}
              {invoice.payee?.name ?? t('invoices.noClient')}
            </p>
          </div>

          {canWrite && (
            <div className="flex flex-wrap items-center gap-2">
              {actions.canIssue && (
                <Button size="sm" onClick={() => issueMutation.mutate()} data-testid="invoice-issue">
                  <Send className="h-4 w-4 mr-1.5" />
                  {t('invoices.action.issue')}
                </Button>
              )}
              {actions.canAllocate && (
                <Button size="sm" onClick={() => setLinkOpen(true)} data-testid="invoice-link-payment">
                  <Link2 className="h-4 w-4 mr-1.5" />
                  {t('invoices.action.markPaid')}
                </Button>
              )}
              {actions.canWriteOff && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => writeOffMutation.mutate()}
                  data-testid="invoice-writeoff"
                >
                  <Ban className="h-4 w-4 mr-1.5" />
                  {t('invoices.action.writeOff')}
                </Button>
              )}
              {actions.canReopen && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => reopenMutation.mutate()}
                  data-testid="invoice-reopen"
                >
                  <RotateCcw className="h-4 w-4 mr-1.5" />
                  {t('invoices.action.reopen')}
                </Button>
              )}
              {actions.canVoid && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => voidMutation.mutate()}
                  data-testid="invoice-void"
                >
                  <Ban className="h-4 w-4 mr-1.5" />
                  {t('invoices.action.void')}
                </Button>
              )}
              {actions.canDelete && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => deleteMutation.mutate()}
                  data-testid="invoice-delete"
                >
                  <Trash2 className="h-4 w-4 mr-1.5" />
                  {t('common.delete')}
                </Button>
              )}
            </div>
          )}
        </div>

        <div className="grid gap-4 sm:grid-cols-4 border-t pt-4">
          <Figure label={t('invoices.column.total')} value={money(invoice.total)} />
          <Figure label={t('invoices.field.paid')} value={money(invoice.amount_paid)} testId="invoice-paid" />
          <Figure
            label={t('invoices.column.balance')}
            value={money(invoice.balance)}
            testId="invoice-balance"
            tone={Number(invoice.balance) > 0 ? 'text-foreground' : 'text-emerald-600 dark:text-emerald-400'}
          />
          <Figure
            label={t('invoices.column.due')}
            value={invoice.due_date}
            hint={
              invoice.days_overdue > 0
                ? t('invoices.daysLate', { count: invoice.days_overdue })
                : undefined
            }
          />
        </div>

        {/* Competência only earns a row when it disagrees with the issue
            date — otherwise it is noise on every invoice. */}
        {invoice.competence_date && invoice.competence_date !== invoice.issue_date && (
          <div className="rounded-lg bg-muted/40 px-3 py-2 text-xs text-muted-foreground" data-testid="invoice-competence">
            {t('invoices.competenceDiverges', {
              competence: invoice.competence_date,
              issue: invoice.issue_date,
            })}
          </div>
        )}

        {defs.length > 0 && invoice.custom_fields && (
          <div className="grid gap-2 sm:grid-cols-2 border-t pt-4">
            {defs.map((def) => (
              <div key={def.key} className="text-sm" data-testid={`invoice-custom-value-${def.key}`}>
                <span className="text-muted-foreground">{def.label}: </span>
                {invoice.custom_fields?.[def.key] ?? '—'}
              </div>
            ))}
          </div>
        )}

        {invoice.lines.length > 0 && (
          <div className="border-t pt-4">
            <table className="w-full text-sm">
              <tbody>
                {invoice.lines.map((line) => (
                  <tr key={line.id} className="border-b last:border-0">
                    <td className="py-2">{line.description}</td>
                    <td className="py-2 text-right tabular-nums text-muted-foreground">
                      {line.quantity} × {money(line.unit_price)}
                    </td>
                    <td className="py-2 text-right tabular-nums font-medium">{money(line.total)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {invoice.notes && <p className="text-sm text-muted-foreground border-t pt-4">{invoice.notes}</p>}
        {snapshotIssuer?.footer_note && (
          <p className="text-xs text-muted-foreground">{snapshotIssuer.footer_note}</p>
        )}
      </div>

      <div className="rounded-xl border bg-card p-6 space-y-4">
        <h2 className="text-sm font-semibold">{t('invoices.payments')}</h2>
        {invoice.allocations.length === 0 ? (
          <p className="text-sm text-muted-foreground" data-testid="invoice-no-payments">
            {t('invoices.noPayments')}
          </p>
        ) : (
          <ul className="space-y-2">
            {invoice.allocations.map((allocation) => (
              <li
                key={allocation.id}
                data-testid="invoice-allocation"
                className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2"
              >
                <div className="min-w-0">
                  <div className="text-sm truncate">
                    {allocation.transaction?.description ?? t('invoices.linkedPayment')}
                  </div>
                  <div className="text-xs text-muted-foreground tabular-nums">
                    {allocation.transaction?.date} ·{' '}
                    {allocation.method === 'manual'
                      ? t('invoices.linkedManually')
                      : t('invoices.linkedAutomatically')}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-sm font-medium tabular-nums">{money(allocation.amount)}</span>
                  {canWrite && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => unlinkMutation.mutate(allocation.id)}
                      data-testid="invoice-unlink"
                      aria-label={t('invoices.action.unlink')}
                    >
                      <Unlink className="h-4 w-4" />
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      <LinkPaymentDialog
        open={linkOpen}
        onOpenChange={setLinkOpen}
        invoiceId={id}
        balance={invoice.balance}
        currency={invoice.currency}
        onLinked={refresh}
      />
    </div>
  )
}

function Figure({
  label,
  value,
  hint,
  tone,
  testId,
}: {
  label: string
  value: string
  hint?: string
  tone?: string
  testId?: string
}) {
  return (
    <div data-testid={testId}>
      <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className={cn('mt-1 text-lg font-semibold tabular-nums', tone)}>{value}</div>
      {hint && <div className="text-xs text-red-600 dark:text-red-400">{hint}</div>}
    </div>
  )
}

function LinkPaymentDialog({
  open,
  onOpenChange,
  invoiceId,
  balance,
  currency,
  onLinked,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  invoiceId: string
  balance: string
  currency: string
  onLinked: () => void
}) {
  const { t } = useTranslation()
  const [selected, setSelected] = useState<string>('')
  const [amount, setAmount] = useState('')

  // Credits only, newest first: an invoice is settled by money coming
  // in, and offering debits would be offering a mistake.
  const { data } = useQuery({
    queryKey: ['transactions', 'for-invoice'],
    queryFn: () => transactionsApi.list({ type: 'credit', limit: 50 }),
    enabled: open,
  })

  // Same currency only — the server refuses a cross-currency allocation
  // rather than inventing a rate, so offering one here would only be
  // offering an error.
  const candidates = useMemo(
    () => (data?.items ?? []).filter((tx) => (tx.currency ?? currency) === currency),
    [data, currency],
  )

  const mutation = useMutation({
    mutationFn: () => invoicesApi.allocate(invoiceId, selected, amount || undefined),
    onSuccess: () => {
      toast.success(t('invoices.linked'))
      onOpenChange(false)
      setSelected('')
      setAmount('')
      onLinked()
    },
    onError: (error) => {
      const key = invoiceErrorKey(error)
      toast.error(key ? t(key, t('invoices.errors.generic')) : t('invoices.errors.generic'))
    },
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('invoices.action.markPaid')}</DialogTitle>
          <DialogDescription>{t('invoices.linkDescription')}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3 max-h-72 overflow-y-auto">
          {candidates.length === 0 && (
            <p className="text-sm text-muted-foreground">{t('invoices.noCandidates')}</p>
          )}
          {candidates.map((tx) => (
            <button
              key={tx.id}
              onClick={() => setSelected(tx.id)}
              data-testid="invoice-candidate"
              className={cn(
                'w-full flex items-center justify-between gap-3 rounded-lg border px-3 py-2 text-left transition-colors',
                selected === tx.id ? 'border-primary bg-primary/5' : 'hover:bg-muted/40',
              )}
            >
              <div className="min-w-0">
                <div className="text-sm truncate">{tx.description}</div>
                <div className="text-xs text-muted-foreground tabular-nums">{tx.date}</div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <span className="text-sm tabular-nums">
                  {formatCurrency(Number(tx.amount), tx.currency ?? currency, 'en')}
                </span>
                {selected === tx.id && <CheckCircle2 className="h-4 w-4 text-primary" />}
              </div>
            </button>
          ))}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="allocation-amount">{t('invoices.field.amountToApply')}</Label>
          <Input
            id="allocation-amount"
            data-testid="invoice-allocation-amount"
            inputMode="decimal"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder={balance}
          />
          {/* Leaving it blank is the common case — one payment closing one
              invoice should not require typing the number twice. */}
          <p className="text-[11px] text-muted-foreground">{t('invoices.field.amountHint')}</p>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={!selected || mutation.isPending}
            data-testid="invoice-allocation-submit"
          >
            {t('invoices.action.link')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
