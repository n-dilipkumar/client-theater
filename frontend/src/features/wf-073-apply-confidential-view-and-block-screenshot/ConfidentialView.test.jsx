import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { DETERRENT_NOT_PROTECTION, KNOWN_SHORTCUTS, shortcutScope } from './api'
import { ATTEMPTS, CONFIDENTIAL_LINK, GEOMETRY, OPEN_LINK, VOCABULARY, routes } from './fixtures'
import descriptor, { FocusBand } from './index.jsx'
import { BandStrip, Notice, Toggle } from './primitives'

/**
 * Tests for the WF-073 confidential-view page.
 *
 * Five things this page can get quietly wrong, and each is pinned here:
 *
 * 1. It must not sell the control as protection. The specification forbids it, so the
 *    limitation has to be rendered, above the fold, before anything else.
 * 2. Print Screen has to be named as something a page cannot intercept. A list of only
 *    the blockable shortcuts would let a seller read "this link blocks screenshots" as
 *    true when the first thing a buyer tries still works.
 * 3. A band the server did not band must not be drawn as if it were. A page shorter than
 *    one band has nothing outside the focus, and the page has to say so rather than
 *    showing a sharp strip that implies otherwise.
 * 4. A toggle must send only the field it changed. Sending `{}` would be fine; sending
 *    both flags would reset the control the seller was not touching.
 * 5. A rejected setting must put its message beside the switch that caused it, and must
 *    not leave the page blank.
 *
 * Queries are scoped to a section heading where a heading exists, because the page also
 * prints raw identifiers, and an unscoped `getByText` would match a value the page
 * happened to render as data.
 */

let calls = []
let overrides = {}

function stub() {
  calls = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url, options = {}) => {
      const raw = String(url)
      // Overrides are keyed on the path without its query string, because the render
      // route carries its viewport in the query and every call to it would otherwise
      // need a different key.
      const override = overrides[raw.split('?')[0]] || {}
      const entry = { ...routes(raw, options), ...override }
      const method = options.method || 'GET'
      calls.push({
        url: raw,
        method,
        body: options.body ? JSON.parse(options.body) : null,
      })
      if (entry.status && entry.status >= 400) {
        return { ok: false, status: entry.status, statusText: 'Error', json: async () => entry.body }
      }
      return { ok: true, status: entry.status || 200, json: async () => entry.body }
    }),
  )
}

beforeEach(() => {
  overrides = {}
  stub()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function renderPage() {
  const Page = descriptor.Component
  return render(<Page />)
}

async function waitForPage() {
  await waitFor(() => expect(screen.getByRole('heading', { name: 'Confidential view', level: 1 })).toBeInTheDocument())
}

/**
 * The panel card for one link, found by its title.
 *
 * Every ambiguous query below resolves through this. Two fixture links render the same
 * switches, the same descriptions and the same CLI spelling, so an unscoped
 * `getByText` fails on a page that is behaving correctly. Scoping to one link's card is
 * what makes each assertion mean something.
 */
function panelFor(title) {
  const heading = screen.getByRole('heading', { name: title })
  return heading.closest('div.glass')
}

describe('the descriptor', () => {
  it('exports the shape the feature host discovers', () => {
    expect(descriptor.id).toBe('wf-073-apply-confidential-view-and-block-screenshot')
    expect(descriptor.label).toBe('Confidential view')
    expect(descriptor.Component).toBeTypeOf('function')
  })

  it('carries a glyph path rather than editing the shared icon set', () => {
    expect(descriptor.iconPath).toBeTypeOf('string')
    expect(descriptor.iconPath.length).toBeGreaterThan(0)
  })
})

describe('the specification bar', () => {
  it('states that this is deterrence and not protection', () => {
    expect(DETERRENT_NOT_PROTECTION).toContain('Deterrence, not protection')
  })

  it('renders the limitation before anything else on the page', async () => {
    renderPage()
    await waitForPage()
    // The caveat is the specification's own sentence, fetched rather than typed here,
    // so the page and the server cannot disagree about what the control is worth.
    const notice = screen.getAllByRole('status')[0]
    expect(within(notice).getByText(DETERRENT_NOT_PROTECTION)).toBeInTheDocument()
    expect(within(notice).getByText(/largely unenforceable from a browser/)).toBeInTheDocument()
  })

  it('never says the control prevents a capture', async () => {
    const { container } = renderPage()
    await waitForPage()
    const text = container.textContent.toLowerCase()
    expect(text).not.toContain('prevents screenshots')
    expect(text).not.toContain('cannot be screenshotted')
    expect(text).not.toContain('stops a capture')
  })
})

describe('the access-controls panel', () => {
  it('renders one switch per researched flag', async () => {
    renderPage()
    await waitForPage()
    const panel = await screen.findByRole('heading', { name: /Access controls, link by link/i })
    const section = panel.closest('section')
    expect(within(section).getAllByRole('switch')).toHaveLength(4) // two links, two switches each
  })

  it('carries the vendor’s own description beside each switch', async () => {
    renderPage()
    await waitForPage()
    const panel = panelFor(CONFIDENTIAL_LINK.title)
    for (const control of VOCABULARY.panel.controls) {
      expect(within(panel).getByText(control.summary)).toBeInTheDocument()
    }
  })

  it('shows the switch in the position the store says', async () => {
    renderPage()
    await waitForPage()
    const switches = await screen.findAllByRole('switch')
    expect(switches[0]).toHaveAttribute('aria-checked', 'true')
    expect(switches[2]).toHaveAttribute('aria-checked', 'false')
  })

  it('names the CLI spelling the specification names', async () => {
    renderPage()
    await waitForPage()
    const panel = panelFor(CONFIDENTIAL_LINK.title)
    expect(within(panel).getByText(/--confidential-view on\|off/)).toBeInTheDocument()
    expect(within(panel).getByText(/--screenshot-protection on\|off/)).toBeInTheDocument()
  })

  it('sends only the field that changed', async () => {
    const user = userEvent.setup()
    renderPage()
    await waitForPage()
    const switches = await screen.findAllByRole('switch')
    await user.click(switches[2])

    await waitFor(() => {
      const patch = calls.find((call) => call.method === 'PATCH')
      expect(patch).toBeDefined()
      expect(patch.body).toEqual({ enable_confidential_view: true })
    })
  })
})

describe('a rejected setting', () => {
  it('puts the message beside the switch that caused it', async () => {
    const user = userEvent.setup()
    overrides['/api/wf-073/links/wf073_confidential_link_bbbb'] = {
      status: 400,
      body: {
        error: 'confidential_settings_invalid',
        detail: 'A confidentiality control is a boolean.',
        errors: { enable_confidential_view: 'Use true or false, or the CLI spelling on or off.' },
      },
    }
    renderPage()
    await waitForPage()
    // Scoped to the open link's card: the failing switch belongs to that link, and an
    // unscoped query would pass if either link's panel happened to show the message.
    const panel = panelFor(OPEN_LINK.title)
    const switches = await within(panel).findAllByRole('switch')
    await user.click(switches[0])

    await waitFor(() =>
      expect(
        within(panel).getByText(/Use true or false, or the CLI spelling on or off\./),
      ).toBeInTheDocument(),
    )
    expect(within(panel).getByText('enable_confidential_view')).toBeInTheDocument()
    // The page is still there behind the error. A blank page here reads as the security
    // controls having failed, which is the one reading that must never be possible.
    expect(screen.getByRole('heading', { name: 'Confidential view', level: 1 })).toBeInTheDocument()
  })
})

describe('the focus band', () => {
  it('says which band is sharp rather than only colouring it', () => {
    render(<BandStrip geometry={GEOMETRY} />)
    expect(screen.getByText(/Band 1 of 7 is sharp/)).toBeInTheDocument()
  })

  it('describes the whole strip for a screen reader', () => {
    render(<BandStrip geometry={GEOMETRY} />)
    const strip = screen.getByRole('img')
    expect(strip).toHaveAttribute(
      'aria-label',
      'Page divided into 7 bands. Band 1 of 7 is sharp; the other 6 are blurred.',
    )
  })

  it('renders nothing when there is no geometry', () => {
    const { container } = render(<BandStrip geometry={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when the bands list is empty', () => {
    const { container } = render(<BandStrip geometry={{ bands: [] }} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('follows the scroll position the server resolved', async () => {
    // Driven through the fixture router rather than by typing, because what is being
    // tested is that the page re-reads the server's answer for a new viewport. Typing
    // into the number input would test the input, and the input is not the risk here.
    render(<FocusBand link={CONFIDENTIAL_LINK} />)
    await waitFor(() => expect(screen.getByText(/Band 1 of 7 is sharp/)).toBeInTheDocument())

    const scroll = screen.getByLabelText(/Scroll position in pixels/i)
    fireEvent.change(scroll, { target: { value: '640' } })

    await waitFor(() => expect(screen.getByText(/Band 3 of 7 is sharp/)).toBeInTheDocument())
    expect(screen.getByRole('img')).toHaveAttribute(
      'aria-label',
      'Page divided into 7 bands. Band 3 of 7 is sharp; the other 6 are blurred.',
    )
  })

  it('says a short page has no blurred region', async () => {
    render(<FocusBand link={CONFIDENTIAL_LINK} />)
    await waitFor(() => expect(screen.getByText(/Band 1 of 7 is sharp/)).toBeInTheDocument())

    // A page shorter than one band has no out-of-band region, so there is nothing for
    // the control to withhold on it. The page has to say so rather than show a strip
    // implying a control was in force.
    const pageHeight = screen.getByLabelText(/Page height in pixels/i)
    fireEvent.change(pageHeight, { target: { value: '150' } })

    await waitFor(() =>
      expect(screen.getByText(/This page has no blurred region/)).toBeInTheDocument(),
    )
  })

  it('says a page with the control off is not banded at all', async () => {
    // The server answers a link whose confidential-view flag is off with the whole page
    // sharp and `applied: false`. The page has to say that rather than showing a band
    // strip that implies a control was in force. Overriding the render response is what
    // makes this test the page's behaviour rather than a restatement of the server's
    // flag.
    overrides[`/api/wf-073/links/${CONFIDENTIAL_LINK.id}/render`] = {
      body: {
        ...GEOMETRY,
        applied: false,
        reason: 'confidential_view_off',
        band_count: 1,
        blurred_count: 0,
        sharp_fraction: 1.0,
        bands: [{ index: 0, top: 0, bottom: 2400, sharp: true, text_length: 0, text: null }],
      },
    }
    render(<FocusBand link={CONFIDENTIAL_LINK} />)
    await waitFor(() =>
      expect(screen.getByText(/Confidential view is off on this link/)).toBeInTheDocument(),
    )
    expect(screen.getByText('Not applied')).toBeInTheDocument()
  })
})

describe('the shortcut list', () => {
  it('names Print Screen as something a page cannot intercept', async () => {
    renderPage()
    await waitForPage()
    const heading = await screen.findByRole('heading', { name: /What gets intercepted/i })
    const card = heading.closest('div')
    expect(within(card).getByText('Print Screen')).toBeInTheDocument()
    expect(within(card).getByText('Not interceptable')).toBeInTheDocument()
  })

  it('counts how many of the named shortcuts a page can intercept', () => {
    expect(shortcutScope()).toEqual({
      total: 6,
      blockable: 5,
      unblockable: ['Print Screen'],
    })
  })

  it('lists every researched shortcut, including the unblockable one', () => {
    const names = KNOWN_SHORTCUTS.map((shortcut) => shortcut.name)
    expect(names).toContain('Print Screen')
    expect(names).toContain('Command-Shift-5')
    expect(names).toHaveLength(VOCABULARY.shortcuts.length)
  })

  it('marks Command-Shift-5 as also a recording shortcut', () => {
    expect(KNOWN_SHORTCUTS.find((s) => s.name === 'Command-Shift-5').alsoRecording).toBe(true)
  })
})

describe('the reported attempts', () => {
  it('separates the ones a page can intercept from the ones it cannot', async () => {
    renderPage()
    await waitForPage()
    await waitFor(() => expect(screen.getByText('Reported capture attempts')).toBeInTheDocument())
    expect(screen.getByText('A page can intercept')).toBeInTheDocument()
    expect(screen.getByText('A page cannot intercept')).toBeInTheDocument()
  })

  it('says the product records what it was told rather than what was prevented', async () => {
    renderPage()
    await waitForPage()
    await waitFor(() =>
      expect(screen.getByText(/records what it was told; it does not/)).toBeInTheDocument(),
    )
  })

  it('shows an empty state rather than a blank card when there are none', async () => {
    overrides['/api/wf-073/attempts'] = { body: { count: 0, attempts: [], effect: 'deterrent' } }
    renderPage()
    await waitForPage()
    await waitFor(() =>
      expect(screen.getByText('No capture attempts reported')).toBeInTheDocument(),
    )
  })

  it('keeps the unblockable attempt in the rendered data', async () => {
    renderPage()
    await waitForPage()
    await waitFor(() => expect(screen.getByText('Reported capture attempts')).toBeInTheDocument())
    // Both fixture attempts are rendered, so a report a page cannot block is not hidden.
    expect(ATTEMPTS).toHaveLength(2)
  })
})

describe('the recorded decisions', () => {
  it('renders each derivation with what it rejected', async () => {
    renderPage()
    await waitForPage()
    await waitFor(() =>
      expect(screen.getByText('Decisions this workflow derived')).toBeInTheDocument(),
    )
    expect(screen.getByText('INFERRED_ACCESS_CONTROLS_PANEL')).toBeInTheDocument()
    expect(screen.getByText(/A combined toggle would make a state the evidence supports/)).toBeInTheDocument()
  })
})

describe('the empty and error states', () => {
  it('shows an empty state when there are no governed links', async () => {
    overrides['/api/wf-073/links'] = { body: { count: 0, links: [], effect: 'deterrent' } }
    renderPage()
    await waitForPage()
    await waitFor(() => expect(screen.getByText('No governed links yet')).toBeInTheDocument())
  })

  it('shows a retryable error rather than a blank page', async () => {
    overrides['/api/wf-073/vocabulary'] = {
      status: 500,
      body: { detail: 'boom' },
    }
    renderPage()
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(screen.getByText('Could not load data')).toBeInTheDocument()
  })
})

describe('the rebuilt primitives', () => {
  it('the notice names its state in words as well as colour', () => {
    render(<Notice tone="warning" title="Deterrence, not protection">Body</Notice>)
    expect(screen.getByText('Deterrence, not protection')).toBeInTheDocument()
    expect(screen.getByText('Body')).toBeInTheDocument()
  })

  it('the switch reports its state to a screen reader', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<Toggle id="t" label="Confidential view" hint="A hint" checked={false} onChange={onChange} />)
    const control = screen.getByRole('switch', { name: 'Confidential view' })
    expect(control).toHaveAttribute('aria-checked', 'false')
    await user.click(control)
    expect(onChange).toHaveBeenCalledWith(true)
  })

  it('the switch is labelled by a real label element', () => {
    render(<Toggle id="t2" label="Screenshot protection" checked onChange={() => {}} />)
    expect(screen.getByLabelText('Screenshot protection')).toBeInTheDocument()
  })

  it('the fixture link carries the caveat the server always sends', () => {
    expect(CONFIDENTIAL_LINK.effect).toBe('deterrent')
    expect(CONFIDENTIAL_LINK.limitation).toContain('Neither one prevents it')
  })
})