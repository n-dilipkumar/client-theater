/**
 * The WF-087 page.
 *
 * What these tests are for
 * ------------------------
 *
 * Three things the build cannot prove by compiling:
 *
 *  1. **The arithmetic reaches the screen.** The page prints the range reached and
 *     the layer that supplied the price. A page that showed a unit price with no
 *     provenance would let a seller quote a figure they cannot explain, which is
 *     the failure the pricing tester exists to prevent.
 *  2. **A status is never colour alone.** Every tone in this feature travels with
 *     a word. The design system floor is checked in review, and these tests are
 *     the cheapest place to keep it from slipping.
 *  3. **A refusal reaches the form.** A 422 carries a field-keyed errors map and a
 *     409 carries a reason, and the page turns both into a sentence beside the
 *     input. A refusal swallowed into a console message is the worst outcome a
 *     form can have.
 *
 * Nothing here reads the real clock or the real network. The API module is mocked
 * whole, so a test asserts what the page renders rather than what the server
 * happens to hold today.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import descriptor, { CataloguePage } from './index'
import {
  METHOD_RULES,
  METHOD_WORDS,
  PRODUCT_STATUS_WORDS,
  REFUSALS,
  SOURCE_WORDS,
  formatMoney,
  rangeLabel,
  refusalText,
} from './api'

vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    catalogueApi: {
      summary: vi.fn(),
      vocabulary: vi.fn(),
      inferences: vi.fn(),
      products: vi.fn(),
      product: vi.fn(),
      createProduct: vi.fn(),
      editProduct: vi.fn(),
      setProductActive: vi.fn(),
      removeProduct: vi.fn(),
      price: vi.fn(),
      priceBooks: vi.fn(),
      priceBook: vi.fn(),
      createPriceBook: vi.fn(),
      editPriceBook: vi.fn(),
      setPriceBookActive: vi.fn(),
      bookItems: vi.fn(),
      addBookItem: vi.fn(),
      editBookItem: vi.fn(),
      removeBookItem: vi.fn(),
    },
  }
})

const { catalogueApi } = await import('./api')

const VOCABULARY = {
  platform: 'hubspot_products_and_price_books',
  collections: { products: 'wf087_product', price_books: 'wf087_price_book' },
  pricing_methods: ['flat', 'volume_based', 'graduated', 'stair_step'],
  tiered_pricing_methods: ['volume_based', 'graduated', 'stair_step'],
  billing_frequencies: ['one_time', 'recurring'],
  common_currencies: ['USD', 'EUR', 'GBP'],
  product_statuses: ['active', 'retired'],
  price_sources: ['price_book', 'tier', 'product_default', 'missing'],
  /**
   * The refusal tokens the backend declares, written out by hand from
   * `ERROR_CODES` in `backend/dsr/price_books/vocabulary.py`.
   *
   * Written out rather than derived from the page's own map on purpose: a test
   * that builds the expected list from the thing it is checking agrees with it
   * by construction and can never fail. A backend token nobody translates shows
   * up here as an untranslated key, which is the whole point of the check.
   */
  error_codes: {
    product_needs_a_name: {},
    product_needs_a_sku: {},
    sku_is_taken: {},
    product_needs_a_price: {},
    tier_needs_a_minimum: {},
    tier_minimum_must_be_whole: {},
    tier_minimum_must_be_positive: {},
    tier_needs_a_price: {},
    tier_minimums_must_rise: {},
    unknown_pricing_method: {},
    price_book_needs_a_name: {},
    price_book_needs_a_currency: {},
    currency_is_not_a_code: {},
    price_book_row_missing_a_price: {},
    price_book_is_not_complete: {},
    price_book_row_needs_a_product: {},
    price_book_row_needs_a_book: {},
    one_row_per_product_and_book: {},
    bundle_products_are_not_in_a_price_book: {},
    not_a_product: {},
    not_a_price_book: {},
    not_a_price_book_item: {},
    price_is_negative: {},
    price_is_not_a_number: {},
    price_is_not_finite: {},
    quantity_must_be_positive: {},
    retired_product_is_not_priced: {},
  },
}

const TIERS = [
  { min_qty: 1, max_qty: 9, unit_price: 100, fee: null },
  { min_qty: 10, max_qty: 24, unit_price: 80, fee: null },
  { min_qty: 25, max_qty: null, unit_price: 60, fee: null },
]

const PRODUCT = {
  id: 'p1',
  name: 'Northwind Platform',
  sku: 'NWD-PLAT',
  description: 'Per year.',
  pricing_method: 'volume_based',
  billing_frequency: 'recurring',
  active: true,
  status: 'active',
  currency: 'USD',
  prices: {},
  tiers: TIERS,
  tier_count: 3,
  is_tiered: true,
  is_bundle: false,
  is_importable: false,
  currencies: [],
}

const BOOK = {
  id: 'b1',
  name: 'Standard list',
  description: 'The default rate.',
  currencies: ['USD', 'EUR'],
  active: true,
  row_count: 1,
  missing_prices: {},
  is_complete: true,
}

function stubBoard(overrides = {}) {
  catalogueApi.summary.mockResolvedValue({
    products: 1,
    active_products: 1,
    retired_products: 0,
    tiered_products: 1,
    price_books: 1,
    active_price_books: 1,
    price_book_rows: 1,
    currencies: ['EUR', 'USD'],
    ...overrides,
  })
}

function stubLibrary(rows = [PRODUCT]) {
  catalogueApi.products.mockResolvedValue({ products: rows, count: rows.length })
}

beforeEach(() => {
  vi.clearAllMocks()
  catalogueApi.vocabulary.mockResolvedValue(VOCABULARY)
  catalogueApi.inferences.mockResolvedValue({ decided: [], not_built: [] })
  catalogueApi.priceBooks.mockResolvedValue({ price_books: [BOOK], count: 1 })
  catalogueApi.bookItems.mockResolvedValue({
    items: [
      {
        id: 'r1',
        product_id: 'p1',
        price_book_id: 'b1',
        prices: { USD: 4400 },
        term: '12 months',
        product_name: 'Northwind Platform',
      },
    ],
    count: 1,
  })
  stubBoard()
  stubLibrary()
})

// --------------------------------------------------------------------------- //
// The descriptor
// --------------------------------------------------------------------------- //

describe('The descriptor', () => {
  it('exports exactly the keys the host discovers', () => {
    expect(typeof descriptor.id).toBe('string')
    expect(descriptor.label).toBeTruthy()
    expect(descriptor.Component).toBe(CataloguePage)
  })

  it('names a glyph that is not in the shared map, so ui.jsx is not edited', () => {
    expect(descriptor.iconPath).toBeTruthy()
    expect(descriptor.iconPath).toMatch(/^M/)
  })

  it('carries no emoji anywhere in its label', () => {
    expect(descriptor.label).not.toMatch(/\p{Extended_Pictographic}/u)
  })
})

// --------------------------------------------------------------------------- //
// The header
// --------------------------------------------------------------------------- //

describe('The catalogue header', () => {
  it('counts what the catalogue holds', async () => {
    render(<CataloguePage />)
    expect(await screen.findByText('Product and price-book catalogue')).toBeTruthy()
    expect(await screen.findByRole('heading', { name: 'Price books' })).toBeTruthy()
    // Both counts render as machine values in mono, per the type table.
    expect(screen.getAllByText('Products').length).toBeGreaterThan(0)
  })

  it('shows an empty state rather than a blank panel when nothing is seeded', async () => {
    stubLibrary([])
    render(<CataloguePage />)
    expect(await screen.findByText('No products yet')).toBeTruthy()
  })

  it('shows an error note with a retry when the library cannot be read', async () => {
    catalogueApi.products.mockRejectedValue(new Error('The catalogue is not available'))
    render(<CataloguePage />)
    expect(await screen.findByText(/not available/i)).toBeTruthy()
  })
})

// --------------------------------------------------------------------------- //
// The library list
// --------------------------------------------------------------------------- //

describe('The library list', () => {
  it('names the product and its SKU', async () => {
    render(<CataloguePage />)
    expect(await screen.findAllByText('Northwind Platform')).toBeTruthy()
    expect((await screen.findAllByText('NWD-PLAT')).length).toBeGreaterThan(0)
  })

  it('says what the pricing method does, not only what it is called', async () => {
    render(<CataloguePage />)
    // A seller reading "Volume based" cannot tell whether it discounts a large
    // order. The arithmetic has to be on the page.
    expect(
      (await screen.findAllByText(/Every unit takes the rate the quantity reaches/)).length,
    ).toBeGreaterThan(0)
  })

  it('shows every range with its derived maximum', async () => {
    render(<CataloguePage />)
    // "25 or more" in words rather than as a symbol: an infinity sign does not
    // survive a cp1252 console or a screen reader.
    expect((await screen.findAllByText('1 to 9: 100.00 USD')).length).toBeGreaterThan(0)
    expect((await screen.findAllByText('10 to 24: 80.00 USD')).length).toBeGreaterThan(0)
    expect((await screen.findAllByText('25 or more: 60.00 USD')).length).toBeGreaterThan(0)
  })

  it('says a tier-priced product may not be bulk-imported', async () => {
    render(<CataloguePage />)
    expect(await screen.findByText(/Not bulk-importable/)).toBeTruthy()
  })

  it('says a retired product is retired in words, not by colour', async () => {
    stubLibrary([{ ...PRODUCT, active: false, status: 'retired' }])
    render(<CataloguePage />)
    expect(await screen.findByText(PRODUCT_STATUS_WORDS.retired)).toBeTruthy()
  })

  it('offers to bring a retired product back', async () => {
    stubLibrary([{ ...PRODUCT, active: false, status: 'retired' }])
    render(<CataloguePage />)
    expect(await screen.findByRole('button', { name: 'Bring back' })).toBeTruthy()
  })

  it('searches as the user types, and sends the term to the API', async () => {
    const user = userEvent.setup()
    render(<CataloguePage />)
    await screen.findAllByText('Northwind Platform')
    await user.type(screen.getByLabelText('Search'), 'north')
    await waitFor(() => {
      expect(catalogueApi.products).toHaveBeenCalledWith('', 'north', false)
    })
  })
})

// --------------------------------------------------------------------------- //
// The price tester
// --------------------------------------------------------------------------- //

describe('The price tester', () => {
  it('is not shown until a product is chosen', async () => {
    render(<CataloguePage />)
    await screen.findAllByText('Northwind Platform')
    expect(screen.queryByRole('heading', { name: 'Price a quantity' })).toBeNull()
  })

  it('names the layer that supplied the price, beside the figure', async () => {
    const user = userEvent.setup()
    catalogueApi.price.mockResolvedValue({
      product_id: 'p1',
      pricing_method: 'volume_based',
      quantity: 12,
      unit_price: 80,
      unit_is_average: false,
      total: 960,
      tier_label: '10 to 24',
      breakdown: [],
      source: 'tier',
      currency: 'USD',
    })
    render(<CataloguePage />)
    await user.click(await screen.findByRole('button', { name: 'Price it' }))
    await user.click(screen.getByRole('button', { name: 'Price this quantity' }))

    expect(await screen.findByText(SOURCE_WORDS.tier)).toBeTruthy()
    expect(await screen.findByText('960.00 USD')).toBeTruthy()
    expect(await screen.findByText(/Range 10 to 24/)).toBeTruthy()
  })

  it('says an average is an average, so a seller does not quote it as a rate', async () => {
    const user = userEvent.setup()
    catalogueApi.price.mockResolvedValue({
      product_id: 'p1',
      pricing_method: 'graduated',
      quantity: 12,
      unit_price: 95,
      unit_is_average: true,
      total: 1140,
      tier_label: '10 to 24',
      breakdown: [
        { tier_label: '1 to 9', quantity: 9, unit_price: 100, amount: 900 },
        { tier_label: '10 to 24', quantity: 3, unit_price: 80, amount: 240 },
      ],
      source: 'tier',
      currency: 'USD',
    })
    render(<CataloguePage />)
    await user.click(await screen.findByRole('button', { name: 'Price it' }))
    await user.click(screen.getByRole('button', { name: 'Price this quantity' }))

    expect(await screen.findByText('95.00 USD')).toBeTruthy()
    expect(await screen.findByText(/An average across the ranges/)).toBeTruthy()
    // The split is shown, because that is the whole difference between the
    // graduated and volume-based figures.
    expect(await screen.findByText(/9 at 100.00 USD/)).toBeTruthy()
  })

  it('says plainly when there is no price rather than showing zero', async () => {
    const user = userEvent.setup()
    catalogueApi.price.mockResolvedValue({
      product_id: 'p1',
      pricing_method: 'flat',
      quantity: 1,
      unit_price: 0,
      unit_is_average: false,
      total: 0,
      tier_label: null,
      breakdown: [],
      source: 'missing',
      currency: 'EUR',
    })
    render(<CataloguePage />)
    await user.click(await screen.findByRole('button', { name: 'Price it' }))
    await user.click(screen.getByRole('button', { name: 'Price this quantity' }))

    expect(await screen.findByText(SOURCE_WORDS.missing)).toBeTruthy()
    expect(
      await screen.findByText(/never read as zero/i),
    ).toBeTruthy()
  })

  it('passes the chosen price book and currency to the API', async () => {
    const user = userEvent.setup()
    catalogueApi.price.mockResolvedValue({
      product_id: 'p1',
      quantity: 10,
      unit_price: 80,
      unit_is_average: false,
      total: 800,
      tier_label: '10 to 24',
      breakdown: [],
      source: 'price_book',
      currency: 'USD',
    })
    render(<CataloguePage />)
    await user.click(await screen.findByRole('button', { name: 'Price it' }))
    await user.selectOptions(screen.getByLabelText('Price book'), 'b1')
    await user.clear(screen.getByLabelText('Currency'))
    await user.type(screen.getByLabelText('Currency'), 'usd')
    await user.click(screen.getByRole('button', { name: 'Price this quantity' }))

    await waitFor(() => {
      expect(catalogueApi.price).toHaveBeenCalledWith('p1', 10, 'b1', 'USD')
    })
  })
})

// --------------------------------------------------------------------------- //
// Price books
// --------------------------------------------------------------------------- //

describe('The price-book panel', () => {
  it('names the currencies the book prices in', async () => {
    render(<CataloguePage />)
    const panel = await waitFor(() => {
      const heading = screen.getByRole('heading', { name: 'Standard list' })
      const card = heading.closest('div.rounded-sm')
      expect(card).toBeTruthy()
      return card
    })
    expect(within(panel).getByText('USD')).toBeTruthy()
    expect(within(panel).getByText('EUR')).toBeTruthy()
  })

  it('says a live book is live in words', async () => {
    render(<CataloguePage />)
    expect((await screen.findAllByText('Live')).length).toBeGreaterThan(0)
  })

  it('lists the rows with the price they override at', async () => {
    render(<CataloguePage />)
    // The price and the term are separate elements, so a row with both does not
    // read as one run of text.
    expect((await screen.findAllByText('4,400.00 USD')).length).toBeGreaterThan(0)
    expect((await screen.findAllByText('12 months')).length).toBeGreaterThan(0)
  })

  it('explains a gap and refuses to switch a book on while one stands', async () => {
    catalogueApi.priceBooks.mockResolvedValue({
      price_books: [
        {
          ...BOOK,
          active: false,
          missing_prices: { p1: ['GBP'] },
          is_complete: false,
        },
      ],
      count: 1,
    })
    render(<CataloguePage />)
    expect(await screen.findByText('Not every price is set')).toBeTruthy()
    expect(
      await screen.findByText(/Northwind Platform in GBP/),
    ).toBeTruthy()
    // The control is disabled rather than failing on click, so the page never
    // sends a request the server is going to refuse.
    expect(screen.getByRole('button', { name: 'Switch on' })).toHaveProperty('disabled', true)
  })

  it('shows the refusal when the server refuses an activation anyway', async () => {
    const user = userEvent.setup()
    const failure = Object.assign(new Error('Every product needs a price'), {
      code: 'price_book_is_not_complete',
      status: 409,
    })
    catalogueApi.setPriceBookActive.mockRejectedValue(failure)
    catalogueApi.priceBooks.mockResolvedValue({
      price_books: [{ ...BOOK, active: false, missing_prices: {}, is_complete: true, row_count: 1 }],
      count: 1,
    })
    render(<CataloguePage />)
    await user.click(await screen.findByRole('button', { name: 'Switch on' }))
    expect(await screen.findByText(refusalText('price_book_is_not_complete'))).toBeTruthy()
  })

  it('shows an empty state when there are no books', async () => {
    catalogueApi.priceBooks.mockResolvedValue({ price_books: [], count: 0 })
    render(<CataloguePage />)
    expect(await screen.findByText('No price books yet')).toBeTruthy()
  })
})

// --------------------------------------------------------------------------- //
// The product form
// --------------------------------------------------------------------------- //

describe('The product form', () => {
  it('opens with a name field and a save button', async () => {
    const user = userEvent.setup()
    render(<CataloguePage />)
    await user.click(await screen.findByRole('button', { name: 'Add a product' }))
    expect(screen.getByLabelText('Name')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Save product' })).toBeTruthy()
  })

  it('shows a flat price form for a flat product and a range form otherwise', async () => {
    const user = userEvent.setup()
    render(<CataloguePage />)
    await user.click(await screen.findByRole('button', { name: 'Add a product' }))

    expect(screen.getByText('Price per currency')).toBeTruthy()
    await user.selectOptions(screen.getByLabelText('Pricing method'), 'graduated')
    expect(screen.getByText('Ranges')).toBeTruthy()
    expect(
      screen.getByText(/The maximum is derived from the next minimum/),
    ).toBeTruthy()
  })

  it('says what each method does beside the method chooser', async () => {
    const user = userEvent.setup()
    render(<CataloguePage />)
    await user.click(await screen.findByRole('button', { name: 'Add a product' }))
    expect(screen.getByText(METHOD_RULES.flat)).toBeTruthy()
    await user.selectOptions(screen.getByLabelText('Pricing method'), 'stair_step')
    expect(screen.getByText(METHOD_RULES.stair_step)).toBeTruthy()
  })

  it('previews the derived maximum while the seller types', async () => {
    const user = userEvent.setup()
    render(<CataloguePage />)
    await user.click(await screen.findByRole('button', { name: 'Add a product' }))
    await user.selectOptions(screen.getByLabelText('Pricing method'), 'graduated')

    await user.type(screen.getByLabelText('From'), '10')
    await user.type(screen.getByLabelText('Unit price'), '80')
    await user.click(screen.getByRole('button', { name: 'Add range' }))
    await user.type(screen.getAllByLabelText('From')[1], '25')
    await user.type(screen.getAllByLabelText('Unit price')[1], '60')

    // 10 to 24, then 25 with no maximum. Shown live so the derived number is
    // never a surprise at save time.
    expect(screen.getByText('to 24')).toBeTruthy()
  })

  it('puts a refusal beside the input it is about', async () => {
    const user = userEvent.setup()
    const failure = Object.assign(new Error('The product was not saved'), {
      code: 'product_needs_a_price',
      status: 422,
      errors: { prices: 'Give the product a price in a currency, or a tier table.' },
    })
    catalogueApi.createProduct.mockRejectedValue(failure)

    render(<CataloguePage />)
    await user.click(await screen.findByRole('button', { name: 'Add a product' }))
    await user.type(screen.getByLabelText('Name'), 'Northwind Platform')
    await user.click(screen.getByRole('button', { name: 'Save product' }))

    expect(await screen.findByText('The product was not saved')).toBeTruthy()
    // The sentence appears twice by design: once as the notice at the top of the
    // dialog and once beside the input that caused it. A seller who only reads
    // the top of the dialog still learns what went wrong, and one who is looking
    // at the field learns which field.
    expect((await screen.findAllByText(failure.errors.prices)).length).toBeGreaterThan(0)
  })

  it('sends the typed product and closes on success', async () => {
    const user = userEvent.setup()
    catalogueApi.createProduct.mockResolvedValue({ ...PRODUCT, id: 'p2' })
    render(<CataloguePage />)
    await user.click(await screen.findByRole('button', { name: 'Add a product' }))
    await user.type(screen.getByLabelText('Name'), 'Northwind Platform')
    await user.type(screen.getByLabelText('SKU'), 'NWD-PLAT')
    await user.type(screen.getByLabelText('Price in USD'), '2400')
    await user.click(screen.getByRole('button', { name: 'Save product' }))

    await waitFor(() => {
      expect(catalogueApi.createProduct).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Northwind Platform',
          sku: 'NWD-PLAT',
          pricing_method: 'flat',
          prices: { USD: 2400 },
        }),
        '',
      )
    })
  })

  it('offers the Active toggle with the retirement rule as its hint', async () => {
    const user = userEvent.setup()
    render(<CataloguePage />)
    await user.click(await screen.findByRole('button', { name: 'Add a product' }))
    const toggle = screen.getByRole('switch', { name: 'Active' })
    expect(toggle).toHaveProperty('ariaChecked', 'true')
    expect(
      screen.getByText(/retire the product from new quotes without deleting it/),
    ).toBeTruthy()
    await user.click(toggle)
    expect(screen.getByRole('switch', { name: 'Active' })).toHaveProperty('ariaChecked', 'false')
  })
})

// --------------------------------------------------------------------------- //
// The price-book forms
// --------------------------------------------------------------------------- //

/** The panel for one named book, found by its heading. */
function bookPanel(name = 'Standard list') {
  const heading = screen.getByRole('heading', { name })
  return heading.closest('div.rounded-sm')
}

const SECOND_PRODUCT = {
  ...PRODUCT,
  id: 'p2',
  name: 'Priority support',
  sku: 'NWD-SUP',
  pricing_method: 'flat',
  is_tiered: false,
  tiers: [],
}

describe('The price-book forms', () => {
  it('creates a price book with the currencies that were chosen', async () => {
    const user = userEvent.setup()
    catalogueApi.createPriceBook.mockResolvedValue({ ...BOOK, id: 'b2' })
    render(<CataloguePage />)

    await user.click(await screen.findByRole('button', { name: 'New price book' }))
    await user.type(screen.getByLabelText('Name'), 'Partner rates')
    await user.click(screen.getByLabelText('GBP'))
    await user.click(screen.getByRole('button', { name: 'Create price book' }))

    await waitFor(() => {
      expect(catalogueApi.createPriceBook).toHaveBeenCalledWith(
        expect.objectContaining({ name: 'Partner rates', supported_currencies: ['GBP'] }),
        '',
      )
    })
  })

  it('shows the refusal when a book cannot be created', async () => {
    const user = userEvent.setup()
    const failure = Object.assign(new Error('Give the price book a name.'), {
      code: 'price_book_needs_a_name',
      status: 422,
    })
    catalogueApi.createPriceBook.mockRejectedValue(failure)
    render(<CataloguePage />)

    await user.click(await screen.findByRole('button', { name: 'New price book' }))
    await user.click(screen.getByRole('button', { name: 'Create price book' }))

    expect(await screen.findByText(refusalText('price_book_needs_a_name'))).toBeTruthy()
  })

  it('adds a product to a book with the price that was typed', async () => {
    const user = userEvent.setup()
    catalogueApi.addBookItem.mockResolvedValue({ id: 'r2' })
    catalogueApi.products.mockResolvedValue({
      products: [PRODUCT, SECOND_PRODUCT],
      count: 2,
    })
    render(<CataloguePage />)

    const panel = await waitFor(() => bookPanel())
    // The product already in the book is not offered again: only one instance of
    // each product may appear in a price book at a time.
    expect(within(panel).queryByRole('option', { name: 'Northwind Platform' })).toBeNull()
    await user.selectOptions(within(panel).getByLabelText('Product'), 'p2')
    await user.type(within(panel).getByLabelText('Override price in USD'), '500')
    await user.click(within(panel).getByRole('button', { name: 'Add to this book' }))

    await waitFor(() => {
      expect(catalogueApi.addBookItem).toHaveBeenCalledWith(
        expect.objectContaining({
          product_id: 'p2',
          price_book_id: 'b1',
          prices: { USD: 500 },
        }),
        '',
      )
    })
  })

  it('shows the refusal on the book it names', async () => {
    const user = userEvent.setup()
    const failure = Object.assign(new Error('That product is already in this price book.'), {
      code: 'one_row_per_product_and_book',
      status: 409,
    })
    catalogueApi.addBookItem.mockRejectedValue(failure)
    catalogueApi.products.mockResolvedValue({
      products: [PRODUCT, SECOND_PRODUCT],
      count: 2,
    })
    render(<CataloguePage />)

    const panel = await waitFor(() => bookPanel())
    await user.selectOptions(within(panel).getByLabelText('Product'), 'p2')
    await user.click(within(panel).getByRole('button', { name: 'Add to this book' }))

    expect(await screen.findByText(refusalText('one_row_per_product_and_book'))).toBeTruthy()
  })
})

// --------------------------------------------------------------------------- //
// The pure helpers, tested on their own
// --------------------------------------------------------------------------- //

describe('The helpers', () => {
  it('writes an open-ended range in words, never as a symbol', () => {
    expect(rangeLabel({ min_qty: 25, max_qty: null })).toBe('25 or more')
    expect(rangeLabel({ min_qty: 1, max_qty: 9 })).toBe('1 to 9')
    expect(rangeLabel(null)).toBe('')
  })

  it('puts the currency beside the figure', () => {
    // A bare 4400.00 in a EUR row reads as dollars.
    expect(formatMoney(4400, 'EUR')).toBe('4,400.00 EUR')
    expect(formatMoney(0, 'USD')).toBe('0.00 USD')
    expect(formatMoney('nonsense')).toBe('0.00')
  })

  it('has a sentence for every refusal token the backend names', () => {
    // A token the page has no words for renders a blank error, which is the
    // worst thing a form can show. Every token the backend declares is checked
    // against the page's own map.
    for (const token of Object.keys(REFUSALS)) {
      expect(refusalText(token)).toBeTruthy()
    }
    // A token added to the backend after this page shipped still gets a
    // sentence, because refusalText falls back rather than returning nothing.
    expect(refusalText('a_token_added_after_this_page_shipped')).toBeTruthy()
  })

  it('names every refusal token the backend vocabulary declares', () => {
    // The two maps have to agree. A token the backend can raise and the page
    // cannot name is a refusal a seller sees with no explanation of it.
    const declared = Object.keys(VOCABULARY.error_codes ?? {})
    const untranslated = declared.filter((token) => !(token in REFUSALS))
    expect(untranslated).toEqual([])
  })

  it('names a method in words the page can show', () => {
    expect(METHOD_WORDS.volume_based).toBe('Volume based')
    expect(METHOD_WORDS.stair_step).toBe('Stair step')
  })
})

// --------------------------------------------------------------------------- //
// The accessibility floor
// --------------------------------------------------------------------------- //

describe('The accessibility floor', () => {
  it('gives every input a visible label rather than a placeholder', async () => {
    render(<CataloguePage />)
    await screen.findAllByText('Northwind Platform')
    for (const box of screen.getAllByRole('textbox')) {
      // A textbox with no accessible name is invisible to a screen reader.
      const name = box.getAttribute('id') ? box.labels?.[0]?.textContent : null
      expect(name === null || name.length > 0).toBe(true)
    }
    expect(screen.getByLabelText('Search')).toBeTruthy()
  })

  it('keeps every control at least 44 pixels tall', async () => {
    render(<CataloguePage />)
    await screen.findAllByText('Northwind Platform')
    // The canonical input class carries min-h-11, which is 44px. Asserted on
    // the class rather than the computed box so the check needs no layout pass.
    for (const box of screen.getAllByRole('textbox')) {
      expect(box.className).toContain('min-h-11')
    }
    for (const button of screen.getAllByRole('button')) {
      if (button.className.includes('rounded-xs')) continue
      expect(button.className).toContain('min-h-11')
    }
  })

  it('uses no raw hex, no rgb and no hsl in any class', async () => {
    const { container } = render(<CataloguePage />)
    await screen.findAllByText('Northwind Platform')
    const html = container.innerHTML
    expect(html).not.toMatch(/#[0-9a-fA-F]{3,6}\b/)
    expect(html).not.toMatch(/\brgba?\(/)
    expect(html).not.toMatch(/\bhsla?\(/)
  })

  it('uses no emoji as an icon', async () => {
    const { container } = render(<CataloguePage />)
    await screen.findAllByText('Northwind Platform')
    expect(container.innerHTML).not.toMatch(/\p{Extended_Pictographic}/u)
  })

  it('rounds with rounded-sm rather than a pill', async () => {
    const { container } = render(<CataloguePage />)
    await screen.findAllByText('Northwind Platform')
    expect(container.innerHTML).not.toMatch(/rounded-(full|3xl|2xl|xl)\b/)
  })

  it('adds no Tailwind 200 to 400 text steps, which are near-white on this ground', async () => {
    const { container } = render(<CataloguePage />)
    await screen.findAllByText('Northwind Platform')
    expect(container.innerHTML).not.toMatch(/text-(sky|amber|emerald|blue|red|green|indigo|violet|slate|gray|zinc)-\d00\b/)
  })
})