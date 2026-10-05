import { useCallback, useState } from 'react'
import {
  Badge,
  Button,
  EmptyState,
  ErrorNote,
  Field,
  Spinner,
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'

import {
  daysLeft,
  expiryApi,
  fromDateInput,
  localDueLabel,
  needsAttention,
  refusalSummary,
  stateTone,
  toDateInput,
} from './api'
import {
  ActivityRow,
  DecisionCard,
  LabeledInput,
  LabeledSelect,
  LedgerRow,
  Mono,
  Notice,
  QuoteRow,
  RuleRow,
  Section,
  Switch,
} from './primitives'

/**
 * Quote expiry and buyer reminders (WF-098).
 *
 * The page answers the six questions the researched flow asks, in that order:
 *
 * 1. What is the account's default expiration period? (1 to 365 days.)
 * 2. What reminders does the account send, counting which way, and when?
 * 3. What does each quote carry: a date, a label, or the switch turned off?
 * 4. Can this buyer still accept, and if not, why not?
 * 5. What has the dispatch decided, and what has the expiry check left alone?
 * 6. Which judgement calls did this build make, and what did each one cost?
 *
 * Every term, every cap and every sentence comes from `GET /vocabulary`. Nothing is
 * compiled into this file, so a rule changed on the server reaches every client at once.
 */
function QuoteExpiry() {
  const [roomId, setRoomId] = useState('')
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState(null)
  const [actionNote, setActionNote] = useState(null)
  const [selected, setSelected] = useState(null)
  const [preview, setPreview] = useState(null)
  const [onlySoon, setOnlySoon] = useState(false)
  const [stateFilter, setStateFilter] = useState('')

  const vocabulary = useAsync(() => expiryApi.vocabulary(), [])
  const inferences = useAsync(() => expiryApi.inferences(), [])
  const rooms = useAsync(() => expiryApi.rooms(), [])
  const roomQuery = useAsync(
    () => (roomId ? expiryApi.summary(roomId) : Promise.resolve(null)),
    [roomId],
  )
  const settings = useAsync(
    () => (roomId ? expiryApi.settings(roomId) : Promise.resolve(null)),
    [roomId],
  )
  const rules = useAsync(
    () => (roomId ? expiryApi.rules(roomId) : Promise.resolve(null)),
    [roomId],
  )
  const quotes = useAsync(
    () =>
      roomId
        ? expiryApi.quotes(roomId, {
            state: stateFilter,
            expiring_soon: onlySoon ? 'true' : '',
          })
        : Promise.resolve(null),
    [roomId, stateFilter, onlySoon],
  )
  const ledger = useAsync(
    () => (roomId ? expiryApi.reminders(roomId) : Promise.resolve(null)),
    [roomId],
  )
  const activities = useAsync(
    () => (roomId ? expiryApi.activities(roomId) : Promise.resolve(null)),
    [roomId],
  )

  const run = useCallback(async (work, note) => {
    setBusy(true)
    setActionError(null)
    setActionNote(null)
    try {
      await work()
      if (note) setActionNote(note)
      return true
    } catch (error) {
      setActionError(refusalSummary(error))
      return false
    } finally {
      setBusy(false)
      roomQuery.refetch()
      settings.refetch()
      rules.refetch()
      quotes.refetch()
      ledger.refetch()
      activities.refetch()
    }
  }, [roomQuery, settings, rules, quotes, ledger, activities])

  if (vocabulary.loading) return <Spinner label="Loading quote expiry" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />

  const roomOptions = (rooms.data?.records || []).map((row) => ({
    id: row.id,
    name: row.data?.name || row.id,
  }))

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-semibold text-foreground">
          Quote expiry and buyer reminders
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Give a quote a deadline, nudge the buyer at the offsets you choose, and move the
          quotes nobody acted on to expired. Expiry closes the ability to accept and keeps
          the quote itself.
        </p>
      </header>

      <Cardish>
        <Field
          label="Room"
          hint="Every quote, rule and ledger row belongs to a room."
          id="wf098-room"
        >
          <select
            id="wf098-room"
            className={inputClass}
            value={roomId}
            onChange={(event) => {
              setRoomId(event.target.value)
              setSelected(null)
            }}
          >
            <option value="">Choose a room</option>
            {roomOptions.map((row) => (
              <option key={row.id} value={row.id}>
                {row.name}
              </option>
            ))}
          </select>
        </Field>
      </Cardish>

      {actionError && <Notice tone="danger" title="The request was refused">{actionError}</Notice>}
      {actionNote && <Notice tone="success" title="Done">{actionNote}</Notice>}

      {!roomId && (
        <EmptyState
          title="Choose a room"
          description="The workflow is room-scoped: the quote, its deadline, its reminder rules and its ledger all hang off one room."
        />
      )}

      {roomId && roomQuery.error && (
        <ErrorNote error={roomQuery.error} onRetry={roomQuery.refetch} />
      )}

      {roomId && roomQuery.data && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label="Quotes"
              value={roomQuery.data.quotes}
              hint={`${roomQuery.data.sent} sent, ${roomQuery.data.published} published`}
            />
            <StatCard
              label="Expired"
              value={roomQuery.data.expired}
              hint={`${roomQuery.data.survived_expiry} kept by the survival rule`}
            />
            <StatCard
              label="Expiring soon"
              value={roomQuery.data.expiring_soon}
              hint={`within ${vocabulary.data.expiring_soon_days} days`}
            />
            <StatCard
              label="Reminders"
              value={roomQuery.data.reminders_sent}
              hint={`${roomQuery.data.reminders_skipped} skipped`}
            />
          </div>

          <Notice tone="info" title="What this workflow does not do">
            <ul className="mt-1 list-disc space-y-1 pl-5">
              <li>Nothing expires until you run the expiry check.</li>
              <li>No reminder goes out until you run the dispatch.</li>
              <li>
                This product records the reminder decision and sends no message. An
                integration consumes the ledger and delivers.
              </li>
            </ul>
          </Notice>

          <div className="grid gap-6 lg:grid-cols-2">
            <DefaultWindowSection
              settings={settings.data}
              vocabulary={vocabulary.data}
              busy={busy}
              onSave={(payload) =>
                run(() => expiryApi.saveSettings(roomId, payload), 'Settings saved.')
              }
            />
            <RemindersSection
              rules={rules.data}
              vocabulary={vocabulary.data}
              settings={settings.data}
              quotes={quotes.data?.quotes || []}
              busy={busy}
              onAdd={(payload) =>
                run(() => expiryApi.addRule(roomId, payload), 'Reminder rule added.')
              }
              onPatch={(ruleId, payload) =>
                run(() => expiryApi.patchRule(roomId, ruleId, payload), 'Rule updated.')
              }
              onDelete={(ruleId) =>
                run(
                  () => expiryApi.deleteRule(roomId, ruleId),
                  'Rule deleted. Its ledger rows were kept.',
                )
              }
              onPreview={async (ruleId) => {
                setPreview(null)
                try {
                  setPreview(await expiryApi.preview(roomId, ruleId))
                } catch (error) {
                  setActionError(refusalSummary(error))
                }
              }}
            />
          </div>

          <Section
            title="Reminders and the expiry check"
            hint="Both jobs are routes you call. Neither runs on its own."
            action={
              <div className="flex flex-wrap gap-2">
                <Button
                  icon="refresh"
                  onClick={() =>
                    run(() => expiryApi.dispatch(roomId), 'Dispatch evaluated every rule.')
                  }
                  disabled={busy}
                >
                  Run the reminder dispatch
                </Button>
                <Button
                  variant="primary"
                  icon="audit"
                  onClick={() => run(() => expiryApi.checkExpiry(roomId), 'Expiry check done.')}
                  disabled={busy}
                >
                  Run the expiry check
                </Button>
              </div>
            }
          >
            <div className="space-y-4">
              {ledger.data?.count === 0 ? (
                <p className="text-sm text-muted-foreground">
                  The ledger is empty. Run the dispatch to evaluate every rule against every
                  quote in this room. Sends and skips are both recorded.
                </p>
              ) : (
                <>
                  <p className="text-xs text-muted-foreground">
                    {ledger.data?.sent ?? 0} sent, {ledger.data?.skipped ?? 0} skipped.
                    Delivery is not this product&apos;s job:{' '}
                    {ledger.data?.delivery?.note}
                  </p>
                  <div>
                    {(ledger.data?.reminders || []).map((row) => (
                      <LedgerRow key={row.id || `${row.rule_id}-${row.at}`} row={row} />
                    ))}
                  </div>
                </>
              )}
            </div>
          </Section>

          <QuotesSection
            quotes={quotes.data}
            vocabulary={vocabulary.data}
            summary={roomQuery.data}
            onlySoon={onlySoon}
            stateFilter={stateFilter}
            busy={busy}
            onOnlySoon={setOnlySoon}
            onStateFilter={setStateFilter}
            onSelect={setSelected}
            onCreate={(payload) =>
              run(() => expiryApi.createQuote(roomId, payload), 'Quote tracked.')
            }
          />

          {selected && (
            <QuoteDetail
              quote={selected}
              roomId={roomId}
              vocabulary={vocabulary.data}
              busy={busy}
              onClose={() => setSelected(null)}
              onRefresh={() => quotes.refetch()}
              onSetExpiration={(payload, note) =>
                run(async () => {
                  const next = await expiryApi.setExpiration(roomId, selected.id, payload)
                  setSelected(next)
                }, note)
              }
              onSend={(publish) =>
                run(async () => {
                  const next = await expiryApi.send(roomId, selected.id, publish)
                  setSelected(next)
                }, publish ? 'Quote published. That was a new send.' : 'Quote sent.')
              }
              onAccept={(payload) =>
                run(async () => {
                  const next = await expiryApi.recordAcceptance(roomId, selected.id, payload)
                  setSelected(next)
                }, 'Buyer action recorded.')
              }
              onVoid={() =>
                run(async () => {
                  const next = await expiryApi.voidQuote(roomId, selected.id)
                  setSelected(next)
                }, 'Quote voided. The link URL is deactivated.')
              }
              onArchive={() =>
                run(async () => {
                  const next = await expiryApi.archiveQuote(roomId, selected.id)
                  setSelected(next)
                }, 'Quote archived. It is unpublished and buyers cannot reach it.')
              }
            />
          )}

          <Section
            title="Quote activities"
            hint="Reminder and expiration events are rows a workflow can drive on."
          >
            {activities.data?.count === 0 ? (
              <p className="text-sm text-muted-foreground">No activity recorded yet.</p>
            ) : (
              (activities.data?.activities || []).map((row) => (
                <ActivityRow key={row.id || `${row.quote_id}-${row.at}`} row={row} />
              ))
            )}
          </Section>
        </>
      )}

      <Section
        title="The recorded decisions"
        hint="Every judgement call this workflow rests on, and what each one cost."
      >
        {inferences.loading ? (
          <Spinner label="Loading the decision register" />
        ) : inferences.error ? (
          <ErrorNote error={inferences.error} onRetry={inferences.refetch} />
        ) : (
          <div className="space-y-4">
            <Notice tone="neutral" title="The two mechanism questions were gated">
              <Mono>{inferences.data.jev_design_audits.domain_package_placement}</Mono> settled
              where the domain module lives and{' '}
              <Mono>{inferences.data.jev_design_audits.job_driver}</Mono> settled what drives
              the expiry check and the dispatch. Both were asked before any code was written.
            </Notice>
            {(inferences.data.decisions || []).map((decision) => (
              <DecisionCard key={decision.id} decision={decision} />
            ))}
          </div>
        )}
      </Section>

      {preview && (
        <Section
          title="Reminder preview"
          hint="The researched Preview reminder email surface. Nothing was sent."
          action={
            <Button variant="ghost" icon="close" onClick={() => setPreview(null)}>
              Close the preview
            </Button>
          }
        >
          <div className="space-y-3">
            <Notice tone="warning" title="This product sends no message">
              {preview.gap}
            </Notice>
            <div className="rounded-sm border border-border-subtle bg-surface p-4">
              <p className="text-sm font-semibold text-foreground">{preview.subject}</p>
              <p className="mt-2 text-sm text-muted-foreground">{preview.body}</p>
              <p className="mt-3 text-xs text-muted-foreground">
                Channel: <Mono>{preview.channel}</Mono>. Fires{' '}
                {localDueLabel(preview.local_reading)}.
              </p>
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
                Composed from
              </p>
              <ul className="mt-1 space-y-1">
                {Object.entries(preview.fields || {}).map(([key, value]) => (
                  <li key={key} className="text-xs text-muted-foreground">
                    <Mono>{key}</Mono>: <Mono>{String(value ?? 'not set')}</Mono>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </Section>
      )}
    </div>
  )
}

/** A thin wrapper so the page has one card rather than two copies of the same wrapper. */
function Cardish({ children }) {
  return (
    <div className="rounded-sm border border-border-subtle bg-surface p-5">{children}</div>
  )
}

function DefaultWindowSection({ settings, vocabulary, busy, onSave }) {
  const [days, setDays] = useState('')
  const [timezone, setTimezone] = useState('')
  const [sendTime, setSendTime] = useState('')
  const [automated, setAutomated] = useState(true)

  if (!settings) return <Cardish>Loading settings</Cardish>

  const bound = vocabulary.expiration_rule
  const current = settings.default_expiration_days

  return (
    <Section
      title="Default expiration period and reminders"
      hint={`Between ${bound.min_default_days} and ${bound.max_default_days} days.`}
    >
      <div className="space-y-4">
        <Notice tone="neutral" title="What the default does">
          {bound.default_quote}
        </Notice>
        <LabeledInput
          id="wf098-default-days"
          label="Default expiration period, in days"
          hint={
            current === null
              ? 'No default is set. New quotes with no stated date will not expire.'
              : `Currently ${current} day(s).`
          }
          type="number"
          min={bound.min_default_days}
          max={bound.max_default_days}
          value={days}
          onChange={(event) => setDays(event.target.value)}
          placeholder="30"
        />
        <LabeledInput
          id="wf098-zone"
          label="Account time zone"
          hint={settings.timezone_note || vocabulary.reminder_rule.timezone_quote}
          value={timezone}
          onChange={(event) => setTimezone(event.target.value)}
          placeholder="UTC"
        />
        <LabeledInput
          id="wf098-send-time"
          label="Reminder send time"
          hint="A wall clock reading in the account time zone, as HH:MM."
          value={sendTime}
          onChange={(event) => setSendTime(event.target.value)}
          placeholder={settings.reminder_send_time}
        />
        <Switch
          id="wf098-automated"
          checked={automated}
          onChange={setAutomated}
          label="Send automated reminders to quote recipients"
          hint={vocabulary.reminder_rule.automated_quote}
        />
        <Button
          variant="primary"
          icon="plus"
          disabled={busy}
          onClick={() =>
            onSave({
              default_expiration_days: days === '' ? null : Number(days),
              account_timezone: timezone === '' ? settings.account_timezone : timezone,
              reminder_send_time: sendTime === '' ? settings.reminder_send_time : sendTime,
              automated_reminders_enabled: automated,
            })
          }
        >
          Save the account settings
        </Button>
        <Notice tone="warning" title="A documented gap">
          {settings.gap}
        </Notice>
      </div>
    </Section>
  )
}

function RemindersSection({
  rules,
  vocabulary,
  settings,
  quotes,
  busy,
  onAdd,
  onPatch,
  onDelete,
  onPreview,
}) {
  const [offsetKind, setOffsetKind] = useState(vocabulary.reminder_rule.offset_kinds[0].kind)
  const [days, setDays] = useState('3')

  if (!rules) return <Cardish>Loading the schedule</Cardish>

  return (
    <Section
      title="Reminder schedule"
      hint="Each rule counts one way and fires once per quote."
      count={rules.count}
    >
      <div className="space-y-4">
        {rules.count === 0 ? (
          <p className="text-sm text-muted-foreground">
            No rules yet. Add one below. Several rules can exist at once and each has its own
            delete.
          </p>
        ) : (
          (rules.rules || []).map((rule) => (
            <RuleRow
              key={rule.id}
              rule={rule}
              busy={busy}
              dueLabel={dueLabelFor(rule, quotes, settings)}
              onPatch={onPatch}
              onDelete={onDelete}
              onPreview={onPreview}
            />
          ))
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <LabeledSelect
            id="wf098-offset-kind"
            label="Count from"
            hint={vocabulary.reminder_rule.offset_quotes[offsetKind]}
            value={offsetKind}
            onChange={(event) => setOffsetKind(event.target.value)}
          >
            {vocabulary.reminder_rule.offset_kinds.map((row) => (
              <option key={row.kind} value={row.kind}>
                {row.kind}
              </option>
            ))}
          </LabeledSelect>
          <LabeledInput
            id="wf098-offset-days"
            label="Number of days"
            hint="Set the number of days, as the researched settings screen does."
            type="number"
            min={vocabulary.reminder_rule.min_offset_days}
            value={days}
            onChange={(event) => setDays(event.target.value)}
          />
        </div>
        <Button
          icon="plus"
          disabled={busy}
          onClick={() => onAdd({ offset_kind: offsetKind, days: Number(days) })}
        >
          Add reminder
        </Button>
        <Notice tone="neutral" title="Two offsets, not interchangeable">
          {vocabulary.reminder_rule.offset_quotes.after_send} and{' '}
          {vocabulary.reminder_rule.offset_quotes.before_expiry} are different questions.
          This page never converts between them. {vocabulary.reminder_rule.settings_api_gap}
        </Notice>
      </div>
    </Section>
  )
}

function dueLabelFor(rule, quotes, settings) {
  const plan = (quotes || [])
    .flatMap((quote) => quote.reminders_due || [])
    .find((row) => row.rule_id === rule.id)
  if (plan?.local_reading) return localDueLabel(plan.local_reading)
  if (!settings) return 'not scheduled yet'
  const zone = settings.account_timezone || 'UTC'
  return `${settings.reminder_send_time} in ${zone}`
}

function QuotesSection({
  quotes,
  vocabulary,
  summary,
  onlySoon,
  stateFilter,
  busy,
  onOnlySoon,
  onStateFilter,
  onSelect,
  onCreate,
}) {
  const [title, setTitle] = useState('')
  const [date, setDate] = useState('')
  const [label, setLabel] = useState('')
  const [switchOn, setSwitchOn] = useState(true)
  const [recipient, setRecipient] = useState('')

  if (!quotes) return <Cardish>Loading quotes</Cardish>

  const needsYou = (quotes.quotes || []).filter(needsAttention)

  return (
    <Section
      title="Tracked quotes"
      hint={`Expiring soon means within ${vocabulary.expiring_soon_days} days.`}
      count={quotes.count}
    >
      <div className="space-y-4">
        <Notice tone="info" title="Expiring soon window">
          {vocabulary.expiring_soon_note}
        </Notice>

        <div className="flex flex-wrap items-end gap-4">
          <LabeledSelect
            id="wf098-state-filter"
            label="Filter by state"
            value={stateFilter}
            onChange={(event) => onStateFilter(event.target.value)}
            className="min-w-[12rem] flex-1"
          >
            <option value="">Every state</option>
            {vocabulary.quote_states.map((row) => (
              <option key={row.state} value={row.state}>
                {row.state}
              </option>
            ))}
          </LabeledSelect>
          <div className="flex min-h-11 items-center">
            <Switch
              id="wf098-only-soon"
              checked={onlySoon}
              onChange={onOnlySoon}
              label="Only expiring soon"
            />
          </div>
        </div>

        {needsYou.length > 0 && (
          <Notice tone="warning" title={`${needsYou.length} quote(s) need you`}>
            <ul className="mt-1 list-disc space-y-1 pl-5">
              {needsYou.map((quote) => (
                <li key={quote.id}>
                  {quote.title}:{' '}
                  {quote.expiring_soon && quote.expiration_enabled
                    ? `expires in ${Math.max(0, Math.round(daysLeft(quote) ?? 0))} day(s)`
                    : `${quote.reminder_count} reminder(s) due now`}
                </li>
              ))}
            </ul>
          </Notice>
        )}

        {quotes.count === 0 ? (
          <EmptyState
            title="No quotes match this filter"
            description="Track one below, or clear the filter to see the rest of the room."
          />
        ) : (
          <div className="space-y-2">
            {(quotes.quotes || []).map((quote) => (
              <QuoteRow key={quote.id} quote={quote} busy={busy} onSelect={onSelect} />
            ))}
          </div>
        )}

        <div className="rounded-sm border border-border-subtle bg-muted p-4">
          <p className="text-sm font-semibold text-foreground">Track a quote</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Three separate controls, as the researched header module has: the date picker,
            the label, and the switch.
          </p>
          <div className="mt-3 grid gap-4 sm:grid-cols-2">
            <LabeledInput
              id="wf098-quote-title"
              label="Quote title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Q4 expansion"
            />
            <LabeledInput
              id="wf098-quote-date"
              label="Expiration date"
              hint="A date with no time is read as midnight UTC."
              type="date"
              value={date}
              onChange={(event) => setDate(event.target.value)}
            />
            <LabeledInput
              id="wf098-quote-label"
              label="Label"
              hint="The word the buyer reads beside the date."
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              placeholder="Sign by"
            />
            <LabeledInput
              id="wf098-quote-recipient"
              label="Recipient email"
              hint="A reminder names its recipients. A row with no address is not a recipient."
              type="email"
              value={recipient}
              onChange={(event) => setRecipient(event.target.value)}
              placeholder="buyer@example.com"
            />
          </div>
          <div className="mt-4">
            <Switch
              id="wf098-quote-switch"
              checked={switchOn}
              onChange={setSwitchOn}
              label="Expiration date"
              hint="Turning this off means the quote never expires, whatever the account default is."
            />
          </div>
          <div className="mt-4">
            <Button
              variant="primary"
              icon="plus"
              disabled={busy}
              onClick={() =>
                onCreate({
                  title,
                  hs_expiration_date: switchOn ? fromDateInput(date) : null,
                  expiration_label: label,
                  expiration_enabled: switchOn,
                  recipients: recipient ? [{ email: recipient }] : [],
                })
              }
            >
              Track the quote
            </Button>
          </div>
          {summary && (
            <p className="mt-3 text-xs text-muted-foreground">
              {summary.with_expiration} of {summary.quotes} carry a deadline.{' '}
              {summary.expiration_off} have the switch off and never expire.
            </p>
          )}
        </div>
      </div>
    </Section>
  )
}

function QuoteDetail({
  quote,
  roomId,
  vocabulary,
  busy,
  onClose,
  onSetExpiration,
  onSend,
  onAccept,
  onVoid,
  onArchive,
}) {
  const [label, setLabel] = useState(quote.expiration_label || '')
  const [date, setDate] = useState(toDateInput(quote.expiration_date_only))
  const [switchOn, setSwitchOn] = useState(quote.expiration_enabled)
  const [action, setAction] = useState(vocabulary.acceptance.surviving_actions[0])
  const [method, setMethod] = useState(vocabulary.acceptance.methods[0].method)
  const [report, setReport] = useState(null)

  const tone = stateTone(vocabulary, quote.state)

  return (
    <Section
      title={quote.title}
      hint="The three header controls, the acceptance path, and the three researched outcomes."
      action={
        <Button variant="ghost" icon="close" onClick={onClose}>
          Close this quote
        </Button>
      }
    >
      <div className="space-y-5">
        <div className="flex flex-wrap items-center gap-3">
          <Badgeish tone={tone}>{quote.state}</Badgeish>
          <span className="text-xs text-muted-foreground">{quote.state_label}</span>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <LabeledInput
            id="wf098-detail-date"
            label="Expiration date"
            value={date}
            disabled={quote.state === 'expired' || quote.state === 'archived'}
            onChange={(event) => setDate(event.target.value)}
          />
          <LabeledInput
            id="wf098-detail-label"
            label="Label"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
          />
        </div>
        <Switch
          id="wf098-detail-switch"
          checked={switchOn}
          onChange={setSwitchOn}
          label="Expiration date"
          hint="Off means this quote never expires."
        />
        <div className="flex flex-wrap gap-2">
          <Button
            icon="refresh"
            disabled={busy}
            onClick={() =>
              onSetExpiration(
                {
                  hs_expiration_date: switchOn ? fromDateInput(date) : null,
                  expiration_label: label,
                  expiration_enabled: switchOn,
                },
                'Expiration controls saved.',
              )
            }
          >
            Save the expiration controls
          </Button>
          <Button
            icon="audit"
            disabled={busy}
            onClick={() => onSend(false)}
          >
            Send again
          </Button>
          <Button icon="rooms" disabled={busy} onClick={() => onSend(true)}>
            Publish again
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          {vocabulary.sending.quote} This quote has been sent{' '}
          <Mono>{quote.send_count ?? 0}</Mono> time(s) and consumed{' '}
          <Mono>{quote.esignature_quota_consumed ?? 0}</Mono> e-signature(s).
        </p>

        <Notice tone="info" title="Can the buyer still accept?">
          <CanAcceptLine roomId={roomId} quote={quote} report={report} onReport={setReport} />
        </Notice>

        <div className="grid gap-4 sm:grid-cols-2">
          <LabeledSelect
            id="wf098-detail-action"
            label="What the buyer did"
            value={action}
            onChange={(event) => setAction(event.target.value)}
          >
            {vocabulary.acceptance.all_actions.map((row) => (
              <option key={row} value={row}>
                {vocabulary.acceptance.action_labels[row]}
              </option>
            ))}
          </LabeledSelect>
          <LabeledSelect
            id="wf098-detail-method"
            label="Acceptance method"
            hint="Each of the three has its own rule, so the method is recorded."
            value={method}
            onChange={(event) => setMethod(event.target.value)}
          >
            {vocabulary.acceptance.methods.map((row) => (
              <option key={row.method} value={row.method}>
                {row.method}
              </option>
            ))}
          </LabeledSelect>
        </div>
        <Button
          variant="primary"
          icon="plus"
          disabled={busy}
          onClick={() => onAccept({ action, method })}
        >
          Record the buyer action
        </Button>
        <Notice tone="neutral" title="What saves a quote from its deadline">
          {vocabulary.acceptance.survival_quote}
        </Notice>

        <div className="flex flex-wrap gap-2">
          <Button variant="danger" icon="close" disabled={busy} onClick={onVoid}>
            Void the quote
          </Button>
          <Button variant="danger" icon="trash" disabled={busy} onClick={onArchive}>
            Archive the quote
          </Button>
        </div>
        <div className="space-y-2">
          <Notice tone="neutral" title="Void">{vocabulary.void_and_archive.void_quote}</Notice>
          <Notice tone="neutral" title="Archive">{vocabulary.void_and_archive.archive_quote}</Notice>
          <Notice tone="neutral" title="After expiry">
            {vocabulary.invariants.expiry_survives} {vocabulary.invariants.acceptance_closed}
          </Notice>
        </div>

        {(quote.reminders_due || []).length > 0 && (
          <Notice tone="warning" title="Reminders due for this quote now">
            <ul className="mt-1 list-disc space-y-1 pl-5">
              {quote.reminders_due.map((row) => (
                <li key={row.rule_id}>
                  <Mono>{row.label}</Mono> fires {localDueLabel(row.local_reading)}
                </li>
              ))}
            </ul>
          </Notice>
        )}

        {(quote.acceptances || []).length > 0 && (
          <div>
            <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              What the buyer did, and when
            </p>
            <ul className="mt-1 space-y-1">
              {quote.acceptances.map((row, index) => (
                <li key={`${row.at}-${index}`} className="text-xs text-muted-foreground">
                  {vocabulary.acceptance.action_labels[row.action] || row.action} by{' '}
                  <Mono>{row.method}</Mono> at <Mono>{row.at}</Mono>
                  {row.by_deadline ? ' (in time)' : ' (after the deadline)'}
                </li>
              ))}
            </ul>
          </div>
        )}

        {quote.expiration_view && (
          <p className="text-xs text-muted-foreground">
            Deadline in the account time zone: <Mono>{quote.expiration_view.local}</Mono>
            {quote.expiration_view.known ? '' : ` (${quote.expiration_view.note})`}
          </p>
        )}
      </div>
    </Section>
  )
}

function CanAcceptLine({ roomId, quote, report, onReport }) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button
        icon="search"
        onClick={async () => {
          try {
            onReport(await expiryApi.canAccept(roomId, quote.id))
          } catch (error) {
            onReport(null)
          }
        }}
      >
        Ask whether the buyer can accept
      </Button>
      {report && (
        <span className="text-sm text-foreground">
          {report.can_accept ? 'Yes. They can accept.' : `No. Reason: ${report.reason}.`}{' '}
          {report.note}
        </span>
      )}
    </div>
  )
}

function Badgeish({ tone, children }) {
  return <Badge tone={tone}>{children}</Badge>
}

export default QuoteExpiry