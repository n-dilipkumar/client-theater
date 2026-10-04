/**
 * The helpers the intent-alerting page formats with (WF-133).
 *
 * These are pure and they carry decisions, so they are tested on their own rather
 * than only through the page. The one that matters most is `dwellLabel`: the
 * research quotes 90 **seconds**, and a helper that renders that as "1m 30s" would
 * hide the single number the specification actually states.
 */

import { describe, expect, it } from 'vitest'

import { accountLabel, dwellLabel, dwellSentence, refusalMessage, thresholdSentence } from './api.js'

describe('dwellLabel', () => {
  it.each([
    [0, 'no dwell recorded'],
    [90, '90s'],
    [142, '142s'],
    [299, '299s'],
    [300, '5m 0s'],
    [400, '6m 40s'],
  ])('renders %i seconds as %s', (seconds, expected) => {
    expect(dwellLabel(seconds)).toBe(expected)
  })

  it('keeps the sourced threshold and a real reading in the same unit', () => {
    // The comparison a rep actually makes. If these two units ever diverge again,
    // the alert asks the reader to do arithmetic to check the rule that fired it.
    expect(dwellLabel(90)).toBe('90s')
    expect(dwellLabel(142)).toBe('142s')
  })
})

describe('dwellSentence', () => {
  it('carries both readings, because the threshold is one and the question is the other', () => {
    expect(
      dwellSentence({ longest_page_seconds: 142, window_total_seconds: 400, threshold_seconds: 90 })
    ).toBe('142s on the longest page, 6m 40s in the window')
  })

  it('survives an observation that measured nothing', () => {
    expect(dwellSentence({})).toBe('no dwell recorded on the longest page, no dwell recorded in the window')
  })
})

describe('thresholdSentence', () => {
  it('names the sourced threshold in the same unit as the reading', () => {
    expect(thresholdSentence({ threshold_seconds: 90 })).toBe('sourced threshold is 90s')
  })
})

describe('accountLabel', () => {
  it('prefers a name and falls back to the key rather than showing a blank', () => {
    expect(accountLabel({ company_name: 'Northwind Energy', company_key: 'nw' })).toBe(
      'Northwind Energy'
    )
    expect(accountLabel({ company_key: 'nw' })).toBe('nw')
    expect(accountLabel({})).toBe('Unknown account')
  })
})

describe('refusalMessage', () => {
  it('turns an unresolved account into the next action the seller can take', () => {
    const error = { status: 409, message: 'no opportunity is on file for it in this room' }
    expect(refusalMessage(error)).toMatch(/Import the deal first/)
  })

  it('turns an unknown company into the reason, not a bare 404', () => {
    const error = { status: 404, message: 'no identified company is on file under that key' }
    expect(refusalMessage(error)).toMatch(/visitor-identification workflow/)
  })

  it('explains that an unthresholded field was refused', () => {
    expect(refusalMessage({ status: 422, message: 'the observation carries field(s) email' })).toMatch(
      /no threshold reads/
    )
  })

  it('explains why a Slack handle cannot be a recipient here', () => {
    expect(refusalMessage({ status: 422, message: "'@dana' is not an email address" })).toMatch(
      /no Slack surface/
    )
  })

  it('explains why a dismissal needs a note', () => {
    expect(refusalMessage({ status: 422, message: 'a dismissed needs a note' })).toMatch(
      /which alerts were not worth a call/
    )
  })

  it('shows an unrecognised refusal verbatim rather than swallowing it', () => {
    expect(refusalMessage({ status: 500, message: 'the disk is on fire' })).toBe('the disk is on fire')
  })
})
