import { useCallback, useState } from 'react'
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
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'
import { intentApi, payloadLabel, sendModeLabel, SKIP_REASONS, STATE_TONES } from './api'
import { CheckRow, Glyph, Notice } from './primitives'

/**
 * WF-032: stream identified company/contact intent to your own systems.
 *
 * The page follows the researched flow rather than the data model, because the
 * flow is what an operator actually does:
 *
 *  1. **Workflows -> New Workflow -> Webhooks**: one workflow type, published as
 *     data rather than chosen from a hard-coded list.
 *  2. **Name it and enter the destination URL**: a name and an absolute URL, and
 *     an http:// destination is accepted with a warning rather than refused,
 *     because the research names no scheme.
 *  3. **Conditions from saved Segments**, plus the researched once-or-updates
 *     choice. A workflow with no Segment is refused, so the picker here cannot
 *     submit one by accident.
 *  4. **Company only, or Company + Contacts**, with the keyword and
 *     required-field filters, and a **preview** of the exact bytes before any
 *     company visits.
 *  5. **The generated token**, shown once at creation and behind a reveal button,
 *     and masked everywhere else.
 *  6. **The delivery log**, including the rows where nothing was sent.
 *
 * Three researched rules are visible in the UI rather than only in the code,
 * because each changes what a reader should expect to see:
 *
 *  - **a skip is not a failure.** "Already sent", "paused" and "your Segment did
 *    not match" are three different answers, all written down, none shown in the
 *    failure colour.
 *  - **a filter that keeps nobody still sends the company.** The delivery shows
 *    `contactsConsidered` beside `contactsIncluded` so an empty `contacts` array
 *    reads as a filter result rather than as a broken workflow.
 *  - **the token is masked in every read path.** A list is a page somebody reads,
 *    and a live secret on a page is a leak waiting for a shoulder-surfer.
 *
 * A note on the shared primitives
 * ------------------------------
 * `components/ui.jsx` has no `Modal`, `Toggle` or `Checkbox` - the feature
 * contract lists them, but they are not in the file. Rather than edit a shared
 * file a hundred features would collide on, the forms here are inline and the
 * switches are the `CheckRow` in this folder. Both are reported in the PR
 * description as candidates for promotion into `ui.jsx`, which is platform work.
 *
 * This page animates nothing, has no scroll reveal and does not poll, so
 * `prefers-reduced-motion` is satisfied by there being nothing to reduce. That is
 * deliberate: an operations page that ticked every second would need the
 * reduced-motion branch, and a page that does not tick does not.
 */

const EMPTY_FILTER = { keywords: [], requiredFields: [] }

function csv(list) {
  return (list || []).join(', ')
}

function splitCsv(value) {
  return value
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean)
}

/* -------------------------------------------------------------------------
 * Summary
 * ---------------------------------------------------------------------- */

function Summary({ summary }) {
  const byState = summary.byState || {}
  const states = ['delivered', 'failed', 'skipped']
    .map((state) => `${byState[state] || 0} ${state}`)
    .join(', ')
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard
        label="Workflows live"
        value={summary.workflows}
        hint={`${summary.activeWorkflows} active, ${(summary.workflows || 0) - (summary.activeWorkflows || 0)} paused`}
        icon="database"
      />
      <StatCard
        label="Companies identified"
        value={summary.leads}
        hint={`${summary.segments} saved Segments`}
        icon="rooms"
      />
      <StatCard
        label="Visits recorded"
        value={summary.visits}
        hint={`${summary.visitsMatching} matched a workflow`}
        icon="audit"
      />
      <StatCard
        label="Decisions logged"
        value={summary.deliveries}
        hint={states || 'none yet'}
        icon="refresh"
      />
    </div>
  )
}

/* -------------------------------------------------------------------------
 * What the research fixes, and what this build decided
 * ---------------------------------------------------------------------- */

function Reference({ explain, inferences, destinations }) {
  const [open, setOpen] = useState(null)
  const sections = [
    {
      id: 'rules',
      label: 'What the research fixes',
      render: () => (
        <div className="mt-3 space-y-3">
          <p className="text-xs text-muted-foreground">
            Every claim below is a sentence from the vendor help centre, served by the API with its
            source rather than paraphrased here. That is what makes the page checkable.
          </p>
          <ol className="space-y-2">
            {(explain.flow || []).map((step) => (
              <li key={step.step} className="flex gap-3 text-xs">
                <span className="font-mono text-muted-foreground">{step.step}</span>
                <span className="min-w-0">
                  <span className="block text-foreground">{step.text}</span>
                  <span className="font-mono text-muted-foreground">{step.route}</span>
                </span>
              </li>
            ))}
          </ol>
          <ul className="space-y-2 border-t border-border-subtle/30 pt-3">
            {(explain.evidence || []).map((item) => (
              <li key={item.id} className="text-xs">
                <span className="block text-foreground italic">&ldquo;{item.quote}&rdquo;</span>
                <span className="font-mono text-muted-foreground">{item.source}</span>
              </li>
            ))}
          </ul>
        </div>
      ),
    },
    {
      id: 'inferences',
      label: 'Judgement calls this build made',
      render: () => (
        <div className="mt-3 space-y-3">
          <p className="text-xs text-muted-foreground">
            The research is explicit about the flow and silent about the edges. Each entry below is
            one of those edges, with what the research does say, what the alternative reading was,
            and how to change it. A reviewer can settle one by name rather than taking the build as
            a whole.
          </p>
          {(inferences.inferences || []).map((entry) => (
            <div key={entry.id} className="rounded-lg border border-border-subtle/30 p-3">
              <p className="font-mono text-xs text-foreground">{entry.id}</p>
              <p className="mt-1 text-sm text-foreground">{entry.decision}</p>
              <dl className="mt-2 grid gap-2 text-xs sm:grid-cols-2">
                <div>
                  <dt className="font-medium text-muted-foreground">What the research says</dt>
                  <dd className="text-muted-foreground">{entry.researched}</dd>
                </div>
                <div>
                  <dt className="font-medium text-muted-foreground">The other reading</dt>
                  <dd className="text-muted-foreground">{entry.alternative}</dd>
                </div>
                <div className="sm:col-span-2">
                  <dt className="font-medium text-muted-foreground">Why this one</dt>
                  <dd className="text-muted-foreground">{entry.why}</dd>
                </div>
                <div className="sm:col-span-2">
                  <dt className="font-medium text-muted-foreground">How to change it</dt>
                  <dd className="font-mono text-muted-foreground">{entry.change}</dd>
                </div>
              </dl>
            </div>
          ))}
        </div>
      ),
    },
    {
      id: 'destinations',
      label: 'Where the JSON can go',
      render: () => (
        <div className="mt-3 space-y-3">
          <p className="text-xs text-muted-foreground">{destinations.note}</p>
          <div>
            <p className="font-mono text-xs text-foreground">Webhook recipes</p>
            <ul className="mt-1 space-y-1">
              {(destinations.recipes || []).map((item) => (
                <li key={item.id} className="text-xs">
                  <span className="text-foreground">{item.label}</span>
                  <span className="block text-muted-foreground">{item.note}</span>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <p className="font-mono text-xs text-foreground">Other documented integration surfaces</p>
            <ul className="mt-1 flex flex-wrap gap-2">
              {(destinations.surfaces || []).map((item) => (
                <li key={item.id}>
                  <Badge>{item.label}</Badge>
                </li>
              ))}
            </ul>
            <p className="mt-1 text-xs text-muted-foreground">
              Listed by the research. No recipe was read for them, so none is claimed here.
            </p>
          </div>
        </div>
      ),
    },
  ]

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-2">
        {sections.map((section) => (
          <Button
            key={section.id}
            icon="schema"
            variant={open === section.id ? 'primary' : 'secondary'}
            onClick={() => setOpen(open === section.id ? null : section.id)}
          >
            {open === section.id ? 'Hide' : 'Show'} {section.label}
          </Button>
        ))}
      </div>
      {sections.map((section) =>
        open === section.id ? (
          <Card key={section.id}>{section.render()}</Card>
        ) : null,
      )}
    </div>
  )
}

/* -------------------------------------------------------------------------
 * Saved Segments
 * ---------------------------------------------------------------------- */

function Segments({ segments, leads, vocabulary, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState(null)
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [match, setMatch] = useState('any')
  const [path, setPath] = useState('employees')
  const [operator, setOperator] = useState('gte')
  const [value, setValue] = useState('1000')
  const [probe, setProbe] = useState({ segment: '', lead: '', verdict: null })

  const run = useCallback(
    async (label, action) => {
      setBusy(true)
      setNotice(null)
      try {
        const message = await action()
        if (message) setNotice({ tone: 'success', title: label, children: message })
        onChanged()
      } catch (error) {
        setNotice({ tone: 'danger', title: `${label} failed`, children: String(error.message || error) })
      } finally {
        setBusy(false)
      }
    },
    [onChanged],
  )

  const create = () =>
    run('Segment saved', async () => {
      const unary = operator === 'exists' || operator === 'not_exists'
      const numeric = ['gt', 'gte', 'lt', 'lte'].includes(operator)
      await intentApi.saveSegment(
        {
          name,
          description,
          match,
          rules: [
            unary
              ? { path, operator }
              : { path, operator, value: numeric ? Number(value) : value },
          ],
        },
        'operator',
      )
      setName('')
      setDescription('')
      setCreating(false)
      return 'A workflow can now name it in its conditions.'
    })

  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-mono text-sm font-semibold">Saved Segments</h2>
          <p className="text-xs text-muted-foreground">
            The researched conditions: &ldquo;which leads you want your Workflow to send, based on saved
            Segments from your account&rdquo;. A rule is a dotted path into the company&rsquo;s own
            data, so a field your team added yesterday can be selected today.
          </p>
        </div>
        <Button icon="plus" onClick={() => setCreating((open) => !open)}>
          {creating ? 'Cancel' : 'New Segment'}
        </Button>
      </div>

      {notice && <div className="mt-3"><Notice tone={notice.tone} title={notice.title} onDismiss={() => setNotice(null)}>{notice.children}</Notice></div>}

      {creating && (
        <div className="mt-4 space-y-3 rounded-lg border border-border-subtle/40 p-3">
          <Field label="Name" hint="A workflow refers to a Segment by this name.">
            <input className={inputClass} value={name} onChange={(event) => setName(event.target.value)} />
          </Field>
          <Field label="What it selects">
            <input
              className={inputClass}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="Software companies over 1000 employees, in market."
            />
          </Field>
          <Field label="Combine its rules with" hint="The Segment matches when any rule holds, or when all of them do.">
            <select className={inputClass} value={match} onChange={(event) => setMatch(event.target.value)}>
              <option value="any">any rule holds</option>
              <option value="all">every rule holds</option>
            </select>
          </Field>
          <div className="grid gap-3 sm:grid-cols-[2fr_1fr_2fr]">
            <Field label="Path" hint="Dotted, into the company's data.">
              <input className={inputClass} value={path} onChange={(event) => setPath(event.target.value)} />
            </Field>
            <Field label="Operator">
              <select className={inputClass} value={operator} onChange={(event) => setOperator(event.target.value)}>
                {(vocabulary.segmentOperators || []).map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Value" hint="Not used by exists / not_exists.">
              <input
                className={inputClass}
                value={value}
                onChange={(event) => setValue(event.target.value)}
                disabled={operator === 'exists' || operator === 'not_exists'}
              />
            </Field>
          </div>
          <div className="flex gap-2">
            <Button variant="primary" disabled={busy || !name.trim()} onClick={create}>
              Save Segment
            </Button>
            <Button onClick={() => setCreating(false)}>Cancel</Button>
          </div>
        </div>
      )}

      {segments.length === 0 ? (
        <div className="mt-4">
          <EmptyState
            title="No Segments yet"
            description="A workflow's conditions are a selection of saved Segments, so this is the first thing to make."
          />
        </div>
      ) : (
        <ul className="mt-4 space-y-2">
          {segments.map((segment) => (
            <li key={segment.id} className="rounded-lg border border-border-subtle/30 p-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="font-mono text-sm font-semibold">{segment.name}</p>
                  {segment.description && (
                    <p className="text-xs text-muted-foreground">{segment.description}</p>
                  )}
                  <p className="mt-1 font-mono text-xs text-accent">{segment.summary}</p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge>{segment.match} of {segment.ruleCount}</Badge>
                  <Button
                    icon="trash"
                    variant="danger"
                    disabled={busy}
                    onClick={() =>
                      run('Delete Segment', async () => {
                        await intentApi.removeSegment(segment.id, 'operator')
                        return 'Deleted.'
                      })
                    }
                  >
                    Delete
                  </Button>
                </div>
              </div>
              <details className="mt-2">
                <summary className="min-h-11 cursor-pointer py-2 text-xs text-muted-foreground">
                  Show the rules
                </summary>
                <JsonView value={segment.rules} />
              </details>
              <div className="mt-1 flex flex-wrap items-end gap-2">
                <div>
                  <label
                    htmlFor={`probe-lead-${segment.id}`}
                    className="block text-xs font-medium text-muted-foreground"
                  >
                    Test against
                  </label>
                  <select
                    id={`probe-lead-${segment.id}`}
                    className={`${inputClass} min-w-44`}
                    value={probe.segment === segment.id ? probe.lead : ''}
                    onChange={(event) => setProbe({ segment: segment.id, lead: event.target.value, verdict: null })}
                  >
                    <option value="">Choose a company…</option>
                    {leads.map((lead) => (
                      <option key={lead.id} value={lead.id}>
                        {lead.name}
                      </option>
                    ))}
                  </select>
                </div>
                <Button
                  disabled={busy || !(probe.segment === segment.id && probe.lead)}
                  onClick={() =>
                    run('Segment evaluated', async () => {
                      const verdict = await intentApi.evaluateSegment(segment.id, probe.lead)
                      setProbe((current) => ({ ...current, verdict }))
                      return null
                    })
                  }
                >
                  Why?
                </Button>
              </div>
              {probe.segment === segment.id && probe.verdict && (
                <div className="mt-2 rounded-lg border border-border-subtle/40 p-2">
                  <p className="text-xs">
                    <Badge tone={probe.verdict.matched ? 'insert' : 'neutral'}>
                      {probe.verdict.matched ? 'matches' : 'does not match'}
                    </Badge>{' '}
                    <span className="text-muted-foreground">{probe.verdict.reason}</span>
                  </p>
                  <p className="mt-1 font-mono text-xs text-muted-foreground">
                    Every rule, with the value the company actually had. This sends nothing.
                  </p>
                  <JsonView value={probe.verdict.rules} />
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

/* -------------------------------------------------------------------------
 * Workflows
 * ---------------------------------------------------------------------- */

function Workflows({ workflows, segments, vocabulary, onChanged, setNotice }) {
  const [busy, setBusy] = useState(false)
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [sendMode, setSendMode] = useState('once')
  const [payload, setPayload] = useState('company')
  const [segmentIds, setSegmentIds] = useState([])
  const [match, setMatch] = useState('any')
  const [keywords, setKeywords] = useState('')
  const [requiredFields, setRequiredFields] = useState('')
  const [revealed, setRevealed] = useState({})
  const [expanded, setExpanded] = useState(null)

  const run = useCallback(
    async (label, action) => {
      setBusy(true)
      setNotice(null)
      try {
        await action()
        onChanged()
      } catch (error) {
        setNotice({ tone: 'danger', title: `${label} failed`, children: String(error.message || error) })
      } finally {
        setBusy(false)
      }
    },
    [onChanged, setNotice],
  )

  const toggleSegment = (id, checked) =>
    setSegmentIds((current) => (checked ? [...current, id] : current.filter((item) => item !== id)))

  const create = () =>
    run('Workflow saved', async () => {
      const created = await intentApi.saveWorkflow(
        {
          name,
          url,
          sendMode,
          payload,
          conditions: { segmentIds, match },
          contactFilter:
            payload === 'company_contacts'
              ? { keywords: splitCsv(keywords), requiredFields: splitCsv(requiredFields) }
              : EMPTY_FILTER,
        },
        'operator',
      )
      setName('')
      setUrl('')
      setSegmentIds([])
      setKeywords('')
      setRequiredFields('')
      setCreating(false)
      setNotice({
        tone: 'success',
        title: `Created ${created.workflow.name}`,
        children: (
          <>
            <p>{created.tokenNote}</p>
            <p className="mt-2 font-mono text-sm break-all text-foreground">{created.token}</p>
            {(created.warnings || []).map((warning) => (
              <p key={warning} className="mt-2 text-amber-200">
                {warning}
              </p>
            ))}
          </>
        ),
      })
    })

  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-mono text-sm font-semibold">Webhook workflows</h2>
          <p className="text-xs text-muted-foreground">
            Workflows &rarr; New Workflow &rarr; Webhooks. One researched workflow type, a name, a
            destination URL, conditions from saved Segments, and the once-or-updates choice.
          </p>
        </div>
        <Button icon="plus" onClick={() => setCreating((open) => !open)} disabled={segments.length === 0}>
          {creating ? 'Cancel' : 'New workflow'}
        </Button>
      </div>
      {segments.length === 0 && (
        <p className="mt-2 text-xs text-amber-200">
          A workflow must name at least one Segment, so save one first. The research makes conditions
          a step rather than an option, and a workflow with no Segment would send every company in the
          account to a production endpoint.
        </p>
      )}

      {creating && (
        <div className="mt-4 space-y-3 rounded-lg border border-border-subtle/40 p-3">
          <Field label="Name" hint="Step 3: &lsquo;Add a name for your Workflow and the URL you want to send data to&rsquo;.">
            <input className={inputClass} value={name} onChange={(event) => setName(event.target.value)} />
          </Field>
          <Field
            label="Destination URL"
            hint="The POST target. It must be an absolute http or https URL with a host; a plain-http destination is accepted with a warning, because the research names no scheme."
          >
            <input
              className={inputClass}
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="https://hooks.example/intent"
            />
          </Field>

          <fieldset>
            <legend className="text-xs font-medium text-muted-foreground">
              Conditions: which leads this workflow sends
            </legend>
            <div className="mt-1 space-y-1">
              {segments.map((segment) => (
                <CheckRow
                  key={segment.id}
                  checked={segmentIds.includes(segment.id)}
                  onChange={(checked) => toggleSegment(segment.id, checked)}
                  label={segment.name}
                  hint={segment.summary}
                />
              ))}
            </div>
            {segmentIds.length > 1 && (
              <Field label="When several match" hint="A lead is sent when any of the selected Segments matches it, or only when all of them do.">
                <select className={inputClass} value={match} onChange={(event) => setMatch(event.target.value)}>
                  <option value="any">any of them matches</option>
                  <option value="all">all of them match</option>
                </select>
              </Field>
            )}
          </fieldset>

          <Field
            label="Send a lead"
            hint="&lsquo;Only send a lead once&rsquo; or &lsquo;send updates as well&rsquo;. With updates, the same lead is re-sent with refreshed activity data on a later visit."
          >
            <select className={inputClass} value={sendMode} onChange={(event) => setSendMode(event.target.value)}>
              {(vocabulary.sendModes || []).map((mode) => (
                <option key={mode} value={mode}>
                  {mode === 'once' ? 'only once' : 'and send updates as well'}
                </option>
              ))}
            </select>
          </Field>

          <Field
            label="Payload output"
            hint="&lsquo;Only Company&rsquo; or &lsquo;Company + Contacts&rsquo; for the company lead and the contacts employed at it."
          >
            <select
              className={inputClass}
              value={payload}
              onChange={(event) => setPayload(event.target.value)}
            >
              {(vocabulary.payloadModes || []).map((mode) => (
                <option key={mode} value={mode}>
                  {mode === 'company' ? 'Company only' : 'Company + Contacts'}
                </option>
              ))}
            </select>
          </Field>

          {payload === 'company_contacts' && (
            <div className="grid gap-3 sm:grid-cols-2">
              <Field
                label="Filter contacts on keywords"
                hint={`Optional, comma separated. A keyword is matched against ${(vocabulary.keywordFields || []).join(', ')}.`}
              >
                <input
                  className={inputClass}
                  value={keywords}
                  onChange={(event) => setKeywords(event.target.value)}
                  placeholder="engineering, security"
                />
              </Field>
              <Field
                label="Required fields"
                hint="Optional, comma separated. A contact without all of them is not sent. A filter that keeps everybody out still sends the company."
              >
                <input
                  className={inputClass}
                  value={requiredFields}
                  onChange={(event) => setRequiredFields(event.target.value)}
                  placeholder="email"
                />
              </Field>
            </div>
          )}

          <div className="flex gap-2">
            <Button
              variant="primary"
              disabled={busy || !name.trim() || !url.trim() || segmentIds.length === 0}
              onClick={create}
            >
              Save workflow
            </Button>
            <Button onClick={() => setCreating(false)}>Cancel</Button>
          </div>
        </div>
      )}

      {workflows.length === 0 ? (
        <div className="mt-4">
          <EmptyState
            title="No workflows yet"
            description="Nothing is being streamed anywhere until a workflow exists with a destination that can handle the POST."
          />
        </div>
      ) : (
        <ul className="mt-4 space-y-2">
          {workflows.map((workflow) => (
            <li key={workflow.id} className="rounded-lg border border-border-subtle/30 p-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="font-mono text-sm font-semibold">{workflow.name}</p>
                  <p className="font-mono text-xs break-all text-muted-foreground">{workflow.url}</p>
                  <div className="mt-1 flex flex-wrap gap-1">
                    <Badge tone={workflow.active ? 'insert' : 'neutral'}>
                      {workflow.active ? 'active' : 'paused'}
                    </Badge>
                    <Badge>{sendModeLabel(workflow)}</Badge>
                    <Badge>{payloadLabel(workflow)}</Badge>
                    {workflow.segmentCount > 0 && (
                      <Badge>{`${workflow.segmentCount} Segment(s)`}</Badge>
                    )}
                  </div>
                  {workflow.contactFilter && workflow.contactFilter.active && (
                    <p className="mt-1 font-mono text-xs text-muted-foreground">
                      keywords: {csv(workflow.contactFilter.keywords) || 'any'} &middot; required:{' '}
                      {csv(workflow.contactFilter.requiredFields) || 'any'}
                    </p>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    icon="audit"
                    disabled={busy}
                    onClick={() =>
                      run('Read token', async () => {
                        const shown = await intentApi.revealToken(workflow.id)
                        setRevealed((current) => ({ ...current, [workflow.id]: shown.token }))
                      })
                    }
                  >
                    {revealed[workflow.id] ? 'Token shown' : 'Show token'}
                  </Button>
                  <Button
                    disabled={busy}
                    onClick={() =>
                      run(workflow.active ? 'Pause' : 'Resume', async () => {
                        await intentApi.amendWorkflow(workflow.id, { active: !workflow.active }, 'operator')
                        return workflow.active
                          ? 'Paused. Visits will record a workflow_inactive skip rather than sending nothing at all.'
                          : 'Resumed.'
                      })
                    }
                  >
                    {workflow.active ? 'Pause' : 'Resume'}
                  </Button>
                  <Button
                    icon="trash"
                    variant="danger"
                    disabled={busy}
                    onClick={() =>
                      run('Delete workflow', async () => {
                        await intentApi.removeWorkflow(workflow.id, 'operator')
                        return 'Deleted. The delivery log it produced is kept.'
                      })
                    }
                  >
                    Delete
                  </Button>
                </div>
              </div>
              {revealed[workflow.id] && (
                <p className="mt-2 rounded border border-border-subtle/40 p-2 font-mono text-xs break-all">
                  {revealed[workflow.id]}
                </p>
              )}
              <div className="mt-1 flex gap-2">
                <Button onClick={() => setExpanded(expanded === workflow.id ? null : workflow.id)}>
                  {expanded === workflow.id ? 'Hide' : 'Show'} the setup
                </Button>
              </div>
              {expanded === workflow.id && (
                <div className="mt-2 space-y-1 font-mono text-xs text-muted-foreground">
                  <p>workflow type: {workflow.type}</p>
                  <p>token: {workflow.tokenHint} (masked; the destination receives it in full)</p>
                  <p>conditions: {workflow.conditions?.summary || '(none)'}</p>
                  <p>revision: {workflow.revision}</p>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

/* -------------------------------------------------------------------------
 * Companies, and the trigger
 * ---------------------------------------------------------------------- */

function Companies({ leads, visits, onChanged, setNotice }) {
  const [busy, setBusy] = useState(false)
  const [leadId, setLeadId] = useState('')
  const [pages, setPages] = useState('')

  const visit = async (label) => {
    setBusy(true)
    setNotice(null)
    try {
      const result = await intentApi.recordVisit(leadId, splitCsv(pages), 'operator')
      const sent = (result.deliveries || []).filter((row) => row.state === 'delivered').length
      const skipped = (result.deliveries || []).filter((row) => row.state === 'skipped').length
      const failed = (result.deliveries || []).filter((row) => row.state === 'failed').length
      setNotice({
        tone: failed ? 'warning' : 'success',
        title: `${label}: ${sent} sent, ${skipped} skipped, ${failed} failed`,
        children:
          'The trigger is the segment-matching company visit. Every workflow evaluated, and the ones that sent nothing are recorded with the reason.',
      })
      onChanged()
    } catch (error) {
      setNotice({ tone: 'danger', title: `${label} failed`, children: String(error.message || error) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <h2 className="font-mono text-sm font-semibold">Identified companies</h2>
        <p className="text-xs text-muted-foreground">
          The company leads a Segment is matched against, with the activity data a visit refreshes.
          The payload carries this data whole, so a field your team added is in the JSON without any
          change here.
        </p>
        {leads.length === 0 ? (
          <div className="mt-3">
            <EmptyState title="No companies yet" description="Nothing matches a Segment without a lead to match it against." />
          </div>
        ) : (
          <ul className="mt-3 space-y-2">
            {leads.map((lead) => (
              <li key={lead.id} className="rounded-lg border border-border-subtle/30 p-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="font-mono text-sm font-semibold">{lead.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {[lead.industry, lead.country, lead.employees && `${lead.employees} staff`]
                        .filter(Boolean)
                        .join(' · ')}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {lead.visitCount || 0} visit(s)
                      {lead.lastVisitAt && ` · last ${relativeTime(lead.lastVisitAt)}`}
                      {lead.pagesViewed?.length ? ` · ${csv(lead.pagesViewed)}` : ''}
                    </p>
                  </div>
                  <Badge tone={lead.visitCount ? 'update' : 'neutral'}>
                    {lead.visitCount ? 'visited' : 'never visited'}
                  </Badge>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <h2 className="font-mono text-sm font-semibold">Record a company visit</h2>
        <p className="text-xs text-muted-foreground">
          The researched automation: &ldquo;a company visit matches a saved Segment &rarr; the
          workflow&rsquo;s conditions are evaluated &rarr; a JSON payload is built &rarr; it is POSTed
          to your URL&rdquo;. No user action on the receiving side.
        </p>
        <div className="mt-3 space-y-3">
          <Field label="Company">
            <select className={inputClass} value={leadId} onChange={(event) => setLeadId(event.target.value)}>
              <option value="">Choose a company…</option>
              {leads.map((lead) => (
                <option key={lead.id} value={lead.id}>
                  {lead.name}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label="Pages seen on this visit"
            hint="Comma separated. Merged into the company's activity data, and the payload is built after that, so an update carries this visit."
          >
            <input
              className={inputClass}
              value={pages}
              onChange={(event) => setPages(event.target.value)}
              placeholder="Pricing One-Pager, Security & Compliance Pack"
            />
          </Field>
          <Button variant="primary" disabled={busy || !leadId} onClick={() => visit('Visit recorded')}>
            Record the visit
          </Button>
        </div>

        <h3 className="mt-5 font-mono text-xs text-muted-foreground">Recent visits</h3>
        {visits.length === 0 ? (
          <p className="mt-1 text-xs text-muted-foreground">
            No visits yet. A visit that matched nothing is recorded too — that is how a Segment gets
            debugged.
          </p>
        ) : (
          <ul className="mt-2 space-y-1">
            {visits.map((row) => (
              <li key={row.id} className="flex flex-wrap items-center justify-between gap-2 text-xs">
                <span className="font-mono text-muted-foreground">
                  {absoluteTime(row.at)} · {row.matchedWorkflows} matched
                </span>
                {row.matchedWorkflows ? (
                  <Badge tone="insert">matched</Badge>
                ) : (
                  <Badge>matched nothing</Badge>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

/* -------------------------------------------------------------------------
 * Deliveries
 * ---------------------------------------------------------------------- */

function Deliveries({ deliveries, onChanged, setNotice }) {
  const [busy, setBusy] = useState(false)
  const [state, setState] = useState('')
  const [expanded, setExpanded] = useState(null)
  const [detail, setDetail] = useState(null)

  const resend = async (delivery) => {
    setBusy(true)
    setNotice(null)
    try {
      await intentApi.resend(delivery.id, 'operator')
      setNotice({
        tone: 'success',
        title: 'Re-attempted',
        children: 'The attempt was appended to the delivery&rsquo;s own log, so both attempts stay readable.',
      })
      onChanged()
    } catch (error) {
      setNotice({ tone: 'danger', title: 'Re-attempt failed', children: String(error.message || error) })
    } finally {
      setBusy(false)
    }
  }

  const open = async (delivery) => {
    if (expanded === delivery.id) {
      setExpanded(null)
      setDetail(null)
      return
    }
    setExpanded(delivery.id)
    setDetail(null)
    setDetail(await intentApi.delivery(delivery.id))
  }

  const rows = state ? deliveries.filter((row) => row.state === state) : deliveries

  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-mono text-sm font-semibold">What was sent, and what was not</h2>
          <p className="text-xs text-muted-foreground">
            One row per decision. A <span className="text-foreground">skipped</span> row is this
            product deliberately not sending, with the reason; a{' '}
            <span className="text-foreground">failed</span> row is a destination that refused or
            could not be reached. Neither can be reconstructed from a row that was never written.
          </p>
        </div>
        <div className="flex gap-2">
          {['', 'delivered', 'failed', 'skipped'].map((option) => (
            <Button
              key={option || 'all'}
              variant={state === option ? 'primary' : 'secondary'}
              onClick={() => setState(option)}
            >
              {option || 'all'}
            </Button>
          ))}
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="mt-4">
          <EmptyState title="Nothing logged yet" description="Record a company visit and this fills in." />
        </div>
      ) : (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[46rem] text-left text-sm">
            <thead className="text-xs text-muted-foreground">
              <tr>
                <th className="py-2 pr-3 font-medium">When</th>
                <th className="py-2 pr-3 font-medium">Workflow</th>
                <th className="py-2 pr-3 font-medium">Outcome</th>
                <th className="py-2 pr-3 font-medium">Contacts</th>
                <th className="py-2 pr-3 font-medium">Send</th>
                <th className="py-2 font-medium">Action</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((delivery) => (
                <tr key={delivery.id} className="border-t border-border-subtle/25">
                  <td className="py-2 pr-3 font-mono text-xs whitespace-nowrap">
                    {relativeTime(delivery.at)}
                  </td>
                  <td className="py-2 pr-3">{delivery.workflowName || delivery.workflowId}</td>
                  <td className="py-2 pr-3">
                    <div className="flex flex-wrap items-center gap-1">
                      <Badge tone={STATE_TONES[delivery.state] || 'neutral'}>{delivery.state}</Badge>
                      {delivery.state === 'skipped' && (
                        <span className="text-xs text-muted-foreground">
                          {SKIP_REASONS[delivery.skipReason] || delivery.skipReason}
                        </span>
                      )}
                      {delivery.state === 'failed' && (
                        <span className="text-xs text-muted-foreground">
                          {delivery.status ? `HTTP ${delivery.status}` : 'unreachable'}
                          {delivery.retryable ? ' · a re-attempt could work' : ' · a re-attempt will not help'}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="py-2 pr-3 font-mono text-xs whitespace-nowrap">
                    {delivery.contactsConsidered == null
                      ? '—'
                      : `${delivery.contactsIncluded} of ${delivery.contactsConsidered}`}
                  </td>
                  <td className="py-2 pr-3 font-mono text-xs whitespace-nowrap">
                    {delivery.isUpdate ? `update ${delivery.updateCount}` : 'first send'}
                    {delivery.attempts > 1 ? ` · ${delivery.attempts} attempts` : ''}
                  </td>
                  <td className="py-2">
                    <div className="flex gap-1">
                      <Button onClick={() => open(delivery)}>
                        {expanded === delivery.id ? 'Close' : 'Detail'}
                      </Button>
                      {delivery.state === 'failed' && (
                        <Button variant="primary" disabled={busy} onClick={() => resend(delivery)}>
                          Re-attempt
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {expanded && detail && (
        <div className="mt-4 space-y-3 rounded-lg border border-border-subtle/40 p-3">
          <p className="font-mono text-xs text-muted-foreground">{detail.id}</p>
          <div>
            <p className="font-mono text-xs text-foreground">Why it decided that</p>
            <JsonView value={detail.conditions} />
          </div>
          <div>
            <p className="font-mono text-xs text-foreground">Attempts</p>
            <JsonView value={detail.attemptLog} />
          </div>
          {detail.contactFilter && (
            <div>
              <p className="font-mono text-xs text-foreground">Contact filter</p>
              <JsonView value={detail.contactFilter} />
            </div>
          )}
          {detail.payloadBody && (
            <div>
              <p className="font-mono text-xs text-foreground">
                The body that was sent ({detail.payloadBytes} bytes, token withheld)
              </p>
              <JsonView value={detail.payloadBody} />
            </div>
          )}
        </div>
      )}
    </Card>
  )
}

/* -------------------------------------------------------------------------
 * The page
 * ---------------------------------------------------------------------- */

export default function IntentStreamPage() {
  const [notice, setNotice] = useState(null)
  const [roomFilter, setRoomFilter] = useState('')

  const { data, loading, error, refetch } = useAsync(async () => {
    const rooms = await intentApi.rooms()
    const roomId = roomFilter
    const [summary, segments, workflows, leads, visits, deliveries, vocabulary, explain, inferences, destinations] =
      await Promise.all([
        roomId ? intentApi.roomSummary(roomId) : intentApi.summary(),
        intentApi.segments(),
        intentApi.workflows(),
        intentApi.leads({ room_id: roomId, limit: 200 }),
        intentApi.visits({ room_id: roomId, limit: 40 }),
        intentApi.deliveries({ room_id: roomId, limit: 60 }),
        intentApi.vocabulary(),
        intentApi.explain(),
        intentApi.inferences(),
        intentApi.destinations(),
      ])
    return {
      summary,
      segments: segments.segments,
      workflows: workflows.workflows,
      leads: leads.leads,
      visits: visits.visits,
      deliveries: deliveries.deliveries,
      vocabulary,
      explain,
      inferences,
      destinations,
      rooms: rooms.records || [],
    }
    // `roomFilter` is a dependency on purpose: choosing a room is a re-read, not
    // a client-side filter, because the room-scoped counts come from the server.
  }, [roomFilter])

  if (loading) return <Spinner label="Loading the intent stream" />
  if (error) return <ErrorNote error={error} onRetry={refetch} />

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="rounded-lg bg-accent/15 p-2 text-accent">
            <Glyph name="intent" size={20} />
          </span>
          <div>
            <h1 className="font-mono text-lg font-semibold">Intent stream</h1>
            <p className="text-xs text-muted-foreground">
              WF-032 &middot; identified company and contact intent POSTed to your own systems, with
              every decision recorded.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="wf032-room" className="text-xs text-muted-foreground">
            Room
          </label>
          <select
            id="wf032-room"
            className={`${inputClass} min-w-48`}
            value={roomFilter}
            onChange={(event) => setRoomFilter(event.target.value)}
          >
            <option value="">All rooms</option>
            {data.rooms.map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.data?.account || room.id}
              </option>
            ))}
          </select>
        </div>
      </header>

      {notice && (
        <Notice tone={notice.tone} title={notice.title} onDismiss={() => setNotice(null)}>
          {notice.children}
        </Notice>
      )}

      <Summary summary={data.summary} />

      <Reference
        explain={data.explain}
        inferences={data.inferences}
        destinations={data.destinations}
      />

      <Workflows
        workflows={data.workflows}
        segments={data.segments}
        vocabulary={data.vocabulary}
        onChanged={refetch}
        setNotice={setNotice}
      />

      <Segments
        segments={data.segments}
        leads={data.leads}
        vocabulary={data.vocabulary}
        onChanged={refetch}
      />

      <Companies leads={data.leads} visits={data.visits} onChanged={refetch} setNotice={setNotice} />

      <Deliveries deliveries={data.deliveries} onChanged={refetch} setNotice={setNotice} />

      <p className="text-xs text-muted-foreground">
        <Icon name="database" size={14} className="mr-1 inline" />
        Every write on this page goes through the audited store. The audit log names the route that
        served it, so a delivery can be traced back to the request that caused it.
      </p>
    </div>
  )
}
