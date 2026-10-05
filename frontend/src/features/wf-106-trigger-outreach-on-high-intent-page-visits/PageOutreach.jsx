/**
 * Page outreach: who browsed a high-intent page, and what the room did about it (WF-106).
 *
 * The page is built around the researched data flow rather than around the tables. A
 * prospect browses the upgrade page more than once, the room matches the page view
 * against the workflow's targeting rules, counts the visits inside the window, applies
 * the *Show workflow until* mode and the session damper, and shows an in-app block.
 * The buyer's answer is recorded as a content_stat receipt. So the screen leads with
 * the prospects, shows the arithmetic behind every decision, and offers the four things
 * a seller can change.
 *
 * Three things this page refuses to do, and all three refusals are visible rather than
 * implied:
 *
 * **It does not imply a message was sent.** A delivery record says the block would be
 * shown, in the one channel the research names. The foot note says the same thing in a
 * sentence, and the vocabulary route publishes four boolean fields a client can read
 * rather than infer.
 *
 * **It does not present a derived threshold as a sourced one.** The repeat count, the
 * repeat window and the dwell threshold are this build's readings. They are labelled
 * as derived at the top of the page, in the Decisions tab, and on each decision.
 *
 * **It does not show only the buyers who were shown the block.** The researched work is
 * finding the prospect who keeps coming back, so the Prospects tab is built over the
 * page views rather than over the deliveries. A buyer with three visits and no block is
 * the row a seller most needs and the one a delivery-driven view can never produce.
 *
 * The room picker is here because the host hands a page no props, so the page fetches
 * its own room list. Room scoping is a query parameter rather than a path segment for
 * the same reason.
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
  outreachApi,
  progressSentence,
  refusalMessage,
  ruleSentence,
  workflowLabel,
} from './api'
import {
  Dash,
  DeliveryHonestyNote,
  DeliveryPill,
  DerivedThresholdNote,
  Fact,
  GatePill,
  Glyph,
  Notice,
  deliveryMeaning,
} from './primitives'

const TABS = [
  { id: 'prospects', label: 'Prospects' },
  { id: 'workflows', label: 'Workflows' },
  { id: 'views', label: 'Page views' },
  { id: 'deliveries', label: 'Blocks shown' },
  { id: 'decisions', label: 'Decisions' },
]

export default function PageOutreach() {
  const [tab, setTab] = useState('prospects')
  const [roomId, setRoomId] = useState('')

  const rooms = useAsync(() => outreachApi.rooms(), [])
  const roomRows = rooms.data?.records || []
  const activeRoom = roomId || (roomRows.length === 1 ? roomRows[0]?.id : '')

  const vocabulary = useAsync(() => outreachApi.vocabulary(), [])
  const summary = useAsync(
    () => (activeRoom ? outreachApi.summary(activeRoom) : Promise.resolve(null)),
    [activeRoom]
  )
  const prospects = useAsync(
    () => (activeRoom ? outreachApi.prospects(activeRoom) : Promise.resolve(null)),
    [activeRoom]
  )
  // Hoisted rather than created inside the JSX below. A hook called in the props of a
  // conditional branch is a hook called conditionally, which React reports as an error
  // and which changes the hook order the moment the Decisions tab is selected.
  const inferences = useAsync(() => outreachApi.inferences(), [])

  const loading = [rooms, vocabulary].some((call) => call.loading)
  const failure = [rooms, vocabulary].find((call) => call.error)

  if (loading) return <Spinner label="Loading page outreach" />
  if (failure) return <ErrorNote error={failure.error} onRetry={failure.refetch} />

  const refreshAll = () => {
    summary.refetch()
    prospects.refetch()
  }

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <Glyph name="browse" size={20} />
          <h1 className="font-display text-2xl font-semibold text-foreground">Page outreach</h1>
        </div>
        <p className="text-sm text-muted-foreground">
          A page view is the trigger. The targeting rules, the repeat window and the Show workflow
          until mode decide whether the block goes anywhere, and every decision says which one
          stopped it.
        </p>
      </header>

      <DerivedThresholdNote thresholds={vocabulary.data?.thresholds || []} />
      <DeliveryHonestyNote />

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
            label="Workflows"
            value={summary.data.workflows}
            hint={`${summary.data.live_workflows} live, ${summary.data.draft_workflows} draft`}
            icon="schema"
          />
          <StatCard
            label="Matching page views"
            value={summary.data.matched_views}
            hint={`of ${summary.data.views} recorded`}
            icon="search"
          />
          <StatCard
            label="Blocks shown"
            value={summary.data.deliveries}
            hint="recorded, not sent"
            icon="dashboard"
          />
          <StatCard
            label="Receipts"
            value={summary.data.receipts}
            hint={
              Object.entries(summary.data.receipt_kinds || {})
                .map(([kind, count]) => `${count} ${kind.replace(/_/g, ' ')}`)
                .join(', ') || 'none yet'
            }
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

      {tab === 'prospects' ? (
        <ProspectsPanel prospects={prospects} roomId={activeRoom} />
      ) : null}
      {tab === 'workflows' ? (
        <WorkflowsPanel roomId={activeRoom} vocabulary={vocabulary} onChanged={refreshAll} />
      ) : null}
      {tab === 'views' ? <ViewsPanel roomId={activeRoom} /> : null}
      {tab === 'deliveries' ? <DeliveriesPanel roomId={activeRoom} /> : null}
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
        description="A room holds a seller's audience. Create one and the prospects below become per-room."
      />
    )
  }
  return (
    <div className="max-w-md">
      <Field id="wf106-room" label="Room" hint="Every workflow, view and receipt below is scoped to this room.">
        <select
          id="wf106-room"
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

function ChooseRoom() {
  return (
    <EmptyState
      title="Choose a room"
      description="A page view is interest in one seller's site through one room, so this page needs a room before it can show anything."
    />
  )
}

/**
 * Who browsed, and how far each one got.
 *
 * Built over the page views, so a buyer with three visits and no block appears. That is
 * the row a seller acts on and the one a delivery-driven table cannot produce.
 */
function ProspectsPanel({ prospects, roomId }) {
  if (!roomId) return <ChooseRoom />
  if (prospects.loading) return <Spinner label="Loading prospects" />
  if (prospects.error) return <ErrorNote error={prospects.error} onRetry={prospects.refetch} />

  const rows = prospects.data?.prospects || []
  if (!rows.length) {
    return (
      <EmptyState
        title="No page views recorded in this room yet"
        description="Post a page view to /api/wf-106/views and the prospect appears here whether or not the block was shown."
      />
    )
  }
  return (
    <Card>
      <p className="text-sm text-muted-foreground">{prospects.data.reads}</p>
      <p className="mt-2 font-mono text-xs text-muted-foreground">
        {prospects.data.shown_to} shown the block, {prospects.data.not_shown_to} not,{' '}
        {prospects.data.engaged} engaged, {prospects.data.hidden_for_session} hidden for the session
      </p>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[820px] border-collapse text-left text-sm">
          <caption className="sr-only">
            Every visitor with a recorded page view, how many of those visits matched, and what
            happened to them
          </caption>
          <thead>
            <tr className="border-b border-border-subtle text-[11px] tracking-[0.14em] text-muted-foreground uppercase">
              <th scope="col" className="py-2 pr-3">
                Visitor
              </th>
              <th scope="col" className="py-2 pr-3">
                Where they got to
              </th>
              <th scope="col" className="py-2 pr-3">
                Visits
              </th>
              <th scope="col" className="py-2 pr-3">
                Pages
              </th>
              <th scope="col" className="py-2">
                Last seen
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.visitor_key} className="border-b border-border-subtle/40">
                <td className="py-2 pr-3">
                  <span className="flex items-center gap-2">
                    <Glyph name="sessions" size={16} />
                    <span className="font-mono text-[13px] text-foreground">{row.visitor_key}</span>
                  </span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {row.company_key || 'no company key'}
                  </span>
                </td>
                <td className="py-2 pr-3">
                  <DeliveryPill state={stateFor(row)} />
                  <span className="mt-1 block text-xs text-muted-foreground">
                    {progressSentence(row)}
                  </span>
                </td>
                <td className="py-2 pr-3 font-mono text-[13px] text-foreground">
                  {row.matched_visits} of {row.visits}
                </td>
                <td className="py-2 pr-3 font-mono text-xs text-foreground">
                  {row.paths?.length ? row.paths.join(', ') : <Dash />}
                </td>
                <td className="py-2 text-muted-foreground">
                  {row.last_seen_at ? relativeTime(row.last_seen_at) : <Dash />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

function stateFor(row) {
  if (row.engaged) return 'engaged'
  if (row.hidden_for_session) return 'hidden_for_session'
  if (row.deliveries) return 'shown'
  return 'hidden_for_session'
}

/** The workflows, their rules and their frequency mode, and the form to add one. */
function WorkflowsPanel({ roomId, vocabulary, onChanged }) {
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({
    name: '',
    frequency: 'seen',
    state: 'draft',
    repeatVisits: '3',
    repeatWindowDays: '7',
    dwellSeconds: '60',
    url: '',
    dwellRule: '',
  })

  const workflows = useAsync(
    () => (roomId ? outreachApi.workflows(roomId) : Promise.resolve(null)),
    [roomId]
  )

  if (!roomId) return <ChooseRoom />
  if (workflows.loading) return <Spinner label="Loading workflows" />
  if (workflows.error) return <ErrorNote error={workflows.error} onRetry={workflows.refetch} />

  const rows = workflows.data?.workflows || []
  const modes = vocabulary.data?.frequency_modes || []

  const save = async () => {
    setBusy(true)
    setNotice(null)
    try {
      const rules = []
      if (form.url.trim()) {
        rules.push({ kind: 'url', mode: 'prefix', value: form.url.trim() })
      }
      if (form.dwellRule.trim()) {
        rules.push({ kind: 'dwell', value: form.dwellRule.trim() })
      }
      await outreachApi.createWorkflow(
        {
          name: form.name.trim(),
          frequency: form.frequency,
          state: form.state,
          repeat_visits: Number(form.repeatVisits) || 3,
          repeat_window_days: Number(form.repeatWindowDays) || 7,
          dwell_seconds: Number(form.dwellSeconds) || 60,
          rules,
          audience: { company_keys: [], tags: [], segments: [] },
          blocks: rules.length
            ? [{ kind: 'message', text: 'You have been back to this page a few times.', apps: [] }]
            : [],
          paths: [],
        },
        roomId
      )
      setForm({ ...form, name: '', url: '', dwellRule: '' })
      setNotice({
        tone: 'ok',
        text: 'Workflow saved. A draft never fires until you set it live.',
      })
      workflows.refetch()
      onChanged()
    } catch (error) {
      setNotice({ tone: 'error', text: refusalMessage(error) })
    } finally {
      setBusy(false)
    }
  }

  const publish = async (id, state) => {
    setBusy(true)
    setNotice(null)
    try {
      await outreachApi.setState(id, state)
      setNotice({
        tone: 'ok',
        text:
          state === 'live'
            ? 'Set live. The next matching page view shows the block.'
            : 'Back to draft. It will not fire whatever the page views say.',
      })
      workflows.refetch()
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
      await outreachApi.deleteWorkflow(id)
      setNotice({
        tone: 'ok',
        text: 'Workflow retired. It was a soft delete, so the views recorded while it was live keep their rules.',
      })
      workflows.refetch()
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
          The outreach workflows
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          The rules on one workflow are an AND: every rule has to hold. The repeat count, the window
          and the mode are the other three gates, and each decision on the other tabs says which one
          stopped it.
        </p>
        {rows.length ? (
          <ul className="mt-4 flex flex-col gap-2">
            {rows.map((row) => (
              <li
                key={row.id}
                className="flex flex-wrap items-start justify-between gap-3 rounded-sm border border-border-subtle p-3"
              >
                <div className="min-w-0 flex-1">
                  <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-foreground">
                    <Glyph name="repeat" size={16} />
                    {workflowLabel(row)}
                    <Badgeish state={row.state} />
                  </p>
                  <p className="font-mono text-xs text-muted-foreground">
                    {row.frequency} &middot; {row.repeat_visits} visit(s) in {row.repeat_window_days}{' '}
                    day(s) &middot; dwell {row.dwell_seconds}s &middot;{' '}
                    {ruleCountLabel(row.rule_count)}
                  </p>
                  <ul className="mt-1 flex flex-col gap-1">
                    {(row.rules || []).map((rule, index) => (
                      <li key={`${rule.kind}-${rule.value}-${index}`} className="text-xs text-muted-foreground">
                        {ruleSentence(rule)}
                      </li>
                    ))}
                    {row.note ? (
                      <li className="text-xs text-muted-foreground">{row.note}</li>
                    ) : null}
                  </ul>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    disabled={busy}
                    icon={row.state === 'live' ? 'restore' : 'plus'}
                    onClick={() => publish(row.id, row.state === 'live' ? 'draft' : 'live')}
                  >
                    {row.state === 'live' ? 'Set draft' : 'Set live'}
                  </Button>
                  <Button disabled={busy} icon="trash" onClick={() => remove(row.id)}>
                    Retire
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <div className="mt-4">
            <EmptyState
              title="No workflow in this room yet"
              description="Add one on the right. Until a workflow is live, no page view shows a block."
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
        <h3 className="font-display text-base font-semibold text-foreground">Add a workflow</h3>
        <div className="mt-3 flex flex-col gap-3">
          <Field id="wf106-wf-name" label="Name" hint="Unique within the room.">
            <input
              id="wf106-wf-name"
              className={inputClass}
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
            />
          </Field>
          <Field
            id="wf106-wf-frequency"
            label="Show workflow until"
            hint={modeHint(modes, form.frequency)}
          >
            <select
              id="wf106-wf-frequency"
              className={inputClass}
              value={form.frequency}
              onChange={(event) => setForm({ ...form, frequency: event.target.value })}
            >
              {modes.map((mode) => (
                <option key={mode.mode} value={mode.mode}>
                  {mode.label}
                </option>
              ))}
            </select>
          </Field>
          <Field id="wf106-wf-url" label="URL rule" hint="A path prefix, matched at a slash boundary. Leave blank to target every page.">
            <input
              id="wf106-wf-url"
              className={inputClass}
              value={form.url}
              placeholder="/upgrade"
              onChange={(event) => setForm({ ...form, url: event.target.value })}
            />
          </Field>
          <Field
            id="wf106-wf-dwell-rule"
            label="Dwell rule"
            hint="Seconds on the page. Leave blank to use the workflow default below."
          >
            <input
              id="wf106-wf-dwell-rule"
              className={inputClass}
              value={form.dwellRule}
              onChange={(event) => setForm({ ...form, dwellRule: event.target.value })}
            />
          </Field>
          <div className="grid gap-3 sm:grid-cols-3">
            <Field id="wf106-wf-visits" label="Visits" hint="Before it fires.">
              <input
                id="wf106-wf-visits"
                className={inputClass}
                value={form.repeatVisits}
                onChange={(event) => setForm({ ...form, repeatVisits: event.target.value })}
              />
            </Field>
            <Field id="wf106-wf-window" label="Window" hint="In days.">
              <input
                id="wf106-wf-window"
                className={inputClass}
                value={form.repeatWindowDays}
                onChange={(event) => setForm({ ...form, repeatWindowDays: event.target.value })}
              />
            </Field>
            <Field id="wf106-wf-dwell" label="Dwell" hint="In seconds.">
              <input
                id="wf106-wf-dwell"
                className={inputClass}
                value={form.dwellSeconds}
                onChange={(event) => setForm({ ...form, dwellSeconds: event.target.value })}
              />
            </Field>
          </div>
          <Field id="wf106-wf-state" label="State" hint="The researched flow ends with set it live.">
            <select
              id="wf106-wf-state"
              className={inputClass}
              value={form.state}
              onChange={(event) => setForm({ ...form, state: event.target.value })}
            >
              <option value="draft">Draft</option>
              <option value="live">Live</option>
            </select>
          </Field>
          <Button variant="primary" disabled={busy} icon="plus" onClick={save}>
            Save workflow
          </Button>
        </div>
      </Card>
    </div>
  )
}

function ruleCountLabel(count) {
  if (!count) return 'no targeting rule, so every page counts'
  return `${count} targeting rule${count === 1 ? '' : 's'}, all of which must hold`
}

function modeHint(modes, chosen) {
  const found = modes.find((entry) => entry.mode === chosen)
  return found ? found.reads : 'Pick one of the three the research names.'
}

/** A state word beside the workflow name, so live and draft never rely on colour. */
function Badgeish({ state }) {
  return (
    <span className="rounded-xs border border-border-subtle bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground">
      {state}
    </span>
  )
}

/** Every recorded page view, and which gate stopped each one. */
function ViewsPanel({ roomId }) {
  const views = useAsync(
    () => (roomId ? outreachApi.views({ room_id: roomId }) : Promise.resolve(null)),
    [roomId]
  )

  if (!roomId) return <ChooseRoom />
  if (views.loading) return <Spinner label="Loading page views" />
  if (views.error) return <ErrorNote error={views.error} onRetry={views.refetch} />

  const rows = views.data?.views || []
  if (!rows.length) {
    return (
      <EmptyState
        title="No page view recorded in this room yet"
        description="A view is written whether or not the block was shown, because the repeat count is made of the visits that did not qualify."
      />
    )
  }
  return (
    <Card>
      <p className="text-sm text-muted-foreground">
        {views.data.count} view(s), {views.data.matched} of them matching a targeting rule.
      </p>
      <ul className="mt-4 flex flex-col gap-2">
        {rows.map((row) => (
          <li key={row.id} className="rounded-sm border border-border-subtle p-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="flex flex-wrap items-center gap-2 font-mono text-[13px] text-foreground">
                  <Glyph name="browse" size={16} />
                  {row.path}
                  <span className="text-muted-foreground">{row.visitor_key}</span>
                </p>
                <p className="font-mono text-xs text-muted-foreground">
                  {row.workflow_name || row.workflow_id} &middot; {row.session_id} &middot;{' '}
                  {row.dwell_seconds}s dwell &middot; {relativeTime(row.visited_at)}
                </p>
              </div>
              <GatePill stoppedBy={row.stopped_by} />
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              {row.matched ? 'Counted toward the repeat total.' : 'Not counted: the rules did not hold.'}
              {row.counts?.matching_visits
                ? ` ${row.counts.matching_visits} of ${row.counts.visits_required} matching visits in the window at the time.`
                : ''}
            </p>
          </li>
        ))}
      </ul>
    </Card>
  )
}

/** The blocks the room recorded, and the receipts recorded against each one. */
function DeliveriesPanel({ roomId }) {
  const deliveries = useAsync(
    () => (roomId ? outreachApi.deliveries({ room_id: roomId }) : Promise.resolve(null)),
    [roomId]
  )

  if (!roomId) return <ChooseRoom />
  if (deliveries.loading) return <Spinner label="Loading blocks shown" />
  if (deliveries.error) return <ErrorNote error={deliveries.error} onRetry={deliveries.refetch} />

  const rows = deliveries.data?.deliveries || []
  if (!rows.length) {
    return (
      <EmptyState
        title="No block shown in this room yet"
        description="A delivery record appears here when a buyer crosses the repeat threshold and nothing in their session has hidden the block."
      />
    )
  }
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        {deliveries.data.count} block(s). States:{' '}
        {Object.entries(deliveries.data.by_state || {})
          .map(([state, count]) => `${count} ${state.replace(/_/g, ' ')}`)
          .join(', ')}
      </p>
      {rows.map((delivery) => (
        <DeliveryCard key={delivery.id} delivery={delivery} />
      ))}
    </div>
  )
}

function DeliveryCard({ delivery }) {
  const trigger = delivery.trigger || {}
  const blocks = delivery.blocks || []

  return (
    <Card>
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="font-display text-base font-semibold text-foreground">
              {delivery.workflow_name || delivery.workflow_id}
            </h2>
            <p className="font-mono text-xs text-muted-foreground">
              {delivery.visitor_key} &middot; session {delivery.session_id} &middot;{' '}
              {relativeTime(delivery.shown_at || delivery.created_at)}
            </p>
          </div>
          <DeliveryPill state={delivery.state} />
        </div>

        <p className="text-xs text-muted-foreground">{deliveryMeaning(delivery.state)}</p>

        <dl className="grid gap-4 sm:grid-cols-3">
          <Fact label="The page">
            <span className="font-mono text-[13px]">{trigger.path || <Dash />}</span>
            <span className="mt-1 block text-xs text-muted-foreground">
              {trigger.dwell_seconds ?? 0} seconds on the page
            </span>
          </Fact>
          <Fact label="Why it fired">
            <span className="font-mono text-[13px]">
              {trigger.matching_visits ?? 0} of {trigger.visits_required ?? 0} matching visits
            </span>
            <span className="mt-1 block text-xs text-muted-foreground">
              inside a {trigger.window_days ?? 0} day window
            </span>
          </Fact>
          <Fact label="The channel">
            <span className="font-mono text-[13px]">{delivery.channel}</span>
            <span className="mt-1 block text-xs text-muted-foreground">
              message_type {delivery.message_type}, recorded and not sent
            </span>
          </Fact>
        </dl>

        <div>
          <p className="text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
            Which rules held
          </p>
          <ul className="mt-2 flex flex-wrap gap-2">
            {(trigger.rule_matches || []).map((rule, index) => (
              <li key={`${rule.kind}-${index}`}>
                <span className="inline-flex items-center gap-2 rounded-xs border border-border-subtle bg-muted px-2 py-1 text-xs">
                  <span className="text-foreground">{ruleSentence(rule)}</span>
                  <span className="font-mono text-muted-foreground">
                    saw {rule.observed || 'nothing'}
                  </span>
                  <span className="font-mono text-muted-foreground">
                    {rule.met ? 'held' : 'did not hold'}
                  </span>
                </span>
              </li>
            ))}
            {!(trigger.rule_matches || []).length ? (
              <li className="text-xs text-muted-foreground">
                This workflow carries no targeting rule, so every page counted toward the repeat
                total.
              </li>
            ) : null}
          </ul>
        </div>

        {blocks.length ? (
          <div>
            <p className="text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
              What the buyer was shown
            </p>
            <ul className="mt-2 flex flex-col gap-2">
              {blocks.map((block, index) => (
                <li key={`${block.kind}-${index}`} className="rounded-sm border border-border-subtle p-3">
                  <p className="text-sm text-foreground">{block.text}</p>
                  {block.apps?.length ? (
                    <ul className="mt-2 flex flex-wrap gap-2">
                      {block.apps.map((app, position) => (
                        <li
                          key={`${app.kind}-${position}`}
                          className="inline-flex items-center gap-2 rounded-xs border border-border-subtle bg-muted px-2 py-1 text-xs"
                        >
                          <span className="font-mono text-muted-foreground">{app.kind}</span>
                          <span className="text-foreground">{app.title}</span>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <div>
          <p className="text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
            What the buyer did
          </p>
          <p className="mt-1 font-mono text-xs text-muted-foreground">
            {delivery.receipt_count
              ? delivery.receipt_kinds.join(', ')
              : 'nothing recorded against this block yet'}
          </p>
        </div>

        <p className="rounded-sm border border-border-subtle bg-muted px-3 py-2 text-xs text-muted-foreground">
          This is a record of what the room decided, not a message that was delivered. Nothing was
          posted to any vendor and no browser messenger was opened.
        </p>
      </div>
    </Card>
  )
}

/** Every judgement this workflow rests on, and what would change each one. */
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
        {inferences.data.count} recorded, of which {inferences.data.sourced_count} the research states
        and {inferences.data.inferred_count} it does not. The unsourced half is where the repeat count,
        the window and the dwell threshold live.
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
            <p className="mt-1 text-sm text-muted-foreground">{entry.why}</p>
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
