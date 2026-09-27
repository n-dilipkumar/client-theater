# WF-023 — Technical Design: Relate buyer engagement to CRM pipeline and close rate

- **Ticket:** WF-023
- **Workflow:** [`WF-023.md`](./WF-023.md)
- **Research source:** `docs/research/raw/analytics-intent.md` §8
- **Branch:** `n-dilipkumar/dsr-wf-023-build`
- **Prefix:** `/api/wf-023` · **Feature id:** `wf-023-relate-buyer-engagement-to-crm-pipelin`
- **Domain module:** `backend/dsr/salesimpact/` (package, named for this workflow)

This document separates three things the research blurs together: what is **sourced**
from a primary source, what is a **design inference** we chose, and what is
**deliberately divergent** from the researched vendor.

This is a **build, not a port.** The workflow had a finished research document and no
code. The research document is the specification, and the rules it fixes are landed
unchanged. Everything the research does *not* fix is listed in §1.2 and served at
runtime from `GET /api/wf-023/inferences`, so a reviewer can disagree with a named
decision instead of finding it in a diff.

---

## 1. Evidence ledger

### 1.1 Sourced (quoted from the research document's evidence list)

| # | Behaviour | Quote |
|---|-----------|-------|
| S1 | A workspace enters the report only when it is **typed `Sales`** *and* has a **CRM opportunity** attached | "The Sales Impact report pulls in any workspace designated as a 'Sales' type that has a CRM opportunity." |
| S2 | The report's tile set is exactly: **Total deals, Total pipeline touched, Active deals, Active pipeline, Closed won deals, Revenue (closed won revenue), Close rate, Days to close (average)** | "Sales Impact (synced to your CRM): Total deals, Total pipeline touched, Active deals, Active pipeline, Closed won deals, Revenue (closed won revenue), Close rate, Days to close (average)" |
| S3 | **Close rate = closed won ÷ (closed won + closed lost)** — the denominator contains *only* closed deals | "**Close rate** — How many workspaces with deals/opportunities that have been closed won, divided by the total (closed won + closed lost)." |
| S4 | The report joins CRM data to workspace data; it is not a workspace-only report | "This report combines your CRM data with Dock workspace data to show the impact Dock has on your pipeline and close rates." |
| S5 | Setting the workspace type is a **prerequisite the user performs first**, in Settings → the workspace's **Internal** tab | "To populate this report, remember to set the **workspace type** for your sales workspaces from the **Settings** in the workspace's **Internal** tab." |
| S6 | Buyer-engagement half of the report: **Views, actions, average buyers per workspace, most engaged buyers**, and views over time | "Buyer Engagement: Views, actions, and average buyers per workspace, Most engaged buyers." |
| S7 | Two further panels: **Deals Created Over Time** and **Deals By Owner** | workflow `user_flow` steps 4–5 |
| S8 | The report is filterable by **date range, CRM stage, owners, teams** | `user_flow` step 6 |
| S9 | Tiles have **drill-in** | `features_tools`: "metric tiles with drill-in" |
| S10 | The integration is a *precondition*, and its absence is not detected silently: a Sales room with no deal attached means the report **is missing data** | automations: "CRM integration must be on and deals attached or the report is incomplete — 'unless you are requiring reps attach a deal to each space, it's possible this report is missing data.'" |
| S11 | Stage and amount changes arrive by sync and keep the rollup fresh — a deal's stage and amount are mutable | automations: "Deal stage/amount sync keeps the rollup fresh." |
| S12 | The data is reachable from `GET /v1/deals`, `GET /v1/deals/{id}`, `GET /v1/accounts`, `GET /v1/workspaces`, with a **`properties`** selection parameter | `apis_hit` |
| S13 | The documented API constraints to design around are the **429 "Too many requests"** rate limit and the **`properties`** parameter | `extensibility` |
| S14 | A buyer with no invite appears **by their email** | research §1, the same `user`-object behaviour this ecosystem documents |

### 1.2 Design inference (unsourced — chosen by us, each served at `/inferences`)

| # | Inference | Rationale |
|---|-----------|-----------|
| D1 | **`Total pipeline touched`** = Σ amount over *every* in-scope deal; **`Active pipeline`** = Σ amount over the *open* subset | The two tiles only mean different things if one is the whole population and one is the open part. `Active deals` is the count of the same open subset, which is the reading that makes the four tiles mutually consistent. |
| D2 | **In-scope** means Sales-typed **and** deal-attached, and the report also returns the excluded workspaces **with the reason**, so a Sales room missing from the numbers is explainable | S1 is a two-part conjunction and S10 says a missing deal makes the report *incomplete* rather than merely small. A report that silently drops a room cannot honour S5, which asks the user to set the type as step one. |
| D3 | **An unclassifiable stage is a bucket, not a refusal.** It is counted, appears in the funnel as `unknown`, and is excluded from both arms of the close-rate fraction | Schema flexibility is a hard requirement: refusing to store a team's stage string is a migration by the back door. S3's denominator then means "closed won + closed lost", which is exactly what the quote says. |
| D4 | **`close_rate` is `null`, not `0.0`, when nothing has closed** | S3's fraction is 0/0. Reporting `0%` would assert every deal was lost, which is a different and wrong claim. |
| D5 | **`days_to_close` = mean(closed date − created date) over every *closed* deal**, and is `null` when no deal has closed | S2 gives the name and no formula. Pairing it with *Deals Created Over Time* (S7) fixes the start of the interval at the deal's own creation, which is the only start date the report already shows. Won + lost, because "days to close" is a cycle length and S3's denominator already treats both as closed. |
| D6 | **Money is summed in one currency at a time.** The report picks the currency carrying the most deals (ties broken lexicographically for determinism), reports it on every figure, and returns the full per-currency split plus a warning when deals span more than one | The research never mentions currency, and a single "Revenue" number that silently adds EUR to USD is worse than no number. This is a correctness guard on S2's `Revenue`, not a new requirement. |
| D7 | **Engagement is counted only in in-scope rooms.** A view in a workspace with no deal attached is not evidence that the room moved pipeline, and S4 makes the report a join | Without this, `Buyer Views` would answer a different question than the `Revenue` beside it. |
| D8 | **`buyer_actions` includes `buyer_views`**, and `buyer_views` counts the events whose action classifies as a view | The ecosystem's own gloss is "how many times a client has interacted with a space … clicking into pages, embedded content", and clicking into a page is a view. Two overlapping counters, stated explicitly so the page can label them. |
| D9 | **The date range filters the deal's own created date on the deal side and the event's occurred-at on the engagement side** | One range, two populations, each filtered on the date that means something for it. S8 names a single range and does not say what it ranges over. |
| D10 | **A deal with no owner borrows its workspace's owner**, and every row says which of the two it came from (`owner_source`) | *Deals By Owner* (S7) is unreadable if half the rows are `unassigned` because a CRM payload omitted the field. The provenance is exposed so a reader can tell a real "unassigned" from a borrowed one. |
| D11 | **Unknown fields are never required.** Every field is discovered by synonym list, and a deal missing all of them is still stored, counted, and shown | A team adding a CRM field ships a record, not a pull request. This mirrors `dsr/analytics.py`'s `analytics_config`. |
| D12 | **The rollup writes nothing.** Every figure is computed on read | The repo pins that HTTP reads never write. A stored metric row would be a cache that silently goes stale against a CRM sync (S11). |
| D13 | **A close date before the created date is excluded from days-to-close** rather than contributing a negative day count | A negative average is not a finding, it is a data error. It is counted in `data_warnings` so the exclusion is visible. |
| D14 | **The report does not refuse when the integration is off.** It returns its figures with `complete: false` and a named warning | S10 says the report is *incomplete*, not *wrong*. Refusing to answer would make the missing data harder to diagnose, not easier. |

### 1.3 Deliberately divergent from the vendor

| # | Divergence | Rationale |
|---|------------|-----------|
| V1 | **The completeness state is a first-class panel, not a footnote.** `GET /api/wf-023/coverage` names every Sales-typed workspace with no deal attached, and every workspace that is not Sales-typed at all | S10's whole content is that missing data is *possible* and undetected. A tile cannot express "this number is low because of these four rooms", so the rooms are listed. |
| V2 | **The inferred half is served as data** at `GET /api/wf-023/inferences` | The research for WF-016 makes no claims about a CRM endpoint; the same is true here of `total pipeline touched`, days-to-close arithmetic, and currency. A judgement call left in a comment is one nobody re-reads. |
| V3 | **Registering a `crm_deal_id` twice is a 409, not a second row** | S11 says stage/amount arrive by sync. A sync that upserts would let a mistyped `crm_deal_id` double-count a deal in the rollup; the conflict returns the existing record id so the caller patches instead. |

### 1.4 Known-unsourced (flagged, not resolved)

- The research names the vendor endpoints (`/v1/deals`, `/v1/accounts`, `/v1/workspaces`) and
  their constraints but **documents no request or response schema for them**. This build does
  not call them and does not claim their shape. The local mirror is described by §3 alone.
- "Total pipeline touched" and "Days to close" are named in the insights list with **no
  definition** anywhere in the research. See D1 and D5.
- Currency is **absent from the research entirely**. See D6.

---

## 2. Architecture decisions (Jev-gated)

Decisions are recorded in `orchestration/decisions/jev-audit.jsonl`.

### A1 — Where the deal mirror lives → a feature-owned `crm_deal` collection

`choose_approach`, audit id `jev-20260927T062748-26392-68603`, returned **`pass`**:
`feature_owned_crm_deal_collection` selected at confidence **1.00** (margin 1.00 over the
runner-up; `is_confident` 0.90). The rationale, in the order it mattered:

1. S3's close-rate fraction is defined over **closed-won and closed-lost** stages.
   `room.stage` in this product is a lifecycle label (`evaluation`, `discovery`,
   `negotiation`, `closed`) and is written by several other features. Making this
   feature's arithmetic depend on another feature's field is how two features end up
   coupled without a reviewer noticing.
2. S11 says stage and amount **change by sync**. A deal is a record with its own id that
   can be patched; a room's fields are a bag of unrelated concerns.
3. The envelope's own `room_id` column is the attachment mechanism, so the join is a
   column, not a convention.

The mirror is schema-flexible: any field the CRM sends is stored verbatim in `data`.

### A2 — Design readiness

`validate_design` over this document, audit id
`jev-20260927T063531-18304-31253`, returned **`pass`**: `ready` at confidence **0.83**.

The bridge would not return a typed result for the whole markdown document — it routed
the request to a coding model and answered in prose — so the gate was asked about a
section-by-section digest of this document instead: the same content, transcribed into
plain JSON so the bridge would route it to the System One model, and re-transcribed after
each round so it could not drift from this file. The digest was an input to a gate call,
not a deliverable, so it is not committed; this section and `jev-audit.jsonl` are the
durable record of what was asked and what came back.

The first three attempts returned `gaps` at confidence 0.47 and then `uncertain` at 0.47
and 0.71, so the diagnostic was run rather than the gate overridden:

| Audit id | Verdict | What the diagnostic then asked for |
|----------|---------|-------------------------------------|
| `jev-20260927T062825-4416-05603` | `fail` — `gaps` @ 0.47 | which required area is weakest |
| `jev-20260927T062854-24676-34964` | `fail` — `gaps` @ 0.48 | (same question, confirming) |
| `jev-20260927T063145-160-05365` | `uncertain` — `ready` @ 0.47 | which *part* of the API surface |
| `jev-20260927T063349-23988-29561` | `uncertain` — `ready` @ 0.71 | same, after the fixes below |
| `jev-20260927T063410-23696-50161` | `uncertain` — `ready` @ 0.74 | where the residual doubt sat |
| **`jev-20260927T063531-18304-31253`** | **`pass` — `ready` @ 0.83** | — |

Each round added a specific, named gap rather than restating the document: §5.1 grew a
per-parameter table and the rules for a deal with no created date and for `limit`;
§5.4 grew a per-field request table giving every field its type, whether it is required,
the synonym keys it is read from, and what happens when it is absent; §5.7 grew the same
for the integration record; and §6 grew the file-by-file layout. The last diagnostic
(`weakest_area` / `api_weakest_part`) reported the API surface's weakest part as "none"
at 0.68, which is why the fourth round went after the code layout rather than the API.

---

## 3. Data model (schema-flexible, no migration)

All payloads are arbitrary JSON in `records.data`. **No new table, column, or migration.**

| Collection | Purpose | Key fields (all optional and open) |
|------------|---------|--------------------------------------|
| `crm_deal` | The CRM opportunity/deal mirror, attached to a workspace through the envelope's `room_id` | `crm_deal_id`, `name`, `account`, `stage`, `amount`, `currency`, `owner`, `team`, `created_at`, `closed_at`, `close_date` |
| `sales_impact_config` | The one configuration record: which collections to read, field synonym overrides, the CRM integration state | `key`, `integration{provider,connected,connected_at}`, `collections{rooms,engagement}`, `fields{…}` |
| `room` | Read, not written, except for a schema-flexible `type` field set by a team | `type` |
| `activity` | Read. The product's buyer view/action record | `person`, `action`, `occurred_at` |

The envelope (`id`, `collection`, `room_id`, `revision`, `created_at`, `updated_at`,
`deleted_at`) is the only fixed vocabulary. `find()` resolves dotted JSON paths through
the dynamic index, so `{"stage": "Closed Won"}` or `{"crm_deal_id": "006NW"}` filters
without a migration.

**Which collection engagement is read from is configured, not hard-coded.** The default is
`activity`, which is the collection the core seeder fills and the collection
`dsr/analytics.py` already reads; a team with its own webhook-derived store points
`sales_impact_config.collections.engagement` at it instead.

---

## 4. Metric semantics

### 4.1 The population (S1, D2)

```
all rooms
  ├─ type normalises to "sales"?            no → excluded, reason "not_sales"
  └─ has ≥1 live crm_deal attached?         no → excluded, reason "no_deal"   (S10)
        └─ in scope
```

Deals attached to a room that failed either test contribute to nothing: not to the tile
totals, not to the owner breakdown, not to the engagement counts.

### 4.2 The eight tiles (S2)

| Tile | Definition |
|------|------------|
| `total_deals` | count of in-scope deals |
| `total_pipeline_touched` | Σ `amount` over in-scope deals (D1) |
| `active_deals` | count of in-scope deals whose stage is neither closed-won nor closed-lost |
| `active_pipeline` | Σ `amount` over that open subset (D1) |
| `closed_won_deals` | count of in-scope deals in a closed-won stage |
| `revenue` | Σ `amount` over closed-won deals, in the report currency (D6) |
| `close_rate` | `closed_won / (closed_won + closed_lost)`, `null` when the denominator is 0 (S3, D3, D4) |
| `days_to_close` | mean days from a closed deal's created date to its closed date, over every closed deal; `null` when nothing has closed (D5, D13) |

A deal with no `amount` contributes to the counts and contributes 0 to the sums. A deal
with a missing `currency` joins the report currency.

### 4.3 Stage classification

Normalise (lowercase, `_`/`-`/`.`/`/`/whitespace → one space, trim), then match on three
levels — exact, `startswith`, substring — and the order is **term-major**: every level is
tried against the lost set before any level is tried against the won set. Anything that
matches neither set at any level is `unknown`, which is an open deal for `active_deals` and
is outside both arms of the close-rate fraction (D3).

Term-major rather than level-major is not a preference. `"won/lost"` normalises to
`"won lost"`, where a *prefix* match for `"won"` would claim it — putting a lost deal into
Revenue — while `"lost"` matches it only as a *substring*. Trying the whole lost set first
lets the more specific signal beat the more general one. Level-major ordering was the
first attempt and is wrong for exactly this reason; it is the one place where the obvious
reading of "lost first" does not achieve it, and it is recorded in the `lost-before-won`
inference as a finding rather than quietly corrected.

`WON_STAGES` / `LOST_STAGES` cover both documented CRMs — Salesforce
(`Closed Won`, `Closed Lost`) and HubSpot (`closedwon`, `closedlost`) — plus the
underscore/camel/space spellings of the same words, and are **configurable** in
`sales_impact_config.fields.stage`.

### 4.4 Engagement (S6, D7, D8)

`buyer_views` and `buyer_actions` count events in in-scope rooms only.
`buyer_actions` includes views. `average_buyers_per_workspace` is
`distinct buyers / in-scope rooms`, `null` when there are no in-scope rooms.
`most_engaged_buyers` ranks by `actions` descending, then `views`, then the buyer's
email for determinism, and carries `workspaces`, `last_view_at`.
`buyer_views_over_time` buckets by the event's own date, ascending, with zero-filled gaps
inside the requested range so a chart is not lying about a quiet week.

A buyer is identified by its email, lowercased (S14).

### 4.5 The two panel charts (S7)

* `deals_created_over_time` — in-scope deals bucketed by created date, ascending.
* `deals_by_owner` — in-scope deals grouped by resolved owner, each with count, amount,
  won, lost, open, ordered by amount then owner name (D10).

---

## 5. API surface

Prefix `/api/wf-023`. **Every write passes `source=` built from `router.prefix`.**

| Method | Path | Writes? |
|--------|------|---------|
| `GET` | `/report` | no — tiles, funnel, both charts, engagement, coverage, warnings |
| `GET` | `/report/rooms/{room_id}` | no — one workspace's contribution **and its eligibility verdict** |
| `GET` | `/deals` | no — filtered list, the drill-in behind the tiles (S9) |
| `POST` | `/deals` | **yes** — register and attach a deal |
| `GET` | `/deals/{deal_id}` | no |
| `PATCH` | `/deals/{deal_id}` | **yes** — the stage/amount sync (S11) |
| `DELETE` | `/deals/{deal_id}` | **yes** — detach, soft delete |
| `GET` | `/buyers` | no — most engaged buyers, filtered |
| `GET` | `/engagement` | no — views/actions totals and views over time |
| `GET` | `/coverage` | no — the completeness readout (V1, S10) |
| `GET` | `/integration` | no — the CRM integration state |
| `PATCH` | `/integration` | **yes** — turn the integration on or off |
| `GET` | `/vocabulary` | no — stages, synonyms, `properties` selection, the 429 constraint |
| `GET` | `/inferences` | no — the registry behind §1.2 (V2) |

Room-scoped paths are room-scoped: `/report/rooms/{room_id}`.

### 5.1 Filters, on every aggregating read

S8 names a date range, CRM stage, owners and teams. All five are accepted by every
aggregating read — `GET /report`, `GET /report/rooms/{room_id}` (as a report over one
room), `GET /deals`, `GET /buyers`, `GET /engagement` — and every one of them is echoed
back under `filters` so a tile is never read as a whole-report total.

| Parameter | Type | Applies to | Semantics |
|-----------|------|-----------|-----------|
| `from` | ISO-8601 date, inclusive | deals **and** engagement | Deal side: the deal's created date. Engagement side: the event's `occurred_at`. A deal whose created date is absent is never excluded by the range; it is kept and reported in `data_warnings` as `unranged_deal`, because dropping it would silently shrink a total. |
| `to` | ISO-8601 date, inclusive | deals **and** engagement | as `from` |
| `stage` | string, or comma-separated list | deals only | Either a raw stage string (`Closed Won`, `closed_lost`, `negotiation`) **or** one of the classes `won`, `lost`, `open`, `unknown`. A class matches every deal classified into it; a raw string matches that deal's stage text after case- and separator-insensitive normalisation. An entry may be either kind and the two are never mixed in one value. |
| `owner` | comma-separated list | deals only | Matched against the resolved owner, which is the deal's own owner when it has one and otherwise its workspace's owner (D10). |
| `team` | comma-separated list | deals only | Matched against the deal's `team`, normalised the same way. |
| `actor` | string, or omitted | **write routes only** | Recorded on the audit row, matching every other feature in this repo. |
| `limit` | integer 1–1000, default 100 | `/deals`, `/buyers` | Page size for the drill-in lists. Aggregated figures are always computed over the **whole** filtered population, never over the page, so paging cannot change a total. |

Unknown query parameters are ignored rather than rejected, so a client written against a
newer vocabulary degrades instead of breaking.

`from` after `to`, or either unparseable, → `InvalidFilter` → **422**.

### 5.2 `GET /report` — the whole report

```jsonc
// GET /api/wf-023/report?from=2026-01-01&to=2026-12-31&team=enterprise
{
  "filters": { "from": "2026-01-01", "to": "2026-12-31", "stage": [], "owner": [], "team": ["enterprise"] },
  "currency": "USD",                  // the report currency (D6)
  "currencies": ["USD"],              // every currency seen among in-scope deals
  "money": { "USD": { "pipeline_touched": 210000, "active_pipeline": 90000, "revenue": 120000 } },

  "tiles": {
    "total_deals": 7, "total_pipeline_touched": 210000,
    "active_deals": 3, "active_pipeline": 90000,
    "closed_won_deals": 3, "revenue": 120000,
    "close_rate": 0.6,                // 3 / (3 + 2); null when nothing has closed
    "days_to_close": 41.7
  },

  "scope": {                          // the population, and what fell out of it
    "rooms_total": 9, "sales_typed_rooms": 6, "in_scope_rooms": 5,
    "excluded": [ { "room_id": "…", "name": "Contoso Health — Security Review", "reason": "not_sales" },
                  { "room_id": "…", "name": "Adventure Works — Pilot",         "reason": "no_deal" } ]
  },

  "funnel": [                         // every stage present, counts reconcile with tiles
    { "stage": "Closed Won", "class": "won",  "deals": 3, "amount": 120000, "revenue": 120000 },
    { "stage": "Closed Lost","class": "lost", "deals": 2, "amount":  45000, "revenue": 0 },
    { "stage": "Negotiation","class": "open", "deals": 2, "amount":  90000, "revenue": 0 }
  ],

  "deals_created_over_time": [ { "date": "2026-02-03", "deals": 2, "amount": 50000 } ],
  "deals_by_owner": [ { "owner": "dana", "owner_source": "deal", "deals": 4,
                        "amount": 150000, "won": 2, "lost": 1, "open": 1 } ],

  "engagement": {
    "buyer_views": 214, "buyer_actions": 388, "unique_buyers": 9,
    "average_buyers_per_workspace": 1.8,
    "buyer_views_over_time": [ { "date": "2026-02-01", "views": 12, "actions": 21 } ],
    "most_engaged_buyers": [ { "buyer": "a.buyer@northwind.example", "views": 61, "actions": 104,
                                "workspaces": 2, "last_view_at": "2026-11-02T09:14:00+00:00" } ]
  },

  "coverage": {                       // V1 / S10, the same object GET /coverage returns
    "crm_connected": true, "provider": "salesforce",
    "sales_typed_rooms": 6, "with_deal": 5, "without_deal": 1,
    "untyped_rooms": 3, "deals_not_attached": 0,
    "complete": false,
    "rooms_without_deal": [ { "room_id": "…", "name": "Adventure Works — Pilot" } ],
    "untyped": [ { "room_id": "…", "name": "Contoso Health — Security Review" } ]
  },

  "warnings": [ { "code": "sales_room_without_deal", "message": "…", "room_id": "…" } ],
  "data_warnings": [ { "code": "close_before_create", "deal_id": "…", "message": "…" } ]
}
```

### 5.3 `GET /report/rooms/{room_id}` — the drill-in behind a tile

`200`:

```jsonc
{
  "room": { "id": "…", "name": "Fabrikam Logistics — Renewal", "type": "sales", "owner": "dana", "account": "Fabrikam Logistics" },
  "in_scope": true, "reason": null,       // or {"reason": "no_deal"} / {"reason": "not_sales"}
  "deals": [ { "id": "…", "crm_deal_id": "006FK", "name": "…", "stage": "Negotiation",
               "stage_class": "open", "amount": 45000, "currency": "USD",
               "owner": "dana", "owner_source": "deal", "team": "enterprise",
               "created_at": "2026-02-03", "closed_at": null, "days_to_close": null } ],
  "tiles": { "total_deals": 2, "active_deals": 2, "closed_won_deals": 0, "revenue": 0,
             "active_pipeline": 90000, "total_pipeline_touched": 90000,
             "close_rate": null, "days_to_close": null },
  "engagement": { "buyer_views": 71, "buyer_actions": 130, "unique_buyers": 4,
                  "most_engaged_buyers": [ … ] }
}
```

`404` `{"error": "unknown_workspace", "detail": "room … not found"}` when the id does not
resolve to a live `room`. An existing room that is simply not in scope is **200 with
`in_scope: false` and a reason**, not a 404 — the research makes that the user's first
step, so the page has to be able to explain it.

### 5.4 `POST /deals` — register and attach

The workspace is named by the `room_id` query parameter **or** by `workspace_id` in the
body (the researched spelling); `crm_deal_id` identifies the deal inside the CRM and is
what makes a second `POST` a conflict rather than a duplicate row.

Every field is optional except that the body must carry at least one of `crm_deal_id` or
`name`. **Nothing is required beyond that**, because the CRM is the writer here and a
field it has not sent yet is a fact about the deal, not a malformed request.

| Field | Type | Required | Read from these keys, in order | If absent |
|-------|------|----------|--------------------------------|-----------|
| `crm_deal_id` | string | one of it or `name` | `crm_deal_id`, `deal_id`, `opportunity_id`, `opp_id`, `external_id`, `crmId` | A new record id is generated and the deal is keyed by `name` instead |
| `name` | string | one of it or `crm_deal_id` | `name`, `title`, `deal_name`, `opportunity_name` | `""` |
| `account` | string | no | `account`, `company`, `organisation`, `organization`, `account_name` | `""` |
| `stage` | string | no | `stage`, `status`, `phase`, `stage_name`, `deal_stage` | Classified `unknown`, which is an open deal and is outside both arms of the close rate (D3) |
| `amount` | number or numeric string | no | `amount`, `value`, `deal_amount`, `opp_amount`, `arr`, `total` | Contributes to the counts and 0 to every sum |
| `currency` | string | no | `currency`, `currency_code`, `currencyCode` | Joins the report currency (D6) |
| `owner` | string | no | `owner`, `owner_name`, `deal_owner`, `assigned_to` | Borrows the workspace's owner, and every row says `owner_source: "room"` (D10) |
| `team` | string | no | `team`, `team_name`, `group` | `""`; the team filter simply never matches it |
| `created_at` | ISO-8601 date or datetime | no | `created_at`, `created_date`, `created`, `open_date`, `opened_at`, `start_date` | The record's own `created_at` envelope value is used; flagged `no_created_date` in `data_warnings`, and the deal is excluded from days-to-close rather than contributing a fabricated zero |
| `closed_at` | ISO-8601 date or datetime | no | `closed_at`, `closed_date`, `close_date`, `won_at`, `lost_at`, `decision_date` | The deal reads as open, and is excluded from days-to-close |
| `room_id` / `workspace_id` | string | no (one of them, or the query parameter) | query `room_id`, then body `room_id`, then body `workspace_id` | `422` if the body has neither a `crm_deal_id` nor a `name`; otherwise the deal is stored unattached and appears in `coverage.deals_not_attached` |

Any key not in the table is stored verbatim in `data` and returned untouched, which is
what makes a team's own CRM field ship as a record rather than a pull request (D11).

```jsonc
// POST /api/wf-023/deals?room_id=<room>&actor=dana
{ "crm_deal_id": "006FK", "name": "Fabrikam renewal FY27", "account": "Fabrikam Logistics",
  "stage": "Negotiation", "amount": 45000, "currency": "USD", "owner": "dana",
  "team": "enterprise", "created_at": "2026-02-03" }
```

`201`:

```jsonc
{ "id": "crm_deal_…", "room_id": "<room>", "source": "POST /api/wf-023/deals",
  "data": { …everything sent… },
  "view": { "crm_deal_id": "006FK", "stage": "Negotiation", "stage_class": "open",
            "amount": 45000, "currency": "USD", "owner": "dana", "owner_source": "deal",
            "created_at": "2026-02-03", "closed_at": null, "days_to_close": null,
            "in_scope": true, "reason": null } }
```

* `422` `{"error": "invalid_deal", "detail": "…"}` — the body has neither `crm_deal_id`
  nor `name`, and no room was named.
* `409` `{"error": "deal_conflict", "detail": "…", "existing_id": "crm_deal_…",
  "existing_room_id": "…"}` — that `crm_deal_id` is already attached (V3). The client
  patches `existing_id` instead of creating a second row.
* `404` `{"error": "unknown_workspace", "detail": "…"}` — a room was named and does not
  resolve to a live `room`.

### 5.5 `PATCH /deals/{deal_id}` — the stage/amount sync

A **shallow merge patch** over `data`: only the keys sent change, and any key from the
table above may be sent. A key sent as JSON `null` is stored as `null`, which is how a
field is cleared.

```jsonc
// PATCH /api/wf-023/deals/crm_deal_…?actor=dana  {"stage": "Closed Won", "amount": 52000, "closed_at": "2026-11-04"}
// 200 -> { "id": "…", "revision": 3, "data": { …merged… },
//          "view": { "stage_class": "won", "days_to_close": 274, … } }
```

`stage_class`, `owner_source` and `days_to_close` are recomputed in the returned `view`
but never persisted, because they are derived and a stored copy would be a cache that
goes stale against the next sync.

`404` when the id is not a live `crm_deal`.

### 5.6 `DELETE /deals/{deal_id}`

Soft delete (the audited store's default), so the detachment and its audit row survive a
restore. Returns the store's `{id, collection, room_id, hard: false}`. `404` when the id
is not a live `crm_deal`.

### 5.7 `PATCH /integration`

A **shallow merge patch** over the single `sales_impact_config` record's `data`, and the
record is created on first write so the endpoint works on a fresh database without a
separate create route. Creating it is the write that is audited; the first call with
`{"connected": true}` therefore produces an `insert` audit row and later calls produce
`update` rows.

| Field | Type | Meaning |
|-------|------|---------|
| `connected` | boolean | Whether the CRM integration is on. The researched precondition for the report (S10). |
| `provider` | string, free text | `salesforce` and `hubspot` are named in the research as having native integrations, but it defines no enum, so none is enforced and any string is accepted. |
| `connected_at` | ISO-8601 datetime, or omitted | When the integration came on. Left untouched unless sent, because the store owns the record's own `created_at` and a client should not have to invent this. |
| `collections.engagement` | string | Which collection buyer views/actions are read from. Defaults to `activity`; re-pointing it at a team's own webhook-derived store needs no code change (D11). |
| `collections.rooms` | string | Defaults to `room`. |
| `fields.stage.won` / `fields.stage.lost` | list of strings | Overrides the built-in won/lost stage sets. |
| `fields.*` | list of strings | Overrides any of the synonym lists in §5.4. |

```jsonc
// PATCH /api/wf-023/integration?actor=dana  {"connected": true, "provider": "salesforce"}
// 200 -> { "id": "sales_impact_config_…",
//          "data": { "key": "default",
//                     "integration": { "provider": "salesforce", "connected": true, "connected_at": "…" },
//                     …overrides preserved… } }
```

### 5.8 `GET /vocabulary` and `GET /inferences`

`/vocabulary` returns `won_stages`, `lost_stages`, `stage_classes`, `field_synonyms`,
`properties_selection` (the S12 `properties` parameter's documented purpose: without it
an endpoint returns only `id`, `object` and `url`) and `constraints` (S13: `429 Too many
requests`).

`/inferences` returns `{count, sourced_quotes, inferences: [{id, topic, basis, value, why, change_it, blast_radius}]}`
— one entry per row of §1.2, plus the sourced quotes it is contrasted against.

### 5.9 Error mapping

`EXCEPTION_HANDLERS` covers four of this feature's own types, which is why the base is a
domain type and not `Exception`:

| Type | Status | Error code | Raised when |
|------|--------|-----------|-------------|
| `InvalidFilter` | 422 | `invalid_filter` | `from` after `to`, or an unparseable date |
| `InvalidDeal` | 422 | `invalid_deal` | neither `crm_deal_id` nor `name` |
| `DealConflict` | 409 | `deal_conflict` | that `crm_deal_id` is already attached (V3) |
| `UnknownWorkspace` | 404 | `unknown_workspace` | the `room_id` does not resolve to a live room |

Every body is `{"error": <code>, "detail": <message>}`; `DealConflict` adds
`existing_id` and `existing_room_id`. `RecordNotFound` is deliberately **not** claimed:
the core app already maps it to 404, and two handlers for one type is a collision the
host refuses.

---

## 6. Code layout

Every path below is new. No shared file is edited, which is what lets this feature merge
alongside the eleven already on `main`.

| Path | Responsibility |
|------|----------------|
| `backend/dsr/salesimpact/__init__.py` | The `SalesImpact` façade holding the store handle, plus the package's public re-exports. Built per request from a dependency, never hung on `app.state`, so no shared file needs editing and the tests get a seam. |
| `backend/dsr/salesimpact/errors.py` | `SalesImpactError` and `InvalidFilter`, `InvalidDeal`, `DealConflict`, `UnknownWorkspace`. A domain hierarchy rather than `Exception`, so a global handler cannot intercept an unrelated error anywhere in the product. |
| `backend/dsr/salesimpact/vocabulary.py` | The `Sales` workspace-type constant, the won/lost stage sets, the per-field synonym lists, the documented API constraints (the 429 and the `properties` selection), `normalise_stage` / `classify_stage`, and `describe()` for `/vocabulary`. |
| `backend/dsr/salesimpact/filters.py` | The `ReportFilter` dataclass, `parse_filters` from query parameters, and the validation that raises `InvalidFilter`. Its own module so parsing is unit-testable with no store at all. |
| `backend/dsr/salesimpact/deals.py` | `DealBook`: config load/save, deal create / get / update / soft-delete, the external-id conflict check, paged listing, and the deal-view projection. `source` is a required keyword-only argument on every write. |
| `backend/dsr/salesimpact/rollup.py` | The population gate, the eight tiles, the funnel, the two panel charts, the engagement rollup, the coverage panel, and the per-room report. Pure functions over already-loaded records, so the arithmetic is testable with no database. |
| `backend/dsr/salesimpact/inferences.py` | The named inference registry behind `/inferences`. |
| `backend/dsr/features/wf023_relate_buyer_engagement_to_crm_pipelin.py` | `FEATURE`, `router`, the 14 routes, `EXCEPTION_HANDLERS`, and `seed(db, context)`. |
| `backend/tests/test_wf023.py` | The four groups in §9. |
| `frontend/src/features/wf-023-relate-buyer-engagement-to-crm-pipelin/index.jsx` | The descriptor, and the whole of the frontend registration. |
| `…/SalesImpact.jsx` | The page. |
| `…/api.js` | The feature's own fetch wrappers over the shared `apiRequest` escape hatch. |
| `…/primitives.jsx` | `Notice`, `BarRow`, and the reduced-motion path, built inside the feature folder. |
| `…/icons.js` | The glyph paths, passed as `iconPath` and `Icon path=`. |

The feature module imports from `dsr.deps` and `dsr.salesimpact` only. It never imports
`dsr.api`; a test in the shared suite already asserts that for every module under
`dsr/features/`.

---

## 7. Frontend

New page `SalesImpact.jsx` at
`frontend/src/features/wf-023-relate-buyer-engagement-to-crm-pipelin/`, registered only
by that folder's `index.jsx`:

- **Coverage banner** first. When `complete` is false it names *why* and links to
  `/coverage`, because S10's failure mode is a number that is quietly too small.
- **Filter bar** — date range, stage, owner, team, from the `/vocabulary` endpoint.
- **Eight `StatCard` tiles** in the researched order, each a drill-in link into the
  filtered `/deals` or `/buyers` list (S9).
- **Deals Created Over Time** and **Deals By Owner** as bar rows.
- **Buyer Engagement** — views, actions, average buyers per workspace, the time series,
  and the Most Engaged Buyers ranking.
- **Inferences** behind a disclosure, rendered from the server's list rather than from
  copy in this file, so the page cannot drift from the registry.

Accessibility floor, per `design-system/digital-sales-room/MASTER.md`: 44px minimum touch
targets, visible focus rings (never removed), a text label beside every icon, no emoji as
an icon, `cursor-pointer` on every clickable element, 150–300ms transitions, and
`prefers-reduced-motion` honoured by rendering the final state with no animation. The two
charts and the tile link are built inside the feature folder and named as promotion
candidates in the report.

---

## 8. Explicitly not built

- **Calling the vendor API.** S12/S13 document endpoints and constraints, not a schema,
  and the ticket asks for a local implementation. The `properties` selection and the 429
  constraint are *served* as documented constraints at `/vocabulary`; no socket is opened.
- **A retry or backoff policy for a CRM pull.** S13 documents the 429 and nothing else.
  Inventing an attempt count would be exactly the kind of unsourced number §1.4 is for.
- **Correlation coefficients.** The data flow says the report exists "to show the impact
  Dock has on your pipeline and close rates", but the research never claims a statistical
  method, and joining views to wins across a handful of deals is not one. The report shows
  both sides of the join and lets the reader see it.
- **Any notion of forecast, quota attainment, or attribution to a single asset.** Not in
  the research.
- **Per-buyer `workspace.*` webhook ingestion.** Buyer events already arrive through the
  product's records API; this feature reads the collection and lets it be re-pointed, so a
  second ingestion path would be a duplicate of a capability that exists.

---

## 9. Tests

`backend/tests/test_wf023.py`, in four groups:

* **Domain** — stage classification across both CRMs and the three match levels; the close
  rate fraction including the 0/0 case; days-to-close including the negative-interval
  exclusion; currency selection; view-versus-action counting; owner fallback; filter
  parsing and rejection.
* **Rollup** — the population gate and its two exclusion reasons; that an excluded room's
  deal contributes to nothing; the coverage panel; funnel totals reconciling with the
  tiles; time-series zero-filling; determinism of every ordering.
* **HTTP** — every route through this feature's own router; the three error statuses; 404
  for an unknown workspace; a schema-flexibility round trip of an unknown field.
* **Audit** — every write is audited; **every audit row this feature's HTTP layer produces
  names a route the host actually mounted**, matched against the live `/api/features` route
  table; and the no-`dsr.api`-import guard.
