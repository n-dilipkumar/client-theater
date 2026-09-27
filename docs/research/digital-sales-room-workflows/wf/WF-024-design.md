# WF-024 — Technical Design: Roll up client engagement and multi-threading portfolio-wide

- **Ticket:** WF-024
- **Workflow:** [`WF-024.md`](./WF-024.md)
- **Research source:** `docs/research/raw/analytics-intent.md` §9
- **Branch:** `n-dilipkumar/dsr-wf-024-build`
- **Feature module:** `backend/dsr/features/wf024_roll_up_client_engagement_and_multi_th.py`
- **Domain module:** `backend/dsr/client_engagement.py`
- **Frontend:** `frontend/src/features/wf-024-roll-up-client-engagement-and-multi-th/`

This document separates three things the research blurs together: what is **sourced**
from a primary source, what is a **design inference** we chose, and what was
**decided with Jev** rather than by preference. This is a build, not a port: there
is no source branch, and the research document is the specification.

---

## 1. Evidence ledger

### Sourced (quoted from primary sources)

| # | Behaviour | Source |
|---|-----------|--------|
| S1 | "The Client Engagement report analyzes external activity across ALL workspaces in your Dock instance." | `help.dock.us/en/articles/9006160-client-engagement-report` |
| S2 | The report's insights are "Total client views, Total client actions, Client views over time, Most engaged clients" | same |
| S3 | "**Total client actions** counts how many times a client has interacted with a space. We think of this as clicking into pages, embedded content, etc." | same |
| S4 | "Click into the cell to expand upon who these individuals are!" | same |
| S5 | "This report tracks how well you're multithreading accounts, helps you identify champions, and lets you track trends in engagement over time." | same |
| S6 | "Reports in Dock automatically show holistic data across all workspaces. You can filter the report down by date range, owners, and/or teams." | `dock.us/library/reports` |
| S7 | "**Team Usage:** How actively is your team using Dock?" and "**Implementation Status:** How long are customer implementations taking?" | same |
| S8 | "Webhooks allow you to push all the workspace activity data out of Dock for use in other applications." | `dock.us/library/api-webhooks` |
| S9 | The equivalent data is reachable as `GET /v1/workspaces` (+ `properties`), `GET /v1/accounts`, `GET /v1/workspace-users`, and the full `workspace.*` webhook stream | same, and the spec's `apis_hit` |
| S10 | "Total/Active/Completed implementations, Time to completion average, % completed on time, Implementations by owner, Customer Views/Actions, Most Engaged Customers", for "at-risk delivery tracking" | `help.dock.us/en/articles/9006640-implementations-report` |
| S11 | The data source is "Dock `workspace` + `workspace-user` activity across the whole instance; **external vs internal user distinction**; account and deal links" | spec `data_sources` |
| S12 | "underlying event capture is continuous" | spec `automations` |

### Design inference (unsourced — chosen by us)

| # | Inference | Rationale |
|---|-----------|-----------|
| D1 | **A view is a kind of action.** `actions` is every external interaction; `views` is the subset whose action is in a configurable `view_actions` list; `client_actions_other_than_views` exposes the difference. | S3 puts page clicks inside the action count, so a view is an action. The research never says the two tiles are disjoint, and two disjoint counters that do not relate to each other cannot be read together. Gated by Jev, §2. |
| D2 | **The "Average unique clients per workspace" denominator is every workspace in scope**, including any nobody has opened. | S1 makes the report holistic and S2 makes the tile a coverage measure. Excluding unopened workspaces would make a portfolio report unable to show an unworked account. Gated by Jev, §2. |
| D3 | **Multi-threading is buyer-side.** An account is multi-threaded when the number of distinct engaged *clients* reaches `multi_thread_floor` (default 2); the champion is the most engaged of them; one client means `champion_only_thread`. | S5 promises a champion and a multi-threading measure; the tile S2 names counts *clients* per workspace, and S4 expands to *client* individuals. A rep-side reading has no supporting tile. Gated by Jev, §2. |
| D4 | **Audience classification order**: an explicit field on the event (`user_type`, `external`, `is_internal`, …) first, then a configured list of internal identities and address domains, then a configurable default. An event with **no person** is `unknown` whatever the default says. | S11 names the external/internal distinction as the data source but this product's records do not carry it. Explicit first because it is a decision somebody made; the configured list because a dataset that marks nothing still has a known seller team. An anonymous action cannot be attributed to a client, and counting it as one inflates every client metric. |
| D5 | **A date-only `to` bound means the end of that day.** | Parsed as an instant it is midnight at the *start* of the day, so "through the 27th" would silently drop the 27th — the most likely way for a report filter to be quietly wrong. |
| D6 | **An unknown sort column is refused with 422**, not ignored, and the error lists the sortable columns. | S6's "sort by any column" is served as data (`sortable_columns`); a silently dropped `sort` is a report that looks sorted by something it is not. |
| D7 | **The chart's axis starts at the earliest in-scope event** when no `from` is given, and the grain widens from day to week past `max_chart_points` with `grain_widened: true`. | S2 names the tile but no axis. A report whose chart is empty while its tiles are full reads as a bug. The widening is reported, so a coarser chart is never shown silently. |
| D8 | **An account key is a deterministic path-safe slug**, with names that collapse to the same slug disambiguated in name order. | The drill-down is a path segment, so an account named `Northwind / EMEA` would otherwise not be addressable at all. |
| D9 | **At-risk** = active and past due, active with no committed date, or completed after its due date. Urgency is reported as `days_to_due` instead. | S10 states the report's purpose as "at-risk delivery tracking" and does not define at-risk. Folding "due next week" into the list would make it unusable as a work list. |
| D10 | **Everything the research does not fix is in a `client_engagement_config` record**: the audience vocabulary, the view taxonomy, the multi-thread floor, the stale threshold, the chart window, and the implementation field names. | Sourced rules are not configurable; inferred ones must be, or changing one is a code change. Served at `GET /config` so a reviewer can disagree with a named entry. |
| D11 | **An event on a workspace outside the scope is excluded and counted** in `coverage.events_out_of_scope`. | Filtering by owner must not leave that owner's activity in the totals, and an event that vanishes from every number with no trace is the failure mode a report cannot have. |

### Decided with Jev, not by preference

`tools/jev.py` is the only gate. Every decision below is appended to
`orchestration/decisions/jev-audit.jsonl`.

| Question | Options put to Jev | Result | Audit id |
|----------|-------------------|--------|----------|
| Which of the three reports does the feature ship? | `client_engagement_only`, `client_engagement_plus_team`, `engagement_plus_siblings` | `engagement_plus_siblings` at confidence **0.99** | `jev-20260927T062104-16408-64900` |
| Is a client view also a client action? | `subset`, `disjoint`, `undefined` | `subset` at **0.69** (p=0.79) | `jev-20260927T061954-25528-94194` |
| Which side does multi-threading measure? | `client_side`, `rep_side`, `both` | `client_side` at **0.99** | `jev-20260927T061954-25528-94530` |
| Denominator of the average | `all_workspaces`, `engaged_only` | `all_workspaces` at **0.99** | `jev-20260927T061954-25528-94901` |

The first question returned `uncertain` on its first pass (selected
`engagement_plus_siblings`, confidence 0.30, with its own `is_confident` noul at
0.57). Per `AGENTS.md` an `uncertain` verdict is not overridden; the two ways
forward are more evidence or a human. The missing evidence was supplied — the
source weighting (4 of the 5 cited sources document the Client Engagement report,
1 documents the Implementations Report, Team Usage appears as one sentence) and
the marginal cost of each option — and the re-ask returned `pass` at 0.99 with a
0.98 margin. The intermediate `uncertain` row is still in the audit log.

---

## 2. Why three reports

The research document names three reports in one family and cites five sources.
The workflow's title, `user_flow` and `data_flow` are all Client Engagement; the
other two appear under `extensibility` (`features_tools`, `evidence`) and one of
them, the Implementations Report, is one of the five cited sources in its own
right. The build brief forbids quietly dropping a requirement the research states.

Jev chose to ship all three with Client Engagement primary. The cost is low
because all three are reads over one rollup:

* **Client Engagement** — the five sourced tiles, the expanded account list, the
  per-account individuals.
* **Team Usage** — the internal mirror of the same external/internal distinction.
  Sourced as one sentence with no widget vocabulary, so every column beyond a
  count is an inference and is named as one in `dsr/client_engagement.py`.
* **Implementations** — all six sourced widgets, over an `implementation`
  collection whose every field name is discovered from config.

---

## 3. Data model

No migration, no typed column, no new required field. Everything is a record.

| Collection | Read | Written by | Notes |
|------------|------|-----------|-------|
| `room` | yes | elsewhere (core, other features) | the workspaces. Account, owner and teams are discovered from the payload. |
| `activity` | yes | `POST /wf-024/events` | the interactions. Person, action, timestamp and audience are discovered from the payload. |
| `implementation` | yes | elsewhere (core records API) | collection name itself is configurable. |
| `client_engagement_config` | yes | `PATCH /wf-024/config`, `seed` | every inferred rule, deep-merged over the defaults. |

`data` is arbitrary JSON. The only fixed vocabulary is the envelope: `id`,
`collection`, `room_id`, `revision`, `created_at`, `updated_at`, `deleted_at`.
A team adding a field to an activity payload changes the report without
coordinating with anyone; a test asserts an undeclared field survives the ingest
round trip and is queryable.

Nothing here writes a metric row. The report is computed on read, so it is never
stale and never has to be backfilled.

---

## 4. The data flow, step by step

Every number in every report comes from this pipeline. Nothing is pre-aggregated
and nothing is cached, so a record written one second ago is in the report on the
next read.

### 4.1 Read

**Workspaces.** `scan_workspaces` reads every live `room` record in pages of 1000
ordered by `id`. Per record: `name` from `room.name_fields`, `account` from
`room.account_fields`, `owner` from `room.owner_fields`, `teams` from
`room.team_fields` (a field holding a list yields a list, a field holding one
value yields a one-element list), `created` from the record's own `created_at`.
Every list of field names is config, so a team that calls them something else
overrides the list rather than patching code.

**Events.** `collect_events` reads every live `activity` record the same way.
Per record: `person` from `event.person_fields`; `action` from
`event.action_fields`; `target` from `event.target_fields`; `at` from
`event.timestamp_fields`, **falling back to the record's `created_at`** so a
dataset that never wrote a timestamp still gets a dated report; `account` from
`room.account_fields`; `is_view` = `action.lower() in taxonomy.view_actions`;
`audience` = `classify_audience(person, payload, config)` (D4).

**Implementations.** Read only by the Implementations Report, from the
configurable collection, with every field name and every state word discovered
from `implementation` in the config.

### 4.2 Filter

`resolve_filters` turns the three sourced filters into one `Filters` value:

1. `from`/`to` parse to an **inclusive** instant. A date-only `to` becomes the
   last microsecond of that day (D5). An unparseable bound, or a `from` after a
   `to`, raises `InvalidWindow` → 422.
2. `owner` and `team` are lowercased into sets. A workspace is in scope when its
   owner is in the owner set **and** any of its teams is in the team set. A
   workspace with **no owner is excluded once an owner filter is on** — it is not
   known to belong to that owner, and keeping it would make a filtered report
   disagree with the list of owners it was filtered by.
3. An event is in the date window when its instant is inside both bounds. An
   event with no resolvable instant is kept only when **no** range is set; with a
   range it is excluded, because it cannot be shown to be inside one.

### 4.3 Roll up

`build_rollup`, in this order:

1. Partition the in-scope events by `audience` into `external`, `internal` and
   `unknown`. Drop any event whose workspace is not in scope and count it in
   `coverage.events_out_of_scope` (D11).
2. One bucket per distinct **account name**. A workspace contributes its account
   name, or `(unattributed)` when it has none. An event contributes **its own**
   account if it names one, otherwise its workspace's, so an activity row that
   names an account is not misfiled.
3. Per account, over external events only:
   * `views` = events with `is_view`; `actions` = all external events;
     `other_actions` = the difference.
   * `clients` = one row per distinct person, carrying that person's `actions`,
     `views`, workspaces, targets and last-seen instant.
   * `first_activity_at` / `last_activity_at` = min and max of the dated events.
   * `owners` / `teams` = the union over the account's workspaces.
   * `multi_threaded` = `unique_clients >= thresholds.multi_thread_floor` (D3).
   * `champion` = the first client under the rank key
     `(-actions, -views, -last_seen, person)` — a total order, so two clients with
     identical engagement cannot swap places between two reads.
   * `champion_only_thread` = there is a champion and exactly one client. This is
     the coverage risk the report exists for.
   * `stale` = `days_since_activity > thresholds.stale_after_days`.
4. Portfolio totals: `client_views` and `client_actions` are the sums over
   accounts; `avg_unique_clients_per_workspace` is
   `(sum over workspaces of distinct engaged clients) / (workspaces in scope)`,
   and `0` when there are none (D2); `multi_threaded_accounts`,
   `single_threaded_accounts`, `accounts_without_client_activity` and
   `stale_accounts` are counts over accounts; `unique_clients_portfolio` is the
   count of distinct persons across **all** accounts, so one person engaging two
   accounts is one portfolio client and two account clients.
5. `ranked_clients` = the per-person sums across accounts, ordered by actions,
   then views, then recency, then name.

### 4.4 Chart

`_chart_window` sets the axis: `end` is the `to` bound or now; `start` is the
`from` bound, else the earliest in-scope event floored to its bucket, else
`now - chart_days` (D7). `_resolve_grain` widens day to week past
`max_chart_points` and reports `grain_widened`. `views_over_time` buckets the
**external** events by `bucket_start(at, grain)` across the axis, dense and
zero-filled, each bucket carrying `views`, `actions` and distinct `clients`, so a
day with no client activity reads as a gap rather than being absent.

### 4.5 Serve

`_context` assembles `as_of`, `grain`, `scope`, the filter echo, the effective
config (with `audience.internal_people` withheld, since it is a list of
identities rather than a rule), `totals` and `coverage`. `_tile_values` turns
that into the five tiles, each carrying the sort key it expands to; the **route**
builds the `accounts_href` from that key, so the domain holds no URL knowledge.

`account_list` runs the same rollup, orders it with `sort_accounts` (which
refuses an unknown key, D6), applies the configured cap **only when the caller
asked for no limit of its own** — applying it first would make `limit` mean "the
first N rows" and every page after the first empty — and then pages by
`offset`/`limit`. `account_detail` reuses the list and raises `UnknownAccount`
for a key that is not in scope, so "you filtered it away" is never reported as
"this account has no engagement".

`team_usage` and `implementations` run the same rollup for their client figures
and regroup: Team Usage by workspace owner, with a rep's own activity attributed
by name and an internal person who owns nothing reported in
`unattributed_internal_people`; Implementations by owner, over the
`implementation` collection, with the customer figures taken unchanged from the
client rollup so the two reports cannot disagree.

### 4.6 Write

**`POST /events`.** Reject an empty body. If `room_id` is given, require a live
`room` record or raise `UnknownWorkspace` → 404. Load the config, classify the
audience, and stamp `user_type` **only** when the payload carries no recognised
audience field — an explicit field is a decision somebody made and is left
alone. Create the `activity` record through the audited store with
`source=f"POST {router.prefix}/events"` and return the record plus `counted_as`,
so the caller can see which side of the report its event landed on.

**`PATCH /config`.** Deep-merge the patch onto the existing record's `data`, force
`key`, create with a fixed `record_id` on the first call and update thereafter —
one record, so the audit log reads as a history of one configuration rather than
a pile of rows. `source=f"PATCH {router.prefix}/config"`, and `source` is a
required keyword on `save_config` with no default, so it cannot regress
silently.

---

## 5. What the tests pin

`backend/tests/test_wf024.py`, against a throwaway database. Grouped as the
sections below name them:

* **The audience rule** (D4) on its own, because a test that passes while the
  distinction is broken is easy to write: explicit field, case and space
  insensitivity, boolean field, configured identity, configured domain including
  the lookalike-domain non-match, an unrecognised value falling through, the
  anonymous case, the configurable default, and the rule that the default does
  not rescue an anonymous event.
* **Each tile's arithmetic**: five tiles with the researched labels; actions
  never below views; an unclassified action still an action; the taxonomy being
  configurable; the average's denominator (D2) and its behaviour with one, two
  and no workspaces; one person engaging two workspaces counting once in each and
  once portfolio-wide.
* **The chart**: dense and zero-filled, starting at the earliest event, empty but
  well formed, week grain, automatic widening and the flag, an unknown grain
  refused, distinct clients per bucket, and internal activity excluded.
* **Multi-threading and the champion** (D3): the thread count is distinct clients,
  internal reps do not add to it, the champion is the most engaged, an unopened
  account names no champion, both thresholds are configurable, and account rows
  sum to the portfolio totals.
* **The three filters** (S6): the range narrowing events, a date-only `to` meaning
  the end of the day (D5), a date-only `from` meaning the start, an inverted or
  unparseable range refused, owner and team filters combined, case insensitivity,
  repeated and comma-separated values, a workspace with no owner excluded by an
  owner filter, a filter matching nothing being an empty report rather than an
  error, and the filter echo.
* **Sorting** (D6): every column of the list is a sort key, every one accepted and
  reported back, both directions, an unknown key refused with the alternatives
  listed, a blank key falling back, ties broken on the account name so two reads
  agree, and paging without losing the total.
* **Account keys** (D8): path-safety, a name that collapses onto another being
  disambiguated, stability between calls, and every listed key resolving.
* **The drill-down** (S4): the individuals, the champion, the thread depth, the
  workspaces, the owners and teams, what a client looked at, an unknown key, and a
  key filtered out of scope.
* **The workspace-scoped report**: one workspace's rollup, its identity, the same
  five tiles, the date range kept and owner/team dropped, an unopened workspace
  still reporting, an unknown workspace, a record that is not a workspace, and
  another workspace's events excluded.
* **Team Usage** and **the Implementations Report** against all six sourced
  widgets, each at-risk shape, an unrecognised status counted rather than dropped,
  an implementation for an out-of-scope account, the configurable collection,
  state vocabulary and field names, and the no-data cases reported as `None`
  rather than a wrong number.
* **Config**: defaults, merge not replace, recursive merge, create-then-update on
  one record, and `source` being required.
* **Ingest**: external by default, internal kept out, an explicit field left
  alone, anonymous counted for nobody, the payload stored verbatim with an
  undeclared field, an empty body refused, an unknown workspace refused, and the
  event reaching the report.
* **The HTTP surface** through the feature's own router, including that
  `GET /report` has **no** workspace parameter, that every tile's
  `accounts_href` resolves, that an account name with a slash is still
  addressable, and that the filters reach every read route.
* **The audit source rule**: the only sources naming this feature's prefix are
  the two routes that write, no row names a path the app does not serve, the
  source is built from the live prefix, the row carries the room scope, actor and
  new state, and a second config patch updates rather than duplicating.
* **The registration and the contract**: mounted by discovery with
  `failed_count == 0`, the exact route set, no other feature serving a path under
  this prefix, no import of `dsr.api`, no SQLite handle, the domain module
  importing nothing but `dsr.store` and no web framework, no migration or typed
  column, the three error handlers and their signatures, and a `seed` export.
* **The demo data**: the states that change an answer, each asserted — see §9.

---

## 6. HTTP surface

`router.prefix = "/api/wf-024"`, unique across every mounted feature.

| Method | Path | Sourced behaviour it serves |
|--------|------|------------------------------|
| GET | `/report` | S1, S2, S3, S6 — the five tiles, over all workspaces. No workspace selector. |
| GET | `/accounts` | S6 — the expanded tile, sortable by any column, filtered by date/owner/team. |
| GET | `/accounts/{account_key}` | S4 — "who these individuals are". |
| GET | `/rooms/{room_id}/engagement` | S1 — the same rollup narrowed to one workspace. Room-scoped paths stay room-scoped. |
| GET | `/filters` | S6 — the owners, teams, accounts, date bounds, grains and sort keys in scope. |
| GET | `/reports/team-usage` | S7 — Team Usage. |
| GET | `/reports/implementations` | S10 — the Implementations Report. |
| GET | `/config` | the inferred rules, served so a reviewer can disagree with a named entry. |
| PATCH | `/config` | the same, merged and audited. |
| POST | `/events` | S12, S9 — the ingest seam for the event stream. |

### Error mapping

`EXCEPTION_HANDLERS` exports three handlers, all for types this feature owns:

| Type | Status | Body |
|------|--------|------|
| `EngagementReportError` (and `InvalidWindow`, `InvalidSortColumn` beneath it) | 422 | `{"error": "invalid_report_request", "detail": ...}` |
| `UnknownWorkspace` | 404 | `{"error": "unknown_workspace", ...}` |
| `UnknownAccount` | 404 | `{"error": "unknown_account", ...}` |

`RecordNotFound` is deliberately **not** claimed: the core app already maps it,
and two handlers for one type is a collision the host refuses.

### The audit source rule

Every write takes `source=` from the HTTP layer, built from `router.prefix`, and
`save_config`'s `source` is a **required keyword** with no default so this cannot
regress silently. `test_wf024.py` asserts that the only sources in the audit log
naming this feature's prefix are `POST /api/wf-024/events` and
`PATCH /api/wf-024/config`, and that no row names a path the app does not serve.

---

## 7. Frontend

One page, three tabs, following the researched flow: read the tiles, click a tile
to expand the account list, sort by any column, filter by date range / owners /
teams, and read whether the account is multi-threaded and who the champion is.

Two things the page is deliberate about:

* **The external/internal distinction is visible.** A coverage line under the
  tiles names how many events were excluded as internal and how many as
  unattributable. A report about client activity that hides what it excluded is
  asking to be trusted.
* **The average's denominator is stated on the tile.** The hint says how many
  workspaces it is over, so the number cannot be read as "across the deals that
  worked".

Accessibility and design-system floor: 44px minimum targets, `aria-sort` on the
sortable `<th>`, `aria-pressed` on the toggle chips, `aria-expanded` /
`aria-controls` on the expandable account rows, visible focus from the global
`:focus-visible` in `index.css`, text labels beside every icon, no emoji as an
icon (the sort arrows are inline SVG passed through `Icon path=`), and no motion
at all — so `prefers-reduced-motion` is respected by there being nothing to
reduce.

### UI primitives built inside the feature folder

`components/ui.jsx` does not export a clickable metric tile, a multi-select filter
chip, or a sortable column header, and it is shared, so `Tile`, `ToggleChip`,
`SortHeader` and `Tabs` live in this feature's `primitives.jsx` and are named as
promotion candidates there. The glyph is *not* a promotion candidate: the
contract already has the mechanism (`Icon path=`, `iconPath` in the descriptor).

Note for the integrator: `docs/FEATURE-CONTRACT.md` lists `Modal`, `Notice`,
`Toggle` and `Checkbox` among the primitives a feature may use, and none of the
four is exported by `components/ui.jsx`. That is a documentation/implementation
gap worth closing as platform work, and this feature did not need any of them.

---

## 8. What was deliberately not built

* **Webhook delivery.** S8 and S9 name the `workspace.*` stream and Dock's promise
  that a third party can push it into a warehouse. That is WF-023's subject, and
  here it is the *extensibility* of the report rather than the report. This
  feature is the consumer-shaped end: the report is computed on read from the
  events already stored, and `POST /events` is the seam a webhook consumer lands
  events through. Nothing in the feature opens a socket.
* **A stored metric row per tile.** Every number is derived on read. A stored
  rollup would need invalidation rules, and the fields it froze would be the ones
  a team most wants to add.
* **A `workspace` filter on the portfolio report.** S1 is explicit that the report
  spans all workspaces; a workspace selector would make this the WF-006 Analytics
  view wearing this report's name. `GET /rooms/{room_id}/engagement` is the
  workspace-scoped read.
* **Per-workspace owner/team filters on the workspace-scoped route.** The scope is
  already the workspace; an owner filter there could only ever empty it.
* **A push-to-BI export.** S8 describes a third party consuming the stream into
  *their* BI tool. The stable `GET /accounts` shape is what such a consumer reads;
  building an export format the research does not specify would be inventing one.

---

## 9. Demo data

`seed(db, context)` in the feature module, seeding the states that change an
answer rather than a spread of success:

* **Northwind Traders** — three engaged buyers: multi-threaded, with a champion.
* **Contoso Health** — one engaged buyer: the account resting on a single person.
* **Fabrikam Logistics** — two engaged buyers, no activity in the last month.
* **Quiet Holdings** and **Adventure Works** — workspaces nobody has opened, so
  the average's denominator has something to count.
* **Internal rep activity** from three reps, which must never reach a client tile.
  `kai` owns no workspace, so Team Usage has an internal person to name.
* **One anonymous interaction**, which must land in `coverage` and in no total.
* **Seven implementations**: one delivered early, one delivered late, one active
  and overdue, one due in three days, one active with no committed date, one with
  no owner, one for an account that is not in scope.

The seeder writes through the audited store with `source="seed"`, and a test
asserts the audit log is populated.
