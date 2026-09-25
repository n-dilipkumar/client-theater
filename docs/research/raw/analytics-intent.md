# DOMAIN 2 — analytics-intent: Buyer engagement analytics & intent signals

Research date: 2026-09-26. All workflows below are traced to primary sources that were actually
fetched and read (vendor product docs, vendor developer/API reference, vendor help centres,
vendor engineering blog announcements). No feature is described that was not read in a source.

Primary source set (vendors):
- **Dock** (digital sales rooms / onboarding portals) — `developers.dock.us` API + webhook reference,
  `help.dock.us` help centre, `dock.us/library` product-update posts.
- **Seismic** (Digital Sales Rooms) — `developer.seismic.com` Reporting v2 API reference + webhook catalogue.
- **Liferay DXP** (open-source Digital Sales Room) — `learn.liferay.com/w/digital-sales-room/*`.
- **Salesloft** (Rhythm signals) — `developers.salesloft.com` guides + API reference.
- **Outreach** — `developers.outreach.io` client extensions + REST API webhooks.
- **Albacross** (anonymous-visitor → company identification) — `help.albacross.com`.
- **HubSpot** (buyer intent) — `knowledge.hubspot.com`.

---

## 1. Audit per-buyer engagement inside a room

- **name:** Audit per-buyer engagement inside a room
- **user_flow:**
  1. Open the workspace/sales room (Dock "Workspaces" list) and select a deal.
  2. Switch the top nav to the **Internal** view (internal-only; external users never see it).
  3. Open the **People** tab to get one row per individual contact.
  4. Read per-person **Workspace views**, **Actions taken**, **Last view date**; click into a person for the activity feed.
  5. (Alternative surface) In the Liferay DSR module, open the room and read the **Most Active Visitors** widget, which ranks individuals by total actions.
- **data_flow:** Buyer clicks/views inside the shared (external) workspace → Dock records them against the authenticated workspace user → internal People tab aggregates by `user` → per-person view/action/last-view rows → seller's deal-review decision (who to multi-thread to next).
- **data_sources:** Dock `workspace` + `workspace-user` objects; the `user` object embedded in every webhook payload (with `email`); Liferay room engagement records; uninvited buyer contacts appear by email.
- **apis_hit:** Read surface is the Dock UI; the same data is exposed as the `user` object in `associatedObjects` on every `*.viewed` / `*.clicked` webhook (e.g. `workspace.viewed`, `workspace.page.viewed`) and can be pulled with `GET https://api.dock.us/v1/workspaces/{id}`, `GET /v1/workspace-users`, `GET /v1/users` (Bearer token; `properties` query param selects returned fields). Liferay has no public DSR read API documented in the pages read.
- **automations:** None needed for the read path, but the data is continuously produced by buyer activity with no user action; Dock notes the People analytics is applied **retroactively to all existing workspaces**. As a feed source it can be combined with workflow #11 (webhook stream).
- **features_tools:** Dock **Internal** view → **People** tab; Dock **Activity Feed** ("an activity feed showing what action has been taken in that space, internal/external participation analytics"); Liferay **Most Active Visitors** widget.
- **extensibility:** A third party consumes this by subscribing to Dock webhooks and keying on `associatedObjects.user.id` / `.email`, or by polling `GET /v1/workspace-users`; workspace custom fields can be written back via `PATCH /v1/workspaces/{id}` / `PATCH /v1/deals/{id}` to store derived per-buyer state.
- **sources:**
  - https://www.dock.us/library/people-analytics
  - https://help.dock.us/en/articles/6867976-navigating-workspaces-internally
  - https://learn.liferay.com/w/digital-sales-room/engagement-metrics-dashboard
  - https://developers.dock.us/webhooks/event-types.md
  - https://developers.dock.us/api-reference/introduction
- **evidence:**
  - Dock: "To track which contacts are engaging with a workspace, just head to the **Internal** tab and select **People.**" and "For each person who accesses a workspace, you get: Workspace views / Actions taken / Last view date." Also: "This will apply retroactively to all your workspaces too, so check out any of your existing workspaces" and "Customer-side contacts that you haven't invited to the workspace will show up by their email."
  - Dock help: the Internal view contains "an activity feed showing what action has been taken in that space, internal/external participation analytics".
  - Liferay: "**Most Active Visitors:** Lists individuals ranked by their total actions."
  - Dock webhook `user` object shape: `"user": { "id": "6bxK1Cyh88EK", "object": "user", "url": "https://api.dock.us/v1/users/6bxK1Cyh88EK", "email": "john.doe@example.com" }`.
  - Stated use cases: "Track key stakeholder activity: See if a particular buyer-side contact—like the economic buyer—is engaging with your sales materials (or not)" and "Identify changes in stakeholders: Uncover new stakeholders or opportunities you weren't aware of".

---

## 2. Inspect a single room's engagement dashboard (dwell, live feed, trend)

- **name:** Inspect a single room's engagement dashboard
- **user_flow:**
  1. Global Menu → **Commerce → Digital Sales Room Management** (Liferay Launchpad).
  2. Click **Analytics** in the left sidebar for the all-rooms aggregate.
  3. Click a deal card / metric to drill into that room's engagement view.
  4. Read the widgets: **Room Stats** (Time Viewed, Total Visits, Visitors, Actions), **Most Active Visitors**, **Most Engaged Documents**, **Latest Activity** (live feed), **Recent Engagement** (time series), **Visit Frequency** (by day/week), **Room Trend** (Cold/Warm/Hot).
  5. Switch the room selector at the top right to scope a different deal.
- **data_flow:** Liferay DSR emits interaction records for the room → stored as LDP (Liferay Data Platform) metrics → aggregated into Launchpad Analytics tiles and per-room widget charts → seller's prioritise-follow-up decision.
- **data_sources:** Liferay Data Platform (LDP) — a hard prerequisite; Liferay DSR room, document, and account objects; buyer sessions.
- **apis_hit:** No public DSR REST/GraphQL endpoint is documented on the pages read. LDP is the backing store; the Launchpad is the documented UI. Related Liferay headless capabilities exist elsewhere in the platform but were **not** documented for DSR metrics in the sources read.
- **automations:** Dashboard-level **Alerts** for "rooms with low engagement or approaching deadlines" fire without user action. Metric refresh is continuous (the launchpad is described as giving "real-time interaction data").
- **features_tools:** Liferay DSR **Launchpad → Analytics**; widgets **Room Stats**, **Most Active Visitors**, **Most Engaged Documents**, **Latest Activity**, **Recent Engagement**, **Visit Frequency**, **Room Trend**; **Timeline** tab for a chronological log of room updates.
- **extensibility:** Requires connecting the Liferay instance to an LDP environment — that is the documented extension point for self-hosted/custom deployments. Downstream consumption of the same data is not documented.
- **sources:**
  - https://learn.liferay.com/w/digital-sales-room/engagement-metrics-dashboard
  - https://learn.liferay.com/w/digital-sales-room/dsr-launchpad
  - https://learn.liferay.com/w/digital-sales-room/index
- **evidence:**
  - "The Digital Sales Room (DSR) tracks buyer engagement across every active room and surfaces it in two places: an aggregate Analytics view in the DSR Launchpad for monitoring your full pipeline, and per-room metrics for inspecting how a single buyer interacts with the content you've shared."
  - "DSR reports engagement using Liferay Data Platform (LDP) metrics, so your instance must be connected to an LDP environment. Every metric on this page requires that connection."
  - "**Room Stats:** View Time Viewed (e.g., 5h 32 min), Total Visits, Visitors, and Actions (document views, downloads, and comments)."
  - "**Latest Activity:** See a live feed of the most recent buyer actions, showing the user, the action they took, and when it occurred."
  - "**Room Trend:** Indicates the room's engagement health as Cold, Warm, or Hot."
  - "Alerts for rooms with low engagement or approaching deadlines."
  - "Use the Timeline tab to access a chronological log of updates to ensure your team remains aligned on deal developments."

---

## 3. Read per-page dwell time and drop-off inside a PDF

- **name:** Read per-page dwell time and drop-off inside a PDF
- **user_flow:**
  1. Open **Content Management → Library** in Dock.
  2. Open a multi-page **PDF** asset.
  3. Scroll to **Advanced Analytics → PDF Analytics**.
  4. Read **Time spent per page** and **Drop off per page** to see which pages resonate and where readers stop.
  5. (Adjacent) For self-hosted video assets, read **average watch time** under Video Analytics.
- **data_flow:** Buyer opens the PDF inside a workspace or via a trackable asset link → Dock's viewer emits per-page timing → aggregated to an average per page plus a drop-off curve per page → seller decides which pages to cut, reorder, or re-pitch.
- **data_sources:** Dock `asset` objects (`type: pdf`, `type: video`), workspace file viewer, asset share links, external (buyer) user sessions.
- **apis_hit:** No documented per-page analytics endpoint. Adjacent documented surfaces: `GET https://api.dock.us/v1/assets`, `GET /v1/assets/{id}`, and the `asset.viewed` / `asset.downloaded` webhooks whose payload embeds the `asset` snapshot (`name`, `type`, `shareUrl`, `isInternal`, `tags`, `downloadEnabled`, `trackingEnabled`).
- **automations:** Analytics are computed continuously; the *action* on them is manual. Asset views can be fanned out in real time via the `asset.viewed` webhook (see #11).
- **features_tools:** Library asset detail page → **Advanced Analytics**; **PDF Analytics** (Time spent per page, Drop off per page); **Video Analytics** (average watch time); **Core Analytics** bar charts.
- **extensibility:** A third party can build its own per-page scoring by consuming `asset.viewed` events and joining to its own viewer telemetry; there is no documented Dock endpoint for per-page timing.
- **sources:**
  - https://help.dock.us/en/articles/6989922-content-analytics
  - https://developers.dock.us/webhooks/event-types.md
- **evidence:**
  - "**PDF Analytics** — For multi-page PDFs, we're able to show two additional metics: **Time spent per page:** the average amount of time that's spent per page. This shows you what content resonates most with your audience. / **Drop off per page:** understand when someone stops looking at your content. This gives you a sense of where people are falling off and what may be less valuable to share."
  - "**Video Analytics** — For self-hosted videos, we're able to show you the average watch time of the video."
  - "Dock's analytics only show engagement from external users (i.e. buyers and customers). The one exception is 'Shares' which is an internal metric showing how often the internal team shares a specific asset."

---

## 4. Rank content influence and associate revenue with assets

- **name:** Rank content influence and associate revenue with assets
- **user_flow:**
  1. Open the **Reports** tab on the Dock dashboard and choose **Content Influence**.
  2. Read portfolio metrics: **Number of assets**, **Content shares**, **Content client views**, **Utilization rate**, **Engagement rate**.
  3. Read the **Content engagement over time** graph (day / week / month / quarter / year).
  4. Open **Top content** — most-viewed assets plus shares, **total time spent**, downloads, last share, last view; sort by any column.
  5. Read **Content & Sales Influence** to see revenue and deals associated with each asset (requires CRM integration and accounts/deals linked to workspaces).
  6. Filter by collection, client-activity time range, and shares time range.
- **data_flow:** Library asset share/view events across all workspaces + CRM account/deal/opportunity links → aggregated into utilization/engagement rates and a per-asset revenue breakdown → content governance and quota decisions.
- **data_sources:** Dock Content Management Library (assets, collections, tags); Dock `workspace` activity; CRM integration (Salesforce/HubSpot) accounts, deals/opportunities; internal share events by reps.
- **apis_hit:** Reported through the UI. Underlying data available via `GET https://api.dock.us/v1/assets`, `GET /v1/accounts`, `GET /v1/deals`, and the `asset.viewed` / `asset.shared` / `asset.downloaded` webhooks. CRM-side joins are surfaced as Dock workspace fields, not as a documented DSR API.
- **automations:** None inherent — this is a periodic report. The revenue join depends on the CRM integration continuously syncing accounts/deals to workspaces.
- **features_tools:** Dock **Reports → Content Influence**; filterable by collection / client activity / shares; **Top content** table; **Content & Sales Influence** breakdown; **Library Views & Tags** organisation.
- **extensibility:** Third parties can reproduce this in BI by piping the `asset.*` and `workspace.*` webhooks into a warehouse ("Push Dock activity to a data warehouse to analyze the data in your own BI tools") and joining to CRM extracts.
- **sources:**
  - https://help.dock.us/en/articles/9114397-content-influence-report
  - https://help.dock.us/en/articles/6989922-content-analytics
  - https://www.dock.us/library/api-webhooks
- **evidence:**
  - "The Content Influence report analyzes activity of library assets across all workspaces. Here you'll find the following insights: Number of assets, Content shares, Content client views, Utilization rate, Engagement rate, Content engagement over time, Top content."
  - "**Utilization rate:** the % of content that has been shared at least once." / "**Engagement rate:** the % of content has been viewed at least once."
  - "The top content report is organized by most viewed content, but also shows additional reporting such as amount of shares, total time spent, download amount, last share, and last view."
  - "**Content & Sales Influence** — This report will be shown assuming you have integrated with your CRM, and have connected accounts & deals/opportunities to workspaces. For each piece of content, you will see the breakdown of revenue and deals associated with the asset."
  - "Please note, this report relies on your Content Management Library having been built out!"
  - "Push Dock activity to a data warehouse to analyze the data in your own BI tools" (Dock webhooks announcement).

---

## 5. Extract DSR viewing sessions (dwell time + geography) for BI

- **name:** Extract DSR viewing sessions (dwell time + geography) for BI
- **user_flow:**
  1. Generate a Seismic JWT / API token in the Seismic admin console.
  2. Call `GET https://api.seismic.com/reporting/v2/digitalSalesRoomViewingSessions` nightly from an ETL job, with `modifiedAtStartTime` / `modifiedAtEndTime` (or `sessionStartedAtTime` / `sessionEndedAtTime`) for incremental extraction.
  3. Set `Accept: application/json` or `Accept: text/csv`.
  4. Join each row to `GET /reporting/v2/digitalSalesRooms` on `digitalSalesRoomId` to resolve the room.
  5. Land rows in a data warehouse / lake and compute dwell-time, geography, internal-vs-external, and per-user engagement in BI.
- **data_flow:** Buyer opens a DSR link in a browser tab → Seismic records a viewing session with start/end timestamps, `roomDurationSeconds`, geo fields, IP, and the engagement user's email/internal flag → incremental ETL by `modifiedAt` → warehouse → BI dashboards.
- **data_sources:** Seismic DSR records (`digitalSalesRooms`); DSR link sessions; Seismic `userId`; buyer email; IP + derived city/state/country/lat/long.
- **apis_hit:**
  - `GET https://api.seismic.com/reporting/v2/digitalSalesRoomViewingSessions` — "Provides the list of viewing sessions by DSR recipients including the datetime that the session started and ended." Query params: `limit`, `modifiedAtStartTime`, `modifiedAtEndTime`, `sessionStartedAtTime`, `sessionEndedAtTime`; header `Accept: application/json | text/csv`. Response fields include `digitalSalesRoomId`, `roomDurationSeconds`, `engagementUserEmail`, `isEngagementUserInternal`, `sessionStartedAt`, `sessionEndedAt`, `city`, `state`, `country`, `ipAddress`, `geolocationLatitude`, `geoLocationLongitude`, `userId`.
  - `GET https://api.seismic.com/reporting/v2/digitalSalesRooms` — room inventory; params `limit`, `modifiedAtStartTime`, `modifiedAtEndTime`, `createdAtStartTime`, `createdAtEndTime`; returns `id`, `name`, `digitalSalesRoomTemplateId`, `digitalSalesRoomTemplateVersionId`, `createdBy`, `createdByUsername`, `createdAt`, `modifiedAt`, `userModifiedAt`.
  - Auth: `Authorization: Bearer <JWT>` on every call.
- **automations:** Reporting data refreshes on a fixed SLA — "updated **no less than every 24 hours**" — so the incremental `modifiedAt` sweep is the intended scheduled job. Seismic explicitly warns these APIs "are not designed to be used in high-frequency, interactive use cases."
- **features_tools:** Seismic Reporting (v2) API; CSV or JSON output; a data dictionary and a recommended star schema are published by Seismic for the consuming BI layer.
- **extensibility:** Seismic publishes a "Data Dictionary" and a "Recommended Schema" (star schema) for warehouses/data lakes; `Accept: text/csv` allows flat-file loads; a data-dictionary page lists every model and field.
- **sources:**
  - https://developer.seismic.com/seismicsoftware/reference/reporting-digitalsalesroomviewingsessionsget
  - https://developer.seismic.com/seismicsoftware/reference/reporting-digitalsalesroomsget
  - https://developer.seismic.com/seismicsoftware/reference/h1-reporting-api-overview
- **evidence:**
  - "Each row represents a single session in a DSR link from a single user in a single browser tab." (This is the tab-level dwell-time granularity.)
  - Response example includes `"roomDurationSeconds": 1`, `"engagementUserEmail": "test engagementUserEmail"`, `"isEngagementUserInternal": true`, `"city": "test city"`, `"ipAddress": "test ipAddress"`, `"geolocationLatitude": 4`.
  - "The reporting APIs are built with data extraction (ETL) in mind. They are intended to allow large portions of data to be extracted to an external database/data lake/data warehouse."
  - "The data that is available through our reporting APIs is updated **no less than every 24 hours**."
  - Date-filter semantics: "modifiedAtStartTime / EndTime (preferred for all use cases) — This is a data modifiedAt time and has no 'business' meaning. It is meant entirely for machines to know what rows may have changed so that it can pull the updates and merge them into existing data sets."
  - "Most modern business intelligence platforms require a form of star schema."

---

## 6. Classify workspace engagement health (Hot / Warm / Cooling / Cold)

- **name:** Classify workspace engagement health
- **user_flow:**
  1. Open the Dock **Workspaces** dashboard (or clone a default view).
  2. Ensure the **Trend** column is present (addable to a customised view).
  3. Sort/filter on Trend plus `Last Client View` to isolate Hot rooms and Cold rooms.
  4. Act: re-engage Cold rooms, protect Hot ones.
- **data_flow:** Workspace-level engagement events (views, clicks, downloads, interactions) → recency bucket computed against sliding windows (7 / 14 / 30 days) → a single Trend value per workspace → dashboard column used for prioritisation.
- **data_sources:** Dock workspace activity events (`workspace.viewed`, `workspace.page.viewed`, `workspace.file.viewed`, `workspace.link.clicked`, `workspace.order_form.*`); owner/team metadata.
- **apis_hit:** Documented as a **Dock activity metric** on the dashboard; no separate public API in the sources read. The same signals are available as `workspace.*` webhooks, so a third party could recompute or mirror the buckets.
- **automations:** The classification recomputes continuously from activity; no user action. Buckets are time-window based, so a workspace decays from Hot → Warm → Cooling → Cold without any new activity.
- **features_tools:** Dock workspace dashboard **Trend** column; **filter and sort** by owner, workspace creation date, recent client activity.
- **extensibility:** Recompute externally from `workspace.*` webhooks (`occurredAt`) to add your own health model; Dock also documents a parallel `Room Trend` Cold/Warm/Hot widget in Liferay DSR — a precedent for a per-room health badge.
- **sources:**
  - https://help.dock.us/en/articles/11664129-trends-in-workspace-views
  - https://learn.liferay.com/w/digital-sales-room/engagement-metrics-dashboard
- **evidence:**
  - "In your workspace dashboard view you will see a column for Trend (which you can also add in if you don't see it on your current customized view!). This is a Dock activity metric created to give your team a quick pulse on workspace health and external engagement."
  - "These are based on an algorithm of the engagement of a workspace. **Hot** = workspaces that have tons of recent engagement within the last 7 days. **Warm** = workspaces that have a decent amount of engagement within the last 14 days. **Cooling** = workspaces that previously had engagement, but none within the last 14 days. **Cold** = workspace with no engagement within the last month."
  - Liferay: "**Room Trend:** Indicates the room's engagement health as Cold, Warm, or Hot."

---

## 7. Triage the pipeline with saved workspace views

- **name:** Triage the pipeline with saved workspace views
- **user_flow:**
  1. Open the Dock **Workspaces** dashboard.
  2. Click **Add view** and start from a default view: **All Workspaces**, **My Workspaces**, **Active Pipeline**, **Deal Desk**, or **Implementations**.
  3. **Clone** the view, then filter/sort by owner, creation date, recent client activity, CRM stage, workspace type.
  4. Edit and rearrange **columns** — add **Views**, **Actions**, **Last Client View**, `Stage`, `Team`, plus CRM columns such as Salesforce `Opportunity Stage` / `Opp Amount` or HubSpot `Deal Stage` / `Deal Amount` / `Deal Closed Date`, and Order form `Status`.
  5. Save as a **private view** (personal) or **public view** (team); Dock remembers the views you had open.
- **data_flow:** Dock workspace metadata + engagement metrics + CRM-synced opportunity/deal fields → joined rows in a configurable table → sorted/filtered slice used for pipeline triage.
- **data_sources:** Dock workspaces (with `workspace type` set on the template or per-workspace Settings), Dock engagement metrics, CRM (Salesforce / HubSpot) opportunity & deal objects, order-form records.
- **apis_hit:** Documented as a UI capability. Row data maps to `GET https://api.dock.us/v1/workspaces` (with `properties` for selected fields and `workspaceFilters`/`workspaceDomainFilters` params) plus `GET /v1/deals` / `GET /v1/accounts`. `PATCH /v1/workspaces/{id}` can write custom field values, and workspace pages/sections can be shown/hidden via `PATCH /v1/workspace-pages/{id}` / `PATCH /v1/workspace-sections/{id}` based on CRM state.
- **automations:** Workspace `type` is inherited from the template so "Any future workspaces created from that template will be automatically categorized". CRM sync keeps the joined columns current without user action.
- **features_tools:** Dock **Add view** / **Clone** / **filter** / **sort** / **edit and rearrange columns**; default views incl. **Active Pipeline** ("Any workspace that has a 'Sales' workspace type, or an opportunity or deal connected from your CRM") and **Deal Desk** ("Any workspaces that uses Dock's order forms"); **workspace type** on template Settings.
- **extensibility:** Private vs public views let a vendor add team-shared pipeline surfaces; column/filter set is user-defined so new CRM fields flow through automatically; deeper automation uses `POST /v1/workspaces`, `PATCH /v1/workspaces/{id}`, and the "Dynamic workspaces: Show or hide specific workspace sections based on what a customer has done in your product" pattern documented by Dock.
- **sources:**
  - https://www.dock.us/library/workspace-views
  - https://developers.dock.us/api-reference/introduction
  - https://developers.dock.us/llms.txt
- **evidence:**
  - "The dashboard combines all the analytics from your Dock sales deal rooms and customer onboarding plans with data from your CRM."
  - "You can create **private views** for yourself or **public views** for your entire team." / "You can **filter** and **sort** views by owner, workspace creation date, recent client activity, and other advanced filters." / "You can also **clone** existing views to make your own customized copy." / "The views you have open are unique to your user account. We'll remember which views you had open the next time you open the Workspaces dashboard."
  - Available fields include "**Engagement analytics:** Views, Actions, Last Client View", "**Salesforce data:** Opportunity Stage, Opportunity Created Date, Opp Amount, Opportunity Type", "**Hubspot data:** Deal Stage, Deal Type, Deal Closed Date, Deal Amount", "**Order forms**: Status and Deal Type".
  - Dock API: "Endpoints that return a resource accept a `properties` query parameter that controls which fields are included in the response. If you omit it, the response contains **only** the resource's `id`, `object`, and `url`".
  - "**Dynamic workspaces:** Show or hide specific workspace sections based on what a customer has done in your product".

---

## 8. Relate buyer engagement to CRM pipeline and close rate

- **name:** Relate buyer engagement to CRM pipeline and close rate
- **user_flow:**
  1. Ensure sales workspaces are typed "Sales" and have a CRM opportunity attached (Settings in the workspace **Internal** tab).
  2. Open **Reports → Sales Impact**.
  3. Read **Sales Impact (synced to your CRM)**: Total deals, Total pipeline touched, Active deals, Active pipeline, Closed won deals, Revenue, Close rate, Days to close.
  4. Read **Deals Created Over Time** and **Deals By Owner**.
  5. Read **Buyer Engagement**: Buyer Views, Buyer Actions, Buyer Views Over Time, **Most Engaged Buyers**.
  6. Filter by date range, CRM stage, owners, teams.
- **data_flow:** Buyer engagement in Sales-type workspaces + CRM opportunity/deal objects → pipeline-weighted rollup (pipeline touched, close rate, avg days to close) → leadership read on whether engagement correlates with revenue outcomes.
- **data_sources:** Dock Sales-type workspaces; Dock buyer views/actions; CRM (Salesforce/HubSpot) opportunities and deals including stage, amount, close date, owner.
- **apis_hit:** Report is UI-driven. Data is available via `GET https://api.dock.us/v1/deals`, `GET /v1/deals/{id}`, `GET /v1/accounts`, `GET /v1/workspaces` with `properties` selection, plus `workspace.*` webhooks for the engagement side. Dock's announcement lists "Push Dock workspace links to other CRMs (we already have native integrations with Salesforce and HubSpot)."
- **automations:** CRM integration must be on and deals attached or the report is incomplete — "unless you are requiring reps attach a deal to each space, it's possible this report is missing data." Deal stage/amount sync keeps the rollup fresh.
- **features_tools:** Dock **Reports → Sales Impact**; metric tiles with drill-in; **workspace type = Sales** setting; CRM stage/owner/team filters.
- **extensibility:** Recreate outside Dock by joining `workspace.*` webhook events to your own CRM deal extract; the 429 "Too many requests" rate-limit response and `properties` parameter are the documented API constraints to design around.
- **sources:**
  - https://help.dock.us/en/articles/9006259-sales-impact-report
  - https://www.dock.us/library/reports
  - https://developers.dock.us/api-reference/introduction
- **evidence:**
  - "The Sales Impact report pulls in any workspace designated as a 'Sales' type that has a CRM opportunity."
  - Insights list: "Sales Impact (synced to your CRM): Total deals, Total pipeline touched, Active deals, Active pipeline, Closed won deals, Revenue (closed won revenue), Close rate, Days to close (average) … Buyer Engagement: Views, actions, and average buyers per workspace, Most engaged buyers."
  - "**Close rate** — How many workspaces with deals/opportunities that have been closed won, divided by the total (closed won + closed lost)."
  - "This report combines your CRM data with Dock workspace data to show the impact Dock has on your pipeline and close rates."
  - "To populate this report, remember to set the **workspace type** for your sales workspaces from the **Settings** in the workspace's **Internal** tab."

---

## 9. Roll up client engagement and multi-threading portfolio-wide

- **name:** Roll up client engagement and multi-threading portfolio-wide
- **user_flow:**
  1. Open the Dock **Reports** tab → **Client Engagement**.
  2. Read the tiles: **Total client views**, **Total client actions**, **Average unique clients per workspace**, **Client views over time**, **Most engaged clients**.
  3. Click a metric tile to expand the full list of accounts and their engagement; sort by any column.
  4. Filter by date range, owners, teams.
  5. Read out loud: is the account being multi-threaded, and who is the champion?
- **data_flow:** External (client) activity across **all** workspaces → account-level rollup of views, actions, unique clients per workspace, ranked most-engaged clients → leadership/coaching decision on account coverage.
- **data_sources:** Dock `workspace` + `workspace-user` activity across the whole instance; external vs internal user distinction; account and deal links.
- **apis_hit:** UI report. Equivalent data via `GET https://api.dock.us/v1/workspaces` (+ `properties`), `GET /v1/accounts`, `GET /v1/workspace-users`, and the full `workspace.*` webhook stream.
- **automations:** Report spans all workspaces automatically ("Reports in Dock automatically show holistic data across all workspaces"); underlying event capture is continuous.
- **features_tools:** Dock **Reports → Client Engagement**; expandable metric tiles; **Team Usage** counterpart report for internal reps; click-through to per-account lists.
- **extensibility:** Third parties push the webhook stream into a warehouse/BI tool instead; per Dock, "Webhooks allow you to push all the workspace activity data out of Dock for use in other applications." Related Dock report in the same family: **Implementations Report** (Total/Active/Completed implementations, Time to completion average, % completed on time, Implementations by owner, Customer Views/Actions, Most Engaged Customers) for at-risk delivery tracking.
- **sources:**
  - https://help.dock.us/en/articles/9006160-client-engagement-report
  - https://www.dock.us/library/reports
  - https://www.dock.us/library/api-webhooks
  - https://help.dock.us/en/articles/9006640-implementations-report
- **evidence:**
  - "The Client Engagement report analyzes external activity across ALL workspaces in your Dock instance. Here you'll find the following insights: Total client views, Total client actions, Client views over time, Most engaged clients."
  - "**Total client actions** counts how many times a client has interacted with a space. We think of this as clicking into pages, embedded content, etc. Click into the cell to expand upon who these individuals are!"
  - "This report tracks how well you're multithreading accounts, helps you identify champions, and lets you track trends in engagement over time."
  - "Reports in Dock automatically show holistic data across all workspaces. You can filter the report down by date range, owners, and/or teams."
  - Dock reports announcement also lists "**Team Usage:** How actively is your team using Dock?" and "**Implementation Status:** How long are customer implementations taking?"
  - "Webhooks allow you to push all the workspace activity data out of Dock for use in other applications." / "Push Dock activity to a data warehouse to analyze the data in your own BI tools."

---

## 10. Stream workspace activity events to your own systems in real time

- **name:** Stream workspace activity events to your own systems in real time
- **user_flow:**
  1. As an account **admin**, go to **Settings → Webhooks** (under **Data Management**).
  2. Click **Create Webhook**, name it, and enter the HTTPS target URL (Dock verifies the URL with a POST).
  3. Copy the generated **secret** (View Key) and verify each delivery's signature.
  4. On the **Subscriptions** page, click **Create subscription** and pick subscription types (e.g. `workspace.viewed`, `workspace.page.viewed`, `workspace.file.downloaded`, `workspace.order_form.viewed`, `workspace.form.submitted`, `asset.viewed`).
  5. Pause or unsubscribe from the **Webhook** page when needed; use **Send test events** during setup.
  6. Consume `webhook-event` JSON and push to a data warehouse, Slack, or a CRM.
- **data_flow:** Buyer action inside the workspace → Dock emits a `webhook-event` with `occurredAt`, `propertyName`/`propertyPreviousValue`/`propertyValue`, and `associatedObjects` (`workspace`, `account`, `user`, plus `workspacePage` / `workspaceSection` / `workspacePlanTask` / `file` / `workspaceForm`) → POST to the subscriber URL → subscriber's warehouse/CRM/BI.
- **data_sources:** Dock `workspace`, `workspace-page`, `workspace-section`, `workspace-plan`, `workspace-plan-task`, `file`, `workspace-form`, `form-question`, `asset`, `user`, `account` objects; private object storage for presigned file URLs.
- **apis_hit:**
  - `POST` to the configured webhook URL; subscription types include `workspace.created`, `workspace.viewed`, `workspace.page.viewed`, `workspace.section_navigation.clicked`, `workspace.file.viewed`, `workspace.file.downloaded`, `workspace.link.clicked`, `workspace.text_link.clicked`, `workspace.embed.interacted`, `workspace.custom_code.interacted`, `workspace.order_form.viewed|downloaded|signed|fully_signed`, `workspace.NDA.signed`, `workspace.form.submitted`, `asset.viewed|shared|downloaded`, `presentation.viewed|downloaded|shared`, `course.completed|course.reviewed`.
  - REST API for pull-based backfill: base `https://api.dock.us`, `Authorization: Bearer <Your-Token>`; `GET /v1/workspaces`, `GET /v1/workspaces/{id}`, `GET /v1/assets`, `GET /v1/forms/{id}/responses`, `GET /v1/workspace-plan-tasks`; `properties` query param; `429` on rate limit.
  - Seismic equivalent (for a second vendor's DSR): `DSRCreatedV1`, `DSRUpdatedV1`, `DSRExpiredV1`, `DSRContentUpdatedV1` webhooks with `x-seismic-signature` HMAC header, 26-retry policy (1 min, then 10 min, then hourly ×24) and a 10-second webhook timeout; and `livesend-session-summary-enriched-v3` / `email-enriched-v3` events.
- **automations:** Fully event-driven — every buyer action fires without user action. Delivery reliability: Dock documents a secret for verifying requests and a retry behaviour page; Seismic documents its own retry ladder. Presigned file URLs in payloads expire ("The URL expires at `expiresAt` (one hour)").
- **features_tools:** **Settings → Webhooks**, **Create Webhook**, **View Key** (secret), **Subscriptions** page (create/view/pause/unsubscribe), **Send test events**; Seismic app-level webhook URL with JSONPath filter expressions (e.g. `$.data..[?(@.teamSiteId == '1')]`) and signing-secret rotation with `x-seismic-signature-old`.
- **extensibility:** This is the primary vendor-sanctioned extension point: "Dock now has an API and webhooks. Both are available now to Enterprise customers through our early-access program." Sub-filtering happens in the subscriber; Seismic offers server-side JSONPath filters. Note `workspace.form.submitted` payloads carry `formQuestions` + `formQuestionResponses` (typed questions incl. `file_upload`) and `asset.*` payloads include `trackingEnabled` to distinguish gated share links.
- **sources:**
  - https://developers.dock.us/webhooks/introduction
  - https://developers.dock.us/webhooks/event-types.md
  - https://developers.dock.us/webhooks/create-subscription.md
  - https://developers.dock.us/api-reference/introduction
  - https://developers.dock.us/llms.txt
  - https://developer.seismic.com/seismicsoftware/docs/webhooksoverview
  - https://www.dock.us/library/api-webhooks
- **evidence:**
  - Dock: "Use webhooks to get real-time notifications on events happening across your Dock workspace." Listed uses: "Sending click events in real-time for further processing", "Triggering a Slack notification when someone clicks on your pitch deck link", "Tracking library asset views, shares, and downloads".
  - "You must be an account `admin` to create a webhook." / "Go to **Settings** and click on **Webhooks** from the **Data Management** section".
  - "A subscription can be **viewed in detail**, **paused** or **unsubscribed** from the **Webhook** page."
  - "Anonymous activity omits `user`." (important for anonymous deal-room visitors)
  - "`presentation.viewed` and `presentation.downloaded` are emitted for presentation share-link activity only. Asset link activity is available through `asset.viewed`, `asset.shared`, and `asset.downloaded`."
  - Seismic: "Seismic sends a `x-seismic-signature` header with each webhook request. This header contains a HMAC signature of the request body." / "Total number of retries: 26" / "Seismic will wait for **10 seconds** for the webhook to respond."
  - Seismic DSR event table: "DSRCreatedV1 (Early Access) — Occurs when dsr is created", "DSRContentUpdatedV1 — Occurs when a file in dsr is updated".

---

## 11. Write DSR events into the seller activity feed

- **name:** Write DSR events into the seller activity feed
- **user_flow:**
  1. In the Outreach developer portal, create an app and add the **Activity feed custom events** feature.
  2. Configure one or more custom events (event name + template string; `{{prospect}}` placeholder supported).
  3. On each qualifying DSR event, `POST https://api.outreach.io/api/v2/events` with an S2S token, the configured `name`, an `externalUrl` deep link back into the DSR, an optional `body`, and a `prospect` relationship.
  4. Reps see the event card in the prospect activity feed; the card links back to the DSR.
- **data_flow:** DSR engagement event → mapped to a configured custom event name → JSON:API `event` create with a `prospect` relationship → Outreach prospect activity feed → seller sees a chronological, clickable trail of DSR intent.
- **data_sources:** Outreach `prospect` object (and the account/opportunity behind it); the DSR event stream as the source.
- **apis_hit:** `POST https://api.outreach.io/api/v2/events` with `Authorization: Bearer S2S_TOKEN` and body `{"data":{"type":"event","attributes":{"name":"my-app:my-event","externalUrl":"…","body":"…"},"relationships":{"prospect":{"data":{"type":"prospect","id":"PROSPECT_ID"}}}}}`. Related: the **Mailing links custom tracker** client extension ("allows you to replace the embedded links in Outreach mailings with custom tracking URLs") and the tab/tile extensions for rendering a DSR widget on the Prospect/Opportunity/Account detail page.
- **automations:** No user action on the Outreach side once configured — the feed entry appears as events arrive. Outreach webhooks can also carry intent back out: `mailing` events include `bounced`, `delivered`, `opened`, `replied`; `POST https://api.outreach.io/api/v2/webhooks` with `payloadVersion: 2` returns a `beforeUpdate` block; deliveries carry an `Outreach-Webhook-Signature` HMAC header; "The timeout while waiting for response is set to 5 seconds."
- **features_tools:** Outreach **Activity feed** on the prospect record; developer portal **Client extensions → Activity feed custom events**; **Text editor extension**; **Mailing links custom tracker**.
- **extensibility:** Event names are app-scoped (`<app identifier>:<event id>`); localized descriptions are configured as templates. A seller platform that whitelists frontend integrations also exposes Salesloft's `POST /v2/third_party_live_feed_items` ("Creates a live feed item that can be sent to users. May only be used by whitelisted Frontend Integrations with notifications:write, people:read, and accounts:read scopes.") and `POST /v2/live_website_tracking_parameters` ("Creates a Live Website Tracking parameter to identify a person").
- **sources:**
  - https://developers.outreach.io/client-extensions/activity-feed-custom-events
  - https://developers.outreach.io/client-extensions
  - https://developers.outreach.io/api/webhooks
  - https://developers.salesloft.com/docs/api/third-party-live-feed-items-create/
  - https://developers.salesloft.com/docs/api/live-website-tracking-parameters-create/
- **evidence:**
  - "If you are aware of interesting events happening to Outreach prospects you can send custom events to indicate these changes inside prospect activity feed."
  - "Add the 'Activity feed custom events' feature to your app, then configure one or more custom events. The event name and template will be displayed in the activity feed. In the payload you can additionally send accompanying text that will also appear in the event card."
  - `curl https://api.outreach.io/api/v2/events -X POST -H "Authorization: Bearer S2S_TOKEN" -d …`
  - "The event card will also contain the template string you have configured for the event. In the template string you can use the {{prospect}} placeholder which Outreach will replace with a link to the prospect."
  - Outreach webhook resources: "mailing* created updated destroyed bounced delivered opened replied"; "Outreach does not retry webhook deliveries upon receiving any of the Status Codes including 500 Internal Server Error and 429 Too Many Requests."

---

## 12. Emit buyer intent signals with indicators, urgency, and attribution

- **name:** Emit buyer intent signals with indicators, urgency, and attribution
- **user_flow:**
  1. Register a **signal type** once per integration: `POST https://api.salesloft.com/v2/integrations/signals/registrations/` with `signal_name`, `type`, `data_shape` (JSON-Schema), localized `description` (ICU Messages), at least one `indicator` with `key` + `metadata_shape`, and an `attribution` list.
  2. On each qualifying DSR interaction, emit a live signal: `POST https://api.salesloft.com/v2/signals.json` with `type`, `data`, `indicators[]`, `urgency` (high/medium/low), `occurred_at`, `idempotency_key` (UUID4), `attribution` object, and `broadcast_notification`.
  3. Salesloft hydrates the signal to a Person/Account and publishes the rendered description to the seller's **Live Feed**.
  4. Verify by sending a signal and checking the Live Feed for the correct seller.
- **data_flow:** DSR interaction (e.g. PDF viewed, video watched >75%, order form opened) → matched to a registered indicator with quantified metadata (e.g. `spent_more_than_30s_on_site`, `time_viewed_seconds`) → `POST /v2/signals` → Salesloft resolves `attribution` (`person_id`, `account_id`, `opportunity_id`, `email_tracked_content_id`, `user_guid`) to the receiving seller → Live Feed notification / task generation.
- **data_sources:** Salesloft `Person`, `Account`, `Opportunity`, `Email Content`, `User` objects; the signal registration (`data_shape` / `metadata_shape`) defines the contract; the DSR event stream is the producer.
- **apis_hit:**
  - `POST https://api.salesloft.com/v2/integrations/signals/registrations/` — register signal type; fields `signal_name`, `type`, `broadcast_notification`, `data_shape`, `description` (localized), `indicators[]`, `idempotency_key`, `attribution[]`, `integration_id`.
  - `POST https://api.salesloft.com/v2/signals` (also documented as `POST /v2/signals.json`) — "Deliver a Signal to Salesloft. Signals must follow the structure defined on the Signal Registration."
  - `GET /v2/integrations/signals/registrations` and `/v2/integrations/signals/registrations/{id}` for registration lookups; Salesloft API Logs guide for usage/claiming integrations.
- **automations:** Fully automatic once emitted. Idempotency: "If we receive two signals with the same `idempotency_key` one of them will be dropped. The first one wins." `urgency` (high/medium/low) drives priority; `broadcast_notification` controls Live Feed display. Actionability is governed by the end user's Play configuration, not the sender.
- **features_tools:** Salesloft **Live Feed**; Signal Registrations; Play Registrations; Signal indicators rendered as human sentences (e.g. "`{video_name}` was viewed `{view_count, plural, =1 {# time} other {# times}} within 7 days").
- **extensibility:** This is explicitly the partner-extension surface: "Per integration, the signal type can only be registered once" and "Partners with a completed and tested signal will proceed through Salesloft's allowlisting process. Globally installed signals should be considered an immutable API contract with Salesloft and only additive changes will be allowed." Signals can also be used to render **email content** ("Sending Email Content using Signals").
- **sources:**
  - https://developers.salesloft.com/docs/platform/rhythm-resources/sending-signals/
  - https://developers.salesloft.com/docs/api/signals-create/
  - https://developer.salesloft.com/docs/api/signal-registrations/
- **evidence:**
  - "Salesloft defines a signal as a buyer-focused action or event that happened in the partner's or customer's platform. A signal should have high value and should drive a seller to act."
  - "A signal is made up of: a signal type, a signal name, data shape (metadata about the signal), descriptions, at least one indicator (Indicators have metadata and description), an attribution."
  - Good vs poor indicator: `{"key":"spent_more_than_30s_on_site","metadata":{"time_in_seconds":42}}` vs `{"key":"time_spent_on_site","metadata":{"time_in_seconds":30}}` — "Indicators should be very specific."
  - "Possible attribution choices include Person, Account, User, Opportunity and Email Content." / "Salesloft will use the attribution value to derive the appropriate Salesloft user to receive the signal."
  - "urgency ✅ String — A way for the application to define the urgency level. Accepted values are high, medium, and low."
  - "It is important to note that users may choose to not take action on a signal. Actionability depends on the end user's governance (Play) configurations and settings within Salesloft."

---

## 13. Turn a signal into an automatic seller action (Play registration)

- **name:** Turn a signal into an automatic seller action
- **user_flow:**
  1. After registering a signal (see #12), register a **Play**: `POST https://api.salesloft.com/v2/integrations/signals/registrations/plays` with `signal_registration_id`, localized `name`/`label`/`description`, the `indicators[]` that should trigger it, and `attributes` (`task_type`: `call` | `email` | add-to-cadence, `task_subject`, `task_reminder_hours`, `email_subject`, `email_template`).
  2. A seller (or admin) enables the Play in the UI: **Settings → Workflow → Plays → Edit Play**.
  3. When a matching signal arrives, Salesloft creates the task (call / email / cadence membership) and assigns it via the precedence order **User → Content → Person → Account**.
  4. Track outcomes via Salesloft webhooks (`task_created`, `task_completed`, `step_created`, `success_created`).
- **data_flow:** Registered signal fires → matches a Play's indicator list → Salesloft generates a one-off task → assignment resolved by precedence (falling back to "the most engaged Person on the Account in the Last 30 days (Highest Buyer Engagement Score)") → seller acts → task/step/success events stream out via webhooks.
- **data_sources:** Salesloft signal registrations, plays, `task_type` options, Person/Account/Opportunity/Email Content objects, Buyer Engagement Score, Salesloft Tasks/Steps/Successes.
- **apis_hit:**
  - `POST https://api.salesloft.com/v2/integrations/signals/registrations/plays` — register a Play framework (multiple plays allowed per signal registration).
  - Play framework endpoints: `POST /v2/integrations/signals/registrations/plays`, `.../plays/{id}` (update/destroy), and `.../plays` (index).
  - Downstream engagement webhooks: `GET/POST https://api.salesloft.com/v2/webhook_subscriptions`; event types include `task_created`, `task_updated`, `task_completed`, `step_created`, `step_updated`, `success_created`, `email_updated`, `conversation_created`, `conversation_recording_created`, `call_created`, `meeting_booked`, `link_swap`.
- **automations:** This workflow *is* the automation — signal → task with no human in the loop until the seller acts. It must be explicitly switched on in the UI after registration ("After registration, the registered Play must be enabled in the Salesloft UI"). Webhook delivery retries: "A failing webhook is retried three additional times, spaced 15 seconds apart, before being marked as failed."
- **features_tools:** Salesloft **Settings → Workflow → Plays → Edit Play**; task types **Call**, **Email**, **Add Person to a Cadence**; Tasks / Cadences; webhook subscriptions.
- **extensibility:** Vendors ship reusable "play frameworks" as "a pre-configured template made available to customers as a best practice (similar to cadence frameworks)"; the API supports multiple plays per signal registration, and localized `name`/`label`/`description`. Note the documented limitation: "At this time, Dynamic Fields are not supported outside of email templates. The only exception here is that `task_subject` supports `name`."
- **sources:**
  - https://developers.salesloft.com/docs/platform/rhythm-resources/registering-plays/
  - https://developers.salesloft.com/docs/platform/webhooks/event-types/
  - https://developer.salesloft.com/docs/api/integrations-signals-registrations-plays-create/
- **evidence:**
  - "A Play is an automation that generates a one-off action in response to an internal or external signal. The overall structure, or play framework is a pre-configured template made available to customers as a best practice (similar to cadence frameworks)."
  - "These are the available Play task types: Call, Email, Add Person to a Cadence."
  - "After registration, the registered Play must be enabled in the Salesloft UI. You can do so by going to Settings → Workflow → Plays → Edit Play."
  - "For task assignment, we will use an object precedence order of User, Content, Person, Account." and the assignment table row "The most engaged Person on the Account in the Last 30 days (Highest Buyer Engagement Score) / If there is no engagement, relate the task to the last person whose most recent contact was with the Account Owner".
  - "An application can create more than one framework per signal registration."
  - "Note: A failing webhook is retried three additional times, spaced 15 seconds apart, before being marked as failed."

---

## 14. Score DSR activity as CRM lead-score criteria

- **name:** Score DSR activity as CRM lead-score criteria
- **user_flow:**
  1. Confirm the Dock ↔ HubSpot integration is enabled and that the workspace is connected to a deal/account.
  2. In HubSpot: **Settings → Contact property**, search for **HubSpot score** (lead scoring is auto-created as a contact property named "HubSpot Score").
  3. Click **Add criteria** for a positive or negative score.
  4. Scroll to the **Dock** options in the lead-score system; choose the Dock property to score against.
  5. Set filters and assign a score value.
  6. Save; subsequent buyer activity in the DSR moves the contact's score automatically.
- **data_flow:** DSR engagement (Views, Clicks, Downloads, Interactions, MAP task activity) → synced to HubSpot as Dock activity properties on the contact → evaluated against lead-score criteria + filters (date, link name, file name, task name) → added/subtracted from the `HubSpot Score` contact property → rep prioritisation and lifecycle automation.
- **data_sources:** HubSpot contact property `HubSpot Score`; HubSpot lead-score criteria rules; Dock activity properties (analytics events + mutual-action-plan activity); Dock↔HubSpot integration.
- **apis_hit:** Documented as a HubSpot configuration surface. The underlying CRM primitives are HubSpot's: `PATCH /crm/v3/objects/contacts/{contactId}` to write a score-like contact property, `POST /crm/v3/objects/contacts/batch/upsert`, and `POST /crm/v4/associations/{fromObjectType}/{toObjectType}/labels` to read association type IDs. HubSpot scopes: `crm.objects.contacts.read`, `crm.objects.contacts.write`.
- **automations:** The scoring rule is continuous — every matching Dock activity event re-evaluates the score without user action. "auto-add will only add companies that enter your saved views after enabling the auto-add" is the analogous HubSpot behaviour for buyer intent.
- **features_tools:** HubSpot **Settings → Contact property → HubSpot score → Add criteria**; positive/negative score buckets; Dock filter options (Analytics events: Views, Clicks, Downloads, Interactions; MAP activity by task name); HubSpot workflows.
- **extensibility:** Third parties can add their own criteria by writing custom contact properties via `POST /crm/v3/properties` and feeding Dock webhooks into HubSpot workflows; the same Dock activity filters also drive HubSpot workflow enrollment (see #15).
- **sources:**
  - https://help.dock.us/en/articles/8507359-dock-activity-hubspot-lead-scoring
  - https://help.dock.us/en/articles/8507275-create-hubspot-workflows-based-on-dock-activity
  - https://developers.hubspot.com/docs/api-reference/crm-contacts-v3/guide
- **evidence:**
  - "Dock activity can be pulled in as lead scoring criteria within HubSpot." / "In HubSpot, lead scoring is automatically created as a contact property as 'HubSpot Score.'"
  - Setup path: "Within HubSpot, go to your settings. Go to the Contact property. Search for HubSpot score. Click Add criteria for either positive or negative scores; Scroll down until you see the Dock options in the lead score system. Choose the Dock property you want to score against. Setup filters and assign score."
  - "For activities like Clicks, Downloads, Interactions and Views, we recommend using the 'Occurred' filter as a baseline. From there, you can add more refinement around the link name or file name. Based on what they are interacting with, you might want to have higher or lower scores."
  - "For MAP activity, you can also refine by 'Occurred', but then also refine by task name to give certain tasks more weight than others."
  - HubSpot API: "When you include the `lifecyclestage` property, you can only set the value *forward* in the stage order." (constraint to design around)

---

## 15. Fire CRM workflows off DSR activity

- **name:** Fire CRM workflows off DSR activity
- **user_flow:**
  1. Verify the HubSpot integration is on and the workspace is connected to a deal/account.
  2. In HubSpot go to the **Workflows** page and create a Workflow.
  3. Choose **Contact based** (Dock only supports contact-based workflows "since the activities are tied to the contact record").
  4. Trigger: **When filter criteria is met** → search "Dock" or open the **Integration** section → select **Dock**.
  5. Pick from the five filter families: **Analytics events** (Views, Clicks, Downloads, Interactions) or **MAP activity**.
  6. Refine filters — Clicks/Interactions by date or link URL; Downloads by date and/or file name; Views by date; MAP activity by activity text such as `completed task 'Sign up for free account'`.
  7. Add actions: send emails, send Slack notifications, update HubSpot fields, change stages.
  8. Publish the workflow; DSR activity then drives it with no further setup.
- **data_flow:** DSR activity (view / click / download / interaction / mutual-action-plan task completion) → HubSpot's Dock integration filter evaluates criteria per contact → workflow enrollment → emails, Slack notifications, field writes, stage changes → buyer receives a contextual follow-up.
- **data_sources:** HubSpot contacts; Dock analytics-event properties and MAP activity; HubSpot workflows; Slack (via HubSpot workflow actions); workspace↔deal/account links.
- **apis_hit:** Configuration is in the HubSpot UI. The write side is HubSpot's CRM API (`PATCH /crm/v3/objects/contacts/{contactId}`, `POST /crm/v3/objects/contacts/batch/upsert`) and HubSpot workflow APIs. Source side: Dock `workspace.*` / `workspace.plan.task.*` webhooks and `GET /v1/workspace-plan-tasks`; Dock documents a dedicated pattern "Dock Webhooks + HubSpot Workflows".
- **automations:** The workflow is the automation; it fires continuously on matching activity. Nothing happens on the seller's screen. Dock's alternative path is to send the same events as webhooks into a HubSpot workflow webhook endpoint.
- **features_tools:** HubSpot **Workflows → Create Workflow → Contact based → When filter criteria is met → Integration: Dock**; HubSpot workflow actions (email, Slack notification, field update, stage change); Dock **Internal tab → MAP / plans**.
- **extensibility:** Vendors add new trigger families by extending the integration's filterable properties; the same criteria model is reused for lead scoring (#14), so one integration can power both. Custom Dock workspace fields are writable via `PATCH /v1/workspaces/{id}` and can drive "Dynamic workspaces: Show or hide specific workspace sections based on what a customer has done in your product".
- **sources:**
  - https://help.dock.us/en/articles/8507275-create-hubspot-workflows-based-on-dock-activity
  - https://help.dock.us/en/articles/8507359-dock-activity-hubspot-lead-scoring
  - https://www.dock.us/library/api-webhooks
  - https://developers.dock.us/llms.txt
- **evidence:**
  - "You can create HubSpot workflows based on Dock activity filters. There are a few examples for when this might be helpful: To trigger emails and/or slack notifications based on Dock activity. Change stages in HubSpot based on onboarding or mutual action plan tasks. Update HubSpot fields based on Dock activity."
  - "Dock only supports Contact based workflows since the activities are tied to the contact record."
  - "When you select Dock, you'll see five different options for your filter. You have the option to select from Analytics events (Views, Clicks, Downloads, or Interactions), or MAP activity (movement related to project plans in the workspace)."
  - "**Downloads:** filter by date and/or file name. **Views:** filter by date." / "Filter by activity text - e.g. 'completed task \"Sign up for free account\"' or 'completed task \"Intro call\"'."
  - "Once you setup your filters, you can use HubSpot's Workflows to send emails, slack notifications, update fields, change stages and more!"

---

## 16. Identify anonymous web visitors as companies and filter by pages visited

- **name:** Identify anonymous web visitors as companies and filter by pages visited
- **user_flow:**
  1. Install the Albacross tracking snippet on the site and note the **Client ID**.
  2. In Albacross, open the dashboard; the script maps traffic to companies automatically.
  3. Open **Pages** (Account name → top-right → **Pages**) and add the pages that signal intent, entering a name and the URL **path without the domain** (e.g. `/newsroom/converting-the-unconverted-article`).
  4. Choose the match condition: **Exact**, **Contains**, or **Starts with**.
  5. In the lead list, apply the **Pages** filter to isolate companies that visited those pages; combine with Segment filters, tags, and the ICP.
  6. Drill into a company to see its name, website, address, size, and associated employees/contacts.
- **data_flow:** Anonymous website request → tracking script reads IP address, country, network and other public parameters → matched against Albacross' company database → a **company-level** record is created (never an individual user) → page-visit events recorded per URL path → Pages/Segment/ICP filters → a ranked list of in-market companies with contact candidates.
- **data_sources:** Visitor IP/geo/network; Albacross' internal company database; defined intent pages; saved Segments/ICP; tagged leads.
- **apis_hit:** No public REST reference was reachable in the sources read; the documented surfaces are the dashboard, the tracking code install guides (WordPress, Drupal, Shopify, Wix, Squarespace, Adobe Launch, Joomla), CRM connectors (HubSpot, Salesforce, Attio, Pipedrive, Google Sheets), Zapier/n8n, Slack, Microsoft Teams, LinkedIn, and **Webhooks** (see #17). Albacross also runs an Outreach integration for auto-engagement.
- **automations:** Page-visit capture is continuous with no user action. Downstream automation is configured in **Workflows** (see #17) and in **Auto-engage** campaigns: define a target group by Role / Industry / Location, let "our AI" generate the sequence, pick a sequence template (e.g. "Effective Sales Funnel (LinkedIn + Email)"), adjust touchpoints and waiting periods, add personalization tags like `{{firstName}}` / `{{organizationName}}`, send a test, then **Start Campaign** and monitor in **Statistics**.
- **features_tools:** Albacross dashboard lead table; **Pages** filter; **Segments and Filters**; **ICP**; tags/batch tagging; **Auto-engage** campaign builder with sequence templates and AI message generation; **Statistics**.
- **extensibility:** Company-level identification is a deliberate privacy stance: "Albacross focuses on exclusively company-level identification rather than tracking individual users, ensuring respect for user privacy." Extensions: CRM syncs, Slack/Teams channel posting, and "Outreach" auto-engage — "Automatically adding prospects to Outreach campaigns".
- **sources:**
  - http://help.albacross.com/en/articles/1108676-how-do-you-identify-visitors-on-my-website
  - http://help.albacross.com/en/articles/1508553-filter-leads-based-on-the-web-pages-they-ve-visited
  - http://help.albacross.com/en/articles/10146587-creating-auto-engage-campaigns
  - http://help.albacross.com/en/collections/54059-intent-data
  - http://help.albacross.com/en/collections/1918043-integrations-connectors
- **evidence:**
  - "We identify the visitors by mapping different data points such as IP addresses, cookies etc. with company information. Additionally, Albacross focuses on exclusively company-level identification rather than tracking individual users."
  - "We check the IP address, the country, the network, and other publicly available parameters to stay GDPR compliant."
  - "The insights provided include the company's name, website, address, size, and a list of employees or contacts associated with the company."
  - Page filter: "Click your Account name on the top-right corner of the page then go to Pages. … Give the page a name and input the URL. You can now select this page when using the Pages filter." and "you can select which condition should be followed: Exact … Contains … Starts with". "When you type in the web page URL do not include the domain."
  - Auto-engage: "Specify your target group by selecting: Role, Industry, Location" / "After adding your content, our AI will automatically generate the sequence." / "Use personalization tags like `{{firstName}}` and `{{organizationName}}`".

---

## 17. Stream identified company/contact intent to your own systems

- **name:** Stream identified company/contact intent to your own systems
- **user_flow:**
  1. In Albacross, click **Workflows → New Workflow**.
  2. Choose **Webhooks** in the popup.
  3. Name the workflow and enter the destination URL that will accept the POST.
  4. Add conditions selecting which leads to send, based on **saved Segments**; choose whether to send a lead **only once** or to **send updates as well**.
  5. Choose the payload output: **Company only**, or **Company + Contacts** (optionally filtering contact fields by 'Keywords' and required fields).
  6. Save changes. Optionally supply the automatically generated token so the destination can verify traffic is from Albacross.
  7. Start receiving JSON at the destination; combine with a webhook workflow such as "Integrating with Microsoft Teams via Webhooks" or "Integrating with Google Sheets via Webhooks".
- **data_flow:** A company visit matches a saved Segment → Albacross evaluates the webhook workflow's conditions → builds a JSON payload (Company, or Company + filtered Contacts) → POSTs to your URL → your system enriches, scores, or routes the company. With "send updates" enabled, the same company is re-sent with refreshed activity data on subsequent visits.
- **data_sources:** Albacross company leads + activity data; saved Segments/ICP; contact data at the identified company; destination system (own API, third-party webhook consumer, Microsoft Teams, Google Sheets).
- **apis_hit:** Outbound `POST` to the configured destination URL (Albacross' "Webhooks" workflow type). Related documented integration surfaces: Zapier, n8n, Slack (private channel), Microsoft Teams, LinkedIn, Google Sheets, HubSpot/Salesforce/Attio/Pipedrive CRM syncs. No public inbound REST reference for Albacross was reachable in the sources read.
- **automations:** Trigger is the segment-matching company visit — no user action. Update mode: "If you choose to send updates as well, you will receive the same lead with updated activity data if that lead visits your webpage again." Security token is auto-generated and optional to enforce.
- **features_tools:** **Workflows → New Workflow → Webhooks**; saved **Segments**; a dedicated **What are Webhooks?** explainer; Slack/Teams/Sheets webhook recipes.
- **extensibility:** Deliberately open — the destination can be "a public API for a third party tool or a custom solution", and the payload composition (Company vs Company + Contacts, keyword-filtered) is chosen per workflow, so each downstream system gets exactly the fields it needs.
- **sources:**
  - http://help.albacross.com/en/articles/3116448-integrating-with-webhooks
  - http://help.albacross.com/en/articles/3306905-what-are-webhooks
  - http://help.albacross.com/en/articles/3387390-integrating-with-google-sheets-via-webhooks
  - http://help.albacross.com/en/articles/3368587-integrating-with-microsoft-teams-via-webhooks
  - http://help.albacross.com/en/collections/1918043-integrations-connectors
- **evidence:**
  - "This is a guide for setting up an Albacross Workflow in order to automatically export your leads via a Webhook to your own system, third party applications supporting Webhooks, or simply for storing them in a JSON format."
  - "In order to create an Albacross Workflow via Webhooks, you'll need a destination that can handle receiving the data object being sent via the webhooks. This can be a public API for a third party tool or a custom solution."
  - "When logged in to Albacross, click Workflows and select New Workflow. Choose Webhooks in the popup. Add a name for your Workflow and the URL you want to send data to."
  - "Add the conditions you want to be applied to the workflow to sort out which leads you want your Workflow to send based on saved Segments from your account. You will also have the option to choose if the workflow should only send a lead once or if it should send updates as well. If you choose to send updates as well, you will receive the same lead with updated activity data if that lead visits your webpage again."
  - "Choose the output of data you want to be sent to your URL, for example only Company for the company lead or Company + Contacts for the company lead and contacts employed at the company. If you choose to include Contacts, you have the option of filtering the contact details on 'Keywords' and required fields."
  - "As an option for security measures, you have a token to use in your service or tool to prove that traffic is coming from the Albacross platform. It is optional to specify this automatically generated token in your system to verify the Webhook."

---

## 18. Auto-add and continuously track in-market companies from intent signals

- **name:** Auto-add and continuously track in-market companies from intent signals
- **user_flow:**
  1. Prerequisites: set up **target markets** and **intent criteria**, set up **research topics**, and confirm the HubSpot tracking code is installed and firing (and the site domains are active).
  2. In HubSpot go to **More → Marketing > Buyer Intent**; open the **Visitors** tab (and the **Research** tab for topic research).
  3. In the left panel, filter companies: **Time frame** (last-visit based, up to 90 days, midnight-UTC based), **Showing visitor intent**, **Traffic source**, **Visitor country**, **Specific page views** (Path equal / not equal / contains / does not contain / starts with, plus Domain), **In my target markets**, **Filter by segment**, and **HubSpot CRM** (Lifecycle stage, Deal Stage, Owner).
  4. Sort by **Page views**, **Unique visitors**, or **Last visit** (asc/desc).
  5. Click **Save view** to persist the filter set as a named view.
  6. In the left sidebar under **Automations**, click **Add new companies** and/or **Track intent signals**, toggle them on, then **Save automation**.
  7. Monitor the **Overview** tab (companies showing research intent, visitor intent, converted-to-lifecycle-stage, Added Companies, Popular auto-adds) and optionally enable the four stock auto-add categories.
  8. Add the **Buyer Intent card** to company records (Settings → Objects → Companies → Record Customization → Add cards) for website visits, unique visitors, last seen, and top page views at the record level.
- **data_flow:** HubSpot tracking code collects website activity, IP addresses and other online identifiers → visits are matched to companies (anonymous → known company) and evaluated against intent criteria / target markets / research topics → company rows in the buyer-intent table (website visits, unique visitors, last visit, top page views) → saved views → auto-add and auto-enroll into CRM records, static segments, and HubSpot workflows → Buyer's **Record source** property set to `Buyer-Intent` → Buyer Intent card on the company record.
- **data_sources:** HubSpot tracking code; company IP-to-company matching; HubSpot target markets; intent criteria per page; research topics (off-site company research); company news signals ("funding, executive hires, layoffs, product launches, and mergers"); HubSpot company/deal/segment records; HubSpot Credits.
- **apis_hit:** Documented as HubSpot UI + tracking code. Related CRM API primitives for a custom build: `POST /crm/v3/objects/contacts/batch/upsert`, `PATCH /crm/v3/objects/contacts/{contactId}` (`lifecyclestage` is forward-only), `PUT /crm/v3/objects/contacts/{contactId}/associations/{toObjectType}/{toObjectId}/{associationTypeId}`, and `GET /crm/v4/associations/{fromObjectType}/{toObjectType}/labels` to resolve association type IDs. HubSpot scopes: `crm.objects.contacts.read` / `.write`. Note this article is UI-first; no public buyer-intent REST endpoint was documented in the page read.
- **automations:** Auto-add / track-intent toggles per saved view fire without user action, plus manual **Enroll in workflow** from the Visitor tab, plus 4 stock auto-add categories (net-new with visitor intent, net-new with research intent, in-CRM with visitor intent, net-new with both). Billing automation: "Tracking a company costs 10 credits. However, if a company is added and tracked in the same billing period, you're only charged once for tracking (10 credits) — not for both actions separately. After that initial charge, tracking continues to be charged monthly."
- **features_tools:** **Marketing > Buyer Intent** with **Visitors**, **Research**, **Overview**, **Saved views**, **Configuration/Exclusions** tabs; per-company **Top page views** / **Recent page views** / **About** / **Contacts** drill-down tabs; **Buyer Intent card** on the company record with **View full visit activity**; exclusions by domain.
- **extensibility:** Custom intent properties are derived automatically: "if you have SMB Intent as a criterion and a custom property called Showing SMB Intent, that property will automatically update to reflect whether an existing company meets or no longer meets the SMB Intent criteria." Gating and attribution rules: "When adding a company to your CRM from buyer intent, the company will have a `Record source` property value of `Buyer-Intent`" and "Buyer intent uses a hierarchical root-domain model and truncates 'www' for display purposes. This means that activity from subdomains is rolled up into the root domain."
- **sources:**
  - https://knowledge.hubspot.com/reports/use-buyer-intent
  - https://developers.hubspot.com/docs/api-reference/crm-contacts-v3/guide
- **evidence:**
  - "Buyer intent stores company-level website activity in a table, including details such as website visits, unique visitors, last visit, and top page views. It can also surface broader intent signals beyond your website, such as companies researching topics across the web or company news like funding, executive hires, layoffs, product launches, and mergers."
  - "the HubSpot tracking code installed on your website collects visitor data, such as website activity data, IP addresses, and other online identifiers. This data is used to monitor your website traffic and for website visits to be matched to companies."
  - "Buyer intent connects anonymous web visitors to known companies' IP addresses. Companies currently in your account will appear with a HubSpot icon."
  - Path filters: "Path is equal to / Path is not equal to / Path contains / Path does not contain / Path starts with"; timeframe "You can only set timeframes within the last 90 days." and "This timeframe is based on midnight UTC".
  - Drill-down: "If you're reviewing companies that have shown intent, the specific domain and page path that qualified the company will be tagged with Intent." / "To review the most recent page views from a company, including IP-derived country and date and time of the website visit, click the Recent page views tab." / Contacts tab: "you can also review the contact's last touch, last engagement, and any recently scheduled interactions such as planned meetings."
  - Automation: "In the left sidebar, under Automations, click **Add new companies** or **Track intent signals** to open the editing panel. … toggle the **Add new companies** and **Tracking intent signals** switches on. At the bottom, click **Save automation**."
  - "Please note: auto-add will only add companies that enter your saved views after enabling the auto-add. It will not add all existing companies in your saved views."
  - "Turn on auto-add for any of the following: Net-new companies with visitor intent: companies that are in your target markets and visiting high-intent pages, but aren't in your CRM yet. … Net-new companies with research and visitor intent".
  - Buyer Intent card fields: "Website visits: the count sessions of website visits from this company. Unique visitors … Last seen … Top page views: the pages with the most visits from visitors from this company."
  - Cost/permission gating: "To access buyer intent features like filtering by segments and excluding companies, you need HubSpot Credits." / "To add and enrich companies from buyer intent, Super Admin must assign users with Data enrichment permissions."

---

## Appendix A — Additional documented analytics surfaces found (not counted as separate workflows)

- **Per-rep attribution of asset views via tracked share links.** Dock: "Every internal user has a unique link to each asset. This allows you to easily see the performance of each link for each individual, but also see a rollup across the company." Core Analytics: "Views: the number of times an external person viewed the asset. Shares: the number of times an internal person shared the asset a new time (aka clicked Share)." API: `GET https://api.dock.us/v1/asset-share-link/...` — "Retrieve the trackable and non-trackable share links for an asset, attributed to a user. Creates the links if they do not exist yet." Webhook: `asset.viewed` payload carries `shareUrl` and `trackingEnabled` ("indicates whether it is gated"). Source: https://help.dock.us/en/articles/6989922-content-analytics , https://developers.dock.us/llms.txt , https://developers.dock.us/webhooks/event-types.md
- **At-risk implementation / time-to-value rollup.** Dock Implementations Report: "Total implementations, Active implementations, Completed implementations, Time to completion (average), % of implementations completed on time, Implementations started over time (graph), Implementations by owner, Customer engagement"; relies on workspace **Key Dates** (start / projected end / actual end) and on workspace type "Implementation". Source: https://help.dock.us/en/articles/9006640-implementations-report
- **Real-time Slack alerting on deal activity.** Dock → Settings → Company > Integrations → Slack → **Internal Notifications** tab → toggle notifications → choose channel; "This is a great way to get real-time updates & insights into the progress of all sales deals, customer onboarding and more" and "This integration will push notifications for ALL workspaces in your Dock account." Source: https://help.dock.us/en/articles/8175614-slack-internal-notifications
- **Liferay low-engagement / deadline alerts.** "Alerts for rooms with low engagement or approaching deadlines" on the Launchpad Analytics view. Source: https://learn.liferay.com/w/digital-sales-room/engagement-metrics-dashboard

## Appendix B — Gaps I could not fill from primary sources

1. **Predictive lead scoring inside a Digital Sales Room.** No vendor documents a *model-based* (probabilistic) score computed from DSR engagement. What exists is rule/criteria-based scoring (#14) and recency-bucket "trend" health (#6). Salesloft references a "Buyer Engagement Score" as a tie-breaker for task assignment but does not document how it is computed: https://developers.salesloft.com/docs/platform/rhythm-resources/registering-plays/
2. **An explicit engagement heatmap.** Dock ships *drop-off per page* and *time per page* for PDFs (a page-level heat/funnel for documents) and Liferay ships per-widget charts, but neither published doc describes a click/scroll heatmap over a room UI. Closest real capability: https://help.dock.us/en/articles/6989922-content-analytics
3. **A real-time DSR engagement dashboard API.** Seismic explicitly forbids interactive use of its engagement endpoints ("not designed to be used in high-frequency, interactive use cases", 24h refresh SLA). Liferay DSR has no documented public read API for the engagement widgets; it is backed by LDP only. Dock exposes events rather than read-your-own-aggregate endpoints.
4. **Seismic DSR content-level engagement tables.** I could only find `GET /reporting/v2/digitalSalesRooms` and `GET /reporting/v2/digitalSalesRoomsViewingSessions` (exact path: `/digitalSalesRoomViewingSessions`) in the public reference. DSR *per-document* / *per-link* / *per-page* engagement tables were not locatable — guessed operation IDs all returned 404, and the Seismic data dictionary is a JS app that could not be read. This is a real gap for a competitor-teardown of Seismic.
5. **Highspot, Showroom, DealHub, Dealfront/6sense, Common Room, Warmly, Getaccept, Vidyard, Gainsight PX.** Not usable as primary sources: `learn.showroom.sh` returned HTTP 526; `developers.dealhub.io` returned HTTP 429 on every attempt; `docs.6sense.com` failed TLS verification; `docs.commonroom.io` is a Gatsby app that loads page content from Sanity at runtime so no text is retrievable; `help.dealfront.com` and `docs.qualified.com` did not resolve; `developers.vidyard.com` did not resolve. No claims from these vendors are made anywhere in this file.
6. **Attribution modelling / multi-touch across rooms.** No source documented a multi-touch attribution model. Content-to-revenue association exists only as a per-asset breakdown (Dock Content & Sales Influence), not as a model.
7. **Consent / privacy plumbing for anonymous-to-known stitching inside a DSR.** Dock's webhook payload supports it ("Anonymous activity omits `user`") but the sources read do not document a consent gate, IP anonymisation, or a data-retention policy for DSR engagement events. Albacross documents GDPR-compatible company-level identification; HubSpot documents that "you may need to install a cookie consent banner on your own website". No DSR-specific consent UI was found.
