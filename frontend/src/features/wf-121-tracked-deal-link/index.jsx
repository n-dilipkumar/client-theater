/**
 * WF-121: tracked deal-link distribution with per-page dwell.
 *
 * One link a rep shares, the per-page dwell and revisits it records, the rep
 * alerts it raises, and the engagement record it writes back to the room's CRM
 * opportunity.
 *
 * The page is built from `@/components/ui` and from the primitives in this
 * folder. It reads its states, its alert triggers and its reused values from the
 * server's `/vocabulary` route rather than repeating them, so the panel cannot
 * tell a seller a value the rules do not accept.
 *
 * Four things this page deliberately does not do, each from the research:
 *
 *   - It does not render the document. The researched viewer (PDF.js in an
 *     in-browser viewer, object storage behind it) is out of scope; the link
 *     references a WF-018 library asset and this product holds no document
 *     bytes. The "Record a reading" control below is the viewer's ingest, so a
 *     reviewer can produce telemetry, and it is labelled as such.
 *   - It does not claim to have sent an email. This product has no mail
 *     transport, so an alert is a recorded row.
 *   - It does not build a second gate. Password, expiry and email verification
 *     are WF-069's, and this page says so rather than offering a password box.
 *   - It does not show a CRM row the vendor has not made. The write is queued
 *     through WF-037's worker; with no connector registered every row is
 *     blocked by name, and the page shows that rather than a green tick.
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
  ALERT_LABELS,
  formatSeconds,
  listRooms,
  shortTimestamp,
  trackedLinkApi,
  wholeInRange,
} from './api'
import {
  AlertRow,
  DropOffList,
  InlineNote,
  PageDwellBars,
  StateBadge,
  ViewerRow,
} from './primitives'

const LIFETIME_MIN = 1
const LIFETIME_MAX = 3650
const PAGE_MIN = 1
const PAGE_MAX = 1000

/** Everything the page reads, loaded together so the panels cannot disagree. */
function useBoard(roomId, selectedLink, selectedViewer) {
  const vocabulary = useAsync(() => trackedLinkApi.vocabulary(), [])
  const summary = useAsync(() => trackedLinkApi.summary(roomId), [roomId])
  const links = useAsync(() => trackedLinkApi.links(roomId), [roomId])
  const alerts = useAsync(() => trackedLinkApi.alerts(roomId), [roomId])
  const analytics = useAsync(
    () => (selectedLink ? trackedLinkApi.analytics(selectedLink) : Promise.resolve(null)),
    [selectedLink],
  )
  const deliveries = useAsync(
    () => (selectedLink ? trackedLinkApi.deliveries(selectedLink) : Promise.resolve(null)),
    [selectedLink],
  )
  const viewerPages = useAsync(
    () =>
      selectedLink && selectedViewer
        ? trackedLinkApi.viewerViews(selectedLink, selectedViewer)
        : Promise.resolve(null),
    [selectedLink, selectedViewer],
  )

  const loading = vocabulary.loading || summary.loading || links.loading || alerts.loading
  const error = vocabulary.error || summary.error || links.error || alerts.error
  return { vocabulary, summary, links, alerts, analytics, deliveries, viewerPages, loading, error }
}

function TrackedDealLinkPage() {
  const [roomId, setRoomId] = useState('')
  const [selectedLink, setSelectedLink] = useState(null)
  const [selectedViewer, setSelectedViewer] = useState(null)
  const [note, setNote] = useState(null)
  const [problem, setProblem] = useState(null)

  const rooms = useAsync(() => listRooms(), [])
  const board = useBoard(roomId, selectedLink, selectedViewer)
  const link = board.analytics.data?.link || null

  function refetchAll() {
    board.summary.refetch()
    board.links.refetch()
    board.alerts.refetch()
    board.analytics.refetch()
    board.deliveries.refetch()
    board.viewerPages.refetch()
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

  if (board.loading && !board.summary.data) return <Spinner label="Loading tracked links" />
  if (board.error) return <ErrorNote error={board.error} onRetry={refetchAll} />

  const vocabulary = board.vocabulary.data
  const summary = board.summary.data
  const links = board.links.data?.links || []
  const alerts = board.alerts.data?.alerts || []
  const analytics = board.analytics.data

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold text-foreground">Tracked deal link</h1>
        <p className="text-sm text-muted-foreground">
          One link, shared with a buying committee: who viewed, which page, for how long, on which
          revisit.
        </p>
      </header>

      {note && <InlineNote tone="success">{note}</InlineNote>}
      {problem && <ErrorNote error={problem} />}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Links"
          value={summary?.links ?? 0}
          icon="rooms"
          hint={`${summary?.open ?? 0} open, ${summary?.expired ?? 0} expired, ${summary?.revoked ?? 0} revoked`}
        />
        <StatCard label="Page views" value={summary?.views ?? 0} icon="audit" hint="Buyer views recorded" />
        <StatCard label="Viewers" value={summary?.viewers ?? 0} icon="database" hint="Distinct buyers" />
        <StatCard label="Alerts" value={summary?.alerts ?? 0} icon="schema" hint="Raised for the rep" />
      </div>

      <CreateLinkCard
        vocabulary={vocabulary}
        roomId={roomId}
        onCreate={(payload) =>
          act(
            () => trackedLinkApi.createLink(roomId, payload),
            (created) => `Created ${created.title}. The share URL is ${created.share_url}.`,
          )
        }
      />

      <Card className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold text-foreground">Tracked links</h2>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Room" id="wf121-room" hint="A link belongs to a room.">
            <select
              id="wf121-room"
              className={inputClass}
              value={roomId}
              onChange={(event) => {
                setRoomId(event.target.value)
                setSelectedLink(null)
                setSelectedViewer(null)
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

        {links.length === 0 ? (
          <EmptyState
            title="No tracked link yet"
            description="Pick a room and share one link over a deck in the library. The view telemetry appears here."
          />
        ) : (
          <ul className="flex flex-col">
            {links.map((row) => (
              <li
                key={row.id}
                className="flex flex-wrap items-center justify-between gap-3 border-b border-border-subtle py-3 last:border-b-0"
              >
                <span className="flex min-w-0 flex-col">
                  <span className="truncate text-sm text-foreground">{row.title}</span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {row.id} | {row.asset_name || row.asset_id} | {row.page_count ?? '?'} page(s) |{' '}
                    expires {shortTimestamp(row.expires_at)}
                  </span>
                  <span className="truncate font-mono text-xs text-muted-foreground">
                    {row.share_url || 'no share URL yet'}
                  </span>
                </span>
                <span className="flex flex-wrap items-center gap-2">
                  <StateBadge state={row.state} />
                  <Button
                    icon="audit"
                    onClick={() => {
                      setSelectedLink(row.id)
                      setSelectedViewer(null)
                    }}
                  >
                    Analytics
                  </Button>
                  <Button
                    icon="plus"
                    onClick={() =>
                      act(
                        () => trackedLinkApi.distribute(row.id, row.recipients),
                        'Recorded the recipients the link was shared with.',
                      )
                    }
                    disabled={!row.recipients?.length}
                  >
                    Record the share
                  </Button>
                  <Button
                    variant="danger"
                    icon="trash"
                    disabled={row.state !== 'active'}
                    onClick={() =>
                      act(() => trackedLinkApi.revokeLink(row.id), 'Revoked the link.')
                    }
                  >
                    Revoke
                  </Button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {selectedLink && link && (
        <AnalyticsPanel
          analytics={analytics}
          deliveries={board.deliveries.data}
          viewerPages={board.viewerPages.data}
          selectedViewer={selectedViewer}
          onSelectViewer={setSelectedViewer}
          onClose={() => {
            setSelectedLink(null)
            setSelectedViewer(null)
          }}
          onRecord={(view) =>
            act(
              () => trackedLinkApi.recordView(selectedLink, view),
              (result) =>
                `Recorded revisit ${result.revisit} for ${result.viewer || 'an anonymous viewer'}. ` +
                `${result.alerts.length} alert(s) raised.`,
            )
          }
          onPush={() =>
            act(
              () => trackedLinkApi.push(link.room_id),
              (result) =>
                `CRM queue: ${result.counts.synced} synced, ${result.counts.failed} failed, ` +
                `${result.counts.blocked} blocked.`,
            )
          }
        />
      )}

      <Card className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold text-foreground">Rep alerts</h2>
        {alerts.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No buyer view has raised an alert. A first view, a return visit, reaching the page
            threshold, or a long read each raise one.
          </p>
        ) : (
          <ul className="flex flex-col">
            {alerts.map((alert) => (
              <AlertRow key={alert.id || `${alert.link_id}-${alert.notified_at}`} alert={alert} />
            ))}
          </ul>
        )}
      </Card>

      <Card className="flex flex-col gap-2">
        <h2 className="text-base font-semibold text-foreground">Not built here</h2>
        <p className="text-xs text-muted-foreground">
          The research names these, and they belong to another workflow or to a deployment. They are
          listed so their absence reads as a decision rather than an oversight.
        </p>
        <ul className="list-inside list-disc text-xs text-muted-foreground">
          <li>An in-browser document viewer and its object storage. WF-018 holds the library asset.</li>
          <li>A password and email-verification gate. WF-069 owns that gate; this page has none.</li>
          <li>Email delivery for an alert. This product has no mail transport, so an alert is a row.</li>
          <li>A direct CRM write. WF-037 queues it, and WF-042 supplies the opportunity.</li>
        </ul>
      </Card>
    </div>
  )
}

function CreateLinkCard({ vocabulary, roomId, onCreate }) {
  const [draft, setDraft] = useState({
    asset_id: '',
    title: '',
    recipients: '',
    lifetime_days: '',
    alert_on_page: '',
    crm_deal_id: '',
  })
  const [busy, setBusy] = useState(false)

  const lifetime = wholeInRange(draft.lifetime_days, LIFETIME_MIN, LIFETIME_MAX)
  const pages = wholeInRange(draft.alert_on_page, PAGE_MIN, PAGE_MAX)
  const refused = []
  if (!draft.asset_id.trim()) refused.push('An asset id is required: the link shares one library deck.')
  if (draft.lifetime_days !== '' && lifetime === null)
    refused.push(`The lifetime must be a whole number from ${LIFETIME_MIN} to ${LIFETIME_MAX} days.`)
  if (draft.alert_on_page !== '' && pages === null)
    refused.push(`The page threshold must be a whole number from ${PAGE_MIN} to ${PAGE_MAX}.`)

  async function submit() {
    setBusy(true)
    const payload = {
      asset_id: draft.asset_id.trim(),
      title: draft.title.trim() || undefined,
      recipients: draft.recipients
        .split(',')
        .map((value) => value.trim())
        .filter(Boolean),
      crm_deal_id: draft.crm_deal_id.trim() || undefined,
    }
    if (lifetime !== null) payload.lifetime_days = lifetime
    if (pages !== null) payload.alert_on_page = pages
    await onCreate(payload)
    setBusy(false)
  }

  return (
    <Card className="flex flex-col gap-3">
      <h2 className="text-lg font-semibold text-foreground">Share one tracked link</h2>
      <p className="text-xs text-muted-foreground">
        The link references a deck in the content library. Per-page dwell is WF-018's metric; the
        engagement record is written back to the room's CRM opportunity by WF-037.
      </p>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Field label="Library asset id" id="wf121-asset" hint="The WF-018 asset id or external id.">
          <input
            id="wf121-asset"
            className={inputClass}
            value={draft.asset_id}
            onChange={(event) => setDraft({ ...draft, asset_id: event.target.value })}
          />
        </Field>
        <Field label="Title" id="wf121-title" hint="How the link reads to the rep.">
          <input
            id="wf121-title"
            className={inputClass}
            value={draft.title}
            onChange={(event) => setDraft({ ...draft, title: event.target.value })}
          />
        </Field>
        <Field label="Recipients" id="wf121-recipients" hint="Comma separated email addresses.">
          <input
            id="wf121-recipients"
            className={inputClass}
            value={draft.recipients}
            onChange={(event) => setDraft({ ...draft, recipients: event.target.value })}
          />
        </Field>
        <Field
          label="Lifetime in days"
          id="wf121-lifetime"
          hint={`The research names link-expiry without a rule; the default is ${
            vocabulary?.defaults?.link_lifetime_days ?? 30
          } days.`}
        >
          <input
            id="wf121-lifetime"
            className={inputClass}
            type="number"
            inputMode="numeric"
            value={draft.lifetime_days}
            placeholder="Not set, so the default applies"
            onChange={(event) => setDraft({ ...draft, lifetime_days: event.target.value })}
          />
        </Field>
        <Field
          label="Alert at page"
          id="wf121-page"
          hint={`The research names "N-th page view" without N; the default is ${
            vocabulary?.defaults?.alert_page_threshold ?? 3
          }.`}
        >
          <input
            id="wf121-page"
            className={inputClass}
            type="number"
            inputMode="numeric"
            value={draft.alert_on_page}
            placeholder="Not set, so the default applies"
            onChange={(event) => setDraft({ ...draft, alert_on_page: event.target.value })}
          />
        </Field>
        <Field
          label="CRM opportunity id"
          id="wf121-deal"
          hint="WF-042 provisions the room's deal. The write-back carries this id."
        >
          <input
            id="wf121-deal"
            className={inputClass}
            value={draft.crm_deal_id}
            onChange={(event) => setDraft({ ...draft, crm_deal_id: event.target.value })}
          />
        </Field>
      </div>
      {refused.length > 0 && <InlineNote tone="warning">{refused.join(' ')}</InlineNote>}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="primary"
          icon="plus"
          disabled={busy || !roomId || refused.length > 0}
          onClick={submit}
        >
          Share the link
        </Button>
        <span className="text-xs text-muted-foreground">
          {roomId ? 'The link belongs to the selected room.' : 'Pick a room first.'}
        </span>
      </div>
    </Card>
  )
}

function AnalyticsPanel({
  analytics,
  deliveries,
  viewerPages,
  selectedViewer,
  onSelectViewer,
  onClose,
  onRecord,
  onPush,
}) {
  const [viewer, setViewer] = useState('')
  const [pages, setPages] = useState('3')
  const [seconds, setSeconds] = useState('30')
  const link = analytics?.link
  const pageCount = wholeInRange(pages, PAGE_MIN, PAGE_MAX)
  const dwell = wholeInRange(seconds, 1, 3600)

  async function record() {
    if (pageCount === null || dwell === null) return
    const started = new Date()
    const timings = []
    let elapsed = 0
    for (let page = 1; page <= pageCount; page += 1) {
      timings.push({
        page,
        seconds: dwell,
        occurred_at: new Date(started.getTime() + elapsed * 1000).toISOString(),
      })
      elapsed += dwell + 3
    }
    await onRecord({ viewer: viewer.trim(), timings })
  }

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-foreground">{link?.title || 'Tracked link'}</h2>
          <p className="mt-0.5 font-mono text-xs text-muted-foreground">
            {link?.id} | {link?.share_url || 'no share URL'}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StateBadge state={link?.state} />
          <Button icon="close" onClick={onClose}>
            Close the analytics
          </Button>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <span className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            Buyer readings
          </span>
          <span className="font-mono text-sm text-foreground">{analytics?.readings ?? 0}</span>
        </span>
        <span className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            Revisits
          </span>
          <span className="font-mono text-sm text-foreground">
            {analytics?.revisits?.length ?? 0}
          </span>
        </span>
        <span className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            Revisit gap
          </span>
          <span className="font-mono text-sm text-foreground">
            {analytics?.session_gap_seconds ?? 0}s
          </span>
        </span>
      </div>

      <InlineNote tone="info">
        This block counts buyers only, which is WF-018's rule. Internal previews are recorded and are
        not counted, and they raise no alert.
      </InlineNote>

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="flex flex-col gap-2">
          <h3 className="text-base font-semibold text-foreground">Time spent per page</h3>
          <PageDwellBars entries={analytics?.time_spent_per_page || []} />
        </div>
        <div className="flex flex-col gap-2">
          <h3 className="text-base font-semibold text-foreground">Drop off per page</h3>
          <DropOffList
            curve={analytics?.drop_off_per_page || []}
            summary={analytics?.drop_off}
          />
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <h3 className="text-base font-semibold text-foreground">Buyers behind the link</h3>
        {(analytics?.viewers || []).length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No buyer view has been recorded yet. The link records a view when the viewer emits its
            per-page timing.
          </p>
        ) : (
          <ul className="flex flex-col">
            {analytics.viewers.map((row) => (
              <ViewerRow key={row.viewer} viewer={row} onOpen={onSelectViewer} />
            ))}
          </ul>
        )}
      </div>

      {selectedViewer && viewerPages && (
        <div className="flex flex-col gap-1">
          <h3 className="text-base font-semibold text-foreground">
            Pages read by {selectedViewer}
          </h3>
          <p className="font-mono text-xs text-muted-foreground">
            {viewerPages.readings} reading(s) in {viewerPages.revisits?.length ?? 0} revisit(s)
          </p>
          <ul className="flex flex-wrap gap-2">
            {(viewerPages.views || []).map((view) => (
              <li
                key={view.id || `${view.page}-${view.occurredAt}`}
                className="rounded-xs border border-border-subtle bg-muted px-2 py-1 font-mono text-xs text-muted-foreground"
              >
                page {view.page}: {formatSeconds(view.seconds)}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="flex flex-col gap-3">
          <h3 className="text-base font-semibold text-foreground">Record a reading</h3>
          <p className="text-xs text-muted-foreground">
            This is the viewer's ingest, not a seller control. The research's viewer emits a page
            event per page, and this records one so the analytics above can be read. No document
            body is ever sent.
          </p>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Viewer email" id="wf121-viewer" hint="The address the link was gated to.">
              <input
                id="wf121-viewer"
                className={inputClass}
                value={viewer}
                onChange={(event) => setViewer(event.target.value)}
              />
            </Field>
            <Field label="Pages read" id="wf121-pages" hint="How far the reader got.">
              <input
                id="wf121-pages"
                className={inputClass}
                type="number"
                inputMode="numeric"
                value={pages}
                onChange={(event) => setPages(event.target.value)}
              />
            </Field>
            <Field label="Seconds per page" id="wf121-seconds" hint="The dwell the viewer measured.">
              <input
                id="wf121-seconds"
                className={inputClass}
                type="number"
                inputMode="numeric"
                value={seconds}
                onChange={(event) => setSeconds(event.target.value)}
              />
            </Field>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="primary"
              icon="plus"
              disabled={pageCount === null || dwell === null}
              onClick={record}
            >
              Record the reading
            </Button>
            <span className="text-xs text-muted-foreground">
              The engagement record is queued for the CRM opportunity; nothing is sent.
            </span>
          </div>
        </div>

        <div className="flex flex-col gap-3">
          <h3 className="text-base font-semibold text-foreground">Shared with</h3>
          {(deliveries?.deliveries || []).length === 0 ? (
            <p className="text-sm text-muted-foreground">
              The link has not been recorded as shared with anyone yet.
            </p>
          ) : (
            <ul className="flex flex-col">
              {deliveries.deliveries.map((row) => (
                <li
                  key={row.id}
                  className="flex items-center justify-between gap-2 border-b border-border-subtle py-1.5 last:border-b-0"
                >
                  <span className="truncate font-mono text-xs text-foreground">{row.recipient}</span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {shortTimestamp(row.delivered_at)}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <Button icon="refresh" onClick={onPush}>
              Send the queued CRM writes
            </Button>
            <span className="text-xs text-muted-foreground">
              Runs WF-037's worker. With no connector registered every row is blocked by name, never
              lost.
            </span>
          </div>
        </div>
      </div>

      <p className="text-xs text-muted-foreground">
        Alerts this link raises:{' '}
        {Object.keys(ALERT_LABELS)
          .map((trigger) => ALERT_LABELS[trigger])
          .join(', ')}
        .
      </p>
    </Card>
  )
}

export default {
  id: 'wf-121-tracked-deal-link',
  label: 'Tracked deal link',
  icon: 'audit',
  order: 1210,
  Component: TrackedDealLinkPage,
}
