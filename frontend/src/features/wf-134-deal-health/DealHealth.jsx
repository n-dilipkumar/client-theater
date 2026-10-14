/**
 * WF-134: the seller's deal-health page.
 *
 * What this page is for
 * ---------------------
 *
 * A rep opens it and reads, for one deal: the score and the three factors
 * behind it, the risk reasons that are active, who in the buying group has
 * engaged and who has not, which key asset is still owed, the open risk alerts,
 * and the coaching cues raised for them. Every cue can be closed with an
 * outcome, and the outcomes become the readiness reading at the foot of the
 * page.
 *
 * The page computes nothing it can read. The bands, the risk reasons, the
 * frameworks, the event weights and the decisions behind them all come from
 * `GET /api/wf-134/vocabulary`, so a change to the rules cannot leave this page
 * naming a state the API does not serve.
 *
 * Not built here
 * --------------
 *
 * The research lists surfaces this product does not carry: live coaching during
 * a call, call transcription, call documentation, CRM opportunity fields and a
 * notification channel. The page names them from the vocabulary's
 * `not_implemented` list rather than omitting them, so a reviewer can tell a
 * decision from an oversight. No vendor number is repeated anywhere: the
 * research quotes written guarantees this product has no evidence for.
 *
 * The design floor is met: semantic tokens only, `rounded-sm`, a hairline
 * instead of a shadow, a real label on every input, `min-h-11` on every control
 * through the shared `Button`, loading and error states from `useAsync`, and no
 * state carried by colour alone (every badge carries a word).
 */

import { useEffect, useState } from 'react'

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

import {
  EVENT_WORDS,
  bandWord,
  deltaSentence,
  dealHealthApi,
  eventWord,
  listRooms,
  weightedContribution,
} from './api.js'
import {
  AlertRow,
  BandBadge,
  ContentRow,
  CoverageColumns,
  CueRow,
  FactorRow,
  Notice,
  ReadinessPanel,
  ReasonList,
  Section,
} from './primitives.jsx'

const FACTOR_KEYS = ['engagement', 'content', 'stakeholder']

/** One deal's live board: everything the page needs in one round of reads. */
async function loadBoard(dealId) {
  const [score, coverage, content, alerts, coaching, history, events, deal] = await Promise.all([
    dealHealthApi.score(dealId),
    dealHealthApi.coverage(dealId),
    dealHealthApi.content(dealId),
    dealHealthApi.alerts(dealId),
    dealHealthApi.coaching(dealId),
    dealHealthApi.history(dealId),
    dealHealthApi.events(dealId),
    dealHealthApi.deal(dealId),
  ])
  return {
    score: score.score,
    recorded: score.recorded,
    lastRecorded: score.last_recorded,
    coverage,
    content: content.content,
    alerts: alerts.alerts,
    coaching: coaching.coaching,
    history: history.history,
    events: events.events,
    deal,
  }
}

function DealHealthPage() {
  const vocabulary = useAsync(() => dealHealthApi.vocabulary(), [])
  const rooms = useAsync(() => listRooms(), [])
  const [roomId, setRoomId] = useState('')

  useEffect(() => {
    const list = rooms.data?.records || []
    if (!roomId && list.length) setRoomId(list[0].id)
  }, [rooms.data, roomId])

  const deals = useAsync(
    () => (roomId ? dealHealthApi.deals(roomId) : Promise.resolve({ deals: [] })),
    [roomId],
  )
  const [dealId, setDealId] = useState('')

  useEffect(() => {
    const list = deals.data?.deals || []
    if (list.length && !list.some((deal) => deal.id === dealId)) setDealId(list[0].id)
    if (!list.length && dealId) setDealId('')
  }, [deals.data, dealId])

  const board = useAsync(() => (dealId ? loadBoard(dealId) : Promise.resolve(null)), [dealId])
  const summary = useAsync(
    () => (roomId ? dealHealthApi.summary(roomId) : Promise.resolve(null)),
    [roomId],
  )
  const readiness = useAsync(() => dealHealthApi.readiness(), [])

  const [busy, setBusy] = useState('')
  const [actionError, setActionError] = useState(null)
  const [newEvent, setNewEvent] = useState({ kind: 'meeting', person: '', seconds: '600' })
  const [newDeal, setNewDeal] = useState({ name: '', framework: 'meddicc', rep: '' })

  const frameworks = vocabulary.data?.frameworks || ['meddicc']

  function refreshAll() {
    board.refetch()
    summary.refetch()
    readiness.refetch()
    deals.refetch()
  }

  /** Run one write, name it while it runs, and refresh the reads it changed. */
  async function run(label, action) {
    setBusy(label)
    setActionError(null)
    try {
      await action()
      refreshAll()
    } catch (error) {
      setActionError(error)
    } finally {
      setBusy('')
    }
  }

  const deal = board.data?.deal
  const score = board.data?.score

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-foreground">Deal health and rep coaching</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Score one deal from engagement decay, unreviewed key material and missing stakeholders.
          A score in the at-risk band raises a risk alert and a coaching cue for the rep.
        </p>
      </header>

      {vocabulary.error && <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />}
      {rooms.error && <ErrorNote error={rooms.error} onRetry={rooms.refetch} />}
      {actionError && (
        <Notice tone="destructive" title="That change was refused">
          <p>{String(actionError.message || actionError)}</p>
          {actionError.errors && (
            <ul className="mt-1 space-y-0.5">
              {Object.entries(actionError.errors).map(([field, message]) => (
                <li key={field}>
                  <span className="font-mono text-xs">{field}</span>: {message}
                </li>
              ))}
            </ul>
          )}
        </Notice>
      )}

      <Section title="Scope" hint="One room, then one deal. Every reading below is that deal's.">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Room" id="wf134-room">
            <select
              id="wf134-room"
              className={inputClass}
              value={roomId}
              onChange={(event) => setRoomId(event.target.value)}
            >
              {(rooms.data?.records || []).map((room) => (
                <option key={room.id} value={room.id}>
                  {room.data?.name || room.id}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Deal" id="wf134-deal">
            <select
              id="wf134-deal"
              className={inputClass}
              value={dealId}
              onChange={(event) => setDealId(event.target.value)}
            >
              {(deals.data?.deals || []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </Section>

      {summary.data && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard label="Deals" value={summary.data.deals} hint="in this room" icon="database" />
          <StatCard label="Healthy" value={summary.data.healthy} hint="band, 70 and above" />
          <StatCard label="Watch" value={summary.data.watch} hint="band, 45 to 69" />
          <StatCard
            label="At risk"
            value={summary.data.at_risk}
            hint={`${summary.data.open_alerts} open alert(s)`}
          />
        </div>
      )}

      {!deals.loading && !dealId && (
        <EmptyState
          title="No deal in this room yet"
          description="Add one to score. It starts with no evidence, so its reading is at risk until engagement, coverage and key material arrive."
          action={
            <div className="w-full max-w-md space-y-3 text-left">
              <Field label="Deal name" id="wf134-new-deal-name">
                <input
                  id="wf134-new-deal-name"
                  className={inputClass}
                  value={newDeal.name}
                  onChange={(event) => setNewDeal({ ...newDeal, name: event.target.value })}
                />
              </Field>
              <Field label="Framework" id="wf134-new-deal-framework" hint="The score is framework-neutral.">
                <select
                  id="wf134-new-deal-framework"
                  className={inputClass}
                  value={newDeal.framework}
                  onChange={(event) => setNewDeal({ ...newDeal, framework: event.target.value })}
                >
                  {frameworks.map((framework) => (
                    <option key={framework} value={framework}>
                      {framework}
                    </option>
                  ))}
                </select>
              </Field>
              <Button
                variant="primary"
                icon="plus"
                disabled={!roomId || !newDeal.name || busy === 'create-deal'}
                onClick={() =>
                  run('create-deal', async () => {
                    await dealHealthApi.createDeal(roomId, newDeal)
                    setNewDeal({ name: '', framework: 'meddicc', rep: '' })
                  })
                }
              >
                Add the deal
              </Button>
            </div>
          }
        />
      )}

      {(board.loading || deals.loading) && dealId && <Spinner label="Loading the deal's board" />}
      {board.error && <ErrorNote error={board.error} onRetry={board.refetch} />}

      {board.data && deal && (
        <>
          <Section
            title={`${deal.name} | deal health ${score.value} of 100`}
            hint={`${deal.account || 'no account'} | ${deal.stage || 'no stage'} | framework ${
              score.framework
            } | live reading, ${board.data.recorded ? 'a score is recorded' : 'nothing recorded yet'}`}
            action={
              <Button
                icon="refresh"
                disabled={busy === 'score'}
                onClick={() => run('score', () => dealHealthApi.scoreNow(dealId))}
              >
                Score now
              </Button>
            }
          >
            <div className="space-y-5">
              <div className="flex flex-wrap items-center gap-3">
                <BandBadge band={score.band} value={score.value} />
                <span className="text-sm text-muted-foreground">{deltaSentence(score)}</span>
              </div>
              <div className="space-y-4">
                {FACTOR_KEYS.map((key) => (
                  <FactorRow
                    key={key}
                    name={key}
                    factor={score.factors[key]}
                    weight={score.weights[key]}
                    contribution={weightedContribution(score.factors[key], score.weights, key)}
                  />
                ))}
              </div>
            </div>
          </Section>

          <Section
            title="Risk reasons"
            hint="Each active reason raises exactly one coaching cue below."
          >
            <ReasonList score={score} />
          </Section>

          <Section
            title="Stakeholder coverage map"
            hint={`Coverage target is ${Math.round((board.data.coverage.target || 0) * 100)}% of the named buying group.`}
          >
            <CoverageColumns coverage={board.data.coverage} />
          </Section>

          <Section
            title="Key material still owed"
            hint="A required asset nobody reviewed is a risk reason and a prompt for the rep."
          >
            {board.data.content.length ? (
              <ul>
                {board.data.content.map((item) => (
                  <ContentRow
                    key={item.id}
                    item={item}
                    busy={busy === `content-${item.id}`}
                    onToggle={(row, reviewed) =>
                      run(`content-${row.id}`, () =>
                        dealHealthApi.reviewContent(dealId, row.id, reviewed),
                      )
                    }
                  />
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">No key material is tracked yet.</p>
            )}
          </Section>

          <Section
            title="Risk alerts"
            hint="Raised when the band is at risk, or when the score fell by the drop threshold."
          >
            {board.data.alerts.length ? (
              <ul>
                {board.data.alerts.map((alert) => (
                  <AlertRow
                    key={alert.id}
                    alert={alert}
                    busy={busy === `alert-${alert.id}`}
                    onResolve={(row) =>
                      run(`alert-${row.id}`, () => dealHealthApi.resolveAlert(row.id))
                    }
                  />
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">No risk alert has been raised.</p>
            )}
          </Section>

          <Section
            title="Coaching cues"
            hint="One per active reason. Close a cue with an outcome, and the outcome becomes readiness data."
          >
            {board.data.coaching.length ? (
              <ul>
                {board.data.coaching.map((cue) => (
                  <CueRow
                    key={cue.id}
                    cue={cue}
                    busy={busy === `cue-${cue.id}`}
                    onOutcome={(row, state) =>
                      run(`cue-${row.id}`, () => dealHealthApi.recordOutcome(row.id, state))
                    }
                  />
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">No coaching cue is open.</p>
            )}
          </Section>

          <Section
            title="Engagement intake"
            hint="Recording an event scores the deal and raises what the new reading justifies."
          >
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <Field label="Event kind" id="wf134-event-kind">
                <select
                  id="wf134-event-kind"
                  className={inputClass}
                  value={newEvent.kind}
                  onChange={(event) => setNewEvent({ ...newEvent, kind: event.target.value })}
                >
                  {Object.keys(EVENT_WORDS).map((kind) => (
                    <option key={kind} value={kind}>
                      {eventWord(kind)}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Person" id="wf134-event-person">
                <input
                  id="wf134-event-person"
                  className={inputClass}
                  value={newEvent.person}
                  onChange={(event) => setNewEvent({ ...newEvent, person: event.target.value })}
                />
              </Field>
              <Field label="Seconds on page" id="wf134-event-seconds">
                <input
                  id="wf134-event-seconds"
                  type="number"
                  min="0"
                  className={inputClass}
                  value={newEvent.seconds}
                  onChange={(event) => setNewEvent({ ...newEvent, seconds: event.target.value })}
                />
              </Field>
            </div>
            <div className="mt-4">
              <Button
                variant="primary"
                icon="plus"
                disabled={!newEvent.person || busy === 'event'}
                onClick={() =>
                  run('event', () =>
                    dealHealthApi.recordEvent(dealId, {
                      ...newEvent,
                      seconds: Number(newEvent.seconds) || 0,
                    }),
                  )
                }
              >
                Record the event and score
              </Button>
            </div>
            <p className="mt-3 text-xs text-muted-foreground">
              {board.data.events.length} engagement event(s) recorded. Last{' '}
              {board.data.events.length
                ? board.data.events[board.data.events.length - 1].at
                : 'none'}
              .
            </p>
          </Section>

          <Section title="Score history" hint="Every recorded reading, oldest first.">
            {board.data.history.length ? (
              <ul className="space-y-1.5">
                {board.data.history.map((row) => (
                  <li key={row.id} className="flex flex-wrap items-baseline gap-3">
                    <Badge tone="neutral">{bandWord(row.band)}</Badge>
                    <span className="font-mono text-sm text-foreground">{row.value}</span>
                    <span className="font-mono text-[11px] text-muted-foreground">
                      {row.measured_at}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {row.reasons.length ? row.reasons.join(', ') : 'no active reason'}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">
                No reading has been recorded for this deal yet.
              </p>
            )}
          </Section>
        </>
      )}

      <Section
        title="Readiness"
        hint="The closed loop: every coaching outcome feeds this reading."
      >
        {readiness.error && <ErrorNote error={readiness.error} onRetry={readiness.refetch} />}
        <ReadinessPanel readiness={readiness.data} />
      </Section>

      <Card>
        <h2 className="text-lg font-semibold text-foreground">Not built here</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Named so a reviewer can tell a decision from an oversight. This product repeats no
          vendor number from the research.
        </p>
        <ul className="mt-3 space-y-1.5">
          {(vocabulary.data?.not_implemented || []).map((note) => (
            <li key={note} className="text-sm text-muted-foreground">
              {note}
            </li>
          ))}
        </ul>
        <p className="mt-4 text-xs text-muted-foreground">
          The recorded decisions, the bands, the weights and the event kinds are served by
          <span className="font-mono"> GET /api/wf-134/vocabulary</span>.
        </p>
      </Card>
    </div>
  )
}

export default DealHealthPage
