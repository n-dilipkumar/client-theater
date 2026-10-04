import { useState } from 'react'

import { ndaApi, refusalText } from './api'
import { Notice } from './primitives'
import { SEAL_ICON } from './icons'
import {
  Button,
  Card,
  ErrorNote,
  Field,
  Icon,
  Spinner,
  inputClass,
  useAsync,
} from '@/components/ui'

/**
 * The viewer's screen: read the agreement, accept it, and only then ask for content.
 *
 * Why this is a separate component
 * --------------------------------
 *
 * The seller wants a board. The viewer wants a block of legal text and one button.
 * They are the same workflow with two audiences, and putting both on one page would
 * mean the buyer is shown a seller's dashboard with someone's pipeline on it.
 *
 * The security property does not depend on this page at all: no room content is
 * requested until the API grants it, and the API grants nothing without a matching
 * acceptance. This page is the honest face of a rule the server already enforces.
 * That is worth saying because it means a bug here cannot leak the room.
 *
 * Three states this has to get right
 * ----------------------------------
 *
 * 1. Not accepted. The agreement, and a button that does nothing until the viewer
 *    has actually read it. Requiring a scroll past the end is the only "I have read
 *    this" signal available without a signature, and it is a weak one, so the copy
 *    does not overstate it.
 * 2. Accepted, but the text was amended since. The refusal reason is
 *    `agreement_changed`, not `agreement_not_accepted`, and the page says so. Sending
 *    the viewer back to the same box to click the same button would be a loop.
 * 3. The link is closed. Expired and revoked are worded identically by the API, and
 *    this page does not distinguish them either.
 */
export default function AgreementGate({ linkId }) {
  const [sessionId, setSessionId] = useState(null)
  const [email, setEmail] = useState('')
  const [readToEnd, setReadToEnd] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState(null)
  const [accepted, setAccepted] = useState(false)

  const gate = useAsync(() => ndaApi.gate(linkId), [linkId])
  // The content is fetched only once an acceptance has actually been recorded.
  // Gating this on `sessionId` alone asked for the room the moment the viewer chose
  // to read the agreement, which is the request the research says must not happen:
  // "Before any document renders, is shown the NDA." The server refuses it, so
  // nothing leaked, but a page that asks for what it is not entitled to is a page
  // that will leak the day the server check is refactored. `accepted` is the only
  // signal that means the gate opened.
  const content = useAsync(
    () => (accepted && sessionId ? ndaApi.content(linkId, sessionId) : Promise.resolve(null)),
    [sessionId, accepted],
  )

  const body = gate.data

  if (gate.loading) return <Spinner label="Opening the link" />
  if (gate.error) return <ViewerClosed reason={gate.error.reason} />
  if (!body) return <ErrorNote error={new Error('No gate state came back.')} onRetry={gate.refetch} />

  if (!body.gate_required) {
    return (
      <ViewerShell title={body.title}>
        <Notice tone="success" title="This link is open">
          No agreement is required, so nothing is asked of you before the room loads.
        </Notice>
      </ViewerShell>
    )
  }

  async function accept() {
    setBusy(true)
    setFailure(null)
    try {
      await ndaApi.accept(linkId, sessionId, email || undefined)
      setAccepted(true)
    } catch (error) {
      setFailure(error)
      // A superseded acceptance needs a fresh session, because the session the
      // viewer holds is the one bound to the text they have to re-read.
      if (error.reason === 'agreement_changed') await restart()
    } finally {
      setBusy(false)
    }
  }

  async function restart() {
    const reopened = await ndaApi.gate(linkId)
    // Only the session id is kept. The cleartext token comes back once and is not
    // stored: the API identifies the viewer by session id, and a token sitting in
    // component state is one more copy of a credential to leak through a screenshot.
    setSessionId(reopened.session_id)
    setReadToEnd(false)
    setAccepted(false)
  }

  if (!sessionId) {
    // Minting the viewer session is a request of its own, so it happens on a click
    // rather than on render. A page that silently opened a session the moment it
    // loaded would write a row for every prefetch and every crawler.
    return (
      <ViewerShell title={body.title}>
        <Card>
          <div className="flex items-start gap-3">
            <span className="rounded-sm bg-accent-soft p-2 text-accent">
              <Icon path={SEAL_ICON} size={20} />
            </span>
            <div className="min-w-0">
              <h2 className="text-base font-semibold text-foreground">
                {body.agreement?.title}
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Read the agreement before the room opens. Nothing from the room loads until you
                accept it.
              </p>
            </div>
          </div>

          <Button
            variant="primary"
            className="mt-4 w-full sm:w-auto"
            icon="chevron"
            onClick={restart}
          >
            Read the agreement
          </Button>
        </Card>
      </ViewerShell>
    )
  }

  return (
    <ViewerShell title={body.title}>
      <div className="space-y-4">
        {failure ? (
          <Notice tone="warning" title={refusalText(failure.reason)}>
            {failure.reason === 'agreement_changed'
              ? 'The sender changed the agreement since you last accepted it. The current wording is below.'
              : failure.message}
          </Notice>
        ) : null}

        <Card>
          <h2 className="font-display text-lg font-semibold text-foreground">
            {body.agreement?.title}
          </h2>
          {body.agreement?.governing_law ? (
            <p className="mt-0.5 text-xs text-muted-foreground">
              Governing law: {body.agreement.governing_law}
            </p>
          ) : null}

          <div
            className="mt-4 max-h-[26rem] overflow-y-auto whitespace-pre-wrap rounded-sm border border-border-subtle bg-muted p-4 font-mono text-[13px] leading-relaxed text-foreground"
            tabIndex={0}
            role="region"
            aria-label="Agreement text"
            onScroll={(event) => {
              const element = event.currentTarget
              if (element.scrollTop + element.clientHeight >= element.scrollHeight - 4) {
                setReadToEnd(true)
              }
            }}
          >
            {body.agreement?.body}
          </div>

          <p className="mt-2 text-xs text-muted-foreground">
            Version {body.agreement?.version}. Accepting records that you read this wording,
            against this link.
          </p>
        </Card>

        <Card>
          <Field
            label="Your email address"
            id="wf070-viewer-email"
            hint="Optional. It is recorded with your acceptance so the sender knows who read it."
          >
            <input
              id="wf070-viewer-email"
              className={inputClass}
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </Field>

          <label className="mt-4 flex min-h-11 items-center gap-3 text-sm text-foreground">
            <input
              type="checkbox"
              className="h-4 w-4"
              checked={readToEnd}
              onChange={(event) => setReadToEnd(event.target.checked)}
            />
            I have read the agreement above.
          </label>

          <Button
            variant="primary"
            className="mt-4"
            icon="plus"
            disabled={!readToEnd || busy}
            onClick={accept}
          >
            {busy ? 'Recording' : 'Accept and open the room'}
          </Button>
          {!readToEnd ? (
            <p className="mt-2 text-xs text-muted-foreground">
              Scroll to the end of the agreement, or tick the box, to enable this.
            </p>
          ) : null}
        </Card>

        {accepted && content.loading ? <Spinner label="Opening the room" /> : null}
        {content.error ? (
          <Notice tone="warning" title={refusalText(content.error.reason)} />
        ) : null}
        {content.data ? <Released content={content.data} /> : null}
      </div>
    </ViewerShell>
  )
}

function Released({ content }) {
  return (
    <Card>
      <div className="flex items-start gap-3">
        <span className="rounded-sm bg-accent-soft p-2 text-accent">
          <Icon path={SEAL_ICON} size={20} />
        </span>
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-foreground">Agreement accepted</h2>
          <p className="mt-1 text-sm text-muted-foreground">{content.note}</p>
        </div>
      </div>

      {content.remaining_gates?.length ? (
        <Notice tone="info" title="One more step before the room opens">
          This link also asks for {content.remaining_gates.join(' and ')}. The sender will
          provide that separately.
        </Notice>
      ) : null}

      <div className="mt-4 rounded-sm border border-border-subtle bg-surface p-4">
        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          Shared content
        </p>
        {content.resolved ? (
          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap font-mono text-[13px] text-foreground">
            {JSON.stringify(content.content, null, 2)}
          </pre>
        ) : (
          <p className="mt-2 text-sm text-muted-foreground">
            The sender has not published anything to this link yet.
          </p>
        )}
      </div>
    </Card>
  )
}

function ViewerShell({ title, children }) {
  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-8">
      <h1 className="font-display text-2xl font-semibold text-foreground">
        {title || 'Shared with you'}
      </h1>
      <div className="mt-6">{children}</div>
    </main>
  )
}

function ViewerClosed({ reason }) {
  return (
    <ViewerShell>
      <Card>
        <Notice tone="warning" title={refusalText(reason)}>
          Nothing from the room is available on this link.
        </Notice>
      </Card>
    </ViewerShell>
  )
}