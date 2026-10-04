import { useState } from 'react'

import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Icon,
  Spinner,
  StatCard,
  useAsync,
} from '@/components/ui'

import { documentState, formatInstant, plural, proposalApi } from './api'
import {
  BrandTokenRow,
  Dialog,
  LifecyclePanel,
  ModuleList,
  ProposalHeader,
  ReportPanel,
  SectionHeading,
} from './primitives'

/**
 * WF-093: build a branded proposal from a template.
 *
 * The page has four jobs, in this order, and the order is the design.
 *
 * **Say what the merge produced, before anything else.** The specification's whole point
 * is that a proposal is a merge of a template with a quote. So the rendered document is
 * the first thing on the page, not a list of templates with a button to render one.
 *
 * **Say what the merge could not do.** A proposal that rendered with two unresolved
 * bindings and dropped three line items past the researched cap must say so before a
 * seller sends it. A board that showed only a green tick would be the failure this
 * workflow exists to prevent, so the report sits beside the document, not on another tab.
 *
 * **Say which level won.** The evidence fixes that "properties set on the quote override
 * the quote template's settings", and it lets a template override a brand kit. Each
 * branding token and each resolved binding names the level that answered, because a
 * reader who cannot tell a seller's brand from a per-quote override cannot review one.
 *
 * **Claim nothing the research did not.** The research names three document models and
 * chooses none. This page says which one was chosen, why, and what the other two would
 * have cost. It also says that a custom-coded module can be selected but never authored
 * through this API, because the evidence says exactly that.
 *
 * Every state this page can be in is rendered: loading, error, and empty. A board that
 * goes blank when the API is down reads as "there is nothing here", which for a quoting
 * page is the one reading that must never be possible.
 */

const ROOM = 'room_a'

/** The status line after a write. One shape, so every outcome reads the same way. */
function Outcome({ outcome, onDismiss }) {
  if (!outcome) return null
  const failed = outcome.status === 'error'
  return (
    <div
      role="status"
      className={`flex flex-wrap items-center justify-between gap-3 rounded-sm border p-4 ${
        failed ? 'border-destructive/40 bg-destructive/10' : 'border-accent/30 bg-accent/10'
      }`}
    >
      <div className="min-w-0">
        <p className={`text-sm font-semibold ${failed ? 'text-destructive' : 'text-accent'}`}>
          {outcome.title}
        </p>
        <p className="mt-0.5 text-sm text-muted-foreground">{outcome.detail}</p>
        {outcome.errors ? (
          <ul className="mt-2 space-y-1">
            {Object.entries(outcome.errors).map(([field, message]) => (
              <li key={field} className="text-xs text-foreground">
                <span className="font-mono">{field}</span>: {message}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <Button icon="close" onClick={onDismiss}>
        Dismiss
      </Button>
    </div>
  )
}

/** The authoring form for a template or a brand, whichever the caller asked for. */
function AuthorForm({ kind, onSubmit, onClose, busy, error }) {
  const [name, setName] = useState(kind === 'brand' ? 'Halcyon Cloud' : 'Standard proposal')
  const [accent, setAccent] = useState('#10506f')
  const [companyName, setCompanyName] = useState('')
  const [fallback, setFallback] = useState(false)

  return (
    <Dialog open title={kind === 'brand' ? 'New brand kit' : 'New template'} onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          onSubmit(
            kind === 'brand'
              ? {
                  name,
                  accent,
                  company_name: companyName,
                  show_company_name_when_no_logo: fallback,
                }
              : { name },
          )
        }}
      >
        <Field label="Name" id={`wf093-${kind}-name`}>
          <input
            id={`wf093-${kind}-name`}
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground"
          />
        </Field>

        {kind === 'brand' ? (
          <>
            <Field
              label="Accent colour"
              hint="A hex colour such as #10506f. Anything else is refused rather than stored."
              id="wf093-brand-accent"
            >
              <input
                id="wf093-brand-accent"
                value={accent}
                onChange={(event) => setAccent(event.target.value)}
                className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 font-mono text-sm text-foreground"
              />
            </Field>
            <Field label="Company name" id="wf093-brand-company">
              <input
                id="wf093-brand-company"
                value={companyName}
                onChange={(event) => setCompanyName(event.target.value)}
                className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground"
              />
            </Field>
            <label className="flex min-h-11 items-center gap-2 text-sm text-foreground">
              <input
                type="checkbox"
                checked={fallback}
                onChange={(event) => setFallback(event.target.checked)}
                className="h-4 w-4"
              />
              Show the company name when no logo is set
            </label>
          </>
        ) : (
          <p className="text-xs text-muted-foreground">
            A template is layout, module order, branding tokens and bindings. It is never a
            rendered document. Every module the specification names is included; a template
            may hide any of them.
          </p>
        )}

        {error ? (
          <ul className="space-y-1 rounded-sm border border-destructive/30 bg-destructive/10 p-3">
            {Object.entries(error).map(([field, message]) => (
              <li key={field} className="text-xs text-foreground">
                <span className="font-mono">{field}</span>: {message}
              </li>
            ))}
          </ul>
        ) : null}

        <div className="flex justify-end gap-2">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? 'Saving' : `Save ${kind}`}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

/** The eight recorded decisions, each with what it rejected. */
function DecisionList({ decisions }) {
  if (!decisions?.length) return null
  return (
    <div className="space-y-3">
      {decisions.map((decision) => (
        <Card key={decision.id} className="p-4">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <h3 className="text-base font-semibold text-foreground">{decision.question}</h3>
            <Badge tone="info">{decision.chosen}</Badge>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">{decision.left_open_by}</p>
          <div className="mt-3">
            <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              What was rejected, and what it would have cost
            </p>
            <p className="mt-1 text-sm text-foreground">{decision.rejected_because}</p>
            <p className="mt-2 text-xs text-muted-foreground">{decision.cost_of_the_choice}</p>
          </div>
        </Card>
      ))}
    </div>
  )
}

/** The three document models the research names, and which one was chosen. */
function ModelChoice({ models }) {
  if (!models?.length) return null
  const chosen = models.find((model) => model.chosen) || models[0]
  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-base font-semibold text-foreground">Document model</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            The research names three and chooses none. This is the one that was chosen.
          </p>
        </div>
        <Badge tone="success">Chosen</Badge>
      </div>
      <p className="mt-3 text-sm font-medium text-foreground">{chosen.label}</p>
      <ul className="mt-3 space-y-2">
        {models
          .filter((model) => !model.chosen)
          .map((model) => (
            <li key={model.id} className="text-xs text-foreground">
              <span className="font-medium">Not chosen: {model.label}.</span>{' '}
              {model.rejection}
            </li>
          ))}
      </ul>
    </Card>
  )
}

/** The templates on offer, each with the custom-module flag the evidence requires. */
function TemplateList({ templates, onUse, busy }) {
  if (!templates?.length) {
    return (
      <EmptyState
        title="No templates yet"
        description="A template is layout, module order, branding tokens and bindings. Create one to merge a proposal."
      />
    )
  }
  return (
    <div className="space-y-3">
      {templates.map((template) => (
        <Card key={template.id} className="p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className="text-base font-semibold text-foreground">{template.name}</h3>
              <p className="mt-0.5 font-mono text-xs text-muted-foreground">{template.key}</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {template.carry_custom_modules ? (
                <Badge tone="warning">Carries a custom-coded module</Badge>
              ) : null}
              <Button onClick={() => onUse(template.id)} disabled={busy} icon="plus">
                Merge this template
              </Button>
            </div>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground">Renders</span>
            {(template.rendered || []).map((module) => (
              <Badge key={module} tone="neutral">
                {module}
              </Badge>
            ))}
          </div>
          {(template.hidden || []).length > 0 ? (
            <p className="mt-2 text-xs text-muted-foreground">
              Hidden: {(template.hidden || []).join(', ')}
            </p>
          ) : null}
        </Card>
      ))}
    </div>
  )
}

/** The quotes this workflow reads, and where they come from. */
function QuoteList({ quotes, source, onUse, busy }) {
  if (!quotes?.length) {
    return (
      <EmptyState
        title="No quotes to merge into"
        description={`${source?.collection || 'wf086_quote'} is provisioned by ${
          source?.provisioned_by || 'WF-086'
        } and read by this workflow. It is never written here, so an empty list is a supported state rather than a failure.`}
      />
    )
  }
  return (
    <div className="space-y-3">
      {quotes.map((quote) => (
        <Card key={quote.id} className="p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className="text-base font-semibold text-foreground">
                {quote.title || 'Untitled quote'}
              </h3>
              <p className="mt-0.5 font-mono text-xs text-muted-foreground">{quote.id}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {quote.company_name || 'No company on the quote'} &middot;{' '}
                {quote.currency_label || 'no currency'} &middot; issued{' '}
                {formatInstant(quote.issue_date)}
              </p>
            </div>
            <Button onClick={() => onUse(quote.id)} disabled={busy} icon="plus">
              Merge into this quote
            </Button>
          </div>
        </Card>
      ))}
    </div>
  )
}

/** The rendered documents, newest first, each with its state in words. */
function DocumentList({ documents, onOpen, onTransition, busy }) {
  if (!documents?.length) {
    return (
      <EmptyState
        title="No proposals rendered yet"
        description="Merge a template with a quote and the document is stored here as a snapshot."
      />
    )
  }
  return (
    <div className="space-y-3">
      {documents.map((document) => {
        const state = documentState(document.state)
        const missing = (document.unresolved_bindings || []).length
        const dropped = (document.truncated || {}).dropped || 0
        return (
          <Card key={document.id} className="p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={state.tone}>{state.label}</Badge>
                  {missing > 0 ? <Badge tone="warning">{plural(missing, 'unresolved binding')}</Badge> : null}
                  {dropped > 0 ? <Badge tone="warning">{plural(dropped, 'dropped line item')}</Badge> : null}
                </div>
                <p className="mt-2 break-all font-mono text-xs text-muted-foreground">
                  {document.id}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {plural(document.line_item_count, 'line item')} from template{' '}
                  <span className="font-mono">{document.template_key || document.template_id}</span>
                  {document.published_at ? `, published ${formatInstant(document.published_at)}` : ''}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button onClick={() => onOpen(document.id)}>Open</Button>
                {document.state !== 'published' ? (
                  <Button
                    onClick={() => onTransition(document.id, 'publish')}
                    disabled={busy}
                    icon="audit"
                  >
                    Publish
                  </Button>
                ) : null}
              </div>
            </div>
          </Card>
        )
      })}
    </div>
  )
}

/** The one rendered document, in full, with its report beside it. */
function DocumentDetail({ document, onTransition, onClose, busy }) {
  if (!document) return null
  const header = (document.modules || []).find((module) => module.module === 'header')
  const tokens = Object.values((document.branding || {}).tokens || {})
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Rendered proposal</h2>
          <p className="mt-0.5 break-all font-mono text-xs text-muted-foreground">
            {document.id}
          </p>
        </div>
        <Button onClick={onClose} icon="close">
          Close
        </Button>
      </div>

      <ProposalHeader header={header} brandName={(document.branding || {}).brand_id} />

      {tokens.length > 0 ? (
        <Card className="p-4">
          <h3 className="text-base font-semibold text-foreground">Brand tokens</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Which level set each token. The brand kit loses to the template when &quot;override
            brand kit&quot; is on, and both lose to the quote.
          </p>
          <div className="mt-3">
            {tokens.map((token) => (
              <BrandTokenRow key={token.token} token={token} />
            ))}
          </div>
        </Card>
      ) : null}

      <ModuleList modules={document.modules} />

      <ReportPanel
        unresolved={document.unresolved_bindings}
        truncated={document.truncated}
        placeholderBounds={document.placeholder_bounds}
        precedence={document.precedence}
      />

      <LifecyclePanel document={document} onTransition={onTransition} busy={busy} />

      <Card className="p-4">
        <h3 className="text-base font-semibold text-foreground">Custom-coded modules</h3>
        <p className="mt-2 text-sm text-foreground">
          {document.custom_module_advisory?.note}
        </p>
        <p className="mt-2 text-xs text-muted-foreground">
          &quot;{document.custom_module_advisory?.evidence}&quot;
        </p>
      </Card>
    </div>
  )
}

function ProposalPage() {
  const [templateId, setTemplateId] = useState('')
  const [quoteId, setQuoteId] = useState('')
  const [openId, setOpenId] = useState('')
  const [outcome, setOutcome] = useState(null)
  const [busy, setBusy] = useState(false)
  const [authoring, setAuthoring] = useState(null)
  const [formError, setFormError] = useState(null)

  const board = useAsync(
    () =>
      Promise.all([
        proposalApi.summary(ROOM),
        proposalApi.vocabulary(),
        proposalApi.decisions(),
        proposalApi.templates(ROOM),
        proposalApi.brands(ROOM),
        proposalApi.quotes(ROOM),
        proposalApi.documents(ROOM),
      ]).then(([summary, vocabulary, decisions, templates, brands, quotes, documents]) => ({
        summary,
        vocabulary,
        decisions,
        templates,
        brands,
        quotes,
        documents,
      })),
    [],
  )

  const open = useAsync(
    () => (openId ? proposalApi.document(openId) : Promise.resolve(null)),
    [openId],
  )

  async function run(work, title) {
    setBusy(true)
    try {
      const result = await work()
      setOutcome({ status: 'ok', title, detail: 'The board has been reloaded.' })
      await board.refetch()
      return result
    } catch (error) {
      setOutcome({
        status: 'error',
        title: 'That did not complete',
        detail: error.message,
        errors: error.errors,
      })
      if (error.status === 403 && error.body?.remediation) {
        await board.refetch()
        if (openId) await open.refetch()
      }
      return null
    } finally {
      setBusy(false)
    }
  }

  async function transition(documentId, action) {
    await run(
      () => proposalApi.transition(documentId, action, { actor: 'dana' }),
      action === 'publish' ? 'Published' : 'The document was re-rendered',
    )
    if (openId) await open.refetch()
  }

  async function author(payload) {
    const kind = authoring
    setFormError(null)
    setBusy(true)
    try {
      if (kind === 'brand') {
        await proposalApi.saveBrand(payload, { roomId: ROOM, actor: 'dana' })
      } else {
        await proposalApi.saveTemplate(payload, { roomId: ROOM, actor: 'dana' })
      }
      setAuthoring(null)
      setOutcome({ status: 'ok', title: `Saved the ${kind}`, detail: payload.name })
      await board.refetch()
    } catch (error) {
      setFormError(error.errors || { detail: error.message })
    } finally {
      setBusy(false)
    }
  }

  if (board.loading) return <Spinner label="Loading proposals" />
  if (board.error) return <ErrorNote error={board.error} onRetry={board.refetch} />

  const {
    summary,
    vocabulary,
    decisions,
    templates,
    brands,
    quotes,
    documents,
  } = board.data

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-foreground">Branded proposals</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          A proposal is a merge of a template definition with quote record data. The result
          is stored as the quote&apos;s presentation layer, and a published one is never
          rewritten by a later template change.
        </p>
      </header>

      <Outcome outcome={outcome} onDismiss={() => setOutcome(null)} />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Templates" value={summary.templates} icon="schema" />
        <StatCard label="Brand kits" value={summary.brands} icon="rooms" />
        <StatCard label="Proposals" value={summary.documents} icon="audit" />
        <StatCard
          label="Quotes read"
          value={summary.quotes_read}
          hint={`${summary.line_items_read} priced line items`}
          icon="database"
        />
      </div>

      {(summary.unresolved_bindings > 0 || summary.truncated_line_items > 0) && (
        <Card className="border-warning/40 bg-warning/10 p-4">
          <div className="flex items-start gap-3">
            <Icon name="schema" className="mt-0.5 text-warning" />
            <div>
              <p className="text-sm font-semibold text-foreground">
                Proposals here are not complete
              </p>
              <p className="mt-1 text-sm text-muted-foreground">
                {plural(summary.unresolved_bindings, 'binding')} resolved to nothing and{' '}
                {plural(summary.truncated_line_items, 'line item')} fell past the researched
                cap. Open a proposal to see which.
              </p>
            </div>
          </div>
        </Card>
      )}

      {open.data ? (
        <DocumentDetail
          document={open.data}
          busy={busy}
          onClose={() => setOpenId('')}
          onTransition={(action) => transition(openId, action)}
        />
      ) : null}

      <section className="space-y-3">
        <SectionHeading
          title="Rendered proposals"
          hint="Each row is a stored snapshot. A published one does not take a later template change."
          count={plural(documents.count, 'document')}
        />
        {open.loading ? <Spinner label="Loading the proposal" /> : null}
        <DocumentList
          documents={documents.documents}
          busy={busy}
          onOpen={setOpenId}
          onTransition={transition}
        />
      </section>

      <section className="space-y-3">
        <SectionHeading
          title="Build a proposal"
          hint="Pick a template and a quote. Nothing is written until you merge."
        />
        <Card className="p-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block">
              <span className="block text-xs font-medium text-foreground">Template</span>
              <select
                value={templateId}
                onChange={(event) => setTemplateId(event.target.value)}
                className="mt-1 min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground"
              >
                <option value="">Choose a template</option>
                {(templates.templates || []).map((template) => (
                  <option key={template.id} value={template.id}>
                    {template.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="block text-xs font-medium text-foreground">Quote</span>
              <select
                value={quoteId}
                onChange={(event) => setQuoteId(event.target.value)}
                className="mt-1 min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground"
              >
                <option value="">Choose a quote</option>
                {(quotes.quotes || []).map((quote) => (
                  <option key={quote.id} value={quote.id}>
                    {quote.title || quote.id}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              variant="primary"
              disabled={busy || !templateId || !quoteId}
              onClick={() =>
                run(
                  () => proposalApi.instantiate(templateId, quoteId, { roomId: ROOM, actor: 'dana' }),
                  'The proposal was rendered and stored',
                )
              }
              icon="plus"
            >
              Merge and store
            </Button>
            <Button
              disabled={busy || !templateId || !quoteId}
              onClick={async () => {
                setBusy(true)
                try {
                  const preview = await proposalApi.preview(templateId, quoteId)
                  setOutcome({
                    status: 'ok',
                    title: 'Preview only. Nothing was written.',
                    detail: `${plural(
                      (preview.modules || []).length,
                      'module',
                    )} rendered, ${plural(
                      (preview.unresolved_bindings || []).length,
                      'binding',
                    )} unresolved, ${plural(
                      (preview.truncated || {}).dropped || 0,
                      'line item',
                    )} dropped.`,
                  })
                } catch (error) {
                  setOutcome({ status: 'error', title: 'The preview failed', detail: error.message })
                } finally {
                  setBusy(false)
                }
              }}
              icon="search"
            >
              Preview without writing
            </Button>
          </div>
          <p className="mt-3 text-xs text-muted-foreground">
            Preview resolves no change and records no audit row. Merging stores the document,
            and the audit row names the route that served it.
          </p>
        </Card>
      </section>

      <section className="space-y-3">
        <SectionHeading
          title="Templates"
          hint="Layout, module order, branding tokens and bindings. Never a rendered document."
          count={plural(templates.count, 'template')}
        />
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => setAuthoring('template')} icon="plus">
            New template
          </Button>
          <Button onClick={() => setAuthoring('brand')} icon="plus">
            New brand kit
          </Button>
        </div>
        <TemplateList
          templates={templates.templates}
          busy={busy}
          onUse={(id) => {
            setTemplateId(id)
            setOutcome({
              status: 'ok',
              title: 'Template chosen',
              detail: 'Choose a quote and merge.',
            })
          }}
        />
      </section>

      <section className="space-y-3">
        <SectionHeading
          title="Quotes this workflow reads"
          hint={`Read from ${quotes.collection}, provisioned by ${quotes.provisioned_by}. Never written here.`}
          count={plural(quotes.count, 'quote')}
        />
        <QuoteList
          quotes={quotes.quotes}
          source={quotes}
          busy={busy}
          onUse={(id) => {
            setQuoteId(id)
            setOutcome({ status: 'ok', title: 'Quote chosen', detail: 'Choose a template and merge.' })
          }}
        />
      </section>

      <section className="space-y-3">
        <SectionHeading
          title="Brand kits"
          hint="A brand drives the logo and the brand-kit colours. A template may override it."
          count={plural(brands.count, 'brand')}
        />
        {brands.count === 0 ? (
          <EmptyState
            title="No brand kits"
            description="A brand kit carries the accent colour, the logo and the company name used when no logo is set."
          />
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {brands.brands.map((brand) => (
              <Card key={brand.id} className="p-4">
                <h3 className="text-base font-semibold text-foreground">{brand.name}</h3>
                <p className="mt-0.5 font-mono text-xs text-muted-foreground">{brand.key}</p>
                <div className="mt-3">
                  {Object.entries(brand.tokens || {}).map(([token, entry]) => (
                    <div
                      key={token}
                      className="flex items-center justify-between gap-2 border-t border-border-subtle py-1.5 first:border-t-0"
                    >
                      <span className="text-xs text-muted-foreground">{entry.label}</span>
                      <span className="font-mono text-xs text-foreground">
                        {entry.value ? String(entry.value) : 'unset'}
                      </span>
                    </div>
                  ))}
                </div>
                {brand.show_company_name_when_no_logo ? (
                  <p className="mt-2 text-xs text-muted-foreground">
                    Falls back to the company name when no logo is set.
                  </p>
                ) : null}
              </Card>
            ))}
          </div>
        )}
        <Card className="p-4">
          <p className="text-xs text-muted-foreground">{brands.logo_source_note}</p>
          <ul className="mt-2 space-y-1">
            {(brands.logo_sources || []).map((source, index) => (
              <li key={source} className="text-xs text-foreground">
                {index + 1}. {source.replace(/_/g, ' ')}
                {index === 0 ? ' (wins)' : ''}
              </li>
            ))}
          </ul>
        </Card>
      </section>

      <section className="space-y-3">
        <SectionHeading
          title="Document model"
          hint="What the research named and what was chosen."
        />
        <ModelChoice models={vocabulary.document_models} />
      </section>

      <section className="space-y-3">
        <SectionHeading
          title="Decisions"
          hint="Every judgement call this workflow made, with the alternative it rejected."
          count={plural(decisions.count, 'decision')}
        />
        <DecisionList decisions={decisions.decisions} />
      </section>

      {authoring ? (
        <AuthorForm
          kind={authoring}
          busy={busy}
          error={formError}
          onClose={() => {
            setAuthoring(null)
            setFormError(null)
          }}
          onSubmit={author}
        />
      ) : null}
    </div>
  )
}

export default {
  id: 'wf-093-build-a-branded-proposal-from-a-template',
  label: 'Branded proposals',
  icon: 'audit',
  order: 930,
  Component: ProposalPage,
}