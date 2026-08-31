import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Info,
  RefreshCw,
} from 'lucide-react'
import { reports } from '@/lib/api'
import { localDateString } from '@/lib/date-utils'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import type { FinancialReviewQueueName, FinancialReviewItem } from '@/types'
import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Skeleton } from '@/components/ui/skeleton'

const PAGE_SIZE = 20

const QUEUE_OPTIONS: { value: FinancialReviewQueueName; label: string }[] = [
  { value: 'all', label: 'Todas as filas' },
  { value: 'pending', label: 'Pendentes' },
  { value: 'uncategorized', label: 'Sem categoria' },
  { value: 'third_party_transfers', label: 'Transferências a confirmar' },
  { value: 'high_value', label: 'Valores altos' },
  { value: 'ignored', label: 'Ignoradas' },
  { value: 'rule_managed', label: 'Gerenciadas por regra' },
]

const REASON_LABELS: Record<Exclude<FinancialReviewQueueName, 'all'>, string> = {
  pending: 'pendente',
  uncategorized: 'sem categoria',
  third_party_transfers: 'transferência a confirmar',
  high_value: 'valor alto',
  ignored: 'ignorada',
  rule_managed: 'regra',
}

function dateLabel(value: string | null | undefined, locale: string): string {
  if (!value) return '—'
  const parsed = new Date(`${value.slice(0, 10)}T12:00:00`)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString(locale)
}

function amountLabel(amount: number, currency: string, locale: string, mask: (value: string) => string): string {
  return mask(formatCurrency(Math.abs(Number(amount) || 0), currency || 'BRL', locale))
}

function summaryLabel(queue: FinancialReviewQueueName): string {
  return QUEUE_OPTIONS.find((option) => option.value === queue)?.label ?? queue
}

export default function FinancialReviewPage() {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const [fromDate, setFromDate] = useState('2026-01-01')
  const [toDate, setToDate] = useState(localDateString())
  const [queue, setQueue] = useState<FinancialReviewQueueName>('all')
  const [offset, setOffset] = useState(0)

  const validPeriod = /^\d{4}-\d{2}-\d{2}$/.test(fromDate)
    && /^\d{4}-\d{2}-\d{2}$/.test(toDate)
    && fromDate <= toDate

  const query = useQuery({
    queryKey: ['financial-review-queue', fromDate, toDate, queue, offset],
    queryFn: () => reports.financialReviewQueue(fromDate, toDate, queue, PAGE_SIZE, offset),
    enabled: validPeriod,
    staleTime: 30_000,
  })

  const data = query.data
  const userCurrency = user?.preferences?.currency_display ?? 'BRL'
  const summaryEntries = useMemo(
    () => QUEUE_OPTIONS.filter(({ value }) => value !== 'all').map(({ value }) => {
      const summaryQueue = value as Exclude<FinancialReviewQueueName, 'all'>
      return { queue: summaryQueue, summary: data?.summaries?.[summaryQueue] }
    }),
    [data?.summaries],
  )

  const pageStart = data && data.total_count > 0 ? data.offset + 1 : 0
  const pageEnd = data ? Math.min(data.offset + data.items.length, data.total_count) : 0
  const canGoBack = offset > 0
  const canGoForward = Boolean(data && offset + PAGE_SIZE < data.total_count)

  function applyPeriod() {
    setOffset(0)
  }

  function renderItem(item: FinancialReviewItem) {
    return (
      <TableRow key={item.id}>
        <TableCell className="text-muted-foreground">{dateLabel(item.date, dateLocale)}</TableCell>
        <TableCell className="max-w-[22rem] truncate" title={item.description}>{item.description || '—'}</TableCell>
        <TableCell>{item.type || '—'}</TableCell>
        <TableCell className="text-right tabular-nums">
          {item.amount < 0 ? '−' : '+'}{amountLabel(item.amount, item.currency, locale, mask)}
        </TableCell>
        <TableCell>
          <span className="rounded-full bg-muted px-2 py-1 text-xs text-muted-foreground">
            {REASON_LABELS[item.reason]}
          </span>
        </TableCell>
      </TableRow>
    )
  }

  return (
    <div>
      <PageHeader
        section={t('reports.section')}
        title="Revisão financeira"
        action={
          <Button asChild variant="outline" size="sm">
            <Link to="/reports"><ChevronLeft className="h-4 w-4" /> Voltar aos relatórios</Link>
          </Button>
        }
      />

      <Card className="mb-5">
        <CardHeader className="pb-4">
          <CardTitle className="text-base">Fila de saneamento</CardTitle>
          <CardDescription>
            Revise os dados reais do período. Esta tela é somente leitura e não aplica correções.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-[1fr_1fr_1.4fr_auto] md:items-end">
            <div className="space-y-2">
              <Label htmlFor="review-from">De</Label>
              <Input id="review-from" type="date" value={fromDate} onChange={(event) => setFromDate(event.target.value)} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="review-to">Até</Label>
              <Input id="review-to" type="date" value={toDate} onChange={(event) => setToDate(event.target.value)} />
            </div>
            <div className="space-y-2">
              <Label>Fila</Label>
              <Select value={queue} onValueChange={(value) => { setQueue(value as FinancialReviewQueueName); setOffset(0) }}>
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {QUEUE_OPTIONS.map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <Button type="button" onClick={applyPeriod} disabled={!validPeriod || query.isFetching}>
              <RefreshCw className={query.isFetching ? 'animate-spin' : ''} /> Atualizar
            </Button>
          </div>
          {!validPeriod && <p className="mt-3 text-sm text-destructive">Informe um intervalo válido, com a data inicial antes da final.</p>}
        </CardContent>
      </Card>

      {query.isError && (
        <Card className="mb-5 border-destructive/40">
          <CardContent className="flex items-center gap-3 py-4 text-sm text-destructive">
            <AlertTriangle className="h-4 w-4 shrink-0" /> Não foi possível carregar a fila. Tente atualizar novamente.
          </CardContent>
        </Card>
      )}

      {data && (
        <>
          <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Card>
              <CardHeader className="pb-2"><CardDescription>Itens únicos na fila</CardDescription><CardTitle className="text-2xl">{data.total_count.toLocaleString()}</CardTitle></CardHeader>
              <CardContent className="pt-0"><p className="text-xs text-muted-foreground">{mask(formatCurrency(Number(data.total_amount), userCurrency, locale))} agregado</p></CardContent>
            </Card>
            {summaryEntries.slice(0, 3).map(({ queue: summaryQueue, summary }) => (
              <Card key={summaryQueue}>
                <CardHeader className="pb-2"><CardDescription>{summaryLabel(summaryQueue)}</CardDescription><CardTitle className="text-2xl">{(summary?.count ?? 0).toLocaleString()}</CardTitle></CardHeader>
                <CardContent className="pt-0"><p className="text-xs text-muted-foreground">{mask(formatCurrency(Number(summary?.total_amount ?? 0), userCurrency, locale))}</p></CardContent>
              </Card>
            ))}
          </div>

          <Card className="mb-5">
            <CardContent className="flex flex-wrap items-center gap-x-5 gap-y-2 py-4 text-sm">
              {data.sync_is_stale ? <AlertTriangle className="h-4 w-4 text-amber-500" /> : <CheckCircle2 className="h-4 w-4 text-emerald-500" />}
              <span><strong>Cutoff:</strong> {dateLabel(data.cutoff_date, dateLocale)} ({data.cutoff_source})</span>
              <span><strong>Última sincronização:</strong> {data.latest_sync_at ? new Date(data.latest_sync_at).toLocaleString(dateLocale) : 'não disponível'}</span>
              {data.sync_is_stale && <span className="text-amber-600">Sincronização possivelmente desatualizada.</span>}
            </CardContent>
          </Card>

          {Object.keys(data.coverage_notes).length > 0 && (
            <Card className="mb-5 border-sky-500/30 bg-sky-500/[0.04]">
              <CardContent className="py-4">
                <div className="mb-2 flex items-center gap-2 text-sm font-semibold"><Info className="h-4 w-4 text-sky-600" /> Notas de cobertura</div>
                <ul className="space-y-1 text-sm text-muted-foreground">
                  {Object.entries(data.coverage_notes).map(([key, note]) => <li key={key}><span className="font-medium">{summaryLabel(key as FinancialReviewQueueName)}:</span> {note}</li>)}
                </ul>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader className="flex-row items-center justify-between space-y-0">
              <div><CardTitle className="text-base">{summaryLabel(queue)}</CardTitle><CardDescription>{pageStart}–{pageEnd} de {data.total_count.toLocaleString()} itens</CardDescription></div>
              <div className="flex gap-2">
                <Button variant="outline" size="icon-sm" onClick={() => setOffset((current) => Math.max(0, current - PAGE_SIZE))} disabled={!canGoBack || query.isFetching} aria-label="Página anterior"><ChevronLeft /></Button>
                <Button variant="outline" size="icon-sm" onClick={() => setOffset((current) => current + PAGE_SIZE)} disabled={!canGoForward || query.isFetching} aria-label="Próxima página"><ChevronRight /></Button>
              </div>
            </CardHeader>
            <CardContent className="px-0 pb-0">
              {data.items.length === 0 ? <p className="px-6 py-8 text-center text-sm text-muted-foreground">Nenhum item nesta fila para o período.</p> : (
                <Table>
                  <TableHeader><TableRow><TableHead>Data</TableHead><TableHead>Descrição</TableHead><TableHead>Tipo</TableHead><TableHead className="text-right">Valor</TableHead><TableHead>Motivo</TableHead></TableRow></TableHeader>
                  <TableBody>{data.items.map(renderItem)}</TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </>
      )}

      {query.isLoading && <Card><CardContent className="space-y-3 py-6">{Array.from({ length: 5 }, (_, index) => <Skeleton key={index} className="h-8 w-full" />)}</CardContent></Card>}
    </div>
  )
}
