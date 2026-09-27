# WF-013 — Technical Design: Personalise room content with conditional rules

- **Ticket:** WF-013
- **Workflow:** [`WF-013.md`](./WF-013.md)
- **Research source:** `docs/research/raw/room-experience.md` §13
- **Branch:** `feature/WF-013-personalise-room-content-with-conditional-rules`

This document separates three things the research blurs together: what is **sourced**
from a primary source, what is a **design inference** we chose, and what is
**deliberately divergent** from the researched vendor.

> **Ported.** The decisions below are unchanged by the move onto the plugin host.
> Two things in §5 are not, and are called out here rather than rewritten, because
> this document is the record of what the branch decided:
>
> * **The paths are now under a prefix the feature owns.** `/api/rules/catalog` is
>   `/api/wf-013/catalog` and `/api/rooms/{room}/...` is
>   `/api/wf-013/rooms/{room}/...`. The branch registered those on the shared
>   `app`; a feature that claims core vocabulary is either a collision or dead
>   code, and the branch was never merged.
> * **The "no bypass" guarantee is only partly carried.** Validating a `rule` on
>   the generic `/api/records/{collection}` routes required editing `dsr/api.py`,
>   which a feature must not do. `guard_rule_payload` is still exported from the
>   feature module and still guards every route the feature owns; the generic
>   record routes remain the open gap, recorded as a strict `xfail` in
>   `backend/tests/test_wf013.py`.
>
> Everything in §1 (the evidence ledger), §2 (the two Jev-gated decisions), §3
> (the data model), §4 (the rule semantics), §6 (the frontend) and §7 (what was
> deliberately not built) stands exactly as researched. The domain module
> `dsr/rules.py` was taken over unchanged.

---

## 1. Evidence ledger

### Sourced (quoted from primary sources)

| # | Behaviour | Source |
|---|-----------|--------|
| S1 | A block's menu offers **Add rules**; **+ Add condition** opens a **Show block if** screen | `help.qwilr.com/article/849-conditional-content-with-qwilr-templates` |
| S2 | A rule is a set of conditions `{variable, category, modifier, value}` | same |
| S3 | **Text:** is / is not / contains / does not contain / starts with / ends with / includes / does not include | same |
| S4 | **Number:** equals / does not equal / is more than / is less than | same |
| S5 | **Any:** has no value / has any value | same |
| S6 | Conditions combine with **And** or **Or** | same |
| S7 | Up to **10 Or** conditions per block; **And** is unlimited | same |
| S8 | A condition with no Variable set is **incomplete**, is **ignored** at page creation, and **the block appears** (fail open) | same |
| S9 | An empty value and the string `"0"` are **both valid match values** | same |
| S10 | All block types accept rules **except the Accept Block** | same |
| S11 | Saved Blocks cannot have rules; saving a ruled block to the library "will not maintain the rule" | same |
| S12 | Variables come from Account Variables **or** a CRM integration, and appear in the conditions list identically | same |
| S13 | Evaluation is **generation-time**, not viewer-time | same |
| S14 | There is **no rules API**; rules are configured in the editor | `docs.qwilr.com/api-reference` |
| S15 | The adjacent documented endpoint is `POST /v1/pages` (create), not a rules call | `docs.qwilr.com/api-reference` |

### Design inference (unsourced — chosen by us)

| # | Inference | Rationale |
|---||-----------|-----------|
| D1 | Text comparisons are **case-insensitive by default**, overridable per condition via `case_sensitive` | S3 says only "is" / "contains". Sellers type "Australia" and "australia" interchangeably. Kept a field rather than a global flag so a team can change it with no migration. |
| D2 | A rule carries **one** joiner (`and` or `or`) for all its conditions | S6 gives a single And/Or modifier per rule. A flat mixed list (`a OR b AND c`) has no defined precedence in the source, so we do not invent one. |
| D3 | A condition referencing a variable that was **not supplied** at personalisation time does not match, unless the modifier is `has_no_value` | S5 defines `has no value`, which is meaningless unless an unsupplied variable is distinguishable from an empty one. S9 requires exactly that distinction. |
| D4 | The 11th Or condition is **rejected with an error**, not silently dropped | S7 states a limit but not the overflow behaviour. Silent loss is the failure mode S11 warns about elsewhere, so we fail loudly. |
| D5 | A non-numeric supplied value against a Number modifier **does not match** (it is not a 500) | Undefined in source; a bad CRM value should hide a block, not break a page. |
| D6 | Sub-block / "Rules widget" granularity is **not** built | §13 contradicts itself: the user flow and feature list mention a Rules widget, but the same section records a documented non-goal that widget-level conditional logic does not work "at this time". Building it would be inventing capability. See §7. |

### Deliberately divergent from the vendor

| # | Divergence | Rationale |
|---|------------|-----------|
| V1 | The Saved Block boundary **warns loudly** instead of silently dropping rules | S11 describes *silent* loss. AGENTS.md makes a complete audit trail the product's central promise; a silent drop is an unaudited change. We record `rules_dropped: true` and the dropped rule on the saved-block record and return a warning. |
| V2 | We expose an evaluation API at all | S14 says the vendor has none. This is an open-source implementation and the ticket asks for a schema-flexible API at every layer, so the capability needs a surface. |

### Known-unsourced (per AGENTS.md, flagged not resolved)

- The plan gate ("documented as Scale, extra cost") is asserted in the research prose with **no supporting quote** in §13's evidence list. We do not implement plan gating; it is recorded here as unverified.
- §13 states the Salesforce/HubSpot template-token articles were **not read**, so we make no claims about CRM token mapping. Our `variable` records carry a free-form `source` / `crm` field; nothing depends on the vendor's CRM behaviour.

---

## 2. Architecture decisions (Jev-gated)

Two `choose_approach` decisions were recorded in `orchestration/decisions/jev-audit.jsonl`.

### A1 — Where the rule lives → inline on the block

`jev-20260925T215723-6064-43694` returned **`uncertain`** (0.63 vs 0.32, confidence 0.45 < 0.75). Per AGENTS.md this was not overridden. Re-asked with narrower framing at `jev-20260925T215835-29524-15211`, which returned **`pass`** at confidence **0.99** for `create_plus_preview` and established:

1. **The rule is stored inline** in the block record's own `data.rule`. A rule belongs to exactly one block; a separate `rule` collection would add a read and a join on every evaluation and turn each edit into its own audit row, for no gain. The documented lossy boundary (S11) is implemented as its own `saved_block` collection.
2. **The personalisation API is create + preview** (see A2).

### A2 — Evaluation shape → create plus preview

`POST /api/rooms/{id}/personalise` **persists**; `POST /api/rooms/{id}/preview` **does not**. Both return the identical decision shape.

Why this is the evidence-led answer rather than a compromise:

- S15 shows the vendor's evaluation is bound to a **create** call. Persisting is consistent with the documented surface.
- This repo pins that **HTTP reads never write** (`test_reads_do_not_appear_in_the_audit_log`). A single always-persisting endpoint would make the first preview of a room stored state.
- A seller needs to try variable values repeatedly before committing; a buyer needs to know later which values produced which visible blocks. `create_plus_preview` serves both without breaking either contract.

---

## 3. Data model (schema-flexible, no migration)

All payloads are arbitrary JSON in `records.data`. **No new table, column, or migration.**

| Collection | Purpose | Key fields (all optional and open) |
|------------|---------|--------------------------------------|
| `block` | A content block in a room. Carries its rule inline. | `title`, `type`, `body`, `position`, `rule` |
| `variable` | A value that conditions can filter on. | `name`, `label`, `category`, `source` (`account`\|`crm`), `crm` |
| `personalisation` | A persisted generation-time decision. | `variables`, `decision`, `shown`, `hidden` |
| `saved_block` | A block copied to the library. **Rule-less by construction.** | `title`, `type`, `body`, `rules_dropped`, `dropped_rule`, `source_block_id` |

The envelope (`id`, `collection`, `room_id`, `revision`, `created_at`, `updated_at`,
`deleted_at`) is the only fixed vocabulary. `rule` is an ordinary JSON field, so
`find()` can filter on `rule.join` or any dotted path inside it through the
dynamic index, with no migration.

## 4. Rule semantics

```jsonc
{
  "join": "and",              // or "or"  (D2: one joiner per rule)
  "conditions": [
    { "variable": "region", "category": "text", "modifier": "is", "value": "Australia" }
  ]
}
```

### Completeness (S8, S9)

A condition is **incomplete** only when it names no variable. An empty string and
the literal `"0"` are **valid values** (S9), so neither makes a condition
incomplete. This is the subtlest rule in the ticket and is pinned by tests both
ways.

**Fail open (S8):** incomplete conditions are ignored, and if *every* condition is
incomplete the block is **shown**. A half-configured rule must never silently hide
buyer-facing content.

### Evaluation result

```jsonc
{
  "shown": true,
  "reason": "no_rule" | "all_incomplete" | "matched" | "unmatched",
  "join": "and",
  "conditions": [
    { "variable": "region", "modifier": "is", "value": "Australia",
      "status": "matched", "observed": "Australia" }
  ]
}
```

`status` is one of `matched`, `unmatched`, `incomplete`, `no_value` (the variable
was not supplied), `not_numeric` (D5). The trace is returned on **every** call so
a seller can see *why* a block hid, not just that it did.

## 5. API surface

| Method | Path | Writes? |
|--------|------|---------|
| `GET` | `/api/rules/catalog` | no — categories, modifiers, limits, reserved types |
| `GET` | `/api/rules/variables` | no — account + CRM variables merged (S12) |
| `POST` | `/api/rooms/{room}/blocks` | yes |
| `PUT` | `/api/rooms/{room}/blocks/{block}/rule` | yes — validated |
| `DELETE` | `/api/rooms/{room}/blocks/{block}/rule` | yes |
| `POST` | `/api/rooms/{room}/preview` | **no** |
| `POST` | `/api/rooms/{room}/personalise` | **yes** |
| `GET` | `/api/rooms/{room}/personalisations` | no |
| `POST` | `/api/rooms/{room}/blocks/{block}/save-to-library` | yes — warns (V1) |

**No bypass.** The generic `POST /api/records/{collection}` and
`PATCH /api/records/{collection}/{id}` routes validate a `rule` key whenever one is
present, so a rule cannot enter the store unvalidated through the fast path. All
writes go through `AuditedDatabase`, so every route above produces its audit row
in the same transaction as the change.

## 6. Frontend

New page `ConditionalRules.jsx`, routed at `#/rules`, following
`design-system/digital-sales-room/MASTER.md`:

- **Room selector** → blocks for that room, each with a rule-status badge.
- **"Add rules"** → the *Show block if* builder (S1): variable select, category,
  modifier, value, **+ Add condition**, a trash icon per condition, and a live
  `n / 10 Or` counter (S7).
- **Preview panel** → variable value inputs, then per-block shown / hidden with the
  full condition trace.
- Accept-type blocks disable the builder and say why (S10).

Accessibility floor, per the design system: 44px minimum touch targets, visible
focus rings (never removed), no emoji as icons, `cursor-pointer` on every
clickable element, 150–300ms transitions, `prefers-reduced-motion` honoured by the
global block in `index.css`.

## 7. Explicitly not built

- **Sub-block / widget-level rules** (D6). §13 contradicts itself; building it
  would assert capability the research marks as a documented non-goal.
- **Plan gating.** Asserted without a supporting quote.
- **CRM variable import.** The integration is out of scope; `variable` records
  with `source: "crm"` are accepted and listed, so an integration team can add
  them with no schema change.

## 8. Tests

`backend/tests/test_rules.py` — domain semantics: all 14 modifiers across the
three categories, empty-string and `"0"` as valid values, incomplete-condition
fail-open, And/Or, the 10-Or limit (10 passes, 11 rejected), unlimited And, the
Accept Block rejection, case sensitivity, and non-numeric values.

`backend/tests/test_rules_api.py` — the HTTP contract: catalog and variable
discovery, validated rule attach, the generic-API no-bypass guarantee, preview
writing nothing, personalise writing exactly one record and one audit row, the
save-to-library warning, and a schema-flexibility round trip.
