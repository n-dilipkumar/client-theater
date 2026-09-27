/**
 * WF-009: approve and publish library content, immediately or on schedule.
 *
 * Ported from `frontend/src/pages/Publishing.jsx` on
 * `feature/WF-009-approve-and-publish-library-content-immediately-or-on`. The
 * layout and behaviour are the branch's; what changed is what it imports and
 * what it is allowed to touch:
 *
 * * `api` and the branch's `publishing` namespace are replaced by this feature's
 *   own wrapper in `./api`, which calls `apiRequest`. Nothing is added to the
 *   shared client.
 * * The branch's page was reachable only because it was appended to the
 *   hard-coded `ROUTES` array in `App.jsx`. It is now a folder under
 *   `src/features/`, discovered at build time, so `App.jsx` is never edited.
 * * The five `Badge` tones the branch added to `components/ui.jsx` are not added
 *   here either - see `STATUS_TONE` below for why they turned out to be
 *   unnecessary.
 *
 * The page is arranged in the order the work happens rather than in the order
 * the data is stored: a draft is submitted into an approval process, reviewers
 * clear its ordered steps, and cleared content is then published now or on a
 * schedule. Publication lands the content in whichever Dynamic Folders its own
 * metadata matches.
 *
 * Two things from the researched API are deliberately visible here rather than
 * hidden: a publish can partly succeed, so the result panel names every item
 * that did not; and a scheduled publish does not fire on its own, so the queue
 * offers an explicit sweep instead of pretending something is running.
 */

import { useMemo, useState } from 'react'
import { absoluteTime, relativeTime } from '@/lib/api'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Icon,
  JsonView,
  Spinner,
  inputClass,
  useAsync,
} from '@/components/ui'
import { publishingApi } from './api'
import { ICONS } from './icons'

/** Documented batch ceilings, mirrored from the API so the limit is visible
 *  before a request is rejected rather than after. */
const IMMEDIATE_LIMIT = 10
const SCHEDULED_LIMIT = 50

/**
 * Status vocabulary mapped onto tones the shared `Badge` already has.
 *
 * The branch added `pending`, `approved`, `rejected`, `scheduled`, and `draft` to
 * `ACTION_TONES` in `components/ui.jsx`. That file is shared, and a feature
 * appending to it is the collision the plugin host removes - so the branch's edit
 * is not carried over. It turns out not to be needed: comparing the two lists,
 * the branch's five tones are *byte-identical* to five that already exist.
 *
 *   branch `pending`    == shared `restore`  (amber)
 *   branch `approved`   == shared `insert`   (accent)
 *   branch `rejected`   == shared `delete`   (destructive)
 *   branch `scheduled`  == shared `update`   (sky)
 *   branch `draft`      == shared `neutral`
 *
 * So the vocabulary maps onto what is there and the page renders identically,
 * with no local badge component and no edit to a shared file.
 */
const STATUS_TONE = {
  Pending: 'restore',
  Approved: 'insert',
  Rejected: 'delete',
  Draft: 'neutral',
  Published: 'insert',
  scheduled: 'update',
  published: 'insert',
  rejected: 'delete',
}

function toneFor(status) {
  return STATUS_TONE[status] || 'neutral'
}

function isPublished(document) {
  return String(document.data?.status || '').toLowerCase() === 'published'
}

/* -------------------------------------------------------------------------- */
/* Reviewer identity                                                          */
/* -------------------------------------------------------------------------- */

/**
 * The API records who acted, and there is no auth layer in this project, so the
 * reviewer is chosen explicitly. Naming yourself is the point: it is what makes
 * the audit log attributable rather than anonymous.
 */
function ReviewerBar({ reviewer, onChange }) {
  return (
    <Card>
      <div className="flex flex-wrap items-end gap-4">
        <Field
          label="Acting as"
          id="wf009-reviewer"
          hint="Recorded on the workflow and in the audit log."
        >
          <input
            id="wf009-reviewer"
            className={inputClass}
            value={reviewer}
            onChange={(event) => onChange(event.target.value)}
            placeholder="reviewer name"
          />
        </Field>
        <p className="flex-1 pb-3 text-xs text-muted-foreground">
          A step with nobody assigned accepts any reviewer. A step with approvers
          accepts only them, and a step further down the queue is not actionable
          until the step before it is decided.
        </p>
      </div>
    </Card>
  )
}

/* -------------------------------------------------------------------------- */
/* Approval queue                                                             */
/* -------------------------------------------------------------------------- */

function StepList({ workflow, reviewer, busy, onDecide }) {
  const steps = workflow.derived?.steps || []

  return (
    <ol className="mt-4 space-y-2">
      {steps.map((step) => (
        <li
          key={step.key}
          className={`rounded-lg border p-3 transition-colors duration-150 ${
            step.actionable
              ? 'border-accent/40 bg-accent/5'
              : 'border-border-subtle/25 bg-background/30'
          }`}
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs text-muted-foreground">#{step.order}</span>
                <span className="truncate text-sm font-medium text-foreground">{step.label}</span>
                <Badge tone={toneFor(step.status)}>{step.status}</Badge>
              </div>
              <p className="mt-1 truncate text-xs text-muted-foreground">
                {step.assigned_to
                  ? `Assigned to ${step.assigned_to}`
                  : step.approvers?.length
                    ? `Approvers: ${step.approvers.join(', ')}`
                    : 'Open step - any reviewer may decide'}
                {step.decided_by ? `, decided by ${step.decided_by}` : ''}
              </p>
            </div>

            {step.actionable && (
              <div className="flex gap-2">
                {/* The researched schema carries per-step approveButtonLabel and
                    rejectButtonLabel; the UI uses them verbatim. */}
                <Button
                  variant="primary"
                  disabled={busy}
                  onClick={() => onDecide(workflow, step, 'approve')}
                >
                  {step.approve_button_label}
                </Button>
                <Button
                  variant="danger"
                  disabled={busy}
                  onClick={() => onDecide(workflow, step, 'reject')}
                >
                  {step.reject_button_label}
                </Button>
              </div>
            )}
          </div>
          {step.comment && (
            <p className="mt-2 border-l-2 border-border-subtle/40 pl-3 text-xs text-muted-foreground">
              {step.comment}
            </p>
          )}
        </li>
      ))}
      {reviewer ? null : (
        <li className="text-xs text-amber-300">
          Set a reviewer name above before deciding a step.
        </li>
      )}
    </ol>
  )
}

function WorkflowCard({ workflow, reviewer, busy, onDecide, onPublish }) {
  const cleared = workflow.data.status === 'Approved'
  const content = workflow.data.content || []

  return (
    <li>
      <Card className="card-hover h-full">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="truncate font-mono text-base font-semibold">
              {workflow.data.approval_process?.name || 'Approval workflow'}
            </h3>
            <p className="mt-0.5 text-sm text-muted-foreground">
              {content.length} item{content.length === 1 ? '' : 's'}, submitted{' '}
              {relativeTime(workflow.data.submitted_at)}
            </p>
          </div>
          <Badge tone={toneFor(workflow.data.status)}>{workflow.data.status}</Badge>
        </div>

        <ul className="mt-3 space-y-1">
          {content.map((entry) => (
            <li key={entry.id} className="truncate text-[13px] text-foreground">
              {entry.title || entry.id}
            </li>
          ))}
        </ul>

        <StepList workflow={workflow} reviewer={reviewer} busy={busy} onDecide={onDecide} />

        {cleared && (
          <div className="mt-4 flex flex-wrap gap-2">
            <Button variant="primary" onClick={() => onPublish(content)}>
              <Icon path={ICONS.publish} />
              Publish {content.length} item{content.length === 1 ? '' : 's'}
            </Button>
            <span className="self-center text-xs text-muted-foreground">
              Cleared {relativeTime(workflow.data.decided_at)}
            </span>
          </div>
        )}
      </Card>
    </li>
  )
}

function ApprovalQueue({ reviewer, onChanged }) {
  const [filter, setFilter] = useState('Pending')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState([])

  const queue = useAsync(
    () => publishingApi.workflows({ status: filter === 'All' ? undefined : filter, limit: 50 }),
    [filter],
  )

  async function decide(workflow, step, decision) {
    setBusy(true)
    setError(null)
    try {
      await publishingApi.decide(workflow.id, step.key, decision, { actor: reviewer })
      queue.refetch()
      onChanged()
    } catch (failure) {
      setError(failure)
    } finally {
      setBusy(false)
    }
  }

  async function publishSelected() {
    if (selected.length === 0) return
    setBusy(true)
    setError(null)
    try {
      // A workflow that reached Approved is the evidence a publish needs, so the
      // clear content is submitted here rather than the workflow itself.
      const ids = [...new Set(selected.flatMap((id) => id))]
      await publishingApi.publish({ content: ids })
      setSelected([])
      queue.refetch()
      onChanged()
    } catch (failure) {
      setError(failure)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-mono text-lg font-semibold">Approval queue</h2>
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="wf009-filter" className="text-xs text-muted-foreground">
            Show
          </label>
          <select
            id="wf009-filter"
            className={`${inputClass} w-auto`}
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          >
            {['Pending', 'Approved', 'Rejected', 'All'].map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>
      </div>

      {selected.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-accent/40 bg-accent/10 p-3">
          <p className="text-sm text-foreground">
            {selected.length} cleared item{selected.length === 1 ? '' : 's'} selected
          </p>
          <Button variant="primary" disabled={busy} onClick={publishSelected}>
            <Icon path={ICONS.publish} />
            Publish selected
          </Button>
        </div>
      )}

      {error && <ErrorNote error={error} />}
      {queue.loading && <Spinner label="Loading approval queue" />}
      {queue.error && <ErrorNote error={queue.error} onRetry={queue.refetch} />}

      {!queue.loading && !queue.error && (queue.data?.entries || []).length === 0 && (
        <EmptyState
          title="Nothing in this queue"
          description="Submit a draft from the library below to start an approval workflow."
        />
      )}

      <ul className="grid gap-4 xl:grid-cols-2">
        {(queue.data?.entries || []).map((workflow) => (
          <WorkflowCard
            key={workflow.id}
            workflow={workflow}
            reviewer={reviewer}
            busy={busy}
            onDecide={decide}
            onPublish={(content) => setSelected(content.map((entry) => entry.id))}
          />
        ))}
      </ul>

      {queue.data && (
        <p className="text-xs text-muted-foreground">
          {queue.data.total_count} workflow{queue.data.total_count === 1 ? '' : 's'} total
          {queue.data.next_page ? ', more pages available' : ''}
        </p>
      )}
    </section>
  )
}

/* -------------------------------------------------------------------------- */
/* Library                                                                    */
/* -------------------------------------------------------------------------- */

function Library({ processes, onChanged }) {
  const documents = useAsync(() => publishingApi.documents(), [])
  const [submitting, setSubmitting] = useState({})
  const [error, setError] = useState(null)

  async function submit(document, processId) {
    if (!processId) {
      setError(new Error('Choose an approval process first'))
      return
    }
    setSubmitting((state) => ({ ...state, [document.id]: true }))
    setError(null)
    try {
      await publishingApi.submit([document.id], processId)
      documents.refetch()
      onChanged()
    } catch (failure) {
      setError(failure)
    } finally {
      setSubmitting((state) => ({ ...state, [document.id]: false }))
    }
  }

  const records = documents.data?.records || []

  return (
    <section className="space-y-4">
      <h2 className="font-mono text-lg font-semibold">Library</h2>

      {processes.length === 0 && (
        <p className="text-sm text-amber-300">
          No approval process is defined yet. Create one below before submitting content.
        </p>
      )}
      {error && <ErrorNote error={error} />}
      {documents.loading && <Spinner label="Loading library" />}
      {documents.error && <ErrorNote error={documents.error} onRetry={documents.refetch} />}

      {!documents.loading && records.length === 0 && (
        <EmptyState
          title="The library is empty"
          description="Add a document through the records API, then submit it here for approval."
        />
      )}

      {records.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead>
              <tr className="border-b border-border-subtle/30 text-xs tracking-wide text-muted-foreground uppercase">
                <th scope="col" className="py-2 pr-4 font-medium">Title</th>
                <th scope="col" className="py-2 pr-4 font-medium">Status</th>
                <th scope="col" className="py-2 pr-4 font-medium">Updated</th>
                <th scope="col" className="py-2 font-medium">Submit for approval</th>
              </tr>
            </thead>
            <tbody>
              {records.map((document) => (
                <tr key={document.id} className="border-b border-border-subtle/15 last:border-0">
                  <td className="py-2 pr-4 font-mono text-[13px] text-foreground">
                    {document.data.title || document.id}
                  </td>
                  <td className="py-2 pr-4">
                    <Badge tone={toneFor(document.data.status || 'Draft')}>
                      {document.data.status || 'Draft'}
                    </Badge>
                  </td>
                  <td className="py-2 pr-4 text-xs text-muted-foreground">
                    {relativeTime(document.updated_at)}
                  </td>
                  <td className="py-2">
                    {isPublished(document) ? (
                      <span className="text-xs text-muted-foreground">Already published</span>
                    ) : (
                      <div className="flex items-center gap-2">
                        <label htmlFor={`process-${document.id}`} className="sr-only">
                          Approval process for {document.data.title || document.id}
                        </label>
                        <select
                          id={`process-${document.id}`}
                          className={`${inputClass} w-auto`}
                          defaultValue=""
                          onChange={(event) => submit(document, event.target.value)}
                          disabled={submitting[document.id] || processes.length === 0}
                        >
                          <option value="">Choose process…</option>
                          {processes.map((process) => (
                            <option key={process.id} value={process.id}>
                              {process.data.name}
                            </option>
                          ))}
                        </select>
                        {submitting[document.id] && (
                          <span className="text-xs text-muted-foreground">Submitting…</span>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

/* -------------------------------------------------------------------------- */
/* Publish                                                                    */
/* -------------------------------------------------------------------------- */

function PublishPanel({ documents, onPublished }) {
  const [selected, setSelected] = useState([])
  const [mode, setMode] = useState('immediate')
  const [publishAt, setPublishAt] = useState('')
  const [notify, setNotify] = useState(true)
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  const drafts = useMemo(() => documents.filter((d) => !isPublished(d)), [documents])
  const limit = mode === 'scheduled' ? SCHEDULED_LIMIT : IMMEDIATE_LIMIT
  const overLimit = selected.length > limit

  function toggle(id) {
    setSelected((current) =>
      current.includes(id) ? current.filter((value) => value !== id) : [...current, id],
    )
  }

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const body = {
        content: selected,
        is_send_notification: notify,
        comment: comment || undefined,
      }
      if (mode === 'scheduled') body.publish_at = publishAt
      const response = await publishingApi.publish(body)
      setResult(response)
      setSelected([])
      onPublished()
    } catch (failure) {
      setError(failure)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <form onSubmit={submit} className="space-y-4">
        <div>
          <h2 className="font-mono text-lg font-semibold">Publish</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            A publish releases the latest version of each document. Destinations are
            decided by content metadata, not chosen here.
          </p>
        </div>

        <fieldset className="space-y-2">
          <legend className="text-xs font-medium text-muted-foreground">Content</legend>
          {drafts.length === 0 ? (
            <p className="text-sm text-muted-foreground">No unpublished content.</p>
          ) : (
            <ul className="grid gap-1 sm:grid-cols-2">
              {drafts.map((document) => (
                <li key={document.id}>
                  <label className="flex min-h-11 cursor-pointer items-center gap-2 rounded-lg px-2 text-sm text-foreground transition-colors duration-150 hover:bg-muted/50">
                    <input
                      type="checkbox"
                      className="h-4 w-4 accent-[var(--color-accent)]"
                      checked={selected.includes(document.id)}
                      onChange={() => toggle(document.id)}
                    />
                    <span className="truncate">{document.data.title || document.id}</span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </fieldset>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="When" id="wf009-mode" hint={`Up to ${limit} items in this mode.`}>
            <select
              id="wf009-mode"
              className={inputClass}
              value={mode}
              onChange={(event) => setMode(event.target.value)}
            >
              <option value="immediate">Publish immediately</option>
              <option value="scheduled">Schedule for later (UTC)</option>
            </select>
          </Field>

          {mode === 'scheduled' && (
            <Field label="Publish at" id="wf009-publish-at" hint="Interpreted as UTC.">
              <input
                id="wf009-publish-at"
                type="datetime-local"
                className={inputClass}
                required
                value={publishAt}
                onChange={(event) => setPublishAt(event.target.value)}
              />
            </Field>
          )}

          <Field label="Comment" id="wf009-comment" hint="Visible in content history.">
            <input
              id="wf009-comment"
              className={inputClass}
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              placeholder="Optional"
            />
          </Field>
        </div>

        <label className="flex min-h-11 cursor-pointer items-center gap-2 text-sm text-foreground">
          <input
            type="checkbox"
            className="h-4 w-4 accent-[var(--color-accent)]"
            checked={notify}
            onChange={(event) => setNotify(event.target.checked)}
          />
          Notify subscribers
        </label>

        {overLimit && (
          <p role="alert" className="text-sm text-destructive">
            {selected.length} items selected; this mode accepts at most {limit}.
          </p>
        )}
        {error && <ErrorNote error={error} />}

        <Button
          type="submit"
          variant="primary"
          disabled={busy || selected.length === 0 || overLimit}
        >
          <Icon path={mode === 'scheduled' ? ICONS.schedule : ICONS.publish} />
          {busy ? 'Working…' : mode === 'scheduled' ? 'Schedule publish' : 'Publish now'}
        </Button>
      </form>

      {result && <PublishResult result={result} />}
    </Card>
  )
}

/**
 * The partial-success envelope, shown in full.
 *
 * A batch can partly succeed, so hiding the failures behind a green toast would
 * be the one thing a reviewer most needs to be told about.
 */
function PublishResult({ result }) {
  const clean = result.total_errors === 0 && result.total_warnings === 0

  return (
    <div
      role="status"
      aria-live="polite"
      className={`mt-4 rounded-lg border p-4 ${
        clean ? 'border-accent/40 bg-accent/10' : 'border-amber-500/40 bg-amber-500/10'
      }`}
    >
      <p className="font-mono text-sm font-semibold text-foreground">
        {result.status === 'scheduled' ? 'Scheduled' : 'Published'} - {result.total_succeeded} of{' '}
        {result.total_requests} succeeded
      </p>
      {result.publish_at && (
        <p className="mt-1 text-xs text-muted-foreground">Publishes at {absoluteTime(result.publish_at)}</p>
      )}

      {result.errors?.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-medium tracking-wide text-destructive uppercase">
            Errors ({result.total_errors})
          </p>
          <ul className="mt-1 space-y-1">
            {result.errors.map((message) => (
              <li key={message} className="text-sm text-foreground">
                {message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {result.warnings?.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-medium tracking-wide text-amber-300 uppercase">
            Warnings ({result.total_warnings})
          </p>
          <ul className="mt-1 space-y-1">
            {result.warnings.map((message) => (
              <li key={message} className="text-sm text-foreground">
                {message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {result.results?.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Landed in
          </p>
          <ul className="mt-1 space-y-1">
            {result.results.map((entry) => (
              <li key={entry.id} className="truncate text-sm text-muted-foreground">
                {entry.id} in {entry.folders?.length ? entry.folders.join(', ') : 'no matching folder'}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Destinations and schedule                                                  */
/* -------------------------------------------------------------------------- */

function DynamicFolders({ folders }) {
  if (folders.length === 0) {
    return (
      <EmptyState
        title="No dynamic folders"
        description="A folder matches content by its own metadata and collects it automatically on publish."
      />
    )
  }

  return (
    <ul className="grid gap-3 sm:grid-cols-2">
      {folders.map((folder) => (
        <li key={folder.id}>
          <Card className="h-full">
            <div className="flex items-start gap-3">
              <span className="rounded-lg bg-muted p-2 text-accent">
                <Icon path={ICONS.folder} />
              </span>
              <div className="min-w-0">
                <h3 className="truncate font-mono text-sm font-semibold">{folder.data.name}</h3>
                <p className="truncate text-xs text-muted-foreground">
                  Profile: {folder.data.profile || 'default'}
                </p>
              </div>
            </div>
            <div className="mt-3">
              <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Matches
              </p>
              <ul className="mt-1 space-y-0.5">
                {Object.entries(folder.data.matches || {}).map(([path, value]) => (
                  <li key={path} className="truncate font-mono text-[13px] text-foreground">
                    {path} = {String(value)}
                  </li>
                ))}
              </ul>
            </div>
            <p className="mt-3 text-xs text-muted-foreground">
              {folder.data.content?.length || 0} item
              {folder.data.content?.length === 1 ? '' : 's'} collected
            </p>
          </Card>
        </li>
      ))}
    </ul>
  )
}

function Schedule({ publications, onSwept }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [swept, setSwept] = useState(null)

  const scheduled = publications.filter((entry) => entry.data.status === 'scheduled')

  async function runDue() {
    setBusy(true)
    setError(null)
    try {
      const response = await publishingApi.runDue()
      setSwept(response)
      onSwept()
    } catch (failure) {
      setError(failure)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-mono text-lg font-semibold">Scheduled publishes</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Nothing runs in the background here. A schedule fires when the due
            sweep is called.
          </p>
        </div>
        <Button icon="refresh" disabled={busy} onClick={runDue}>
          {busy ? 'Sweeping…' : 'Run due publications'}
        </Button>
      </div>

      {error && <ErrorNote error={error} />}
      {swept && (
        <p role="status" className="text-sm text-foreground">
          Swept at {absoluteTime(swept.ran_at)} - {swept.count} publication
          {swept.count === 1 ? '' : 's'} applied.
        </p>
      )}

      {scheduled.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing is scheduled.</p>
      ) : (
        <ul className="space-y-2">
          {scheduled.map((entry) => (
            <li
              key={entry.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border-subtle/25 p-3"
            >
              <div className="min-w-0">
                <p className="truncate text-sm text-foreground">
                  {entry.data.content?.length || 0} item
                  {entry.data.content?.length === 1 ? '' : 's'}
                </p>
                <p className="text-xs text-muted-foreground">
                  {absoluteTime(entry.data.publish_at)} (UTC)
                </p>
              </div>
              <Badge tone="update">scheduled</Badge>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/* -------------------------------------------------------------------------- */
/* Process definition                                                         */
/* -------------------------------------------------------------------------- */

function ProcessDefinition({ onCreated }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [stepsText, setStepsText] = useState('Legal review\nBrand review')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function create(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      // One approver name per line, comma-separated for several. Anything the
      // server does not recognise is still stored, so the shape stays open.
      const steps = stepsText
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean)
        .map((line) => {
          const [label, approversRaw] = line.split(',')
          const approvers = (approversRaw || '')
            .split(/[;\s]+/)
            .map((value) => value.trim())
            .filter(Boolean)
          return approvers.length ? { label, approvers } : { label }
        })
      await publishingApi.createProcess({ name, steps })
      setName('')
      setOpen(false)
      onCreated()
    } catch (failure) {
      setError(failure)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-mono text-lg font-semibold">Approval processes</h2>
        <Button icon={open ? 'close' : 'plus'} variant={open ? 'ghost' : 'secondary'} onClick={() => setOpen((v) => !v)}>
          {open ? 'Cancel' : 'New process'}
        </Button>
      </div>

      {open && (
        <Card>
          <form onSubmit={create} className="space-y-4">
            <Field label="Process name" id="wf009-process-name">
              <input
                id="wf009-process-name"
                className={inputClass}
                required
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </Field>
            <Field
              label="Steps, one per line, in order"
              id="wf009-process-steps"
              hint="Optional: Step label, approver1; approver2"
            >
              <textarea
                id="wf009-process-steps"
                className={`${inputClass} min-h-24 py-2`}
                value={stepsText}
                onChange={(event) => setStepsText(event.target.value)}
              />
            </Field>
            {error && <ErrorNote error={error} />}
            <Button type="submit" variant="primary" disabled={busy}>
              {busy ? 'Creating…' : 'Create process'}
            </Button>
          </form>
        </Card>
      )}
    </section>
  )
}

/* -------------------------------------------------------------------------- */
/* Page                                                                       */
/* -------------------------------------------------------------------------- */

export default function PublishingPage() {
  const [reviewer, setReviewer] = useState('')
  // One nonce refetches everything, so a decision in one panel cannot leave
  // another panel showing a count that has already changed.
  const [nonce, setNonce] = useState(0)
  const refresh = () => setNonce((n) => n + 1)

  const processes = useAsync(() => publishingApi.processes(), [nonce])
  const documents = useAsync(() => publishingApi.documents(), [nonce])
  const folders = useAsync(() => publishingApi.folders(), [nonce])
  const publications = useAsync(() => publishingApi.publications({ limit: 50 }), [nonce])

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-mono text-2xl font-semibold">Approval &amp; publishing</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Clear a draft through an ordered approval process, then publish it now or
          on a UTC schedule. Every decision and every publish is audited.
        </p>
      </header>

      <ReviewerBar reviewer={reviewer} onChange={setReviewer} />

      <ProcessDefinition onCreated={refresh} />

      <ApprovalQueue reviewer={reviewer} onChanged={refresh} />

      <Library
        processes={processes.data?.entries || []}
        onChanged={refresh}
      />

      <PublishPanel
        documents={documents.data?.records || []}
        onPublished={refresh}
      />

      <Schedule publications={publications.data?.entries || []} onSwept={refresh} />

      <section className="space-y-4">
        <h2 className="font-mono text-lg font-semibold">Dynamic folders</h2>
        {folders.loading && <Spinner label="Loading folders" />}
        {folders.error && <ErrorNote error={folders.error} onRetry={folders.refetch} />}
        {!folders.loading && !folders.error && <DynamicFolders folders={folders.data?.entries || []} />}
      </section>

      {publications.data?.entries?.length > 0 && (
        <section className="space-y-3">
          <h2 className="font-mono text-lg font-semibold">Publication history</h2>
          <details className="rounded-lg border border-border-subtle/25 p-3">
            <summary className="cursor-pointer text-sm text-foreground">
              Show the stored payload for {publications.data.entries.length} publication
              {publications.data.entries.length === 1 ? '' : 's'}
            </summary>
            <ul className="mt-3 space-y-3">
              {publications.data.entries.map((entry) => (
                <li key={entry.id} className="rounded-lg bg-background/40 p-3">
                  <JsonView value={entry.data} />
                </li>
              ))}
            </ul>
          </details>
        </section>
      )}
    </div>
  )
}
