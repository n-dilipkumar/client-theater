/**
 * Round robin booking (WF-054).
 *
 * One page, four sections, in the order the researched flow runs in:
 *
 * 1. Teams, and who on each team can actually be assigned a booking.
 * 2. Distributions, each with its mode and its credit ledger.
 * 3. An evaluation, showing the single combined window and who was chosen.
 * 4. Bookings and the credits that moved, including a no-show credit-back.
 *
 * Two things this page does not do, both because the research forbids them:
 * it does not offer an unlicensed member as a choice, and it does not treat a
 * distribution's calendar combination as settled fact. The second is served from
 * the `/inferences` endpoint with the quote that fixes it, so a reviewer can
 * disagree with the derivation on screen rather than in a diff.
 *
 * Design floor: every control is `min-h-11`, every glyph sits beside a text
 * label, every status is carried by words rather than by colour alone, and the
 * loading, error and empty branches are all present.
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
import { roundRobinApi } from './api'
import { Fact, MemberRow, ModeChip, Notice, Quote, Subhead } from './primitives'

function formatTime(value) {
  if (!value) return '--'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value)
  return parsed.toISOString().replace('T', ' ').slice(0, 16)
}

export default function RoundRobinBooking() {
  const [roomId, setRoomId] = useState('')
  const [selected, setSelected] = useState(null)
  const [evaluation, setEvaluation] = useState(null)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState(null)

  const summary = useAsync(() => roundRobinApi.summary(), [])
  const catalog = useAsync(() => roundRobinApi.catalog(), [])
  const vocabulary = useAsync(() => roundRobinApi.vocabulary(), [])
  const inferences = useAsync(() => roundRobinApi.inferences(), [])
  const bookings = useAsync(() => roundRobinApi.bookings({ limit: 50 }), [])
  const credits = useAsync(() => roundRobinApi.credits({ limit: 50 }), [])

  const reloadAll = () => {
    summary.refetch()
    catalog.refetch()
    bookings.refetch()
    credits.refetch()
  }

  if (summary.loading || catalog.loading) {
    return <Spinner label="Loading round robin teams and distributions" />
  }
  if (summary.error) return <ErrorNote error={summary.error} onRetry={summary.refetch} />
  if (catalog.error) return <ErrorNote error={catalog.error} onRetry={catalog.refetch} />

  const counts = summary.data || {}
  const teams = catalog.data?.teams || []
  const distributions = catalog.data?.distributions || []
  const distribution = distributions.find((entry) => entry.id === selected) || null
  const members = distribution
    ? (teams.find((team) => team.id === distribution.data.team_ref)?.members || [])
    : []
  const windowMinutes = distribution?.data?.interval
    ? Math.round(
        (new Date(distribution.data.interval.end) - new Date(distribution.data.interval.start)) /
          60000,
      )
    : 0

  /**
   * Run the distribution without writing anything, then refresh what changed.
   *
   * `check` is the read-only half of the researched `init-simple`: same
   * selection and same calendar arithmetic, no routing session and no credit
   * consumed. So the counts are refreshed afterwards rather than written to,
   * because nothing was created.
   */
  async function evaluate() {
    if (!distribution || !roomId) {
      setActionError('Choose a distribution and a room before evaluating.')
      return
    }
    setBusy(true)
    setActionError(null)
    try {
      const result = await roundRobinApi.check(roomId, { distribution_id: distribution.id })
      setEvaluation(result)
      reloadAll()
    } catch (error) {
      setActionError(error.message || String(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-semibold text-foreground">Round robin booking</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          A distribution rotates bookings across a team. Strict gives equal turns. Flexible weights
          each member by how much of the shared window they hold.
        </p>
      </header>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Teams" value={counts.teams ?? 0} icon="rooms" />
        <StatCard label="Distributions" value={counts.distributions ?? 0} icon="audit" />
        <StatCard label="Bookings" value={counts.bookings ?? 0} icon="dashboard" />
        <StatCard label="No-shows" value={counts.no_shows ?? 0} icon="search" />
      </section>

      {counts.excluded_members > 0 && (
        <Notice tone="warning">
          <span className="font-medium">{counts.excluded_members} member(s) cannot be assigned.</span>{' '}
          The researched licensing rule is a hard gate: an unlicensed member routes to the Not
          Scheduled path rather than being warned about.
        </Notice>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <Subhead count={teams.length}>Teams</Subhead>
          {teams.length === 0 ? (
            <EmptyState
              title="No teams yet"
              description="A team is the membership list a distribution rotates through."
            />
          ) : (
            <ul className="divide-y divide-border-subtle">
              {teams.map((team) => (
                <li key={team.id} className="py-2">
                  <p className="text-sm font-medium text-foreground">{team.data.name}</p>
                  <p className="font-mono text-xs text-muted-foreground">{team.id}</p>
                  <div className="mt-2">
                    <ul>
                      {team.members.map((member) => (
                        <MemberRow
                          key={member.member_id}
                          member={member}
                          freeMinutes={undefined}
                          windowMinutes={0}
                        />
                      ))}
                    </ul>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <Subhead count={distributions.length}>Distributions</Subhead>
          {distributions.length === 0 ? (
            <EmptyState
              title="No distributions yet"
              description="A distribution names a team, a mode, and the weights and credits the rotation reads."
            />
          ) : (
            <ul className="space-y-2">
              {distributions.map((entry) => (
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
                      <ModeChip mode={entry.data.mode} />
                    </span>
                    <span className="mt-1 block font-mono text-xs text-muted-foreground">
                      {entry.credit_totals.consumed} consumed, {entry.credit_totals.returned} returned
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {distribution && (
        <Card>
          <Subhead>Selected distribution</Subhead>
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <ModeChip mode={distribution.data.mode} />
              {distribution.data.credit_back_on_no_show ? (
                <Badge tone="update">credits back on no-show</Badge>
              ) : (
                <Badge tone="neutral">no credit-back on no-show</Badge>
              )}
              <Badge tone="neutral">
                cycle {distribution.data.cycle}, cursor {distribution.data.cursor}
              </Badge>
            </div>

            <div className="grid gap-2 sm:grid-cols-2">
              <Fact label="team">{distribution.data.team_name}</Fact>
              <Fact label="rule">{distribution.eligibility.eligible} of {distribution.eligibility.members} eligible</Fact>
              <Fact label="window">
                {formatTime(distribution.data.interval.start)} to {formatTime(distribution.data.interval.end)}
              </Fact>
              <Fact label="meeting">
                {distribution.data.interval.duration_minutes} min
              </Fact>
            </div>

            {members.length > 0 && (
              <div>
                <Subhead count={members.length}>Members</Subhead>
                <ul>
                  {members.map((member) => {
                    const weight = distribution.member_weights?.find(
                      (row) => row.member_id === member.member_id,
                    )
                    return (
                      <MemberRow
                        key={member.member_id}
                        member={{ ...member, eligible: weight?.eligible ?? member.eligible }}
                        freeMinutes={weight?.free_minutes}
                        windowMinutes={windowMinutes}
                      />
                    )
                  })}
                </ul>
              </div>
            )}

            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Room id">
                <input
                  className={inputClass}
                  value={roomId}
                  onChange={(event) => setRoomId(event.target.value)}
                  placeholder="room-1"
                />
              </Field>
              <div className="flex items-end">
                <Button variant="primary" onClick={evaluate} disabled={busy} className="min-h-11 w-full">
                  {busy ? 'Evaluating' : 'Evaluate the distribution'}
                </Button>
              </div>
            </div>

            {actionError && <ErrorNote error={actionError} onRetry={evaluate} />}

            {evaluation && (
              <div className="space-y-2 rounded-sm border border-border-subtle p-3">
                <Subhead>Preview</Subhead>
                {evaluation.outcome === 'allocation' && evaluation.chosen ? (
                  <>
                    <div className="grid gap-2 sm:grid-cols-2">
                      <Fact label="reaches">{evaluation.chosen.name || evaluation.chosen.member_id}</Fact>
                      <Fact label="by">{evaluation.chosen.rule}</Fact>
                      <Fact label="slots">{evaluation.window.slot_count}</Fact>
                      <Fact label="first">{formatTime(evaluation.window?.slots?.[0]?.start_at)}</Fact>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      The window is a union: a slot is on offer when at least one licensed member is
                      free for its whole length. Booking re-checks that this member is still free then.
                    </p>
                  </>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    Nobody on this team is free in the window, so no slots are on offer. That is the
                    Not Scheduled path, not an error.
                  </p>
                )}
              </div>
            )}
          </div>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <Subhead count={bookings.data?.count ?? 0}>Bookings</Subhead>
          {bookings.loading ? (
            <Spinner label="Loading bookings" />
          ) : bookings.error ? (
            <ErrorNote error={bookings.error} onRetry={bookings.refetch} />
          ) : (bookings.data?.bookings || []).length === 0 ? (
            <EmptyState
              title="No bookings yet"
              description="Evaluate a distribution, then book one of the offered slots."
            />
          ) : (
            <ul className="space-y-2">
              {bookings.data.bookings.map((booking) => (
                <li
                  key={booking.id}
                  className="flex min-h-11 items-center justify-between gap-3 rounded-sm border border-border-subtle px-3 py-2"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm text-foreground">
                      {booking.data.member_name || booking.data.member_id}
                    </p>
                    <p className="truncate font-mono text-xs text-muted-foreground">
                      {formatTime(booking.data.start_at)}
                    </p>
                  </div>
                  <Badge tone={booking.data.status === 'confirmed' ? 'insert' : 'restore'}>
                    {booking.data.status}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <Subhead count={credits.data?.count ?? 0}>Credit movements</Subhead>
          {credits.loading ? (
            <Spinner label="Loading credits" />
          ) : credits.error ? (
            <ErrorNote error={credits.error} onRetry={credits.refetch} />
          ) : (credits.data?.movements || []).length === 0 ? (
            <EmptyState
              title="No credits have moved"
              description="A booking consumes one credit. A no-show credited back returns it."
            />
          ) : (
            <ul className="space-y-2">
              {credits.data.movements.map((movement) => (
                <li
                  key={movement.id}
                  className="flex min-h-11 items-center justify-between gap-3 border-b border-border-subtle py-2 last:border-b-0"
                >
                  <div className="min-w-0">
                    <p className="truncate font-mono text-xs text-foreground">
                      {movement.data.member_id}
                    </p>
                    <p className="truncate font-mono text-xs text-muted-foreground">
                      {movement.data.booking_ref}
                    </p>
                  </div>
                  <Badge tone={movement.data.direction === 'returned' ? 'restore' : 'insert'}>
                    {movement.data.direction === 'returned' ? 'credited back' : 'consumed'}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card>
        <Subhead>How the window is built</Subhead>
        <div className="space-y-3">
          <Quote source={vocabulary.data?.calendar_combination?.source}>
            {vocabulary.data?.calendar_combination?.offered_when ||
              'An instant is on offer when at least one licensed member is free for the whole slot.'}
          </Quote>
          <p className="text-sm text-muted-foreground">
            The research names union and intersection without saying which belongs to which mode. This
            build uses a union for both and re-checks the chosen member at booking time.
          </p>
          {(inferences.data?.inferences || [])
            .filter((entry) => entry.id === 'inference_calendar_combination')
            .map((entry) => (
              <dl key={entry.id} className="space-y-1 rounded-sm border border-border-subtle p-3">
                <Subhead>Why</Subhead>
                <p className="text-sm text-muted-foreground">{entry.why}</p>
                <Subhead>Change it by</Subhead>
                <p className="text-sm text-muted-foreground">{entry.change_if}</p>
                <Subhead>Ratified by Jev</Subhead>
                <Fact label="audit">
                  {entry.jev_audit_id} ({entry.jev_verdict})
                </Fact>
              </dl>
            ))}
        </div>
      </Card>
    </div>
  )
}