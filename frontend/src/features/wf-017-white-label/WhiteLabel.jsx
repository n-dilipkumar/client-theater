import { useRef, useState } from 'react'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Icon,
  Spinner,
  inputClass,
  useAsync,
} from '@/components/ui'
import { whiteLabelApi } from './api'
import { CopyButton, Glyph, StatusPill } from './primitives'

/**
 * White-label rooms on a custom domain: the operator's screen.
 *
 * The layout follows the researched flow in the researched order, because the
 * order is the point: add the CNAME, wait for it to propagate, enter the domain,
 * let the service verify it, then save. Each step is a step, not a field on a
 * settings form, because a CNAME that has not propagated yet is the *normal*
 * state here rather than an error, and a form that hides the order hides that.
 *
 * Two things are deliberately not editable from this screen, because the
 * research says they are not the customer's to change:
 *
 *   - the link secret, a "unique, non-removable identifier ... for security
 *     purposes". It appears inside the share link so it can be recognised in a
 *     support conversation, and there is no control that implies it can be
 *     changed, because there is not one.
 *   - the collaborator URL, which "won't use the custom domain, as they are for
 *     internal use only", so it sits under an internal heading and is visibly
 *     not a buyer link.
 *
 * The page does not read `window.location.pathname` itself. Whether this
 * component renders the operator's screen or the buyer's room is decided in
 * `./index.jsx`, which is where the path matcher and the reason it exists live.
 */

const DNS_STEPS = [
  { key: 'name', label: 'Name', note: 'the subdomain you want, e.g. proposals' },
  { key: 'type', label: 'Type', note: 'CNAME' },
  { key: 'value', label: 'Value', note: 'the canonical target shown below' },
]

/** The brand tokens this form edits. Anything else under `branding` is the
 *  team's own and is displayed but never written by this form. */
const BRAND_FIELDS = ['primary', 'accent', 'heading_font', 'body_font']

/** The empty form. A fresh object per use, so one room's typing can never be
 *  visible in another's. */
const EMPTY_BRANDING = Object.fromEntries(BRAND_FIELDS.map((field) => [field, '']))

/** The stored tokens this form can edit, as strings.
 *
 *  A stored value that is not a string becomes an empty field rather than the
 *  raw value: `String({})` is `"[object Object]"`, which would then be sent back
 *  as a brand token. `branding` is an open JSON field, so that is a real
 *  possibility, and an empty field is the honest rendering of "not a token I can
 *  show or edit". */
function tokenStrings(branding) {
  return Object.fromEntries(
    BRAND_FIELDS.map((field) => [field, typeof branding?.[field] === 'string' ? branding[field] : '']),
  )
}

/** The DNS record the operator has to create, as a labelled list. */
function StepList({ steps, target }) {
  return (
    <ol className="space-y-2">
      {steps.map((step, index) => (
        <li key={step.key} className="flex gap-3 text-sm">
          <span
            aria-hidden="true"
            className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-muted font-mono text-xs text-muted-foreground"
          >
            {index + 1}
          </span>
          <span className="min-w-0">
            <span className="font-medium text-foreground">
              {step.label}
              {step.key === 'value' && target ? (
                <code className="ml-2 rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-accent">
                  {target}
                </code>
              ) : null}
            </span>
            <span className="block text-muted-foreground">{step.note}</span>
          </span>
        </li>
      ))}
    </ol>
  )
}

/**
 * Every verification check, with what it saw.
 *
 * A check that merely failed is not actionable; "resolves to somewhere.else.test"
 * is. The list is a live region so the result of a check is announced when it
 * arrives, because the check is the reason the operator pressed the button.
 */
function CheckList({ checks }) {
  return (
    <ul className="space-y-2" aria-live="polite" aria-label="Domain verification checks">
      {checks.map((check) => (
        <li key={check.name} className="flex items-start gap-2.5 text-sm">
          <span className={`mt-0.5 shrink-0 ${check.ok ? 'text-accent' : 'text-destructive'}`}>
            <Glyph name={check.ok ? 'check' : 'alert'} size={16} />
          </span>
          <span className="min-w-0">
            <span className="text-foreground">{check.label}</span>
            <span className="block font-mono text-xs break-words text-muted-foreground">
              {check.detail}
            </span>
          </span>
        </li>
      ))}
    </ul>
  )
}

export default function WhiteLabel() {
  const rooms = useAsync(() => whiteLabelApi.rooms(), [])
  const config = useAsync(() => whiteLabelApi.config(), [])

  const [roomId, setRoomId] = useState('')
  const [state, setState] = useState(null)
  const [domainInput, setDomainInput] = useState('')
  const [report, setReport] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [branding, setBranding] = useState(EMPTY_BRANDING)
  // Monotonic token identifying the newest load. A response whose token is no
  // longer the latest is stale and discards itself. This is the `cancelled`
  // flag the docstring describes, implemented so that something actually
  // cancels: the old code declared `let cancelled = false` and never assigned
  // it, so the guard it was written for never fired.
  const latestLoad = useRef(0)

  const selected = (rooms.data?.records || []).find((room) => room.id === roomId) || null

  /**
   * Load a room's white-label state, discarding the answer if it is stale.
   *
   * The `cancelled` flag is not decoration. Without it, choosing room A and then
   * room B quickly enough lets the slower response land last and overwrite B's
   * panel with A's, and the shared `busy` flag is cleared by whichever request
   * finishes first while the other is still in flight -- so a second click is
   * accepted against a half-loaded form.
   */
  async function load(id) {
    setRoomId(id)
    // Everything scoped to the previously-selected room is dropped, including
    // the report and the brand form. Both are load-bearing: a verification
    // report describes a specific domain, and the brand form holds half-typed
    // tokens, and neither means anything for a different room.
    setReport(null)
    setNotice(null)
    setError(null)
    setState(null)
    setBranding(EMPTY_BRANDING)
    if (!id) return

    const token = ++latestLoad.current
    setBusy(true)
    try {
      const next = await whiteLabelApi.forRoom(id)
      const stale = token !== latestLoad.current
      // The form is seeded from what is stored, so the operator can see the
      // current values instead of typing over a blank form, and a blank field
      // unambiguously means "leave this alone" rather than "clear it".
      if (!stale) {
        setState(next)
        setBranding({ ...EMPTY_BRANDING, ...tokenStrings(next?.branding) })
      }
    } catch (err) {
      if (token === latestLoad.current) setError(err)
    } finally {
      if (token === latestLoad.current) setBusy(false)
    }
  }

  /** Run a write, adopt its answer as the new state, and report what happened. */
  async function run(action, successMessage) {
    setBusy(true)
    setError(null)
    try {
      const next = await action()
      setState(next)
      setNotice(successMessage)
      return next
    } catch (err) {
      setError(err)
      return null
    } finally {
      setBusy(false)
    }
  }

  async function verifyDomain(event) {
    event.preventDefault()
    if (!domainInput.trim()) return
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      setReport(await whiteLabelApi.verifyDomain(domainInput.trim()))
    } catch (err) {
      setReport(null)
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  async function claim() {
    if (!domainInput.trim() || !roomId) return
    const domain = domainInput.trim()

    // A report describes one specific domain. If the field has moved on since it
    // was produced, it says nothing about what is about to be saved, so the
    // operator is sent back to verify rather than shown a confirmation that
    // quotes checks for a different host -- which is the only warning the
    // research's forced save has.
    if (!report || report.domain !== domain) {
      setReport(null)
      setError(new Error('Verify the domain again before saving it. The field has changed since it was checked.'))
      return
    }

    // A failed verification is a normal state here, not an error, so offer the
    // researched escape hatch rather than sending the operator back to the docs
    // to work out that propagation is the reason. The confirmation repeats every
    // failing check, because "save anyway?" over a domain pointing at the wrong
    // host is a question worth asking twice.
    const unpropagated = !report.ready
    const force = unpropagated
      ? window.confirm(
          `The checks for ${report.domain} have not all passed:\n\n` +
            report.checks
              .filter((check) => !check.ok)
              .map((check) => `- ${check.label}: ${check.detail}`)
              .join('\n') +
            '\n\nSave it anyway? The room stays on the default host until DNS propagates.',
        )
      : false
    if (unpropagated && !force) return

    const next = await run(
      () => whiteLabelApi.claimDomain(roomId, domain, { force }),
      `Domain saved: ${domain}`,
    )
    if (next) setReport(null)
  }

  async function saveBranding(event) {
    event.preventDefault()
    if (!roomId) return
    // Empty inputs are dropped rather than sent, so a field left blank in the
    // form does not wipe a token that is already stored. `branding` merges
    // server-side, so a partial write is safe -- but an empty one is not.
    const payload = Object.fromEntries(
      Object.entries(branding).filter(([, value]) => value.trim()),
    )
    if (Object.keys(payload).length === 0) {
      setNotice('Nothing to save')
      return
    }
    await run(() => whiteLabelApi.saveBranding(roomId, payload), 'Brand tokens saved')
  }

  if (rooms.loading || config.loading) return <Spinner label="Loading white-label settings" />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />
  // A failed config load is not recoverable by retrying inline: without the
  // CNAME target this page has nothing true to say about DNS, and rendering
  // step 1 with an empty target would tell an operator to create a DNS record
  // pointing at nothing. So it is the whole page, not a banner above one.
  if (config.error) return <ErrorNote error={config.error} onRetry={config.refetch} />

  const roomRecords = rooms.data?.records || []
  const cnameTarget = config.data?.cname_target

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-mono text-2xl font-semibold">White-label rooms</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Serve a room from your own domain instead of the default host. A CNAME record, a
          verification, and the links switch host. Links you have already shared keep working.
        </p>
      </header>

      {/* Above the empty-state branch, not inside it. A verification or a
          branding save can fail before any room exists, and a message that is
          only rendered when rooms do exist is a message that can be lost. */}
      {error && <ErrorNote error={error} />}
      {notice && (
        <div
          role="status"
          className="flex items-center gap-2 rounded-lg border border-accent/40 bg-accent/10 p-3 text-sm text-foreground"
        >
          <Glyph name="check" size={16} className="text-accent" />
          {notice}
        </div>
      )}

      {roomRecords.length === 0 ? (
        <EmptyState
          title="No rooms to white-label yet"
          description="Create a sales room first. Every room can be given its own custom domain."
        />
      ) : (
        <>
          <Card>
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0">
                <h2 className="font-mono text-base font-semibold">1. Point a CNAME at this deployment</h2>
                <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
                  {config.data?.propagation_note}
                </p>
              </div>
              <Badge tone="neutral">
                <span className="inline-flex items-center gap-1.5">
                  <Glyph name="globe" size={13} />
                  {cnameTarget}
                </span>
              </Badge>
            </div>

            <div className="mt-4 grid gap-5 md:grid-cols-2">
              <StepList steps={DNS_STEPS} target={cnameTarget} />
              <div className="rounded-lg border border-border-subtle/25 bg-background/40 p-3">
                <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                  Cloudflare
                </p>
                <p className="mt-1 text-sm text-muted-foreground">{config.data?.cloudflare_note}</p>
              </div>
            </div>

            <p className="mt-4 text-sm text-muted-foreground">{config.data?.format_note}</p>
          </Card>

          <Card>
            <h2 className="font-mono text-base font-semibold">2. Choose a room</h2>
            <div className="mt-3">
              <Field label="Sales room" id="wl-room" hint="The room whose links will use this domain.">
                <select
                  id="wl-room"
                  className={inputClass}
                  value={roomId}
                  onChange={(event) => load(event.target.value)}
                >
                  <option value="">Select a room…</option>
                  {roomRecords.map((room) => (
                    <option key={room.id} value={room.id}>
                      {room.data?.name || 'Untitled room'}
                      {room.data?.domain ? ` — ${room.data.domain}` : ''}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
          </Card>

          <Card>
            <h2 className="font-mono text-base font-semibold">3. Verify and save a domain</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Enter the domain, verify it, then save. Verification is separate from saving so you
              can see what is wrong before committing.
            </p>

            <form onSubmit={verifyDomain} className="mt-4 space-y-3">
              <Field label="Custom domain" id="wl-domain" hint="Subdomain format, e.g. proposals.acme.com">
                <input
                  id="wl-domain"
                  className={inputClass}
                  placeholder="proposals.acme.com"
                  value={domainInput}
                  onChange={(event) => setDomainInput(event.target.value)}
                />
              </Field>
              <div className="flex flex-wrap gap-2">
                <Button type="submit" disabled={busy || !domainInput.trim()}>
                  <Glyph name="shield" size={16} />
                  {busy ? 'Checking…' : 'Verify domain'}
                </Button>
                {/* Only offered for a report about the domain currently in the
                    field. A report for anything else describes a host that is
                    not about to be saved, and offering "Save domain" against it
                    is how an unverified domain gets saved with no warning. */}
                {report?.domain === domainInput.trim() && roomId && (
                  <Button variant="primary" disabled={busy} onClick={claim}>
                    <Glyph name="check" size={16} />
                    Save domain
                  </Button>
                )}
              </div>
              {report && !roomId && (
                <p className="text-xs text-muted-foreground">
                  Choose a room above before saving this domain to it.
                </p>
              )}
            </form>

            {report && (
              <div className="mt-4 rounded-lg border border-border-subtle/25 bg-background/40 p-3">
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                  <p className="font-mono text-sm break-all text-foreground">{report.domain}</p>
                  <StatusPill status={report.status} />
                </div>
                <CheckList checks={report.checks} />
                <p className="mt-3 text-xs text-muted-foreground">{report.cloudflare_note}</p>
              </div>
            )}
          </Card>

          {selected && (
            <>
              <Card>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="font-mono text-base font-semibold">4. Current domain</h2>
                    <p className="mt-1 text-sm break-all text-muted-foreground">
                      {state?.domain || 'No custom domain set. Links use the default host.'}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    {state?.domain_status && <StatusPill status={state.domain_status} />}
                    {state?.domain && (
                      <Button
                        onClick={() => run(() => whiteLabelApi.recheckDomain(roomId), 'Re-checked DNS')}
                      >
                        <Icon name="refresh" size={16} />
                        Re-check
                      </Button>
                    )}
                    {state?.domain && (
                      <Button
                        variant="danger"
                        onClick={() => run(() => whiteLabelApi.releaseDomain(roomId), 'Domain released')}
                      >
                        <Glyph name="unlink" size={16} />
                        Release
                      </Button>
                    )}
                  </div>
                </div>

                {state?.domain && (
                  <dl className="mt-4 grid gap-3 text-xs sm:grid-cols-2">
                    <div>
                      <dt className="text-muted-foreground">Last checked</dt>
                      <dd className="font-mono text-foreground">
                        {state.domain_last_checked_at || '—'}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Activated</dt>
                      <dd className="font-mono text-foreground">{state.domain_activated_at || '—'}</dd>
                    </div>
                  </dl>
                )}

                {state?.domain_history?.length > 0 && (
                  <div className="mt-4 rounded-lg border border-border-subtle/25 bg-background/40 p-3">
                    <p className="mb-2 flex items-center gap-1.5 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                      <Glyph name="clock" size={13} />
                      Previously used
                    </p>
                    <ul className="space-y-1 text-sm text-muted-foreground">
                      {state.domain_history.map((entry, index) => (
                        <li key={`${entry.domain}-${index}`} className="font-mono break-all">
                          {entry.domain}
                          <span className="text-muted-foreground/70"> — retired {entry.retired_at}</span>
                        </li>
                      ))}
                    </ul>
                    <p className="mt-2 text-xs text-muted-foreground">
                      Retiring a domain never breaks a shared link: the secret in the URL is what
                      identifies the room, not the host.
                    </p>
                  </div>
                )}
              </Card>

              <Card>
                <h2 className="font-mono text-base font-semibold">5. Share links</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  The room name becomes the readable part of the URL and a security secret is always
                  appended. The secret is not removable.
                </p>

                <div className="mt-4 space-y-3">
                  <div className="rounded-lg border border-accent/30 bg-accent/5 p-3">
                    <p className="mb-1.5 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                      Buyer link
                    </p>
                    <p className="mb-2 font-mono text-sm break-all text-foreground">
                      {state?.share_url || 'Create the link secret to generate a share link.'}
                    </p>
                    {state?.share_url && <CopyButton value={state.share_url} label="Copy buyer link" />}
                  </div>

                  {state?.default_host_share_url && state.default_host_share_url !== state.share_url && (
                    <div className="rounded-lg border border-border-subtle/25 bg-background/40 p-3">
                      <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                        <Glyph name="link" size={13} />
                        Default-host link
                      </p>
                      <p className="mb-2 font-mono text-sm break-all text-foreground">
                        {state.default_host_share_url}
                      </p>
                      <p className="mb-2 text-xs text-muted-foreground">
                        Links shared before the domain was set up, or under the old one, keep
                        working on this host.
                      </p>
                      <CopyButton value={state.default_host_share_url} label="Copy default link" />
                    </div>
                  )}

                  {state?.collaborator_url && (
                    <div className="rounded-lg border border-border-subtle/25 bg-background/40 p-3">
                      <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                        <Glyph name="eye" size={13} />
                        Internal collaborator link
                      </p>
                      <p className="mb-2 font-mono text-sm break-all text-foreground">
                        {state.collaborator_url}
                      </p>
                      <p className="mb-2 text-xs text-muted-foreground">
                        Internal use only. This never uses the custom domain, so it is not a buyer
                        link.
                      </p>
                      <CopyButton value={state.collaborator_url} label="Copy internal link" />
                    </div>
                  )}
                </div>

                {!state?.has_link_secret && (
                  <div className="mt-4">
                    <Button
                      variant="primary"
                      disabled={busy}
                      onClick={() => run(() => whiteLabelApi.mintLinkSecret(roomId), 'Link secret created')}
                    >
                      <Glyph name="shield" size={16} />
                      Create the link secret
                    </Button>
                  </div>
                )}
              </Card>

              <Card>
                <h2 className="font-mono text-base font-semibold">6. Brand setup</h2>
                <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
                  Colours and fonts are checked before they are stored, because they are rendered
                  into inline styles. Any other field you add here is stored as-is.
                </p>
                <form onSubmit={saveBranding} className="mt-4 space-y-4">
                  <div className="grid gap-4 sm:grid-cols-2">
                    <Field label="Primary colour" id="wl-primary" hint="Hex, rgb(), hsl() or a named colour.">
                      <input
                        id="wl-primary"
                        className={inputClass}
                        placeholder="#1e293b"
                        value={branding.primary}
                        onChange={(event) => setBranding({ ...branding, primary: event.target.value })}
                      />
                    </Field>
                    <Field label="Accent colour" id="wl-accent">
                      <input
                        id="wl-accent"
                        className={inputClass}
                        placeholder="#22c55e"
                        value={branding.accent}
                        onChange={(event) => setBranding({ ...branding, accent: event.target.value })}
                      />
                    </Field>
                    <Field label="Heading font" id="wl-heading-font" hint="A CSS font stack.">
                      <input
                        id="wl-heading-font"
                        className={inputClass}
                        placeholder="Fira Code, monospace"
                        value={branding.heading_font}
                        onChange={(event) => setBranding({ ...branding, heading_font: event.target.value })}
                      />
                    </Field>
                    <Field label="Body font" id="wl-body-font">
                      <input
                        id="wl-body-font"
                        className={inputClass}
                        placeholder="'Fira Sans', sans-serif"
                        value={branding.body_font}
                        onChange={(event) => setBranding({ ...branding, body_font: event.target.value })}
                      />
                    </Field>
                  </div>
                  <Button type="submit" variant="primary" disabled={busy}>
                    Save brand tokens
                  </Button>
                </form>

                {state?.branding && Object.keys(state.branding).length > 0 && (
                  <div className="mt-4 rounded-lg border border-border-subtle/25 bg-background/40 p-3">
                    <p className="mb-2 flex items-center gap-1.5 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                      <Glyph name="palette" size={13} />
                      Stored brand tokens
                    </p>
                    <dl className="grid gap-2 text-sm sm:grid-cols-2">
                      {Object.entries(state.branding).map(([key, value]) => (
                        <div key={key} className="flex items-center gap-2">
                          <dt className="font-mono text-xs text-muted-foreground">{key}</dt>
                          <dd className="min-w-0 truncate font-mono text-xs text-foreground">
                            {String(value)}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                )}
              </Card>
            </>
          )}
        </>
      )}
    </div>
  )
}
