/**
 * WF-100's own primitives.
 *
 * Every one of them is drawn from `@/components/ui`. Nothing here rebuilds a Card, a Badge or
 * a Button, because twelve features each shipping their own dialog is how one product ends up
 * with twelve different dialogs. What this file adds is the two shapes the shared set does not
 * carry: a section heading with a count and a hint, and a labelled value row that renders a
 * machine value in mono beside a prose label.
 *
 * Styling is semantic tokens only. A raw hex here would opt this page out of every future
 * theme change, and `index.css` is the enforceable source.
 */

import { Badge, Button, Card, Icon } from '@/components/ui'

/**
 * A checkbox with a visible label and a 44px touch target.
 *
 * Built here rather than imported because `docs/FEATURE-CONTRACT.md` lists `Checkbox` among the
 * shared primitives and `components/ui.jsx` does not export one. The contract says a primitive
 * that genuinely does not exist belongs in the feature folder until the integrator promotes it,
 * so this is a native input with the design system's tokens, not a fork of a shared component.
 */
export function CheckRow({ label, checked, onChange, id, hint }) {
  return (
    <div className="flex flex-col gap-1">
      <label className="flex min-h-11 items-center gap-2 text-sm text-foreground" htmlFor={id}>
        <input
          id={id}
          type="checkbox"
          checked={checked}
          onChange={(event) => onChange(event.target.checked)}
          className="h-4 w-4 shrink-0 accent-accent"
        />
        {label}
      </label>
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  )
}

/** A section heading with a count and a hint, at the level the design system reserves for it. */
export function SectionHeading({ title, hint, count }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <div>
        <h2 className="text-lg font-semibold text-foreground">{title}</h2>
        {hint ? <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p> : null}
      </div>
      {count ? <span className="font-mono text-xs text-muted-foreground">{count}</span> : null}
    </div>
  )
}

/**
 * One labelled value.
 *
 * `mono` marks a machine value: an id, a date, a count, an amount, a state. A sentence is
 * never set in mono, so a `label` plus a `value` pair reads as a definition list rather than
 * as a log line.
 */
export function ValueRow({ label, value, mono = false, tone }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-t border-border-subtle py-1.5 first:border-t-0">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span
        className={`text-right text-xs ${mono ? 'font-mono' : ''} ${
          tone === 'muted' ? 'text-muted-foreground' : 'text-foreground'
        }`}
      >
        {value}
      </span>
    </div>
  )
}

/** A value row whose value is a set of badges, for a field with several allowed values. */
export function BadgeRow({ label, badges }) {
  return (
    <div className="border-t border-border-subtle py-1.5 first:border-t-0">
      <span className="text-xs text-muted-foreground">{label}</span>
      <div className="mt-1 flex flex-wrap gap-1.5">
        {(badges || []).map((badge) => (
          <Badge key={badge.key || badge} tone={badge.tone || 'neutral'}>
            {badge.label || badge}
          </Badge>
        ))}
      </div>
    </div>
  )
}

/**
 * The renewal chain, walked in one direction.
 *
 * The chain is recorded on both contracts, so this renders a walk rather than a search. An
 * absent step renders as `none` rather than as an empty row, because "this contract starts a
 * chain" and "the chain link is missing" are different facts.
 */
export function ChainTrail({ chain, onOpen }) {
  const steps = [
    { key: 'previous', label: 'Replaced', step: chain?.previous },
    { key: 'self', label: 'This contract', step: null },
    { key: 'next', label: 'Replaced by', step: chain?.next },
  ]
  return (
    <ol className="space-y-1.5">
      {steps.map((entry) => (
        <li key={entry.key} className="flex items-center gap-2">
          <Icon name="chevron" size={14} className="text-muted-foreground" />
          <span className="w-32 shrink-0 text-xs text-muted-foreground">{entry.label}</span>
          {entry.key === 'self' ? (
            <span className="truncate text-xs font-medium text-foreground">
              the contract you are reading
            </span>
          ) : entry.step ? (
            <button
              type="button"
              onClick={() => onOpen?.(entry.step.id)}
              className="min-h-11 truncate text-left font-mono text-xs text-accent underline-offset-2 hover:underline"
            >
              {entry.step.name || entry.step.id}
            </button>
          ) : (
            <span className="text-xs text-muted-foreground">none</span>
          )}
        </li>
      ))}
    </ol>
  )
}

/**
 * A decision, with what it rejected and what the choice cost.
 *
 * The unsourced badge is not decoration. The specification asks an implementer to record the
 * derivation rather than assume it, so a reviewer has to be able to tell a sourced rule from a
 * judgement call at a glance.
 */
export function DecisionCard({ decision }) {
  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="text-base font-semibold text-foreground">{decision.question}</h3>
        <Badge tone={decision.sourced ? 'success' : 'warning'}>
          {decision.sourced ? 'From the research' : 'Decision, not sourced'}
        </Badge>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">{decision.left_open_by}</p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div>
          <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            What was chosen
          </p>
          <p className="mt-1 text-sm text-foreground">{decision.chosen}</p>
        </div>
        <div>
          <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            What was rejected, and what it would have cost
          </p>
          <p className="mt-1 text-sm text-foreground">{decision.rejected_because}</p>
        </div>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">{decision.cost_of_the_choice}</p>
      {decision.jev_audit_id ? (
        <p className="mt-2 font-mono text-[11px] text-muted-foreground">
          Jev {decision.jev_verdict} {decision.jev_audit_id}
        </p>
      ) : null}
    </Card>
  )
}

/**
 * A quote, with its state in words and never by colour alone.
 *
 * The badge carries the state name, and the row beneath it repeats what the state means, so a
 * reader who cannot see the colour still gets the fact. Every status in this feature is
 * conveyed by text as well as by tone.
 */
export function QuoteRow({ quote, state, onAccept, onShare, onOpen, busy }) {
  const accepted = quote.state === 'accepted'
  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={state.tone}>{state.label}</Badge>
            {quote.prorate === false ? <Badge tone="neutral">Proration cleared</Badge> : null}
          </div>
          <h3 className="mt-2 truncate text-base font-semibold text-foreground">
            {quote.name || 'Untitled renewal'}
          </h3>
          <p className="mt-0.5 break-all font-mono text-xs text-muted-foreground">{quote.id}</p>
          <p className="mt-1 text-xs text-muted-foreground">{state.meaning}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => onOpen?.(quote.id)}>Open</Button>
          {!accepted ? (
            <>
              {quote.state === 'draft' ? (
                <Button onClick={() => onShare?.(quote.id)} disabled={busy} icon="audit">
                  Mark shared
                </Button>
              ) : null}
              <Button
                variant="primary"
                onClick={() => onAccept?.(quote.id)}
                disabled={busy}
                icon="plus"
              >
                Record acceptance
              </Button>
            </>
          ) : null}
        </div>
      </div>
      <dl className="mt-3">
        <ValueRow label="Change effective date" value={quote.effective_date?.label || 'not set'} />
        <ValueRow
          label="Resolves to"
          value={quote.effective_date?.resolved ? quote.effective_date.on : 'not until accepted'}
          mono
        />
        <ValueRow
          label="Deal"
          value={quote.deal_stage ? `New deal in ${quote.deal_stage}` : 'New deal, stage not set'}
        />
        {quote.new_contract_id ? (
          <ValueRow label="New contract" value={quote.new_contract_id} mono />
        ) : null}
      </dl>
    </Card>
  )
}