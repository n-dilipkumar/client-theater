/**
 * WF-094's own primitives.
 *
 * Everything here is built from `@/components/ui` and every button, badge and
 * input still reaches the accessibility floor: `min-h-11` on each control, a
 * visible label beside each icon, and no status carried by colour alone.
 *
 * Two of these are local because `ui.jsx` has no equivalent, and building them
 * here is the contract's instruction rather than an oversight. Promotion work
 * for the integrator, not a reason to edit a shared file:
 *
 *   - `StatusBadge`, because this workflow has five statuses and each needs a
 *     word beside its colour. `Badge` takes a tone but not a label mapping, and
 *     a status that is only a colour is unreadable to a colour-blind seller.
 *   - `CopyField`, because a seller who has just published a quote needs to
 *     select the link and see that the copy happened. The feedback is the whole
 *     point of the control and a plain `Field` cannot carry it.
 */

import { useState } from 'react'

import { Badge, Button, Field, Icon, inputClass } from '@/components/ui'

import { isLocked, isShareable, statusLabel, statusTone } from './api'

/**
 * An inline note about something that is about to happen, or did not.
 *
 * Local rather than shared, because `ui.jsx` has no `Notice` despite the feature
 * contract listing one. This page needs exactly three states and each names its
 * tone in words beside the colour, so the meaning never rests on the colour
 * alone.
 */
export function InlineNote({ tone = 'neutral', children }) {
  const tones = {
    neutral: 'border-border-subtle bg-muted text-muted-foreground',
    warning: 'border-warning/40 bg-warning/10 text-warning',
    info: 'border-info/40 bg-info/10 text-info',
    success: 'border-success/40 bg-success/10 text-success',
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

/**
 * A status as a word plus a colour.
 *
 * The word is always present. `Badge` alone would leave the state readable only
 * by its colour, which is not an option for a panel a seller uses to decide
 * whether to send a quote.
 */
export function StatusBadge({ quote }) {
  const status = quote?.data?.status
  return (
    <span className="inline-flex items-center gap-2">
      <Badge tone={statusTone(status)}>{statusLabel(status)}</Badge>
      {isLocked(quote) && <Badge tone="neutral">Total locked</Badge>}
      {!isLocked(quote) && isShareable(quote) && <Badge tone="neutral">Total editable</Badge>}
    </span>
  )
}

/**
 * A read-only field holding a URL, with a copy button.
 *
 * The confirmation is the reason this exists. A seller who copies a link has to
 * be able to tell that it happened, and a button that does nothing visible is
 * indistinguishable from one that failed.
 */
export function CopyField({ label, value, hint }) {
  const [copied, setCopied] = useState(false)
  const [failed, setFailed] = useState(false)

  async function copy() {
    setFailed(false)
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
    } catch {
      // A clipboard the browser refuses is not a reason to hide the link: the
      // field is selectable, so the seller can still take it by hand.
      setFailed(true)
    }
  }

  return (
    <div className="flex flex-col gap-1.5">
      <Field label={label} hint={hint} id="wf094-copy-field">
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            id="wf094-copy-field"
            className={inputClass}
            value={value}
            readOnly
            onFocus={(event) => event.target.select()}
            aria-describedby="wf094-copy-status"
          />
          <Button
            icon={copied ? 'plus' : 'audit'}
            onClick={copy}
            className="shrink-0"
            disabled={!value}
          >
            {copied ? 'Copied' : 'Copy link'}
          </Button>
        </div>
      </Field>
      <p id="wf094-copy-status" role="status" aria-live="polite" className="min-h-4 text-xs">
        {copied && (
          <span className="text-success">
            Copied. The buyer can open this link in a browser.
          </span>
        )}
        {failed && (
          <span className="text-warning">
            The browser refused the clipboard. Select the link and copy it by hand.
          </span>
        )}
      </p>
    </div>
  )
}

/**
 * A labelled cell whose value is rendered as children.
 *
 * Children rather than a `value` prop, because three of the four facts on a
 * quote are not plain text: the total is a figure plus a freeze label, and
 * passing that through a string prop would lose the label.
 */
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

/**
 * A sentence explaining that a figure is a fallback rather than a setting.
 *
 * A link served from the default domain looks exactly like a real one, so the
 * difference has to be stated rather than implied by a greyed-out field.
 */
export function FallbackNote({ children }) {
  return (
    <p className="flex items-start gap-2 text-xs text-warning">
      <Icon name="schema" size={14} className="mt-0.5 shrink-0" />
      <span>{children}</span>
    </p>
  )
}

/**
 * An amount with its currency-free formatting and a label saying it is frozen.
 *
 * The label matters more than the formatting. "2,850.00" on its own does not say
 * whether the seller may still change it.
 */
export function FrozenAmount({ amount, locked }) {
  if (amount === undefined || amount === null || amount === '') {
    return <span className="font-mono text-sm text-muted-foreground">Not computed yet</span>
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <span className="font-mono text-sm text-foreground">{Number(amount).toFixed(2)}</span>
      {locked ? (
        <Badge tone="neutral">Frozen on publish</Badge>
      ) : (
        <Badge tone="neutral">Still editable</Badge>
      )}
    </span>
  )
}