import { useCallback, useEffect, useState } from 'react'
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
import {
  cacheAge,
  cacheNote,
  crmReadApi,
  isUnlabelled,
  outcomeNote,
  planSummary,
} from './api'
import { DataTable, Fact, Glyph, OptionValue, Section } from './primitives'

/**
 * The buyer's deal panel (WF-042).
 *
 * The researched flow, in the order the page renders it: a room resolves which
 * CRM records a buyer belongs to, issues a read-only field-scoped paged read,
 * normalises the paged response into one view model, labels every option value,
 * and caches the panel per buyer for a short TTL. The page shows each step's
 * evidence rather than only the result, because the three things a reviewer needs
 * to check are the plan that was sent, the label source that was used, and
 * whether the panel came from the cache or from the vendor.
 *
 * A room whose seller authored it without CRM context still renders. That state
 * is a panel with its reason attached, not an error, and it is the most ordinary
 * room in the product.
 */

const ROOM_STORAGE_KEY = 'wf-042:last-room'

function lastRoom() {
  try {
    return window.localStorage.getItem(ROOM_STORAGE_KEY) || ''
  } catch {
    // Private browsing, a locked-down profile, or a server render. An empty
    // picker is the right answer in all three, so nothing is invented here.
    return ''
  }
}

function PanelPart({ title, part, glyph, idValue, nameValue, extra }) {
  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            {title}
          </p>
          {part.found ? (
            <p className="mt-2 font-display text-base font-semibold text-foreground">
              {nameValue || 'Unnamed'}
            </p>
          ) : (
            <p className="mt-2 font-display text-base font-semibold text-muted-foreground">
              Not in the CRM
            </p>
          )}
        </div>
        <span className="rounded-sm bg-accent-soft p-2 text-accent">
          <Glyph name={glyph} size={20} />
        </span>
      </div>
      <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
        {extra}
      </dl>
      <p className="mt-3 font-mono text-[11px] text-muted-foreground">
        {part.external_id ? `record ${part.external_id}` : 'no record id resolved'}
        {idValue ? `, matched on ${idValue}` : ''}
      </p>
    </Card>
  )
}

function CachePanel({ cache }) {
  const state = cache?.state || 'absent'
  const tones = { fresh: 'insert', expired: 'restore', stale: 'delete', absent: 'neutral' }
  return (
    <Section
      title="Read-through cache"
      hint="The research asks for a short TTL and no number. This room's default, its floor and its ceiling are all reported, and a room outside them is clamped rather than refused."
    >
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <span className="rounded-sm bg-accent-soft p-2 text-accent">
              <Glyph name="cache" size={20} />
            </span>
            <div>
              <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                Last read
              </p>
              <p className="mt-1 text-sm text-foreground">{cacheNote(state)}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge tone={tones[state] || 'neutral'}>{state}</Badge>
            {cacheAge(cache) ? (
              <span className="font-mono text-xs">{cacheAge(cache)}</span>
            ) : null}
          </div>
        </div>
        <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Fact term="TTL" mono>
            {cache?.ttl_seconds ?? '-'}s
          </Fact>
          <Fact term="Expires" mono>
            {cache?.expires_at ? new Date(cache.expires_at).toLocaleTimeString() : '-'}
          </Fact>
          <Fact term="Mode" mono>
            {cache?.refresh_mode || 'pull'}
          </Fact>
        </dl>
        <p className="mt-3 text-xs text-muted-foreground">
          The research states &quot;read-through cache refresh on a room scheduler; nothing
          pushes&quot;. This room pulls. Dataverse change tracking, the alternative the research
          records, is a different workflow.
        </p>
      </Card>
    </Section>
  )
}

function NoContextPanel({ body, onRegister }) {
  return (
    <Card>
      <div className="flex items-start gap-3">
        <span className="rounded-sm bg-accent-soft p-2 text-accent">
          <Glyph name="read" size={20} />
        </span>
        <div className="min-w-0">
          <p className="font-display text-base font-semibold text-foreground">
            No CRM context for this buyer
          </p>
          <p className="mt-1 text-sm text-muted-foreground">{body.reason}</p>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button variant="primary" icon="plus" onClick={onRegister}>
          Register this buyer
        </Button>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        The rest of the room is unaffected. This is a state the product supports on
        purpose, not an error to clear.
      </p>
    </Card>
  )
}

function RegisterForm({ roomId, onDone, onCancel }) {
  const [values, setValues] = useState({
    system: 'salesforce',
    buyer_email: '',
    buyer_name: '',
    account_id: '',
    contact_id: '',
    deal_id: '',
  })
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const set = (field) => (event) => setValues((prev) => ({ ...prev, [field]: event.target.value }))

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await crmReadApi.registerIdentity(values, roomId)
      onDone()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <form className="flex flex-col gap-4" onSubmit={submit}>
        <div>
          <h3 className="font-display text-base font-semibold text-foreground">
            Register a buyer identity
          </h3>
          <p className="mt-1 text-sm text-muted-foreground">
            The room mapping the research names as one of its two identity sources. Leave the
            record ids blank and the room will read by owner instead.
          </p>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="CRM system" id="wf042-system" hint="One of the three researched vendors.">
            <select
              id="wf042-system"
              className={inputClass}
              value={values.system}
              onChange={set('system')}
            >
              <option value="salesforce">Salesforce</option>
              <option value="dataverse">Dataverse</option>
              <option value="hubspot">HubSpot</option>
            </select>
          </Field>
          <Field label="Buyer email" id="wf042-email" hint="The room-scoped key the read is filtered by.">
            <input
              id="wf042-email"
              className={inputClass}
              value={values.buyer_email}
              onChange={set('buyer_email')}
              autoComplete="off"
            />
          </Field>
          <Field label="Buyer name" id="wf042-name">
            <input id="wf042-name" className={inputClass} value={values.buyer_name} onChange={set('buyer_name')} />
          </Field>
          <Field label="Owner id" id="wf042-owner" hint="Read by owner when no record id is known.">
            <input id="wf042-owner" className={inputClass} value={values.owner_id || ''} onChange={set('owner_id')} />
          </Field>
          <Field label="Deal id" id="wf042-deal">
            <input id="wf042-deal" className={inputClass} value={values.deal_id} onChange={set('deal_id')} />
          </Field>
          <Field label="Contact id" id="wf042-contact">
            <input id="wf042-contact" className={inputClass} value={values.contact_id} onChange={set('contact_id')} />
          </Field>
          <Field label="Account id" id="wf042-account">
            <input id="wf042-account" className={inputClass} value={values.account_id} onChange={set('account_id')} />
          </Field>
        </div>
        {error ? <ErrorNote error={error} /> : null}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" icon="plus" disabled={busy}>
            {busy ? 'Registering' : 'Register'}
          </Button>
          <Button variant="ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
        </div>
      </form>
    </Card>
  )
}

function QueryLog({ rows }) {
  return (
    <DataTable
      rows={rows}
      rowKey={(row) => row.id}
      empty="No read has been issued for this room yet."
      columns={[
        {
          key: 'object',
          header: 'Object',
          render: (row) => <span className="font-mono text-[13px]">{row.vendor_name}</span>,
        },
        {
          key: 'plan',
          header: 'Query sent',
          render: (row) => (
            <span className="font-mono text-[11px] break-all text-muted-foreground">
              {planSummary(row)}
            </span>
          ),
        },
        {
          key: 'rows',
          header: 'Rows',
          render: (row) => (
            <span className="font-mono text-[13px]">
              {row.returned} of {row.total}
            </span>
          ),
        },
        {
          key: 'done',
          header: 'Page',
          render: (row) => (
            <Badge tone={row.done ? 'insert' : 'restore'}>{row.done ? 'last page' : 'continued'}</Badge>
          ),
        },
      ]}
    />
  )
}

export default function CrmReadPanel() {
  const [roomId, setRoomId] = useState(lastRoom)
  const [nonce, setNonce] = useState(0)
  const [registering, setRegistering] = useState(false)

  useEffect(() => {
    try {
      window.localStorage.setItem(ROOM_STORAGE_KEY, roomId)
    } catch {
      // A room the browser will not remember is still a room this page can read.
    }
  }, [roomId])

  const rooms = useAsync(() => crmReadApi.rooms(), [])
  const vocabulary = useAsync(() => crmReadApi.vocabulary(), [])
  const panel = useAsync(
    () => (roomId ? crmReadApi.panel(roomId, { refresh: true }) : Promise.resolve(null)),
    [roomId, nonce]
  )
  const queries = useAsync(
    () => (roomId ? crmReadApi.queries(roomId, { limit: 50 }) : Promise.resolve({ queries: [] })),
    [roomId, nonce]
  )
  const cache = useAsync(
    () => (roomId ? crmReadApi.cache(roomId) : Promise.resolve(null)),
    [roomId, nonce]
  )
  const identities = useAsync(
    () => (roomId ? crmReadApi.identities({ room_id: roomId }) : Promise.resolve({ identities: [] })),
    [roomId, nonce]
  )

  const refresh = useCallback(() => setNonce((value) => value + 1), [])

  const pull = useCallback(async () => {
    if (!roomId) return
    await crmReadApi.pull(roomId, {})
    refresh()
  }, [roomId, refresh])

  if (rooms.loading || vocabulary.loading) {
    return <Spinner label="Loading the CRM read panel" />
  }
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />

  const roomOptions = (rooms.data?.records || []).map((room) => ({
    value: room.id,
    label: `${room.data?.name || 'Room'} - ${room.id}`,
  }))

  const body = panel.data
  const noContext = body && body.crm_context === false
  const summary = cache.data
  const logged = queries.data?.queries || []

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="font-display text-2xl font-semibold text-foreground">CRM read panel</h1>
          <p className="mt-1 max-w-3xl text-[15px] text-muted-foreground">
            The room resolves which CRM records a buyer belongs to, issues a read-only
            field-scoped paged read, normalises the paged response into one view model,
            labels every option value, and caches the panel per buyer.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button icon="refresh" onClick={refresh}>
            Reload
          </Button>
          <Button
            variant="primary"
            icon="plus"
            onClick={pull}
            disabled={!roomId || noContext}
            title={noContext ? 'This room has no CRM identity to read' : 'Force a read'}
          >
            Force read
          </Button>
        </div>
      </header>

      <Card>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Field label="Room" id="wf042-room" hint="Pick a room, or paste an id.">
            <select
              id="wf042-room"
              className={inputClass}
              value={roomOptions.some((option) => option.value === roomId) ? roomId : ''}
              onChange={(event) => setRoomId(event.target.value)}
            >
              <option value="">Choose a room</option>
              {roomOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Room id" id="wf042-room-id" hint="Used when the room is not in the list.">
            <input
              id="wf042-room-id"
              className={inputClass}
              value={roomId}
              onChange={(event) => setRoomId(event.target.value)}
              autoComplete="off"
            />
          </Field>
          <Field label="Identities" id="wf042-identities" hint="Buyers this room can read for.">
            <input
              id="wf042-identities"
              className={inputClass}
              value={String(identities.data?.count ?? 0)}
              readOnly
            />
          </Field>
        </div>
      </Card>

      {!roomId ? (
        <EmptyState
          title="Choose a room"
          description="The deal panel is scoped to a room, because that is where the research puts it: a buyer opens a room and the room reads the records that buyer belongs to."
        />
      ) : null}

      {roomId && panel.loading ? <Spinner label="Reading the CRM" /> : null}
      {roomId && panel.error ? <ErrorNote error={panel.error} onRetry={refresh} /> : null}

      {roomId && body && noContext ? (
        <>
          <NoContextPanel body={body} onRegister={() => setRegistering(true)} />
          {registering ? (
            <RegisterForm
              roomId={roomId}
              onDone={() => {
                setRegistering(false)
                refresh()
              }}
              onCancel={() => setRegistering(false)}
            />
          ) : null}
        </>
      ) : null}

      {roomId && body && body.crm_context ? (
        <>
          <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label="Identities"
              value={String(identities.data?.count ?? 0)}
              hint="buyers this room can read for"
              icon="rooms"
            />
            <StatCard
              label="Reads issued"
              value={String(logged.length)}
              hint="read plans, newest first"
              icon="audit"
            />
            <StatCard
              label="Cache"
              value={summary ? String(summary.fresh ?? 0) : '-'}
              hint="fresh panels right now"
              icon="database"
            />
            <StatCard
              label="Outcome"
              value={body.outcome}
              hint={outcomeNote(body.outcome)}
              icon="schema"
            />
          </section>

          <CachePanel cache={body.cache} />

          <Section
            title="Deal panel"
            hint="The five fields the user flow names: deal name, stage, amount, primary contact, account industry. Both the label and the stored value are shown, because a seller recognises the code and a buyer reads the label."
            action={
              <Badge tone={body.display_labels ? 'insert' : 'restore'}>
                labels from {body.label_source}
              </Badge>
            }
          >
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
              <PanelPart
                title="Deal"
                part={body.panel.deal}
                glyph="read"
                idValue={body.reads?.deal?.read_set?.length ? 'the mapped columns' : ''}
                nameValue={body.panel.deal.name}
                extra={
                  <>
                    <Fact term="Stage">
                      <OptionValue option={body.panel.deal.stage} labelled={body.display_labels} />
                    </Fact>
                    <Fact term="Amount" mono>
                      {body.panel.deal.amount === null || body.panel.deal.amount === undefined
                        ? '-'
                        : body.panel.deal.amount}
                    </Fact>
                  </>
                }
              />
              <PanelPart
                title="Primary contact"
                part={body.panel.contact}
                glyph="fieldmap"
                nameValue={body.panel.contact.name}
                extra={
                  <>
                    <Fact term="Title">{body.panel.contact.title || '-'}</Fact>
                    <Fact term="Email" mono>
                      {body.panel.contact.email || '-'}
                    </Fact>
                  </>
                }
              />
              <PanelPart
                title="Account"
                part={body.panel.account}
                glyph="fieldmap"
                nameValue={body.panel.account.name}
                extra={
                  <>
                    <Fact term="Industry">
                      <OptionValue
                        option={body.panel.account.industry}
                        labelled={body.display_labels}
                      />
                    </Fact>
                    <Fact term="Fields read" mono>
                      {(body.reads?.account?.read_set || []).length}
                    </Fact>
                  </>
                }
              />
            </div>
            {isUnlabelled(body.panel.deal.stage) || isUnlabelled(body.panel.account.industry) ? (
              <p className="text-xs text-muted-foreground">
                A value marked unlabelled has no label from the vendor and none in this room
                option sets. Register one and the next read shows the label instead.
              </p>
            ) : null}
          </Section>

          <Section
            title="Read set, derived from the field map"
            hint="The research's extensibility claim: the read set comes from the same field map the writes use, so a deployment that adds a field reaches this panel with no extra API code."
          >
            <DataTable
              rows={body.panel.read_set || []}
              rowKey={(row) => `${row.object}.${row.crm_field}`}
              empty="This identity carries no field map."
              columns={[
                {
                  key: 'object',
                  header: 'Object',
                  render: (row) => <span className="font-mono text-[13px]">{row.object}</span>,
                },
                {
                  key: 'crm',
                  header: 'CRM column',
                  render: (row) => <span className="font-mono text-[13px]">{row.crm_field}</span>,
                },
                {
                  key: 'room',
                  header: 'Room field',
                  render: (row) => <span className="font-mono text-[13px]">{row.room_field}</span>,
                },
              ]}
            />
          </Section>

          <Section
            title="Read query log"
            hint="One row per read plan issued, carrying the exact SOQL, the OData query options or the HubSpot body the vendor would have received. A read path nobody can account for is indistinguishable from one that never ran."
          >
            <QueryLog rows={logged} />
          </Section>

          <Section title="Response" hint="The panel as the API returned it, for a reader who wants the whole shape.">
            <Card>
              <JsonView value={body} depth={0} />
            </Card>
          </Section>
        </>
      ) : null}

      <Section
        title="Vocabulary"
        hint="Every published value, served as data. The limits are the vendor's own numbers and each is the sentence that fixes it."
      >
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          {Object.entries(vocabulary.data?.limits || {}).map(([system, limits]) => (
            <Card key={system}>
              <div className="flex items-center justify-between gap-3">
                <p className="font-mono text-sm font-semibold text-foreground">{system}</p>
                <Badge tone={vocabulary.data?.display_labels?.[system] ? 'insert' : 'neutral'}>
                  {vocabulary.data?.display_labels?.[system] ? 'annotates labels' : 'no label API'}
                </Badge>
              </div>
              <dl className="mt-3 grid grid-cols-1 gap-2">
                {Object.entries(limits).map(([name, value]) => (
                  <Fact key={name} term={name.replace(/_/g, ' ')} mono>
                    {String(value)}
                  </Fact>
                ))}
              </dl>
            </Card>
          ))}
        </div>
      </Section>

      <p className="flex items-center gap-2 text-xs text-muted-foreground">
        <Glyph name="fieldmap" size={14} />
        Every write on this page is audited against the route that served it. A room id is
        remembered in this browser only.
      </p>
    </div>
  )
}