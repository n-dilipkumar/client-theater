/**
 * WF-099: turn an accepted quote into a contract.
 *
 * The page reads its modes, flows and refusal reasons from the server's
 * `/vocabulary` route rather than repeating them, so a panel cannot drift from
 * the rules it enforces.
 *
 * Two things this page deliberately does not do, both from the research:
 *
 *   - It offers no way to mark a quote accepted directly. Acceptance is a
 *     platform event, so the only control is "Record acceptance", which is the
 *     acceptance path the server keys contract creation on.
 *   - It does not show a subscription. Billing is WF-096's and is a separate
 *     switch; the page only reports the billing flow the quote describes.
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
  blockedReason,
  canAccept,
  contractApi,
  listQuotes,
  listRooms,
} from './api'
import { Fact, FlowBadge, InlineNote, LineItemsTable, Money } from './primitives'

function ToggleRow({ label, hint, enabled, busy, onToggle }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border-subtle py-3 last:border-b-0">
      <div className="min-w-0">
        <p className="text-sm text-foreground">{label}</p>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </div>
      <Button
        disabled={busy}
        onClick={onToggle}
        aria-pressed={Boolean(enabled)}
        aria-label={`${label}: ${enabled ? 'on' : 'off'}`}
        icon="schema"
      >
        {enabled ? 'On' : 'Off'}
      </Button>
    </div>
  )
}

function SettingsPanel({ settings, roomId, onSaved }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function save(patch) {
    setBusy(true)
    setError(null)
    try {
      await contractApi.updateSettings(roomId, patch)
      onSaved()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  const modes = ['days', 'custom', 'off']

  return (
    <Card className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold text-foreground">Contract settings</h2>
        {!settings?.explicit && <span className="text-xs text-muted-foreground">Default</span>}
      </div>
      <ToggleRow
        label="Create contracts from accepted quotes"
        hint="Set whether contracts are automatically created from accepted quotes."
        enabled={settings?.create_contracts_from_accepted_quotes}
        busy={busy}
        onToggle={() =>
          save({
            create_contracts_from_accepted_quotes: !settings?.create_contracts_from_accepted_quotes,
          })
        }
      />
      <ToggleRow
        label="Connected CPQ, Billing and Payments beta"
        hint="On, an accepted quote creates a contract and an order."
        enabled={settings?.connected_cpq}
        busy={busy}
        onToggle={() => save({ connected_cpq: !settings?.connected_cpq })}
      />
      <ToggleRow
        label="Direct Create, Edit, and Renew beta"
        hint="Allows a contract with no quote."
        enabled={settings?.direct_contracts_enabled}
        busy={busy}
        onToggle={() => save({ direct_contracts_enabled: !settings?.direct_contracts_enabled })}
      />

      <Field
        label="Renewal approaching alert"
        id="wf099-renewal-alert"
        hint="Recorded here; renewal alerts and workflows belong to WF-100."
      >
        <select
          id="wf099-renewal-alert"
          className={inputClass}
          value={settings?.renewal_alert_mode || 'off'}
          disabled={busy}
          onChange={(event) => save({ renewal_alert_mode: event.target.value })}
        >
          {modes.map((mode) => (
            <option key={mode} value={mode}>
              {mode === 'off' ? "Don't show an alert" : mode === 'days' ? 'Days before' : 'Custom'}
            </option>
          ))}
        </select>
      </Field>

      {error && <ErrorNote error={error} />}
    </Card>
  )
}

function QuotePanel({ quotes, onChanged }) {
  const [selected, setSelected] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [note, setNote] = useState(null)
  const [nonce, setNonce] = useState(0)

  const readiness = useAsync(
    () => (selected ? contractApi.quoteReadiness(selected) : Promise.resolve(null)),
    [selected, nonce],
  )

  async function run(action, successNote) {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      await action()
      setNote(successNote)
      setNonce((value) => value + 1)
      onChanged()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  const data = readiness.data
  const ready = canAccept(data)
  const blocked = data ? blockedReason(data) : null

  return (
    <Card className="flex flex-col gap-4">
      <h2 className="text-lg font-semibold text-foreground">Accept a quote</h2>

      <Field label="Quote" id="wf099-quote" hint="A quote that exists in this room.">
        <select
          id="wf099-quote"
          className={inputClass}
          value={selected}
          onChange={(event) => setSelected(event.target.value)}
        >
          <option value="">Select a quote</option>
          {quotes.map((quote) => (
            <option key={quote.id} value={quote.id}>
              {quote.data?.title || quote.id}
            </option>
          ))}
        </select>
      </Field>

      {selected && readiness.loading && <Spinner label="Reading the quote" />}
      {readiness.error && <ErrorNote error={readiness.error} onRetry={readiness.refetch} />}

      {data && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Fact label="Status" hint={data.published ? 'Published' : 'Not published yet'}>
              {data.status || 'unknown'}
            </Fact>
            <Fact label="Flow" hint="What acceptance will create">
              <FlowBadge flow={data.flow} />
            </Fact>
            <Fact label="Effective start" hint={data.effective_start?.mode || data.effective_start_date_mode}>
              {data.effective_start?.start_date || 'Unresolved'}
            </Fact>
            <Fact label="Billing" hint="A separate switch from the contract">
              {data.billing_flow === 'subscription' ? 'Subscription expected' : 'No billing'}
            </Fact>
          </div>

          {blocked && <InlineNote tone={data.legacy ? 'warning' : 'neutral'}>{blocked}</InlineNote>}
          {data.legacy && (
            <InlineNote tone="warning">
              Accepted legacy quotes do not create contracts.
            </InlineNote>
          )}
          {data.existing_contract_id && (
            <InlineNote tone="info">Contract {data.existing_contract_id} already exists.</InlineNote>
          )}

          {note && <InlineNote tone="success">{note}</InlineNote>}
          {error && <ErrorNote error={error} />}

          <div className="flex flex-wrap gap-2">
            <Button
              variant="primary"
              icon="plus"
              disabled={busy || !ready}
              onClick={() =>
                run(
                  () => contractApi.accept(selected, { method: 'esignature' }),
                  'Acceptance recorded and the contract created.',
                )
              }
            >
              Record acceptance
            </Button>
            <Button
              icon="refresh"
              disabled={busy || !data.accepted || Boolean(data.existing_contract_id)}
              onClick={() =>
                run(() => contractApi.generate(selected), 'Contract created from the recorded acceptance.')
              }
            >
              Create contract
            </Button>
          </div>
        </>
      )}
    </Card>
  )
}

function ContractsPanel({ contracts }) {
  if (contracts.length === 0) {
    return (
      <EmptyState
        title="No contracts yet"
        description="Accept a published quote above, or create a standalone contract, and it will appear here."
      />
    )
  }
  return (
    <div className="space-y-4">
      {contracts.map((contract) => {
        const data = contract.data || {}
        return (
          <Card key={contract.id} className="flex flex-col gap-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <h3 className="text-base font-semibold text-foreground">
                  {data.name || contract.id}
                </h3>
                <p className="mt-0.5 font-mono text-xs text-muted-foreground">{contract.id}</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <FlowBadge flow={data.contract_flow} />
                {contract.derived?.has_order && (
                  <span className="font-mono text-xs text-muted-foreground">
                    Order {contract.derived.order_id}
                  </span>
                )}
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Fact label="Start" hint={data.effective_start_date_mode}>
                {data.start_date || 'Not set'}
              </Fact>
              <Fact label="End" hint="Start plus the term">
                {data.end_date || 'No term'}
              </Fact>
              <Fact label="Term" hint={contract.derived?.term_label}>
                {data.term_length ? `${data.term_length} months` : 'Evergreen or unset'}
              </Fact>
              <Fact label="Total" hint="Inherited from the quote">
                <Money amount={data.totals?.total} currency={data.currency} />
              </Fact>
            </div>

            <div>
              <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                Inherited line items
              </p>
              <LineItemsTable lines={data.line_items} />
            </div>

            {data.billing_flow === 'subscription' && (
              <InlineNote tone="info">
                Billing is enabled, so a subscription is expected. Billing is a separate switch
                owned by the payment workflow.
              </InlineNote>
            )}
          </Card>
        )
      })}
    </div>
  )
}

function StandalonePanel({ enabled, roomId, onCreated }) {
  const [form, setForm] = useState({ name: '', currency: 'EUR', start_date: '', term_length: '12' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  function update(key, value) {
    setForm((current) => ({ ...current, [key]: value }))
  }

  async function create() {
    setBusy(true)
    setError(null)
    try {
      await contractApi.createStandalone(roomId, {
        name: form.name,
        currency: form.currency,
        start_date: form.start_date,
        term_length: Number(form.term_length),
      })
      setForm({ name: '', currency: 'EUR', start_date: '', term_length: '12' })
      onCreated()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="flex flex-col gap-4">
      <h2 className="text-lg font-semibold text-foreground">Standalone contract</h2>
      {!enabled && (
        <InlineNote tone="warning">
          Turn on the Direct Create, Edit, and Renew beta above to create a contract with no quote.
        </InlineNote>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name" id="wf099-standalone-name">
          <input
            id="wf099-standalone-name"
            className={inputClass}
            value={form.name}
            disabled={!enabled || busy}
            onChange={(event) => update('name', event.target.value)}
          />
        </Field>
        <Field label="Currency" id="wf099-standalone-currency">
          <input
            id="wf099-standalone-currency"
            className={inputClass}
            value={form.currency}
            disabled={!enabled || busy}
            onChange={(event) => update('currency', event.target.value)}
          />
        </Field>
        <Field label="Start date" id="wf099-standalone-start" hint="ISO date, for example 2026-04-01.">
          <input
            id="wf099-standalone-start"
            className={inputClass}
            value={form.start_date}
            disabled={!enabled || busy}
            onChange={(event) => update('start_date', event.target.value)}
          />
        </Field>
        <Field label="Term in months" id="wf099-standalone-term">
          <input
            id="wf099-standalone-term"
            className={inputClass}
            inputMode="numeric"
            value={form.term_length}
            disabled={!enabled || busy}
            onChange={(event) => update('term_length', event.target.value)}
          />
        </Field>
      </div>
      {error && <ErrorNote error={error} />}
      <div>
        <Button
          icon="plus"
          disabled={!enabled || busy || form.name.trim() === ''}
          onClick={create}
        >
          Create standalone contract
        </Button>
      </div>
    </Card>
  )
}

function ContractsPage() {
  const [roomId, setRoomId] = useState('')

  const rooms = useAsync(() => listRooms(), [])
  const vocabulary = useAsync(() => contractApi.vocabulary(), [])
  const settings = useAsync(() => contractApi.settings(roomId), [roomId])
  const contracts = useAsync(() => contractApi.contracts(roomId), [roomId])
  const summary = useAsync(() => contractApi.summary(roomId), [roomId])
  const quotes = useAsync(() => listQuotes(), [])

  if (vocabulary.loading || rooms.loading) return <Spinner label="Loading contracts" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (contracts.error) return <ErrorNote error={contracts.error} onRetry={contracts.refetch} />

  const entries = contracts.data?.entries || []
  const roomOptions = rooms.data?.records || []
  const quoteOptions = quotes.data?.records || []

  function refresh() {
    contracts.refetch()
    summary.refetch()
    settings.refetch()
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-1">
        <h1 className="font-display text-2xl font-semibold text-foreground">
          Contracts from accepted quotes
        </h1>
        <p className="text-sm text-muted-foreground">
          A contract inherits the quote&apos;s line items and terms. The contract becomes the
          record of committed revenue, so editing the quote afterwards changes nothing here.
        </p>
      </header>

      <Card className="flex flex-col gap-3">
        <Field label="Room" id="wf099-room" hint="Contracts and settings belong to a room.">
          <select
            id="wf099-room"
            className={inputClass}
            value={roomId}
            onChange={(event) => setRoomId(event.target.value)}
          >
            <option value="">All rooms</option>
            {roomOptions.map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </select>
        </Field>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Contracts"
          value={summary.data?.contracts ?? 0}
          icon="audit"
          hint="Created from quotes or standalone"
        />
        <StatCard
          label="With an order"
          value={summary.data?.with_order ?? 0}
          icon="database"
          hint="Connected CPQ beta"
        />
        <StatCard
          label="Acceptances"
          value={summary.data?.acceptances ?? 0}
          icon="schema"
          hint="Recorded acceptance events"
        />
        <StatCard
          label="Legacy excluded"
          value={summary.data?.legacy_acceptances ?? 0}
          icon="close"
          hint="Accepted, but no contract"
        />
      </div>

      <SettingsPanel settings={settings.data} roomId={roomId} onSaved={refresh} />

      <QuotePanel quotes={quoteOptions} onChanged={refresh} />

      <StandalonePanel
        enabled={Boolean(settings.data?.direct_contracts_enabled)}
        roomId={roomId}
        onCreated={refresh}
      />

      <section className="space-y-4">
        <h2 className="font-display text-lg font-semibold text-foreground">Contracts</h2>
        <ContractsPanel contracts={entries} />
      </section>
    </div>
  )
}

export default {
  id: 'wf-099-auto-create-a-contract-from-an-accepted-quote',
  label: 'Contracts',
  icon: 'audit',
  order: 999,
  Component: ContractsPage,
}
