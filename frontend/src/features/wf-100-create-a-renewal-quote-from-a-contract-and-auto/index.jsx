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
  inputClass,
  useAsync,
} from '@/components/ui'

import {
  CONTRACT_TARGETS,
  DEAL_SELECTION_METHODS,
  EFFECTIVE_DATE_MODES,
  contractTarget,
  formatDate,
  formatMoney,
  plural,
  quoteState,
  renewalApi,
} from './api'
import {
  ChainTrail,
  CheckRow,
  DecisionCard,
  QuoteRow,
  SectionHeading,
  ValueRow,
} from './primitives'

/**
 * WF-100: create a renewal quote from a contract and auto-create the renewal deal.
 *
 * The page has four jobs, in this order, and the order is the design.
 *
 * **Say what acceptance produced, before anything else.** The specification's whole point is
 * that a renewal quote becomes a new contract and a renewal deal. So the contracts and their
 * chains are the first thing on the page, not a list of quotes with a button to accept one.
 *
 * **Say what acceptance is, and is not.** The research never says who accepts a renewal quote
 * or how the room learns that it happened. This page makes acceptance an explicit action, says
 * that the signal was not sourced, and carries the Jev audit id for that choice. A page that
 * let a reviewer assume the platform learned of acceptance on its own would be asserting
 * something the evidence does not support.
 *
 * **Say which branch of the renewal date rule answered.** The evidence states the rule as a
 * conditional and gives both branches, so each contract names the branch that produced its
 * renewal date and the sourced sentence behind it.
 *
 * **Claim nothing the research did not.** The direct renewal bypass is not built, the workflow
 * action has no vendor endpoint, and the template collection is owned here because no pending
 * ticket provisions one. Each is stated on the page rather than left for a reviewer to find.
 *
 * Every state this page can be in is rendered: loading, error, and empty. A board that goes
 * blank when the API is down reads as "there is nothing here", which for a quoting page is the
 * one reading that must never be possible.
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
        {outcome.remedy ? (
          <p className="mt-2 text-xs text-foreground">To fix it: {outcome.remedy}</p>
        ) : null}
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

/** One contract, its renewal date, its alert, and where it sits in the chain. */
function ContractCard({ contract, onRenew, onOpen, busy }) {
  const renewal = contract.renewal || {}
  const evergreen = contract.term_label === 'Evergreen'
  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            {evergreen ? (
              <Badge tone="info">Evergreen</Badge>
            ) : (
              <Badge tone="neutral">{plural(contract.line_items?.length || 0, 'line item')}</Badge>
            )}
            <Badge tone={contract.renewable?.renewable ? 'success' : 'warning'}>
              {contract.renewable?.renewable ? 'Renewable now' : 'Not renewable now'}
            </Badge>
          </div>
          <h3 className="mt-2 truncate text-base font-semibold text-foreground">
            {contract.name || contract.id}
          </h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {contract.buyer || 'No buyer on the contract'} &middot; term{' '}
            <span className="font-mono">{contract.term_label}</span>
          </p>
        </div>
        <Button
          variant="primary"
          icon="plus"
          disabled={busy || !contract.renewable?.renewable}
          onClick={() => onRenew(contract.id)}
        >
          Create renewal quote
        </Button>
      </div>

      <dl className="mt-3">
        <ValueRow label="Term length" value={contract.term_label} mono />
        <ValueRow label="Contract ends" value={formatDate(contract.end_date)} mono />
        <ValueRow label="Renewal date" value={formatDate(renewal.renewal_date)} mono />
        <ValueRow
          label="Alert falls due"
          value={contract.alert?.due ? formatDate(contract.alert.due) : 'no alert'}
          mono
        />
        <ValueRow
          label="Rule that answered"
          value={renewal.branch === 'if_finalised' ? 'Renewal finalised' : 'Not yet finalised'}
        />
      </dl>

      {contract.renewable && !contract.renewable.renewable ? (
        <p className="mt-3 text-xs text-muted-foreground">{contract.renewable.reason}</p>
      ) : null}
      {evergreen ? (
        <p className="mt-3 text-xs text-muted-foreground">
          Every line item renews until cancelled, so the term length is labelled Evergreen. That
          is a label on the record, not a separate contract type.
        </p>
      ) : null}

      <div className="mt-3 border-t border-border-subtle pt-3">
        <ChainTrail chain={contract.chain} onOpen={onOpen} />
      </div>
    </Card>
  )
}

/** The one contract in full, with the sourced sentence behind its renewal date. */
function ContractDetail({ contract, onClose, onOpen }) {
  const renewal = contract.renewal || {}
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-foreground">{contract.name || contract.id}</h2>
          <p className="mt-0.5 break-all font-mono text-xs text-muted-foreground">{contract.id}</p>
        </div>
        <Button onClick={onClose} icon="close">
          Close
        </Button>
      </div>

      <Card className="p-4">
        <h3 className="text-base font-semibold text-foreground">Commercial content</h3>
        <dl className="mt-3">
          <ValueRow label="Seller" value={contract.seller || 'not set'} />
          <ValueRow label="Buyer" value={contract.buyer || 'not set'} />
          <ValueRow label="Currency" value={contract.currency || 'not set'} mono />
          <ValueRow label="Term length" value={contract.term_label} mono />
          <ValueRow label="Starts" value={formatDate(contract.start_date)} mono />
          <ValueRow label="Ends" value={formatDate(contract.end_date)} mono />
          <ValueRow label="Total" value={formatMoney(contract.total, contract.currency)} mono />
        </dl>
        <div className="mt-3 space-y-1 border-t border-border-subtle pt-3">
          {(contract.line_items || []).map((item, index) => (
            <div
              key={item.sku || index}
              className="flex items-baseline justify-between gap-3 text-xs"
            >
              <span className="font-mono text-foreground">{item.sku || `line ${index + 1}`}</span>
              <span className="font-mono text-muted-foreground">
                {plural(item.quantity, 'unit')} &middot;{' '}
                {formatMoney(item.amount, contract.currency)}
              </span>
            </div>
          ))}
        </div>
      </Card>

      <Card className="p-4">
        <h3 className="text-base font-semibold text-foreground">Renewal date</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          The research states this as a conditional, so the page says which branch answered and
          quotes the sentence behind it.
        </p>
        <dl className="mt-3">
          <ValueRow label="Renewal date" value={formatDate(renewal.renewal_date)} mono />
          <ValueRow
            label="Branch"
            value={renewal.branch === 'if_finalised' ? 'A renewal was finalised' : 'Not finalised'}
          />
          <ValueRow label="From quote" value={renewal.source_quote_id || 'none'} mono />
        </dl>
        <p className="mt-3 border-l-2 border-border-subtle pl-3 text-xs italic text-muted-foreground">
          {renewal.rule}
        </p>
      </Card>

      <Card className="p-4">
        <h3 className="text-base font-semibold text-foreground">Renewal chain</h3>
        <div className="mt-3">
          <ChainTrail chain={contract.chain} onOpen={onOpen} />
        </div>
      </Card>

      {contract.quotes?.length ? (
        <Card className="p-4">
          <h3 className="text-base font-semibold text-foreground">
            {plural(contract.quotes.length, 'renewal quote')}
          </h3>
          <ul className="mt-2 space-y-1">
            {contract.quotes.map((id) => (
              <li key={id} className="break-all font-mono text-xs text-foreground">
                {id}
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  )
}

/** The form that builds a quote from a contract. */
function QuoteForm({
  contracts,
  templates,
  pipelines,
  presetContractId,
  onSubmit,
  busy,
}) {
  const [contractId, setContractId] = useState(presetContractId || '')
  const [templateId, setTemplateId] = useState('')
  const [mode, setMode] = useState('on_agreement')
  const [on, setOn] = useState('')
  const [delayDays, setDelayDays] = useState('30')
  const [delayMonths, setDelayMonths] = useState('3')
  const [prorate, setProrate] = useState(true)
  const [selection, setSelection] = useState('new_deal_default_stage')
  const [pipelineId, setPipelineId] = useState('')

  const chosen = pipelines.find((pipeline) => pipeline.id === pipelineId)
  const [stage, setStage] = useState('')

  function effectiveDateField() {
    if (mode === 'custom_date') {
      return (
        <Field label="Change effective date" id="wf100-date">
          <input
            id="wf100-date"
            type="date"
            value={on}
            onChange={(event) => setOn(event.target.value)}
            className={`${inputClass} font-mono`}
          />
        </Field>
      )
    }
    if (mode === 'delayed_start') {
      return (
        <Field
          label="Days after agreement"
          hint="The change takes effect this many days after the day the buyer accepts."
          id="wf100-days"
        >
          <input
            id="wf100-days"
            type="number"
            min="0"
            value={delayDays}
            onChange={(event) => setDelayDays(event.target.value)}
            className={`${inputClass} font-mono`}
          />
        </Field>
      )
    }
    if (mode === 'months') {
      return (
        <Field
          label="Months after agreement"
          hint="The change takes effect this many months after the day the buyer accepts."
          id="wf100-months"
        >
          <input
            id="wf100-months"
            type="number"
            min="0"
            value={delayMonths}
            onChange={(event) => setDelayMonths(event.target.value)}
            className={`${inputClass} font-mono`}
          />
        </Field>
      )
    }
    return (
      <p className="text-xs text-muted-foreground">
        The change takes effect on the day the buyer accepts, so the quote reports no date until
        it is accepted.
      </p>
    )
  }

  const ready = Boolean(contractId) && (selection !== 'new_deal_default_stage' || (pipelineId && stage))

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit({
          contract_id: contractId,
          template_id: templateId || null,
          effective_date_mode: mode,
          effective_date_on: mode === 'custom_date' ? on : null,
          delay_days: mode === 'delayed_start' ? Number(delayDays) : null,
          delay_months: mode === 'months' ? Number(delayMonths) : null,
          prorate,
          deal_selection_method: selection,
          deal_pipeline_id: selection === 'new_deal_default_stage' ? pipelineId : null,
          deal_stage: selection === 'new_deal_default_stage' ? stage : null,
        })
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Contract" id="wf100-contract">
          <select
            id="wf100-contract"
            value={contractId}
            onChange={(event) => setContractId(event.target.value)}
            className={inputClass}
          >
            <option value="">Choose an expiring contract</option>
            {contracts.map((contract) => (
              <option key={contract.id} value={contract.id} disabled={!contract.renewable?.renewable}>
                {contract.name || contract.id}
                {contract.renewable?.renewable ? '' : ' (already renewed)'}
              </option>
            ))}
          </select>
        </Field>

        <Field
          label="Quote template"
          hint={
            templates.length
              ? 'A renewal or change template supplies the term and the discount format.'
              : 'No templates yet. A quote can be created without one.'
          }
          id="wf100-template"
        >
          <select
            id="wf100-template"
            value={templateId}
            onChange={(event) => setTemplateId(event.target.value)}
            className={inputClass}
          >
            <option value="">No template</option>
            {templates.map((template) => (
              <option key={template.id} value={template.id}>
                {template.name} ({template.change_type})
              </option>
            ))}
          </select>
        </Field>
      </div>

      <Field
        label="Change effective date"
        hint="The research names four modes for the quote editor's Summary module."
        id="wf100-mode"
      >
        <select
          id="wf100-mode"
          value={mode}
          onChange={(event) => setMode(event.target.value)}
          className={inputClass}
        >
          {EFFECTIVE_DATE_MODES.map((entry) => (
            <option key={entry.value} value={entry.value}>
              {entry.label}
            </option>
          ))}
        </select>
      </Field>

      {effectiveDateField()}

      <CheckRow
        id="wf100-prorate"
        label="Prorate charges and credits for the remaining billing period"
        hint="Clear it and the remaining billing period carries no prorated charge and no prorated credit."
        checked={prorate}
        onChange={setProrate}
      />

      <Field
        label="Deal selection"
        hint="The research offers a new deal in a chosen pipeline and stage, or an existing deal."
        id="wf100-selection"
      >
        <select
          id="wf100-selection"
          value={selection}
          onChange={(event) => setSelection(event.target.value)}
          className={inputClass}
        >
          {DEAL_SELECTION_METHODS.map((method) => (
            <option key={method.value} value={method.value}>
              {method.label}
            </option>
          ))}
        </select>
      </Field>

      {selection === 'new_deal_default_stage' ? (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Deal pipeline" id="wf100-pipeline">
            <select
              id="wf100-pipeline"
              value={pipelineId}
              onChange={(event) => {
                setPipelineId(event.target.value)
                setStage('')
              }}
              className={inputClass}
            >
              <option value="">Choose a pipeline</option>
              {pipelines.map((pipeline) => (
                <option key={pipeline.id} value={pipeline.id}>
                  {pipeline.name}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label="Deal stage"
            hint={chosen ? 'The stages this pipeline offers.' : 'Choose a pipeline first.'}
            id="wf100-stage"
          >
            <select
              id="wf100-stage"
              value={stage}
              onChange={(event) => setStage(event.target.value)}
              disabled={!chosen}
              className={inputClass}
            >
              <option value="">Choose a stage</option>
              {(chosen?.stages || []).map((entry) => (
                <option key={entry} value={entry}>
                  {entry}
                </option>
              ))}
            </select>
          </Field>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          An existing deal selection needs a deal that already exists, so it is chosen on the deal
          rather than here. This form creates new deals.
        </p>
      )}

      <div className="flex justify-end gap-2">
        <Button type="submit" variant="primary" disabled={busy || !ready}>
          {busy ? 'Creating' : 'Create quote'}
        </Button>
      </div>
    </form>
  )
}

/** The form that registers a template or a pipeline, whichever the caller asked for. */
function SetupForm({ kind, onSubmit, onClose, busy }) {
  const [name, setName] = useState(kind === 'template' ? 'Annual renewal template' : 'Renewals')
  const [termMonths, setTermMonths] = useState('12')
  const [changeType, setChangeType] = useState('renewal')
  const [stages, setStages] = useState('Qualification, Contract sent, Closed won')

  return (
    <Card className="border-accent/30 bg-accent/5 p-4">
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          if (kind === 'template') {
            onSubmit({
              name,
              change_type: changeType,
              term_months: Number(termMonths) || null,
            })
          } else {
            onSubmit({
              name,
              stages: stages
                .split(',')
                .map((entry) => entry.trim())
                .filter(Boolean),
            })
          }
        }}
      >
        <h3 className="text-base font-semibold text-foreground">
          {kind === 'template' ? 'New quote template' : 'New deal pipeline'}
        </h3>
        <Field label="Name" id={`wf100-${kind}-name`}>
          <input
            id={`wf100-${kind}-name`}
            value={name}
            onChange={(event) => setName(event.target.value)}
            className={inputClass}
          />
        </Field>

        {kind === 'template' ? (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Change type" id="wf100-change-type">
              <select
                id="wf100-change-type"
                value={changeType}
                onChange={(event) => setChangeType(event.target.value)}
                className={inputClass}
              >
                <option value="renewal">Renewal</option>
                <option value="change">Change</option>
              </select>
            </Field>
            <Field label="Term in months" id="wf100-term-months">
              <input
                id="wf100-term-months"
                type="number"
                min="1"
                value={termMonths}
                onChange={(event) => setTermMonths(event.target.value)}
                className={`${inputClass} font-mono`}
              />
            </Field>
          </div>
        ) : (
          <Field
            label="Stages"
            hint="Comma separated, in the order the deal moves through them."
            id="wf100-stages"
          >
            <input
              id="wf100-stages"
              value={stages}
              onChange={(event) => setStages(event.target.value)}
              className={inputClass}
            />
          </Field>
        )}

        <div className="flex justify-end gap-2">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? 'Saving' : `Save ${kind}`}
          </Button>
        </div>
      </form>
    </Card>
  )
}

function RenewalQuotePage() {
  const [outcome, setOutcome] = useState(null)
  const [busy, setBusy] = useState(false)
  const [openContractId, setOpenContractId] = useState('')
  const [presetContractId, setPresetContractId] = useState('')
  const [authoring, setAuthoring] = useState(null)

  const board = useAsync(
    () =>
      Promise.all([
        renewalApi.summary(ROOM),
        renewalApi.vocabulary(),
        renewalApi.decisions(),
        renewalApi.contracts(ROOM),
        renewalApi.templates(ROOM),
        renewalApi.pipelines(ROOM),
        renewalApi.quotes(ROOM),
        renewalApi.deals(ROOM),
        renewalApi.workflows(ROOM),
      ]).then(([summary, vocabulary, decisions, contracts, templates, pipelines, quotes, deals, workflows]) => ({
        summary,
        vocabulary,
        decisions,
        contracts,
        templates,
        pipelines,
        quotes,
        deals,
        workflows,
      })),
    [],
  )

  const open = useAsync(
    () => (openContractId ? renewalApi.contract(openContractId) : Promise.resolve(null)),
    [openContractId],
  )

  async function run(work, title, detail) {
    setBusy(true)
    try {
      const result = await work()
      setOutcome({ status: 'ok', title, detail })
      await board.refetch()
      if (openContractId) await open.refetch()
      return result
    } catch (error) {
      setOutcome({
        status: 'error',
        title: 'That did not complete',
        detail: error.message,
        remedy: error.body?.remedy,
        errors: error.errors,
      })
      return null
    } finally {
      setBusy(false)
    }
  }

  async function accept(quoteId) {
    const result = await run(
      () => renewalApi.accept(quoteId, { accepted_by: 'Priya Nair' }, { roomId: ROOM, actor: 'dana' }),
      'The renewal was accepted',
      'A new contract was created and linked, and the renewal deal was created.',
    )
    // Optional, because a 200 that carries no new contract is not a shape this page should
    // crash on. The acceptance still happened and the board is already reloaded.
    if (result?.new_contract?.id) setOpenContractId(result.new_contract.id)
  }

  if (board.loading) return <Spinner label="Loading renewal quotes" />
  if (board.error) return <ErrorNote error={board.error} onRetry={board.refetch} />

  const {
    summary,
    vocabulary,
    decisions,
    contracts,
    templates,
    pipelines,
    quotes,
    deals,
    workflows,
  } = board.data

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-foreground">Renewal quotes</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          A seller opens an expiring contract and creates a renewal quote from it. When the buyer
          accepts, the room creates the new contract, links it to the prior contract as a renewal
          chain, and creates the renewal deal in the chosen pipeline and stage.
        </p>
      </header>

      <Outcome outcome={outcome} onDismiss={() => setOutcome(null)} />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Contracts"
          value={summary.contracts}
          hint={`${summary.renewable_contracts} renewable now`}
          icon="audit"
        />
        <StatCard
          label="Renewal quotes"
          value={summary.quotes}
          hint={`${summary.accepted_quotes} accepted`}
          icon="schema"
        />
        <StatCard
          label="Renewal contracts"
          value={summary.renewal_contracts}
          hint="created by an acceptance"
          icon="rooms"
        />
        <StatCard
          label="Renewal deals"
          value={summary.deals}
          hint="created at acceptance"
          icon="database"
        />
      </div>

      <Card className="border-warning/40 bg-warning/10 p-4">
        <div className="flex items-start gap-3">
          <Icon name="audit" className="mt-0.5 text-warning" />
          <div>
            <p className="text-sm font-semibold text-foreground">
              The acceptance signal was not sourced
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              The research says a new contract is created when a renewal quote is accepted. It
              never says who accepts or how the room learns that it happened. This workflow makes
              acceptance an explicit action rather than inferring it from a status write, so every
              contract carries the acceptance signal as an unsourced fact.
            </p>
            <p className="mt-2 font-mono text-xs text-muted-foreground">
              Jev pass jev-20261004T231639-22752-99672
            </p>
          </div>
        </div>
      </Card>

      {open.data ? (
        <ContractDetail
          contract={open.data}
          onClose={() => setOpenContractId('')}
          onOpen={setOpenContractId}
        />
      ) : null}

      <section className="space-y-3">
        <SectionHeading
          title="Contracts"
          hint="Each row names the branch of the renewal date rule that produced its date."
          count={plural(contracts.count, 'contract')}
        />
        {open.loading ? <Spinner label="Loading the contract" /> : null}
        {contracts.count === 0 ? (
          <EmptyState
            title="No contracts yet"
            description="A renewal quote is built from a contract. Create one in another feature or wait for the demo data."
          />
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {contracts.contracts.map((contract) => (
              <ContractCard
                key={contract.id}
                contract={contract}
                busy={busy}
                onOpen={setOpenContractId}
                onRenew={(id) => {
                  setPresetContractId(id)
                  setOutcome({
                    status: 'ok',
                    title: 'Contract chosen',
                    detail: 'Set the effective date and the deal, then create the quote.',
                  })
                }}
              />
            ))}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <SectionHeading
          title="Create a renewal quote"
          hint="The quote is prefilled from the contract. Nothing is written until you create it."
        />
        <Card className="p-4">
          <QuoteForm
            contracts={contracts.contracts}
            templates={templates.templates}
            pipelines={pipelines.pipelines}
            presetContractId={presetContractId}
            busy={busy}
            onSubmit={(payload) =>
              run(
                () => renewalApi.createQuote(payload, { roomId: ROOM, actor: 'dana' }),
                'The renewal quote was created',
                'It is a draft until the buyer accepts it.',
              )
            }
          />
        </Card>
      </section>

      <section className="space-y-3">
        <SectionHeading
          title="Renewal quotes"
          hint="A draft has no deal. The deal is created when the buyer accepts."
          count={plural(quotes.count, 'quote')}
        />
        {quotes.count === 0 ? (
          <EmptyState
            title="No renewal quotes yet"
            description="Create one from an expiring contract above."
          />
        ) : (
          <div className="space-y-3">
            {quotes.quotes.map((quote) => (
              <QuoteRow
                key={quote.id}
                quote={quote}
                state={quoteState(quote.state)}
                busy={busy}
                onOpen={setOpenContractId}
                onShare={(id) =>
                  run(
                    () => renewalApi.changeState(id, 'shared', { roomId: ROOM, actor: 'dana' }),
                    'The quote is marked shared',
                    'Sharing is a seller action. It changes no data beyond the state.',
                  )
                }
                onAccept={accept}
              />
            ))}
          </div>
        )}
        <p className="text-xs text-muted-foreground">{quotes.derived_not_sourced}</p>
      </section>

      <section className="space-y-3">
        <SectionHeading
          title="Renewal deals"
          hint="Created at acceptance, in the pipeline and stage the seller chose."
          count={plural(deals.count, 'deal')}
        />
        {deals.count === 0 ? (
          <EmptyState
            title="No renewal deals yet"
            description="A deal appears when a renewal quote is accepted, not when it is created."
          />
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {deals.deals.map((deal) => (
              <Card key={deal.id} className="p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="success">Auto-created</Badge>
                  <Badge tone="neutral">{deal.deal_type}</Badge>
                </div>
                <h3 className="mt-2 truncate text-base font-semibold text-foreground">
                  {deal.name || deal.id}
                </h3>
                <dl className="mt-3">
                  <ValueRow label="Pipeline" value={deal.pipeline_name || 'not set'} />
                  <ValueRow label="Stage" value={deal.stage || 'not set'} />
                  <ValueRow label="Tracks quote" value={deal.quote_id} mono />
                  <ValueRow label="New contract" value={deal.new_contract_id} mono />
                </dl>
              </Card>
            ))}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <SectionHeading
          title="Renewal workflows"
          hint="The deal-based action the research names. There is no vendor endpoint underneath it."
          count={plural(workflows.count, 'workflow')}
        />
        {workflows.count === 0 ? (
          <EmptyState
            title="No renewal workflows yet"
            description="A workflow creates a renewal quote per contract on its own schedule."
          />
        ) : (
          <div className="space-y-3">
            {workflows.workflows.map((workflow) => (
              <Card key={workflow.id} className="p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone="info">{contractTarget(workflow.contract_target).label}</Badge>
                      {workflow.re_enroll ? (
                        <Badge tone="warning">Re-enrol on</Badge>
                      ) : (
                        <Badge tone="neutral">Re-enrol off</Badge>
                      )}
                    </div>
                    <h3 className="mt-2 truncate text-base font-semibold text-foreground">
                      {workflow.name}
                    </h3>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      Enrolled on <span className="font-mono">{workflow.enrolled_contract_id || 'no contract'}</span>
                      {workflow.cycles ? `, ${plural(workflow.cycles, 'cycle')}` : ''}
                    </p>
                  </div>
                  <Button
                    variant="primary"
                    icon="refresh"
                    disabled={busy}
                    onClick={() =>
                      run(
                        () => renewalApi.runWorkflow(workflow.id, {}, { roomId: ROOM, actor: 'dana' }),
                        'The workflow ran',
                        'One renewal quote per contract it covers.',
                      )
                    }
                  >
                    Run now
                  </Button>
                </div>
                <dl className="mt-3">
                  <ValueRow
                    label="Trigger"
                    value={String(workflow.enrollment_trigger || '').replace(/_/g, ' ')}
                  />
                  <ValueRow
                    label="Deal selection"
                    value={
                      DEAL_SELECTION_METHODS.find(
                        (method) => method.value === workflow.deal_selection_method,
                      )?.label || workflow.deal_selection_method
                    }
                  />
                </dl>
              </Card>
            ))}
          </div>
        )}
        <Card className="p-4">
          <p className="text-xs text-muted-foreground">{workflows.action_note}</p>
          <p className="mt-2 text-xs text-muted-foreground">{workflows.re_enroll_note}</p>
        </Card>
      </section>

      <section className="space-y-3">
        <SectionHeading
          title="Templates and pipelines"
          hint="A renewal quote needs a template and a deal pipeline and stage before it can carry them."
        />
        <div className="flex flex-wrap gap-2">
          <Button icon="plus" onClick={() => setAuthoring('template')}>
            New quote template
          </Button>
          <Button icon="plus" onClick={() => setAuthoring('pipeline')}>
            New deal pipeline
          </Button>
        </div>
        {authoring ? (
          <SetupForm
            kind={authoring}
            busy={busy}
            onClose={() => setAuthoring(null)}
            onSubmit={(payload) => {
              const kind = authoring
              const call =
                kind === 'template' ? renewalApi.createTemplate : renewalApi.createPipeline
              setAuthoring(null)
              return run(
                () => call(payload, { roomId: ROOM, actor: 'dana' }),
                `The ${kind} was saved`,
                payload.name,
              )
            }}
          />
        ) : null}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {templates.templates.map((template) => (
            <Card key={template.id} className="p-4">
              <Badge tone="info">{template.change_type}</Badge>
              <h3 className="mt-2 truncate text-base font-semibold text-foreground">
                {template.name}
              </h3>
              <dl className="mt-3">
                <ValueRow label="Term" value={plural(template.term_months, 'month')} />
                <ValueRow label="Discount format" value={template.discount_format} />
                <ValueRow label="Association type" value={template.association_type} mono />
              </dl>
            </Card>
          ))}
          {pipelines.pipelines.map((pipeline) => (
            <Card key={pipeline.id} className="p-4">
              <Badge tone="neutral">{plural(pipeline.stages.length, 'stage')}</Badge>
              <h3 className="mt-2 truncate text-base font-semibold text-foreground">
                {pipeline.name}
              </h3>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {pipeline.stages.map((stage) => (
                  <Badge key={stage} tone="neutral">
                    {stage}
                  </Badge>
                ))}
              </div>
            </Card>
          ))}
        </div>
        <Card className="p-4">
          <p className="text-xs text-muted-foreground">{templates.ownership_note}</p>
          <p className="mt-2 text-xs text-muted-foreground">{templates.association_note}</p>
        </Card>
      </section>

      <section className="space-y-3">
        <SectionHeading
          title="The renewal date rule"
          hint="Both branches are implemented, because the research states both."
        />
        <Card className="p-4">
          <dl className="space-y-3">
            {Object.entries(vocabulary.renewal_date_branches).map(([key, sentence]) => (
              <div key={key}>
                <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
                  {key === 'if_finalised'
                    ? 'A renewal has been finalised'
                    : 'A renewal has not been finalised'}
                </p>
                <p className="mt-1 border-l-2 border-border-subtle pl-3 text-sm italic text-foreground">
                  {sentence}
                </p>
              </div>
            ))}
          </dl>
          <p className="mt-3 text-xs text-muted-foreground">{vocabulary.renewal_date_branch_note}</p>
        </Card>
        <Card className="p-4">
          <p className="text-sm font-semibold text-foreground">{vocabulary.evergreen_label}</p>
          <p className="mt-1 border-l-2 border-border-subtle pl-3 text-sm italic text-foreground">
            {vocabulary.evergreen_rule}
          </p>
          <p className="mt-2 text-xs text-muted-foreground">{vocabulary.evergreen_is_a_label}</p>
        </Card>
        <Card className="p-4">
          <p className="text-sm font-semibold text-foreground">Direct contract renewal</p>
          <p className="mt-1 text-sm text-foreground">{vocabulary.direct_renewal_note}</p>
        </Card>
      </section>

      <section className="space-y-3">
        <SectionHeading
          title="Decisions"
          hint="Every judgement call this workflow made, with the alternative it rejected."
          count={plural(decisions.count, 'decision')}
        />
        <div className="space-y-3">
          {decisions.decisions.map((decision) => (
            <DecisionCard key={decision.id} decision={decision} />
          ))}
        </div>
      </section>

      <section className="space-y-3">
        <SectionHeading title="The change effective date modes" hint="All four, as the research lists them." />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {EFFECTIVE_DATE_MODES.map((entry) => (
            <Card key={entry.value} className="p-4">
              <Badge tone="info">{entry.label}</Badge>
              <p className="mt-2 text-xs text-muted-foreground">{entry.hint}</p>
            </Card>
          ))}
        </div>
        <Card className="p-4">
          <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Contract scopes a workflow can target
          </p>
          <ul className="mt-2 space-y-1">
            {CONTRACT_TARGETS.map((target) => (
              <li key={target.value} className="text-sm text-foreground">
                {target.label}
              </li>
            ))}
          </ul>
        </Card>
      </section>
    </div>
  )
}

export default {
  id: 'wf-100-create-a-renewal-quote-from-a-contract-and-auto',
  label: 'Renewal quotes',
  icon: 'audit',
  order: 1000,
  Component: RenewalQuotePage,
}