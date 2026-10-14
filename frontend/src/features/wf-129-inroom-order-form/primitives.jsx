/**
 * WF-129's own primitives.
 *
 * Everything here is built from `@/components/ui`, `ui.jsx` has no equivalent,
 * and every control still meets the accessibility floor: `min-h-11`, a visible
 * label beside every icon, and no status carried by colour alone.
 *
 * Three are local because the shared set has no equivalent, which the contract
 * names as the reason to build them here rather than edit `ui.jsx`:
 *
 *   - `StatusBadge`, because the order form's state is what the whole workflow
 *     turns on and it needs its word beside a colour.
 *   - `LinesTable`, because a buyer has to read the itemised lines they sign.
 *   - `CertificatePanel`, because the audit certificate is the artefact this
 *     workflow issues and it must state plainly that no provider verified it.
 */

import { Badge } from '@/components/ui'

import { modeLabel, statusLabel, statusTone } from './api'

/** An inline note whose meaning never rests on colour alone. */
export function InlineNote({ tone = 'neutral', children }) {
  const tones = {
    neutral: 'border-border-subtle bg-muted text-muted-foreground',
    warning: 'border-warning/40 bg-warning/10 text-warning',
    info: 'border-info/40 bg-info/10 text-info',
    success: 'border-success/40 bg-success/10 text-success',
    destructive: 'border-destructive/40 bg-destructive/10 text-destructive',
  }
  return (
    <p
      className={`rounded-sm border px-3 py-2 text-xs ${tones[tone] || tones.neutral}`}
      role="status"
      aria-live="polite"
    >
      {children}
    </p>
  )
}

/** A labelled cell whose value is rendered as children. */
export function Fact({ label, hint, children }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </p>
      <div className="min-w-0 truncate text-sm text-foreground">{children}</div>
      {hint && <p className="truncate text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

/** A currency amount, formatted, or a spelled-out absence. */
export function Money({ amount, currency }) {
  if (amount === undefined || amount === null || amount === '') {
    return <span className="font-mono text-sm text-muted-foreground">Not set</span>
  }
  return (
    <span className="font-mono text-sm text-foreground">
      {Number(amount).toFixed(2)}
      {currency ? ` ${currency}` : ''}
    </span>
  )
}

/** An order form status as a word plus a colour. */
export function StatusBadge({ status }) {
  return <Badge tone={statusTone(status)}>{statusLabel(status)}</Badge>
}

/** The itemised lines the buyer reads and signs. */
export function LinesTable({ lines }) {
  if (!lines || lines.length === 0) {
    return <p className="text-xs text-muted-foreground">No line items.</p>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">Itemised lines on the order form</caption>
        <thead>
          <tr className="border-b border-border-subtle text-left">
            <th scope="col" className="py-2 pr-4 text-xs font-medium text-muted-foreground">
              Item
            </th>
            <th scope="col" className="py-2 pr-4 text-xs font-medium text-muted-foreground">
              SKU
            </th>
            <th scope="col" className="py-2 pr-4 text-right text-xs font-medium text-muted-foreground">
              Qty
            </th>
            <th scope="col" className="py-2 pr-4 text-xs font-medium text-muted-foreground">
              Billing
            </th>
            <th scope="col" className="py-2 pr-4 text-right text-xs font-medium text-muted-foreground">
              Unit price
            </th>
            <th scope="col" className="py-2 text-right text-xs font-medium text-muted-foreground">
              Total
            </th>
          </tr>
        </thead>
        <tbody>
          {lines.map((line, index) => (
            <tr key={line.id || index} className="border-b border-border-subtle/60">
              <td className="py-2 pr-4 text-foreground">{line.name || 'Unnamed'}</td>
              <td className="py-2 pr-4 font-mono text-xs text-muted-foreground">
                {line.sku || 'n/a'}
              </td>
              <td className="py-2 pr-4 text-right font-mono text-xs text-foreground">
                {line.quantity}
              </td>
              <td className="py-2 pr-4 text-xs text-muted-foreground">
                {line.billing_frequency === 'recurring' ? 'Recurring' : 'One time'}
              </td>
              <td className="py-2 pr-4 text-right font-mono text-xs text-foreground">
                {Number(line.unit_price || 0).toFixed(2)}
              </td>
              <td className="py-2 text-right font-mono text-xs text-foreground">
                {Number(line.amounts?.total || 0).toFixed(2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * The audit certificate, and the three steps this workflow does not perform.
 *
 * The panel states in words that no e-sign provider verified the signature, and
 * that no PDF or DOCX was rendered. A certificate that only showed a hash would
 * read as a verified artefact, which it is not.
 */
export function CertificatePanel({ certificate }) {
  if (!certificate) return null
  return (
    <div className="rounded-sm border border-border-subtle bg-muted px-3 py-3">
      <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        Audit certificate
      </p>
      <dl className="mt-2 grid gap-2 sm:grid-cols-2">
        <div className="min-w-0">
          <dt className="text-xs text-muted-foreground">Signed by</dt>
          <dd className="truncate text-sm text-foreground">
            {certificate.signer?.name} ({certificate.signer?.email})
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-muted-foreground">Method</dt>
          <dd className="text-sm text-foreground">{modeLabel(certificate.signature_mode)}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-muted-foreground">Signed at</dt>
          <dd className="font-mono text-xs text-foreground">{certificate.signed_at || 'n/a'}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-muted-foreground">Document version</dt>
          <dd className="font-mono text-xs text-foreground">{certificate.document_version}</dd>
        </div>
        <div className="min-w-0 sm:col-span-2">
          <dt className="text-xs text-muted-foreground">Document SHA-256</dt>
          <dd className="break-all font-mono text-xs text-foreground">
            {certificate.document_sha256}
          </dd>
        </div>
      </dl>
      <p className="mt-3 text-xs text-muted-foreground">{certificate.statement}</p>
      <div className="mt-2 flex flex-wrap gap-2">
        <Badge tone="update">No provider verification</Badge>
        <Badge tone="neutral">No PDF or DOCX rendered</Badge>
      </div>
    </div>
  )
}
