import { useMemo, useState } from 'react'

import { Button, Card, EmptyState, ErrorNote, Field, JsonView, Spinner, StatCard, inputClass, useAsync } from '@/components/ui'

import { canReview, canSubmit, endpointRows, marketplaceApi, parseSamplePayload } from './api'
import { EndpointRow, Select, StateBadge, Textarea } from './primitives'

const DEFAULT_PAYLOAD = '{\n  "contact": { "name": "Dana" },\n  "page": { "url": "/pricing" }\n}'

const KIND_OPTIONS = [
  { value: 'trigger', label: 'Trigger' },
  { value: 'action', label: 'Action' },
]

const STATE_FILTER_OPTIONS = [
  { value: '', label: 'Every state' },
  { value: 'draft', label: 'Draft' },
  { value: 'submitted', label: 'Submitted' },
  { value: 'in_review', label: 'In review' },
  { value: 'changes_requested', label: 'Changes requested' },
  { value: 'approved', label: 'Approved' },
  { value: 'published', label: 'Published' },
  { value: 'withdrawn', label: 'Withdrawn' },
  { value: 'rejected', label: 'Rejected' },
]

function CreateForm({ onCreated }) {
  const [kind, setKind] = useState('trigger')
  const [key, setKey] = useState('')
  const [name, setName] = useState('')
  const [summaryText, setSummaryText] = useState('')
  const [payload, setPayload] = useState(DEFAULT_PAYLOAD)
  const [subscriptionUrl, setSubscriptionUrl] = useState('https://apps.example.com/subscribe')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const parsed = parseSamplePayload(payload)

  async function submit(event) {
    event.preventDefault()
    setError(null)
    if (!key.trim() || !name.trim()) {
      setError('A key and a name are required.')
      return
    }
    if (!parsed.ok) {
      setError(parsed.error)
      return
    }
    const body = {
      kind,
      key: key.trim(),
      name: name.trim(),
      sample_payload: parsed.value,
    }
    if (summaryText.trim()) body.summary = summaryText.trim()
    if (kind === 'trigger') body.subscription_url = subscriptionUrl.trim()
    if (kind === 'action') body.execution = { method: 'url', url: subscriptionUrl.trim() }
    setBusy(true)
    try {
      await marketplaceApi.create(body)
      setKey('')
      setName('')
      setSummaryText('')
      onCreated()
    } catch (problem) {
      setError(problem.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">Author a module</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        A trigger or an action. The key is immutable once the module is saved.
      </p>
      <form className="mt-4 grid gap-4 sm:grid-cols-2" onSubmit={submit}>
        <Select id="wf115-kind" label="Kind" value={kind} onChange={setKind} options={KIND_OPTIONS} />
        <Field label="Key" hint="lowercase, starts with a letter" id="wf115-key">
          <input
            id="wf115-key"
            className={inputClass}
            value={key}
            onChange={(event) => setKey(event.target.value)}
            placeholder="mycustomtrigger"
          />
        </Field>
        <Field label="Name" id="wf115-name">
          <input
            id="wf115-name"
            className={inputClass}
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Northwind page viewed"
          />
        </Field>
        <Field label="Summary" id="wf115-summary">
          <input
            id="wf115-summary"
            className={inputClass}
            value={summaryText}
            onChange={(event) => setSummaryText(event.target.value)}
            placeholder="What the module does"
          />
        </Field>
        <div className="sm:col-span-2">
          <Textarea
            id="wf115-payload"
            label="Sample JSON payload"
            hint="Powers filters and custom variables. Must be a JSON object."
            value={payload}
            onChange={setPayload}
          />
        </div>
        <Field
          label={kind === 'trigger' ? 'Subscription URL' : 'Execution URL'}
          hint="Recorded as configuration. This room never calls it."
          id="wf115-url"
        >
          <input
            id="wf115-url"
            className={inputClass}
            value={subscriptionUrl}
            onChange={(event) => setSubscriptionUrl(event.target.value)}
          />
        </Field>
        <div className="flex items-end">
          <Button type="submit" variant="primary" disabled={busy || !parsed.ok}>
            {busy ? 'Saving…' : 'Save draft'}
          </Button>
        </div>
      </form>
      {error && <p className="mt-3 text-sm text-destructive">{error}</p>}
      <p className="mt-2 text-xs text-muted-foreground">
        Payload check: {parsed.ok ? 'valid JSON object' : parsed.error}
      </p>
    </Card>
  )
}

function ModuleActions({ module, onChanged }) {
  const [changelog, setChangelog] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function run(action) {
    setBusy(true)
    setError(null)
    try {
      await action()
      onChanged()
    } catch (problem) {
      setError(problem.message)
    } finally {
      setBusy(false)
    }
  }

  const actions = module.available_actions || {}

  return (
    <div className="mt-4 space-y-4">
      {canSubmit(module) && (
        <Field label="Changelog" hint="Required to submit for review." id="wf115-changelog">
          <input
            id="wf115-changelog"
            className={inputClass}
            value={changelog}
            onChange={(event) => setChangelog(event.target.value)}
          />
        </Field>
      )}
      {canReview(module) && (
        <Field label="Review note" hint="A note is required to request changes or reject." id="wf115-note">
          <input
            id="wf115-note"
            className={inputClass}
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
        </Field>
      )}
      <div className="flex flex-wrap gap-2">
        {canSubmit(module) && (
          <Button
            variant="primary"
            disabled={busy || !changelog.trim()}
            onClick={() => run(() => marketplaceApi.submit(module.id, changelog.trim()))}
          >
            Submit for review
          </Button>
        )}
        {canReview(module) && (
          <>
            <Button disabled={busy} onClick={() => run(() => marketplaceApi.review(module.id, 'approve', note))}>
              Approve
            </Button>
            <Button
              disabled={busy || !note.trim()}
              onClick={() => run(() => marketplaceApi.review(module.id, 'request_changes', note))}
            >
              Request changes
            </Button>
          </>
        )}
        {actions.publish && (
          <Button disabled={busy} onClick={() => run(() => marketplaceApi.publish(module.id))}>
            Publish
          </Button>
        )}
        {actions.withdraw && (
          <Button disabled={busy} onClick={() => run(() => marketplaceApi.withdraw(module.id))}>
            Withdraw
          </Button>
        )}
        {actions.deliver && (
          <Button
            disabled={busy}
            onClick={() =>
              run(() =>
                marketplaceApi.deliver(module.id, {
                  event_type: 'CREATED',
                  target_url: 'https://services.leadconnectorhq.com/workflows-marketplace/triggers/execute/abc/def',
                  filters: [],
                  extras: {},
                }),
              )
            }
          >
            Record a delivery
          </Button>
        )}
        {actions.restore && (
          <Button disabled={busy} onClick={() => run(() => marketplaceApi.restore(module.id))}>
            Restore
          </Button>
        )}
      </div>
      {actions.delete && (
        <div>
          <p className="text-xs text-muted-foreground">
            Deleting is a soft delete. A workflow that references this key is told the trigger was
            deleted.
          </p>
          <Button
            className="mt-2"
            variant="danger"
            disabled={busy}
            onClick={() => run(() => marketplaceApi.remove(module.id))}
          >
            Delete module
          </Button>
        </div>
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  )
}

export default function MarketplacePage() {
  const [kind, setKind] = useState('')
  const [state, setState] = useState('')
  const [selectedId, setSelectedId] = useState(null)

  const loader = useMemo(
    () => async () => {
      const [summary, listed, vocab] = await Promise.all([
        marketplaceApi.summary(),
        marketplaceApi.modules({ kind, review_state: state }),
        marketplaceApi.vocabulary(),
      ])
      return { summary, modules: listed.modules, vocabulary: vocab }
    },
    [kind, state],
  )

  const { data, loading, error, refetch } = useAsync(loader, [kind, state])

  function changed() {
    setSelectedId(null)
    refetch()
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-semibold text-foreground">Marketplace primitives</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Publish custom workflow triggers and actions to an app marketplace. Author a module,
          submit it for review, and publish an approved version to every sub-account.
        </p>
      </header>

      {loading && <Spinner label="Loading marketplace modules" />}
      {error && <ErrorNote error={error} onRetry={refetch} />}

      {data && (
        <>
          <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="Modules" value={data.summary.total} icon="schema" />
            <StatCard label="Published" value={data.summary.published} icon="database" />
            <StatCard label="Awaiting review" value={data.summary.awaiting_review} icon="audit" />
            <StatCard label="Deleted" value={data.summary.deleted} icon="trash" />
          </section>

          <CreateForm onCreated={changed} />

          <section>
            <div className="grid gap-4 sm:grid-cols-2">
              <Select
                id="wf115-filter-kind"
                label="Filter by kind"
                value={kind}
                onChange={setKind}
                options={[{ value: '', label: 'Every kind' }, ...KIND_OPTIONS]}
              />
              <Select
                id="wf115-filter-state"
                label="Filter by review state"
                value={state}
                onChange={setState}
                options={STATE_FILTER_OPTIONS}
              />
            </div>

            <h2 className="mt-6 font-display text-lg font-semibold text-foreground">Modules</h2>
            {data.modules.length === 0 ? (
              <div className="mt-3">
                <EmptyState
                  title="No modules yet"
                  description="Author a trigger or an action above to see it here."
                />
              </div>
            ) : (
              <ul className="mt-3 space-y-3">
                {data.modules.map((module) => (
                  <li key={module.id}>
                    <Card>
                      <button
                        type="button"
                        className="w-full text-left"
                        aria-pressed={module.id === selectedId}
                        onClick={() => setSelectedId(module.id === selectedId ? null : module.id)}
                      >
                        <div className="flex flex-wrap items-center justify-between gap-3">
                          <div className="min-w-0">
                            <p className="font-display text-base font-semibold text-foreground">
                              {module.name}
                            </p>
                            <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                              {module.key} · {module.kind} · v{module.version}
                            </p>
                          </div>
                          <StateBadge state={module.review_state} />
                        </div>
                      </button>

                      {module.issues && module.issues.length > 0 && (
                        <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-destructive">
                          {module.issues.map((issue) => (
                            <li key={issue}>{issue}</li>
                          ))}
                        </ul>
                      )}
                      {module.warnings && module.warnings.length > 0 && (
                        <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
                          {module.warnings.map((warning) => (
                            <li key={warning}>{warning}</li>
                          ))}
                        </ul>
                      )}

                      {module.id === selectedId && (
                        <div className="mt-4 border-t border-border-subtle pt-4">
                          <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
                            Allowed next states
                          </p>
                          <p className="mt-1 font-mono text-[13px] text-foreground">
                            {(module.targets || []).join(', ') || 'none'}
                          </p>

                          {endpointRows(module).length > 0 && (
                            <div className="mt-4 grid gap-2">
                              {endpointRows(module).map((row) => (
                                <EndpointRow key={row.label} {...row} />
                              ))}
                            </div>
                          )}

                          {module.sample_payload && (
                            <div className="mt-4 rounded-sm border border-border-subtle bg-surface p-3">
                              <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
                                Sample payload
                              </p>
                              <div className="mt-1">
                                <JsonView value={module.sample_payload} />
                              </div>
                            </div>
                          )}

                          <ModuleActions module={module} onChanged={changed} />
                        </div>
                      )}
                    </Card>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  )
}
