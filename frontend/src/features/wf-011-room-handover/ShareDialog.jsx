import { useState } from 'react'
import { absoluteTime, relativeTime } from '@/lib/api'
import { Badge, Button, ErrorNote, Field, Icon, Spinner, inputClass, useAsync } from '@/components/ui'
import { publishingApi } from './api'
import Modal from './Modal'
import { CHECK, COPY } from './icons'
import { copyToClipboard, statusTone } from './publish'

/**
 * The share pop-up.
 *
 * The researched flow in one dialog: pick the status, the public link switches
 * on when it is a public one, the link is copied for pasting into a message, and
 * the access settings sit in the same place rather than behind another screen.
 *
 * Setting a room live also copies the link, because that is what the flow
 * promises. Nothing here sends a message: the app hands over a link and the
 * seller owns the send.
 *
 * This was `src/components/ShareDialog.jsx` on the branch. `src/components` is
 * shared, so it moved into the feature folder; its only importer was the page
 * beside it.
 */
export default function ShareDialog({ roomId, onClose, onChanged }) {
  const share = useAsync(() => publishingApi.share(roomId), [roomId])
  const room = share.data

  const [statusChoice, setStatusChoice] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [linkState, setLinkState] = useState({ seed: undefined, link: null })
  const [copied, setCopied] = useState(null)
  const [accessState, setAccessState] = useState({ seed: undefined, access: null })
  const [accessBusy, setAccessBusy] = useState(false)
  const [accessError, setAccessError] = useState(null)

  // The form is seeded from the room, and re-seeded when it is refetched. Each
  // seeded value remembers which room payload it came from, so a different
  // payload derives a fresh value during render instead of being copied into
  // state by an effect.
  //
  // The consequence that matters: while the room is in flight the form shows
  // the incoming room's values immediately, rather than for one render showing
  // the empty defaults and then the real ones.
  const status = statusChoice ?? room?.status ?? ''

  const access =
    accessState.seed === room
      ? accessState.access
      : room
        ? {
            // A date input cannot hold a timestamp, so an ISO timestamp is
            // shown as its date. Saving then stores the end of that day, which
            // is what a seller who typed a full timestamp meant anyway.
            expires_at: (room.access.expires_at || '').slice(0, 10),
            max_views: room.access.max_views ?? '',
            password: '',
            require_identity_verification: room.access.require_identity_verification,
          }
        : null

  // `applyStatus` adopts the link the server returns; until that write happens
  // the link is whatever the loaded room says.
  const link =
    linkState.seed === room
      ? linkState.link
      : room?.public_url
        ? { url: room.public_url, shareable: !room.access.expired }
        : null

  const setStatus = (next) => setStatusChoice(next)
  const setAccess = (next) =>
    setAccessState({
      seed: room,
      access: typeof next === 'function' ? next(access) : next,
    })
  const setLink = (next) => setLinkState({ seed: room, link: next })

  async function applyStatus() {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const result = await publishingApi.setStatus(roomId, { status })
      setLink(result.share_link)
      if (result.share_link) {
        const ok = await copyToClipboard(result.share_link.url)
        setCopied(ok ? 'copied' : 'manual')
      }
      const what = result.transition.changed
        ? `${room.name} is now ${status}.`
        : `${room.name} was already ${status}, so nothing changed.`
      setNotice(
        result.event
          ? `${what} Sent ${result.event.event} to ${result.deliveries.length} subscriber(s).`
          : what,
      )
      share.refetch()
      onChanged()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  async function saveAccess(event) {
    event.preventDefault()
    setAccessBusy(true)
    setAccessError(null)
    try {
      const body = {
        expires_at: access.expires_at,
        max_views: access.max_views === '' ? null : Number(access.max_views),
        require_identity_verification: access.require_identity_verification,
      }
      if (access.password) body.password = access.password
      if (room?.access?.password_protected) body.clear_password = !access.password
      await publishingApi.setAccess(roomId, body)
      setAccess((prev) => ({ ...prev, password: '' }))
      share.refetch()
      onChanged()
    } catch (err) {
      setAccessError(err)
    } finally {
      setAccessBusy(false)
    }
  }

  async function copyLink() {
    if (!link) return
    const ok = await copyToClipboard(link.url)
    setCopied(ok ? 'copied' : 'manual')
  }

  if (share.loading) {
    return (
      <Modal title="Share" onClose={onClose}>
        <Spinner label="Loading room" />
      </Modal>
    )
  }

  if (share.error || !room) {
    return (
      <Modal title="Share" onClose={onClose}>
        <ErrorNote error={share.error} onRetry={share.refetch} />
      </Modal>
    )
  }

  const selected = (room.status_options || []).find((option) => option.value === status)
  const dirty = status !== room.status

  return (
    <Modal
      title={room.name}
      description={room.account ? `${room.account} · owner ${room.owner || 'unassigned'}` : undefined}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Close</Button>
          <Button
            variant="primary"
            disabled={!dirty || busy || !room.can_publish}
            onClick={applyStatus}
          >
            {busy ? 'Saving…' : dirty ? `Set ${status}` : 'No change'}
          </Button>
        </>
      }
    >
      {room.warnings.length > 0 && (
        <ul className="space-y-1.5 rounded-lg border border-border-subtle/30 bg-background/40 p-3">
          {room.warnings.map((note) => (
            <li key={note} className="flex items-start gap-2 text-sm text-muted-foreground">
              <span className="mt-0.5 text-accent">
                <Icon path={CHECK} size={16} />
              </span>
              {note}
            </li>
          ))}
        </ul>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={statusTone(room.status)}>{room.status}</Badge>
        {room.published ? (
          <span className="text-sm text-muted-foreground">Public link is on</span>
        ) : (
          <span className="text-sm text-muted-foreground">Public link is off</span>
        )}
        {room.archived && <Badge tone="restore">archived</Badge>}
        {room.is_template && <Badge tone="neutral">template</Badge>}
      </div>

      <Field label="Status" id="share-status" hint={selected?.description}>
        <select
          id="share-status"
          className={inputClass}
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          {(room.status_options || []).map((option) => (
            <option key={option.value} value={option.value}>
              {option.value}
              {option.is_public ? ' — public' : ''}
            </option>
          ))}
        </select>
      </Field>

      {error && <ErrorNote error={error} />}

      <section aria-label="Shareable link" className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <h3 className="font-mono text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Shareable link
          </h3>
          {link?.shareable === false && <Badge tone="delete">not shareable</Badge>}
        </div>
        {link ? (
          <>
            <div className="flex flex-wrap gap-2">
              <input
                aria-label="Public link"
                readOnly
                value={link.url}
                onFocus={(event) => event.target.select()}
                className={`${inputClass} font-mono`}
              />
              {/* The copy glyph is not in the shared PATHS map, so it is passed
                  as a child rather than through Button's `icon` name; the label
                  beside it is what carries the meaning. */}
              <Button onClick={copyLink}>
                <Icon path={COPY} />
                Copy link
              </Button>
            </div>
            <p role="status" aria-live="polite" className="text-sm text-muted-foreground">
              {copied === 'copied' && 'Copied. Paste it into your message.'}
              {copied === 'manual' &&
                'The browser would not copy for you. The link is selected above; copy it manually.'}
              {!copied && 'Not copied yet.'}
            </p>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">
            No public link yet. Set the room to live and the link appears here.
          </p>
        )}
        <p className="text-xs text-muted-foreground/80">{room.handover}</p>
      </section>

      <form onSubmit={saveAccess} className="space-y-3 border-t border-border-subtle/25 pt-4">
        <h3 className="font-mono text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Access settings
        </h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Link expiry" id="access-expiry" hint="A date, or blank for no expiry.">
            <input
              id="access-expiry"
              type="date"
              className={inputClass}
              value={access.expires_at}
              onChange={(event) => setAccess({ ...access, expires_at: event.target.value })}
            />
          </Field>
          <Field
            label="View limit"
            id="access-max-views"
            hint="Stored and shown. Not enforced here."
          >
            <input
              id="access-max-views"
              type="number"
              min="1"
              className={inputClass}
              value={access.max_views}
              onChange={(event) => setAccess({ ...access, max_views: event.target.value })}
            />
          </Field>
        </div>
        <Field
          label={room.access.password_protected ? 'Replace password' : 'Password'}
          id="access-password"
          hint={
            room.access.password_protected
              ? 'Leave blank and save to remove the password. Stored as a hash, never shown again.'
              : 'Stored as a salted hash. It is never shown again.'
          }
        >
          <input
            id="access-password"
            type="password"
            autoComplete="new-password"
            className={inputClass}
            value={access.password}
            onChange={(event) => setAccess({ ...access, password: event.target.value })}
          />
        </Field>
        <label className="flex min-h-11 items-center gap-3 text-sm text-foreground">
          <input
            type="checkbox"
            className="h-5 w-5 accent-[var(--color-accent)]"
            checked={access.require_identity_verification}
            onChange={(event) =>
              setAccess({ ...access, require_identity_verification: event.target.checked })
            }
          />
          Require identity verification before the room opens
        </label>
        {accessError && <ErrorNote error={accessError} />}
        <Button type="submit" disabled={accessBusy}>
          {accessBusy ? 'Saving…' : 'Save access settings'}
        </Button>
      </form>

      <section aria-label="Recent transitions" className="border-t border-border-subtle/25 pt-4">
        <h3 className="mb-2 font-mono text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Recent transitions
        </h3>
        {notice && (
          <p role="status" aria-live="polite" className="mb-2 text-sm text-accent">
            {notice}
          </p>
        )}
        <dl className="grid grid-cols-2 gap-3 text-xs">
          <div>
            <dt className="text-muted-foreground">First published</dt>
            <dd className="font-mono text-foreground">
              {room.published_at
                ? `${relativeTime(room.published_at)} (${absoluteTime(room.published_at)})`
                : '—'}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Status changed</dt>
            <dd className="font-mono text-foreground">
              {room.status_changed_at ? relativeTime(room.status_changed_at) : '—'}
            </dd>
          </div>
        </dl>
      </section>
    </Modal>
  )
}
