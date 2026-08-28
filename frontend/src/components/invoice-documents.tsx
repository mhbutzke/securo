import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { FileText, Paperclip, Star, Trash2, Upload } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { IconAction, SectionCard, SectionHeader, TH } from '@/components/invoice-ui'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { invoices as invoicesApi } from '@/lib/api'
import { formatFileSize, previewKind } from '@/lib/invoice-utils'
import { useDateLocale } from '@/hooks/use-display-locale'
import type { InvoiceAttachment, InvoiceAttachmentKind } from '@/types'

/**
 * The paper gathered under one invoice.
 *
 * An invoice is often a folder rather than a document: the supplier's
 * bill arrives by email, the fiscal document follows days later from a
 * government portal, the receipt after that. This is where they land,
 * and the starred one is the file that *is* the invoice — downloading
 * the invoice hands that file over instead of a page we drew ourselves.
 */

const KINDS: InvoiceAttachmentKind[] = ['bill', 'fiscal', 'receipt', 'contract', 'other']

export function InvoiceDocuments({
  invoiceId,
  canWrite,
  onChanged,
}: {
  invoiceId: string
  canWrite: boolean
  onChanged?: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const dateLocale = useDateLocale()
  const fileInput = useRef<HTMLInputElement>(null)
  const [kind, setKind] = useState<InvoiceAttachmentKind>('bill')
  const [error, setError] = useState<string | null>(null)

  const { data: attachments = [] } = useQuery({
    queryKey: ['invoice-attachments', invoiceId],
    queryFn: () => invoicesApi.attachments.list(invoiceId),
  })

  const refresh = () => {
    // Clearing the error here, not only after an upload: a refused file
    // leaves a message on screen, and if it survives the next successful
    // action it stops describing anything that is true.
    setError(null)
    void queryClient.invalidateQueries({ queryKey: ['invoice-attachments', invoiceId] })
    void queryClient.invalidateQueries({ queryKey: ['invoice-document', invoiceId] })
    onChanged?.()
  }

  const uploadMutation = useMutation({
    mutationFn: (file: File) => invoicesApi.attachments.upload(invoiceId, file, { kind }),
    onSuccess: refresh,
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(detail ?? t('invoices.documents.uploadFailed'))
    },
  })

  const primaryMutation = useMutation({
    mutationFn: (id: string) =>
      invoicesApi.attachments.update(invoiceId, id, { is_primary: true }),
    onSuccess: refresh,
  })

  const removeMutation = useMutation({
    mutationFn: (id: string) => invoicesApi.attachments.remove(invoiceId, id),
    onSuccess: refresh,
  })

  const open = async (attachment: InvoiceAttachment) => {
    const url = await invoicesApi.attachments.blobUrl(invoiceId, attachment.id)
    window.open(url, '_blank', 'noopener')
    // Released on the next tick: revoking immediately races the new tab,
    // which has not read the blob yet.
    setTimeout(() => URL.revokeObjectURL(url), 60_000)
  }

  const showDate = (value: string) =>
    new Date(`${value}T00:00:00`).toLocaleDateString(dateLocale)

  return (
    <SectionCard>
      <SectionHeader
        title={t('invoices.documents.title')}
        action={
          canWrite ? (
            <div className="flex items-center gap-2">
              <Select value={kind} onValueChange={(v) => setKind(v as InvoiceAttachmentKind)}>
                <SelectTrigger className="h-8 w-[150px] text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {KINDS.map((k) => (
                    <SelectItem key={k} value={k} className="text-xs">
                      {t(`invoices.documents.kind.${k}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                size="sm"
                variant="outline"
                disabled={uploadMutation.isPending}
                onClick={() => fileInput.current?.click()}
              >
                <Upload className="h-3.5 w-3.5 mr-1.5" />
                {t('invoices.documents.add')}
              </Button>
              <input
                ref={fileInput}
                type="file"
                className="hidden"
                data-testid="invoice-document-input"
                onChange={(event) => {
                  const file = event.target.files?.[0]
                  if (file) uploadMutation.mutate(file)
                  event.target.value = ''
                }}
              />
            </div>
          ) : undefined
        }
      />

      {error && (
        <p className="px-4 sm:px-5 pt-3 text-xs text-destructive">{error}</p>
      )}

      {attachments.length === 0 ? (
        <p
          className="px-4 sm:px-5 py-8 text-center text-sm text-muted-foreground"
          data-testid="invoice-no-documents"
        >
          {t('invoices.documents.empty')}
        </p>
      ) : (
        <table className="w-full">
          <thead>
            <tr className="border-b border-border">
              <th className={`${TH} text-left pl-4 sm:pl-5`}>
                {t('invoices.documents.file')}
              </th>
              <th className={`${TH} text-left hidden sm:table-cell`}>
                {t('invoices.documents.reference')}
              </th>
              <th className={`${TH} text-right pr-4 sm:pr-5 w-28`}>
                <span className="sr-only">{t('invoices.documents.actions')}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {attachments.map((attachment: InvoiceAttachment) => (
              <tr
                key={attachment.id}
                data-testid="invoice-document-row"
                className="border-b border-border last:border-0"
              >
                <td className="py-3 pl-4 sm:pl-5">
                  <button
                    onClick={() => void open(attachment)}
                    className="flex items-center gap-2 text-left group"
                  >
                    <FileText className="h-4 w-4 text-muted-foreground shrink-0" />
                    <span>
                      <span className="text-sm font-medium text-foreground group-hover:text-primary truncate block">
                        {attachment.filename}
                        {attachment.is_primary && (
                          <span
                            className="ml-2 text-[11px] font-semibold px-2 py-0.5 rounded-full border border-primary/20 bg-primary/5 text-primary align-middle"
                            title={t('invoices.documents.primaryHint')}
                          >
                            {t('invoices.documents.primary')}
                          </span>
                        )}
                      </span>
                      <span className="text-xs text-muted-foreground mt-0.5 block">
                        {t(`invoices.documents.kind.${attachment.kind}`)}
                        {' · '}
                        {formatFileSize(attachment.size)}
                        {attachment.issued_at ? ` · ${showDate(attachment.issued_at)}` : ''}
                      </span>
                    </span>
                  </button>
                </td>
                <td className="py-3 hidden sm:table-cell">
                  <span className="text-xs text-muted-foreground tabular-nums break-all">
                    {attachment.document_number ?? ''}
                  </span>
                </td>
                <td className="py-3 pr-4 sm:pr-5 text-right whitespace-nowrap">
                  {canWrite && (
                    <>
                      {!attachment.is_primary && (
                        <IconAction
                          onClick={() => primaryMutation.mutate(attachment.id)}
                          label={t('invoices.documents.makePrimary')}
                        >
                          <Star className="h-4 w-4" />
                        </IconAction>
                      )}
                      <IconAction
                        onClick={() => removeMutation.mutate(attachment.id)}
                        label={t('invoices.documents.remove')}
                        destructive
                      >
                        <Trash2 className="h-4 w-4" />
                      </IconAction>
                    </>
                  )}
                  {!canWrite && <Paperclip className="h-4 w-4 text-muted-foreground inline" />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </SectionCard>
  )
}


/**
 * The filed document itself, shown instead of a page we would have drawn.
 *
 * When a supplier sends a PDF, that file *is* the invoice. Redrawing it
 * from our own fields produces something that looks official and is not,
 * and on a bill we received it would be a page nobody issued. So when a
 * primary file exists, this is what the Document tab shows.
 */
export function InvoiceSourceDocument({
  invoiceId,
  file,
}: {
  invoiceId: string
  file: { id: string; filename: string; content_type: string }
}) {
  const { t } = useTranslation()
  const [url, setUrl] = useState<string | null>(null)

  useEffect(() => {
    let revoked: string | null = null
    let cancelled = false
    void invoicesApi.attachments.blobUrl(invoiceId, file.id).then((next: string) => {
      if (cancelled) {
        URL.revokeObjectURL(next)
        return
      }
      revoked = next
      setUrl(next)
    })
    return () => {
      cancelled = true
      if (revoked) URL.revokeObjectURL(revoked)
    }
  }, [invoiceId, file.id])

  const preview = previewKind(file.content_type)

  return (
    <div className="rounded-xl border border-border bg-muted/50 p-3 sm:p-8">
      <div className="mx-auto mb-3 max-w-[794px] text-xs text-muted-foreground">
        <span className="font-medium text-foreground">{t('invoices.documents.original')}</span>{' '}
        {t('invoices.documents.originalHint', { filename: file.filename })}
      </div>
      <div
        className="mx-auto max-w-[794px] rounded-sm bg-white shadow-[0_1px_2px_rgba(0,0,0,0.08),0_12px_32px_-10px_rgba(0,0,0,0.22)] overflow-hidden"
        data-testid="invoice-source-document"
      >
        {!url ? (
          <div className="h-[520px]" />
        ) : preview === 'pdf' ? (
          // An <iframe>, not an <object>: both hand the file to Chrome's
          // PDF viewer, but the object element is a replaced element the
          // plugin can resize past its box, and it takes the page layout
          // with it. The iframe stays in the frame it was given.
          <iframe
            src={url}
            title={file.filename}
            className="w-full h-[1123px] max-h-[80vh] border-0 block"
          />
        ) : preview === 'image' ? (
          <img src={url} alt={file.filename} className="w-full" />
        ) : (
          <div className="p-8 text-center">
            <p className="text-sm text-muted-foreground">
              {t('invoices.documents.cannotPreview')}
            </p>
          </div>
        )}
      </div>
      {url && (
        <div className="mx-auto mt-3 max-w-[794px] text-center">
          <Button size="sm" variant="outline" onClick={() => window.open(url, '_blank', 'noopener')}>
            <FileText className="h-3.5 w-3.5 mr-1.5" />
            {t('invoices.documents.openOriginal')}
          </Button>
        </div>
      )}
    </div>
  )
}


/**
 * An imported invoice whose file has not been filed yet.
 *
 * Drawing our own page here would be the very thing the aggregator
 * exists to stop: a blank sheet in our layout, standing in for a
 * document a supplier issued and we have not received a copy of. Better
 * to say what is missing and where it goes.
 */
export function MissingSourceDocument() {
  const { t } = useTranslation()
  return (
    <div
      className="rounded-xl border border-border bg-muted/50 px-6 py-16 text-center"
      data-testid="invoice-missing-source"
    >
      <FileText className="h-6 w-6 mx-auto text-muted-foreground" />
      <p className="mt-3 text-sm font-medium text-foreground">
        {t('invoices.documents.notOurs')}
      </p>
      <p className="mt-1 text-sm text-muted-foreground max-w-md mx-auto">
        {t('invoices.documents.notOursHint')}
      </p>
    </div>
  )
}
