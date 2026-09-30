import { useState } from 'react'

import {
  Badge,
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
import { absoluteTime, relativeTime } from '@/lib/api'

import { companyQuery, enrichedCount, gateMessage, intentApi, isGateError } from './api'
import { Checkbox, Facts, Glyph, Notice } from './primitives'

/**
 * In-market companies, auto-added and tracked (WF-033).
 *
 * The flow this page drives is the researched one: hold the buyer-intent table of
 * companies showing intent, save a named view over it, switch on **Add new
 * companies** and **Track intent signals**, and let the two fire.
 *
 * Five things the page is careful about, and the reason for each:
 *
 * * **It shows what the automation did *not* do.** "Auto-add will only add
 *   companies that enter your saved views after enabling the auto-add. It will
 *   not add all existing companies in your saved views." A page that reported only
 *   the additions would make that rule invisible, and it is the one sentence in
 *   the research a seller is most likely to be surprised by.
 * * **It shows each company's entry time beside the switch-on time.** The two are
 *   the rule, and a row visibly older than the switch is a row the seller can
 *   check rather than take on trust.
 * * **It never implies a company is a task.** Nothing here calls a company "hot"
 *   or "ready to chase". The research gives no such rule, and a page that invents
 *   one teaches a seller to act on a number nobody defined.
 * * **It says which gate refused it.** Credits and the Data enrichment permission
 *   are two different remedies, and both are in the research.
 * * **It shows the credit ledger with the saving.** "You're only charged once for
 *   tracking (10 credits) - not for both actions separately" is a rule about
 *   money, and a number that appears without the saving looks like a bug.
 */

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'visitors', label: 'Visitors' },
  { id: 'research', label: 'Research' },
  { id: 'views', label: 'Saved views' },
  { id: 'categories', label: 'Auto-adds' },
  { id: 'configuration', label: 'Configuration' },
  { id: 'billing', label: 'Tracking and credits' },
]

/** The filter set the left panel starts from. */
const EMPTY_FILTERS = {
  days: 30,
  visitor_intent: false,
  in_target_markets: false,
  traffic_source: [],
  path: '',
  segment: '',
  sort: 'page_views',
  direction: 'desc',
}

/** StatCard only accepts a name from the shared icon set, so these are shared. */
const STAT_ICONS = { search: 'search', research: 'schema', stage: 'audit', added: 'database', tracked: 'refresh' }

function IntentCompanies() {
  const [tab, setTab] = useState('overview')
  const [actor, setActor] = useState('dana')
  const [nonce, setNonce] = useState(0)

  const vocabulary = useAsync(() => intentApi.vocabulary(), [])
  const capabilities = useAsync(() => intentApi.capabilities(actor), [actor, nonce])

  const refetch = () => setNonce((value) => value + 1)

  if (vocabulary.loading || capabilities.loading) {
    return <Spinner label="Loading in-market company intent" />
  }
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (capabilities.error) return <ErrorNote error={capabilities.error} onRetry={capabilities.refetch} />

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="font-mono text-2xl font-semibold text-foreground">In-market companies</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Companies whose own staff have been on the site, held by root domain, judged against
            intent criteria and target markets, then added to the CRM and tracked - but only the ones
            that entered a saved view <em>after</em> the automation was switched on.
          </p>
        </div>
        <div className="w-48">
          <Field id="wf033-actor" label="Acting as" hint="The Data enrichment permission is per user.">
            <input
              id="wf033-actor"
              className={inputClass}
              value={actor}
              onChange={(event) => setActor(event.target.value)}
            />
          </Field>
        </div>
      </header>

      <Gates gates={capabilities.data} onChanged={refetch} />

      <nav aria-label="Sections" className="flex flex-wrap gap-2">
        {TABS.map((entry) => (
          <button
            key={entry.id}
            type="button"
            aria-current={tab === entry.id ? 'page' : undefined}
            onClick={() => setTab(entry.id)}
            className={`min-h-11 rounded-lg border px-4 text-sm font-medium transition-colors duration-200 ${
              tab === entry.id
                ? 'border-accent/50 bg-accent/15 text-accent'
                : 'border-border-subtle/50 text-muted-foreground hover:bg-muted hover:text-foreground'
            }`}
          >
            {entry.label}
          </button>
        ))}
      </nav>

      {tab === 'overview' && <OverviewTab actor={actor} />}
      {tab === 'visitors' && <VisitorsTab actor={actor} vocabulary={vocabulary.data} />}
      {tab === 'research' && <ResearchTab />}
      {tab === 'views' && <ViewsTab actor={actor} onChanged={refetch} />}
      {tab === 'categories' && <CategoriesTab actor={actor} onChanged={refetch} />}
      {tab === 'configuration' && <ConfigurationTab actor={actor} onChanged={refetch} gates={capabilities.data} />}
      {tab === 'billing' && <BillingTab actor={actor} onChanged={refetch} />}
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* The two researched gates                                                   */
/* -------------------------------------------------------------------------- */

function Gates({ gates, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  if (!gates) return null
  if (gates.credits_enabled && gates.enrichment_granted) return null

  async function open() {
    setBusy(true)
    setError(null)
    try {
      await intentApi.saveSettings(
        { credits_enabled: true, enrichment_actors: [gates.actor].filter(Boolean) },
        gates.actor,
      )
      onChanged?.()
    } catch (caught) {
      setError(String(caught?.message || caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Notice tone="warning" title="Two capabilities are closed" icon="tracking">
      <ul className="mt-1 space-y-1">
        {!gates.credits_enabled && (
          <li>
            <strong className="text-foreground">HubSpot Credits.</strong> &ldquo;To access buyer intent
            features like filtering by segments and excluding companies, you need HubSpot
            Credits.&rdquo; The segment filter and the exclusions list are refused until they are on.
          </li>
        )}
        {!gates.enrichment_granted && (
          <li>
            <strong className="text-foreground">Data enrichment permission.</strong> &ldquo;To add and
            enrich companies from buyer intent, Super Admin must assign users with Data enrichment
            permissions.&rdquo;{' '}
            <span className="font-mono">{gates.actor || 'This caller'}</span> does not hold it, so
            auto-add, tracking and manual enrolment are refused.
          </li>
        )}
      </ul>
      <div className="mt-3">
        <Button icon="plus" onClick={open} disabled={busy}>
          {busy ? 'Switching on' : 'Switch on credits and grant this user the permission'}
        </Button>
      </div>
      {error && <p className="mt-2">{error}</p>}
    </Notice>
  )
}

/* -------------------------------------------------------------------------- */
/* Overview                                                                    */
/* -------------------------------------------------------------------------- */

function OverviewTab({ actor }) {
  const { data, loading, error, refetch } = useAsync(() => intentApi.overview(actor), [actor])

  if (loading) return <Spinner label="Loading the overview" />
  if (error) return <ErrorNote error={error} onRetry={refetch} />

  const body = data || {}
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <StatCard
          label="Showing visitor intent"
          value={body.companies_showing_visitor_intent ?? 0}
          icon={STAT_ICONS.search}
          hint="Matched an intent criterion on the site"
        />
        <StatCard
          label="Showing research intent"
          value={body.companies_showing_research_intent ?? 0}
          icon={STAT_ICONS.research}
          hint="Researching a topic, or in the news"
        />
        <StatCard
          label="Converted to a lifecycle stage"
          value={body.companies_converted_to_lifecycle_stage ?? 0}
          icon={STAT_ICONS.stage}
          hint="Added companies that have moved on since"
        />
        <StatCard
          label="Added companies"
          value={body.added_companies ?? 0}
          icon={STAT_ICONS.added}
          hint="Carrying Record source: Buyer-Intent"
        />
        <StatCard
          label="Under tracking"
          value={body.tracked_companies ?? 0}
          icon={STAT_ICONS.tracked}
          hint="10 credits per company, per period"
        />
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Card>
          <h2 className="font-mono text-sm font-semibold text-foreground">Popular auto-adds</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            The four stock categories, with how many companies each has added or enriched. They are
            stock: their definitions are fixed, and only the switch is yours.
          </p>
          <ul className="mt-3 space-y-2">
            {(body.popular_auto_adds || []).map((row) => (
              <li
                key={row.id}
                className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle/30 pb-2"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium text-foreground">{row.label}</p>
                  <p className="text-xs text-muted-foreground">
                    {row.added || 0} added &middot; {row.enriched || 0} enriched
                  </p>
                </div>
                <Badge tone={row.enabled ? 'update' : 'neutral'}>{row.enabled ? 'On' : 'Off'}</Badge>
              </li>
            ))}
          </ul>
        </Card>

        <Card>
          <h2 className="font-mono text-sm font-semibold text-foreground">Also worth knowing</h2>
          <div className="mt-3">
            <Facts
              rows={[
                ['News signals', body.news_signals],
                ['Excluded domains', body.excluded_domains],
                ['Unattributed page views', body.unattributed_views],
                ['Credits charged', body.credits?.total_charged],
                ['Credits saved', body.credits?.total_waived],
              ]}
            />
          </div>
          <p className="mt-3 text-xs text-muted-foreground">
            Unattributed page views are real traffic from an address no company could be matched to.
            They are stored and counted rather than dropped, so a tracking-code problem shows up here
            instead of looking like a quiet market.
          </p>
        </Card>
      </div>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Visitors: the table and the left panel                                     */
/* -------------------------------------------------------------------------- */

function VisitorsTab({ actor, vocabulary }) {
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [applied, setApplied] = useState(EMPTY_FILTERS)
  const [openKey, setOpenKey] = useState(null)

  const { data, loading, error, refetch } = useAsync(
    () => intentApi.companies(companyQuery(applied)),
    [applied],
  )

  const set = (key) => (event) => {
    const raw = event?.target?.type === 'checkbox' ? event.target.checked : event.target.value
    setFilters((previous) => ({ ...previous, [key]: raw }))
  }

  const sortKeys = vocabulary?.sort_keys || []
  const sources = vocabulary?.traffic_sources || []
  const operators = vocabulary?.path_operators || []
  const rows = data?.companies || []
  const window = data?.window

  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-[20rem_1fr]">
      <Card className="h-fit">
        <h2 className="font-mono text-sm font-semibold text-foreground">Filter</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          The left panel, in the order the research lists it. &ldquo;You can only set timeframes
          within the last 90 days&rdquo;, on midnight-UTC lines, and it is last-visit based.
        </p>

        <div className="mt-4 space-y-3">
          <Field
            id="wf033-days"
            label="Time frame (days)"
            hint={
              window
                ? `Opens ${window.start}, on a midnight-UTC line. Ceiling ${window.max_days}.`
                : 'At most 90.'
            }
          >
            <input
              id="wf033-days"
              type="number"
              min={1}
              max={90}
              className={inputClass}
              value={filters.days ?? ''}
              onChange={set('days')}
            />
          </Field>

          <Checkbox
            id="wf033-visitor-intent"
            checked={filters.visitor_intent}
            onChange={set('visitor_intent')}
            label="Showing visitor intent"
            hint="On narrows to companies with intent; off does not narrow at all."
          />
          <Checkbox
            id="wf033-target-markets"
            checked={filters.in_target_markets}
            onChange={set('in_target_markets')}
            label="In my target markets"
          />

          <Field id="wf033-traffic" label="Traffic source" hint="Ctrl-click for more than one.">
            <select
              id="wf033-traffic"
              multiple
              className={`${inputClass} h-28`}
              value={filters.traffic_source}
              onChange={(event) =>
                setFilters((previous) => ({
                  ...previous,
                  traffic_source: Array.from(event.target.selectedOptions, (option) => option.value),
                }))
              }
            >
              {sources.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.label}
                </option>
              ))}
            </select>
          </Field>

          <Field
            id="wf033-path"
            label="Specific page views"
            hint={`${operators.map((row) => row.label).join(' / ')}, optionally with @domain`}
          >
            <input
              id="wf033-path"
              className={inputClass}
              placeholder="starts_with:/pricing"
              value={filters.path}
              onChange={set('path')}
            />
          </Field>

          <Field
            id="wf033-segment"
            label="Filter by segment"
            hint="Needs HubSpot Credits - this is one of the two things they buy."
          >
            <input
              id="wf033-segment"
              className={inputClass}
              value={filters.segment}
              onChange={set('segment')}
            />
          </Field>

          <div className="grid grid-cols-2 gap-2">
            <Field id="wf033-sort" label="Sort by">
              <select id="wf033-sort" className={inputClass} value={filters.sort} onChange={set('sort')}>
                {sortKeys.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field id="wf033-direction" label="Direction">
              <select
                id="wf033-direction"
                className={inputClass}
                value={filters.direction}
                onChange={set('direction')}
              >
                <option value="desc">Descending</option>
                <option value="asc">Ascending</option>
              </select>
            </Field>
          </div>

          <div className="flex gap-2">
            <Button variant="primary" icon="search" onClick={() => setApplied(filters)}>
              Apply
            </Button>
            <Button
              onClick={() => {
                setFilters(EMPTY_FILTERS)
                setApplied(EMPTY_FILTERS)
              }}
            >
              Reset
            </Button>
          </div>
        </div>
      </Card>

      <div className="min-w-0 space-y-3">
        {error && isGateError(error) && <Notice tone="info">{gateMessage(error)}</Notice>}
        {error && !isGateError(error) && <ErrorNote error={error} onRetry={refetch} />}

        {loading ? (
          <Spinner label="Loading the buyer-intent table" />
        ) : rows.length === 0 ? (
          <EmptyState
            title="No company matches this filter set"
            description="Widen the time frame, or switch off the path filter. A company known only from a topic match has no last visit, so a last-visit time frame cannot see it."
          />
        ) : (
          <>
            <p className="text-sm text-muted-foreground">
              {rows.length} of {data?.total_before_filters ?? rows.length} companies, one row per root
              domain.
              {Object.keys(data?.truncated || {}).length > 0 && (
                <span className="text-amber-300">
                  {' '}Some collections hit the read limit, so these totals understate what is
                  stored.
                </span>
              )}
            </p>
            <ul className="space-y-2">
              {rows.map((row) => (
                <CompanyRow
                  key={row.company_key}
                  row={row}
                  actor={actor}
                  expanded={openKey === row.company_key}
                  onToggle={() => setOpenKey(openKey === row.company_key ? null : row.company_key)}
                />
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  )
}

function CompanyRow({ row, actor, expanded, onToggle }) {
  return (
    <li>
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="flex flex-wrap items-center gap-2 font-mono text-sm font-semibold text-foreground">
              {row.name}
              {row.in_crm && (
                <Badge tone="update">
                  <span className="inline-flex items-center gap-1">
                    <Glyph name="crm" size={12} /> In CRM
                  </span>
                </Badge>
              )}
              {row.visitor_intent && <Badge tone="insert">Visitor intent</Badge>}
              {row.research_intent && <Badge tone="insert">Research intent</Badge>}
              {row.tracked && (
                <Badge tone="update">
                  <span className="inline-flex items-center gap-1">
                    <Glyph name="tracking" size={12} /> Tracked
                  </span>
                </Badge>
              )}
            </p>
            <p className="mt-1 font-mono text-xs text-muted-foreground">
              {row.root_domain}
              {row.hosts.length > 1 && ` \u00b7 ${row.hosts.length} hosts rolled up`}
            </p>
          </div>
          <Button onClick={onToggle} aria-expanded={expanded}>
            {expanded ? 'Hide detail' : 'Detail'}
          </Button>
        </div>

        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Fact label="Website visits" value={row.website_visits} hint="Sessions" />
          <Fact label="Unique visitors" value={row.unique_visitors} />
          <Fact label="Page views" value={row.page_views} />
          <Fact
            label="Last visit"
            value={relativeTime(row.last_visit_at)}
            hint={absoluteTime(row.last_visit_at)}
          />
        </div>

        {expanded && <CompanyDetail row={row} actor={actor} />}
      </Card>
    </li>
  )
}

function CompanyDetail({ row, actor }) {
  const [enrol, setEnrol] = useState('')
  const [result, setResult] = useState(null)

  const card = useAsync(() => intentApi.card(row.company_key), [row.company_key])
  const views = useAsync(() => intentApi.pageViews(row.company_key, { limit: 20 }), [row.company_key])
  const contacts = useAsync(() => intentApi.contacts(row.company_key), [row.company_key])

  async function enroll(event) {
    event.preventDefault()
    setResult(null)
    try {
      const response = await intentApi.enroll(row.company_key, { workflow: enrol }, actor)
      setResult({
        tone: 'success',
        text:
          response.outcome === 'already_enrolled'
            ? `${row.company_key} was already enrolled in ${response.workflow}.`
            : `Enrolled ${row.company_key} in ${response.workflow}.`,
      })
    } catch (error) {
      setResult({ tone: 'danger', text: gateMessage(error) })
    }
  }

  const cardFields = card.data?.fields

  return (
    <div className="mt-4 space-y-4 border-t border-border-subtle/40 pt-4">
      <section>
        <h3 className="font-mono text-sm font-semibold text-foreground">Buyer Intent card</h3>
        <p className="text-xs text-muted-foreground">
          The four fields the record card carries: website visits, unique visitors, last seen, and
          top page views.
        </p>
        {card.loading && <Spinner label="Loading the card" />}
        {card.error && <ErrorNote error={card.error} onRetry={card.refetch} />}
        {cardFields && (
          <>
            <div className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Fact label="Website visits" value={cardFields.website_visits.value} />
              <Fact label="Unique visitors" value={cardFields.unique_visitors.value} />
              <Fact label="Last seen" value={relativeTime(cardFields.last_seen.value)} />
              <Fact
                label="Top page views"
                value={(cardFields.top_page_views.value || []).length}
              />
            </div>
            <ul className="mt-3 space-y-1">
              {(cardFields.top_page_views.value || []).map((entry) => (
                <li key={entry.path} className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="truncate font-mono text-foreground">{entry.path}</span>
                  <span className="text-muted-foreground">
                    {entry.visits} visits &middot; {entry.unique_visitors} visitors
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      <section>
        <h3 className="font-mono text-sm font-semibold text-foreground">About</h3>
        <div className="mt-2">
          <Facts
            rows={[
              ['Record source', row.record_source],
              ['Added via', row.added_via],
              ['Added at', absoluteTime(row.added_at)],
              ['Lifecycle stage', row.lifecycle_stage],
              ['Deal stage', row.deal_stage],
              ['Owner', row.owner],
              ['Segment', row.segment],
              ['Industry', row.industry],
              ['Target markets', (row.in_target_market_names || []).join(', ')],
              ['Countries', (row.countries || []).join(', ')],
              ['Traffic sources', (row.traffic_sources || []).join(', ')],
            ]}
          />
        </div>
        {Object.keys(row.derived_properties || {}).length > 0 && (
          <p className="mt-3 text-xs text-muted-foreground">
            Derived intent properties, which follow their criteria and can go back to false:{' '}
            {Object.entries(row.derived_properties)
              .map(([name, value]) => `${name}=${String(value)}`)
              .join(', ')}
          </p>
        )}
      </section>

      <section>
        <h3 className="font-mono text-sm font-semibold text-foreground">Recent page views</h3>
        <p className="text-xs text-muted-foreground">
          With the IP-derived country, and the qualifying page tagged with Intent.
        </p>
        {views.loading && <Spinner label="Loading page views" />}
        {views.error && <ErrorNote error={views.error} onRetry={views.refetch} />}
        {!!views.data?.page_views?.length && (
          <ul className="mt-2 space-y-1">
            {views.data.page_views.map((visit, index) => (
              <li
                key={`${visit.occurred_at}-${index}`}
                className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border-subtle/30 py-1 text-sm"
              >
                <span className="truncate font-mono text-foreground">
                  {visit.host}
                  {visit.path}
                </span>
                <span className="flex items-center gap-2 text-muted-foreground">
                  {visit.country && <Badge>{visit.country} (from IP)</Badge>}
                  {visit.intent && <Badge tone="insert">Intent</Badge>}
                  {relativeTime(visit.occurred_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h3 className="font-mono text-sm font-semibold text-foreground">Contacts</h3>
        <p className="text-xs text-muted-foreground">
          Last touch, last engagement, and any recently scheduled interactions.
        </p>
        {contacts.loading && <Spinner label="Loading contacts" />}
        {contacts.error && <ErrorNote error={contacts.error} onRetry={contacts.refetch} />}
        {!!contacts.data?.contacts?.length && (
          <ul className="mt-2 space-y-2">
            {contacts.data.contacts.map((contact) => (
              <li key={contact.email} className="text-sm">
                <p className="font-medium text-foreground">{contact.name || contact.email}</p>
                <p className="text-xs text-muted-foreground">
                  Last touch {relativeTime(contact.last_touch_at)} &middot; last engagement{' '}
                  {relativeTime(contact.last_engagement_at)}
                </p>
                {(contact.scheduled || []).length > 0 && (
                  <p className="text-xs text-muted-foreground">
                    Scheduled: {(contact.scheduled || []).map((entry) => entry.title).join(', ')}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h3 className="font-mono text-sm font-semibold text-foreground">Enrol in workflow</h3>
        <p className="text-xs text-muted-foreground">
          The researched manual control, beside the automated ones. No watermark: this is a person
          pointing at a company in front of them.
        </p>
        <form onSubmit={enroll} className="mt-2 flex flex-wrap items-end gap-2">
          <div className="min-w-48 flex-1">
            <Field id="wf033-enrol" label="Workflow">
              <input
                id="wf033-enrol"
                className={inputClass}
                value={enrol}
                onChange={(event) => setEnrol(event.target.value)}
                placeholder="Enterprise nurture"
              />
            </Field>
          </div>
          <Button type="submit" variant="primary" icon="plus" disabled={!enrol.trim()}>
            Enrol
          </Button>
        </form>
        {result && <p className="mt-2 text-sm">{result.text}</p>}
        {(row.enrolments || []).length > 0 && (
          <p className="mt-2 text-xs text-muted-foreground">
            Already enrolled in: {(row.enrolments || []).map((entry) => entry.workflow).join(', ')}
          </p>
        )}
      </section>
    </div>
  )
}

function Fact({ label, value, hint }) {
  return (
    <div className="min-w-0">
      <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{label}</p>
      <p className="truncate font-mono text-lg text-foreground">{value ?? 0}</p>
      {hint && <p className="truncate text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Research                                                                    */
/* -------------------------------------------------------------------------- */

function ResearchTab() {
  const { data, loading, error, refetch } = useAsync(() => intentApi.research({}), [])

  if (loading) return <Spinner label="Loading research" />
  if (error) return <ErrorNote error={error} onRetry={refetch} />

  const rows = data?.companies || []

  return (
    <Card>
      <h2 className="font-mono text-sm font-semibold text-foreground">
        Broader intent signals, beyond your own website
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">
        &ldquo;Companies researching topics across the web or company news like funding, executive
        hires, layoffs, product launches, and mergers.&rdquo; Both are research intent; the evidence
        below says which kind each one was, so a funding round is not reported as topic research.
      </p>
      {rows.length === 0 ? (
        <EmptyState
          title="No research intent recorded"
          description="Record a topic match or a company news signal and the company appears here."
        />
      ) : (
        <ul className="mt-4 space-y-3">
          {rows.map((row) => (
            <li key={row.company_key} className="border-b border-border-subtle/30 pb-3">
              <p className="font-mono text-sm font-semibold text-foreground">{row.name}</p>
              <p className="text-xs text-muted-foreground">
                {(row.countries || []).join(', ') || 'country unknown'} &middot;{' '}
                {(row.traffic_sources || []).join(', ') || 'no website visits'}
              </p>
              <ul className="mt-1 space-y-1">
                {(row.research_evidence || []).map((entry, index) => (
                  <li key={index} className="flex flex-wrap items-baseline gap-2 text-sm">
                    <Badge tone={entry.kind === 'news' ? 'update' : 'neutral'}>
                      {entry.kind === 'news' ? entry.signal_type : entry.topic}
                    </Badge>
                    <span className="text-muted-foreground">
                      {entry.headline || `researching \u201c${entry.topic}\u201d`} &middot;{' '}
                      {relativeTime(entry.at)}
                    </span>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

/* -------------------------------------------------------------------------- */
/* Saved views and their automations                                          */
/* -------------------------------------------------------------------------- */

function ViewsTab({ actor, onChanged }) {
  const views = useAsync(() => intentApi.views(), [])
  const automations = useAsync(() => intentApi.automations(), [])
  const [name, setName] = useState('')
  const [days, setDays] = useState(90)
  const [visitorIntent, setVisitorIntent] = useState(true)
  const [result, setResult] = useState(null)
  const [openView, setOpenView] = useState(null)

  if (views.loading || automations.loading) return <Spinner label="Loading saved views" />
  if (views.error) return <ErrorNote error={views.error} onRetry={views.refetch} />
  if (automations.error) return <ErrorNote error={automations.error} onRetry={automations.refetch} />

  const listed = views.data?.views || []
  const automationFor = (viewId) =>
    (automations.data?.automations || []).find((row) => row.view_id === viewId) || null

  async function saveView(event) {
    event.preventDefault()
    setResult(null)
    try {
      await intentApi.saveView({ name, filters: { days: Number(days), visitor_intent: visitorIntent } })
      setName('')
      setResult({ tone: 'success', text: 'View saved. Its filter set is now named and persistent.' })
      views.refetch()
    } catch (error) {
      setResult({ tone: 'danger', text: String(error?.message || error) })
    }
  }

  return (
    <div className="space-y-5">
      <Card>
        <h2 className="font-mono text-sm font-semibold text-foreground">Save view</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          &ldquo;Click Save view to persist the filter set as a named view.&rdquo; A name already in
          use is refused, because an automation is attached to a view and two views under one name
          would make which one it watches a matter of load order.
        </p>
        <form
          onSubmit={saveView}
          className="mt-3 grid grid-cols-1 items-end gap-3 sm:grid-cols-[1fr_8rem]"
        >
          <Field id="wf033-view-name" label="Name">
            <input
              id="wf033-view-name"
              className={inputClass}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="In-market: priced or asked for sales"
            />
          </Field>
          <Field id="wf033-view-days" label="Time frame (days)">
            <input
              id="wf033-view-days"
              type="number"
              min={1}
              max={90}
              className={inputClass}
              value={days}
              onChange={(event) => setDays(event.target.value)}
            />
          </Field>
          <div className="sm:col-span-2">
            <Checkbox
              id="wf033-view-intent"
              checked={visitorIntent}
              onChange={(event) => setVisitorIntent(event.target.checked)}
              label="Only companies showing visitor intent"
            />
          </div>
          <div className="sm:col-span-2">
            <Button type="submit" variant="primary" icon="plus" disabled={!name.trim()}>
              Save view
            </Button>
          </div>
        </form>
        {result && <p className="mt-2 text-sm">{result.text}</p>}
      </Card>

      {listed.length === 0 ? (
        <EmptyState title="No saved view yet" description="Save one above to switch automations on." />
      ) : (
        <ul className="space-y-3">
          {listed.map((view) => (
            <li key={view.id}>
              <ViewCard
                view={view}
                automation={automationFor(view.id)}
                actor={actor}
                expanded={openView === view.id}
                onToggle={() => setOpenView(openView === view.id ? null : view.id)}
                onChanged={() => {
                  views.refetch()
                  automations.refetch()
                  onChanged?.()
                }}
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function ViewCard({ view, automation, actor, expanded, onToggle, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [addOn, setAddOn] = useState(Boolean(automation?.add_new_companies))
  const [trackOn, setTrackOn] = useState(Boolean(automation?.track_intent_signals))
  const [members, setMembers] = useState(null)
  const [membersError, setMembersError] = useState(null)

  async function loadMembers() {
    setMembersError(null)
    try {
      setMembers(await intentApi.viewCompanies(view.id))
    } catch (error) {
      setMembersError(error)
    }
  }

  async function saveAutomation() {
    setBusy(true)
    setResult(null)
    try {
      const saved = await intentApi.saveAutomation({
        view_id: view.id,
        add_new_companies: addOn,
        track_intent_signals: trackOn,
      })
      setResult({
        tone: 'success',
        text: `Automation saved. Switched on at ${absoluteTime(saved.add_enabled_at || saved.track_enabled_at)}.`,
      })
      onChanged()
    } catch (error) {
      setResult({ tone: 'danger', text: String(error?.message || error) })
    } finally {
      setBusy(false)
    }
  }

  async function run() {
    setBusy(true)
    setResult(null)
    try {
      const response = await intentApi.runAutomation(automation.id, actor)
      setResult({
        tone: response.held_back?.length ? 'warning' : 'success',
        text: `${response.matched} in the view, ${response.added.length} added, ${
          response.tracked.length
        } tracked, ${response.held_back.length} held back because they entered before the switch.`,
      })
      onChanged()
      if (expanded) await loadMembers()
    } catch (error) {
      setResult({ tone: 'danger', text: gateMessage(error) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-mono text-sm font-semibold text-foreground">{view.name}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {view.filters?.timeframe
              ? `${view.filters.days} days, midnight-UTC, last-visit based`
              : 'No time frame'}
            {view.filters?.visitor_intent ? ' \u00b7 showing visitor intent' : ''}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            onClick={() => {
              onToggle()
              if (!expanded) loadMembers()
            }}
            aria-expanded={expanded}
          >
            {expanded ? 'Hide companies' : 'Companies in this view'}
          </Button>
          <Button icon="plus" onClick={saveAutomation} disabled={busy}>
            Save automation
          </Button>
          <Button
            variant="primary"
            icon="refresh"
            onClick={run}
            disabled={busy || !automation}
            title={automation ? undefined : 'Switch a toggle on and save the automation first'}
          >
            Run
          </Button>
        </div>
      </div>

      <div className="mt-3">
        <Checkbox
          id={`wf033-add-${view.id}`}
          checked={addOn}
          onChange={(event) => setAddOn(event.target.checked)}
          label="Add new companies"
        />
        <Checkbox
          id={`wf033-track-${view.id}`}
          checked={trackOn}
          onChange={(event) => setTrackOn(event.target.checked)}
          label="Track intent signals"
        />
      </div>

      {automation?.add_enabled_at && (
        <p className="mt-2 text-xs text-muted-foreground">
          Add new companies switched on {absoluteTime(automation.add_enabled_at)}; tracking switched
          on {automation.track_enabled_at ? absoluteTime(automation.track_enabled_at) : 'not yet'}.
        </p>
      )}

      {result && (
        <p className="mt-2 text-sm" role="status">
          {result.text}
        </p>
      )}

      {expanded && (
        <div className="mt-4 border-t border-border-subtle/40 pt-3">
          {membersError && <ErrorNote error={membersError} onRetry={loadMembers} />}
          {!members && !membersError && <Spinner label="Working out who is in this view" />}
          {members && (
            <>
              <Notice tone="info" title="The rule this tab is built on">
                {members.note}
              </Notice>
              {members.count === 0 ? (
                <p className="mt-3 text-sm text-muted-foreground">
                  No company is in this view&rsquo;s filter set right now.
                </p>
              ) : (
                <ul className="mt-3 space-y-1">
                  {members.companies.map((row) => (
                    <li
                      key={row.company_key}
                      className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border-subtle/30 py-1 text-sm"
                    >
                      <span className="truncate font-mono text-foreground">{row.company_key}</span>
                      <span className="text-muted-foreground">
                        entered {absoluteTime(row.entered_at)} &middot;{' '}
                        {row.entered_after_auto_add ? 'after auto-add' : 'before auto-add'}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      )}
    </Card>
  )
}

/* -------------------------------------------------------------------------- */
/* The four stock auto-add categories                                         */
/* -------------------------------------------------------------------------- */

function CategoriesTab({ actor, onChanged }) {
  const { data, loading, error, refetch } = useAsync(() => intentApi.categories(), [])
  const [busy, setBusy] = useState(null)
  const [result, setResult] = useState(null)

  if (loading) return <Spinner label="Loading the auto-add categories" />
  if (error) return <ErrorNote error={error} onRetry={refetch} />

  async function toggle(row) {
    setBusy(row.id)
    setResult(null)
    try {
      await intentApi.setCategory(row.id, { enabled: !row.enabled })
      refetch()
      onChanged?.()
    } catch (caught) {
      setResult({ tone: 'danger', text: String(caught?.message || caught) })
    } finally {
      setBusy(null)
    }
  }

  async function run(row) {
    setBusy(row.id)
    setResult(null)
    try {
      const response = await intentApi.runCategory(row.id, actor)
      setResult({
        tone: response.held_back?.length ? 'warning' : 'success',
        text: `${response.label}: ${response.matched} matched, ${response.added?.length || 0} added, ${enrichedCount(
          response,
        )} enriched, ${response.held_back?.length || 0} held back.`,
      })
      refetch()
      onChanged?.()
    } catch (caught) {
      setResult({ tone: 'danger', text: gateMessage(caught) })
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-4">
      <Notice tone="info" title="These four are stock">
        Their definitions come from the research and are fixed; only the switch is yours. Three of
        them require a target market and one deliberately does not, because the research&rsquo;s own
        wording for &ldquo;in-CRM with visitor intent&rdquo; omits it.
      </Notice>
      {result && <p className="text-sm">{result.text}</p>}
      <ul className="space-y-3">
        {(data?.categories || []).map((row) => (
          <li key={row.id}>
            <Card>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-mono text-sm font-semibold text-foreground">{row.label}</p>
                  <p className="mt-1 text-sm text-muted-foreground">{row.description}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Requires:{' '}
                    {[
                      row.requires_target_market && 'a target market',
                      row.requires_visitor_intent && 'visitor intent',
                      row.requires_research_intent && 'research intent',
                      row.requires_in_crm ? 'already in the CRM' : 'not yet in the CRM',
                    ]
                      .filter(Boolean)
                      .join(', ')}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={row.enabled ? 'update' : 'neutral'}>{row.enabled ? 'On' : 'Off'}</Badge>
                  <Button onClick={() => toggle(row)} disabled={busy === row.id}>
                    {row.enabled ? 'Switch off' : 'Switch on'}
                  </Button>
                  <Button
                    variant="primary"
                    icon="refresh"
                    onClick={() => run(row)}
                    disabled={busy === row.id || !row.enabled}
                  >
                    Run
                  </Button>
                </div>
              </div>
              {(row.added?.length || row.enriched?.length) && (
                <p className="mt-2 text-xs text-muted-foreground">
                  Added so far: {(row.added || []).join(', ') || 'none'}. Enriched so far:{' '}
                  {(row.enriched || []).join(', ') || 'none'}.
                </p>
              )}
            </Card>
          </li>
        ))}
      </ul>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Configuration and exclusions                                               */
/* -------------------------------------------------------------------------- */

function ConfigurationTab({ actor, onChanged, gates }) {
  const criteria = useAsync(() => intentApi.criteria(), [])
  const markets = useAsync(() => intentApi.markets(), [])
  const topics = useAsync(() => intentApi.topics(), [])
  const inferences = useAsync(() => intentApi.inferences(), [])
  const [criterion, setCriterion] = useState({ name: '', path: '/pricing', property: '' })
  const [domain, setDomain] = useState('')
  const [result, setResult] = useState(null)
  const [exclusions, setExclusions] = useState(null)
  const [exclusionsError, setExclusionsError] = useState(null)
  const [exclusionsLoading, setExclusionsLoading] = useState(false)

  const creditsOn = Boolean(gates?.credits_enabled)

  async function loadExclusions() {
    if (!creditsOn) return
    setExclusionsLoading(true)
    setExclusionsError(null)
    try {
      setExclusions(await intentApi.exclusions())
    } catch (error) {
      setExclusionsError(error)
    } finally {
      setExclusionsLoading(false)
    }
  }

  async function addCriterion(event) {
    event.preventDefault()
    setResult(null)
    try {
      await intentApi.addCriterion({
        name: criterion.name,
        derived_property: criterion.property,
        page_filters: [{ operator: 'starts_with', path: criterion.path }],
      })
      setCriterion({ name: '', path: '/pricing', property: '' })
      setResult({
        tone: 'success',
        text: 'Criterion saved. It is applied to page views as they arrive and to the ones already stored.',
      })
      criteria.refetch()
    } catch (error) {
      setResult({ tone: 'danger', text: String(error?.message || error) })
    }
  }

  async function addExclusion(event) {
    event.preventDefault()
    setResult(null)
    try {
      const created = await intentApi.exclude({ domain }, actor)
      setDomain('')
      setResult({
        tone: 'success',
        text: `Excluded ${created.root_domain}. Everything rolled up into that root domain is now invisible.`,
      })
      await loadExclusions()
      onChanged?.()
    } catch (error) {
      setResult({ tone: 'danger', text: gateMessage(error) })
    }
  }

  if (criteria.loading || markets.loading || topics.loading) {
    return <Spinner label="Loading configuration" />
  }
  if (criteria.error) return <ErrorNote error={criteria.error} onRetry={criteria.refetch} />
  if (markets.error) return <ErrorNote error={markets.error} onRetry={markets.refetch} />
  if (topics.error) return <ErrorNote error={topics.error} onRetry={topics.refetch} />

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Card>
          <h2 className="font-mono text-sm font-semibold text-foreground">Intent criteria</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            &ldquo;Intent criteria per page&rdquo;, so a criterion with no page is refused rather than
            stored. A criterion that names a custom property has that property follow it, including
            back to false.
          </p>
          <form onSubmit={addCriterion} className="mt-3 space-y-2">
            <Field id="wf033-criterion-name" label="Name">
              <input
                id="wf033-criterion-name"
                className={inputClass}
                value={criterion.name}
                onChange={(event) => setCriterion({ ...criterion, name: event.target.value })}
                placeholder="SMB Intent"
              />
            </Field>
            <Field id="wf033-criterion-path" label="Path (starts with)">
              <input
                id="wf033-criterion-path"
                className={inputClass}
                value={criterion.path}
                onChange={(event) => setCriterion({ ...criterion, path: event.target.value })}
              />
            </Field>
            <Field
              id="wf033-criterion-property"
              label="Custom property to derive"
              hint="For example: Showing SMB Intent"
            >
              <input
                id="wf033-criterion-property"
                className={inputClass}
                value={criterion.property}
                onChange={(event) => setCriterion({ ...criterion, property: event.target.value })}
              />
            </Field>
            <Button type="submit" variant="primary" icon="plus" disabled={!criterion.name.trim()}>
              Add criterion
            </Button>
          </form>
          <ul className="mt-3 space-y-1">
            {(criteria.data?.criteria || []).map((row) => (
              <li key={row.id} className="flex items-baseline justify-between gap-2 text-sm">
                <span className="truncate text-foreground">
                  {row.name}{' '}
                  <span className="font-mono text-xs text-muted-foreground">
                    {row.page_filters.map((entry) => `${entry.operator} ${entry.path}`).join(' or ')}
                  </span>
                </span>
                <Badge tone={row.active ? 'insert' : 'neutral'}>
                  {row.active ? 'Active' : 'Withdrawn'}
                </Badge>
              </li>
            ))}
          </ul>
        </Card>

        <div className="space-y-5">
          <Card>
            <h2 className="font-mono text-sm font-semibold text-foreground">Target markets</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              What &ldquo;In my target markets&rdquo; resolves against. A market naming neither a
              country nor an industry is refused, because then every company would be in it.
            </p>
            <ul className="mt-3 space-y-1">
              {(markets.data?.markets || []).map((row) => (
                <li key={row.id} className="text-sm">
                  <span className="font-medium text-foreground">{row.name}</span>
                  <span className="text-xs text-muted-foreground">
                    {' '}
                    &middot; {(row.countries || []).join(', ')} {(row.industries || []).join(', ')}
                  </span>
                </li>
              ))}
            </ul>
          </Card>

          <Card>
            <h2 className="font-mono text-sm font-semibold text-foreground">Research topics</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Matched against a topic observation case-insensitively, on any of its terms.
            </p>
            <p className="mt-2">
              {(topics.data?.topics || []).map((row) => (
                <span key={row.id} className="mr-2 mb-2 inline-block">
                  <Badge tone={row.active ? 'neutral' : 'delete'}>
                    {row.name}
                    {(row.terms || []).length ? `: ${row.terms.join(', ')}` : ''}
                  </Badge>
                </span>
              ))}
            </p>
          </Card>
        </div>
      </div>

      <Card>
        <h2 className="font-mono text-sm font-semibold text-foreground">Exclusions by domain</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          An excluded domain is invisible to the table and to every automation, and it is reduced
          through the root-domain model first - so excluding <code>www.south.example</code> excludes{' '}
          <code>south.example</code> and every host under it.
        </p>
        {!creditsOn ? (
          <Notice tone="warning" title="Needs HubSpot Credits">
            Excluding companies is one of the two features the research puts behind credits, so this
            list is closed until they are on.
          </Notice>
        ) : (
          <>
            <div className="mt-2">
              <Button icon="refresh" onClick={loadExclusions} disabled={exclusionsLoading}>
                {exclusionsLoading ? 'Loading' : 'Load the exclusion list'}
              </Button>
            </div>
            <form onSubmit={addExclusion} className="mt-3 flex flex-wrap items-end gap-2">
              <div className="min-w-48 flex-1">
                <Field id="wf033-exclude" label="Domain">
                  <input
                    id="wf033-exclude"
                    className={inputClass}
                    value={domain}
                    onChange={(event) => setDomain(event.target.value)}
                    placeholder="talent-insight-partners.example"
                  />
                </Field>
              </div>
              <Button type="submit" variant="primary" icon="plus" disabled={!domain.trim()}>
                Exclude
              </Button>
            </form>
            {exclusionsError && <ErrorNote error={exclusionsError} onRetry={loadExclusions} />}
            {exclusions && (exclusions.exclusions || []).length === 0 && (
              <p className="mt-3 text-sm text-muted-foreground">No domain is excluded.</p>
            )}
            {exclusions && (exclusions.exclusions || []).length > 0 && (
              <ul className="mt-3 space-y-1">
                {exclusions.exclusions.map((row) => (
                  <li key={row.domain} className="flex items-center justify-between gap-2 text-sm">
                    <span className="truncate font-mono text-foreground">{row.domain}</span>
                    <Button
                      icon="trash"
                      onClick={async () => {
                        await intentApi.unexclude(row.domain, actor)
                        await loadExclusions()
                        onChanged?.()
                      }}
                    >
                      Remove
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
        {result && <p className="mt-2 text-sm">{result.text}</p>}
      </Card>

      <Card>
        <h2 className="font-mono text-sm font-semibold text-foreground">Where this stops being sourced</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          {inferences.data?.note}
        </p>
        {inferences.loading && <Spinner label="Loading the judgement calls" />}
        {inferences.data && (
          <ul className="mt-3 space-y-2">
            {(inferences.data.inferences || []).slice(0, 8).map((entry) => (
              <li key={entry.id} className="border-b border-border-subtle/30 pb-2 text-sm">
                <p className="font-medium text-foreground">{entry.question}</p>
                <p className="text-muted-foreground">{entry.reading}</p>
                <p className="text-xs text-muted-foreground">Change it with: {entry.change}</p>
              </li>
            ))}
          </ul>
        )}
        {inferences.data && (
          <p className="mt-3 text-xs text-muted-foreground">
            {inferences.data.count} in total, served at <code>/api/wf-033/inferences</code>.
          </p>
        )}
      </Card>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Tracking and credits                                                       */
/* -------------------------------------------------------------------------- */

function BillingTab({ actor, onChanged }) {
  const tracked = useAsync(() => intentApi.tracked(), [])
  const credits = useAsync(() => intentApi.credits(), [])
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)

  if (tracked.loading || credits.loading) return <Spinner label="Loading tracking and credits" />
  if (tracked.error) return <ErrorNote error={tracked.error} onRetry={tracked.refetch} />
  if (credits.error) return <ErrorNote error={credits.error} onRetry={credits.refetch} />

  const ledger = credits.data || {}

  async function renew() {
    setBusy(true)
    setResult(null)
    try {
      const response = await intentApi.renew(actor)
      setResult({
        tone: 'success',
        text: `Billing period ${response.period}: ${response.renewed.length} tracked companies, ${response.charged_total} credits charged.`,
      })
      tracked.refetch()
      credits.refetch()
      onChanged?.()
    } catch (error) {
      setResult({ tone: 'danger', text: String(error?.message || error) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-5">
      <Notice tone="info" title="The billing rule, in the research's own words">
        {ledger.rule}
      </Notice>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard label="Charged" value={ledger.total_charged ?? 0} icon="database" hint="Credits" />
        <StatCard
          label="Saved"
          value={ledger.total_waived ?? 0}
          icon="database"
          hint="Add and track in one period"
        />
        <StatCard
          label="Tracked companies"
          value={tracked.data?.count ?? 0}
          icon="refresh"
          hint="Charged monthly while tracked"
        />
      </div>

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-mono text-sm font-semibold text-foreground">
            Under continuous tracking
          </h2>
          <Button icon="refresh" onClick={renew} disabled={busy}>
            {busy ? 'Charging' : 'Charge this period'}
          </Button>
        </div>
        {result && <p className="mt-2 text-sm">{result.text}</p>}
        {(tracked.data?.tracked || []).length === 0 ? (
          <p className="mt-3 text-sm text-muted-foreground">
            No company is being tracked. Switch &ldquo;Track intent signals&rdquo; on for a saved view
            and run it.
          </p>
        ) : (
          <ul className="mt-3 space-y-1">
            {(tracked.data?.tracked || []).map((row) => (
              <li
                key={row.id}
                className="flex flex-wrap items-baseline justify-between gap-2 text-sm"
              >
                <span className="truncate font-mono text-foreground">{row.company_key}</span>
                <span className="text-muted-foreground">
                  since {absoluteTime(row.since)} &middot; charged in{' '}
                  {(row.periods || []).join(', ')}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <h2 className="font-mono text-sm font-semibold text-foreground">Credit ledger</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          One row per company per billing period, which is what makes &ldquo;charged once, not for
          both actions separately&rdquo; structural rather than a conditional. &ldquo;Saved&rdquo; is
          the amount the second action of the period would have cost.
        </p>
        {(ledger.entries || []).length === 0 ? (
          <p className="mt-3 text-sm text-muted-foreground">Nothing charged yet.</p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[36rem] text-left text-sm">
              <thead>
                <tr className="border-b border-border-subtle/40 text-xs tracking-wide text-muted-foreground uppercase">
                  <th className="py-2 pr-3 font-medium">Company</th>
                  <th className="py-2 pr-3 font-medium">Period</th>
                  <th className="py-2 pr-3 font-medium">Actions</th>
                  <th className="py-2 pr-3 font-medium">Charged</th>
                  <th className="py-2 font-medium">Saved</th>
                </tr>
              </thead>
              <tbody>
                {(ledger.entries || []).map((row) => (
                  <tr key={row.id} className="border-b border-border-subtle/20">
                    <td className="truncate py-2 pr-3 font-mono text-foreground">
                      {row.company_key}
                    </td>
                    <td className="py-2 pr-3 font-mono text-muted-foreground">{row.period}</td>
                    <td className="py-2 pr-3 text-muted-foreground">
                      {(row.actions || []).join(' then ')}
                    </td>
                    <td className="py-2 pr-3 font-mono text-foreground">{row.amount}</td>
                    <td className="py-2 font-mono text-foreground">{row.waived || 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}

export default IntentCompanies
