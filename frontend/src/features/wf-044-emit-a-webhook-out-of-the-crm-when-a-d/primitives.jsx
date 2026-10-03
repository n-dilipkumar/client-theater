/**
 * Local presentation pieces for WF-044.
 *
 * `components/ui.jsx` is shared and a hundred features each appending to it is
 * the collision the feature host exists to prevent, so the glyph is passed as a
 * path through `<Icon path=...>` and the two pieces this workflow genuinely needs
 * are built in this folder: a copyable field (an endpoint URL and a secret are
 * both things a rep has to paste somewhere else, and a value they cannot select is
 * a value they retype) and a definition list for the settings page.
 *
 * The accessibility floor is the same as everywhere else: 44px minimum touch
 * targets, visible focus, a text label beside every glyph so meaning survives with
 * icons off, no emoji, and nothing that moves under `prefers-reduced-motion`.
 */

import { useState } from 'react'
import { Icon } from '@/components/ui'

/**
 * A bolt inside a ring: the CRM's outbound request.
 *
 * Passed as a `path` rather than an `icon` name because this glyph is not in the
 * shared `PATHS` map and adding it there would be an edit to a shared file.
 * `WEBHOOK_ICON` is the fallback for a consumer that reads only that field.
 */
export const WEBHOOK_ICON =
  'M12 2a10 10 0 100 20 10 10 0 000-20zm0 2a8 8 0 110 16 8 8 0 010-16zm1.5 3.5L8 13h4l-1.5 4.5L16 10h-4l1.5-4.5z'

/** A padlock, for the one shared secret the whole endpoint authenticates with. */
export const SECRET_ICON =
  'M12 2a5 5 0 00-5 5v3H6a2 2 0 00-2 2v8a2 2 0 002 2h12a2 2 0 002-2v-8a2 2 0 00-2-2h-1V7a5 5 0 00-5-5zm0 2a3 3 0 013 3v3H9V7a3 3 0 013-3zm0 9a2 2 0 011 2v2a2 2 0 01-1 2 2 2 0 01-1-2v-2a2 2 0 011-2z'

/** A stamp, for a delivery that was applied. */
export const APPLIED_ICON =
  'M12 2l3 3-3 3-3-3 3-3zm0 8l3 3-3 3-3-3 3-3zM4 20h16v2H4v-2z'

/** A cross in a ring, for a delivery the endpoint turned away. */
export const REFUSED_ICON =
  'M12 2a10 10 0 100 20 10 10 0 000-20zm0 2a8 8 0 110 16 8 8 0 010-16zm-1 4h2v6h-2V8zm0 8h2v2h-2v-2z'

const ICONS = { webhook: WEBHOOK_ICON, secret: SECRET_ICON, applied: APPLIED_ICON, refused: REFUSED_ICON }

/** Draw one of this feature's glyphs, falling through to the shared set. */
export function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} path={ICONS[name]} size={size} className={className} />
}

/**
 * A value a rep has to copy, with a copy button that says what it did.
 *
 * The button reports its own outcome in words rather than only turning into a
 * tick: a control that silently resets after two seconds leaves a rep unsure
 * whether the value is on the clipboard, and this is the control they will use
 * most. Copying is the only thing on the page that touches the clipboard, and it
 * degrades to a selectable, focusable `input` when the API is unavailable.
 */
export function CopyField({ label, value, secret = false, hint }) {
  const [state, setState] = useState('idle')

  async function copy() {
    try {
      await navigator.clipboard.writeText(String(value ?? ''))
      setState('copied')
    } catch {
      // Clipboard access can be refused outright - an insecure origin, or a
      // permission the browser withheld. Saying so beats a button that appears
      // to do nothing.
      setState('unavailable')
    }
  }

  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <div className="flex flex-wrap items-center gap-2">
        <input
          readOnly
          value={value ?? ''}
          type={secret ? 'password' : 'text'}
          spellCheck={false}
          aria-label={label}
          className="min-h-11 flex-1 rounded-lg border border-border-subtle/50 bg-background/60 px-3 font-mono text-[13px] text-foreground focus:border-accent"
        />
        <button
          type="button"
          onClick={copy}
          className="inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-lg bg-muted px-4 text-sm text-foreground transition-colors duration-200 hover:bg-border-subtle focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          {secret ? <Glyph name="secret" /> : <Glyph name="webhook" />}
          Copy
        </button>
      </div>
      <p className="text-xs text-muted-foreground" role="status">
        {state === 'copied'
          ? 'Copied to the clipboard.'
          : state === 'unavailable'
            ? 'This browser would not give the page the clipboard. The value is selectable - copy it with Ctrl+C.'
            : hint || ' '}
      </p>
    </div>
  )
}

/**
 * A definition list of the settings page's facts.
 *
 * A grid of label/value pairs rather than free text, because a reader comparing
 * two rooms' endpoints is looking for the same key in both, and prose makes that
 * comparison a reading task instead of a glance.
 */
export function Facts({ rows }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
      {rows
        .filter((row) => row && row.value !== undefined && row.value !== null && row.value !== '')
        .map((row) => (
          <div key={row.label} className="flex items-baseline justify-between gap-3 border-b border-border-subtle/30 py-1.5">
            <dt className="text-xs text-muted-foreground">{row.label}</dt>
            <dd className="font-mono text-[13px] text-foreground">{row.value}</dd>
          </div>
        ))}
    </dl>
  )
}

/**
 * The sentence from the research that governs whatever is on screen.
 *
 * Rendered at the top of the page rather than in a footer, every time. The two
 * most expensive things to lose in this workflow are the publish rule - an
 * endpoint nobody published silently takes nothing - and the fact that the room
 * needs no per-workflow secret, which is the whole reason one endpoint serves any
 * number of automations.
 */
export function ResearchNote({ children, glyph = 'webhook' }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-border-subtle/40 bg-muted/40 p-4">
      <span className="mt-0.5 text-accent">
        <Glyph name={glyph} size={18} />
      </span>
      <p className="text-sm text-muted-foreground">{children}</p>
    </div>
  )
}
