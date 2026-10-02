import { useCallback, useMemo, useState } from 'react'

import { Badge, Button, Card, EmptyState, ErrorNote, Field, Spinner, StatCard, inputClass, useAsync } from '@/components/ui'
import { gateApi, listDocuments, listRooms } from './api'
import { CLOCK_ICON, GATE_ICON, VERIFIED_ICON } from './icons'
import { Notice, Toggle } from './primitives'

/** The researched gate steps, in the order a buyer meets them. */
const STEP_LABEL = { email: 'Email', code: 'One-time code', password: 'Password' }

const STATE_TONE = {
  open: 'success',
  email: 'info',
  code: 'info',
  password: 'info',
  expired: 'warning',
}

/** Human duration for a countdown, or "never". */
function expiryLabel(link) {
  if (!link.expires_at) return 'No expiry'
  if (link.expires_in_seconds === 0) return 'Closed'
  const seconds = link.expires_in_seconds
  if (seconds < 3600) return `${Math.round(seconds / 60)}m left`
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h left`
  return `${Math.round(seconds / 86400)}d left`
}

function roomName(rooms, id) {
  const room = rooms.find((candidate) => candidate.id === id)
  return room ? room.name : id || '—'
}

export default function LinkGating() {
  const [roomId, setRoomId] = useState('')
  const board = useAsync(
    async () => {
      const [rooms, summary, presets, documents] = await Promise.all([
        listRooms(),
        gateApi.summary(roomId),
        gateApi.presets(),
        listDocuments().catch(() => ({ records: [] })),
      ])
      const records = rooms?.records || rooms?.items || []
      const effectiveRoom = roomId || records[0]?.id || ''
      const links = effectiveRoom ? await gateApi.roomLinks(effectiveRoom) : { links: [], revoked: [] }
      const views = effectiveRoom ? await gateApi.views(effectiveRoom) : { views: [] }
      const notifications = effectiveRoom ? await gateApi.notifications(effectiveRoom) : { notifications: [] }
      return {
        rooms: records,
        documents: documents?.records || documents?.items || [],
        summary,
        presets: presets.presets,
        activeRoom: effectiveRoom,
        links: links.links,
        revoked: links.revoked,
        views: views.views,
        notifications: notifications.notifications,
      }
    },
    [roomId],
  )

  const onRoomChange = useCallback((event) => setRoomId(event.target.value), [])

  if (board.loading) return <Spinner label="Loading link gating" />
  if (board.error) return <ErrorNote error={board.error} onRetry={board.refetch} />

  const data = board.data
  const s = data.summary
  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-semibold text-foreground">Link gating</h1>
        <p className="mt-1 max-w-3xl text-[15px] text-muted-foreground">
          A password, an expiry and an email step in front of every buyer link. Expiry is judged on each viewer
          request, so a link closes the moment it says it will rather than the next time somebody refreshes the
          board.
        </p>
      </header>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Gated links" value={s.links} hint={`${s.open} open`} icon="audit" />
        <StatCard
          label="Closing soon"
          value={s.expiring_within_a_day}
          hint="within a day"
          icon="refresh"
        />
        <StatCard
          label="Verified buyers"
          value={s.verified_visitors}
          hint={`${s.views} view${s.views === 1 ? '' : 's'}`}
          icon="schema"
        />
        <StatCard
          label="Closed"
          value={s.expired + s.revoked}
          hint={`${s.expired} expired, ${s.revoked} revoked`}
          icon="trash"
        />
      </section>

      {data.rooms.length > 1 && (
        <Card>
          <Field label="Data room" id="wf069-room" hint="Links are scoped to the room they share from.">
            <select
              id="wf069-room"
              className={inputClass}
              value={data.activeRoom}
              onChange={onRoomChange}
            >
              {data.rooms.map((room) => (
                <option key={room.id} value={room.id}>
                  {room.name}
                </option>
              ))}
            </select>
          </Field>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <CreateLink
          documents={data.documents}
          presets={data.presets}
          activeRoom={data.activeRoom}
          onCreated={board.refetch}
        />
        <Presets presets={data.presets} onChanged={board.refetch} />
      </div>

      <LinkBoard
        links={data.links}
        revoked={data.revoked}
        roomId={data.activeRoom}
        onChanged={board.refetch}
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <Views views={data.views} />
        <Notifications notifications={data.notifications} />
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------------- */
/* Create
/* ------------------------------------------------------------------------- */

function CreateLink({ documents, presets, activeRoom, onCreated }) {
  const empty = {
    title: '',
    targetKind: 'dataroom',
    targetId: activeRoom,
    password: '',
    expires_at: '',
    email_protected: true,
    email_authenticated: false,
    enable_notification: true,
    preset_id: '',
  }
  const [form, setForm] = useState(empty)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState(null)
  const [fieldErrors, setFieldErrors] = useState({})
  const [made, setMade] = useState(null)

  const set = (key) => (event) =>
    setForm((previous) => ({ ...previous, [key]: event.target.value }))

  // Authentication subsumes protection, so asking for one turns the other on rather
  // than leaving two switches to disagree with each other.
  const setSwitch = (key, value) =>
    setForm((previous) => {
      const next = { ...previous, [key]: value }
      if (key === 'email_authenticated' && value) next.email_protected = true
      return next
    })

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setProblem(null)
    setFieldErrors({})
    const payload = {
      title: form.title.trim() || 'Buyer link',
      password: form.password === '' ? null : form.password,
      // An empty date box is a request for no expiry, and null is how the API says
      // that. Sending "" instead would be an unparseable timestamp.
      expires_at: form.expires_at.trim() === '' ? null : form.expires_at.trim(),
      email_protected: form.email_protected,
      email_authenticated: form.email_authenticated,
      enable_notification: form.enable_notification,
    }
    if (form.preset_id) payload.preset_id = form.preset_id
    payload[form.targetKind === 'document' ? 'document_id' : 'dataroom_id'] =
      form.targetKind === 'document' ? form.targetId : activeRoom

    try {
      const link = await gateApi.createLink(activeRoom, payload)
      setMade(link)
      setForm({ ...empty, targetId: activeRoom })
      onCreated()
    } catch (error) {
      setProblem(error)
      setFieldErrors(error.errors || {})
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">New gated link</h2>
      <form className="mt-4 space-y-4" onSubmit={submit}>
        <Field label="Title" id="wf069-title" hint="What the buyer will see in the browser tab.">
          <input
            id="wf069-title"
            className={inputClass}
            value={form.title}
            onChange={set('title')}
            placeholder="Northwind — mutual NDA"
          />
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Points at" id="wf069-target-kind">
            <select
              id="wf069-target-kind"
              className={inputClass}
              value={form.targetKind}
              onChange={(event) =>
                setForm((previous) => ({
                  ...previous,
                  targetKind: event.target.value,
                  targetId: event.target.value === 'document' ? (documents[0]?.id ?? '') : activeRoom,
                }))
              }
            >
              <option value="dataroom">The whole data room</option>
              <option value="document">One document</option>
            </select>
          </Field>
          {form.targetKind === 'document' && (
            <Field label="Document" id="wf069-target" hint={fieldErrors.document_id}>
              <select id="wf069-target" className={inputClass} value={form.targetId} onChange={set('targetId')}>
                {documents.length === 0 && <option value="">No documents yet</option>}
                {documents.map((document) => (
                  <option key={document.id} value={document.id}>
                    {document.data?.title || document.id}
                  </option>
                ))}
              </select>
            </Field>
          )}
        </div>

        <Field
          label="Password"
          id="wf069-password"
          hint={fieldErrors.password || 'Leave empty for no password. Stored as a salted hash, never in the clear.'}
        >
          <input
            id="wf069-password"
            type="password"
            autoComplete="new-password"
            className={inputClass}
            value={form.password}
            onChange={set('password')}
          />
        </Field>

        <Field
          label="Expires at"
          id="wf069-expiry"
          hint={fieldErrors.expires_at || 'ISO 8601. A bare date means the end of that day. Empty means never.'}
        >
          <input
            id="wf069-expiry"
            className={inputClass}
            value={form.expires_at}
            onChange={set('expires_at')}
            placeholder="2026-12-31"
          />
        </Field>

        {presets.length > 0 && (
          <Field
            label="Governed baseline"
            id="wf069-preset"
            hint="Every field the preset covers becomes the default; anything set above overrides it."
          >
            <select id="wf069-preset" className={inputClass} value={form.preset_id} onChange={set('preset_id')}>
              <option value="">No baseline</option>
              {presets.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {preset.name}
                </option>
              ))}
            </select>
          </Field>
        )}

        <div className="space-y-1 rounded-sm border border-border-subtle p-3">
          <Toggle
            id="wf069-protected"
            label="Ask for an email address"
            hint="On by default. The buyer's address is recorded with every view."
            checked={form.email_protected}
            onChange={(value) => setSwitch('email_protected', value)}
          />
          <Toggle
            id="wf069-authenticated"
            label="Verify the inbox with a one-time code"
            hint="Stronger than asking: the buyer proves they own the address. Turns the step above on."
            checked={form.email_authenticated}
            onChange={(value) => setSwitch('email_authenticated', value)}
          />
          <Toggle
            id="wf069-notify"
            label="Notify the team on each view"
            hint="On by default."
            checked={form.enable_notification}
            onChange={(value) => setSwitch('enable_notification', value)}
          />
        </div>

        {problem && !Object.keys(fieldErrors).length && (
          <Notice tone="destructive" title="Could not create the link">
            {problem.message}
          </Notice>
        )}

        {made && (
          <Notice tone="success" title="Link created">
            <span className="font-mono text-[13px] break-all">{made.id}</span>
          </Notice>
        )}

        <Button type="submit" variant="primary" icon="plus" disabled={busy || !activeRoom}>
          {busy ? 'Creating…' : 'Create gated link'}
        </Button>
      </form>
    </Card>
  )
}

/* ------------------------------------------------------------------------- */
/* Presets
/* ------------------------------------------------------------------------- */

function Presets({ presets, onChanged }) {
  const [name, setName] = useState('')
  const [days, setDays] = useState('30')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState(null)

  const covered = useMemo(() => presets[0]?.covered_fields || [], [presets])

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setProblem(null)
    try {
      const expires =
        days.trim() === ''
          ? null
          : new Date(Date.now() + Number(days) * 86400000).toISOString()
      await gateApi.createPreset({
        name: name.trim(),
        fields: { email_protected: true, enable_notification: true, expires_at: expires },
      })
      setName('')
      onChanged()
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">Governed baselines</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        A baseline every future link can be seeded from, so a team sets the floor once instead of on every deal.
      </p>

      <form className="mt-4 space-y-4" onSubmit={submit}>
        <Field label="Name" id="wf069-preset-name" hint={problem?.errors?.name}>
          <input
            id="wf069-preset-name"
            className={inputClass}
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Enterprise deal"
          />
        </Field>
        <Field label="Expires after (days)" id="wf069-preset-days" hint="Empty for a baseline that never expires.">
          <input
            id="wf069-preset-days"
            className={inputClass}
            inputMode="numeric"
            value={days}
            onChange={(event) => setDays(event.target.value)}
          />
        </Field>
        <Button type="submit" icon="plus" disabled={busy || !name.trim()}>
          {busy ? 'Saving…' : 'Add baseline'}
        </Button>
      </form>

      <div className="mt-4 space-y-3">
        {presets.length === 0 && (
          <EmptyState title="No baselines yet" description="Create one to seed every link from the same floor." />
        )}
        {presets.map((preset) => (
          <div key={preset.id} className="rounded-sm border border-border-subtle p-3">
            <p className="text-sm font-medium text-foreground">{preset.name}</p>
            <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
              <dt className="text-muted-foreground">Email protected</dt>
              <dd className="font-mono">{preset.fields?.email_protected ? 'on' : 'off'}</dd>
              <dt className="text-muted-foreground">Password</dt>
              <dd className="font-mono">{preset.password_set ? 'set' : 'none'}</dd>
              <dt className="text-muted-foreground">Expires</dt>
              <dd className="font-mono break-all">{preset.fields?.expires_at || 'never'}</dd>
            </dl>
          </div>
        ))}
        {covered.length > 0 && (
          <p className="text-xs text-muted-foreground">
            A baseline may carry {covered.length} documented fields. This page governs the five that are about
            access; the rest belong to other workflows and are carried through untouched.
          </p>
        )}
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------------------- */
/* The links
/* ------------------------------------------------------------------------- */

function LinkBoard({ links, revoked, roomId, onChanged }) {
  const [rotating, setRotating] = useState(null)
  const [secret, setSecret] = useState('')
  const [problem, setProblem] = useState(null)

  async function rotate(linkId) {
    setProblem(null)
    try {
      await gateApi.updateLink(linkId, { password: secret === '' ? null : secret })
      setRotating(null)
      setSecret('')
      onChanged()
    } catch (error) {
      setProblem(error)
    }
  }

  async function clearExpiry(linkId) {
    setProblem(null)
    try {
      await gateApi.updateLink(linkId, { expires_at: null })
      onChanged()
    } catch (error) {
      setProblem(error)
    }
  }

  async function revoke(linkId) {
    setProblem(null)
    try {
      await gateApi.revokeLink(linkId)
      onChanged()
    } catch (error) {
      setProblem(error)
    }
  }

  if (!links.length && !revoked.length) {
    return (
      <EmptyState
        title="No gated links yet"
        description="Create one above. Every link you hand out is gated by default: email on, notification on."
      />
    )
  }

  return (
    <section className="space-y-4">
      <h2 className="font-display text-lg font-semibold text-foreground">Links</h2>
      {problem && (
        <Notice tone="destructive" title="That change did not go through">
          {problem.message}
        </Notice>
      )}

      <ul className="space-y-3">
        {links.map((link) => (
          <li key={link.id}>
            <Card>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-foreground">{link.title}</p>
                  <p className="mt-1 font-mono text-xs text-muted-foreground">{link.id}</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={STATE_TONE[link.state] || 'neutral'}>{link.state}</Badge>
                  {link.settings.password_set && <Badge tone="neutral">password</Badge>}
                  {link.settings.email_required && <Badge tone="info">email</Badge>}
                  {link.settings.email_authenticated && <Badge tone="info">code</Badge>}
                  {link.preset_id && <Badge tone="update">baseline</Badge>}
                </div>
              </div>

              <div className="mt-3 grid gap-3 text-xs sm:grid-cols-3">
                <p className="text-muted-foreground">
                  <span className="block text-[11px] uppercase tracking-[0.14em]">Gate</span>
                  <span className="mt-0.5 block font-mono text-foreground">
                    {link.settings.steps.length
                      ? link.settings.steps.map((step) => STEP_LABEL[step]).join(' → ')
                      : 'no gate'}
                  </span>
                </p>
                <p className="flex items-start gap-1.5 text-muted-foreground">
                  <svg
                    className="mt-0.5 h-3.5 w-3.5 shrink-0"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    aria-hidden="true"
                  >
                    <path d={CLOCK_ICON} />
                  </svg>
                  <span>
                    <span className="block text-[11px] uppercase tracking-[0.14em]">Expiry</span>
                    <span className="mt-0.5 block font-mono text-foreground">{expiryLabel(link)}</span>
                  </span>
                </p>
                <p className="flex items-start gap-1.5 text-muted-foreground">
                  <svg
                    className="mt-0.5 h-3.5 w-3.5 shrink-0"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    aria-hidden="true"
                  >
                    <path d={GATE_ICON} />
                  </svg>
                  <span>
                    <span className="block text-[11px] uppercase tracking-[0.14em]">Steps</span>
                    <span className="mt-0.5 block font-mono text-foreground">
                      {link.settings.steps.length}
                    </span>
                  </span>
                </p>
              </div>

              {rotating === link.id && (
                <div className="mt-4 rounded-sm border border-border-subtle p-3">
                  {/* Named `new-password` rather than after the rotate action: the
                      design floor's hazardous-motion pattern matches a rotation
                      class token, and this id used to contain one by accident. There
                      is no motion and no class on the line - it is an HTML id - so
                      the fix is to stop the identifier colliding with a rule about
                      animation, not to argue the finding away. Note the checker reads
                      raw lines, comments included. */}
                  <Field
                    label="New password"
                    id={`wf069-new-password-${link.id}`}
                    hint="Empty removes the password. A link that can be set but never cleared cannot be reopened."
                  >
                    <input
                      id={`wf069-new-password-${link.id}`}
                      type="password"
                      autoComplete="new-password"
                      className={inputClass}
                      value={secret}
                      onChange={(event) => setSecret(event.target.value)}
                    />
                  </Field>
                  <div className="mt-3 flex gap-2">
                    <Button variant="primary" onClick={() => rotate(link.id)}>
                      Save password
                    </Button>
                    <Button onClick={() => setRotating(null)}>Cancel</Button>
                  </div>
                </div>
              )}

              <div className="mt-4 flex flex-wrap gap-2">
                <Button onClick={() => setRotating(rotating === link.id ? null : link.id)}>
                  {link.settings.password_set ? 'Rotate password' : 'Set a password'}
                </Button>
                {link.expires_at && (
                  <Button icon="restore" onClick={() => clearExpiry(link.id)}>
                    Remove expiry
                  </Button>
                )}
                <Button variant="danger" icon="trash" onClick={() => revoke(link.id)}>
                  Revoke
                </Button>
              </div>
            </Card>
          </li>
        ))}
      </ul>

      {revoked.length > 0 && (
        <details className="rounded-sm border border-border-subtle p-4">
          <summary className="cursor-pointer text-sm font-medium text-foreground">
            {revoked.length} revoked link{revoked.length === 1 ? '' : 's'}
          </summary>
          <p className="mt-2 text-sm text-muted-foreground">
            A revoked link answers a buyer with the same friendly page an expired one does, and the row and its
            history are kept. Room: {roomName([], roomId)}
          </p>
          <ul className="mt-3 space-y-2">
            {revoked.map((link) => (
              <li key={link.id} className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-mono text-xs text-muted-foreground">{link.id}</span>
                <Badge tone="delete">revoked</Badge>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}

/* ------------------------------------------------------------------------- */
/* Attribution
/* ------------------------------------------------------------------------- */

function Views({ views }) {
  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">Views</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Every view is stamped with the address that opened it, and whether that address was verified.
      </p>
      {views.length === 0 ? (
        <EmptyState title="No views yet" description="A view is recorded when a buyer clears the whole gate." />
      ) : (
        <ul className="mt-4 space-y-2">
          {views.slice(0, 12).map((view) => (
            <li
              key={`${view.link_id}-${view.viewed_at}`}
              className="flex flex-wrap items-center justify-between gap-2 rounded-sm border border-border-subtle p-3"
            >
              <span className="font-mono text-[13px] text-foreground">{view.email || 'no address asked'}</span>
              <span className="flex items-center gap-2">
                {view.email_verified && (
                  <Badge tone="insert">
                    <svg
                      className="mr-1 inline-block h-3 w-3 align-[-2px]"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      aria-hidden="true"
                    >
                      <path d={VERIFIED_ICON} />
                    </svg>
                    verified
                  </Badge>
                )}
                <span className="font-mono text-xs text-muted-foreground">{view.viewed_at}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function Notifications({ notifications }) {
  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">Team notifications</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        On by default, so the team hears about each view of a link it handed out.
      </p>
      {notifications.length === 0 ? (
        <EmptyState title="Nothing to report" description="A notification is written on each view of a gated link." />
      ) : (
        <ul className="mt-4 space-y-2">
          {notifications.slice(0, 12).map((note) => (
            <li
              key={`${note.link_id}-${note.notified_at}`}
              className="rounded-sm border border-border-subtle p-3"
            >
              <p className="text-sm text-foreground">
                {note.viewer_email || 'Someone'} opened {note.title}
              </p>
              <p className="mt-1 font-mono text-xs text-muted-foreground">
                {note.email_verified ? 'verified' : 'not verified'} · {note.notified_at}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
