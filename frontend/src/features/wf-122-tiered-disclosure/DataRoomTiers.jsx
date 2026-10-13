import { useState } from 'react'

import { BELOW_TIER_NOTES, conflictText, listRooms, tiersApi } from './api'
import { DisclosureBadge, Notice, StateBadge, TierChip } from './primitives'
import {
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  StatCard,
  Spinner,
  inputClass,
  useAsync,
} from '@/components/ui'

/**
 * WF-122: the seller's page for a tiered data room.
 *
 * What the page is shaped around
 * ------------------------------
 *
 * A tiered data room is one room seen at several levels. So the page's first job
 * is to show the room tree with each section's minimum tier and its below-tier
 * state, and then to show what a viewer at a given level actually receives.
 *
 * Three things get deliberate treatment rather than a generic list row:
 *
 *   1. **The preview is the server's response, not a client-side filter.** The
 *      page asks `/rooms/{id}/view` for a tier, so what a seller sees when they
 *      preview is exactly what that viewer's room render contains. A hidden
 *      section is absent from that response, not greyed out.
 *   2. **An unlock is verified by the server.** The unlock route writes and reads
 *      the change back before it reports success, so a 409 here means the change
 *      did not land. The page says so with the server's own reason token rather
 *      than an English sentence it guessed.
 *   3. **A link is shown once.** The access token is returned only at creation
 *      and rotation, so the page shows it once and says so.
 *
 * Loading, error and empty states are all present, as the design system requires.
 */

async function loadBoard(roomId) {
  const [sections, recipients, events, policy, summary, requests] = await Promise.all([
    tiersApi.sections(roomId),
    tiersApi.recipients(roomId),
    tiersApi.events(roomId),
    tiersApi.policy(roomId),
    tiersApi.summary(roomId),
    tiersApi.requests(roomId),
  ])
  return {
    sections: sections.sections || [],
    recipients: recipients.recipients || [],
    events: events.events || [],
    policy,
    summary,
    requests: requests.requests || [],
  }
}

function StatRow({ summary }) {
  if (!summary) return null
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard label="Sections" value={summary.sections} hint="In the room tree" />
      <StatCard label="Counterparty links" value={summary.recipients} hint="One per organisation" />
      <StatCard
        label="Unlocked"
        value={summary.unlocked}
        hint="Above the curated room"
      />
      <StatCard
        label="Closed"
        value={Number(summary.revoked || 0) + Number(summary.expired || 0)}
        hint={`${summary.revoked} revoked, ${summary.expired} expired`}
      />
    </div>
  )
}

export default function DataRoomTiers() {
  const rooms = useAsync(() => listRooms(), [])
  const roomList = (rooms.data?.records || []).map((record) => ({
    id: record.id,
    name: record.data?.name || record.id,
  }))

  const [pickedRoomId, setPickedRoomId] = useState('')
  const roomId = pickedRoomId || roomList[0]?.id || ''

  const board = useAsync(() => (roomId ? loadBoard(roomId) : Promise.resolve(null)), [roomId])
  const [previewTier, setPreviewTier] = useState(0)
  const preview = useAsync(
    () => (roomId ? tiersApi.preview(roomId, previewTier) : Promise.resolve(null)),
    [roomId, previewTier],
  )

  const [focusRecipientId, setFocusRecipientId] = useState('')
  const recipientId = focusRecipientId || board.data?.recipients?.[0]?.id || ''
  const recipientView = useAsync(
    () => (recipientId ? tiersApi.recipientView(recipientId) : Promise.resolve(null)),
    [recipientId],
  )

  const [sectionName, setSectionName] = useState('')
  const [sectionTier, setSectionTier] = useState('1')
  const [sectionBelow, setSectionBelow] = useState('teaser')
  const [sectionTeaser, setSectionTeaser] = useState('')
  const [org, setOrg] = useState('')
  const [orgTier, setOrgTier] = useState('0')
  const [link, setLink] = useState(null)
  const [problem, setProblem] = useState(null)
  const [busy, setBusy] = useState(false)

  async function run(action) {
    setBusy(true)
    setProblem(null)
    try {
      const result = await action()
      await board.refetch()
      return result
    } catch (error) {
      setProblem(error)
      return null
    } finally {
      setBusy(false)
    }
  }

  if (rooms.loading) return <Spinner label="Loading rooms" />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />
  if (roomList.length === 0) {
    return (
      <EmptyState
        title="No rooms yet"
        description="Seed the demo data to create a room, then assign tiers to its sections."
      />
    )
  }

  if (board.loading) return <Spinner label="Loading the room tree" />
  if (board.error) return <ErrorNote error={board.error} onRetry={board.refetch} />

  const data = board.data || { sections: [], recipients: [], events: [], requests: [], policy: {} }
  const labels = data.policy?.tier_labels || {}
  const tierCount = Math.max(
    3,
    ...data.sections.map((section) => Number(section.min_tier) || 0),
    ...data.recipients.map((recipient) => Number(recipient.tier) || 0),
  )

  async function addSection(event) {
    event.preventDefault()
    const created = await run(() =>
      tiersApi.createSection(roomId, {
        name: sectionName,
        min_tier: Number(sectionTier) || 0,
        below_tier: sectionBelow,
        teaser: sectionTeaser,
      }),
    )
    if (created) {
      setSectionName('')
      setSectionTeaser('')
    }
  }

  async function addRecipient(event) {
    event.preventDefault()
    const created = await run(() =>
      tiersApi.createRecipient(roomId, { org, tier: Number(orgTier) || 0 }),
    )
    if (created) {
      setOrg('')
      setLink({ org: created.org, path: created.access_path })
    }
  }

  async function reissue(recipient) {
    const rotated = await run(() => tiersApi.rotate(recipient.id))
    if (rotated) setLink({ org: rotated.org, path: rotated.access_path })
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold text-foreground">Tiered disclosure</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Every recipient starts at the curated room. Unlock financials, legal or security
            for one counterparty as diligence progresses. Below-tier sections show a teaser,
            an on-request marker, or nothing at all.
          </p>
        </div>
        <Field label="Room" id="wf122-room">
          <select
            id="wf122-room"
            className={inputClass}
            value={roomId}
            onChange={(event) => setPickedRoomId(event.target.value)}
          >
            {roomList.map((room) => (
              <option key={room.id} value={room.id}>
                {room.name}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <StatRow summary={data.summary} />

      {problem && (
        <Notice tone="destructive" title="That change was refused">
          {problem.reason ? conflictText(problem.reason) : problem.message}
        </Notice>
      )}

      {link && (
        <Notice
          tone="info"
          title={`Access link for ${link.org}`}
          action={
            <Button variant="secondary" onClick={() => setLink(null)}>
              Dismiss
            </Button>
          }
        >
          <p>
            This link is shown once. Copy it now; only a hash is stored, so it cannot be shown
            again.
          </p>
          <p className="mt-1 font-mono text-xs break-all text-foreground">{link.path}</p>
        </Notice>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <h2 className="text-lg font-semibold text-foreground">Room tree</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            A section at tier 0 is the curated room. A higher tier is revealed by unlocking a
            link or by granting this one section.
          </p>

          {data.sections.length === 0 ? (
            <p className="mt-4 text-sm text-muted-foreground">
              This room has no sections yet. Add one to start the tree.
            </p>
          ) : (
            <ul className="mt-4 divide-y divide-border-subtle">
              {data.sections.map((section) => (
                <li key={section.id} className="py-3">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-foreground">{section.name}</p>
                      <p className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                        <TierChip tier={section.min_tier} label={labels[String(section.min_tier)]} />
                        <span>below tier: {section.below_tier}</span>
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {BELOW_TIER_NOTES[section.below_tier]}
                      </p>
                    </div>
                    <div className="flex shrink-0 flex-wrap gap-2">
                      <Button
                        variant="secondary"
                        disabled={busy || !recipientId}
                        onClick={() => run(() => tiersApi.grant(section.id, recipientId))}
                      >
                        Grant
                      </Button>
                      <Button
                        variant="ghost"
                        disabled={busy || !recipientId}
                        onClick={() => run(() => tiersApi.requestAccess(section.id, recipientId))}
                      >
                        Request
                      </Button>
                      <Button
                        variant="ghost"
                        disabled={busy}
                        onClick={() => run(() => tiersApi.deleteSection(section.id))}
                      >
                        Delete
                      </Button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}

          <form className="mt-5 space-y-3" onSubmit={addSection}>
            <h3 className="text-base font-semibold text-foreground">Add a section</h3>
            <Field label="Section name" id="wf122-section-name">
              <input
                id="wf122-section-name"
                className={inputClass}
                value={sectionName}
                onChange={(event) => setSectionName(event.target.value)}
                required
              />
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Minimum tier" id="wf122-section-tier">
                <input
                  id="wf122-section-tier"
                  type="number"
                  min="0"
                  className={inputClass}
                  value={sectionTier}
                  onChange={(event) => setSectionTier(event.target.value)}
                />
              </Field>
              <Field label="Below tier" id="wf122-section-below">
                <select
                  id="wf122-section-below"
                  className={inputClass}
                  value={sectionBelow}
                  onChange={(event) => setSectionBelow(event.target.value)}
                >
                  <option value="teaser">Teaser</option>
                  <option value="on_request">On request</option>
                  <option value="hidden">Hidden</option>
                </select>
              </Field>
            </div>
            <Field
              label="Teaser"
              id="wf122-section-teaser"
              hint="Shown only when this section is below a viewer's tier and its below-tier state is teaser."
            >
              <input
                id="wf122-section-teaser"
                className={inputClass}
                value={sectionTeaser}
                onChange={(event) => setSectionTeaser(event.target.value)}
              />
            </Field>
            <Button type="submit" variant="primary" disabled={busy}>
              Add section
            </Button>
          </form>
        </Card>

        <Card>
          <h2 className="text-lg font-semibold text-foreground">Counterparty links</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            One link per organisation. A link is issued once, revoked when the process ends,
            and expires on its own date.
          </p>

          {data.recipients.length === 0 ? (
            <p className="mt-4 text-sm text-muted-foreground">No links issued yet.</p>
          ) : (
            <ul className="mt-4 divide-y divide-border-subtle">
              {data.recipients.map((recipient) => (
                <RecipientRow
                  key={recipient.id}
                  recipient={recipient}
                  labels={labels}
                  tierCount={tierCount}
                  busy={busy}
                  onUnlock={(tier) => run(() => tiersApi.unlock(recipient.id, tier))}
                  onRevoke={() => run(() => tiersApi.revoke(recipient.id))}
                  onReissue={() => reissue(recipient)}
                />
              ))}
            </ul>
          )}

          <form className="mt-5 space-y-3" onSubmit={addRecipient}>
            <h3 className="text-base font-semibold text-foreground">Issue a link</h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Organisation" id="wf122-org">
                <input
                  id="wf122-org"
                  className={inputClass}
                  value={org}
                  onChange={(event) => setOrg(event.target.value)}
                  required
                />
              </Field>
              <Field label="Starting tier" id="wf122-org-tier">
                <input
                  id="wf122-org-tier"
                  type="number"
                  min="0"
                  className={inputClass}
                  value={orgTier}
                  onChange={(event) => setOrgTier(event.target.value)}
                />
              </Field>
            </div>
            <Button type="submit" variant="primary" disabled={busy}>
              Issue link
            </Button>
          </form>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <h2 className="text-lg font-semibold text-foreground">Preview a tier</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            What a viewer at this tier receives. Hidden sections are absent from this response.
          </p>
          <div className="mt-3 max-w-xs">
            <Field label="Preview tier" id="wf122-preview-tier">
              <input
                id="wf122-preview-tier"
                type="number"
                min="0"
                className={inputClass}
                value={previewTier}
                onChange={(event) => setPreviewTier(Number(event.target.value) || 0)}
              />
            </Field>
          </div>
          {preview.loading ? (
            <Spinner label="Loading the preview" />
          ) : (
            <PreviewList rows={preview.data?.sections || []} />
          )}
        </Card>

        <Card>
          <h2 className="text-lg font-semibold text-foreground">View as a counterparty</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            The same response one link receives, with that link's tier and section grants.
          </p>
          <div className="mt-3 max-w-xs">
            <Field label="Counterparty" id="wf122-view-as">
              <select
                id="wf122-view-as"
                className={inputClass}
                value={recipientId}
                onChange={(event) => setFocusRecipientId(event.target.value)}
              >
                {data.recipients.map((recipient) => (
                  <option key={recipient.id} value={recipient.id}>
                    {recipient.org}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          {recipientView.loading ? (
            <Spinner label="Loading the counterparty view" />
          ) : recipientId ? (
            <>
              {recipientView.data?.state && recipientView.data.state !== 'active' && (
                <div className="mt-3">
                  <Notice tone="warning" title="This link is closed">
                    {recipientView.data.state === 'revoked'
                      ? 'It was revoked, so it discloses nothing.'
                      : 'It expired, so it discloses nothing.'}
                  </Notice>
                </div>
              )}
              <PreviewList rows={recipientView.data?.sections || []} />
            </>
          ) : (
            <p className="mt-4 text-sm text-muted-foreground">Issue a link to preview it.</p>
          )}
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <h2 className="text-lg font-semibold text-foreground">Access requests</h2>
          {data.requests.length === 0 ? (
            <p className="mt-3 text-sm text-muted-foreground">
              No counterparty has asked for an on-request section.
            </p>
          ) : (
            <ul className="mt-3 divide-y divide-border-subtle">
              {data.requests.map((request) => (
                <li key={request.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                  <span className="font-mono text-xs text-muted-foreground">{request.section_id}</span>
                  <span>{request.state}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <h2 className="text-lg font-semibold text-foreground">Disclosure log</h2>
          {data.events.length === 0 ? (
            <p className="mt-3 text-sm text-muted-foreground">
              No unlock, revoke or grant has happened yet.
            </p>
          ) : (
            <ul className="mt-3 divide-y divide-border-subtle">
              {data.events.map((event) => (
                <li key={event.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                  <span className="font-mono text-xs text-muted-foreground">{event.kind}</span>
                  <span className="font-mono text-xs text-muted-foreground">{event.at}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card>
        <h2 className="text-lg font-semibold text-foreground">Note on what is not built</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          This room stores records, not files, so a section carries document names rather than
          uploads. CRM-driven room creation, page-level named-recipient access and section
          version history are recorded as out of scope in the feature's vocabulary route.
        </p>
      </Card>
    </div>
  )
}

function RecipientRow({ recipient, labels, tierCount, busy, onUnlock, onRevoke, onReissue }) {
  const [tier, setTier] = useState(recipient.tier)
  const active = recipient.effective_state === 'active'
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 py-3">
      <div className="min-w-0">
        <p className="text-sm font-medium text-foreground">{recipient.org}</p>
        <p className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <TierChip tier={recipient.tier} label={labels[String(recipient.tier)]} />
          <StateBadge state={recipient.effective_state} />
          {recipient.token_hint && <span className="font-mono">ends {recipient.token_hint}</span>}
          {recipient.expires_at && (
            <span className="font-mono">expires {recipient.expires_at}</span>
          )}
        </p>
      </div>
      <div className="flex shrink-0 flex-wrap items-end gap-2">
        <Field label="Unlock to" id={`wf122-unlock-${recipient.id}`}>
          <select
            id={`wf122-unlock-${recipient.id}`}
            className={inputClass}
            value={tier}
            disabled={!active}
            onChange={(event) => setTier(Number(event.target.value) || 0)}
          >
            {Array.from({ length: tierCount + 1 }, (_, value) => (
              <option key={value} value={value}>
                Tier {value}
                {labels[String(value)] ? ` / ${labels[String(value)]}` : ''}
              </option>
            ))}
          </select>
        </Field>
        <Button variant="secondary" disabled={busy || !active} onClick={() => onUnlock(tier)}>
          Unlock
        </Button>
        <Button variant="ghost" disabled={busy || !active} onClick={onRevoke}>
          Revoke
        </Button>
        <Button variant="ghost" disabled={busy} onClick={onReissue}>
          Re-issue link
        </Button>
      </div>
    </li>
  )
}

function PreviewList({ rows }) {
  if (rows.length === 0) {
    return <p className="mt-4 text-sm text-muted-foreground">This viewer sees no sections.</p>
  }
  return (
    <ul className="mt-4 divide-y divide-border-subtle">
      {rows.map((row) => (
        <li key={row.id} className="flex flex-wrap items-start justify-between gap-3 py-3">
          <div className="min-w-0">
            <p className="text-sm font-medium text-foreground">{row.name}</p>
            {row.teaser && <p className="mt-0.5 text-xs text-muted-foreground">{row.teaser}</p>}
            {row.message && <p className="mt-0.5 text-xs text-muted-foreground">{row.message}</p>}
            {row.summary && <p className="mt-0.5 text-xs text-muted-foreground">{row.summary}</p>}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {row.granted && <span className="text-xs text-accent">granted</span>}
            <DisclosureBadge disclosure={row.disclosure} />
          </div>
        </li>
      ))}
    </ul>
  )
}
