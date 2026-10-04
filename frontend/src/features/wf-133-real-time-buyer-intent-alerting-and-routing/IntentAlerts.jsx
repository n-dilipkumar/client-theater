/**
 * Intent alerts: who is showing intent, who heard, and what the rep did (WF-133).
 *
 * The page is built around the researched data flow rather than around the tables.
 * A buyer reads the pricing section for ninety seconds, the thresholds fire, the
 * account is resolved, an alert is dispatched, a task lands on the opportunity, and
 * a rep closes the loop. So the screen leads with the alerts, shows the three
 * researched facts on each one (which pages, how long, which stakeholder), states
 * plainly what happened on each channel, and offers the four things a rep can do
 * about it.
 *
 * Two things this page refuses to do, and both refusals are visible rather than
 * implied:
 *
 * **It does not imply mail was sent.** Every alert carries a dispatch state per
 * channel and this page renders every one of them with its meaning in words. Email
 * reads "queued", Slack reads "held for this integration", and an owner with no
 * address reads "skipped" with the owner's name on it. The foot note says the same
 * thing in a sentence.
 *
 * **It does not show only accounts that alerted.** The researched "who is engaged
 * and who is not" surface needs the half that has never engaged to be visible, so
 * the Engagement tab is built over the watchlist rather than over the alerts. An
 * account on the list that has never fired is the row a seller most needs and the
 * one an alert-driven view can never produce.
 *
 * The room picker is here because the host hands a page no props, so the page
 * fetches its own room list. Room scoping is a query parameter rather than a path
 * segment for the same reason.
 */

import { useState } from 'react'
import {
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Spinner,
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'
import { relativeTime } from '@/lib/api'
import {
  accountLabel,
  dwellSentence,
  intentApi,
  refusalMessage,
  thresholdSentence,
} from './api'
import {
  ActionabilityNote,
  Dash,
  Fact,
  Glyph,
  Notice,
  StatusPill,
  statusMeaning,
} from './primitives'

const TABS = [
  { id: 'alerts', label: 'Alerts' },
  { id: 'engagement', label: 'Engagement' },
  { id: 'watchlists', label: 'Watchlists' },
  { id: 'rules', label: 'Routing' },
  { id: 'decisions', label: 'Decisions' },
]

/** The four things a rep may record. Ordered as the log reads. */
const ACTIONS = [
  { kind: 'acknowledged', label: 'Acknowledge' },
  { kind: 'contacted', label: 'Mark contacted' },
  { kind: 'noted', label: 'Add a note' },
  { kind: 'dismissed', label: 'Dismiss' },
]

export default function IntentAlerts() {
  const [tab, setTab] = useState('alerts')
  const [roomId, setRoomId] = useState('')

  const rooms = useAsync(() => intentApi.rooms(), [])
  const roomRows = rooms.data?.records || []
  const activeRoom = roomId || (roomRows.length === 1 ? roomRows[0]?.id : '')

  const vocabulary = useAsync(() => intentApi.vocabulary(), [])
  const summary = useAsync(
    () => (activeRoom ? intentApi.summary(activeRoom) : Promise.resolve(null)),
    [activeRoom]
  )
  const alerts = useAsync(
    () => (activeRoom ? intentApi.alerts({ room_id: activeRoom }) : Promise.resolve(null)),
    [activeRoom]
  )
  const engagement = useAsync(
    () => (activeRoom ? intentApi.engagement(activeRoom) : Promise.resolve(null)),
    [activeRoom]
  )
  const watchlists = useAsync(
    () => (activeRoom ? intentApi.watchlists(activeRoom) : Promise.resolve(null)),
    [activeRoom]
  )
  const rules = useAsync(
    () => (activeRoom ? intentApi.rules(activeRoom) : Promise.resolve(null)),
    [activeRoom]
  )
  const inferences = useAsync(() => intentApi.inferences(), [])

  const loading = [rooms, vocabulary].some((call) => call.loading)
  const failure = [rooms, vocabulary].find((call) => call.error)

  if (loading) return <Spinner label="Loading intent alerting" />
  if (failure) return <ErrorNote error={failure.error} onRetry={failure.refetch} />

  const refreshAll = () => {
    summary.refetch()
    alerts.refetch()
    engagement.refetch()
  }

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <Glyph name="bell" size={20} />
          <h1 className="font-display text-2xl font-semibold text-foreground">Intent alerts</h1>
        </div>
        <p className="text-sm text-muted-foreground">
          A threshold fires on a target account, the owner is routed the three researched facts,
          and a follow-up task lands on the opportunity. Nothing is sent.
        </p>
      </header>

      <ActionabilityNote />

      <RoomPicker
        rooms={roomRows}
        roomId={activeRoom}
        onChange={setRoomId}
        loading={rooms.loading}
        error={rooms.error}
        onRetry={rooms.refetch}
      />

      {activeRoom && summary.error ? (
        <ErrorNote error={summary.error} onRetry={summary.refetch} />
      ) : null}

      {activeRoom && summary.data ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Watched accounts"
            value={summary.data.watched_accounts}
            hint={`across ${summary.data.watchlists} watchlist(s)`}
            icon="search"
          />
          <StatCard
            label="Alerts"
            value={summary.data.alerts}
            hint={
              Object.entries(summary.data.alert_states || {})
                .map(([state, count]) => `${count} ${state}`)
                .join(', ') || 'none yet'
            }
            icon="schema"
          />
          <StatCard
            label="Follow-up tasks"
            value={summary.data.tasks}
            hint="on opportunities in this room"
            icon="database"
          />
          <StatCard
            label="Rep actions"
            value={summary.data.actions}
            hint="logged, newest last"
            icon="audit"
          />
        </div>
      ) : null}

      <nav aria-label="Sections" className="flex flex-wrap gap-2">
        {TABS.map((entry) => (
          <button
            key={entry.id}
            type="button"
            aria-current={tab === entry.id ? 'page' : undefined}
            onClick={() => setTab(entry.id)}
            className={`min-h-11 rounded-sm border px-4 text-sm font-medium ${
              tab === entry.id
                ? 'border-accent bg-accent text-on-accent'
                : 'border-border-subtle bg-surface text-foreground hover:border-accent hover:text-accent'
            }`}
          >
            {entry.label}
          </button>
        ))}
      </nav>

      {tab === 'alerts' ? (
        <AlertsPanel alerts={alerts} roomId={activeRoom} onChanged={refreshAll} />
      ) : null}
      {tab === 'engagement' ? (
        <EngagementPanel engagement={engagement} roomId={activeRoom} />
      ) : null}

      {tab === 'watchlists' ? (
        <WatchlistsPanel watchlists={watchlists} roomId={activeRoom} onChanged={refreshAll} />
      ) : null}

      {tab === 'rules' ? <RulesPanel rules={rules} roomId={activeRoom} /> : null}

      {tab === 'decisions' ? <DecisionsPanel inferences={inferences} /> : null}
    </div>
  )
}

function RoomPicker({ rooms, roomId, onChange, loading, error, onRetry }) {
  if (loading) return <Spinner label="Loading rooms" />
  if (error) return <ErrorNote error={error} onRetry={onRetry} />
  if (!rooms.length) {
    return (
      <EmptyState
        title="No rooms yet"
        description="A room holds a seller's audience. Create one and the alerts below become per-room."
      />
    )
  }
  return (
    <div className="max-w-md">
      <Field id="wf133-room" label="Room" hint="Every alert below is scoped to this room.">
        <select
          id="wf133-room"
          className={inputClass}
          value={roomId}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="">Choose a room</option>
          {rooms.map((room) => (
            <option key={room.id} value={room.id}>
              {room.data?.name || room.id}
            </option>
          ))}
        </select>
      </Field>
    </div>
  )
}

function AlertsPanel({ alerts, roomId, onChanged }) {
  if (!roomId) return <ChooseRoom />
  if (alerts.loading) return <Spinner label="Loading alerts" />
  if (alerts.error) return <ErrorNote error={alerts.error} onRetry={alerts.refetch} />

  const rows = alerts.data?.alerts || []
  if (!rows.length) {
    return (
      <EmptyState
        title="No alerts in this room yet"
        description="Add the account to a watchlist, then send an observation that meets a threshold."
      />
    )
  }
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        {alerts.data.count} alert(s). Channel states:{' '}
        {Object.entries(alerts.data.by_channel || {})
          .map(([key, count]) => `${count} ${key.replace(':', ' on ')}`)
          .join(', ')}
      </p>
      {rows.map((alert) => (
        <AlertCard key={alert.id} alert={alert} onChanged={onChanged} />
      ))}
    </div>
  )
}

function ChooseRoom() {
  return (
    <EmptyState
      title="Choose a room"
      description="Alerts belong to a buyer's interest in one room, so this page needs a room before it can show anything."
    />
  )
}

function AlertCard({ alert, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [problem, setProblem] = useState(null)
  const [done, setDone] = useState('')

  const payload = alert.payload || {}
  const pages = payload.pages || {}
  const dwell = payload.dwell || {}
  const stakeholder = payload.stakeholder || {}

  const record = async (kind) => {
    setBusy(true)
    setProblem(null)
    setDone('')
    try {
      await intentApi.recordAction(
        alert.signal_id,
        { kind, note: kind === 'dismissed' ? note : note || '' },
        'dana.kelly'
      )
      setNote('')
      setDone(`Recorded: ${kind}.`)
      onChanged()
    } catch (error) {
      setProblem(refusalMessage(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="font-display text-base font-semibold text-foreground">{alert.subject}</h2>
            <p className="font-mono text-xs text-muted-foreground">
              {alert.company_key} &middot; opportunity {alert.opportunity_id || 'none'} &middot;{' '}
              {relativeTime(alert.dispatched_at || alert.created_at)}
            </p>
          </div>
          <StatusPill state={alert.state} />
        </div>

        <dl className="grid gap-4 sm:grid-cols-3">
          <Fact label="Which pages">
            {pages.visited?.length ? (
              <ul className="flex flex-col gap-1">
                {pages.visited.map((path) => (
                  <li key={path} className="font-mono text-[13px] text-foreground">
                    {path}
                  </li>
                ))}
              </ul>
            ) : (
              <Dash />
            )}
          </Fact>
          <Fact label="How long">
            {/* Composed into one string rather than interpolated inline. A rep reads
                this as a sentence, and a sentence split across five text nodes is a
                sentence no test can assert on and no screen reader reads cleanly. */}
            <span className="font-mono text-[13px]">{dwellSentence(dwell)}</span>
            <span className="mt-1 block text-xs text-muted-foreground">
              {thresholdSentence(dwell)}
            </span>
          </Fact>
          <Fact label="Which stakeholder">
            {stakeholder.identified === 'true' ? (
              <>
                <span className="text-sm">{stakeholder.name}</span>
                {stakeholder.role ? (
                  <span className="block text-xs text-muted-foreground">{stakeholder.role}</span>
                ) : null}
              </>
            ) : (
              <Dash />
            )}
            <span className="mt-1 block text-xs text-muted-foreground">
              no address on file, by design
            </span>
          </Fact>
        </dl>

        <div>
          <p className="text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
            Why this fired
          </p>
          <ul className="mt-2 flex flex-wrap gap-2">
            {(payload.triggered_by || []).map((entry) => (
              <li key={entry.kind}>
                <span className="inline-flex items-center gap-2 rounded-xs border border-border-subtle bg-muted px-2 py-1 text-xs">
                  <Glyph name="threshold" size={14} />
                  <span className="text-foreground">{entry.label}</span>
                  <span className="font-mono text-muted-foreground">
                    {entry.observed} {entry.unit}, threshold {entry.threshold}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>

        <div>
          <p className="text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
            Who was told
          </p>
          <ul className="mt-2 flex flex-col gap-2">
            {(alert.dispatches || []).map((dispatch) => (
              <li
                key={dispatch.channel}
                className="flex flex-wrap items-center justify-between gap-2 rounded-sm border border-border-subtle p-2"
              >
                <span className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-[13px] text-foreground">{dispatch.channel}</span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {dispatch.to || 'no address'}
                  </span>
                  <StatusPill state={dispatch.state} />
                </span>
                <span className="text-xs text-muted-foreground">{statusMeaning(dispatch.state)}</span>
              </li>
            ))}
          </ul>
          {alert.recipients?.length ? (
            <p className="mt-2 text-xs text-muted-foreground">
              Accountable: {alert.accountable}. Recipients considered:{' '}
              {alert.recipients.map((entry) => entry.who).join(', ')}
              {alert.consulted?.length ? ` Rules consulted: ${alert.consulted.join(', ')}` : ''}
            </p>
          ) : null}
        </div>

        {alert.body ? (
          <details className="rounded-sm border border-border-subtle p-3">
            <summary className="min-h-11 cursor-pointer text-sm font-medium text-foreground">
              Read the queued message
            </summary>
            <pre className="mt-2 overflow-x-auto font-mono text-xs whitespace-pre-wrap text-foreground">
              {alert.body}
            </pre>
          </details>
        ) : null}

        <div className="flex flex-col gap-2 border-t border-border-subtle pt-3">
          <Field
            id={`wf133-note-${alert.id}`}
            label="Note"
            hint="Required to dismiss, optional otherwise. The log exists to answer which alerts were not worth a call."
          >
            <input
              id={`wf133-note-${alert.id}`}
              className={inputClass}
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          </Field>
          <div className="flex flex-wrap gap-2">
            {ACTIONS.map((entry) => (
              <Button
                key={entry.kind}
                disabled={busy}
                onClick={() => record(entry.kind)}
                icon={entry.kind === 'dismissed' ? 'close' : 'plus'}
              >
                {entry.label}
              </Button>
            ))}
          </div>
          {done ? <Notice tone="ok">{done}</Notice> : null}
          {problem ? (
            <Notice tone="error" title="Not recorded." onDismiss={() => setProblem(null)}>
              {problem}
            </Notice>
          ) : null}
        </div>
      </div>
    </Card>
  )
}

function EngagementPanel({ engagement, roomId }) {
  if (!roomId) return <ChooseRoom />
  if (engagement.loading) return <Spinner label="Loading engagement" />
  if (engagement.error) return <ErrorNote error={engagement.error} onRetry={engagement.refetch} />

  const rows = engagement.data?.accounts || []
  if (!rows.length) {
    return (
      <EmptyState
        title="No target accounts in this room"
        description="The list here is the watchlist, so an account nobody has engaged still appears once it is watched."
      />
    )
  }
  return (
    <Card>
      <p className="text-sm text-muted-foreground">{engagement.data.reads}</p>
      <p className="mt-2 font-mono text-xs text-muted-foreground">
        {engagement.data.engaged} engaged, {engagement.data.not_engaged} not engaged
      </p>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[720px] border-collapse text-left text-sm">
          <caption className="sr-only">
            Every watched account, whether it has an alert, and when it was last seen
          </caption>
          <thead>
            <tr className="border-b border-border-subtle text-[11px] tracking-[0.14em] text-muted-foreground uppercase">
              <th scope="col" className="py-2 pr-3">
                Account
              </th>
              <th scope="col" className="py-2 pr-3">
                Engagement
              </th>
              <th scope="col" className="py-2 pr-3">
                Pages
              </th>
              <th scope="col" className="py-2 pr-3">
                Last seen
              </th>
              <th scope="col" className="py-2">
                Owner
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.company_key} className="border-b border-border-subtle/40">
                <td className="py-2 pr-3">
                  <span className="flex items-center gap-2">
                    <Glyph name="pages" size={16} />
                    <span className="font-medium text-foreground">{accountLabel(row)}</span>
                  </span>
                  <span className="font-mono text-xs text-muted-foreground">{row.company_key}</span>
                </td>
                <td className="py-2 pr-3">
                  <StatusPill state={row.engaged ? 'contacted' : 'skipped'} />
                  <span className="ml-2 text-xs text-muted-foreground">
                    {row.engaged ? 'has a live alert' : 'no alert'}
                  </span>
                </td>
                <td className="py-2 pr-3 font-mono text-[13px] text-foreground">
                  {row.pages?.length || <Dash />}
                </td>
                <td className="py-2 pr-3 text-muted-foreground">
                  {row.last_seen_at ? relativeTime(row.last_seen_at) : <Dash />}
                </td>
                <td className="py-2 font-mono text-[13px] text-foreground">
                  {row.accountable || <Dash />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

function WatchlistsPanel({ watchlists, roomId, onChanged }) {
  const [notice, setNotice] = useState(null)
  const [form, setForm] = useState({ name: '', tier: 'strategic', accounts: '', notify: '' })
  const [busy, setBusy] = useState(false)

  if (!roomId) return <ChooseRoom />
  if (watchlists.loading) return <Spinner label="Loading watchlists" />
  if (watchlists.error) return <ErrorNote error={watchlists.error} onRetry={watchlists.refetch} />

  const rows = watchlists.data?.watchlists || []

  const save = async () => {
    setBusy(true)
    setNotice(null)
    try {
      await intentApi.createWatchlist(
        {
          name: form.name.trim(),
          tier: form.tier,
          accounts: form.accounts
            .split(/[\s,]+/)
            .map((entry) => entry.trim())
            .filter(Boolean),
          notify: form.notify
            .split(/[\s,]+/)
            .map((entry) => entry.trim())
            .filter(Boolean),
        },
        roomId
      )
      setForm({ name: '', tier: 'strategic', accounts: '', notify: '' })
      setNotice({ tone: 'ok', text: 'Watchlist saved. Its accounts now alert.' })
      watchlists.refetch()
      onChanged()
    } catch (error) {
      setNotice({ tone: 'error', text: refusalMessage(error) })
    } finally {
      setBusy(false)
    }
  }

  const remove = async (id) => {
    setBusy(true)
    setNotice(null)
    try {
      await intentApi.deleteWatchlist(id)
      setNotice({ tone: 'ok', text: 'Watchlist removed. It was a soft delete, so the audit trail keeps it.' })
      watchlists.refetch()
      onChanged()
    } catch (error) {
      setNotice({ tone: 'error', text: refusalMessage(error) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
      <Card>
        <h2 className="font-display text-base font-semibold text-foreground">
          Target-account watchlists
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Only an account on a watchlist raises an alert. The research names the surface and states
          no rule, so membership is the rule.
        </p>
        {rows.length ? (
          <ul className="mt-4 flex flex-col gap-2">
            {rows.map((entry) => (
              <li
                key={entry.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-sm border border-border-subtle p-3"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium text-foreground">{entry.name}</p>
                  <p className="font-mono text-xs text-muted-foreground">
                    {entry.tier} &middot; {entry.account_count} account(s) &middot;{' '}
                    {entry.notify.join(', ') || 'no address on file'}
                  </p>
                  {entry.note ? (
                    <p className="mt-1 text-xs text-muted-foreground">{entry.note}</p>
                  ) : null}
                </div>
                <Button disabled={busy} icon="trash" onClick={() => remove(entry.id)}>
                  Remove
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <div className="mt-4">
            <EmptyState
              title="No watchlist yet"
              description="Add one on the right. Until an account is watched, nothing alerts."
            />
          </div>
        )}
        {notice ? (
          <div className="mt-4">
            <Notice
              tone={notice.tone}
              onDismiss={() => setNotice(null)}
              title={notice.tone === 'error' ? 'Not saved.' : ''}
            >
              {notice.text}
            </Notice>
          </div>
        ) : null}
      </Card>

      <Card>
        <h3 className="font-display text-base font-semibold text-foreground">Watch a set</h3>
        <div className="mt-3 flex flex-col gap-3">
          <Field id="wf133-watch-name" label="Name" hint="Unique within the room.">
            <input
              id="wf133-watch-name"
              className={inputClass}
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
            />
          </Field>
          <Field id="wf133-watch-tier" label="Tier">
            <select
              id="wf133-watch-tier"
              className={inputClass}
              value={form.tier}
              onChange={(event) => setForm({ ...form, tier: event.target.value })}
            >
              <option value="strategic">Strategic</option>
              <option value="named">Named</option>
              <option value="market">Market</option>
            </select>
          </Field>
          <Field
            id="wf133-watch-accounts"
            label="Company keys"
            hint="Space or comma separated, as the visitor-identification workflow wrote them."
          >
            <input
              id="wf133-watch-accounts"
              className={inputClass}
              value={form.accounts}
              onChange={(event) => setForm({ ...form, accounts: event.target.value })}
            />
          </Field>
          <Field id="wf133-watch-notify" label="Notify" hint="Email addresses. One alert per recipient per day.">
            <input
              id="wf133-watch-notify"
              className={inputClass}
              value={form.notify}
              onChange={(event) => setForm({ ...form, notify: event.target.value })}
            />
          </Field>
          <Button variant="primary" disabled={busy} icon="plus" onClick={save}>
            Save watchlist
          </Button>
        </div>
      </Card>
    </div>
  )
}

function RulesPanel({ rules, roomId }) {
  const [notice, setNotice] = useState(null)
  const [form, setForm] = useState({ name: '', kind: 'team', matchKey: 'team', matchValue: '', notify: '', position: 1 })
  const [busy, setBusy] = useState(false)

  if (!roomId) return <ChooseRoom />
  if (rules.loading) return <Spinner label="Loading routing rules" />
  if (rules.error) return <ErrorNote error={rules.error} onRetry={rules.refetch} />

  const rows = rules.data?.rules || []

  const save = async () => {
    setBusy(true)
    setNotice(null)
    try {
      await intentApi.createRule(
        {
          name: form.name.trim(),
          kind: form.kind,
          match: form.matchValue.trim() ? { [form.matchKey]: form.matchValue.trim() } : {},
          notify: form.kind === 'team' || form.kind === 'watchlist'
            ? form.notify
                .split(/[\s,]+/)
                .map((entry) => entry.trim())
                .filter(Boolean)
            : [],
          position: Number(form.position) || 0,
        },
        roomId
      )
      setForm({ ...form, name: '', matchValue: '', notify: '' })
      setNotice({ tone: 'ok', text: 'Rule saved. It is consulted in position order.' })
      rules.refetch()
    } catch (error) {
      setNotice({ tone: 'error', text: refusalMessage(error) })
    } finally {
      setBusy(false)
    }
  }

  const remove = async (id) => {
    setBusy(true)
    setNotice(null)
    try {
      await intentApi.deleteRule(id)
      setNotice({ tone: 'ok', text: 'Rule removed from the chain.' })
      rules.refetch()
    } catch (error) {
      setNotice({ tone: 'error', text: refusalMessage(error) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
      <Card>
        <h2 className="font-display text-base font-semibold text-foreground">The routing chain</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          The opportunity owner is always consulted first: the research names that recipient, so it
          is a property of the deal rather than a rule somebody has to remember to add. Rules below
          add to that, in position order. A rule that says stop ends the walk for the rules after
          it.
        </p>
        <p className="mt-2 font-mono text-xs text-muted-foreground">
          Opportunity owner &rarr; then these, in order &rarr; then the watchlist default.
        </p>
        {rows.length ? (
          <ul className="mt-4 flex flex-col gap-2">
            {rows.map((entry) => (
              <li
                key={entry.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-sm border border-border-subtle p-3"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium text-foreground">
                    {entry.position}. {entry.name}
                  </p>
                  <p className="font-mono text-xs text-muted-foreground">
                    {entry.kind}
                    {Object.keys(entry.match || {}).length
                      ? ` on ${Object.entries(entry.match)
                          .map(([key, value]) => `${key}=${value}`)
                          .join(', ')}`
                      : ''}
                    {entry.notify?.length ? ` to ${entry.notify.join(', ')}` : ''}
                    {entry.stop ? '' : ', does not stop the chain'}
                  </p>
                  {entry.note ? (
                    <p className="mt-1 text-xs text-muted-foreground">{entry.note}</p>
                  ) : null}
                </div>
                <Button disabled={busy} icon="trash" onClick={() => remove(entry.id)}>
                  Remove
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <div className="mt-4">
            <EmptyState
              title="No configured rules"
              description="The owner alone is enough to route an alert. Add a territory rule when more than one person should hear."
            />
          </div>
        )}
        {notice ? (
          <div className="mt-4">
            <Notice
              tone={notice.tone}
              onDismiss={() => setNotice(null)}
              title={notice.tone === 'error' ? 'Not saved.' : ''}
            >
              {notice.text}
            </Notice>
          </div>
        ) : null}
      </Card>

      <Card>
        <h3 className="font-display text-base font-semibold text-foreground">Add a rule</h3>
        <div className="mt-3 flex flex-col gap-3">
          <Field id="wf133-rule-name" label="Name">
            <input
              id="wf133-rule-name"
              className={inputClass}
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
            />
          </Field>
          <Field id="wf133-rule-kind" label="Kind">
            <select
              id="wf133-rule-kind"
              className={inputClass}
              value={form.kind}
              onChange={(event) => setForm({ ...form, kind: event.target.value })}
            >
              <option value="team">Team</option>
              <option value="watchlist">Watchlist</option>
              <option value="fallback">Fallback</option>
              <option value="crm_owner">Opportunity owner</option>
            </select>
          </Field>
          <Field
            id="wf133-rule-match-key"
            label="Match on"
            hint="A key no rule reads is refused rather than stored, so a rule cannot silently never fire."
          >
            <select
              id="wf133-rule-match-key"
              className={inputClass}
              value={form.matchKey}
              onChange={(event) => setForm({ ...form, matchKey: event.target.value })}
            >
              <option value="team">Team</option>
              <option value="segment">Segment</option>
              <option value="country">Country</option>
              <option value="size">Size</option>
              <option value="company_key">Company key</option>
            </select>
          </Field>
          <Field id="wf133-rule-match-value" label="Value">
            <input
              id="wf133-rule-match-value"
              className={inputClass}
              value={form.matchValue}
              onChange={(event) => setForm({ ...form, matchValue: event.target.value })}
            />
          </Field>
          <Field id="wf133-rule-notify" label="Notify" hint="Email addresses. A Slack handle has nowhere to go.">
            <input
              id="wf133-rule-notify"
              className={inputClass}
              value={form.notify}
              onChange={(event) => setForm({ ...form, notify: event.target.value })}
            />
          </Field>
          <Field id="wf133-rule-position" label="Position" hint="Lower is consulted first.">
            <input
              id="wf133-rule-position"
              className={inputClass}
              value={form.position}
              onChange={(event) => setForm({ ...form, position: event.target.value })}
            />
          </Field>
          <Button variant="primary" disabled={busy} icon="plus" onClick={save}>
            Save rule
          </Button>
        </div>
      </Card>
    </div>
  )
}

function DecisionsPanel({ inferences }) {
  if (inferences.loading) return <Spinner label="Loading the decision record" />
  if (inferences.error) return <ErrorNote error={inferences.error} onRetry={inferences.refetch} />

  const rows = inferences.data?.inferences || []
  return (
    <Card>
      <h2 className="font-display text-base font-semibold text-foreground">
        Every judgement this workflow rests on
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">
        {inferences.data.count} recorded. The research quotes one number for one threshold and
        defines none of the rest, so these are the readings taken, what would change them, and the
        risk of having got them wrong.
      </p>
      <ul className="mt-4 flex flex-col gap-3">
        {rows.map((entry) => (
          <li key={entry.id} className="rounded-sm border border-border-subtle p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="font-mono text-[13px] font-medium text-foreground">{entry.id}</h3>
              <span className="rounded-xs border border-border-subtle bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground">
                {entry.sourced ? 'sourced' : 'inferred'}
              </span>
            </div>
            <p className="mt-1 text-sm font-medium text-foreground">{entry.question}</p>
            <p className="mt-1 text-sm text-muted-foreground">{entry.reading}</p>
            <p className="mt-2 text-xs text-muted-foreground">
              <span className="font-medium text-foreground">Change: </span>
              {entry.change}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              <span className="font-medium text-foreground">Risk: </span>
              {entry.risk}
            </p>
          </li>
        ))}
      </ul>
    </Card>
  )
}
