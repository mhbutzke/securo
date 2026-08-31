import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { ArrowLeft, Archive, CheckCircle2, ChevronDown, ChevronUp, Info, Scale } from 'lucide-react'
import { positions } from '@/lib/api'
import type { Position, PositionSide } from '@/types'
import { localDateString } from '@/lib/date-utils'
import { toast } from 'sonner'
import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useWorkspace } from '@/contexts/workspace-context'
import { useCollectionFilter } from '@/contexts/collection-filter-context'
import { formatCurrency } from '@/lib/format'

type SideFilter = 'all' | PositionSide

function dateLabel(value: string | null, locale: string): string {
  if (!value) return 'sem vencimento'
  const parsed = new Date(`${value.slice(0, 10)}T12:00:00`)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString(locale)
}

function displayAmount(value: number, currency: string, locale: string, mask: (value: string) => string): string {
  return mask(formatCurrency(Number(value) || 0, currency || 'BRL', locale))
}

function sideLabel(side: PositionSide): string {
  return side === 'receivable' ? 'A receber' : 'Passivo'
}

function sideTotals(items: Position[], side: PositionSide): string[] {
  const totals = new Map<string, number>()
  for (const item of items) {
    if (item.side !== side) continue
    totals.set(item.currency, (totals.get(item.currency) ?? 0) + Number(item.balance || 0))
  }
  return [...totals.entries()].map(([currency, value]) => `${currency} ${value.toFixed(2)}`)
}

export default function PositionsPage() {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const { canWrite } = useWorkspace()
  const { activePositionIds } = useCollectionFilter()
  const queryClient = useQueryClient()
  const [side, setSide] = useState<SideFilter>('all')
  const [search, setSearch] = useState('')
  const [includeArchived, setIncludeArchived] = useState(false)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [formSide, setFormSide] = useState<PositionSide>('receivable')
  const [formName, setFormName] = useState('')
  const [formCounterparty, setFormCounterparty] = useState('')
  const [formCurrency, setFormCurrency] = useState('BRL')
  const [formPrincipal, setFormPrincipal] = useState('')
  const [formStartDate, setFormStartDate] = useState(localDateString())
  const [formDueDate, setFormDueDate] = useState('')
  const [movementPosition, setMovementPosition] = useState<Position | null>(null)
  const [movementKind, setMovementKind] = useState<'increase' | 'decrease' | 'writeoff'>('increase')
  const [movementPrincipal, setMovementPrincipal] = useState('')
  const [movementInterest, setMovementInterest] = useState('0')
  const [movementFee, setMovementFee] = useState('0')
  const [movementTax, setMovementTax] = useState('0')
  const [movementDate, setMovementDate] = useState(localDateString())

  const query = useQuery({
    queryKey: ['positions', includeArchived],
    queryFn: () => positions.list(includeArchived),
    staleTime: 30_000,
  })

  const createMutation = useMutation({
    mutationFn: () => positions.create({
      side: formSide,
      name: formName.trim(),
      counterparty: formCounterparty.trim() || null,
      currency: formCurrency.trim().toUpperCase() || 'BRL',
      original_principal: Number(formPrincipal),
      start_date: formStartDate,
      due_date: formDueDate || null,
    }),
    onSuccess: () => {
      toast.success('Posição criada')
      setCreateOpen(false)
      setFormName('')
      setFormCounterparty('')
      setFormPrincipal('')
      setFormDueDate('')
      queryClient.invalidateQueries({ queryKey: ['positions'] })
    },
  })

  const movementMutation = useMutation({
    mutationFn: () => {
      if (!movementPosition) throw new Error('Position not selected')
      return positions.addMovement(movementPosition.id, {
        kind: movementKind,
        principal_amount: Number(movementPrincipal),
        interest_amount: Number(movementInterest) || 0,
        fee_amount: Number(movementFee) || 0,
        tax_amount: Number(movementTax) || 0,
        effective_date: movementDate,
        idempotency_key: `manual:${movementPosition.id}:${movementDate}:${movementKind}:${movementPrincipal}:${Date.now()}`,
      })
    },
    onSuccess: () => {
      toast.success('Movimento registrado')
      setMovementPosition(null)
      setMovementPrincipal('')
      setMovementInterest('0')
      setMovementFee('0')
      setMovementTax('0')
      queryClient.invalidateQueries({ queryKey: ['positions'] })
    },
  })

  const reverseMutation = useMutation({
    mutationFn: ({ positionId, movementId }: { positionId: string; movementId: string }) => positions.reverseMovement(positionId, movementId),
    onSuccess: () => {
      toast.success('Movimento revertido')
      queryClient.invalidateQueries({ queryKey: ['positions'] })
    },
  })

  const filtered = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase()
    return (query.data ?? []).filter((item) => {
      if (activePositionIds && !activePositionIds.includes(item.id)) return false
      if (side !== 'all' && item.side !== side) return false
      if (!needle) return true
      return [item.name, item.counterparty ?? '', item.currency].some((value) => value.toLocaleLowerCase().includes(needle))
    })
  }, [query.data, search, side, activePositionIds])

  const receivables = filtered.filter((item) => item.side === 'receivable')
  const liabilities = filtered.filter((item) => item.side === 'liability')

  return (
    <div>
      <PageHeader
        section={t('assets.title')}
        title="Recebíveis e passivos"
        action={
          <div className="flex items-center gap-2">
            <Button asChild variant="outline" size="sm">
              <Link to="/assets"><ArrowLeft className="h-4 w-4" /> Voltar aos ativos</Link>
            </Button>
            {canWrite && <Button size="sm" onClick={() => setCreateOpen(true)}><Scale className="h-4 w-4" /> Nova posição</Button>}
          </div>
        }
      />

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader><DialogTitle>Nova posição</DialogTitle></DialogHeader>
          <form onSubmit={(event) => { event.preventDefault(); if (formName.trim() && Number(formPrincipal) > 0 && formStartDate) createMutation.mutate() }} className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2"><Label htmlFor="position-side">Lado</Label><select id="position-side" value={formSide} onChange={(event) => setFormSide(event.target.value as PositionSide)} className="h-9 w-full rounded-md border border-input bg-card px-3 text-sm"><option value="receivable">A receber</option><option value="liability">Passivo</option></select></div>
              <div className="space-y-2"><Label htmlFor="position-currency">Moeda</Label><Input id="position-currency" value={formCurrency} maxLength={3} onChange={(event) => setFormCurrency(event.target.value.toUpperCase())} /></div>
            </div>
            <div className="space-y-2"><Label htmlFor="position-name">Nome</Label><Input id="position-name" value={formName} onChange={(event) => setFormName(event.target.value)} placeholder="Ex.: Empréstimo ao irmão" required /></div>
            <div className="space-y-2"><Label htmlFor="position-counterparty">Contraparte (opcional)</Label><Input id="position-counterparty" value={formCounterparty} onChange={(event) => setFormCounterparty(event.target.value)} /></div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2"><Label htmlFor="position-principal">Principal original</Label><Input id="position-principal" type="number" min="0.01" step="0.01" value={formPrincipal} onChange={(event) => setFormPrincipal(event.target.value)} required /></div>
              <div className="space-y-2"><Label htmlFor="position-start">Data inicial</Label><Input id="position-start" type="date" value={formStartDate} onChange={(event) => setFormStartDate(event.target.value)} required /></div>
            </div>
            <div className="space-y-2"><Label htmlFor="position-due">Vencimento (opcional)</Label><Input id="position-due" type="date" value={formDueDate} onChange={(event) => setFormDueDate(event.target.value)} /></div>
            {createMutation.isError && <p className="text-sm text-destructive">Não foi possível criar a posição. Verifique os campos e tente novamente.</p>}
            <DialogFooter><Button type="button" variant="outline" onClick={() => setCreateOpen(false)}>Cancelar</Button><Button type="submit" disabled={createMutation.isPending || !formName.trim() || Number(formPrincipal) <= 0}>{createMutation.isPending ? 'Salvando…' : 'Criar posição'}</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={movementPosition !== null} onOpenChange={(open) => { if (!open) setMovementPosition(null) }}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader><DialogTitle>Registrar movimento{movementPosition ? ` · ${movementPosition.name}` : ''}</DialogTitle></DialogHeader>
          <form onSubmit={(event) => { event.preventDefault(); if (Number(movementPrincipal) > 0 && movementDate) movementMutation.mutate() }} className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2"><Label htmlFor="movement-kind">Tipo</Label><select id="movement-kind" value={movementKind} onChange={(event) => setMovementKind(event.target.value as typeof movementKind)} className="h-9 w-full rounded-md border border-input bg-card px-3 text-sm"><option value="increase">Aumento</option><option value="decrease">Redução</option><option value="writeoff">Baixa</option></select></div>
              <div className="space-y-2"><Label htmlFor="movement-date">Data efetiva</Label><Input id="movement-date" type="date" value={movementDate} onChange={(event) => setMovementDate(event.target.value)} required /></div>
            </div>
            <div className="space-y-2"><Label htmlFor="movement-principal">Principal</Label><Input id="movement-principal" type="number" min="0.01" step="0.01" value={movementPrincipal} onChange={(event) => setMovementPrincipal(event.target.value)} required /></div>
            <div className="grid grid-cols-3 gap-3">
              <div className="space-y-2"><Label htmlFor="movement-interest">Juros</Label><Input id="movement-interest" type="number" min="0" step="0.01" value={movementInterest} onChange={(event) => setMovementInterest(event.target.value)} /></div>
              <div className="space-y-2"><Label htmlFor="movement-fee">Taxa</Label><Input id="movement-fee" type="number" min="0" step="0.01" value={movementFee} onChange={(event) => setMovementFee(event.target.value)} /></div>
              <div className="space-y-2"><Label htmlFor="movement-tax">Imposto</Label><Input id="movement-tax" type="number" min="0" step="0.01" value={movementTax} onChange={(event) => setMovementTax(event.target.value)} /></div>
            </div>
            {movementMutation.isError && <p className="text-sm text-destructive">Não foi possível registrar o movimento. Verifique os valores.</p>}
            <DialogFooter><Button type="button" variant="outline" onClick={() => setMovementPosition(null)}>Cancelar</Button><Button type="submit" disabled={movementMutation.isPending || Number(movementPrincipal) <= 0}>{movementMutation.isPending ? 'Salvando…' : 'Registrar movimento'}</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Card className="mb-5 border-sky-500/30 bg-sky-500/[0.04]">
        <CardContent className="flex items-start gap-3 py-4 text-sm text-muted-foreground">
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-sky-600" />
          <p>Posições são mantidas em um ledger separado de ativos. O saldo abaixo é calculado pelos movimentos registrados; esta tela não altera lançamentos.</p>
        </CardContent>
      </Card>

      <div className="mb-5 grid gap-3 sm:grid-cols-2">
        <Card>
          <CardHeader className="pb-2"><CardDescription>A receber</CardDescription><CardTitle className="text-2xl">{receivables.length.toLocaleString()}</CardTitle></CardHeader>
          <CardContent className="pt-0"><p className="text-xs text-muted-foreground">{sideTotals(receivables, 'receivable').join(' · ') || 'nenhum valor'}</p></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardDescription>Passivos</CardDescription><CardTitle className="text-2xl">{liabilities.length.toLocaleString()}</CardTitle></CardHeader>
          <CardContent className="pt-0"><p className="text-xs text-muted-foreground">{sideTotals(liabilities, 'liability').join(' · ') || 'nenhum valor'}</p></CardContent>
        </Card>
      </div>

      <Card className="mb-5">
        <CardContent className="flex flex-wrap items-end gap-3 py-4">
          <div className="min-w-[220px] flex-1 space-y-2">
            <Label htmlFor="position-search">Buscar</Label>
            <Input id="position-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Nome ou contraparte" />
          </div>
          <div className="flex items-center gap-1 rounded-lg border border-border bg-muted/40 p-1">
            {(['all', 'receivable', 'liability'] as SideFilter[]).map((option) => (
              <button key={option} type="button" onClick={() => setSide(option)} className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${side === option ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}>
                {option === 'all' ? 'Todas' : sideLabel(option)}
              </button>
            ))}
          </div>
          <Button type="button" variant={includeArchived ? 'secondary' : 'outline'} size="sm" onClick={() => setIncludeArchived((value) => !value)}>
            <Archive className="h-4 w-4" /> {includeArchived ? 'Ocultar arquivadas' : 'Mostrar arquivadas'}
          </Button>
        </CardContent>
      </Card>

      {query.isError && <Card className="mb-5 border-destructive/40"><CardContent className="py-4 text-sm text-destructive">Não foi possível carregar as posições.</CardContent></Card>}
      {query.isLoading && <Card><CardContent className="py-6 text-sm text-muted-foreground">Carregando posições…</CardContent></Card>}

      {!query.isLoading && filtered.length === 0 && <Card><CardContent className="py-10 text-center text-sm text-muted-foreground">Nenhuma posição encontrada.</CardContent></Card>}

      {filtered.length > 0 && (
        <div className="space-y-3">
          {filtered.map((item) => {
            const expanded = expandedId === item.id
            return (
              <Card key={item.id} className={item.is_archived ? 'opacity-60' : undefined}>
                <CardContent className="p-4 sm:p-5">
                  <div className="flex items-start gap-3">
                    <div className={`mt-0.5 rounded-lg p-2 ${item.side === 'receivable' ? 'bg-emerald-500/10 text-emerald-600' : 'bg-rose-500/10 text-rose-600'}`}><Scale className="h-4 w-4" /></div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h2 className="font-semibold text-foreground truncate">{item.name}</h2>
                        <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${item.side === 'receivable' ? 'bg-emerald-500/10 text-emerald-700' : 'bg-rose-500/10 text-rose-700'}`}>{sideLabel(item.side)}</span>
                        {item.is_archived && <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-semibold text-muted-foreground">arquivada</span>}
                      </div>
                      <p className="mt-0.5 text-xs text-muted-foreground">{item.counterparty || 'Contraparte não informada'} · início {dateLabel(item.start_date, dateLocale)} · {item.due_date ? `vencimento ${dateLabel(item.due_date, dateLocale)}` : 'sem vencimento'}</p>
                    </div>
                    <div className="text-right">
                      <p className={`font-semibold tabular-nums ${item.side === 'receivable' ? 'text-emerald-600' : 'text-rose-600'}`}>{displayAmount(item.balance, item.currency, locale, mask)}</p>
                      <p className="text-[10px] text-muted-foreground">{item.status} · {item.liquidity}</p>
                    </div>
                    <button type="button" onClick={() => setExpandedId(expanded ? null : item.id)} className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={expanded ? 'Ocultar movimentos' : 'Mostrar movimentos'}>
                      {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                    </button>
                  </div>
                  {expanded && (
                    <div className="mt-4 border-t border-border pt-3">
                      <div className="mb-2 flex items-center justify-between gap-2 text-xs font-semibold text-muted-foreground"><span className="flex items-center gap-2"><CheckCircle2 className="h-3.5 w-3.5" /> Ledger de movimentos</span>{canWrite && <Button type="button" variant="outline" size="xs" onClick={() => setMovementPosition(item)}>Registrar movimento</Button>}</div>
                      {item.movements.length === 0 ? <p className="text-xs text-muted-foreground">Nenhum movimento.</p> : <div className="space-y-1.5">{item.movements.map((movement) => <div key={movement.id} className="flex flex-wrap items-center justify-between gap-2 text-xs"><span className="text-muted-foreground">{movement.kind} · {dateLabel(movement.effective_date, dateLocale)}{movement.reversed_at ? ' · revertido' : ''}</span><span className="flex items-center gap-2 font-medium tabular-nums">{movement.kind === 'decrease' || movement.kind === 'writeoff' ? '−' : '+'}{displayAmount(movement.principal_amount, item.currency, locale, mask)}{canWrite && !movement.reversed_at && <button type="button" className="text-[10px] text-muted-foreground underline hover:text-rose-600" onClick={() => { if (window.confirm('Reverter este movimento? O histórico será preservado.')) reverseMutation.mutate({ positionId: item.id, movementId: movement.id }) }}>reverter</button>}</span></div>)}</div>}
                    </div>
                  )}
                </CardContent>
              </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}
