/**
 * Primitives this page needs and the shared set cannot express.
 *
 * The shared `components/ui.jsx` carries Icon, Button, Badge, Card, StatCard, Field,
 * Spinner, ErrorNote, EmptyState, inputClass, useAsync and JsonView. Three things are
 * missing for this page, and all three are built here rather than added to the shared
 * file, which a feature may not edit. The integrator promotes a recurring one into
 * `ui.jsx` as platform work, once.
 *
 * **Why no status is a colour.** This page's whole subject is a consent and recording
 * state machine, and the two questions a seller asks are always "what state is this in"
 * and "did they agree". A rep looking at a booking that was recorded after a decline is
 * reading exactly this row, and the answer must never depend on hue - which is also the
 * design system's accessibility floor, not only good practice. So every state carries a
 * glyph, the state's own name, and a written sentence. Three redundant channels, and a
 * reader who cannot see one of them still has the other two.
 *
 * **Why the glyphs are paths rather than emoji.** The design system bans emoji as
 * icons, and the shared PATHS map is a shared file. So the three marks live here.
 */

import { Icon } from '@/components/ui'

/**
 * Marks for the recording axis.
 *
 * `recording` is a filled square because it is the only genuinely in-progress state and
 * it should read as different in kind, not just in colour, from everything else.
 */
export const Glyphs = {
  blocked: 'M12 3a9 9 0 100 18 9 9 0 000-18zm0 4v6m0 4h.01',
  armed: 'M5 12l5 5L19 7',
  inProgress: 'M7 7h10v10H7z',
  complete: 'M20 6L9 17l-5-5',
  cancelled: 'M6 6l12 12M18 6L6 18',
  pending: 'M12 7v5l3 2m6-2a9 9 0 11-18 0 9 9 0 0118 0z',
  consent: 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z',
  recording: 'M12 18a3 3 0 003-3V9a3 3 0 00-6 0v6a3 3 0 003 3z',
  bot: 'M12 3v4m0 10v4M5 12H3m18 0h-2M12 8a4 4 0 100 8 4 4 0 000-8z',
  link: 'M10 13a5 5 0 007 0l2-2a5 5 0 00-7-7l-1 1m-1 8a5 5 0 00-7 0l-2 2a5 5 0 007 7l1-1',
  warn: 'M12 9v4m0 4h.01M10.3 3.9L2.4 17.5A2 2 0 004.1 20.5h15.8a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z',
  email: 'M3 7l9 6 9-6M4 5h16a1 1 0 011 1v12a1 1 0 01-1 1H4a1 1 0 01-1-1V6a1 1 0 011-1z',
}

/** The recording-axis mark for a state name the server sends. */
const RECORDING_GLYPHS = {
  blocked: Glyphs.blocked,
  armed: Glyphs.armed,
  in_progress: Glyphs.inProgress,
  complete: Glyphs.complete,
  cancelled: Glyphs.cancelled,
}

const RECORDING_TONES = {
  blocked: 'border-warning/40 bg-warning/10 text-warning',
  armed: 'border-accent/30 bg-accent-soft text-accent',
  in_progress: 'border-accent/40 bg-accent-soft text-accent',
  complete: 'border-success/40 bg-success/10 text-success',
  cancelled: 'border-border-subtle bg-muted text-muted-foreground',
}

/**
 * A recording's state, in words.
 *
 * The sentences are the point of this component. `blocked` in particular is the state
 * a seller most needs to understand and least needs to decode: the recording is waiting
 * on a person, not broken.
 */
const RECORDING_WORDS = {
  blocked: 'Waiting on a consent decision',
  armed: 'Ready to record',
  in_progress: 'Recording now',
  complete: 'Recording complete',
  cancelled: 'Not recorded',
}

/**
 * The recording state, as a chip.
 *
 * The chip shows the server's state name in mono, a mark, and the written sentence. A
 * colour is never the only channel, and the sentence is what a support conversation can
 * quote.
 */
export function RecordingChip({ state }) {
  const key = String(state || '')
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-xs border px-2 py-0.5 font-mono
        text-xs font-medium ${RECORDING_TONES[key] || RECORDING_TONES.cancelled}`}
    >
      <Icon path={RECORDING_GLYPHS[key] || Glyphs.cancelled} size={13} />
      {key || 'unknown'}
      <span className="font-sans text-foreground/80">{RECORDING_WORDS[key] || ''}</span>
    </span>
  )
}

const CONSENT_WORDS = {
  not_required: 'Nobody was asked',
  pending: 'No answer yet',
  granted: 'Consent given',
  declined: 'Consent refused',
  joined_without_consent: 'Joined without consenting',
}

/**
 * The consent axis, as a chip.
 *
 * The wording is deliberately plain rather than formal. `not_required` says "Nobody was
 * asked" rather than "not required", because a seller reading it needs to know a person
 * was never asked - that is the whole difference between the two.
 */
export function ConsentChip({ state, enforced }) {
  const key = String(state || '')
  const unenforced = enforced === false && key === 'declined'
  const tone = unenforced
    ? 'border-warning/40 bg-warning/10 text-warning'
    : 'border-border-subtle bg-muted text-muted-foreground'
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-xs border px-2 py-0.5 font-mono
        text-xs font-medium ${tone}`}
    >
      <Icon path={key === 'granted' ? Glyphs.consent : Glyphs.pending} size={13} />
      {key || 'unknown'}
      <span className="font-sans text-foreground/80">{CONSENT_WORDS[key] || ''}</span>
      {unenforced && <span className="font-sans text-warning">enforcement is off</span>}
    </span>
  )
}

/**
 * The callout for a booking recorded after a consent refusal.
 *
 * This is the one state that needs more than a chip. With enforcement off, a decline is
 * stored as evidence and the recording proceeds, so the record shows a call that was
 * recorded after a participant said no. That is a consequence of an administrator's
 * configuration and it has to be stated in words on the row, not left for a reviewer to
 * work out from two chips side by side.
 */
export function RecordedAfterDeclineNote({ enforced }) {
  if (enforced !== false) return null
  return (
    <p className="flex items-start gap-2 rounded-sm border border-warning/40 bg-warning/10 p-2.5 text-xs text-foreground">
      <Icon path={Glyphs.warn} size={14} className="mt-0.5 shrink-0 text-warning" />
      <span>
        This call was recorded after a participant refused consent, because
        <strong className="font-semibold"> Enforce use of consent page </strong>
        is off on its profile. Turn enforcement on to gate the recording on an explicit
        consent decision.
      </span>
    </p>
  )
}

/**
 * A labelled key/value row.
 *
 * `mono` is for machine values and `prose` for sentences. The design system is explicit
 * that mono is for IDs, counts, timestamps and JSON, and this component is the place
 * that rule is easiest to break, so it takes the choice as a named prop rather than
 * letting each caller decide inline.
 */
export function Fact({ label, children, mono = true }) {
  return (
    <div className="flex min-w-0 gap-2 text-xs">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd
        className={`min-w-0 truncate ${mono ? 'font-mono text-foreground' : 'text-foreground'}`}
      >
        {children}
      </dd>
    </div>
  )
}

/**
 * A labelled switch, with its own visible label rather than a placeholder or a bare box.
 *
 * The shared set has no toggle, and a bare checkbox next to prose is exactly the pattern
 * that makes "which box was ticked" ambiguous. The control is 44px tall, so it meets the
 * touch-target floor.
 *
 * The hint is a sibling of the label rather than inside it, and that is deliberate. A
 * `<label>` that wraps both the switch name and its hint gives the input an accessible
 * name of "enforce_consent_page With this off the page is advisory", which is a sentence
 * rather than a name - so a screen reader announces the whole explanation on every pass
 * through the form, and an assistive-technology lookup for the switch by name misses.
 * The hint stays visible and is tied to the control with `aria-describedby`, which is
 * what that attribute is for.
 */
export function Switch({ label, hint, checked, onChange, name }) {
  const hintId = hint ? `${name}-hint` : undefined
  return (
    <div className="flex min-h-11 items-start gap-3 rounded-sm border border-border-subtle bg-surface px-3 py-2.5 hover:border-accent">
      <input
        id={name}
        name={name}
        type="checkbox"
        checked={Boolean(checked)}
        onChange={(event) => onChange(event.target.checked)}
        aria-describedby={hintId}
        className="mt-1 h-4 w-4 shrink-0 accent-[var(--color-accent)]"
      />
      <div className="min-w-0">
        <label htmlFor={name} className="block cursor-pointer text-[13px] font-medium text-foreground">
          {label}
        </label>
        {hint && (
          <p id={hintId} className="mt-0.5 text-xs text-muted-foreground">
            {hint}
          </p>
        )}
      </div>
    </div>
  )
}

/**
 * The inline message for a refused field.
 *
 * The backend returns a field-keyed `errors` map, so a message lands beside the input
 * that caused it rather than at the top of a long form. `aria-live` is set because the
 * message appears after a submit, and a screen-reader user needs to hear why nothing
 * happened.
 */
export function FieldError({ message }) {
  if (!message) return null
  return (
    <p role="alert" className="mt-1 text-xs text-destructive">
      {message}
    </p>
  )
}

/** The page's own section heading, so four sections read as four sections. */
export function SectionHeading({ children, hint }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <h2 className="font-display text-lg font-semibold text-foreground">{children}</h2>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}