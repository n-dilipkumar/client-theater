import { useCallback, useMemo, useState } from 'react'

import { Badge, Button, Card, EmptyState, ErrorNote, Field, JsonView, Spinner, StatCard, inputClass, useAsync } from '@/components/ui'

import { DETERRENT_NOT_PROTECTION, KNOWN_SHORTCUTS, confidentialApi, listRooms, shortcutScope } from './api'
import { CONFIDENTIAL_ICON } from './icons'
import { BandStrip, Notice, Toggle } from './primitives'

/**
 * WF-073: apply confidential view and block screenshot shortcuts.
 *
 * The page has three jobs, in this order, and the order is the design.
 *
 * **Say what the controls are worth before showing that they are on.** The specification
 * for this workflow says screenshot blocking "is largely unenforceable from a browser;
 * treat as deterrence, and do not sell it as protection." A page that opens with two
 * switches and a green tick is selling it. So the limitation is the first thing rendered,
 * above the fold, and the server sends the same sentence with every response so a caller
 * cannot read a control without reading the caveat beside it.
 *
 * **Show the access-controls panel the specification inferred.** The specification marks
 * that panel as inferred from the field set rather than sourced, and this build records
 * the derivation rather than inventing a vendor screenshot. It is two switches, one per
 * flag, each carrying the vendor's own flag name and the vendor's own description - both
 * fetched from `GET /wf-073/vocabulary`, never hard-coded here, so the page cannot drift
 * from the rules that validate it.
 *
 * **Show the state, not just the setting.** A security board that only lists settings
 * tells a reviewer nothing. The page also carries the reported capture attempts, split
 * into the ones a page can intercept and the ones it cannot, because Print Screen is the
 * one a seller most needs to know about and a list without it would let them read "this
 * link blocks screenshots" as true.
 *
 * A fourth thing, the focus band, is drawn rather than described. It is the object the
 * whole workflow is about: one band of a page is sharp and the rest arrives blurred, so
 * a single screenshot cannot capture a whole page.
 *
 * Every state this page can be in is rendered: loading, error, empty. A security page
 * that goes blank when the API is down looks like the controls failing, which is the one
 * reading that must never be possible.
 */

/** Toggle one flag on one link, and report the failure without losing the page. */
function useLinkControls() {
  const [busy, setBusy] = useState(null)
  const [fieldError, setFieldError] = useState(null)

  const apply = useCallback(async (link, field, on) => {
    setBusy(`${link.id}:${field}`)
    setFieldError(null)
    try {
      // Only the field being changed is sent. `undefined` is dropped from the body by
      // the api wrapper, and a field the body omits is left alone server-side, so a
      // switch never resets the control it was not touching.
      await confidentialApi.updateLink(link.id, { [field]: on })
      return { ok: true }
    } catch (error) {
      // The server answers a 400 with a field-keyed map, so each message lands next to
      // the input that caused it rather than in one sentence the seller has to decode.
      setFieldError(error.errors || { [field]: String(error.message || error) })
      return { ok: false }
    } finally {
      setBusy(null)
    }
  }, [])

  return { busy, fieldError, apply, clearFieldError: () => setFieldError(null) }
}

function AccessControlsPanel({ link, vocabulary, busy, fieldError, onToggle, onClearError }) {
  const controls = vocabulary?.panel?.controls || []
  if (controls.length === 0) return null

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-display text-lg font-semibold text-foreground">
            {link.title || 'Untitled link'}
          </h2>
          <p className="mt-0.5 font-mono text-xs text-muted-foreground">{link.id}</p>
        </div>
        <Badge tone={link.flags?.enable_confidential_view ? 'update' : 'neutral'}>
          {link.flags?.enable_confidential_view ? 'Confidential view on' : 'Confidential view off'}
        </Badge>
      </div>

      {fieldError && (
        <div className="mt-4">
          <Notice
            tone="destructive"
            title="That setting was not accepted"
            action={
              <Button onClick={onClearError}>Dismiss</Button>
            }
          >
            <ul className="list-disc space-y-0.5 pl-4">
              {Object.entries(fieldError).map(([field, message]) => (
                <li key={field}>
                  <span className="font-mono">{field}</span>: {message}
                </li>
              ))}
            </ul>
          </Notice>
        </div>
      )}

      <div className="mt-4 divide-y divide-border-subtle">
        {controls.map((control) => (
          <Toggle
            key={control.field}
            id={`toggle-${link.id}-${control.field}`}
            label={control.label}
            hint={control.summary}
            checked={Boolean(link.flags?.[control.field])}
            disabled={busy === `${link.id}:${control.field}`}
            onChange={(on) => onToggle(link, control.field, on)}
          />
        ))}
      </div>

      {/* The CLI spelling is named because the specification names it: `links update
          --confidential-view on|off`. A seller who works from the command line should
          be able to find the same switch here without a lookup. */}
      <p className="mt-4 border-t border-border-subtle pt-3 font-mono text-xs text-muted-foreground">
        {(vocabulary.cli_flags || {})[vocabField('confidential')]} on|off
        {'  '}
        {(vocabulary.cli_flags || {})[vocabField('protection')]} on|off
      </p>
    </Card>
  )
}

function vocabField(which) {
  // The field names are the vendor's and are served rather than hard-coded, but this
  // helper keeps the two places that print them readable.
  return which === 'confidential' ? 'enable_confidential_view' : 'enable_screenshot_protection'
}

/**
 * The focus band, drawn.
 *
 * Exported as a named export alongside the descriptor. The contract requires the
 * *default* export to be the descriptor and nothing else is discovered, so this does not
 * change how the host finds the feature - it only lets a test drive this one panel
 * directly instead of rendering the whole page to reach it.
 */
export function FocusBand({ link }) {
  const [pageHeight, setPageHeight] = useState(2400)
  const [viewportTop, setViewportTop] = useState(0)

  const viewport = useMemo(
    () => ({ pageHeight, viewportHeight: 800, viewportTop }),
    [pageHeight, viewportTop],
  )
  const { data, loading, error } = useAsync(() => confidentialApi.render(link.id, viewport), [
    link.id,
    pageHeight,
    viewportTop,
  ])

  if (loading) return <Spinner label="Resolving the focus band" />
  if (error) return <ErrorNote error={error} />
  if (!data) return null

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 className="font-display text-lg font-semibold text-foreground">Focus band</h2>
        <Badge tone={data.applied ? 'update' : 'neutral'}>
          {data.applied ? `Sharp fraction ${data.sharp_fraction}` : 'Not applied'}
        </Badge>
      </div>

      <p className="mt-2 text-sm text-muted-foreground">
        {data.applied
          ? 'Only the band in focus is delivered sharp. The rest arrives with its text withheld, so a single screenshot cannot capture a whole page.'
          : 'Confidential view is off on this link, so the whole page is delivered sharp. Nothing is withheld.'}
      </p>

      <BandStrip geometry={data} className="mt-4" />

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <Field label="Page height in pixels" id="band-page-height" hint="The specification names a band of a page and no size for it.">
          <input
            id="band-page-height"
            type="number"
            min="0"
            className={inputClass}
            value={pageHeight}
            onChange={(event) => setPageHeight(Number(event.target.value) || 0)}
          />
        </Field>
        <Field
          label="Scroll position in pixels"
          id="band-viewport-top"
          hint="The band containing this position is the sharp one."
        >
          <input
            id="band-viewport-top"
            type="number"
            min="0"
            className={inputClass}
            value={viewportTop}
            onChange={(event) => setViewportTop(Number(event.target.value) || 0)}
          />
        </Field>
      </div>

      {data.applied && data.blurred_count === 0 && (
        <div className="mt-4">
          <Notice tone="warning" title="This page has no blurred region">
            A page shorter than one band has nothing outside the focus, so there is
            nothing this control can withhold on it. The page is delivered whole.
          </Notice>
        </div>
      )}
    </Card>
  )
}

function ShortcutScope({ vocabulary }) {
  const served = (vocabulary?.shortcuts || []).length > 0 ? vocabulary.shortcuts : KNOWN_SHORTCUTS
  const scope = shortcutScope()
  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">What gets intercepted</h2>
      <p className="mt-2 text-sm text-muted-foreground">
        {scope.blockable} of the {scope.total} named capture shortcuts can be intercepted by a
        web page. The rest cannot, and are listed rather than hidden, because a seller told
        only about the ones that work would read this as protection.
      </p>
      <ul className="mt-4 divide-y divide-border-subtle">
        {served.map((shortcut) => (
          <li key={shortcut.name} className="flex flex-wrap items-center justify-between gap-3 py-2">
            <span className="font-mono text-sm text-foreground">{shortcut.name}</span>
            <span className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">{shortcut.action}</span>
              <Badge tone={shortcut.blockable === false ? 'warning' : 'insert'}>
                {shortcut.blockable === false ? 'Not interceptable' : 'Interceptable'}
              </Badge>
            </span>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function CaptureAttempts({ attempts }) {
  if (attempts.length === 0) {
    return (
      <EmptyState
        title="No capture attempts reported"
        description="A viewer's browser reports a capture attempt when it sees one. Nothing has been reported on any governed link yet."
      />
    )
  }

  const blockable = attempts.filter((row) => row.blockable)
  const unblockable = attempts.filter((row) => !row.blockable)

  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">Reported capture attempts</h2>
      <p className="mt-2 text-sm text-muted-foreground">
        What a viewer's browser saw. This product records what it was told; it does not
        assert that a capture was prevented.
      </p>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <div className="rounded-sm border border-border-subtle bg-muted p-3">
          <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            A page can intercept
          </p>
          <p className="mt-1 font-mono text-2xl font-semibold text-foreground">{blockable.length}</p>
        </div>
        <div className="rounded-sm border border-warning/30 bg-warning/10 p-3">
          <p className="text-[11px] uppercase tracking-[0.14em] text-warning">
            A page cannot intercept
          </p>
          <p className="mt-1 font-mono text-2xl font-semibold text-foreground">
            {unblockable.length}
          </p>
        </div>
      </div>
      <div className="mt-4">
        <JsonView value={attempts.slice(0, 10)} />
      </div>
    </Card>
  )
}

function RecordedDecisions({ decisions }) {
  if (!decisions || decisions.length === 0) return null
  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">
        Decisions this workflow derived
      </h2>
      <p className="mt-2 text-sm text-muted-foreground">
        The specification names some of its own surfaces as inferred and names two delivery
        shapes without choosing between them. Each decision below records what was chosen and
        what was rejected.
      </p>
      <ul className="mt-4 space-y-3">
        {decisions.map((decision) => (
          <li key={decision.id} className="rounded-sm border border-border-subtle p-3">
            <p className="font-mono text-xs text-muted-foreground">{decision.id}</p>
            <p className="mt-1 text-sm font-medium text-foreground">{decision.question}</p>
            {decision.rejected_because && (
              <p className="mt-1 text-sm text-muted-foreground">{decision.rejected_because}</p>
            )}
          </li>
        ))}
      </ul>
    </Card>
  )
}

function ConfidentialViewPage() {
  const [roomId, setRoomId] = useState('')

  const rooms = useAsync(() => listRooms(), [])
  const vocabulary = useAsync(() => confidentialApi.vocabulary(), [])
  const links = useAsync(() => confidentialApi.links(roomId), [roomId])
  const attempts = useAsync(() => confidentialApi.attempts(roomId), [roomId])
  const decisions = useAsync(() => confidentialApi.decisions(), [])
  const { busy, fieldError, apply, clearFieldError } = useLinkControls()

  const onToggle = useCallback(
    async (link, field, on) => {
      const result = await apply(link, field, on)
      // The controls live on the server, so a change is only real once the server has
      // answered. Refetching rather than patching local state means the page can never
      // show a switch in a position the store is not in - which for a security control is
      // the one failure that matters.
      if (result.ok) links.refetch()
    },
    [apply, links],
  )

  const board = useAsync(() => confidentialApi.summary(roomId), [roomId])

  if (vocabulary.loading || rooms.loading) return <Spinner label="Loading confidential view" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (links.error) return <ErrorNote error={links.error} onRetry={links.refetch} />

  const vocab = vocabulary.data
  const rows = links.data?.links || []

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-semibold text-foreground">Confidential view</h1>
        <p className="mt-2 max-w-3xl text-[15px] text-muted-foreground">
          Two booleans on a link. Confidential view renders one narrow band of each page sharp
          and delivers the rest blurred. Screenshot protection intercepts the capture and
          screen-recording shortcuts a page can intercept, and records what the viewer's
          browser saw.
        </p>
      </header>

      {/* The limitation, first and above the fold. This is the specification's own
          instruction - "treat as deterrence, and do not sell it as protection" - and it is
          a `warning` tone rather than a `neutral` one so it reads as a standing caveat
          rather than as body copy. */}
      <Notice tone="warning" title={DETERRENT_NOT_PROTECTION}>
        <p>{vocab?.limitation}</p>
      </Notice>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Governed links" value={board.data?.links ?? 0} icon="database" />
        <StatCard
          label="Confidential view"
          value={board.data?.confidential_view ?? 0}
          hint="links with the band on"
          icon="schema"
        />
        <StatCard
          label="Screenshot protection"
          value={board.data?.screenshot_protection ?? 0}
          hint="links with the guard on"
          icon="audit"
        />
        <StatCard
          label="Reported attempts"
          value={board.data?.attempts ?? 0}
          hint={`${board.data?.attempts_unblockable ?? 0} a page cannot intercept`}
          icon="search"
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Field label="Room" id="wf073-room" hint="Leave empty to see every governed link.">
          <select
            id="wf073-room"
            className={inputClass}
            value={roomId}
            onChange={(event) => setRoomId(event.target.value)}
          >
            <option value="">Every room</option>
            {(rooms.data?.records || rooms.data || []).map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <section className="space-y-4">
        <h2 className="font-display text-lg font-semibold text-foreground">
          Access controls, link by link
        </h2>
        {rows.length === 0 ? (
          <EmptyState
            title="No governed links yet"
            description="A link appears here once it is created with confidential view or screenshot protection."
          />
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {rows.map((link) => (
              <AccessControlsPanel
                key={link.id}
                link={link}
                vocabulary={vocab}
                busy={busy}
                fieldError={fieldError}
                onToggle={onToggle}
                onClearError={clearFieldError}
              />
            ))}
          </div>
        )}
      </section>

      {rows.length > 0 && (
        <FocusBand
          link={rows.find((link) => link.flags?.enable_confidential_view) || rows[0]}
        />
      )}

      <ShortcutScope vocabulary={vocab} />

      {attempts.loading ? (
        <Spinner label="Loading reported attempts" />
      ) : attempts.error ? (
        <ErrorNote error={attempts.error} onRetry={attempts.refetch} />
      ) : (
        <CaptureAttempts attempts={attempts.data?.attempts || []} />
      )}

      <RecordedDecisions decisions={decisions.data?.decisions} />
    </div>
  )
}

export default {
  id: 'wf-073-apply-confidential-view-and-block-screenshot',
  label: 'Confidential view',
  // The glyph is not in the shared PATHS map, so `iconPath` carries it and `icon`
  // falls back to the shared mark. `components/ui.jsx` is not edited.
  icon: 'audit',
  iconPath: CONFIDENTIAL_ICON,
  order: 730,
  Component: ConfidentialViewPage,
}