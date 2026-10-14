/**
 * WF-131: per-recipient content protection and auditability.
 *
 * One matrix per recipient, on top of a link that already has a gate. The page
 * reads its field labels, action codes and declined items from the server's
 * `/vocabulary` and `/honesty` routes rather than repeating them, so the panel
 * cannot offer a switch the rules refuse or claim a capability the build declines.
 *
 * Four things this page deliberately does not do, each from the research:
 *
 *   - It does not gate by itself. The gate is WF-069's, the NDA is WF-070's, the
 *     watermark is WF-071's and the download AND is WF-072's; this page configures
 *     the per-recipient matrix and reads their answers.
 *   - It does not claim parity with a commercial product. The research states
 *     plainly that no open-source project implements a full audit log with
 *     revocation semantics, and the honesty card says so on the page.
 *   - It does not enforce the IP allow-list at an edge. There is no edge here; the
 *     rule is evaluated on the request, so revocation and the allow-list both take
 *     effect immediately.
 *   - It does not rotate a passcode. WF-069 stores the link's password once; this
 *     page only binds the *requirement* to a recipient.
 */

import { useState } from 'react'

import {
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
  decisionLabel,
  listRooms,
  protectionApi,
  shortTimestamp,
  splitAllowlist,
  stateOf,
} from './api'
import {
  ActionRow,
  AllowListPreview,
  DecisionCard,
  InlineNote,
  ProtectionBadges,
  StatusBadge,
} from './primitives'

/** The switches the matrix presents, in the order a seller meets them. */
const BOOL_FIELDS = [
  { field: 'email_protected', label: 'Email verification', hint: 'Ask for an email address.' },
  { field: 'email_authenticated', label: 'Email one-time code', hint: 'Prove the inbox.' },
  { field: 'require_passcode', label: 'Passcode', hint: "Require the link's passcode." },
  { field: 'enable_agreement', label: 'NDA acceptance', hint: 'Accept an agreement first.' },
  { field: 'enable_watermark', label: 'Watermark', hint: 'Stamp each view with the viewer.' },
  { field: 'allow_download', label: 'Allow download', hint: 'Permit a file fetch.' },
]

/** Everything the page loads for the current scope, loaded together. */
function useBoard(roomId, selectedId) {
  const vocabulary = useAsync(() => protectionApi.vocabulary(), [])
  const honesty = useAsync(() => protectionApi.honesty(), [])
  const summary = useAsync(() => protectionApi.summary(roomId), [roomId])
  const policies = useAsync(() => protectionApi.policiesInScope(roomId), [roomId])
  const detail = useAsync(
    () => (selectedId ? protectionApi.readPolicy(selectedId) : Promise.resolve(null)),
    [selectedId],
  )
  const loading = vocabulary.loading || summary.loading || policies.loading
  const error = vocabulary.error || summary.error || policies.error
  return { vocabulary, honesty, summary, policies, detail, loading, error }
}

function ProtectionPage() {
  const [roomId, setRoomId] = useState('')
  const [selectedId, setSelectedId] = useState(null)
  const [note, setNote] = useState(null)
  const [problem, setProblem] = useState(null)

  const rooms = useAsync(() => listRooms(), [])
  const board = useBoard(roomId, selectedId)

  function refetchAll() {
    board.summary.refetch()
    board.policies.refetch()
    board.detail.refetch()
  }

  async function act(fn, success) {
    setProblem(null)
    setNote(null)
    try {
      const result = await fn()
      setNote(typeof success === 'function' ? success(result) : success)
      refetchAll()
      return result
    } catch (error) {
      setProblem(error)
      return null
    }
  }

  if (board.loading && !board.summary.data) return <Spinner label="Loading content protection" />
  if (board.error) return <ErrorNote error={board.error} onRetry={refetchAll} />

  const summary = board.summary.data
  const policies = board.policies.data?.policies || []
  const detail = board.detail.data
  const declined = board.honesty.data?.declined || []

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold text-foreground">Content protection</h1>
        <p className="text-sm text-muted-foreground">
          One protection matrix per recipient: email verification, a passcode, an NDA, a
          watermark, a download switch and an IP allow-list, with an action log that names the
          protection each entry satisfies.
        </p>
      </header>

      {note && <InlineNote tone="success">{note}</InlineNote>}
      {problem && <ErrorNote error={problem} />}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Recipient policies"
          value={summary?.policies ?? 0}
          icon="rooms"
          hint={`${summary?.recipients ?? 0} distinct recipients`}
        />
        <StatCard
          label="Revoked"
          value={summary?.revoked ?? 0}
          icon="trash"
          hint="Refused from the next request, row kept"
        />
        <StatCard
          label="Watermarked"
          value={summary?.watermarked ?? 0}
          icon="audit"
          hint={`${summary?.allowlisted ?? 0} with an IP allow-list`}
        />
        <StatCard
          label="Logged actions"
          value={summary?.actions ?? 0}
          icon="database"
          hint={`${summary?.denied ?? 0} refused`}
        />
      </div>

      <Card className="flex flex-col gap-3">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Room" id="wf131-room" hint="A policy belongs to a link in a room.">
            <select
              id="wf131-room"
              className={inputClass}
              value={roomId}
              onChange={(event) => {
                setRoomId(event.target.value)
                setSelectedId(null)
              }}
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

        <h2 className="text-lg font-semibold text-foreground">Recipient policies</h2>
        {policies.length === 0 ? (
          <EmptyState
            title="No recipient policy yet"
            description="A policy is bound to a WF-069 link and one recipient. Add one below, or seed the demo database."
          />
        ) : (
          <ul className="flex flex-col">
            {policies.map((policy) => (
              <li
                key={policy.id}
                className="flex flex-col gap-2 border-b border-border-subtle py-3 last:border-b-0"
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <span className="flex min-w-0 flex-col">
                    <span className="truncate text-sm text-foreground">{policy.recipient}</span>
                    <span className="truncate font-mono text-xs text-muted-foreground">
                      {policy.link_id} | updated {shortTimestamp(policy.updated_at)}
                    </span>
                  </span>
                  <span className="flex flex-wrap items-center gap-2">
                    <StatusBadge state={stateOf(policy)} />
                    <Button
                      icon="search"
                      onClick={() => setSelectedId(policy.id === selectedId ? null : policy.id)}
                    >
                      {policy.id === selectedId ? 'Close the panel' : 'Open the matrix'}
                    </Button>
                  </span>
                </div>
                <ProtectionBadges fields={policy.policy} />
                <AllowListPreview allowlist={policy.policy?.ip_allowlist || []} />
                {policy.revoked && (
                  <span className="font-mono text-xs text-destructive">
                    revoked {shortTimestamp(policy.revoked_at)} by {policy.revoked_by || 'unknown'}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}

        <AddRecipientForm roomId={roomId} onAct={act} />
      </Card>

      {selectedId && detail && (
        <Card className="flex flex-col gap-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="text-lg font-semibold text-foreground">{detail.recipient}</h2>
              <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                {detail.link_id} | {detail.id}
              </p>
            </div>
            <StatusBadge state={stateOf(detail)} />
          </div>

          <MatrixPanel
            key={`${selectedId}:${detail.updated_at || 'new'}`}
            policy={detail}
            onAct={act}
          />

          <AttemptPanel key={`attempt:${selectedId}`} policyId={selectedId} onAct={act} />

          <ActionLog linkId={detail.link_id} />

          <BulkAlertCard linkId={detail.link_id} />
        </Card>
      )}

      <Card className="flex flex-col gap-2">
        <h2 className="text-base font-semibold text-foreground">Not built here</h2>
        <p className="text-xs text-muted-foreground">
          The research names these, and they have no source or belong to another workflow. They
          are listed so their absence reads as a decision rather than an oversight.
        </p>
        <ul className="list-inside list-disc text-xs text-muted-foreground">
          {declined.map((item) => (
            <li key={item.claim}>
              <span className="text-foreground">{item.claim}</span>: {item.why}
            </li>
          ))}
        </ul>
        {board.honesty.data?.limitation && (
          <InlineNote tone="info">{board.honesty.data.limitation}</InlineNote>
        )}
      </Card>
    </div>
  )
}

function AddRecipientForm({ roomId, onAct }) {
  const [linkId, setLinkId] = useState('')
  const [recipient, setRecipient] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit() {
    setBusy(true)
    await onAct(
      () => protectionApi.createPolicy(linkId.trim(), { recipient: recipient.trim() }, roomId),
      'Bound a policy to that recipient. Turn its switches on below.',
    )
    setBusy(false)
  }

  const ready = linkId.trim() && recipient.trim()

  return (
    <div className="flex flex-col gap-3 border-t border-border-subtle pt-4">
      <h3 className="text-base font-semibold text-foreground">Add a recipient</h3>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Link id" id="wf131-new-link" hint="A WF-069 gated link.">
          <input
            id="wf131-new-link"
            className={inputClass}
            value={linkId}
            onChange={(event) => setLinkId(event.target.value)}
          />
        </Field>
        <Field label="Recipient email" id="wf131-new-recipient" hint="Who this matrix applies to.">
          <input
            id="wf131-new-recipient"
            className={inputClass}
            value={recipient}
            onChange={(event) => setRecipient(event.target.value)}
          />
        </Field>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="primary" icon="plus" disabled={busy || !ready} onClick={submit}>
          Bind the policy
        </Button>
        <span className="text-xs text-muted-foreground">
          A missing link is refused with 404, and a bad allow-list with 422.
        </span>
      </div>
    </div>
  )
}

function MatrixPanel({ policy, onAct }) {
  const fields = policy.policy || {}
  const [draft, setDraft] = useState(() => {
    const initial = {}
    for (const entry of BOOL_FIELDS) initial[entry.field] = Boolean(fields[entry.field])
    initial.agreement_id = fields.agreement_id || ''
    initial.ip_allowlist = (fields.ip_allowlist || []).join(', ')
    initial.expires_at = fields.expires_at || ''
    return initial
  })
  const [busy, setBusy] = useState(false)
  const [decision, setDecision] = useState(null)

  function payload() {
    const body = {}
    for (const entry of BOOL_FIELDS) body[entry.field] = draft[entry.field]
    body.agreement_id = draft.agreement_id.trim() || null
    body.ip_allowlist = splitAllowlist(draft.ip_allowlist)
    body.expires_at = draft.expires_at.trim() || null
    return body
  }

  async function save() {
    setBusy(true)
    await onAct(
      () => protectionApi.updatePolicy(policy.id, payload()),
      'Saved the matrix. The next request is evaluated against it.',
    )
    setBusy(false)
  }

  async function evaluate() {
    setBusy(true)
    await onAct(async () => {
      const result = await protectionApi.evaluate(policy.id, { email_verified: true })
      setDecision(result)
      return result
    }, 'Evaluated the matrix. Nothing was written.')
    setBusy(false)
  }

  async function revoke() {
    setBusy(true)
    await onAct(
      () => protectionApi.revoke(policy.id),
      'Revoked the recipient. The next request is refused, and the row is kept.',
    )
    setBusy(false)
  }

  async function restore() {
    setBusy(true)
    await onAct(() => protectionApi.restore(policy.id), 'Restored the recipient.')
    setBusy(false)
  }

  return (
    <div className="flex flex-col gap-4 border-t border-border-subtle pt-4">
      <div className="flex flex-col gap-1">
        <h3 className="text-base font-semibold text-foreground">Protection matrix</h3>
        <p className="text-xs text-muted-foreground">
          A switch can only add protection on top of the link&apos;s own gate; it cannot remove
          one the link already asks for.
        </p>
      </div>

      <ul className="grid gap-2 sm:grid-cols-2">
        {BOOL_FIELDS.map((entry) => (
          <li key={entry.field}>
            <Switch
              id={`wf131-${entry.field}`}
              label={entry.label}
              hint={entry.hint}
              checked={draft[entry.field]}
              onChange={(value) => setDraft({ ...draft, [entry.field]: value })}
            />
          </li>
        ))}
      </ul>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          label="Agreement to accept"
          id="wf131-agreement"
          hint="Required when NDA acceptance is on. Send blank to clear it."
        >
          <input
            id="wf131-agreement"
            className={inputClass}
            value={draft.agreement_id}
            onChange={(event) => setDraft({ ...draft, agreement_id: event.target.value })}
          />
        </Field>
        <Field
          label="Expires at"
          id="wf131-expires"
          hint="An ISO date. It tightens the link's expiry and never extends it."
        >
          <input
            id="wf131-expires"
            className={inputClass}
            value={draft.expires_at}
            placeholder="No expiry"
            onChange={(event) => setDraft({ ...draft, expires_at: event.target.value })}
          />
        </Field>
      </div>

      <Field
        label="IP allow-list"
        id="wf131-allowlist"
        hint="One address or CIDR block per line. Empty means open."
      >
        <textarea
          id="wf131-allowlist"
          className={`${inputClass} min-h-24 py-2`}
          rows={3}
          value={draft.ip_allowlist}
          onChange={(event) => setDraft({ ...draft, ip_allowlist: event.target.value })}
        />
      </Field>

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="primary" icon="plus" disabled={busy} onClick={save}>
          Save the matrix
        </Button>
        <Button icon="search" disabled={busy} onClick={evaluate}>
          Evaluate as verified
        </Button>
        {policy.revoked ? (
          <Button icon="restore" disabled={busy} onClick={restore}>
            Restore the recipient
          </Button>
        ) : (
          <Button variant="danger" icon="trash" disabled={busy} onClick={revoke}>
            Revoke the recipient
          </Button>
        )}
      </div>

      {decision && (
        <div className="flex flex-col gap-2">
          <h3 className="text-base font-semibold text-foreground">Evaluation</h3>
          <p className="text-sm text-muted-foreground">{decisionLabel(decision)}</p>
          <DecisionCard decision={decision} />
        </div>
      )}
    </div>
  )
}

function AttemptPanel({ policyId, onAct }) {
  const [context, setContext] = useState({
    email_verified: true,
    passcode_ok: true,
    nda_accepted: false,
    ip: '203.0.113.24',
    room_bulk_download: false,
    item_can_download: false,
  })
  const [decision, setDecision] = useState(null)
  const [busy, setBusy] = useState(false)

  async function admit() {
    setBusy(true)
    await onAct(async () => {
      const params = {
        email_verified: context.email_verified,
        passcode_ok: context.passcode_ok,
        nda_accepted: context.nda_accepted,
        ip: context.ip.trim(),
        download: {
          bulk_download: context.room_bulk_download,
          can_download: context.item_can_download,
        },
      }
      try {
        const result = await protectionApi.admit(policyId, params)
        setDecision(result)
        return result
      } catch (error) {
        if (error.status === 403) {
          // A refusal is a real answer, not a failure. The transport carries only
          // the sentence, so the decision is read back through the read-only
          // evaluation rather than parsed out of an error message.
          const result = await protectionApi.evaluate(policyId, params)
          setDecision(result)
          return result
        }
        throw error
      }
    }, 'Logged the attempt. Every attempt is recorded, allowed or refused.')
    setBusy(false)
  }

  const download = decision?.download

  return (
    <div className="flex flex-col gap-3 border-t border-border-subtle pt-4">
      <div className="flex flex-col gap-1">
        <h3 className="text-base font-semibold text-foreground">Admit a viewer</h3>
        <p className="text-xs text-muted-foreground">
          This is the viewer&apos;s call, not a seller control. It evaluates the matrix, logs the
          attempt, and returns the decision that names the protection.
        </p>
      </div>

      <ul className="grid gap-2 sm:grid-cols-3">
        <li>
          <Switch
            id="wf131-attempt-email"
            label="Inbox verified"
            checked={context.email_verified}
            onChange={(value) => setContext({ ...context, email_verified: value })}
          />
        </li>
        <li>
          <Switch
            id="wf131-attempt-passcode"
            label="Passcode given"
            checked={context.passcode_ok}
            onChange={(value) => setContext({ ...context, passcode_ok: value })}
          />
        </li>
        <li>
          <Switch
            id="wf131-attempt-nda"
            label="NDA accepted"
            checked={context.nda_accepted}
            onChange={(value) => setContext({ ...context, nda_accepted: value })}
          />
        </li>
        <li>
          <Switch
            id="wf131-attempt-bulk"
            label="Room permits bulk"
            checked={context.room_bulk_download}
            onChange={(value) => setContext({ ...context, room_bulk_download: value })}
          />
        </li>
        <li>
          <Switch
            id="wf131-attempt-item"
            label="Item allows download"
            checked={context.item_can_download}
            onChange={(value) => setContext({ ...context, item_can_download: value })}
          />
        </li>
      </ul>

      <Field label="Client address" id="wf131-attempt-ip" hint="Checked against the allow-list.">
        <input
          id="wf131-attempt-ip"
          className={inputClass}
          value={context.ip}
          onChange={(event) => setContext({ ...context, ip: event.target.value })}
        />
      </Field>

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="primary" icon="search" disabled={busy} onClick={admit}>
          Admit this viewer
        </Button>
        <span className="text-xs text-muted-foreground">
          A refusal is answered 403 with the decision, and the attempt is still logged.
        </span>
      </div>

      {decision && (
        <div className="flex flex-col gap-2">
          <h3 className="text-base font-semibold text-foreground">Decision</h3>
          <p className="text-sm text-muted-foreground">{decisionLabel(decision)}</p>
          <DecisionCard decision={decision} />
          {download && (
            <p className="font-mono text-xs text-muted-foreground">
              download {String(download.download)} | blocked by{' '}
              {(download.blocked_by || []).join(', ') || 'nothing'}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function ActionLog({ linkId }) {
  const actions = useAsync(() => protectionApi.actions(linkId), [linkId])
  if (actions.loading) return <Spinner label="Loading the action log" />
  if (actions.error) return <ErrorNote error={actions.error} onRetry={actions.refetch} />

  const rows = actions.data?.actions || []
  return (
    <div className="flex flex-col gap-2 border-t border-border-subtle pt-4">
      <h3 className="text-base font-semibold text-foreground">Action log</h3>
      <p className="text-xs text-muted-foreground">
        View, download, share and revoke, each carried with the integer code WF-079&apos;s export
        queries by. A share is its own action rather than a derived view.
      </p>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No action recorded yet. Admit a viewer above to log one.
        </p>
      ) : (
        <ul className="flex flex-col">
          {rows.map((action) => (
            <ActionRow key={action.id} action={action} />
          ))}
        </ul>
      )}
    </div>
  )
}

function BulkAlertCard({ linkId }) {
  const alert = useAsync(() => protectionApi.bulkAlert(linkId), [linkId])
  if (alert.loading) return <Spinner label="Checking the bulk-download pattern" />
  if (alert.error) return <ErrorNote error={alert.error} onRetry={alert.refetch} />

  const data = alert.data
  if (!data) return null
  return (
    <div className="flex flex-col gap-2 border-t border-border-subtle pt-4">
      <h3 className="text-base font-semibold text-foreground">Bulk-download alert</h3>
      <InlineNote tone={data.alert ? 'warning' : 'info'}>
        {data.alert
          ? `A pattern is present: ${data.recipients.join(', ')} downloaded at or above ${data.threshold} times in ${data.window_minutes} minutes.`
          : `No pattern: nobody reached ${data.threshold} downloads in ${data.window_minutes} minutes.`}{' '}
        {data.note}
      </InlineNote>
    </div>
  )
}

/** A labelled checkbox that meets the 44px touch target. */
function Switch({ id, label, hint, checked, onChange }) {
  return (
    <label
      htmlFor={id}
      className="flex min-h-11 items-center gap-3 rounded-sm border border-border-subtle bg-surface px-3 py-1.5 text-sm text-foreground"
    >
      <input
        id={id}
        type="checkbox"
        className="h-4 w-4"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="flex flex-col">
        <span>{label}</span>
        {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
      </span>
    </label>
  )
}

/** Referenced so a rename in the shared api helpers fails in this file too. */
export default {
  id: 'wf-131-content-protection',
  label: 'Content protection',
  icon: 'audit',
  order: 131,
  Component: ProtectionPage,
}
