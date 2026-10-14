/**
 * WF-135's page: capture the deal conversation into the CRM through one entry.
 *
 * What the page shows, and why
 * ----------------------------
 * The researched flow is one entry and one fan-out. So the page records a
 * conversation once, then shows the four extraction outputs, the MEDDICC coverage
 * across the room and the three destinations the entry reached, each with the
 * outcome a caller reported. Agreement is a word, never a colour alone: a capture
 * either agrees across the room's notes, the handoff packet and the CRM custom
 * fields, or it is partial and says which destination failed.
 *
 * Transcription and extraction are not built here (see the module docstring of
 * `dsr.competitive_baseline.conversation_intelligence`), so the rep pastes the
 * four outputs. The page says so rather than implying the product heard the call.
 *
 * Every list is fetched from the server; the page counts nothing it was not sent,
 * so a section the server withheld cannot be reconstructed by the UI.
 */

import { useState } from 'react'

import { Button, Card, EmptyState, ErrorNote, Field, Spinner, StatCard, inputClass, useAsync } from '@/components/ui'

import {
  CRM_OUTCOME_WORDS,
  DESTINATION_NOTES,
  DESTINATION_ORDER,
  DESTINATION_WORDS,
  REPORTABLE_OUTCOMES,
  SOURCE_WORDS,
  conflictText,
  conversationApi,
  fieldError,
  listRooms,
} from './api'
import { AgreementBadge, LetterBadge, Notice, OutcomeBadge, StateBadge } from './primitives'

const MEDDICC_FIELDS = [
  { key: 'metrics', letter: 'M', label: 'Metrics' },
  { key: 'economic_buyer', letter: 'E', label: 'Economic buyer' },
  { key: 'decision_criteria', letter: 'D', label: 'Decision criteria' },
  { key: 'decision_process', letter: 'D', label: 'Decision process' },
  { key: 'identify_pain', letter: 'I', label: 'Identify pain' },
  { key: 'champion', letter: 'C', label: 'Champion' },
  { key: 'competition', letter: 'C', label: 'Competition' },
]

/** One item per line; "text | owner" carries an owner without a second control. */
function parseLines(raw) {
  return raw
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [text, owner] = line.split('|').map((part) => part.trim())
      return owner ? { text, owner } : text
    })
}

function textOf(item) {
  if (typeof item === 'string') return item
  if (item && typeof item === 'object') return item.text || JSON.stringify(item)
  return String(item)
}

function DestinationRow({ destination, onReport }) {
  const kind = destination.kind
  const outcomes = REPORTABLE_OUTCOMES[kind] || ['written', 'failed']
  const [outcome, setOutcome] = useState(outcomes[0])
  const label = DESTINATION_WORDS[kind] || kind
  const words = kind === 'crm_field' ? CRM_OUTCOME_WORDS : { written: 'Written', failed: 'Failed' }

  return (
    <li className="rounded-sm border border-border-subtle p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-foreground">{label}</span>
          <StateBadge state={destination.state} />
          {kind === 'crm_field' && <OutcomeBadge outcome={destination.crm_state} />}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-xs text-muted-foreground" htmlFor={`outcome-${destination.id}`}>
            {`Outcome for ${label}`}
          </label>
          <select
            id={`outcome-${destination.id}`}
            aria-label={`Outcome for ${label}`}
            className={`${inputClass} w-auto`}
            value={outcome}
            onChange={(event) => setOutcome(event.target.value)}
          >
            {outcomes.map((name) => (
              <option key={name} value={name}>
                {words[name] || name}
              </option>
            ))}
          </select>
          <Button onClick={() => onReport(destination.id, outcome)}>Report</Button>
        </div>
      </div>
      <p className="mt-1.5 text-xs text-muted-foreground">{DESTINATION_NOTES[kind]}</p>
    </li>
  )
}

function CaptureCard({ capture, onReport }) {
  const extraction = capture.extraction || {}
  const coverage = capture.meddicc_coverage || { covered_letters: [], missing_letters: [] }
  const covered = new Set(coverage.covered_letters || [])
  const destinations = [...(capture.destinations || [])].sort(
    (a, b) => DESTINATION_ORDER.indexOf(a.kind) - DESTINATION_ORDER.indexOf(b.kind),
  )

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-mono text-xs text-muted-foreground">
            {`${SOURCE_WORDS[capture.source_kind] || capture.source_kind} · ${capture.occurred_at || ''}`}
          </p>
          <h3 className="mt-1 text-base font-semibold text-foreground">{extraction.summary}</h3>
        </div>
        <AgreementBadge agreement={capture.agreement} />
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            Next steps
          </p>
          <ul className="mt-1.5 space-y-1 text-sm text-foreground">
            {(extraction.next_steps || []).map((item, index) => (
              <li key={index}>{textOf(item)}</li>
            ))}
            {!(extraction.next_steps || []).length && (
              <li className="text-muted-foreground">None captured.</li>
            )}
          </ul>
        </div>
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            Requirements
          </p>
          <ul className="mt-1.5 space-y-1 text-sm text-foreground">
            {(extraction.requirements || []).map((item, index) => (
              <li key={index}>{textOf(item)}</li>
            ))}
            {!(extraction.requirements || []).length && (
              <li className="text-muted-foreground">None captured.</li>
            )}
          </ul>
        </div>
      </div>

      <div className="mt-4">
        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          Qualification evidence (MEDDICC)
        </p>
        <div className="mt-1.5 flex flex-wrap gap-2">
          {MEDDICC_FIELDS.map((letter) => (
            <LetterBadge key={letter.key} row={{ ...letter, covered: covered.has(letter.key) }} />
          ))}
        </div>
      </div>

      {capture.plan_ref && (
        <p className="mt-3 text-xs text-muted-foreground">
          Mutual Action Plan reference: <span className="font-mono">{capture.plan_ref}</span>
        </p>
      )}

      <div className="mt-4">
        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          Destinations this entry reached
        </p>
        <ul className="mt-1.5 space-y-2">
          {destinations.map((destination) => (
            <DestinationRow
              key={destination.id}
              destination={destination}
              onReport={onReport}
            />
          ))}
        </ul>
      </div>
    </Card>
  )
}

function RecordPanel({ roomId, onRecorded }) {
  const [summary, setSummary] = useState('')
  const [nextSteps, setNextSteps] = useState('')
  const [requirements, setRequirements] = useState('')
  const [planRef, setPlanRef] = useState('')
  const [meddicc, setMeddicc] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    const evidence = Object.fromEntries(
      Object.entries(meddicc).filter(([, value]) => String(value || '').trim()),
    )
    try {
      await conversationApi.record({
        room_id: roomId,
        summary,
        next_steps: parseLines(nextSteps),
        requirements: parseLines(requirements),
        plan_ref: planRef.trim() || undefined,
        meddicc: evidence,
      })
      setSummary('')
      setNextSteps('')
      setRequirements('')
      setPlanRef('')
      setMeddicc({})
      onRecorded()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <h2 className="text-lg font-semibold text-foreground">Record a conversation</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        Transcription and extraction run outside this product, so paste the four outputs. One entry
        fans them out to the room&apos;s notes, the handoff packet and the CRM custom fields.
      </p>
      <form className="mt-4 space-y-4" onSubmit={submit}>
        {error && (
          <Notice tone="destructive" title={error.reason ? 'That change was refused' : 'Could not record'}>
            {error.reason ? conflictText(error.reason) : String(error.message || error)}
          </Notice>
        )}
        <Field
          label="Summary"
          id="wf135-summary"
          hint={fieldError(error, 'summary') || 'The one-line read of the conversation.'}
        >
          <textarea
            id="wf135-summary"
            className={inputClass}
            rows={2}
            value={summary}
            onChange={(event) => setSummary(event.target.value)}
          />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Next steps" id="wf135-next-steps" hint="One per line. Add '| owner' to name an owner.">
            <textarea
              id="wf135-next-steps"
              className={inputClass}
              rows={3}
              value={nextSteps}
              onChange={(event) => setNextSteps(event.target.value)}
            />
          </Field>
          <Field label="Requirements" id="wf135-requirements" hint="One per line.">
            <textarea
              id="wf135-requirements"
              className={inputClass}
              rows={3}
              value={requirements}
              onChange={(event) => setRequirements(event.target.value)}
            />
          </Field>
        </div>
        <fieldset className="rounded-sm border border-border-subtle p-3">
          <legend className="px-1 text-[13px] font-medium text-foreground">
            Qualification evidence (MEDDICC)
          </legend>
          <div className="grid gap-3 sm:grid-cols-2">
            {MEDDICC_FIELDS.map((letter) => (
              <Field key={letter.key} label={`${letter.letter} - ${letter.label}`} id={`meddicc-${letter.key}`}>
                <input
                  id={`meddicc-${letter.key}`}
                  className={inputClass}
                  value={meddicc[letter.key] || ''}
                  onChange={(event) =>
                    setMeddicc((current) => ({ ...current, [letter.key]: event.target.value }))
                  }
                />
              </Field>
            ))}
          </div>
        </fieldset>
        <Field label="Mutual Action Plan reference" id="wf135-plan-ref" hint="Optional. Recorded as a reference, not written into the plan.">
          <input
            id="wf135-plan-ref"
            className={inputClass}
            value={planRef}
            onChange={(event) => setPlanRef(event.target.value)}
          />
        </Field>
        <Button type="submit" variant="primary" disabled={busy || !summary.trim()}>
          {busy ? 'Recording…' : 'Record conversation'}
        </Button>
      </form>
    </Card>
  )
}

export default function ConversationIntelligence() {
  const rooms = useAsync(() => listRooms(), [])
  const [roomId, setRoomId] = useState('')
  const [reportError, setReportError] = useState(null)

  const chosenRoom = roomId || rooms.data?.records?.[0]?.id || ''
  const scoped = useAsync(
    () =>
      Promise.all([
        conversationApi.summary(chosenRoom),
        conversationApi.coverage(chosenRoom),
        conversationApi.captures(chosenRoom),
      ]),
    [chosenRoom],
  )

  if (rooms.loading) return <Spinner label="Loading rooms" />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />

  const records = rooms.data?.records || []
  if (!records.length) {
    return (
      <EmptyState
        title="No rooms yet"
        description="A conversation is captured into a room, so this page needs at least one room."
      />
    )
  }

  const [summary, coverage, captures] = scoped.data || [null, null, null]
  const counts = summary || {}
  const letters = coverage?.letters || []
  const listed = captures?.captures || []

  async function report(destinationId, outcome) {
    setReportError(null)
    try {
      await conversationApi.reportDestination(destinationId, { outcome })
    } catch (caught) {
      setReportError(caught)
    }
    scoped.refetch()
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-foreground">Conversation intelligence</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            One entry per conversation, fanned out to the room&apos;s notes, the handoff packet and
            the CRM custom fields.
          </p>
        </div>
        <Field label="Room" id="wf135-room">
          <select
            id="wf135-room"
            className={`${inputClass} w-auto`}
            value={chosenRoom}
            onChange={(event) => setRoomId(event.target.value)}
          >
            {records.map((record) => (
              <option key={record.id} value={record.id}>
                {record.data?.name || record.id}
              </option>
            ))}
          </select>
        </Field>
      </div>

      {scoped.loading && <Spinner label="Loading the conversation records" />}
      {scoped.error && <ErrorNote error={scoped.error} onRetry={scoped.refetch} />}

      {scoped.data && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="Captures" value={counts.captures ?? 0} hint="Conversations recorded" />
            <StatCard
              label="Fan-out rows"
              value={counts.destinations ?? 0}
              hint={`${counts.written ?? 0} written, ${counts.failed ?? 0} failed`}
            />
            <StatCard label="Agreed" value={counts.agreed ?? 0} hint="All three destinations agree" />
            <StatCard label="Partial" value={counts.partial ?? 0} hint="A destination failed" />
          </div>

          <Card>
            <h2 className="text-lg font-semibold text-foreground">MEDDICC coverage</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              {coverage?.note ||
                'Evidence is recorded per letter. This build reports coverage and no score.'}
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              {letters.map((row) => (
                <LetterBadge key={row.key} row={row} />
              ))}
            </div>
            <p className="mt-3 font-mono text-xs text-muted-foreground">
              {`${coverage?.letters_with_evidence ?? 0} of ${coverage?.letters_total ?? 7} letters carry evidence`}
            </p>
          </Card>

          <RecordPanel roomId={chosenRoom} onRecorded={scoped.refetch} />

          {reportError && (
            <Notice
              tone="destructive"
              title={reportError.reason ? 'That change was refused' : 'Could not report the outcome'}
            >
              {reportError.reason
                ? conflictText(reportError.reason)
                : String(reportError.message || reportError)}
            </Notice>
          )}

          <div className="space-y-4">
            <h2 className="text-lg font-semibold text-foreground">Captured conversations</h2>
            {!listed.length && (
              <EmptyState
                title="No captures yet"
                description="Record a conversation above. This workflow records one entry and fans it out."
              />
            )}
            {listed.map((capture) => (
              <CaptureCard key={capture.id} capture={capture} onReport={report} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}
