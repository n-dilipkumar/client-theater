/**
 * The helpers the quoting page formats with (WF-086).
 *
 * These are pure and two of them carry decisions, so they are tested on their own
 * rather than only through the page.
 *
 * `formatMoney` matters most. A quote's figures come from the server already
 * rounded, and this must not round them a second time or re-round a value the
 * server sent as a string. A currency code beside the number matters too: a bare
 * "4,200.00" in a EUR quote reads as dollars, and the seller who checks it against
 * the deal is the person who catches the mistake.
 *
 * `conflictText` matters because it is the only place the page turns a stable
 * server token into English. A token with no sentence would print the raw
 * identifier at the user, which is the failure this file exists to prevent.
 */

import { describe, expect, it } from 'vitest'

import {
  CONTRACT_VALUE_KEY,
  TOTAL_ROWS,
  conflictText,
  formatMoney,
} from './api.js'

describe('formatMoney', () => {
  it('shows two decimals and a thousands separator', () => {
    expect(formatMoney(4200)).toBe('4,200.00')
    expect(formatMoney(0)).toBe('0.00')
    expect(formatMoney(15300.5)).toBe('15,300.50')
  })

  it('puts the currency beside the number, because a bare figure reads as dollars', () => {
    expect(formatMoney(4200, 'EUR')).toBe('4,200.00 EUR')
  })

  it('reads a figure the server sent as a string rather than showing NaN', () => {
    expect(formatMoney('4200')).toBe('4,200.00')
    expect(formatMoney('4200', 'GBP')).toBe('4,200.00 GBP')
  })

  it('treats an absent or unreadable figure as zero rather than printing NaN', () => {
    expect(formatMoney(null)).toBe('0.00')
    expect(formatMoney(undefined)).toBe('0.00')
    expect(formatMoney('not a number')).toBe('0.00')
  })

  it('does not round a value the server already rounded', () => {
    // 2.675 arrives as 2.68 from the server. Re-rounding here as a float would
    // turn it into 2.67, and the column would stop adding up to the total.
    expect(formatMoney(2.68)).toBe('2.68')
    expect(formatMoney(0.1 + 0.2)).toBe('0.30')
  })
})

describe('conflictText', () => {
  it('turns each token the backend serves into a sentence a seller can act on', () => {
    for (const reason of [
      'custom_module_not_api_authorable',
      'custom_module_needs_ui_provenance',
      'module_key_unknown',
      'already_published',
      'quote_expired',
      'quote_is_empty',
      'quote_frozen',
      'deal_not_found',
    ]) {
      const text = conflictText(reason)
      expect(text, reason).toBeTruthy()
      expect(text, reason).not.toBe(reason)
    }
  })

  it('says the module rule in terms of the editor, which is where the work goes', () => {
    expect(conflictText('custom_module_not_api_authorable')).toMatch(/editor/)
  })

  it('tells a seller what to do about an expired quote rather than only that it expired', () => {
    expect(conflictText('quote_expired')).toMatch(/Extend the date/)
  })

  it('falls back to a neutral sentence for a token it has never seen', () => {
    // Not an empty string and not the raw token. A new server reason must not put
    // an identifier on screen.
    expect(conflictText('a_reason_added_later')).toBe('That change was refused.')
    expect(conflictText(undefined)).toBe('That change was refused.')
  })
})

describe('the totals panel', () => {
  it('names the contract value key the publish writes from', () => {
    expect(CONTRACT_VALUE_KEY).toBe('total_contract_value')
  })

  it('lists every figure the server sends except the contract value, which leads', () => {
    expect(TOTAL_ROWS.map((row) => row.key)).toEqual([
      'subtotal',
      'discount',
      'tax',
      'total',
      'future_payments',
    ])
  })

  it('gives every row a hint, because a figure the page cannot explain is one the seller cannot check', () => {
    for (const row of TOTAL_ROWS) {
      expect(row.label, row.key).toBeTruthy()
      expect(row.hint, row.key).toBeTruthy()
    }
  })

  it('says the tax is charged after the discount, which is the rule chosen', () => {
    const tax = TOTAL_ROWS.find((row) => row.key === 'tax')
    expect(tax.hint).toMatch(/after its discount/)
  })
})
