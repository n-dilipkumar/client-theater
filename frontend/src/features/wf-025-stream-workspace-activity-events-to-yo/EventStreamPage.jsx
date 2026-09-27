import { useCallback, useMemo, useState } from 'react'
import { apiRequest, absoluteTime, relativeTime } from '@/lib/api'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Icon,
  JsonView,
  Spinner,
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'

/**
 * WF-025: stream workspace activity events to your own systems.
 *
 * The page follows the researched flow rather than the data model, because the
 * flow is what an operator actually does:
 *
 *  1. **Settings -> Webhooks**: the list of endpoints, each with a masked secret
 *     hint, its counters, and whether its URL was verified with a POST.
 *  2. **Create Webhook**: name plus HTTPS target. The form says the target is
 *     verified before the webhook exists, because it is - and an endpoint that
 *     would not answer leaves nothing behind to clean up.
 *  3. **View Key**: read the generated secret again, from the same row.
 *  4. **Subscriptions**: pick types from the list the server publishes, and
 *     optionally a filter.
 *  5. **Pause / unsubscribe**, and **Send test events**.
 *  6. **Deliveries**: what went out, what the subscriber answered, and where the
 *     retry ladder says to go next.
 *
 * Two researched rules are visible in the UI rather than only in the code,
 * because they change what a reader should expect to see:
 *
 *  - **anonymous activity omits `user`**, so an event row with no user is a
 *    deal-room visitor, not a missing field;
 *  - a delivery is **skipped with a reason** when a subscription is paused or a
 *    filter excluded the event, and that is never shown as a failure.
 *
 * A note on the shared primitives
 * ------------------------------
 * `components/ui.jsx` has no `Notice`, `Modal`, `Toggle` or `Checkbox` - the
 * feature contract lists them, but they are not in the file. Rather than edit a
 * shared file a hundred features would collide on, the two things this page
 * needs are built here: `Banner` for the one-line outcome of an action, and the
 * create form is inline rather than in a dialog. Both are reported in the PR
 * description as candidates for promotion into `ui.jsx`, which is platform work.
 *
 * Two design-system items this page cannot meet without that same shared-file
 * edit, reported rather than worked around:
 *
 *  - the shared `Button` carries no `cursor-pointer` and no focus ring, and the
 *    design system lists both as requirements. Every control here meets the 44px
 *    floor (`min-h-11` is on the shared class), and the checkbox row adds
 *    `cursor-pointer` itself, because adding it to the shared `Button` would make
 *    this page the only one that looks different.
 *  - there is no motion to respect. This page animates nothing, has no scroll
 *    reveal, and does not poll, so `prefers-reduced-motion` is satisfied by there
 *    being nothing to reduce. That is deliberate: an operations page that ticked
 *    every second would need the reduced-motion branch, and a page that does not
 *    tick does not.
 */

const PREFIX = '/wf-025'

/** Delivery states, and the tone each is shown in. */
const STATE_TONES = {
  delivered: 'insert',
  tested: 'insert',
  retrying: 'update',
  skipped: 'neutral',
  failed: 'delete',
}

const SKIP_REASONS = {
  webhook_paused: 'the webhook was paused',
  subscription_paused: 'the subscription was paused',
  filter_excluded: 'the subscription filter excluded it',
}

const BANNER_TONES = {
  ok: 'border-accent/40 bg-accent/10 text-foreground',
  bad: 'border-destructive/40 bg-destructive/10 text-foreground',
}

function tone(state) {
  return STATE_TONES[state] || 'neutral'
}

function stateLabel(delivery) {
  if (delivery.state === 'skipped' && delivery.reason) {
    return `skipped - ${SKIP_REASONS[delivery.reason] || delivery.reason}`
  }
  return delivery.state
}

function Banner({ notice }) {
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

async function get(path, params) {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== '') query.set(key, value)
  }
  const suffix = query.toString() ? `?${query}` : ''
  return apiRequest(`${PREFIX}${path}${suffix}`)
}

async function send(path, method, body, params) {
  const query = new URLSearchParams(params || {}).toString()
  return apiRequest(`${PREFIX}${path}${query ? `?${query}` : ''}`, {
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

const post = (path, body, params) => send(path, 'POST', body ?? {}, params)
const patch = (path, body, params) => send(path, 'PATCH', body ?? {}, params)
const del = (path, params) => send(path, 'DELETE', undefined, params)

/* -------------------------------------------------------------------------
 * Sections
 * ---------------------------------------------------------------------- */

function Summary({ summary }) {
  const states = Object.entries(summary.delivery_states || {})
    .map(([state, count]) => `${count} ${state}`)
    .join(', ')
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard
        label="Webhooks live"
        value={summary.webhooks}
        hint={`${summary.webhooks_paused} paused`}
        icon="database"
      />
      <StatCard
        label="Subscriptions live"
        value={summary.subscriptions}
        hint={`${summary.subscriptions_paused} paused`}
        icon="schema"
      />
      <StatCard
        label="Events recorded"
        value={summary.events}
        hint={`${summary.events_anonymous} anonymous`}
        icon="audit"
      />
      <StatCard
        label="Deliveries"
        value={summary.deliveries}
        hint={states || 'none yet'}
        icon="refresh"
      />
    </div>
  )
}

/** One row of the researched rules, kept visible rather than in a comment. */
function Rules({ open, onToggle }) {
  return (
    <div>
      <Button icon="schema" onClick={onToggle}>
        {open ? 'Hide' : 'Show'} what the research fixes
      </Button>
      {open && (
        <Card className="mt-3">
          <h2 className="font-mono text-sm font-semibold">What the research fixes</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Two of these change what you should expect to see rather than only how the code is shaped:
            anonymous activity omits <span className="font-mono">user</span>, and a paused subscription or
            an excluding filter produces a <em>skipped</em> row rather than nothing.
          </p>
          <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
            {[
              [
                'Targets',
                'HTTPS only, verified with a POST before the webhook exists. Only an account admin may create one.',
              ],
              [
                'Signing',
                'HMAC-SHA256 in X-DSR-Signature, with X-DSR-Signature-Old while a rotation overlaps.',
              ],
              [
                'Retries',
                '26 rungs: one minute, then ten, then hourly. A 10-second timeout on every attempt.',
              ],
              [
                'Presigned uploads',
                'A file_upload response expires one hour after it was signed, and the event says so when read back.',
              ],
              [
                'Anonymous activity',
                'Omits user entirely, so a view with no user is a deal-room visitor.',
              ],
              [
                'Presentation events',
                'Share-link activity only. Asset link activity is asset.viewed, asset.shared, asset.downloaded.',
              ],
            ].map(([term, description]) => (
              <div key={term}>
                <dt className="font-medium text-foreground">{term}</dt>
                <dd className="text-muted-foreground">{description}</dd>
              </div>
            ))}
          </dl>
        </Card>
      )}
    </div>
  )
}

function Webhooks({ webhooks, onChanged, notice, setNotice }) {
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [targetUrl, setTargetUrl] = useState('')
  const [busy, setBusy] = useState(false)

  const run = useCallback(
    async (label, action) => {
      setBusy(true)
      setNotice(null)
      try {
        await action()
        onChanged()
      } catch (error) {
        setNotice({ tone: 'bad', text: `${label} failed: ${String(error.message || error)}` })
      } finally {
        setBusy(false)
      }
    },
    [onChanged, setNotice],
  )

  const create = () =>
    run('Create webhook', async () => {
      // `role=admin` is the researched requirement, and it is sent rather than
      // assumed: a permission rule that cannot fail is not a rule.
      const created = await post('/webhooks', { name, targetUrl }, { role: 'admin', actor: 'operator' })
      setNotice({
        tone: 'ok',
        text: `Created ${created.webhook.name} and verified it with a POST. Copy the signing secret now.`,
        detail: created.secret,
      })
      setName('')
      setTargetUrl('')
      setCreating(false)
    })

  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-mono text-sm font-semibold">Webhooks</h2>
          <p className="text-xs text-muted-foreground">
            Account level, under Settings andrarr; Data Management. The target URL is verified with a POST
            before the webhook is created.
          </p>
        </div>
        <Button variant="primary" icon="plus" onClick={() => setCreating((open) => !open)}>
          {creating ? 'Cancel' : 'Create webhook'}
        </Button>
      </div>

      {creating && (
        <form
          className="mt-4 grid gap-3 rounded-lg border border-border-subtle/40 p-4 md:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault()
            create()
          }}
        >
          <Field label="Name" id="webhook-name" hint="How you will recognise it in this list.">
            <input
              id="webhook-name"
              className={inputClass}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Northwind to warehouse"
              required
            />
          </Field>
          <Field label="HTTPS target URL" id="webhook-url" hint="Verified with a POST. http is refused.">
            <input
              id="webhook-url"
              className={inputClass}
              value={targetUrl}
              onChange={(event) => setTargetUrl(event.target.value)}
              placeholder="https://hooks.example/dsr"
              required
            />
          </Field>
          <div className="md:col-span-2">
            <Button type="submit" variant="primary" icon="plus" disabled={busy}>
              Create and verify
            </Button>
          </div>
        </form>
      )}

      <Banner notice={notice} />

      {webhooks.length === 0 && !creating && (
        <div className="mt-4">
          <EmptyState
            title="No webhooks yet"
            description="Create one to give a warehouse, a Slack channel, or a CRM somewhere to send activity events."
          />
        </div>
      )}

      <ul className="mt-4 flex flex-col gap-3">
        {webhooks.map((webhook) => (
          <li key={webhook.id} className="rounded-lg border border-border-subtle/40 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-mono text-sm font-semibold">{webhook.name}</p>
                <p className="truncate text-xs text-muted-foreground">{webhook.target_url}</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={webhook.active ? 'insert' : 'neutral'}>
                  {webhook.active ? 'active' : 'paused'}
                </Badge>
                <Badge tone={webhook.failures > 0 ? 'delete' : 'neutral'}>
                  {webhook.failures} failed
                </Badge>
                {webhook.rotation_in_progress && <Badge tone="update">key rotating</Badge>}
                <Badge tone={webhook.verified_at ? 'neutral' : 'delete'}>
                  {webhook.verified_at ? `verified ${relativeTime(webhook.verified_at)}` : 'not verified'}
                </Badge>
              </div>
            </div>

            <dl className="mt-3 grid gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
              {[
                ['Subscriptions', webhook.subscription_count ?? webhook.subscriptions],
                ['Deliveries', webhook.deliveries],
                ['Last state', webhook.last_state || '-'],
                ['Signing secret', webhook.secret_hint || 'none'],
              ].map(([term, value]) => (
                <div key={term}>
                  <dt className="text-muted-foreground">{term}</dt>
                  <dd className="font-mono text-foreground">{value}</dd>
                </div>
              ))}
            </dl>

            <div className="mt-3 flex flex-wrap gap-2">
              <Button
                icon="audit"
                disabled={busy}
                onClick={() =>
                  run('View key', async () => {
                    const revealed = await post(`/webhooks/${webhook.id}/key`)
                    setNotice({ tone: 'ok', text: `Signing secret for ${webhook.name}:`, detail: revealed.secret })
                  })
                }
              >
                View key
              </Button>
              <Button
                icon="refresh"
                disabled={busy}
                onClick={() =>
                  run('Rotate key', async () => {
                    const rotated = await post(`/webhooks/${webhook.id}/key/rotate`, {}, { actor: 'operator' })
                    setNotice({
                      tone: 'ok',
                      text:
                        'Secret rotated. Until the first delivery succeeds under it, deliveries also carry the previous secret so subscribers can still verify.',
                      detail: rotated.secret,
                    })
                  })
                }
              >
                Rotate key
              </Button>
              <Button
                icon="schema"
                disabled={busy}
                onClick={() =>
                  run('Send test events', async () => {
                    const sent = await post(`/webhooks/${webhook.id}/test-events`, {}, { actor: 'operator' })
                    setNotice({
                      tone: 'ok',
                      text: `Sent ${sent.count} test event(s): ${sent.events
                        .map((entry) => `${entry.event} (${entry.state})`)
                        .join(', ')}.`,
                    })
                  })
                }
              >
                Send test events
              </Button>
              <Button
                disabled={busy}
                onClick={() =>
                  run('Pause', async () => {
                    await patch(`/webhooks/${webhook.id}`, { active: !webhook.active }, { actor: 'operator' })
                  })
                }
              >
                {webhook.active ? 'Pause' : 'Resume'}
              </Button>
              <Button
                variant="danger"
                icon="trash"
                disabled={busy}
                onClick={() => run('Retire', async () => del(`/webhooks/${webhook.id}`, { actor: 'operator' }))}
              >
                Retire
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function Subscriptions({ subscriptions, webhooks, vocabulary, onChanged, notice, setNotice }) {
  const [webhookId, setWebhookId] = useState('')
  const [types, setTypes] = useState([])
  const [filter, setFilter] = useState('')
  const [busy, setBusy] = useState(false)

  const published = useMemo(() => vocabulary?.event_types || [], [vocabulary])

  const run = useCallback(
    async (label, action) => {
      setBusy(true)
      setNotice(null)
      try {
        await action()
        onChanged()
      } catch (error) {
        setNotice({ tone: 'bad', text: `${label} failed: ${String(error.message || error)}` })
      } finally {
        setBusy(false)
      }
    },
    [onChanged, setNotice],
  )

  const toggle = (type) => {
    setTypes((current) => (current.includes(type) ? current.filter((t) => t !== type) : [...current, type]))
  }

  const create = () =>
    run('Subscribe', async () => {
      await post(
        `/webhooks/${webhookId}/subscriptions`,
        { types, filter: filter || null },
        { actor: 'operator' },
      )
      setTypes([])
      setFilter('')
    })

  return (
    <Card>
      <h2 className="font-mono text-sm font-semibold">Subscriptions</h2>
      <p className="text-xs text-muted-foreground">
        A subscription can be viewed in detail, paused, or unsubscribed. With no filter it receives
        everything it subscribed to, which is the researched behaviour for a vendor with no server-side
        filtering.
      </p>

      <form
        className="mt-4 grid gap-3 rounded-lg border border-border-subtle/40 p-4"
        onSubmit={(event) => {
          event.preventDefault()
          create()
        }}
      >
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Webhook" id="subscription-webhook">
            <select
              id="subscription-webhook"
              className={inputClass}
              value={webhookId}
              onChange={(event) => setWebhookId(event.target.value)}
              required
            >
              <option value="">Choose a webhook</option>
              {webhooks.map((webhook) => (
                <option key={webhook.id} value={webhook.id}>
                  {webhook.name}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label="Filter (optional)"
            id="subscription-filter"
            hint="A JSONPath subset. A filter that does not parse is refused here, not ignored."
          >
            <input
              id="subscription-filter"
              className={inputClass}
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="$.associatedObjects.account[?(@.id == 'acc_1')]"
            />
          </Field>
        </div>

        <fieldset>
          <legend className="text-xs font-medium text-muted-foreground">
            Subscription types ({published.length} published)
          </legend>
          <div className="mt-2 flex flex-wrap gap-2">
            {published.map((type) => (
              <label
                key={type}
                className="inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border border-border-subtle/40 px-3 text-xs hover:bg-muted"
              >
                <input
                  type="checkbox"
                  className="h-4 w-4"
                  checked={types.includes(type)}
                  onChange={() => toggle(type)}
                />
                <span className="font-mono">{type}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <div>
          <Button type="submit" variant="primary" icon="plus" disabled={busy || !webhookId || types.length === 0}>
            Subscribe
          </Button>
        </div>
      </form>

      <Banner notice={notice} />

      <ul className="mt-4 flex flex-col gap-2">
        {subscriptions.map((subscription) => (
          <li
            key={subscription.id}
            className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-border-subtle/40 p-3"
          >
            <div className="min-w-0">
              <p className="font-mono text-xs font-semibold">{subscription.webhook_name}</p>
              <p className="font-mono text-xs break-all text-muted-foreground">
                {subscription.types.join(', ')}
              </p>
              {subscription.filter && (
                <p className="font-mono text-xs break-all text-accent">filter: {subscription.filter}</p>
              )}
              <p className="text-xs text-muted-foreground">
                {subscription.room_id ? `room ${subscription.room_id}` : 'every room'} &middot;{' '}
                {subscription.deliveries} delivered &middot; {subscription.deliveries_skipped} skipped
                {subscription.deliveries_filtered_out > 0
                  && ` \u00b7 ${subscription.deliveries_filtered_out} filtered out`}
                {subscription.failures > 0 && ` \u00b7 ${subscription.failures} failed`}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={subscription.active ? 'insert' : 'neutral'}>
                {subscription.active ? 'active' : 'paused'}
              </Badge>
              {subscription.unknown_types.map((type) => (
                <Badge key={type} tone="update">
                  not published: {type}
                </Badge>
              ))}
              <Button
                disabled={busy}
                onClick={() =>
                  run('Pause', async () => {
                    await patch(
                      `/subscriptions/${subscription.id}`,
                      { active: !subscription.active },
                      { actor: 'operator' },
                    )
                  })
                }
              >
                {subscription.active ? 'Pause' : 'Resume'}
              </Button>
              <Button
                variant="danger"
                icon="trash"
                disabled={busy}
                onClick={() =>
                  run('Unsubscribe', async () => del(`/subscriptions/${subscription.id}`, { actor: 'operator' }))
                }
              >
                Unsubscribe
              </Button>
            </div>
          </li>
        ))}
      </ul>
      {subscriptions.length === 0 && (
        <div className="mt-4">
          <EmptyState title="No subscriptions" description="Pick a webhook and at least one type above." />
        </div>
      )}
    </Card>
  )
}

function Deliveries({ deliveries, onChanged, notice, setNotice }) {
  const [selected, setSelected] = useState(null)
  const [busy, setBusy] = useState(false)

  const retry = async (delivery) => {
    setBusy(true)
    setNotice(null)
    try {
      await post(`/deliveries/${delivery.id}/retry`, {}, { actor: 'operator' })
      onChanged()
    } catch (error) {
      setNotice({ tone: 'bad', text: `Retry failed: ${String(error.message || error)}` })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-mono text-sm font-semibold">Deliveries</h2>
          <p className="text-xs text-muted-foreground">
            Every attempt, kept whole. A skipped row is one this product deliberately did not send.
          </p>
        </div>
        <Button icon="refresh" disabled={busy} onClick={onChanged}>
          Refresh
        </Button>
      </div>

      <Banner notice={notice} />

      {deliveries.length > 0 && (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[48rem] text-left text-xs">
            <thead className="text-muted-foreground">
              <tr>
                <th scope="col" className="py-2 pr-3 font-medium">State</th>
                <th scope="col" className="py-2 pr-3 font-medium">Event</th>
                <th scope="col" className="py-2 pr-3 font-medium">Endpoint</th>
                <th scope="col" className="py-2 pr-3 font-medium">Attempt</th>
                <th scope="col" className="py-2 pr-3 font-medium">Next attempt</th>
                <th scope="col" className="py-2 pr-3 font-medium">When</th>
                <th scope="col" className="py-2 font-medium">Detail</th>
              </tr>
            </thead>
            <tbody>
              {deliveries.map((delivery) => (
                <tr key={delivery.id} className="border-t border-border-subtle/30">
                  <td className="py-2 pr-3">
                    <Badge tone={tone(delivery.state)}>{stateLabel(delivery)}</Badge>
                  </td>
                  <td className="py-2 pr-3 font-mono">{delivery.event}</td>
                  <td className="py-2 pr-3 text-muted-foreground">{delivery.webhook_name}</td>
                  <td className="py-2 pr-3 font-mono">
                    {delivery.attempts_total || 0}
                    {delivery.attempts_remaining > 0 && (
                      <span className="text-muted-foreground"> ({delivery.attempts_remaining} left)</span>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-muted-foreground">
                    {delivery.next_attempt_at ? relativeTime(delivery.next_attempt_at) : '-'}
                  </td>
                  <td className="py-2 pr-3 text-muted-foreground">{relativeTime(delivery.at)}</td>
                  <td className="py-2">
                    <span className="inline-flex items-center gap-2">
                      <Button className="px-3" onClick={() => setSelected(delivery)}>
                        Inspect
                      </Button>
                      {delivery.state === 'retrying' && (
                        <Button
                          variant="primary"
                          className="px-3"
                          disabled={busy}
                          onClick={() => retry(delivery)}
                        >
                          Retry now
                        </Button>
                      )}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {deliveries.length === 0 && (
        <div className="mt-4">
          <EmptyState title="No deliveries yet" description="Record an event to see what goes out." />
        </div>
      )}

      {selected && (
        <div className="mt-4 rounded-lg border border-border-subtle/40 p-4">
          <div className="flex items-center justify-between gap-3">
            <p className="font-mono text-xs font-semibold">Attempt log</p>
            <Button onClick={() => setSelected(null)}>Close</Button>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {absoluteTime(selected.at)} &middot; HTTP {selected.http_status || '-'} &middot;{' '}
            {selected.attempts_total || 0} attempt(s)
            {selected.next_attempt_at && ` \u00b7 next ${absoluteTime(selected.next_attempt_at)}`}
          </p>
          {(selected.attempt_log || []).length === 0 ? (
            <p className="mt-2 text-xs text-muted-foreground">
              Nothing was sent.{' '}
              {selected.reason ? SKIP_REASONS[selected.reason] || selected.reason : ''}
            </p>
          ) : (
            <ul className="mt-2 flex flex-col gap-2">
              {selected.attempt_log.map((entry) => (
                <li key={entry.attempt} className="rounded border border-border-subtle/30 p-2">
                  <p className="font-mono text-xs">
                    attempt {entry.attempt} &middot; HTTP {entry.status || '-'} &middot;{' '}
                    {entry.ok ? 'ok' : entry.error || 'failed'}
                    {entry.at && ` \u00b7 ${absoluteTime(entry.at)}`}
                  </p>
                  {entry.response_body && (
                    <p className="font-mono text-xs break-all text-muted-foreground">
                      {entry.response_body}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
          {selected.payload && (
            <details className="mt-3">
              <summary className="cursor-pointer text-xs text-muted-foreground">
                The exact payload that was signed
              </summary>
              <div className="mt-2">
                <JsonView value={selected.payload} />
              </div>
            </details>
          )}
        </div>
      )}
    </Card>
  )
}

function Events({ events }) {
  return (
    <Card>
      <h2 className="font-mono text-sm font-semibold">Recorded activity</h2>
      <p className="text-xs text-muted-foreground">
        Anonymous activity omits <span className="font-mono">user</span>, so a row with no user is a
        deal-room visitor, not a missing field.
      </p>
      <ul className="mt-4 flex flex-col gap-2">
        {events.map((event) => (
          <li key={event.id} className="rounded-lg border border-border-subtle/40 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="font-mono text-xs font-semibold">{event.data.event}</p>
              <div className="flex flex-wrap items-center gap-2">
                {!event.data.known && <Badge tone="update">outside the published set</Badge>}
                {event.data.anonymous && <Badge tone="neutral">anonymous</Badge>}
                <span className="text-xs text-muted-foreground">
                  {relativeTime(event.data.occurred_at)}
                </span>
              </div>
            </div>
            <p className="mt-1 font-mono text-xs break-all text-muted-foreground">
              {Object.entries(event.data.associated_objects || {})
                .map(([kind, value]) => `${kind}:${value.id}`)
                .join('  ')}
            </p>
            {event.data.shareLink && (
              <p className="font-mono text-xs break-all text-accent">
                share link: {event.data.shareLink}
              </p>
            )}
            {event.data.asset && (
              <p className="font-mono text-xs text-muted-foreground">
                asset: {event.data.asset.name} &middot; trackingEnabled{' '}
                {String(event.data.asset.trackingEnabled)}
              </p>
            )}
            {(event.data.form_question_responses || []).map((response) => (
              <p key={response.questionId} className="font-mono text-xs text-muted-foreground">
                {response.questionId}:{' '}
                {response.presigned_expired ? (
                  <span className="text-amber-300">{response.presigned_note}</span>
                ) : (
                  String(response.value)
                )}
              </p>
            ))}
          </li>
        ))}
      </ul>
      {events.length === 0 && (
        <div className="mt-4">
          <EmptyState title="No activity recorded" description="Recorded events appear here." />
        </div>
      )}
    </Card>
  )
}

function Backfill() {
  const PATHS = {
    workspaces: '/backfill/workspaces',
    assets: '/backfill/assets',
    'workspace-plan-tasks': '/backfill/workspace-plan-tasks',
  }
  const [resource, setResource] = useState('workspaces')
  const [properties, setProperties] = useState('')
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const pull = async () => {
    setBusy(true)
    setError(null)
    try {
      setResult(await get(PATHS[resource], { properties: properties || undefined, actor: 'operator' }))
    } catch (exc) {
      setError(exc)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <h2 className="font-mono text-sm font-semibold">Pull-based backfill</h2>
      <p className="text-xs text-muted-foreground">
        For activity the stream missed. Omit <span className="font-mono">properties</span> and the
        response carries only <span className="font-mono">id</span>,{' '}
        <span className="font-mono">object</span> and <span className="font-mono">url</span>. A
        misspelled property is refused rather than silently narrowing the response.
      </p>
      <div className="mt-3 grid gap-3 md:grid-cols-[1fr_2fr_auto] md:items-end">
        <Field label="Resource" id="backfill-resource">
          <select
            id="backfill-resource"
            className={inputClass}
            value={resource}
            onChange={(event) => setResource(event.target.value)}
          >
            <option value="workspaces">GET /v1/workspaces</option>
            <option value="assets">GET /v1/assets</option>
            <option value="workspace-plan-tasks">GET /v1/workspace-plan-tasks</option>
          </select>
        </Field>
        <Field label="Properties" id="backfill-properties" hint="Comma separated.">
          <input
            id="backfill-properties"
            className={inputClass}
            value={properties}
            onChange={(event) => setProperties(event.target.value)}
            placeholder="name,stage"
          />
        </Field>
        <Button variant="primary" icon="database" disabled={busy} onClick={pull}>
          Pull
        </Button>
      </div>
      {result && (
        <p className="mt-2 text-xs text-muted-foreground">
          {result.count} row(s) &middot; stands in for{' '}
          <span className="font-mono">{result.vendor_path}</span> &middot;{' '}
          {result.rate_limit.remaining} of {result.rate_limit.limit} backfill calls left this window
        </p>
      )}
      {result && (
        <div className="mt-2">
          <JsonView value={result.results} />
        </div>
      )}
      {error && (
        <div className="mt-3">
          <ErrorNote error={error} onRetry={pull} />
        </div>
      )}
    </Card>
  )
}

/* -------------------------------------------------------------------------
 * The page
 * ---------------------------------------------------------------------- */

export default function EventStreamPage() {
  const [notice, setNotice] = useState(null)
  const [showRules, setShowRules] = useState(false)

  const { data, loading, error, refetch } = useAsync(async () => {
    const [summary, webhooks, subscriptions, deliveries, events, vocabulary] = await Promise.all([
      get('/summary'),
      get('/webhooks'),
      get('/subscriptions'),
      get('/deliveries', { limit: 40 }),
      get('/events', { limit: 20 }),
      get('/vocabulary'),
    ])
    return {
      summary,
      webhooks: webhooks.webhooks,
      subscriptions: subscriptions.subscriptions,
      deliveries: deliveries.deliveries,
      events: events.events,
      eventTypes: vocabulary.event_types,
    }
  }, [])

  if (loading) return <Spinner label="Loading the event stream" />
  if (error) return <ErrorNote error={error} onRetry={refetch} />

  return (
    <div className="flex flex-col gap-6">
      <header className="flex items-center gap-3">
        <span className="rounded-lg bg-accent/15 p-2 text-accent">
          <Icon name="schema" size={20} />
        </span>
        <div>
          <h1 className="font-mono text-lg font-semibold">Event stream</h1>
          <p className="text-xs text-muted-foreground">
            WF-025 &middot; workspace activity pushed to your own systems, signed, with every delivery
            recorded.
          </p>
        </div>
      </header>

      <Summary summary={data.summary} />

      <Rules open={showRules} onToggle={() => setShowRules((open) => !open)} />

      <Webhooks
        webhooks={data.webhooks}
        onChanged={refetch}
        notice={notice}
        setNotice={setNotice}
      />
      <Subscriptions
        subscriptions={data.subscriptions}
        webhooks={data.webhooks}
        vocabulary={{ event_types: data.eventTypes }}
        onChanged={refetch}
        notice={notice}
        setNotice={setNotice}
      />
      <Deliveries
        deliveries={data.deliveries}
        onChanged={refetch}
        notice={notice}
        setNotice={setNotice}
      />
      <Events events={data.events} />
      <Backfill />
    </div>
  )
}
