# Client Theater design system

**Read this before you write any page or feature.** It is short on purpose.

This product is built by many agents in parallel. The rules below are what stop
fifty features from shipping fifty different-looking pages. If you follow them,
your feature matches the other forty-nine without anyone reviewing your CSS.

Source of truth for values: `frontend/src/index.css`.
Prose for agents: this file. Prose for humans/designers:
`design-system/digital-sales-room/MASTER.md`.

---

## The one rule that matters

**Style with semantic tokens. Never with a raw hex value.**

```jsx
// correct
<div className="border border-border-subtle bg-surface text-foreground" />

// wrong, and it will not follow a theme change
<div style={{ background: '#ffffff', borderRadius: '8px' }} />
```

Because tokens live in one file, a palette change lands everywhere at once. If
you hardcode a colour you have opted out of that, permanently.

---

## Colour

Tokens are defined in `@theme` in `frontend/src/index.css`. These are the only
ones you should reach for.

| Token | Value | Use for |
|---|---|---|
| `background` | `#fbfcfd` | Page ground. Cool near-white, never warm. |
| `surface` / `card` | `#ffffff` | Cards, panels, inputs, sidebar. |
| `muted` | `#eef2f5` | Inset fills, progress tracks, hover grounds. |
| `muted-foreground` | `#5a6874` | Secondary text. Body copy must stay readable at 4.5:1. |
| `foreground` | `#0c1620` | Primary text. Off-black, never `#000`. |
| `primary` | `#10506f` | Hover/darker state of the accent. |
| `accent` | `#10506f` | **The only accent in the product.** CTAs, active nav, links. |
| `accent-soft` | `#e7eef4` | Tinted fills behind active states and icon chips. |
| `accent-bright` | `#2a7fae` | Charts and data series needing a second tone. |
| `border-subtle` | `#d8e0e6` | Every hairline. The primary separator. |
| `destructive` | `#a4262c` | Errors, delete, "before" values in a diff. |
| `success` | `#1f6f4a` | Pass, complete, healthy. |
| `warning` | `#8a5a12` | At risk, pending, restore. |
| `info` | `#1c5c86` | Neutral informational state, booleans in JSON. |

### Rules

- **One accent.** Do not introduce a second brand colour. If a chart needs more
  series, use `accent-bright` and the semantic status tokens.
- **No purple or violet.** Banned outright.
- **Elevation is a hairline, not a shadow.** Use `border border-border-subtle`.
  Do not add drop shadows to cards.
- Status colours are for status. Never use `destructive` decoratively.

### The sky/amber exception, and why

Forty-seven feature folders predate the light theme and use `text-sky-300` and
`text-amber-300` as text. Those steps are near-white in Tailwind's default
palette: correct on a dark ground, invisible on `#fbfcfd`. The light steps are
remapped in `index.css`, so existing usages now render dark enough to pass AA.

**Do not add new usages of Tailwind's 200-400 text steps.** They will read as
inconsistent on the light ground. Use the semantic tokens above.

---

## Shape

| Purpose | Class |
|---|---|
| Cards, inputs, buttons, panels | `rounded-sm` (2px) |
| Chips, badges | `rounded-xs` (1px) |
| Circles only | `rounded-full` |

Every radius from `sm` upward has been redefined to 2px in `index.css`, so
`rounded-lg` and `rounded-xl` inherited from older code also compute to 2px.
**Prefer `rounded-sm` in new code** so the intent is visible.

Why: near-square corners are the strongest single signal that this is enterprise
software. Rounded cards and pill buttons read as consumer.

---

## Type

| Role | Token | Use for |
|---|---|---|
| Display | `font-display` (Schibsted Grotesk) | Page `<h1>`, section headings. |
| Body | `font-sans` (Geist) | Everything else. |
| Mono | `font-mono` (Geist Mono) | IDs, collections, timestamps, counts, JSON, badges. |

Set in `frontend/index.html` via Google Fonts. Loaded synchronously with
`display=swap`.

**Mono is for machine values, not prose.** A collection name or a revision count
is mono. A sentence is not. Do not set a paragraph in mono.

If you want a heading in the display face and it inherits mono from an older
pattern, you do not need to fix it; mono headings are an accepted convention in
this product's data-dense pages.

### Scale

- Page `<h1>`: `text-2xl font-semibold`
- Section `<h2>`: `text-lg font-semibold`
- Card `<h3>`: `text-base font-semibold`
- Body: `text-sm` in cards, `text-[15px]` on the login page
- Secondary: `text-xs text-muted-foreground`
- Micro label: `text-[11px] uppercase tracking-[0.14em] text-muted-foreground`

---

## Layout

- Page shell: `max-w-[1400px] mx-auto px-4 py-6 sm:px-6 lg:px-8 lg:py-8`. Set in
  `App.jsx`. Do not add your own outer wrapper.
- Spacing scale: Tailwind default `0.25rem` base. Use `gap-4`, `gap-6`, not
  arbitrary pixel values.
- Vertical rhythm between sections: `space-y-6`.
- Grid over flex math. `grid gap-4 sm:grid-cols-2 lg:grid-cols-4`.
- Every multi-column layout must state its `< 768px` collapse. `grid-cols-1` by
  default, more columns at `sm:` or `lg:`.

---

## Components

Import from `@/components/ui`. Do not rebuild these locally; twelve features
each shipping their own modal means twelve different dialogs.

`Button` · `Card` · `StatCard` · `Badge` · `Field` · `Modal` · `Notice` ·
`Toggle` · `Checkbox` · `Spinner` · `ErrorNote` · `EmptyState` · `Icon` ·
`inputClass` · `useAsync` · `JsonView`

```jsx
import { Button, Card, Spinner, useAsync, inputClass } from '@/components/ui'
```

`inputClass` is the canonical input styling. Never hand-write input classes.

### Button variants

| Variant | When |
|---|---|
| `primary` | The one main action on a screen. Accent fill. |
| `secondary` | Default. Bordered surface. |
| `ghost` | Tertiary, low emphasis. |
| `danger` | Destructive, with confirmation nearby. |

One `primary` per screen. If a page has two, one of them is `secondary`.

### Icons

Use `<Icon name="..." />` from `@/components/ui`. The set is small and
deliberate. For a glyph that is not in the set, pass a path. Do not append to
the shared `PATHS` map, and never hand-roll an SVG path for an interface icon.

Every icon sits beside a text label or an `aria-label`. An icon alone is never
the only label.

---

## Accessibility floor

This is not optional and is checked in review.

- **44px minimum touch targets.** `min-h-11` on every control.
- **Never remove `:focus-visible`.** The audit log is used by keyboard only by
  some people. The ring is defined in `index.css` and must survive.
- **Labels are always visible.** Placeholder-as-label is banned. Use `Field`.
- **Contrast 4.5:1 minimum** for body text against its own background. Both
  `foreground` and `muted-foreground` pass on `background` and `surface`.
- **`prefers-reduced-motion` is honoured** globally in `index.css`. Do not add
  `animation` without checking it degrades.
- **Empty, loading, and error states are required.** Use `useAsync` and branch
  on `loading` / `error`. A feature without them is not finished.

---

## Things that are banned

- Purple, violet, neon, or gradient text.
- Pure `#000000` and pure `#ffffff` as text colours.
- Drop shadows on cards. Use a hairline.
- Pill or heavily-rounded buttons.
- Emoji as icons.
- Hardcoded hex, `rgb()`, or `hsl()` in a component.
- Tailwind's 200-400 text-colour steps as text.
- `Inter`, `Roboto`, or any face not in the type table above.
- A second accent colour "just for this chart".
- `window.addEventListener('scroll')` for scroll effects.

---

## Adding a page

Pages are discovered. Create the folder, export the descriptor, ship.

```jsx
import { apiRequest } from '@/lib/api'
import { Card, Spinner, useAsync } from '@/components/ui'

function MyPage() {
  const { data, loading, error, refetch } = useAsync(() => apiRequest('/wf-999/summary'), [])
  if (loading) return <Spinner label="Loading my page" />
  if (error) return <ErrorNote error={error} onRetry={refetch} />
  return <Card>{data.rooms} rooms</Card>
}

export default {
  id: 'wf-999-my-page',
  label: 'My page',
  icon: 'dashboard',
  order: 900,
  Component: MyPage,
}
```

It appears in the sidebar automatically, already sorted and already filterable.
You do not edit `App.jsx`.

---

## Before you open a PR

- [ ] No raw hex or `rgb()` in any component
- [ ] No Tailwind 200-400 text steps added
- [ ] `rounded-sm`, not `rounded-xl`
- [ ] Labels visible on every input, via `Field` and `inputClass`
- [ ] `min-h-11` on every control
- [ ] Loading, error, and empty states present
- [ ] No shared file edited (`App.jsx`, `components/ui.jsx`, `lib/api.js`,
      `index.css`, `MASTER.md`) unless the change is platform work
- [ ] Checked in both themes' worth of states: hover, focus, disabled, error
- [ ] Backend tests pass: `cd backend && ../.venv/Scripts/python -m pytest`

If you need a primitive that does not exist, build it inside your own feature
folder and say so in the PR description. The integrator promotes recurring ones
into `ui.jsx` once. Do not edit `ui.jsx` yourself.
