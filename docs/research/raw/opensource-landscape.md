# Domain 8 — Open-Source & Self-Hostable Landscape for a Digital Sales Room (DSR)

**Research date:** 2026-09-26
**Method:** every row below was resolved against a live primary source — the GitHub REST API
(`api.github.com/repos/{owner}/{repo}`) for licence / stars / `pushed_at` / `archived` / primary
language, and `raw.githubusercontent.com/{repo}/{branch}/LICENSE` where the API returned
`NOASSERTION` (i.e. a split or source-available licence). Vendor claims come from the vendor's own
pages, fetched directly.

**Honesty rules applied:**
- A GitHub URL is listed **only** if `GET /repos/{owner}/{repo}` returned HTTP 200. Guessed or
  remembered repo names that 404'd were discarded, not guessed at.
- `NOASSERTION` from the GitHub API is reported as `NOASSERTION (split/source-available)` and the
  actual licence text is quoted.
- "Activity" is derived from the API's `pushed_at` field, not impressions.
- **Nothing in this file is inferred from a vendor's marketing claim alone.** Where I could not
  verify, the row says so.

---

## 0. Headline finding

**There is no mature, permissively-licensed, self-hostable Digital Sales Room on GitHub.**

The entire category reduces to:

| Tier | Projects |
|---|---|
| Genuinely DSR-shaped, self-hostable, ≥1k★ | **1** — Papermark |
| Genuinely DSR-shaped, self-hostable, <1k★ or prototype | **5** — Coneshare, Deckly, Hyperportal, DeckLens, discovery-media-player, data-room-skill |
| Room/folder primitives only (no engagement analytics, no buyer flows) | **5** — ONLYOFFICE DocSpace, Pydio Cells, Nextcloud, Docmost, Teedy |
| Building blocks you would assemble a DSR *from* (37 projects) | see §4 |

The two largest "open source VDR" claims in the wild — Papermark's own comparison blog and
third-party listicles — reduce, on inspection, to Papermark plus a generic DMS or a Chinese-language
"big-screen BI dashboard" repo that merely has the words *data room* in its name
(`gcpaas/DataRoom`, a Vue/Apache-2.0 charting tool, **not** a VDR). Treat those listicles as
marketing.

---

## 1. Genuinely DSR-related open-source projects

### 1.1 Papermark — the only serious one

| Field | Value |
|---|---|
| **URL** | https://github.com/papermark/papermark |
| **Licence** | `NOASSERTION` per GitHub API. LICENSE states: AGPL-3.0 for everything **outside** `ee/` and `app/(ee)/`; those two directories are under a separate **Commercial License** defined in `ee/LICENSE`. GitHub renders the badge as `AGPLv3`. |
| **Stack** | TypeScript, Next.js, Tailwind, shadcn/ui, Prisma, PostgreSQL, NextAuth.js, Tinybird (analytics), Resend (email), Stripe, AWS S3 / Vercel Blob for blobs |
| **Verified features** | Shareable tracked links; custom domain + custom branding; per-page / per-slide read analytics; **Data Rooms** (repo description: "secure data rooms with built-in analytics and custom domains"); self-hosting; 70,000+ claimed dealmakers (vendor claim) |
| **Activity** | **Active.** 9,214★, last push 2026-08-28, not archived, 240 open issues |
| **Caveats** | README is stale (still says "soon page-by-page analytics" while the repo description and site claim page-level analytics). The self-host path drags in **Tinybird** for analytics and **Resend** for email — both commercial hosted services; self-hosting is not fully self-contained out of the box. The security features the website markets (SSO, dynamic watermarking, NDA gating, IP allow-listing, SOC 2 / ISO 27001) are almost certainly the `ee/` commercial directories, not the AGPL core. |

**Feature claims verified on papermark.com (vendor's own marketing — treat as claim, not spec):**
"Every room ships with SSO, granular permissions, dynamic watermarking, NDA gating, audit logs, IP
allow-listing, end-to-end encryption and full data lifecycle controls"; certifications listed as SOC 2
Type II, ISO 27001, GDPR, HIPAA, CCPA; use cases listed as M&A, investment management, client portal,
due diligence, fundraising, LP/IR, real-estate transactions, government document distribution.

### 1.2 Coneshare — a distribution/security layer over existing storage

| Field | Value |
|---|---|
| **URL** | https://github.com/coneshare/coneshare |
| **Licence** | **MIT** (GitHub API SPDX: `MIT`; README carries the OSI MIT badge) |
| **Stack** | Python |
| **Verified features** (from the README, fetched) | Virtual datarooms; PDF rendering + secure video streaming; inline viewers; password protection, **link expiration**, email verification; **download restrictions and dynamic watermarks**; real-time views/revisits/downloads; **page-by-page reading duration** and video playback metrics; **Slack and webhook notifications on link events**; connectors for Nextcloud / Google Drive / Dropbox. Files are **not copied** — Coneshare fronts the storage provider. |
| **Activity** | **Active but early.** 41★, 0 forks beyond base, last push 2026-09-25, not archived. Separate `coneshare/coneshare-compose` for Docker Compose deploys. |
| **Honesty** | This is the closest thing to a full DSR in open source — it implements the access-control and page-level-analytics loop that most DMSs lack. At 41★ it is a very young project; the README is aspirational in places (e.g. "more storage connectors are in development"). Treat as a reference implementation, not a product. |

### 1.3 Deckly — pitch-deck workspace with data rooms (tiny, but on-target)

| Field | Value |
|---|---|
| **URL** | https://github.com/ManishBlueprints/Deckly |
| **Licence** | **AGPL-3.0** (verified: repo page shows "AGPL-3.0 license"; README §License) |
| **Stack** | React 19, Vite, TypeScript, Tailwind, shadcn/ui, Radix, Supabase (Auth/Postgres/RLS/Storage/Edge Functions), TanStack Query, **pdf.js in the browser**, CloudConvert (office docs), Cloudflare R2, PostHog + Sentry |
| **Verified features** (README, fetched) | Stable share URL while the deck version changes; **purpose-built data rooms**; **password gates, expiry dates, download controls, optional email capture**; engagement analytics — *views, time spent, **slide-level drop-offs**, saves, revisit signals*; AI deck summaries; private investor workspace (save/tag/notes); client-side PDF rendering; row-level security |
| **Activity** | **Pre-release.** 10★, 0 watchers, 0 forks, 1 open PR, 739 commits, last push 2026-09-21. |
| **Honesty** | Genuinely DSR-shaped and honest about its AGPL §13 source-offer obligation. But 10 stars / 0 forks means one author and no adoption. Valuable as a **feature-list and schema reference**, not as a dependency. |

### 1.4 Hyperportal — AI-agent-operated dataroom

| Field | Value |
|---|---|
| **URL** | https://github.com/hypersocialinc/hyperportal |
| **Licence** | **MIT** (GitHub API SPDX: `MIT`) |
| **Stack** | Next.js, MDX content in a git repo, Vercel, PostHog |
| **Verified features** (README, fetched) | **One private link per recipient organisation** (`/r/<token>` sets an HttpOnly cookie and redirects, so tokens never sit in history/referrers); **tiered / staged disclosure** — each section has `minTier` and `belowTier: "teaser" \| "hidden"`; revocable links (CLI re-probes the live URL before reporting success); engagement stats per recipient; ships as a **Claude/Codex agent skill** (`npx skills add hypersocialinc/hyperportal`) so the dataroom is managed by an LLM |
| **Activity** | **Prototype.** 0★, last push 2026-07-14 |
| **Honesty** | Its `minTier` / `belowTier` staged-disclosure model is the single most interesting open-source idea for a DSR in this survey. 0 stars; treat as a design artefact. |

### 1.5 DeckLens — buyer-engagement instrumentation SDK

| Field | Value |
|---|---|
| **URL** | https://github.com/cloudbtl/DeckLens |
| **Licence** | **Apache-2.0** (repo page badge + `LICENSE`) |
| **Stack** | Vanilla browser JS, **zero runtime dependencies** |
| **Verified features** (README, fetched) | `DeckLens.createTracker({projectId, deckId, endpoint}).start()`; auto-recognises semantic `<section>`/`<article>`; events `section_session_start`, `section_enter`, `section_view`, `section_session_end`, `action`; emits section ID/title/visibility-ratio, **dwell duration**, max ratio, next-section, reading path, named interactions; explicitly **does not transmit document bodies or raw form values**; failed requests queued and flushed, unload beacon best-effort; ships a demo dashboard + a local JSONL collector |
| **Activity** | **Prototype.** 0★, last push 2026-09-21 |
| **Honesty** | The **only** permissively-licensed (Apache-2.0) buyer-engagement tracker found. Its "sends no document content" privacy posture is the correct default for a DSR. Production collector is explicitly out of repo. |

### 1.6 Smaller / near-zero DSR-shaped repos (listed for completeness, not for use)

| Name | URL | Licence | Activity | What it is |
|---|---|---|---|---|
| discovery-media-player | https://github.com/Juli1artha/discovery-media-player | AGPL-3.0 | 0★, push 2026-09-15 | "Self-hosted document viewer: per-recipient tracked links, reading analytics, live presentation." Decoupled headless component. 0★. |
| data-room-skill | https://github.com/dashaworks/data-room-skill | MIT | 1★, push 2026-08-07 | Not an app. A **Claude Code / Codex skill** for building an M&A or fundraising due-diligence data room: "Email/passcode/allowlist access". Sibling repos: `dashaworks/share-as-link-skill` (223★), `dashaworks/report-skills`. |

---

## 2. Room / workspace primitives — real and mature, but NOT DSRs

These implement *rooms* without implementing the DSR loop (no per-recipient engagement analytics,
no buyer-facing onboarding, no MAP, no deal-health signal).

| Project | URL | Licence | Stack | DSR features actually implemented | Activity |
|---|---|---|---|---|---|
| **ONLYOFFICE DocSpace** | https://github.com/ONLYOFFICE/DocSpace | **AGPL-3.0** (API) | Dockerised platform (API reports no dominant language) | "Room-based collaborative platform which allows organizing a clear file structure... Flexible access permissions and user roles allow fine-tuning the access to the whole space or separate rooms." Folder/room hierarchy + roles. **No engagement analytics.** | 275★, push 2026-08-20, active |
| **Pydio Cells** | https://github.com/pydio/cells | **AGPL-3.0** (API) | Go | "Future-proof content collaboration platform." Self-hosted file sync/share, user/session management, ACLs. Used as a VDR substrate. **No buyer analytics.** | 2,253★, push 2026-09-24, active |
| **Nextcloud** | https://github.com/nextcloud/server | **AGPL-3.0** (API) | PHP | Files, Talk, Office, Group folders, OCS/WebDAV API, full app framework. Best-in-class self-hostable substrate for a DSR. **Zero DSR features out of the box.** | 36,921★, push 2026-09-25, very active |
| **Docmost** | https://github.com/docmost/docmost | **AGPL-3.0** (API) | TypeScript (NestJS + React) | Collaborative wiki/docs with **Collections** and ACLs — the nearest open-source thing to a *room with a content tree*. Not buyer-facing. | 21,791★, push 2026-09-22, very active |
| **Teedy** (`sismics/docs`) | https://github.com/sismics/docs | **GPL-2.0** (API) | Java / JavaScript | Document management: tags, full-text, workflows, ACLs. **GPL-2.0, not AGPL** — note the copyleft difference if you plan to modify. | 2,566★, push 2026-08-17, active |
| **Paperless-ngx** | https://github.com/paperless-ngx/paperless-ngx | **GPL-3.0** (API) | Python/Django | OCR + indexing + archival. Consumed as an *indexing backend* by VDR products. | 46,014★, push 2026-09-25, very active |
| **Mayan EDMS** | https://github.com/mayan-edms/Mayan-EDMS | `NOASSERTION` — and **the entry is explicitly a "mirror, no pull request or issues"** | Python | Document management. ⚠️ The long-canonical `mattpottermann/mayan-edms` now returns **HTTP 404**. I could not locate a live canonical repo or read a licence. Treat as **unverified**; do not link to it. | 837★, last push 2026-05-23 |

---

## 3. e-Signature — the piece vendors bolt on

| Project | URL | Licence | Notes |
|---|---|---|---|
| **Documenso** | https://github.com/documenso/documenso | **AGPL-3.0** (API SPDX) | "The Open Source DocuSign Alternative." 15,194★, TypeScript, push 2026-09-25. The most credible OSS signing stack. Embeddable signing, fields, templates. |
| **OpenSign** | https://github.com/OpenSignLabs/OpenSign | `NOASSERTION` — LICENSE: AGPL-3.0 outside `apps/OpenSignServer/cloud/customRoute`, which carries its own licence | "The free & Open Source DocuSign alternative." 7,032★, JavaScript, push 2026-08-21. |
| **DocuSign Rooms** (commercial) | — | proprietary | See `competitive-baseline.md` — note Docusign has **rebranded to "Intelligent Agreement Management (IAM)"**; `/products/rooms`, `/products/docusign-rooms` and `/products/rooms/overview` all now **404**, and the Rooms product no longer appears in the top-level product menu. `developers.docusign.com/docs/rooms-api/` still resolves (title only, JS-rendered). |

---

## 4. Building blocks — 37 verified projects you could assemble a DSR from

Grouped by the DSR capability each one serves. **Every URL here returned HTTP 200 from the GitHub API.**

### 4.1 Document rendering & presentation
| Project | URL | Licence | Activity |
|---|---|---|---|
| pdf.js | https://github.com/mozilla/pdf.js | **Apache-2.0** (verified on repo page) | 53.9k★, active — the PDF viewer every OSS DSR above builds on |
| reveal.js | https://github.com/hakimel/reveal.js | **MIT** | 72,348★, push 2026-09-18 |
| impress.js | https://github.com/impress/impress.js | **MIT** (canonical owner is `impress`, **not** `impressjs`) | 38,174★, push 2026-07-23 |
| Slidev | https://github.com/slidevjs/slidev | **MIT** | 48,841★, push 2026-09-16 — presenter mode + PDF export |
| Marp | https://github.com/marp-team/marp | **MIT** | 12,560★, push 2026-07-29 |
| PptxGenJS | https://github.com/gitbrent/PptxGenJS | **MIT** (LICENSE on `master`) | generate .pptx server-side (note: default branch is `master`, not `main`) |
| ONLYOFFICE DocumentServer | https://github.com/ONLYOFFICE/DocumentServer | **AGPL-3.0** (API) | 6,942★, push 2026-07-22 — **Community Edition carries AGPL + a concurrent-licence restriction; check before shipping** |
| Collabora Online | https://github.com/CollaboraOnline/online | `NOASSERTION` (API) — **I could not read the repo LICENSE file (404 on `master/LICENSE` and `master/COPYING.LICENCE`). Unverified.** | 3,353★, push 2026-09-25 |

### 4.2 Collaborative editing &amp; whiteboarding (in-room co-authoring)
| Project | URL | Licence | Activity |
|---|---|---|---|
| Etherpad | https://github.com/ether/etherpad | **Apache-2.0** | 18,556★, push 2026-09-25 |
| CodiMD / HackMD | https://github.com/hackmdio/codimd | **AGPL-3.0** | 10,152★, **last push 2025-10-02 — ~1 year stale. Slowing.** |
| Excalidraw | https://github.com/excalidraw/excalidraw | **MIT** | 132,912★, push 2026-09-25 |
| tldraw | https://github.com/tldraw/tldraw | **NOT open source.** LICENSE.md = "tldraw license ... Alternative licenses available" — a source-available commercial licence | 50,569★. Include only if you accept the licence terms. |
| AppFlowy | https://github.com/AppFlowy-IO/AppFlowy | **AGPL-3.0** | 76,934★, Dart, push 2026-09-22 |
| AFFiNE | https://github.com/toeverything/AFFiNE | `NOASSERTION` — LICENSE: **MIT** outside `packages/backend` + `packages/common/native`, which use their own licences | 72,975★, push 2026-09-25 |

### 4.3 Buyer &amp; deal analytics
| Project | URL | Licence | Activity |
|---|---|---|---|
| PostHog | https://github.com/PostHog/posthog | `NOASSERTION` — "fair use"/MIT core + EE | 39,935★, push 2026-09-25 |
| Umami | https://github.com/umami-software/umami | **MIT** | 39,011★, push 2026-09-25 |
| Plausible | https://github.com/plausible/analytics | **AGPL-3.0** | 29,218★, Elixir, push 2026-09-25 |
| Matomo | https://github.com/matomo-org/matomo | **GPL-3.0** | 21,898★, push 2026-09-25 — heatmaps, session recording, form tracking |
| OpenReplay | https://github.com/openreplay/openreplay | `NOASSERTION` — LICENSE: **AGPL default**, `ee/` under its own licence, some dirs MIT | 12,900★, push 2026-09-25 |
| Snowplow | https://github.com/snowplow/snowplow | **Apache-2.0** | 7,034★, Scala, push 2026-06-26 — self-hosted behavioural event pipeline |
| RudderStack | https://github.com/rudderlabs/rudder-server | `NOASSERTION` | 4,488★, Go, push 2026-09-25 |

### 4.4 BI / dashboards over deal data
| Project | URL | Licence | Activity |
|---|---|---|---|
| Apache Superset | https://github.com/apache/superset | **Apache-2.0** | 74,921★, push 2026-09-25 |
| Metabase | https://github.com/metabase/metabase | `NOASSERTION` — LICENSE.txt: **AGPL outside `enterprise/`, Metabase Commercial License inside** | 49,421★, Clojure, push 2026-09-25 |
| Redash | https://github.com/getredash/redash | **BSD-2-Clause** | 28,810★, push 2026-09-24 — ⚠️ upstream project is in **slow decline**; treat as legacy |

### 4.5 Automation / integration runtime
| Project | URL | Licence | Activity |
|---|---|---|---|
| n8n | https://github.com/n8n-io/n8n | `NOASSERTION` — **Sustainable Use License, source-available, not OSI open source** | 205,962★, push 2026-09-25 |
| Node-RED | https://github.com/node-red/node-red | **Apache-2.0** | 23,687★, push 2026-09-18 |
| Activepieces | https://github.com/activepieces/activepieces | `NOASSERTION` (MIT community + commercial) | 24,728★, push 2026-09-25 |
| Kestra | https://github.com/kestra-io/kestra | **Apache-2.0** | 28,349★, push 2026-09-25 |
| Windmill | https://github.com/windmill-labs/windmill | `NOASSERTION` — LICENSE: **mixed Apache-2.0 / AGPL-3.0 per file**, plus a proprietary licence for enterprise features | 18,032★, Rust, push 2026-09-25 |

### 4.6 Identity, access control, SSO
| Project | URL | Licence | Activity |
|---|---|---|---|
| Keycloak | https://github.com/keycloak/keycloak | **Apache-2.0** | 36,989★, push 2026-09-25 |
| Zitadel | https://github.com/zitadel/zitadel | **AGPL-3.0** | 15,104★, push 2026-09-25 |

### 4.7 Live video / co-presence inside a room
| Project | URL | Licence | Activity |
|---|---|---|---|
| Jitsi Meet | https://github.com/jitsi/jitsi-meet | **Apache-2.0** | 29,990★, push 2026-09-25 |
| LiveKit | https://github.com/livekit/livekit | **Apache-2.0** | 21,113★, Go, push 2026-09-25 — SFU + egress (recording/transcoding), the piece you'd want for recorded in-room walkthroughs |
| BigBlueButton | https://github.com/bigbluebutton/bigbluebutton | **LGPL-3.0** | 9,228★, push 2026-09-25 |
| mediasoup | https://github.com/versatica/mediasoup | **ISC** | 7,376★, C++, push 2026-09-25 |

### 4.8 Qualifying forms, scheduling, flags, storage
| Project | URL | Licence | Activity |
|---|---|---|---|
| Formbricks | https://github.com/formbricks/formbricks | `NOASSERTION` — LICENSE: `apps/web/modules/ee` proprietary; `packages/js`, `android`, `ios`, `api` **MIT**; remainder per root LICENSE (AGPL) | 13,021★, push 2026-09-25 |
| Cal.com (now `calcom/cal.diy`) | https://github.com/calcom/cal.diy | **MIT** (API) | 48,648★, push 2026-09-20 — ⚠️ **the repo was renamed to `cal.diy`; the old `calcom/cal.com` path is the redirect** |
| Unleash | https://github.com/Unleash/unleash | **AGPL-3.0** (LICENSE verified) | active |
| GrowthBook | https://github.com/growthbook/growthbook | `NOASSERTION` — LICENSE: **MIT core + GrowthBook Enterprise License** in named dirs | active |
| MinIO | https://github.com/minio/minio | **AGPL-3.0** (LICENSE on `master`) | S3-compatible object store |
| Liferay Portal | https://github.com/liferay/liferay-portal | `NOASSERTION` — **LGPL-2.1 core + Liferay DXP commercial** | 2,267★, push 2026-09-25. Liferay markets a "Digital Sales Room" capability at `liferay.com/capabilities/digital-sales-room`, but that page is behind a bot challenge (could not fetch) and **the DSR is a DXP/SaaS feature, not part of the LGPL portal.** Do not claim Liferay CE gives you a DSR. |

---

## 5. Explicitly *not* DSRs — false positives I checked and rejected

| Candidate | Why it is not a DSR |
|---|---|
| `gcpaas/DataRoom` (Apache-2.0, 862★, Vue) | A Chinese-language **AI big-screen / BI dashboard** generator. Appears in "open source VDR" listicles purely because of the repo name. |
| `RINDOGATAN/deal-room` | "Smart contract negotiation platform" — blockchain, not a document room. |
| `zahidraja56/deal-room` / `hinorpio/TKFM-Data-Room` / `isfaaghyth/Android-Data-Room` | Unrelated (a CRM-ish "deal room", a TFM data-room site, an Android persistence library). |
| `captableinc/captable` (AGPL-3.0, 824★) | Shows up under `topic:dataroom`, but is a **cap-table / equity management** tool for startups. |
| `documark/documark` (MIT, 19★, last push **2015**) | Name collision. It is a **Jade/Markdown→PDF generator**, not the Documenso fork. Abandoned. |
| `RINDOGATAN/deal-room`, `Reticulate`, `Spinnaker`, `aptly`, `Bizzabo` | Non-DSR. |
| **Guidebook** (commercial) | `guidebook.com` now titles itself "Campus & Event App Platform for Higher Ed". It was a sales-room/onboarding-guide vendor; **the sales-room product is gone from the site.** |
| **Fletch** (commercial) | `fletch.co` now positions as "Embedded and Agentic Financial Services Distribution" (acquisition flows, quote-to-bind). `fletch.co/docs` → 404. The historical "Fletch Limitless" sales room is **not on the site**; current status unverified. |
| **GTMKing** | `gtmking.com/deal-rooms` and `gtmking.com/` both return `window.location.href="/lander"` — a **parked/expired domain**. Only one Wayback capture (2025-07-12). **The company appears to be gone; not verifiable.** |
| **SpinMe** | `spinme.com` resolves but now serves a personal blog ("a shockingly durable blog about business for creators curated by Joe Taylor Jr."). `www.spinme.com` is NXDOMAIN. **Not verifiable as a DSR vendor.** |
| **Folderware** | `folderware.com` and `www.folderware.com` are **NXDOMAIN**; `folderware.io` also NXDOMAIN. Latest Wayback capture is 2014. **Not verifiable.** |
| **Slaytools** | `slaytools.com` and `www.slaytools.com` are **NXDOMAIN**; `slaytools.io` NXDOMAIN. **Not verifiable.** |
| **Onward** | `onward.com` resolves to `67.20.112.130` (a known parking range). `onwardsales.com` returns only a cookie-consent string. **Not verifiable.** |
| **Perplex** | `getperplex.com` is a **Namecheap parking page for sale**. `perplex.ai` SERVFAIL, `perplex.so`/`perplexhq.com` NXDOMAIN. **Not verifiable.** |
| **Humo** | Could not locate. `humoai.co` = a ByteDance multi-modal **video generation** model; `humoai.com` unreachable; `humo.co`/`humo.dev`/`humo.tech`/`humo.work` return empty/403/TLS-error. **Not verifiable.** |
| **6sense** | `6sense.com`, `www.6sense.com/sitemap.xml`, `help.6sense.com` and `apidocs.6sense.com` all sit behind a **Cloudflare "Just a moment… Enable JavaScript" interstitial** (403) from this network, with a normal browser UA and with a Googlebot UA. **No primary-source verification was possible.** Treat 6sense's DSR feature set as unverified from this pass. |

---

## 6. What this means for a build decision

1. **Fork Papermark or build on Nextcloud + Papermark's analytics model.** Papermark is the only
   project where a self-hosting DSR is a working, populated codebase. Consequence: AGPL-3.0 on the
   core, and the commercially-licensed `ee/` directories must be left behind.
2. **Coneshare is the cleanest MIT option** and its architecture — fronting Nextcloud / Drive /
   Dropbox rather than copying files — is the right shape for a self-hosted DSR.
3. **Two licence traps to avoid:** Papermark and Coneshare both depend on **hosted commercial
   services** (Tinybird, Resend, Stripe) for their most valuable parts. A "self-hosted DSR" that
   phones home to Tinybird for page-level analytics is not self-hosted in the sense buyers mean.
4. **Analytic composability is real; room composability is not.** The buyer-analytics side has 20+
   permissively-licensed options (§4.3). The room side has one. Budget accordingly.
5. **The buyer-facing half of a DSR is a security product.** Watermarking, expiry, NDA gating,
   per-recipient links and audit logs are all implemented by Papermark and Coneshare and by *nobody*
   else in open source. This is the highest-value, least-served gap.
6. **Nobody in open source ships MAP, CRM bi-directional sync, or deal-health scoring** — the three
   capabilities the commercial baseline (§1 of `competitive-baseline.md`) treats as table stakes.
