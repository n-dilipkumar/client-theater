import { useMemo, useState } from 'react'

import {
  Badge,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Icon,
  Spinner,
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'

import { pdfAnalyticsApi } from './api'
import { DwellBars, EventSeries, PageAxis, RetentionCurve } from './charts'
import { DROP_OFF_ICON, DWELL_ICON, PAGE_STACK_ICON, WATCH_ICON } from './icons'
import { describeAsset, formatCount, formatDuration, formatRate, unavailableReason } from './format'

/**
 * WF-018: read per-page dwell time and drop-off inside a PDF.
 *
 * The page follows the researched flow exactly: the Library on the left, the
 * asset's **Advanced Analytics** on the right, and inside it the three blocks
 * the source product puts there - **Core Analytics**, **PDF Analytics** and
 * **Video Analytics**.
 *
 * Three things this page is careful about, because each is easy to get wrong:
 *
 * 1. **A block that does not apply says why, in words.** "Multi-page PDFs" and
 *    "self-hosted videos" are restrictions the research states, so a one-page
 *    PDF gets an explanation rather than a blank panel, and a 404-free empty
 *    curve that a seller would read as "nobody read it".
 * 2. **The external-only rule is stated on the page.** Every figure here counts
 *    buyers, and the researched exception - Shares counts the internal team - is
 *    printed next to the number it applies to, so "views 0, shares 4" reads as
 *    a policy rather than as a bug.
 * 3. **Every chart has a table.** The SVGs are `aria-hidden`; the same numbers
 *    are in real `<table>` elements a screen reader can reach, and each figure
 *    has a text label beside it rather than a bare glyph.
 *
 * No writes happen on this page. The research says analytics are computed
 * continuously and the *action* on them is manual, so there is no "record a
 * page view" button here: in the source product the buyer's viewer emits the
 * timing, not the seller's dashboard.
 */

const GRAINS = [
  { value: 'day', label: 'Daily' },
  { value: 'week', label: 'Weekly' },
  { value: 'month', label: 'Monthly' },
]

function PanelHeading({ path, title, children }) {
  return (
    <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
        <Icon path={path} size={16} className="text-accent" />
        {title}
      </h3>
      {children}
    </div>
  )
}

/** A researched block that does not apply, with the reason in a rep's words. */
function Unavailable({ block, reason }) {
  return (
    <Card>
      <PanelHeading path={PAGE_STACK_ICON} title={block} />
      <p className="text-sm text-muted-foreground">{unavailableReason(block, reason)}</p>
    </Card>
  )
}

/** One row of the Library list. A button, so it is reachable by keyboard. */
function AssetRow({ asset, active, onSelect }) {
  const unavailable = []
  if (asset.type === 'pdf' && asset.pdfAnalyticsAvailable === false) unavailable.push('no per-page curve')
  if (asset.type === 'video' && asset.videoAnalyticsAvailable === false) unavailable.push('not self-hosted')
  if (asset.trackingEnabled === false) unavailable.push('tracking off')

  return (
    <button
      type="button"
      onClick={() => onSelect(asset.id)}
      aria-pressed={active}
      className={`min-h-11 w-full rounded-lg border px-3 py-2 text-left transition-colors duration-200 ${
        active
          ? 'border-accent/50 bg-accent/10'
          : 'border-border-subtle/40 bg-background/40 hover:border-border-subtle hover:bg-muted/40'
      }`}
    >
      <span className="flex items-baseline justify-between gap-2">
        <span className="truncate text-sm font-medium text-foreground">{asset.name}</span>
        {asset.isInternal && <Badge tone="restore">internal</Badge>}
      </span>
      <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        <span>{describeAsset(asset)}</span>
        {asset.counts && <span>· {formatCount(asset.counts.views)} views</span>}
        {unavailable.map((note) => (
          <span key={note} className="text-amber-300">
            · {note}
          </span>
        ))}
      </span>
    </button>
  )
}

/** The per-page numbers, as a real table for anyone not reading the bars. */
function PageTable({ dwell, dropOff }) {
  return (
    <details className="mt-4 rounded-lg border border-border-subtle/40 bg-background/40 p-3">
      <summary className="min-h-11 cursor-pointer py-2 text-sm font-medium text-foreground">
        Per-page numbers (time spent, reads, drop off)
      </summary>
      <div className="max-h-96 overflow-auto">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Average time spent per page, how many readers reached it, and how many left there</caption>
          <thead className="sticky top-0 bg-card text-xs text-muted-foreground">
            <tr>
              <th scope="col" className="py-2 pr-3 font-medium">Page</th>
              <th scope="col" className="py-2 pr-3 font-medium">Avg time spent</th>
              <th scope="col" className="py-2 pr-3 font-medium">Reads</th>
              <th scope="col" className="py-2 pr-3 font-medium">Reached</th>
              <th scope="col" className="py-2 font-medium">Stopped here</th>
            </tr>
          </thead>
          <tbody>
            {dwell.map((row, index) => {
              const curve = dropOff[index] || {}
              return (
                <tr key={row.page} className="border-t border-border-subtle/30">
                  <th scope="row" className="py-1.5 pr-3 font-mono font-normal text-foreground">
                    {row.page}
                    {curve.is_last_page && <span className="ml-1 text-muted-foreground">(last)</span>}
                  </th>
                  <td className="py-1.5 pr-3 font-mono text-accent">{formatDuration(row.average_seconds)}</td>
                  <td className="py-1.5 pr-3 font-mono text-muted-foreground">{formatCount(row.reads)}</td>
                  <td className="py-1.5 pr-3 font-mono text-muted-foreground">{formatCount(curve.reached)}</td>
                  <td className="py-1.5 font-mono text-muted-foreground">
                    {curve.is_last_page
                      ? 'last page'
                      : `${formatCount(curve.dropped)} (${formatRate(curve.drop_off_rate)})`}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </details>
  )
}

export default function PdfAnalytics() {
  const [roomId, setRoomId] = useState('')
  const [search, setSearch] = useState('')
  const [grain, setGrain] = useState('day')
  const [selected, setSelected] = useState(null)

  const vocabulary = useAsync(() => pdfAnalyticsApi.vocabulary(), [])
  const inferences = useAsync(() => pdfAnalyticsApi.inferences(), [])
  const rooms = useAsync(() => pdfAnalyticsApi.rooms(), [])

  // A room selection switches to the room-scoped route rather than filtering the
  // library in the browser: the figures on the right are then that room's
  // readers, and a room number can never be read as a library-wide one.
  //
  // Both branches unwrap `assets`. The library-wide route returns the whole
  // `{assets, count}` object, so returning it raw made `library.data` an object
  // on this path and an array on the room path -- and `assets.map` then threw on
  // a page that had never been loaded with a room selected, which is the default.
  // A crash here unmounts the whole React root, so every other page in the app
  // went blank behind it. One `.map` on a mismatched shape, and 34 of 48 pages
  // stopped rendering.
  const library = useAsync(
    () =>
      roomId
        ? pdfAnalyticsApi.roomAssets(roomId, { grain }).then((data) => data.assets || [])
        : pdfAnalyticsApi.assets({ q: search, limit: 300 }).then((data) => data.assets || []),
    [roomId, search, grain],
  )

  const detail = useAsync(
    () =>
      selected
        ? pdfAnalyticsApi.detail(selected, { room_id: roomId, grain })
        : Promise.resolve(null),
    [selected, roomId, grain],
  )

  // `Array.isArray` rather than `|| []`: a `||` guard only catches null and
  // undefined, so an object where a list belongs sails straight through and
  // throws at the first `.map`. There is no error boundary above this page, so
  // the throw unmounts the whole application.
  const assets = Array.isArray(library.data) ? library.data : []
  const roomList = Array.isArray(rooms.data?.records) ? rooms.data.records : []
  const panels = detail.data?.advanced_analytics || null
  const asset = detail.data?.asset || null
  const pdf = panels?.pdf
  const video = panels?.video
  const core = panels?.core

  const neverRead = useMemo(
    () => (pdf?.pages_never_read || []).slice(0, 12),
    [pdf],
  )

  if (vocabulary.loading || library.loading) {
    return <Spinner label="Loading the library" />
  }
  if (vocabulary.error) {
    return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  }
  if (library.error) {
    return <ErrorNote error={library.error} onRetry={library.refetch} />
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-lg font-semibold text-foreground">
            <Icon path={PAGE_STACK_ICON} size={20} className="text-accent" />
            PDF analytics
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Per-page dwell time and the drop-off curve for every multi-page PDF in the library, plus average
            watch time for self-hosted video. Every figure counts buyers; Shares is the one internal metric.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="wf018-grain" className="text-xs font-medium text-muted-foreground">
            Chart grain
          </label>
          <select
            id="wf018-grain"
            value={grain}
            onChange={(event) => setGrain(event.target.value)}
            className={inputClass}
          >
            {GRAINS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
      </header>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
        <div className="space-y-3">
          <Field
            label="Room"
            id="wf018-room"
            hint="All rooms reads the whole library, as the source's Library view does. The demo dataset has rooms with duplicate names, so each option carries the last of its id."
          >
            <select
              id="wf018-room"
              value={roomId}
              onChange={(event) => setRoomId(event.target.value)}
              className={inputClass}
            >
              <option value="">All rooms (whole library)</option>
              {roomList.map((room) => (
                <option key={room.id} value={room.id}>
                  {room.data?.name || room.id} · {String(room.id).slice(-6)}
                </option>
              ))}
            </select>
          </Field>

          {!roomId && (
            <Field label="Find an asset" id="wf018-search">
              <input
                id="wf018-search"
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Deck, deck, one-pager…"
                className={inputClass}
              />
            </Field>
          )}

          {assets.length === 0 ? (
            <EmptyState
              title="No library assets yet"
              description="Register an asset from the researched snapshot, or record an asset.viewed event carrying one, and it appears here."
            />
          ) : (
            <ul className="space-y-2">
              {assets.map((entry) => (
                <li key={entry.id}>
                  <AssetRow asset={entry} active={entry.id === selected} onSelect={setSelected} />
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="space-y-4">
          {!selected && (
            <EmptyState
              title="Choose an asset"
              description="Open a multi-page PDF to read its time spent per page and where readers stop."
            />
          )}

          {selected && detail.loading && <Spinner label="Loading advanced analytics" />}
          {selected && detail.error && <ErrorNote error={detail.error} onRetry={detail.refetch} />}

          {selected && asset && (
            <>
              <Card>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="truncate text-base font-semibold text-foreground">{asset.name}</h2>
                    <p className="mt-1 text-sm text-muted-foreground">{describeAsset(asset)}</p>
                    {Array.isArray(asset.tags) && asset.tags.length > 0 && (
                      <ul className="mt-2 flex flex-wrap gap-1.5">
                        {asset.tags.map((tag) => (
                          <li key={tag}>
                            <Badge>{tag}</Badge>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                  <ul className="flex flex-wrap gap-1.5">
                    {asset.isInternal && <li><Badge tone="restore">Internal asset</Badge></li>}
                    {/* neutral, not the `delete` tone: tracking being off is a
                        state of the asset, not a destructive action, and the
                        destructive tint measures 3.75:1 on its own background. */}
                    {asset.trackingEnabled === false && <li><Badge>Tracking off</Badge></li>}
                    {asset.downloadEnabled === false && <li><Badge>Downloads disabled</Badge></li>}
                  </ul>
                </div>
              </Card>

              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <StatCard
                  label="Buyer views"
                  value={formatCount(core?.counts?.views)}
                  hint="External users only"
                  icon="audit"
                />
                <StatCard
                  label="Downloads"
                  value={formatCount(core?.counts?.downloads)}
                  hint="External users only"
                  icon="database"
                />
                <StatCard
                  label="Shares"
                  value={formatCount(core?.counts?.shares)}
                  hint="The one internal metric"
                  icon="plus"
                />
                <StatCard
                  label="Reading sessions"
                  value={formatCount(pdf?.drop_off?.sessions)}
                  hint={
                    pdf?.drop_off
                      ? `${formatCount(pdf.drop_off.completed_sessions)} reached the last page`
                      : 'No per-page curve'
                  }
                  icon="refresh"
                />
              </div>

              {pdf?.available === false ? (
                <Unavailable block="PDF Analytics" reason={pdf.reason} />
              ) : (
                pdf && (
                  <Card>
                    <PanelHeading path={DWELL_ICON} title="PDF Analytics — time spent per page">
                      <span className="text-xs text-muted-foreground">
                        {formatCount(pdf.readings)} readings · buyers only
                      </span>
                    </PanelHeading>
                    <p className="mb-3 text-sm text-muted-foreground">
                      The average amount of time each page is spent on. A hairline bar is a page nobody read.
                    </p>
                    <DwellBars points={pdf.time_spent_per_page} />
                    <PageAxis count={pdf.page_count} />
                    {neverRead.length > 0 && (
                      <p className="mt-3 text-xs text-amber-300">
                        {neverRead.length} page{neverRead.length === 1 ? '' : 's'} never read
                        {neverRead.length > 12 ? ` (pages ${neverRead[0]}–${neverRead[neverRead.length - 1]} and more)` : `: ${neverRead.join(', ')}`}
                      </p>
                    )}

                    <div className="mt-6">
                      <PanelHeading path={DROP_OFF_ICON} title="Drop off per page">
                        <span className="text-xs text-muted-foreground">
                          {formatRate(pdf.drop_off_per_page[0]?.retained_rate)} of readers open at page 1
                        </span>
                      </PanelHeading>
                      <p className="mb-3 text-sm text-muted-foreground">
                        Where readers stop. Each step is the share of the readers who reached that page
                        and then stopped there, so a fall on the cover means the cover.
                      </p>
                      <RetentionCurve points={pdf.drop_off_per_page} />
                      <PageAxis count={pdf.page_count} />
                    </div>

                    <PageTable dwell={pdf.time_spent_per_page} dropOff={pdf.drop_off_per_page} />
                  </Card>
                )
              )}

              {video?.available === false ? (
                <Unavailable block="Video Analytics" reason={video.reason} />
              ) : (
                video && (
                  <Card>
                    <PanelHeading path={WATCH_ICON} title="Video Analytics — average watch time">
                      <span className="text-xs text-muted-foreground">
                        {formatCount(video.watches)} watches · buyers only
                      </span>
                    </PanelHeading>
                    <p className="font-mono text-3xl font-semibold text-foreground">
                      {formatDuration(video.average_seconds)}
                    </p>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {formatCount(video.unique_viewers)} unique viewers · shortest{' '}
                      {formatDuration(video.shortest_seconds)} · longest {formatDuration(video.longest_seconds)}
                    </p>
                  </Card>
                )
              )}

              <Card>
                <PanelHeading path={PAGE_STACK_ICON} title="Core Analytics">
                  <span className="text-xs text-muted-foreground">by {core?.grain || grain}</span>
                </PanelHeading>
                <p className="mb-3 text-sm text-muted-foreground">
                  Views and downloads count buyers. Shares counts how often the internal team shared the asset.
                </p>
                <EventSeries series={core?.series || []} />
              </Card>
            </>
          )}
        </div>
      </div>

      {inferences.data && (
        <Card>
          <PanelHeading path={PAGE_STACK_ICON} title="What is sourced, and what is inferred" />
          <p className="mb-3 max-w-3xl text-sm text-muted-foreground">
            The research for this workflow names the metrics, the asset fields, the webhook events and the
            external-only rule, and says plainly that there is no documented per-page endpoint. Everything
            below the line is a judgement call, written down so it can be disagreed with by name.
          </p>
          <ul className="space-y-2">
            {(inferences.data.inferences || []).map((entry) => (
              <li key={entry.id} className="rounded-lg border border-border-subtle/40 bg-background/40 p-3">
                <p className="text-sm font-medium text-foreground">
                  {entry.topic}
                  {entry.topic_note && <span className="ml-2 text-xs text-amber-300">{entry.topic_note}</span>}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">{entry.basis}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  <span className="text-foreground">This build chose: </span>
                  <span className="font-mono break-all">{JSON.stringify(entry.value)}</span>
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  <span className="text-foreground">Change it: </span>
                  {entry.change_it}
                </p>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {vocabulary.data && (
        <p className="text-xs text-muted-foreground">
          Accepted event types: {vocabulary.data.webhook_event_types.join(', ')}. Asset types:{' '}
          {vocabulary.data.asset_types.join(', ')}. PDF Analytics needs{' '}
          {vocabulary.data.min_pages_for_pdf_analytics}+ pages. Internal metrics:{' '}
          {vocabulary.data.internal_only_metrics.join(', ')}.
        </p>
      )}
    </div>
  )
}
