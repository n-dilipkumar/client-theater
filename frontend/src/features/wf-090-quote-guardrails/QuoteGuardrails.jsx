/**
 * Quote guardrails (WF-090).
 *
 * The page has three jobs and is arranged in the order a seller does them.
 *
 * 1. Configure rules. A rule is a small expression in the vendor's DSL, an outcome
 *    (warn or block) and a message the seller will read. Before a rule is saved the page
 *    asks the server whether the expression parses, and shows the parser's own answer.
 *    That is the sandbox the research describes: an author tries a definition without
 *    touching a live quote.
 *
 * 2. Try the rules on a quote. Evaluating writes nothing, because the specification says
 *    rules evaluate continuously as a quote is built, and a writing endpoint would leave
 *    an audit row per keystroke. The page shows the verdicts grouped by what they mean.
 *
 * 3. Publish, through the gate. A Block publish rule stops the publish with 409 and names
 *    the rule that stopped it. That refusal is the researched rule working, so the page
 *    renders it as a result, not as a crash.
 *
 * The third state matters as much as the other two: a rule whose property the quote does
 * not carry is reported as "cannot check" and never blocks. A seller who sees a false
 * block would disable the whole feature.
 */

import { useCallback, useMemo, useState } from 'react'

import { Button, Card, EmptyState, ErrorNote, Field, Spinner, StatCard, inputClass, useAsync } from '@/components/ui'

import { guardrailApi } from './api'
import { Notice, RuleCard, SectionLabel, VerdictList } from './primitives'

const BLANK_RULE = {
  name: '',
  rule_definition: '',
  outcome: 'block_publish',
  message: '',
}

function quotesOf(payload) {
  if (!payload) return []
  return payload.records || payload.quotes || []
}

export default function QuoteGuardrails() {
  const vocabulary = useAsync(() => guardrailApi.vocabulary(), [])
  const rules = useAsync(() => guardrailApi.rules(), [])
  const quotes = useAsync(() => guardrailApi.listQuotes(), [])
  const summary = useAsync(() => guardrailApi.summary(), [])

  const [draft, setDraft] = useState(BLANK_RULE)
  const [checked, setChecked] = useState(null)
  const [checking, setChecking] = useState(false)
  const [saving, setSaving] = useState(false)
  const [busyRuleId, setBusyRuleId] = useState(null)
  const [formError, setFormError] = useState(null)

  const [selectedQuoteId, setSelectedQuoteId] = useState('')
  const [evaluation, setEvaluation] = useState(null)
  const [evaluating, setEvaluating] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [gate, setGate] = useState(null)

  const reloadRules = rules.refetch
  const reloadSummary = summary.refetch

  // The five example definitions the specification quotes are offered as one-click
  // fills. A DSL an author has to memorise is a DSL that goes unused.
  const examples = useMemo(() => vocabulary.data?.grammar?.examples || [], [vocabulary.data])

  const setDraftField = (field) => (event) =>
    setDraft((current) => ({ ...current, [field]: event.target.value }))

  const resetCheck = () => setChecked(null)

  const runCheck = useCallback(async () => {
    setChecking(true)
    setFormError(null)
    try {
      setChecked(await guardrailApi.validate(draft.rule_definition))
    } catch (error) {
      setChecked({ valid: false, error: 'guardrail_rule_unparseable', detail: String(error.message) })
    } finally {
      setChecking(false)
    }
  }, [draft.rule_definition])

  const save = useCallback(async () => {
    setSaving(true)
    setFormError(null)
    try {
      await guardrailApi.createRule(draft)
      setDraft(BLANK_RULE)
      setChecked(null)
      reloadRules()
      reloadSummary()
    } catch (error) {
      // The server refuses an unreadable rule with 422 and the parser's reason. The page
      // shows that rather than a generic failure, because it is actionable.
      setFormError(error)
    } finally {
      setSaving(false)
    }
  }, [draft, reloadRules, reloadSummary])

  const toggleRule = useCallback(
    async (rule) => {
      const data = rule.data || rule
      const next = data.status === 'enabled' ? 'disabled' : 'enabled'
      setBusyRuleId(rule.id)
      try {
        await guardrailApi.patchRule(rule.id, { status: next })
        reloadRules()
        reloadSummary()
      } finally {
        setBusyRuleId(null)
      }
    },
    [reloadRules, reloadSummary],
  )

  const removeRule = useCallback(
    async (rule) => {
      setBusyRuleId(rule.id)
      try {
        await guardrailApi.deleteRule(rule.id)
        reloadRules()
        reloadSummary()
      } finally {
        setBusyRuleId(null)
      }
    },
    [reloadRules, reloadSummary],
  )

  const evaluate = useCallback(async () => {
    if (!selectedQuoteId) return
    setEvaluating(true)
    setGate(null)
    try {
      setEvaluation(await guardrailApi.evaluate(selectedQuoteId))
    } finally {
      setEvaluating(false)
    }
  }, [selectedQuoteId])

  const publish = useCallback(async () => {
    if (!selectedQuoteId) return
    setPublishing(true)
    try {
      const answer = await guardrailApi.publish(selectedQuoteId)
      setGate({ allowed: true, answer })
      setEvaluation(answer)
      reloadSummary()
    } catch (error) {
      // A blocked publish is the rule working. Re-read the verdicts so the page shows
      // which rule stopped it, and record the refusal as a result rather than a fault.
      setGate({ allowed: false, error })
      try {
        setEvaluation(await guardrailApi.evaluate(selectedQuoteId))
      } catch {
        // The verdicts were already on screen; a failed refresh is not worth a second
        // error box.
      }
    } finally {
      setPublishing(false)
    }
  }, [selectedQuoteId, reloadSummary])

  if (vocabulary.loading || rules.loading) return <Spinner label="Loading quote guardrails" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />

  const limits = vocabulary.data?.limitations || []
  const outcomeOptions = vocabulary.data?.rule?.outcomes || []
  const ruleList = rules.data?.rules || []
  const quoteList = quotesOf(quotes.data)

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <SectionLabel>WF-090 · Quoting</SectionLabel>
        <h1 className="font-display text-2xl font-semibold text-foreground">Quote guardrails</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Rules check a quote as it is built. A <strong>Show warning</strong> rule warns the
          seller; a <strong>Block publish</strong> rule stops Share and Publish until the quote
          is fixed.
        </p>
      </header>

      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard
          label="Rules"
          value={summary.data?.rules?.total ?? '—'}
          hint={`${summary.data?.rules?.enabled ?? 0} enabled · ${summary.data?.rules?.disabled ?? 0} disabled`}
          icon="schema"
        />
        <StatCard
          label="Block rules"
          value={summary.data?.rules?.block_publish ?? '—'}
          hint={`${summary.data?.rules?.show_warning ?? 0} warn only`}
          icon="audit"
        />
        <StatCard
          label="Publish attempts"
          value={summary.data?.publish_attempts?.total ?? '—'}
          hint={`${summary.data?.publish_attempts?.blocked ?? 0} stopped by a rule`}
          icon="close"
        />
      </div>

      {/* ---------------------------------------------------------------- */}
      {/* Configure                                                         */}
      {/* ---------------------------------------------------------------- */}
      <Card>
        <SectionLabel>Add a rule</SectionLabel>
        <form
          className="mt-4 grid gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            save()
          }}
        >
          <Field label="Name" id="rule-name" hint="What the seller sees this rule called.">
            <input
              id="rule-name"
              className={inputClass}
              value={draft.name}
              onChange={setDraftField('name')}
              placeholder="Enterprise bundle radius"
            />
          </Field>

          <Field
            label="Definition"
            id="rule-definition"
            hint="The expression the server evaluates. A true evaluation is a violation."
          >
            <textarea
              id="rule-definition"
              className={`${inputClass} min-h-24 py-2 font-mono`}
              value={draft.rule_definition}
              onChange={(event) => {
                setDraftField('rule_definition')(event)
                resetCheck()
              }}
              placeholder='SOLD_TOGETHER FROM line_item WHERE [hs_product_id] IN ("A","B")'
            />
          </Field>

          {examples.length > 0 && (
            <div className="space-y-2">
              <SectionLabel>Quoted examples</SectionLabel>
              <div className="flex flex-wrap gap-2">
                {examples.map((example) => (
                  <button
                    key={example}
                    type="button"
                    onClick={() => {
                      setDraft((current) => ({ ...current, rule_definition: example }))
                      resetCheck()
                    }}
                    className="max-w-full truncate rounded-sm border border-border-subtle px-3 py-2 font-mono text-xs text-muted-foreground transition-colors hover:border-accent hover:text-accent"
                  >
                    {example}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Outcome" id="rule-outcome">
              <select
                id="rule-outcome"
                className={inputClass}
                value={draft.outcome}
                onChange={setDraftField('outcome')}
              >
                {outcomeOptions.map((option) => (
                  <option key={option.outcome} value={option.outcome}>
                    {option.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Message" id="rule-message" hint="Shown to the seller when the rule fires.">
              <input
                id="rule-message"
                className={inputClass}
                value={draft.message}
                onChange={setDraftField('message')}
              />
            </Field>
          </div>

          {checked && checked.valid && (
            <Notice tone="success" title="Readable">
              <span className="font-mono text-xs">{checked.normalised}</span>
            </Notice>
          )}
          {checked && !checked.valid && (
            <Notice tone="error" title="The parser refused this definition">
              <span className="font-mono text-xs">{checked.error}</span>
              {checked.detail && <span className="ml-2">{checked.detail}</span>}
            </Notice>
          )}
          {formError && (
            <Notice tone="error" title={`Could not save (${formError.status || 'error'})`}>
              {formError.message}
            </Notice>
          )}

          <div className="flex flex-wrap gap-2">
            <Button icon="search" onClick={runCheck} disabled={checking || !draft.rule_definition}>
              {checking ? 'Checking…' : 'Check definition'}
            </Button>
            <Button
              variant="primary"
              icon="plus"
              type="submit"
              disabled={saving || !draft.name || !draft.rule_definition || !draft.message}
            >
              {saving ? 'Saving…' : 'Save rule'}
            </Button>
          </div>
        </form>
      </Card>

      {/* ---------------------------------------------------------------- */}
      {/* The configured rules                                              */}
      {/* ---------------------------------------------------------------- */}
      <section className="space-y-3">
        <SectionLabel>Configured rules</SectionLabel>
        {rules.error && <ErrorNote error={rules.error} onRetry={reloadRules} />}
        {!rules.error && ruleList.length === 0 && (
          <EmptyState
            title="No rules yet"
            description="A room with no rules publishes anything. Add one above to guard a discount, a bundle, or an incompatible pairing."
          />
        )}
        {ruleList.map((rule) => (
          <RuleCard
            key={rule.id}
            rule={rule}
            busy={busyRuleId === rule.id}
            onToggle={toggleRule}
            onDelete={removeRule}
          />
        ))}
      </section>

      {/* ---------------------------------------------------------------- */}
      {/* Try it on a quote                                                 */}
      {/* ---------------------------------------------------------------- */}
      <Card>
        <SectionLabel>Try the rules on a quote</SectionLabel>
        <p className="mt-2 text-sm text-muted-foreground">
          Evaluating writes nothing: the rules run against the quote as it stands, so a seller
          can watch them as they edit.
        </p>
        <div className="mt-4 grid gap-4 sm:grid-cols-[1fr_auto] sm:items-end">
          <Field label="Quote" id="guardrail-quote">
            <select
              id="guardrail-quote"
              className={inputClass}
              value={selectedQuoteId}
              onChange={(event) => {
                // A verdict belongs to the quote it was computed for. Switching quotes
                // clears it here rather than in an effect, so the previous quote's
                // verdicts can never be read against the new one.
                setSelectedQuoteId(event.target.value)
                setEvaluation(null)
                setGate(null)
              }}
            >
              <option value="">Choose a quote…</option>
              {quoteList.map((quote) => (
                <option key={quote.id} value={quote.id}>
                  {(quote.data || quote).name || quote.id}
                </option>
              ))}
            </select>
          </Field>
          <div className="flex flex-wrap gap-2">
            <Button icon="refresh" onClick={evaluate} disabled={!selectedQuoteId || evaluating}>
              {evaluating ? 'Checking…' : 'Evaluate'}
            </Button>
            <Button
              variant="primary"
              icon="audit"
              onClick={publish}
              disabled={!selectedQuoteId || publishing}
            >
              {publishing ? 'Publishing…' : 'Publish'}
            </Button>
          </div>
        </div>

        {quoteList.length === 0 && !quotes.loading && (
          <p className="mt-3 text-sm text-muted-foreground">
            No quotes exist yet. Author one in WF-086 and it appears here.
          </p>
        )}

        {gate && (
          <div className="mt-4">
            {gate.allowed ? (
              <Notice tone="success" title="Published">
                No Block publish rule fired
                {gate.answer?.warnings?.length
                  ? `, with ${gate.answer.warnings.length} warning(s) recorded.`
                  : '.'}
              </Notice>
            ) : (
              <Notice tone="error" title={`Publish stopped (${gate.error?.status || 409})`}>
                {gate.error?.message}
              </Notice>
            )}
          </div>
        )}

        {evaluation && (
          <div className="mt-4 space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <SectionLabel>Verdicts</SectionLabel>
              <span className="font-mono text-xs text-muted-foreground">
                {evaluation.rules_evaluated} rule(s) evaluated · {evaluation.reason_code}
              </span>
            </div>
            <VerdictList evaluation={evaluation} />
          </div>
        )}
      </Card>

      {/* ---------------------------------------------------------------- */}
      {/* What this build does not do                                       */}
      {/* ---------------------------------------------------------------- */}
      <Card>
        <SectionLabel>Limits of the rule language</SectionLabel>
        <p className="mt-2 text-sm text-muted-foreground">
          These are documented limits of the DSL, not faults in this build. A definition that
          uses one is refused with the reason code beside it.
        </p>
        <ul className="mt-3 space-y-2">
          {limits.map((limit) => (
            <li key={limit.code} className="text-sm">
              <span className="font-mono text-xs text-muted-foreground">{limit.code}</span>
              <span className="ml-2 text-foreground">{limit.text}</span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}
