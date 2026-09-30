/**
 * The two primitives this page needs that the shared file does not carry.
 *
 * `components/ui.jsx` has `Banner`-shaped needs and nothing that fills them, and
 * the contract's own list of primitives mentions a `Notice` and a `Toggle` that
 * are not in the file. Rather than edit a shared file a hundred features would
 * collide on, both live here, and both are reported as candidates for promotion
 * into `ui.jsx` - which is platform work, and is somebody else's decision.
 *
 * On the shared `Button`
 * ---------------------
 * Every control here meets the 44px floor because the shared class carries
 * `min-h-11`, and the banner's action adds `cursor-pointer` itself: adding it to
 * the shared `Button` would make this page the only one that looks different.
 */

const BANNER_TONES = {
  ok: 'border-accent/40 bg-accent/10 text-foreground',
  bad: 'border-destructive/40 bg-destructive/10 text-foreground',
}

/**
 * A one-line outcome of an action.
 *
 * `role="alert"` when the outcome is bad and `role="status"` when it is not, so
 * a screen reader announces the failure and leaves the success to be found. The
 * two are not interchangeable: an action that was refused is the one a rep has to
 * know about.
 */
export function Banner({ notice }) {
  if (!notice) return null
  return (
    <div
      role={notice.tone === 'bad' ? 'alert' : 'status'}
      className={`mt-3 rounded-lg border p-3 text-sm ${BANNER_TONES[notice.tone] || BANNER_TONES.ok}`}
    >
      <p>{notice.text}</p>
      {notice.detail && (
        <div className="mt-2 rounded border border-border-subtle/40 p-2">
          <p className="text-xs text-muted-foreground">Copy this now</p>
          <p className="font-mono text-sm break-all">{notice.detail}</p>
        </div>
      )}
    </div>
  )
}

/**
 * A labelled on/off control.
 *
 * A real checkbox with a visible label rather than a styled `div`, so it is
 * reachable by keyboard and announced as what it is. The research's two states
 * are named in the label rather than left to a coloured pill, because "the
 * standard channel cannot be enriched" is a rule and not a preference.
 *
 * The label *wraps* the checkbox rather than pointing at it with `htmlFor`, so
 * the whole row is the hit area. A 20x20 box beside a text label leaves a
 * touch target of 20x20, and the label's own height is one line of text rather
 * than the 44px the floor asks for. The `id` is kept for the label association
 * so the click and the focus behaviour do not depend on the wrapping.
 */
export function LabelledToggle({ id, label, hint, checked, onChange, disabled }) {
  return (
    <label
      htmlFor={id}
      className="flex min-h-11 cursor-pointer items-start gap-3 py-1"
    >
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 h-5 w-5 shrink-0 cursor-pointer rounded border-border-subtle/60 bg-background/60 accent-current"
      />
      <span className="text-sm">
        <span className="font-medium text-foreground">{label}</span>
        {hint && <span className="mt-0.5 block text-xs text-muted-foreground">{hint}</span>}
      </span>
    </label>
  )
}

/**
 * A read-only JSON editor, for the two free-form parts of a change event.
 *
 * A textarea rather than a `contenteditable` div: the payload is arbitrary JSON
 * and a textarea is what a rep will paste into, is keyboard reachable, and is
 * announced as a text field. The parse error is shown where the mistake was made
 * rather than as a toast the rep has already scrolled past.
 */
export function JsonField({ id, label, hint, value, onChange, rows = 5 }) {
  let parseError = ''
  try {
    JSON.parse(value || '{}')
  } catch (error) {
    parseError = String(error.message || error)
  }
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-xs font-medium text-muted-foreground">
        {label}
      </label>
      <textarea
        id={id}
        rows={rows}
        spellCheck={false}
        value={value}
        aria-invalid={parseError ? 'true' : undefined}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-lg border border-border-subtle/50 bg-background/60 px-3 py-2 font-mono text-[13px] text-foreground focus:border-accent"
      />
      <p className={`text-xs ${parseError ? 'text-destructive' : 'text-muted-foreground/80'}`}>
        {parseError || hint}
      </p>
    </div>
  )
}
