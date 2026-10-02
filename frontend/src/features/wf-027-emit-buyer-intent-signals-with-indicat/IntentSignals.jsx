import { useMemo, useState } from 'react'

import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  JsonView,
  Spinner,
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'
import { absoluteTime, relativeTime } from '@/lib/api'

import { isChecked, newIdempotencyKey, signalApi, signalText } from './api'
import { Glyph, Notice } from './primitives'

/**
 * Buyer intent signals (WF-027).
 *
 * The flow this page drives is the researched one: register a signal type once
 * per integration, send each qualifying buyer interaction, and read the rendered
 * sentences the seller's Live Feed is built from.
 *
 * Four things the page is careful about, and the reason for each:
 *
 * * It never implies that a signal gives a seller something to do. The research is
 *   explicit that "users may choose to not take action on a signal" and that
 *   actionability is the end user's own Play configuration. The note sits under
 *   the feed permanently rather than being a one-time footnote, because the
 *   failure this guards against is a seller who believes an intent signal is a
 *   task and stops acting on the ones that are.
 * * It shows whether an indicator's claim was *checked* or accepted on trust. The
 *   research's own correction is that an indicator "should be very specific", and
 *   its example of a poor one is a key that states no bound - so a signal resting
 *   on such a key is flagged rather than presented as a verified claim.
 * * It renders the locale actually used, and says when that was a fallback. A
 *   seller reading English because the registration has no German is a fact worth
 *   showing, not a detail to hide.
 * * A dropped duplicate says so and shows the signal that won. "The first one
 *   wins" is a rule someone will hit, and a page that silently showed one signal
 *   would leave a sender wondering which of their two calls landed.
 */

const URGENCY_TONES = { high: 'delete', medium: 'update', low: 'neutral' }

function roomName(rooms, roomId) {
  return rooms.find((room) => room.id === roomId)?.data?.name || roomId
}

/* -------------------------------------------------------------------------- */
/* The composer                                                                */
/* -------------------------------------------------------------------------- */

function Composer({ room, registrations, onSent }) {
  const [form, setForm] = useState({
    type: '',
    observations: '{"time_in_seconds": 42}',
    urgency: 'medium',
    document_name: 'Security & Compliance Pack',
    action: 'viewed',
    buyer_first_name: 'Priya',
    room_name: '',
    person_id: 'per_000042',
    locale: 'en',
    broadcast: true,
  })
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  const declared = useMemo(() => {
    const found = registrations.find((row) => row.type === form.type)
    return found?.indicators || []
  }, [registrations, form.type])

  const set = (key) => (event) => {
    const value = event?.target?.type === 'checkbox' ? event.target.checked : event.target.value
    setForm((previous) => ({ ...previous, [key]: value }))
  }

  async function send(event) {
    event.preventDefault()
    setSaving(true)
    setError(null)
    setResult(null)
    try {
      let observations
      try {
        observations = JSON.parse(form.observations || '{}')
      } catch (caught) {
        // `cause` keeps the JSON parser's own message attached, so the refusal
        // names both what was wrong and why, rather than dropping the original.
        throw new Error(`observations must be a JSON object: ${caught.message}`, { cause: caught })
      }
      const body = {
        type: form.type,
        data: {
          document_name: form.document_name,
          action: form.action,
          buyer_first_name: form.buyer_first_name,
          room_name: form.room_name || roomName([], room?.id) || 'this room',
        },
        observations,
        urgency: form.urgency,
        occurred_at: new Date().toISOString(),
        idempotency_key: newIdempotencyKey(),
        attribution: { person_id: form.person_id },
        locale: form.locale,
        broadcast_notification: form.broadcast,
      }
      const outcome = await signalApi.interact(room.id, body)
      setResult(outcome)
      onSent()
    } catch (caught) {
      setError(caught)
    } finally {
      setSaving(false)
    }
  }

  if (!room) {
    return (
      <Card>
        <EmptyState
          title="Pick a room"
          description="Signals belong to a room: the buyer's action happened inside one, and that is what the receiving seller is resolved from."
        />
      </Card>
    )
  }

  if (!registrations.length) {
    return (
      <Card>
        <EmptyState
          title="No signal type is registered"
          description="A signal must follow the structure defined on its registration, so there is nothing to emit against until one is registered."
          action={
            <Button icon="plus" onClick={() => document.getElementById('wf027-register')?.focus()}>
              Register a signal type
            </Button>
          }
        />
      </Card>
    )
  }

  return (
    <Card>
      <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">Send a buyer interaction</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        The researched flow is &ldquo;on each qualifying DSR interaction, emit a live signal&rdquo;.
        This sends the interaction and its measurements, and the server reports which registered
        indicators it satisfies and why. An interaction that satisfies none is reported, not
        refused: a buyer who watched 40% of a video is a fact, not a mistake.
      </p>

      <form onSubmit={send} className="mt-4 space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Signal type" id="wf027-type" hint="The registered type to match against.">
            <select id="wf027-type" className={inputClass} value={form.type} onChange={set('type')} required>
              <option value="">Choose a registered type…</option>
              {registrations.map((row) => (
                <option key={row.id} value={row.type}>
                  {row.type} — {row.signal_name}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Urgency" id="wf027-urgency" hint="High, medium or low. It drives the feed's order and nothing else.">
            <select id="wf027-urgency" className={inputClass} value={form.urgency} onChange={set('urgency')}>
              <option value="high">high</option>
              <option value="medium">medium</option>
              <option value="low">low</option>
            </select>
          </Field>
        </div>

        <Field
          label="Measurements"
          id="wf027-observations"
          hint="The quantified evidence each indicator's claim is checked against. Sent verbatim as the interaction's observations."
        >
          <textarea
            id="wf027-observations"
            className={`${inputClass} min-h-24 font-mono`}
            value={form.observations}
            onChange={set('observations')}
            spellCheck={false}
          />
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Buyer's first name" id="wf027-buyer" hint="A description may refer to it.">
            <input id="wf027-buyer" className={inputClass} value={form.buyer_first_name} onChange={set('buyer_first_name')} />
          </Field>
          <Field label="Content" id="wf027-document" hint="What they were looking at.">
            <input id="wf027-document" className={inputClass} value={form.document_name} onChange={set('document_name')} required />
          </Field>
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Action" id="wf027-action">
            <select id="wf027-action" className={inputClass} value={form.action} onChange={set('action')}>
              <option value="viewed">viewed</option>
              <option value="downloaded">downloaded</option>
              <option value="opened">opened</option>
              <option value="watched">watched</option>
            </select>
          </Field>
          <Field label="Attribution: person_id" id="wf027-attribution" hint="Salesloft derives the receiving user from this.">
            <input id="wf027-attribution" className={inputClass} value={form.person_id} onChange={set('person_id')} required />
          </Field>
          <Field label="Locale" id="wf027-locale" hint="Falls back, and says so, if unregistered.">
            <input id="wf027-locale" className={inputClass} value={form.locale} onChange={set('locale')} />
          </Field>
        </div>

        <label className="flex min-h-11 items-center gap-3 text-sm" htmlFor="wf027-broadcast">
          <input
            id="wf027-broadcast"
            type="checkbox"
            checked={form.broadcast}
            onChange={set('broadcast')}
            className="h-5 w-5 shrink-0 cursor-pointer accent-[#22c55e]"
          />
          <span className="text-foreground">
            Show in the Live Feed
            <span className="ml-2 text-muted-foreground">
              — <span className="font-mono">broadcast_notification</span>. Off still stores the signal.
            </span>
          </span>
        </label>

        {declared.length > 0 && (
          <Notice tone="info" title="This registration declares" icon="signal">
            <ul className="mt-1 space-y-1">
              {declared.map((entry) => (
                <li key={entry.key}>
                  <span className="font-mono">{entry.key}</span>
                  {entry.description?.en ? ` — ${entry.description.en}` : ' (no description of its own)'}
                </li>
              ))}
            </ul>
          </Notice>
        )}

        <Button type="submit" variant="primary" icon="plus" disabled={saving || !form.type}>
          {saving ? 'Sending…' : 'Send interaction'}
        </Button>
      </form>

      {error && (
        <div className="mt-4">
          <ErrorNote error={error} />
        </div>
      )}

      {result && <InteractionResult result={result} />}
    </Card>
  )
}

function InteractionResult({ result }) {
  const reported = result.indicators || []
  if (!result.qualified) {
    return (
      <div className="mt-4 space-y-3">
        <Notice tone="warning" title="Nothing was emitted" icon="schema">
          {result.detail}
        </Notice>
        <Card>
          <p className="font-mono text-xs font-semibold tracking-wide text-muted-foreground uppercase">
            Every declared indicator, and why
          </p>
          <ul className="mt-2 space-y-2">
            {reported.map((row) => (
              <li key={row.key} className="rounded-lg border border-border-subtle/25 px-3 py-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-sm text-foreground">{row.key}</span>
                  <Badge tone={row.qualifies ? 'insert' : 'delete'}>{row.reason}</Badge>
                  {!row.checked && <Badge tone="restore">accepted on trust</Badge>}
                </div>
                <p className="mt-1 text-sm text-muted-foreground">{row.meaning}</p>
                {row.observation && (
                  <p className="mt-1 font-mono text-xs text-muted-foreground">
                    {row.observation.field} = {row.observation.value} against{' '}
                    {row.observation.comparison} {row.observation.threshold}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </Card>
      </div>
    )
  }

  const signal = result.signal
  return (
    <div className="mt-4 space-y-3">
      <Notice
        tone={result.outcome === 'dropped' ? 'warning' : 'success'}
        title={
          result.outcome === 'dropped'
            ? `Dropped as a duplicate — the first signal with this key won (attempt ${result.duplicate_attempts})`
            : 'Emitted'
        }
        icon={result.outcome === 'dropped' ? 'restore' : 'signal'}
      >
        {result.outcome === 'dropped' ? (
          <>
            {result.detail} The signal below is the one that was kept.
          </>
        ) : (
          <>{signalText(signal)}</>
        )}
      </Notice>
      <Card>
        <p className="font-mono text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          Indicators this interaction matched
        </p>
        <ul className="mt-2 space-y-2">
          {(signal?.indicators || []).map((entry) => (
            <li key={entry.key} className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-sm text-foreground">{entry.key}</span>
              <Badge tone={entry.qualification?.checked ? 'insert' : 'restore'}>
                {entry.qualification?.reason}
              </Badge>
            </li>
          ))}
        </ul>
        <details className="mt-3">
          <summary className="cursor-pointer text-sm text-muted-foreground">Every reported reason</summary>
          <div className="mt-2">
            <JsonView value={reported} />
          </div>
        </details>
      </Card>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* The Live Feed                                                               */
/* -------------------------------------------------------------------------- */

function FeedRow({ row }) {
  const [open, setOpen] = useState(false)
  const localeNote = row.rendered?.locale_fallback
  const unqualified = (row.qualification || []).filter((entry) => entry && !isChecked(entry))

  return (
    <li className="rounded-xl border border-border-subtle/25">
      <div className="flex flex-wrap items-start justify-between gap-3 p-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-lg bg-muted p-2 text-accent">
              <Glyph name="signal" size={16} />
            </span>
            <Badge tone={URGENCY_TONES[row.urgency] || 'neutral'}>{row.urgency} urgency</Badge>
            <span className="font-mono text-xs text-muted-foreground">{row.type}</span>
            {row.receiver?.seller ? (
              <span className="text-xs text-muted-foreground">to {row.receiver.seller}</span>
            ) : (
              <Badge tone="delete">no receiving seller</Badge>
            )}
            {row.duplicate_attempts > 0 && (
              <Badge tone="restore">{row.duplicate_attempts} duplicate dropped</Badge>
            )}
          </div>
          <p className="mt-2 text-sm text-foreground">{signalText(row)}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {absoluteTime(row.occurred_at)} · {relativeTime(row.occurred_at)}
            {localeNote && ` · rendered in ${row.rendered.locale_resolved}, not ${row.rendered.locale_requested}`}
          </p>
          {unqualified.length > 0 && (
            <p className="mt-1 text-xs text-amber-200">
              {unqualified.length} indicator{unqualified.length === 1 ? '' : 's'} on this signal stated
              no bound, so {unqualified.length === 1 ? 'it' : 'they'} qualified on trust rather than
              being checked.
            </p>
          )}
        </div>
        <Button icon={open ? 'close' : 'chevron'} onClick={() => setOpen((value) => !value)}>
          {open ? 'Hide detail' : 'Show detail'}
        </Button>
      </div>
      {open && (
        <div className="border-t border-border-subtle/25 p-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <div>
              <p className="font-mono text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                Data sent
              </p>
              <JsonView value={row.data} />
            </div>
            <div>
              <p className="font-mono text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                Attribution and receiver
              </p>
              <JsonView value={{ attribution: row.attribution, receiver: row.receiver }} />
            </div>
          </div>
          {(row.warnings || []).length > 0 && (
            <div className="mt-3">
              <Notice tone="warning" title="Warnings on this signal" icon="schema">
                <ul className="list-disc pl-5">
                  {row.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </Notice>
            </div>
          )}
          <p className="mt-3 text-xs text-muted-foreground">{row.actionability_note}</p>
        </div>
      )}
    </li>
  )
}

function LiveFeed({ feed, loading, error, onRetry, onToggleHidden }) {
  const [showHidden, setShowHidden] = useState(false)

  if (loading) return <Spinner label="Loading the Live Feed" />
  if (error) return <ErrorNote error={error} onRetry={onRetry} />

  const entries = feed?.entries || []
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">Live Feed</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Highest urgency first, then newest. Only signals whose{' '}
            <span className="font-mono">broadcast_notification</span> is set appear here; the rest
            are still stored and still delivered.
          </p>
        </div>
        <Button
          icon={showHidden ? 'close' : 'search'}
          onClick={() => {
            setShowHidden((value) => !value)
            onToggleHidden()
          }}
        >
          {showHidden ? 'Hide withheld' : `Show ${feed?.withheld || 0} withheld`}
        </Button>
      </div>

      {entries.length === 0 ? (
        <EmptyState
          title="No signals in this feed"
          description="Nothing qualifying has been emitted into this room, or everything emitted has broadcast_notification off."
        />
      ) : (
        <ul className="space-y-3">
          {entries.map((row) => (
            <FeedRow key={row.id || row.occurred_at} row={row} />
          ))}
        </ul>
      )}

      <p className="text-xs text-muted-foreground">{feed?.actionability_note}</p>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* The registry                                                               */
/* -------------------------------------------------------------------------- */

function RegistrationRow({ registration, vocabulary, onAmend, onWithdraw, busy }) {
  const [open, setOpen] = useState(false)
  const [locale, setLocale] = useState({})
  const warnings = (registration.warnings || []).filter((entry) => entry.severity === 'warning')

  return (
    <li className="rounded-xl border border-border-subtle/25">
      <div className="flex flex-wrap items-start justify-between gap-3 p-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-sm font-semibold text-foreground">{registration.type}</span>
            <Badge tone="neutral">{registration.integration_id}</Badge>
            {warnings.length > 0 && <Badge tone="delete">{warnings.length} flagged</Badge>}
            <span className="text-xs text-muted-foreground">
              {registration.locales?.join(', ')} · {registration.indicator_keys?.length} indicator
              {registration.indicator_keys?.length === 1 ? '' : 's'}
            </span>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">{registration.signal_name}</p>
          {warnings.map((warning) => (
            <p key={warning.code} className="mt-2 text-xs text-amber-200">
              {warning.indicator ? `${warning.indicator}: ` : ''}
              {warning.detail}
            </p>
          ))}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button icon={open ? 'close' : 'plus'} onClick={() => setOpen((value) => !value)}>
            {open ? 'Close' : 'Add a locale'}
          </Button>
          <Button
            icon="trash"
            variant="danger"
            disabled={busy}
            onClick={() => onWithdraw(registration.id)}
          >
            Withdraw
          </Button>
        </div>
      </div>

      {open && (
        <form
          className="space-y-3 border-t border-border-subtle/25 p-4"
          onSubmit={(event) => {
            event.preventDefault()
            onAmend(registration.id, { description: locale })
            setLocale({})
            setOpen(false)
          }}
        >
          <Notice tone="info" title="A registration is a contract" icon="audit">
            A registered signal type &ldquo;should be considered an immutable API contract&rsquo; and
            only additive changes will be allowed.&rdquo; A new locale is additive and is applied.
            Rewriting one that exists, removing an indicator, or adding a required field is refused
            with every offending path named.
          </Notice>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Locale" id={`wf027-locale-${registration.id}`} hint="For example fr or en-GB.">
              <input
                id={`wf027-locale-${registration.id}`}
                className={inputClass}
                value={locale.key || ''}
                onChange={(event) => setLocale((p) => ({ ...p, key: event.target.value }))}
                placeholder="fr"
              />
            </Field>
            <Field
              label="Message"
              id={`wf027-message-${registration.id}`}
              hint="An ICU Message. {name} and {n, plural, =1 {# time} other {# times}} are supported."
            >
              <input
                id={`wf027-message-${registration.id}`}
                className={inputClass}
                value={locale.message || ''}
                onChange={(event) => setLocale((p) => ({ ...p, message: event.target.value }))}
                placeholder="{buyer_first_name} a passe {time_in_seconds} secondes sur {document_name}."
              />
            </Field>
          </div>
          <Button type="submit" variant="primary" icon="plus" disabled={!locale.key || !locale.message}>
            Add locale
          </Button>
          <details className="mt-2">
            <summary className="cursor-pointer text-sm text-muted-foreground">
              Declared shape and indicators
            </summary>
            <div className="mt-2">
              <JsonView
                value={{
                  data_shape: registration.data_shape,
                  indicators: registration.indicators,
                  attribution: registration.attribution,
                  claim_form: vocabulary?.claim_form,
                }}
              />
            </div>
          </details>
        </form>
      )}
    </li>
  )
}

function Registry({ registrations, flagged, vocabulary, onRegister, onAmend, onWithdraw, busy }) {
  const [form, setForm] = useState({
    type: '',
    signal_name: '',
    integration_id: 'dsr',
    description: '{"en": "{buyer_first_name} spent {time_in_seconds} seconds on {document_name}."}',
    data_shape: '{"type": "object", "properties": {"document_name": {"type": "string"}, "action": {"type": "string"}}, "required": ["document_name", "action"]}',
    indicators:
      '{"spent_more_than_30s_on_site": {"type": "integer", "minimum": 0}}',
    attribution: 'person_id, account_id, user_guid',
    broadcast_notification: true,
  })
  const [open, setOpen] = useState(false)

  const set = (key) => (event) => {
    const value = event?.target?.type === 'checkbox' ? event.target.checked : event.target.value
    setForm((previous) => ({ ...previous, [key]: value }))
  }

  async function register(event) {
    event.preventDefault()
    const dataShape = JSON.parse(form.data_shape || '{}')
    // Indicators are typed as one JSON object per key here rather than as the
    // researched array, because a form field holding a JSON array of objects with
    // nested schemas is unreadable and unmaintainable. The server still receives
    // the researched shape.
    const declared = JSON.parse(form.indicators || '{}')
    const indicators = Object.entries(declared).map(([key, shape]) => ({
      key,
      metadata_shape: { type: 'object', properties: { [key]: shape }, required: [key] },
      description: { en: `${key.replace(/_/g, ' ')}.` },
    }))
    await onRegister({
      type: form.type,
      signal_name: form.signal_name,
      integration_id: form.integration_id,
      description: JSON.parse(form.description || '{}'),
      data_shape: dataShape,
      indicators,
      attribution: form.attribution.split(',').map((value) => value.trim()).filter(Boolean),
      broadcast_notification: form.broadcast_notification,
      idempotency_key: newIdempotencyKey(),
    })
    setOpen(false)
  }

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">Signal registry</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            A signal type is registered once per integration. {registrations.length} registered
            {flagged ? `, ${flagged} with an indicator the specificity check objects to` : ''}.
          </p>
        </div>
        <Button icon={open ? 'close' : 'plus'} onClick={() => setOpen((value) => !value)}>
          {open ? 'Cancel' : 'Register a signal type'}
        </Button>
      </div>

      {open && (
        <form onSubmit={register} className="mt-4 space-y-4 border-t border-border-subtle/25 pt-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Type" id="wf027-reg-type" hint="Machine-readable, and permanent.">
              <input id="wf027-reg-type" className={inputClass} value={form.type} onChange={set('type')} required />
            </Field>
            <Field label="Signal name" id="wf027-reg-name" hint="What a seller sees.">
              <input
                id="wf027-reg-name"
                className={inputClass}
                value={form.signal_name}
                onChange={set('signal_name')}
                required
              />
            </Field>
          </div>
          <Field label="Integration" id="wf027-reg-integration" hint="The type may be registered once per integration.">
            <input
              id="wf027-reg-integration"
              className={inputClass}
              value={form.integration_id}
              onChange={set('integration_id')}
              required
            />
          </Field>
          <Field label="Localized description" id="wf027-reg-description" hint="A JSON object of locale to ICU Message.">
            <textarea
              id="wf027-reg-description"
              className={`${inputClass} min-h-20 font-mono`}
              value={form.description}
              onChange={set('description')}
              spellCheck={false}
              required
            />
          </Field>
          <Field label="Data shape" id="wf027-reg-shape" hint="JSON Schema. Signals are checked against it.">
            <textarea
              id="wf027-reg-shape"
              className={`${inputClass} min-h-20 font-mono`}
              value={form.data_shape}
              onChange={set('data_shape')}
              spellCheck={false}
              required
            />
          </Field>
          <Field
            label="Indicators"
            id="wf027-reg-indicators"
            hint={`One JSON object per indicator key, naming its measurement. The key states the claim: ${vocabulary?.claim_form || '<subject> <comparative> <number><unit?>'} is what the server can check, and "Indicators should be very specific" is why a key with no bound is flagged.`}
          >
            <textarea
              id="wf027-reg-indicators"
              className={`${inputClass} min-h-20 font-mono`}
              value={form.indicators}
              onChange={set('indicators')}
              spellCheck={false}
              required
            />
          </Field>
          <Field label="Attribution" id="wf027-reg-attribution" hint="Comma-separated. Person, Account, User, Opportunity, Email Content.">
            <input id="wf027-reg-attribution" className={inputClass} value={form.attribution} onChange={set('attribution')} required />
          </Field>
          <label className="flex min-h-11 items-center gap-3 text-sm" htmlFor="wf027-reg-broadcast">
            <input
              id="wf027-reg-broadcast"
              type="checkbox"
              checked={form.broadcast_notification}
              onChange={set('broadcast_notification')}
              className="h-5 w-5 shrink-0 cursor-pointer accent-[#22c55e]"
            />
            <span className="text-foreground">Default signals to the Live Feed</span>
          </label>
          <Button type="submit" variant="primary" icon="plus" disabled={busy}>
            Register
          </Button>
        </form>
      )}

      {registrations.length === 0 ? (
        <div className="mt-4">
          <EmptyState
            title="Nothing registered"
            description="Register a signal type to start emitting. It may be registered once per integration, and only amended additively afterwards."
          />
        </div>
      ) : (
        <ul className="mt-4 space-y-3">
          {registrations.map((registration) => (
            <RegistrationRow
              key={registration.id}
              registration={registration}
              vocabulary={vocabulary}
              onAmend={onAmend}
              onWithdraw={onWithdraw}
              busy={busy}
            />
          ))}
        </ul>
      )}
    </Card>
  )
}

/* -------------------------------------------------------------------------- */
/* The inference register                                                      */
/* -------------------------------------------------------------------------- */

function Inferences({ register }) {
  const [open, setOpen] = useState(false)
  if (!register) return null
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">
            What this workflow infers
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            {register.count} decisions the researched document does not settle, each named with what
            the research does and does not say, and how to change it. Served at{' '}
            <span className="font-mono">/wf-027/inferences</span> beside the sourced vocabulary.
          </p>
        </div>
        <Button icon={open ? 'close' : 'audit'} onClick={() => setOpen((value) => !value)}>
          {open ? 'Hide' : 'Read them'}
        </Button>
      </div>
      {open && (
        <ul className="mt-4 space-y-3">
          {register.inferences.map((entry) => (
            <li key={entry.id} className="rounded-lg border border-border-subtle/25 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm font-semibold text-foreground">{entry.id}</span>
                <span className="text-sm text-muted-foreground">{entry.topic}</span>
              </div>
              <p className="mt-1.5 text-sm text-muted-foreground">{entry.basis}</p>
              <p className="mt-1.5 text-sm text-foreground">
                <span className="font-semibold">Why: </span>
                {entry.why}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                <span className="font-mono">change_it: </span>
                {entry.change_it}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

/* -------------------------------------------------------------------------- */
/* The page                                                                    */
/* -------------------------------------------------------------------------- */

export default function IntentSignalsPage() {
  const [roomId, setRoomId] = useState('')
  const [busy, setBusy] = useState(false)
  const [showWithheld, setShowWithheld] = useState(false)
  const [actionError, setActionError] = useState(null)
  const [notice, setNotice] = useState(null)

  const rooms = useAsync(() => signalApi.rooms(), [])
  const vocabulary = useAsync(() => signalApi.vocabulary(), [])
  const inferences = useAsync(() => signalApi.inferences(), [])
  const registry = useAsync(() => signalApi.registrations(), [])

  const summary = useAsync(
    () => (roomId ? signalApi.summary(roomId) : Promise.resolve(null)),
    [roomId],
  )
  const feed = useAsync(
    () => (roomId ? signalApi.liveFeed(roomId) : Promise.resolve(null)),
    [roomId, showWithheld],
  )
  const withheld = useAsync(
    () =>
      roomId
        ? signalApi.signals(roomId, { broadcast: false, limit: 200 })
        : Promise.resolve(null),
    [roomId],
  )

  const feedWithWithheld = useMemo(() => {
    if (!showWithheld) return feed.data
    const extra = (withheld.data?.signals || []).map((row) => ({ ...row, withheld: true }))
    const entries = [...(feed.data?.entries || []), ...extra]
    return feed.data ? { ...feed.data, entries, count: entries.length } : null
  }, [showWithheld, feed.data, withheld.data])

  const listRooms = rooms.data?.records || []
  const selectedRoomId = roomId || listRooms[0]?.id || ''

  async function run(action, describe) {
    setBusy(true)
    setActionError(null)
    setNotice(null)
    try {
      const result = await action()
      registry.refetch()
      summary.refetch()
      feed.refetch()
      withheld.refetch()
      setNotice({ tone: 'success', text: describe(result) })
    } catch (caught) {
      setActionError(caught)
    } finally {
      setBusy(false)
    }
  }

  if (rooms.loading || vocabulary.loading) {
    return <Spinner label="Loading intent signals" />
  }
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />

  const registrations = registry.data?.registrations || []
  const counts = summary.data

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="flex items-center gap-3 font-mono text-xl font-semibold text-foreground">
            <span className="rounded-lg bg-muted p-2 text-accent">
              <Glyph name="signal" size={20} />
            </span>
            Intent signals
          </h1>
          <p className="mt-2 max-w-3xl text-sm text-muted-foreground">
            A signal is &ldquo;a buyer-focused action or event that happened in the partner&rsquo;s or
            customer&rsquo;s platform&rdquo; and &ldquo;should have high value and should drive a seller
            to act&rdquo;. Register the type once per integration, send each qualifying interaction,
            and read the rendered description the seller&rsquo;s Live Feed is built from.
          </p>
        </div>
        <div className="w-full max-w-xs">
          <Field label="Room" id="wf027-room" hint="Signals are scoped to a room.">
            <select
              id="wf027-room"
              className={inputClass}
              value={selectedRoomId}
              onChange={(event) => setRoomId(event.target.value)}
            >
              {listRooms.map((record) => (
                <option key={record.id} value={record.id}>
                  {record.data?.name || record.id}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </header>

      {rooms.error || registry.error ? (
        <ErrorNote error={rooms.error || registry.error} onRetry={registry.refetch} />
      ) : null}

      {actionError && <ErrorNote error={actionError} />}
      {notice && (
        <Notice tone={notice.tone} title={notice.text} icon="signal" onDismiss={() => setNotice(null)} />
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Signals in room"
          value={counts ? counts.signals : '—'}
          hint={counts ? `${counts.duplicates_dropped} duplicate dropped` : 'Pick a room'}
          icon="signal"
        />
        <StatCard
          label="High urgency"
          value={counts ? counts.by_urgency.high : '—'}
          hint="Outranks everything in the feed"
        />
        <StatCard
          label="In the Live Feed"
          value={counts ? counts.live_feed : '—'}
          hint={counts ? `${counts.withheld} withheld from display` : undefined}
        />
        <StatCard
          label="Registrations"
          value={registry.data?.count ?? 0}
          hint={registry.data?.flagged ? `${registry.data.flagged} flagged` : 'none flagged'}
          icon="schema"
        />
      </div>

      <Composer
        room={selectedRoomId ? { id: selectedRoomId } : null}
        registrations={registrations}
        onSent={() => {
          summary.refetch()
          feed.refetch()
          withheld.refetch()
        }}
      />

      <LiveFeed
        feed={feedWithWithheld}
        loading={feed.loading}
        error={feed.error}
        onRetry={feed.refetch}
        onToggleHidden={() => setShowWithheld((value) => !value)}
      />

      <Registry
        registrations={registrations}
        flagged={registry.data?.flagged || 0}
        vocabulary={vocabulary.data}
        busy={busy}
        onRegister={(payload) =>
          run(
            () => signalApi.register(payload),
            (result) =>
              result.outcome === 'already_registered'
                ? 'That idempotency key was already registered, so the first registration stands.'
                : `Registered ${result.registration.type}.`,
          )
        }
        onAmend={(id, patch) =>
          run(
            () => signalApi.amend(id, patch),
            (result) => `Added a locale. ${result.type} now declares ${result.locales.join(', ')}.`,
          )
        }
        onWithdraw={(id) =>
          run(
            () => signalApi.withdraw(id),
            (result) => `Withdrew ${result.type}. A registration with signals already under it cannot be withdrawn.`,
          )
        }
      />

      <Inferences register={inferences.data} />
    </div>
  )
}
