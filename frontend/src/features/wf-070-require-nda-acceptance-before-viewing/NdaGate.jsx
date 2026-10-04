import { useMemo, useState } from 'react'

import { ndaApi, listRooms } from './api'
import { GateBadge, Notice, Toggle } from './primitives'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  StatCard,
  Spinner,
  inputClass,
  useAsync,
} from '@/components/ui'

/**
 * The seller's board for WF-070: the agreements a room holds, and the gate on each
 * of its buyer links.
 *
 * What the page is shaped around
 * ------------------------------
 *
 * A seller has three questions, and the page answers them in that order: how many
 * links are gated, which NDA is each one asking for, and who has accepted it. The
 * fail-closed state gets its own row and its own wording rather than a red dot,
 * because "gated" and "gated but asking for nothing" look identical in the flag and
 * only one of them is safe.
 *
 * The acceptance list shows the version and the digest a buyer was given under,
 * because the whole rule of this workflow is that an acceptance is bound to a
 * particular text. A seller looking at "3 accepted" who cannot see which text those
 * three accepted has been given a number they cannot act on.
 */
export default function NdaGate() {
  const [chosen, setChosen] = useState('')

  const rooms = useAsync(() => listRooms(), [])
  const records = rooms.data?.records || []

  // Derived, not set from an effect. Writing the room from an effect needs a second
  // render to take effect and trips `react-hooks/set-state-in-effect`; deriving it
  // costs nothing and leaves no frame where the board is loading the wrong room.
  const roomId = chosen || records[0]?.id || ''

  const board = useAsync(
    () =>
      Promise.all([
        ndaApi.summary(roomId || undefined),
        ndaApi.agreements(roomId || undefined),
        roomId ? ndaApi.roomGates(roomId) : Promise.resolve({ gates: [] }),
        roomId ? ndaApi.acceptances(roomId) : Promise.resolve({ acceptances: [] }),
      ]).then(([summary, agreements, gates, acceptances]) => ({
        summary,
        agreements: agreements.agreements || [],
        gates: gates.gates || [],
        acceptances: acceptances.acceptances || [],
      })),
    [roomId],
  )

  if (rooms.loading) return <Spinner label="Loading rooms" />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />

  if (!records.length) {
    return (
      <EmptyState
        title="No rooms yet"
        description="An NDA gate hangs off a buyer link, and a buyer link hangs off a room. Seed the demo data to see this page with content in it."
      />
    )
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-semibold text-foreground">
          NDA gate
        </h1>
        <p className="mt-1 max-w-3xl text-[15px] text-muted-foreground">
          A viewer accepts the agreement before any room content loads. An acceptance is
          bound to the viewer session that made it and to a fingerprint of the text they
          read, so amending the agreement re-opens the gate for anyone who has not
          accepted the current wording.
        </p>
      </header>

      <RoomPicker rooms={records} roomId={roomId} onChange={setChosen} />

      {board.loading ? <Spinner label="Loading the agreement board" /> : null}
      {board.error ? <ErrorNote error={board.error} onRetry={board.refetch} /> : null}

      {board.data ? (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="Agreements" value={board.data.summary.agreements} icon="audit" />
            <StatCard label="Links gated" value={board.data.summary.gated} icon="schema" />
            <StatCard
              label="Acceptances"
              value={board.data.summary.acceptances}
              hint={`on ${board.data.summary.accepted_sessions} of ${board.data.summary.sessions} viewer sessions`}
              icon="database"
            />
            <StatCard
              label="Failing closed"
              value={board.data.summary.broken_gates}
              hint="gated, but the agreement is gone"
              icon="schema"
            />
          </div>

          {board.data.summary.broken_gates > 0 ? (
            <Notice tone="destructive" title="A gate is asking for nothing">
              One or more links have the agreement gate on and name an agreement that is
              retired. Those links release no content. Point each one at another agreement,
              or turn its gate off.
            </Notice>
          ) : null}

          <AgreementEditor roomId={roomId} onSaved={board.refetch} />

          <GateBoard
            gates={board.data.gates}
            agreements={board.data.agreements}
            onChanged={board.refetch}
          />

          <AcceptanceLog acceptances={board.data.acceptances} agreements={board.data.agreements} />
        </>
      ) : null}
    </div>
  )
}

function RoomPicker({ rooms, roomId, onChange }) {
  return (
    <Field label="Room" id="wf070-room" hint="An NDA gate hangs off a buyer link, and a link belongs to a room.">
      <select
        id="wf070-room"
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
    </Field>
  )
}

function AgreementEditor({ roomId, onSaved }) {
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function save(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await ndaApi.createAgreement(roomId, { title, body })
      setTitle('')
      setBody('')
      onSaved()
    } catch (failure) {
      setError(failure)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">Write an agreement</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        The text a viewer reads and accepts. Amending it later moves its version and re-opens
        the gate for anyone who has not accepted the new wording.
      </p>

      {error?.errors ? (
        <div className="mt-4 space-y-1">
          <Notice tone="destructive" title="The agreement was not saved">
            <ul className="list-disc pl-5">
              {Object.entries(error.errors).map(([field, message]) => (
                <li key={field}>{message}</li>
              ))}
            </ul>
          </Notice>
        </div>
      ) : null}

      <form className="mt-4 space-y-4" onSubmit={save}>
        <Field label="Title" id="wf070-title">
          <input
            id="wf070-title"
            className={inputClass}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </Field>
        <Field
          label="Agreement text"
          id="wf070-body"
          hint="Plain text. Shown to the viewer before any room content loads."
        >
          <textarea
            id="wf070-body"
            rows={6}
            className={`${inputClass} font-mono text-[13px]`}
            value={body}
            onChange={(event) => setBody(event.target.value)}
          />
        </Field>
        <Button type="submit" variant="primary" icon="plus" disabled={busy}>
          {busy ? 'Saving' : 'Save agreement'}
        </Button>
      </form>
    </Card>
  )
}

/**
 * The gate list.
 *
 * `agreements` is passed in rather than fetched per row. The board already loads
 * the room's agreements in the same batch as the gates, so a row that fetched its
 * own copy would issue one request per link for data already in hand, and would
 * render its picker empty until that second request came back. That empty window is
 * not cosmetic: a `<select>` with nothing in it is a control that appears broken,
 * and it is a race a caller cannot see through. One list, loaded once, rendered
 * populated on the first paint.
 */
function GateBoard({ gates, agreements, onChanged }) {
  const [busyId, setBusyId] = useState(null)
  const [failure, setFailure] = useState(null)

  if (!gates.length) {
    return (
      <EmptyState
        title="No buyer links in this room"
        description="The gate lives on a buyer link. Create one in Link gating, then come back and choose an agreement for it."
        action={
          <Button icon="refresh" onClick={onChanged}>
            Check again
          </Button>
        }
      />
    )
  }

  async function toggle(gate, enabled) {
    setBusyId(gate.id)
    setFailure(null)
    try {
      // Enabling with an agreement is one request, because that is what the CLI's
      // single `--agreement` flag does. Turning it off leaves the selection alone so
      // the gate can be paused mid-deal and resumed.
      await ndaApi.setLinkGate(gate.id, enabled ? { agreement: gate.agreementChoice } : { enable_agreement: false })
      onChanged()
    } catch (error) {
      setFailure(error)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-display text-lg font-semibold text-foreground">Buyer links</h2>
        <p className="text-sm text-muted-foreground">
          Choose the agreement each link asks a viewer to accept.
        </p>
      </div>

      {failure ? (
        <Notice tone="destructive" title="The gate was not changed">
          <p>{failure.message}</p>
          {failure.errors ? (
            <ul className="mt-1 list-disc pl-5">
              {Object.values(failure.errors).map((message) => (
                <li key={message}>{message}</li>
              ))}
            </ul>
          ) : null}
        </Notice>
      ) : null}

      <div className="space-y-3">
        {gates.map((gate) => (
          // Keyed on the agreement as well as the link, so a change made from
          // another row or from the API resets the picker. Remounting on a changed
          // key is the documented way to reset state from props, and it avoids
          // writing `setSelected` inside an effect.
          <GateRow
            key={`${gate.id}:${gate.gate?.agreement_id || ''}`}
            gate={gate}
            agreements={agreements}
            onToggle={toggle}
            busy={busyId === gate.id}
          />
        ))}
      </div>
    </section>
  )
}

function GateRow({ gate, agreements, onToggle, busy }) {
  const gateData = gate.gate || {}
  const [selected, setSelected] = useState(gateData.agreement_id || '')

  const enriched = { ...gate, agreementChoice: selected }

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-base font-semibold text-foreground">{gate.title || 'Untitled link'}</h3>
            <GateBadge gate={gateData} />
            {gate.revoked ? <Badge tone="delete">revoked</Badge> : null}
            {gate.expired ? <Badge tone="restore">expired</Badge> : null}
          </div>
          <p className="mt-1 font-mono text-xs text-muted-foreground">{gate.id}</p>
          {gateData.enabled && !gateData.agreement_ok ? (
            <p className="mt-2 text-sm text-destructive">
              The gate is on and names an agreement that is retired, so this link releases
              nothing.
            </p>
          ) : null}
        </div>

        <Toggle
          id={`wf070-gate-${gate.id}`}
          label="Require NDA acceptance"
          hint="Off means viewers see the room with nothing asked."
          checked={Boolean(gateData.enabled)}
          disabled={busy}
          onChange={(next) => onToggle(enriched, next)}
        />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Agreement" id={`wf070-agreement-${gate.id}`}>
          <select
            id={`wf070-agreement-${gate.id}`}
            className={inputClass}
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
          >
            {/* An agreement that is failing a link closed is still offered, because
                the fix is usually to point the link at it. The placeholder covers the
                one genuine empty case: a room with no agreements written yet. */}
            <option value="">
              {agreements.length ? 'Choose an agreement' : 'No agreements written yet'}
            </option>
            {agreements.map((agreement) => (
              <option key={agreement.id} value={agreement.id}>
                {agreement.title} (v{agreement.version})
              </option>
            ))}
          </select>
        </Field>
        <Button
          icon="plus"
          disabled={busy || !selected || selected === gateData.agreement_id}
          onClick={() => onToggle(enriched, true)}
        >
          Apply to this link
        </Button>
      </div>
    </Card>
  )
}

function AcceptanceLog({ acceptances, agreements }) {
  const titles = useMemo(() => {
    const map = {}
    for (const agreement of agreements) map[agreement.id] = agreement
    return map
  }, [agreements])

  if (!acceptances.length) {
    return (
      <EmptyState
        title="No acceptances yet"
        description="When a viewer accepts, the record shows which version of the text they read."
      />
    )
  }

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-display text-lg font-semibold text-foreground">Acceptances</h2>
        <p className="text-sm text-muted-foreground">
          Each row names the version and the fingerprint the viewer was given under.
        </p>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] border-collapse text-left text-sm">
          <thead>
            <tr className="border-b border-border-subtle">
              <th className="py-2 pr-4 font-medium text-muted-foreground">Viewer</th>
              <th className="py-2 pr-4 font-medium text-muted-foreground">Link</th>
              <th className="py-2 pr-4 font-medium text-muted-foreground">Agreement</th>
              <th className="py-2 pr-4 font-medium text-muted-foreground">Version read</th>
              <th className="py-2 font-medium text-muted-foreground">Accepted at</th>
            </tr>
          </thead>
          <tbody>
            {acceptances.map((row) => {
              const agreement = titles[row.agreement_id]
              const current = agreement && agreement.body_digest === row.body_digest
              return (
                <tr key={row.id} className="border-b border-border-subtle last:border-0">
                  <td className="py-2 pr-4 font-mono text-[13px] text-foreground">
                    {row.email || 'no address given'}
                  </td>
                  <td className="py-2 pr-4 font-mono text-[13px] text-muted-foreground">{row.link_id}</td>
                  <td className="py-2 pr-4 text-foreground">{agreement?.title || row.agreement_id}</td>
                  <td className="py-2 pr-4">
                    <Badge tone={current ? 'insert' : 'restore'}>
                      v{row.agreement_version}
                      {current ? '' : ' superseded'}
                    </Badge>
                  </td>
                  <td className="py-2 font-mono text-[13px] text-muted-foreground">{row.accepted_at}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}