import { useState } from 'react'

import { Button, Card, EmptyState, ErrorNote, Field, JsonView, Spinner, StatCard, inputClass, useAsync } from '@/components/ui'

import {
  ROW_MOVES,
  activationForm,
  activationPayload,
  activationSummary,
  eligibilityText,
  guideApi,
} from './api'
import { Badge, NumberField, Select, StatusBadge, TierBadge } from './primitives'

const REPEAT_UNIT_OPTIONS = [
  { value: 'day', label: 'Day' },
  { value: 'hour', label: 'Hour' },
]

const THROTTLE_UNIT_OPTIONS = [
  { value: 'minute', label: 'Minute' },
  { value: 'hour', label: 'Hour' },
  { value: 'day', label: 'Day' },
]

const STOP_MODE_OPTIONS = [
  { value: 'never', label: 'Never' },
  { value: 'count', label: 'A set number of dismissals' },
]

const DEFAULT_AT = '2026-03-01T09:00:00+00:00'

function Section({ title, description, children }) {
  return (
    <Card>
      <h2 className="text-lg font-semibold text-foreground">{title}</h2>
      {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      <div className="mt-4">{children}</div>
    </Card>
  )
}

function GuideOrderTable({ ranking, onMove, onSelect, busy, appConfigured }) {
  if (!ranking.length) {
    return (
      <EmptyState
        title="No guides yet"
        description="Create a guide, then set the order list for the application. Guides that ignore throttling sort first."
      />
    )
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] border-collapse text-left text-sm">
        <caption className="sr-only">
          The two-tier order list. Unthrottled guides rank before throttled guides.
        </caption>
        <thead>
          <tr className="border-b border-border-subtle text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            <th className="px-2 py-2 font-medium">#</th>
            <th className="px-2 py-2 font-medium">Guide</th>
            <th className="px-2 py-2 font-medium">Tier</th>
            <th className="px-2 py-2 font-medium">Status</th>
            <th className="px-2 py-2 font-medium">Activation</th>
            <th className="px-2 py-2 font-medium">Eligibility</th>
            <th className="px-2 py-2 font-medium">Reorder</th>
          </tr>
        </thead>
        <tbody>
          {ranking.map((row) => (
            <tr key={row.key} className="border-b border-border-subtle align-top">
              <td className="px-2 py-3 font-mono text-xs text-muted-foreground">{row.position + 1}</td>
              <td className="px-2 py-3">
                <button
                  type="button"
                  onClick={() => onSelect(row.key)}
                  className="min-h-11 text-left font-medium text-foreground hover:text-accent"
                >
                  {row.name}
                </button>
                <p className="font-mono text-xs text-muted-foreground">{row.key}</p>
              </td>
              <td className="px-2 py-3">
                <TierBadge tier={row.tier} />
              </td>
              <td className="px-2 py-3">
                <StatusBadge status={row.status} />
              </td>
              <td className="max-w-[22rem] px-2 py-3 text-muted-foreground">
                <span>{activationSummary(row)}</span>
              </td>
              <td className="px-2 py-3 text-muted-foreground">{eligibilityText(row)}</td>
              <td className="px-2 py-3">
                <div className="flex flex-wrap gap-1">
                  {ROW_MOVES.map((option) => (
                    <Button
                      key={option.move}
                      variant="ghost"
                      disabled={busy || !appConfigured}
                      aria-label={`${option.label}: ${option.description}`}
                      onClick={() => onMove(row.key, option.move)}
                    >
                      {option.label}
                    </Button>
                  ))}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ThrottleEditor({ appId, order, onSave, busy }) {
  const [enabled, setEnabled] = useState(Boolean(order.throttling.enabled))
  const [count, setCount] = useState(order.throttling.count)
  const [unit, setUnit] = useState(order.throttling.unit)

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        The throttle sets the minimum time between one automatic guide and the next. The countdown
        starts when the previous guide is dismissed or completed, not when it is displayed.
      </p>
      <div className="grid gap-4 sm:grid-cols-3">
        <NumberField
          id="throttle-count"
          label="Guides"
          hint="1 to 10,000"
          value={count}
          min={1}
          max={10000}
          disabled={!enabled || busy}
          onChange={setCount}
        />
        <Select
          id="throttle-unit"
          label="Every"
          hint="Minute, hour or day"
          value={unit}
          disabled={!enabled || busy}
          onChange={setUnit}
          options={THROTTLE_UNIT_OPTIONS}
        />
        <div className="flex items-end">
          <Button
            variant={enabled ? 'secondary' : 'primary'}
            disabled={busy}
            onClick={() => setEnabled((value) => !value)}
          >
            {enabled ? 'Throttling on' : 'Throttling off'}
          </Button>
        </div>
      </div>
      <Button
        variant="primary"
        disabled={busy}
        onClick={() => onSave(appId, { guide_keys: order.guide_keys, throttling: { enabled, count: Number(count), unit } })}
      >
        Save ordering
      </Button>
    </div>
  )
}

export default function GuidePromptsPage() {
  const [appId, setAppId] = useState('demo-app')
  const [userId, setUserId] = useState('dana')
  const [at, setAt] = useState(DEFAULT_AT)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState(null)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(null)
  const [newGuide, setNewGuide] = useState({ key: '', name: '' })

  const vocabulary = useAsync(() => guideApi.vocabulary(), [])
  const summary = useAsync(() => guideApi.summary(), [])
  const guides = useAsync(() => guideApi.guides(), [])
  const order = useAsync(() => guideApi.order(appId), [appId])
  const ranking = useAsync(() => guideApi.ranking(appId, userId, at), [appId, userId, at])
  const session = useAsync(() => guideApi.session(appId, userId), [appId, userId])

  const selectedGuide = selected ? guides.data?.guides.find((g) => g.key === selected) || null : null

  const run = async (action, message) => {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const result = await action()
      setNotice(message)
      await Promise.all([guides.refetch(), order.refetch(), ranking.refetch(), summary.refetch(), session.refetch()])
      return result
    } catch (caught) {
      setError(caught)
      return null
    } finally {
      setBusy(false)
    }
  }

  if (vocabulary.loading || summary.loading || guides.loading) {
    return <Spinner label="Loading guide ordering" />
  }
  if (vocabulary.error || summary.error || guides.error) {
    return (
      <ErrorNote
        error={vocabulary.error || summary.error || guides.error}
        onRetry={() => {
          vocabulary.refetch()
          summary.refetch()
          guides.refetch()
        }}
      />
    )
  }

  const counts = summary.data
  const orderValue = order.data

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-foreground">Guide ordering and throttling</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Rank guides in an order list, throttle how often they appear, and relaunch one after a
          dismissal. Two clocks are modelled: the app-level throttle and the per-guide repeat.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Guides" value={counts.total} icon="schema" />
        <StatCard label="Ignore throttling" value={counts.unthrottled} hint="Sort first" icon="schema" />
        <StatCard label="Applications" value={counts.applications} icon="rooms" />
        <StatCard label="Events recorded" value={counts.events} icon="audit" />
      </div>

      {error && <ErrorNote error={error} />}
      {notice && (
        <div className="rounded-sm border border-border-subtle bg-muted px-4 py-3 text-sm text-foreground">
          {notice}
        </div>
      )}

      <Section
        title="Application"
        description="Throttling is scoped to an application, and the order list is shared by every platform for that application."
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Application" hint="The order list and throttle are keyed by this value." id="app-id">
            <input id="app-id" className={inputClass} value={appId} onChange={(event) => setAppId(event.target.value)} />
          </Field>
        </div>
      </Section>

      <Section
        title="Guide throttling"
        description="Guides > Guides > Ordering tab. Throttling determines the minimum time between one automatic guide and the next."
      >
        {order.loading ? (
          <Spinner label="Loading the order list" />
        ) : order.error ? (
          <ErrorNote error={order.error} onRetry={order.refetch} />
        ) : (
          <ThrottleEditor
            key={appId}
            appId={appId}
            order={orderValue}
            busy={busy}
            onSave={(id, body) =>
              run(() => guideApi.setOrder(id, body), 'Saved the order list and throttle.')
            }
          />
        )}
      </Section>

      <Section
        title="Order list"
        description="Unthrottled guides appear first; each tier keeps its own row order. Reorder with the row arrows."
      >
        {ranking.loading ? (
          <Spinner label="Evaluating eligibility" />
        ) : ranking.error ? (
          <ErrorNote error={ranking.error} onRetry={ranking.refetch} />
        ) : (
          <GuideOrderTable
            ranking={ranking.data.ranking}
            busy={busy}
            appConfigured={Boolean(orderValue && orderValue.configured)}
            onSelect={setSelected}
            onMove={(key, move) => run(() => guideApi.move(appId, key, move), `Moved ${key} ${move}.`)}
          />
        )}
      </Section>

      <Section
        title="Session simulator"
        description="Freeze the moment to see which clock holds a guide back. A dismissal restarts both the repeat clock and the throttle clock."
      >
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Visitor" id="user-id">
            <input id="user-id" className={inputClass} value={userId} onChange={(event) => setUserId(event.target.value)} />
          </Field>
          <Field label="Evaluation moment (ISO 8601)" id="at">
            <input id="at" className={inputClass} value={at} onChange={(event) => setAt(event.target.value)} />
          </Field>
          <div className="flex items-end">
            <Button variant="secondary" disabled={busy} onClick={() => run(async () => null, 'Re-evaluated eligibility.')}>
              Re-evaluate
            </Button>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant="secondary" disabled={busy} onClick={() => run(() => guideApi.event(appId, { user_id: userId, event: 'shown', key: selected || '', at }), 'Recorded a show.')}>
            Show guide
          </Button>
          <Button variant="secondary" disabled={busy} onClick={() => run(() => guideApi.event(appId, { user_id: userId, event: 'dismissed', key: selected || '', at, cause: 'close' }), 'Recorded a dismissal.')}>
            Dismiss
          </Button>
          <Button variant="secondary" disabled={busy} onClick={() => run(() => guideApi.event(appId, { user_id: userId, event: 'advanced', key: selected || '', at }), 'Recorded a step advance.')}>
            Advance step
          </Button>
          <Button variant="secondary" disabled={busy} onClick={() => run(() => guideApi.event(appId, { user_id: userId, event: 'completed', key: selected || '', at }), 'Recorded a completion.')}>
            Complete
          </Button>
          <Button variant="secondary" disabled={busy} onClick={() => run(() => guideApi.event(appId, { user_id: userId, event: 'shown', key: selected || '', at, programmatic: true }), 'Recorded a programmatic launch.')}>
            Programmatic launch
          </Button>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          The simulator acts on the selected guide. Select one from the order list first.
        </p>
        {session.loading ? (
          <Spinner label="Loading the session" />
        ) : session.error ? (
          <ErrorNote error={session.error} onRetry={session.refetch} />
        ) : (
          <div className="mt-3 rounded-sm border border-border-subtle bg-surface p-3">
            <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">Session state</p>
            <JsonView value={session.data} />
          </div>
        )}
      </Section>

      <Section title="Activation" description="Guide Settings tab, Activation card, Edit display options.">
        {selectedGuide ? (
          <ActivationEditor
            key={selected}
            guide={selectedGuide}
            busy={busy}
            onSave={(payload) =>
              run(() => guideApi.update(selectedGuide.key, { activation: payload }), `Saved the activation for ${selectedGuide.key}.`)
            }
            onDelete={() => run(() => guideApi.remove(selectedGuide.key), `Deleted ${selectedGuide.key}.`)}
            onRestore={() => run(() => guideApi.restore(selectedGuide.key), `Restored ${selectedGuide.key}.`)}
          />
        ) : (
          <p className="text-sm text-muted-foreground">Select a guide in the order list to edit its activation.</p>
        )}
      </Section>

      <Section title="New guide" description="Author a guide in the staging state, then publish it in the order list.">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Key" hint="Immutable, lowercase, 3 to 64 characters." id="new-key">
            <input id="new-key" className={inputClass} value={newGuide.key} onChange={(event) => setNewGuide({ ...newGuide, key: event.target.value })} />
          </Field>
          <Field label="Name" id="new-name">
            <input id="new-name" className={inputClass} value={newGuide.name} onChange={(event) => setNewGuide({ ...newGuide, name: event.target.value })} />
          </Field>
          <div className="flex items-end">
            <Button
              variant="primary"
              disabled={busy || !newGuide.key || !newGuide.name}
              onClick={() =>
                run(() => guideApi.create({ key: newGuide.key, name: newGuide.name, status: 'public' }), `Created ${newGuide.key}.`).then(
                  (result) => {
                    if (result) setNewGuide({ key: '', name: '' })
                  },
                )
              }
            >
              Create guide
            </Button>
          </div>
        </div>
      </Section>

      <Section title="Decisions and sources" description="Where the rules and the open readings came from.">
        <div className="space-y-3">
          {vocabulary.data.decisions.map((item) => (
            <div key={item.id} className="rounded-sm border border-border-subtle p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={item.verdict === 'pass' ? 'insert' : 'neutral'}>{item.verdict}</Badge>
                <span className="font-medium text-foreground">{item.decision}</span>
              </div>
              <p className="mt-1 text-sm text-muted-foreground">Selected: {item.selected}</p>
              {/* The rejected alternative is part of the record, not decoration. */}
              <p className="mt-1 text-sm text-muted-foreground">Rejected: {item.alternative_rejected}</p>
              {item.audit_id && <p className="mt-1 font-mono text-xs text-muted-foreground">{item.audit_id}</p>}
            </div>
          ))}
          <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
            {vocabulary.data.sources.map((url) => (
              <li key={url} className="break-all font-mono text-[13px]">
                {url}
              </li>
            ))}
          </ul>
        </div>
      </Section>
    </div>
  )
}

function ActivationEditor({ guide, busy, onSave, onDelete, onRestore }) {
  const [form, setForm] = useState(() => activationForm(guide.activation))
  const set = (patch) => setForm((current) => ({ ...current, ...patch }))

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={guide.status} />
        <span className="font-mono text-xs text-muted-foreground">{guide.key}</span>
        <Badge tone="neutral">{guide.stop_after_label}</Badge>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex items-end">
          <Button variant={form.repeatEnabled ? 'secondary' : 'primary'} disabled={busy} onClick={() => set({ repeatEnabled: !form.repeatEnabled })}>
            {form.repeatEnabled ? 'Repeat guide display on' : 'Repeat guide display off'}
          </Button>
        </div>
        <Select
          id="repeat-unit"
          label="Show every"
          hint="Day or hour; 1 to 10,000"
          value={form.repeatUnit}
          disabled={!form.repeatEnabled || busy}
          onChange={(value) => set({ repeatUnit: value })}
          options={REPEAT_UNIT_OPTIONS}
        />
        <NumberField
          id="repeat-every"
          label="Repeat interval"
          hint="1 to 10,000"
          min={1}
          max={10000}
          value={form.repeatEvery}
          disabled={!form.repeatEnabled || busy}
          onChange={(value) => set({ repeatEvery: value })}
        />
        <Select
          id="stop-mode"
          label="Stop showing after"
          value={form.stopMode}
          disabled={busy}
          onChange={(value) => set({ stopMode: value })}
          options={STOP_MODE_OPTIONS}
        />
        <NumberField
          id="stop-count"
          label="Dismissals before stopping"
          hint="The minimum is 2; completion counts as a dismissal."
          min={2}
          value={form.stopCount}
          disabled={form.stopMode !== 'count' || busy}
          onChange={(value) => set({ stopCount: value })}
        />
        <NumberField
          id="delay-seconds"
          label="Delay guide display (seconds)"
          hint="Up to 60, so the prompt does not fire on landing."
          min={0}
          max={60}
          value={form.delaySeconds}
          disabled={busy}
          onChange={(value) => set({ delaySeconds: value })}
        />
        <div className="flex items-end">
          <Button variant={form.ignoreThrottling ? 'secondary' : 'ghost'} disabled={busy} onClick={() => set({ ignoreThrottling: !form.ignoreThrottling })}>
            {form.ignoreThrottling ? 'Ignores guide throttling' : 'Respects guide throttling'}
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button variant="primary" disabled={busy} onClick={() => onSave(activationPayload(form))}>
          Save activation
        </Button>
        {guide.deleted ? (
          <Button variant="secondary" disabled={busy} onClick={onRestore}>
            Restore guide
          </Button>
        ) : (
          <Button variant="danger" disabled={busy} onClick={onDelete}>
            Delete guide
          </Button>
        )}
      </div>
    </div>
  )
}
