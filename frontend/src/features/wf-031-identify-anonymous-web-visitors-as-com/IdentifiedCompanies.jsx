import { useState } from 'react'

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
import { relativeTime } from '@/lib/api'

import { companyLabel, leadQuery, matchedPageNames, refusalMessage, visitorApi } from './api'
import { Checkbox, Facts, Glyph, Notice } from './primitives'

/**
 * Identify anonymous web visitors as companies, and filter by pages read (WF-031).
 *
 * The flow this page drives is the researched one, in order:
 *
 * 1. Install the tracking snippet and note the **Client ID**.
 * 2. Define the pages that signal intent: a name, the URL **path without the
 *    domain**, and one of **Exact**, **Contains** or **Starts with**.
 * 3. Filter the lead list by those pages, and by segment, tags and the ICP.
 * 4. Drill into a company for its name, website, address, size and contacts.
 *
 * Five things the page is careful about, and the reason for each:
 *
 * * **It never offers a field for identifying a person.** "Albacross focuses on
 *   exclusively company-level identification rather than tracking individual
 *   users." A capture form with a name field in it would be the product
 *   contradicting its own stance, and the server refuses the field anyway.
 * * **It shows the condition label next to every page.** "you can select which
 *   condition should be followed: Exact ... Contains ... Starts with" is a choice
 *   with three answers, and a table that printed only the machine name would hide
 *   which one was in force.
 * * **It shows the ranking it applied.** The research says "ranked list" and names
 *   no key, so the page states the order rather than implying it is obvious.
 * * **It shows the five researched fields as present-and-empty, not blank.** A
 *   company identified a moment ago has no name yet, and that is a normal state
 *   rather than a broken one.
 * * **It names the two surfaces it does not build.** Workflows and Auto-engage are
 *   in the research, and a page that silently omitted them would leave a reviewer
 *   wondering whether they were missed.
 *
 * `components/ui.jsx` names a `Toggle` and a `Checkbox` in its own docstring and
 * exports neither, so both are built in this feature's `primitives.jsx` rather
 * than imported. Promoting them is a one-off platform change belonging to whoever
 * owns that shared file.
 */

const TABS = [
  { id: 'lead', label: 'Lead list' },
  { id: 'pages', label: 'Pages' },
  { id: 'capture', label: 'Capture' },
  { id: 'install', label: 'Install' },
  { id: 'decisions', label: 'Decisions' },
]

const EMPTY_FILTERS = {
  page: [],
  tag: [],
  segment: '',
  icp: '',
  country: '',
  size: '',
  limit: 50,
}

function IdentifiedCompanies() {
  const [tab, setTab] = useState('lead')
  const [clientId, setClientId] = useState('')
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [nonce, setNonce] = useState(0)

  const vocabulary = useAsync(() => visitorApi.vocabulary(), [])
  const installations = useAsync(() => visitorApi.installations(), [nonce])
  const pages = useAsync(() => visitorApi.pages(clientId), [clientId, nonce])
  const profiles = useAsync(() => visitorApi.profiles(), [nonce])
  const leads = useAsync(() => visitorApi.companies(leadQuery(filters)), [filters, nonce])

  const refetch = () => setNonce((value) => value + 1)
  const loading = [vocabulary, installations, pages, profiles, leads].some((call) => call.loading)
  const failure = [vocabulary, installations, pages, profiles, leads].find((call) => call.error)

  if (loading && !leads.data) return <Spinner label="Loading identified companies" />
  if (failure) return <ErrorNote error={failure.error} onRetry={failure.refetch} />

  const clients = installations.data?.installations || []
  const activeClient = clientId || clients[0]?.client_id || ''
  const pageRows = pages.data?.pages || []
  const profileRows = profiles.data?.profiles || []

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="font-display text-2xl font-semibold text-foreground">
            Identified companies
          </h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            An anonymous website request is resolved to a <strong>company</strong>, never to a
            person, from the three public parameters the tracking script reads: the IP address, the
            country and the network. Define the pages that signal intent, then filter the ranked
            lead list by the pages each company actually read.
          </p>
        </div>
        <div className="w-56">
          <Field
            id="wf031-client"
            label="Tracking client"
            hint="The Pages list belongs to one account."
          >
            <select
              id="wf031-client"
              className={inputClass}
              value={activeClient}
              onChange={(event) => setClientId(event.target.value)}
            >
              <option value="">Every client</option>
              {clients.map((client) => (
                <option key={client.id} value={client.client_id}>
                  {client.client_id}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </header>

      <nav aria-label="Sections" className="flex flex-wrap gap-2">
        {TABS.map((entry) => (
          <button
            key={entry.id}
            type="button"
            aria-current={tab === entry.id ? 'page' : undefined}
            onClick={() => setTab(entry.id)}
            className={`min-h-11 rounded-sm border px-4 text-sm font-medium transition-colors duration-150 ${
              tab === entry.id
                ? 'border-accent bg-accent text-on-accent'
                : 'border-border-subtle bg-surface text-foreground hover:border-accent hover:text-accent'
            }`}
          >
            {entry.label}
          </button>
        ))}
      </nav>

      {tab === 'lead' && (
        <LeadList
          vocabulary={vocabulary.data}
          leads={leads.data}
          pages={pageRows}
          profiles={profileRows}
          filters={filters}
          onChange={setFilters}
          onChanged={refetch}
        />
      )}
      {tab === 'pages' && (
        <PagesPanel
          vocabulary={vocabulary.data}
          pages={pageRows}
          clientId={activeClient}
          onChanged={refetch}
        />
      )}
      {tab === 'capture' && <CapturePanel vocabulary={vocabulary.data} clientId={activeClient} />}
      {tab === 'install' && <InstallPanel clients={clients} onChanged={refetch} />}
      {tab === 'decisions' && <DecisionsPanel vocabulary={vocabulary.data} />}
    </div>
  )
}

/* ------------------------------------------------------------------ lead list */

function LeadList({ vocabulary, leads, pages, profiles, filters, onChange, onChanged }) {
  const [selected, setSelected] = useState(null)

  if (!leads) return <Spinner label="Loading the lead list" />

  const rows = leads.companies || []
  const summary = leads.summary || {}

  const set = (patch) => onChange({ ...filters, ...patch })
  const toggleIn = (key, value) => {
    const current = filters[key]
    set({ [key]: current.includes(value) ? current.filter((e) => e !== value) : [...current, value] })
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Companies" value={summary.companies ?? 0} icon="database" hint="After the filter" />
        <StatCard label="Page views" value={summary.page_views ?? 0} icon="search" />
        <StatCard
          label="With contacts"
          value={summary.with_contact_candidates ?? 0}
          icon="rooms"
          hint="A named employee or contact"
        />
        <StatCard label="Countries seen" value={(summary.countries || []).length} icon="schema" />
      </div>

      <Card>
        <div className="flex items-center gap-2">
          <Glyph name="filter" />
          <h2 className="font-display text-base font-semibold text-foreground">Pages filter</h2>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          &ldquo;Apply the Pages filter to isolate companies that visited those pages.&rdquo; Naming
          two pages means either of them. Matches are worked out when this list is read, so a page
          defined today filters a visit from last month.
        </p>
        <div className="mt-3 grid grid-cols-1 gap-x-6 sm:grid-cols-2">
          {(pages || []).map((page) => (
            <Checkbox
              key={page.id}
              id={`wf031-filter-${page.id}`}
              checked={filters.page.includes(page.id)}
              onChange={() => toggleIn('page', page.id)}
              label={page.name}
              hint={`${page.condition_label} on ${page.path}`}
            />
          ))}
        </div>
        {!pages?.length && (
          <p className="mt-3 text-sm text-muted-foreground">
            No intent pages are defined yet, so this filter has nothing to select. Define one on the
            Pages tab.
          </p>
        )}

        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Field id="wf031-segment" label="Segment">
            <input
              id="wf031-segment"
              className={inputClass}
              value={filters.segment}
              placeholder="enterprise"
              onChange={(event) => set({ segment: event.target.value })}
            />
          </Field>
          <Field id="wf031-size" label="Size">
            <input
              id="wf031-size"
              className={inputClass}
              value={filters.size}
              placeholder="1000+"
              onChange={(event) => set({ size: event.target.value })}
            />
          </Field>
          <Field id="wf031-country" label="Country">
            <input
              id="wf031-country"
              className={inputClass}
              value={filters.country}
              placeholder="GB"
              onChange={(event) => set({ country: event.target.value })}
            />
          </Field>
        </div>

        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field id="wf031-icp" label="Ideal customer profile" hint="Combine with the filter above.">
            <select
              id="wf031-icp"
              className={inputClass}
              value={filters.icp}
              onChange={(event) => set({ icp: event.target.value })}
            >
              <option value="">Any company</option>
              {(profiles || []).map((profile) => (
                <option key={profile.id} value={profile.id}>
                  {profile.name}
                </option>
              ))}
            </select>
          </Field>
          <Field id="wf031-tag" label="Tags" hint="Space separated. Every tag has to be present.">
            <input
              id="wf031-tag"
              className={inputClass}
              value={filters.tag.join(' ')}
              placeholder="in-market tail-lights"
              onChange={(event) =>
                set({ tag: event.target.value.split(/\s+/).filter(Boolean) })
              }
            />
          </Field>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Button icon="refresh" onClick={() => onChange(EMPTY_FILTERS)}>
            Clear filters
          </Button>
          <span className="font-mono text-xs text-muted-foreground">
            ranked by {(vocabulary?.ranking || []).join(', ')}
          </span>
        </div>
      </Card>

      <Card>
        <h2 className="font-display text-base font-semibold text-foreground">
          In-market companies ({rows.length})
        </h2>
        {!rows.length && (
          <div className="mt-3">
            <EmptyState
              title="No company matches this filter"
              description="Widen the filter, or capture a page view from the Capture tab so there is traffic to rank."
            />
          </div>
        )}
        {rows.length > 0 && (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[720px] border-collapse text-left text-sm">
              <caption className="sr-only">
                Ranked in-market companies with the five researched fields
              </caption>
              <thead>
                <tr className="border-b border-border-subtle text-[11px] tracking-[0.14em] text-muted-foreground uppercase">
                  <th scope="col" className="py-2 pr-3">Company</th>
                  <th scope="col" className="py-2 pr-3">Size</th>
                  <th scope="col" className="py-2 pr-3">Countries</th>
                  <th scope="col" className="py-2 pr-3">Views</th>
                  <th scope="col" className="py-2 pr-3">Last seen</th>
                  <th scope="col" className="py-2 pr-3">Pages matched</th>
                  <th scope="col" className="py-2">Drill in</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((company) => (
                  <tr key={company.company_key} className="border-b border-border-subtle/40">
                    <td className="py-2 pr-3">
                      <span className="flex items-center gap-2">
                        <Glyph name="company" size={16} />
                        <span className="font-medium text-foreground">{companyLabel(company)}</span>
                      </span>
                      <span className="font-mono text-xs text-muted-foreground">
                        {company.company_key}
                      </span>
                    </td>
                    <td className="py-2 pr-3 font-mono text-[13px] text-foreground">
                      {company.size || <Dash />}
                    </td>
                    <td className="py-2 pr-3 font-mono text-[13px] text-foreground">
                      {(company.countries || []).join(', ') || <Dash />}
                    </td>
                    <td className="py-2 pr-3 font-mono text-[13px] text-foreground">
                      {company.page_views}
                    </td>
                    <td className="py-2 pr-3 font-mono text-[13px] text-muted-foreground">
                      {relativeTime(company.last_visit_at)}
                    </td>
                    <td className="py-2 pr-3">
                      {(company.matched_pages || []).length ? (
                        <span className="flex flex-wrap gap-1">
                          {matchedPageNames(company, pages).map((name) => (
                            <Badge key={name} tone="insert">
                              {name}
                            </Badge>
                          ))}
                        </span>
                      ) : (
                        <span className="text-xs text-muted-foreground">No intent page matched</span>
                      )}
                    </td>
                    <td className="py-2">
                      <Button
                        onClick={() =>
                          setSelected(selected === company.company_key ? null : company.company_key)
                        }
                      >
                        {selected === company.company_key ? 'Close' : 'Open'}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {selected && <CompanyPanel companyKey={selected} onChanged={onChanged} />}
    </div>
  )
}

function Dash() {
  return <span className="text-muted-foreground">not known yet</span>
}

/* -------------------------------------------------------------- company drill */

function CompanyPanel({ companyKey, onChanged }) {
  const detail = useAsync(() => visitorApi.company(companyKey), [companyKey])
  const visits = useAsync(() => visitorApi.visits(companyKey, { limit: 25 }), [companyKey])
  const pages = useAsync(() => visitorApi.companyPages(companyKey), [companyKey])

  const [form, setForm] = useState(null)
  const [saved, setSaved] = useState(null)
  const [problem, setProblem] = useState(null)

  if (detail.loading) return <Spinner label="Loading the company" />
  if (detail.error) return <ErrorNote error={detail.error} onRetry={detail.refetch} />

  const company = detail.data
  const fields = form || {
    name: company.name || '',
    website: company.website || '',
    address: company.address || '',
    size: company.size || '',
    segment: company.segment || '',
    tags: (company.tags || []).join(' '),
  }

  const save = async () => {
    setProblem(null)
    setSaved(null)
    try {
      await visitorApi.amendCompany(
        companyKey,
        {
          ...fields,
          tags: fields.tags.split(/\s+/).filter(Boolean),
        },
        'dana',
      )
      setForm(null)
      setSaved('Saved. The lead list now shows the new detail.')
      detail.refetch()
      onChanged?.()
    } catch (error) {
      setProblem(refusalMessage(error))
    }
  }

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-display text-base font-semibold text-foreground">
            {companyLabel(company)}
          </h2>
          <p className="text-sm text-muted-foreground">
            &ldquo;The insights provided include the company's name, website, address, size, and a
            list of employees or contacts associated with the company.&rdquo;
          </p>
        </div>
        <Badge tone={company.identified_from === 'manual' ? 'update' : 'insert'}>
          {company.identified_from === 'manual' ? 'Added by hand' : 'Identified from a capture'}
        </Badge>
      </div>

      <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="space-y-2">
          <Facts
            rows={[
              ['Company key', company.company_key],
              ['Size', company.size],
              ['Segment', company.segment],
              ['Tags', (company.tags || []).join(', ')],
              ['Countries', (company.countries || []).join(', ')],
              ['Networks', (company.known_networks || []).join(', ')],
              ['Addresses', (company.known_ips || []).join(', ')],
              ['Page views', company.page_views],
              ['First seen', relativeTime(company.first_seen_at)],
              ['Last seen', relativeTime(company.last_visit_at)],
            ]}
          />
          <div>
            <p className="text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
              Employees and contacts
            </p>
            {(company.contacts || []).length ? (
              <ul aria-label="Employees and contacts" className="mt-1 space-y-1">
                {company.contacts.map((contact) => (
                  <li key={contact.name} className="text-sm text-foreground">
                    {contact.name}
                    {contact.role && (
                      <span className="text-muted-foreground"> - {contact.role}</span>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1 text-sm text-muted-foreground">
                None recorded. A contact carries a name and a role, and nothing else.
              </p>
            )}
          </div>
        </div>

        <div className="space-y-3">
          <div>
            <p className="text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
              Pages this company satisfied
            </p>
            <div className="mt-1 flex flex-wrap gap-1">
              {(pages.data?.pages || []).map((entry) => (
                <Badge key={entry.id} tone="insert">
                  {entry.name} ({entry.condition_label})
                </Badge>
              ))}
              {!(pages.data?.pages || []).length && (
                <span className="text-sm text-muted-foreground">No intent page matched</span>
              )}
            </div>
          </div>
          <div>
            <p className="text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
              Most read paths
            </p>
            {(visits.data?.top_paths || []).length ? (
              <ul className="mt-1 space-y-1">
                {visits.data.top_paths.map(([entry, count]) => (
                  <li key={entry} className="flex justify-between gap-3 text-sm">
                    <span className="truncate font-mono text-[13px] text-foreground">{entry}</span>
                    <span className="font-mono text-[13px] text-muted-foreground">{count}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1 text-sm text-muted-foreground">No visit recorded.</p>
            )}
          </div>
          <details className="rounded-sm border border-border-subtle p-3">
            <summary className="min-h-11 cursor-pointer py-2 text-sm font-medium text-foreground">
              Raw company record
            </summary>
            <JsonView value={company} />
          </details>
        </div>
      </div>

      <div className="mt-4 border-t border-border-subtle pt-4">
        <h3 className="font-display text-base font-semibold text-foreground">Set the detail</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          A field sent as blank is refused, because the drill-down has five columns and a blank one
          cannot say &ldquo;not known yet&rdquo;. Leave a field alone to keep what is there.
        </p>
        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field id="wf031-name" label="Name">
            <input
              id="wf031-name"
              className={inputClass}
              value={fields.name}
              onChange={(event) => setForm({ ...fields, name: event.target.value })}
            />
          </Field>
          <Field id="wf031-website" label="Website" hint="Starts with http:// or https://.">
            <input
              id="wf031-website"
              className={inputClass}
              value={fields.website}
              onChange={(event) => setForm({ ...fields, website: event.target.value })}
            />
          </Field>
          <Field id="wf031-address" label="Address">
            <input
              id="wf031-address"
              className={inputClass}
              value={fields.address}
              onChange={(event) => setForm({ ...fields, address: event.target.value })}
            />
          </Field>
          <Field id="wf031-size-edit" label="Size">
            <input
              id="wf031-size-edit"
              className={inputClass}
              value={fields.size}
              onChange={(event) => setForm({ ...fields, size: event.target.value })}
            />
          </Field>
          <Field id="wf031-segment-edit" label="Segment">
            <input
              id="wf031-segment-edit"
              className={inputClass}
              value={fields.segment}
              onChange={(event) => setForm({ ...fields, segment: event.target.value })}
            />
          </Field>
          <Field id="wf031-tags" label="Tags" hint="Space separated.">
            <input
              id="wf031-tags"
              className={inputClass}
              value={fields.tags}
              onChange={(event) => setForm({ ...fields, tags: event.target.value })}
            />
          </Field>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button variant="primary" onClick={save}>
            Save detail
          </Button>
          {saved && <Notice tone="success">{saved}</Notice>}
        </div>
        {problem && (
          <div className="mt-3">
            <Notice tone="danger" title="The detail was not saved" onDismiss={() => setProblem(null)}>
              {problem}
            </Notice>
          </div>
        )}
      </div>
    </Card>
  )
}

/* -------------------------------------------------------------------- pages */

function PagesPanel({ vocabulary, pages, clientId, onChanged }) {
  const conditions = vocabulary?.match_conditions || []
  const [name, setName] = useState('')
  const [path, setPath] = useState('')
  const [condition, setCondition] = useState('exact')
  const [problem, setProblem] = useState(null)
  const [saved, setSaved] = useState(null)

  const add = async () => {
    setProblem(null)
    setSaved(null)
    try {
      await visitorApi.definePage({ name, path, condition }, clientId)
      setName('')
      setPath('')
      setSaved('Page added. The filter on the lead list now includes it.')
      onChanged?.()
    } catch (error) {
      setProblem(refusalMessage(error))
    }
  }

  const change = async (id, patch) => {
    setProblem(null)
    try {
      await visitorApi.amendPage(id, patch)
      onChanged?.()
    } catch (error) {
      setProblem(refusalMessage(error))
    }
  }

  const remove = async (id) => {
    setProblem(null)
    try {
      await visitorApi.dropPage(id)
      onChanged?.()
    } catch (error) {
      setProblem(refusalMessage(error))
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <h2 className="font-display text-base font-semibold text-foreground">Add a page</h2>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          &ldquo;Give the page a name and input the URL.&rdquo; The path goes in <em>without</em> the
          domain: &ldquo;When you type in the web page URL do not include the domain.&rdquo; A
          definition carrying a domain is refused, because a capture records a path and no domain,
          so it would never match.
        </p>
        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Field id="wf031-page-name" label="Name">
            <input
              id="wf031-page-name"
              className={inputClass}
              value={name}
              placeholder="Read the conversion article"
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          <Field
            id="wf031-page-path"
            label="URL path"
            hint="Path only, no domain. Example: /newsroom/converting-the-unconverted-article"
          >
            <input
              id="wf031-page-path"
              className={inputClass}
              value={path}
              placeholder="/newsroom/converting-the-unconverted-article"
              onChange={(event) => setPath(event.target.value)}
            />
          </Field>
          <Field id="wf031-page-condition" label="Match condition">
            <select
              id="wf031-page-condition"
              className={inputClass}
              value={condition}
              onChange={(event) => setCondition(event.target.value)}
            >
              {conditions.map((entry) => (
                <option key={entry.name} value={entry.name}>
                  {entry.label}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button variant="primary" onClick={add}>
            Add page
          </Button>
          {saved && <Notice tone="success">{saved}</Notice>}
        </div>
        {problem && (
          <div className="mt-3">
            <Notice tone="danger" title="The page was not added" onDismiss={() => setProblem(null)}>
              {problem}
            </Notice>
          </div>
        )}
      </Card>

      <Card>
        <h2 className="font-display text-base font-semibold text-foreground">
          Pages ({pages?.length || 0})
        </h2>
        {!pages?.length && (
          <div className="mt-3">
            <EmptyState
              title="No intent pages yet"
              description="Add the pages that mean a company is in market. Until one exists the Pages filter has nothing to select."
            />
          </div>
        )}
        <ul className="mt-3 space-y-2">
          {(pages || []).map((page) => (
            <li
              key={page.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-sm border border-border-subtle p-3"
            >
              <div className="min-w-0">
                <p className="text-sm font-medium text-foreground">{page.name}</p>
                <p className="font-mono text-xs text-muted-foreground">{page.path}</p>
                <p className="font-mono text-xs text-muted-foreground">{page.client_id}</p>
              </div>
              <div className="flex items-center gap-2">
                <select
                  aria-label={`Match condition for ${page.name}`}
                  className={`${inputClass} w-40`}
                  value={page.condition}
                  onChange={(event) => change(page.id, { condition: event.target.value })}
                >
                  {conditions.map((entry) => (
                    <option key={entry.name} value={entry.name}>
                      {entry.label}
                    </option>
                  ))}
                </select>
                <Button variant="danger" icon="trash" onClick={() => remove(page.id)}>
                  Remove
                </Button>
              </div>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}

/* ------------------------------------------------------------------ capture */

function CapturePanel({ vocabulary, clientId }) {
  const parameters = vocabulary?.capture_parameters || []
  const [form, setForm] = useState({
    path: '/newsroom/converting-the-unconverted-article',
    network: '203.0.113.0/24',
    ip_address: '203.0.113.11',
    country: 'GB',
  })
  const [result, setResult] = useState(null)
  const [problem, setProblem] = useState(null)
  const [busy, setBusy] = useState(false)

  const send = async () => {
    setBusy(true)
    setProblem(null)
    setResult(null)
    try {
      const body = Object.fromEntries(
        Object.entries(form).filter(([, value]) => String(value).trim() !== ''),
      )
      body.client_id = clientId
      setResult(await visitorApi.capture(body))
    } catch (error) {
      setProblem(refusalMessage(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <Notice tone="info" title="Company-level only" icon="sweep">
        &ldquo;Albacross focuses on exclusively company-level identification rather than tracking
        individual users, ensuring respect for user privacy.&rdquo; This form offers only the
        parameters the tracking script reads, because the server refuses anything else - and it
        refuses a person-level field by name.
      </Notice>

      <Card>
        <h2 className="font-display text-base font-semibold text-foreground">
          One anonymous website request
        </h2>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          &ldquo;We check the IP address, the country, the network, and other publicly available
          parameters to stay GDPR compliant.&rdquo; Three are named. At least one of the address or
          the network has to be present, because one of them is what identifies the company.
        </p>
        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field id="wf031-capture-path" label="URL path requested">
            <input
              id="wf031-capture-path"
              className={inputClass}
              value={form.path}
              onChange={(event) => setForm({ ...form, path: event.target.value })}
            />
          </Field>
          {parameters.map((parameter) => (
            <Field
              key={parameter.name}
              id={`wf031-capture-${parameter.name}`}
              label={parameter.label}
              hint={parameter.note}
            >
              <input
                id={`wf031-capture-${parameter.name}`}
                className={inputClass}
                value={form[parameter.name] || ''}
                onChange={(event) =>
                  setForm({ ...form, [parameter.name]: event.target.value })
                }
              />
            </Field>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button variant="primary" onClick={send} disabled={busy || !clientId}>
            {busy ? 'Capturing' : 'Capture request'}
          </Button>
          {!clientId && (
            <span className="text-sm text-muted-foreground">
              Install a tracking snippet first, then choose its Client ID.
            </span>
          )}
        </div>
        {problem && (
          <div className="mt-3">
            <Notice tone="danger" title="The request was not accepted" onDismiss={() => setProblem(null)}>
              {problem}
            </Notice>
          </div>
        )}
      </Card>

      {result && (
        <Card>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <h2 className="font-display text-base font-semibold text-foreground">
              Resolved to a company
            </h2>
            <Badge tone="insert">
              {result.company_created ? 'Company created' : 'Existing company'}
            </Badge>
          </div>
          <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Facts
              rows={[
                ['Company key', result.company_key],
                ['Name', result.company.name],
                ['Website', result.company.website],
                ['Address', result.company.address],
                ['Size', result.company.size],
                ['Contacts', (result.company.contacts || []).length],
                ['Page views', result.company.page_views],
                ['Countries', (result.company.countries || []).join(', ')],
              ]}
              empty="The company record is empty. A capture identifies a network, and the name arrives later."
            />
            <div>
              <p className="text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
                Intent pages this request satisfied
              </p>
              {result.matched_pages?.length ? (
                <ul className="mt-1 space-y-1">
                  {result.matched_pages.map((page) => (
                    <li key={page.id} className="text-sm text-foreground">
                      {page.name}{' '}
                      <span className="font-mono text-xs text-muted-foreground">
                        {page.condition_label} on {page.path}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-1 text-sm text-muted-foreground">None. The path matched no page.</p>
              )}
            </div>
          </div>
          <details className="mt-3 rounded-sm border border-border-subtle p-3">
            <summary className="min-h-11 cursor-pointer py-2 text-sm font-medium text-foreground">
              Raw response
            </summary>
            <JsonView value={result} />
          </details>
        </Card>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ install */

function InstallPanel({ clients, onChanged }) {
  const [clientId, setClientId] = useState('')
  const [site, setSite] = useState('')
  const [message, setMessage] = useState(null)
  const [problem, setProblem] = useState(null)

  const add = async () => {
    setProblem(null)
    setMessage(null)
    try {
      const answer = await visitorApi.install({ client_id: clientId, site })
      setMessage(
        answer.created
          ? 'Snippet installed. A capture can now arrive under this Client ID.'
          : 'Reinstalled. The site for this Client ID was updated.',
      )
      setClientId('')
      setSite('')
      onChanged?.()
    } catch (error) {
      setProblem(refusalMessage(error))
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <h2 className="font-display text-base font-semibold text-foreground">
          Install the tracking snippet
        </h2>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          &ldquo;Install the Albacross tracking snippet on the site and note the{' '}
          <strong>Client ID</strong>.&rdquo; A capture is refused without one, so this is what makes
          a capture addressable. Installing the same ID twice is not a failure: it re-points the
          site.
        </p>
        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field id="wf031-install-client" label="Client ID">
            <input
              id="wf031-install-client"
              className={inputClass}
              value={clientId}
              onChange={(event) => setClientId(event.target.value)}
            />
          </Field>
          <Field id="wf031-install-site" label="Site" hint="Optional. Shown beside the client id.">
            <input
              id="wf031-install-site"
              className={inputClass}
              value={site}
              onChange={(event) => setSite(event.target.value)}
            />
          </Field>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button variant="primary" onClick={add}>
            Install snippet
          </Button>
          {message && <Notice tone="success">{message}</Notice>}
        </div>
        {problem && (
          <div className="mt-3">
            <Notice tone="danger" title="Not installed" onDismiss={() => setProblem(null)}>
              {problem}
            </Notice>
          </div>
        )}
      </Card>

      <Card>
        <h2 className="font-display text-base font-semibold text-foreground">
          Installed snippets ({clients.length})
        </h2>
        {!clients.length && (
          <div className="mt-3">
            <EmptyState
              title="No snippet is installed"
              description="Until one is, every capture is refused with the Client ID it arrived under."
            />
          </div>
        )}
        <ul className="mt-3 space-y-2">
          {clients.map((client) => (
            <li
              key={client.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-sm border border-border-subtle p-3"
            >
              <span className="font-mono text-[13px] text-foreground">{client.client_id}</span>
              <span className="font-mono text-[13px] text-muted-foreground">
                {client.site || 'no site recorded'}
              </span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}

/* ---------------------------------------------------------------- decisions */

function DecisionsPanel({ vocabulary }) {  const inferences = useAsync(() => visitorApi.inferences(), [])
  const [showRaw, setShowRaw] = useState(false)

  if (inferences.loading) return <Spinner label="Loading the decisions" />
  if (inferences.error) return <ErrorNote error={inferences.error} onRetry={inferences.refetch} />

  const entries = inferences.data?.inferences || []

  return (
    <div className="space-y-4">
      <Card>
        <h2 className="font-display text-base font-semibold text-foreground">
          Where this workflow stops ({entries.length} decisions)
        </h2>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          The research names the five company fields, the three match conditions, the three public
          parameters and the rule that a page definition carries a path and not a URL. It does not
          say what identifies a company, how the lead list ranks, or where the workflow ends. Those
          edges are written down rather than left as comments in the code.
        </p>
        <div className="mt-3 space-y-3">
          {(vocabulary?.downstream_surfaces || []).map((surface) => (
            <Notice key={surface.surface} tone="info" title={surface.surface} icon="sweep">
              {surface.note}
            </Notice>
          ))}
        </div>
      </Card>

      <Card>
        <Checkbox
          id="wf031-raw"
          checked={showRaw}
          onChange={(event) => setShowRaw(event.target.checked)}
          label="Show the served payload"
          hint="The same records the API returns."
        />
        {showRaw ? (
          <div className="mt-3">
            <JsonView value={entries} />
          </div>
        ) : (
          <ul className="mt-3 space-y-3">
            {entries.map((entry) => (
              <li key={entry.id} className="rounded-sm border border-border-subtle p-3">
                <p className="text-sm font-medium text-foreground">{entry.question}</p>
                <p className="mt-1 text-sm text-foreground">{entry.reading}</p>
                <p className="mt-1 text-sm text-muted-foreground">{entry.why}</p>
                <p className="mt-1 font-mono text-xs text-muted-foreground">
                  change: {entry.change}
                </p>
                <p className="font-mono text-xs text-muted-foreground">risk: {entry.risk}</p>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

export default IdentifiedCompanies
