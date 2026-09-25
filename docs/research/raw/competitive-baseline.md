# Domain 8 — Competitive Baseline: 18 Concrete DSR Workflows

**Research date:** 2026-09-26
**Field schema (used for every workflow below):**
`name` / `user_flow` / `data_flow` / `data_sources` / `apis_hit` / `automations` / `features_tools` / `extensibility` / `sources` / `evidence`

**Sourcing rule:** every `sources:` URL below was fetched directly in this session unless flagged
`NOT VERIFIED`. Vendor marketing copy is quoted as *claim*, and the `evidence:` field states exactly
what the vendor page asserts versus what is independently established.

---

## Market context established during research (read this first)

Three incumbents in the requested list have materially changed, which changes what "the baseline" is:

| Vendor | Finding | Evidence |
|---|---|---|
| **Recapped** | **Shutting down 31 July 2026**, no new customers, after 7 years. | recapped.io homepage banner: *"Unfortunately, after 7 long years Recapped is shutting down 7/31/2026 and will not be taking on new customers."* |
| **Highspot** | **Now "Highspot by Seismic."** highspot.com: *"Highspot is part of Seismic."* `gethighspot.com` is now **NXDOMAIN**. | highspot.com homepage |
| **DocuSign Rooms** | **Rebranded into "Docusign IAM" (Intelligent Agreement Management).** `/products/rooms`, `/products/docusign-rooms`, `/products/rooms/overview` all return **404** and "Rooms" is absent from the product menu. `developers.docusign.com/docs/rooms-api/` still resolves. | docusign.com fetch 404s; developers.docusign.com |
| **Dealfront** | **Rebranded to Leadfeeder.** dealfront.com: *"Dealfront is now Leadfeeder. Same powerful solutions. Same team you trust. New brand."* | dealfront.com homepage |
| **Guidebook** | Now *"Campus & Event App Platform for Higher Ed."* Sales-room product absent. | guidebook.com |
| **Fletch** | Now *"Embedded and Agentic Financial Services Distribution."* `fletch.co/docs` → 404. Historical "Limitless" sales room not on site. | fletch.co |
| **GTMKing / Slaytools / Folderware / SpinMe / Onward / Perplex / Humo** | **No reachable primary source.** GTMKing's domain is a parked lander; Slaytools/Folderware are NXDOMAIN; SpinMe's domain is a personal blog; Perplex's is a domain-for-sale; Onward's resolves to a parking IP. See `opensource-landscape.md` §5. | DNS + fetch checks, 2026-09-26 |
| **6sense** | Entire domain, help centre and API docs are behind a Cloudflare JS challenge (403) from this network. **No primary-source verification possible.** Flagged `NOT VERIFIED` where it appears. | fetch 403 with browser and Googlebot UA |

Two additional vendors surfaced from primary sources and are used below as evidence because they
document their DSR features more precisely than the requested list does:
**Walnut** (`help.walnut.io`) and **Consensus** (`goconsensus.com/overview/demolytics`).
**Trumpet** and **Allego** are used as secondary corroboration via Mindtickle's published
comparison (`mindtickle.com/blog/best-digital-sales-room-platforms-comparison-and-review/`,
published 2026-05-19, updated 2026-06-29).

**The evaluation criteria the market has converged on** (Mindtickle, as a competitor, publishes
them): bi-directional CRM sync covering *custom objects*; **native** MAPs that persist past
closed-won; workflow connectors for e-signature and CPQ; granular CRM-synced buyer-intent
analytics; and enterprise security (encryption, RBAC, password protection, email authentication).

---

## 1. Tracked, per-recipient document link with page-level read analytics

- **name:** Tracked deal-link distribution with per-page dwell
- **user_flow:** Rep uploads a deck or PDF → shares a single link → a buyer (often several people behind one link) opens it in an in-browser viewer → the system records *who* viewed, *which page*, *for how long*, on which revisit → the rep is alerted and the engagement record is written back to the CRM opportunity.
- **data_flow:** Upload → object storage → viewer URL → `section_enter` / `section_view` events per page (page ID, visibility ratio, duration, max ratio, next page, viewport, session) → analytics store → (a) rep dashboard, (b) CRM field write-back.
- **data_sources:** Uploaded PDF/PPT/DOCX; the viewer's own telemetry; CRM opportunity + contact records; user identity (email gate or CRM-matched user).
- **apis_hit:** Object storage (S3 / R2 / Vercel Blob); PDF.js for client-side rendering; CRM REST API (Salesforce / HubSpot / Dynamics) for read/write; email (Resend); a web-analytics ingest endpoint.
- **automations:** Link-expiry; per-viewer real-time alert; CRM field sync; auto-notify rep on N-th page view or on return visit.
- **features_tools:** In-browser viewer (no download); optional download; page/slide-level dwell and drop-off; revisits; download events; link-level password and email-verification gate.
- **extensibility:** Open-source analogue: **DeckLens** (Apache-2.0, https://github.com/cloudbtl/DeckLens) — zero-dependency browser SDK, `DeckLens.createTracker({projectId, deckId, endpoint}).start()`, emits `section_session_start` / `section_enter` / `section_view` / `section_session_end` / `action`, and explicitly **does not transmit document bodies or raw form values**. Commercial analogue: Papermark (https://www.papermark.com), Coneshare (https://github.com/coneshare/coneshare), DocuSign Rooms, DealHub DealRoom, Walnut, Dock, Seismic, Recapped.
- **sources:**
  - https://github.com/cloudbtl/DeckLens (README fetched)
  - https://github.com/papermark/papermark (README + LICENSE fetched)
  - https://github.com/coneshare/coneshare (README fetched)
  - https://www.papermark.com/ (fetched)
  - https://dealhub.io/platform/dealroom/ (fetched)
  - https://help.walnut.io/help/deal-rooms/getting-started (fetched)
  - https://www.mindtickle.com/blog/best-digital-sales-room-platforms-comparison-and-review/ (fetched)
  - https://seismic.com/platform/digital-sales-rooms/ (fetched)
- **evidence:** Coneshare's README states verbatim: *"Track views, revisits, and downloads in real time. View page-by-page reading duration and video playback metrics."* DeckLens' event table defines `section_view` as "A visible interval ended" with fields `duration, maximum ratio, next section`. Papermark's site claims *"See exactly which document pages closed the deal, and which lost the room."* Mindtickle, writing as a competitor, defines the category requirement as DSRs that *"track who viewed what, for how long, and surface that data back to the rep in real time."*

---

## 2. Structured data room with staged, tiered disclosure

- **name:** Tiered data room — reveal sections as trust grows
- **user_flow:** Seller assembles a room of documents → each document/section is assigned a **tier** → every recipient starts at tier 0 (the curated room) → the seller unlocks financials, legal or security for specific recipients as diligence progresses → lower-tier recipients see a *teaser* or nothing at all → the room is revoked when the process ends.
- **data_flow:** Documents uploaded → folder tree → per-section `minTier` + per-recipient tier assignment → room render (sections filtered by the viewer's tier) → unlock/revoke events logged.
- **data_sources:** Seller-uploaded documents; per-recipient identity (one link per organisation); seller-set tier policy; deal-stage data from CRM.
- **apis_hit:** Object storage; CRM API to read deal stage and to write "diligence advanced" state; email for invite/revoke; audit-log store.
- **automations:** Auto-create a room on CRM stage change; auto-unlock on milestone; auto-expire / auto-revoke; re-issue of a new link per recipient.
- **features_tools:** Room tree; per-recipient tokens; tiered visibility with teaser/hidden states; section-level access control; version history; revocable links; audit trail.
- **extensibility:** Open-source: **Hyperportal** (MIT, https://github.com/hypersocialinc/hyperportal) implements exactly this model — *"each section has `minTier` and `belowTier: "teaser" | "hidden"`: below-tier recipients…"* and *"One link per counterparty org — `/r/<token>` sets an HttpOnly cookie and redirects, so tokens never live in history or referrers."* Also **Deckly** (AGPL-3.0) for "purpose-built data rooms", and **ONLYOFFICE DocSpace** (AGPL-3.0) / **Pydio Cells** (AGPL-3.0) for room primitives without tiers.
- **sources:** https://github.com/hypersocialinc/hyperportal (README fetched); https://github.com/ManishBlueprints/Deckly (README fetched); https://github.com/ONLYOFFICE/DocSpace (API); https://github.com/pydio/cells (API); https://help.walnut.io/help/deal-rooms/getting-started; https://dealhub.io/platform/dealroom/
- **evidence:** Hyperportal's README states *"Tiered disclosure — each section has `minTier` and `belowTier: "teaser" | "hidden"`"* and *"Sections can show as 'on request' or stay completely invisible until unlocked."* Its CLI *"verifies against the live site before reporting success, instead of trusting a dashboard edit."* Walnut's KB documents a *different but adjacent* primitive — **page-level access**: *"Restrict an individual page to named, verified email addresses so sensitive content sits in the same room as everything else."* No commercial vendor in the requested list documents numeric tiers; they document role-based and page-based access instead.

---

## 3. In-room qualifying form / buyer data capture

- **name:** In-room qualification capture
- **user_flow:** Seller embeds a short form inside the room (budget, timeline, authority, procurement path) → buyer completes it without leaving the room → the responses are attached to the deal record → the rep sees qualification status next to the content analytics and can route on it.
- **data_flow:** Form schema → room-rendered widget → submission → deal/contact record in CRM → qualification score/flag → routing rule.
- **data_sources:** Buyer-entered answers; deal context (account, value, stage); rep-assigned owner; rep's own territory rules.
- **apis_hit:** CRM create/update (custom objects); form service or first-party endpoint; email/Slack for notification.
- **automations:** Auto-route on qualification band; auto-create or update a lead/opportunity; auto-assign owner; auto-trigger a follow-up task.
- **features_tools:** Customisable form fields inside the room; buyer-facing simplicity; auto-save of answers; qualification summary visible to seller only.
- **extensibility:** Open-source substitute: **Formbricks** (https://github.com/formbricks/formbricks — mixed licence: `apps/web/modules/ee` proprietary, `packages/js|android|ios|api` MIT, remainder AGPL). Commercial: DealHub ("Buyer Qualification Insights — Collect key buyer details with customizable DealRoom forms"), Seismic, Mindtickle, Dealfront/Leadfeeder ("Collect key buyer details" / campaign capture), Ebsta ("Qualify Deals Inside Your CRM").
- **sources:** https://dealhub.io/platform/dealroom/ (fetched); https://dealfront.com/ (fetched); https://www.ebsta.com/ (fetched); https://github.com/formbricks/formbricks (LICENSE fetched); https://www.mindtickle.com/blog/best-digital-sales-room-platforms-comparison-and-review/
- **evidence:** DealHub's DealRoom page lists **"Buyer Qualification Insights — Collect key buyer details with customizable DealRoom forms to ensure smooth deal transitions"** and **"Real-time Buyer Intent — Track buyer activity with live updates via email and Slack."** Ebsta's homepage lists **"CRM Automation — Capture every contact, e-mail, call and meeting. Ebsta connects your mailbox, calendar, call recorder and workspace with your CRM."** and a use case titled *"Qualify Deals Inside Your CRM."* ⚠️ The specific in-room form widget is documented only by DealHub; the other vendors document CRM-side qualification, not a room-embedded form.

---

## 4. Native Mutual Action Plan with owners, timelines and reminders

- **name:** Native Mutual Action Plan execution
- **user_flow:** Seller and buyer agree a dated plan → tasks are created with named owners on both sides → each party updates status in-room (list or Kanban) → internal-only tasks stay invisible to the client → overdue items escalate → the plan **survives closed-won** and becomes the implementation plan.
- **data_flow:** MAP template → instantiated per deal → task graph (owner, due date, status, dependency) → status events → reminders → post-sale continuation.
- **data_sources:** Deal milestones; both sides' stakeholder lists and calendars; template library; CRM close date and phase.
- **apis_hit:** CRM (opportunity, tasks, close-date); calendar (reminders); email + in-room notifications; e-signature for a final agreed plan.
- **automations:** Automatic reminders (Recapped markets this explicitly); auto-generation of the plan from a template; auto-conversion of a closed MAP into an onboarding plan; internal-vs-external task visibility rules.
- **features_tools:** Templated plans; list/Kanban views; internal-only tasks; per-task owners and dates; MAP tracked against the room (not in a spreadsheet).
- **extensibility:** This is the **least-served capability in open source — no OSS project implements MAP at all.** Commercial: Dock, Walnut, Recapped, Mindtickle, DealHub, Seismic, Highspot, Allego.
- **sources:** https://www.dock.us/features/mutual-action-plan (fetched); https://www.dock.us/solutions/sales (fetched); https://help.walnut.io/help/deal-rooms/getting-started (fetched); https://recapped.io/ (fetched); https://dealhub.io/platform/dealroom/ (fetched); https://www.mindtickle.com/blog/best-digital-sales-room-platforms-comparison-and-review/; https://www.allego.com/ (fetched)
- **evidence:** Dock's MAP page is the most explicit: *"Templated action plans… Switch between List or Kanban view… Internal-only tasks — Make internal tasks hidden from clients."* It defines MAP as *"a shared roadmap designed to keep sales account executives, sales leaders, buyer reps, and important stakeholders aligned,"* also known as mutual success plans, joint execution plans, go-live plans or close plans. Walnut's KB: *"Build and track MAPs directly within your Deal Room, with sellers and stakeholders updating action items live."* Recapped's homepage: *"Mutual Action Plans — Hold clients accountable with timelines and automatic reminders."* DealHub: *"Mutual Action Plans — Guide buyers with embedded action plans detailing next steps, roles, and timelines."* Mindtickle, as a competitor, calls native MAPs a **disqualifying criterion** if missing: *"Mutual action plans need to be native. The DSR must replace fragmented spreadsheets and support persistent rooms that survive the closed-won transition to customer success."*

---

## 5. Bi-directional CRM sync with room creation on CRM conditions

- **name:** CRM-driven room lifecycle
- **user_flow:** A seller picks a Salesforce Opportunity or HubSpot Deal → the room is generated from CRM fields (name, contacts, value, stage, products) → a **rule** creates rooms automatically when a condition is met → room engagement writes back to the opportunity, account and contact records, **including custom fields** → the rep never logs activity by hand.
- **data_flow:** CRM read (opportunity, contacts, products, stage) → room template instantiation + content population → room engagement events → CRM write to standard *and* custom objects → dashboards keyed off deal id.
- **data_sources:** Salesforce / HubSpot / Dynamics opportunity, contact, account, product, custom objects; room analytics; the seller's room template.
- **apis_hit:** **Salesforce REST/Composite API, HubSpot CRM API, Microsoft Dynamics**; DocuSign (for the contract inside the room); Gong and Slack (DealHub lists all four integrations).
- **automations:** Rule-based room creation from CRM conditions (Walnut: *"Automatically create Deal Rooms using rules based on Salesforce Opportunities or HubSpot Deals"*); automatic CRM write-back of every view/download/share; per-user room provisioning based on role.
- **features_tools:** Deep bi-directional sync incl. custom objects; room templates; per-user room permissions (Walnut roles: *Admin, Editor, Presenter*); integration hub; analytics attached to the deal record.
- **extensibility:** Open-source substitute: a warehouse/event layer — **Snowplow** (Apache-2.0), **RudderStack**, **PostHog** (self-hosted), plus **Apache Superset** (Apache-2.0) or **Metabase** (AGPL outside `enterprise/`) for the dashboard, and **n8n / Node-RED / Windmill / Kestra** for the rule engine. None of these speak Salesforce natively in a way that replaces a native connector.
- **sources:** https://help.walnut.io/help/deal-rooms/getting-started (fetched); https://dealhub.io/platform/dealroom/ + dealhub.io 404 footer listing "TOP INTEGRATIONS Salesforce CRM / Microsoft Dynamics CRM / HubSpot CRM / NetSuite ERP / DocuSign / Gong / Slack / DealHub API" (fetched); https://www.mindtickle.com/blog/best-digital-sales-room-platforms-comparison-and-review/; https://github.com/snowplow/snowplow (API); https://github.com/node-red/node-red (API); https://github.com/n8n-io/n8n (API)
- **evidence:** Walnut's KB is explicit: *"Seamless CRM Integration: Instantly connect Deal Rooms to Salesforce Opportunities or HubSpot Deals"* and *"Deal Room Automation: Automatically create Deal Rooms using rules based on Salesforce Opportunities or HubSpot Deals."* DealHub's own site lists the integration set. Mindtickle states the failure mode bluntly: *"A bi-directional CRM sync is the baseline. Without it, every asset view, download, and share has to be logged manually… The sync should cover custom objects rather than just standard fields, or the data that lands in your CRM is too shallow to act on."*

---

## 6. Seller-recorded video intro and in-room video walkthrough

- **name:** Asynchronous personalised video inside the room
- **user_flow:** A rep records a short personal intro **from inside the room** (or a product team records a walkthrough) → the video is placed on the room's hero section and personalised per buyer → the buyer watches, loops back, and the seller's message lands before a call.
- **data_flow:** Screen/camera capture → transcode → adaptive stream (HLS) → per-viewer playback events (start, % watched, completes, replays) → engagement record on the deal.
- **data_sources:** Rep's camera/mic/screen; room content (case study, demo) being walked through; buyer identity for personalisation.
- **apis_hit:** **LiveKit** (Apache-2.0, https://github.com/livekit/livekit — SFU + egress for compositing/recording/transcoding to HLS) or **Jitsi Meet** (Apache-2.0) or **BigBlueButton** (LGPL-3.0); media storage; CRM write-back.
- **automations:** Auto-transcode and publish on save; auto-embed into new rooms from a template; notify the buyer when a new video lands; alert the rep on low completion.
- **features_tools:** In-room recorder; drag-and-drop placement; per-buyer personalisation; viewer-visible "why I'm reaching out"; playback analytics.
- **extensibility:** Open-source: LiveKit / Jitsi / mediasoup (ISC) for capture and egress; a browser recorder (MediaRecorder API) is sufficient for the intro use case. Commercial: Seismic, Consensus, Allego.
- **sources:** https://seismic.com/platform/digital-sales-rooms/ (fetched); https://goconsensus.com/ (fetched, nav: "On-Demand Video Demos"); https://www.allego.com/ (fetched); https://www.mindtickle.com/blog/best-digital-sales-room-platforms-comparison-and-review/; https://github.com/livekit/livekit (API); https://github.com/jitsi/jitsi-meet (API); https://github.com/versatica/mediasoup (API)
- **evidence:** Seismic's DSR page: *"Personalize — Enable personalized content delivery with an easy-to-use, drag-and-drop design tool. Stand out from the crowd and win buyers' attention with video intros that add a personal touch, easily recorded right within the Digital Sales Room."* and *"Improve the buyer experience — Attract buyers and keep them engaged throughout the deal cycle with personalized video recordings, tailored content, and timely insights."* Consensus' own nav markets **"On-Demand Video Demos — Easily shareable and always available."** Mindtickle describes Allego as *"a unified platform combining sales learning and content sharing… create and inject personalized video recordings into buyer portals, enabling buyers to see and hear the seller directly in the portal."*

---

## 7. Interactive product demo with activity heatmaps and organic stakeholder discovery

- **name:** Interactive demo / sandbox analytics with stakeholder discovery
- **user_flow:** A product marketer records a real product once → the recording is converted into a **branching, sandboxed interactive demo** → the demo is placed in the room or sent as a link → when a buyer re-shares it internally, Consensus surfaces the **newly discovered stakeholders** and their engagement → the seller knows who else is in the room and what they poked at.
- **data_flow:** Screen capture + click-path → interactive demo build → per-viewer event stream (demos sent, stakeholders discovered, discovery rate, views, view rate, features/sections selected, docs downloaded) → six-level analytics roll-up → activity heatmaps → CRM push via integration or open API.
- **data_sources:** The captured product session; the demo's own click-path tree; the initial recipient's forward/intra-viral shares; downstream stakeholder identities; the buyer's collateral downloads.
- **apis_hit:** Consensus' **open API** plus its integration hub; CRM (Salesforce appexchange integration is referenced); Slack; Microsoft 365 App Store distribution; mobile (iPhone).
- **automations:** Auto-generate the interactive demo from a recording; auto-track a DQL (demo-qualified lead) score; auto-alert the seller when a *new* stakeholder appears; auto-route by demo type.
- **features_tools:** Six drill-down levels (buyer-group analysis, top-level performance, team performance, individual performance, most-recently-shared performance, all individual demos); **activity heatmaps**; feature/section-level selection counts; stakeholder discovery rate; DQL scoring; branded interactive tours; on-demand video variants.
- **extensibility:** Open-source: **Excalidraw** (MIT) for a whiteboard layer; a custom event schema like DeckLens' for section-level telemetry; **Apache Superset / Metabase** for the heatmap roll-ups. Consensus itself is **not** open source and offers no self-host.
- **sources:** https://goconsensus.com/overview/demolytics (fetched); https://goconsensus.com/ (fetched); https://github.com/excalidraw/excalidraw (API); https://github.com/apache/superset (API)
- **evidence:** Demolytics' own page: *"See actionable insights on six levels. Drill down from high-level DemoBoard tracking to detailed activity heatmaps and solution specific dashboards."* and *"View stakeholder insights—including demos sent, organic stakeholders discovered, discovery rate, views, view rate, features/sections selected, and docs downloaded—for any recipient of the initial link. All of this seamlessly passes into your other systems with one of our integrations or open API."* and *"Discover stakeholders with intra-viral sharing."* Consensus' homepage claims *"The #1 Demo Platform on G2 and a leader in the Agentic GTM Platforms category."* — vendor claim, not independently verified here.

---

## 8. Live co-presented walkthrough inside the deal

- **name:** In-room live presentation / co-browsing
- **user_flow:** A rep starts a live session from the room → the buyer (or a whole buying committee, each with their own link) joins and follows the presenter through the deck or the actual product → the session is recorded and attached to the room as a replay the buyer can revisit asynchronously → questions asked live are logged against the deal.
- **data_flow:** Presenter screen/audio → SFU (selective forwarding) → per-participant tiles; recording composed via egress → HLS/MP4 → stored with the room; attendance + questions → deal record.
- **data_sources:** Presenter's screen; the room's content; participant identities; the recording artefact.
- **apis_hit:** **LiveKit** (Apache-2.0) with egress; or **Jitsi Meet** (Apache-2.0); or **BigBlueButton** (LGPL-3.0) which is purpose-built for presentation + recording; **mediasoup** (ISC) if building a custom SFU; presentation engine (**reveal.js** MIT, **Slidev** MIT, **impress.js** MIT).
- **automations:** Auto-record and auto-publish; auto-notify invitees; auto-translate captions; auto-clip highlights; attendance written to CRM.
- **features_tools:** Multi-party video; screen share; shared cursor/annotation; chat and Q&A; recording with playback; per-participant attendance; presenter mode.
- **extensibility:** Fully buildable open source — this is the *best-covered* DSR capability in FOSS. LiveKit (Go, Apache-2.0) + reveal.js/Slidev (MIT) + Mediasoup (ISC) is a complete stack. Consensus and Dock ship this commercially ("Live Demo Platform — Deliver live demos with confidence" on Consensus' nav; Dock's rooms include "Watch recording" next to "View notes").
- **sources:** https://goconsensus.com/ (fetched, nav); https://www.dock.us/ (fetched, in-page product demo markup); https://github.com/livekit/livekit (API); https://github.com/jitsi/jitsi-meet (API); https://github.com/bigbluebutton/bigbluebutton (API); https://github.com/hakimel/reveal.js (API); https://github.com/slidevjs/slidev (API)
- **evidence:** Consensus' nav lists **"Live Demo Platform — Deliver live demos with confidence"** alongside **"Interactive Product Demos — Create on-demand product demos."** Dock's homepage product walkthrough markup literally shows a room section labelled "Technical demo / Watch recording" next to "Discovery call / View notes," i.e. the live session and its recording are both first-class room objects. LiveKit's repo verifies as Apache-2.0 and actively pushed (2026-09-25).

---

## 9. In-room pricing, quote and signable order form

- **name:** Quote-to-order-form inside the room, with e-signature
- **user_flow:** Seller generates a price quote or pulls one from CPQ → it renders inside the room as an itemised, buyer-editable line-item document → the buyer accepts and signs **in the room, without a redirect** → the executed order form is filed back on the deal → MAP tasks and close dates advance.
- **data_flow:** CPQ/price-book data → document model (line items, quantities, price per unit, billing frequency, totals) → PDF/DOCX rendering → signature capture (typed/drawn/typed-photo) → signed artefact + audit certificate → CRM + contract system.
- **data_sources:** Product catalogue and price book; quote/CPQ record; buyer legal entity and signatory; currency/tax rules; company branding.
- **apis_hit:** **CPQ** (DealHub's own, or Salesforce CPQ); **DocuSign** for e-sign and redlining; **Documenso** (AGPL-3.0, https://github.com/documenso/documenso) or **OpenSign** (AGPL-3.0 core, https://github.com/OpenSignLabs/OpenSign) if self-hosting signature capture; document generation (Gotenberg/Puppeteer, or `docx`/`PptxGenJS`); Stripe for payment if in-flow.
- **automations:** Auto-generate the quote from deal context; auto-send for signature; auto-advance the MAP; auto-file the signed document; auto-update CRM amount and close date.
- **features_tools:** Branded order forms; in-room e-signature; real-time redlining and mutual revisions; secure permission-based document storage; payment links.
- **extensibility:** Open-source path exists end to end: **Documenso** (AGPL-3.0, 15,194★, active) for signing + **DealHub-style** template rendering via **`docx`**/**Gotenberg**. Note: **DocuSign Rooms' own product pages are now 404** — the capability survives inside Docusign IAM. ⚠️ Vendor feature parity for *in-room* signing with redlining is documented only by DealHub and Dock; Dock also exposes a **ZoomVerify** feature (visible in its sitemap at `dock.us/zoomverify`) and **Slack**/**Microsoft Teams** notifications.
- **sources:** https://dealhub.io/platform/dealroom/ (fetched); https://www.dock.us/ (fetched + `https://www.dock.us/sitemap.xml`, 1,221 URLs incl. `/features/mutual-action-plan`, `/product/order-forms`, `/product/ai-documents`, `/features/slack`, `/features/microsoft-teams`, `/zoomverify`); https://github.com/documenso/documenso (API); https://github.com/OpenSignLabs/OpenSign (API + LICENSE); https://www.docusign.com/products/rooms (fetched, 404)
- **evidence:** DealHub lists **"Built-in eSignature — Accelerate deal closure with fast, secure signatures via built-in eSign and DocuSign integration"** and **"Contract Collaboration — Streamline contract workflows with real-time redlining and mutual revisions and document sharing."** Dock's homepage markup shows a live order form with line items, quantities, prices, billing frequency and totals plus a `Sign / Signed` control. Dock's nav also lists **"Order Forms & Price Quotes."** DocuSign's own product menu no longer contains Rooms (see Market context).

---

## 10. Generated, personalised collateral from deal data (AI document generation)

- **name:** Data-driven document/deck generation into the room
- **user_flow:** A seller describes the deal (or the AI reads the CRM record) → the platform assembles a personalised room or document — demos, docs and a mutual action plan, populated from CRM → the seller reviews and edits → publish. Repeat deals start from a template, not a blank page.
- **data_flow:** CRM opportunity/contact/product records + seller prompt or template → LLM or rules assembly → content blocks + generated PDF/PPTX → review/edit → publish to room + brand controls applied.
- **data_sources:** CRM record; seller's content library (case studies, decks, pricing); brand/theme tokens; template set.
- **apis_hit:** CRM API (Salesforce / HubSpot); LLM provider; document generation (`docx`, **PptxGenJS** MIT, Gotenberg, pdf.js for preview); storage; the room's publish API.
- **automations:** Auto-generate on deal creation; auto-refresh content in every live room when the source asset changes (Dock: *"Synced Sections — Push content changes—like updated case studies or new demo videos—across all active deals instantly"*); auto-unhide sections at the right stage.
- **features_tools:** Company-wide room templates; synced sections; **hidden sections revealed as the deal progresses**; AI content generation; AI document generation; AI enablement agent; Conversational AI content creation; version history / branch.
- **extensibility:** Open-source: any LLM + `docx`/`PptxGenJS`/pdf.js; **Slidev** (MIT) and **Marp** (MIT) for generated decks; **AppFlowy** (AGPL-3.0) / **AFFiNE** (MIT outside `packages/backend`) for a workspace. Commercial: Walnut, Dock, Highspot (AutoDocs), Consensus, DealHub, Fletch (Studio).
- **sources:** https://help.walnut.io/help/deal-rooms/getting-started (fetched); https://www.dock.us/solutions/sales (fetched); https://www.walnut.io/digital-sales-room-software/ (fetched); https://www.highspot.com/ (fetched); https://dealhub.io/platform/dealroom/ (fetched); https://fletch.co/ (fetched); https://github.com/gitbrent/PptxGenJS (LICENSE fetched); https://github.com/slidevjs/slidev (API); https://github.com/marp-team/marp (API)
- **evidence:** Walnut's own page: *"describe the deal and Walnut's AI assembles the room — demos, docs and a mutual action plan, personalized from your CRM. You review, edit and p…"* (truncated in source). Dock's sales page: *"Sales Room Templates — Launch personalized sales rooms in a few clicks"*; *"Synced Sections — Automatically keep every workspace up to date"*; *"Hidden Sections — Reveal relevant content as the deal progresses. Reps can unhide pre-made sections like pricing, legal, or security only when the buyer's ready."* Highspot's nav lists **"AutoDocs"**, **"Highspot Agents"** (*Deal Agent*, *GTM Agent*) and an **"MCP Server."** Fletch's current page documents **"Studio — Design and update branded acquisition journeys without rebuilds. No-code, no waiting on engineering."** PptxGenJS' MIT licence is verified on `master`.

---

## 11. Content protection: watermarking, download control, NDA gating, audit log

- **name:** Per-recipient content protection and auditability
- **user_flow:** Before a buyer can see anything, the room presents a gate — email verification, passcode, or an NDA to accept → each viewer's session is watermarked with their identity (dynamic watermark) → downloads can be disabled entirely or allowed per-file → every action is written to an immutable audit log → the seller can allow-list IPs or revoke a link, and it dies immediately.
- **data_flow:** Gate challenge → verified viewer identity → per-session watermark applied at render (server-side burn-in or overlay) → action log (view/download/share/revoke) → audit store; revocation → token invalidated at the edge.
- **data_sources:** Viewer identity (email, IP, passcode); the document; the NDA artefact and its acceptance record; the seller's policy (expiry, allow-list, download rules).
- **apis_hit:** Identity provider (Keycloak Apache-2.0, Zitadel AGPL-3.0); e-signature for the NDA (**Documenso** / **OpenSign** / DocuSign); storage with signed, short-lived URLs; IP allow-list enforcement at the edge; audit-log sink.
- **automations:** Auto-watermark on render; auto-expire at the configured date; auto-revoke on demand; auto-alert on a bulk-download pattern; auto-retention/deletion at the end of the data-lifecycle window.
- **features_tools:** Email verification; passcode; NDA acceptance; dynamic + forensic watermarking; download on/off; IP allow-listing; end-to-end encryption; audit logs; RBAC; custom buyer privacy settings; certifications.
- **extensibility:** Open-source: **Coneshare** (MIT) ships *"Password protection, link expiration dates, and email verification. Download restrictions and dynamic watermarks."* **Papermark** (AGPL-3.0 core) markets the same set but the SSO/watermark/NDA-gating claims are almost certainly in its proprietary `ee/` directories. Ident: **Keycloak** (Apache-2.0) / **Zitadel** (AGPL-3.0). Signing: **Documenso** (AGPL-3.0). Edge: Caddy/nginx. ⚠️ **No open-source project implements a full audit log with revocation semantics.** Coneshare is the closest.
- **sources:** https://github.com/coneshare/coneshare (README + LICENSE fetched); https://www.papermark.com/ (fetched); https://seismic.com/platform/digital-sales-rooms/ (fetched); https://dealhub.io/platform/dealroom/ (fetched); https://github.com/papermark/papermark (LICENSE fetched); https://github.com/keycloak/keycloak (API); https://github.com/zitadel/zitadel (API); https://github.com/documenso/documenso (API)
- **evidence:** Coneshare's README: *"Password protection, link expiration dates, and email verification. Download restrictions and dynamic watermarks."* Papermark's site **claims** *"Every room ships with SSO, granular permissions, dynamic watermarking, NDA gating, audit logs, IP allow-listing, end-to-end encryption and full data lifecycle controls"* with SOC 2 Type II / ISO 27001 / GDPR / HIPAA / CCPA — **but its LICENSE carves `ee/` and `app/(ee)/` out to a Commercial License, so treat the AGPL self-host as not including these.** Seismic claims *"secure document sharing with access controls like password protection—buyers don't need a Seismic account to view content"* and *"custom buyer privacy settings."* DealHub claims *"Secure Document Management — Centralize deal files with secure, permission-based access for all stakeholders."* Mindtickle, as a competitor, sets the bar as *"Encryption, role-based access controls, password protection, and email authentication are baseline requirements."*

---

## 12. In-room collaboration: comments, threads, tagging, and champion enablement

- **name:** In-room conversation and champion self-service
- **user_flow:** A buyer leaves a comment on a specific page or asset → tags a colleague → the thread is answered by the seller or the champion → the champion edits the content, project plan and timeline themselves to win internal approval → the buyer never has to email a zip of attachments, and the rep is not the courier.
- **data_flow:** Comment/thread anchored to a room object (page, asset, MAP task) → mentions/notifications → resolution state; champion edits → content delta → change log.
- **data_sources:** Room content objects; the buyer/stakeholder list; comment bodies; MAP task state; the buyer's internal approval process.
- **apis_hit:** Real-time transport (WebSocket/SSE) or polling; email + Slack/Microsoft Teams notification; CRM for stakeholder identity; the room content API.
- **automations:** Auto-notify on mention; auto-subscribe watchers; auto-thread-to-MAP-task conversion; auto-escalate unanswered buyer questions to the rep's manager or the account owner.
- **features_tools:** Contextual comments on pages, sections, assets and MAP tasks (Walnut: *"including PDFs, sections, assets, and Mutual Action Plan (MAP) tasks"*); replies; @-tagging; in-room chat; question posting; champion-editable sections; one shareable link for the champion.
- **extensibility:** Open-source: the closest primitives are **Etherpad** (Apache-2.0) for real-time co-editing, **CodiMD** (AGPL-3.0, ⚠️ last push 2025-10-02 — stale) for anchored markdown commenting, and **AppFlowy** (AGPL-3.0) for a shared workspace. **None of them is per-recipient or deal-scoped.** Commercial: Dock, Walnut, Seismic, DealHub, Mindtickle, Highspot, Recapped.
- **sources:** https://www.dock.us/solutions/sales (fetched); https://help.walnut.io/help/deal-rooms/getting-started (fetched); https://seismic.com/platform/digital-sales-rooms/ (fetched); https://dealhub.io/platform/dealroom/ (fetched); https://github.com/ether/etherpad (API); https://github.com/hackmdio/codimd (API); https://github.com/AppFlowy-IO/AppFlowy (API)
- **evidence:** Dock: *"Collaborative Sections — Let champions edit content, project plans, timelines, and deliverables directly—so they can tailor your pitch to their internal teams"*; *"Messages & Comments — Buyers and sellers can leave comments, tag teammates, and chat in message threads directly in the workspace—no email threads required"*; *"Arm your champion to sell when you can't be there… one shareable link with everything they need."* Walnut's KB: *"Contextual Comments: Leave comments directly on Deal Room content—including PDFs, sections, assets, and Mutual Action Plan (MAP) tasks."* Seismic: *"tagging, commenting, and posting questions"* and *"give them the ability to comment, reply to threads, and tag colleagues as needed."* DealHub: *"Buyer-seller Collaboration — Keep deals moving with in-room chat, mutual action plans, and real-time engagement alerts."*

---

## 13. Intent signal → seller alert → CRM task (routing workflow)

- **name:** Real-time buyer-intent alerting and routing
- **user_flow:** A target account's stakeholder returns to the room and reads the pricing section for 90 seconds → the platform raises a signal → the account owner gets an email and a Slack DM with the context ("which pages, how long, which stakeholder") → a follow-up task is created on the opportunity → the rep acts while the interest is live.
- **data_flow:** Room/page events → threshold evaluation (pages, dwell, revisit, download, demo interaction) → account + contact resolution (reverse IP → company) → alert dispatch (email, Slack) + CRM task/field update → rep action logged.
- **data_sources:** Room engagement events; firmographic/IP-to-company resolution; the target-account list; CRM ownership and territory rules; the buyer's stakeholder identities.
- **apis_hit:** **Slack API** (DealHub and Leadfeeder both document Slack alerts); email; CRM (Salesforce, HubSpot, Dynamics); IP-to-company data provider; webhooks (Coneshare: *"Slack and webhook notifications for link events"*).
- **automations:** Threshold alerts; real-time handoff to CRM; auto-create follow-up tasks; auto-enrich with buying signals; auto-suppress contacted accounts.
- **features_tools:** Real-time activity alerts with per-page detail; "who is engaged and who is not" view; target-account watchlists; alert routing rules; Slack and email delivery; CRM push.
- **extensibility:** Open-source: **PostHog** (self-hostable, events + session replay), **Umami** (MIT), **Matomo** (GPL-3.0 — heatmaps + session recording), **OpenReplay** (AGPL outside `ee/`) for replay, **Snowplow** (Apache-2.0) for a proper behavioural pipeline, plus **Node-RED / n8n / Kestra** for threshold logic and **Apache Superset / Metabase** for the dashboard. Reverse-IP company resolution is the one piece with no clean OSS equivalent.
- **sources:** https://dealhub.io/platform/dealroom/ (fetched); https://dealfront.com/ (fetched); https://www.ebsta.com/ (fetched); https://goconsensus.com/overview/demolytics (fetched); https://recapped.io/ (fetched); https://seismic.com/platform/digital-sales-rooms/ (fetched); https://github.com/coneshare/coneshare; https://github.com/PostHog/posthog (API); https://github.com/snowplow/snowplow (API)
- **evidence:** DealHub: *"Real-time Buyer Intent — Track buyer activity with live updates via email and Slack to engage decision-makers at the right moment."* Dealfront (now Leadfeeder): *"Get alerts when target accounts engage with key content"*, *"Reveal companies engaging with high-converting pages"*, *"Automatically notify sales via real-time Slack or email alerts to connect at the right time"*, and *"Unlock 40+ real-time buying signals."* Recapped: *"Get automatic alerts when engagement drops or key materials remain unreviewed."* Seismic: *"Receive alerts with detailed insights into what specifically buyers engage with, so you can deliver timely, relevant follow-ups."* Coneshare: *"Slack and webhook notifications for link events. Trigger actions based on viewer activity."* Consensus: *"demos sent, organic stakeholders discovered, discovery rate, views, view rate…"*

---

## 14. Engagement-driven deal-health scoring, risk detection and coaching trigger

- **name:** Deal-risk scoring and rep coaching from in-room behaviour
- **user_flow:** The platform continuously scores the deal from engagement decay, unreviewed key material, and missing stakeholders → when the score drops, it raises a risk alert and, in some platforms, a **live coaching cue to the rep during the call** → the rep also gets a "which content do I still owe them" prompt → closed-loop: the coaching feeds back into how the rep is trained.
- **data_flow:** Engagement time-series + milestone/MAP state + framework criteria (MEDDICC, BANT, Value Selling) → deal score + risk reasons → alert / coaching card → CRM update; coaching outcome → readiness data.
- **data_sources:** Room engagement events; call recordings and transcripts; CRM opportunity fields; the chosen qualification framework; rep behaviour history.
- **apis_hit:** CRM; telephony/recording capture (Gmail, calendar, dialer); LLM for call summarisation and coaching; notification channels; conversation-intelligence store.
- **automations:** Auto-score on every event; auto-alert on risk; auto-generate AI next steps; auto-document calls; one-time data entry fanned out to CRM; auto-flag revenue risk to leadership.
- **features_tools:** Engagement heat; stakeholder coverage map (who engaged, who hasn't); risk reasons; built-in frameworks (MEDDICC / Value Selling / BANT); live call coaching; call prep; readiness index; deal guide; AI assistant; visual org chart of the buying group.
- **extensibility:** Open-source: **PostHog** (behavioural events) + **Snowplow** (pipeline) + **Apache Superset** (dashboards) + **n8n** (rules). LLM coaching would need a model and a transcription pipeline — **no OSS project ships a qualification framework or a coaching loop.** Commercial: Recapped, Ebsta, Mindtickle, Highspot, Seismic, DealHub, Consensus.
- **sources:** https://recapped.io/ (fetched); https://www.ebsta.com/ (fetched); https://www.highspot.com/ (fetched); https://dealhub.io/platform/dealroom/ (fetched); https://seismic.com/ (fetched, nav: "Aura AI", "Winter '26 Release"); https://www.mindtickle.com/blog/best-digital-sales-room-platforms-comparison-and-review/; https://github.com/PostHog/posthog; https://github.com/snowplow/snowplow; https://github.com/apache/superset
- **evidence:** Recapped: *"AI Deal Intelligence — Uncover what buyers are doing behind closed doors… See which content each stakeholder reviews and for how long… Get automatic alerts when engagement drops or key materials remain unreviewed… Create visual org charts"*; *"AI Sales Coach — Live call coaching with AI… Apply the same evaluation criteria to every deal through built-in frameworks (MEDDICC, Value Selling, BANT)"*; *"AI alerts revenue risk."* Ebsta: *"Spot risks and opportunities in your pipeline — Stop deals from slipping"*; *"Coaching — Ebsta learns from your success and failure and recommends how to improve your seller's performance"*; and Ebsta makes **written guarantees**: *"we guarantee to improve your sellers' quota attainment in the first 6 months"* and *"to achieve accurate forecasts to +/- 10% of the figure within 6 months."* Highspot's nav lists **"Deal Agent"**, **"GTM Agent"** and **"Conversational Intelligence."** DealHub's nav lists **"Revenue Intelligence — Aligning CRO & CFO"** and features **DealStream** and **DealBox.** Mindtickle: *"The platform must link execution to coaching and vice versa, so there are no gaps between what reps are coached on and what they execute on live deals."*

---

## 15. Conversation intelligence capture and CRM write-back

- **name:** Auto-capture of the deal conversation into the CRM
- **user_flow:** Calls and meetings are recorded/transcribed → AI summarises them and extracts next steps, requirements and qualification evidence → fields are written to the opportunity, account and contact → the rep enters data once; the room, the MAP and the CRM all agree → post-sales gets the requirements without re-listening to calls.
- **data_flow:** Meeting audio/video + email + phone → transcription → LLM extraction (summary, next steps, requirements, MEDDICC evidence) → one-time entry → fan-out to CRM custom fields, the room's notes, and the handoff packet.
- **data_sources:** Recorded calls and meetings; email threads; calendar events; the rep's spoken playbook; the deal record.
- **apis_hit:** Gmail / Microsoft 365 / calendar; telephony and meeting recorders (Gong is a listed DealHub integration); LLM; CRM write API (Salesforce, HubSpot, Bullhorn — Ebsta documents all three); document storage for the transcript.
- **automations:** Auto-transcribe; auto-summarise; auto-extract next steps; auto-write CRM fields; auto-generate reports/dashboards; auto-prepare the handoff.
- **features_tools:** AI meeting notes; automatic next-step capture; bi-directional CRM field sync; 100+ integrations; one-time data entry; AI-generated reports; handoff packets.
- **extensibility:** Open-source: this is a **glue problem with no single OSS answer** — self-hosted Whisper for transcription, an LLM for extraction, **n8n / Windmill / Kestra** for orchestration, **RudderStack** for the CDP write, and Nextcloud/Docmost for the store. There is **no** OSS MEDDICC-enforcing conversation-intelligence product of note. Commercial: Ebsta, Recapped, Mindtickle, Allego, Highspot, Seismic.
- **sources:** https://www.ebsta.com/ (fetched); https://recapped.io/ (fetched); https://dealhub.io/ (fetched, integrations footer); https://www.allego.com/ (fetched, nav: "Conversation Intelligence"); https://www.highspot.com/ (fetched, nav: "Conversational Intelligence"); https://github.com/rudderlabs/rudder-server (API); https://github.com/kestra-io/kestra (API); https://github.com/windmill-labs/windmill (API)
- **evidence:** Ebsta's product list: **"Conversation Intelligence," "Relationship Intelligence," "Revenue Intelligence"**; use cases **"CRM Automation — Capture every contact, e-mail, call and meeting. Ebsta connects your mailbox, calendar, call recorder and workspace with your CRM"**; integrations **"Ebsta + Salesforce," "Ebsta + HubSpot," "Ebsta for Bullhorn"**; and **"Deal Management — Guide sellers to deal-winning conversations by analyzing every deal and interaction."** Recapped: *"AI Assistant — Eliminate busy admin work with guaranteed data accuracy… Capture meeting insights with AI — AI documents calls and identifies next steps without manual note-taking… One-time data entry — Update information once and watch it populate everywhere through bi-directional CRM integrations… Integrate with 100+ tools."* DealHub's integration footer lists **Gong** explicitly.

---

## 16. Buyer self-serve booking of the next step

- **name:** In-deal scheduling and next-step booking
- **user_flow:** At the end of a room session the seller proposes the next step → the buyer picks a slot from the seller's live availability, inside the room → the meeting is created, invites go out, the recording links back to the room → the MAP task flips to done and the room's "next steps" panel updates.
- **data_flow:** Calendar availability (Google/Microsoft) + routing rules → slot list rendered in-room → booking → calendar event + conferencing link → room object + MAP task + CRM activity.
- **data_sources:** Seller/buyer calendars; timezone and routing rules; the MAP's open tasks; the room's "next steps" panel.
- **apis_hit:** Google Calendar / Microsoft Graph; conferencing (LiveKit / Jitsi / Zoom); email; CRM; the room's task API.
- **automations:** Auto-propose a slot when a room is idle; auto-book on acceptance; auto-generate the conferencing room; auto-record and attach; auto-update the MAP and CRM.
- **features_tools:** Embedded availability; one-click accept; recorded demo auto-attached ("Watch recording"); task completion; next-steps panel.
- **extensibility:** Open-source: **Cal.com — note the repo is now `calcom/cal.diy`, MIT, 48,648★, active** — is a complete, self-hostable, MIT-licensed replacement for the booking half of this workflow. Plus **LiveKit** for the conferencing half. **No OSS project does booking *inside* a deal room**; the room integration is the custom work. Commercial: Dock (its room markup shows a "Schedule call" action), DealHub, Consensus, GTMKing (⚠️ **company appears defunct — domain parked**).
- **sources:** https://github.com/calcom/cal.diy (API); https://github.com/livekit/livekit (API); https://www.dock.us/ (fetched); https://goconsensus.com/ (fetched); gtmking.com (fetched → parking lander)
- **evidence:** Cal.com's GitHub API record: `full_name: "calcom/cal.diy"`, licence `MIT`, 48,648 stars, `pushed_at: 2026-09-20`, language TypeScript. Dock's homepage product demo markup includes **"Schedule call"** and **"Download workflow rules"** as room actions, alongside **"Watch recording."** `https://gtmking.com/deal-rooms` returns literally `<html><head><script>window.onload=function(){window.location.href="/lander"}</script></head></html>` — a parked domain; the Wayback Machine holds a single 2025-07-12 capture.

---

## 17. Governance: versioning, brand control, and the closed-won → onboarding handoff

- **name:** Version control, brand governance and post-sale room continuity
- **user_flow:** Marketing and enablement own the approved content and the brand; a rep instantiates a room from that governed template → every publish is snapshotted, so a bad edit can be previewed, restored or branched → when the deal closes, the room is **repurposed, not rebuilt**, into an onboarding/implementation portal with phases → the buyer keeps one link for the whole relationship → content stays in version and permission compliance.
- **data_flow:** Approved asset library + brand tokens → room template → published snapshot (immutable version) → phase transition at close → onboarding plan instantiation → client-project plan and training content → archive/retention at end of lifecycle.
- **data_sources:** The content library with its approval state; brand/theme tokens; the opportunity's close date; onboarding phases and milestones; the compliance/retention policy.
- **apis_hit:** Content/DAM API; brand-token service; CRM close event; LMS (Mindtickle/Highspot/Allego embed training in the same portal); e-signature for the SOW/plan; storage with retention.
- **automations:** Auto-snapshot on every publish; auto-restore/branch on request; auto-apply brand rules to every new room; auto-repurpose the room at closed-won; auto-archive and delete on the retention schedule; auto-inject refreshed content across all live rooms.
- **features_tools:** Room template library; per-publish version history with preview/restore/branch; **synced sections** across all live rooms; brand and messaging control; client portal + white-label portal; phased onboarding plan; project plans; training content in the same room; data lifecycle controls.
- **extensibility:** Open-source: **Docmost** (AGPL-3.0, 21,791★) with Collections + version history is the strongest governance-shaped OSS option for room *content*; **Nextcloud** (AGPL-3.0) for versioned files + ACLs; **AppFlowy**/**AFFiNE** for a shared workspace. **Phase-based onboarding plans and MAP continuation across close are not implemented by any OSS project.** Commercial: Seismic, Mindtickle, Highspot, Dock, Allego, Recapped.
- **sources:** https://help.walnut.io/help/deal-rooms/getting-started (fetched); https://www.dock.us/solutions/sales (fetched); https://www.dock.us/sitemap.xml (fetched — confirms `/solutions/customer-onboarding`, `/solutions/client-portal`, `/solutions/white-label-client-portal`, `/solutions/project-management-client-portal`, `/product/project-plans`, `/product/learning-management`, `/product/learning-playbooks`, `/templates/customer-onboarding-plan`, `/templates/mutual-action-plan`, `/templates/implementation-plan`); https://recapped.io/ (fetched); https://www.allego.com/ (fetched); https://seismic.com/platform/digital-sales-rooms/ (fetched); https://github.com/docmost/docmost (API); https://github.com/nextcloud/server (API)
- **evidence:** Walnut's KB: *"Version History — Every publish and editing session is snapshotted, so you can preview, restore, or branch from any earlier state"*; *"Reusable Sections — Save a section to a company-wide library and drop it in"*; and critically *"persistent rooms that survive the closed-won transition to customer success"* is listed by Mindtickle as a **requirement**, not a nicety. Dock's sales page: *"Synced Sections — Automatically keep every workspace up to date. Push content changes… across all active deals instantly."* Dock's 1,221-URL sitemap confirms the post-sale surface is a first-class product line, not an afterthought. Recapped: *"Seamless Handoffs — Preserve complete context throughout the entire customer journey, ensuring nothing gets lost from qualification to implementation… Give post-sales teams instant access to all requirements without reviewing hours of calls."* Seismic: *"Scale — Empower reps to easily create best-in-class client experiences. Seismic's Digital Sales Room features give marketing and enablement teams control over the branding, messaging, and content in a scalable way."*

---

## 18. Branded, custom-domain, white-label buyer experience

- **name:** Branded room on your own domain
- **user_flow:** The company registers its own domain (e.g. `deals.acme.com`) → the room is served from that domain with the seller's brand, fonts and colour → the buyer sees a vendor-branded experience, not a third-party portal with a vendor's logo → for agencies and advisory firms the room is white-labelled so the *client's* brand appears, not the agency's.
- **data_flow:** Domain → TLS + reverse proxy → room tenant resolved by host → branded theme tokens applied → per-tenant asset and colour overrides; white-label mode swaps owner identity.
- **data_sources:** Domain/DNS records; brand tokens (logo, colour, type); tenant configuration; per-tenant branding overrides.
- **apis_hit:** DNS/ACME; CDN/edge; a headless CMS or DAM for the brand assets; the room's multi-tenant theming API; storage/CDN for tenant-scoped assets.
- **automations:** Auto-issue and renew TLS; auto-apply brand tokens to new rooms; auto-propagate a brand change to every tenant; auto-verify domain ownership.
- **features_tools:** Custom domain; custom branding; theme tokens; drag-and-drop design tool; white-label portal; multi-tenant theming; version-controlled messaging.
- **extensibility:** Open-source: this is genuinely easy to self-build — any headless CMS (Directus/AppFlowy/Docmost) for brand tokens plus a proxy. **Papermark advertises custom domains and custom branding, but that is a hosted-plan feature**; its Coneshare equivalent is MIT and explicitly "Custom Branding"-capable via configuration rather than a hosted control panel. **Liferay markets a "Digital Sales Room" capability but it is a commercial DXP feature, not part of the LGPL-2.1 portal** — I could not load `liferay.com/capabilities/digital-sales-room` (bot challenge), so this is flagged unverified. Commercial: Papermark, Liferay (DXP), DealHub (*"Branded Sales Rooms — Customize your DealRoom to deliver a branded, consistent sales experience"*), Seismic, Dock.
- **sources:** https://github.com/papermark/papermark (README fetched — *"Custom Branding: Add a custom domain and your own branding"*); https://dealhub.io/platform/dealroom/ (fetched); https://seismic.com/platform/digital-sales-rooms/ (fetched); https://github.com/liferay/liferay-portal (API — `NOASSERTION`, 2,267★, Java, push 2026-09-25); https://www.docker.com/ — not used; `liferay.com/capabilities/digital-sales-room` returned a Cloudflare "Client Challenge" (HTTP 403) and could not be read.
- **evidence:** Papermark's README: *"Custom Branding: Add a custom domain and your own branding."* DealHub: *"Branded Sales Rooms — Customize your DealRoom to deliver a branded, consistent sales experience"* and *"Centralized Content Hub — Share proposals, videos, and contracts in one central DealRoom."* Seismic: *"Can I personalize the content and branding within a DSR? Yes! Seismic's digital sales rooms let you customize branding, messaging, and content."* Dock's sitemap confirms a dedicated **`/solutions/white-label-client-portal`** page. ⚠️ **Liferay's DSR could not be verified** — the capability page is gated; treat "Liferay has a Digital Sales Room" as unconfirmed from this pass, and note in any case that Liferay Portal CE is LGPL-2.1 and would not include a commercial DXP module.

---

## Appendix A — Vendors I could not reach, and what that means

| Vendor | Blocked by | What I could still establish |
|---|---|---|
| **6sense** | Cloudflare "Just a moment… Enable JavaScript and cookies" (HTTP 403) on `6sense.com`, `www.6sense.com/sitemap.xml`, `help.6sense.com`, `apidocs.6sense.com` — with both a browser UA and a Googlebot UA. DNS resolves (6sense.com → 141.193.213.20/21; help.6sense.com → AWS). | Nothing. **No 6sense feature claim in this document is sourced.** Treat 6sense's deal-room feature set as an open gap for a follow-up pass from a different network. |
| **GTMKing** | `gtmking.com` and `gtmking.com/deal-rooms` both return a JavaScript redirect to `/lander` (parked domain). Wayback has one capture (2025-07-12). | **The company appears to no longer operate.** Do not build a competitive model on it. |
| **Slaytools** | `slaytools.com`, `www.slaytools.com`, `slaytools.io` all NXDOMAIN. | Nothing. |
| **Folderware** | `folderware.com`, `www.folderware.com`, `folderware.io` all NXDOMAIN. Latest Wayback capture 2014. | Nothing. |
| **SpinMe** | `spinme.com` now serves a personal blog; `www.spinme.com` NXDOMAIN. | Nothing. |
| **Onward** | `onward.com` → 67.20.112.130 (parking range); `onwardsales.com` returns only a cookie-consent string; `onward.ai` → GitHub Pages. | Nothing. |
| **Perplex** | `getperplex.com` → Namecheap "domain for sale" page; `perplex.ai` SERVFAIL; `perplex.so`, `perplexhq.com` NXDOMAIN. | Nothing. |
| **Humo** | `humoai.co` = a ByteDance video-generation model, unrelated; `humoai.com` unreachable; `humo.co` empty; `humo.dev` TLS error; `humo.tech`/`humo.work` 403. | Nothing. |

**Net effect: of the 21 vendors requested, 12 have a verified, current, first-party feature source
(DocuSign/IAM rebrand, Seismic, Mindtickle, Consensus, Recapped, Walnut, DealHub, Ebsta, Dealfront→Leadfeeder,
Fletch, Dock, Guidebook, Highspot→Seismic, Allego). Seven are unverified or defunct
(6sense, GTMKing, Slaytools, Onward, Perplex, Humo, Folderware, SpinMe).**

The search tooling was degraded for most of this session (`websearch` returned HTTP 401
throughout; DuckDuckGo, Bing and Mojeek either rate-limited or, in Bing's case, returned
results for an unrelated cached query). Every claim above therefore comes from a direct fetch
of a vendor or repository URL, never from a search-result snippet. Where I could not fetch,
I said so rather than filling the gap.

## Appendix B — Feature-parity matrix (verified sources only)

| Capability | Papermark (OSS core) | Coneshare | Deckly | DocSpace | Nextcloud | Dock | Walnut | DealHub | Seismic | Consensus | Recapped | Highspot | Mindtickle | Allego | Ebsta | Leadfeeder | Fletch | Guidebook |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Shareable tracked link | ✅ | ✅ | ✅ | – | – | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Page/slide dwell analytics | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ content-only | ✅ | ⚠️ call | ⚠️ | ⚠️ | ❌ | ❌ |
| Multi-document data room | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | – | – | – | ❌ |
| Staged/tiered disclosure | ❌ | ⚠️ link-level | ❌ | ⚠️ rooms | ⚠️ shares | ✅ hidden sections | ✅ page-level | ⚠️ | ⚠️ privacy | ❌ | ❌ | ⚠️ | ⚠️ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Native MAP | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Bi-directional CRM sync | ⚠️ ee | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| In-room comments/threads | ❌ | ❌ | ❌ | ❠️ | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Dynamic watermarking | ⚠️ ee | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ⚠️ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Download control | ⚠️ ee | ✅ | ✅ | ⚠️ | ✅ | ⚠️ | ✅ | ✅ | ⚠️ | ❌ | ❌ | ❌ | ⚠️ | ❌ | ❌ | ❌ | ❌ |
| NDA gating | ⚠️ ee | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Audit log | ⚠️ ee | ❌ | ❌ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ✅ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ❌ | ❌ | ⚠️ | ❌ | ❌ |
| In-room e-signature | ❌ | ❌ | ❌ | ❌ | ⚠️ | ✅ | ❌ | ✅ | ⚠️ | ❌ | ❌ | ⚠️ | ⚠️ | ❌ | ❌ | ⚠️ | ❌ |
| Redlining / contract collab | ❌ | ❌ | ❌ | ❌ | ❌ | ⚠️ | ❌ | ✅ | ❌ | ❌ | ❌ | ⚠️ | ❌ | ❌ | ❌ | ❌ | ❌ |
| In-room live video | ❌ | ⚠️ streaming | ❌ | ❌ | ⚠️ Talk | ✅ | ✅ | ❌ | ⚠️ | ✅ | ❌ | ❌ | ⚠️ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Async video intro | ❌ | ⚠️ | ❌ | ❌ | ⚠️ Talk | ⚠️ | ⚠️ | ⚠️ | ✅ | ✅ | ❌ | ⚠️ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Interactive demo + heatmap | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ⚠️ playlists | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ⚠️ | ❌ |
| Stakeholder discovery | ❌ | ⚠️ | ❌ | ❌ | ❌ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ✅ | ✅ | ⚠️ | ⚠️ | ⚠️ | ✅ | ✅ | ❌ | ❌ |
| Real-time intent alerts | ⚠️ ee | ✅ webhooks | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ email+Slack | ✅ | ⚠️ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| Deal-health / risk score | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ⚠️ insights | ⚠️ | ⚠️ | ⚠️ DQL | ✅ | ✅ agent | ✅ readiness | ❌ | ✅ | ⚠️ | ❌ | ❌ |
| Conversation intelligence | ❌ | ❌ | ❌ | ❌ | ⚠️ Talk | ❌ | ❌ | ⚠️ Gong | ✅ | ⚠️ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| AI generation of room content | ❌ | ❌ | ⚠️ summaries | ❌ | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ Studio | ❌ |
| Version history / branching | ❌ | ❌ | ❌ | ⚠️ | ✅ | ⚠️ | ✅ | ⚠️ | ✅ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| White-label / custom domain | ⚠️ hosted | – | ❌ | ⚠️ | ⚠️ | ✅ | ⚠️ | ✅ | ✅ | ✅ | ✅ | ⚠️ | ⚠️ | ❌ | ❌ | ⚠️ | ❌ | ⚠️ |
| Post-sale room continuity | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ⚠️ | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | – | – | – | ⚠️ pivoted |

`✅` documented on a first-party page I fetched · `⚠️` partial, caveated, or a vendor claim without
mechanism · `❌` not documented in anything I fetched · `–` not applicable · `⚠️ ee` = marketed on
the vendor's site but excluded from the AGPL core by the repo's own LICENSE.

**The two boldest cells in that matrix:** the `❌` column under the OSS products for MAP, CRM sync,
watermarking, NDA gating, intent alerts and deal-health scoring is the entire opportunity. Papermark
is the only OSS project with more than one `✅`, and it has 240 open issues and a proprietary `ee/`
directory doing the interesting work.
