/**
 * WF-095: collect acceptance by e-signature, countersignature and identity verification.
 *
 * The page has five jobs, in this order, and the order is the design.
 *
 * **Say where every quote stands first.** A seller opening this page is asking one question:
 * which quotes are waiting on a buyer and which on a countersigner. So the board and the
 * status rail come before any authoring form.
 *
 * **Show the signing order, because the research fixes it.** The buyer signs first and the
 * countersigner second, and a countersignature recorded before the buyer's is a 400. The rail
 * shows the four researched statuses, and every step reads "Done", "Now" or "Waiting" in
 * words, so the state is never carried by colour alone.
 *
 * **Say what the research decided and what it left open.** The one-hour verification window,
 * the 40 MB cap, one quota usage per envelope rather than per signature, and a ceiling the
 * research never stated all carry the sentence they came from. A seller who cannot see why a
 * number is what it is cannot review it.
 *
 * **Say what this workflow does not own.** Identity verification is the integrator's duty, the
 * countersigners come from this room's own users because the research names no directory, and
 * the contract created on acceptance belongs to WF-099. Each is stated on the page rather than
 * left in a docstring.
 *
 * **Render every state this page can be in.** Loading, error, empty, and the outcome of every
 * write. A board that goes blank when the API is down reads as "there is nothing here", which
 * for a signing page is the one reading that must never be possible.
 */

import { useCallback, useState } from 'react'

import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Spinner,
  StatCard,
  useAsync,
} from '@/components/ui'

import {
  ACCEPTANCE_METHODS,
  acceptanceApi,
  acceptanceFacts,
  documentSize,
  plural,
} from './api'
import {
  Dialog,
  EvidenceNote,
  EventRow,
  QuotaPanel,
  SignerField,
  SignerRow,
  SigningRail,
  StatusBadge,
  ToggleRow,
} from './primitives'

const ROOM = 'room_a'

/** The status line after a write. One shape, so every outcome reads the same way. */
function Outcome({ outcome, onDismiss }) {
  if (!outcome) return null
  const failed = outcome.status === 'error'
  return (
    <div
      role="status"
      className={`flex flex-wrap items-start justify-between gap-3 rounded-sm border p-4 ${
        failed ? 'border-destructive/40 bg-destructive/10' : 'border-accent/30 bg-accent/10'
      }`}
    >
      <div className="min-w-0">
        <p className={`text-sm font-semibold ${failed ? 'text-destructive' : 'text-accent'}`}>
          {outcome.title}
        </p>
        <p className="mt-0.5 text-sm text-muted-foreground">{outcome.detail}</p>
        {outcome.errors ? (
          <ul className="mt-2 space-y-1">
            {Object.entries(outcome.errors).map(([field, message]) => (
              <li key={field} className="text-xs text-foreground">
                <span className="font-mono">{field}</span>: {message}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <Button icon="close" onClick={onDismiss}>
        Dismiss
      </Button>
    </div>
  )
}

/**
 * The seller-side configuration form: the acceptance method, the buyer contacts, the
 * countersigners, and the two toggles the research names.
 */
function OpenEnvelopeForm({ busy, error, onClose, onSubmit }) {
  const [method, setMethod] = useState('esignature')
  const [buyerName, setBuyerName] = useState('Ada Byron')
  const [buyerEmail, setBuyerEmail] = useState('ada@northwind.example')
  const [counterName, setCounterName] = useState('Dana Reyes')
  const [counterEmail, setCounterEmail] = useState('dana@halcyon.example')
  const [reassignAllowed, setReassignAllowed] = useState(false)
  const [verifyRequired, setVerifyRequired] = useState(true)
  const [published, setPublished] = useState(true)
  const [withCountersigner, setWithCountersigner] = useState(true)

  return (
    <Dialog
      open
      title="Open a signing envelope"
      description="The acceptance configuration the seller's sidebar sets."
      onClose={onClose}
    >
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          const countersigners = withCountersigner
            ? [{ name: counterName, email: counterEmail, role: 'countersigner' }]
            : []
          onSubmit({
            hs_acceptance_method: method,
            buyer_signers: [{ name: buyerName, email: buyerEmail, role: 'buyer' }],
            countersigners,
            reassign_allowed: reassignAllowed,
            identity_verification_required: verifyRequired,
            is_published: published,
          })
        }}
      >
        <Field label="Acceptance method" id="wf095-method">
          <select
            id="wf095-method"
            className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground focus:border-accent"
            value={method}
            onChange={(event) => setMethod(event.target.value)}
          >
            {ACCEPTANCE_METHODS.map((choice) => (
              <option key={choice.value} value={choice.value}>
                {choice.label}
              </option>
            ))}
          </select>
        </Field>

        <SignerField
          id="wf095-buyer"
          label="Buyer contact"
          hint={acceptanceFacts.buyerSignersLabel}
          name={buyerName}
          email={buyerEmail}
          onName={setBuyerName}
          onEmail={setBuyerEmail}
        />

        <ToggleRow
          id="wf095-countersigner-on"
          label={acceptanceFacts.countersignersLabel}
          hint="Drawn from your own users."
          checked={withCountersigner}
          onChange={setWithCountersigner}
        />
        {withCountersigner ? (
          <SignerField
            id="wf095-counter"
            label="Countersigner"
            name={counterName}
            email={counterEmail}
            onName={setCounterName}
            onEmail={setCounterEmail}
          />
        ) : null}

        <ToggleRow
          id="wf095-reassign"
          label={acceptanceFacts.reassignAllowedLabel}
          hint="A signer who has already signed cannot be reassigned."
          checked={reassignAllowed}
          onChange={setReassignAllowed}
        />

        <ToggleRow
          id="wf095-verify"
          label="Require identity verification"
          hint={acceptanceFacts.verificationWindowQuote}
          checked={verifyRequired}
          onChange={setVerifyRequired}
        />

        <ToggleRow
          id="wf095-published"
          label="Quote is published"
          hint={acceptanceFacts.quotaConsumedOnEnableQuote}
          checked={published}
          onChange={setPublished}
        />

        {error ? (
          <div className="rounded-sm border border-destructive/40 bg-destructive/10 p-3">
            <p className="text-sm font-semibold text-destructive">The envelope was refused</p>
            <ul className="mt-1 space-y-1">
              {Object.entries(error).map(([field, message]) => (
                <li key={field} className="text-xs text-foreground">
                  <span className="font-mono">{field}</span>: {message}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" disabled={busy} icon="plus">
            Open envelope
          </Button>
          <Button onClick={onClose}>Cancel</Button>
        </div>
      </form>
    </Dialog>
  )
}

/** The reassignment dialog: the buyer's own step, which precedes the signature. */
function ReassignForm({ signer, busy, error, onClose, onSubmit }) {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  return (
    <Dialog
      open
      title="Reassign this quote signer"
      description={signer.signed ? acceptanceFacts.reassignAfterSignQuote : signer.email}
      onClose={onClose}
    >
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          onSubmit({ name, email })
        }}
      >
        <SignerField
          id="wf095-reassign"
          label="New signer"
          name={name}
          email={email}
          onName={setName}
          onEmail={setEmail}
        />
        <EvidenceNote label="Why the buyer may do this" quote={acceptanceFacts.reassignAfterSignQuote} />
        {error ? (
          <div className="rounded-sm border border-destructive/40 bg-destructive/10 p-3">
            <p className="text-sm text-destructive">{error.detail}</p>
            {error.errors ? (
              <ul className="mt-1 space-y-1">
                {Object.entries(error.errors).map(([field, message]) => (
                  <li key={field} className="text-xs text-foreground">
                    <span className="font-mono">{field}</span>: {message}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" disabled={busy || signer.signed} icon="refresh">
            Reassign signer
          </Button>
          <Button onClick={onClose}>Cancel</Button>
        </div>
      </form>
    </Dialog>
  )
}

/** The signing dialog, which runs the research's widget steps in order. */
function SignDialog({ signer, envelope, busy, error, outcome, onClose, onSign, onVerify }) {
  const [mode, setMode] = useState('draw')
  const [typed, setTyped] = useState('')
  const [filename, setFilename] = useState('')
  const [token, setToken] = useState('')

  const needsVerification = signer.verification_required && !signer.verified
  const payload =
    mode === 'type'
      ? { text: typed || signer.name }
      : mode === 'upload'
        ? { filename: filename || 'signature.png' }
        : { strokes: 1 }

  return (
    <Dialog
      open
      title={`Sign as ${signer.role_label}`}
      description={signer.email}
      onClose={onClose}
    >
      <div className="space-y-4">
        {needsVerification ? (
          <section className="rounded-sm border border-warning/40 bg-warning/10 p-3">
            <p className="text-sm font-semibold text-foreground">
              {acceptanceFacts.verifyEmailLabel} before signing
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {acceptanceFacts.verificationWindowQuote}
            </p>
            <div className="mt-3 flex flex-wrap items-end gap-2">
              <Button disabled={busy} onClick={() => onVerify()} icon="audit">
                {acceptanceFacts.verifyEmailLabel}
              </Button>
              <div className="min-w-[16rem] flex-1">
                <Field label="Verification link" id="wf095-token">
                  <input
                    id="wf095-token"
                    className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 font-mono text-sm text-foreground focus:border-accent"
                    value={token}
                    onChange={(event) => setToken(event.target.value)}
                    placeholder="paste the token from the link"
                  />
                </Field>
              </div>
            </div>
            {outcome?.verification_link ? (
              <p className="mt-2 break-all font-mono text-xs text-foreground">
                Link: {outcome.verification_link}
              </p>
            ) : null}
          </section>
        ) : null}

        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault()
            onSign({ signatureMode: mode, signaturePayload: payload, verificationToken: token })
          }}
        >
          <Field label="Signature method" id="wf095-sign-mode">
            <select
              id="wf095-sign-mode"
              className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground focus:border-accent"
              value={mode}
              onChange={(event) => setMode(event.target.value)}
            >
              <option value="draw">{acceptanceFacts.drawLabel}</option>
              <option value="type">{acceptanceFacts.typeLabel}</option>
              <option value="upload">{acceptanceFacts.uploadLabel}</option>
            </select>
          </Field>

          {mode === 'type' ? (
            <Field label="Typed signature" id="wf095-typed">
              <input
                id="wf095-typed"
                className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground focus:border-accent"
                value={typed}
                onChange={(event) => setTyped(event.target.value)}
                placeholder={signer.name}
              />
            </Field>
          ) : null}

          {mode === 'upload' ? (
            <Field label="Uploaded file name" id="wf095-filename">
              <input
                id="wf095-filename"
                className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 font-mono text-sm text-foreground focus:border-accent"
                value={filename}
                onChange={(event) => setFilename(event.target.value)}
                placeholder="signature.png"
              />
            </Field>
          ) : null}

          {error ? (
            <div className="rounded-sm border border-destructive/40 bg-destructive/10 p-3">
              <p className="text-sm text-destructive">{error.detail}</p>
            </div>
          ) : null}

          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="primary" disabled={busy} icon="audit">
              Insert signature
            </Button>
            <Button onClick={onClose}>Cancel</Button>
          </div>
        </form>

        <EvidenceNote label="PDF cap" quote={acceptanceFacts.pdfSizeCapQuote} />
        <p className="text-xs text-muted-foreground">
          This document is {documentSize(envelope.document_size_bytes)} of a{' '}
          {envelope.pdf_size_cap_mb} MB cap.
        </p>
      </div>
    </Dialog>
  )
}

/** One envelope, with its signers, its rail and its event log. */
function EnvelopeCard({ envelope, events, busy, onSelect }) {
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-mono text-xs text-muted-foreground">{envelope.id}</p>
          <h3 className="mt-1 text-base font-semibold text-foreground">
            {envelope.quote_id || 'Quote not bound'}
          </h3>
          <p className="mt-1 text-xs text-muted-foreground">
            {plural(envelope.signer_count, 'signer')}, {envelope.signed_count} signed
            {envelope.reassign_allowed ? ', reassignment on' : ''}
            {envelope.identity_verification_required ? ', identity verification on' : ''}
          </p>
        </div>
        <div className="flex flex-col items-end gap-2">
          <StatusBadge status={envelope.signing_status} />
          <Button disabled={busy} onClick={() => onSelect(envelope)} icon="audit">
            Open
          </Button>
        </div>
      </div>

      <div className="mt-4">
        <SigningRail status={envelope.signing_status} />
      </div>

      <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">Document</dt>
          <dd className="mt-1 font-mono text-foreground">
            {documentSize(envelope.document_size_bytes)}
          </dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Signers required
          </dt>
          <dd className="mt-1 font-mono text-foreground">{envelope.hs_esign_num_signers_required}</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">Quota</dt>
          <dd className="mt-1 font-mono text-foreground">{envelope.quota_cost}</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">Sealed</dt>
          <dd className="mt-1 font-mono text-foreground">{envelope.sealed ? 'Yes' : 'No'}</dd>
        </div>
      </dl>

      {events.length ? (
        <details className="mt-4">
          <summary className="min-h-11 cursor-pointer text-sm font-medium text-foreground">
            {plural(events.length, 'event')} on this envelope
          </summary>
          <ul className="mt-2">
            {events.map((event) => (
              <EventRow key={event.id} event={event} />
            ))}
          </ul>
        </details>
      ) : null}
    </Card>
  )
}

/** The detail panel for one envelope: its rail, its signers and its evidence. */
function EnvelopeDetail({ envelope, events, busy, onClose, onSign, onReassign }) {
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-foreground">Signing envelope</h2>
          <p className="mt-1 font-mono text-xs text-muted-foreground">{envelope.id}</p>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={envelope.signing_status} />
          <Button onClick={onClose} icon="close">
            Close
          </Button>
        </div>
      </div>

      <div className="mt-4">
        <SigningRail status={envelope.signing_status} />
      </div>

      <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
        <div>
          <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">Quote</dt>
          <dd className="mt-1 break-all font-mono text-xs text-foreground">
            {envelope.quote_id || 'not bound'}
          </dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">Document</dt>
          <dd className="mt-1 break-all font-mono text-xs text-foreground">
            {envelope.document_id || 'not bound'}
          </dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Accepted by
          </dt>
          <dd className="mt-1 break-all font-mono text-xs text-foreground">
            {envelope.accepted_by || 'not accepted'}
          </dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Payment status
          </dt>
          <dd className="mt-1 font-mono text-xs text-foreground">
            {envelope.hs_payment_status || 'unchanged'}
          </dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Verification window
          </dt>
          <dd className="mt-1 font-mono text-xs text-foreground">
            {envelope.verification_window_minutes} minutes
          </dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Signing provider
          </dt>
          <dd className="mt-1 font-mono text-xs text-foreground">{envelope.signing_provider}</dd>
        </div>
      </dl>

      <h3 className="mt-6 text-base font-semibold text-foreground">Signers</h3>
      <ul className="mt-2">
        {envelope.signers.map((signer) => (
          <SignerRow
            key={signer.id}
            signer={signer}
            busy={busy}
            onSign={onSign}
            onReassign={onReassign}
          />
        ))}
      </ul>

      {envelope.in_signing_attachments?.length ? (
        <div className="mt-4">
          <EvidenceNote
            label="Attachments in the signing envelope"
            quote={acceptanceFacts.inSigningForcesEsignatureQuote}
          />
          <p className="mt-2 text-sm text-foreground">
            {envelope.in_signing_attachments.join(', ')}
          </p>
        </div>
      ) : null}

      <h3 className="mt-6 text-base font-semibold text-foreground">What this workflow decided</h3>
      <div className="mt-2 space-y-2">
        <EvidenceNote label="Identity duty" quote={envelope.authentication_owner} />
        <EvidenceNote
          label="Sealed copy"
          quote={envelope.sealed_copy_expiry_quote}
        />
        <EvidenceNote label="PDF export" quote={envelope.pdf_export_is_lossy_quote} />
        <EvidenceNote label="Contract creation" quote={envelope.contract_is_downstream} />
      </div>

      <h3 className="mt-6 text-base font-semibold text-foreground">Event log</h3>
      {events.length ? (
        <ul className="mt-2">
          {events.map((event) => (
            <EventRow key={event.id} event={event} />
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-sm text-muted-foreground">No signature events on this envelope yet.</p>
      )}
    </Card>
  )
}

export function AcceptancePage() {
  const summary = useAsync(() => acceptanceApi.summary(ROOM), [])
  const envelopes = useAsync(() => acceptanceApi.envelopes(ROOM), [])
  const decisions = useAsync(() => acceptanceApi.decisions(), [])

  const [outcome, setOutcome] = useState(null)
  const [busy, setBusy] = useState(false)
  const [authoring, setAuthoring] = useState(false)
  const [formError, setFormError] = useState(null)
  const [reassigning, setReassigning] = useState(null)
  const [signing, setSigning] = useState(null)
  const [signError, setSignError] = useState(null)
  const [signOutcome, setSignOutcome] = useState(null)
  const [selected, setSelected] = useState(null)
  const [events, setEvents] = useState([])

  const reload = useCallback(async () => {
    await Promise.all([summary.refetch(), envelopes.refetch()])
  }, [summary, envelopes])

  const loadEvents = useCallback(async (envelopeId) => {
    try {
      const body = await acceptanceApi.events(envelopeId)
      setEvents(body.events)
    } catch {
      setEvents([])
    }
  }, [])

  const select = useCallback(
    async (envelope) => {
      setSelected(envelope)
      setSignOutcome(null)
      setSignError(null)
      await loadEvents(envelope.id)
    },
    [loadEvents],
  )

  const openEnvelope = useCallback(
    async (payload) => {
      setBusy(true)
      setFormError(null)
      try {
        const { is_published: isPublished, ...body } = payload
        const envelope = await acceptanceApi.openEnvelope(body, {
          roomId: ROOM,
          isPublished,
          actor: 'dana',
        })
        setOutcome({
          status: 'ok',
          title: 'Envelope opened',
          detail: `${envelope.status_label}, ${plural(envelope.signer_count, 'signer')}.`,
        })
        setAuthoring(false)
        await reload()
        await select(envelope)
      } catch (error) {
        setFormError(error.errors || { detail: error.message })
      } finally {
        setBusy(false)
      }
    },
    [reload, select],
  )

  const viewEnvelope = useCallback(
    async (envelopeId) => {
      setBusy(true)
      try {
        await acceptanceApi.markViewed(envelopeId, { actor: 'ada' })
        setOutcome({ status: 'ok', title: 'Marked viewed', detail: 'The buyer opened the quote.' })
        await reload()
        await select((await acceptanceApi.envelope(envelopeId)))
      } catch (error) {
        setOutcome({ status: 'error', title: 'Could not mark viewed', detail: error.message })
      } finally {
        setBusy(false)
      }
    },
    [reload, select],
  )

  const requestVerification = useCallback(
    async (envelopeId) => {
      setBusy(true)
      setSignError(null)
      try {
        const body = await acceptanceApi.requestVerification(envelopeId, { actor: 'ada' })
        setSignOutcome(body)
        setOutcome({
          status: 'ok',
          title: 'Verification link sent',
          detail: `The window closes ${new Date(body.window.expires_at).toLocaleTimeString()}.`,
        })
      } catch (error) {
        setSignError(error)
      } finally {
        setBusy(false)
      }
    },
    [],
  )

  const sign = useCallback(
    async ({ signatureMode, signaturePayload, verificationToken }) => {
      setBusy(true)
      setSignError(null)
      try {
        const result = await acceptanceApi.sign(signing.signer.id, {
          signatureMode,
          signaturePayload,
          verificationToken,
          actor: signing.signer.role === 'buyer' ? 'ada' : 'dana',
        })
        if (result.outcome === 'signed') {
          setSignOutcome(null)
          setSigning(null)
          setOutcome({
            status: 'ok',
            title: result.activity_label || 'Signature recorded',
            detail: `The status is now ${result.envelope.status_label}.`,
          })
        } else {
          setSignOutcome(null)
          setOutcome({
            status: 'error',
            title: 'The signature was refused',
            detail: result.detail,
          })
        }
        await reload()
        if (selected) await select(await acceptanceApi.envelope(selected.id))
      } catch (error) {
        setSignError(error)
      } finally {
        setBusy(false)
      }
    },
    [reload, select, selected, signing],
  )

  const reassign = useCallback(
    async ({ name, email }) => {
      setBusy(true)
      try {
        const signer = await acceptanceApi.reassign(reassigning.id, { name, email, actor: 'ada' })
        setOutcome({
          status: 'ok',
          title: 'Quote reassigned',
          detail: `${signer.name} is now the signer on this envelope.`,
        })
        setReassigning(null)
        await reload()
        if (selected) await select(await acceptanceApi.envelope(selected.id))
      } catch (error) {
        setFormError(error)
      } finally {
        setBusy(false)
      }
    },
    [reassigning, reload, select, selected],
  )

  if (summary.loading && envelopes.loading) {
    return <Spinner label="Loading quote acceptance" />
  }
  if (summary.error) {
    return <ErrorNote error={summary.error} onRetry={summary.refetch} />
  }
  if (envelopes.error) {
    return <ErrorNote error={envelopes.error} onRetry={envelopes.refetch} />
  }

  const rows = envelopes.data?.envelopes || []
  const board = summary.data

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="font-display text-2xl font-semibold text-foreground">
            Collect acceptance by e-signature
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Countersignature, signer reassignment and identity verification. Every quote below is
            a signing envelope.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="primary"
            icon="plus"
            onClick={() => {
              setFormError(null)
              setAuthoring(true)
            }}
          >
            Open envelope
          </Button>
          <Button icon="refresh" onClick={reload}>
            Refresh
          </Button>
        </div>
      </header>

      <Outcome outcome={outcome} onDismiss={() => setOutcome(null)} />

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Envelopes" value={board.envelopes} icon="audit" />
        <StatCard
          label="Accepted"
          value={board.accepted}
          hint={`${board.signers_signed} of ${board.signers_total} signatures collected`}
          icon="audit"
        />
        <StatCard
          label="Outstanding signatures"
          value={board.signers_outstanding}
          hint={plural(board.signers_outstanding, 'party still owes', 'parties still owe')}
          icon="schema"
        />
        <StatCard
          label="Verification on"
          value={board.verification_required_envelopes}
          hint={`${acceptanceFacts.verificationWindowMinutes} minute window`}
          icon="database"
        />
      </section>

      <section className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          {rows.length ? (
            <div className="space-y-4">
              {rows.map((envelope) => (
                <EnvelopeCard
                  key={envelope.id}
                  envelope={envelope}
                  events={[]}
                  busy={busy}
                  onSelect={select}
                />
              ))}
            </div>
          ) : (
            <EmptyState
              title="No signing envelopes yet"
              description="Open one to collect a quote's acceptance by e-signature, countersignature and identity verification."
              action={
                <Button variant="primary" icon="plus" onClick={() => setAuthoring(true)}>
                  Open envelope
                </Button>
              }
            />
          )}
        </div>
        <div className="space-y-4">
          <QuotaPanel quota={board.quota} />
          <Card>
            <h3 className="text-base font-semibold text-foreground">What this workflow does not own</h3>
            <div className="mt-2 space-y-2">
              <EvidenceNote label="Identity duty" quote={board.authentication_owner} />
              <EvidenceNote label="Contract creation" quote={board.contract_is_downstream} />
              <EvidenceNote label="Countersigners" quote={board.countersigner_pool_quote} />
            </div>
          </Card>
        </div>
      </section>

      {selected ? (
        <EnvelopeDetail
          envelope={selected}
          events={events}
          busy={busy}
          onClose={() => setSelected(null)}
          onSign={(signer) => {
            setSignError(null)
            setSignOutcome(null)
            setSigning({ signer, envelope: selected })
          }}
          onReassign={(signer) => {
            setFormError(null)
            setReassigning(signer)
          }}
        />
      ) : null}

      {selected && selected.signing_status === 'pending_signature' ? (
        <Card>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <h3 className="text-base font-semibold text-foreground">
                The buyer has not opened this quote yet
              </h3>
              <p className="mt-1 text-sm text-muted-foreground">
                Viewing advances the status to viewed - pending signature and writes no activity row,
                because the research names four activities and this is not one of them.
              </p>
            </div>
            <Button disabled={busy} onClick={() => viewEnvelope(selected.id)} icon="audit">
              Mark viewed
            </Button>
          </div>
        </Card>
      ) : null}

      <section>
        <h2 className="text-lg font-semibold text-foreground">Decisions this workflow made</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          The research left these open. Each names the alternative that was rejected and what the
          rejection would have cost.
        </p>
        <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {(decisions.data?.decisions || []).map((decision) => (
            <Card key={decision.chosen}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <h3 className="text-sm font-semibold text-foreground">{decision.question}</h3>
                <Badge tone="insert">{decision.chosen}</Badge>
              </div>
              <p className="mt-2 text-xs text-muted-foreground">
                <span className="font-medium text-foreground">Chosen:</span> {decision.chosen}
              </p>
              <p className="mt-2 text-xs text-muted-foreground">{decision.rejected_because}</p>
              {decision.audit_id ? (
                <p className="mt-2 font-mono text-xs text-muted-foreground">{decision.audit_id}</p>
              ) : null}
            </Card>
          ))}
        </div>
      </section>

      {authoring ? (
        <OpenEnvelopeForm
          busy={busy}
          error={formError}
          onClose={() => setAuthoring(false)}
          onSubmit={openEnvelope}
        />
      ) : null}

      {reassigning ? (
        <ReassignForm
          signer={reassigning}
          busy={busy}
          error={formError}
          onClose={() => setReassigning(null)}
          onSubmit={reassign}
        />
      ) : null}

      {signing ? (
        <SignDialog
          signer={signing.signer}
          envelope={signing.envelope}
          busy={busy}
          error={signError}
          outcome={signOutcome}
          onClose={() => setSigning(null)}
          onSign={sign}
          onVerify={() => requestVerification(signing.envelope.id)}
        />
      ) : null}
    </div>
  )
}

export default {
  id: 'wf-095-collect-acceptance-by-e-signature-countersignature',
  label: 'Quote acceptance',
  icon: 'audit',
  order: 950,
  Component: AcceptancePage,
}
