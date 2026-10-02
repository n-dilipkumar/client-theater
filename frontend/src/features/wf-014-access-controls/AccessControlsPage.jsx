import { useState } from 'react'
import { apiRequest } from '@/lib/api'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Spinner,
  StatCard,
  useAsync,
} from '@/components/ui'
import { BuyerBanner } from './primitives'
import WindowEditor from './WindowEditor'

/**
 * WF-014: expire or cap access to a room.
 *
 * The screen a seller opens to answer one question: which of my links stop
 * working, when, and why. Two constraints, independently settable, and the page
 * leads with the thing that is easy to get wrong elsewhere - a badge of `live`
 * does not mean the link is open, so every closed link is listed with the reason
 * it is closed rather than being left to be inferred from a status.
 */

const STATUS_TONE = {
  live: 'success',
  expiring_soon: 'restore',
  declined: 'delete',
  view_limit: 'insert',
  draft: 'neutral',
}

const STATUS_LABEL = {
  live: 'Live',
  expiring_soon: 'Expiring soon',
  declined: 'Declined',
  view_limit: 'View Limit',
  draft: 'Draft',
}

const CLOSED_REASON = {
  expired: 'expired on its date',
  view_limit: 'out of views',
  declined: 'declined by hand',
  unpublished: 'never published',
}

async function act(pageId, path, method, body) {
  return apiRequest(path, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-Role': 'room_collaborator' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

/** One page's window: the badge, why it is closed, and the two switches. */
function WindowRow({ window, buyerView, onChange }) {
  const [busy, setBusy] = useState('')

  async function run(key, path, method, body) {
    setBusy(key)
    try {
      await act(window.page_id, path, method, body)
      await onChange()
    } finally {
      setBusy('')
    }
  }

  const closed = window.closed_by.length > 0

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-mono text-[13px] text-muted-foreground">{window.page_id}</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <Badge tone={STATUS_TONE[window.access_status] || 'neutral'}>
              {STATUS_LABEL[window.access_status] || window.access_status}
            </Badge>
            {/* The distinction the whole workflow turns on. */}
            {!closed && <span className="text-sm text-foreground">Open to buyers</span>}
            {closed && (
              <span className="text-sm text-muted-foreground">
                Closed - {window.closed_by.map((reason) => CLOSED_REASON[reason] || reason).join(', ')}
              </span>
            )}
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button
            variant="secondary"
            disabled={busy !== ''}
            onClick={() => run('view', `/wf-014/pages/${window.page_id}/views`, 'POST', { viewer: 'alex@northwind.example' })}
          >
            {busy === 'view' ? 'Counting' : 'Count a view'}
          </Button>
          {window.manual_status === 'declined' ? (
            <Button
              variant="primary"
              disabled={busy !== ''}
              onClick={() => run('live', `/wf-014/pages/${window.page_id}/set-live`, 'POST')}
            >
              Set live
            </Button>
          ) : (
            <Button
              variant="danger"
              disabled={busy !== ''}
              onClick={() => run('decline', `/wf-014/pages/${window.page_id}/decline`, 'POST')}
            >
              Decline
            </Button>
          )}
        </div>
      </div>

      <dl className="mt-4 grid gap-3 sm:grid-cols-2">
        <div>
          <dt className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            Expiry
          </dt>
          <dd className="mt-1 text-sm text-foreground">
            {window.expiry.enabled
              ? window.expiry.expires_at
                ? `Stops ${window.expiry.expires_at.slice(0, 10)}`
                : 'Waiting for publish'
              : 'None'}
          </dd>
        </div>
        <div>
          <dt className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            Views
          </dt>
          <dd className="mt-1 font-mono text-sm text-foreground">
            {window.view_limit.enabled
              ? `${window.view_limit.views} / ${window.view_limit.max_views}`
              : `${window.view_limit.views} / unlimited`}
          </dd>
        </div>
      </dl>

      <details className="mt-4">
        <summary className="min-h-11 cursor-pointer py-2 text-sm font-medium text-accent">
          Link settings
        </summary>
        <div className="mt-2">
          <WindowEditor window={window} onChange={onChange} />
        </div>
      </details>

      <BuyerPreview window={window} buyerView={buyerView} onChange={onChange} />
    </Card>
  )
}

/** What a buyer following this link sees, fetched from the buyer route. */
function BuyerPreview({ window, buyerView, onChange }) {
  const { data, loading, error } = useAsync(
    () => apiRequest(`/wf-014/pages/${window.page_id}/buyer`),
    [window.page_id],
  )

  if (loading) return <p className="mt-4 text-sm text-muted-foreground">Loading buyer view...</p>
  if (error) return <p className="mt-4 text-sm text-destructive">{String(error.message || error)}</p>

  const view = data || buyerView
  if (!view) return null

  return (
    <div className="mt-4 rounded-sm border border-border-subtle bg-muted p-4">
      <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        What a buyer sees
      </p>
      {/* The real banner, rendered inline rather than fixed to the viewport: this
          page is a preview of the buyer's view, not the buyer's view itself, so a
          `position: fixed` element here would overlap the seller's own screen.
          The researched placement is carried in `message_placement` below. */}
      <div className="mt-2">
        <BuyerBanner view={view} onDismiss={onChange} />
      </div>
      <p className="mt-1.5 text-sm text-foreground">
        {view.message || 'The page, with no notice.'}
      </p>
      <p className="mt-1 font-mono text-xs text-muted-foreground">
        state={view.state} placement={view.message_placement} show_content={String(view.show_content)}
      </p>
      <div className="mt-3">
        <Button variant="ghost" onClick={() => onChange()}>
          Refresh
        </Button>
      </div>
    </div>
  )
}

export default function AccessControlsPage() {
  const [filter, setFilter] = useState('')
  const { data, loading, error, refetch } = useAsync(
    () => apiRequest('/wf-014/summary'),
    [],
  )
  const pages = useAsync(
    () => apiRequest(`/wf-014/pages${filter ? `?status=${encodeURIComponent(filter)}` : ''}`),
    [filter],
  )

  if (loading) return <Spinner label="Loading access windows" />
  if (error) return <ErrorNote error={error} onRetry={refetch} />

  const summary = data || {}
  const windows = pages.data?.windows || []

  async function reload() {
    await Promise.all([refetch(), pages.refetch()])
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-semibold text-foreground">Access controls</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Bound a room&rsquo;s link by date and by view count. A link badged Live is not
          necessarily open: a page over its view limit keeps its Live status and is
          closed to every buyer.
        </p>
      </header>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Links" value={summary.total ?? 0} hint="pages with an access window" />
        <StatCard label="Open" value={summary.open ?? 0} hint="a buyer can open these" />
        <StatCard label="Closed" value={summary.closed ?? 0} hint="expired, capped or declined" />
        <StatCard label="Expiring soon" value={summary.by_status?.expiring_soon ?? 0} hint="within 7 days" />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          Filter
        </span>
        {['', 'live', 'expiring_soon', 'view_limit', 'declined', 'draft'].map((status) => (
          <Button
            key={status || 'all'}
            variant={filter === status ? 'primary' : 'secondary'}
            onClick={() => setFilter(status)}
          >
            {status === '' ? 'All' : STATUS_LABEL[status] || status}
          </Button>
        ))}
      </div>

      {pages.loading && <Spinner label="Loading pages" />}

      {pages.error && <ErrorNote error={pages.error} onRetry={pages.refetch} />}

      {!pages.loading && !pages.error && windows.length === 0 && (
        <EmptyState
          title="No pages match this filter"
          description={
            filter
              ? 'Nothing is badged with that status right now.'
              : 'Publish a page, then come back to give its link a window.'
          }
        />
      )}

      <div className="space-y-4">
        {windows.map((window) => (
          <WindowRow
            key={window.page_id}
            window={window}
            onChange={reload}
          />
        ))}
      </div>
    </div>
  )
}
