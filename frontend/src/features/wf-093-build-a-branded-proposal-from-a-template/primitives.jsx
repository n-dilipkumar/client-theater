/**
 * Primitives drawn from the shared components in `@/components/ui`.
 *
 * Nothing here redefines a control that `components/ui.jsx` already provides. The rule
 * from the feature contract is twelve features each shipping their own `Modal` means
 * twelve subtly different dialogs in one product, so `Button`, `Card`, `Badge`, `Field`,
 * `inputClass`, `Icon` and the rest all come from the shared module and only the
 * arrangements this page needs are built here.
 *
 * What this file adds is presentation the shared set does not carry: a brand token row
 * that names the level that set it, a module block that shows its resolved bindings, and
 * a totals table that distinguishes an absent row from a zero one.
 */

import { Badge, Card, Icon, inputClass } from '@/components/ui'

import {
  bindingState,
  brandingLevel,
  documentState,
  formatInstant,
  formatMoney,
  logoSource,
} from './api'

/**
 * A dialog, built here rather than imported.
 *
 * `components/ui.jsx` does not export a `Modal` on this branch. The feature contract says
 * to build a primitive that genuinely does not exist inside my own feature folder and say
 * so in the pull request, rather than editing the shared file. It is the one control here
 * that is not shared, and it is why: the shared module cannot be edited by a feature.
 *
 * It meets the same accessibility floor as the rest of the product. `role="dialog"` with
 * `aria-modal`, a visible title, Escape closes, and the backdrop is a button so it is
 * reachable by keyboard rather than by pointer only.
 */
export function Dialog({ open, title, onClose, children }) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-foreground/40 p-4 sm:p-6">
      <button
        type="button"
        aria-label="Close the dialog"
        onClick={onClose}
        className="fixed inset-0 h-full w-full cursor-default"
        tabIndex={-1}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onKeyDown={(event) => {
          if (event.key === 'Escape') onClose()
        }}
        className="relative z-10 w-full max-w-lg rounded-sm border border-border-subtle bg-surface p-5"
      >
        <div className="flex items-start justify-between gap-3">
          <h2 className="text-lg font-semibold text-foreground">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex min-h-11 min-w-11 items-center justify-center rounded-sm border border-border-subtle bg-surface text-foreground hover:border-accent hover:text-accent"
          >
            <Icon name="close" />
          </button>
        </div>
        <div className="mt-4">{children}</div>
      </div>
    </div>
  )
}

/**
 * The heading and the label beside it, in the micro-label style the design system
 * prescribes. Status is never carried by colour alone: every state on this page has a
 * word, and every badge has text in it.
 */
export function SectionHeading({ title, hint, count }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <div>
        <h2 className="text-lg font-semibold text-foreground">{title}</h2>
        {hint ? <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p> : null}
      </div>
      {count === undefined ? null : (
        <span className="font-mono text-xs text-muted-foreground">{count}</span>
      )}
    </div>
  )
}

/**
 * A machine value in the mono face.
 *
 * Mono is for IDs, timestamps, counts and JSON, never for prose, which is what the type
 * table in the design system says. Every value this renders is a machine value.
 */
export function Mono({ children, className = '' }) {
  return <span className={`font-mono text-xs ${className}`}>{children}</span>
}

/**
 * One resolved binding: the field, the record path, and what came back.
 *
 * A field that resolved to nothing is shown as a named row with the word "Unresolved"
 * rather than as an empty cell. An empty cell is indistinguishable from a layout bug,
 * and the whole reason this workflow reports an unresolved binding is so nobody has to
 * guess which one they are looking at.
 */
export function BindingRow({ name, binding }) {
  if (!binding) return null
  const state = bindingState(binding.status)
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border-subtle py-2 first:border-t-0">
      <div className="min-w-0">
        <p className="text-sm text-foreground">{name}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {binding.path ? (
            <>
              Bound to <Mono>{binding.path}</Mono>
            </>
          ) : (
            'No path bound. The template supplies the text.'
          )}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <Badge tone={state.tone}>{state.label}</Badge>
        <Mono className="text-foreground">
          {binding.value === null || binding.value === undefined || binding.value === ''
            ? 'empty'
            : String(binding.value)}
        </Mono>
      </div>
    </div>
  )
}

/**
 * One branding token and the level that set it.
 *
 * The specification lets a brand drive the colours, lets a template override them, and
 * lets a quote override the template. Those are three different things, and a page that
 * showed only the final colour could not tell them apart, so each token names its level.
 */
export function BrandTokenRow({ token }) {
  if (!token) return null
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border-subtle py-2 first:border-t-0">
      <div className="min-w-0">
        <p className="text-sm text-foreground">{token.label || token.token}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">{brandingLevel(token.level)}</p>
      </div>
      <Mono className="text-foreground">{String(token.value ?? 'unset')}</Mono>
    </div>
  )
}

/**
 * The header of a rendered proposal.
 *
 * The expiration date is labelled as derived when the specification's date was not
 * supplied. A derived date and a stated one look identical in a date field, and a buyer
 * checking the header first deserves to know which one they are reading.
 */
export function ProposalHeader({ header, brandName }) {
  if (!header) return null
  return (
    <Card className="p-0">
      <div className="border-b border-border-subtle p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              Quote reference
            </p>
            <Mono className="mt-1 block break-all text-foreground">{header.quote_reference}</Mono>
          </div>
          <div className="text-right">
            <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              {brandName ? 'Brand' : 'Seller'}
            </p>
            <p className="mt-1 text-sm font-medium text-foreground">{brandName || 'No brand set'}</p>
          </div>
        </div>
      </div>
      <dl className="grid gap-4 p-4 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="Issued" value={formatInstant(header.issue_date)} />
        <Field
          label="Expires"
          value={formatInstant(header.expiration_date)}
          note={header.expiration_derived ? 'Derived, not stated' : 'Stated on the quote'}
        />
        <Field label="Currency" value={header.currency_label || 'unset'} />
        <Field label="PO number" value={header.po_number || 'none'} />
      </dl>
      <div className="flex flex-wrap items-center gap-2 border-t border-border-subtle px-4 py-3">
        <Icon name="audit" />
        <span className="text-xs text-muted-foreground">Logo</span>
        <Badge tone={header.logo_fallback_applied ? 'warning' : 'info'}>
          {logoSource(header.logo_source).label}
        </Badge>
        {header.logo_url ? (
          <Mono className="break-all text-foreground">{String(header.logo_url)}</Mono>
        ) : (
          <span className="text-xs text-muted-foreground">No logo resolved.</span>
        )}
      </div>
    </Card>
  )
}

/** One label and value pair, with the label always visible. */
function Field({ label, value, note }) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">{label}</dt>
      <dd className="mt-1 font-mono text-sm text-foreground">{String(value)}</dd>
      {note ? <p className="mt-0.5 text-xs text-muted-foreground">{note}</p> : null}
    </div>
  )
}

/**
 * The three parties, each with what its binding resolved to.
 *
 * An unresolved party renders the word "Unresolved" rather than nothing. A proposal with a
 * blank bill-to party is a normal outcome when the quote carries no billing address, and
 * the page must be able to say that rather than leave a gap.
 */
export function PartyList({ parties }) {
  if (!parties?.length) return null
  return (
    <div className="space-y-3">
      {parties.map((party) => {
        const state = bindingState(party.status)
        return (
          <div key={party.role} className="flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-0">
              <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
                {party.label}
              </p>
              <p className="mt-0.5 text-sm text-foreground">
                {party.value ? String(party.value) : 'No value on the quote'}
              </p>
            </div>
            <Badge tone={state.tone}>{state.label}</Badge>
          </div>
        )
      })}
    </div>
  )
}

/**
 * The priced rows, in the order the quote holds them.
 *
 * The line number is in the mono face because it is a machine position, and the amount
 * too. The names are prose and are not.
 */
export function LineItemTable({ items, currencyLabel }) {
  if (!items?.length) {
    return (
      <p className="text-sm text-muted-foreground">
        This quote has no priced line items. The module renders empty and the totals below
        sum to zero, which is what the quote says rather than a failure.
      </p>
    )
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-border-subtle">
            <th scope="col" className="py-2 text-left text-xs font-medium text-muted-foreground">
              No.
            </th>
            <th scope="col" className="py-2 text-left text-xs font-medium text-muted-foreground">
              Item
            </th>
            <th scope="col" className="py-2 text-right text-xs font-medium text-muted-foreground">
              Amount
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, index) => (
            <tr key={`${item.position}-${index}`} className="border-b border-border-subtle last:border-b-0">
              <td className="py-2 pr-3 font-mono text-xs text-muted-foreground">
                {String(item.position ?? index + 1).padStart(2, '0')}
              </td>
              <td className="py-2 pr-3 text-foreground">
                {item.name || 'Unnamed line'}
                {item.amount_derived ? (
                  <span className="ml-2 text-xs text-muted-foreground">
                    computed from quantity and unit price
                  </span>
                ) : null}
              </td>
              <td className="py-2 text-right font-mono text-xs text-foreground">
                {formatMoney(item.amount, item.currency_label || currencyLabel)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * The totals tab.
 *
 * A row the quote never stated shows as zero with the word "not stated" beside it. The
 * specification names a Totals tab and does not say how a discount is computed, so this
 * workflow reads the figure or reports the absence. Printing an unstated row as a plain
 * zero would make an absent discount indistinguishable from a real one.
 */
export function TotalsTable({ totals }) {
  const rows = totals?.ordered || []
  if (!rows.length) return null
  return (
    <dl className="space-y-2">
      {rows.map((row) => (
        <div
          key={row.label}
          className="flex items-center justify-between gap-3 border-b border-border-subtle pb-2 last:border-b-0 last:pb-0"
        >
          <dt className="flex items-center gap-2 text-sm text-foreground">
            {row.label}
            {row.stated ? null : (
              <span className="text-xs text-muted-foreground">not stated on the quote</span>
            )}
          </dt>
          <dd className="font-mono text-sm text-foreground">
            {formatMoney(row.value, totals.currency_label)}
          </dd>
        </div>
      ))}
    </dl>
  )
}

/**
 * The whole document, module by module, in the order the template stored.
 *
 * Each module names its own kind and, where it has fields, renders them. A module whose
 * bindings did not resolve still renders, because one missing field must not cost a buyer
 * the whole proposal.
 */
export function ModuleList({ modules }) {
  if (!modules?.length) {
    return (
      <p className="text-sm text-muted-foreground">
        This template renders no modules. Every module it stores is hidden, so the document
        is empty by the template&apos;s own choice.
      </p>
    )
  }
  return (
    <div className="space-y-4">
      {modules.map((module) => (
        <Card key={module.module} className="p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-base font-semibold text-foreground">{module.label}</h3>
            <Mono className="text-muted-foreground">{module.module}</Mono>
          </div>

          {module.fields && Object.keys(module.fields).length > 0 ? (
            <div className="mt-3">
              {Object.entries(module.fields).map(([name, binding]) => (
                <BindingRow key={name} name={name} binding={binding} />
              ))}
            </div>
          ) : null}

          {module.parties ? (
            <div className="mt-3">
              <PartyList parties={module.parties} />
            </div>
          ) : null}

          {module.line_items ? (
            <div className="mt-3">
              <LineItemTable items={module.line_items} />
            </div>
          ) : null}

          {module.totals ? (
            <div className="mt-3">
              <TotalsTable totals={module.totals} />
            </div>
          ) : null}

          {module.body ? (
            <p className="mt-3 whitespace-pre-line text-sm text-foreground">{module.body}</p>
          ) : null}

          {module.module === 'executive_summary' ? (
            <p className="mt-3 text-xs text-muted-foreground">
              This workflow generates no text. This summary came from the{' '}
              {module.source === 'caller_supplied' ? 'caller' : 'template'}, and the
              specification names the five inputs a generated summary may be derived from:{' '}
              {(module.inputs || []).join(', ')}.
            </p>
          ) : null}

          {module.module === 'acceptance' ? (
            <p className="mt-3 text-xs text-muted-foreground">
              The acceptance module renders. This workflow does not collect a signature and
              does not claim to.
            </p>
          ) : null}
        </Card>
      ))}
    </div>
  )
}

/**
 * What the merge could not do, in words, with the evidence beside it.
 *
 * This block is the reason the page exists. A proposal that rendered with two unresolved
 * fields and dropped three line items past the researched cap must say so before a seller
 * sends it to a buyer.
 */
export function ReportPanel({ unresolved, truncated, placeholderBounds, precedence }) {
  const missing = unresolved || []
  const drop = truncated?.dropped || 0
  const hasSomething = missing.length > 0 || drop > 0
  return (
    <Card className="p-4">
      <h3 className="text-base font-semibold text-foreground">What the merge reported</h3>

      <div className="mt-3 space-y-3">
        <div>
          <p className="text-sm text-foreground">
            {hasSomething
              ? `${missing.length + drop} thing${missing.length + drop === 1 ? '' : 's'} this document does not contain in full.`
              : 'This document resolved every binding and dropped no line item.'}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            A quote property wins over the template. {precedence === 'quote_over_template'
              ? 'That rule was applied.'
              : 'That rule is recorded on the document.'}
          </p>
        </div>

        {missing.length > 0 ? (
          <div>
            <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              Bindings that resolved to nothing
            </p>
            <ul className="mt-1 space-y-1">
              {missing.map((entry) => (
                <li key={`${entry.module}-${entry.field}`} className="text-xs text-foreground">
                  <Mono>{entry.module}</Mono> / {entry.field} bound to{' '}
                  <Mono>{entry.path}</Mono>, which this quote does not carry. The field
                  rendered empty.
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {drop > 0 ? (
          <div>
            <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              Line items past the cap
            </p>
            <p className="mt-1 text-xs text-foreground">
              {drop} line item(s) were dropped. The cap is {truncated?.cap}. The evidence:{' '}
              &quot;{truncated?.evidence}&quot;. The totals above sum the rows printed, not
              every row the quote holds, so the total always matches what is shown.
            </p>
          </div>
        ) : null}

        {placeholderBounds ? (
          <div>
            <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              Content-placeholder bound
            </p>
            <p className="mt-1 text-xs text-foreground">
              {placeholderBounds.min} to {placeholderBounds.max} items per placeholder.{' '}
              {placeholderBounds.note} The evidence: &quot;{placeholderBounds.evidence}&quot;
            </p>
          </div>
        ) : null}
      </div>
    </Card>
  )
}

/**
 * The lifecycle of one document, with the rule computed rather than described.
 *
 * The buttons a document offers depend on its state, because offering "Re-render" on a
 * published document would invite the exact action the specification forbids. The
 * refusal is still handled on the page, since a caller can reach it through the API.
 */
export function LifecyclePanel({ document, onTransition, busy }) {
  if (!document) return null
  const state = documentState(document.state)
  const frozen = document.retroactivity?.rerenderable === false
  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-foreground">Lifecycle</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">{state.meaning}</p>
        </div>
        <Badge tone={state.tone}>{state.label}</Badge>
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        {document.state === 'instantiated' ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => onTransition('draft')}
            className="min-h-11 rounded-sm border border-border-subtle bg-surface px-4 text-sm text-foreground hover:border-accent hover:text-accent disabled:opacity-50"
          >
            Return to draft
          </button>
        ) : null}
        {document.state !== 'published' ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => onTransition('publish')}
            className="min-h-11 rounded-sm border border-border-subtle bg-surface px-4 text-sm text-foreground hover:border-accent hover:text-accent disabled:opacity-50"
          >
            Publish
          </button>
        ) : null}
        <button
          type="button"
          disabled={busy || frozen}
          onClick={() => onTransition('rerender')}
          className="min-h-11 rounded-sm border border-border-subtle bg-surface px-4 text-sm text-foreground hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-50"
        >
          Re-render from the template
        </button>
      </div>

      {frozen ? (
        <p className="mt-3 rounded-sm border border-border-subtle bg-muted p-3 text-xs text-foreground">
          {document.retroactivity.reason} {document.no_retroactive_application_quote}
        </p>
      ) : null}
    </Card>
  )
}

/** A labelled input using the shared `inputClass`. Placeholder-only labelling is banned. */
export function LabelledInput({ id, label, hint, value, onChange, placeholder }) {
  return (
    <div>
      <label htmlFor={id} className="block text-xs font-medium text-foreground">
        {label}
      </label>
      <input
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className={`${inputClass} mt-1`}
      />
      {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  )
}