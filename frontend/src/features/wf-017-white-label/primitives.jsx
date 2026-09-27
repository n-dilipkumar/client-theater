import { useEffect, useState } from 'react'
import { Badge, Button, Icon } from '@/components/ui'
import { ICONS } from './icons'

/**
 * Three things this feature needs that the shared set in `components/ui.jsx`
 * does not export: a named glyph, a copy control, and a status pill.
 *
 * The branch added all three -- `CopyButton` and `StatusPill` as components and
 * ten paths to the shared `PATHS` map. `components/ui.jsx` is shared, so that is
 * a hundred branches editing one file at the same line, which is the collision
 * the plugin host exists to prevent. The contract's answer is explicit: build
 * what you need inside your own feature folder and say so in the report, and the
 * integrator promotes recurring pieces into `ui.jsx` as platform work, once.
 *
 * What an integrator should take, stated unambiguously:
 *
 *   - `CopyButton` and `StatusPill` are platform-shaped. A "copy this value"
 *     control and a labelled status pill are not specific to white-labelling,
 *     and both meet a real accessibility requirement -- a clipboard write is
 *     invisible to a screen reader, and a status conveyed by colour alone is
 *     invisible to about one man in twelve. Both belong in `ui.jsx` once, and
 *     both are ready to move: they import nothing from this folder except the
 *     glyph map.
 *   - The glyphs are not a promotion candidate as a set. `components/ui.jsx`
 *     already has the mechanism -- `Icon path=` -- and this feature uses it.
 *     `Glyph` is the one exception: it is a two-line convenience over that
 *     mechanism, and it disappears the moment `Icon` accepts a `name` for a
 *     per-feature map.
 *
 * Every control here meets the same floor as the shared primitives: a 44px
 * minimum target via the shared `Button`, a real focusable element (the global
 * `:focus-visible` rule in `index.css` supplies the ring and is never removed
 * here), a visible text label, and no emoji used as an icon.
 */

/**
 * A glyph from this feature's own map, drawn through the shared `Icon`.
 *
 * `Icon` takes a `path` precisely so a feature never has to append to `PATHS`.
 * Naming the lookups keeps the paths in one place and means a glyph swapped for
 * a better one is a one-line change rather than a search.
 *
 * A name this feature does not carry is a programming error, not a fallback, so
 * it throws rather than quietly drawing the shared `schema` mark: an earlier cut
 * used `<Glyph name="refresh" />`, and because `refresh` lives in the *shared*
 * map rather than this one, the shared `Icon` silently substituted a
 * three-lines glyph and nothing in any test noticed. A glyph that is already
 * shared is now requested from the shared `Icon` by name, and a typo is loud.
 */
export function Glyph({ name, size = 18, className = '' }) {
  const path = ICONS[name]
  if (!path) throw new Error(`Glyph "${name}" is not in this feature's icon map (icons.js)`)
  return <Icon path={path} size={size} className={className} />
}

/**
 * Copy a value to the clipboard and acknowledge that it happened.
 *
 * The share link is the one thing an operator copies and sends, so a button
 * that silently does nothing is worse than no button: the operator sends the
 * wrong link. The acknowledgement is announced through a live region as well as
 * shown, because a clipboard write is otherwise a change with no observable
 * effect for anyone not looking at the button.
 *
 * The link stays visible and selectable either way, so a refused clipboard --
 * which happens for real, on an insecure origin and under some permission
 * policies -- degrades to "read it and copy it yourself" rather than to an error.
 */
export function CopyButton({ value, label = 'Copy' }) {
  const [copied, setCopied] = useState(false)
  const [timer, setTimer] = useState(null)

  // Clear the pending revert on unmount. Without this the callback fires into a
  // component that no longer exists, and two clicks in quick succession leave two
  // timers, so `copied` reverts two seconds after the *first* click rather than
  // after the last -- the label changes back while the operator is still looking
  // at it.
  useEffect(() => () => clearTimeout(timer), [timer])

  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimer(setTimeout(() => setCopied(false), 2000))
    } catch {
      // Clipboard access can be refused. Not worth an error banner: the value
      // is on screen next to this button and remains selectable.
      setCopied(false)
    }
  }

  return (
    <>
      <Button onClick={copy} aria-label={`${label}: ${value}`}>
        <Glyph name={copied ? 'check' : 'copy'} size={16} />
        {copied ? 'Copied' : label}
      </Button>
      <span role="status" aria-live="polite" className="sr-only">
        {copied ? `${label}: copied to clipboard` : ''}
      </span>
    </>
  )
}

/**
 * The domain verification state, as a word.
 *
 * Tone is never the only channel: each state carries its own text and its own
 * glyph, so "Awaiting DNS" cannot be mistaken for "Check failed" by anyone who
 * cannot separate amber from red. That is the whole reason this is a component
 * rather than a `<Badge tone=...>` at each call site -- a bare tone is exactly
 * the mistake.
 *
 * An unrecognised status falls back to "Awaiting DNS" rather than rendering
 * nothing, because the status is an open JSON field: a value this build has
 * never seen should read as unconfirmed, not as absent.
 */
const STATES = {
  verified: { tone: 'insert', glyph: 'check', text: 'Verified' },
  unverified: { tone: 'restore', glyph: 'clock', text: 'Awaiting DNS' },
  failed: { tone: 'delete', glyph: 'alert', text: 'Check failed' },
}

export function StatusPill({ status }) {
  const state = STATES[status] || STATES.unverified
  return (
    <Badge tone={state.tone}>
      <span className="inline-flex items-center gap-1.5">
        <Glyph name={state.glyph} size={13} />
        {state.text}
      </span>
    </Badge>
  )
}
