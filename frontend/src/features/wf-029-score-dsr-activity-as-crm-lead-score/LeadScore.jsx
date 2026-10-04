/**
 * Lead score: DSR activity as CRM lead-score criteria (WF-029).
 *
 * The page follows the researched flow in order, because the flow is the
 * specification:
 *
 *   1. Confirm the Dock and HubSpot integration is enabled and that the workspace is
 *      connected to a deal/account.
 *   2. Settings, Contact property, search for HubSpot score.
 *   3. Click Add criteria for either positive or negative scores.
 *   4. Scroll to the Dock options. Choose the property to score against.
 *   5. Set filters and assign score.
 *   6. Save; subsequent buyer activity in the DSR moves the contact's score.
 *
 * Two facts the page states rather than assumes. First, the score is **recomputed**
 * from a contact's whole activity history on every matching event, so a withdrawn
 * criterion takes its points off at that contact's next event and a replayed webhook
 * moves nothing. Second, nothing is sent to the CRM: every run records the requests
 * the researched endpoints would have received, marked `not executed`, because this
 * build holds no token for either researched scope.
 *
 * Nothing here is compiled from a list: the two buckets, the five Dock activity
 * properties, the filters each publishes and the signs all come from `/vocabulary`,
 * so the editor and the server's validator cannot disagree about what is legal.
 */

import { useMemo, useState } from 'react'
import {
  Badge,
  Button,
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
import {
  bucketMeaning,
  familyLabel,
  leadScoreApi,
  refinementsFor,
  refinementLabel,
  signedScore,
} from './api'
import {
  ActionabilityNote,
  ContributionRow,
  CriterionRunRow,
  FindingRow,
  LEAD_SCORE_ICON,
  LintRow,
  PlanRow,
  ScorePill,
} from './primitives'

/** The buckets the editor offers, in the order the research names them. */
const BUCKET_CHOICES = ['positive', 'negative']

/** The filter the research recommends as a baseline, offered on every property. */
const OCCURRED = 'occurred'

function LeadScore() {
  const vocabulary = useAsync(() => leadScoreApi.vocabulary(), [])
  const inferences = useAsync(() => leadScoreApi.inferences(), [])
  const rooms = useAsync(() => leadScoreApi.rooms(), [])
  const integrations = useAsync(() => leadScoreApi.integrations(), [])

  const [chosenRoom, setChosenRoom] = useState('')
  const roomOptions = rooms.data?.records || []
  // Derived rather than written from an effect. The first room is the default, and an
  // effect that sets state on arrival costs a second render plus a lint warning for a
  // value that is already computable from the list.
  const roomId = chosenRoom || (roomOptions.length > 0 ? roomOptions[0].id : '')

  const summary = useAsync(
    () => (roomId ? leadScoreApi.summary(roomId) : Promise.resolve(null)),
    [roomId],
  )
  // Every panel loads once per room and is refetched by the action that changed it.
  // Deriving a dependency from another panel's data would refetch on that panel's
  // arrival too, which puts a spinner over content that was already on screen - and a
  // test - for no benefit.
  const criteria = useAsync(() => leadScoreApi.criteria(), [])
  const properties = useAsync(() => leadScoreApi.properties(), [])
  const scores = useAsync(() => (roomId ? leadScoreApi.scores(roomId) : Promise.resolve(null)), [
    roomId,
  ])
  const history = useAsync(
    () => (roomId ? leadScoreApi.history(roomId, { limit: 20 }) : Promise.resolve(null)),
    [roomId],
  )

  if (vocabulary.loading) return <Spinner label="Loading the lead-score vocabulary" />
  if (vocabulary.error) {
    return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  }

  const served = vocabulary.data
  const actionability =
    served.actionability_note ||
    'Nothing happens on the seller screen. The scoring rule is continuous, so a score that moved is a record of what the buyer activity did, not a task for anyone.'

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <span className="text-accent">
            <Icon path={LEAD_SCORE_ICON} size={20} />
          </span>
          <h1 className="font-display text-2xl font-semibold text-foreground">Lead score</h1>
        </div>
        <p className="max-w-3xl text-sm text-muted-foreground">
          DSR engagement is scored against the{' '}
          <span className="font-mono">{served.score_property}</span> contact property. One criterion
          per Dock activity property, its filters, and a score value in a positive or negative
          bucket. Every matching event re-evaluates the score from the contact whole activity
          history.
        </p>
      </header>

      <ActionabilityNote note={actionability} />

      <RoomPicker
        rooms={roomOptions}
        roomId={roomId}
        onChange={setChosenRoom}
        loading={rooms.loading}
      />

      {roomId === '' ? (
        <EmptyState
          title="No sales room to score in yet"
          description="A room is the workspace the Dock activities happen in. Create one and this page fills in."
        />
      ) : (
        <>
          {summary.loading || scores.loading ? (
            <Spinner label="Loading the room score" />
          ) : summary.error ? (
            <ErrorNote error={summary.error} onRetry={summary.refetch} />
          ) : (
            <SummaryPanel summary={summary.data} served={served} />
          )}

          <StepOnePanel
            integrations={integrations}
            criteria={criteria}
            served={served}
          />
          <PropertiesPanel
            properties={properties}
            criteria={criteria}
            served={served}
            roomId={roomId}
          />
          <CriteriaPanel criteria={criteria} served={served} />
          <ScoresPanel
            scores={scores}
            history={history}
            summary={summary}
            roomId={roomId}
            served={served}
          />
          <RunsPanel history={history} roomId={roomId} served={served} />
        </>
      )}

      <InferencesPanel inferences={inferences} />
    </div>
  )
}

/* ------------------------------------------------------------------------- */
/* The room picker                                                            */
/* ------------------------------------------------------------------------- */

function RoomPicker({ rooms, roomId, onChange, loading }) {
  return (
    <Card>
      <Field
        label="Sales room"
        id="wf029-room"
        hint="The workspace the Dock activities happen in. Step 1 of the researched flow also connects this room to a deal."
      >
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading rooms</p>
        ) : (
          <select
            id="wf029-room"
            className={inputClass}
            value={roomId}
            onChange={(event) => onChange(event.target.value)}
          >
            {rooms.map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </select>
        )}
      </Field>
    </Card>
  )
}

/* ------------------------------------------------------------------------- */
/* The numbers above the fold                                                 */
/* ------------------------------------------------------------------------- */

function SummaryPanel({ summary, served }) {
  if (!summary) return null
  const states = summary.states || []
  return (
    <section className="space-y-3" aria-labelledby="wf029-summary-heading">
      <h2 id="wf029-summary-heading" className="font-display text-lg font-semibold text-foreground">
        This room
      </h2>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Criteria saved"
          value={summary.criteria_count}
          hint={`${summary.criteria_by_bucket.positive} positive, ${summary.criteria_by_bucket.negative} negative`}
          icon="schema"
        />
        <StatCard
          label="Contacts scored"
          value={summary.contacts_scored}
          hint={`${summary.contacts_above_zero} above zero, ${summary.contacts_below_zero} below zero`}
          icon="rooms"
        />
        <StatCard
          label="Highest score"
          value={summary.top_score}
          hint={`Average ${summary.average_score}`}
          icon="audit"
        />
        <StatCard
          label="Runs"
          value={summary.runs}
          hint={`${summary.runs_that_moved} moved the number`}
          icon="refresh"
        />
      </div>
      <Card>
        <h3 className="text-base font-semibold text-foreground">States that are not successes</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          A page of only green rows teaches nobody what to look for. These are the four this
          workflow distinguishes.
        </p>
        <ul className="mt-3 flex flex-col">
          {states.map((state) => (
            <li
              key={state.code}
              className="flex flex-col gap-1 border-t border-border-subtle/30 py-2 first:border-t-0"
            >
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={state.count > 0 ? 'update' : 'neutral'}>{state.count}</Badge>
                <span className="font-mono text-[12px] text-foreground">{state.code}</span>
              </div>
              <p className="text-xs text-muted-foreground">{state.meaning}</p>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-muted-foreground">{summary.execution_note}</p>
        <p className="mt-1 font-mono text-[11px] text-muted-foreground/70">
          score property {served.score_property}
        </p>
      </Card>
    </section>
  )
}

/* ------------------------------------------------------------------------- */
/* Step 1: the integration                                                    */
/* ------------------------------------------------------------------------- */

function StepOnePanel({ integrations, criteria, served }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const list = integrations.data?.integrations || []

  async function toggle(entry) {
    setBusy(true)
    setError(null)
    try {
      await leadScoreApi.amendIntegration(entry.id, { enabled: !entry.enabled })
      await integrations.refetch()
      // Whether a criterion can be saved depends on this organisation, so the list
      // is re-read rather than left showing a state the server would now refuse.
      await criteria.refetch()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-3" aria-labelledby="wf029-step1-heading">
      <h2 id="wf029-step1-heading" className="font-display text-lg font-semibold text-foreground">
        Step 1: the CRM integration
      </h2>
      <p className="max-w-3xl text-sm text-muted-foreground">
        Confirm the Dock and HubSpot integration is enabled, and that the workspace is connected
        to a deal or account. Both researched scopes are needed before a criterion can be saved.
      </p>
      {integrations.loading ? (
        <Spinner label="Loading the integration" />
      ) : integrations.error ? (
        <ErrorNote error={integrations.error} onRetry={integrations.refetch} />
      ) : list.length === 0 ? (
        <EmptyState
          title="No CRM organisation registered"
          description="Step 1 registers one. Without it there is no contact property to write and no token to write it with, so no criterion can be saved."
        />
      ) : (
        list.map((entry) => (
          <Card key={entry.id}>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <h3 className="text-base font-semibold text-foreground">{entry.label}</h3>
                <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                  {entry.vendor}
                  {entry.portal_id ? ` / ${entry.portal_id}` : ''}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={entry.enabled ? 'insert' : 'neutral'}>
                  {entry.enabled ? 'enabled' : 'disabled'}
                </Badge>
                <Badge tone={entry.writable ? 'insert' : 'update'}>
                  {entry.writable ? 'writable' : 'not writable'}
                </Badge>
                <Button
                  variant="secondary"
                  disabled={busy}
                  onClick={() => toggle(entry)}
                  aria-label={`${entry.enabled ? 'Disable' : 'Enable'} ${entry.label}`}
                >
                  {entry.enabled ? 'Switch off' : 'Switch on'}
                </Button>
              </div>
            </div>
            <ul className="mt-3 flex flex-col gap-1">
              {served.integration.required_scopes.map((scope) => {
                const held = entry.scopes.includes(scope)
                return (
                  <li key={scope} className="flex flex-wrap items-center gap-2 text-xs">
                    <Badge tone={held ? 'insert' : 'delete'}>{held ? 'held' : 'missing'}</Badge>
                    <span className="font-mono text-muted-foreground">{scope}</span>
                  </li>
                )
              })}
            </ul>
            <p className="mt-2 text-xs text-muted-foreground">
              {entry.connected_rooms.length === 0
                ? 'No room is connected to a deal. A run in an unconnected room reports that rather than attributing a score to nobody.'
                : `${entry.connected_rooms.length} room(s) connected to a deal.`}
            </p>
          </Card>
        ))
      )}
      {error && <ErrorNote error={error} />}
    </section>
  )
}

/* ------------------------------------------------------------------------- */
/* Step 1b: the Dock activity properties                                      */
/* ------------------------------------------------------------------------- */

function PropertiesPanel({ properties, criteria, served, roomId }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const awaiting = properties.data?.awaiting_provisioning || []

  async function provision(family) {
    setBusy(true)
    setError(null)
    try {
      await leadScoreApi.provisionProperty({ family, name: `dsr_${family}` })
      await properties.refetch()
      // A criterion is armed by its Dock property existing, so both lists move.
      await criteria.refetch()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-3" aria-labelledby="wf029-props-heading">
      <h2 id="wf029-props-heading" className="font-display text-lg font-semibold text-foreground">
        Step 1b: the Dock activity properties
      </h2>
      <p className="max-w-3xl text-sm text-muted-foreground">
        {properties.data?.provisioning_note ||
          'These properties come from provisioning the sales-room engagement object into the CRM. Until they exist, every criterion on one of them matches nothing.'}
      </p>
      {properties.loading ? (
        <Spinner label="Loading the Dock properties" />
      ) : properties.error ? (
        <ErrorNote error={properties.error} onRetry={properties.refetch} />
      ) : (
        <Card>
          <ul className="grid gap-3 sm:grid-cols-2">
            {(properties.data?.families || served.families.map((row) => row.name)).map((family) => {
              const entry = (properties.data?.properties || []).find(
                (row) => row.family === family,
              )
              const ready = entry && entry.state === 'provisioned'
              return (
                <li
                  key={family}
                  className="flex flex-col gap-2 border border-border-subtle rounded-sm p-3"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-mono text-[13px] text-foreground">{family}</span>
                    <Badge tone={ready ? 'insert' : 'update'}>
                      {ready ? 'provisioned' : 'not provisioned'}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">{familyLabel(served, family)}</p>
                  {!ready && (
                    <Button
                      variant="secondary"
                      disabled={busy}
                      onClick={() => provision(family)}
                      aria-label={`Record ${family} as provisioned`}
                    >
                      Record as provisioned
                    </Button>
                  )}
                </li>
              )
            })}
          </ul>
          {awaiting.length > 0 && (
            <p className="mt-3 text-xs text-muted-foreground">
              Awaiting provisioning: <span className="font-mono">{awaiting.join(', ')}</span>. Every
              criterion on one of these matches nothing, and every run says so.
            </p>
          )}
          <p className="mt-2 font-mono text-[11px] text-muted-foreground/70">room {roomId}</p>
        </Card>
      )}
      {error && <ErrorNote error={error} />}
    </section>
  )
}

/* ------------------------------------------------------------------------- */
/* Steps 2 to 5: the criteria                                                 */
/* ------------------------------------------------------------------------- */

function CriteriaPanel({ criteria, served }) {
  const [family, setFamily] = useState('downloads')
  const [bucket, setBucket] = useState('positive')
  const [score, setScore] = useState('20')
  const [label, setLabel] = useState('')
  const [occurred, setOccurred] = useState('')
  const [textFilter, setTextFilter] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const published = refinementsFor(served, family)
  const extra = published.filter((name) => name !== OCCURRED)[0] || ''

  async function save(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    const refinements = {}
    if (occurred.trim()) refinements[OCCURRED] = occurred.trim()
    if (extra && textFilter.trim()) refinements[extra] = textFilter.trim()
    try {
      await leadScoreApi.create({
        family,
        bucket,
        score: Number(score),
        label: label.trim(),
        refinements,
      })
      setLabel('')
      setOccurred('')
      setTextFilter('')
      await criteria.refetch()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  async function withdraw(id) {
    setBusy(true)
    setError(null)
    try {
      await leadScoreApi.withdraw(id)
      await criteria.refetch()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  const rows = criteria.data?.criteria || []

  return (
    <section className="space-y-3" aria-labelledby="wf029-criteria-heading">
      <h2 id="wf029-criteria-heading" className="font-display text-lg font-semibold text-foreground">
        Steps 2 to 5: add criteria
      </h2>
      <p className="max-w-3xl text-sm text-muted-foreground">
        Click Add criteria for either positive or negative scores. Choose the Dock property to
        score against, set filters, and assign a score. Save and the next matching event moves the
        contact score, with no further setup.
      </p>

      <Card>
        <form className="grid gap-4 sm:grid-cols-2" onSubmit={save}>
          <Field label="Score bucket" id="wf029-bucket" hint={bucketMeaning(served, bucket)}>
            <select
              id="wf029-bucket"
              className={inputClass}
              value={bucket}
              onChange={(event) => setBucket(event.target.value)}
            >
              {BUCKET_CHOICES.map((choice) => (
                <option key={choice} value={choice}>
                  {choice}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label="Dock property to score against"
            id="wf029-family"
            hint={familyLabel(served, family)}
          >
            <select
              id="wf029-family"
              className={inputClass}
              value={family}
              onChange={(event) => setFamily(event.target.value)}
            >
              {served.families.map((row) => (
                <option key={row.name} value={row.name}>
                  {row.name}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label="Score value"
            id="wf029-score"
            hint={`Adds ${signedScore(served, bucket, score)} points per matching event. The value is a magnitude; the bucket carries the sign.`}
          >
            <input
              id="wf029-score"
              type="number"
              min="1"
              step="1"
              className={inputClass}
              value={score}
              onChange={(event) => setScore(event.target.value)}
            />
          </Field>
          <Field label="Label" id="wf029-label" hint="Optional. What a person calls this rule.">
            <input
              id="wf029-label"
              className={inputClass}
              value={label}
              onChange={(event) => setLabel(event.target.value)}
            />
          </Field>
          <Field
            label={refinementLabel(served, OCCURRED)}
            id="wf029-occurred"
            hint="The recommended baseline. A UTC date, or from and to separated by a comma."
          >
            <input
              id="wf029-occurred"
              className={inputClass}
              placeholder="2026-10-01"
              value={occurred}
              onChange={(event) => setOccurred(event.target.value)}
            />
          </Field>
          <Field
            label={extra ? refinementLabel(served, extra) : 'No second filter for this property'}
            id="wf029-text-filter"
            hint={
              extra
                ? 'Exact text. No substring matching: a criterion naming a document also has to name its year if it means that year.'
                : 'The research names only the Occurred baseline for this property.'
            }
          >
            <input
              id="wf029-text-filter"
              className={inputClass}
              disabled={!extra}
              value={textFilter}
              onChange={(event) => setTextFilter(event.target.value)}
            />
          </Field>
          <div className="sm:col-span-2">
            <Button type="submit" variant="primary" disabled={busy}>
              Save criterion
            </Button>
          </div>
        </form>
      </Card>

      {error && <ErrorNote error={error} onRetry={criteria.refetch} />}

      {criteria.loading ? (
        <Spinner label="Loading the criteria" />
      ) : criteria.error ? (
        <ErrorNote error={criteria.error} onRetry={criteria.refetch} />
      ) : rows.length === 0 ? (
        <EmptyState
          title="No criterion saved yet"
          description="Nothing scores until one is. Add criteria above and the next matching DSR event moves the contact score."
        />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {rows.map((row) => (
            <Card key={row.id}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <h3 className="text-base font-semibold text-foreground">
                    {row.label || 'Untitled criterion'}
                  </h3>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {signedScore(served, row.bucket, row.score)} points per matching event
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={row.bucket === 'negative' ? 'delete' : 'insert'}>{row.bucket}</Badge>
                  <Badge tone={row.armed ? 'insert' : 'update'}>
                    {row.armed ? 'armed' : 'not armed'}
                  </Badge>
                  <Button
                    variant="danger"
                    disabled={busy}
                    onClick={() => withdraw(row.id)}
                    aria-label={`Withdraw ${row.label || 'criterion'}`}
                  >
                    Withdraw
                  </Button>
                </div>
              </div>
              <p className="mt-2 font-mono text-xs text-muted-foreground">
                {row.family} / {row.score_property}
              </p>
              <ul className="mt-2 flex flex-wrap gap-2">
                {Object.entries(row.refinements).map(([name, value]) => (
                  <li key={name}>
                    <Badge tone="neutral">
                      {name}={typeof value === 'object' ? JSON.stringify(value) : String(value)}
                    </Badge>
                  </li>
                ))}
                {Object.keys(row.refinements).length === 0 && (
                  <li className="text-xs text-muted-foreground">No filters set.</li>
                )}
              </ul>
              {!row.armed && (
                <p className="mt-2 text-xs text-muted-foreground">
                  No Dock activity property is provisioned for this criterion, so it has nothing to
                  score against and matches nothing. Its property is{' '}
                  <span className="font-mono">{row.family}</span>
                </p>
              )}
              {!row.property_resolved && (
                <p className="mt-2 text-xs text-muted-foreground">
                  Writes to <span className="font-mono">{row.score_property}</span>, which this build
                  does not provision. The points are computed; no property of that name is written.
                </p>
              )}
              {(row.lint || []).length > 0 && (
                <ul className="mt-2 flex flex-col">
                  {row.lint.map((lint) => (
                    <LintRow key={lint.code} lint={lint} />
                  ))}
                </ul>
              )}
            </Card>
          ))}
        </div>
      )}
    </section>
  )
}

/* ------------------------------------------------------------------------- */
/* The scores                                                                 */
/* ------------------------------------------------------------------------- */

function ScoresPanel({ scores, history, summary, roomId, served }) {
  const [contact, setContact] = useState('')
  const [run, setRun] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const rows = scores.data?.scores || []

  async function rerun(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      setRun(await leadScoreApi.score(roomId, { contact: contact.trim() }))
      // One request wrote three things: the score row, a run, and the audit trail.
      await Promise.all([scores.refetch(), history.refetch(), summary.refetch()])
    } catch (problem) {
      setError(problem)
      setRun(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-3" aria-labelledby="wf029-scores-heading">
      <h2 id="wf029-scores-heading" className="font-display text-lg font-semibold text-foreground">
        Contact scores
      </h2>
      <p className="max-w-3xl text-sm text-muted-foreground">
        The score is the signed total over the contact whole activity history, so a withdrawn
        criterion takes its points off at that contact next event and a replayed webhook moves
        nothing.
      </p>

      <Card>
        <form className="flex flex-col gap-3 sm:flex-row sm:items-end" onSubmit={rerun}>
          <div className="flex-1">
            <Field
              label="Contact"
              id="wf029-contact"
              hint="Re-evaluate one contact now and see exactly what would be written to the CRM."
            >
              <input
                id="wf029-contact"
                className={inputClass}
                value={contact}
                onChange={(event) => setContact(event.target.value)}
              />
            </Field>
          </div>
          <Button type="submit" variant="secondary" disabled={busy || contact.trim() === ''}>
            Re-evaluate now
          </Button>
        </form>
        {error && <ErrorNote error={error} />}
        {run && (
          <div className="mt-4 space-y-3">
            <h3 className="text-base font-semibold text-foreground">
              Last run for <span className="font-mono">{run.contact}</span>
            </h3>
            <ScorePill score={run.score} />
            {(run.contributions || []).length > 0 ? (
              <ul className="flex flex-col">
                {run.contributions.map((contribution) => (
                  <ContributionRow
                    key={contribution.criterion_id || contribution.label}
                    contribution={contribution}
                  />
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted-foreground">
                No criterion scored this contact on this run.
              </p>
            )}
            {(run.criteria || []).length > 0 && (
              <div>
                <h4 className="text-sm font-semibold text-foreground">
                  Why each criterion did or did not score
                </h4>
                <ul className="mt-1 flex flex-col">
                  {run.criteria.map((criterion) => (
                    <CriterionRunRow
                      key={criterion.criterion_id || criterion.label || criterion.family}
                      criterion={criterion}
                    />
                  ))}
                </ul>
              </div>
            )}
            {(run.findings || []).length > 0 && (
              <ul className="flex flex-col">
                {run.findings.map((finding) => (
                  <FindingRow key={finding.code} finding={finding} />
                ))}
              </ul>
            )}
            <div>
              <h4 className="text-sm font-semibold text-foreground">What would be written</h4>
              <p className="mt-0.5 text-xs text-muted-foreground">{served.execution_note}</p>
              <ul className="mt-2 flex flex-col">
                {(run.crm_plan?.requests || []).map((request) => (
                  <PlanRow key={request.purpose} request={request} />
                ))}
              </ul>
              {(run.crm_plan?.dropped_properties || []).length > 0 && (
                <ul className="mt-2 flex flex-col">
                  {run.crm_plan.dropped_properties.map((row) => (
                    <li
                      key={row.property}
                      className="flex flex-col gap-1 border-t border-border-subtle/30 py-2"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone="delete">not written</Badge>
                        <span className="font-mono text-[12px] text-foreground">
                          {row.property}
                        </span>
                      </div>
                      <p className="text-xs text-muted-foreground">{row.comparison || row.reason}</p>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </Card>

      {scores.loading ? (
        <Spinner label="Loading the scores" />
      ) : scores.error ? (
        <ErrorNote error={scores.error} onRetry={scores.refetch} />
      ) : rows.length === 0 ? (
        <EmptyState
          title="No contact scored yet"
          description="The rule is continuous: the first matching DSR event scores its contact with no action from anyone."
        />
      ) : (
        <Card>
          <ul className="flex flex-col">
            {rows.map((row) => (
              <li
                key={row.contact}
                className="flex flex-col gap-2 border-t border-border-subtle/30 py-3 first:border-t-0 sm:flex-row sm:items-start sm:justify-between"
              >
                <div className="min-w-0">
                  <p className="font-mono text-[13px] text-foreground">{row.contact}</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {row.account || 'no account recorded'} / {row.event_count} event(s) /{' '}
                    {row.matched_criteria} of {row.criterion_count} criterion/criteria matched
                  </p>
                  {(row.contributions || []).length > 0 && (
                    <ul className="mt-1 flex flex-col">
                      {row.contributions.map((contribution) => (
                        <ContributionRow
                          key={contribution.criterion_id || contribution.label}
                          contribution={contribution}
                        />
                      ))}
                    </ul>
                  )}
                </div>
                <ScorePill score={row.score} />
              </li>
            ))}
          </ul>
        </Card>
      )}
    </section>
  )
}

/* ------------------------------------------------------------------------- */
/* The runs                                                                   */
/* ------------------------------------------------------------------------- */

function RunsPanel({ history, roomId, served }) {
  const [movedOnly, setMovedOnly] = useState(false)

  const rows = useMemo(() => {
    const all = history.data?.runs || []
    return movedOnly ? all.filter((row) => row.changed) : all
  }, [history.data, movedOnly])

  return (
    <section className="space-y-3" aria-labelledby="wf029-runs-heading">
      <h2 id="wf029-runs-heading" className="font-display text-lg font-semibold text-foreground">
        Scoring runs
      </h2>
      <p className="max-w-3xl text-sm text-muted-foreground">
        The rule is continuous, so a run is recorded for every event that reached the scoring step
        whether or not the number moved. Both views are here: the runs that moved it, and all of
        them.
      </p>
      <Card>
        <Field label="Show" id="wf029-moved-only">
          <select
            id="wf029-moved-only"
            className={inputClass}
            value={movedOnly ? 'moved' : 'all'}
            onChange={(event) => setMovedOnly(event.target.value === 'moved')}
          >
            <option value="all">Every run</option>
            <option value="moved">Only the runs that moved the number</option>
          </select>
        </Field>
      </Card>
      {history.loading ? (
        <Spinner label="Loading the runs" />
      ) : history.error ? (
        <ErrorNote error={history.error} onRetry={history.refetch} />
      ) : rows.length === 0 ? (
        <EmptyState
          title="No run recorded yet"
          description="A run appears the first time a DSR event reaches the scoring step for a contact in this room."
        />
      ) : (
        <div className="space-y-3">
          {rows.map((row) => (
            <Card key={row.run_id}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <h3 className="font-mono text-[13px] text-foreground">{row.contact}</h3>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {row.driver} / {row.from_score} to {row.to_score} / {row.matched_criteria} of{' '}
                    {row.criterion_count} criterion/criteria matched / room {roomId}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={row.changed ? 'insert' : 'neutral'}>
                    {row.changed ? 'moved' : 'no change'}
                  </Badge>
                  {row.floor_applied && <Badge tone="update">floor applied</Badge>}
                </div>
              </div>
              {(row.findings || []).length > 0 && (
                <ul className="mt-2 flex flex-col">
                  {row.findings.map((finding) => (
                    <FindingRow key={finding.code} finding={finding} />
                  ))}
                </ul>
              )}
              <p className="mt-2 text-xs text-muted-foreground">
                Miss tally: <span className="font-mono">{row.miss_tally}</span>
              </p>
            </Card>
          ))}
        </div>
      )}
      <p className="font-mono text-[11px] text-muted-foreground/70">
        {served.baseline_quote}
      </p>
    </section>
  )
}

/* ------------------------------------------------------------------------- */
/* The judgement calls                                                        */
/* ------------------------------------------------------------------------- */

function InferencesPanel({ inferences }) {
  if (inferences.loading) return null
  if (inferences.error) return <ErrorNote error={inferences.error} onRetry={inferences.refetch} />
  const rows = inferences.data?.inferences || []
  if (rows.length === 0) return null
  return (
    <section className="space-y-3" aria-labelledby="wf029-inferences-heading">
      <h2
        id="wf029-inferences-heading"
        className="font-display text-lg font-semibold text-foreground"
      >
        What this build decided, and why
      </h2>
      <p className="max-w-3xl text-sm text-muted-foreground">
        The research fixes the surface: the score property, the two buckets, the five Dock
        properties, the filters worth setting, the scopes and the endpoints. It does not settle the
        edges, so the edges are listed here rather than left in a function body.
      </p>
      <div className="space-y-3">
        {rows.map((entry) => (
          <Card key={entry.id}>
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-base font-semibold text-foreground">{entry.topic}</h3>
              {entry.jev_audit && <Badge tone="update">decided by Jev</Badge>}
            </div>
            <p className="mt-1 font-mono text-[11px] text-muted-foreground">{entry.id}</p>
            <p className="mt-2 text-sm text-muted-foreground">{entry.why}</p>
            <p className="mt-2 text-xs text-muted-foreground">
              <span className="font-medium text-foreground">Change it:</span> {entry.change_it}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              <span className="font-medium text-foreground">Affects:</span> {entry.blast_radius}
            </p>
            {entry.jev_audit && (
              <p className="mt-1 font-mono text-[11px] text-muted-foreground/70">
                audit {entry.jev_audit}
              </p>
            )}
          </Card>
        ))}
      </div>
    </section>
  )
}

export default LeadScore
