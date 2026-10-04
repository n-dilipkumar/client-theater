import { useCallback, useMemo, useState } from 'react'

import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  JsonView,
  Spinner,
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'

import {
  AUTHENTICATION_OWNER,
  NOT_PROOF,
  PASSCODE_RULE,
  PHONE_RULE,
  PLACES,
  isAuthFactor,
  listRooms,
  methodLabel,
  placeLabel,
  verificationApi,
} from './api'
import { TWO_AXIS_ICON, VERIFICATION_ICON } from './icons'
import { GateMatrix, Notice, OutcomeBadge, Select } from './primitives'

/**
 * WF-078: require recipient identity verification before open or sign.
 *
 * The page has four jobs, in this order, and the order is the design.
 *
 * **Say what the gate proves before showing that it is on.** A pass records that the right
 * answer was given. It does not establish who is holding the phone, the knowledge-based
 * check compares what a recipient typed against what a sender recorded, and the ID check
 * does not read a document. A security page that opens with green ticks and no caveat is
 * the failure this workflow's own specification warns against, so the limitation is the
 * first thing rendered, above the fold, and the server sends the same sentence with every
 * response.
 *
 * **Show the two axes as two axes.** The specification's extensibility note says the method
 * is "a discriminated union on the recipient with an independent timing axis - the same
 * recipient object can be verified differently for viewing and signing". A page that listed
 * one method per recipient would render a shape the specification rules out, so each
 * recipient is a grid: a row per moment, and a recipient who carries both moments occupies
 * two of them. That is the sentence, made visible.
 *
 * **Show the audience next to every gate.** `before_open` is "All recipients" and
 * `before_sign` is "Signers only", and both come from the same sourced table. A seller who
 * sets a before-sign gate on a non-signer has configured a gate nobody can clear, so the
 * audience is a column rather than a footnote.
 *
 * **Show the failures.** The specification requires a rejected attempt to be "as visible as
 * a successful one", so the trail carries both and a failure names its reason. A board
 * holding only passes would misrepresent the control it is demonstrating.
 *
 * Every state this page can be in is rendered: loading, error, empty. A security page that
 * goes blank when the API is down looks like the gates failing, which is the one reading
 * that must never be possible.
 */

/** One attempt against one recipient, and the fields the method actually needs. */
function useAttempt() {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [fieldError, setFieldError] = useState(null)
  const [evidence, setEvidence] = useState({ passcode: '', code: '', answers: '', id_document: '' })

  const run = useCallback(async (recipientId, place, method) => {
    setBusy(true)
    setFieldError(null)
    try {
      const row = await verificationApi.attempt(
        recipientId,
        place,
        buildEvidence(method, evidence),
      )
      setResult(row)
      return { ok: true, row }
    } catch (error) {
      setFieldError(error.errors || { attempt: String(error.message || error) })
      setResult(null)
      return { ok: false }
    } finally {
      setBusy(false)
    }
  }, [evidence])

  const reset = useCallback(() => {
    setResult(null)
    setFieldError(null)
  }, [])

  const clearFieldError = useCallback(() => setFieldError(null), [])

  return { busy, result, fieldError, evidence, setEvidence, run, reset, clearFieldError }
}

/**
 * The evidence body for one method.
 *
 * Built from the gate's own method rather than sent whole, because a passcode field's
 * contents have no business being sent to a knowledge-based gate and vice versa. That is
 * the discriminated union doing its job at the wire boundary as well as in the store.
 *
 * ``method`` arrives from the gate the form is rendering, so a form built from a stale
 * recipient list cannot send a passcode to a knowledge-based gate.
 */
function buildEvidence(method, evidence) {
  if (method === 'sms') return { code: evidence.code.trim() }
  if (method === 'kba') return { answers: parseAnswers(evidence.answers) }
  if (method === 'id') return { id_document: parseAnswers(evidence.id_document) }
  return { passcode: evidence.passcode }
}

/**
 * `prompt: answer` per line, as an object.
 *
 * Keyed by prompt rather than by index, because the prompt is what the recipient is shown
 * and what they answer against. Indexing would let a reordering of the questions silently
 * change which answer belongs to which question.
 */
function parseAnswers(text) {
  const answers = {}
  for (const line of String(text || '').split('\n')) {
    const separator = line.indexOf(':')
    if (separator < 1) continue
    const prompt = line.slice(0, separator).trim()
    const answer = line.slice(separator + 1).trim()
    if (prompt) answers[prompt] = answer
  }
  return answers
}

function RecipientPanel({ recipient, places, onAttempted }) {
  // One attempt state per recipient, built here rather than once on the page. A shared
  // state would put one recipient's result banner inside every other recipient's form,
  // which reads as though the wrong recipient had been verified - and on a board whose
  // whole job is to say who has been verified and who has not, that is the one reading
  // that must not be possible.
  const attemptState = useAttempt()
  const gates = recipient.gates || []
  const gated = gates.length > 0
  const smsDeliveryOnly = gates.some((gate) => gate.method === 'sms' && gate.sms_type === 'delivery')

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-display text-lg font-semibold text-foreground">
            {recipient.name || recipient.email || recipient.id}
          </h2>
          <p className="mt-0.5 font-mono text-xs text-muted-foreground">{recipient.id}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={recipient.role === 'signer' ? 'update' : 'neutral'}>{recipient.role}</Badge>
          <Badge tone={gated ? 'insert' : 'neutral'}>
            {gated ? `${gates.length} gate${gates.length === 1 ? '' : 's'}` : 'No verification'}
          </Badge>
        </div>
      </div>

      {recipient.email && (
        <p className="mt-2 font-mono text-sm text-muted-foreground">{recipient.email}</p>
      )}

      {/* The two axes, drawn. One row per moment, so a recipient carrying both is two
          occupied rows rather than one field with a compound value. */}
      <div className="mt-4">
        <GateMatrix places={places} gates={gates} />
      </div>

      {smsDeliveryOnly && (
        <div className="mt-3">
          <Notice tone="info" title="This number delivers, it does not authenticate">
            One of the gates above uses a number for SMS delivery only. The specification
            separates authentication from delivery, so a delivery-only number carries the
            document and proves nothing on its own.
          </Notice>
        </div>
      )}

      {gated ? (
        <AttemptForm recipient={recipient} attemptState={attemptState} onDone={onAttempted} />
      ) : (
        <p className="mt-4 text-sm text-muted-foreground">
          This recipient carries no verification, so the document is released to them without
          a check. Most recipients in a room are in this state.
        </p>
      )}

      <p className="mt-4 border-t border-border-subtle pt-3 text-xs text-muted-foreground">
        {NOT_PROOF}
      </p>
    </Card>
  )
}

function AttemptForm({ recipient, attemptState, onDone }) {
  const [place, setPlace] = useState(recipient.gates[0]?.place)
  const gate = recipient.gates.find((entry) => entry.place === place)
  const { evidence, setEvidence, busy, result, fieldError, run, clearFieldError } = attemptState

  if (!gate) return null

  const submit = async () => {
    const outcome = await run(recipient.id, place, gate.method)
    if (outcome.ok) onDone()
  }

  return (
    <div className="mt-4 rounded-sm border border-border-subtle bg-muted p-4">
      <h3 className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        Run an attempt
      </h3>

      {recipient.gates.length > 1 && (
        <div className="mt-3">
          <Field
            label="Moment"
            id={`wf078-place-${recipient.id}`}
            hint="Each moment is checked on its own. A pass at one does not answer for the other."
          >
            <Select
              id={`wf078-place-${recipient.id}`}
              value={place}
              onChange={setPlace}
            >
              {recipient.gates.map((entry) => (
                <option key={entry.place} value={entry.place}>
                  {placeLabel(entry.place)}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      )}

      <div className="mt-3 space-y-3">
        {gate.method === 'passcode' && (
          <Field label="Passcode" id={`wf078-passcode-${recipient.id}`} hint={PASSCODE_RULE}>
            <input
              id={`wf078-passcode-${recipient.id}`}
              type="password"
              autoComplete="off"
              className={inputClass}
              value={evidence.passcode}
              onChange={(event) => setEvidence({ ...evidence, passcode: event.target.value })}
            />
          </Field>
        )}

        {gate.method === 'sms' && (
          <>
            <Field
              label="One-time code"
              id={`wf078-code-${recipient.id}`}
              hint={`${gate.sms_type_meaning || ''} Request a new code if this one has expired; the old one stops working.`}
            >
              <input
                id={`wf078-code-${recipient.id}`}
                inputMode="numeric"
                autoComplete="one-time-code"
                className={inputClass}
                value={evidence.code}
                onChange={(event) => setEvidence({ ...evidence, code: event.target.value })}
              />
            </Field>
            <div className="space-y-2">
              <Button
                icon="refresh"
                disabled={busy}
                onClick={() => verificationApi.sendCode(recipient.id)}
              >
                Send a new code
              </Button>
              {/* The E.164 bound, named on the screen where a seller picks the number. The
                  number is never echoed back, so the rule is the only thing a sender has
                  to go on. */}
              <p className="text-xs text-muted-foreground">{PHONE_RULE}</p>
            </div>
          </>
        )}

        {gate.method === 'kba' && (
          <Field
            label="Answers"
            id={`wf078-answers-${recipient.id}`}
            hint="One per line, written as the question, then a colon, then the answer."
          >
            <textarea
              id={`wf078-answers-${recipient.id}`}
              rows={4}
              className={inputClass}
              value={evidence.answers}
              onChange={(event) => setEvidence({ ...evidence, answers: event.target.value })}
            />
          </Field>
        )}

        {gate.method === 'id' && (
          <Field
            label="ID details"
            id={`wf078-id-${recipient.id}`}
            hint="The details the sender recorded, one per line as field: value. This build does not read a document."
          >
            <textarea
              id={`wf078-id-${recipient.id}`}
              rows={3}
              className={inputClass}
              value={evidence.id_document}
              onChange={(event) => setEvidence({ ...evidence, id_document: event.target.value })}
            />
          </Field>
        )}
      </div>

      {fieldError && (
        <div className="mt-3">
          <Notice tone="destructive" title="That attempt was not accepted" action={<Button onClick={clearFieldError}>Dismiss</Button>}>
            <ul className="list-disc space-y-0.5 pl-4">
              {Object.entries(fieldError).map(([field, message]) => (
                <li key={field}>
                  <span className="font-mono">{field}</span>: {message}
                </li>
              ))}
            </ul>
          </Notice>
        </div>
      )}

      {result && (
        <div className="mt-3">
          <Notice
            tone={result.outcome === 'pass' ? 'success' : 'destructive'}
            title={
              result.outcome === 'pass'
                ? 'The check cleared and the row is in the trail'
                : 'The check did not clear, and the row is in the trail anyway'
            }
          >
            <p className="flex flex-wrap items-center gap-2">
              <OutcomeBadge outcome={result.outcome} reason={result.reason} />
              {result.event_code !== null && result.event_code !== undefined && (
                <span className="font-mono text-xs">action code {result.event_code}</span>
              )}
            </p>
            <p className="mt-2 text-xs">{NOT_PROOF}</p>
          </Notice>
        </div>
      )}

      <div className="mt-4">
        <Button variant="primary" disabled={busy} onClick={submit}>
          {busy ? 'Checking' : 'Submit this attempt'}
        </Button>
      </div>
    </div>
  )
}

function AttemptTrail({ attempts }) {
  if (attempts.length === 0) {
    return (
      <EmptyState
        title="No verification attempts yet"
        description="Every attempt is recorded here, whether it passed or failed. A rejected attempt is as visible as a successful one."
      />
    )
  }

  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">Verification trail</h2>
      <p className="mt-2 text-sm text-muted-foreground">
        Every attempt, newest first. The action code is what a compliance review filters on,
        and a failure carries the same code a pass does, so a rejection cannot be filtered
        out of a query.
      </p>
      <ul className="mt-4 divide-y divide-border-subtle">
        {attempts.slice(0, 20).map((row) => (
          <li key={row.id} className="flex flex-wrap items-start justify-between gap-3 py-2">
            <span className="min-w-0">
              <span className="block font-mono text-xs text-muted-foreground">
                {row.place} / {methodLabel(row.method)}
              </span>
              <span className="mt-0.5 block font-mono text-xs text-muted-foreground">{row.at}</span>
              {row.sms_type && (
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  SMS role: {row.sms_type}
                  {isAuthFactor(row) ? ' (authentication factor)' : ' (not a factor)'}
                </span>
              )}
            </span>
            <span className="flex flex-wrap items-center gap-2">
              {row.event_code !== null && row.event_code !== undefined && (
                <span className="font-mono text-xs text-muted-foreground">code {row.event_code}</span>
              )}
              <OutcomeBadge outcome={row.outcome} reason={row.reason} />
            </span>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function VocabularyPanel({ vocabulary }) {
  const methods = vocabulary?.methods || []
  const sms = vocabulary?.sms
  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">
        What this workflow enforces
      </h2>
      <p className="mt-2 text-sm text-muted-foreground">
        Four methods and two moments, fetched from the server that validates them. The
        vendor's own field name is beside each one, so a caller can tell a documented
        verification settings object from this room&apos;s.
      </p>

      <ul className="mt-4 divide-y divide-border-subtle">
        {methods.map((method) => (
          <li key={method.id} className="flex flex-wrap items-start justify-between gap-3 py-2">
            <span className="min-w-0">
              <span className="block text-sm font-medium text-foreground">{method.label}</span>
              <span className="mt-0.5 block text-xs text-muted-foreground">{method.description}</span>
              <span className="mt-0.5 block font-mono text-xs text-muted-foreground">
                {method.vendor_field}
              </span>
            </span>
            {method.needs_delivery && (
              <Badge tone="warning">Needs a code sent first</Badge>
            )}
          </li>
        ))}
      </ul>

      {sms?.types && (
        <div className="mt-4 border-t border-border-subtle pt-4">
          <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            An SMS number can be three things
          </p>
          <ul className="mt-2 space-y-1.5">
            {sms.types.map((entry) => (
              <li key={entry.id} className="text-sm text-muted-foreground">
                <span className="font-mono text-xs text-foreground">{entry.id}</span>: {entry.meaning}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-4 border-t border-border-subtle pt-4">
        <JsonView
          value={{
            passcode: vocabulary?.passcode,
            phone: vocabulary?.phone,
            code_table_owner: vocabulary?.code_table_owner,
            code_bands: vocabulary?.code_bands,
          }}
        />
      </div>
    </Card>
  )
}

function AssumptionPanel({ assumptions }) {
  const rows = assumptions?.assumptions || []
  if (rows.length === 0) return null
  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">
        What is assumed rather than sourced
      </h2>
      <p className="mt-2 text-sm text-muted-foreground">
        Three parts of this workflow are marked inferred by the specification itself. They
        are named here so a seller can discount the right part of an answer rather than
        assuming the whole thing is evidenced.
      </p>
      <ul className="mt-4 space-y-3">
        {rows.map((row) => (
          <li key={row.id} className="rounded-sm border border-border-subtle p-3">
            <p className="font-mono text-xs text-muted-foreground">{row.id}</p>
            <p className="mt-1 text-sm font-medium text-foreground">{row.question}</p>
            <p className="mt-1 text-sm text-muted-foreground">{row.rejected_because}</p>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function DecisionPanel({ decisions }) {
  const rows = decisions?.decisions || []
  if (rows.length === 0) return null
  const ownership = rows.find((row) => row.id === 'OWNERSHIP_WF078_OWNS_THE_GATE')
  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">
        Decisions this workflow derived
      </h2>
      {ownership && (
        <div className="mt-3">
          <Notice tone="info" title="Which workflow owns the setting">
            <p>
              The e-signature workflow and this one both describe a recipient verification
              object. This workflow owns the setting, the two axes, the validation and the
              withholding; the other reads it. Jev chose this over three alternatives at
              confidence 0.93, audit {ownership.jev_audit_id}.
            </p>
          </Notice>
        </div>
      )}
      <ul className="mt-4 space-y-3">
        {rows.map((row) => (
          <li key={row.id} className="rounded-sm border border-border-subtle p-3">
            <p className="font-mono text-xs text-muted-foreground">{row.id}</p>
            <p className="mt-1 text-sm font-medium text-foreground">{row.question}</p>
            {row.rejected_because && (
              <p className="mt-1 text-sm text-muted-foreground">{row.rejected_because}</p>
            )}
          </li>
        ))}
      </ul>
    </Card>
  )
}

function VerificationPage() {
  const [roomId, setRoomId] = useState('')

  const rooms = useAsync(() => listRooms(), [])
  const vocabulary = useAsync(() => verificationApi.vocabulary(), [])
  const board = useAsync(() => verificationApi.summary(roomId), [roomId])
  const recipients = useAsync(() => verificationApi.recipients(roomId), [roomId])
  const attempts = useAsync(() => verificationApi.attempts(roomId), [roomId])
  const decisions = useAsync(() => verificationApi.decisions(), [])
  const assumptions = useAsync(() => verificationApi.assumptions(), [])

  const servedPlaces = useMemo(() => {
    const served = vocabulary.data?.places
    return Array.isArray(served) && served.length > 0 ? served : PLACES
  }, [vocabulary.data])

  const onAttempted = useCallback(() => {
    // The gate state lives on the server, so the page refetches rather than patching local
    // state. A security panel that shows a switch in a position the store is not in is the
    // one failure that must not be possible.
    attempts.refetch()
    board.refetch()
    recipients.refetch()
  }, [attempts, board, recipients])

  if (vocabulary.loading || rooms.loading) return <Spinner label="Loading recipient verification" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (recipients.error) return <ErrorNote error={recipients.error} onRetry={recipients.refetch} />

  const rows = recipients.data?.recipients || []
  const vocab = vocabulary.data

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-semibold text-foreground">
          Recipient verification
        </h1>
        <p className="mt-2 max-w-3xl text-[15px] text-muted-foreground">
          A verification on a recipient, and the moment it is demanded: before the recipient
          can view the document, or before they can sign it. Four methods, as a
          discriminated union: a typed passcode, an SMS one-time password,
          knowledge-based questions and a government-issued ID check. The document body is
          withheld until the check clears, and every attempt is written to the trail.
        </p>
      </header>

      {/* The limitation first, above the fold. A pass records that the right answer was
          given, and the cheapest way to keep that true is to make it part of the page
          rather than a line of copy somebody can delete. */}
      <Notice tone="warning" title="What a pass does and does not prove">
        <p>{vocab?.limitation}</p>
        <p className="mt-2">{NOT_PROOF}</p>
      </Notice>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Gated recipients"
          value={board.data?.gated_recipients ?? 0}
          hint={`${board.data?.recipients ?? 0} in total`}
          icon="audit"
        />
        <StatCard
          label="Before open"
          value={board.data?.before_open_recipients ?? 0}
          hint="all recipients"
          icon="schema"
        />
        <StatCard
          label="Before sign"
          value={board.data?.before_sign_recipients ?? 0}
          hint="signers only"
          icon="database"
        />
        <StatCard
          label="Failed attempts"
          value={board.data?.attempts_failed ?? 0}
          hint={`${board.data?.attempts ?? 0} in total`}
          icon="search"
        />
      </div>

      {/* The two-axis count is the specification's central claim, so it gets its own line
          rather than living inside a tooltip. */}
      {board.data?.two_axis_recipients > 0 && (
        <Notice tone="info" title="Some recipients are verified differently at each moment">
          {board.data.two_axis_recipients} recipient(s) carry a gate at both moments. The
          specification&apos;s extensibility note says the same recipient object can be
          verified differently for viewing and signing, and those are the rows where that is
          visible.
        </Notice>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Room" id="wf078-room" hint="Leave empty to see every verified recipient.">
          <Select id="wf078-room" value={roomId} onChange={setRoomId}>
            <option value="">Every room</option>
            {(rooms.data?.records || rooms.data || []).map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </Select>
        </Field>
      </div>

      <section className="space-y-4">
        <h2 className="font-display text-lg font-semibold text-foreground">
          Recipients, gate by gate
        </h2>
        {recipients.loading ? (
          <Spinner label="Loading recipients" />
        ) : rows.length === 0 ? (
          <EmptyState
            title="No verified recipients yet"
            description="A recipient appears here once a document names one. Add a gate to it and it becomes verified before open, before sign, or both."
          />
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {rows.map((recipient) => (
              <RecipientPanel
                key={recipient.id}
                recipient={recipient}
                places={servedPlaces}
                onAttempted={onAttempted}
              />
            ))}
          </div>
        )}
      </section>

      {attempts.loading ? (
        <Spinner label="Loading the verification trail" />
      ) : attempts.error ? (
        <ErrorNote error={attempts.error} onRetry={attempts.refetch} />
      ) : (
        <AttemptTrail attempts={attempts.data?.attempts || []} />
      )}

      <VocabularyPanel vocabulary={vocab} />

      <Notice tone="info" title="Who owns the authentication decision">
        <p>{AUTHENTICATION_OWNER}</p>
      </Notice>

      <AssumptionPanel assumptions={assumptions.data} />
      <DecisionPanel decisions={decisions.data} />
    </div>
  )
}

export default {
  id: 'wf-078-require-recipient-identity-verification',
  label: 'Recipient verification',
  // The glyph is not in the shared PATHS map, so `iconPath` carries it and `icon` falls
  // back to the shared mark. `components/ui.jsx` is not edited.
  icon: 'audit',
  iconPath: VERIFICATION_ICON,
  order: 780,
  Component: VerificationPage,
}

export { TWO_AXIS_ICON }
