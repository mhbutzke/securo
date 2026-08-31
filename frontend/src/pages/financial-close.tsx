import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Info,
  RefreshCw,
} from 'lucide-react'
import { collections as collectionsApi, reports } from '@/lib/api'
import { localDateString } from '@/lib/date-utils'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import type { FinancialCloseMetricQuality, FinancialCloseSnapshot } from '@/types'
import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

function dateLabel(value: string | null | undefined, locale: string): string {
  if (!value) return 'não disponível'
  const parsed = new Date(`${value.slice(0, 10)}T12:00:00`)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString(locale)
}

function currencyLabel(
  value: number | null | undefined,
  currency: string,
  locale: string,
  mask: (value: string) => string,
): string {
  if (value == null) return 'indisponível'
  return mask(formatCurrency(Number(value), currency, locale))
}

function percentLabel(
  value: number | null | undefined,
  locale: string,
  mask: (value: string) => string,
): string {
  if (value == null) return 'indisponível'
  const formatted = new Intl.NumberFormat(locale, {
    style: 'percent',
    maximumFractionDigits: 1,
  }).format(Number(value))
  return mask(formatted)
}

function ratioLabel(value: number | null | undefined, locale: string, mask: (value: string) => string): string {
  if (value == null) return 'indisponível'
  return mask(`${new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(Number(value))}x`)
}

function MetricCard({
  label,
  value,
  kind = 'currency',
  currency,
  locale,
  mask,
  quality,
  qualityReason,
  emphasis = false,
}: {
  label: string
  value: number | null | undefined
  kind?: 'currency' | 'percent' | 'ratio'
  currency: string
  locale: string
  mask: (value: string) => string
  quality?: FinancialCloseMetricQuality
  qualityReason?: string
  emphasis?: boolean
}) {
  const rendered = kind === 'percent'
    ? percentLabel(value, locale, mask)
    : kind === 'ratio'
      ? ratioLabel(value, locale, mask)
      : currencyLabel(value, currency, locale, mask)
  const unavailable = value == null

  return (
    <Card className={emphasis ? 'border-primary/40 bg-primary/[0.04]' : undefined}>
      <CardHeader className="pb-2">
        <CardDescription>{label}</CardDescription>
        <CardTitle className={`text-2xl tabular-nums ${unavailable ? 'text-muted-foreground' : ''}`}>
          {rendered}
        </CardTitle>
      </CardHeader>
      {quality && (unavailable || quality.status !== 'available') && (
        <CardContent className="pt-0">
          <p className={`text-xs leading-relaxed ${quality.status === 'provisional' ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'}`}>{qualityReason ?? quality.reason}</p>
        </CardContent>
      )}
    </Card>
  )
}

function SnapshotSkeleton() {
  return (
    <div className="space-y-5">
      <Skeleton className="h-20 w-full" />
      {[0, 1, 2].map((row) => (
        <div key={row} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[0, 1, 2, 3].map((item) => <Skeleton key={item} className="h-28 w-full" />)}
        </div>
      ))}
    </div>
  )
}

export default function FinancialClosePage() {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const currency = user?.preferences?.currency_display ?? 'BRL'
  const [period, setPeriod] = useState(() => localDateString().slice(0, 7))
  const [selectedCollectionId, setSelectedCollectionId] = useState('')
  const collectionsQuery = useQuery({
    queryKey: ['collections'],
    queryFn: collectionsApi.list,
    staleTime: 30_000,
  })
  const collections = collectionsQuery.data ?? []
  const investibleCollections = collections.filter(
    (collection) => collection.name.trim().toLocaleLowerCase() === 'carteira investível',
  )
  const autoCollectionId = investibleCollections.length === 1 ? investibleCollections[0].id : null
  const effectiveCollectionId = selectedCollectionId || autoCollectionId

  const validPeriod = /^\d{4}-\d{2}$/.test(period)
  const query = useQuery<FinancialCloseSnapshot>({
    queryKey: ['financial-close', period, effectiveCollectionId],
    queryFn: () => reports.financialClose(period, effectiveCollectionId ?? undefined),
    enabled: validPeriod,
    staleTime: 30_000,
  })
  const data = query.data

  const quality = (key: string) => data?.metric_quality?.[key]
  const portfolioQuality = quality('financial_portfolio_net')
  const portfolioQualityReason = portfolioQuality?.code === 'investible_portfolio_lens_required'
    ? t('financialClose.portfolioNetProxyReason')
    : undefined

  return (
    <div>
      <PageHeader
        section={t('reports.section')}
        title="Fechamento financeiro"
        action={
          <div className="flex items-center gap-2">
            <Button asChild variant="outline" size="sm">
              <Link to="/reports/review"><ArrowLeft className="h-4 w-4" /> Revisão financeira</Link>
            </Button>
            <Button asChild variant="outline" size="sm">
              <Link to="/reports"><ArrowLeft className="h-4 w-4" /> Relatórios</Link>
            </Button>
          </div>
        }
      />

      <Card className="mb-5">
        <CardHeader className="pb-4">
          <CardTitle className="text-base">Período do fechamento</CardTitle>
          <CardDescription>
            Snapshot determinístico e somente leitura. O corte efetivo limita os dados à última sincronização disponível.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-2">
              <Label htmlFor="close-period">Mês</Label>
              <Input id="close-period" type="month" value={period} onChange={(event) => setPeriod(event.target.value)} className="w-44" />
            </div>
            <Button type="button" variant="outline" onClick={() => query.refetch()} disabled={!validPeriod || query.isFetching}>
              <RefreshCw className={query.isFetching ? 'animate-spin' : ''} /> Atualizar
            </Button>
            <div className="space-y-2 min-w-64">
              <Label htmlFor="close-investible-collection">{t('financialClose.investibleCollectionLabel')}</Label>
              <Select
                value={selectedCollectionId || autoCollectionId || undefined}
                onValueChange={setSelectedCollectionId}
              >
                <SelectTrigger id="close-investible-collection" className="w-full">
                  <SelectValue placeholder={t('financialClose.noInvestibleCollection')} />
                </SelectTrigger>
                <SelectContent>
                  {investibleCollections.map((collection) => (
                    <SelectItem key={collection.id} value={collection.id}>{collection.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          {!validPeriod && <p className="mt-3 text-sm text-destructive">Informe um mês válido.</p>}
        </CardContent>
      </Card>

      {query.isError && (
        <Card className="mb-5 border-destructive/40">
          <CardContent className="flex items-center gap-3 py-4 text-sm text-destructive">
            <AlertTriangle className="h-4 w-4 shrink-0" /> Não foi possível carregar o fechamento. Tente atualizar novamente.
          </CardContent>
        </Card>
      )}

      {query.isLoading && <SnapshotSkeleton />}

      {data && (
        <>
          <Card className="mb-5">
            <CardContent className="flex flex-wrap items-center gap-x-5 gap-y-2 py-4 text-sm">
              {data.sync_is_stale ? <AlertTriangle className="h-4 w-4 text-amber-500" /> : <CheckCircle2 className="h-4 w-4 text-emerald-500" />}
              <span><strong>Período:</strong> {data.period}</span>
              <span><strong>Corte:</strong> {dateLabel(data.cutoff_date, dateLocale)} ({data.cutoff_source})</span>
              <span><strong>Última sincronização:</strong> {data.latest_sync_at ? new Date(data.latest_sync_at).toLocaleString(dateLocale) : 'não disponível'}</span>
              <span><strong>{t('financialClose.investibleCollectionLabel')}:</strong> {data.financial_portfolio_collection_name ?? t('financialClose.noInvestibleCollection')}</span>
              {data.sync_is_stale && <span className="text-amber-600">Sincronização possivelmente desatualizada.</span>}
            </CardContent>
          </Card>

          <section aria-labelledby="close-results" className="mb-5">
            <h2 id="close-results" className="mb-3 text-lg font-semibold">Resultado do mês</h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <MetricCard label="Renda econômica" value={data.income_economic} currency={currency} locale={locale} mask={mask} emphasis />
              <MetricCard label="Consumo recorrente" value={data.consumption_recurring} currency={currency} locale={locale} mask={mask} />
              <MetricCard label="Taxa de economia" value={data.savings_rate} kind="percent" currency={currency} locale={locale} mask={mask} />
              <MetricCard label="Rendimentos de posições" value={data.position_interest_income} currency={currency} locale={locale} mask={mask} />
              <MetricCard label="Custos de posições" value={data.position_costs} currency={currency} locale={locale} mask={mask} />
              <MetricCard label="Transferências patrimoniais" value={data.transfers_and_patrimonial_movements} currency={currency} locale={locale} mask={mask} />
              <MetricCard label="Ignorado no período" value={data.ignored_amount} currency={currency} locale={locale} mask={mask} />
            </div>
          </section>

          <section aria-labelledby="close-assets" className="mb-5">
            <h2 id="close-assets" className="mb-3 text-lg font-semibold">Patrimônio consolidado</h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <MetricCard label="Saldos em contas" value={data.account_balance} currency={currency} locale={locale} mask={mask} />
              <MetricCard label="Ativos" value={data.asset_value} currency={currency} locale={locale} mask={mask} />
              <MetricCard label="Recebíveis" value={data.receivables} currency={currency} locale={locale} mask={mask} />
              <MetricCard label="Passivos" value={data.liabilities} currency={currency} locale={locale} mask={mask} />
              <MetricCard label="Patrimônio líquido consolidado" value={data.net_worth_consolidated} currency={currency} locale={locale} mask={mask} emphasis />
              <MetricCard label={data.financial_portfolio_collection_id ? t('financialClose.portfolioNetLabel') : t('financialClose.portfolioNetProxyLabel')} value={data.financial_portfolio_net} currency={currency} locale={locale} mask={mask} quality={portfolioQuality} qualityReason={portfolioQualityReason} emphasis />
            </div>
          </section>

          <section aria-labelledby="close-indicators" className="mb-5">
            <h2 id="close-indicators" className="mb-3 text-lg font-semibold">Indicadores da carteira</h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <MetricCard label="Retirada líquida" value={data.portfolio_withdrawal_net} currency={currency} locale={locale} mask={mask} quality={quality('portfolio_withdrawal_net')} />
              <MetricCard label="Taxa de retirada (12 meses)" value={data.withdrawal_rate_12m} kind="percent" currency={currency} locale={locale} mask={mask} quality={quality('withdrawal_rate_12m')} />
              <MetricCard label="Cobertura de liquidez" value={data.liquidity_coverage} kind="ratio" currency={currency} locale={locale} mask={mask} quality={quality('liquidity_coverage')} />
              <MetricCard label="TWR" value={data.twr} kind="percent" currency={currency} locale={locale} mask={mask} quality={quality('twr')} />
              <MetricCard label="XIRR" value={data.xirr} kind="percent" currency={currency} locale={locale} mask={mask} quality={quality('xirr')} />
              <MetricCard label="Custo essencial médio" value={data.essential_cost_average} currency={currency} locale={locale} mask={mask} />
            </div>
          </section>

          <Card className="mb-5 border-sky-500/30 bg-sky-500/[0.04]">
            <CardContent className="py-4">
              <div className="mb-2 flex items-center gap-2 text-sm font-semibold"><Info className="h-4 w-4 text-sky-600" /> Metodologia e limites</div>
              <p className="mb-3 text-sm text-muted-foreground">Nenhuma alteração é feita nesta tela. Valores futuros ao corte não entram no fechamento; principal/resgate é patrimonial, enquanto juros, taxas e impostos afetam o resultado.</p>
              <details className="text-sm">
                <summary className="cursor-pointer font-medium">Ver critérios do snapshot</summary>
                <dl className="mt-3 grid gap-x-6 gap-y-2 sm:grid-cols-2">
                  {Object.entries(data.methodology ?? {}).map(([key, value]) => (
                    <div key={key}>
                      <dt className="font-medium text-foreground">{key.replaceAll('_', ' ')}</dt>
                      <dd className="text-muted-foreground">
                        {key === 'financial_portfolio_net'
                          ? t(data.financial_portfolio_collection_id ? 'financialClose.portfolioNetSelectedMethodology' : 'financialClose.portfolioNetProxyMethodology')
                          : value}
                      </dd>
                    </div>
                  ))}
                </dl>
              </details>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}
