# Digital Sales Room

An open-source, self-hostable digital sales room: buyer-facing workspaces built
from your own content, with every change recorded in an audit log you can prove.

Licensed under GPLv3. The backend is Python and FastAPI, the frontend is React,
and the whole application needs nothing more exotic than a SQLite file to run.

## The goal

Sales teams today keep the interesting part of a deal in six different places —
a deck in the drive, pricing in a spreadsheet, security answers in an email
thread, and the buyer's actual behaviour nowhere at all. The digital sales room
is where those things are supposed to come together, yet the commercial
implementations are closed, priced per seat, and treat the customer's own
engagement data as their property.

This project sets out to build that capability in the open, and to take three
requirements seriously in a way the off-the-shelf products do not:

- **Auditability as the foundation, not a feature.** Every mutation of data
  writes its audit row inside the same transaction as the change. The log
  cannot drift from the data, so a customer may be told exactly who saw what,
  and when, and may rely on it.
- **Schema flexibility without coordination.** A team adding a field to a room
  or a document must not require a migration, a pull request from another team,
  or a deployment. Fields are discovered from the data at runtime.
- **Many features, merged without collision.** The roadmap is one hundred
  workflows authored by many different people and agents in parallel. The
  architecture is designed so that a workflow is a file you add, never a shared
  file you edit.

## Tech stack

| Layer | Choice | Notes |
|---|---|---|
| Frontend | React 18, Tailwind CSS 4, Vite 6 | Hash-based routing to keep the dependency surface small |
| Frontend testing | Vitest, Testing Library | |
| Backend | Python 3.11+, FastAPI, Uvicorn | `python-multipart` for document uploads |
| Database | SQLite via `AuditedDatabase` | Single writer, WAL journalling; direct connections are not permitted |
| Backend testing | Pytest, httpx | 56 test modules, run against a temporary database |
| Documentation | Markdown under `docs/`, with a researched workflow corpus | |
| Design system | `design-system/digital-sales-room/MASTER.md` | 44px touch targets, visible focus, 4.5:1 contrast, `prefers-reduced-motion` honoured |

A single FastAPI process serves both the API and the built frontend, so a
browser pointing at `http://127.0.0.1:8000` sees one application.

## Implementation strategy

**Features are added as files, never by editing shared files.** A workflow is
one Python module under `backend/dsr/features/` exporting a `FEATURE`
descriptor and a FastAPI `router`, plus one folder under
`frontend/src/features/` whose default export is `{ id, label, icon, Component }`.
The host walks the package at startup and the frontend glob expands at build
time, so there is no registration step and nothing to merge into. `App.jsx`
builds its navigation from whatever it discovers. An earlier attempt at this
project stalled with twelve finished branches and none merged, because all
twelve had edited the same three files; the plugin host is the response to that.

**One audited write path.** All storage goes through `AuditedDatabase`
(`backend/dsr/db/audited.py`). It guarantees an atomic audit row per change,
atomic multi-record transactions, a single lock-guarded connection, and honest
failures that roll back with no audit row written. Dependencies arrive through
`dsr.deps`, never by importing the app — there is a test that enforces it.

**Schema-flexible storage.** Records are arbitrary JSON in `records.data`. The
only fixed vocabulary is the envelope: `id`, `collection`, `room_id`,
`revision`, `created_at`, `updated_at`, `deleted_at`. Queries and the
`?where=` API resolve dotted JSON paths through a dynamic index.

**Domain logic sits beside the feature, not inside the host.** Anything large
enough to need its own tests gets its own package under `backend/dsr/` —
`crm_oauth`, `fieldmap`, `crm_upsert`, `crm_provisioning`, `crm_engagement`,
`atomic_bundle`, `partial_failures`, `dedupe`, `crm_backfill`, `plays` — and is
imported by the one feature that owns it. A feature folder stays small; what it
drives does not have to be.

**Route collisions are refused, not silently shadowed.** The host compares
concrete `(method, path)` pairs across every mounted feature and the core
application. Two features may share a prefix while their paths differ — three
of them share `/api/library` quite happily — but a second
`GET /api/rooms` is rejected and reported as a failed feature.

**A broken feature is visible, never fatal.** A module that raises on import is
recorded in the registry's `failed` list, reported at `/api/features`, shown in
the interface, and skipped. One bad file out of a hundred must not take the
product offline.

**Research first, then code, then a validation gate.** `docs/research/raw/`
holds per-domain workflow specifications that cite primary sources and record
plainly what could not be sourced. A workflow marked unsourced is a hypothesis,
not a specification. Each change is gated by a typed judgement through the Jev
validator in `tools/jev.py` before merge, and every such decision is appended
to `orchestration/decisions/jev-audit.jsonl`, which is the project's own audit
trail. A feature without tests is not considered finished.

## What works now

Thirty-seven workflow plugins are merged, over 580 API routes. Each has its own
page in the left-hand navigation; where a page is scoped to a particular deal,
choose the room at the top of the page. Run the seeder first and there is
realistic demo data for every one of them.

### Core application

| Feature | What it is | How you use it |
|---|---|---|
| Dashboard | Landing view of rooms and activity on the audited store. | Open **Dashboard**. |
| Sales rooms | The room list and editor; a room is a schema-flexible, fully audited record. | Open **Sales rooms**, pick a room, edit its fields. Any field you add is stored and queryable immediately. |
| Audit log | Every SQLite change, recorded in the same transaction as the change. | Open **Audit log**. Deep links work, so you may share a specific entry. |
| Schema explorer | Fields discovered at runtime from stored data rather than declared up front. | Open **Schema explorer** to see what each collection actually holds and filter on any dotted path. |
| Workflows | Live inventory of installed plugins, their prefixes, routes and failures, read from `/api/features`. | Open **Workflows** to see what this installation actually has. |

### Creating and building a room

| Feature | What it is | How you use it |
|---|---|---|
| Create a room (WF-001) | A three-step wizard — bind the room to an account, pick a template, name it — with the room and the site it is bound to written in one transaction. | Open **Create a room** and work through the three steps. Every other field you send is stored as-is. |
| Buyer pages (WF-002) | A fragment catalogue, a drag-and-drop page editor with a configuration panel, and an immutable published revision. | Open **Buyer pages**, compose the page from fragments, then **Publish**. Buyers are served published revisions only. |
| Documents (WF-003) | The room's document folder as one canonical library: upload, workflow status with a transition graph, role gates on every write, and the four-slot Document Gallery Block. | Open **Documents** for a room, upload or move a document through its status, and place up to four in the gallery block. |
| Content library (WF-007) | Organisation-wide ingest of a binary plus schema-flexible metadata: required name, derived format, name de-collision, rollback on a failed binary, versioning rather than replacement, asynchronous thumbnail. | Open **Content library**, upload a file with its metadata, and add a new version later rather than replacing the binary. |
| External content (WF-008) | A file living in someone else's cloud, referenced as a link rather than a copy, with auto-sync deciding whether the link follows the source or is a one-time snapshot. | Open **External content**, add the external file, choose auto-sync or snapshot, then run a re-sync pass to pick up what the source has moved on to. |
| Approval & publishing (WF-009) | Named approval processes with ordered steps, then publish immediately or on a UTC schedule; published content lands in dynamic folders by its own metadata. | Open **Approval & publishing**, submit content into a process such as *Standard content review*, clear each step in order, and publish now or on a schedule. |
| Library search (WF-010) | A real query over the content library — term, fields, filters, sort, paging — then attaching the chosen documents to a room in one audited write. | Open **Library search**, refine the query, select the results, and attach them to the room. |
| Template generator (WF-012) | A template shell of `{{ variable }}` blocks, a generation preview that writes nothing, and a personalised, audited room that stays a draft unless you ask for publication. | Open **Template generator**, declare the shell, preview the generation, then generate. Templates themselves never get a public link. |
| Conditional rules (WF-013) | Show-or-hide blocks driven by the variables supplied at personalisation time, with And/Or conditions, a read-only preview, and a recorded generation-time decision. | Open **Conditional rules**, build a *show block if* condition, preview it, and the decision is recorded when the room is generated. |

### Access and delivery

| Feature | What it is | How you use it |
|---|---|---|
| Invite buyers (WF-004) | Sharing by email: one role and one expiry for the whole invitation, a 48-hour acceptance window, and a Who Has Access list where only the owner may hand out Room Collaborator. | Open **Invite buyers**, enter addresses, choose role and expiry, send. Manage the list from the same page; the owner can never be removed. |
| Publish (WF-011) | Room lifecycle — draft, live, accepting, accepted, disabled, declined — with a public link, expiry, view limit, password and identity checks, plus subscriber notifications on each transition. | Open **Publish**, move the room through its states, copy the link for the buyer, and set the bounds while you are there. |
| Access and identity (WF-015) | Three assurance tiers in front of a room — open, identification only, or verified by email — with an approved-domain allowlist on top and a template default that inheriting rooms follow. | Open **Access and identity**, choose the tier and the allowlist. A delivery outbox makes the verification round trip work on an installation with no mail server. |
| White-label (WF-017) | Serving a room from the customer's own domain, with a non-removable link secret so every link already shared keeps working. | Open **White-label**, point a CNAME at this deployment, verify it, and share links switch host automatically. |

### Analytics and reporting

| Feature | What it is | How you use it |
|---|---|---|
| Analytics (WF-006) | Consolidated engagement across the pipeline, per-room drill-down, a shared timeline, and alerts for low engagement or approaching deadlines. | Open **Analytics** to prioritise follow-up, then drill into a room. |
| PDF analytics (WF-018) | Per-page average dwell and a drop-off curve for every multi-page PDF, average watch time for self-hosted video, and core counts counted buyers-only with shares the one internal metric. | Open **PDF analytics** to see where a buyer stopped reading. |
| Content influence (WF-019) | Portfolio metrics, engagement trend at five grains, a sortable top-content table, and a per-asset revenue breakdown that names missing CRM links rather than showing a confident zero. | Open **Content influence**, sort the top-content table, and read the coverage gaps honestly. |
| Viewing sessions (WF-020) | Tab-level viewing sessions from the Reporting v2 extraction, merged on an incremental `modifiedAt` watermark, answering dwell-time, geography, internal-versus-external and per-user questions. | Open **Viewing sessions**, or pull the same rows into your warehouse for BI. |
| Trend (WF-021) | A Hot / Warm / Cooling / Cold value per workspace, derived on the researched 7 / 14 / 30-day windows, showing the arithmetic and the decay if nothing else happens. | Open **Trend** to see which workspaces are cooling and why the number says so. |
| Pipeline triage (WF-022) | The Workspaces dashboard as saved views: add one from a default, clone it, filter and sort the joined rows, rearrange columns, keep it private or share it. | Open **Pipeline triage**, build the view you actually want, save it, and reopen it tomorrow. |
| Sales impact (WF-023) | Pipeline-weighted rollup over Sales-type workspaces with a CRM deal attached, joined to buyer engagement: close rate, revenue, days to close, and a coverage panel naming what is missing. | Open **Sales impact** for the deal-level view, and read the coverage panel before quoting the numbers. |
| Client engagement (WF-024) | Portfolio-wide client views and actions, average unique clients per workspace, views over time, most engaged clients, expandable to a sortable account list, plus team usage and implementations reports. | Open **Client engagement** and expand a metric down to the accounts and individuals behind it. |

### Engagement signals and automation

| Feature | What it is | How you use it |
|---|---|---|
| CRM sync (WF-016) | Room events pushed to a CRM over webhooks, or described as a *When / Do this* automation, both reporting into an activity log that says which rows need a human. | Open **CRM sync**, register the endpoint or write the automation, then watch the activity log. |
| Event stream (WF-025) | Signed HTTPS endpoints subscribed to researched workspace activity types, with full delivery visibility: what was sent, what the subscriber answered, and where the retry ladder goes next. | Open **Event stream**, register the endpoint, choose the event types, and inspect each delivery. |
| Seller activity feed (WF-026) | Qualifying DSR events mapped onto configured Outreach custom events, posted to the prospect activity feed with a deep link back into the room, and intent recorded coming back out. | Open **Seller activity feed**, configure the event mapping, and follow the deep link straight back to the room. |
| Intent signals (WF-027) | Signal types registered once per integration, a live signal per qualifying buyer interaction, and a rendered description on the seller's live feed, with urgency driving priority. | Open **Intent signals** to see what fired, why, and at what urgency. A signal informs; it makes nobody do anything. |
| Play automations (WF-028) | A Play framework registered against a signal: a matching signal creates a one-off call, email or cadence step with no human in the loop, with assignment on the researched User / Content / Person / Account precedence. | Open **Play automations**, register the Play against a signal, and switch it on. Outcomes are tracked over webhooks on the researched retry schedule. |
| CRM workflows (WF-030) | Contact-based CRM workflows triggered on the five published DSR filter families, refined by the keys that family publishes, with the four researched actions. | Open **CRM workflows**, write the workflow and publish it; DSR activity then drives it with no further setup. Actions are recorded, not executed. |
| Intent stream (WF-032) | Segments pointed at webhook destinations, with every matching company visit accounted for: first send or update, which contacts survived the filters, and where the rules said not to send. | Open **Intent stream**, save a segment, attach a destination, and read the delivery rows, including the retries a downed destination needs. |

### CRM connection and synchronisation

| Feature | What it is | How you use it |
|---|---|---|
| CRM connections (WF-034) | OAuth 2.0 authorization-code flow for a Salesforce, HubSpot or Dynamics org, with the refresh token sealed in a vault keyed by org id, refreshed on the stored TTL and verified by a low-cost authenticated call. | Open **CRM connections**, authorise the org, and check the token verification result. |
| Field mapping (WF-035) | The mapping grid: choose the CRM object, map each sales-room field to a CRM property with a direction and a named transform, pin the sync key that carries the room's row id, and validate against the CRM's own property metadata before anything is written. | Open **Field mapping**, build the mapping, pin the sync key, and validate. Nothing is written from this page. |
| CRM provisioning (WF-036) | Installing a versioned manifest into a connection: read the live schema, create only what is missing, request the sync key where one is sourced, and record the room-object to CRM-object mapping. | Open **CRM provisioning** and run the installer. Re-running is a no-op; no field is ever renamed or dropped. |
| CRM engagement log (WF-037) | A buyer's engagement recorded in the room, the CRM write enqueued, and a worker that resolves the buyer, maps the fields and creates the CRM row — storing the id it returns. | Open **CRM engagement log** to record an event and read the Sync log for everything that will not send. |
| Batch upsert (WF-038) | Pending engagement rows chunked into vendor-sized batches keyed on the external or alternate ID, with a created, updated, failed or unconfirmed outcome per row, and per-row PATCH fallback where a table cannot take a batch. | Open **Batch upsert** to push a room's pending rows and read the outcome for each. |
| Opportunity bundle (WF-039) | Account, contact and opportunity committed as one request through Salesforce Composite, sObject Tree, a Dataverse changeset or HubSpot's documented sequence — `allOrNone` rollback, `collateSubrequests` ordering. | Open **Opportunity bundle**, declare the related records as ordered subrequests, and check the preview of the subrequest order before sending. |
| Sync log (WF-040) | Per-record outcomes from all three vendors normalised into one room error model, naming the offending property on each failed row, with an admin able to compare what was sent against what was expected. | Open **Sync log** to see partial failures, then retry the failed rows only. |
| Duplicate guard (WF-041) | An inbound lead checked against the CRM's duplicate rule, then blocked, updated, or deliberately duplicated according to the connection's policy, with the decision logged alongside the matched record id. | Open **Duplicate guard** to set the policy and read the decision log. |
| Backfill history (WF-045) | A room's CRM history read into its replica on a schedule — asynchronous extract for volume, delta or paged read otherwise — with a resumable cursor so a crash restarts with no duplicates and no gaps. | Open **Backfill history**, schedule the run, and watch the cursor. A run that cannot be resumed says so. |

## Getting started

The commands below assume a POSIX shell; on Windows, use `.venv\Scripts\` in
place of `.venv/bin/`.

```sh
# 1. Backend environment
python3 -m venv .venv
.venv/bin/pip install -e "backend[dev]"

# 2. Demo data into a throwaway database
DSR_DB_PATH=data/dsr.db .venv/bin/python backend/seed.py

# 3. Serve the API and the built frontend together on :8000
.venv/bin/python -m uvicorn dsr.api:app --app-dir backend --host 127.0.0.1 --port 8000

# 4. Backend tests
cd backend && ../.venv/bin/python -m pytest
```

For frontend work, run the Vite dev server on :5173; it proxies `/api` to
:8000, which keeps the browser same-origin exactly as in production.

```sh
cd frontend && npm install
npm run dev      # development, with the /api proxy
npm run build    # production build, served by the backend
npm test         # Vitest
```

Other useful entry points:

```sh
.venv/bin/python tools/verify_localhost.py     # verify what a browser would load, headlessly
.venv/bin/python tools/jev.py doctor           # check the validation gate transport
.venv/bin/python orchestration/make_status.py  # regenerate orchestration/STATUS.md
```

## Adding a workflow

Read [`docs/FEATURE-CONTRACT.md`](docs/FEATURE-CONTRACT.md) before writing any
code. In short: add `backend/dsr/features/<ticket>_<slug>.py` and
`frontend/src/features/<id>/index.jsx`, add `backend/tests/test_<ticket>.py`,
import dependencies from `dsr.deps` rather than `dsr.api`, export a `seed()`
for your own demo data, and check that your branch touches none of the shared
files:

```sh
.venv/bin/python tools/check_feature_diff.py --base origin/main
```

## Programme status

The target is one hundred workflows built from 141 researched specifications.
[`orchestration/STATUS.md`](orchestration/STATUS.md) is generated, not
maintained by hand: it records the workflows on `main`, the mounted route
count, the test tally, features that failed to load, and what is in flight.
Read it for live figures rather than trusting any count quoted here.

## Licence

Distributed under the GNU General Public License v3.0. See
[`LICENSE`](LICENSE) for the full text.
