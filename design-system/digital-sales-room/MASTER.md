# Design System Master File

> **SUPERSEDED. Do not implement from this file.**
>
> This document was generated on 2026-09-26 for a **dark operations dashboard**
> theme: dark ground `#0f172a`, green `#22c55e` accent, Fira Sans / Fira Code,
> glassmorphism, 8px and 12px radii, drop-shadowed cards.
>
> The product was redesigned on 2026-10-01 to a **light corporate** theme. Every
> colour, font, and radius below is stale. Implementing from it produces pages
> that do not match the other forty-nine features.
>
> **Read [`docs/DESIGN-SYSTEM.md`](../../docs/DESIGN-SYSTEM.md) instead.** It is
> the maintained description of the shipped system, written for agents.
>
> **The enforceable source is [`frontend/src/index.css`](../../frontend/src/index.css).**
> If this file and `index.css` disagree, `index.css` is correct.

---

## What changed

| | Was (stale) | Now (authoritative) |
|---|---|---|
| Ground | `#0F172A` dark | `#fbfcfd` cool near-white |
| Surface | `#1B2336` | `#ffffff` |
| Accent | `#22C55E` green | `#10506f` steel blue |
| Mode | `color-scheme: dark` | `color-scheme: light` |
| Display font | Fira Code | Schibsted Grotesk |
| Body font | Fira Sans | Geist |
| Mono font | Fira Code | Geist Mono |
| Card radius | 12px | 2px |
| Button radius | 8px | 2px |
| Card elevation | `--shadow-md` drop shadow | 1px hairline border |
| Status text | `sky-300`, `amber-300` | remapped light steps, AA-passing |
| Effects | glassmorphism | none; hairline elevation |

Density is unchanged at the Tailwind default `0.25rem` base, and the touch target
floor, focus ring, and reduced-motion behaviour all still hold.

---

## Still true

The accessibility floor has not changed and is still enforced:

- No emoji as icons. Use `<Icon>` from `@/components/ui`.
- `cursor: pointer` on all clickable elements.
- Transitions of 150-300ms.
- 4.5:1 minimum contrast for body text.
- Visible focus states, never removed.
- `prefers-reduced-motion` respected.
- No layout-shifting hovers.
- Check 375px, 768px, 1024px, 1440px.
- No content hidden behind fixed navbars.
- No horizontal scroll on mobile.

## And the two product-level bans

- ❌ **Slow updates.** Rooms are read by buyers on their own time.
- ❌ **No automation.** Agents are the point; the mutual action plan is the spine.
