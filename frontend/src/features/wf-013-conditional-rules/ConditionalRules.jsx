import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Icon,
  Spinner,
  inputClass,
  useAsync,
} from '@/components/ui'

import { rulesApi } from './api'
import { ICONS } from './icons'
import { Notice, Segmented } from './primitives'

/**
 * WF-013: personalise room content with conditional rules.
 *
 * Ported from `feature/WF-013-personalise-room-content-with-conditional-rules`.
 * The page follows the researched flow: pick a room, open a block's
 * "Add rules", build conditions on the "Show block if" screen, then preview
 * with variable values before committing.
 *
 * Two things this page is careful about, because both are easy to get wrong:
 *
 * 1. **Preview never writes.** "Preview" calls the read-only endpoint, so a
 *    seller can try values repeatedly without generating stored records.
 *    "Personalise" is the explicit commit.
 * 2. **The constraint is shown, not just enforced.** The Or limit, the Accept
 *    Block exception and the Saved Block rule loss are all surfaced in the
 *    interface with the reason, so a seller is never left guessing why a
 *    button is disabled.
 *
 * Two departures from the branch, both forced by the contract: the calls go
 * through this feature's own `rulesApi` rather than methods appended to the
 * shared `api` object, and the glyphs the shared `PATHS` map does not carry are
 * passed as `path` rather than added to `components/ui.jsx`.
 */

const EMPTY_CONDITION = { variable: '', category: 'text', modifier: '', value: '' }

const REASON_COPY = {
  no_rule: 'No rule: this block always shows.',
  all_incomplete: 'Every condition is incomplete, so the block shows.',
  matched: 'Shown: the conditions matched.',
  unmatched: 'Hidden: the conditions did not match.',
  // A fifth reason the server can return and the design doc does not enumerate:
  // the block carries a rule that would not validate, so it fails open and shows.
  // Saying "no rule" here would be a lie and would send a seller looking for a
  // rule that is right there.
  invalid_rule: 'This rule cannot be applied, so the block shows unhidden.',
}

const STATUS_TONES = {
  matched: 'insert',
  unmatched: 'delete',
  incomplete: 'restore',
  no_value: 'restore',
  not_numeric: 'restore',
  invalid_rule: 'delete',
}

/** Human labels for the modifier slugs the API uses. */
const MODIFIER_LABELS = {
  is: 'is',
  is_not: 'is not',
  contains: 'contains',
  does_not_contain: 'does not contain',
  starts_with: 'starts with',
  ends_with: 'ends with',
  includes: 'includes',
  does_not_include: 'does not include',
  equals: 'equals',
  does_not_equal: 'does not equal',
  is_more_than: 'is more than',
  is_less_than: 'is less than',
  has_no_value: 'has no value',
  has_any_value: 'has any value',
}

/** Turn a stored rule back into editable form state. */
function toDraft(rule) {
  if (!rule || !Array.isArray(rule.conditions) || rule.conditions.length === 0) {
    return { join: 'and', conditions: [{ ...EMPTY_CONDITION }] }
  }
  return {
    join: rule.join || 'and',
    conditions: rule.conditions.map((condition) => ({
      variable: condition.variable ?? '',
      category: condition.category || 'text',
      modifier: condition.modifier || '',
      value: condition.value ?? '',
      case_sensitive: Boolean(condition.case_sensitive),
    })),
  }
}

function RuleBuilder({ block, catalog, variables, onSave, onCancel, saving, error }) {
  const [draft, setDraft] = useState(() => toDraft(block.data.rule))

  const acceptsRules = !catalog.forbidden_block_types.includes(
    String(block.data.type || '')
      .toLowerCase()
      .replace(/[\s-]+/g, '_'),
  )
  const maxOr = catalog.limits.max_or_conditions
  const orCount = draft.conditions.length
  const orExceeded = draft.join === 'or' && orCount > maxOr

  function updateCondition(index, patch) {
    setDraft((current) => ({
      ...current,
      conditions: current.conditions.map((condition, position) =>
        position === index
          ? {
              ...condition,
              ...patch,
              // Changing category invalidates the modifier, so reset it to the
              // first of the new category rather than leaving an impossible pair.
              ...(patch.category && patch.category !== condition.category
                ? { modifier: catalog.categories[patch.category]?.[0] || '' }
                : {}),
            }
          : condition,
      ),
    }))
  }

  function addCondition() {
    setDraft((current) => ({
      ...current,
      conditions: [...current.conditions, { ...EMPTY_CONDITION }],
    }))
  }

  function removeCondition(index) {
    setDraft((current) => ({
      ...current,
      conditions: current.conditions.filter((_, position) => position !== index),
    }))
  }

  function submit(event) {
    event.preventDefault()
    onSave({
      join: draft.join,
      conditions: draft.conditions
        .filter((condition) => condition.variable || condition.modifier)
        .map((condition) => ({
          variable: condition.variable,
          category: condition.category,
          modifier: condition.modifier,
          value: condition.value,
          ...(condition.case_sensitive ? { case_sensitive: true } : {}),
        })),
    })
  }

  if (!acceptsRules) {
    return (
      <Card>
        <Notice tone="warn" title="An Accept Block cannot have rules">
          Every other block type can. The researched product makes the Accept Block the single
          exception, because an accept block is the call to action and hiding it would remove the
          buyer's next step.
        </Notice>
        <div className="mt-4">
          <Button onClick={onCancel}>Close</Button>
        </div>
      </Card>
    )
  }

  return (
    <Card>
      <form onSubmit={submit} className="space-y-4">
        <header>
          <h3 className="font-mono text-base font-semibold">
            Show block if
            {draft.join === 'or' && (
              <span className="ml-2 font-sans text-xs font-normal">
                <span className={orExceeded ? 'text-destructive' : 'text-muted-foreground'}>
                  {orCount} / {maxOr} Or
                </span>{' '}
                conditions
              </span>
            )}
          </h3>
          <p className="mt-1 text-sm text-muted-foreground">{block.data.title || 'Untitled block'}</p>
        </header>

        <Segmented
          label="Combine conditions with"
          name="rule-join"
          value={draft.join}
          onChange={(join) => setDraft((current) => ({ ...current, join }))}
          options={catalog.joiners.map((joiner) => ({
            value: joiner,
            label: joiner === 'and' ? 'And' : 'Or',
          }))}
          hint={
            draft.join === 'or'
              ? `Or conditions are limited to ${maxOr} per block. And has no limit.`
              : 'And has no limit on the number of conditions.'
          }
        />

        {orExceeded && (
          <Notice tone="danger" title="Too many Or conditions">
            A rule may use at most {maxOr} Or conditions and this one has {orCount}. Remove{' '}
            {orCount - maxOr} condition{orCount - maxOr === 1 ? '' : 's'}, or switch to And.
          </Notice>
        )}

        <ol className="space-y-3">
          {draft.conditions.map((condition, index) => {
            const modifiers = catalog.categories[condition.category] || []
            return (
              <li key={index} className="rounded-lg border border-border-subtle/30 bg-background/40 p-3">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span className="font-mono text-xs text-muted-foreground">
                    Condition {index + 1}
                    {index > 0 && (
                      <span className="ml-1 text-foreground">
                        {draft.join === 'and' ? ' AND' : ' OR'}
                      </span>
                    )}
                  </span>
                  <Button
                    icon="trash"
                    variant="danger"
                    onClick={() => removeCondition(index)}
                    disabled={draft.conditions.length === 1}
                    aria-label={`Delete condition ${index + 1}`}
                  >
                    Delete
                  </Button>
                </div>

                <div className="grid gap-3 sm:grid-cols-3">
                  <Field label="Variable" id={`cond-${index}-variable`}>
                    <select
                      id={`cond-${index}-variable`}
                      className={inputClass}
                      value={condition.variable}
                      onChange={(event) => updateCondition(index, { variable: event.target.value })}
                    >
                      <option value="">Not set</option>
                      {variables.map((variable) => (
                        <option key={variable.name} value={variable.name}>
                          {variable.label}
                          {variable.source === 'crm' ? ` (${variable.crm || 'CRM'})` : ''}
                        </option>
                      ))}
                    </select>
                  </Field>

                  <Field label="Category" id={`cond-${index}-category`}>
                    <select
                      id={`cond-${index}-category`}
                      className={inputClass}
                      value={condition.category}
                      onChange={(event) => updateCondition(index, { category: event.target.value })}
                    >
                      {Object.keys(catalog.categories).map((category) => (
                        <option key={category} value={category}>
                          {category}
                        </option>
                      ))}
                    </select>
                  </Field>

                  <Field label="Condition" id={`cond-${index}-modifier`}>
                    <select
                      id={`cond-${index}-modifier`}
                      className={inputClass}
                      value={condition.modifier}
                      onChange={(event) => updateCondition(index, { modifier: event.target.value })}
                    >
                      <option value="">Choose</option>
                      {modifiers.map((modifier) => (
                        <option key={modifier} value={modifier}>
                          {MODIFIER_LABELS[modifier] || modifier}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>

                {condition.category !== 'any' && (
                  <div className="mt-3">
                    <Field
                      label="Value"
                      id={`cond-${index}-value`}
                      hint="An empty value and 0 are both valid match values."
                    >
                      <input
                        id={`cond-${index}-value`}
                        className={inputClass}
                        value={condition.value}
                        onChange={(event) => updateCondition(index, { value: event.target.value })}
                      />
                    </Field>
                  </div>
                )}

                {condition.category === 'text' && (
                  <label className="mt-3 flex min-h-11 cursor-pointer items-center gap-2 text-sm text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={Boolean(condition.case_sensitive)}
                      onChange={(event) =>
                        updateCondition(index, { case_sensitive: event.target.checked })
                      }
                      className="h-4 w-4 accent-accent"
                    />
                    Case sensitive
                  </label>
                )}
              </li>
            )
          })}
        </ol>

        <Button icon="plus" onClick={addCondition} disabled={draft.join === 'or' && orCount >= maxOr}>
          Add condition
        </Button>

        <Notice tone="info" title="A condition with no variable is ignored">
          Incomplete conditions are skipped and the block still shows, so a half-finished rule can
          never hide content from a buyer.
        </Notice>

        {error && <ErrorNote error={error} />}

        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" disabled={saving || orExceeded}>
            {saving ? 'Saving…' : 'Save rule'}
          </Button>
          <Button onClick={onCancel}>Cancel</Button>
        </div>
      </form>
    </Card>
  )
}

function TraceRow({ condition }) {
  return (
    <li className="flex flex-wrap items-center gap-2 py-1 text-sm">
      <Badge tone={STATUS_TONES[condition.status] || 'neutral'}>{condition.status}</Badge>
      <span className="font-mono text-foreground">{condition.variable || 'no variable'}</span>
      <span className="text-muted-foreground">
        {MODIFIER_LABELS[condition.modifier] || condition.modifier}
      </span>
      <span className="font-mono text-muted-foreground">
        {condition.status === 'incomplete' || condition.value === undefined
          ? ''
          : String(condition.value)}
      </span>
      {condition.observed !== undefined && condition.observed !== null && (
        <span className="text-muted-foreground/70">
          (saw {String(condition.observed) === '' ? 'empty' : String(condition.observed)})
        </span>
      )}
    </li>
  )
}

export default function ConditionalRules() {
  const catalog = useAsync(() => rulesApi.catalog(), [])
  const rooms = useAsync(() => rulesApi.rooms(), [])
  const variableCatalogue = useAsync(() => rulesApi.variables(), [])

  const [roomId, setRoomId] = useState('')
  const [blocks, setBlocks] = useState({ loading: false, data: null, error: null })
  const [editing, setEditing] = useState(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)

  const [variables, setVariables] = useState({})
  const [preview, setPreview] = useState(null)
  const [previewing, setPreviewing] = useState(false)
  const [previewError, setPreviewError] = useState(null)
  const [notice, setNotice] = useState(null)

  // Default to the first room so the page is useful on arrival rather than
  // presenting an empty picker.
  useEffect(() => {
    const records = rooms.data?.records || []
    if (!roomId && records.length > 0) setRoomId(records[0].id)
  }, [rooms.data, roomId])

  const loadBlocks = useCallback(async () => {
    if (!roomId) {
      setBlocks({ loading: false, data: null, error: null })
      return
    }
    setBlocks((current) => ({ ...current, loading: true, error: null }))
    try {
      const data = await rulesApi.listBlocks(roomId)
      setBlocks({ loading: false, data, error: null })
    } catch (error) {
      setBlocks({ loading: false, data: null, error })
    }
  }, [roomId])

  useEffect(() => {
    setEditing(null)
    setPreview(null)
    setNotice(null)
    loadBlocks()
  }, [loadBlocks])

  // The value inputs are seeded from the declared variables so a seller only
  // types the values they actually condition on.
  const declared = variableCatalogue.data?.variables || []
  useEffect(() => {
    setVariables(Object.fromEntries(declared.map((variable) => [variable.name, ''])))
  }, [variableCatalogue.data])

  const decisionFor = useMemo(() => {
    if (!preview) return () => null
    return (blockId) => preview.blocks.find((entry) => entry.block_id === blockId) || null
  }, [preview])

  /** Only the values actually filled in are sent, so "not supplied" stays
   *  distinguishable from "supplied empty" in the trace (D3 and S9). */
  function supplied() {
    return Object.fromEntries(Object.entries(variables).filter(([, value]) => value !== ''))
  }

  async function saveRule(rule) {
    setSaving(true)
    setSaveError(null)
    try {
      await rulesApi.putRule(roomId, editing.id, rule)
      setEditing(null)
      await loadBlocks()
      setNotice({
        tone: 'info',
        title: 'Rule saved',
        body: 'Preview to see which blocks it reveals.',
      })
    } catch (error) {
      setSaveError(error)
    } finally {
      setSaving(false)
    }
  }

  async function removeRule(block) {
    setSaving(true)
    setSaveError(null)
    try {
      await rulesApi.deleteRule(roomId, block.id)
      setEditing(null)
      await loadBlocks()
      setNotice({
        tone: 'info',
        title: 'Rule removed',
        body: 'This block will now always show.',
      })
    } catch (error) {
      setSaveError(error)
    } finally {
      setSaving(false)
    }
  }

  async function saveToLibrary(block) {
    setNotice(null)
    setSaveError(null)
    try {
      const result = await rulesApi.saveBlockToLibrary(roomId, block.id)
      setNotice(
        result.rules_dropped
          ? {
              tone: 'warn',
              title: 'Saved to the library without its rule',
              body: 'A Saved Block cannot have rules, so the copy in the library will always show. The dropped rule has been recorded on the saved block.',
            }
          : { tone: 'info', title: 'Saved to the library', body: 'This block had no rule to lose.' },
      )
      await loadBlocks()
    } catch (error) {
      setSaveError(error)
    }
  }

  async function runPreview() {
    setPreviewing(true)
    setPreviewError(null)
    try {
      setPreview(await rulesApi.previewRoom(roomId, supplied()))
    } catch (error) {
      setPreviewError(error)
    } finally {
      setPreviewing(false)
    }
  }

  async function personalise() {
    setPreviewing(true)
    setPreviewError(null)
    try {
      const result = await rulesApi.personaliseRoom(roomId, supplied())
      setPreview(result)
      setNotice({
        tone: 'info',
        title: 'Personalisation recorded',
        body: `${result.shown.length} shown, ${result.hidden.length} hidden. The values and the decision are now in the audit trail.`,
      })
    } catch (error) {
      setPreviewError(error)
    } finally {
      setPreviewing(false)
    }
  }

  if (catalog.loading || rooms.loading || variableCatalogue.loading) {
    return <Spinner label="Loading rule builder" />
  }
  if (catalog.error) return <ErrorNote error={catalog.error} onRetry={catalog.refetch} />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />
  if (variableCatalogue.error) {
    return <ErrorNote error={variableCatalogue.error} onRetry={variableCatalogue.refetch} />
  }

  const catalogData = catalog.data
  const roomRecords = rooms.data?.records || []

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-mono text-2xl font-semibold">Conditional rules</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Reveal or hide room content based on the variable values supplied when a room is
          personalised. Rules are evaluated once, at that moment, not per viewer.
        </p>
      </header>

      {notice && (
        <Notice tone={notice.tone} title={notice.title}>
          {notice.body}
        </Notice>
      )}

      {roomRecords.length === 0 ? (
        <EmptyState
          title="No sales rooms yet"
          description="Create a room first, then add blocks to it and give those blocks rules."
        />
      ) : (
        <>
          <Card>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Room" id="rule-room">
                <select
                  id="rule-room"
                  className={inputClass}
                  value={roomId}
                  onChange={(event) => setRoomId(event.target.value)}
                >
                  {roomRecords.map((room) => (
                    <option key={room.id} value={room.id}>
                      {room.data.name || room.id}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
          </Card>

          {editing && (
            <RuleBuilder
              block={editing}
              catalog={catalogData}
              variables={declared}
              onSave={saveRule}
              onCancel={() => {
                setEditing(null)
                setSaveError(null)
              }}
              saving={saving}
              error={saveError}
            />
          )}

          <Card>
            <header className="mb-4 flex flex-wrap items-end justify-between gap-3">
              <div>
                <h2 className="font-mono text-base font-semibold">Variable values</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  Leave a field empty to leave that variable unsupplied, which is different from
                  supplying it as empty.
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button onClick={runPreview} disabled={previewing}>
                  <Icon path={ICONS.eye} /> Preview
                </Button>
                <Button variant="primary" onClick={personalise} disabled={previewing}>
                  <Icon path={ICONS.check} /> Personalise
                </Button>
              </div>
            </header>

            {declared.length === 0 ? (
              <Notice tone="info" title="No variables declared">
                Add records to the <span className="font-mono">variable</span> collection with a{' '}
                <span className="font-mono">name</span> and a{' '}
                <span className="font-mono">source</span> of account or crm. A CRM-imported
                variable appears in this list exactly like an account one.
              </Notice>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2">
                {declared.map((variable) => (
                  <Field
                    key={variable.name}
                    label={`${variable.label}${variable.source === 'crm' ? ` (${variable.crm || 'CRM'})` : ''}`}
                    id={`var-${variable.name}`}
                    hint={variable.category}
                  >
                    <input
                      id={`var-${variable.name}`}
                      className={inputClass}
                      value={variables[variable.name] ?? ''}
                      onChange={(event) =>
                        setVariables({ ...variables, [variable.name]: event.target.value })
                      }
                    />
                  </Field>
                ))}
              </div>
            )}

            {previewError && (
              <div className="mt-4">
                <ErrorNote error={previewError} />
              </div>
            )}

            {(preview?.problems || []).length > 0 && (
              <div className="mt-4">
                <Notice tone="danger" title="A rule on this room cannot be applied">
                  <ul className="mt-1 list-disc space-y-0.5 pl-4">
                    {preview.problems.map((problem) => (
                      <li key={problem.block_id}>
                        <span className="font-mono">{problem.title || problem.block_id}</span>:{' '}
                        {problem.problem}. The block shows unhidden until the rule is fixed.
                      </li>
                    ))}
                  </ul>
                </Notice>
              </div>
            )}
          </Card>

          <section>
            <h2 className="mb-3 font-mono text-base font-semibold">Blocks</h2>

            {blocks.loading && <Spinner label="Loading blocks" />}
            {blocks.error && <ErrorNote error={blocks.error} onRetry={loadBlocks} />}

            {!blocks.loading && !blocks.error && (
              <>
                {(blocks.data?.blocks || []).length === 0 ? (
                  <EmptyState
                    title="No blocks in this room"
                    description="Add a block before giving it a rule."
                  />
                ) : (
                  <ul className="grid gap-3 md:grid-cols-2">
                    {blocks.data.blocks.map((block) => {
                      const decision = decisionFor(block.id)
                      const hasRule = Boolean(block.data.rule)
                      return (
                        <li key={block.id}>
                          <Card className="card-hover h-full">
                            <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0">
                                <h3 className="truncate font-mono text-sm font-semibold">
                                  {block.data.title || 'Untitled block'}
                                </h3>
                                <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                                  {block.data.type || 'untyped'}
                                </p>
                              </div>
                              {decision ? (
                                <Badge tone={decision.shown ? 'insert' : 'delete'}>
                                  {decision.shown ? 'shown' : 'hidden'}
                                </Badge>
                              ) : (
                                <Badge tone={hasRule ? 'update' : 'neutral'}>
                                  {hasRule ? 'has rule' : 'no rule'}
                                </Badge>
                              )}
                            </div>

                            {hasRule && (
                              <p className="mt-3 text-xs text-muted-foreground">
                                {block.data.rule.join === 'or' ? 'Or' : 'And'} of{' '}
                                {block.data.rule.conditions.length} condition
                                {block.data.rule.conditions.length === 1 ? '' : 's'}
                              </p>
                            )}

                            {decision && (
                              <div className="mt-3">
                                <p className="mb-1 text-xs text-muted-foreground">
                                  {REASON_COPY[decision.reason] || decision.reason}
                                </p>
                                {decision.problems && decision.problems.length > 0 && (
                                  <Notice tone="danger" title="This rule cannot be applied">
                                    {decision.problems.join('; ')}
                                  </Notice>
                                )}
                                {decision.conditions.length > 0 && (
                                  <ul className="divide-y divide-border-subtle/20">
                                    {decision.conditions.map((condition, index) => (
                                      <TraceRow key={index} condition={condition} />
                                    ))}
                                  </ul>
                                )}
                              </div>
                            )}

                            {saveError && !editing && (
                              <div className="mt-3">
                                <ErrorNote error={saveError} />
                              </div>
                            )}

                            <div className="mt-4 flex flex-wrap gap-2">
                              <Button
                                onClick={() => {
                                  setSaveError(null)
                                  setEditing(editing?.id === block.id ? null : block)
                                }}
                              >
                                <Icon path={ICONS.rule} /> {hasRule ? 'Edit rules' : 'Add rules'}
                              </Button>
                              {hasRule && (
                                <Button variant="danger" onClick={() => removeRule(block)}>
                                  Remove rule
                                </Button>
                              )}
                              <Button onClick={() => saveToLibrary(block)}>
                                <Icon path={ICONS.library} /> Save to library
                              </Button>
                            </div>
                          </Card>
                        </li>
                      )
                    })}
                  </ul>
                )}
              </>
            )}
          </section>
        </>
      )}
    </div>
  )
}
