import { useState } from 'react'

import { Button, Card, Field, Spinner, inputClass, useAsync } from '@/components/ui'
import { gateApi } from './api'
import { MAIL_ICON, VERIFIED_ICON } from './icons'
import { Notice } from './primitives'

/**
 * The buyer side of WF-069: the page a buyer sees after following an emailed link.
 *
 * It is rendered by the descriptor when the hash is `#/view-link/<link_id>`, which is
 * the URL a rep copies from the link board. Nothing is requested from the document API
 * until the gate has granted - that ordering is the security property, and it is why
 * this component reads `/gate` first and the content last, and never in between.
 *
 * The copy on the closed page is deliberately identical for an expired link and a
 * revoked one. The researched rule is "anyone with the URL gets the expired page on
 * their next request", and a message that said "this link was withdrawn" would tell a
 * buyer holding a forwarded URL that they were cut off.
 *
 * One read, then arithmetic. The gate is fetched once on mount and never refetched
 * between steps, because each step's response already says which step comes next.
 * Refetching after every step would mean a second round trip per field and a
 * page-level spinner flash between two adjacent inputs, in exchange for information
 * the response already carried. "Try again" on a failed load is the one refetch, and
 * it goes through the shared `useAsync`.
 */

const STEP_TITLE = {
  email: 'Who is this link for?',
  code: 'Check your email',
  password: 'Enter the link password',
}

const STEP_HINT = {
  email: 'The sender sees this address against every view of the link.',
  code: 'We sent a one-time code to that address. It is good for this visit only.',
  password: 'The sender chose this password when they created the link.',
}

const BUTTON_LABEL = {
  email: 'Continue',
  code: 'Confirm code',
  password: 'Open the link',
}

/** The progress indicator. Never a spinner: the buyer can see how much is left. */
function Steps({ required, current }) {
  if (!required.length) return null
  const index = required.indexOf(current)
  return (
    <ol className="flex flex-wrap gap-2" aria-label="Steps to open this link">
      {required.map((step, position) => {
        const state = position < index ? 'done' : position === index ? 'now' : 'todo'
        return (
          <li
            key={step}
            aria-current={state === 'now' ? 'step' : undefined}
            className={`rounded-xs border px-2 py-0.5 font-mono text-xs ${
              state === 'done'
                ? 'border-success/30 bg-success/10 text-success'
                : state === 'now'
                  ? 'border-accent bg-accent-soft text-accent'
                  : 'border-border-subtle text-muted-foreground'
            }`}
          >
            {position + 1}. {step}
          </li>
        )
      })}
    </ol>
  )
}

export default function Gate({ linkId }) {
  const { data: loaded, loading, error: loadError, refetch } = useAsync(
    () => gateApi.gate(linkId),
    [linkId],
  )

  // The step the buyer is on, once a step has answered. Null means "whatever the
  // server said when the page opened".
  const [advance, setAdvance] = useState(null)
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [challengeId, setChallengeId] = useState(null)
  const [verified, setVerified] = useState(false)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState(null)
  const [document, setDocument] = useState(null)

  const state = advance ?? loaded

  /** Run one gate step, then move on using what it told us. */
  async function run(submit, onSuccess) {
    setBusy(true)
    setProblem(null)
    try {
      const response = await submit()
      // A grant is not a step forward - it is the end of the walk. Advancing the state
      // on it made the page fall through to the "nothing is asked" branch and tell a
      // buyer who had just typed the right password that the link was not gated.
      if (!response.granted) {
        setAdvance((previous) => ({
          ...(previous ?? state),
          step: response.step,
          message: response.message ?? null,
        }))
      }
      onSuccess(response)
    } catch (error) {
      // `reason` is a stable token; the message is what the buyer reads.
      setProblem(error)
      // A refused code is spent - one wrong answer burns it - so the buyer has to
      // start again from their address. Saying so beats letting them retype a code
      // that can never match.
      if (error.reason === 'code_rejected') {
        setChallengeId(null)
        setVerified(false)
        setCode('')
      }
    } finally {
      setBusy(false)
    }
  }

  if (loading && !state) return <Spinner label="Checking this link" />

  if (loadError && !state) {
    return (
      <Card className="mx-auto max-w-lg">
        <Notice tone="destructive" title="This link could not be opened">
          {loadError.message}
        </Notice>
        <Button className="mt-4" onClick={refetch}>
          Try again
        </Button>
      </Card>
    )
  }

  if (!state) return null

  if (state.step === 'expired') {
    return (
      <Card className="mx-auto max-w-lg">
        <Notice tone="warning" title="This link is closed">
          {state.message}
        </Notice>
      </Card>
    )
  }

  if (state.step === 'open' && !document) {
    return (
      <Card className="mx-auto max-w-lg">
        <h1 className="font-display text-lg font-semibold text-foreground">{state.title}</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          This link is not gated, so there is nothing to ask before you open it.
        </p>
      </Card>
    )
  }

  // A refused code puts the buyer back at the address step, which is where they have
  // to restart from.
  const current = problem?.reason === 'code_rejected' ? state.steps[0] : state.step
  const done = state.steps[state.steps.length - 1]

  return (
    <Card className="mx-auto max-w-lg">
      <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">{state.title}</p>
      <h1 className="mt-1 font-display text-xl font-semibold text-foreground">
        {document ? 'Opened' : STEP_TITLE[current]}
      </h1>
      <p className="mt-1 text-sm text-muted-foreground">{STEP_HINT[current]}</p>

      <div className="mt-4">
        <Steps required={state.steps} current={document ? done : current} />
      </div>

      {problem && (
        <div className="mt-4">
          <Notice tone="destructive" title="Not quite">
            {problem.message}
          </Notice>
        </div>
      )}

      {document ? (
        <div className="mt-6">
          <Notice tone="success" title="You are through the gate">
            <span className="flex flex-wrap items-center gap-2">
              {document.viewer.email_verified && (
                <span className="inline-flex items-center gap-1 font-mono text-xs">
                  <svg
                    className="h-3.5 w-3.5"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    aria-hidden="true"
                  >
                    <path d={VERIFIED_ICON} />
                  </svg>
                  {document.viewer.email}
                </span>
              )}
              <span>Opened as {document.viewer.email || 'an anonymous reader'}.</span>
            </span>
          </Notice>
          {document.resolved ? (
            <div className="mt-4 rounded-sm border border-border-subtle p-4">
              <p className="text-sm font-semibold text-foreground">{document.content?.title || 'Document'}</p>
              <p className="mt-1 text-sm text-muted-foreground">
                The gate released this content. Rendering it is the document library&rsquo;s job, not this
                one&rsquo;s.
              </p>
            </div>
          ) : (
            <Notice tone="info" title="Nothing to show yet">
              The gate is open, but {state.target?.id} is not in this workspace.
            </Notice>
          )}
        </div>
      ) : (
        <form
          className="mt-6 space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (current === 'email') {
              run(
                () => gateApi.submitEmail(linkId, email),
                (response) => setChallengeId(response.challenge_id),
              )
            } else if (current === 'code') {
              run(
                () => gateApi.submitCode(linkId, challengeId, code),
                () => setVerified(true),
              )
            } else {
              run(async () => {
                const grant = await gateApi.submitPassword(linkId, password, challengeId)
                // The token is used once, immediately, and then forgotten. Keeping it
                // in state would put a live credential in a component that re-renders
                // for unrelated reasons.
                setDocument(await gateApi.document(linkId, grant.view_token))
                return grant
              }, () => {})
            }
          }}
        >
          {current === 'email' && (
            <Field label="Your email address" id="wf069-email" hint={problem?.errors?.email}>
              <div className="flex items-center gap-2">
                <svg
                  className="h-4 w-4 shrink-0 text-muted-foreground"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  aria-hidden="true"
                >
                  <path d={MAIL_ICON} />
                </svg>
                <input
                  id="wf069-email"
                  type="email"
                  autoComplete="email"
                  className={inputClass}
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                />
              </div>
            </Field>
          )}

          {current === 'code' && (
            <>
              <Notice tone="info" title="Code sent">
                If that address is one the sender meant to reach, a six-digit code is on its way. It is good for
                this visit only, and it is never stored or shown in the clear.
              </Notice>
              <Field label="Six-digit code" id="wf069-code">
                <input
                  id="wf069-code"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  className={`${inputClass} font-mono tracking-[0.3em]`}
                  value={code}
                  onChange={(event) => setCode(event.target.value)}
                />
              </Field>
            </>
          )}

          {current === 'password' && (
            <Field
              label="Link password"
              id="wf069-password"
              hint={
                verified
                  ? 'Your email is confirmed. The password is the last step.'
                  : 'The sender chose this when they created the link.'
              }
            >
              <input
                id="wf069-password"
                type="password"
                autoComplete="current-password"
                className={inputClass}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </Field>
          )}

          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? 'Checking…' : BUTTON_LABEL[current]}
          </Button>
        </form>
      )}
    </Card>
  )
}
