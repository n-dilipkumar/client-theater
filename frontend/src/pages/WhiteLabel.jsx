import { useState } from 'react'
import { api } from '../lib/api'
import {
  Badge,
  Button,
  Card,
  CopyButton,
  EmptyState,
  ErrorNote,
  Field,
  Icon,
  Spinner,
  StatusPill,
  inputClass,
  useAsync,
} from '../components/ui'

/**
 * White-label rooms on a custom domain.
 *
 * The screen follows the researched flow in the researched order, because the
 * order is the point: add the CNAME, wait for it to propagate, enter the domain,
 * let the service verify it, then save. Each step is a step, not a field on a
 * settings form, because a CNAME that has not propagated yet is the normal
 * state rather than an error.
 *
 * Two things are deliberately not editable from here, because the research
 * says they are not the customer's to change:
 *
 *   - the link secret, which is "a unique, non-removable identifier ... for
 *     security purposes". It is shown so it can be recognised in a support
 *     conversation, never edited.
 *   - the collaborator URL, which "won't use the custom domain, as they are for
 *     internal use only", so it is shown under an internal heading and is
 *     visibly not a buyer link.
 */

const DNS_STEPS = [
  { key: 'name', label: 'Name', note: 'the subdomain you want, e.g. proposals' },
  { key: 'type', label: 'Type', note: 'CNAME' },
  { key: 'value', label: 'Value', note: 'the canonical target shown below' },
]

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

function CheckList({ checks }) {
  return (
    <ul className="space-y-2" aria-live="polite" aria-label="Domain verification checks">
      {checks.map((check) => (
        <li key={check.name} className="flex items-start gap-2.5 text-sm">
          <span
            className={`mt-0.5 shrink-0 ${check.ok ? 'text-accent' : 'text-destructive'}`}
          >
            <Icon name={check.ok ? 'check' : 'alert'} size={16} />
          </span>
          <span className="min-w-0">
            <span className="text-foreground">{check.label}</span>
            <span className="block break-words font-mono text-xs text-muted-foreground">
              {check.detail}
            </span>
          </span>
        </li>
      ))}
    </ul>
  )
}

export default function WhiteLabel() {
  const rooms = useAsync(() => api.listRecords('room', { limit: 100 }), [])
  const config = useAsync(() => api.whiteLabelConfig(), [])

  const [roomId, setRoomId] = useState('')
  const [state, setState] = useState(null)
  const [domainInput, setDomainInput] = useState('')
  const [report, setReport] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [branding, setBranding] = useState({
    primary: '',
    accent: '',
    heading_font: '',
    body_font: '',
  })

  const selected = (rooms.data?.records || []).find((room) => room.id === roomId) || null

  async function load(id) {
    setRoomId(id)
    setReport(null)
    setNotice(null)
    setError(null)
    setState(null)
    if (!id) return
    setBusy(true)
    try {
      setState(await api.roomWhiteLabel(id))
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

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
      setReport(await api.verifyDomain(domainInput.trim()))
    } catch (err) {
      setReport(null)
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  async function claim(event) {
    event.preventDefault()
    if (!domainInput.trim() || !roomId) return
    const domain = domainInput.trim()
    // A failed verification is a normal state here, not an error, so offer the
    // researched escape hatch rather than making the operator re-read the docs.
    const unpropagated = report && !report.ready
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

    const next = await run(() => api.claimDomain(roomId, domain, { force }), `Domain saved: ${domain}`)
    if (next) setReport(null)
  }

  async function saveBranding(event) {
    event.preventDefault()
    if (!roomId) return
    // Empty inputs are dropped rather than sent, so a field left blank in the
    // form does not wipe a token that is already stored.
    const payload = Object.fromEntries(
      Object.entries(branding).filter(([, value]) => value.trim()),
    )
    if (Object.keys(payload).length === 0) {
      setNotice('Nothing to save')
      return
    }
    await run(() => api.saveBranding(roomId, payload), 'Brand tokens saved')
  }

  if (rooms.loading || config.loading) return <Spinner label="Loading white-label settings" />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />

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

      {config.error && <ErrorNote error={config.error} onRetry={config.refetch} />}

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
                  <Icon name="globe" size={13} />
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
                      {room.data.name || 'Untitled room'}
                      {room.data.domain ? ` — ${room.data.domain}` : ''}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
          </Card>

          {error && <ErrorNote error={error} />}
          {notice && (
            <div
              role="status"
              className="flex items-center gap-2 rounded-lg border border-accent/40 bg-accent/10 p-3 text-sm text-foreground"
            >
              <Icon name="check" size={16} className="text-accent" />
              {notice}
            </div>
          )}

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
                <Button type="submit" icon="shield" disabled={busy || !domainInput.trim()}>
                  {busy ? 'Checking…' : 'Verify domain'}
                </Button>
                {report && roomId && (
                  <Button icon="check" variant="primary" disabled={busy} onClick={claim}>
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
                    <p className="mt-1 break-all text-sm text-muted-foreground">
                      {state?.domain || 'No custom domain set. Links use the default host.'}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    {state?.domain_status && <StatusPill status={state.domain_status} />}
                    {state?.domain && (
                      <Button icon="refresh" onClick={() => run(() => api.recheckDomain(roomId), 'Re-checked DNS')}>
                        Re-check
                      </Button>
                    )}
                    {state?.domain && (
                      <Button
                        icon="unlink"
                        onClick={() => run(() => api.releaseDomain(roomId), 'Domain released')}
                      >
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
                      <Icon name="clock" size={13} />
                      Previously used
                    </p>
                    <ul className="space-y-1 text-sm text-muted-foreground">
                      {state.domain_history.map((entry, index) => (
                        <li key={`${entry.domain}-${index}`} className="break-all font-mono">
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
                    <p className="mb-2 break-all font-mono text-sm text-foreground">
                      {state?.share_url || 'Save a domain to generate a share link.'}
                    </p>
                    {state?.share_url && (
                      <div className="flex flex-wrap gap-2">
                        <CopyButton value={state.share_url} label="Copy buyer link" />
                      </div>
                    )}
                  </div>

                  {state?.default_host_share_url && state.default_host_share_url !== state.share_url && (
                    <div className="rounded-lg border border-border-subtle/25 bg-background/40 p-3">
                      <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                        <Icon name="link" size={13} />
                        Default-host link
                      </p>
                      <p className="mb-2 break-all font-mono text-sm text-muted-foreground">
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
                        <Icon name="eye" size={13} />
                        Internal collaborator link
                      </p>
                      <p className="mb-2 break-all font-mono text-sm text-muted-foreground">
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
                      icon="shield"
                      variant="primary"
                      disabled={busy}
                      onClick={() => run(() => api.mintLinkSecret(roomId), 'Link secret created')}
                    >
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
                      <Icon name="palette" size={13} />
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
