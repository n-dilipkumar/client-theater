/**
 * Fixtures for the WF-073 frontend tests.
 *
 * One place decides what this feature's API looks like, so a test and the stub it runs
 * against cannot drift. Every shape here is copied from a real response of the routes
 * in `backend/dsr/features/wf073_apply_confidential_view_and_block_screenshot.py`,
 * including the two fields - `effect` and `limitation` - that every response carries.
 * A fixture that dropped them would let a page render without the caveat and the test
 * would still pass, which is the one thing this workflow's tests exist to prevent.
 */

export const VOCABULARY = {
  flags: ['enable_confidential_view', 'enable_screenshot_protection'],
  defaults: { enable_confidential_view: false, enable_screenshot_protection: false },
  panel: {
    title: 'Link access controls',
    controls: [
      {
        field: 'enable_confidential_view',
        label: 'Confidential view',
        default: false,
        cli_flag: '--confidential-view',
        effect: 'reveal_band',
        summary:
          'Reveal only a narrow band of each page at a time; rest is blurred (anti-screenshot)',
      },
      {
        field: 'enable_screenshot_protection',
        label: 'Screenshot protection',
        default: false,
        cli_flag: '--screenshot-protection',
        effect: 'block_shortcuts',
        summary: 'Block common screenshot / screen-recording shortcuts while viewing',
      },
    ],
  },
  cli_flags: {
    enable_confidential_view: '--confidential-view',
    enable_screenshot_protection: '--screenshot-protection',
  },
  render: {
    chosen_shape: 'viewport_bands',
    shapes: ['viewport_bands', 'tile_shards'],
    band_fraction: 0.2,
    band_overlap: 0.3333,
    small_page_pixels: 200,
  },
  shortcuts: [
    { keys: ['meta', 'shift', '3'], action: 'capture_fullscreen', name: 'Command-Shift-3', blockable: true },
    { keys: ['meta', 'shift', '4'], action: 'capture_selection', name: 'Command-Shift-4', blockable: true },
    {
      keys: ['meta', 'shift', '5'],
      action: 'capture_toolbar',
      name: 'Command-Shift-5',
      blockable: true,
      also_recording: true,
    },
    {
      keys: ['ctrl', 'shift', 's'],
      action: 'capture_selection',
      name: 'Control-Shift-S',
      blockable: true,
    },
    { keys: ['meta', 'alt', 'r'], action: 'start_recording', name: 'Command-Alt-R', blockable: true },
    {
      keys: ['print_screen'],
      action: 'capture_fullscreen',
      name: 'Print Screen',
      blockable: false,
    },
  ],
  effect: 'deterrent',
  limitation:
    'Screenshot blocking is largely unenforceable from a browser. Both controls raise the cost of an ordinary capture. Neither one prevents it.',
  not_protection:
    'A determined capture is outside this product’s reach. The page reports what the viewer did, not what the viewer was able to see.',
}

export const CONFIDENTIAL_LINK = {
  id: 'wf073_confidential_link_aaaa',
  room_id: 'room_a',
  title: 'Northwind - mutual NDA',
  preset_id: null,
  controls: ['enable_confidential_view', 'enable_screenshot_protection'],
  flags: { enable_confidential_view: true, enable_screenshot_protection: true },
  panel_controls: VOCABULARY.panel.controls.map((control) => ({ ...control, on: true })),
  effect: 'deterrent',
  limitation: VOCABULARY.limitation,
}

export const OPEN_LINK = {
  id: 'wf073_confidential_link_bbbb',
  room_id: 'room_a',
  title: 'Kestrel - open, no controls',
  preset_id: null,
  controls: [],
  flags: { enable_confidential_view: false, enable_screenshot_protection: false },
  panel_controls: VOCABULARY.panel.controls.map((control) => ({ ...control, on: false })),
  effect: 'deterrent',
  limitation: VOCABULARY.limitation,
}

export const SUMMARY = {
  links: 2,
  presets: 1,
  confidential_view: 1,
  screenshot_protection: 1,
  both_controls: 1,
  no_controls: 1,
  attempts: 2,
  attempts_blockable: 1,
  attempts_unblockable: 1,
  by_shortcut: { 'Print Screen': 1, 'Command-Shift-5': 1 },
  effect: 'deterrent',
  limitation: VOCABULARY.limitation,
}

/** A page of 2400px, banded at one fifth with a third overlapping. */
export const GEOMETRY = {
  link_id: CONFIDENTIAL_LINK.id,
  page: 1,
  applied: true,
  reason: 'confidential_view_on',
  shape: 'viewport_bands',
  page_height: 2400,
  viewport_height: 800,
  viewport_top: 0,
  band_count: 7,
  bands: [
    { index: 0, top: 0, bottom: 480, sharp: true, shape: 'viewport_bands', text_length: 900, text: null },
    { index: 1, top: 320, bottom: 800, sharp: false, shape: 'viewport_bands', text_length: 0, text: null },
    { index: 2, top: 640, bottom: 1120, sharp: false, shape: 'viewport_bands', text_length: 0, text: null },
    { index: 3, top: 960, bottom: 1440, sharp: false, shape: 'viewport_bands', text_length: 0, text: null },
    { index: 4, top: 1280, bottom: 1760, sharp: false, shape: 'viewport_bands', text_length: 0, text: null },
    { index: 5, top: 1600, bottom: 2080, sharp: false, shape: 'viewport_bands', text_length: 0, text: null },
    { index: 6, top: 1920, bottom: 2400, sharp: false, shape: 'viewport_bands', text_length: 0, text: null },
  ],
  sharp_index: 0,
  sharp_top: 0,
  sharp_bottom: 480,
  sharp_fraction: 0.2,
  blurred_count: 6,
  effect: 'deterrent',
  limitation: VOCABULARY.limitation,
}

export const ATTEMPTS = [
  {
    id: 'wf073_capture_attempt_1',
    link_id: CONFIDENTIAL_LINK.id,
    shortcut: 'Command-Shift-5',
    action: 'capture_toolbar',
    outcome: 'blocked',
    blockable: true,
    at: '2026-10-03T14:00:00.000+00:00',
    effect: 'deterrent',
    limitation: VOCABULARY.limitation,
  },
  {
    id: 'wf073_capture_attempt_2',
    link_id: CONFIDENTIAL_LINK.id,
    shortcut: 'Print Screen',
    action: 'capture_fullscreen',
    outcome: 'reported',
    blockable: false,
    at: '2026-10-03T14:05:00.000+00:00',
    effect: 'deterrent',
    limitation: VOCABULARY.limitation,
  },
]

export const DECISIONS = {
  count: 2,
  decisions: [
    {
      id: 'INFERRED_ACCESS_CONTROLS_PANEL',
      question: 'The specification marks the link access-controls panel as inferred. What does this build put in it?',
      chosen: 'two_toggles',
      rejected_because: 'A combined toggle would make a state the evidence supports unreachable.',
    },
    {
      id: 'DERIVED_DELIVERY_SHAPE',
      question: 'The specification names two delivery shapes and chooses neither. Which is built?',
      chosen: 'viewport_bands',
      rejected_because: 'A document is read top to bottom, so a horizontal band follows the reading direction.',
    },
  ],
}

export const ROOMS = {
  records: [
    { id: 'room_a', data: { name: 'Northwind data room' } },
    { id: 'room_b', data: { name: 'Halcyon data room' } },
  ],
}

/**
 * The stub router. Keyed on this feature's own prefix plus the one core route the room
 * picker reads, so the page cannot accidentally satisfy a request from a different
 * feature's fixture.
 */
export function routes(url, options = {}) {
  const path = String(url).split('?')[0]
  const method = options.method || 'GET'

  if (path === '/api/wf-073/vocabulary') return { status: 200, body: VOCABULARY }
  if (path === '/api/wf-073/summary') return { status: 200, body: SUMMARY }
  if (path === '/api/wf-073/decisions') return { status: 200, body: DECISIONS }
  if (path === '/api/wf-073/links') return { status: 200, body: { count: 2, links: [CONFIDENTIAL_LINK, OPEN_LINK] } }
  if (path === '/api/wf-073/attempts') {
    return { status: 200, body: { count: 2, attempts: ATTEMPTS, effect: 'deterrent' } }
  }
  if (/^\/api\/wf-073\/links\/[^/]+\/render$/.test(path)) {
    const query = new URLSearchParams(String(url).split('?')[1] || '')
    const pageHeight = Number(query.get('page_height') || 2400)
    const viewportTop = Number(query.get('viewport_top') || 0)
    if (pageHeight < VOCABULARY.render.small_page_pixels) {
      // A page shorter than one band has no out-of-band region. The server says so in
      // `reason` rather than implying the control applied, and the page has to show it.
      return {
        status: 200,
        body: {
          ...GEOMETRY,
          page_height: pageHeight,
          band_count: 1,
          sharp_fraction: 1.0,
          blurred_count: 0,
          bands: [{ index: 0, top: 0, bottom: pageHeight, sharp: true, text_length: 120, text: null }],
        },
      }
    }
    const index = Math.min(Math.floor(viewportTop / 320), GEOMETRY.bands.length - 1)
    return {
      status: 200,
      body: {
        ...GEOMETRY,
        viewport_top: viewportTop,
        sharp_index: index,
        bands: GEOMETRY.bands.map((band, position) => ({ ...band, sharp: position === index })),
      },
    }
  }
  if (/^\/api\/wf-073\/links\/[^/]+$/.test(path) && method === 'PATCH') {
    return { status: 200, body: CONFIDENTIAL_LINK }
  }
  if (path === '/api/records/room') return { status: 200, body: ROOMS }

  return { status: 404, body: { detail: `no fixture for ${method} ${path}` } }
}