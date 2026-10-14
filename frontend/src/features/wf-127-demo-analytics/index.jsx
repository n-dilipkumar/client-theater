/**
 * WF-127: interactive demo / sandbox analytics with stakeholder discovery.
 *
 * A product recording becomes a branching, sandboxed interactive demo. Its
 * per-viewer event stream produces activity heatmaps and a six-level analytics
 * roll-up, and a buyer's internal re-share surfaces the newly discovered
 * stakeholders.
 *
 * The page is built from `@/components/ui` and from the primitives in this
 * folder. It reads its states, event types and levels from the server's
 * `/vocabulary` route rather than repeating them, so the panel cannot tell a
 * seller a value the rules do not accept.
 *
 * Four things this page deliberately does not do, each from the research:
 *
 *   - It does not build a screen-capture ingest. The researched flow is a
 *     recording converted into a demo; a real capture is out of scope, so the
 *     build converts a recording reference into a branching template and lets a
 *     seller paste their own tree.
 *   - It does not build the CRM push. The spec's dependency note names no CRM
 *     object to write to, so the analytics stay in the room and the page says so.
 *   - It does not build the Slack, Microsoft 365 or mobile distribution
 *     surfaces. This product has no app-store packaging and no mail transport.
 *   - It does not repeat the vendor's unverified claim that Consensus is the
 *     number-one demo platform. The spec marks it as a vendor claim.
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

import {
  DEFAULT_CLICK_PATH_TEXT,
  EVENT_LABELS,
  LEVEL_LABELS,
  demoApi,
  formatRate,
  listRooms,
  parseClickPath,
  shortTimestamp,
} from './api'
import {
  HeatmapGrid,
  InlineNote,
  RollupTable,
  SectionCounts,
  StakeholderRow,
  StateBadge,
} from './primitives'

const LEVELS = ['buyer_group', 'top_level', 'team', 'individual', 'recently_shared', 'all_demos']

/** Everything the page reads, loaded together so the panels cannot disagree. */
function useBoard(roomId, selectedDemo, level) {
  const vocabulary = useAsync(() => demoApi.vocabulary(), [])
  const summary = useAsync(() => demoApi.summary(roomId), [roomId])
  const demos = useAsync(() => demoApi.demos(roomId), [roomId])
  const analytics = useAsync(
    () => (selectedDemo ? demoApi.analytics(selectedDemo) : Promise.resolve(null)),
    [selectedDemo],
  )
  const rollup = useAsync(
    () => (selectedDemo ? demoApi.rollup(selectedDemo, level) : Promise.resolve(null)),
    [selectedDemo, level],
  )
  const loading = vocabulary.loading || summary.loading || demos.loading
  const error = vocabulary.error || summary.error || demos.error
  return { vocabulary, summary, demos, analytics, rollup, loading, error }
}

function InteractiveDemoAnalyticsPage() {
  const [roomId, setRoomId] = useState('')
  const [selectedDemo, setSelectedDemo] = useState(null)
  const [level, setLevel] = useState('top_level')
  const [note, setNote] = useState(null)
  const [problem, setProblem] = useState(null)

  const rooms = useAsync(() => listRooms(), [])
  const board = useBoard(roomId, selectedDemo, level)

  function refetchAll() {
    board.summary.refetch()
    board.demos.refetch()
    board.analytics.refetch()
    board.rollup.refetch()
  }

  async function act(fn, success) {
    setProblem(null)
    setNote(null)
    try {
      const result = await fn()
      setNote(typeof success === 'function' ? success(result) : success)
      refetchAll()
      return result
    } catch (error) {
      setProblem(error)
      return null
    }
  }

  if (board.loading && !board.summary.data) return <Spinner label="Loading interactive demos" />
  if (board.error) return <ErrorNote error={board.error} onRetry={refetchAll} />

  const vocabulary = board.vocabulary.data
  const summary = board.summary.data
  const demos = board.demos.data?.demos || []
  const analytics = board.analytics.data

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold text-foreground">Interactive demo analytics</h1>
        <p className="text-sm text-muted-foreground">
          A recording becomes a branching interactive demo. Its activity heatmap and six-level
          roll-up show what buyers poked at, and a buyer's internal re-share discovers the
          stakeholders behind it.
        </p>
      </header>

      {note && <InlineNote tone="success">{note}</InlineNote>}
      {problem && <ErrorNote error={problem} />}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Demos"
          value={summary?.demos ?? 0}
          icon="dashboard"
          hint={`${summary?.published ?? 0} published, ${summary?.archived ?? 0} archived`}
        />
        <StatCard
          label="Demos sent"
          value={summary?.demos_sent ?? 0}
          icon="rooms"
          hint="Distinct initial recipients"
        />
        <StatCard label="Buyer views" value={summary?.views ?? 0} icon="audit" hint="Internal previews excluded" />
        <StatCard
          label="Discovered"
          value={summary?.stakeholders_discovered ?? 0}
          icon="search"
          hint={`Discovery rate ${formatRate(summary?.discovery_rate ?? 0)}`}
        />
      </div>

      <CreateDemoCard
        vocabulary={vocabulary}
        roomId={roomId}
        onCreate={(payload) =>
          act(
            () => demoApi.createDemo(roomId, payload),
            (created) => `Built ${created.title} with ${created.node_count} node(s).`,
          )
        }
      />

      <Card className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold text-foreground">Interactive demos</h2>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Room" id="wf127-room" hint="A demo belongs to a room.">
            <select
              id="wf127-room"
              className={inputClass}
              value={roomId}
              onChange={(event) => {
                setRoomId(event.target.value)
                setSelectedDemo(null)
              }}
            >
              <option value="">Every room</option>
              {(rooms.data?.records || []).map((room) => (
                <option key={room.id} value={room.id}>
                  {room.data?.name || room.id}
                </option>
              ))}
            </select>
          </Field>
        </div>

        {demos.length === 0 ? (
          <EmptyState
            title="No interactive demo yet"
            description="Pick a room and build a demo from a recording. Its click path, activity and stakeholders appear here."
          />
        ) : (
          <ul className="flex flex-col">
            {demos.map((demo) => (
              <li
                key={demo.id}
                className="flex flex-wrap items-center justify-between gap-3 border-b border-border-subtle py-3 last:border-b-0"
              >
                <span className="flex min-w-0 flex-col">
                  <span className="truncate text-sm text-foreground">{demo.title}</span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {demo.id} | {demo.demo_type} | {demo.node_count} node(s), {demo.branch_count}{' '}
                    branch(es) | {demo.team || 'no team'} | {demo.owner || 'no owner'}
                  </span>
                </span>
                <span className="flex flex-wrap items-center gap-2">
                  <StateBadge state={demo.state} />
                  <Button
                    icon="audit"
                    onClick={() => {
                      setSelectedDemo(demo.id)
                      setLevel('top_level')
                    }}
                  >
                    Analytics
                  </Button>
                  <Button
                    icon="plus"
                    disabled={demo.state !== 'draft'}
                    onClick={() =>
                      act(() => demoApi.publishDemo(demo.id), `Published ${demo.title}.`)
                    }
                  >
                    Publish
                  </Button>
                  <Button
                    variant="danger"
                    icon="trash"
                    disabled={demo.state === 'archived'}
                    onClick={() =>
                      act(() => demoApi.archiveDemo(demo.id), `Archived ${demo.title}.`)
                    }
                  >
                    Archive
                  </Button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {selectedDemo && analytics && (
        <AnalyticsPanel
          analytics={analytics}
          rollup={board.rollup.data}
          level={level}
          onLevel={setLevel}
          onClose={() => setSelectedDemo(null)}
          onShare={(payload) =>
            act(
              () => demoApi.recordShare(selectedDemo, payload),
              (result) =>
                `Recorded ${result.count} recipient(s) for a ${result.kind} share; ` +
                `${result.stakeholders_discovered} newly discovered.`,
            )
          }
          onEvent={(payload) =>
            act(
              () => demoApi.recordEvent(selectedDemo, payload),
              (result) =>
                `Recorded ${EVENT_LABELS[result.event] || result.event} for ${result.viewer}. ` +
                `DQL ${result.dql.score}/${result.dql.max}.`,
            )
          }
        />
      )}

      <Card className="flex flex-col gap-2">
        <h2 className="text-base font-semibold text-foreground">Not built here</h2>
        <p className="text-xs text-muted-foreground">
          The research names these, and they belong to another workflow or to a deployment. They are
          listed so their absence reads as a decision rather than an oversight.
        </p>
        <ul className="list-inside list-disc text-xs text-muted-foreground">
          <li>A screen-capture ingest. A recording reference becomes a branching template; a seller may paste a tree.</li>
          <li>A CRM push. The spec's dependency note names no CRM object, so the analytics stay in the room.</li>
          <li>Slack, Microsoft 365 App Store and mobile distribution. This product has no app-store packaging.</li>
          <li>The Excalidraw whiteboard layer and the Apache Superset / Metabase roll-up. Named as extensibility, not built.</li>
          <li>The vendor's unverified claim to be the number-one demo platform. Recorded as a claim, not repeated as fact.</li>
        </ul>
      </Card>
    </div>
  )
}

function CreateDemoCard({ vocabulary, roomId, onCreate }) {
  const [title, setTitle] = useState('')
  const [demoType, setDemoType] = useState('sandbox')
  const [recordingRef, setRecordingRef] = useState('')
  const [clickPath, setClickPath] = useState(DEFAULT_CLICK_PATH_TEXT)
  const [busy, setBusy] = useState(false)

  const parsed = parseClickPath(clickPath)
  const refused = []
  if (!title.trim()) refused.push('A title is required.')
  if (parsed.error) refused.push(parsed.error)

  async function submit() {
    setBusy(true)
    await onCreate({
      title: title.trim(),
      demo_type: demoType,
      recording_ref: recordingRef.trim() || undefined,
      nodes: parsed.nodes,
    })
    setBusy(false)
  }

  return (
    <Card className="flex flex-col gap-3">
      <h2 className="text-lg font-semibold text-foreground">Build a demo from a recording</h2>
      <p className="text-xs text-muted-foreground">
        The recording reference is converted into a branching click path. The path is the demo's
        branch tree, and a buyer's selection on any node is a section selected in the analytics.
      </p>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Field label="Title" id="wf127-title" hint="How the demo reads to the seller.">
          <input
            id="wf127-title"
            className={inputClass}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </Field>
        <Field label="Demo type" id="wf127-type" hint="The researched distribution surfaces.">
          <select
            id="wf127-type"
            className={inputClass}
            value={demoType}
            onChange={(event) => setDemoType(event.target.value)}
          >
            {(vocabulary?.demo_types || ['sandbox', 'tour', 'video']).map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Recording reference" id="wf127-recording" hint="The captured session this demo is built from.">
          <input
            id="wf127-recording"
            className={inputClass}
            value={recordingRef}
            onChange={(event) => setRecordingRef(event.target.value)}
          />
        </Field>
      </div>
      <Field
        label="Click path"
        id="wf127-clickpath"
        hint="One node per line: id | label | next,next. The last column is the branch."
      >
        <textarea
          id="wf127-clickpath"
          className={`${inputClass} min-h-28 font-mono`}
          value={clickPath}
          onChange={(event) => setClickPath(event.target.value)}
          rows={5}
        />
      </Field>
      {refused.length > 0 && <InlineNote tone="warning">{refused.join(' ')}</InlineNote>}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="primary"
          icon="plus"
          disabled={busy || !roomId || refused.length > 0}
          onClick={submit}
        >
          Build the demo
        </Button>
        <span className="text-xs text-muted-foreground">
          {roomId ? 'The demo belongs to the selected room.' : 'Pick a room first.'}
        </span>
      </div>
    </Card>
  )
}

function AnalyticsPanel({ analytics, rollup, level, onLevel, onClose, onShare, onEvent }) {
  const demo = analytics.demo
  const metrics = analytics.metrics
  const heatmap = analytics.heatmap
  const sections = analytics.sections
  const stakeholders = analytics.stakeholders || []
  const [viewer, setViewer] = useState('')
  const [event, setEvent] = useState('view')
  const [section, setSection] = useState(demo.sections?.[0] || '')
  const [sharedFrom, setSharedFrom] = useState('')
  const [shareKind, setShareKind] = useState('initial')
  const [recipients, setRecipients] = useState('')
  const [sender, setSender] = useState('')

  const eventDisabled = !viewer.trim() || (event === 'section_selected' && !section)
  const shareDisabled = !recipients.trim() || (shareKind === 'forward' && !sender.trim())

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-foreground">{demo.title}</h2>
          <p className="mt-0.5 font-mono text-xs text-muted-foreground">
            {demo.id} | {demo.recording_ref || 'no recording'} | {demo.node_count} node(s) |{' '}
            published {shortTimestamp(demo.published_at)}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StateBadge state={demo.state} />
          <Button icon="close" onClick={onClose}>
            Close the analytics
          </Button>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-4">
        <StatCard label="Demos sent" value={metrics.demos_sent} hint="Distinct initial recipients" />
        <StatCard
          label="Stakeholders discovered"
          value={metrics.stakeholders_discovered}
          hint={`Discovery rate ${formatRate(metrics.discovery_rate)}`}
        />
        <StatCard label="Views" value={metrics.views} hint={`View rate ${formatRate(metrics.view_rate)}`} />
        <StatCard
          label="Sections selected"
          value={metrics.features_selected}
          hint={`${metrics.docs_downloaded} doc(s) downloaded`}
        />
      </div>

      <InlineNote tone="info">
        Every metric counts buyers only. {metrics.internal_previews} internal preview(s) are recorded
        and excluded, and discovery is inferred from the re-share: a view that arrives from a known
        stakeholder discovers the viewer behind it.
      </InlineNote>

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="flex flex-col gap-2">
          <h3 className="text-base font-semibold text-foreground">Activity heatmap</h3>
          <HeatmapGrid heatmap={heatmap} />
        </div>
        <div className="flex flex-col gap-2">
          <h3 className="text-base font-semibold text-foreground">Sections selected</h3>
          <SectionCounts sections={sections} />
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <h3 className="text-base font-semibold text-foreground">Stakeholders</h3>
        {stakeholders.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No buyer is behind this demo yet. A demo sent is a stakeholder; a re-share discovers one.
          </p>
        ) : (
          <ul className="flex flex-col">
            {stakeholders.map((stakeholder) => (
              <StakeholderRow key={stakeholder.viewer} stakeholder={stakeholder} />
            ))}
          </ul>
        )}
      </div>

      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <h3 className="text-base font-semibold text-foreground">Six-level roll-up</h3>
          <Field label="Drill-down level" id="wf127-level">
            <select
              id="wf127-level"
              className={inputClass}
              value={level}
              onChange={(event) => onLevel(event.target.value)}
            >
              {LEVELS.map((name) => (
                <option key={name} value={name}>
                  {LEVEL_LABELS[name] || name}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <RollupTable rollup={rollup} />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="flex flex-col gap-3">
          <h3 className="text-base font-semibold text-foreground">Record a viewer event</h3>
          <p className="text-xs text-muted-foreground">
            This is the viewer's ingest, not a seller control. The upstream share reference is what
            discovers a stakeholder, so it is recorded rather than assumed.
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Viewer email" id="wf127-viewer" hint="The address the demo was gated to.">
              <input
                id="wf127-viewer"
                className={inputClass}
                value={viewer}
                onChange={(event) => setViewer(event.target.value)}
              />
            </Field>
            <Field label="Event" id="wf127-event" hint="A view, a section selection or a download.">
              <select
                id="wf127-event"
                className={inputClass}
                value={event}
                onChange={(event) => setEvent(event.target.value)}
              >
                {Object.keys(EVENT_LABELS).map((name) => (
                  <option key={name} value={name}>
                    {EVENT_LABELS[name]}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Section" id="wf127-section" hint="Which click-path node was selected.">
              <select
                id="wf127-section"
                className={inputClass}
                value={section}
                onChange={(event) => setSection(event.target.value)}
              >
                {(demo.sections || []).map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Shared from" id="wf127-from" hint="The buyer who forwarded the demo, if any.">
              <input
                id="wf127-from"
                className={inputClass}
                value={sharedFrom}
                onChange={(event) => setSharedFrom(event.target.value)}
              />
            </Field>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="primary"
              icon="plus"
              disabled={eventDisabled}
              onClick={() =>
                onEvent({
                  viewer: viewer.trim(),
                  event,
                  section: event === 'section_selected' ? section : undefined,
                  shared_from: sharedFrom.trim() || undefined,
                })
              }
            >
              Record the event
            </Button>
            <span className="text-xs text-muted-foreground">
              A known upstream sender discovers the viewer.
            </span>
          </div>
        </div>

        <div className="flex flex-col gap-3">
          <h3 className="text-base font-semibold text-foreground">Record a share</h3>
          <p className="text-xs text-muted-foreground">
            An initial send mints the recipients as stakeholders. A forward re-share discovers them,
            attributed to the buyer who sent it.
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Kind" id="wf127-share-kind" hint="Initial send or forward re-share.">
              <select
                id="wf127-share-kind"
                className={inputClass}
                value={shareKind}
                onChange={(event) => setShareKind(event.target.value)}
              >
                <option value="initial">Initial send</option>
                <option value="forward">Forward re-share</option>
              </select>
            </Field>
            <Field label="Sender" id="wf127-sender" hint="The buyer who forwarded it, for a forward share.">
              <input
                id="wf127-sender"
                className={inputClass}
                value={sender}
                onChange={(event) => setSender(event.target.value)}
              />
            </Field>
            <Field label="Recipients" id="wf127-recipients" hint="Comma separated email addresses.">
              <input
                id="wf127-recipients"
                className={inputClass}
                value={recipients}
                onChange={(event) => setRecipients(event.target.value)}
              />
            </Field>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              icon="plus"
              disabled={shareDisabled}
              onClick={() =>
                onShare({
                  kind: shareKind,
                  sender: sender.trim() || undefined,
                  recipients: recipients
                    .split(',')
                    .map((value) => value.trim())
                    .filter(Boolean),
                })
              }
            >
              Record the share
            </Button>
          </div>
        </div>
      </div>
    </Card>
  )
}

export default {
  id: 'wf-127-demo-analytics',
  label: 'Interactive demos',
  icon: 'dashboard',
  order: 1270,
  Component: InteractiveDemoAnalyticsPage,
}
