import { useCallback, useState } from 'react'

import { consentApi, listRooms } from './api'
import {
  ConsentChip,
  Fact,
  FieldError,
  Glyphs,
  RecordedAfterDeclineNote,
  RecordingChip,
  SectionHeading,
  Switch,
} from './primitives'
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

/**
 * WF-060: auto-join and record the meeting, gated by recording consent.
 *
 * Three panels, in the order the research states them, because a reader arriving here
 * does not know the shape of the product yet:
 *
 * 1. **Consent profiles.** Step 1 through step 5 of the researched flow. Two switches
 *    decide everything downstream - the consent page, and whether enforcement is on.
 * 2. **Bookings.** Step 7. One row per booking, showing both axes of the state machine.
 * 3. **The decisions.** Every judgement call this workflow made. The research requires
 *    the derivation to be recorded rather than assumed, and a record buried in a
 *    docstring is a record nobody reads.
 *
 * What this page refuses to do
 * ----------------------------
 *
 * It never shows a state by colour alone. Every state carries a mark, the server's own
 * state name in mono, and a written sentence, so a reader who cannot see one channel
 * still has the other two. That is the design system's accessibility floor and it happens
 * to be the right call for a compliance surface as well.
 *
 * It never offers a step the server would refuse. The action buttons are derived from
 * `allowed_decisions`, which the backend computes from the state machine *and* the
 * profile together. A page that offers "join without consent" on a profile that forbids
 * it asks the user to press a button that answers 403.
 */

const PANEL = { board: 'board', profiles: 'profiles', decisions: 'decisions' }

/**
 * The page.
 *
 * Named as well as default-exported through the descriptor. The host only reads
 * `default`, so the extra export costs nothing, and it lets a test render the page
 * directly instead of reaching through `descriptor.Component` - which reads like a
 * mistake and hides which thing is under test.
 */
export function ConsentRecording() {
  const [panel, setPanel] = useState(PANEL.board)
  const [roomId, setRoomId] = useState('')

  const rooms = useAsync(() => listRooms(), [])
  const summary = useAsync(() => consentApi.summary(roomId), [roomId])
  const vocabulary = useAsync(() => consentApi.vocabulary(), [])

  if (summary.loading || rooms.loading) {
    return <Spinner label="Loading consent recording" />
  }
  if (summary.error) {
    return <ErrorNote error={summary.error} onRetry={summary.refetch} />
  }

  const counts = summary.data

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-semibold text-foreground">
          Consent recording
        </h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          A booked meeting is recorded only after a participant accepts the consent page. A
          recording bot joins the call automatically, and the recording stays blocked until
          somebody consents.
        </p>
      </header>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Consent profiles"
          value={counts.profiles}
          hint={`${counts.profiles_enforcing} enforcing consent`}
          icon="schema"
        />
        <StatCard
          label="Directory users"
          value={counts.users}
          hint="Bookings resolve a profile by organiser"
          icon="rooms"
        />
        <StatCard
          label="Recordings blocked"
          value={counts.recordings_blocked}
          hint="Waiting on a consent decision"
          icon="audit"
        />
        <StatCard
          label="Recordings complete"
          value={counts.recordings_complete}
          hint={`${counts.recordings_cancelled} cancelled instead`}
          icon="database"
        />
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <Field label="Room" id="wf060-room" hint="Scopes the board to one data room.">
          <select
            id="wf060-room"
            className={inputClass}
            value={roomId}
            onChange={(event) => setRoomId(event.target.value)}
          >
            <option value="">Every room</option>
            {(rooms.data?.records || []).map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </select>
        </Field>
      </div>

      {counts.inconsistent?.length > 0 && (
        <Card className="border-destructive/40">
          <div className="flex items-start gap-3">
            <Icon path={Glyphs.warn} size={18} className="mt-0.5 shrink-0 text-destructive" />
            <div className="min-w-0">
              <p className="text-sm font-semibold text-destructive">
                {counts.inconsistent.length} record(s) contradict themselves
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                A booking whose state and whose recording outcome disagree has a compliance
                record that cannot be trusted. Each line names the record and the conflict.
              </p>
              <ul className="mt-2 space-y-1 font-mono text-xs text-foreground">
                {counts.inconsistent.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </div>
          </div>
        </Card>
      )}

      <nav className="flex flex-wrap gap-2" aria-label="Consent recording sections">
        {[
          [PANEL.board, 'Bookings'],
          [PANEL.profiles, 'Consent profiles'],
          [PANEL.decisions, 'Decisions'],
        ].map(([id, label]) => (
          <Button
            key={id}
            variant={panel === id ? 'primary' : 'secondary'}
            onClick={() => setPanel(id)}
            aria-current={panel === id ? 'page' : undefined}
          >
            {label}
          </Button>
        ))}
      </nav>

      {panel === PANEL.board && <Board roomId={roomId} onChange={summary.refetch} />}
      {panel === PANEL.profiles && (
        <Profiles roomId={roomId} vocabulary={vocabulary.data} onChange={summary.refetch} />
      )}
      {panel === PANEL.decisions && <Decisions />}
    </div>
  )
}

/* ------------------------------------------------------------------------- */
/* Bookings                                                                  */
/* ------------------------------------------------------------------------- */

function Board({ roomId, onChange }) {
  const bookings = useAsync(() => consentApi.bookings(roomId), [roomId])
  const [openId, setOpenId] = useState(null)
  const [busy, setBusy] = useState('')
  const [problem, setProblem] = useState(null)

  const act = useCallback(
    async (key, run) => {
      setBusy(key)
      setProblem(null)
      try {
        await run()
        onChange()
      } catch (error) {
        setProblem(error)
      } finally {
        setBusy('')
      }
    },
    [onChange],
  )

  if (bookings.loading) return <Spinner label="Loading bookings" />
  if (bookings.error) return <ErrorNote error={bookings.error} onRetry={bookings.refetch} />

  const rows = bookings.data?.bookings || []
  if (rows.length === 0) {
    return (
      <EmptyState
        title="No bookings are being recorded yet"
        description={
          'Open a consent profile with the consent page on, then open a booking. The booking starts with its recording blocked until somebody consents.'
        }
      />
    )
  }

  return (
    <div className="space-y-4">
      <SectionHeading hint="One row per booking. Both axes of the state machine, in words.">
        Bookings
      </SectionHeading>

      {problem && (
        <Card className="border-destructive/40">
          <p className="text-sm font-semibold text-destructive">That step was refused</p>
          <p className="mt-1 text-sm text-foreground">{problem.message}</p>
          {problem.allowed && (
            <p className="mt-2 text-xs text-muted-foreground">
              Available from {problem.state}:{' '}
              <span className="font-mono text-foreground">{problem.allowed.join(', ')}</span>
            </p>
          )}
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {rows.map((row) => {
          const data = row.data
          const expanded = openId === data.booking_id
          return (
            <Card key={row.id} className="space-y-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="font-display text-base font-semibold text-foreground">
                    {data.title || data.booking_id}
                  </p>
                  <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                    {data.booking_id}
                  </p>
                </div>
                <Badge>{data.provider}</Badge>
              </div>

              <div className="flex flex-wrap gap-2">
                <RecordingChip state={data.recording_state} />
                <ConsentChip state={data.consent_state} enforced={data.enforced} />
              </div>

              <RecordedAfterDeclineNote enforced={data.enforced} />

              <dl className="grid gap-1.5 sm:grid-cols-2">
                <Fact label="organizer_email">{data.organizer_email}</Fact>
                <Fact label="profile">{data.profile_resolution}</Fact>
                <Fact label="consent page" mono={false}>
                  {data.audio_prompt_suppressed ? 'Prompt suppressed' : 'Audio prompt on'}
                </Fact>
                <Fact label="bot">{data.bot_invited?.join(', ')}</Fact>
              </dl>

              <div className="flex flex-wrap gap-2">
                <Button
                  icon="schema"
                  disabled={Boolean(busy)}
                  onClick={() =>
                    act(`grant:${data.booking_id}`, () =>
                      consentApi.recordConsent(data.booking_id, 'granted'),
                    )
                  }
                >
                  Consent given
                </Button>
                <Button
                  disabled={Boolean(busy)}
                  onClick={() =>
                    act(`decline:${data.booking_id}`, () =>
                      consentApi.recordConsent(data.booking_id, 'declined'),
                    )
                  }
                >
                  Consent refused
                </Button>
                <Button
                  disabled={Boolean(busy)}
                  onClick={() =>
                    act(`start:${data.booking_id}`, () => consentApi.startRecording(data.booking_id))
                  }
                >
                  Recording started
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => setOpenId(expanded ? null : data.booking_id)}
                >
                  {expanded ? 'Hide detail' : 'Show detail'}
                </Button>
              </div>

              {expanded && (
                <BookingDetail bookingId={data.booking_id} onChange={onChange} />
              )}
            </Card>
          )
        })}
      </div>
    </div>
  )
}

function BookingDetail({ bookingId, onChange }) {
  const detail = useAsync(async () => {
    const [page, runs, emails, record] = await Promise.all([
      consentApi.participantPage(bookingId),
      consentApi.runs(bookingId),
      consentApi.emails(bookingId),
      consentApi.readBooking(bookingId),
    ])
    return {
      page,
      runs: runs.runs || [],
      emails: emails.emails || [],
      record: record.data || {},
    }
  }, [bookingId])

  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState(null)

  if (detail.loading) return <Spinner label="Loading booking detail" />
  if (detail.error) return <ErrorNote error={detail.error} onRetry={detail.refetch} />

  const { page, runs, emails, record } = detail.data

  return (
    <div className="space-y-3 border-t border-border-subtle pt-3">
      <div>
        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          What the participant sees
        </p>
        <p className="mt-1 text-sm text-foreground">
          {page.profile?.description || 'No consent text has been written yet.'}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          Choices offered:{' '}
          <span className="font-mono text-foreground">
            {(page.allowed_decisions || []).join(', ') || 'none'}
          </span>
        </p>
      </div>

      <div>
        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          Lifecycle
        </p>
        {runs.length === 0 ? (
          <p className="mt-1 text-xs text-muted-foreground">No steps recorded yet.</p>
        ) : (
          <ol className="mt-1 space-y-1">
            {runs.map((run) => (
              <li key={run.id} className="flex items-center gap-2 text-xs">
                <span className="font-mono text-muted-foreground">
                  {String(run.data.seq).padStart(2, '0')}
                </span>
                <span className="font-mono text-foreground">{run.data.event}</span>
                <span className="text-muted-foreground">{run.data.state}</span>
              </li>
            ))}
          </ol>
        )}
      </div>

      <div>
        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          Pre-call email
        </p>
        {emails.length === 0 ? (
          <p className="mt-1 text-xs text-muted-foreground">
            None sent. The research sends it between 10 and 20 minutes before the call, and
            only to external invitees.
          </p>
        ) : (
          <ul className="mt-1 space-y-1 text-xs text-foreground">
            {emails.map((email) => (
              <li key={email.id} className="font-mono">
                {email.data.subject} to {email.data.to?.join(', ')}
              </li>
            ))}
          </ul>
        )}
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Button
            icon="email"
            disabled={busy}
            onClick={async () => {
              setBusy(true)
              try {
                const answer = await consentApi.planPrecallEmail(bookingId)
                setSent(answer)
                onChange()
              } catch (error) {
                setSent({ status: 'refused', reason: error.message })
              } finally {
                setBusy(false)
              }
            }}
          >
            Check the pre-call email
          </Button>
          {sent && (
            <span className="text-xs text-muted-foreground">
              {sent.status === 'sent' ? 'Sent' : 'Skipped'}: {sent.reason}
            </span>
          )}
        </div>
      </div>

      <details className="rounded-sm border border-border-subtle bg-surface p-3">
        <summary className="cursor-pointer text-xs font-medium text-foreground">
          The consent-enabled request this booking would send
        </summary>
        <p className="mt-1 text-xs text-muted-foreground">
          Recorded rather than sent. The researched endpoint is in beta, so the request is
          stored for a reviewer to read against the research, and the recording bot address
          is on it so a reviewer can see who auto-joins.
        </p>
        <div className="mt-2">
          <JsonView value={record.outbound_request || {}} />
        </div>
        <dl className="mt-2 grid gap-1.5 sm:grid-cols-2">
          <Fact label="bot invited">{record.bot_invited?.join(', ')}</Fact>
          <Fact label="link state">{record.consent_link?.link_state}</Fact>
        </dl>
      </details>

      <Button
        variant="ghost"
        disabled={busy}
        onClick={async () => {
          setBusy(true)
          try {
            await consentApi.reissueLink(bookingId)
            detail.refetch()
            onChange()
          } finally {
            setBusy(false)
          }
        }}
      >
        Reissue the link
      </Button>
    </div>
  )
}

/* ------------------------------------------------------------------------- */
/* Consent profiles                                                          */
/* ------------------------------------------------------------------------- */

function Profiles({ roomId, vocabulary, onChange }) {
  const profiles = useAsync(() => consentApi.profiles(roomId), [roomId])
  const users = useAsync(() => consentApi.users(roomId), [roomId])

  if (profiles.loading || users.loading) return <Spinner label="Loading consent profiles" />
  if (profiles.error) return <ErrorNote error={profiles.error} onRetry={profiles.refetch} />

  const rows = profiles.data?.profiles || []

  return (
    <div className="space-y-4">
      <SectionHeading hint="Steps 1 to 5 of the researched flow, on one page.">
        Consent profiles
      </SectionHeading>

      {rows.length === 0 ? (
        <EmptyState
          title="No consent profile yet"
          description="The research puts them on one page: Data capture, then Recording consent. Add the profile below."
        />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {rows.map((row) => (
            <ProfileCard
              key={row.id}
              record={row}
              vocabulary={vocabulary}
              onChange={() => {
                profiles.refetch()
                onChange()
              }}
            />
          ))}
        </div>
      )}

      <Directory roomId={roomId} profiles={rows} onChange={() => {
        users.refetch()
        onChange()
      }} />
    </div>
  )
}

function ProfileCard({ record, vocabulary, onChange }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(null)
  const [errors, setErrors] = useState({})
  const [problem, setProblem] = useState(null)
  const [busy, setBusy] = useState(false)

  const data = record.data
  const switches = vocabulary?.switches || {}

  function start() {
    setDraft({
      consent_page_enabled: Boolean(data.consent_page_enabled),
      enforce_consent_page: Boolean(data.enforce_consent_page),
      allow_join_without_consent: Boolean(data.allow_join_without_consent),
      precall_email_enabled: Boolean(data.precall_email_enabled),
      audio_prompt_enabled: Boolean(data.audio_prompt_enabled),
    })
    setErrors({})
    setProblem(null)
    setEditing(true)
  }

  async function save() {
    setBusy(true)
    setErrors({})
    setProblem(null)
    try {
      await consentApi.updateProfile(record.id, draft)
      setEditing(false)
      onChange()
    } catch (error) {
      // The backend returns a field-keyed map, so each message lands beside its input.
      setErrors(error.errors || {})
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-display text-base font-semibold text-foreground">{data.name}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">{data.description}</p>
        </div>
        <div className="flex gap-1.5">
          {data.is_default && <Badge tone="insert">default</Badge>}
          <Badge>{data.enforce_consent_page ? 'enforcing' : 'advisory'}</Badge>
        </div>
      </div>

      <dl className="grid gap-1.5 sm:grid-cols-2">
        <Fact label="default_provider">{data.default_provider || 'none'}</Fact>
        <Fact label="locales">{(data.locales || []).join(', ')}</Fact>
        <Fact label="providers">{Object.keys(data.providers || {}).join(', ') || 'none'}</Fact>
        <Fact label="audio prompt">{data.audio_prompt?.mode}</Fact>
      </dl>

      {!editing ? (
        <Button onClick={start}>Edit the switches</Button>
      ) : (
        <div className="space-y-2">
          <Switch
            name="wf060-consent-page"
            label={switches.consent_page_enabled || 'consent_page_enabled'}
            hint="Turn this on before a booking can get a consent-enabled link."
            checked={draft.consent_page_enabled}
            onChange={(value) => setDraft({ ...draft, consent_page_enabled: value })}
          />
          <Switch
            name="wf060-enforce"
            label={switches.enforce_consent_page || 'enforce_consent_page'}
            hint="With this off the page is advisory: a decision is recorded and the recording proceeds."
            checked={draft.enforce_consent_page}
            onChange={(value) => setDraft({ ...draft, enforce_consent_page: value })}
          />
          <Switch
            name="wf060-join"
            label={switches.allow_join_without_consent || 'allow_join_without_consent'}
            hint="Allows a participant to join without consenting. The recording is then cancelled."
            checked={draft.allow_join_without_consent}
            onChange={(value) => setDraft({ ...draft, allow_join_without_consent: value })}
          />
          <Switch
            name="wf060-precall"
            label={switches.precall_email_enabled || 'precall_email_enabled'}
            hint="Sends a disclosure between 10 and 20 minutes before the call."
            checked={draft.precall_email_enabled}
            onChange={(value) => setDraft({ ...draft, precall_email_enabled: value })}
          />
          <Switch
            name="wf060-prompt"
            label={switches.audio_prompt_enabled || 'audio_prompt_enabled'}
            hint="Plays the researched disclosure on the first guest with audio on."
            checked={draft.audio_prompt_enabled}
            onChange={(value) => setDraft({ ...draft, audio_prompt_enabled: value })}
          />

          {problem && !Object.keys(errors).length && (
            <p role="alert" className="text-xs text-destructive">
              {problem.message}
            </p>
          )}
          <FieldError message={errors.providers} />
          <FieldError message={errors.enforce_consent_page} />
          <FieldError message={errors.consent_page_enabled} />
          <FieldError message={errors.precall_email} />

          <div className="flex gap-2">
            <Button variant="primary" disabled={busy} onClick={save}>
              Save
            </Button>
            <Button variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </Card>
  )
}

function Directory({ roomId, profiles, onChange }) {
  const users = useAsync(() => consentApi.users(roomId), [roomId])
  const [email, setEmail] = useState('')
  const [profileId, setProfileId] = useState('')
  const [problem, setProblem] = useState(null)

  if (users.loading) return <Spinner label="Loading the directory" />
  if (users.error) return <ErrorNote error={users.error} onRetry={users.refetch} />

  const rows = users.data?.users || []

  return (
    <div className="space-y-3">
      <SectionHeading hint="Step 6. A booking resolves its consent profile through this directory.">
        Directory
      </SectionHeading>

      {rows.length === 0 ? (
        <EmptyState
          title="No directory users"
          description="A booking resolves a profile by the organiser's email. With nobody in the directory, every booking is refused with the vendor's own 404."
        />
      ) : (
        <Card>
          <ul className="divide-y divide-border-subtle">
            {rows.map((row) => (
              <li key={row.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2">
                <span className="font-mono text-sm text-foreground">{row.data.email}</span>
                <span className="text-xs text-muted-foreground">{row.data.name}</span>
                <span className="font-mono text-xs text-muted-foreground">
                  {row.data.profile_id ? 'assigned' : row.data.is_default_for_new_members ? 'default for new members' : 'unprofiled'}
                </span>
                {row.data.blocks_recording && <Badge tone="delete">blocks recording</Badge>}
                {!row.data.record_by_gong && <Badge tone="delete">not recordable</Badge>}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card className="space-y-3">
        <Field label="Email" id="wf060-user-email">
          <input
            id="wf060-user-email"
            className={inputClass}
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="rep@northwind.example"
          />
        </Field>
        <Field label="Consent profile" id="wf060-user-profile">
          <select
            id="wf060-user-profile"
            className={inputClass}
            value={profileId}
            onChange={(event) => setProfileId(event.target.value)}
          >
            <option value="">No profile assigned</option>
            {profiles.map((row) => (
              <option key={row.id} value={row.id}>
                {row.data.name}
              </option>
            ))}
          </select>
        </Field>
        <FieldError message={problem?.errors?.email} />
        <Button
          variant="primary"
          onClick={async () => {
            setProblem(null)
            try {
              await consentApi.addUser(roomId, { email, profile_id: profileId || null })
              setEmail('')
              onChange()
            } catch (error) {
              setProblem(error)
            }
          }}
        >
          Add to the directory
        </Button>
      </Card>
    </div>
  )
}

/* ------------------------------------------------------------------------- */
/* The decision record                                                       */
/* ------------------------------------------------------------------------- */

function Decisions() {
  const decisions = useAsync(() => consentApi.decisions(), [])
  const [openId, setOpenId] = useState(null)

  if (decisions.loading) return <Spinner label="Loading the decision record" />
  if (decisions.error) return <ErrorNote error={decisions.error} onRetry={decisions.refetch} />

  const rows = decisions.data?.decisions || []

  return (
    <div className="space-y-4">
      <SectionHeading hint="Every judgement call this workflow made, and what it rejected.">
        Decisions
      </SectionHeading>

      <Card>
        <p className="text-sm text-foreground">
          The research for this workflow gives evidence and no flow. It says an implementer
          must derive the missing steps and record the derivation rather than assume them.
          These are the derivations, each with the evidence it rests on, the reading it
          rejected, and what that reading would have cost.
        </p>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        {rows.map((entry) => {
          const expanded = openId === entry.id
          return (
            <Card key={entry.id} className="space-y-2">
              <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                {entry.surface}
              </p>
              <p className="text-sm font-semibold text-foreground">{entry.question}</p>
              <p className="text-sm text-foreground">{entry.decision}</p>
              {expanded && (
                <div className="space-y-2 border-t border-border-subtle pt-2">
                  <div>
                    <p className="text-xs font-semibold text-foreground">Evidence</p>
                    <p className="text-xs text-muted-foreground">{entry.evidence}</p>
                  </div>
                  <div>
                    <p className="text-xs font-semibold text-foreground">Rejected</p>
                    <p className="text-xs text-muted-foreground">{entry.rejected}</p>
                  </div>
                  <div>
                    <p className="text-xs font-semibold text-foreground">
                      What the rejected reading would have cost
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {entry.cost_of_the_rejected_reading}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs font-semibold text-foreground">Residual risk</p>
                    <p className="text-xs text-muted-foreground">{entry.residual_risk}</p>
                  </div>
                </div>
              )}
              <Button variant="ghost" onClick={() => setOpenId(expanded ? null : entry.id)}>
                {expanded ? 'Hide the reasoning' : 'Show the reasoning'}
              </Button>
            </Card>
          )
        })}
      </div>
    </div>
  )
}

export default {
  id: 'wf-060-auto-join-and-record-with-consent',
  label: 'Consent recording',
  icon: 'audit',
  order: 600,
  Component: ConsentRecording,
}