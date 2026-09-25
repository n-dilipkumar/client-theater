# Digital Sales Room - workflow corpus

Every workflow below was researched against primary vendor or open-source
sources before being admitted. Each row links to a page carrying that evidence.

## Summary

- **Distinct workflows:** 138
- **Domains:** 8
- **Distinct source URLs:** 390
- **Duplicates merged after Jev review:** 6

Duplicate handling is recorded rather than silent: Jev was asked, per candidate
cluster, whether the entries were one capability or several. Merges and the
clusters judged distinct are both listed in `dedupe-decisions.json`.

## Merged duplicates

- `room-experience#06` absorbs 1 duplicate(s): `analytics-intent#01`
- `room-experience#06` absorbs 1 duplicate(s): `analytics-intent#02`
- `analytics-intent#11` absorbs 1 duplicate(s): `crm-integration#15`
- `quoting-proposals#10` absorbs 1 duplicate(s): `room-experience#16`
- `security-governance#01` absorbs 1 duplicate(s): `quoting-proposals#12`
- `room-experience#15` absorbs 1 duplicate(s): `security-governance#02`

## Room experience and content

_(17 workflows, domain `room-experience`)_

| Ticket | Workflow | Sources | Merged |
| --- | --- | --- | --- |
| [`WF-001`](wf/WF-001.md) | Create a Digital Sales Room from an account and template | 5 |  |
| [`WF-002`](wf/WF-002.md) | Build the room's buyer-facing pages from DSR fragments | 4 |  |
| [`WF-003`](wf/WF-003.md) | Populate and govern the room's document library | 6 |  |
| [`WF-004`](wf/WF-004.md) | Invite buyers to a room with a role and an access expiry | 3 |  |
| [`WF-005`](wf/WF-005.md) | Archive and restore a room | 7 |  |
| [`WF-006`](wf/WF-006.md) | Review buyer engagement and prioritise follow-up | 11 | analytics-intent#01, analytics-intent#02 |
| [`WF-007`](wf/WF-007.md) | Ingest a document or deck into the content library | 6 |  |
| [`WF-008`](wf/WF-008.md) | Auto-sync an external cloud file into the content library | 5 |  |
| [`WF-009`](wf/WF-009.md) | Approve and publish library content, immediately or on schedule | 7 |  |
| [`WF-010`](wf/WF-010.md) | Search the content library to assemble a room | 3 |  |
| [`WF-011`](wf/WF-011.md) | Take a room from draft to live and hand over the link | 8 |  |
| [`WF-012`](wf/WF-012.md) | Generate a personalised room programmatically from a template | 13 |  |
| [`WF-013`](wf/WF-013.md) | Personalise room content with conditional rules | 3 |  |
| [`WF-014`](wf/WF-014.md) | Expire or cap access to a room | 9 |  |
| [`WF-015`](wf/WF-015.md) | Verify buyer identity and restrict by email domain | 4 | security-governance#02 |
| [`WF-016`](wf/WF-016.md) | Sync room events to the CRM via webhooks and automations | 7 |  |
| [`WF-017`](wf/WF-017.md) | White-label rooms on a custom domain | 3 |  |

## Buyer engagement analytics and intent

_(16 workflows, domain `analytics-intent`)_

| Ticket | Workflow | Sources | Merged |
| --- | --- | --- | --- |
| [`WF-018`](wf/WF-018.md) | Read per-page dwell time and drop-off inside a PDF | 3 |  |
| [`WF-019`](wf/WF-019.md) | Rank content influence and associate revenue with assets | 4 |  |
| [`WF-020`](wf/WF-020.md) | Extract DSR viewing sessions (dwell time + geography) for BI | 5 |  |
| [`WF-021`](wf/WF-021.md) | Classify workspace engagement health (Hot / Warm / Cooling / Cold) | 2 |  |
| [`WF-022`](wf/WF-022.md) | Triage the pipeline with saved workspace views | 4 |  |
| [`WF-023`](wf/WF-023.md) | Relate buyer engagement to CRM pipeline and close rate | 4 |  |
| [`WF-024`](wf/WF-024.md) | Roll up client engagement and multi-threading portfolio-wide | 5 |  |
| [`WF-025`](wf/WF-025.md) | Stream workspace activity events to your own systems in real time | 8 |  |
| [`WF-026`](wf/WF-026.md) | Write DSR events into the seller activity feed | 8 | crm-integration#15 |
| [`WF-027`](wf/WF-027.md) | Emit buyer intent signals with indicators, urgency, and attribution | 6 |  |
| [`WF-028`](wf/WF-028.md) | Turn a signal into an automatic seller action (Play registration) | 5 |  |
| [`WF-029`](wf/WF-029.md) | Score DSR activity as CRM lead-score criteria | 3 |  |
| [`WF-030`](wf/WF-030.md) | Fire CRM workflows off DSR activity | 4 |  |
| [`WF-031`](wf/WF-031.md) | Identify anonymous web visitors as companies and filter by pages visited | 5 |  |
| [`WF-032`](wf/WF-032.md) | Stream identified company/contact intent to your own systems | 5 |  |
| [`WF-033`](wf/WF-033.md) | Auto-add and continuously track in-market companies from intent signals | 2 |  |

## CRM integration and synchronisation

_(17 workflows, domain `crm-integration`)_

| Ticket | Workflow | Sources | Merged |
| --- | --- | --- | --- |
| [`WF-034`](wf/WF-034.md) | Connect a CRM org to the sales room (OAuth 2.0 authorization code) | 7 |  |
| [`WF-035`](wf/WF-035.md) | Map sales-room fields onto CRM fields and define the sync key | 5 |  |
| [`WF-036`](wf/WF-036.md) | Provision the sales-room engagement object and its fields into the CRM | 6 |  |
| [`WF-037`](wf/WF-037.md) | Log a single buyer engagement event into the CRM | 5 |  |
| [`WF-038`](wf/WF-038.md) | Batch-upsert engagement rows keyed on the external ID | 6 |  |
| [`WF-039`](wf/WF-039.md) | Write account + contact + opportunity as one atomic transaction | 6 |  |
| [`WF-040`](wf/WF-040.md) | Surface partial failures and reject invalid writes before commit | 5 |  |
| [`WF-041`](wf/WF-041.md) | Detect and block duplicate records during sync | 7 |  |
| [`WF-042`](wf/WF-042.md) | Pull CRM deal, account and contact data into the room for display | 6 |  |
| [`WF-043`](wf/WF-043.md) | Stream CRM record changes into the room in near real time | 8 |  |
| [`WF-044`](wf/WF-044.md) | Emit a webhook out of the CRM when a deal stage changes | 3 |  |
| [`WF-045`](wf/WF-045.md) | Backfill historical records on a schedule with a resumable cursor | 6 |  |
| [`WF-046`](wf/WF-046.md) | Throttle and retry under vendor API rate limits | 6 |  |
| [`WF-047`](wf/WF-047.md) | Mirror room documents into CRM files | 3 |  |
| [`WF-048`](wf/WF-048.md) | Validate the connector against a sandbox or test account | 3 |  |
| [`WF-049`](wf/WF-049.md) | Monitor integration health and remaining API quota | 6 |  |
| [`WF-050`](wf/WF-050.md) | Reconcile gaps and overflows after a dropped change stream | 3 |  |

## Scheduling and meetings

_(18 workflows, domain `scheduling-meetings`)_

| Ticket | Workflow | Sources | Merged |
| --- | --- | --- | --- |
| [`WF-051`](wf/WF-051.md) | Route and book a demo request inline from a web form | 5 |  |
| [`WF-052`](wf/WF-052.md) | Qualify a lead without offering any calendar | 2 |  |
| [`WF-053`](wf/WF-053.md) | Route a booking to the owner of the CRM record | 3 |  |
| [`WF-054`](wf/WF-054.md) | Spread bookings across a team by availability-weighted round robin | 3 |  |
| [`WF-055`](wf/WF-055.md) | Hand a lead off from an SDR scheduler to an AE | 3 |  |
| [`WF-056`](wf/WF-056.md) | Book a meeting with no scheduling UI (headless / AI agent) | 3 |  |
| [`WF-057`](wf/WF-057.md) | Find a time that works for a multi-person panel | 6 |  |
| [`WF-058`](wf/WF-058.md) | Embed a bookable calendar inside the sales room / app | 5 |  |
| [`WF-059`](wf/WF-059.md) | Provision a per-booking video-conference link (Meet / Zoom / Teams / Gong) | 8 |  |
| [`WF-060`](wf/WF-060.md) | Auto-join and record the meeting, gated by recording consent | 3 |  |
| [`WF-061`](wf/WF-061.md) | Send conditional pre- and post-meeting reminders and SMS nudges | 2 |  |
| [`WF-062`](wf/WF-062.md) | Route a requested slot for host approval before confirming | 3 |  |
| [`WF-063`](wf/WF-063.md) | Reassign a booked meeting to a different host | 4 |  |
| [`WF-064`](wf/WF-064.md) | Reschedule or cancel a meeting and propagate the change | 5 |  |
| [`WF-065`](wf/WF-065.md) | Write the booking back into the CRM | 5 |  |
| [`WF-066`](wf/WF-066.md) | Push meeting lifecycle events to downstream systems | 3 |  |
| [`WF-067`](wf/WF-067.md) | Collect mutual-action-plan approval by e-signature and track it | 12 |  |
| [`WF-068`](wf/WF-068.md) | Prepare for the meeting, then run the post-meeting follow-up sequence | 8 |  |

## Security, access and governance

_(17 workflows, domain `security-governance`)_

| Ticket | Workflow | Sources | Merged |
| --- | --- | --- | --- |
| [`WF-069`](wf/WF-069.md) | Gate each buyer link with a password, an expiry and email verification | 5 | quoting-proposals#12 |
| [`WF-070`](wf/WF-070.md) | Require NDA acceptance before the buyer sees anything | 3 |  |
| [`WF-071`](wf/WF-071.md) | Stamp a dynamic, per-viewer watermark on every page | 3 |  |
| [`WF-072`](wf/WF-072.md) | Enforce view-only access and block bulk download | 3 |  |
| [`WF-073`](wf/WF-073.md) | Apply confidential view and block screenshot / screen-record shortcuts | 3 |  |
| [`WF-074`](wf/WF-074.md) | Scope visibility to an audience with per-item view and download permissions | 4 |  |
| [`WF-075`](wf/WF-075.md) | Review engagement: verification state, per-page dwell, geo, device and downloads | 4 |  |
| [`WF-076`](wf/WF-076.md) | Revoke access early and keep the audit row | 5 |  |
| [`WF-077`](wf/WF-077.md) | Manage internal workspace roles and least-privilege integration scopes | 5 |  |
| [`WF-078`](wf/WF-078.md) | Require recipient identity verification before opening or signing | 4 |  |
| [`WF-079`](wf/WF-079.md) | Export a tamper-evident audit trail with IP and verification outcomes | 3 |  |
| [`WF-080`](wf/WF-080.md) | Download the executed agreement from the e-vault, webhook-driven | 3 |  |
| [`WF-081`](wf/WF-081.md) | Expire an agreement and drive pre-expiry reminders | 3 |  |
| [`WF-082`](wf/WF-082.md) | Verify and IP-allowlist inbound provider webhooks | 4 |  |
| [`WF-083`](wf/WF-083.md) | Record buyer sessions behind a consent gate, masked, IP-excluded and auto-purged | 3 |  |
| [`WF-084`](wf/WF-084.md) | Federate staff SSO and auto-provision / deprovision via SCIM | 2 |  |
| [`WF-085`](wf/WF-085.md) | Meet GDPR / CCPA: region residency, retention limits, DSAR and consent tooling | 3 |  |

## Pricing, quoting and proposals

_(18 workflows, domain `quoting-proposals`)_

| Ticket | Workflow | Sources | Merged |
| --- | --- | --- | --- |
| [`WF-086`](wf/WF-086.md) | Author a quote from a deal or opportunity | 8 |  |
| [`WF-087`](wf/WF-087.md) | Curate a product and price-book catalogue with tiered pricing | 6 |  |
| [`WF-088`](wf/WF-088.md) | Auto-assign the correct price book or price list to a deal by rule | 3 |  |
| [`WF-089`](wf/WF-089.md) | Quote in a transaction currency with FX conversion and same-currency constraints | 7 |  |
| [`WF-090`](wf/WF-090.md) | Enforce configuration and discount guardrails with quote rules before publish | 3 |  |
| [`WF-091`](wf/WF-091.md) | Route a discounted quote for standard approval | 5 |  |
| [`WF-092`](wf/WF-092.md) | Chain sequential multi-level approvals through a workflow | 5 |  |
| [`WF-093`](wf/WF-093.md) | Build a branded proposal from a template | 7 |  |
| [`WF-094`](wf/WF-094.md) | Publish and share the quote as a hosted link or email | 7 |  |
| [`WF-095`](wf/WF-095.md) | Collect acceptance by e-signature, countersignature, signer reassignment and identity verification | 8 | room-experience#16 |
| [`WF-096`](wf/WF-096.md) | Accept a quote without a signature and take payment in the quote | 5 |  |
| [`WF-097`](wf/WF-097.md) | Track buyer engagement on a shared quote and drive follow-up | 3 |  |
| [`WF-098`](wf/WF-098.md) | Expire a quote and auto-send buyer reminders | 5 |  |
| [`WF-099`](wf/WF-099.md) | Auto-create a contract from an accepted quote | 4 |  |
| [`WF-100`](wf/WF-100.md) | Create a renewal quote from a contract and auto-create the renewal deal | 3 |  |
| [`WF-101`](wf/WF-101.md) | Convert an accepted quote into an order and lock the price | 6 |  |
| [`WF-102`](wf/WF-102.md) | Revise a quote to create a new revision instead of losing the deal | 5 |  |
| [`WF-103`](wf/WF-103.md) | Generate a proposal from CRM data via a document API, gate it on internal approval, and sync status back | 10 |  |

## Automations and engagement

_(17 workflows, domain `automation-engagement`)_

| Ticket | Workflow | Sources | Merged |
| --- | --- | --- | --- |
| [`WF-104`](wf/WF-104.md) | Drip a multi-channel nurture sequence with in-app → email fallback | 6 |  |
| [`WF-105`](wf/WF-105.md) | Re-enter a recurring series and tag the contact on completion | 3 |  |
| [`WF-106`](wf/WF-106.md) | Trigger outreach when a prospect repeatedly browses a high-intent page | 4 |  |
| [`WF-107`](wf/WF-107.md) | Chase unresponsive buyers and reroute conversations from unresponsive reps | 4 |  |
| [`WF-108`](wf/WF-108.md) | Run SLA response timers that respect office hours and pause rules | 4 |  |
| [`WF-109`](wf/WF-109.md) | Escalate just before / when an SLA target is breached | 4 |  |
| [`WF-110`](wf/WF-110.md) | Escalate a conversation converted into a ticket to the owning team | 3 |  |
| [`WF-111`](wf/WF-111.md) | Send a multi-channel reminder: push notification plus in-app inbox fallback | 3 |  |
| [`WF-112`](wf/WF-112.md) | Notify the internal team in Slack from a buyer signal | 5 |  |
| [`WF-113`](wf/WF-113.md) | Ingest third-party data by webhook and fan it out as events | 4 |  |
| [`WF-114`](wf/WF-114.md) | Start a workflow from an external system's webhook | 5 |  |
| [`WF-115`](wf/WF-115.md) | Publish custom workflow triggers and actions to an app marketplace | 6 |  |
| [`WF-116`](wf/WF-116.md) | Create an automated seller follow-up task with a reminder | 4 |  |
| [`WF-117`](wf/WF-117.md) | Sweep stale opportunities by an inactivity rule and escalate | 4 |  |
| [`WF-118`](wf/WF-118.md) | Order and throttle in-app guide prompts, and re-prompt after dismissal | 5 |  |
| [`WF-119`](wf/WF-119.md) | Gate an automation on an external human approval, then resume | 4 |  |
| [`WF-120`](wf/WF-120.md) | Enroll records on a schedule with re-enrollment control and backfill | 3 |  |

## Competitive baseline

_(18 workflows, domain `competitive-baseline`)_

| Ticket | Workflow | Sources | Merged |
| --- | --- | --- | --- |
| [`WF-121`](wf/WF-121.md) | Tracked, per-recipient document link with page-level read analytics | 9 |  |
| [`WF-122`](wf/WF-122.md) | Structured data room with staged, tiered disclosure | 6 |  |
| [`WF-123`](wf/WF-123.md) | In-room qualifying form / buyer data capture | 5 |  |
| [`WF-124`](wf/WF-124.md) | Native Mutual Action Plan with owners, timelines and reminders | 7 |  |
| [`WF-125`](wf/WF-125.md) | Bi-directional CRM sync with room creation on CRM conditions | 6 |  |
| [`WF-126`](wf/WF-126.md) | Seller-recorded video intro and in-room video walkthrough | 7 |  |
| [`WF-127`](wf/WF-127.md) | Interactive product demo with activity heatmaps and organic stakeholder discovery | 4 |  |
| [`WF-128`](wf/WF-128.md) | Live co-presented walkthrough inside the deal | 7 |  |
| [`WF-129`](wf/WF-129.md) | In-room pricing, quote and signable order form | 6 |  |
| [`WF-130`](wf/WF-130.md) | Generated, personalised collateral from deal data (AI document generation) | 9 |  |
| [`WF-131`](wf/WF-131.md) | Content protection: watermarking, download control, NDA gating, audit log | 8 |  |
| [`WF-132`](wf/WF-132.md) | In-room collaboration: comments, threads, tagging, and champion enablement | 7 |  |
| [`WF-133`](wf/WF-133.md) | Intent signal → seller alert → CRM task (routing workflow) | 9 |  |
| [`WF-134`](wf/WF-134.md) | Engagement-driven deal-health scoring, risk detection and coaching trigger | 9 |  |
| [`WF-135`](wf/WF-135.md) | Conversation intelligence capture and CRM write-back | 8 |  |
| [`WF-136`](wf/WF-136.md) | Buyer self-serve booking of the next step | 5 |  |
| [`WF-137`](wf/WF-137.md) | Governance: versioning, brand control, and the closed-won → onboarding handoff | 8 |  |
| [`WF-138`](wf/WF-138.md) | Branded, custom-domain, white-label buyer experience | 5 |  |
