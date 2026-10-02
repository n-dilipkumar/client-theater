import { useEffect, useState } from 'react'

import { relativeTime } from '@/lib/api'
import { Badge, Button, Card, Field, Icon, Spinner, inputClass, useAsync } from '@/components/ui'

import { accessApi, getRecord, listRecords } from './api'
import { ICONS } from './icons'
import { CopyField, Notice, PathButton } from './primitives'

/**
 * The buyer-facing gate for WF-015.
 *
 * The important property is negative: the room's content is never fetched until the
 * API has granted access. Nothing is hidden with CSS and nothing is fetched-then-
 * obscured, so "gated" means the request was never made.
 *
 * A finding this port could not resolve, and it belongs to whoever owns `App.jsx`:
 * the branch added a `BUYER_ROUTE` branch to `App.jsx` so a buyer following an
 * emailed verification link got this page and none of the seller's chrome.
 * `App.jsx` is a shared file, so instead the feature's own descriptor renders this
 * component when the hash is a buyer route. The gate is correct and the content is
 * still not requested; the seller's sidebar is simply visible alongside it. The fix
 * is a few lines in `App.jsx` and a decision about whether a buyer-facing route
 * belongs in the product's single-page shell at all.
 */

const REFUSAL_COPY = {
  domain_not_allowed: {
    title: 'This room is restricted',
    body: 'The address you entered is not on the list for this room. Ask the sender which address they invited.',
  },
  likely_bot: {
    title: 'Request not recognised',
    body: 'This did not look like a browser opening the link. If that is wrong, ask the sender to email you the room directly.',
  },
  session_expired: {
    title: 'This link has expired',
    body: 'Verification links are short-lived. Ask the sender to send a new one.',
  },
  too_many_attempts: {
    title: 'This link is no longer valid',
    body: 'The link was opened too many times and has been retired. Ask the sender for a new one.',
  },
  unknown_token: {
    title: 'This link is not valid',
    body: 'The link may have been cut short when it was copied. Ask the sender to resend it.',
  },
  missing_token: {
    title: 'This link is not valid',
    body: 'The link is missing its verification token.',
  },
}

/** The token lives in sessionStorage, scoped to the room it belongs to. */
function tokenKey(roomId) {
  return `dsr.access.${roomId}`
}

function readToken(roomId) {
  try {
    return window.sessionStorage.getItem(tokenKey(roomId)) || ''
  } catch {
    return ''
  }
}

/** The token a shared link carries, if any. */
function tokenFromUrl() {
  const params = new URLSearchParams(window.location.hash.split('?')[1] || '')
  return params.get('token') || ''
}

function writeToken(roomId, token) {
  try {
    if (token) window.sessionStorage.setItem(tokenKey(roomId), token)
    else window.sessionStorage.removeItem(tokenKey(roomId))
  } catch {
    // Private browsing can refuse storage; the page still works, it just
    // cannot remember the token across a reload.
  }
}

export default function Gate({ roomId }) {
  const [form, setForm] = useState({ name: '', email: '' })
  const [method, setMethod] = useState('identified')
  const [submitting, setSubmitting] = useState(false)
  const [notice, setNotice] = useState(null)

  // The token the link carries wins over the remembered one. Reading the URL
  // during render is safe -- it is a synchronous read of the current location --
  // so a shared link needs no effect to notice it.
  const [urlToken] = useState(tokenFromUrl)

  // Persisting a token from the URL is genuinely an external-system write, so
  // it stays an effect. It writes to sessionStorage and to no React state, which
  // is exactly the shape an effect is for.
  useEffect(() => {
    if (urlToken) writeToken(roomId, urlToken)
  }, [roomId, urlToken])

  // What the gate checks is a fetch, and `useAsync` is what this repo provides
  // for a fetch: it owns the loading and error state, refetches when its deps
  // change, and exposes `refetch` for a manual reload. The previous version
  // hand-rolled all of that into `useState` plus an effect that called a
  // `load` which set state synchronously on entry -- the cascading-render shape
  // this replaces.
  //
  // Keying the token into the deps is what keeps `refetch()` honest after a
  // submit that mints a new one.
  const [tokenVersion, setTokenVersion] = useState(0)
  const gate = useAsync(
    async () => {
      const token = urlToken || readToken(roomId)
      const [answer, room] = await Promise.all([
        accessApi.check(roomId, token || undefined),
        getRecord('room', roomId),
      ])
      return { check: answer, room }
    },
    [roomId, urlToken, tokenVersion],
  )

  // The form's fields are only worth fetching once the check says they are.
  // Deriving that from `check` rather than from a flag set inside the fetch
  // means the requirements arrive with the check rather than a render later.
  const answer = gate.data?.check
  const needsForm =
    answer?.status === 'required' || answer?.status === 'pending' || undefined
  const requirements = useAsync(
    () => (needsForm ? accessApi.requirements(roomId) : Promise.resolve(null)),
    [needsForm, roomId],
  )

  const state = {
    loading: gate.loading,
    check: gate.data?.check || null,
    room: gate.data?.room || null,
  }
  const error = gate.error

  async function submit(event) {
    event.preventDefault()
    setSubmitting(true)
    setNotice(null)
    try {
      const result = await accessApi.submit(roomId, { ...form, method })
      if (result.token) {
        writeToken(roomId, result.token)
        // The check must be re-run against the token just issued, not the one
        // remembered before the submit. Bumping the version re-runs `check`
        // with the new token in storage.
        setTokenVersion((n) => n + 1)
      }
      if (result.status === 'pending_verification') {
        setNotice({
          tone: 'info',
          title: 'Check your email',
          body: result.message,
          link: result.delivered_via === 'outbox' ? result.open_link || result.link : null,
          expires: result.expires_at,
        })
      }
      } catch (failure) {
      setNotice({ tone: 'bad', title: 'That did not work', body: failure.message })
    } finally {
      setSubmitting(false)
    }
  }

  function useAccount() {
    setMethod('account_login')
    setNotice({
      tone: 'info',
      title: 'Signed in from your account',
      body: 'Your name and email have been filled in from the account you signed in with. Check they are right, then continue.',
    })
  }

  if (state.loading) {
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <Spinner label="Checking access" />
      </div>
    )
  }

  if (error && !state.check) {
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <Notice tone="bad" title="This room could not be opened">
          {error.message}
        </Notice>
      </div>
    )
  }

  const check = state.check || {}
  const room = state.room?.data || {}
  const requirementsData = requirements.data
  const fields = requirementsData?.fields || []
  // The machine-readable reason on the 403, falling back to the stored one. Four
  // refusals read very differently to a person and none of them is "error".
  const refusal = REFUSAL_COPY[error?.code || check.refusal_reason]

  return (
    <div className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-5 p-5 sm:p-8">
      <header>
        <p className="font-mono text-xs tracking-wide text-muted-foreground uppercase">
          Digital Sales Room
        </p>
        <h1 className="mt-1 font-mono text-2xl font-semibold">{room.name || 'Sales room'}</h1>
        {room.account && <p className="mt-1 text-sm text-muted-foreground">{room.account}</p>}
      </header>

      {check.status === 'granted' && (
        <>
          <Notice tone="good" title="Access granted">
            {check.verified
              ? 'Your email address is verified, so this view is attributed to you.'
              : 'Your details have been recorded for this room.'}
            {check.identity?.email && (
              <p className="mt-1">
                Viewing as <span className="font-mono text-xs">{check.identity.email}</span>
                {check.viewed_at && (
                  <span className="text-muted-foreground">
                    {' '}
                    · first view {relativeTime(check.viewed_at)}
                  </span>
                )}
              </p>
            )}
          </Notice>
          <RoomContent roomId={roomId} />
        </>
      )}

      {check.status === 'pending' && (
        <Card>
          <h2 className="font-mono text-base font-semibold">Waiting for your verification</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Open the link we sent to your email address and this page will open. The link works
            once{check.expires_at && <> and expires {relativeTime(check.expires_at)}</>}.
          </p>
          {notice?.link && (
            <div className="mt-4">
              <Notice tone="info" title={notice.title}>
                <p>{notice.body}</p>
              </Notice>
              <div className="mt-3">
                <CopyField
                  value={notice.link}
                  label="No mail server on this install — open the link yourself"
                />
              </div>
            </div>
          )}
        </Card>
      )}

      {check.status === 'required' && (
        <Card>
          <h2 className="font-mono text-base font-semibold">
            {requirementsData?.requires_verification
              ? 'Verify your email to continue'
              : 'Tell us who you are'}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            {requirementsData?.requires_verification
              ? 'We will email you a link. Once you open it, the room unlocks.'
              : 'Your details are recorded against this room so the sender knows who has been through.'}
            {requirementsData?.domain_security &&
              ' This room also restricts access to approved email domains.'}
          </p>

          <form onSubmit={submit} className="mt-4 space-y-4">
            {fields.includes('name') && (
              <Field label="Your name" id="gate-name">
                <input
                  id="gate-name"
                  className={inputClass}
                  value={form.name}
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                  autoComplete="name"
                />
              </Field>
            )}
            {fields.includes('email') && (
              <Field label="Your email address" id="gate-email">
                <input
                  id="gate-email"
                  type="email"
                  className={inputClass}
                  value={form.email}
                  onChange={(event) => setForm({ ...form, email: event.target.value })}
                  autoComplete="email"
                  required
                />
              </Field>
            )}

            {error && (
              <Notice tone="bad" title={refusal ? refusal.title : 'That did not work'}>
                <p>{refusal ? refusal.body : error.message}</p>
              </Notice>
            )}
            {notice && !error && (
              <Notice tone="info" title={notice.title}>
                <p>{notice.body}</p>
              </Notice>
            )}

            <div className="flex flex-wrap gap-2">
              <Button type="submit" variant="primary" disabled={submitting}>
                {submitting
                  ? 'Checking…'
                  : requirementsData?.requires_verification
                    ? 'Send me the link'
                    : 'Continue'}
              </Button>
              {requirementsData?.allows_account_login && (
                <PathButton glyph={ICONS.inbox} onClick={useAccount}>
                  Use my existing account
                </PathButton>
              )}
            </div>
          </form>
        </Card>
      )}

      {(check.status === 'refused' || check.status === 'expired') && (
        <Notice tone="bad" title={refusal ? refusal.title : 'Access not granted'}>
          <p>{refusal ? refusal.body : 'This room is not available to you.'}</p>
        </Notice>
      )}

      <footer className="flex items-center gap-2 text-xs text-muted-foreground">
        <Icon path={ICONS.shield} size={14} />
        <span>Access is checked on every request, and recorded in the sender's audit log.</span>
      </footer>
    </div>
  )
}

/**
 * The gated content, fetched only once access is granted.
 *
 * This is the property the whole workflow rests on, so the fetch is inside the
 * granted branch and nowhere else: there is no "load it, then decide" path and no
 * CSS that hides it.
 */
function RoomContent({ roomId }) {
  const [documents, setDocuments] = useState({ loading: true, records: [] })

  useEffect(() => {
    listRecords('document', { where: JSON.stringify({ room_id: roomId }), limit: 50 })
      .then((body) => setDocuments({ loading: false, records: body.records || [] }))
      .catch(() => setDocuments({ loading: false, records: [] }))
  }, [roomId])

  if (documents.loading) return <Spinner label="Loading the room" />

  if (documents.records.length === 0) {
    return (
      <Card>
        <p className="text-sm text-muted-foreground">
          There is nothing published in this room yet.
        </p>
      </Card>
    )
  }

  return (
    <ul className="space-y-3">
      {documents.records.map((document) => (
        <li key={document.id}>
          <Card className="card-hover">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h3 className="truncate font-mono text-sm font-semibold text-foreground">
                  {document.data.title || 'Untitled'}
                </h3>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {document.data.kind || 'document'}
                  {document.data.pages ? ` · ${document.data.pages} pages` : ''}
                </p>
              </div>
              {!document.data.public && <Badge tone="restore">private</Badge>}
            </div>
          </Card>
        </li>
      ))}
    </ul>
  )
}
