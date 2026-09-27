import { useMemo, useState } from 'react'

import { Badge, Button, Card, EmptyState, ErrorNote, Field, JsonView, Spinner, inputClass, useAsync } from '@/components/ui'

import { roomTemplatesApi } from './api'
import { Action, ChoiceCard, Glyph, Stepper } from './primitives'

/**
 * WF-001: create a Digital Sales Room from an account and a template, and see the
 * rooms that exist.
 *
 * The three-step wizard is the researched flow exactly: select the account, which
 * "determines the team members and contacts available to invite"; select a
 * template, "use the pre-configured DSR template to get started; you can customise
 * the layout later"; then name the room, optionally give it a Friendly URL, and
 * save. The list below is the researched Rooms list: a search box and a *Status*
 * filter that shows one status at a time and defaults to Active.
 *
 * Why this is a feature page and not the core rooms page: the research puts this
 * wizard on the Launchpad console's Rooms page, and the branch rewrote
 * `frontend/src/pages/Rooms.jsx` to put it there. That file is core, so a feature
 * may not own it. Putting a *New room* affordance on the core rooms page instead
 * is a one-line change in a file this feature may not touch, and it belongs to
 * whoever owns that page. See the module docstring in
 * `backend/dsr/features/wf001_rooms.py`.
 */

const STEPS = [
  { id: 'account', label: 'Account' },
  { id: 'template', label: 'Template' },
  { id: 'details', label: 'Name and URL' },
]

const STATUSES = [
  { value: 'active', label: 'Active' },
  { value: 'archived', label: 'Archived' },
  { value: 'all', label: 'All' },
]

const LAST_STEP = STEPS.length - 1

/**
 * Which step a refusal belongs to, keyed by the HTTP status the server answers
 * with. The shared `apiRequest` keeps the status and the server's operator-facing
 * message, and deliberately does not hand over the machine-readable code, because
 * recovering that would mean either editing `lib/api.js` - refused - or reaching
 * around the client with a raw `fetch`, which is the same coupling wearing a
 * disguise. The status is enough, and here is why:
 *
 * * **404** is `account_not_found`, and it is the only 404 this route produces. It
 *   happens when the account that step 1 offered was deleted in between, so step 1
 *   is where the operator has to go.
 * * **409** is `friendly_url_taken`, a step-3 field.
 * * **400** covers `invalid_name`, `invalid_friendly_url` and
 *   `template_not_found`. Every one of them is a field the operator can see and
 *   fix on step 3, because steps 1 and 2 are both chosen from lists this page
 *   fetched, so a template that is not in the catalogue cannot have been picked
 *   from it.
 *
 * So no refusal can leave the operator on a form that cannot succeed, which is
 * what this mapping buys.
 */
function stepForStatus(status, current) {
  if (status === 404) return 0
  if (status === 409) return 2
  if (status === 400) return 2
  return current
}

/**
 * Preview of the friendly URL the server will derive. Advisory only: the server
 * normalises authoritatively, truncates at its own limit, and adds a numeric
 * suffix on a collision, so this is here to show the shape of the answer and not
 * to promise it.
 */
function previewSlug(value) {
  return value
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

function AccountStep({ accounts, value, onChange }) {
  if (accounts.length === 0) {
    return (
      <EmptyState
        title="No accounts to choose from"
        description="A room is bound to an account, which supplies the team members and contacts available to invite. Add an account record, then start the wizard again."
      />
    )
  }

  return (
    <fieldset className="space-y-3">
      <legend className="text-sm text-muted-foreground">
        The selected account determines the team members and contacts available to invite.
      </legend>
      <div className="grid gap-3">
        {accounts.map((account) => (
          <ChoiceCard
            key={account.id}
            name="wf001-account"
            value={account.id}
            checked={value === account.id}
            onChange={onChange}
            title={account.data.name || account.id}
            description={account.data.domain}
            meta={
              <>
                {account.data.tier && <Badge tone="neutral">{account.data.tier}</Badge>}
                {typeof account.data.member_count === 'number' && (
                  <Badge tone="neutral">{account.data.member_count} members</Badge>
                )}
                {typeof account.data.contact_count === 'number' && (
                  <Badge tone="neutral">{account.data.contact_count} contacts</Badge>
                )}
              </>
            }
          />
        ))}
      </div>
    </fieldset>
  )
}

function TemplateStep({ templates, value, onChange }) {
  return (
    <fieldset className="space-y-3">
      <legend className="text-sm text-muted-foreground">
        Use a pre-configured template to get started; you can customise the layout later.
      </legend>
      <div className="grid gap-3">
        {templates.map((template) => (
          <ChoiceCard
            key={template.template_id}
            name="wf001-template"
            value={template.template_id}
            checked={value === template.template_id}
            onChange={onChange}
            title={template.name || template.template_id}
            description={template.description}
            meta={
              <>
                <Badge tone="neutral">{template.template_id}</Badge>
                <Badge tone="neutral">{template.template_version_id}</Badge>
                {template.source === 'store' && <Badge tone="update">from store</Badge>}
              </>
            }
          />
        ))}
      </div>
    </fieldset>
  )
}

function DetailsStep({ name, friendlyUrl, onName, onFriendlyUrl, onSubmit, saving }) {
  const derived = previewSlug(name)
  const long = derived.length > 64

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <Field label="Room name" id="wf001-name" hint="Shown to the buyer.">
        <input
          id="wf001-name"
          className={inputClass}
          required
          maxLength={200}
          value={name}
          onChange={(event) => onName(event.target.value)}
        />
      </Field>

      <Field
        label="Friendly URL (optional)"
        id="wf001-friendly-url"
        hint={
          long
            ? `The server will shorten this to /${derived.slice(0, 64).replace(/-+$/, '')} and add a number if it is taken.`
            : derived
              ? `Leave blank to use /${derived}. Must be unique across rooms.`
              : 'Lowercase words separated by hyphens, e.g. acme-evaluation.'
        }
      >
        <input
          id="wf001-friendly-url"
          className={inputClass}
          maxLength={64}
          placeholder={derived ? derived.slice(0, 64) : 'acme-evaluation'}
          value={friendlyUrl}
          onChange={(event) => onFriendlyUrl(event.target.value)}
        />
      </Field>

      <Action type="submit" variant="primary" glyph="check" disabled={saving || !name.trim()}>
        {saving ? 'Saving...' : 'Save room'}
      </Action>
      <p className="text-xs text-muted-foreground">
        The room and the site it is bound to are written in one transaction: either both land
        with their audit rows, or neither does.
      </p>
    </form>
  )
}

function CreateWizard({ onCreated, onCancel }) {
  const accounts = useAsync(() => roomTemplatesApi.accounts({ limit: 100 }), [])
  const templates = useAsync(() => roomTemplatesApi.templates(), [])

  const [step, setStep] = useState(0)
  const [accountId, setAccountId] = useState('')
  const [templateId, setTemplateId] = useState('')
  const [name, setName] = useState('')
  const [friendlyUrl, setFriendlyUrl] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const templateList = templates.data?.templates || []
  const accountList = accounts.data?.accounts || []

  // The shipped standard template is preselected once, so the common case is one
  // click from the template step to the name step. A store template that overrides
  // it arrives under the same id and wins, which is the overlay rule.
  const effectiveTemplateId =
    templateId || templateList.find((entry) => entry.template_id === 'tpl_standard')?.template_id || ''

  const selectedAccount = accountList.find((entry) => entry.id === accountId)
  const selectedTemplate = templateList.find((entry) => entry.template_id === effectiveTemplateId)

  const canAdvance = step === 0 ? Boolean(accountId) : Boolean(effectiveTemplateId)

  async function save(event) {
    event.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const room = await roomTemplatesApi.createRoom({
        name: name.trim(),
        account_id: accountId,
        template_id: effectiveTemplateId,
        ...(friendlyUrl.trim() ? { friendly_url: friendlyUrl.trim() } : {}),
      })
      setStep(0)
      setAccountId('')
      setTemplateId('')
      setName('')
      setFriendlyUrl('')
      onCreated(room)
    } catch (caught) {
      // Send the operator back to the step that caused the refusal rather than
      // leaving them on a form that cannot succeed.
      setStep(stepForStatus(caught.status, step))
      setError(caught)
    } finally {
      setSaving(false)
    }
  }

  if (accounts.loading || templates.loading) return <Spinner label="Loading accounts and templates" />
  if (accounts.error) return <ErrorNote error={accounts.error} onRetry={accounts.refetch} />
  if (templates.error) return <ErrorNote error={templates.error} onRetry={templates.refetch} />

  return (
    <Card>
      <div className="space-y-5">
        <Stepper steps={STEPS} current={step} />

        {step === 0 && (
          <AccountStep accounts={accountList} value={accountId} onChange={setAccountId} />
        )}
        {step === 1 && (
          <TemplateStep
            templates={templateList}
            value={effectiveTemplateId}
            onChange={setTemplateId}
          />
        )}
        {step === 2 && (
          <DetailsStep
            name={name}
            friendlyUrl={friendlyUrl}
            onName={setName}
            onFriendlyUrl={setFriendlyUrl}
            onSubmit={save}
            saving={saving}
          />
        )}

        {error && <ErrorNote error={error} />}

        <div className="flex flex-wrap items-center gap-2 border-t border-border-subtle/25 pt-4">
          {step > 0 && (
            <Action glyph="back" onClick={() => setStep((value) => value - 1)} disabled={saving}>
              Back
            </Action>
          )}
          {step < LAST_STEP && (
            <Button
              variant="primary"
              icon="chevron"
              className="[&_svg]:rotate-90"
              onClick={() => setStep((value) => value + 1)}
              disabled={!canAdvance}
            >
              Next
            </Button>
          )}
          <Button variant="ghost" onClick={onCancel} disabled={saving}>
            Cancel
          </Button>
          <p className="text-xs text-muted-foreground">
            {step === 0 &&
              (accountId
                ? `Bound to ${selectedAccount?.data?.name || accountId}`
                : 'Select an account to continue')}
            {step === 1 && selectedTemplate && `Starting from ${selectedTemplate.name}`}
            {step === 2 && 'Name the room, then save.'}
          </p>
        </div>
      </div>
    </Card>
  )
}

function RoomCard({ room, expanded, onToggle }) {
  const data = room.data

  return (
    <Card className="card-hover h-full">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="truncate font-mono text-base font-semibold text-foreground">
            {data.name || 'Untitled room'}
          </h2>
          <p className="mt-0.5 truncate text-sm text-muted-foreground">
            {data.account_name || 'No account bound'}
          </p>
        </div>
        <Badge tone={data.status === 'archived' ? 'restore' : 'insert'}>
          {data.status || 'active'}
        </Badge>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-3 text-xs">
        <div className="min-w-0">
          <dt className="text-muted-foreground">Friendly URL</dt>
          <dd className="truncate font-mono text-foreground">/{data.friendly_url || '-'}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-muted-foreground">Template</dt>
          <dd className="truncate font-mono text-foreground" title={data.template_version_id || ''}>
            {data.template_id || '-'}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-muted-foreground">Site ID</dt>
          <dd className="truncate font-mono text-foreground" title={data.site_id || ''}>
            {data.site_id || '-'}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-muted-foreground">Account</dt>
          <dd className="truncate font-mono text-foreground">{data.account_id || '-'}</dd>
        </div>
      </dl>

      <div className="mt-4">
        <Button onClick={onToggle} aria-expanded={expanded}>
          {expanded ? 'Hide payload' : 'View payload'}
        </Button>
      </div>

      {expanded && (
        <div className="mt-4 rounded-lg border border-border-subtle/25 bg-background/40 p-3">
          <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Stored payload (schema-flexible)
          </p>
          <JsonView value={data} />
        </div>
      )}
    </Card>
  )
}

export default function RoomTemplates() {
  const [creating, setCreating] = useState(false)
  const [status, setStatus] = useState('active')
  const [query, setQuery] = useState('')
  const [created, setCreated] = useState(null)
  const [expanded, setExpanded] = useState(null)

  const params = useMemo(() => ({ status, q: query, limit: 100 }), [status, query])
  const rooms = useAsync(() => roomTemplatesApi.rooms(params), [params])

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-mono text-2xl font-semibold">Create a room</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Bind a room to an account, pin it to a template version, and give it a site. Every
            write is audited.
          </p>
        </div>
        <Button
          icon={creating ? 'close' : 'plus'}
          variant={creating ? 'ghost' : 'primary'}
          onClick={() => setCreating((value) => !value)}
        >
          {creating ? 'Cancel' : 'New room'}
        </Button>
      </header>

      {created && (
        <div
          role="status"
          className="flex flex-wrap items-center gap-2 rounded-lg border border-accent/40 bg-accent/10 p-4 text-sm"
        >
          <Glyph name="check" className="text-accent" />
          <span className="text-foreground">
            Created <span className="font-mono">{created.data.name}</span> at{' '}
            <span className="font-mono">/{created.data.friendly_url}</span>
          </span>
          <span className="font-mono text-xs text-muted-foreground">
            site {created.data.site_id}
          </span>
        </div>
      )}

      {creating && (
        <CreateWizard
          onCancel={() => setCreating(false)}
          onCreated={(room) => {
            setCreated(room)
            setCreating(false)
            rooms.refetch()
          }}
        />
      )}

      <Card>
        <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
          <Field label="Search rooms" id="wf001-rooms-search">
            <input
              id="wf001-rooms-search"
              className={inputClass}
              placeholder="Name, friendly URL, account, or template"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </Field>
          <Field label="Status" id="wf001-rooms-status">
            <select
              id="wf001-rooms-status"
              className={inputClass}
              value={status}
              onChange={(event) => setStatus(event.target.value)}
            >
              {STATUSES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </Card>

      {rooms.loading && <Spinner label="Loading rooms" />}
      {rooms.error && <ErrorNote error={rooms.error} onRetry={rooms.refetch} />}

      {!rooms.loading && !rooms.error && (
        <>
          {(rooms.data?.rooms || []).length === 0 ? (
            <EmptyState
              title={query || status !== 'all' ? 'No rooms match these filters' : 'No rooms yet'}
              description={
                query || status !== 'all'
                  ? 'Try a different search term, or switch the status filter to All.'
                  : 'Create the first room to see it appear here and in the audit log.'
              }
              action={
                !creating && (
                  <Button icon="plus" variant="primary" onClick={() => setCreating(true)}>
                    New room
                  </Button>
                )
              }
            />
          ) : (
            <>
              <p className="text-xs tracking-wide text-muted-foreground uppercase">
                {rooms.data.count} of {rooms.data.total} rooms
              </p>
              <ul className="grid gap-4 md:grid-cols-2">
                {rooms.data.rooms.map((room) => (
                  <li key={room.id}>
                    <RoomCard
                      room={room}
                      expanded={expanded === room.id}
                      onToggle={() => setExpanded(expanded === room.id ? null : room.id)}
                    />
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </div>
  )
}
