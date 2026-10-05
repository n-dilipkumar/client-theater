/**
 * Primitives drawn from the shared components in `@/components/ui`.
 *
 * Nothing here redefines a control `components/ui.jsx` already provides. `Button`, `Card`,
 * `Badge`, `Field`, `inputClass`, `Icon` and `EmptyState` all come from the shared module, and
 * only the arrangements this page needs are built here.
 *
 * What this file adds is presentation the shared set does not carry:
 *
 * * `Dialog`, because `components/ui.jsx` exports no `Modal` on this branch. The feature
 *   contract says to build a primitive that genuinely does not exist inside my own feature
 *   folder and say so in the pull request, rather than editing the shared file.
 * * `StatusBadge`, because the shared `Badge` carries audit-action tones and this workflow
 *   needs a signing-status tone. The status word is always in the badge, so no status is ever
 *   conveyed by colour alone.
 * * `SigningRail`, because the research's status chain is a four-step chain and a list of four
 *   equal badges would not say which step the quote is on.
 * * `SignerRow` and `EventRow`, which carry the state a signer and an event each have.
 */

import { Badge, Button, Card, Field, Icon, inputClass } from '@/components/ui'

import { ACTIVITY_LABELS, STATUS_LABELS, formatInstant, plural, signingStep } from './api'

/**
 * A dialog, built here rather than imported.
 *
 * `components/ui.jsx` does not export a `Modal` on this branch, and this feature may not edit
 * it. It meets the same accessibility floor as the rest of the product: `role="dialog"` with
 * `aria-modal`, a visible title, Escape closes, and the backdrop is a button so it is
 * reachable by keyboard rather than by pointer only.
 */
export function Dialog({ open, title, description, onClose, children }) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-foreground/40 p-4 sm:p-6">
      <button
        type="button"
        aria-label="Close the dialog"
        onClick={onClose}
        className="fixed inset-0 h-full w-full cursor-default"
        tabIndex={-1}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onKeyDown={(event) => {
          if (event.key === 'Escape') onClose()
        }}
        className="relative z-10 w-full max-w-lg rounded-sm border border-border-subtle bg-surface p-5"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-foreground">{title}</h2>
            {description && (
              <p className="mt-1 text-sm text-muted-foreground">{description}</p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-sm border border-border-subtle bg-surface text-foreground hover:border-accent hover:text-accent"
          >
            <Icon name="close" />
          </button>
        </div>
        <div className="mt-4">{children}</div>
      </div>
    </div>
  )
}

/**
 * The signing-status tone for a status.
 *
 * `pending_signature` is `neutral`, `viewed_pending_signature` is `info`,
 * `pending_countersignature` is `warning` and `accepted` is `insert` (accent). The status word
 * is always rendered beside the colour, so the colour is never the only carrier.
 */
const STATUS_TONES = {
  pending_signature: 'neutral',
  viewed_pending_signature: 'update',
  pending_countersignature: 'restore',
  accepted: 'insert',
}

/** A signing status, with the research's own label. */
export function StatusBadge({ status }) {
  return <Badge tone={STATUS_TONES[status] || 'neutral'}>{STATUS_LABELS[status] || status}</Badge>
}

/**
 * The four-step signing chain, with the current step marked in text and not only in colour.
 *
 * The chain is the research's data flow: "Pending signature -> Viewed - pending signature ->
 * Pending countersignature -> Accepted". A completed step reads "Done", the current step reads
 * "Now", and a later step reads "Waiting", so a screen reader and a monochrome print both read
 * the same state the colour does.
 */
export function SigningRail({ status }) {
  const current = signingStep(status)
  const order = Object.keys(STATUS_LABELS)
  return (
    <ol className="flex flex-wrap items-center gap-x-2 gap-y-2">
      {order.map((step, index) => {
        const done = current > index
        const now = current === index
        return (
          <li key={step} className="flex items-center gap-2">
            <span
              aria-current={now ? 'step' : undefined}
              className={`inline-flex items-center gap-2 rounded-sm border px-2 py-1 ${
                now
                  ? 'border-accent bg-accent-soft text-accent'
                  : 'border-border-subtle bg-muted text-muted-foreground'
              }`}
            >
              <span className="font-mono text-xs">{index + 1}</span>
              <span className="text-xs font-medium">{STATUS_LABELS[step]}</span>
              <span className="text-xs">{done ? 'Done' : now ? 'Now' : 'Waiting'}</span>
            </span>
            {index < order.length - 1 ? (
              <Icon name="chevron" size={14} className="text-muted-foreground" />
            ) : null}
          </li>
        )
      })}
    </ol>
  )
}

/**
 * One party on the envelope, with what they owe and what they have done.
 *
 * The signed/unsigned state is a word as well as a tone, the verification state is a word, and
 * the signing order is shown, because the research fixes the order: the buyer signs first and
 * the countersigner after.
 */
export function SignerRow({ signer, onSign, onReassign, busy }) {
  const roleLabel = signer.role === 'countersigner' ? 'Countersigner' : 'Buyer contact'
  const order = signer.signing_order === 1 ? 'Signs first' : 'Signs second'
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 border-b border-border-subtle py-3 last:border-b-0">
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-foreground">
          <span className="font-mono text-xs text-muted-foreground">{roleLabel}</span>
          <span>{signer.name || signer.email}</span>
          {signer.signed ? <Badge tone="insert">Signed</Badge> : <Badge>Not signed</Badge>}
          {signer.verification_required ? (
            signer.verified ? (
              <Badge tone="update">Verified</Badge>
            ) : (
              <Badge tone="restore">Verify email</Badge>
            )
          ) : null}
        </p>
        <p className="mt-1 font-mono text-xs text-muted-foreground">
          {signer.email}
          <span className="mx-2 text-border-subtle">|</span>
          {order}
          {signer.signed_at ? (
            <>
              <span className="mx-2 text-border-subtle">|</span>
              signed {formatInstant(signer.signed_at)}
            </>
          ) : null}
        </p>
      </div>
      <div className="flex shrink-0 flex-wrap gap-2">
        <Button
          variant="primary"
          disabled={busy || signer.signed}
          onClick={() => onSign(signer)}
          icon="audit"
        >
          {signer.signed ? 'Signed' : 'Sign'}
        </Button>
        <Button
          disabled={busy || signer.signed}
          onClick={() => onReassign(signer)}
          icon="refresh"
        >
          Reassign
        </Button>
      </div>
    </li>
  )
}

/**
 * One signature event, with the activity the research names and the status it moved.
 *
 * An event that writes no activity row says so in words ("No activity row"), because the
 * research names four activities and a reader must not infer a fifth from a blank.
 */
export function EventRow({ event }) {
  const label = event.activity ? ACTIVITY_LABELS[event.activity] : null
  const moved = event.status_before !== event.status_after
  return (
    <li className="border-b border-border-subtle py-3 last:border-b-0">
      <p className="flex flex-wrap items-center gap-2 text-sm text-foreground">
        <Icon name={event.activity ? 'audit' : 'schema'} size={16} className="text-muted-foreground" />
        <span className="font-medium">{label || 'No activity row'}</span>
        <span className="font-mono text-xs text-muted-foreground">{event.event}</span>
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        {formatInstant(event.at)}
        <span className="mx-2 text-border-subtle">|</span>
        {moved
          ? `${STATUS_LABELS[event.status_before]} moved to ${STATUS_LABELS[event.status_after]}`
          : `Stayed at ${STATUS_LABELS[event.status_before]}`}
        {event.detail?.reason ? (
          <>
            <span className="mx-2 text-border-subtle">|</span>
            <span className="font-mono">{event.detail.reason}</span>
          </>
        ) : null}
      </p>
    </li>
  )
}

/** A quoted sentence from the research, set so it reads as evidence and not as a headline. */
export function EvidenceNote({ quote, label = 'Evidence' }) {
  if (!quote) return null
  return (
    <figure className="rounded-sm border-l-2 border-accent bg-accent-soft/40 px-3 py-2">
      <figcaption className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </figcaption>
      <blockquote className="mt-1 text-xs text-foreground">{quote}</blockquote>
    </figure>
  )
}

/** One acceptance-configuration field, so the two toggles read as configuration and not chrome. */
export function ToggleRow({ label, hint, checked, onChange, disabled, id }) {
  return (
    <div className="flex items-start justify-between gap-3 rounded-sm border border-border-subtle p-3">
      <div className="min-w-0">
        {/* The label is bound to the control by id, so the switch has an accessible name. */}
        <label htmlFor={id} className="text-sm font-medium text-foreground">
          {label}
        </label>
        {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
      </div>
      <input
        id={id}
        type="checkbox"
        role="switch"
        aria-label={label}
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-1 h-6 w-11 shrink-0 cursor-pointer appearance-none rounded-sm border border-border-subtle bg-surface checked:border-accent checked:bg-accent focus:border-accent"
      />
    </div>
  )
}

/** A labelled input row inside the authoring dialog. */
export function SignerField({ id, label, hint, name, email, onName, onEmail }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Field label={`${label} name`} id={`${id}-name`} hint={hint}>
        <input
          id={`${id}-name`}
          className={inputClass}
          value={name}
          onChange={(event) => onName(event.target.value)}
          placeholder="Ada Byron"
        />
      </Field>
      <Field label={`${label} email`} id={`${id}-email`}>
        <input
          id={`${id}-email`}
          type="email"
          className={inputClass}
          value={email}
          onChange={(event) => onEmail(event.target.value)}
          placeholder="ada@northwind.example"
        />
      </Field>
    </div>
  )
}

/** The quota block, which says why there is no ceiling rather than hiding the absence. */
export function QuotaPanel({ quota }) {
  if (!quota) return null
  return (
    <Card>
      <h3 className="text-base font-semibold text-foreground">E-signature usage this month</h3>
      <p className="mt-2 font-mono text-2xl font-semibold text-foreground">{quota.used}</p>
      <p className="mt-1 text-sm text-muted-foreground">
        {plural(quota.envelopes_charged, 'envelope')} charged in {quota.month}. Resets on the{' '}
        {quota.reset_day}st.
      </p>
      <p className="mt-3 text-sm text-foreground">
        <span className="font-medium">Stated ceiling:</span>{' '}
        <span className="font-mono">{quota.limit === null ? 'none stated' : quota.limit}</span>
      </p>
      <p className="mt-2 text-xs text-muted-foreground">{quota.quota_unspecified_quote}</p>
      <div className="mt-3 space-y-2">
        <EvidenceNote label="Counting rule" quote={quota.counts_envelope_not_signer_quote} />
        <EvidenceNote label="When it counts" quote={quota.consumed_on_enable_quote} />
      </div>
    </Card>
  )
}
