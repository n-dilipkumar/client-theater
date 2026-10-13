/**
 * Primitives this page needs that `components/ui.jsx` does not carry.
 *
 * The feature contract says to reach for the shared components first and to build
 * anything extra inside the feature folder rather than edit `ui.jsx`, because a
 * hundred features each shipping their own modal means a hundred slightly
 * different dialogs in one product. These four are recorded as promotion work:
 * `Notice`, `Modal` and `Toggle` in particular are the kind of primitive the
 * integrator lifts into `ui.jsx` once, when a second feature needs them too.
 *
 * Everything here still obeys the design system floor: semantic tokens only, no
 * raw hex, `rounded-sm` and never a pill, a 44px minimum target on every control,
 * a visible focus ring, and a text label beside every icon.
 */

import { useEffect, useRef } from 'react'

import { Button, Field, Icon, inputClass } from '@/components/ui'

/**
 * An inline message, and a way to dismiss it.
 *
 * `tone` maps onto the semantic status tokens and is always accompanied by a
 * heading word, so the meaning never rests on the colour alone. That is the
 * accessibility floor in the design system and it is not optional.
 */
export function Notice({ tone = 'info', title, children, onDismiss }) {
  const tones = {
    info: 'bg-info/10 border-info/30 text-info',
    success: 'bg-success/10 border-success/30 text-success',
    warning: 'bg-warning/10 border-warning/30 text-warning',
    danger: 'bg-destructive/10 border-destructive/30 text-destructive',
  }
  return (
    <div
      role="status"
      className={`flex items-start gap-3 rounded-sm border p-3 ${tones[tone] || tones.info}`}
    >
      <span className="mt-0.5 shrink-0" aria-hidden="true">
        <Icon name="audit" size={16} />
      </span>
      <div className="min-w-0 flex-1">
        {title && <p className="text-sm font-medium">{title}</p>}
        <div className="text-[13px] leading-relaxed">{children}</div>
      </div>
      {onDismiss && (
        <Button variant="ghost" onClick={onDismiss} aria-label="Dismiss this message">
          <Icon name="close" size={16} />
        </Button>
      )}
    </div>
  )
}

/**
 * A dialog, drawn with the shared `Modal`-in-a-box shape.
 *
 * The whole overlay is a button so a click outside closes it, and Escape closes
 * it too. Focus moves into the panel when the dialog opens and returns to
 * whatever opened it when it closes, because a keyboard user who is dropped
 * back at the top of the document has lost their place.
 */
export function Modal({ open, title, description, onClose, children, footer }) {
  const panel = useRef(null)
  const opener = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    opener.current = document.activeElement
    panel.current?.focus()
    const onKey = (event) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      if (opener.current instanceof HTMLElement) opener.current.focus()
    }
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-foreground/40 p-4 sm:p-8">
      <button
        type="button"
        aria-label="Close this dialog"
        className="absolute inset-0 h-full w-full cursor-default"
        onClick={onClose}
      />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className="relative z-10 w-full max-w-2xl rounded-sm border border-border-subtle bg-surface p-5"
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="font-display text-lg font-semibold text-foreground">{title}</h2>
            {description && (
              <p className="mt-1 text-sm text-muted-foreground">{description}</p>
            )}
          </div>
          <Button variant="ghost" onClick={onClose} aria-label="Close this dialog">
            <Icon name="close" size={16} />
          </Button>
        </div>
        <div className="mt-4 space-y-4">{children}</div>
        {footer && <div className="mt-5 flex flex-wrap justify-end gap-2">{footer}</div>}
      </div>
    </div>
  )
}

/**
 * A switch with a visible label beside it.
 *
 * The label is the control's name, not a caption next to it, so a screen reader
 * announces "Active" rather than an unlabelled switch. The track carries an
 * `aria-checked` state and the whole row is a 44px target.
 */
export function Toggle({ checked, onChange, label, hint, disabled, id }) {
  return (
    <div className="flex flex-col gap-1.5">
      <button
        type="button"
        id={id}
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className="flex min-h-11 items-center gap-3 rounded-sm border border-border-subtle bg-surface px-3 py-2 text-left disabled:cursor-not-allowed disabled:opacity-60"
      >
        <span
          aria-hidden="true"
          className={`relative inline-flex h-5 w-9 shrink-0 rounded-sm border transition-colors ${
            checked ? 'border-accent bg-accent' : 'border-border-subtle bg-muted'
          }`}
        >
          <span
            className={`absolute top-0.5 h-3.5 w-3.5 rounded-xs bg-surface transition-all ${
              checked ? 'left-4.5' : 'left-0.5'
            }`}
          />
        </span>
        <span className="text-[13px] font-medium text-foreground">{label}</span>
      </button>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

/**
 * A labelled number input that reads its value as a number or as nothing.
 *
 * An empty box is `''`, not `0`, and that distinction matters here: a tier
 * minimum left blank must be a refusal that names the field, not a silent zero
 * that becomes the range every quantity below matches.
 */
export function NumberField({ label, hint, value, onChange, id, min, step = 1, placeholder }) {
  return (
    <Field label={label} hint={hint} id={id}>
      <input
        id={id}
        type="number"
        inputMode="decimal"
        min={min}
        step={step}
        placeholder={placeholder}
        value={value}
        onChange={(event) => {
          const raw = event.target.value
          onChange(raw === '' ? '' : Number(raw))
        }}
        className={inputClass}
      />
    </Field>
  )
}

/**
 * A labelled checkbox with a 44px target.
 *
 * The label is the accessible name, so a screen reader announces "USD" rather
 * than an unlabelled box. The whole row is the target, because a 16px box is not
 * a touch target on a phone.
 */
export function Checkbox({ id, label, checked, onChange, disabled }) {
  return (
    <label
      htmlFor={id}
      className="flex min-h-11 cursor-pointer items-center gap-2 rounded-sm border border-border-subtle bg-surface px-3 text-[13px] font-medium text-foreground"
    >
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="h-4 w-4 accent-accent"
      />
      {label}
    </label>
  )
}

/**
 * A small key and value row, for the machine values on a card.
 *
 * The value is set in mono because it is an id, a currency code or a count. The
 * label is not: a label is prose, and the design system says mono is for machine
 * values rather than for sentences.
 */
export function MetaRow({ label, value, mono = false }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className={`text-[13px] text-foreground ${mono ? 'font-mono' : ''}`}>{value}</span>
    </div>
  )
}