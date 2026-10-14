/**
 * WF-138: the branded-domain page.
 *
 * The seller's view of one account's brand. It follows the researched flow in
 * order, so a reviewer can walk it top to bottom:
 *
 *   1. point a CNAME at this deployment, and see what that does not prove;
 *   2. choose the account whose brand this is;
 *   3. set the owner identity and, when the account is an agency, the client's
 *      identity that white-label mode presents instead;
 *   4. set the tenant token overrides;
 *   5. attach the hosts the brand answers on;
 *   6. attach the rooms, which inherit the brand's current tokens.
 *
 * Three properties are load-bearing and are why the page is shaped this way:
 *
 *   * **A host state is never carried by colour alone.** `StatePill` puts the
 *     words ("Live", "Awaiting DNS", "Check failed", "Released") beside the tone.
 *   * **An override is token to token.** Every control is a select over this
 *     product's own token names, so the page cannot send a colour value, and the
 *     preview shows the custom property each pair becomes rather than a swatch.
 *   * **White-label mode swaps the identity.** The panel shows who is presented
 *     and, separately, the owner name that is withheld, so the swap is visible
 *     rather than implied.
 *
 * Every control carries a visible label and reaches `min-h-11`. Nothing here
 * animates. No colour value is written by hand.
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
  EVENT_WORDS,
  TENANT_TOKENS,
  brandedDomainApi,
  listRooms,
  reasonNote,
  reasonText,
  tokenVariables,
} from './api'
import {
  CannotVerify,
  CheckField,
  CheckList,
  KeyValue,
  StatePill,
  TokenSelect,
  VariableList,
} from './primitives'

/**
 * The most useful sentence a failure has.
 *
 * A 422 carries `errors`, a field-keyed map, and those messages are written for
 * the operator; a 500 carries only a detail. Joining the map's values puts the
 * server's own words on the page instead of one status line.
 */
export function errorText(error) {
  const fieldErrors = error?.errors ? Object.values(error.errors) : []
  if (fieldErrors.length) return fieldErrors.join(' ')
  return String(error?.message || error)
}

function Feedback({ feedback }) {
  if (!feedback) return null
  const failed = feedback.kind === 'error'
  return (
    <div
      role={failed ? 'alert' : 'status'}
      className={`rounded-sm border p-3 text-sm ${
        failed
          ? 'border-destructive/40 bg-destructive/10 text-destructive'
          : 'border-border-subtle bg-muted text-foreground'
      }`}
    >
      <p className="font-medium">{failed ? feedback.title || 'That change was refused' : 'Saved'}</p>
      <p className="mt-0.5">{feedback.text}</p>
      {feedback.note && <p className="mt-0.5 text-xs text-muted-foreground">{feedback.note}</p>}
    </div>
  )
}

function CreateBrand({ busy, onCreate }) {
  const [name, setName] = useState('')
  const [ownerName, setOwnerName] = useState('')

  return (
    <form
      className="grid gap-4 sm:grid-cols-2"
      onSubmit={(event) => {
        event.preventDefault()
        onCreate({ name, owner_name: ownerName || name }).then((saved) => {
          if (saved) {
            setName('')
            setOwnerName('')
          }
        })
      }}
    >
      <Field label="New brand name" id="new-brand-name">
        <input
          id="new-brand-name"
          className={inputClass}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </Field>
      <Field
        label="New owner name"
        id="new-brand-owner"
        hint="The identity presented when the room is not white-labelled."
      >
        <input
          id="new-brand-owner"
          className={inputClass}
          value={ownerName}
          onChange={(event) => setOwnerName(event.target.value)}
        />
      </Field>
      <div className="sm:col-span-2">
        <Button type="submit" variant="primary" disabled={busy}>
          Create brand
        </Button>
      </div>
    </form>
  )
}

function IdentityForm({ brand, busy, onSave }) {
  const [ownerName, setOwnerName] = useState(brand.owner_name || '')
  const [clientName, setClientName] = useState(brand.client_name || '')
  const [whiteLabel, setWhiteLabel] = useState(Boolean(brand.white_label))

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault()
        onSave({
          owner_name: ownerName,
          client_name: clientName || null,
          white_label: whiteLabel,
        })
      }}
    >
      <Field label="Owner name" id="brand-owner" hint="Shown to buyers when the room is not white-labelled.">
        <input
          id="brand-owner"
          className={inputClass}
          value={ownerName}
          onChange={(event) => setOwnerName(event.target.value)}
        />
      </Field>

      <CheckField
        id="brand-white-label"
        label="White-label mode"
        hint="The client's brand is presented instead of this account's. The owner name is withheld, not shown beside it."
        checked={whiteLabel}
        onChange={setWhiteLabel}
      />

      <Field
        label="Client name"
        id="brand-client"
        hint="Required while white-label mode is on: the switch swaps an identity, so it needs one to present."
      >
        <input
          id="brand-client"
          className={inputClass}
          value={clientName}
          onChange={(event) => setClientName(event.target.value)}
        />
      </Field>

      <dl className="grid gap-2 rounded-sm border border-border-subtle bg-muted p-4 sm:grid-cols-2">
        <KeyValue label="Presented to buyers">
          {brand.presented?.presented || 'Nobody yet'}
        </KeyValue>
        <KeyValue label="Presented as">{brand.presented?.role || 'owner'}</KeyValue>
        <KeyValue label="Withheld">
          {brand.presented?.withheld || 'Nothing is withheld'}
        </KeyValue>
        <KeyValue label="White-labelled">
          {brand.presented?.white_labelled ? 'Yes' : 'No'}
        </KeyValue>
      </dl>

      <div>
        <Button type="submit" disabled={busy}>
          Save identity
        </Button>
      </div>
    </form>
  )
}

function TokenForm({ brand, tokenOptions, busy, onSave }) {
  const [tokens, setTokens] = useState(() => ({ ...(brand.tokens || {}) }))

  function setToken(token, value) {
    setTokens((previous) => {
      const next = { ...previous }
      if (value) next[token] = value
      else delete next[token]
      return next
    })
  }

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault()
        onSave(tokens)
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {tokenOptions.map((token) => (
          <TokenSelect
            key={token}
            id={`token-${token}`}
            label={token}
            value={tokens[token]}
            options={tokenOptions}
            onChange={(value) => setToken(token, value)}
          />
        ))}
      </div>

      <div>
        <h3 className="text-base font-semibold text-foreground">What these become</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          Each pair renders on the branded host as a scoped custom property, so the stylesheet keeps
          the colour and a later theme change still reaches this account.
        </p>
        <div className="mt-2 rounded-sm border border-border-subtle bg-muted p-3">
          <VariableList variables={tokenVariables(tokens)} />
        </div>
      </div>

      <div>
        <Button type="submit" disabled={busy}>
          Save tokens
        </Button>
      </div>
    </form>
  )
}

function HostsPanel({ hosts, busy, onVerify, onAttach, onRelease, onRecheck }) {
  const [host, setHost] = useState('')
  const [force, setForce] = useState(false)
  const [report, setReport] = useState(null)

  return (
    <div className="flex flex-col gap-4">
      <form
        className="grid gap-4 sm:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault()
          onVerify(host).then((result) => setReport(result))
        }}
      >
        <Field
          label="Host"
          id="host-input"
          hint="Subdomain format, for example deals.acme.com. The A record is not used; this is a CNAME."
        >
          <input
            id="host-input"
            className={inputClass}
            value={host}
            onChange={(event) => {
              setHost(event.target.value)
              setReport(null)
            }}
          />
        </Field>
        <div className="flex items-end gap-3">
          <Button type="submit" disabled={busy || !host}>
            Verify host
          </Button>
          <Button
            variant="primary"
            disabled={busy || !host}
            onClick={() => onAttach(host, force).then((saved) => saved && setReport(null))}
          >
            Attach host
          </Button>
        </div>
        <div className="sm:col-span-2">
          <CheckField
            id="host-force"
            label="Save while the CNAME is still propagating"
            hint="A new CNAME can take up to 24 hours. A saved host does not serve until its check passes."
            checked={force}
            onChange={setForce}
          />
        </div>
      </form>

      {report && (
        <div className="rounded-sm border border-border-subtle bg-surface p-4">
          <div className="flex flex-wrap items-center gap-2">
            <StatePill state={report.status} />
            <span className="font-mono text-[13px] text-foreground">{report.host}</span>
            <span className="text-xs text-muted-foreground">
              CNAME target {report.cname_target}
            </span>
          </div>
          <div className="mt-3">
            <CheckList checks={report.checks} title="Host verification checks" />
          </div>
        </div>
      )}

      <div>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="text-base font-semibold text-foreground">Hosts this brand answers on</h3>
          <Button
            icon="refresh"
            disabled={busy || !hosts.length}
            aria-label="Re-check every host"
            onClick={() => onRecheck()}
          >
            Re-check every host
          </Button>
        </div>

        {hosts.length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">
            No host attached yet, so this brand answers on no domain of its own.
          </p>
        ) : (
          <ul className="mt-3 flex flex-col gap-3">
            {hosts.map((row) => (
              <li
                key={row.id || row.host}
                className="flex flex-wrap items-center justify-between gap-3 rounded-sm border border-border-subtle p-3"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <StatePill state={row.state} />
                    <span className="font-mono text-[13px] text-foreground">{row.host}</span>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{row.expectation}</p>
                </div>
                <Button
                  variant="danger"
                  disabled={busy || row.state === 'released'}
                  aria-label={`Release ${row.host}`}
                  onClick={() => onRelease(row.host)}
                >
                  Release
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

function RoomsPanel({ accountId, rooms, themes, tokenOptions, roomNames, busy, onAttach, onPropagate }) {
  const [roomId, setRoomId] = useState('')
  const [override, setOverride] = useState('')

  const attached = new Set(themes.map((theme) => theme.room_ref))
  const available = rooms.filter((room) => !attached.has(room.id))

  return (
    <div className="flex flex-col gap-4">
      <form
        className="grid gap-4 sm:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault()
          onAttach(accountId, roomId, override).then((saved) => {
            if (saved) {
              setRoomId('')
              setOverride('')
            }
          })
        }}
      >
        <Field label="Room" id="room-select" hint="A room inherits the brand's current tokens when it joins.">
          <select
            id="room-select"
            className={inputClass}
            value={roomId}
            onChange={(event) => setRoomId(event.target.value)}
          >
            <option value="">Choose a room</option>
            {available.map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </select>
        </Field>

        <TokenSelect
          id="room-override"
          label="Room override for the accent token"
          value={override}
          options={tokenOptions}
          onChange={setOverride}
        />

        <div className="flex items-end gap-3 sm:col-span-2">
          <Button type="submit" disabled={busy || !roomId}>
            Attach room
          </Button>
          <Button
            variant="primary"
            disabled={busy || !themes.length}
            onClick={() => onPropagate(accountId)}
          >
            Apply the brand to every room
          </Button>
        </div>
      </form>

      {themes.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No room has joined this brand yet, so no room renders its tokens.
        </p>
      ) : (
        <ul className="flex flex-col gap-3">
          {themes.map((theme) => (
            <li
              key={theme.theme_id}
              className="rounded-sm border border-border-subtle p-3"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-foreground">
                  {roomNames[theme.room_ref] || theme.room_ref}
                </span>
                <span className="font-mono text-xs text-muted-foreground">
                  revision {theme.brand_revision}
                </span>
                <span className="text-xs text-muted-foreground">
                  {theme.inherited ? 'inherits the brand' : 'carries its own override'}
                </span>
              </div>
              <div className="mt-2">
                <VariableList variables={theme.css_variables} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function AccountPanel({ accountId, data, rooms, tokenOptions, onChanged }) {
  const [brand, domains, roomThemes, eventLog] = data
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState(null)

  async function run(action, successText) {
    setBusy(true)
    setFeedback(null)
    try {
      await action()
      onChanged()
      if (successText) setFeedback({ kind: 'ok', text: successText })
      return true
    } catch (error) {
      setFeedback({
        kind: 'error',
        title: error?.reason ? reasonText(error.reason) : null,
        text: errorText(error),
        note: error?.reason ? reasonNote(error.reason) : null,
      })
      return false
    } finally {
      setBusy(false)
    }
  }

  const hosts = domains?.hosts || []
  const themes = roomThemes?.rooms || []
  const events = eventLog?.events || []
  const roomNames = Object.fromEntries(
    rooms.map((room) => [room.id, room.data?.name || room.id]),
  )

  return (
    <div className="space-y-6">
      <Feedback feedback={feedback} />

      <Card>
        <h2 className="text-lg font-semibold text-foreground">3. Brand identity</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          The owner is the account. White-label mode presents the client instead, which is what an
          agency needs and is a swap rather than a second brand.
        </p>
        <div className="mt-4">
          <IdentityForm
            key={`identity-${accountId}-${brand.brand_revision}`}
            brand={brand}
            busy={busy}
            onSave={(payload) =>
              run(() => brandedDomainApi.saveBrand(accountId, payload), 'Identity saved')
            }
          />
        </div>
      </Card>

      <Card>
        <h2 className="text-lg font-semibold text-foreground">4. Brand tokens</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          An override names another token, never a colour value, so a tenant cannot opt this product
          out of a later theme change. Four tokens that decide text contrast stay the product's.
        </p>
        <div className="mt-4">
          <TokenForm
            key={`tokens-${accountId}-${brand.brand_revision}`}
            brand={brand}
            tokenOptions={tokenOptions}
            busy={busy}
            onSave={(tokens) =>
              run(() => brandedDomainApi.saveTokens(accountId, tokens), 'Tokens saved and propagated')
            }
          />
        </div>
      </Card>

      <Card>
        <h2 className="text-lg font-semibold text-foreground">5. Hosts</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Verify first, then attach. Only a verified host serves traffic, and a host another brand
          holds is refused whatever you tick.
        </p>
        <div className="mt-4">
          <HostsPanel
            key={`hosts-${accountId}`}
            hosts={hosts}
            busy={busy}
            onVerify={async (host) => {
              let result = null
              await run(async () => {
                result = await brandedDomainApi.verifyHost(accountId, host)
              })
              return result
            }}
            onAttach={(host, force) =>
              run(
                () => brandedDomainApi.attachHost(accountId, host, force),
                'Host attached',
              )
            }
            onRelease={(host) =>
              run(() => brandedDomainApi.releaseHost(accountId, host), 'Host released')
            }
            onRecheck={() =>
              run(() => brandedDomainApi.recheckHosts(accountId), 'Every host re-checked')
            }
          />
        </div>
      </Card>

      <Card>
        <h2 className="text-lg font-semibold text-foreground">6. Rooms</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          A room joins a brand, inherits the tokens the brand has now, and may refine them. A brand
          change is written to every room of the brand at once.
        </p>
        <div className="mt-4">
          <RoomsPanel
            key={`rooms-${accountId}`}
            accountId={accountId}
            rooms={rooms}
            themes={themes}
            tokenOptions={tokenOptions}
            roomNames={roomNames}
            busy={busy}
            onAttach={(id, roomId, override) =>
              run(
                () =>
                  brandedDomainApi.attachRoom(roomId, {
                    account_id: id,
                    tokens: override ? { accent: override } : undefined,
                  }),
                'Room attached',
              )
            }
            onPropagate={(id) =>
              run(() => brandedDomainApi.propagate(id), 'Brand applied to every room')
            }
          />
        </div>
      </Card>

      <Card>
        <h3 className="text-base font-semibold text-foreground">Change log</h3>
        {events.length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">Nothing has changed for this brand yet.</p>
        ) : (
          <ul className="mt-3 flex flex-col gap-2">
            {events.map((event) => (
              <li key={event.id} className="flex flex-wrap items-baseline gap-2">
                <span className="text-sm text-foreground">
                  {EVENT_WORDS[event.kind] || event.kind}
                </span>
                {event.host && (
                  <span className="font-mono text-xs text-muted-foreground">{event.host}</span>
                )}
                {event.detail && (
                  <span className="text-xs text-muted-foreground">{event.detail}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

export default function BrandedDomain() {
  const config = useAsync(() => brandedDomainApi.config(), [])
  const summary = useAsync(() => brandedDomainApi.summary(), [])
  const accounts = useAsync(() => brandedDomainApi.accounts(), [])
  const rooms = useAsync(() => listRooms(), [])
  const vocabulary = useAsync(() => brandedDomainApi.vocabulary(), [])

  const [chosenId, setChosenId] = useState('')
  // Derived rather than mirrored into state by an effect: the first account is
  // the default until the operator chooses another one.
  const accountId = chosenId || accounts.data?.accounts?.[0]?.account_id || ''

  const account = useAsync(
    () =>
      accountId
        ? Promise.all([
            brandedDomainApi.brand(accountId),
            brandedDomainApi.domains(accountId),
            brandedDomainApi.rooms(accountId),
            brandedDomainApi.events(accountId),
          ])
        : Promise.resolve(null),
    [accountId],
  )

  const [busy, setBusy] = useState(false)
  const [createError, setCreateError] = useState(null)
  const tokenOptions = vocabulary.data?.tenant_tokens?.length
    ? vocabulary.data.tenant_tokens
    : TENANT_TOKENS

  async function createBrand(payload) {
    setBusy(true)
    setCreateError(null)
    try {
      const created = await brandedDomainApi.createAccount(payload)
      setChosenId(created.account_id)
      accounts.refetch()
      summary.refetch()
      return true
    } catch (error) {
      setCreateError(errorText(error))
      return false
    } finally {
      setBusy(false)
    }
  }

  function refresh() {
    account.refetch()
    accounts.refetch()
    summary.refetch()
  }

  // Only the first load blocks the page. A refetch after a write must not blank
  // the form the operator is still looking at.
  if ((config.loading && !config.data) || (accounts.loading && !accounts.data)) {
    return <Spinner label="Loading the branded domain" />
  }
  if (config.error && !config.data) {
    return <ErrorNote error={config.error} onRetry={config.refetch} />
  }
  if (accounts.error && !accounts.data) {
    return <ErrorNote error={accounts.error} onRetry={accounts.refetch} />
  }

  const counts = summary.data || {}
  const roomRecords = rooms.data?.records || rooms.data?.rooms || []

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-foreground">Branded domain</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Serve a room from the account's own domain with the account's brand, and in
          white-label mode with the client's brand instead.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Brands" value={counts.brands ?? 0} hint={`${counts.white_labelled ?? 0} white-labelled`} />
        <StatCard label="Hosts live" value={counts.verified ?? 0} hint={`${counts.unverified ?? 0} awaiting DNS`} />
        <StatCard label="Checks failed" value={counts.failed ?? 0} hint={`${counts.released ?? 0} released`} />
        <StatCard
          label="Rooms themed"
          value={counts.rooms_themed ?? 0}
          hint={`${counts.rooms_inheriting ?? 0} inheriting the brand`}
        />
      </div>

      <Card>
        <h2 className="text-lg font-semibold text-foreground">1. Point a CNAME at this deployment</h2>
        <div className="mt-3 grid gap-4 lg:grid-cols-2">
          <dl className="grid gap-2">
            <KeyValue label="Record type">{config.data.record_type}</KeyValue>
            <KeyValue label="Value">
              <span className="font-mono text-[13px]">{config.data.cname_target}</span>
            </KeyValue>
            <KeyValue label="This deployment answers on">
              <span className="font-mono text-[13px]">{config.data.deployment_host}</span>
            </KeyValue>
          </dl>
          <ul className="flex list-disc flex-col gap-1.5 pl-5 text-sm text-muted-foreground">
            <li>{config.data.propagation_note}</li>
            <li>{config.data.cloudflare_note}</li>
            <li>{config.data.format_note}</li>
            <li>{config.data.token_note}</li>
          </ul>
        </div>
      </Card>

      <CannotVerify notes={config.data.cannot_verify} />

      <Card>
        <h2 className="text-lg font-semibold text-foreground">2. Choose an account</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          A brand belongs to one account. The account's own identity is the owner; white-label mode
          presents its client instead.
        </p>

        {(accounts.data?.accounts || []).length === 0 ? (
          <div className="mt-4">
            <EmptyState
              title="No brands yet"
              description="Create the first account's brand below, then attach a host and its rooms."
            />
          </div>
        ) : (
          <div className="mt-4 max-w-md">
            <Field label="Account" id="account-select">
              <select
                id="account-select"
                className={inputClass}
                value={accountId}
                onChange={(event) => setChosenId(event.target.value)}
              >
                {(accounts.data?.accounts || []).map((row) => (
                  <option key={row.account_id} value={row.account_id}>
                    {row.name}
                  </option>
                ))}
              </select>
            </Field>
          </div>
        )}

        <div className="mt-4 border-t border-border-subtle pt-4">
          <h3 className="text-base font-semibold text-foreground">Add an account's brand</h3>
          <div className="mt-2">
            <CreateBrand busy={busy} onCreate={createBrand} />
          </div>
          {createError && (
            <p className="mt-2 text-sm text-destructive" role="alert">
              {createError}
            </p>
          )}
        </div>
      </Card>

      {account.error && <ErrorNote error={account.error} onRetry={account.refetch} />}

      {account.loading && !account.data && <Spinner label="Loading this account's brand" />}

      {accountId && account.data && (
        <AccountPanel
          accountId={accountId}
          data={account.data}
          rooms={roomRecords}
          tokenOptions={tokenOptions}
          onChanged={refresh}
        />
      )}
    </div>
  )
}
