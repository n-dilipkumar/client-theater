import { useState } from 'react'
import { relativeTime } from '@/lib/api'
import { Badge, Button, Card, EmptyState, ErrorNote, Field, Icon, Spinner, inputClass, useAsync } from '@/components/ui'
import { publishingApi } from './api'
import { BELL, SHARE } from './icons'

/**
 * Webhook subscriptions for room transitions.
 *
 * The point of this panel is that a downstream system learns about a publish
 * without polling, and that a delivery which failed is visible rather than lost.
 * A failed attempt is shown with its error, because "we sent it" and "it
 * arrived" are different claims.
 *
 * This was `src/components/WebhookPanel.jsx` on the branch. `src/components` is
 * shared, so it moved into the feature folder; its only importer was the page
 * beside it.
 */
export default function WebhookPanel() {
  const webhooks = useAsync(() => publishingApi.webhooks(), [])
  const events = useAsync(() => publishingApi.events({ limit: 8 }), [])

  const [form, setForm] = useState({ name: '', url: '', events: [] })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [expanded, setExpanded] = useState(null)

  const subscriptions = webhooks.data?.subscriptions || []
  const eventTypes = webhooks.data?.event_types || []

  async function subscribe(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await publishingApi.subscribe(form)
      setForm({ name: '', url: '', events: [] })
      webhooks.refetch()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  async function cancel(id) {
    try {
      await publishingApi.cancelSubscription(id)
      webhooks.refetch()
    } catch (err) {
      setError(err)
    }
  }

  function showDeliveries(id) {
    setExpanded((current) => (current === id ? null : id))
  }

  function toggleEvent(name) {
    setForm((prev) => ({
      ...prev,
      events: prev.events.includes(name)
        ? prev.events.filter((e) => e !== name)
        : [...prev.events, name],
    }))
  }

  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 font-mono text-sm font-semibold tracking-wide uppercase">
          <span className="text-accent">
            <Icon path={SHARE} size={16} />
          </span>
          Transition events
        </h2>
        <Button
          icon="refresh"
          onClick={() => {
            webhooks.refetch()
            events.refetch()
          }}
        >
          Refresh
        </Button>
      </div>
      <p className="text-sm text-muted-foreground">
        A subscriber is told when a room goes live or is brought back, so it never has to poll
        for the change.
      </p>

      <form onSubmit={subscribe} className="mt-4 space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name" id="hook-name" hint="Who receives it, for your own reading.">
            <input
              id="hook-name"
              className={inputClass}
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
            />
          </Field>
          <Field label="Endpoint URL" id="hook-url" hint="http or https.">
            <input
              id="hook-url"
              type="url"
              required
              placeholder="https://crm.example/hooks/rooms"
              className={inputClass}
              value={form.url}
              onChange={(event) => setForm({ ...form, url: event.target.value })}
            />
          </Field>
        </div>

        <fieldset className="space-y-1.5">
          <legend className="mb-1.5 text-xs font-medium text-muted-foreground">Events</legend>
          {eventTypes.map((name) => (
            <label key={name} className="flex min-h-11 items-center gap-3 text-sm text-foreground">
              <input
                type="checkbox"
                className="h-5 w-5 accent-[var(--color-accent)]"
                checked={form.events.includes(name)}
                onChange={() => toggleEvent(name)}
              />
              <span className="font-mono text-sm">{name}</span>
            </label>
          ))}
        </fieldset>

        {error && <ErrorNote error={error} />}
        <Button type="submit" variant="primary" icon="plus" disabled={busy || !form.events.length}>
          {busy ? 'Subscribing…' : 'Subscribe'}
        </Button>
      </form>

      <div className="mt-5 border-t border-border-subtle/25 pt-4">
        <h3 className="mb-2 font-mono text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Subscriptions
        </h3>
        {webhooks.loading ? (
          <Spinner label="Loading subscriptions" />
        ) : webhooks.error ? (
          <ErrorNote error={webhooks.error} onRetry={webhooks.refetch} />
        ) : subscriptions.length === 0 ? (
          <EmptyState
            title="No subscribers yet"
            description="Add one above to hear about the next publish."
          />
        ) : (
          <ul className="space-y-2">
            {subscriptions.map((subscription) => (
              <li
                key={subscription.id}
                className="rounded-lg border border-border-subtle/25 bg-background/40 p-3"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate font-mono text-sm font-semibold text-foreground">
                      {subscription.name}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">{subscription.url}</p>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {subscription.events.map((name) => (
                      <Badge key={name} tone={name === 'room.set_live' ? 'insert' : 'update'}>
                        {name}
                      </Badge>
                    ))}
                  </div>
                </div>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button onClick={() => showDeliveries(subscription.id)}>
                    <Icon path={BELL} />
                    {expanded === subscription.id ? 'Hide deliveries' : 'Deliveries'}
                  </Button>
                  <Button variant="danger" onClick={() => cancel(subscription.id)}>
                    Cancel
                  </Button>
                </div>
                {expanded === subscription.id && <DeliveryList subscriptionId={subscription.id} />}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="mt-5 border-t border-border-subtle/25 pt-4">
        <h3 className="mb-2 font-mono text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Recent events
        </h3>
        {events.loading ? (
          <Spinner label="Loading events" />
        ) : events.error ? (
          <ErrorNote error={events.error} onRetry={events.refetch} />
        ) : (events.data?.events || []).length === 0 ? (
          <p className="text-sm text-muted-foreground">No room has changed status yet.</p>
        ) : (
          <ul className="divide-y divide-border-subtle/15">
            {events.data.events.map((event) => (
              <li key={event.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                <span className="text-accent">
                  <Icon path={SHARE} size={16} />
                </span>
                <span className="font-mono text-foreground">{event.event}</span>
                <span className="min-w-0 flex-1 truncate text-muted-foreground">
                  {event.room_name} · {event.previous_status} → {event.status}
                </span>
                <span className="text-xs text-muted-foreground">{relativeTime(event.occurred_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  )
}

function DeliveryList({ subscriptionId }) {
  const deliveries = useAsync(() => publishingApi.deliveries(subscriptionId), [subscriptionId])

  if (deliveries.loading) return <Spinner label="Loading deliveries" />
  if (deliveries.error) return <ErrorNote error={deliveries.error} onRetry={deliveries.refetch} />

  const rows = deliveries.data?.deliveries || []
  if (rows.length === 0) {
    return <p className="mt-2 text-sm text-muted-foreground">Nothing delivered yet.</p>
  }

  return (
    <ul className="mt-3 space-y-1.5 border-t border-border-subtle/25 pt-3">
      {rows.map((delivery) => (
        <li key={delivery.id} className="flex flex-wrap items-center gap-2 text-xs">
          <Badge tone={delivery.status === 'delivered' ? 'insert' : 'delete'}>{delivery.status}</Badge>
          <span className="font-mono text-foreground">{delivery.event}</span>
          <span className="text-muted-foreground">
            {delivery.status_code ? `HTTP ${delivery.status_code}` : delivery.error}
          </span>
          <span className="ml-auto text-muted-foreground">{relativeTime(delivery.created_at)}</span>
        </li>
      ))}
    </ul>
  )
}
