import { useMemo, useState } from 'react'
import { api, relativeTime } from '../lib/api'
import {
  Badge,
  Button,
  Card,
  ChoiceCard,
  EmptyState,
  ErrorNote,
  Field,
  Icon,
  JsonView,
  Spinner,
  Stepper,
  inputClass,
  useAsync,
} from '../components/ui'

/**
 * Sales rooms: the primary buyer-facing entity (WF-001).
 *
 * Creating a room here is the researched three-step wizard — pick the account,
 * pick a template, name the room — which produces a room bound to exactly one
 * site. The account binding is what later makes invite-by-email resolve without
 * a separate directory lookup, so step 1 is a real choice rather than a text
 * field.
 *
 * Every write goes through the audited store, so a new room is visible in the
 * audit log immediately. That round trip is the point of the page, not a side
 * effect.
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

/** Where a refused creation sends the operator back to, keyed by error code. */
const STEP_FOR_ERROR = {
  account_not_found: 0,
  template_not_found: 1,
  invalid_name: 2,
  invalid_friendly_url: 2,
  friendly_url_taken: 2,
}

/** Preview of the friendly URL the server will derive. Advisory only: the
 *  server normalises authoritatively and may add a numeric suffix. */
function previewSlug(value) {
  return value
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

function WizardProgress({ step }) {
  return <Stepper steps={STEPS} current={step} />
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
          derived
            ? `Leave blank to use /${derived}. Must be unique across rooms.`
            : 'Lowercase words separated by hyphens, e.g. acme-evaluation.'
        }
      >
        <input
          id="wf001-friendly-url"
          className={inputClass}
          maxLength={64}
          placeholder={derived || 'acme-evaluation'}
          value={friendlyUrl}
          onChange={(event) => onFriendlyUrl(event.target.value)}
        />
      </Field>

      <Button type="submit" variant="primary" icon="check" disabled={saving || !name.trim()}>
        {saving ? 'Saving…' : 'Save room'}
      </Button>
    </form>
  )
}

function CreateWizard({ onCreated }) {
  const accounts = useAsync(() => api.accounts({ limit: 100 }), [])
  const templates = useAsync(() => api.roomTemplates(), [])

  const [step, setStep] = useState(0)
  const [accountId, setAccountId] = useState('')
  const [templateId, setTemplateId] = useState('')
  const [name, setName] = useState('')
  const [friendlyUrl, setFriendlyUrl] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const templateList = templates.data?.templates || []
  const accountList = accounts.data?.accounts || []

  // Preselect the shipped standard template once, so the common case is one
  // click from the template step to the name step.
  const effectiveTemplateId =
    templateId || templateList.find((t) => t.template_id === 'tpl_standard')?.template_id || ''

  const selectedAccount = accountList.find((a) => a.id === accountId)
  const selectedTemplate = templateList.find((t) => t.template_id === effectiveTemplateId)

  const canAdvance = step === 0 ? Boolean(accountId) : Boolean(effectiveTemplateId)

  async function save(event) {
    event.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const room = await api.createRoom({
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
      // Send the operator back to the step that caused the refusal instead of
      // leaving them on a form that cannot succeed.
      const back = STEP_FOR_ERROR[caught.code]
      if (back !== undefined && back !== step) setStep(back)
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
        <WizardProgress step={step} />

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
            <Button icon="back" onClick={() => setStep((value) => value - 1)} disabled={saving}>
              Back
            </Button>
          )}
          {step < 2 && (
            <Button
              variant="primary"
              icon="chevron"
              onClick={() => setStep((value) => value + 1)}
              disabled={!canAdvance}
            >
              Next
            </Button>
          )}
          <p className="text-xs text-muted-foreground">
            {step === 0 && (accountId ? `Bound to ${selectedAccount?.data?.name || accountId}` : 'Select an account to continue')}
            {step === 1 && selectedTemplate && `Starting from ${selectedTemplate.name}`}
            {step === 2 && 'The room and its site are written in one transaction.'}
          </p>
        </div>
      </div>
    </Card>
  )
}

function RoomCard({ room, selected, onToggle }) {
  const data = room.data
  return (
    <Card className="card-hover h-full">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="truncate font-mono text-base font-semibold text-foreground">
            {data.name || 'Untitled room'}
          </h2>
          <p className="mt-0.5 truncate text-sm text-muted-foreground">
            {data.account_name || data.account || 'No account bound'}
          </p>
        </div>
        <Badge tone={data.status === 'archived' ? 'restore' : 'insert'}>{data.status || 'active'}</Badge>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-3 text-xs">
        <div className="min-w-0">
          <dt className="text-muted-foreground">Friendly URL</dt>
          <dd className="truncate font-mono text-foreground">/{data.friendly_url || '—'}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-muted-foreground">Template</dt>
          <dd className="truncate font-mono text-foreground" title={data.template_version_id || ''}>
            {data.template_id || '—'}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-muted-foreground">Site ID</dt>
          <dd className="truncate font-mono text-foreground" title={data.site_id || ''}>
            {data.site_id || '—'}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-muted-foreground">Updated</dt>
          <dd className="font-mono text-foreground">{relativeTime(room.updated_at)}</dd>
        </div>
      </dl>

      <div className="mt-4">
        <Button onClick={onToggle} aria-expanded={selected}>
          {selected ? 'Hide payload' : 'View payload'}
        </Button>
      </div>

      {selected && (
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

export default function Rooms() {
  const [creating, setCreating] = useState(false)
  const [status, setStatus] = useState('active')
  const [query, setQuery] = useState('')
  const [created, setCreated] = useState(null)
  const [selected, setSelected] = useState(null)

  const params = useMemo(() => ({ status, q: query, limit: 100 }), [status, query])
  const rooms = useAsync(() => api.listRooms(params), [params])

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-mono text-2xl font-semibold">Sales rooms</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Each room is bound to an account and pinned to a template version. Every edit is audited.
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
          <Icon name="check" className="text-accent" />
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
          onCreated={(room) => {
            setCreated(room)
            setCreating(false)
            rooms.refetch()
          }}
        />
      )}

      <Card>
        <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
          <Field label="Search rooms" id="rooms-search">
            <input
              id="rooms-search"
              className={inputClass}
              placeholder="Name, friendly URL, account, or template"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </Field>
          <Field label="Status" id="rooms-status">
            <select
              id="rooms-status"
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
              title={query || status !== 'all' ? 'No rooms match these filters' : 'No sales rooms yet'}
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
                      selected={selected === room.id}
                      onToggle={() => setSelected(selected === room.id ? null : room.id)}
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
