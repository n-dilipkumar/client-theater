/**
 * WF-088: auto-assign the correct price book or price list to a deal by rule.
 *
 * The page is built from `@/components/ui` and from the primitives in this folder. It
 * reads its operators, its switches, its modes and its reason codes from the server's
 * `/vocabulary` route rather than repeating them, so the panel cannot tell a seller a
 * reason the rules do not produce.
 *
 * It follows the researched flow in order. The top card is the rule builder, which is
 * `CRM > Products` -> `Manage price books` -> **Assignment rules** -> the edit icon.
 * Under it is the reviewed-rules panel, which is the flow's right panel: "review the
 * matching deals in the right panel". Under that are the deals, each with its *Line
 * items* card price book and the **Change price book** dropdown, and the log of every
 * decision including the ones that assigned nothing.
 *
 * Four things this page deliberately does not do:
 *
 *   - It does not offer a price-book field on a quote. "Users can't select a price book
 *     when creating a quote; they must select it on the deal", so the quote panel is a
 *     read and says where the value came from.
 *   - It does not hide the multiple-match reading. Two sourced sentences describe what
 *     happens when more than one book matches and they agree on nothing being written, so
 *     the panel shows the evidence and the audit that chose it rather than implying the
 *     product picked one.
 *   - It does not offer **Change price book** without saying it is destructive. The
 *     sourced sentence removes the previous book's line items, so the count is on screen
 *     before the button that does it.
 *   - It does not claim the product catalogue exists. WF-087 has not shipped, so the
 *     panel says whether one was found rather than implying pricing is fully scoped.
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
  bookLabel,
  candidateBooks,
  listDeals,
  listQuotes,
  listRooms,
  modeLabel,
  modeName,
  priceBookApi,
} from './api'
import {
  BookStateBadge,
  BuilderFilter,
  DestructiveWarning,
  Notice,
  PRICE_BOOK_ICON,
  ReadingPanel,
  ReasonBadge,
  ReviewedRule,
} from './primitives'

const EMPTY_FILTER = { object: 'deal', property: '', operator: 'is', value: '' }

/**
 * The rule builder, which is the researched settings screen.
 *
 * The price book, the filters, the conjunction and the two switches are saved in one
 * call, because the research's own UI refuses to save a rule that is missing any of them,
 * so a half-configured rule is not a state this product creates either.
 */
function RuleBuilder({ vocabulary, roomId, actor, onSaved }) {
  const [key, setKey] = useState('')
  const [label, setLabel] = useState('')
  const [bookId, setBookId] = useState('')
  const [bookName, setBookName] = useState('')
  const [filters, setFilters] = useState([{ ...EMPTY_FILTER }])
  const [matchMode, setMatchMode] = useState('all')
  const [autoAssign, setAutoAssign] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [done, setDone] = useState(null)

  const objects = vocabulary?.filters?.objects || []
  const operators = vocabulary?.filters?.operators || []
  const matchModes = vocabulary?.filters?.match_modes || []
  const toggleQuote = vocabulary?.switches?.auto_assigned_toggle_quote

  function updateFilter(index, patch) {
    setFilters((current) =>
      current.map((one, position) => (position === index ? { ...one, ...patch } : one)),
    )
  }

  function removeFilter(index) {
    setFilters((current) => current.filter((_, position) => position !== index))
  }

  function addFilter() {
    setFilters((current) => [...current, { ...EMPTY_FILTER }])
  }

  async function save() {
    setBusy(true)
    setError(null)
    setDone(null)
    try {
      const created = await priceBookApi.createRule(
        {
          key,
          label: label || key,
          // Both halves are sent, so the rule works whether this deployment identifies
          // its books by id or by name.
          price_book: { id: bookId.trim() || null, name: bookName.trim() || null },
          matchMode,
          auto_assign: autoAssign,
          filters: filters.map((one) => ({
            ...one,
            // The list operators take a list, so the comma-separated field is split
            // rather than sent as one string that would match nothing.
            value: ['in', 'not_in'].includes(one.operator)
              ? String(one.value)
                  .split(',')
                  .map((entry) => entry.trim())
                  .filter(Boolean)
              : one.value,
          })),
        },
        { roomId, actor },
      )
      setDone(`Saved ${created.data?.label || key}.`)
      setKey('')
      setLabel('')
      setBookId('')
      setBookName('')
      setFilters([{ ...EMPTY_FILTER }])
      onSaved()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="flex flex-col gap-4">
      <div>
        <h2 className="text-lg font-semibold text-foreground">Assignment rules</h2>
        <p className="text-sm text-muted-foreground">
          A rule holds the price book, the filters and the two switches together. Open a
          price book, add the filters, then turn Auto-assigned on.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Rule name" id="wf088-rule-key" hint="Lower-case letters, digits and hyphens.">
          <input
            id="wf088-rule-key"
            className={inputClass}
            value={key}
            onChange={(event) => setKey(event.target.value)}
          />
        </Field>
        <Field label="Label" id="wf088-rule-label" hint="What a seller reads on the deal.">
          <input
            id="wf088-rule-label"
            className={inputClass}
            value={label}
            onChange={(event) => setLabel(event.target.value)}
          />
        </Field>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          label="Price book id"
          id="wf088-book-id"
          hint="The identifier your deployment uses, if it uses one."
        >
          <input
            id="wf088-book-id"
            className={inputClass}
            value={bookId}
            onChange={(event) => setBookId(event.target.value)}
          />
        </Field>
        <Field
          label="Price book name"
          id="wf088-book-name"
          hint="Either half is enough. Both are sent."
        >
          <input
            id="wf088-book-name"
            className={inputClass}
            value={bookName}
            onChange={(event) => setBookName(event.target.value)}
          />
        </Field>
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm font-medium text-foreground">And or or these filters are met</p>
          <div className="flex flex-col gap-1">
            <label htmlFor="wf088-match-mode" className="text-[13px] font-medium text-foreground">
              Combine with
            </label>
            <select
              id="wf088-match-mode"
              className={inputClass}
              value={matchMode}
              disabled={busy}
              onChange={(event) => setMatchMode(event.target.value)}
            >
              {matchModes.map((row) => (
                <option key={row.mode} value={row.mode}>
                  {row.mode}
                </option>
              ))}
            </select>
          </div>
        </div>
        {filters.map((one, index) => (
          <BuilderFilter
            key={index}
            index={index}
            filter={one}
            objects={objects}
            operators={operators}
            onChange={updateFilter}
            onRemove={removeFilter}
            disabled={busy}
          />
        ))}
        <div>
          <Button icon="plus" onClick={addFilter} disabled={busy}>
            Add filter
          </Button>
        </div>
        {vocabulary?.filters?.group_quote && (
          <p className="text-xs italic text-muted-foreground">
            {vocabulary.filters.group_quote}
          </p>
        )}
      </div>

      <div className="flex flex-col gap-1">
        <span className="text-[13px] font-medium text-foreground">Auto-assigned</span>
        <div className="flex flex-wrap items-center gap-3">
          <label className="inline-flex min-h-11 items-center gap-2 text-sm text-foreground">
            <input
              type="checkbox"
              checked={autoAssign}
              disabled={busy}
              onChange={(event) => setAutoAssign(event.target.checked)}
            />
            {autoAssign ? 'On. One matching rule writes its price book.' : 'Off. The rule is tested and nothing is written.'}
          </label>
        </div>
        {toggleQuote && <p className="text-xs italic text-muted-foreground">{toggleQuote}</p>}
      </div>

      {done && (
        <p role="status" aria-live="polite" className="text-xs text-success">
          {done}
        </p>
      )}
      {error && <ErrorNote error={error} />}

      <div>
        <Button variant="primary" disabled={busy} onClick={save}>
          Save the rule
        </Button>
      </div>
    </Card>
  )
}

/**
 * One configured rule, with both researched switches on it.
 *
 * The words on the buttons are the state, not the action, because a button that says
 * "Turn off" does not tell a seller whether the rule is currently assigning anything.
 */
function RuleRow({ rule, actor, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const data = rule.data || {}
  const enabled = data.enabled !== false
  const autoAssign = data.auto_assign !== false

  async function toggle(patch) {
    setBusy(true)
    setError(null)
    try {
      await priceBookApi.patchRule(rule.id, patch, { actor })
      onChanged()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-2 border-b border-border-subtle py-3 last:border-b-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-foreground">{data.label || data.key}</p>
          <p className="text-xs text-muted-foreground">
            Assigns <span className="font-mono">{bookLabel(data.price_book)}</span>
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button disabled={busy} onClick={() => toggle({ enabled: !enabled })}>
            {enabled ? 'Active' : 'Inactive'}
          </Button>
          <Button disabled={busy} onClick={() => toggle({ auto_assign: !autoAssign })}>
            Auto-assigned {autoAssign ? 'on' : 'off'}
          </Button>
        </div>
      </div>

      <ul className="flex flex-col gap-1">
        {(data.filters || []).map((one, index) => (
          <li key={index} className="text-xs text-muted-foreground">
            <span className="font-mono text-foreground">
              {one.object}.{one.property}
            </span>{' '}
            {one.operator}{' '}
            <span className="font-mono">{JSON.stringify(one.value)}</span>
          </li>
        ))}
      </ul>

      {!enabled && (
        <p className="text-xs text-muted-foreground">
          The price book is inactive, so this rule assigns nothing until its Inactive switch
          is turned off.
        </p>
      )}
      {enabled && !autoAssign && (
        <p className="text-xs text-muted-foreground">
          Auto-assigned is off, so this rule is being tested. It matches and it is reported,
          and nothing is written.
        </p>
      )}
      {error && <ErrorNote error={error} />}
    </div>
  )
}

/**
 * One deal: its *Line items* card price book, the rules that matched, and the two
 * buttons the researched flow offers on it.
 *
 * **Run the rule** is the researched moment and is offered with a trigger picker, because
 * "Price books are auto-assigned only when a deal is created" is a rule the user has to be
 * able to see fire. **Change price book** is the override, and it warns first.
 */
function DealPanel({ deal, vocabulary, roomId, actor, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [answer, setAnswer] = useState(null)
  const [trigger, setTrigger] = useState('create')
  const [choosing, setChoosing] = useState(false)
  const [removing, setRemoving] = useState([])

  const conditions = useAsync(() => priceBookApi.conditions(deal.id), [deal.id])
  const card = useAsync(() => priceBookApi.priceBook(deal.id), [deal.id])

  if (conditions.loading || card.loading) return <Spinner label="Reading the assignment rules" />
  if (conditions.error) return <ErrorNote error={conditions.error} onRetry={conditions.refetch} />

  const panel = conditions.data || {}
  // What an override would remove right now, read from the conditions panel rather than
  // counted here, so the number on screen before the button is the number the backend
  // will act on. A line that names a different price book survives the override, so this
  // is deliberately not `panel.line_items.length`.
  const removable = panel.line_items_removed_on_change || []
  const state = card.data?.state || panel.state || 'unassigned'
  const candidates = candidateBooks(panel.candidates)
  const triggers = vocabulary?.triggers || []

  async function run() {
    setBusy(true)
    setError(null)
    setAnswer(null)
    try {
      const result = await priceBookApi.assign(deal.id, { trigger, roomId, actor })
      setAnswer(result)
      conditions.refetch()
      card.refetch()
      onChanged()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  async function change(book) {
    setBusy(true)
    setError(null)
    setAnswer(null)
    try {
      const result = await priceBookApi.changePriceBook(deal.id, book, { roomId, actor })
      setAnswer(result)
      setChoosing(false)
      setRemoving(result.line_items_removed || [])
      conditions.refetch()
      card.refetch()
      onChanged()
    } catch (problem) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-3 border-b border-border-subtle py-4 last:border-b-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-foreground">
            {deal.data?.name || deal.data?.deal_name || deal.id}
          </p>
          <p className="font-mono text-xs text-muted-foreground">{deal.id}</p>
        </div>
        <BookStateBadge state={state} vocabulary={vocabulary} />
      </div>

      <p className="text-sm text-foreground">
        Price book:{' '}
        <span className="font-mono">{card.data?.price_book_label || 'None'}</span>
        {card.data?.authority && (
          <span className="ml-2 text-xs text-muted-foreground">
            from the <span className="font-mono">{card.data.deal_field || card.data.authority}</span>{' '}
            field
          </span>
        )}
      </p>

      {panel.outcome && (
        <Notice
          tone={panel.writes ? 'success' : panel.outcome === 'needs_choice' ? 'warning' : 'neutral'}
          title={`${panel.outcome}`}
        >
          {panel.explanation}
        </Notice>
      )}

      {panel.outcome === 'needs_choice' && (
        <>
          <Notice tone="warning" title="Nothing was written">
            More than one price book matched, so the deal owner chooses. The card reads Price
            book: None until they do.
          </Notice>
          <div className="flex flex-col gap-1">
            <p className="text-xs font-medium text-foreground">Matching price books</p>
            {candidates.map((book) => (
              <p key={book.id || book.name} className="text-xs text-muted-foreground">
                <span className="font-mono text-foreground">{bookLabel(book)}</span> from{' '}
                {(panel.candidates || [])
                  .filter((entry) => bookLabel(entry.price_book) === bookLabel(book))
                  .map((entry) => entry.rule_label)
                  .join(', ')}
              </p>
            ))}
          </div>
        </>
      )}

      {(panel.inactive_rules || []).length > 0 && (
        <Notice tone="neutral" title="A rule matched but is inactive">
          <span className="font-mono">{panel.inactive_rules.join(', ')}</span>. Turn its
          Inactive switch off to activate the price book.
        </Notice>
      )}
      {(panel.test_first_rules || []).length > 0 && (
        <Notice tone="neutral" title="A rule matched but is not auto-assigning">
          <span className="font-mono">{panel.test_first_rules.join(', ')}</span>. This is the
          researched test-first mode: the rule matches and nothing is written.
        </Notice>
      )}

      {removing.length > 0 && (
        <DestructiveWarning removed={removing} quote={vocabulary?.price_book?.line_items_removed_quote} />
      )}

      {choosing && removable.length > 0 && (
        <DestructiveWarning
          removed={removable}
          quote={vocabulary?.price_book?.line_items_removed_quote}
        />
      )}

      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1">
          <label htmlFor={`wf088-trigger-${deal.id}`} className="text-[13px] font-medium text-foreground">
            Trigger
          </label>
          <select
            id={`wf088-trigger-${deal.id}`}
            className={inputClass}
            value={trigger}
            disabled={busy}
            onChange={(event) => setTrigger(event.target.value)}
          >
            {triggers.map((row) => (
              <option key={row.trigger} value={row.trigger}>
                {row.trigger}
              </option>
            ))}
          </select>
        </div>
        <Button variant="primary" disabled={busy} onClick={run}>
          Run the rule
        </Button>
        <Button disabled={busy} onClick={() => setChoosing(!choosing)}>
          {card.data?.price_book ? 'Change price book' : 'Set price book'}
        </Button>
      </div>

      {choosing && (
        <div className="flex flex-col gap-2">
          <p className="text-xs text-muted-foreground">
            {vocabulary?.price_book?.change_quote}. Changing the price book removes the line
            items of the previous one.
          </p>
          <div className="flex flex-wrap gap-2">
            {candidates.map((book) => (
              <Button key={book.id || book.name} disabled={busy} onClick={() => change(book)}>
                Use {bookLabel(book)}
              </Button>
            ))}
            {candidates.length === 0 && (
              <p className="text-xs text-muted-foreground">
                No rule matched this deal, so there are no price books to choose from here.
                The researched dropdown lists the workspace's price books, and WF-087's
                catalogue — which is what would supply that list — has not shipped. Until it
                does, the books this page can name are the ones a rule already names.
              </p>
            )}
          </div>
        </div>
      )}

      {answer && (
        <Notice tone={answer.written || answer.changed ? 'success' : 'neutral'}>
          <span className="font-mono">{answer.outcome}</span>. {answer.explanation}
          {answer.assigned === false && answer.reason && ` (${answer.reason})`}
        </Notice>
      )}
      {error && <ErrorNote error={error} />}

      {panel.create_only_quote && (
        <p className="text-xs italic text-muted-foreground">{panel.create_only_quote}</p>
      )}
    </div>
  )
}

/** The right panel: every rule, what it read on this deal, and whether it matched. */
function ReviewedPanel({ deal, vocabulary }) {
  const conditions = useAsync(() => priceBookApi.conditions(deal.id), [deal.id])

  if (conditions.loading) return <Spinner label="Reviewing the matching deals" />
  if (conditions.error) {
    return <ErrorNote error={conditions.error} onRetry={conditions.refetch} />
  }

  const panel = conditions.data || {}
  const entries = panel.rules || []

  if (entries.length === 0) {
    return (
      <EmptyState
        title="No rules configured"
        description="With no rule, no price book is assigned automatically. This is the researched manual-only mode."
      />
    )
  }

  return (
    <div className="flex flex-col gap-1">
      {/* The full sourced wording, in prose, where it belongs. The stat card above carries
          the short name for the same reason: a sentence in a card-sized value wraps to nine
          lines and reads as a fault. */}
      <p className="text-sm text-muted-foreground">
        Workspace mode: {modeLabel(panel.mode, vocabulary)}
      </p>
      {entries.map((entry, index) => (
        <ReviewedRule key={entry.rule_id || index} entry={entry} />
      ))}
    </div>
  )
}

/**
 * What a quote gets, and why.
 *
 * The sourced sentence is that a quote inherits from its deal and cannot be set directly,
 * so this is a read with the sentence on it rather than a field.
 */
function QuotePanel({ quote }) {
  const inherited = useAsync(() => priceBookApi.quotePriceBook(quote.id), [quote.id])

  if (inherited.loading) return <Spinner label="Reading the inherited price book" />
  if (inherited.error) {
    return <ErrorNote error={inherited.error} onRetry={inherited.refetch} />
  }

  const panel = inherited.data || {}

  return (
    <div className="flex flex-col gap-2 border-b border-border-subtle py-3 last:border-b-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-foreground">
            {quote.data?.name || quote.data?.title || quote.id}
          </p>
          <p className="font-mono text-xs text-muted-foreground">{quote.id}</p>
        </div>
        <InheritanceBadge authority={panel.authority} />
      </div>
      <p className="text-sm text-foreground">
        Inherits <span className="font-mono">{panel.price_book_label || 'None'}</span>
        {panel.authority === 'deal' && (
          <span className="ml-2 text-xs text-muted-foreground">from deal {panel.deal_id}</span>
        )}
      </p>
      <p className="text-xs text-muted-foreground">{panel.reason}</p>
      {panel.evidence && (
        <p className="text-xs italic text-muted-foreground">{panel.evidence}</p>
      )}
    </div>
  )
}

/**
 * Where a quote's price book came from.
 *
 * A span rather than the shared `Badge`, because it needs no tone: "inherited from the
 * deal" and "no deal to inherit from" are the two facts and both are words. The words are
 * the signal, so nothing here depends on colour perception.
 */
function InheritanceBadge({ authority }) {
  return (
    <span className="rounded-sm border border-border-subtle px-2 py-1 text-xs text-muted-foreground">
      {authority === 'deal' ? 'Inherited from the deal' : 'No deal to inherit from'}
    </span>
  )
}

/**
 * The page.
 *
 * Exported by name as well as through the default descriptor, because the frontend host
 * discovers a feature by its *default* export only, and a test that wants to render the
 * page against a stubbed API has no other way to reach it. Naming it here rather than
 * moving it to a second file keeps the page and its descriptor in one place, which is
 * what the host globs for.
 */
export function PriceBookRulesPage() {
  const [roomId, setRoomId] = useState('')
  const [actor, setActor] = useState('')
  const [outcome, setOutcome] = useState('')

  const rooms = useAsync(() => listRooms(), [])
  const vocabulary = useAsync(() => priceBookApi.vocabulary(), [])
  const inferences = useAsync(() => priceBookApi.inferences(), [])
  const summary = useAsync(() => priceBookApi.summary(roomId), [roomId])
  const rules = useAsync(() => priceBookApi.rules(roomId), [roomId])
  const deals = useAsync(() => listDeals(roomId), [roomId])
  const quotes = useAsync(() => listQuotes(roomId), [roomId])
  const log = useAsync(() => priceBookApi.assignments({ roomId, outcome }), [roomId, outcome])

  if (vocabulary.loading || rooms.loading) return <Spinner label="Loading price book rules" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (log.error) return <ErrorNote error={log.error} onRetry={log.refetch} />

  const refreshAll = () => {
    rules.refetch()
    summary.refetch()
    log.refetch()
  }

  const dealEntries = deals.data?.records || []
  const quoteEntries = quotes.data?.records || []
  const assignments = log.data?.assignments || []
  const reasons = vocabulary.data?.assignments || []

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-1">
        <h1 className="font-display text-2xl font-semibold text-foreground">
          Auto-assign the correct price book
        </h1>
        <p className="text-sm text-muted-foreground">
          A deal's and its company's properties are matched against configured filters when
          the deal row is created. One matching rule writes its price book onto the deal, and
          the product lookup and the line-item prices are scoped to it.
        </p>
      </header>

      <ReadingPanel reading={inferences.data?.multiple_matches} title="When more than one book matches" />
      <ReadingPanel
        reading={{
          sourced: [inferences.data?.override_removes_lines?.quote].filter(Boolean),
          why: inferences.data?.override_removes_lines?.why,
          jev_audit_id: inferences.data?.override_removes_lines?.jev_audit_id,
          jev_confidence: inferences.data?.override_removes_lines?.jev_confidence,
          rejected: inferences.data?.override_removes_lines?.rejected,
        }}
        title="Changing the price book"
      />
      {/* The research has two vendors in it and this build implements one of them. Saying
          so on the page, rather than only in a module docstring, is the difference between a
          divergence a reader can find and one they have to discover. */}
      <Notice tone="neutral" title="What is built, and what is only named">
        <p className="mb-2">
          The rule engine above is the HubSpot half: filters on deal and company properties,
          assigned on create, overridable by hand.
        </p>
        <p className="mb-2">
          The Dynamics half is <strong>served as vocabulary, not built</strong>. This build
          publishes{' '}
          <span className="font-mono">
            {vocabulary.data?.dynamics?.message || 'GetDefaultPriceLevelRequest'}
          </span>{' '}
          and{' '}
          <span className="font-mono">
            {vocabulary.data?.dynamics?.connection_role || 'Territory Default Pricelist'}
          </span>
          , because the research names them, but it does not implement the plug-in, does not
          fire on quote, order or invoice rows as{' '}
          <span className="font-mono">GetDefaultPriceLevel</span> does upstream, and models
          territory as an ordinary deal property rather than as a{' '}
          <span className="font-mono">systemuser</span> assignment. The reasoning and the
          rejected alternatives are recorded on{' '}
          <span className="font-mono">GET /inferences</span>.
        </p>
      </Notice>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Waiting on a choice"
          value={summary.data?.awaiting_a_choice ?? 0}
          icon="audit"
          hint="Deals matching more than one rule"
        />
        {/* The mode card carries the mode's *name*, not its explanation. A StatCard's value
            is meant to be read at a glance from across a desk, and a sentence set in the
            display face at that size wraps to nine lines and reads as a mistake. The full
            sourced wording is already on the page once, next to the reading it comes from;
            repeating it in the card bought nothing and cost legibility. */}
        <StatCard
          label="Price books assigned"
          value={summary.data?.written ?? 0}
          icon="audit"
          hint="Decisions that wrote a book"
        />
        <StatCard
          label="Rules auto-assigning"
          value={summary.data?.rules_auto_assigning ?? 0}
          icon="database"
          hint={`Of ${summary.data?.rules ?? 0} configured`}
        />
        <StatCard
          label="Mode"
          value={modeName(summary.data?.mode)}
          icon="audit"
          hint="Read from the rules' own switches"
        />
      </div>

      {!summary.data?.catalogue_found && (
        <Notice tone="neutral" title="No product catalogue was found">
          {summary.data?.catalogue_note}
        </Notice>
      )}

      <Card className="flex flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Room" id="wf088-room" hint="Rules and assignments are scoped to a room.">
            <select
              id="wf088-room"
              className={inputClass}
              value={roomId}
              onChange={(event) => setRoomId(event.target.value)}
            >
              <option value="">All rooms</option>
              {(rooms.data?.records || []).map((room) => (
                <option key={room.id} value={room.id}>
                  {room.data?.name || room.id}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Reason" id="wf088-outcome" hint="One published reason code, or all of them.">
            <select
              id="wf088-outcome"
              className={inputClass}
              value={outcome}
              onChange={(event) => setOutcome(event.target.value)}
            >
              <option value="">Every decision</option>
              {reasons.map((row) => (
                <option key={row.reason} value={row.reason}>
                  {row.reason}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Acting as" id="wf088-actor" hint="Who is running the rule or overriding.">
            <input
              id="wf088-actor"
              className={inputClass}
              value={actor}
              onChange={(event) => setActor(event.target.value)}
              placeholder="dana"
            />
          </Field>
        </div>
      </Card>

      <RuleBuilder
        vocabulary={vocabulary.data}
        roomId={roomId}
        actor={actor}
        onSaved={refreshAll}
      />

      <Card className="flex flex-col gap-2">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Configured rules</h2>
          <p className="text-sm text-muted-foreground">
            Two switches per rule, and both of them are the researched flow's own toggles.
          </p>
        </div>
        {(rules.data?.rules || []).length === 0 && (
          <EmptyState
            title="No rules yet"
            description="With no rule, no price book is assigned automatically. A seller sets one by hand instead."
          />
        )}
        {(rules.data?.rules || []).map((rule) => (
          <RuleRow
            key={rule.id}
            rule={rule}
            actor={actor}
            onChanged={refreshAll}
          />
        ))}
      </Card>

      <Card className="flex flex-col gap-2">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Deals and their price books</h2>
          <p className="text-sm text-muted-foreground">
            Each deal shows the price book on its Line items card, the rules that matched it,
            and the researched override.
          </p>
        </div>
        {dealEntries.length === 0 && (
          <EmptyState
            title="No deals to price"
            description="This workflow reads the deals a CRM mirror writes. Point it at a collection that holds some."
          />
        )}
        {dealEntries.map((deal) => (
          <DealPanel
            key={deal.id}
            deal={deal}
            vocabulary={vocabulary.data}
            roomId={roomId}
            actor={actor}
            onChanged={refreshAll}
          />
        ))}
      </Card>

      {dealEntries.length > 0 && (
        <Card className="flex flex-col gap-2">
          <div>
            <h2 className="text-lg font-semibold text-foreground">Review the matching deals</h2>
            <p className="text-sm text-muted-foreground">
              The researched right panel. Every rule, what each of its filters read, and
              whether it matched.
            </p>
          </div>
          {dealEntries.map((deal) => (
            <div key={deal.id} className="flex flex-col gap-2 py-2">
              <p className="text-sm font-medium text-foreground">
                {deal.data?.name || deal.id}
              </p>
              <ReviewedPanel deal={deal} vocabulary={vocabulary.data} />
            </div>
          ))}
        </Card>
      )}

      <Card className="flex flex-col gap-2">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Every decision</h2>
          <p className="text-sm text-muted-foreground">
            Including the evaluations that assigned nothing, because "why does this deal have
            no price book" is the first question a seller asks.
          </p>
        </div>
        {assignments.length === 0 && (
          <EmptyState
            title="Nothing recorded yet"
            description="A row appears here as soon as the rule runs on a deal, whichever way it went."
          />
        )}
        {assignments.map((entry, index) => (
          <div
            key={entry.id || index}
            className="flex flex-col gap-1 border-b border-border-subtle py-2 last:border-b-0"
          >
            <div className="flex flex-wrap items-center gap-2">
              <ReasonBadge
                reason={entry.outcome}
                vocabulary={vocabulary.data}
                writes={entry.written}
              />
              <span className="font-mono text-xs text-foreground">{entry.deal_id}</span>
              <span className="text-xs text-muted-foreground">{entry.at}</span>
            </div>
            <p className="text-xs text-muted-foreground">
              {entry.price_book_label && entry.price_book_label !== 'None' && (
                <>
                  Assigned <span className="font-mono">{entry.price_book_label}</span>.
                </>
              )}
              {entry.rule_label && (
                <>
                  By rule <span className="font-mono">{entry.rule_label}</span>.
                </>
              )}
              Trigger <span className="font-mono">{entry.trigger}</span>.
              {entry.line_items_removed_count > 0 && (
                <>
                  {' '}
                  <span className="font-mono">{entry.line_items_removed_count}</span> line item
                  removed.
                </>
              )}
            </p>
            {entry.explanation && (
              <p className="text-xs italic text-muted-foreground">{entry.explanation}</p>
            )}
          </div>
        ))}
      </Card>

      {quoteEntries.length > 0 && (
        <Card className="flex flex-col gap-2">
          <div>
            <h2 className="text-lg font-semibold text-foreground">
              Quotes and the price book they inherit
            </h2>
            <p className="text-sm text-muted-foreground">
              {vocabulary.data?.quote_inheritance?.quote}
            </p>
          </div>
          {quoteEntries.map((quote) => (
            <QuotePanel key={quote.id} quote={quote} />
          ))}
        </Card>
      )}
    </div>
  )
}

export default {
  id: 'wf-088-auto-assign-the-correct-price-book-or-price',
  label: 'Price book rules',
  icon: 'audit',
  iconPath: PRICE_BOOK_ICON,
  order: 880,
  Component: PriceBookRulesPage,
}