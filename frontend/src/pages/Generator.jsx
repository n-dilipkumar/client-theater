/**
 * WF-012: generate a personalised room programmatically from a template.
 *
 * Three things happen here, in the order the workflow implies:
 *
 *   1. Pick a template. Its declared variables drive the form, and the blocks
 *      show the shape of what will be produced.
 *   2. Fill in substitution values and preview. The preview is the safety net:
 *      it shows the buyer-visible content, names the values that were left
 *      unresolved, and shows the expiry deadline the room would get.
 *   3. Generate. The room is a draft unless publication was explicitly asked
 *      for, and every write lands in the audit log the moment it is made.
 *
 * Design notes from design-system/digital-sales-room/MASTER.md: dense operator
 * surface, glass panels, green status accent, Fira Code for anything a developer
 * reads, 44px touch targets, visible focus, no emoji as icons, and motion that
 * respects prefers-reduced-motion (handled globally in index.css).
 */

import { useEffect, useMemo, useState } from 'react'
import { api, absoluteTime, relativeTime } from '../lib/api'
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
} from '../components/ui'

/** Derive an API reference key from a display label, mirroring the backend's
 *  rule: the editor shows "Hello World", the API takes `hello_world`. */
function referenceKey(label) {
  const cleaned = String(label || '')
    .trim()
    .toLowerCase()
    .replace(/[^0-9a-z]+/g, '_')
    .replace(/^_+|_+$/g, '')
  return cleaned || 'variable'
}

/** A repeat-typed variable has no fixed shape, so it is edited as JSON. The
 *  documented data shape is a list of objects. */
function isRepeat(variable) {
  return variable.type === 'repeat' || variable.type === 'array' || variable.type === 'list'
}

/** A repeating key is an open list of objects, so it is edited as JSON.
 *
 * The field owns its own text: it is seeded once and then left alone, because
 * re-deriving text from the parsed value on every keystroke reformats whatever
 * the operator is halfway through typing. Switching template remounts it (the
 * parent keys on the template id) rather than syncing it. */
function JsonField({ id, initial, onChange, rows = 5 }) {
  const [text, setText] = useState(initial)
  const [error, setError] = useState(null)

  function commit(next) {
    setText(next)
    if (!next.trim()) {
      setError(null)
      onChange(undefined)
      return
    }
    try {
      onChange(JSON.parse(next))
      setError(null)
    } catch {
      // Keep the text; report that nothing is being sent yet.
      setError('Not valid JSON yet, so this value is not sent.')
    }
  }

  return (
    <>
      <textarea
        id={id}
        className={`${inputClass} font-mono text-[13px]`}
        rows={rows}
        value={text}
        spellCheck={false}
        aria-describedby={error ? `${id}-error` : undefined}
        aria-invalid={error ? 'true' : undefined}
        onChange={(event) => commit(event.target.value)}
      />
      {error && (
        <p id={`${id}-error`} className="text-xs text-destructive">
          {error}
        </p>
      )}
    </>
  )
}

function SubstitutionField({ variable, value, onChange, index }) {
  const key = variable.key || referenceKey(variable.label)
  const id = `substitution-${index}-${key}`

  if (isRepeat(variable)) {
    return (
      <Field
        label={`${variable.label || key} (${key})`}
        id={id}
        hint="Repeating key: a JSON array of objects. Each element renders one line of the block."
      >
        <JsonField
          id={id}
          initial={JSON.stringify(
            [{ description: 'Line item', quantity: 1, unit_price: '0.00' }],
            null,
            2,
          )}
          onChange={onChange}
        />
      </Field>
    )
  }

  return (
    <Field label={variable.label || key} id={id} hint={`API reference key: ${key}`}>
      <input
        id={id}
        className={inputClass}
        value={value ?? ''}
        onChange={(event) => onChange(event.target.value)}
      />
    </Field>
  )
}

/** Renders the blocks the backend produced. `lines` is uniform for every
 *  block, so a repeating block needs no special case here. */
function ContentPreview({ content, unresolved }) {
  const flags = new Set(unresolved || [])
  if (!content || content.length === 0) {
    return <p className="text-sm text-muted-foreground">This template produced no content.</p>
  }

  return (
    <ol className="space-y-3">
      {content.map((block) => (
        <li key={block.id} className="rounded-lg border border-border-subtle/25 bg-background/40 p-3">
          <div className="mb-1.5 flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-foreground">{block.id}</span>
            <Badge>{block.kind}</Badge>
            {block.repeat && <Badge tone="update">repeats on {block.repeat}</Badge>}
          </div>
          <ul className="space-y-1">
            {(block.lines || []).map((line, position) => (
              <li
                key={position}
                className={`font-mono text-[13px] ${
                  line.includes('[unresolved:') ? 'text-amber-300' : 'text-foreground'
                }`}
              >
                {line || <span className="text-muted-foreground/60">(empty)</span>}
              </li>
            ))}
          </ul>
        </li>
      ))}
      {flags.size > 0 && (
        <li className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3">
          <span className="mt-0.5 text-amber-300">
            <Icon name="warning" size={16} />
          </span>
          <p className="text-xs text-amber-200">
            <span className="font-semibold">No value supplied for: </span>
            <span className="font-mono">{Array.from(flags).join(', ')}</span>
            <span className="mt-1 block text-amber-200/80">
              These render as a visible marker rather than blank, so a half-personalised room
              cannot reach a buyer by accident.
            </span>
          </p>
        </li>
      )}
    </ol>
  )
}

const STATUS_TONES = { draft: 'restore', published: 'insert', declined: 'delete' }

function GeneratedRoom({ room, onPublish, busy }) {
  const state = room.generation || {}
  const unresolved = room.data?.unresolved_variables || []

  return (
    <li>
      <Card className="card-hover h-full">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="truncate font-mono text-base font-semibold text-foreground">
              {room.data?.name || 'Untitled room'}
            </h3>
            <p className="mt-0.5 truncate text-sm text-muted-foreground">
              {room.data?.account || 'No account set'}
              {room.data?.external_id && (
                <span className="ml-2 font-mono text-xs text-accent">{room.data.external_id}</span>
              )}
            </p>
          </div>
          <Badge tone={STATUS_TONES[state.status] || 'neutral'}>{state.status || 'unknown'}</Badge>
        </div>

        <dl className="mt-4 grid grid-cols-2 gap-3 text-xs">
          <div>
            <dt className="text-muted-foreground">Template</dt>
            <dd className="truncate font-mono text-foreground" title={room.data?.template_name}>
              {room.data?.template_name || room.data?.template_id}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Generated</dt>
            <dd className="font-mono text-foreground">{relativeTime(room.created_at)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Published</dt>
            <dd className="font-mono text-foreground">
              {state.published_at ? relativeTime(state.published_at) : 'not published'}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Expires</dt>
            <dd className="font-mono text-foreground">
              {state.expires_at
                ? `${relativeTime(state.expires_at)} (${absoluteTime(state.expires_at)})`
                : state.expiry?.days
                  ? `${state.expiry.days}d on publish`
                  : 'never'}
            </dd>
          </div>
        </dl>

        {unresolved.length > 0 && (
          <p className="mt-3 flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 p-2 text-xs text-amber-200">
            <Icon name="warning" size={14} />
            <span>
              <span className="font-mono">{unresolved.join(', ')}</span> unresolved when this room was
              generated.
            </span>
          </p>
        )}

        <div className="mt-4 flex flex-wrap gap-2">
          {state.status === 'draft' && (
            <Button
              icon="send"
              variant="primary"
              disabled={busy}
              onClick={() => onPublish(room.id)}
            >
              Publish
            </Button>
          )}
          {room.data?.metadata && Object.keys(room.data.metadata).length > 0 && (
            <details className="w-full">
              <summary className="mt-1 cursor-pointer text-xs text-muted-foreground">
                Caller metadata (echoed verbatim)
              </summary>
              <div className="mt-1">
                <JsonView value={room.data.metadata} />
              </div>
            </details>
          )}
        </div>
      </Card>
    </li>
  )
}

export default function Generator() {
  const templates = useAsync(() => api.listTemplates(), [])
  const rooms = useAsync(() => api.listGenerations(), [])

  const [selectedId, setSelectedId] = useState('')
  const [form, setForm] = useState({
    name: '',
    account: '',
    owner_id: '',
    tags: '',
    external_id: '',
    expiryDays: '',
    publishNow: false,
  })
  const [values, setValues] = useState({})
  const [preview, setPreview] = useState(null)
  const [previewError, setPreviewError] = useState(null)
  const [previewing, setPreviewing] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [publishing, setPublishing] = useState('')
  const [actionError, setActionError] = useState(null)
  const [result, setResult] = useState(null)

  const records = templates.data?.records || []
  const selected = useMemo(
    () => records.find((record) => record.id === selectedId) || records[0] || null,
    [records, selectedId],
  )

  // A template change invalidates the values and the preview: keys from the
  // previous template mean nothing to the new one.
  useEffect(() => {
    setValues({})
    setPreview(null)
    setPreviewError(null)
  }, [selected?.id])

  const variables = useMemo(
    () => (selected?.data?.variables || []).filter((v) => v && typeof v === 'object'),
    [selected],
  )

  const request = useMemo(() => {
    if (!selected) return null
    const substitutions = {}
    for (const [key, value] of Object.entries(values)) {
      if (value !== undefined && value !== '') substitutions[key] = value
    }
    const payload = {
      template_id: selected.id,
      name: form.name.trim() || selected.data?.name || 'Generated room',
      substitutions,
    }
    if (form.account.trim()) payload.account = form.account.trim()
    if (form.owner_id.trim()) payload.owner_id = form.owner_id.trim()
    if (form.external_id.trim()) payload.external_id = form.external_id.trim()
    if (form.tags.trim()) payload.tags = form.tags.split(',').map((t) => t.trim()).filter(Boolean)
    if (form.expiryDays.trim()) {
      const days = Number(form.expiryDays)
      if (Number.isFinite(days)) payload.expiry = { days }
    }
    if (form.publishNow) payload.published = true
    return payload
  }, [selected, values, form])

  async function runPreview(event) {
    event.preventDefault()
    if (!request) return
    setPreviewing(true)
    setPreviewError(null)
    setResult(null)
    try {
      setPreview(await api.previewGeneration(request))
    } catch (error) {
      setPreview(null)
      setPreviewError(error)
    } finally {
      setPreviewing(false)
    }
  }

  async function runGenerate() {
    if (!request) return
    setGenerating(true)
    setActionError(null)
    setResult(null)
    try {
      const created = await api.generate(request)
      setResult(created)
      setPreview(null)
      rooms.refetch()
      templates.refetch()
    } catch (error) {
      setActionError(error)
    } finally {
      setGenerating(false)
    }
  }

  async function publish(roomId) {
    setPublishing(roomId)
    setActionError(null)
    try {
      await api.publishGeneration(roomId)
      rooms.refetch()
    } catch (error) {
      setActionError(error)
    } finally {
      setPublishing('')
    }
  }

  const canGenerate = Boolean(selected) && !generating
  const blockCount = selected?.data?.blocks?.length || 0

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-mono text-2xl font-semibold">Template generator</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Fill a template&apos;s variables to produce a personalised room. Publication is opt-in and
          every write is audited.
        </p>
      </header>

      {templates.loading && <Spinner label="Loading templates" />}
      {templates.error && <ErrorNote error={templates.error} onRetry={templates.refetch} />}

      {!templates.loading && !templates.error && records.length === 0 && (
        <EmptyState
          title="No templates yet"
          description="A template is a shell: a set of blocks carrying {{ variable }} references, plus the variables it expects. Declare one to start generating."
        />
      )}

      {records.length > 0 && (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
          {/* -- left: the request ------------------------------------- */}
          <div className="space-y-4">
            <Card>
              <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">Template</h2>
              <div className="mt-3">
                <Field
                  label="Template"
                  id="template-select"
                  hint={`${blockCount} block${blockCount === 1 ? '' : 's'}, ${variables.length} declared variable${variables.length === 1 ? '' : 's'}`}
                >
                  <select
                    id="template-select"
                    className={inputClass}
                    value={selected?.id || ''}
                    onChange={(event) => setSelectedId(event.target.value)}
                  >
                    {records.map((record) => (
                      <option key={record.id} value={record.id}>
                        {record.data?.name || record.id}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>

              {selected?.data?.description && (
                <p className="mt-3 text-sm text-muted-foreground">{selected.data.description}</p>
              )}

              {selected && (
                <details className="mt-3">
                  <summary className="cursor-pointer text-xs text-muted-foreground">
                    Blocks in this template
                  </summary>
                  <ul className="mt-2 space-y-1">
                    {(selected.data?.blocks || []).map((block, index) => (
                      <li key={block.id || index} className="font-mono text-xs text-muted-foreground">
                        <span className="text-foreground">{block.id}</span>{' '}
                        <Badge>{block.kind}</Badge>
                        {block.repeat && <span className="ml-1">repeats on {block.repeat}</span>}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </Card>

            <Card>
              <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">Room</h2>
              <form onSubmit={runPreview} className="mt-3 space-y-4">
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Room name" id="gen-name" hint="Shown to the buyer.">
                    <input
                      id="gen-name"
                      className={inputClass}
                      value={form.name}
                      onChange={(event) => setForm({ ...form, name: event.target.value })}
                    />
                  </Field>
                  <Field label="Account" id="gen-account">
                    <input
                      id="gen-account"
                      className={inputClass}
                      value={form.account}
                      onChange={(event) => setForm({ ...form, account: event.target.value })}
                    />
                  </Field>
                  <Field label="Owner" id="gen-owner" hint="Who is responsible for this room.">
                    <input
                      id="gen-owner"
                      className={inputClass}
                      value={form.owner_id}
                      onChange={(event) => setForm({ ...form, owner_id: event.target.value })}
                    />
                  </Field>
                  <Field
                    label="Tags"
                    id="gen-tags"
                    hint="Comma separated. Case-sensitive."
                  >
                    <input
                      id="gen-tags"
                      className={inputClass}
                      value={form.tags}
                      onChange={(event) => setForm({ ...form, tags: event.target.value })}
                    />
                  </Field>
                  <Field
                    label="External id"
                    id="gen-external"
                    hint="Your own reference, e.g. a CRM opportunity id. Must be unique."
                  >
                    <input
                      id="gen-external"
                      className={inputClass}
                      value={form.external_id}
                      onChange={(event) => setForm({ ...form, external_id: event.target.value })}
                    />
                  </Field>
                  <Field
                    label="Link expiry (days)"
                    id="gen-expiry"
                    hint="Counted from publication, not from creation."
                  >
                    <input
                      id="gen-expiry"
                      type="number"
                      min="1"
                      className={inputClass}
                      value={form.expiryDays}
                      onChange={(event) => setForm({ ...form, expiryDays: event.target.value })}
                    />
                  </Field>
                </div>

                <div className="flex items-center gap-3">
                  <label className="flex min-h-11 cursor-pointer items-center gap-2 text-sm text-foreground">
                    <input
                      type="checkbox"
                      className="h-4 w-4 accent-[var(--color-accent)]"
                      checked={form.publishNow}
                      onChange={(event) => setForm({ ...form, publishNow: event.target.checked })}
                    />
                    Publish immediately
                  </label>
                  <span className="text-xs text-muted-foreground">
                    {form.publishNow
                      ? 'This room goes live as soon as it is generated.'
                      : 'Left as a draft. The expiry setting is kept until you publish.'}
                  </span>
                </div>

                {variables.length > 0 && (
                  <div className="space-y-4 border-t border-border-subtle/25 pt-4">
                    <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                      Substitution values
                    </p>
                    {variables.map((variable, index) => {
                      const key = variable.key || referenceKey(variable.label)
                      return (
                        <SubstitutionField
                          // Keyed on the template too, so switching templates
                          // remounts the JSON fields with a clean slate rather
                          // than showing another template's values.
                          key={`${selected.id}-${key}`}
                          index={index}
                          variable={variable}
                          value={values[key]}
                          onChange={(next) => setValues({ ...values, [key]: next })}
                        />
                      )
                    })}
                    <p className="text-xs text-muted-foreground/80">
                      The declared list is advisory. Any other key the blocks reference still works,
                      and nothing is rejected for being undeclared.
                    </p>
                  </div>
                )}

                {previewError && <ErrorNote error={previewError} />}

                <div className="flex flex-wrap gap-2">
                  <Button type="submit" icon="eye" disabled={!selected || previewing}>
                    {previewing ? 'Rendering…' : 'Preview'}
                  </Button>
                  <Button
                    icon="generate"
                    variant="primary"
                    disabled={!canGenerate}
                    onClick={runGenerate}
                  >
                    {generating ? 'Generating…' : 'Generate room'}
                  </Button>
                </div>
              </form>
            </Card>
          </div>

          {/* -- right: what comes out ---------------------------------- */}
          <div className="space-y-4">
            <Card>
              <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">
                {result ? 'Generated' : 'Preview'}
              </h2>
              <p className="mt-1 text-xs text-muted-foreground">
                {result
                  ? 'One room, one audit row. It is also visible in the audit log and the rooms list.'
                  : 'Nothing is written until you generate. This is exactly what the buyer would see.'}
              </p>

              <div className="mt-3">
                {result ? (
                  <div className="space-y-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={STATUS_TONES[result.generation?.status] || 'neutral'}>
                        {result.generation?.status}
                      </Badge>
                      <span className="font-mono text-xs text-muted-foreground">{result.id}</span>
                    </div>
                    <ContentPreview
                      content={result.data?.content}
                      unresolved={result.data?.unresolved_variables}
                    />
                    {result.generation?.expires_at && (
                      <p className="flex items-center gap-2 text-xs text-muted-foreground">
                        <Icon name="clock" size={14} />
                        Link expires {absoluteTime(result.generation.expires_at)}
                      </p>
                    )}
                  </div>
                ) : preview ? (
                  <div className="space-y-3">
                    {preview.if_published_now?.expires_at && (
                      <p className="flex items-center gap-2 text-xs text-muted-foreground">
                        <Icon name="clock" size={14} />
                        If published now, the link would expire{' '}
                        {absoluteTime(preview.if_published_now.expires_at)}
                      </p>
                    )}
                    <ContentPreview
                      content={preview.content}
                      unresolved={preview.unresolved_variables}
                    />
                    {preview.problems?.length > 0 && (
                      <ul className="space-y-1">
                        {preview.problems.map((problem, index) => (
                          <li
                            key={index}
                            className="rounded-md border border-destructive/30 bg-destructive/10 p-2 text-xs text-foreground"
                          >
                            <span className="font-mono text-destructive">{problem.code}</span>{' '}
                            {problem.message}
                          </li>
                        ))}
                      </ul>
                    )}
                    {preview.unused_substitutions?.length > 0 && (
                      <p className="text-xs text-muted-foreground">
                        Supplied but never referenced by the template:{' '}
                        <span className="font-mono">{preview.unused_substitutions.join(', ')}</span>
                      </p>
                    )}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    Fill in some values and press Preview to see the rendered content.
                  </p>
                )}
              </div>
            </Card>

            {actionError && <ErrorNote error={actionError} />}

            <Card>
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">
                  Generated rooms
                </h2>
                <Button icon="refresh" onClick={rooms.refetch}>
                  Refresh
                </Button>
              </div>

              {rooms.loading ? (
                <Spinner label="Loading generated rooms" />
              ) : rooms.error ? (
                <ErrorNote error={rooms.error} onRetry={rooms.refetch} />
              ) : (rooms.data?.rooms || []).length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  Nothing generated yet. Every room you create from a template appears here.
                </p>
              ) : (
                <ul className="grid gap-4 md:grid-cols-2">
                  {rooms.data.rooms.map((room) => (
                    <GeneratedRoom
                      key={room.id}
                      room={room}
                      busy={publishing === room.id}
                      onPublish={publish}
                    />
                  ))}
                </ul>
              )}
            </Card>
          </div>
        </div>
      )}
    </div>
  )
}
