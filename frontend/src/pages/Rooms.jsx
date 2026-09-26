import { useMemo, useState } from 'react'
import { api, accessApi, relativeTime } from '../lib/api'
import { ShareDialog } from '../components/ShareDialog'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  JsonView,
  Spinner,
  inputClass,
  useAsync,
} from '../components/ui'

/**
 * Sales rooms: the primary buyer-facing entity.
 *
 * Creating a room here writes through the audited store, so the change is
 * visible in the audit log immediately. That round trip is the point of the
 * page, not a side effect.
 *
 * The Share action (WF-004) hangs off each room, and whether it is rendered at
 * all depends on the acting user's role in that room: the research states that
 * viewers have no Share button. The role comes from the server, not from
 * anything this page decides.
 */
export default function Rooms() {
  const rooms = useAsync(() => api.listRecords('room', { limit: 100 }), [])
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({ name: '', account: '', stage: 'discovery' })
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)
  const [selected, setSelected] = useState(null)
  const [sharing, setSharing] = useState(null)
  const [actor, setActor] = useState(null)

  const records = rooms.data?.records || []

  /**
   * Who the console user is acting as. There is no authentication in this
   * slice, so the identity is explicit rather than implied: the server resolves
   * it against the room's owner field and its access grants, and refuses
   * anything that role does not permit.
   */
  const identities = useMemo(() => {
    const owners = records.map((room) => room.data?.owner).filter(Boolean)
    const seen = new Set()
    return [...owners, ...['dana', 'sam']].filter(
      (name) => name && !seen.has(name) && seen.add(name),
    )
  }, [records])

  const effectiveActor = actor || identities[0] || null

  // One snapshot per room, so each card knows whether to offer Share. The
  // endpoint is the only source of truth about who may share.
  const capabilities = useAsync(async () => {
    if (!records.length || !effectiveActor) return {}
    const pairs = await Promise.all(
      records.map(async (room) => {
        try {
          const snapshot = await accessApi.snapshot(room.id, effectiveActor)
          return [room.id, snapshot.actor]
        } catch {
          return [room.id, { can_share: false }]
        }
      }),
    )
    return Object.fromEntries(pairs)
  }, [records.map((room) => room.id).join(','), effectiveActor])

  async function createRoom(event) {
    event.preventDefault()
    setSaving(true)
    setSaveError(null)
    try {
      // Payload shape is intentionally open: any field the server has never
      // seen is stored and indexed without a migration.
      await api.createRecord('room', {
        name: form.name,
        account: form.account,
        stage: form.stage,
        branding: { theme: 'dark', accent: '#22c55e' },
        integrations: ['salesforce'],
      })
      setForm({ name: '', account: '', stage: 'discovery' })
      setCreating(false)
      rooms.refetch()
    } catch (error) {
      setSaveError(error)
    } finally {
      setSaving(false)
    }
  }

  async function advanceStage(room) {
    const next = room.data.stage === 'discovery' ? 'evaluation' : 'negotiation'
    await api.updateRecord('room', room.id, { stage: next })
    rooms.refetch()
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-mono text-2xl font-semibold">Sales rooms</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Each room is a schema-flexible record; every edit is audited.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          {identities.length > 0 && (
            <Field label="Viewing as" id="room-actor" hint="Decides who can share a room.">
              <select
                id="room-actor"
                className={`${inputClass} min-w-44`}
                value={effectiveActor || ''}
                onChange={(event) => setActor(event.target.value)}
              >
                {identities.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </Field>
          )}
          <Button
            icon="plus"
            variant={creating ? 'ghost' : 'primary'}
            onClick={() => setCreating((value) => !value)}
          >
            {creating ? 'Cancel' : 'New room'}
          </Button>
        </div>
      </header>

      {creating && (
        <Card>
          <form onSubmit={createRoom} className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="Room name" id="room-name" hint="Shown to the buyer.">
                <input
                  id="room-name"
                  className={inputClass}
                  required
                  value={form.name}
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                />
              </Field>
              <Field label="Account" id="room-account">
                <input
                  id="room-account"
                  className={inputClass}
                  value={form.account}
                  onChange={(event) => setForm({ ...form, account: event.target.value })}
                />
              </Field>
              <Field label="Stage" id="room-stage">
                <select
                  id="room-stage"
                  className={inputClass}
                  value={form.stage}
                  onChange={(event) => setForm({ ...form, stage: event.target.value })}
                >
                  {['discovery', 'evaluation', 'negotiation', 'closed'].map((stage) => (
                    <option key={stage} value={stage}>
                      {stage}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            {saveError && <ErrorNote error={saveError} />}
            <Button type="submit" variant="primary" disabled={saving}>
              {saving ? 'Creating…' : 'Create room'}
            </Button>
          </form>
        </Card>
      )}

      {rooms.loading && <Spinner label="Loading rooms" />}
      {rooms.error && <ErrorNote error={rooms.error} onRetry={rooms.refetch} />}

      {!rooms.loading && !rooms.error && (
        <>
          {(rooms.data?.records || []).length === 0 ? (
            <EmptyState
              title="No sales rooms yet"
              description="Create the first room to see it appear here and in the audit log."
              action={
                <Button icon="plus" variant="primary" onClick={() => setCreating(true)}>
                  New room
                </Button>
              }
            />
          ) : (
            <ul className="grid gap-4 md:grid-cols-2">
              {rooms.data.records.map((room) => (
                <li key={room.id}>
                  <Card className="card-hover h-full">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <h2 className="truncate font-mono text-base font-semibold text-foreground">
                          {room.data.name || 'Untitled room'}
                        </h2>
                        <p className="mt-0.5 truncate text-sm text-muted-foreground">
                          {room.data.account || 'No account set'}
                        </p>
                      </div>
                      <Badge tone={room.data.stage === 'closed' ? 'restore' : 'insert'}>
                        {room.data.stage || 'unset'}
                      </Badge>
                    </div>

                    <dl className="mt-4 grid grid-cols-2 gap-3 text-xs">
                      <div>
                        <dt className="text-muted-foreground">Revision</dt>
                        <dd className="font-mono text-foreground">{room.revision}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Updated</dt>
                        <dd className="font-mono text-foreground">{relativeTime(room.updated_at)}</dd>
                      </div>
                    </dl>

                    <div className="mt-4 flex flex-wrap gap-2">
                      <Button onClick={() => setSelected(selected === room.id ? null : room.id)}>
                        {selected === room.id ? 'Hide payload' : 'View payload'}
                      </Button>
                      {/* Only rendered when the server says this person can
                          share the room: viewers have no Share action. */}
                      {capabilities.data?.[room.id]?.can_share && (
                        <Button icon="share" variant="primary" onClick={() => setSharing(room)}>
                          Share
                        </Button>
                      )}
                      {room.data.stage !== 'closed' && (
                        <Button icon="chevron" onClick={() => advanceStage(room)}>
                          Advance stage
                        </Button>
                      )}
                    </div>

                    {selected === room.id && (
                      <div className="mt-4 rounded-lg border border-border-subtle/25 bg-background/40 p-3">
                        <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                          Stored payload (schema-flexible)
                        </p>
                        <JsonView value={room.data} />
                      </div>
                    )}
                  </Card>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {sharing && (
        <ShareDialog room={sharing} actor={effectiveActor} onClose={() => setSharing(null)} />
      )}
    </div>
  )
}
