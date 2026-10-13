/**
 * WF-087: the product and price-book catalogue.
 *
 * One page, four panels, in the order the research performs the workflow:
 *
 *   1. The library. A searchable list of products, each showing its pricing
 *      method, how many ranges it has, and the currencies it prices in.
 *   2. The tier editor. A form that types a minimum per range and shows the
 *      derived maximum beside it, because the maximum is derived and a seller
 *      who types one has typed a second number that can disagree with the first.
 *   3. The price tester. A quantity in, a unit price, a total, the range reached
 *      and the layer that supplied the figure out. This is the researched moment
 *      and the only way a seller can check the arithmetic before quoting from it.
 *   4. The price books. Each book's currencies, its rows, and the currencies each
 *      row is missing, with an activation control that refuses and explains while
 *      a gap stands.
 *
 * Everything the page labels comes from `GET /vocabulary` rather than from a
 * literal here, so a change to the rules cannot leave the page calling a state
 * the API does not serve.
 */

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
  BADGE_TONES,
  METHOD_RULES,
  METHOD_WORDS,
  PRODUCT_STATUS_WORDS,
  SOURCE_WORDS,
  catalogueApi,
  formatMoney,
  rangeLabel,
  refusalText,
} from './api'
import { Checkbox, MetaRow, Modal, Notice, NumberField, Toggle } from './primitives'

/**
 * A product box with a currency mark on it.
 *
 * Not in the shared `PATHS` map, so it travels as an `iconPath` and
 * `components/ui.jsx` is not edited. Every use of it sits beside a text label.
 */
const CATALOGUE_ICON = 'M4 7l8-4 8 4v10l-8 4-8-4V7zm0 0l8 4m0 0l8-4m-8 4v10M8 9l8 4'

/** An empty tier row, so a new table starts with one editable range. */
function emptyTier() {
  return { min_qty: '', unit_price: '' }
}

/**
 * The product form, in a dialog.
 *
 * The pricing method is chosen first because it decides which of the two shapes
 * below it is live: a flat price per currency, or a tier table. Both are shown
 * and the one that does not apply is disabled, rather than hidden, so a seller
 * can see that the other kind exists and why this product is not using it.
 */
function ProductDialog({ open, onClose, onSave, product, vocabulary, busy, refusal }) {
  const [form, setForm] = useState(() => draftFrom(product))
  const [fieldErrors, setFieldErrors] = useState({})

  const methods = vocabulary?.pricing_methods ?? []
  const currencies = vocabulary?.common_currencies ?? []

  const set = (key, value) => setForm((prev) => ({ ...prev, [key]: value }))

  const setTier = (index, key, value) =>
    setForm((prev) => ({
      ...prev,
      tiers: prev.tiers.map((tier, position) =>
        position === index ? { ...tier, [key]: value } : tier,
      ),
    }))

  const addTier = () => setForm((prev) => ({ ...prev, tiers: [...prev.tiers, emptyTier()] }))
  const removeTier = (index) =>
    setForm((prev) => ({ ...prev, tiers: prev.tiers.filter((_, position) => position !== index) }))

  const save = async () => {
    try {
      await onSave(buildPayload(form))
      setFieldErrors({})
    } catch (error) {
      setFieldErrors(error.errors ?? {})
    }
  }

  return (
    <Modal
      open={open}
      title={product ? 'Edit product' : 'Add a product'}
      description={
        product
          ? 'The record is revalidated when it is saved, so a change that would leave the product unpriceable is refused.'
          : 'Name, SKU, billing frequency, and either a flat price per currency or a tier table.'
      }
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={save} disabled={busy}>
            {busy ? 'Saving' : 'Save product'}
          </Button>
        </>
      }
    >
      {refusal && (
        <Notice tone="danger" title="The product was not saved">
          {refusalText(refusal.code, refusal.message)}
        </Notice>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name" id="product-name" hint={fieldErrors.name}>
          <input
            id="product-name"
            className={inputClass}
            value={form.name}
            onChange={(event) => set('name', event.target.value)}
          />
        </Field>
        <Field label="SKU" id="product-sku" hint={fieldErrors.sku}>
          <input
            id="product-sku"
            className={inputClass}
            value={form.sku}
            onChange={(event) => set('sku', event.target.value)}
          />
        </Field>
      </div>

      <Field label="Description" id="product-description">
        <textarea
          id="product-description"
          rows={2}
          className={inputClass}
          value={form.description}
          onChange={(event) => set('description', event.target.value)}
        />
      </Field>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Pricing method" id="product-method" hint={METHOD_RULES[form.pricing_method]}>
          <select
            id="product-method"
            className={inputClass}
            value={form.pricing_method}
            onChange={(event) => set('pricing_method', event.target.value)}
          >
            {methods.map((name) => (
              <option key={name} value={name}>
                {METHOD_WORDS[name] ?? name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Billing frequency" id="product-billing">
          <select
            id="product-billing"
            className={inputClass}
            value={form.billing_frequency}
            onChange={(event) => set('billing_frequency', event.target.value)}
          >
            {(vocabulary?.billing_frequencies ?? []).map((name) => (
              <option key={name} value={name}>
                {name === 'recurring' ? 'Recurring' : 'One time'}
              </option>
            ))}
          </select>
        </Field>
      </div>

      {form.pricing_method === 'flat' ? (
        <div className="space-y-3 rounded-sm border border-border-subtle p-4">
          <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Price per currency
          </p>
          <p className="text-xs text-muted-foreground">
            The research says a flat product carries a price in each account currency. A currency
            with no price is not priced in that currency: a quote never reads it as zero.
          </p>
          {fieldErrors.prices && (
            <Notice tone="danger" title="No price anywhere">
              {fieldErrors.prices}
            </Notice>
          )}
          <div className="grid gap-3 sm:grid-cols-3">
            {currencies.slice(0, 6).map((code) => (
              <NumberField
                key={code}
                id={`price-${code}`}
                label={`Price in ${code}`}
                step={0.01}
                value={form.prices[code] ?? ''}
                onChange={(value) => set('prices', { ...form.prices, [code]: value })}
              />
            ))}
          </div>
        </div>
      ) : (
        <div className="space-y-3 rounded-sm border border-border-subtle p-4">
          <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">Ranges</p>
          <p className="text-xs text-muted-foreground">
            Type a minimum for each range. The maximum is derived from the next minimum, so there
            is nothing to keep in step. The last range has no maximum, which is the open-ended one.
          </p>
          {fieldErrors.tiers && (
            <Notice tone="danger" title="The ranges are not usable">
              {fieldErrors.tiers}
            </Notice>
          )}
          <div className="space-y-2">
            {form.tiers.map((tier, index) => (
              <div
                key={index}
                className="grid items-end gap-2 sm:grid-cols-[6rem_8rem_1fr_auto]"
              >
                <NumberField
                  id={`tier-min-${index}`}
                  label="From"
                  hint={fieldErrors[`tiers[${index}].min_qty`]}
                  value={tier.min_qty}
                  onChange={(value) => setTier(index, 'min_qty', value)}
                />
                <div className="pb-2 text-xs text-muted-foreground">
                  {tier.max_qty === '' || tier.min_qty === ''
                    ? 'to'
                    : `to ${Number(tier.min_qty) + tierSpan(tier, form.tiers, index)}`}
                </div>
                <NumberField
                  id={`tier-price-${index}`}
                  label={form.pricing_method === 'stair_step' ? 'Fee' : 'Unit price'}
                  step={0.01}
                  hint={fieldErrors[`tiers[${index}].unit_price`]}
                  value={tier.unit_price}
                  onChange={(value) => setTier(index, 'unit_price', value)}
                />
                <Button
                  variant="ghost"
                  onClick={() => removeTier(index)}
                  aria-label={`Remove range ${index + 1}`}
                >
                  <Icon name="trash" size={16} />
                </Button>
              </div>
            ))}
          </div>
          <Button variant="secondary" onClick={addTier} icon="plus">
            Add range
          </Button>
        </div>
      )}

      <Toggle
        id="product-active"
        label="Active"
        hint="Turn this off to retire the product from new quotes without deleting it."
        checked={form.active}
        onChange={(value) => set('active', value)}
      />
    </Modal>
  )
}

/**
 * The highest quantity a range reaches, from the next range's minimum.
 *
 * Shown live in the editor so a seller sees the maximum that will be stored. The
 * backend derives it the same way and a test fixes the rule; this is the same
 * arithmetic in the one place the seller can watch it happen.
 */
function tierSpan(tier, tiers, index) {
  const next = tiers[index + 1]
  const here = Number(tier.min_qty)
  if (!next || next.min_qty === '' || !Number.isFinite(here)) return 0
  return Math.max(0, Number(next.min_qty) - here - 1)
}

/** The payload a save sends: blanks dropped, the rest as typed. */
function buildPayload(form) {
  const payload = {
    name: form.name,
    sku: form.sku,
    description: form.description,
    pricing_method: form.pricing_method,
    billing_frequency: form.billing_frequency,
    active: form.active,
  }
  if (form.pricing_method === 'flat') {
    payload.currency = Object.keys(form.prices)[0] ?? ''
    payload.prices = Object.fromEntries(
      Object.entries(form.prices).filter(([, value]) => value !== '' && value !== null),
    )
  } else {
    payload.currency = form.currency
    payload.tiers = form.tiers
      .filter((tier) => tier.min_qty !== '' && tier.unit_price !== '')
      .map((tier) => ({
        min_qty: Number(tier.min_qty),
        unit_price: Number(tier.unit_price),
      }))
  }
  return payload
}

/** A blank form, or one loaded from a product being edited. */
function draftFrom(product) {
  if (!product) {
    return {
      name: '',
      sku: '',
      description: '',
      pricing_method: 'flat',
      billing_frequency: 'one_time',
      active: true,
      prices: {},
      tiers: [emptyTier()],
      currency: '',
    }
  }
  return {
    name: product.name ?? '',
    sku: product.sku ?? '',
    description: product.description ?? '',
    pricing_method: product.pricing_method ?? 'flat',
    billing_frequency: product.billing_frequency ?? 'one_time',
    active: product.active !== false,
    prices: { ...(product.prices ?? {}) },
    tiers: (product.tiers ?? []).map((tier) => ({
      min_qty: tier.min_qty ?? '',
      unit_price: tier.unit_price ?? tier.fee ?? '',
    })),
    currency: product.currency ?? '',
  }
}

/**
 * The quantity tester.
 *
 * The one panel whose whole job is showing the arithmetic. A seller who cannot
 * see which range a quantity reached and which layer supplied the price cannot
 * check the catalogue before quoting from it, and a catalogue nobody checks is a
 * catalogue nobody trusts.
 */
function PriceTester({ product, books, onError }) {
  const [quantity, setQuantity] = useState(10)
  const [bookId, setBookId] = useState('')
  const [currency, setCurrency] = useState('')
  const [answer, setAnswer] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  if (!product) return null

  const run = async () => {
    setBusy(true)
    setError(null)
    try {
      setAnswer(await catalogueApi.price(product.id, quantity, bookId, currency))
    } catch (failure) {
      setAnswer(null)
      setError(failure)
      onError?.(failure)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="space-y-4">
      <div>
        <h2 className="font-display text-base font-semibold text-foreground">
          Price a quantity
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          The tier table is evaluated against the quantity, then the price book overrides the
          product default. {METHOD_RULES[product.pricing_method]}
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <NumberField
          id="test-quantity"
          label="Quantity"
          min={1}
          value={quantity}
          onChange={(value) => setQuantity(value === '' ? 1 : value)}
        />
        <Field label="Price book" id="test-book">
          <select
            id="test-book"
            className={inputClass}
            value={bookId}
            onChange={(event) => setBookId(event.target.value)}
          >
            <option value="">None, use the product price</option>
            {books.map((book) => (
              <option key={book.id} value={book.id}>
                {book.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Currency" id="test-currency" hint="Empty means the product's own currency.">
          <input
            id="test-currency"
            className={inputClass}
            value={currency}
            placeholder={product.currency || 'USD'}
            onChange={(event) => setCurrency(event.target.value.toUpperCase())}
          />
        </Field>
      </div>

      <Button variant="primary" onClick={run} disabled={busy} icon="audit">
        {busy ? 'Pricing' : 'Price this quantity'}
      </Button>

      {error && <ErrorNote error={error} onRetry={run} />}

      {answer && (
        <div className="space-y-3 rounded-sm border border-border-subtle p-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={BADGE_TONES.source[answer.source] ?? 'neutral'}>
              {SOURCE_WORDS[answer.source] ?? answer.source}
            </Badge>
            {answer.tier_label && <Badge tone="neutral">Range {answer.tier_label}</Badge>}
          </div>

          <div className="grid gap-4 sm:grid-cols-3">
            <StatCard
              label="Unit price"
              value={formatMoney(answer.unit_price, answer.currency)}
              hint={
                answer.unit_is_average
                  ? 'An average across the ranges, not a rate you can charge'
                  : 'The rate this quantity pays per unit'
              }
            />
            <StatCard label="Quantity" value={String(answer.quantity)} />
            <StatCard label="Line total" value={formatMoney(answer.total, answer.currency)} />
          </div>

          {answer.source === 'missing' && (
            <Notice tone="danger" title="No price for this currency">
              This product has no price in {answer.currency || 'that currency'}. A missing price is
              never read as zero, because a quote priced at nothing is worse than a quote that
              cannot be priced.
            </Notice>
          )}

          {answer.breakdown?.length > 1 && (
            <div className="space-y-1">
              <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
                How the quantity was split
              </p>
              {answer.breakdown.map((part, index) => (
                <MetaRow
                  key={index}
                  label={`${part.tier_label}: ${part.quantity} at ${formatMoney(part.unit_price, answer.currency)}`}
                  value={formatMoney(part.amount, answer.currency)}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </Card>
  )
}

/**
 * The create-price-book form, in a dialog.
 *
 * The researched flow is create a book, add products, then switch it on. The
 * currencies are checkboxes rather than a free text field, because a mistyped
 * code is a book that prices in nothing and the backend already refuses a code
 * that is not three letters. A new book starts switched off, which is the
 * vendor's own rule, so the dialog does not offer the Active toggle.
 */
function PriceBookDialog({ open, onClose, onSave, vocabulary, busy, refusal }) {
  const [form, setForm] = useState(() => ({
    name: '',
    description: '',
    currencies: [],
    segment: '',
    region: '',
  }))
  const [fieldErrors, setFieldErrors] = useState({})

  const currencies = vocabulary?.common_currencies ?? []
  const set = (key, value) => setForm((prev) => ({ ...prev, [key]: value }))
  const toggleCurrency = (code) =>
    set(
      'currencies',
      form.currencies.includes(code)
        ? form.currencies.filter((item) => item !== code)
        : [...form.currencies, code],
    )

  const save = async () => {
    try {
      await onSave({
        name: form.name,
        description: form.description,
        supported_currencies: form.currencies,
        segment: form.segment,
        region: form.region,
      })
      setFieldErrors({})
    } catch (error) {
      setFieldErrors(error.errors ?? {})
    }
  }

  return (
    <Modal
      open={open}
      title="Create a price book"
      description="Name the book and the currencies it prices in. It starts switched off."
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={save} disabled={busy}>
            {busy ? 'Creating' : 'Create price book'}
          </Button>
        </>
      }
    >
      {refusal && (
        <Notice tone="danger" title="The price book was not created">
          {refusalText(refusal.code, refusal.message)}
        </Notice>
      )}

      <Field label="Name" id="book-name" hint={fieldErrors.name}>
        <input
          id="book-name"
          className={inputClass}
          value={form.name}
          onChange={(event) => set('name', event.target.value)}
        />
      </Field>

      <Field label="Description" id="book-description">
        <textarea
          id="book-description"
          rows={2}
          className={inputClass}
          value={form.description}
          onChange={(event) => set('description', event.target.value)}
        />
      </Field>

      <fieldset className="space-y-2">
        <legend className="text-[13px] font-medium text-foreground">Currencies</legend>
        <p className="text-xs text-muted-foreground">
          A product must have a price in each currency before this book can go live.
        </p>
        {fieldErrors.supported_currencies && (
          <Notice tone="danger" title="No currency chosen">
            {fieldErrors.supported_currencies}
          </Notice>
        )}
        <div className="flex flex-wrap gap-2">
          {currencies.map((code) => (
            <Checkbox
              key={code}
              id={`book-currency-${code}`}
              label={code}
              checked={form.currencies.includes(code)}
              onChange={() => toggleCurrency(code)}
            />
          ))}
        </div>
      </fieldset>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Segment" id="book-segment">
          <input
            id="book-segment"
            className={inputClass}
            value={form.segment}
            onChange={(event) => set('segment', event.target.value)}
          />
        </Field>
        <Field label="Region" id="book-region">
          <input
            id="book-region"
            className={inputClass}
            value={form.region}
            onChange={(event) => set('region', event.target.value)}
          />
        </Field>
      </div>
    </Modal>
  )
}

/**
 * The form that puts a product into one price book and overrides its price.
 *
 * The research's step 4 is "Add products, then edit Price, Billing frequency and
 * Term per row". A row may be saved with only some of the book's prices set: the
 * vendor blocks activation rather than the save, so the form allows a gap and
 * the book itself reports what is still missing.
 */
function BookRowForm({ book, products, existingIds, billingFrequencies, onAdd, busy }) {
  const [productId, setProductId] = useState('')
  const [prices, setPrices] = useState({})
  const [billing, setBilling] = useState(billingFrequencies?.[0] ?? 'recurring')
  const [term, setTerm] = useState('')

  const available = products.filter(
    (product) => !existingIds.includes(product.id) && !product.is_bundle,
  )
  const currencies = book.currencies ?? []

  const submit = () =>
    onAdd({
      product_id: productId,
      price_book_id: book.id,
      prices: Object.fromEntries(
        Object.entries(prices).filter(([, value]) => value !== '' && value !== null),
      ),
      billing_frequency: billing,
      term,
    })

  return (
    <div className="space-y-3 rounded-sm border border-dashed border-border-subtle p-3">
      <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
        Add a product
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Product" id={`add-product-${book.id}`}>
          <select
            id={`add-product-${book.id}`}
            className={inputClass}
            value={productId}
            onChange={(event) => setProductId(event.target.value)}
          >
            <option value="">Choose a product</option>
            {available.map((product) => (
              <option key={product.id} value={product.id}>
                {product.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Term" id={`add-term-${book.id}`} hint="For example, 12 months.">
          <input
            id={`add-term-${book.id}`}
            className={inputClass}
            value={term}
            onChange={(event) => setTerm(event.target.value)}
          />
        </Field>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        {currencies.map((code) => (
          <NumberField
            key={code}
            id={`add-price-${book.id}-${code}`}
            label={`Override price in ${code}`}
            step={0.01}
            value={prices[code] ?? ''}
            onChange={(value) => setPrices((prev) => ({ ...prev, [code]: value }))}
          />
        ))}
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Row billing frequency" id={`add-billing-${book.id}`}>
          <select
            id={`add-billing-${book.id}`}
            className={inputClass}
            value={billing}
            onChange={(event) => setBilling(event.target.value)}
          >
            {(billingFrequencies ?? []).map((name) => (
              <option key={name} value={name}>
                {name === 'recurring' ? 'Recurring' : 'One time'}
              </option>
            ))}
          </select>
        </Field>
        <div className="flex items-end">
          <Button variant="secondary" onClick={submit} disabled={busy || !productId}>
            Add to this book
          </Button>
        </div>
      </div>
    </div>
  )
}

/** One price book, with its currencies, its gaps and its rows. */
function PriceBookPanel({
  book,
  items,
  products,
  billingFrequencies,
  onToggle,
  onAddRow,
  busy,
  refusal,
  rowRefusal,
}) {
  const gaps = Object.entries(book.missing_prices ?? {})
  const canActivate = book.row_count > 0 && gaps.length === 0

  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-display text-base font-semibold text-foreground">{book.name}</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">{book.description}</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={book.active ? 'insert' : 'neutral'}>
            {book.active ? 'Live' : 'Not live'}
          </Badge>
          <Button
            variant={book.active ? 'ghost' : 'secondary'}
            onClick={() => onToggle(book, !book.active)}
            disabled={busy || (!book.active && !canActivate)}
          >
            {book.active ? 'Switch off' : 'Switch on'}
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        {(book.currencies ?? []).map((code) => (
          <Badge key={code} tone="neutral">
            {code}
          </Badge>
        ))}
        {(book.segment || book.region) && (
          <Badge tone="update">{book.segment || book.region}</Badge>
        )}
      </div>

      {refusal && (
        <Notice tone="warning" title="The book did not change">
          {refusalText(refusal.code, refusal.message)}
        </Notice>
      )}

      {gaps.length > 0 && (
        <Notice tone="warning" title="Not every price is set">
          A price book cannot go live until every product in it has a price in every currency it
          supports. Missing:{' '}
          {gaps
            .map(([productId, codes]) => `${items.find((row) => row.product_id === productId)?.product_name ?? productId} in ${codes.join(', ')}`)
            .join('; ')}
        </Notice>
      )}

      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No products in this book yet. Add one to override its price here.
        </p>
      ) : (
        <div className="space-y-1">
          {items.map((row) => (
            <div
              key={row.id}
              className="flex flex-wrap items-baseline justify-between gap-2 border-t border-border-subtle py-2 first:border-t-0"
            >
              <span className="text-[13px] text-foreground">{row.product_name ?? row.product_id}</span>
              {/* Price and term are separate elements so each is its own
                  readable unit, rather than one run of text a screen reader
                  announces as a single sentence. */}
              <span className="font-mono text-xs text-muted-foreground">
                {Object.entries(row.prices ?? {})
                  .map(([code, value]) => formatMoney(value, code))
                  .join('  ') || 'no price set'}
              </span>
              {row.term && (
                <span className="font-mono text-xs text-muted-foreground">{row.term}</span>
              )}
            </div>
          ))}
        </div>
      )}

      <BookRowForm
        book={book}
        products={products}
        existingIds={items.map((row) => row.product_id)}
        billingFrequencies={billingFrequencies}
        onAdd={onAddRow}
        busy={busy}
      />

      {rowRefusal && (
        <Notice tone="danger" title="The product was not added">
          {refusalText(rowRefusal.code, rowRefusal.message)}
        </Notice>
      )}
    </Card>
  )
}

/** The page. */
function CataloguePage() {
  const [roomId] = useState('')
  const [term, setTerm] = useState('')
  const [activeOnly, setActiveOnly] = useState(false)
  const [editing, setEditing] = useState(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [bookDialogOpen, setBookDialogOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState(null)
  const [bookRefusal, setBookRefusal] = useState(null)
  const [createBookRefusal, setCreateBookRefusal] = useState(null)
  const [rowRefusal, setRowRefusal] = useState(null)
  const [selected, setSelected] = useState(null)

  const vocabulary = useAsync(() => catalogueApi.vocabulary(), [])
  const board = useAsync(() => catalogueApi.summary(roomId), [roomId])
  const products = useAsync(
    () => catalogueApi.products(roomId, term, activeOnly),
    [roomId, term, activeOnly],
  )
  const books = useAsync(() => catalogueApi.priceBooks(roomId), [roomId])

  /**
   * Every price-book row, loaded once and grouped in memory.
   *
   * One request rather than one per book: a page that shows a book list and a
   * product list has no reason to make a request per card, and N books times a
   * request each is N round trips before anything renders.
   */
  const rows = useAsync(() => catalogueApi.bookItems(), [])

  const refreshAll = () => {
    board.refetch()
    products.refetch()
    books.refetch()
    rows.refetch()
  }

  /** The rows of one book, taken from the rows already loaded. */
  const rowsFor = (bookId) =>
    (rows.data?.items ?? []).filter((row) => row.price_book_id === bookId)

  const saveProduct = async (payload) => {
    setBusy(true)
    setRefusal(null)
    try {
      const saved = editing
        ? await catalogueApi.editProduct(editing.id, payload)
        : await catalogueApi.createProduct(payload, roomId)
      setDialogOpen(false)
      setEditing(null)
      setSelected(saved)
      refreshAll()
    } catch (failure) {
      setRefusal({ code: failure.code, message: failure.message })
      throw failure
    } finally {
      setBusy(false)
    }
  }

  const toggleActive = async (product) => {
    setBusy(true)
    setRefusal(null)
    try {
      await catalogueApi.setProductActive(product.id, product.active === false)
      refreshAll()
    } catch (failure) {
      setRefusal({ code: failure.code, message: failure.message })
    } finally {
      setBusy(false)
    }
  }

  const toggleBook = async (book, active) => {
    setBusy(true)
    setBookRefusal(null)
    try {
      await catalogueApi.setPriceBookActive(book.id, active)
      refreshAll()
    } catch (failure) {
      setBookRefusal({ code: failure.code, message: failure.message })
    } finally {
      setBusy(false)
    }
  }

  const createBook = async (payload) => {
    setBusy(true)
    setCreateBookRefusal(null)
    try {
      await catalogueApi.createPriceBook(payload, roomId)
      setBookDialogOpen(false)
      refreshAll()
    } catch (failure) {
      setCreateBookRefusal({ code: failure.code, message: failure.message })
      throw failure
    } finally {
      setBusy(false)
    }
  }

  /**
   * Add one product to one book, and keep the refusal on the book it names.
   *
   * The token is shown with the book id so a refusal on one card does not read
   * as a refusal on every card, which is what a page-wide error would do when
   * two books sit side by side.
   */
  const addRow = async (payload) => {
    setBusy(true)
    setRowRefusal(null)
    try {
      await catalogueApi.addBookItem(payload, roomId)
      refreshAll()
    } catch (failure) {
      setRowRefusal({
        book_id: payload.price_book_id,
        code: failure.code,
        message: failure.message,
      })
    } finally {
      setBusy(false)
    }
  }

  if (vocabulary.loading) return <Spinner label="Loading the catalogue" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (products.loading && !products.data) return <Spinner label="Loading products" />
  if (products.error) return <ErrorNote error={products.error} onRetry={products.refetch} />

  const summary = board.data ?? {}
  const library = products.data?.products ?? []
  const allBooks = books.data?.price_books ?? []

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-semibold text-foreground">
          Product and price-book catalogue
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Build a product library priced flat per currency or by quantity range, then group
          products into price books that override the default price for one buyer segment or
          region.
        </p>
      </header>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Products"
          value={String(summary.products ?? 0)}
          hint={`${summary.active_products ?? 0} active, ${summary.retired_products ?? 0} retired`}
          icon="database"
        />
        <StatCard
          label="Tiered"
          value={String(summary.tiered_products ?? 0)}
          hint="Priced by quantity range"
          icon="audit"
        />
        <StatCard
          label="Price books"
          value={String(summary.price_books ?? 0)}
          hint={`${summary.active_price_books ?? 0} live`}
          icon="schema"
        />
        <StatCard
          label="Currencies"
          value={String((summary.currencies ?? []).length)}
          hint={(summary.currencies ?? []).join(', ') || 'None yet'}
          icon="dashboard"
        />
      </div>

      {refusal && (
        <Notice tone="danger" title="That change was not saved" onDismiss={() => setRefusal(null)}>
          {refusalText(refusal.code, refusal.message)}
        </Notice>
      )}

      <Card className="space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0 flex-1">
            <h2 className="font-display text-lg font-semibold text-foreground">The library</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Every product, newest first. A retired product stays in the list and stops being
              offered for new quotes.
            </p>
          </div>
          <Button
            variant="primary"
            icon="plus"
            onClick={() => {
              setEditing(null)
              setRefusal(null)
              setDialogOpen(true)
            }}
          >
            Add a product
          </Button>
        </div>

        <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
          <Field label="Search" id="catalogue-search" hint="Matches the name, the SKU or the description.">
            <input
              id="catalogue-search"
              className={inputClass}
              value={term}
              onChange={(event) => setTerm(event.target.value)}
              placeholder="Northwind"
            />
          </Field>
          <Toggle
            id="catalogue-active-only"
            label="Active only"
            checked={activeOnly}
            onChange={setActiveOnly}
          />
        </div>

        {library.length === 0 ? (
          <EmptyState
            title="No products yet"
            description="A product carries a name, a SKU, a billing frequency, and either a flat price per currency or a tier table."
            action={
              <Button
                variant="primary"
                icon="plus"
                onClick={() => {
                  setEditing(null)
                  setDialogOpen(true)
                }}
              >
                Add a product
              </Button>
            }
          />
        ) : (
          <div className="space-y-2">
            {library.map((product) => (
              <div
                key={product.id}
                className={`rounded-sm border p-4 ${
                  selected?.id === product.id
                    ? 'border-accent bg-accent-soft'
                    : 'border-border-subtle bg-surface'
                }`}
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <button
                    type="button"
                    onClick={() => setSelected(product)}
                    // min-h-11 because this is a target, not a heading. The
                    // shared Button carries it for the same reason: a row a
                    // seller taps to price has to be 44px tall whatever the
                    // length of the product name inside it.
                    className="min-h-11 min-w-0 flex-1 py-1 text-left"
                  >
                    <span className="block font-display text-base font-semibold text-foreground">
                      {product.name}
                    </span>
                    <span className="mt-0.5 block text-xs text-muted-foreground">{product.sku}</span>
                  </button>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={BADGE_TONES.method[product.pricing_method] ?? 'neutral'}>
                      {METHOD_WORDS[product.pricing_method] ?? product.pricing_method}
                    </Badge>
                    <Badge tone={BADGE_TONES.status[product.status] ?? 'neutral'}>
                      {PRODUCT_STATUS_WORDS[product.status] ?? product.status}
                    </Badge>
                  </div>
                </div>

                <p className="mt-2 text-xs text-muted-foreground">
                  {METHOD_RULES[product.pricing_method]}
                  {product.is_tiered ? ` ${product.tier_count} ranges.` : ''}
                  {product.is_bundle ? ' Bundle.' : ''}
                  {!product.is_importable ? ' Not bulk-importable.' : ''}
                </p>

                {product.is_tiered && product.tiers?.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {product.tiers.map((tier, index) => (
                      <Badge key={index} tone="neutral">
                        {/* One string, so the badge is one readable unit rather
                            than three adjacent text nodes. A screen reader
                            announcing "one to nine", "colon", "one hundred"
                            *  reads as three things, not as a price. */}
                        {`${rangeLabel(tier)}: ${formatMoney(
                          product.pricing_method === 'stair_step'
                            ? (tier.fee ?? tier.unit_price)
                            : tier.unit_price,
                          product.currency,
                        )}`}
                      </Badge>
                    ))}
                  </div>
                )}

                {!product.is_tiered && (product.currencies ?? []).length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {product.currencies.map((code) => (
                      <Badge key={code} tone="neutral">
                        {formatMoney(product.prices[code], code)}
                      </Badge>
                    ))}
                  </div>
                )}

                <div className="mt-3 flex flex-wrap gap-2">
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setEditing(product)
                      setRefusal(null)
                      setDialogOpen(true)
                    }}
                  >
                    Edit
                  </Button>
                  <Button variant="ghost" onClick={() => toggleActive(product)} disabled={busy}>
                    {product.active === false ? 'Bring back' : 'Retire'}
                  </Button>
                  <Button variant="ghost" onClick={() => setSelected(product)}>
                    Price it
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {selected && (
        <PriceTester
          product={selected}
          books={allBooks.map((book) => ({ id: book.id, name: book.name }))}
        />
      )}

      <div className="space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0 flex-1">
            <h2 className="font-display text-lg font-semibold text-foreground">Price books</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              A book names the currencies it prices in and groups products that get a different
              rate. A new book starts switched off: switch it on once every product has a price in
              every currency it supports.
            </p>
          </div>
          <Button
            variant="secondary"
            icon="plus"
            onClick={() => {
              setCreateBookRefusal(null)
              setBookDialogOpen(true)
            }}
          >
            New price book
          </Button>
        </div>

        {books.error && <ErrorNote error={books.error} onRetry={books.refetch} />}

        {allBooks.length === 0 ? (
          <EmptyState
            title="No price books yet"
            description="A price book overrides a product's default price for one buyer segment or region."
          />
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {allBooks.map((book) => (
              <PriceBookPanel
                key={book.id}
                book={book}
                items={rowsFor(book.id)}
                products={library}
                billingFrequencies={vocabulary.data?.billing_frequencies ?? []}
                onToggle={toggleBook}
                onAddRow={addRow}
                busy={busy}
                refusal={bookRefusal}
                rowRefusal={rowRefusal?.book_id === book.id ? rowRefusal : null}
              />
            ))}
          </div>
        )}
      </div>

      {dialogOpen && (
        <ProductDialog
          open
          product={editing}
          vocabulary={vocabulary.data}
          busy={busy}
          refusal={refusal}
          onClose={() => {
            setDialogOpen(false)
            setEditing(null)
            setRefusal(null)
          }}
          onSave={saveProduct}
        />
      )}

      {bookDialogOpen && (
        <PriceBookDialog
          open
          vocabulary={vocabulary.data}
          busy={busy}
          refusal={createBookRefusal}
          onClose={() => {
            setBookDialogOpen(false)
            setCreateBookRefusal(null)
          }}
          onSave={createBook}
        />
      )}
    </div>
  )
}

export { CataloguePage }

export default {
  id: 'wf-087-curate-a-product-and-price-book-catalo',
  label: 'Product catalogue',
  icon: 'database',
  iconPath: CATALOGUE_ICON,
  order: 87,
  Component: CataloguePage,
}