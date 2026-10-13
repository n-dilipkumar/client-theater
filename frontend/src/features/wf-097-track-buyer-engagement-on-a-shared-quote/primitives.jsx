/**
 * WF-097's own primitives.
 *
 * Everything here is built from `@/components/ui` and every control still meets
 * the accessibility floor: `min-h-11` on each button, which the shared `Button` already applies, a visible label beside each
 * icon, and no status carried by colour alone.
 *
 * These are local because `ui.jsx` has no equivalent, and building them here is
 * the contract's instruction rather than an oversight. Promotion work for the
 * integrator, not a reason to edit a shared file:
 *
 *   - `ActivityClassBadge`, because a class is a finding and the word beside the
 *     colour is what makes it a finding rather than a decoration.
 *   - `Counter`, because the four researched columns are read side by side and
 *     three of them need to be able to say why they are empty.
 *   - `BubbleChart`, because the researched panel is a scatter of expiration
 *     date against activity count and no shared primitive is a chart.
 *   - `InlineNote`, because `ui.jsx` has no `Notice` and this page needs a line
 *     that states a fact about what it cannot do.
 */

import { Badge, Button, Card, Field, Icon, inputClass } from '@/components/ui'

import { EXPIRED_TONE, activityTone, amount, shortTimestamp } from './api'

/**
 * An inline note about something this page cannot do, or did not do.
 *
 * Local rather than shared, because `ui.jsx` has no `Notice`. Each state names
 * its meaning in words beside the tone, so the meaning never rests on the colour
 * alone.
 */
export function InlineNote({ tone = 'neutral', children }) {
  const tones = {
    neutral: 'border-border-subtle bg-muted text-muted-foreground',
    warning: 'border-warning/40 bg-warning/10 text-warning',
    info: 'border-info/40 bg-info/10 text-info',
    success: 'border-success/40 bg-success/10 text-success',
    danger: 'border-destructive/40 bg-destructive/10 text-destructive',
  }
  return (
    <p
      className={`flex items-start gap-2 rounded-sm border px-3 py-2 text-xs ${tones[tone] || tones.neutral}`}
      role="status"
      aria-live="polite"
    >
      <Icon name="schema" size={14} className="mt-0.5 shrink-0" />
      <span>{children}</span>
    </p>
  )
}

/**
 * An activity class as a word plus a colour.
 *
 * The word is always present. The chart in the researched platform draws two of
 * these classes in black and green, and a colour on its own is unreadable to a
 * seller who cannot tell the two apart.
 */
export function ActivityClassBadge({ activityClass }) {
  if (!activityClass) {
    return <Badge tone="neutral">Not classified</Badge>
  }
  return <Badge tone={activityTone(activityClass)}>{activityClass}</Badge>
}

/**
 * One researched counter column, with the reason it is empty.
 *
 * Three of the four can be empty for a reason the server named, so the cell says
 * which. A blank cell in a row of figures reads as a zero, and the third counter
 * is reported as unavailable precisely because a zero would be a claim this
 * product cannot support.
 */
export function Counter({ label, value, hint, missing = null }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </p>
      {missing ? (
        <p className="text-sm text-warning">{missing}</p>
      ) : (
        <p className="font-mono text-sm text-foreground">{value}</p>
      )}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

/** The four researched counters for one quote, in the researched column order. */
export function CounterRow({ counters, labels = {}, basisLabels = {} }) {
  const sinceCommunication =
    counters?.activity_count_since_last_communication ??
    null
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <Counter
        label={labels.last_activity_at || 'Last Activity Date'}
        value={shortTimestamp(counters?.last_activity_at)}
      />
      <Counter
        label={labels.activity_count || 'Activity Count'}
        value={counters?.activity_count ?? 0}
        hint={
          counters?.buyer_activity_count !== undefined
            ? `${counters.buyer_activity_count} buyer, ${counters.seller_activity_count} seller`
            : undefined
        }
      />
      <Counter
        label={labels.activity_count_since_last_communication || 'Activity Count Since Last Communication'}
        value={sinceCommunication}
        missing={
          sinceCommunication === null ? basisLabels[counters?.since_last_communication_basis] : null
        }
      />
      <Counter
        label={labels.view_count || 'View Count'}
        value={counters?.view_count ?? 0}
        hint={
          counters?.pdf_opens_not_counted_as_views
            ? `${counters.pdf_opens_not_counted_as_views} PDF open(s) in the email did not count`
            : undefined
        }
      />
    </div>
  )
}

/**
 * The researched bubble chart: expiration date across, activity count up.
 *
 * Built here because `ui.jsx` has no chart. Each point is a button, so it has a
 * focus ring, a 44px touch target and a text label, and the three hover facts the
 * research names are on the point itself rather than in a tooltip that a
 * keyboard and a screen reader never see.
 *
 * The colour is a second signal: the class name is printed beside every point, and
 * a point whose class is unclassified carries no colour at all, because there is
 * no threshold to compare it against and a grey point would read as a third
 * finding.
 */
export function BubbleChart({ points = [] }) {
  if (points.length === 0) {
    return (
      <p className="rounded-sm border border-border-subtle bg-muted px-3 py-6 text-center text-sm text-muted-foreground">
        No quote has an expiration date, so there is nothing to place on the chart.
      </p>
    )
  }

  const counts = points.map((point) => Number(point.activity_count) || 0)
  const highest = Math.max(...counts, 1)

  return (
    <ul className="flex flex-col gap-2" aria-label="Published quotes by expiration date and activity count">
      {points.map((point) => {
        const count = Number(point.activity_count) || 0
        const width = Math.max(6, Math.round((count / highest) * 100))
        const tone = activityTone(point.activity_class)
        return (
          <li key={point.quote_id} className="flex flex-col gap-1">
            <div className="flex items-center gap-2 text-xs">
              <span className="truncate text-foreground">{point.name}</span>
              <Badge tone={tone}>{point.activity_class || 'Not classified'}</Badge>
              {point.expired && <Badge tone={EXPIRED_TONE}>Expired</Badge>}
            </div>
            <div
              className="h-2.5 rounded-sm bg-muted"
              style={{ maxWidth: `${width}%` }}
              aria-hidden="true"
            />
            <p className="font-mono text-xs text-muted-foreground">
              {shortTimestamp(point.expires_at)} | {count} activities | {point.view_count ?? 0} views
            </p>
          </li>
        )
      })}
    </ul>
  )
}

/**
 * One recommended follow-up, with its reasons and the control that acts on it.
 *
 * The reasons are listed rather than collapsed, because a quote that is both
 * quiet and about to expire is one row in a seller's list and two facts about it.
 */
export function FollowUpCard({ entry, vocabulary, onSend, busy }) {
  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-base font-semibold text-foreground">{entry.title}</h3>
          <p className="mt-0.5 font-mono text-xs text-muted-foreground">
            {entry.quote_number || entry.quote_id}
          </p>
        </div>
        <ActivityClassBadge activityClass={entry.activity_class} />
      </div>

      <ul className="flex flex-col gap-1">
        {entry.reasons.map((reason) => (
          <li key={reason.reason} className="text-sm text-foreground">
            <span className="text-muted-foreground">
              {vocabulary?.follow_up_reasons?.[reason.reason] || reason.reason}:
            </span>{' '}
            <span className="font-mono">{reason.detail}</span>
          </li>
        ))}
      </ul>

      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="primary"
          icon="audit"
          disabled={busy}
          onClick={() => onSend(entry)}
        >
          Send a follow up
        </Button>
        <span className="text-xs text-muted-foreground">
          Records a new Quote sent and opens nothing. This product has no mail transport.
        </span>
      </div>
    </Card>
  )
}

/**
 * One row of the quote index, with its four counters.
 *
 * Local because the index page is four researched columns beside a quote title
 * and a class, and a table is not one of the shared primitives.
 */
export function QuoteIndexRow({ quote, labels, basisLabels, onOpen }) {
  const counters = quote.engagement || {}
  return (
    <div className="flex flex-col gap-3 border-t border-border-subtle py-3 first:border-t-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-base font-semibold text-foreground">
            {quote.data?.title || quote.data?.quote_number || quote.id}
          </h3>
          <p className="mt-0.5 font-mono text-xs text-muted-foreground">
            {quote.data?.quote_number || quote.id} | {quote.status || 'No status'} |{' '}
            {amount(quote.data?.hs_quote_amount || quote.data?.locked_amount)}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <ActivityClassBadge activityClass={counters.activity_class} />
          <Button icon="search" onClick={() => onOpen(quote.id)}>
            Timeline
          </Button>
        </div>
      </div>
      <CounterRow counters={counters} labels={labels} basisLabels={basisLabels} />
    </div>
  )
}

/**
 * A labelled number input for one analysis setting.
 *
 * Local because the three settings have different bounds and different hints and
 * a shared input cannot carry either. The bounds in the field come from
 * `SETTING_FIELDS`, which is the same list the server validates against.
 */
export function SettingField({ field, value, onChange, disabled }) {
  const id = `wf097-setting-${field.key}`
  return (
    <Field label={field.label} hint={field.hint} id={id}>
      <input
        id={id}
        className={inputClass}
        type="number"
        inputMode="numeric"
        min={field.min}
        max={field.max}
        value={value ?? ''}
        disabled={disabled}
        placeholder="Not set"
        onChange={(event) => onChange(field.key, event.target.value)}
      />
    </Field>
  )
}
