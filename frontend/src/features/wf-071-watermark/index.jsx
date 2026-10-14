/**
 * WF-071: stamp a dynamic per-viewer watermark on a tracked link.
 *
 * A rep turns a watermark on for a link and writes its text. At view time the
 * renderer substitutes the identity tokens with the values of that view - the
 * viewer's own email, the date and time, the link's URL and the viewer's IP - and
 * composites the overlay onto every page.
 *
 * The page is built from `@/components/ui` and from the primitives in this
 * folder. It reads its positions, rotations, tokens, limits and defaults from the
 * server's `/vocabulary` route rather than repeating them, so the panel cannot
 * offer a value the rules refuse.
 *
 * Four things this page deliberately does not do, each from the research:
 *
 *   - It does not render the document. The researched viewer (a rasterised page
 *     with the overlay composited in) is out of scope; the link references a
 *     WF-018 library asset and this product holds no document bytes. The stamp
 *     panel below is the viewer's call, so a reviewer can produce a real overlay.
 *   - It does not burn a watermark into any stored file. The spec is explicit:
 *     "Nothing is burned into the stored PDF - the watermark exists only in the
 *     delivered view."
 *   - It does not gate access. The spec's own Why: "Watermarking is a deterrent
 *     and a trace, not an access control." Access is WF-069's gate and stays its.
 *   - It does not show the overlay at its rotation. This is a data panel, and a
 *     rotated preview is vestibular motion the design floor refuses; the angle is
 *     printed as a number instead.
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
  POSITION_LABELS,
  TOKEN_LABELS,
  listRooms,
  numberInRange,
  stateOf,
  tokensIn,
  watermarkApi,
  wholeInRange,
  withToken,
} from './api'
import {
  ConfigSummary,
  InlineNote,
  OverlayPreview,
  StampRow,
  TokenChips,
  WatermarkStateBadge,
} from './primitives'

const TEXT_MAX = 200

/** Everything the page reads, loaded together so the panels cannot disagree. */
function useBoard(roomId, selectedLink) {
  const vocabulary = useAsync(() => watermarkApi.vocabulary(), [])
  const summary = useAsync(() => watermarkApi.summary(roomId), [roomId])
  const links = useAsync(() => watermarkApi.links(roomId), [roomId])
  const detail = useAsync(
    () => (selectedLink ? watermarkApi.read(selectedLink) : Promise.resolve(null)),
    [selectedLink],
  )
  const stamps = useAsync(
    () => (selectedLink ? watermarkApi.stamps(selectedLink) : Promise.resolve(null)),
    [selectedLink],
  )
  const loading = vocabulary.loading || summary.loading || links.loading
  const error = vocabulary.error || summary.error || links.error
  return { vocabulary, summary, links, detail, stamps, loading, error }
}

function WatermarkPage() {
  const [roomId, setRoomId] = useState('')
  const [selectedLink, setSelectedLink] = useState(null)
  const [note, setNote] = useState(null)
  const [problem, setProblem] = useState(null)
  const [draftVersion, setDraftVersion] = useState(0)

  const rooms = useAsync(() => listRooms(), [])
  const board = useBoard(roomId, selectedLink)

  function refetchAll() {
    board.summary.refetch()
    board.links.refetch()
    board.detail.refetch()
    board.stamps.refetch()
  }

  async function act(fn, success) {
    setProblem(null)
    setNote(null)
    try {
      const result = await fn()
      setNote(typeof success === 'function' ? success(result) : success)
      setDraftVersion((value) => value + 1)
      refetchAll()
      return result
    } catch (error) {
      setProblem(error)
      return null
    }
  }

  if (board.loading && !board.summary.data) return <Spinner label="Loading viewer watermarks" />
  if (board.error) return <ErrorNote error={board.error} onRetry={refetchAll} />

  const vocabulary = board.vocabulary.data
  const summary = board.summary.data
  const links = board.links.data?.links || []
  const detail = board.detail.data
  const stamps = board.stamps.data?.stamps || []

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold text-foreground">Viewer watermark</h1>
        <p className="text-sm text-muted-foreground">
          A watermark set on a tracked link, stamped with each viewer&apos;s own email, date, time,
          link and IP address at view time.
        </p>
      </header>

      {note && <InlineNote tone="success">{note}</InlineNote>}
      {problem && <ErrorNote error={problem} />}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Tracked links"
          value={summary?.links ?? 0}
          icon="rooms"
          hint="WF-121 links a watermark can be set on"
        />
        <StatCard
          label="Watermarked"
          value={summary?.watermarked ?? 0}
          icon="audit"
          hint={`${summary?.tiled ?? 0} tiled, ${summary?.cleared ?? 0} on with no text`}
        />
        <StatCard label="Stamps" value={summary?.stamps ?? 0} icon="schema" hint="Views stamped" />
        <StatCard
          label="Viewers stamped"
          value={summary?.viewers_stamped ?? 0}
          icon="database"
          hint="Distinct identities carried by a stamp"
        />
      </div>

      <Card className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold text-foreground">Tracked links</h2>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Room" id="wf071-room" hint="A link belongs to a room.">
            <select
              id="wf071-room"
              className={inputClass}
              value={roomId}
              onChange={(event) => {
                setRoomId(event.target.value)
                setSelectedLink(null)
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

        {links.length === 0 ? (
          <EmptyState
            title="No tracked link yet"
            description="A watermark is set on a WF-121 tracked link. Share one from the Tracked deal link page, then enable its watermark here."
          />
        ) : (
          <ul className="flex flex-col">
            {links.map((row) => (
              <li
                key={row.link.id}
                className="flex flex-wrap items-center justify-between gap-3 border-b border-border-subtle py-3 last:border-b-0"
              >
                <span className="flex min-w-0 flex-col">
                  <span className="truncate text-sm text-foreground">
                    {row.link.title || row.link.id}
                  </span>
                  <span className="truncate font-mono text-xs text-muted-foreground">
                    {row.link.id} | {row.link.asset_name || row.link.asset_id} |{' '}
                    {row.link.page_count ?? '?'} page(s)
                  </span>
                  <ConfigSummary config={row.watermark_config} />
                </span>
                <span className="flex flex-wrap items-center gap-2">
                  <WatermarkStateBadge state={stateOf(row)} />
                  <Button
                    icon="audit"
                    onClick={() => setSelectedLink(row.link.id === selectedLink ? null : row.link.id)}
                  >
                    {row.link.id === selectedLink ? 'Close the panel' : 'Open the watermark panel'}
                  </Button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {selectedLink && detail && (
        <Card className="flex flex-col gap-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="text-lg font-semibold text-foreground">
                {detail.link?.title || 'Tracked link'}
              </h2>
              <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                {detail.link?.id} | {detail.link?.share_url || 'no share URL'} | state{' '}
                {detail.link?.state}
              </p>
            </div>
            <WatermarkStateBadge state={stateOf(detail)} />
          </div>

          <ConfigPanel
            key={`${selectedLink}:${draftVersion}`}
            linkId={selectedLink}
            detail={detail}
            vocabulary={vocabulary}
            onAct={act}
          />

          <StampPanel
            linkId={selectedLink}
            pageCount={detail.link?.page_count}
            onAct={act}
          />
        </Card>
      )}

      {selectedLink && (
        <Card className="flex flex-col gap-3">
          <h2 className="text-lg font-semibold text-foreground">Stamps recorded</h2>
          <p className="text-xs text-muted-foreground">
            Every stamped view carries the identity it was stamped with, so a leak can be traced
            back to one viewer.
          </p>
          {stamps.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No view has been stamped. Stamp one above to see the substituted text.
            </p>
          ) : (
            <ul className="flex flex-col">
              {stamps.map((stamp) => (
                <StampRow key={stamp.id} stamp={stamp} />
              ))}
            </ul>
          )}
        </Card>
      )}

      <Card className="flex flex-col gap-2">
        <h2 className="text-base font-semibold text-foreground">Not built here</h2>
        <p className="text-xs text-muted-foreground">
          The research names these, and they belong to another workflow or to a deployment. They are
          listed so their absence reads as a decision rather than an oversight.
        </p>
        <ul className="list-inside list-disc text-xs text-muted-foreground">
          <li>An in-browser document viewer. The overlay is returned for it to composite.</li>
          <li>A watermark burned into stored bytes. The spec forbids it.</li>
          <li>Access control. A watermark is a trace; the gate is WF-069&apos;s.</li>
          <li>A team-plan resolver. The gate accepts the plan named on the request.</li>
        </ul>
      </Card>
    </div>
  )
}

function ConfigPanel({ linkId, detail, vocabulary, onAct }) {
  const defaults = vocabulary?.defaults
  const limits = vocabulary?.limits
  const existing = detail?.watermark_config
  const [draft, setDraft] = useState(() => ({
    text: existing?.text ?? '',
    is_tiled: existing?.is_tiled ?? false,
    position: existing?.position ?? '',
    rotation: existing?.rotation ?? '',
    color: existing?.color ?? '',
    font_size: existing?.font_size ?? '',
    opacity: existing?.opacity ?? '',
  }))
  const [plan, setPlan] = useState('')
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState(false)

  const positions = vocabulary?.positions || []
  const rotations = vocabulary?.rotations || []
  const tokens = vocabulary?.tokens || []
  const textLength = draft.text.trim().length
  const used = tokensIn(draft.text)

  const refused = []
  if (textLength === 0) refused.push('A watermark text is required.')
  if (textLength > TEXT_MAX) refused.push(`The text must be at most ${TEXT_MAX} characters.`)
  const fontOk =
    draft.font_size === '' ? true : wholeInRange(draft.font_size, limits?.font_size_min ?? 1, limits?.font_size_max ?? 96) !== null
  if (!fontOk) refused.push('The font size must be a whole number inside the schema range.')
  if (draft.opacity !== '' && numberInRange(draft.opacity, limits?.opacity_min ?? 0, limits?.opacity_max ?? 1) === null) {
    refused.push('The opacity must be a number from 0 to 1.')
  }

  function payload() {
    const body = { text: draft.text.trim() }
    if (draft.is_tiled) body.is_tiled = true
    if (draft.position) body.position = draft.position
    if (draft.rotation !== '') body.rotation = Number(draft.rotation)
    if (draft.color.trim()) body.color = draft.color.trim()
    if (draft.font_size !== '') body.font_size = wholeInRange(draft.font_size, 1, 96)
    if (draft.opacity !== '') body.opacity = numberInRange(draft.opacity, 0, 1)
    return body
  }

  async function save() {
    setBusy(true)
    await onAct(
      () => watermarkApi.enable(linkId, payload(), plan || undefined),
      'Saved the watermark. It is stamped on every page at view time.',
    )
    setBusy(false)
  }

  async function clearText() {
    setBusy(true)
    await onAct(
      () => watermarkApi.patch(linkId, { watermark_config: null }, plan || undefined),
      'Cleared the text and left the watermark on, so the toggle is unchanged.',
    )
    setBusy(false)
  }

  async function turnOff() {
    setBusy(true)
    await onAct(
      () => watermarkApi.disable(linkId),
      'Turned the watermark off. The text is kept, so enabling it restores it.',
    )
    setBusy(false)
  }

  async function runPreview() {
    setBusy(true)
    await onAct(async () => {
      const result = await watermarkApi.preview({
        ...payload(),
        viewer: 'buyer@northwind.example',
        ip_address: '203.0.113.9',
        page_count: detail.link?.page_count || 1,
      })
      setPreview(result)
      return result
    }, 'Previewed the overlay. Nothing was saved.')
    setBusy(false)
  }

  return (
    <div className="flex flex-col gap-4 border-t border-border-subtle pt-4">
      <div className="flex flex-col gap-1">
        <h3 className="text-base font-semibold text-foreground">Watermark configuration</h3>
        <p className="text-xs text-muted-foreground">
          All seven fields are required by the researched schema. A field left blank is filled from
          the documented default, shown as the placeholder.
        </p>
      </div>

      <Field
        label="Watermark text"
        id="wf071-text"
        hint={`${textLength} of ${TEXT_MAX} characters. Insert a token to stamp the view's own value.`}
      >
        <input
          id="wf071-text"
          className={inputClass}
          value={draft.text}
          placeholder={defaults?.text || 'Confidential'}
          onChange={(event) => setDraft({ ...draft, text: event.target.value })}
        />
      </Field>

      <div className="flex flex-col gap-2">
        <span className="text-xs text-muted-foreground">Tokens</span>
        <TokenChips
          tokens={tokens}
          labels={TOKEN_LABELS}
          onInsert={(token) => setDraft({ ...draft, text: withToken(draft.text, token) })}
        />
        <span className="font-mono text-xs text-muted-foreground">
          {used.length ? `In use: ${used.join(', ')}` : 'No token used: the text is the same on every page.'}
        </span>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Field label="Position" id="wf071-position" hint="One of the nine researched anchors.">
          <select
            id="wf071-position"
            className={inputClass}
            value={draft.position}
            onChange={(event) => setDraft({ ...draft, position: event.target.value })}
          >
            <option value="">{`Default: ${POSITION_LABELS[defaults?.position] || 'Centre'}`}</option>
            {positions.map((value) => (
              <option key={value} value={value}>
                {POSITION_LABELS[value] || value}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Rotation" id="wf071-rotation" hint="The schema allows 0, 30, 45, 90 or 180.">
          <select
            id="wf071-rotation"
            className={inputClass}
            value={draft.rotation}
            onChange={(event) => setDraft({ ...draft, rotation: event.target.value })}
          >
            <option value="">{`Default: ${defaults?.rotation ?? 45} degrees`}</option>
            {rotations.map((value) => (
              <option key={value} value={value}>
                {`${value} degrees`}
              </option>
            ))}
          </select>
        </Field>

        <Field
          label="Colour"
          id="wf071-color"
          hint="Hex, three or six digits. The value is data, not a theme colour."
        >
          <input
            id="wf071-color"
            className={inputClass}
            value={draft.color}
            placeholder={defaults?.color || 'Six-digit hex'}
            onChange={(event) => setDraft({ ...draft, color: event.target.value })}
          />
        </Field>

        <Field
          label="Font size in points"
          id="wf071-size"
          hint={`A whole number from ${limits?.font_size_min ?? 1} to ${limits?.font_size_max ?? 96}.`}
        >
          <input
            id="wf071-size"
            className={inputClass}
            type="number"
            inputMode="numeric"
            value={draft.font_size}
            placeholder={String(defaults?.font_size ?? 24)}
            onChange={(event) => setDraft({ ...draft, font_size: event.target.value })}
          />
        </Field>

        <Field label="Opacity" id="wf071-opacity" hint="A number from 0 to 1.">
          <input
            id="wf071-opacity"
            className={inputClass}
            type="number"
            inputMode="decimal"
            step="0.05"
            value={draft.opacity}
            placeholder={String(defaults?.opacity ?? 0.5)}
            onChange={(event) => setDraft({ ...draft, opacity: event.target.value })}
          />
        </Field>

        <Field
          label="Plan named on the request"
          id="wf071-plan"
          hint="The plan gate fires only when this turns the watermark on. Starter is refused."
        >
          <select
            id="wf071-plan"
            className={inputClass}
            value={plan}
            onChange={(event) => setPlan(event.target.value)}
          >
            <option value="">Not named</option>
            {(vocabulary?.plan?.plans || []).map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <label
        htmlFor="wf071-tiled"
        className="flex min-h-11 w-fit items-center gap-3 rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground"
      >
        <input
          id="wf071-tiled"
          type="checkbox"
          className="h-4 w-4"
          checked={draft.is_tiled}
          onChange={(event) => setDraft({ ...draft, is_tiled: event.target.checked })}
        />
        Tile the watermark across the page rather than placing one
      </label>

      {refused.length > 0 && <InlineNote tone="warning">{refused.join(' ')}</InlineNote>}

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="primary" icon="plus" disabled={busy || refused.length > 0} onClick={save}>
          Save the watermark
        </Button>
        <Button icon="search" disabled={busy} onClick={runPreview}>
          Preview the overlay
        </Button>
        <Button icon="close" disabled={busy || !detail.watermark_config} onClick={clearText}>
          Clear the text, keep it on
        </Button>
        <Button
          variant="danger"
          icon="trash"
          disabled={busy || !detail.enable_watermark}
          onClick={turnOff}
        >
          Turn the watermark off
        </Button>
        <span className="text-xs text-muted-foreground">
          Enabling is refused with 403 when the named plan does not cover a watermark.
        </span>
      </div>

      {preview && (
        <div className="flex flex-col gap-2">
          <h3 className="text-base font-semibold text-foreground">Preview</h3>
          <OverlayPreview
            rendered={preview.rendered_text}
            overlay={preview.overlay}
            unresolved={preview.unresolved_tokens}
          />
        </div>
      )}
    </div>
  )
}

function StampPanel({ linkId, pageCount, onAct }) {
  const [viewer, setViewer] = useState('a.buyer@northwind.example')
  const [ip, setIp] = useState('203.0.113.9')
  const [pages, setPages] = useState(String(pageCount || 1))
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)

  const wanted = wholeInRange(pages, 1, 10000)

  async function stamp() {
    setBusy(true)
    await onAct(async () => {
      const response = await watermarkApi.stamp(linkId, {
        viewer: viewer.trim(),
        ip_address: ip.trim(),
        page_count: wanted,
      })
      setResult(response)
      return response
    }, 'Stamped the view. The text was substituted with this viewer’s values.')
    setBusy(false)
  }

  return (
    <div className="flex flex-col gap-3 border-t border-border-subtle pt-4">
      <div className="flex flex-col gap-1">
        <h3 className="text-base font-semibold text-foreground">Stamp a view</h3>
        <p className="text-xs text-muted-foreground">
          This is the viewer&apos;s call, not a seller control. It substitutes the tokens with the
          values bound to this one view and composites the overlay onto every page. Nothing is
          written into a document.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Viewer email" id="wf071-viewer-email" hint="The address the link was gated to.">
          <input
            id="wf071-viewer-email"
            className={inputClass}
            value={viewer}
            onChange={(event) => setViewer(event.target.value)}
          />
        </Field>
        <Field label="Viewer IP address" id="wf071-viewer-ip" hint="The address this view arrived from.">
          <input
            id="wf071-viewer-ip"
            className={inputClass}
            value={ip}
            onChange={(event) => setIp(event.target.value)}
          />
        </Field>
        <Field label="Pages delivered" id="wf071-viewer-pages" hint="The link's own page count by default.">
          <input
            id="wf071-viewer-pages"
            className={inputClass}
            type="number"
            inputMode="numeric"
            value={pages}
            onChange={(event) => setPages(event.target.value)}
          />
        </Field>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="primary" icon="plus" disabled={busy || wanted === null} onClick={stamp}>
          Stamp this view
        </Button>
        <span className="text-xs text-muted-foreground">
          {pageCount ? `The link delivers ${pageCount} page(s).` : 'The link has no page count; the overlay is one page.'}
        </span>
      </div>

      {result && (
        <div className="flex flex-col gap-2">
          <h3 className="text-base font-semibold text-foreground">Delivered overlay</h3>
          {result.stamped ? (
            <OverlayPreview
              rendered={result.rendered_text}
              overlay={result.overlay}
              unresolved={result.unresolved_tokens}
            />
          ) : (
            <InlineNote tone="info">
              This link has no watermark, so nothing was composited. Enable one above.
            </InlineNote>
          )}
          <p className="font-mono text-xs text-muted-foreground">
            {result.page_count ?? 0} page(s) | stamp {result.stamp_id || 'not recorded'}
          </p>
        </div>
      )}
    </div>
  )
}

export default {
  id: 'wf-071-watermark',
  label: 'Viewer watermark',
  icon: 'audit',
  order: 710,
  Component: WatermarkPage,
}
