/**
 * Nurture series (WF-105).
 *
 * One page, in the order the researched flow runs in:
 *
 * 1. The series, each with its starting rule block, its exit rules, its Goal tab
 *    and its path.
 * 2. The workspace tag vocabulary a tag block selects from. A tag block cannot
 *    create a tag, so the register form is the only way a name enters.
 * 3. A poll, which is the automation: a recurring event is evaluated against the
 *    starting rule block and the outcome is recorded.
 * 4. The enrollments and the tags written onto contacts.
 *
 * Two things this page states rather than hides: a poll that skips a contact is a
 * decision, not a failure (the outcomes are labelled in words), and the poll runs
 * on demand here while the research's cadence is served as vocabulary.
 *
 * Design floor: every control is `min-h-11`, every glyph sits beside a text label,
 * every status is carried by words rather than by colour alone, and the loading,
 * error and empty branches are all present.
 */

import { useState } from 'react'
import {
  Badge,
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
import { outcomeLabel, seriesApi, seriesStateLabel, stateBadge } from './api'
import { BlockRow, Fact, FilterChip, FILTER_GLYPHS, Notice, Quote, Subhead } from './primitives'

function formatTime(value) {
  if (!value) return '--'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value)
  return parsed.toISOString().replace('T', ' ').slice(0, 16)
}

export default function NurtureSeries() {
  const [selected, setSelected] = useState(null)
  const [contactId, setContactId] = useState('con-1')
  const [plan, setPlan] = useState('trial')
  const [advance, setAdvance] = useState(true)
  const [eventType, setEventType] = useState('subscription.expiring')
  const [pollResult, setPollResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState(null)

  const summary = useAsync(() => seriesApi.summary(), [])
  const catalog = useAsync(() => seriesApi.catalog(), [])
  const vocabulary = useAsync(() => seriesApi.vocabulary(), [])
  const inferences = useAsync(() => seriesApi.inferences(), [])
  const enrollments = useAsync(() => seriesApi.enrollments({ limit: 50 }), [])
  const applications = useAsync(() => seriesApi.tagApplications({ limit: 50 }), [])

  const reload = () => {
    summary.refetch()
    catalog.refetch()
    enrollments.refetch()
    applications.refetch()
  }

  if (summary.loading || catalog.loading) {
    return <Spinner label="Loading nurture series" />
  }
  if (summary.error) return <ErrorNote error={summary.error} onRetry={summary.refetch} />
  if (catalog.error) return <ErrorNote error={catalog.error} onRetry={catalog.refetch} />

  const counts = summary.data || {}
  const series = catalog.data?.series || []
  const current = series.find((entry) => entry.id === selected) || null

  async function runPoll() {
    if (!current) {
      setActionError('Choose a series before polling.')
      return
    }
    setBusy(true)
    setActionError(null)
    try {
      const result = await seriesApi.pollSeries(current.id, [
        {
          contact: { contact_id: contactId, plan },
          event_type: eventType,
          advance,
        },
      ])
      setPollResult(result)
      reload()
    } catch (error) {
      setActionError(error.message || String(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-semibold text-foreground">Nurture series</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          A live series re-enrols a contact that matches its starting rule block again when a
          recurring event fires. The tag block writes an existing workspace tag onto the contact, and
          an exit rule removes a contact for good.
        </p>
      </header>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Series" value={counts.series ?? 0} icon="dashboard" />
        <StatCard label="Live" value={counts.live ?? 0} icon="audit" />
        <StatCard label="Enrollments" value={counts.enrollments ?? 0} icon="rooms" />
        <StatCard label="Tag applications" value={counts.tag_applications ?? 0} icon="schema" />
      </section>

      <Notice tone="info">
        The poll runs on demand here. The research polls every{' '}
        <span className="font-mono">{vocabulary.data?.poll_interval_minutes?.value ?? 30}</span>{' '}
        minutes; that cadence is served as vocabulary and the series records when it was last polled,
        so a scheduler can drive it.
      </Notice>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <Subhead count={series.length}>Series</Subhead>
          {series.length === 0 ? (
            <EmptyState
              title="No series yet"
              description="A series needs a starting rule block, an optional goal and exit rules, and a path that ends in a tag block."
            />
          ) : (
            <ul className="space-y-2">
              {series.map((entry) => (
                <li key={entry.id}>
                  <button
                    type="button"
                    onClick={() => setSelected(entry.id)}
                    aria-pressed={selected === entry.id}
                    className={`min-h-11 w-full rounded-sm border px-3 py-2 text-left ${
                      selected === entry.id
                        ? 'border-accent bg-accent-soft'
                        : 'border-border-subtle bg-surface hover:border-accent'
                    }`}
                  >
                    <span className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-sm font-medium text-foreground">{entry.data.name}</span>
                      <Badge tone={entry.data.state === 'live' ? 'insert' : 'neutral'}>
                        {seriesStateLabel(entry.data.state)}
                      </Badge>
                    </span>
                    <span className="mt-1 block font-mono text-xs text-muted-foreground">
                      re-enter {entry.data.reenter ? 'on' : 'off'} / {entry.block_count} block(s) /{' '}
                      {entry.start_filter_count} start filter(s)
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <Subhead count={applications.data?.count ?? 0}>Tags written onto contacts</Subhead>
          {applications.loading ? (
            <Spinner label="Loading tag applications" />
          ) : (applications.data?.tag_applications || []).length === 0 ? (
            <EmptyState
              title="No tags applied yet"
              description="A tag is written when a contact reaches the tag block at the end of a path."
            />
          ) : (
            <ul className="space-y-2">
              {applications.data.tag_applications.map((entry) => (
                <li
                  key={entry.id}
                  className="flex min-h-11 items-center justify-between gap-3 border-b border-border-subtle py-2 last:border-b-0"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm text-foreground">{entry.data.tag}</p>
                    <p className="truncate font-mono text-xs text-muted-foreground">
                      {entry.data.contact_id} / {formatTime(entry.data.applied_at)}
                    </p>
                  </div>
                  <Badge tone="insert">{entry.data.topic}</Badge>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {current && (
        <Card>
          <Subhead>Selected series</Subhead>
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={current.data.state === 'live' ? 'insert' : 'neutral'}>
                {seriesStateLabel(current.data.state)}
              </Badge>
              <Badge tone={current.data.reenter ? 'update' : 'neutral'}>
                Re-enter Series {current.data.reenter ? 'enabled' : 'disabled'}
              </Badge>
              <Badge tone="neutral">polled every {current.data.poll_interval_minutes} min</Badge>
            </div>

            <p className="text-xs text-muted-foreground">
              Last polled{' '}
              <span className="font-mono">
                {current.data.last_polled_at ? formatTime(current.data.last_polled_at) : 'never'}
              </span>
              . <span className="font-mono">GET /api/wf105/due</span> names the live series whose next
              poll is due, so a scheduler needs no timer inside a feature.
            </p>

            <div className="grid gap-3 sm:grid-cols-3">
              <div>
                <Subhead count={current.data.start_filters.length}>Starting rule block</Subhead>
                <div className="flex flex-wrap gap-1.5">
                  {current.data.start_filters.map((filter) => (
                    <FilterChip
                      key={`${filter.field}-${filter.value}`}
                      filter={filter}
                      glyph={FILTER_GLYPHS.start}
                    />
                  ))}
                </div>
              </div>
              <div>
                <Subhead count={current.data.exit_filters.length}>Exit rules</Subhead>
                {current.data.exit_filters.length === 0 ? (
                  <p className="text-xs text-muted-foreground">No exit rules.</p>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {current.data.exit_filters.map((filter) => (
                      <FilterChip
                        key={`${filter.field}-${filter.value}`}
                        filter={filter}
                        glyph={FILTER_GLYPHS.exit}
                      />
                    ))}
                  </div>
                )}
                <p className="mt-1 text-xs text-muted-foreground">
                  A match is permanent: an exited contact cannot re-enter.
                </p>
              </div>
              <div>
                <Subhead>Goal tab</Subhead>
                {current.data.goal ? (
                  <>
                    <div className="flex flex-wrap gap-1.5">
                      {current.data.goal.outcome_filters.map((filter) => (
                        <FilterChip
                          key={`${filter.field}-${filter.value}`}
                          filter={filter}
                          glyph={FILTER_GLYPHS.goal}
                        />
                      ))}
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Match within <span className="font-mono">{current.data.goal.match_window_days}</span>{' '}
                      days of entry.
                    </p>
                  </>
                ) : (
                  <p className="text-xs text-muted-foreground">No goal declared.</p>
                )}
              </div>
            </div>

            <div>
              <Subhead count={current.block_count}>Path</Subhead>
              <ul>
                {current.data.path.map((block, index) => (
                  <BlockRow
                    key={`${block.kind}-${index}`}
                    block={block}
                    position={index}
                    last={index === current.data.path.length - 1}
                  />
                ))}
              </ul>
            </div>

            <div className="rounded-sm border border-border-subtle p-3">
              <Subhead>Run one poll</Subhead>
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Contact id" id="poll-contact">
                  <input
                    id="poll-contact"
                    className={inputClass}
                    value={contactId}
                    onChange={(event) => setContactId(event.target.value)}
                  />
                </Field>
                <Field label="Plan attribute" id="poll-plan">
                  <input
                    id="poll-plan"
                    className={inputClass}
                    value={plan}
                    onChange={(event) => setPlan(event.target.value)}
                  />
                </Field>
                <Field label="Recurring event" id="poll-event">
                  <input
                    id="poll-event"
                    className={inputClass}
                    value={eventType}
                    onChange={(event) => setEventType(event.target.value)}
                  />
                </Field>
              </div>
              <label className="mt-3 flex min-h-11 items-center gap-2 text-sm text-foreground">
                <input
                  type="checkbox"
                  checked={advance}
                  onChange={(event) => setAdvance(event.target.checked)}
                />
                Also advance the contact one block along the path
              </label>
              <div className="mt-3 flex items-end gap-2">
                <Button
                  variant="primary"
                  className="min-h-11"
                  onClick={runPoll}
                  disabled={busy || current.data.state !== 'live'}
                >
                  {busy ? 'Polling' : 'Run poll'}
                </Button>
                {current.data.state !== 'live' && (
                  <span className="text-xs text-muted-foreground">
                    Publish the series before polling it.
                  </span>
                )}
              </div>

              {actionError && <ErrorNote error={actionError} onRetry={runPoll} />}

              {pollResult && (
                <div className="mt-3 space-y-2 rounded-sm border border-border-subtle p-3">
                  <Subhead>Poll result</Subhead>
                  <div className="grid gap-2 sm:grid-cols-3">
                    <Fact label="events">{pollResult.event_count}</Fact>
                    <Fact label="enrolled">{pollResult.enrolled}</Fact>
                    <Fact label="re-entered">{pollResult.reentered}</Fact>
                  </div>
                  <ul className="space-y-1">
                    {Object.entries(pollResult.outcomes || {}).map(([outcome, count]) => (
                      <li key={outcome} className="flex items-center justify-between text-xs">
                        <span className="text-foreground">{outcomeLabel(outcome)}</span>
                        <span className="font-mono text-muted-foreground">{count}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>
        </Card>
      )}

      <Card>
        <Subhead count={enrollments.data?.count ?? 0}>Enrollments</Subhead>
        {enrollments.loading ? (
          <Spinner label="Loading enrollments" />
        ) : enrollments.error ? (
          <ErrorNote error={enrollments.error} onRetry={enrollments.refetch} />
        ) : (enrollments.data?.enrollments || []).length === 0 ? (
          <EmptyState
            title="No enrollments yet"
            description="Run a poll with a contact that matches a series' starting rule block."
          />
        ) : (
          <ul className="space-y-2">
            {enrollments.data.enrollments.map((entry) => {
              const badge = stateBadge(entry.data.state)
              return (
                <li
                  key={entry.id}
                  className="flex min-h-11 items-center justify-between gap-3 border-b border-border-subtle py-2 last:border-b-0"
                >
                  <div className="min-w-0">
                    <p className="truncate font-mono text-xs text-foreground">
                      {entry.data.contact_id}
                    </p>
                    <p className="truncate font-mono text-xs text-muted-foreground">
                      position {entry.data.position} / re-entries {entry.data.reentry_count} / goal{' '}
                      {entry.data.goal_met ? 'met' : 'not met'}
                    </p>
                  </div>
                  <Badge tone={badge.tone}>{badge.label}</Badge>
                </li>
              )
            })}
          </ul>
        )}
      </Card>

      <Card>
        <Subhead>How re-entry and the exit rule work</Subhead>
        <div className="space-y-3">
          <Quote source={vocabulary.data?.reenter_series?.source}>
            {vocabulary.data?.reenter_series?.quote ||
              'Customers can re-enter the same Series multiple times if you enable Re-enter Series.'}
          </Quote>
          <Quote source={vocabulary.data?.sticky_exit_rule?.source}>
            {vocabulary.data?.sticky_exit_rule?.quote ||
              'If a customer matches an exit rule they exit completely, and cannot re-enter.'}
          </Quote>
          <Quote source={vocabulary.data?.existing_tag_only?.source}>
            {vocabulary.data?.existing_tag_only?.quote ||
              'You cannot create a new tag in the tag block, but can use any existing workspace tag.'}
          </Quote>
          {(inferences.data?.inferences || [])
            .filter((entry) =>
              ['inference_poll_is_on_demand', 'inference_exit_is_any_match'].includes(entry.id),
            )
            .map((entry) => (
              <dl key={entry.id} className="space-y-1 rounded-sm border border-border-subtle p-3">
                <dt className="text-sm font-medium text-foreground">{entry.decision}</dt>
                <dd className="text-sm text-muted-foreground">{entry.why}</dd>
              </dl>
            ))}
        </div>
      </Card>
    </div>
  )
}
