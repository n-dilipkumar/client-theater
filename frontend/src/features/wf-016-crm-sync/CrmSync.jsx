/**
 * CRM sync: room events out to a CRM (WF-016).
 *
 * Two paths, mirroring the researched workflow. Webhooks are the general
 * purpose seam: point a URL at an event and every matching room event is POSTed
 * to it. Automations are the no-code path: a When / Do this rule with a field
 * map, scoped to templates, toggled on from this library.
 *
 * The Activity Log is the third section and the one a rep actually reads: it is
 * the researched "did it succeed, did it error, and does it need manual
 * updating" surface, and every row expands to the exact payload that went out
 * plus every delivery attempt that produced it.
 *
 * The pickers are rendered from the vocabulary endpoint rather than from a list
 * compiled into this file, and a field map is free text on both sides, so a team
 * can add a CRM field without a change to this page.
 *
 * Ported from `frontend/src/pages/CrmSync.jsx` on
 * `feature/WF-016-sync-room-events-to-the-crm-via-webhooks`. What changed is
 * named in the repository report: the calls moved to this folder's own `api.js`,
 * the seven glyphs the branch added to the shared icon map are drawn from
 * `./icons.jsx` instead, and the three primitives the shared set cannot express
 * are in `./primitives.jsx`.
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
  JsonView,
  Spinner,
  inputClass,
  useAsync,
} from '@/components/ui'
import { crmApi } from './api'
import Glyph from './icons'
import { Note, StatTile, Toggle } from './primitives'

const SECTIONS = [
  { id: 'automations', label: 'Automations', icon: 'automation' },
  { id: 'webhooks', label: 'Webhooks', icon: 'webhook' },
  { id: 'activity', label: 'Activity log', icon: 'activity' },
  { id: 'inferences', label: 'What this infers', icon: 'warning' },
]

/** One inferred behaviour: what it is, what the research says, and how to change
 *  it. Kept in the product rather than in a code comment, because a judgement
 *  call nobody can find is a judgement call nobody can disagree with. */
function InferenceRow({ entry }) {
  const [open, setOpen] = useState(false)

  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full min-h-11 items-center gap-3 py-2 text-left transition-colors duration-150 hover:bg-muted/40"
      >
        <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-foreground">
          {entry.topic}
        </span>
        <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{entry.id}</span>
      </button>

      {open && (
        <div className="space-y-2 rounded-lg border border-border-subtle/25 bg-background/40 p-3 text-xs">
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">Why</p>
            <p className="mt-0.5 text-foreground/90">{entry.why}</p>
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">
              What the research says
            </p>
            <p className="mt-0.5 text-foreground/90">{entry.basis}</p>
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">
              What this build chose
            </p>
            <JsonView value={entry.value} />
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">
              How to change it
            </p>
            <p className="mt-0.5 font-mono text-foreground/90">{entry.change_it}</p>
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">Affects</p>
            <p className="mt-0.5 text-foreground/90">{entry.blast_radius}</p>
          </div>
        </div>
      )}
    </li>
  )
}

/** One delivery attempt, as the Activity Log recorded it. */
function AttemptRow({ attempt }) {
  return (
    <li className="flex flex-wrap items-baseline gap-2 text-xs">
      <span className="font-mono text-muted-foreground">#{attempt.attempt}</span>
      <Badge tone={attempt.ok ? 'insert' : 'delete'}>{attempt.status ?? 'no status'}</Badge>
      <span className="font-mono text-muted-foreground">
        {attempt.duration_ms}ms{attempt.retryable ? ' · retryable' : ''}
      </span>
      {attempt.error && <span className="font-mono text-destructive">{attempt.error}</span>}
      {attempt.body && (
        <span className="w-full truncate font-mono text-[11px] text-muted-foreground/80">
          {attempt.body}
        </span>
      )}
    </li>
  )
}

/** A single line in an activity row, expanding to the payload that was sent. */
function ActivityRow({ entry }) {
  const [open, setOpen] = useState(false)
  const data = entry.data
  const failed = data.status === 'error'
  const attempts = data.attempt_log || []

  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full min-h-11 items-center gap-3 py-2 text-left transition-colors duration-150 hover:bg-muted/40"
      >
        <Badge tone={failed ? 'delete' : 'insert'}>{data.status}</Badge>
        <Badge tone="neutral">{data.channel}</Badge>
        <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-foreground">
          {data.event}
        </span>
        <span className="hidden min-w-0 flex-1 truncate text-xs text-muted-foreground sm:block">
          {data.channel === 'webhook' ? data.target_url : data.automation_name}
        </span>
        {data.needs_manual_update && (
          <span className="flex items-center gap-1 text-xs text-amber-300">
            <Glyph name="warning" size={14} />
            needs manual update
          </span>
        )}
        <span
          className="shrink-0 font-mono text-xs text-muted-foreground"
          title={absoluteTime(entry.created_at)}
        >
          {relativeTime(entry.created_at)}
        </span>
      </button>

      {open && (
        <div className="space-y-3 rounded-lg border border-border-subtle/25 bg-background/40 p-3">
          <dl className="grid gap-2 text-xs sm:grid-cols-2">
            <div>
              <dt className="text-muted-foreground">Channel</dt>
              <dd className="font-mono text-foreground">{data.channel}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Event</dt>
              <dd className="font-mono text-foreground">{data.event}</dd>
            </div>
            {data.http_status !== undefined && data.http_status !== null && (
              <div>
                <dt className="text-muted-foreground">HTTP status</dt>
                <dd className="font-mono text-foreground">{data.http_status}</dd>
              </div>
            )}
            {data.attempts !== undefined && data.attempts !== null && (
              <div>
                <dt className="text-muted-foreground">Attempts</dt>
                <dd className="font-mono text-foreground">{data.attempts}</dd>
              </div>
            )}
          </dl>

          {data.error && (
            <p className="rounded-lg border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
              {data.error}
            </p>
          )}

          {attempts.length > 0 && (
            <div>
              <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Every attempt
              </p>
              {/* The whole retry history, not just the last status. A rate limit
                  that took three tries and the endpoint's own words are what
                  makes a failure diagnosable after the fact. */}
              <ul className="space-y-1.5">
                {attempts.map((attempt) => (
                  <AttemptRow key={attempt.attempt} attempt={attempt} />
                ))}
              </ul>
            </div>
          )}

          {data.warnings?.length > 0 && (
            <ul className="space-y-1">
              {data.warnings.map((warning, index) => (
                <li key={`${warning.code}-${index}`} className="flex gap-2 text-xs">
                  <Glyph name={warning.severity === 'info' ? 'schema' : 'warning'} size={14} />
                  <span
                    className={
                      warning.severity === 'info' ? 'text-muted-foreground' : 'text-amber-300'
                    }
                  >
                    {warning.message}
                  </span>
                </li>
              ))}
            </ul>
          )}

          {data.resolved && (
            <div>
              <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Resolved CRM payload
              </p>
              <JsonView value={data.resolved} />
            </div>
          )}

          {data.request && (
            <div>
              <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Request sent
              </p>
              <JsonView value={data.request} />
            </div>
          )}
        </div>
      )}
    </li>
  )
}

/** Recommended Automation card: add it with one click, then edit it. */
function PresetCard({ preset, onAdd, busy }) {
  return (
    <Card className="card-hover flex h-full flex-col">
      <h3 className="font-mono text-sm font-semibold text-foreground">{preset.name}</h3>
      <p className="mt-1 flex-1 text-sm text-muted-foreground">{preset.description}</p>
      <div className="mt-4">
        <Button icon="plus" variant="primary" disabled={busy} onClick={() => onAdd(preset)}>
          Add this automation
        </Button>
      </div>
    </Card>
  )
}

/** One When / Do this action row. Free text on both sides, by design. */
function ActionRow({ action, onChange, onRemove, canRemove, factPaths, index }) {
  const fields = action.fields || {}

  const setTarget = (oldTarget, newTarget) => {
    const next = {}
    for (const [key, value] of Object.entries(fields)) {
      next[key === oldTarget ? newTarget : key] = value
    }
    onChange({ ...action, fields: next })
  }

  const setSource = (target, source) => {
    onChange({ ...action, fields: { ...fields, [target]: source } })
  }

  const removeTarget = (target) => {
    const next = { ...fields }
    delete next[target]
    onChange({ ...action, fields: next })
  }

  const addTarget = () => {
    let target = 'crm_field'
    let suffix = 2
    while (target in fields) {
      target = `crm_field_${suffix}`
      suffix += 1
    }
    onChange({ ...action, fields: { ...fields, [target]: factPaths[0] } })
  }

  return (
    <fieldset className="space-y-2 rounded-lg border border-border-subtle/25 p-3">
      <legend className="px-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
        Do this {index + 1}
      </legend>

      <Field label="Action" id={`action-kind-${index}`}>
        <select
          id={`action-kind-${index}`}
          className={inputClass}
          value={action.kind}
          onChange={(event) => onChange({ ...action, kind: event.target.value })}
        >
          <option value="update_fields">Update CRM fields from room details</option>
        </select>
      </Field>

      <ul className="space-y-2">
        {Object.entries(fields).map(([target, source]) => (
          <li key={target} className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
            <Field label="CRM field" id={`target-${index}-${target}`}>
              <input
                id={`target-${index}-${target}`}
                className={inputClass}
                value={target}
                onChange={(event) => setTarget(target, event.target.value)}
              />
            </Field>
            <Field
              label="From room"
              id={`source-${index}-${target}`}
              hint="Any dotted path in the room payload."
            >
              <input
                id={`source-${index}-${target}`}
                className={inputClass}
                list={`fact-paths-${index}`}
                value={source}
                onChange={(event) => setSource(target, event.target.value)}
              />
            </Field>
            <div className="flex items-end">
              <Button icon="trash" variant="danger" onClick={() => removeTarget(target)}>
                <span className="sr-only">Remove {target}</span>
              </Button>
            </div>
          </li>
        ))}
      </ul>

      <datalist id={`fact-paths-${index}`}>
        {factPaths.map((path) => (
          <option key={path} value={path} />
        ))}
      </datalist>

      <div className="flex flex-wrap gap-2">
        <Button icon="plus" onClick={addTarget}>
          Add field
        </Button>
        {canRemove && (
          <Button icon="trash" variant="danger" onClick={onRemove}>
            Remove action
          </Button>
        )}
      </div>
    </fieldset>
  )
}

/** The automation editor: When / Do this, plus name, description and CRM.
 *  Creates a new rule, or patches the existing one when `initial.id` is set. */
function AutomationEditor({ vocabulary, initial, onSaved, onCancel }) {
  const editingExisting = Boolean(initial?.id)
  const [name, setName] = useState(initial?.name || '')
  const [description, setDescription] = useState(initial?.description || '')
  const [crmName, setCrmName] = useState(initial?.crm || 'salesforce')
  const [triggerEvent, setTriggerEvent] = useState(initial?.event || vocabulary.events[0])
  const [templateIds, setTemplateIds] = useState((initial?.template_ids || []).join(', '))
  const [actions, setActions] = useState(
    initial?.actions?.length ? initial.actions : [{ kind: 'update_fields', fields: {} }],
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const updateAction = (index, next) =>
    setActions(actions.map((action, position) => (position === index ? next : action)))

  async function save(submitEvent) {
    submitEvent.preventDefault()
    setSaving(true)
    setError(null)

    const spec = {
      name,
      description,
      crm: crmName,
      trigger: {
        event: triggerEvent,
        template_ids: templateIds
          .split(',')
          .map((value) => value.trim())
          .filter(Boolean),
      },
      actions,
    }

    try {
      if (editingExisting) {
        await crmApi.updateAutomation(initial.id, spec)
      } else {
        await crmApi.createAutomation(spec)
      }
      onSaved()
    } catch (failure) {
      setError(failure)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <form onSubmit={save} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Name" id="automation-name" hint="Shown in the automations library.">
            <input
              id="automation-name"
              className={inputClass}
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </Field>
          <Field label="CRM" id="automation-crm">
            <select
              id="automation-crm"
              className={inputClass}
              value={crmName}
              onChange={(e) => setCrmName(e.target.value)}
            >
              {(vocabulary.crms || ['salesforce']).map((crmOption) => (
                <option key={crmOption} value={crmOption}>
                  {crmOption}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <Field label="Description" id="automation-description">
          <input
            id="automation-description"
            className={inputClass}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="When" id="automation-event" hint="The room event that fires this rule.">
            <select
              id="automation-event"
              className={inputClass}
              value={triggerEvent}
              onChange={(e) => setTriggerEvent(e.target.value)}
            >
              {vocabulary.events.map((eventName) => (
                <option key={eventName} value={eventName}>
                  {eventName}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label="Templates"
            id="automation-templates"
            hint="Comma separated. Empty applies to every room."
          >
            <input
              id="automation-templates"
              className={inputClass}
              value={templateIds}
              onChange={(e) => setTemplateIds(e.target.value)}
            />
          </Field>
        </div>

        <div className="space-y-3">
          {actions.map((action, index) => (
            <ActionRow
              key={index}
              index={index}
              action={action}
              factPaths={vocabulary.fact_paths || []}
              canRemove={actions.length > 1}
              onChange={(next) => updateAction(index, next)}
              onRemove={() => setActions(actions.filter((_, position) => position !== index))}
            />
          ))}
          <Button
            icon="plus"
            onClick={() => setActions([...actions, { kind: 'update_fields', fields: {} }])}
          >
            Add another action
          </Button>
        </div>

        {error && <ErrorNote error={error} />}

        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" disabled={saving}>
            {saving ? 'Saving…' : editingExisting ? 'Save changes' : 'Create automation'}
          </Button>
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </form>
    </Card>
  )
}

/** An automation row: When / Do this at a glance, the toggle, and its warnings. */
function AutomationCard({ automation, onToggle, onDelete, onEdit, busy }) {
  const warnings = automation.warnings || []
  const blocking = warnings.filter((warning) => warning.severity === 'warning')

  return (
    <Card className="card-hover h-full">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate font-mono text-base font-semibold text-foreground">
            {automation.name}
          </h3>
          {automation.description && (
            <p className="mt-0.5 text-sm text-muted-foreground">{automation.description}</p>
          )}
        </div>
        <Toggle
          checked={automation.enabled}
          disabled={busy}
          onChange={() => onToggle(automation)}
          label={`${automation.enabled ? 'Turn off' : 'Turn on'} ${automation.name}`}
        />
      </div>

      <dl className="mt-4 space-y-1.5 text-xs">
        <div className="flex gap-2">
          <dt className="shrink-0 text-muted-foreground">When</dt>
          <dd className="font-mono text-accent">{automation.event}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="shrink-0 text-muted-foreground">Do this</dt>
          <dd className="min-w-0 font-mono text-foreground">
            {automation.actions.length} action{automation.actions.length === 1 ? '' : 's'} on{' '}
            {automation.crm}
          </dd>
        </div>
        <div className="flex gap-2">
          <dt className="shrink-0 text-muted-foreground">Templates</dt>
          <dd className="min-w-0 font-mono text-foreground">
            {automation.template_ids.length ? automation.template_ids.join(', ') : 'all rooms'}
          </dd>
        </div>
        <div className="flex gap-2">
          <dt className="shrink-0 text-muted-foreground">Runs</dt>
          <dd className="font-mono text-foreground">
            {automation.runs}
            {automation.errors > 0 && (
              <span className="text-destructive"> ({automation.errors} errored)</span>
            )}
          </dd>
        </div>
      </dl>

      {blocking.length > 0 && (
        <ul className="mt-3 space-y-1.5 border-t border-border-subtle/25 pt-3">
          {blocking.map((warning, index) => (
            <li key={`${warning.code}-${index}`} className="flex gap-2 text-xs text-amber-300">
              <Glyph name="warning" size={14} />
              <span>{warning.message}</span>
            </li>
          ))}
        </ul>
      )}

      {!automation.enabled && (
        <p className="mt-3 flex items-center gap-2 border-t border-border-subtle/25 pt-3 text-xs text-muted-foreground">
          <Glyph name="power" size={14} />
          Off: not running, and not assigned to its templates.
        </p>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        <Button onClick={() => onEdit(automation)} disabled={busy}>
          Edit
        </Button>
        <Button icon="trash" variant="danger" onClick={onDelete} disabled={busy}>
          Delete
        </Button>
      </div>
    </Card>
  )
}

export default function CrmSync() {
  const [section, setSection] = useState('automations')
  const [editing, setEditing] = useState(null)
  const [busyId, setBusyId] = useState(null)
  const [channel, setChannel] = useState('')
  const [status, setStatus] = useState('')
  const [notice, setNotice] = useState(null)
  const [noticeError, setNoticeError] = useState(null)

  // -- subscription form state
  const [subEvent, setSubEvent] = useState('pageAccepted')
  const [targetUrl, setTargetUrl] = useState('')
  const [secret, setSecret] = useState('')
  const [subscribing, setSubscribing] = useState(false)
  const [subError, setSubError] = useState(null)

  const vocabulary = useAsync(() => crmApi.vocabulary(), [])
  const presets = useAsync(() => crmApi.presets(), [])
  const automations = useAsync(() => crmApi.listAutomations(), [])
  const subscriptions = useAsync(() => crmApi.listSubscriptions(), [])
  const activity = useAsync(() => crmApi.activity({ channel, status, limit: 60 }), [channel, status])
  const inferences = useAsync(() => crmApi.inferences(), [])

  const events = vocabulary.data?.events || []

  // Every mutating action goes through here so the busy flag, the success
  // notice, and the error path are handled the same way each time.
  async function guard(busyKey, successMessage, work) {
    setBusyId(busyKey)
    setNoticeError(null)
    setNotice(null)
    try {
      await work()
      setNotice(successMessage)
    } catch (error) {
      setNoticeError(error)
    } finally {
      setBusyId(null)
    }
  }

  // -- automations
  async function addPreset(preset) {
    await guard('preset', `Added "${preset.name}".`, async () => {
      await crmApi.createAutomation({ preset_id: preset.id })
      automations.refetch()
    })
  }

  async function toggleAutomation(automation) {
    const next = !automation.enabled
    await guard(
      automation.id,
      `"${automation.name}" is now ${next ? 'on' : 'off'}.`,
      async () => {
        await crmApi.updateAutomation(automation.id, { enabled: next })
        automations.refetch()
      },
    )
  }

  async function deleteAutomation(automation) {
    await guard(automation.id, `Deleted "${automation.name}".`, async () => {
      await crmApi.deleteAutomation(automation.id)
      automations.refetch()
    })
  }

  // -- webhooks
  async function subscribe(submitEvent) {
    submitEvent.preventDefault()
    setSubscribing(true)
    setSubError(null)
    try {
      await crmApi.subscribe({ event: subEvent, target_url: targetUrl, secret: secret || undefined })
      setTargetUrl('')
      setSecret('')
      subscriptions.refetch()
    } catch (error) {
      setSubError(error)
    } finally {
      setSubscribing(false)
    }
  }

  async function unsubscribe(subscription) {
    await guard(subscription.id, 'Subscription cancelled.', async () => {
      await crmApi.unsubscribe(subscription.id)
      subscriptions.refetch()
    })
  }

  const summary = activity.data?.summary
  // Memoised so the `stats` below keeps a stable dependency. A fresh `[]` each
// render made every dep change on every render, so the memo never held.
  const automationRows = useMemo(() => automations.data?.automations || [], [automations.data])
  const subscriptionRows = useMemo(
    () => subscriptions.data?.subscriptions || [],
    [subscriptions.data],
  )

  const stats = useMemo(
    () => [
      {
        label: 'Automations',
        value: automationRows.length,
        path: 'automation',
        hint: `${automationRows.filter((a) => a.enabled).length} on`,
      },
      {
        label: 'Webhooks',
        value: subscriptionRows.length,
        path: 'webhook',
        hint: `${subscriptionRows.filter((s) => s.active).length} active`,
      },
      {
        label: 'Delivered',
        value: summary?.success ?? 0,
        path: 'check',
        hint: 'last 60 activity rows',
      },
      {
        label: 'Needs manual update',
        value: summary?.needs_manual_update ?? 0,
        path: 'warning',
        hint: 'could not be fixed by retrying',
      },
    ],
    [automationRows, subscriptionRows, summary],
  )

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-mono text-2xl font-semibold">CRM sync</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Push room events to a CRM over webhooks, or describe the write as an automation. Both
          paths are audited, and both report into the activity log.
        </p>
      </header>

      {noticeError && <ErrorNote error={noticeError} />}
      {notice && <Note>{notice}</Note>}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {stats.map((stat) => (
          <StatTile key={stat.label} {...stat} />
        ))}
      </div>

      {/* Section switcher. A tablist so arrow-key semantics and the aria
          relationship are right, and so the choice is shareable by URL. */}
      <div role="tablist" aria-label="CRM sync sections" className="flex flex-wrap gap-2">
        {SECTIONS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            id={`tab-${item.id}`}
            aria-selected={section === item.id}
            aria-controls={`panel-${item.id}`}
            onClick={() => setSection(item.id)}
            className={`inline-flex min-h-11 items-center gap-2 rounded-lg px-4 text-sm
              transition-colors duration-200 ${
                section === item.id
                  ? 'bg-accent/15 font-medium text-accent'
                  : 'bg-muted text-muted-foreground hover:border-border-subtle hover:text-foreground'
              }`}
          >
            <Glyph name={item.icon} />
            {item.label}
          </button>
        ))}
      </div>

      {/* -- automations -- */}
      {section === 'automations' && (
        <div
          id="panel-automations"
          role="tabpanel"
          aria-labelledby="tab-automations"
          className="space-y-5"
        >
          <section aria-labelledby="recommended-heading" className="space-y-3">
            <div className="flex items-end justify-between gap-3">
              <div>
                <h2 id="recommended-heading" className="font-mono text-lg font-semibold">
                  Recommended automations
                </h2>
                <p className="text-sm text-muted-foreground">
                  A starting point you add once and then edit.
                </p>
              </div>
              <Button
                icon="plus"
                variant={editing ? 'ghost' : 'primary'}
                onClick={() => setEditing(editing ? null : {})}
              >
                {editing ? 'Cancel' : 'Build your own'}
              </Button>
            </div>

            {presets.loading && <Spinner label="Loading presets" />}
            {presets.error && <ErrorNote error={presets.error} onRetry={presets.refetch} />}
            <ul className="grid gap-4 md:grid-cols-3">
              {(presets.data?.presets || []).map((preset) => (
                <li key={preset.id}>
                  <PresetCard preset={preset} onAdd={addPreset} busy={busyId === 'preset'} />
                </li>
              ))}
            </ul>
          </section>

          {editing && vocabulary.data && (
            <AutomationEditor
              vocabulary={vocabulary.data}
              initial={editing}
              onSaved={() => {
                setEditing(null)
                setNotice('Automation saved.')
                automations.refetch()
              }}
              onCancel={() => setEditing(null)}
            />
          )}

          <section aria-labelledby="library-heading" className="space-y-3">
            <h2 id="library-heading" className="font-mono text-lg font-semibold">
              Automations library
            </h2>

            {automations.loading && <Spinner label="Loading automations" />}
            {automations.error && (
              <ErrorNote error={automations.error} onRetry={automations.refetch} />
            )}

            {!automations.loading && !automations.error && automationRows.length === 0 && (
              <EmptyState
                title="No automations yet"
                description="Add one of the recommended automations above, or build your own."
                action={
                  <Button icon="plus" variant="primary" onClick={() => setEditing({})}>
                    Build an automation
                  </Button>
                }
              />
            )}

            <ul className="grid gap-4 lg:grid-cols-2">
              {automationRows.map((automation) => (
                <li key={automation.id}>
                  <AutomationCard
                    automation={automation}
                    busy={busyId === automation.id}
                    onToggle={toggleAutomation}
                    onDelete={deleteAutomation}
                    onEdit={(target) => {
                      setNotice(null)
                      setEditing(target)
                    }}
                  />
                </li>
              ))}
            </ul>
          </section>
        </div>
      )}

      {/* -- webhooks -- */}
      {section === 'webhooks' && (
        <div
          id="panel-webhooks"
          role="tabpanel"
          aria-labelledby="tab-webhooks"
          className="space-y-5"
        >
          <Card>
            <form onSubmit={subscribe} className="space-y-4">
              <div>
                <h2 className="font-mono text-lg font-semibold">Subscribe a URL</h2>
                <p className="text-sm text-muted-foreground">
                  Every matching room event is POSTed to this URL with the room&rsquo;s metadata
                  attached. Keep the id to cancel later.
                </p>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Event" id="subscription-event">
                  <select
                    id="subscription-event"
                    className={inputClass}
                    value={subEvent}
                    onChange={(event) => setSubEvent(event.target.value)}
                  >
                    {events.map((name) => (
                      <option key={name} value={name}>
                        {name}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Target URL" id="subscription-target" hint="Absolute http or https URL.">
                  <input
                    id="subscription-target"
                    className={inputClass}
                    type="url"
                    required
                    placeholder="https://crm.example/hooks/dsr"
                    value={targetUrl}
                    onChange={(event) => setTargetUrl(event.target.value)}
                  />
                </Field>
              </div>

              <Field
                label="Signing secret"
                id="subscription-secret"
                hint="Optional. When set, the payload is signed and the signature travels in X-DSR-Signature."
              >
                <input
                  id="subscription-secret"
                  className={inputClass}
                  type="password"
                  autoComplete="new-password"
                  value={secret}
                  onChange={(event) => setSecret(event.target.value)}
                />
              </Field>

              {subError && <ErrorNote error={subError} />}

              <Button type="submit" variant="primary" disabled={subscribing}>
                {subscribing ? 'Subscribing…' : 'Subscribe'}
              </Button>
            </form>
          </Card>

          <section aria-labelledby="subscriptions-heading" className="space-y-3">
            <h2 id="subscriptions-heading" className="font-mono text-lg font-semibold">
              Subscriptions
            </h2>

            {subscriptions.loading && <Spinner label="Loading subscriptions" />}
            {subscriptions.error && (
              <ErrorNote error={subscriptions.error} onRetry={subscriptions.refetch} />
            )}

            {!subscriptions.loading && !subscriptions.error && subscriptionRows.length === 0 && (
              <EmptyState
                title="No webhook subscriptions"
                description="Subscribe a URL above to push room events into your CRM."
              />
            )}

            {subscriptionRows.length > 0 && (
              <div className="overflow-x-auto">
                <Card>
                  <table className="w-full min-w-[720px] text-left text-sm">
                    <caption className="sr-only">
                      Webhook subscriptions, their delivery counters, and how to cancel them
                    </caption>
                    <thead>
                      <tr className="border-b border-border-subtle/30 text-xs tracking-wide text-muted-foreground uppercase">
                        <th scope="col" className="px-4 py-3 font-medium">
                          Event
                        </th>
                        <th scope="col" className="px-4 py-3 font-medium">
                          Target URL
                        </th>
                        <th scope="col" className="px-4 py-3 font-medium">
                          Sent
                        </th>
                        <th scope="col" className="px-4 py-3 font-medium">
                          Failed
                        </th>
                        <th scope="col" className="px-4 py-3 font-medium">
                          Last
                        </th>
                        <th scope="col" className="px-4 py-3 font-medium">
                          <span className="sr-only">Actions</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {subscriptionRows.map((subscription) => (
                        <tr
                          key={subscription.id}
                          className="border-b border-border-subtle/15 last:border-0"
                        >
                          <td className="px-4 py-3">
                            <Badge tone="insert">{subscription.event}</Badge>
                          </td>
                          <td className="px-4 py-3">
                            <span className="flex items-center gap-2 font-mono text-[13px] text-foreground">
                              <Glyph name="link" size={14} />
                              <span className="max-w-[280px] truncate">
                                {subscription.target_url}
                              </span>
                            </span>
                            <span className="mt-0.5 block font-mono text-[11px] text-muted-foreground">
                              {subscription.signed ? 'signed' : 'unsigned'} · {subscription.id}
                            </span>
                          </td>
                          <td className="px-4 py-3 font-mono text-foreground">
                            {subscription.deliveries}
                          </td>
                          <td
                            className={`px-4 py-3 font-mono ${
                              subscription.failures > 0
                                ? 'text-destructive'
                                : 'text-muted-foreground'
                            }`}
                          >
                            {subscription.failures}
                          </td>
                          <td className="px-4 py-3">
                            {subscription.last_status ? (
                              <Badge tone={subscription.last_status === 'success' ? 'insert' : 'delete'}>
                                {subscription.last_status}
                              </Badge>
                            ) : (
                              <span className="text-muted-foreground">never</span>
                            )}
                          </td>
                          <td className="px-4 py-3 text-right">
                            <Button
                              icon="trash"
                              variant="danger"
                              disabled={busyId === subscription.id}
                              onClick={() => unsubscribe(subscription)}
                            >
                              Cancel
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Card>
              </div>
            )}
          </section>
        </div>
      )}

      {/* -- activity -- */}
      {section === 'activity' && (
        <div
          id="panel-activity"
          role="tabpanel"
          aria-labelledby="tab-activity"
          className="space-y-4"
        >
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="font-mono text-lg font-semibold">Activity log</h2>
              <p className="text-sm text-muted-foreground">
                Success versus errored, and which rows need a human. Select a row to see the exact
                payload that was sent and every attempt made to send it.
              </p>
            </div>
            <div className="flex flex-wrap gap-3">
              <Field label="Channel" id="filter-channel">
                <select
                  id="filter-channel"
                  className={inputClass}
                  value={channel}
                  onChange={(event) => setChannel(event.target.value)}
                >
                  <option value="">All</option>
                  <option value="webhook">Webhook</option>
                  <option value="automation">Automation</option>
                </select>
              </Field>
              <Field label="Status" id="filter-status">
                <select
                  id="filter-status"
                  className={inputClass}
                  value={status}
                  onChange={(event) => setStatus(event.target.value)}
                >
                  <option value="">All</option>
                  <option value="success">Success</option>
                  <option value="error">Errored</option>
                </select>
              </Field>
              <Button icon="refresh" onClick={activity.refetch}>
                Refresh
              </Button>
            </div>
          </div>

          {activity.loading && <Spinner label="Loading activity" />}
          {activity.error && <ErrorNote error={activity.error} onRetry={activity.refetch} />}

          {!activity.loading && !activity.error && (activity.data?.entries || []).length === 0 && (
            <EmptyState
              title="No activity yet"
              description="Record a room event, or let a real one arrive, and every delivery attempt lands here."
            />
          )}

          {(activity.data?.entries || []).length > 0 && (
            <Card className="p-4">
              <ul>
                {(activity.data?.entries || []).map((entry) => (
                  <ActivityRow key={entry.id} entry={entry} />
                ))}
              </ul>
            </Card>
          )}
        </div>
      )}

      {/* -- what this infers -- */}
      {section === 'inferences' && (
        <div
          id="panel-inferences"
          role="tabpanel"
          aria-labelledby="tab-inferences"
          className="space-y-4"
        >
          <div>
            <h2 className="font-mono text-lg font-semibold">What this infers</h2>
            <p className="text-sm text-muted-foreground">
              The published event enum, the five page statuses, and the subscribe, list and cancel
              contract are all sourced. The rest is judgement: the parts of this workflow the
              research makes no claims about. Each one is listed here with the reason for the
              choice and how to change it.
            </p>
          </div>

          {inferences.loading && <Spinner label="Loading inferences" />}
          {inferences.error && <ErrorNote error={inferences.error} onRetry={inferences.refetch} />}

          {inferences.data && (
            <>
              <Card className="p-4">
                <p className="text-xs leading-relaxed text-muted-foreground">
                  <span className="font-medium text-foreground">From the research: </span>
                  &ldquo;{inferences.data.sourced_quote}&rdquo;
                </p>
              </Card>

              <Card className="p-4">
                <ul>
                  {(inferences.data.inferences || []).map((entry) => (
                    <InferenceRow key={entry.id} entry={entry} />
                  ))}
                </ul>
              </Card>
            </>
          )}
        </div>
      )}
    </div>
  )
}
