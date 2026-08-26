import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Receipt, Plus, Settings2, AlertTriangle, Wallet, CalendarClock } from 'lucide-react'
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { PageHeader } from '@/components/page-header'
import { cn } from '@/lib/utils'
import { formatCurrency } from '@/lib/format'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { fiscal as fiscalApi, invoices as invoicesApi, payees as payeesApi } from '@/lib/api'
import { InvoiceLineEditor } from '@/components/invoice-line-editor'
import {
  STATE_TONE,
  customFieldDefs,
  displayNumber,
  invoiceErrorKey,
  linesTotal,
} from '@/lib/invoice-utils'
import type { Invoice, InvoiceLineInput, InvoiceState, IssuerTaxId } from '@/types'

/**
 * The receivables screen: what is owed, what is late, and what landed.
 *
 * Reachable only from a business workspace — `ModuleRoute` sends anyone
 * else home, and every endpoint behind it answers 404 for a workspace
 * without the module. A personal workspace never renders a byte of this.
 */

const FILTERS: { value: string; key: string }[] = [
  { value: 'all', key: 'invoices.filter.all' },
  { value: 'open', key: 'invoices.filter.open' },
  { value: 'overdue', key: 'invoices.filter.overdue' },
  { value: 'paid', key: 'invoices.filter.paid' },
  { value: 'draft', key: 'invoices.filter.draft' },
]

function StateBadge({ state }: { state: InvoiceState }) {
  const { t } = useTranslation()
  return (
    <span
      data-testid={`invoice-state-${state}`}
      className={cn(
        'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium',
        STATE_TONE[state],
      )}
    >
      {t(`invoices.state.${state}`)}
    </span>
  )
}

function SummaryCard({
  icon: Icon,
  label,
  value,
  tone,
  testId,
}: {
  icon: typeof Wallet
  label: string
  value: string
  tone?: string
  testId: string
}) {
  return (
    <div className="rounded-xl border bg-card p-4" data-testid={testId}>
      <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </div>
      <div className={cn('mt-2 text-2xl font-semibold tabular-nums', tone)}>{value}</div>
    </div>
  )
}

export default function InvoicesPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const { current, canWrite } = useWorkspace()
  const locale = current?.locale ?? 'en'
  const userCurrency = user?.preferences?.currency_display ?? 'USD'

  const [filter, setFilter] = useState('all')
  const [createOpen, setCreateOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)

  const { data: settings } = useQuery({
    queryKey: ['invoice-settings'],
    queryFn: invoicesApi.settings,
  })
  const { data: summary } = useQuery({
    queryKey: ['invoice-summary'],
    queryFn: invoicesApi.summary,
  })
  const { data: list = [], isLoading } = useQuery({
    queryKey: ['invoices', filter],
    queryFn: () => invoicesApi.list(filter === 'all' ? {} : { state: filter }),
  })

  // `open` in the filter bar means "still expected", which is three
  // derived states, not one. Asking the server for each and merging
  // would be three round-trips for a list it already sent.
  const visible = useMemo(() => {
    if (filter !== 'open') return list
    return list.filter((i) => ['open', 'partial', 'overdue'].includes(i.state))
  }, [list, filter])

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['invoices'] })
    void queryClient.invalidateQueries({ queryKey: ['invoice-summary'] })
  }

  const money = (value: string | number | null | undefined, currency?: string) =>
    mask(formatCurrency(Number(value ?? 0), currency ?? userCurrency, locale))

  return (
    <div className="container max-w-6xl py-6 space-y-6">
      <PageHeader
        section={t('nav.business', 'Business')}
        title={t('invoices.title')}
        action={
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setSettingsOpen(true)}
              data-testid="invoice-settings-button"
            >
              <Settings2 className="h-4 w-4 mr-1.5" />
              {t('invoices.settings.title')}
            </Button>
            {canWrite && (
              <Button size="sm" onClick={() => setCreateOpen(true)} data-testid="invoice-new-button">
                <Plus className="h-4 w-4 mr-1.5" />
                {t('invoices.new')}
              </Button>
            )}
          </div>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <SummaryCard
          icon={Wallet}
          label={t('invoices.summary.outstanding')}
          value={money(summary?.outstanding)}
          testId="summary-outstanding"
        />
        <SummaryCard
          icon={AlertTriangle}
          label={t('invoices.summary.overdue')}
          value={money(summary?.overdue_amount)}
          tone={Number(summary?.overdue_amount ?? 0) > 0 ? 'text-red-600 dark:text-red-400' : undefined}
          testId="summary-overdue"
        />
        <SummaryCard
          icon={Receipt}
          label={t('invoices.summary.receivedThisMonth')}
          value={money(summary?.received_this_month)}
          testId="summary-received"
        />
        <SummaryCard
          icon={CalendarClock}
          label={t('invoices.summary.upcoming')}
          value={String(summary?.upcoming.length ?? 0)}
          testId="summary-upcoming"
        />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map((option) => (
          <button
            key={option.value}
            onClick={() => setFilter(option.value)}
            data-testid={`invoice-filter-${option.value}`}
            className={cn(
              'rounded-md border px-3 py-1.5 text-sm transition-colors',
              filter === option.value
                ? 'border-primary bg-primary/10 text-primary'
                : 'border-border text-muted-foreground hover:text-foreground',
            )}
          >
            {t(option.key)}
          </button>
        ))}
      </div>

      <div className="rounded-xl border bg-card overflow-hidden">
        {isLoading ? (
          <div className="p-10 text-center text-sm text-muted-foreground">{t('common.loading')}</div>
        ) : visible.length === 0 ? (
          <div className="p-12 flex flex-col items-center text-center gap-3" data-testid="invoices-empty">
            <div className="h-12 w-12 rounded-xl bg-primary/10 flex items-center justify-center">
              <Receipt className="h-6 w-6 text-primary" />
            </div>
            <p className="text-sm text-muted-foreground max-w-sm">{t('invoices.empty')}</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="px-4 py-2.5 text-left font-medium">{t('invoices.column.number')}</th>
                  <th className="px-4 py-2.5 text-left font-medium">{t('invoices.column.client')}</th>
                  <th className="px-4 py-2.5 text-left font-medium">{t('invoices.column.due')}</th>
                  <th className="px-4 py-2.5 text-right font-medium">{t('invoices.column.total')}</th>
                  <th className="px-4 py-2.5 text-right font-medium">{t('invoices.column.balance')}</th>
                  <th className="px-4 py-2.5 text-left font-medium">{t('invoices.column.state')}</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((invoice) => (
                  <tr
                    key={invoice.id}
                    onClick={() => navigate(`/invoices/${invoice.id}`)}
                    data-testid="invoice-row"
                    className="border-t cursor-pointer hover:bg-muted/40 transition-colors"
                  >
                    <td className="px-4 py-3 font-medium tabular-nums">
                      {displayNumber(invoice, settings?.number_prefix) ?? (
                        <span className="text-muted-foreground">{t('invoices.noNumber')}</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {invoice.payee?.name ?? (
                        <span className="text-muted-foreground">{t('invoices.noClient')}</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground tabular-nums">
                      {invoice.due_date}
                      {invoice.days_overdue > 0 && (
                        <span className="ml-2 text-xs text-red-600 dark:text-red-400">
                          {t('invoices.daysLate', { count: invoice.days_overdue })}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {money(invoice.total, invoice.currency)}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums font-medium">
                      {money(invoice.balance, invoice.currency)}
                    </td>
                    <td className="px-4 py-3">
                      <StateBadge state={invoice.state} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <CreateInvoiceDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(invoice) => {
          invalidate()
          navigate(`/invoices/${invoice.id}`)
        }}
      />
      <InvoiceSettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
    </div>
  )
}

function CreateInvoiceDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: (invoice: Invoice) => void
}) {
  const { t } = useTranslation()
  const { data: settings } = useQuery({
    queryKey: ['invoice-settings'],
    queryFn: invoicesApi.settings,
    enabled: open,
  })
  const { data: clients = [] } = useQuery({
    queryKey: ['payees', 'for-invoice'],
    queryFn: () => payeesApi.list({}),
    enabled: open,
  })

  const [payeeId, setPayeeId] = useState<string>('')
  const [total, setTotal] = useState('')
  const [dueDate, setDueDate] = useState('')
  const [notes, setNotes] = useState('')
  const [custom, setCustom] = useState<Record<string, string>>({})
  const [lines, setLines] = useState<InvoiceLineInput[]>([])

  const defs = customFieldDefs(settings?.template)
  const { user } = useAuth()
  const currencyCode = user?.preferences?.currency_display ?? 'USD'

  const mutation = useMutation({
    mutationFn: () =>
      invoicesApi.create({
        payee_id: payeeId || null,
        // Lines are the source of truth once they exist: the server
        // recomputes the total from them and ignores what was typed.
        ...(lines.length ? { lines } : { total }),
        ...(dueDate ? { due_date: dueDate } : {}),
        notes: notes || null,
        ...(Object.keys(custom).length ? { custom_fields: custom } : {}),
      }),
    onSuccess: (invoice) => {
      toast.success(t('invoices.created'))
      onOpenChange(false)
      setPayeeId('')
      setTotal('')
      setDueDate('')
      setNotes('')
      setCustom({})
      setLines([])
      onCreated(invoice)
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
          <DialogTitle>{t('invoices.new')}</DialogTitle>
          {/* Three fields is the whole point under the tracking preset:
              the money is already owed, and the document lives elsewhere. */}
          <DialogDescription>{t('invoices.newDescription')}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label>{t('invoices.field.client')}</Label>
            <Select value={payeeId} onValueChange={setPayeeId}>
              <SelectTrigger data-testid="invoice-client-select">
                <SelectValue placeholder={t('invoices.field.clientPlaceholder')} />
              </SelectTrigger>
              <SelectContent>
                {clients.map((client) => (
                  <SelectItem key={client.id} value={client.id}>
                    {client.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="invoice-total">{t('invoices.field.total')}</Label>
              <Input
                id="invoice-total"
                data-testid="invoice-total-input"
                inputMode="decimal"
                value={lines.length ? linesTotal(lines).toFixed(2) : total}
                onChange={(e) => setTotal(e.target.value)}
                // Derived once lines exist, so the two can never disagree.
                disabled={lines.length > 0}
                placeholder="0.00"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="invoice-due">{t('invoices.field.dueDate')}</Label>
              <Input
                id="invoice-due"
                data-testid="invoice-due-input"
                type="date"
                value={dueDate}
                onChange={(e) => setDueDate(e.target.value)}
              />
              <p className="text-[11px] text-muted-foreground">
                {t('invoices.field.dueDateHint', { days: settings?.default_payment_terms_days ?? 30 })}
              </p>
            </div>
          </div>

          {defs.map((def) => (
            <div key={def.key} className="space-y-1.5">
              <Label htmlFor={`custom-${def.key}`}>{def.label}</Label>
              <Input
                id={`custom-${def.key}`}
                data-testid={`invoice-custom-${def.key}`}
                value={custom[def.key] ?? ''}
                onChange={(e) => setCustom({ ...custom, [def.key]: e.target.value })}
              />
            </div>
          ))}

          <InvoiceLineEditor
            lines={lines}
            onChange={setLines}
            currency={currencyCode}
            showTax={(settings?.tax_fields ?? 'hidden') !== 'hidden'}
            // Under the document preset the server requires line items,
            // so the editor opens with an empty row rather than letting
            // the user discover the rule from a rejected submit.
            required={settings?.document_required ?? false}
          />

          <div className="space-y-1.5">
            <Label htmlFor="invoice-notes">{t('invoices.field.notes')}</Label>
            <Input
              id="invoice-notes"
              data-testid="invoice-notes-input"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={(lines.length ? linesTotal(lines) <= 0 : !total) || mutation.isPending}
            data-testid="invoice-create-submit"
          >
            {t('common.create')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function InvoiceSettingsDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { data: settings } = useQuery({
    queryKey: ['invoice-settings'],
    queryFn: invoicesApi.settings,
    enabled: open,
  })

  const [draft, setDraft] = useState<Record<string, unknown>>({})
  const value = <K extends keyof NonNullable<typeof settings>>(key: K) =>
    (draft[key as string] as NonNullable<typeof settings>[K]) ?? settings?.[key]

  const mutation = useMutation({
    mutationFn: () => invoicesApi.updateSettings(draft),
    onSuccess: () => {
      toast.success(t('invoices.settings.saved'))
      void queryClient.invalidateQueries({ queryKey: ['invoice-settings'] })
      setDraft({})
      onOpenChange(false)
    },
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('invoices.settings.title')}</DialogTitle>
          <DialogDescription>{t('invoices.settings.description')}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label>{t('invoices.settings.preset')}</Label>
            <Select
              value={String(value('preset') ?? 'tracking')}
              onValueChange={(v) => setDraft({ ...draft, preset: v })}
            >
              <SelectTrigger data-testid="invoice-preset-select">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="tracking">{t('invoices.settings.presetTracking')}</SelectItem>
                <SelectItem value="document">{t('invoices.settings.presetDocument')}</SelectItem>
              </SelectContent>
            </Select>
            {/* Says what the choice does, because "tracking vs document"
                means nothing until you know which one issues the paper. */}
            <p className="text-[11px] text-muted-foreground">
              {value('preset') === 'document'
                ? t('invoices.settings.presetDocumentHint')
                : t('invoices.settings.presetTrackingHint')}
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="settings-prefix">{t('invoices.settings.numberPrefix')}</Label>
              <Input
                id="settings-prefix"
                data-testid="invoice-prefix-input"
                value={String(value('number_prefix') ?? '')}
                onChange={(e) => setDraft({ ...draft, number_prefix: e.target.value })}
                placeholder="FAT-"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="settings-terms">{t('invoices.settings.paymentTerms')}</Label>
              <Input
                id="settings-terms"
                data-testid="invoice-terms-input"
                type="number"
                min={0}
                value={String(value('default_payment_terms_days') ?? 30)}
                onChange={(e) =>
                  setDraft({ ...draft, default_payment_terms_days: Number(e.target.value) })
                }
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="settings-issuer">{t('invoices.settings.issuerName')}</Label>
            <Input
              id="settings-issuer"
              data-testid="invoice-issuer-input"
              value={String(value('issuer_display_name') ?? '')}
              onChange={(e) => setDraft({ ...draft, issuer_display_name: e.target.value })}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="settings-logo">{t('invoices.settings.logoUrl')}</Label>
            <Input
              id="settings-logo"
              data-testid="invoice-logo-input"
              value={String(value('logo_url') ?? '')}
              onChange={(e) => setDraft({ ...draft, logo_url: e.target.value })}
              placeholder="https://…"
            />
            {/* The freeze rule, said out loud: people expect a logo change
                to be retroactive, and it deliberately is not. */}
            <p className="text-[11px] text-muted-foreground">{t('invoices.settings.logoHint')}</p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="settings-payment">{t('invoices.settings.paymentDetails')}</Label>
            <Input
              id="settings-payment"
              data-testid="invoice-payment-details-input"
              value={String(value('payment_details') ?? '')}
              onChange={(e) => setDraft({ ...draft, payment_details: e.target.value })}
              placeholder="Pix: …"
            />
            <p className="text-[11px] text-muted-foreground">
              {t('invoices.settings.paymentDetailsHint')}
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="settings-accent">{t('invoices.settings.accentColor')}</Label>
            <div className="flex items-center gap-2">
              <input
                id="settings-accent"
                type="color"
                data-testid="invoice-accent-input"
                className="h-9 w-12 rounded border bg-transparent p-1"
                value={String(value('accent_color') ?? '#111827')}
                onChange={(e) => setDraft({ ...draft, accent_color: e.target.value })}
              />
              <span className="font-mono text-xs text-muted-foreground">
                {String(value('accent_color') ?? '#111827')}
              </span>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="settings-footer">{t('invoices.settings.footerNote')}</Label>
            <Input
              id="settings-footer"
              data-testid="invoice-footer-input"
              value={String(value('footer_note') ?? '')}
              onChange={(e) => setDraft({ ...draft, footer_note: e.target.value })}
            />
          </div>

          <IssuerSection />
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={Object.keys(draft).length === 0 || mutation.isPending}
            data-testid="invoice-settings-save"
          >
            {t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/**
 * The workspace describing itself: what appears as the sender on every
 * document issued from now on.
 *
 * Which fiscal documents are offered comes from the workspace's own
 * jurisdiction pack, so a Brazilian workspace is asked for a CNPJ and a
 * German one for a VAT number without this component knowing either
 * exists. It also never *restricts* the choice — a company can hold a
 * document its country's pack never anticipated.
 */
function IssuerSection() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { data: issuer } = useQuery({ queryKey: ['invoice-issuer'], queryFn: invoicesApi.issuer })
  const { data: kinds } = useQuery({ queryKey: ['tax-id-kinds'], queryFn: fiscalApi.taxIdKinds })

  const [draft, setDraft] = useState<Record<string, string>>({})
  const [docs, setDocs] = useState<IssuerTaxId[] | null>(null)

  const rows = docs ?? issuer?.tax_ids ?? []
  const offered = (kinds?.kinds ?? []).filter((k) => k.offered)

  const mutation = useMutation({
    mutationFn: () =>
      invoicesApi.updateIssuer({
        ...(draft.legal_name !== undefined ? { legal_name: draft.legal_name } : {}),
        ...(draft.address !== undefined ? { address: draft.address } : {}),
        ...(docs ? { tax_ids: docs.filter((d) => d.value.trim()) } : {}),
      }),
    onSuccess: () => {
      toast.success(t('invoices.settings.saved'))
      void queryClient.invalidateQueries({ queryKey: ['invoice-issuer'] })
      setDraft({})
      setDocs(null)
    },
    onError: (error) => {
      const key = invoiceErrorKey(error)
      toast.error(key ? t(key, t('invoices.errors.generic')) : t('invoices.errors.generic'))
    },
  })

  return (
    <div className="border-t pt-4 space-y-3" data-testid="invoice-issuer-section">
      <div>
        <Label>{t('invoices.settings.issuer')}</Label>
        <p className="text-[11px] text-muted-foreground">{t('invoices.settings.issuerHint')}</p>
      </div>

      <Input
        data-testid="issuer-legal-name"
        placeholder={t('invoices.settings.legalName')}
        value={draft.legal_name ?? issuer?.legal_name ?? ''}
        onChange={(e) => setDraft({ ...draft, legal_name: e.target.value })}
      />
      <Input
        data-testid="issuer-address"
        placeholder={t('invoices.settings.addressLabel')}
        value={draft.address ?? issuer?.address ?? ''}
        onChange={(e) => setDraft({ ...draft, address: e.target.value })}
      />

      {offered.map((kind) => {
        const existing = rows.find((r) => r.kind === kind.kind)
        return (
          <Input
            key={kind.kind}
            data-testid={`issuer-tax-${kind.kind}`}
            placeholder={t(kind.label_key, kind.kind.toUpperCase())}
            value={existing?.value ?? ''}
            onChange={(e) => {
              const next = rows.filter((r) => r.kind !== kind.kind)
              if (e.target.value.trim()) next.push({ kind: kind.kind, value: e.target.value })
              setDocs(next)
            }}
          />
        )
      })}

      <Button
        size="sm"
        variant="outline"
        onClick={() => mutation.mutate()}
        disabled={mutation.isPending || (Object.keys(draft).length === 0 && docs === null)}
        data-testid="issuer-save"
      >
        {t('invoices.settings.saveIssuer')}
      </Button>
    </div>
  )
}
