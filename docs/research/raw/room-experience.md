# DOMAIN 1 — room-experience: Digital Sales Room content and room experience

**Research method:** websearch + webfetch against vendor primary documentation only (official product docs, official API reference, official help centres). No vendor marketing pages were used as evidence; no feature was inferred.

**Vendors/doc sets read in full:**

| Vendor | Primary doc set | Why it is usable |
| --- | --- | --- |
| Liferay DXP 2026.Q3+ Digital Sales Room | `learn.liferay.com/w/digital-sales-room/*` — 6 pages, complete feature doc set with per-page screenshots, permission tables and lifecycle rules | The only vendor found shipping a *documented* DSR product with a management console, launchpad, room roles, room lifecycle states, fragment sets and an engagement dashboard |
| Seismic (Content Manager / Library / Reporting / Search) | `developer.seismic.com/seismicsoftware/reference/*` — OpenAPI definitions for Library Content Management, External Content Management, Library Workflow, Library Publishing, Content Search, Reporting v2 | Real public REST/OpenAPI reference with method, path, scopes, request/response schemas and documented side effects. Supplies the "content supply chain" side of a room |
| Qwilr (digital sales room / proposal pages) | `docs.qwilr.com` (public REST API reference + quick start + API concepts) and `help.qwilr.com` (product help centre) | Real public REST API reference (pages, blocks, taxes, payment gateways, webhooks, users) plus detailed help articles for lifecycle statuses, security, acceptance/e-signature, analytics, conditional content and white-labelling |

**Note on what is *not* covered by any primary source I could read:** see "Gaps" at the end of this file.

---

## 1. Create a Digital Sales Room from an account and template

- **name:** Create a room from an account and template
- **user_flow:**
  1. Open *Global Menu* → *Commerce* → *Digital Sales Room Management* (the Launchpad console).
  2. Click *Rooms* in the left sidebar.
  3. Click *New Digital Sales Room*.
  4. Wizard step 1: **select the Liferay account** to associate with the room. This account "determines the team members and contacts available to invite."
  5. Click *Next*.
  6. Wizard step 2: **select a template** ("Use the pre-configured DSR template to get started; you can customize the layout later").
  7. Click *Next*.
  8. Enter a **Name** for the room.
  9. (Optional) Enter a **Friendly URL**.
  10. Click *Save*. The room appears in the Rooms list.
- **data_flow:** Liferay Account (membership graph of internal team members + contacts) + DSR Template (a page-template/site-page definition) + operator input (name, friendly URL) → a **new DSR entry bound to a newly created Liferay site** (one site per room; the site is what later supplies the "Site ID" integration key) → appears in the Rooms list. The account binding is what makes invite-by-email work without a separate directory lookup.
- **data_sources:**
  - Liferay **Account** object (`learn.liferay.com/.../security-and-administration/users-and-permissions/accounts`)
  - Liferay **Site** (Liferay sites are the container; docs later expose a read-only `Site ID` on Room Settings)
  - DSR Template set shipped with the app
  - No external CRM, no file storage, no identity provider involved at creation time
- **apis_hit:** No public vendor API is documented for the Launchpad create-room action in the DSR doc set. What *is* documented is the read-side inventory API, which a third party would call after rooms exist:
  - `GET https://api.seismic.com/reporting/v2/digitalSalesRooms` — "Digital Sales Rooms … A list of all the Digital Sales Rooms that sellers have created." Returns `id`, `name`, `digitalSalesRoomTemplateId`, `digitalSalesRoomTemplateVersionId`, `createdBy`, `createdByUsername`, `createdAt`, `modifiedAt`, `userModifiedAt`. Query params: `limit`, `modifiedAtStartTime`, `modifiedAtEndTime`, `createdAtStartTime`, `createdAtEndTime`; `Accept: application/json | text/csv`. Auth: `JWTBearerToken` bearer in `Authorization` header.
  - Liferay's own extension seams for DSR modules are visible in the activation doc: `STARTED com.liferay.site.dsr.site.initializer_[version]`.
- **automations:** None for creation itself. Liferay's room *site initializer* runs automatically at module start (documented only as a console log line), not per room.
- **features_tools:** Launchpad sidebar (Home / Rooms / Analytics), *New Digital Sales Room* button, 3-step wizard (account → template → name/URL), Rooms list with search box and *Status* filter (*Active* / *Archived*, defaults to Active, one status at a time) and per-row *Actions* menu.
- **extensibility:** The room's **External Reference Code** is the documented third-party hook — see workflow 4. The room is a Liferay site, so site-scoped extensions (custom fragments, headless content APIs) apply. Template identity is a first-class ID pair (`digitalSalesRoomTemplateId` + `digitalSalesRoomTemplateVersionId`) so a template can be version-pinned.
- **sources:**
  - https://learn.liferay.com/w/digital-sales-room/dsr-launchpad
  - https://learn.liferay.com/w/digital-sales-room/managing-rooms
  - https://learn.liferay.com/w/digital-sales-room/activating-digital-sales-room
  - https://developer.seismic.com/seismicsoftware/reference/reporting-digitalsalesroomsget
- **evidence:**
  - "Open the *Global Menu* and navigate to *Commerce* → *Digital Sales Room Management*." / "Click *New Digital Sales Room*." / "Select the account to associate with the room." / "Select a template. Use the pre-configured DSR template to get started; you can customize the layout later." / "Enter a name for the room." / "(Optional) Enter a *Friendly URL*." / "Click *Save*."
  - "The selected account determines the team members and contacts available to invite. If you invite a user who is not a member of the account, they join it automatically upon accepting the invitation."
  - Seismic DSR schema fields: `"digitalSalesRoomTemplateId": "4225d5c4-2df2-a622-2686-e82dc8b05745", "digitalSalesRoomTemplateVersionId": "6ee19b7a-32ce-42ab-23ff-ea5969855c3a"`.

---

## 2. Build the room's buyer-facing pages from DSR fragments

- **name:** Build room pages from DSR fragments
- **user_flow:**
  1. In the Rooms list, click a room's *Actions* → *Edit*. This "Opens the room with its pages in edit mode."
  2. In the editing sidebar select *Components* → *Fragments*, then open a fragment set.
  3. **Drag a fragment onto the page.**
  4. Select the fragment on the page and set its fields in the **configuration panel**.
  5. Click **Publish**. "The fragment appears on the page the next time a member opens the room."
  Requires the *Room Collaborator* role or equivalent permissions.
- **data_flow:** Fragment set definition → instance placed on a Liferay page in the room's site → configured with variable-ish fields (Document 1–4 selectors pointing at the room's documents folder; Video `URL` + `Width`/`Height`; `Number of Steps` + `Current Step`; `Number of Items`) → **published page revision inside the room's site**, rendered to buyers on next room load.
- **data_sources:**
  - The room's own **documents folder** (Document Gallery Block's "Document 1 through Document 4" fields "take one file from the room's documents, the same files listed in the room's Documents view")
  - Liferay page / page-version store (Liferay CMS)
  - Three shipped fragment sets: **Digital Sales Room** (11 fragments), **Digital Sales Room Analytics** (10 fragments), **DSR Fragments** (console chrome)
- **apis_hit:** No public HTTP API is documented for fragment editing. Liferay's own documented extension surface is fragment libraries + Liferay's headless/GraphQL content APIs (not enumerated on the DSR pages I read). The read-side equivalent for external systems is Seismic's `GET https://api.seismic.com/reporting/v2/digitalSalesRooms`, which returns the template + template-version IDs a page was built from.
- **automations:** None. Publishing is explicitly user-initiated ("Click *Publish*").
- **features_tools:**
  - Liferay page editor, *Components* → *Fragments* panel, drag-and-drop, per-fragment configuration panel
  - **Digital Sales Room set (11):** Document Gallery Block, Gallery Block, Header Main, Header User, Our Team Block, PDF Preview Block, Question and Answer Block, Text Block, Timeline Block, Video Block, Welcome Block
  - **Digital Sales Room Analytics set (10):** Activity Log, Documents Statistics, Engagement Chart, Frequency Chart, Latest Activity, Most Active Visitors, Navigation, Room General, Room Statistics, Room Trend
  - **DSR Fragments set (4):** Page Bar, Sidebar, Sidebar Trigger, Vertical Navigation
- **extensibility:** Fragment sets are the primary extension mechanism — a third party adds its own fragment set. Three of the console fragments ship explicit accessibility hooks that are meant to be edited: Page Bar's *Header Image Alt Description*, Sidebar's *Sidebar ARIA Label*, Vertical Navigation's *ARIA Label*. Notes on documented limits: "Document Gallery Block has a fixed set of four document selectors instead of the *Number of Items* field. To show more than four documents on a page, add another Document Gallery Block for each additional set of four." "Video Block … Under *Video Options*, URL points to the video and Width and Height set the player's dimensions. Autoplay is off by default." "PDF Preview Block … The editable link that selects the PDF appears only in the page editor." "Header Main … It also renders the notice shown when the room is archived."
- **sources:**
  - https://learn.liferay.com/w/digital-sales-room/digital-sales-room-fragments
  - https://learn.liferay.com/w/digital-sales-room/managing-rooms
  - https://learn.liferay.com/w/digital-sales-room/managing-room-documents
- **evidence:**
  - "Build a room's page by dragging fragments onto it in the page editor." … "Drag a fragment onto the page." … "Select the fragment on the page, then set its fields in the configuration panel." … "Click *Publish*. The fragment appears on the page the next time a member opens the room."
  - "Editing a room's pages requires the Room Collaborator role or equivalent permissions."
  - "Timeline Block — Displays a numbered sequence of steps, each with a secondary line for an estimate. *Number of Steps* sets how many steps appear (default four), and *Current Step* marks how far the deal has progressed." (this is the closest documented analogue to a mutual action plan in a room page)
  - "Digital Sales Room ships three out-of-the-box fragment sets, and you use each one for a different purpose."

---

## 3. Populate and govern the room's document library

- **name:** Populate the room document library
- **user_flow:**
  1. In the Rooms list, click a room's *Actions* → *View*.
  2. Click *Documents* in the room's **navigation menu**. The view "lists the files in the room's documents folder."
  3. Click **New** (only rendered for Room Collaborators and Content Contributors) to add a file.
  4. Read per-document row metadata: thumbnail, "who last modified it", and its **workflow status**.
  5. *(optional)* Surface selected documents on a room page by adding a **Document Gallery Block** fragment and picking Document 1–4.
- **data_flow:** A file uploaded into the room's documents folder → Liferay DAM document entry scoped to the room → row rendered in the Documents view with thumbnail + last-modifier + workflow status → optionally referenced by a Document Gallery Block on a room page → opened "in a new tab" by the buyer. Role gates whether the write path is reachable at all.
- **data_sources:**
  - Liferay **DAM** (document entries + the room-scoped documents folder)
  - Liferay **Accounts / roles** (drives the New button)
  - Room **site** + **page** store (for the gallery block)
- **apis_hit:** Liferay's own Headless Delivery / DAM APIs are the seam (not enumerated on the DSR pages). On the supply side, the concrete documented ingestion API that fills a sales content library feeding rooms is Seismic:
  - `POST https://api.seismic.com/integration/v2/teamsites/{teamsiteId}/files` — Add a file. `multipart/form-data` with a `metadata` JSON part (required `name`, `parentFolderId`, `format`; optional `ownerId`, `description`, `expiresAt`, `externalId`, `externalConnectionId`, `experts[]`, `properties[]`) and a `content` binary part. Query params `resolveNameCollision` (default `true`) and `rollbackOnError` (default `false`). Max 2 GB. Scope `seismic.library.manage`. Returns `id`, `versionId`, `version`, `status: "Draft"`, `libraryMaterializedPath`, `thumbnailUrl`, `expiresAt`, `externalId`.
  - `GET https://api.seismic.com/integration/v2/teamsites/{teamsiteId}/files/{libraryContentId}` — implied by the workflow-approval response note: "Use this to call GET /integration/v2/teamsites/{teamsiteId}/files/{libraryContentId} for full file metadata."
- **automations:**
  - Seismic **thumbnail rendering is asynchronous**: "thumbnail rendering is asynchronous and may lag the upload by several seconds… Empty when no thumbnail has been generated yet."
  - Seismic **content expiry** is a stored date, not a job: `expiresAt` "After this date the file is treated as expired in consumer surfaces. Must be a future date when set."
- **features_tools:** Documents view, *New* button, search, Document Gallery Block fragment, workflow-status column, archived-room read-only behaviour.
- **extensibility:** Per-room folder isolation + a role gate is the model. Documented constraint worth copying: "neither can delete a document someone else uploaded" (i.e. append-only for non-admins), and "Images added through other fragments are stored outside the room's documents folder and don't appear in the Documents view" — one canonical library, deliberately not a catch-all.
- **sources:**
  - https://learn.liferay.com/w/digital-sales-room/managing-room-documents
  - https://learn.liferay.com/w/digital-sales-room/digital-sales-room-fragments
  - https://developer.seismic.com/seismicsoftware/reference/seismiclibrarycontentmanagementaddafile
  - https://developer.seismic.com/seismicsoftware/reference/seismiclibraryworkflowgetlistofapprovalworkflows
- **evidence:**
  - "Every *Digital Sales Room* has a Documents view for the files shared on a deal, such as proposals, contracts, and case studies. Buyers and colleagues reach the view from the room's navigation menu."
  - "The view lists the files in the room's documents folder. When that folder is empty, the view reads 'There are no documents or media files in this folder.'"
  - "Each document shows a thumbnail, who last modified it, and its workflow status."
  - "The *New* button appears only for Room Collaborators and Content Contributors, and neither can delete a document someone else uploaded."
  - "In an archived room, only instance administrators see the *New* button."
  - Seismic: "Use the special keyword `root` as `parentFolderId` to add files to the root folder of a teamsite." / "You can also optionally include an `externalId` as a well-known and non-editable way of tracking the content ID from an external system." / "Upload Limits — Max file size is limited to 2 GB."

---

## 4. Invite buyers to a room with a role and an access expiry

- **name:** Invite buyers with role and expiry
- **user_flow:**
  1. Rooms list → room's *Actions* → *Share* (or the **Share** button in the room header while viewing it).
  2. In **Email Addresses**, type an address and press Enter or type a comma. Repeat per invitee.
  3. (Optional) Choose the **role** for everyone in this invitation. Default is *Viewer*.
  4. (Optional) Set **Access Valid Until**.
  5. Click **Invite**. A confirmation message appears; each invitee receives an email invitation.
  6. Later, in the **Who Has Access** list: *Edit* to change a date, the role drop-down to change a role, the trash icon to remove someone.
- **data_flow:** Email address + role + expiry → (a) an email invitation record with a **48-hour acceptance window** → on acceptance, an entry in the room's Liferay **account** → (b) a row in **Who Has Access** carrying `{role, accessValidUntil}` → at expiry "Access ends at the end of the expiration date in UTC. The person then no longer appears in the Who Has Access list."
- **data_sources:**
  - Liferay **Account** (invitees join the room's account)
  - Liferay **User** records (invitees who already have a Liferay account join immediately on send; those without join on accept)
  - Liferay **Role** (three room roles)
  - SMTP/email delivery for the invitation
  - No CRM, no external IdP
- **apis_hit:** No public vendor API documented for the Share dialog. Liferay's documented roles/permissions primitives apply (the docs cross-link to `.../security-and-administration/users-and-permissions/accounts`).
- **automations:**
  - **Invitation expiry:** "An invitation expires 48 hours after you send it. Send a new invitation if the recipient doesn't accept in time."
  - **Account join on accept:** "An invitee who already has a Liferay account joins the room's account as soon as you send the invitation. An invitee without a Liferay account joins the room's account when they accept the email invitation."
  - **Imminent-expiry alerting:** "When someone's access expires within seven days, their row shows a warning label and the dialog displays a banner, for example *2 users have access expiring within 7 days.* Both appear in the Share dialog, so they reach only the members who can share the room. Viewers have no *Share* button."
  - **Silent UTC cut-off:** "Access ends at the end of the expiration date in UTC."
- **features_tools:** Share dialog (Email Addresses field, role button, Access Valid Until date picker, Invite button), Who Has Access list (Edit / role drop-down / trash icon), expiring-soon orange warning labels + banner, *View* only when permission is View-only.
- **extensibility:** The permission→action matrix is the extension contract. Documented: Room Roles are *Room Collaborator* ("Users can manage pages and documents, add room comments, and share the room."), *Content Contributor* ("Users can view room content, add comments, upload documents, and share the room."), *Viewer* ("Users can view documents and add comments, but cannot upload documents."). Delegation rule: "Room Collaborators and Content Contributors can assign the Content Contributor and Viewer roles. Only the room site’s administrator or owner can assign the Room Collaborator role." Room action/permission table (Archive=Update, Delete=Delete, Duplicate=Update + create permission, Edit=Update, Restore=Update, Room Settings=Update, Share=Update, View=View) is a usable RBAC spec. Documented safety gaps to note: "Neither action asks for confirmation" for role change/removal, and "The Owner can't be changed."
- **sources:**
  - https://learn.liferay.com/w/digital-sales-room/managing-rooms
  - https://learn.liferay.com/w/digital-sales-room/managing-room-documents
  - https://learn.liferay.com/w/digital-sales-room/dsr-launchpad
- **evidence:**
  - "Type an address in *Email Addresses* and press Enter or type a comma. Repeat for each person you're inviting." / "(Optional) Choose the role for everyone in this invitation. The default role is Viewer." / "(Optional) Set *Access Valid Until* for everyone in this invitation." / "Click *Invite*."
  - "An invitation expires 48 hours after you send it. Send a new invitation if the recipient doesn't accept in time."
  - "One role and one expiration date apply to the whole invitation. To give people different roles or dates, send separate invitations, or change them afterward in the Who Has Access list."
  - "When someone’s access expires within seven days, their row shows a warning label and the dialog displays a banner, for example *2 users have access expiring within 7 days.*"
  - "Access ends at the end of the expiration date in UTC. The person then no longer appears in the Who Has Access list."
  - "When you invite someone without an expiration date, their row reads *No Expiration*, and their access ends only when you remove them, archive the room, or delete it."

---

## 5. Archive and restore a room

- **name:** Archive and restore a room
- **user_flow:**
  1. Rooms list → *Status* filter set to *Archived* to find archived rooms (the filter "defaults to Active, so archived rooms stay hidden until you switch it"). Archived rooms "stay in this list instead of moving to a separate view."
  2. On an **Active** room, click *Actions* → *Archive*.
  3. Confirm in the dialog: "Are you sure you want to archive this digital sales room? It will no longer be available to customers, but you can restore it later."
  4. Observe the buyer-facing change: an informational notice renders at the top of the room.
  5. To reopen: set *Status* filter to *Archived*, click *Actions* → *Restore*. The room returns to *Active* and leaves the Archived list.
  6. Hard-delete alternative: *Actions* → *Delete* (Active or Archived; requires Delete permission) — "Deletion is permanent: it removes the room’s documents and revokes every member’s access."
- **data_flow:** Room record status `Active → Archived` → a cascade of enforced changes: (a) access narrowed to instance admins + the site owner, other invited members "are redirected away from it"; (b) Share dialog becomes read-only for everyone "instance administrators included"; (c) document upload closes — "Uploading documents is closed. The *New* button no longer appears in the Documents view, except for instance administrators"; (d) a notice string is rendered in the room header ("This digital sales room is archived. New comments cannot be added, and it can no longer be shared."). `Archived → Active` reverses all of it.
- **data_sources:**
  - DSR room record (`status`: Active | Archived)
  - Liferay **Site** ACLs (the redirect + access narrowing)
  - Room **documents folder** (preserved, not deleted)
  - Liferay **users/groups/roles** (who can still see an archived room)
- **apis_hit:** No public vendor API for archive/restore. The closest documented equivalent lifecycle filter is Seismic's read API, which is time-windowed rather than state-enumerated:
  - `GET https://api.seismic.com/reporting/v2/digitalSalesRooms?createdAtStartTime=…&createdAtEndTime=…` — windowing by `createdAt` / `modifiedAt` rather than by status.
  - Room status as a first-class filterable concept is instead documented in Qwilr: `GET https://api.qwilr.com/v1/pages?status=draft,live,accepting,accepted,disabled,declined` and `GET https://api.qwilr.com/v1/pages?tags=archived`.
- **automations:**
  - Rendered buyer notice: "An informational notice at the top of the room reads, 'This digital sales room is archived. New comments cannot be added, and it can no longer be shared.'" (state-driven, fires on read)
  - Comments close on archive.
  - Separately documented, same "expired vs. open" idea at the *product* level: Liferay's DSR **license expiry** deactivates the console but deliberately leaves buyer rooms live — "Digital Sales Room Management is deactivated as soon as the license expires. Your existing rooms and their sites remain in place and the people you shared them with keep their access, but Digital Sales Room Management disappears from the Global Menu and you can't create new rooms. The Launchpad's pages remain published, so a bookmark or direct link still opens them." Plus a countdown: "Within 30 days of your DSR license's expiration date, a warning alert appears at the top of every DSR Launchpad page: Home, Rooms, and Analytics."
- **features_tools:** Rooms list *Status* filter (Active/Archived, one at a time, chip shown above the table), *Actions* → Archive/Restore/Delete, archived-room notice banner, read-only Share dialog, per-role *New* button suppression.
- **extensibility:** Documented permission requirements make archive/restore safe to delegate: Archive needs **Update** and only applies to *Active*; Restore needs **Update** and only applies to *Archived*; "With *View* permission alone, you see only *View*. Only instance administrators and the room’s site’s owner can view an archived room." Archive is explicitly the reversible counterpart to Delete: "To close a room to buyers while keeping its content, archive it instead."
- **sources:**
  - https://learn.liferay.com/w/digital-sales-room/managing-rooms
  - https://learn.liferay.com/w/digital-sales-room/digital-sales-room-fragments
  - https://learn.liferay.com/w/digital-sales-room/activating-digital-sales-room
  - https://docs.qwilr.com/api-reference
- **evidence:**
  - "Archive | Closes the room to buyers while keeping its content. | Active | Update" and "Restore | Returns an archived room to the Active status. | Archived | Update" and "Delete | Removes the room. | Active or Archived | Delete".
  - "Archiving changes the room in these ways: An informational notice at the top of the room reads, 'This digital sales room is archived. New comments cannot be added, and it can no longer be shared.' … Access narrows to instance administrators and the site’s owner. Other invited members who visit the room are redirected away from it. … The *Share* dialog becomes read-only for everyone, instance administrators included. … Uploading documents is closed."
  - "The room keeps its place in the Rooms list under the *Archived* status. To see it, set the *Status* filter to *Archived*."
  - Header Main fragment: "It also renders the notice shown when the room is archived."

---

## 6. Review buyer engagement and prioritise follow-up

- **name:** Review buyer engagement and prioritise follow-up
- **user_flow:**
  1. Open *Global Menu* → *Commerce* → *Digital Sales Room Management*.
  2. Click **Analytics** in the left sidebar. Aggregate view: "Total active deals", "Recent buyer activity and engaged documents", "Alerts for rooms with low engagement or approaching deadlines."
  3. Use the **room drop-down at the top right** to scope the dashboard to one deal (default is "All Rooms").
  4. Click a deal card or metric to **drill down** into that room's per-room engagement view.
  5. Read the widgets: Room Stats, Most Active Visitors, Most Engaged Documents, Latest Activity, Recent Engagement chart, Visit Frequency chart, Room Trend.
  6. Coordinate internally via the room's **Timeline** tab: "a chronological log of updates to ensure your team remains aligned on deal developments."
- **data_flow:** Buyer interactions in the room (document views, downloads, comments, page visits) → Liferay instrumentation → **Liferay Data Platform (LDP)** metrics (the instance is "connected to a LDP environment" via an environment token in *Control Panel* → *Instance Settings* → *Analytics Cloud*) → aggregated widgets and per-room widgets → alerts for low engagement / approaching deadlines. LDP is a hard prerequisite: "Every metric on this page requires that connection."
- **data_sources:**
  - **Liferay Data Platform (LDP)** — the metrics store; connected by an *environment token* pasted from the Marketplace product page's *Environment* tab
  - Liferay **Instance Settings → Analytics Cloud** token field
  - Room document store (to attribute per-document engagement: "Total Views, Last Viewed date, Downloads, Average Time, and Users Involved")
  - Liferay **User** records (visitor identity for Most Active Visitors / Latest Activity)
  - Room **activity log** (comment + page events)
- **apis_hit:** LDP's query surface is not published on the pages I read. The documented consumer of the same room-engagement data in a Seismic tenant is the reporting API:
  - `GET https://api.seismic.com/reporting/v2/digitalSalesRooms` — room inventory with `modifiedAt` / `userModifiedAt` for change detection.
  - `GET https://api.seismic.com/reporting/v2/libraryContents` — "a list of content in the Content Manager Library"; fields include `isPublished`, `isAvailableInProfile`, `contentStatus`, `publishedAt`, `publishedByUserId`, `publishedVersionExpiresAt`, `latestLibraryContentVersionId`, `libraryPath`, `externalSystemConnectionName`; windowed by `modifiedAtStartTime` / `modifiedAtEndTime` / `lastModifiedStartTime` / `lastModifiedEndTime` / `createdAtStartTime` / `createdAtEndTime`; `Accept: application/json | text/csv`.
  - `POST https://api.seismic.com/search/v1/content/query` — content discovery with `X-Seismic-Client-Details` header so that "the search activity data can be available to customers for reporting and insight analytics purposes."
  - Auth for all: `JWTBearerToken` bearer in `Authorization`. Rate limits are published per-operation (e.g. `x-seismic-rate-limit: 2` on Content Search, `1` on Add external content).
- **automations:**
  - **Low-engagement / deadline alerts** in the Analytics view: "Alerts for rooms with low engagement or approaching deadlines."
  - **Room Trend health classification** is computed, not manual: "*Room Trend:* Indicates the room's engagement health as *Cold*, *Warm*, or *Hot*."
  - **LDP is itself a scheduled/async pipeline**, and the platform publishes its own refresh contract for reporting consumers: "The data that is available through our reporting APIs is updated **no less than every 24 hours**."
  - **Reporting API is explicitly not interactive:** "The reporting APIs are built with data extraction (ETL) in mind. They are intended to allow large portions of data to be extracted to an external database/data lake/data warehouse. These APIs are not designed to be used in high-frequency, interactive use cases."
- **features_tools:** Launchpad **Analytics** view; room drop-down scope selector; per-room widgets **Room Stats** ("View Time Viewed (e.g., 5h 32 min), Total Visits, Visitors, and Actions (document views, downloads, and comments)"), **Most Active Visitors** ("Lists individuals ranked by their total actions"), **Most Engaged Documents**, **Latest Activity** ("a live feed of the most recent buyer actions, showing the user, the action they took, and when it occurred"), **Recent Engagement**, **Visit Frequency** ("Charts how often the room is visited by day or week"), **Room Trend**; the 10-fragment **Digital Sales Room Analytics** set for custom analytics pages; the room **Timeline** tab; the *Analytics Cloud* environment token field.
- **extensibility:** The Analytics fragment set is explicitly the extension point: "Use these ten fragments to build a custom view of a room's engagement data. The Analytics pages in the Digital Sales Room Launchpad use the same set." Third parties can build their own analytics pages from Activity Log / Documents Statistics / Engagement Chart / Frequency Chart / Latest Activity / Most Active Visitors / Navigation / Room General / Room Statistics / Room Trend. Programmatic consumption pattern is `modifiedAt` windowing plus a `continuationToken`-free `limit` walk.
- **sources:**
  - https://learn.liferay.com/w/digital-sales-room/engagement-metrics-dashboard
  - https://learn.liferay.com/w/digital-sales-room/digital-sales-room-fragments
  - https://learn.liferay.com/w/digital-sales-room/activating-digital-sales-room
  - https://learn.liferay.com/w/digital-sales-room/index
  - https://developer.seismic.com/seismicsoftware/reference/h1-reporting-api-overview
  - https://developer.seismic.com/seismicsoftware/reference/reporting-librarycontentsget
  - https://developer.seismic.com/seismicsoftware/reference/reporting-digitalsalesroomsget
  - https://developer.seismic.com/seismicsoftware/reference/contentsearch
- **evidence:**
  - "DSR reports engagement using Liferay Data Platform (LDP) metrics, so your instance must be connected to an LDP environment. Every metric on this page requires that connection."
  - "Set the environment token, if your instance does not already have one. … Check *Global Menu* → *Control Panel* → *Instance Settings* → *Analytics Cloud* first. … If the token field already holds a value, your instance is connected."
  - "The Analytics view displays consolidated metrics across your pipeline, including: Total active deals. Recent buyer activity and engaged documents. Alerts for rooms with low engagement or approaching deadlines."
  - "By default, the dashboard displays data for All Rooms. To view metrics for a specific deal, use the drop-down menu at the top right to select a room."
  - "*Most Engaged Documents:* View detailed tracking for each shared asset, including Total Views, Last Viewed date, Downloads, Average Time, and Users Involved."
  - "*Room Trend:* Indicates the room's engagement health as *Cold*, *Warm*, or *Hot*."
  - "Use the Timeline tab to access a chronological log of updates to ensure your team remains aligned on deal developments."
  - Seismic: "This is a data modifiedAt time and has no 'business' meaning. It is meant entirely for machines to know what rows may have changed so that it can pull the updates and merge them into existing data sets."

---

## 7. Ingest a document or deck into the content library

- **name:** Ingest a document into the content library
- **user_flow:**
  1. Acquire a Seismic JWT bearer token in the `Authorization` header with the `seismic.library.manage` scope.
  2. `POST https://api.seismic.com/integration/v2/teamsites/{teamsiteId}/files` as `multipart/form-data`.
  3. Send a `metadata` JSON string part with `name`, `parentFolderId` (`root` or a folder GUID) and `format`; optionally `description`, `ownerId`, `expiresAt`, `externalId`, `experts[]`, `properties[]`.
  4. Send the `content` binary part (≤ 2 GB).
  5. Optionally set `resolveNameCollision=true` to auto-suffix `(1)`, `(2)`… and `rollbackOnError=true` for transactional safety.
  6. Read the 201 response; persist `id` and `versionId` for follow-up publish/fetch/update calls. Thumbnail arrives later via `thumbnailUrl`.
- **data_flow:** Binary file + metadata → Seismic Library Content Management service → creates a Library content item (`type: "file"`, `repository: "library"`, `status: "Draft"`, `version: "0.1"`) inside the target teamsite folder, with a generated `libraryMaterializedPath` such as `/Sales Enablement/Q2 Decks/Q2 Sales Deck.pptx` → thumbnail generated asynchronously → `thumbnailUrl` becomes non-empty. `externalId` lets an external system (e.g. a CRM deal) correlate the file.
- **data_sources:**
  - Seismic **teamsite** (tenant-scoped Library container; `1` = default teamsite or a UUID)
  - Seismic **Library folder hierarchy** (`parentFolderId`)
  - Seismic **Users** (`ownerId`, `createdBy`/`modifiedBy` embedded user objects with `fullName`, `email`, `photoUrl`, `isDeleted`)
  - Seismic **custom properties** (tenant-defined metadata: audience, region, campaign tags)
  - Seismic **groups** (as `experts` of `type: "group"`)
  - Origin: local filesystem / caller-supplied binary, or an external cloud connection
- **apis_hit:**
  - `POST https://api.seismic.com/integration/v2/teamsites/{teamsiteId}/files` — Add a file. `201 Created`; `Location` header set to the new file resource. Scope `seismic.library.manage`. Errors: 400 (malformed `teamsiteId`, missing/invalid metadata), 401, 403 ("Insufficient permissions to add a file"), 404, 500, 504.
  - `GET https://api.seismic.com/integration/v2/teamsites/{teamsiteId}/files/{libraryContentId}` — Get file information/properties (referenced explicitly from the approval-workflow schema).
  - `PUT` on the same resource — Add a new file version (referenced as the correct alternative: "Do not call to replace the binary of an existing file — use the add-new-version endpoint instead").
  - `POST https://api.seismic.com/cm/v1/teamSites/{teamSiteId}/external/contents` — Add external file to Library (see workflow 8).
- **automations:**
  - **Async thumbnail rendering** — "thumbnail rendering is asynchronous and may lag the upload by several seconds."
  - **Optional automatic name de-collision** — `resolveNameCollision=true` "automatically appends an index such as (1), (2), (3), etc. until a name is found that does not conflict with an existing document in the folder."
  - **Optional transactional rollback** — `rollbackOnError=true` "automatically deletes any partially-created Library file record if the binary content upload fails after the metadata record was created, preventing orphaned draft entries in the target folder." Documented as: "Agents performing unattended ingestion should set this to `true` for transactional safety."
  - **Sticky expiry** — `expiresAt` "After this date the file is treated as expired in consumer surfaces."
- **features_tools:** Teamsite Library / Content Manager UI is the human surface; the API is the machine surface. Returned metadata powers UI: `thumbnailUrl`, `size`, `language` ("BCP-47 language tag… used by search and personalization features"), `assignedToProfiles` ("Newly uploaded files have no profile assignments yet"), `libraryMaterializedPath`, `version` / `majorVersion.minorVersion` version policy.
- **extensibility:** `externalId` is the documented external-correlation field ("used to correlate this file with a record in an external system… Provide a value when the file was created in response to an external trigger"). `externalConnectionId` links the item to a named external content connection ("such as SharePoint or Google Drive"). `properties[]` lets a tenant require its own metadata on ingest: "the `id`… Must reference an existing custom property configured on the target teamsite, otherwise the upload is rejected with a 400 response." `experts[]` supports `type: "user" | "group"`.
- **sources:**
  - https://developer.seismic.com/seismicsoftware/reference/seismiclibrarycontentmanagementaddafile
  - https://developer.seismic.com/seismicsoftware/reference/introduction-overview
  - https://developer.seismic.com/seismicsoftware/reference/reporting-librarycontentsget
- **evidence:**
  - "Uploads a new file to the Library in the specified teamsite using a `multipart/form-data` POST containing the file metadata and binary content."
  - "The `metadata` part is a JSON string with required properties `name`, `format`, and `parentFolderId`."
  - "Use the special keyword `root` as `parentFolderId` to add files to the root folder of a teamsite."
  - "You can also optionally include an `externalId` as a well-known and non-editable way of tracking the content ID from an external system."
  - "📘 Upload Limits — Max file size is limited to 2 GB."
  - Response example: `"status": "Draft"`, `"version": "0.1"`, `"libraryMaterializedPath": "/Sales Enablement/Q2 Decks/Q2 Sales Deck.pptx"`, `"thumbnailUrl": ""`, `"expiresAt": "2026-12-31"`, `"externalId": "ext-12345"`.
  - "Agents performing unattended ingestion should set this to `true` for transactional safety."

---

## 8. Auto-sync an external cloud file into the content library

- **name:** Auto-sync an external cloud file
- **user_flow:**
  1. Prerequisites: "The caller must have a valid Google Drive connection configured in Seismic." (404 `ExternalConnectionNotFoundApiException` otherwise.)
  2. `POST https://api.seismic.com/cm/v1/teamSites/{teamSiteId}/external/contents` with `externalSource: "GoogleDrive"`, `externalContentId: "<Google Drive file ID>"`, `parentFolderId` (GUID or `root`), `autoSync: true|false`.
  3. Read the 200 response and persist the returned Seismic `contentId`.
  4. (documented follow-up) Query sync status via the referenced `GetContentSyncStatus` operation using that `contentId`.
- **data_flow:** Google Drive file ID → Seismic resolves it *through the caller's own Drive connection* → creates a **linked** Library content item (not a copy) placed in the target Seismic folder → with `autoSync: true` the item "will automatically re-sync whenever the linked source file is updated in the external system" → with `false`/omitted it is "a one-time snapshot without ongoing synchronization."
- **data_sources:**
  - **Google Drive** (only supported `externalSource`; file ID e.g. `1XK_AinrjCzyylNGaH6oX9MUfY5-G-Y54`)
  - Seismic **external content connection** (per-user Drive connection)
  - Seismic **teamsite Library folder** (`parentFolderId`)
  - Seismic Library (destination record, identified by returned `contentId`)
- **apis_hit:**
  - `POST https://api.seismic.com/cm/v1/teamSites/{teamSiteId}/external/contents` — Add external file to Library. `x-seismic-release` not set; scope `seismic.library.manage`; `x-seismic-rate-limit: 1` ("rate-limited to **1 request per second** per token"). Responses: 200 (returns `contentId`), 400 `InvalidParameterApiException`, 401/403 (`NoPermissionApiException`), 404 (`ExternalConnectionNotFoundApiException` / `FolderNotFoundApiException` / `ExternalContentNotFoundApiException`), 500 `UnknownError`. Every error carries `Remediation` and `CorrelationId`.
  - Referenced companion: "GetContentSyncStatus" (no public reference page reached).
  - `GET https://api.seismic.com/reporting/v2/libraryContents` exposes `externalSystemConnectionMapping` and `externalSystemConnectionName`, i.e. the lineage back to the source connection.
- **automations:** This is the automation: `autoSync: true` → "the Seismic Library item will automatically re-sync whenever the linked source file is updated in Google Drive." `autoSync` default is `false` ("The backend defaults this value to `false` when the field is omitted"). So the same endpoint serves both the "one-time snapshot" and "living link" modes.
- **features_tools:** Content Manager External Content Management API; `externalSource` / `externalContentId` / `parentFolderId` / `autoSync` request body; `contentId` response; `externalConnectionId` on the ingest path records "the external content connection that sourced this file, used when ingesting content originally stored in a connected third-party repository such as SharePoint or Google Drive."
- **extensibility:** Deliberately narrow and honest about it: "Only `GoogleDrive` is supported at this time" and the schema notes "Additional sources may be added in future API versions." Third parties add sources by adding a connection + source enum, and correlate back via `contentId` / `externalConnectionId` / the reporting fields `externalSystemConnectionName`.
- **sources:**
  - https://developer.seismic.com/seismicsoftware/reference/addexternalcontent
  - https://developer.seismic.com/seismicsoftware/reference/seismiclibrarycontentmanagementaddafile
  - https://developer.seismic.com/seismicsoftware/reference/reporting-librarycontentsget
- **evidence:**
  - "Add an external file from a supported cloud source (currently Google Drive) to the Seismic Library under the specified teamsite. The operation creates a new Library content item **linked to the external source file** and places it in the target parent folder."
  - "`autoSync`: When `true`, the Library item re-syncs automatically whenever the source file is updated in Google Drive."
  - "The caller must have a valid Google Drive connection configured in Seismic."
  - "This operation is rate-limited to **1 request per second** per token."
  - "`autoSync` … `default: false` … Set to `false` (or omit the field) to import the file as a one-time snapshot without ongoing synchronization. The backend defaults this value to `false` when the field is omitted from the request body."
  - 404 remediation example: "Connect a Google Drive account in Seismic profile settings before retrying."

---

## 9. Approve and publish library content, immediately or on schedule

- **name:** Approve and publish library content
- **user_flow:**
  1. A document is uploaded (workflow 7) and sits at `status: "Draft"`.
  2. An author submits it into a named **approval process** (documented operation: *Submit a document into workflow*, `PUT`, Library Workflow).
  3. Reviewers act on ordered steps (`status`: `Pending` | `Approved` | `Rejected`; each step has `approveButtonLabel` / `rejectButtonLabel`, `assignedTo`, `approvers[]`, `watchers[]`).
  4. To watch the queue, poll `GET /integration/v2/approvalWorkflows` (optionally filtered to `status=Pending`).
  5. Once cleared, `POST https://api.seismic.com/integration/v2/teamsites/{teamsiteId}/publish` with `content: [{id}, {id}, …]`, optionally `comment`, `publishAt`, `IsSendNotification`.
- **data_flow:** Library content item (`id` + `versionId`) → submitted into an `approvalProcess` template → workflow instance with ordered `steps[]` and assigned `approvers`/`watchers` → on approval, `POST …/publish` transitions "its status from Draft to Published", and the item lands in profiles via **Dynamic Folders** that "automatically detect the content based on the content's metadata and content properties." Subscribers may be notified. `publishAt` defers the whole transition to a UTC instant.
- **data_sources:**
  - Seismic **Library** content items + versions
  - Seismic **approval process templates** (`approvalProcess: {id, name}`) — "A named template that defines the ordered steps, assigned approvers, and routing rules applied to a class of approval workflows."
  - Seismic **Users** and **Groups** (as `assignedTo` / `approvers` / `watchers`, `type: "User" | "Group"`, with `isDefault`)
  - Seismic **teamsite** (publish is teamsite-scoped)
  - Seismic **Profiles** + **Dynamic Folders** (the actual publication destination)
  - Seismic notification/subscriber list
- **apis_hit:**
  - `GET https://api.seismic.com/integration/v2/approvalWorkflows` — Get list of approval workflows. `limit` (1–1000, default 100), `offset` (default 0), `continuationToken` (takes precedence over `offset`). Returns `entries[]`, `totalCount`, `limit`, `offset`, `continuationToken`, `nextPageLink`. Scope `seismic.library.manage`.
  - `GET https://api.seismic.com/integration/v2/approvalWorkflows/{approvalWorkflowId}` — get a particular workflow (referenced in the schema note).
  - `PUT` Submit a document into workflow (Library Workflow) — referenced, no reference page reached.
  - `POST https://api.seismic.com/integration/v2/teamsites/{teamsiteId}/publish` — Publish one or more documents. `content` array `minItems: 1`; `maxItems: 10` for immediate publish ("when `publishAt` is supplied for scheduled publishing the backend accepts up to 50 items"); `publishAt` pattern `^\d{4}-\d{2}-\d{2}( \d{2}:\d{2} (AM|PM))?$`, "All times are processed in UTC"; `IsSendNotification` default `true`; `comment` "visible in content history". Returns `totalRequests` / `totalSucceeded` / `totalErrors` / `totalWarnings` + `errors[]` / `warnings[]`.
  - `PUT https://api.seismic.com/integration/v2/teamsites/{teamsiteId}/unpublish` — Unpublish a document (referenced).
- **automations:**
  - **Scheduled publish** — "To schedule a future publish, supply a `publishAt` value… All times are processed in UTC. When `publishAt` is omitted, the documents are published immediately."
  - **Subscriber notification on publish** — "Set `IsSendNotification` to `true` to notify subscribers when the content is published" (default `true`).
  - **Automatic profile landing via Dynamic Folders** — "Documents are published to the profiles with Dynamic Folders that automatically detect the content based on the content's metadata and content properties."
  - **Documented polling pattern for a queue/ETL consumer** — "Poll this endpoint periodically to detect newly submitted workflows with `status=Pending`. To audit all historical workflows, iterate using `offset` increments of `limit` until `entries` is empty or `offset >= totalCount`."
- **features_tools:** Library UI (Profile Builder → teamsite → profile → "add dynamic folder"); per-step Approve/Reject buttons with configurable labels; Activity/state visible via the reporting API (`contentStatus`, `publishedAt`, `publishedByUserId`, `publishedVersionExpiresAt`, `addLibraryContentToProfileAt`, `unpublishedAt`).
- **extensibility:** Partial-success semantics are documented and worth mirroring: per-item `errors[]` / `warnings[]` (e.g. `"Content is already published"`, `"Content was published but notification delivery failed"`), and an explicit refusal to expose a parameter that does not exist — "This endpoint does not currently support a parameter to publish to specific profiles." Batch limit is published per mode (10 immediate / 50 scheduled).
- **sources:**
  - https://developer.seismic.com/seismicsoftware/reference/seismiclibrarypublishingpublishoneormoredocuments
  - https://developer.seismic.com/seismicsoftware/reference/seismiclibraryworkflowgetlistofapprovalworkflows
  - https://developer.seismic.com/seismicsoftware/reference/reporting-librarycontentsget
- **evidence:**
  - "Immediately publishes or schedules publication of up to 10 unpublished Library documents within the specified teamsite. Each document in the `content` array is identified by its library content `id`. The endpoint always publishes the latest version of each document, transitioning its status from Draft to Published."
  - "Documents are published to the profiles with Dynamic Folders that automatically detect the content based on the content's metadata and content properties. This endpoint does not currently support a parameter to publish to specific profiles."
  - "Returns a paginated list of all approval workflows across the tenant, including their current status, steps, assigned approvers, and submitted library content."
  - "`approvalProcess` — A named template that defines the ordered steps, assigned approvers, and routing rules applied to a class of approval workflows."
  - "The documented maximum of 10 items per request applies to immediate (unscheduled) publishing; when `publishAt` is supplied for scheduled publishing the backend accepts up to 50 items."
  - "Whether to send a notification to subscribers when the content is published. Defaults to true when omitted… Set to false to publish silently without notifying subscribers."

---

## 10. Search the content library to assemble a room

- **name:** Search the content library to assemble a room
- **user_flow:**
  1. Obtain a bearer token with the `seismic.search` scope.
  2. `POST https://api.seismic.com/search/v1/content/query` with a JSON body: `term`, `options.searchFields`, `options.returnFields`, `filter`, `sort`, `options.pageSize`, `options.enableSuggestedQueryResults`.
  3. Optionally send `X-Seismic-Client-Details: base64({"application":"application1"})` so the search is attributable in the vendor's own analytics.
  4. Page with `?continuationToken=<value>` (body not required on subsequent pages).
  5. Read `documents[]` with `id`, `versionId`, `format`, `applicationUrls`, `thumbnailUrl`, `pageThumbnailUrls`, `downloadUrl`, `properties[]`, `publishDate` — and feed the chosen items into a room.
- **data_flow:** Query intent (term + facets + return fields) → Seismic search service, which searches `name`, `description`, `body` ("Text in the content. It can be normal text or text transformed from image, audio or video."), `properties` → filter expression (max depth 2) → sorted result pages → resolved content identifiers and **pre-signed asset URLs** → room content assembly. Returned URLs are short-lived: thumbnail / page thumbnails / download tokens "will expire in 1 day."
- **data_sources:**
  - Seismic **search index** over Library + Workspace content (`repository`: `library` for Content Manager / DocCenter / NewsCenter, or `WorkSpace`)
  - Seismic **custom properties** (filterable via `custom.<CustomPropertyName>`)
  - Seismic **content profiles** (filter field `profile`)
  - Seismic **content versions** (`majorVersion`, `minorVersion`, `latestVersion`, `latestApprovedVersion`)
  - Pre-signed URL issuer (`newdownload-ctr.seismic.com/...` in examples)
- **apis_hit:**
  - `POST https://api.seismic.com/search/v1/content/query` — Content Search. Scope `seismic.search`. Rate limit `x-seismic-rate-limit: 2`. Query param `continuationToken`. Response: `queryTimeInMs`, `serviceTimeInMs`, `totalCount`, `documents[]`, `continuationToken`, optional `actualSearchTerm`. Errors: 400 (with messages such as "Search term should be less than 150 characters", "Filter is too complex. Currently the max filter depth is 2.", "PageSize 150 is incorrect. Please set a value between 0-100", "continuationToken is invalid or expired. Please regenerate it."), 401, 403, 500.
  - Conditional-operator surface: `in`, `equal`, `greaterThan` / `greaterThanOrEqual` / `lessThan` / `lessThanOrEqual`; filter operators `and`, `or`.
  - `repository` values documented as: "The value can be library (if location is Content Manager or DocCenter or NewsCenter) or WorkSpace".
  - Documented companion on the same server: `POST /v1/generative/query` and `GET /v1/generative/source` ("Do not use this endpoint when users expect a synthesized answer with citations; use /v1/generative/query instead.").
- **automations:**
  - **Zero-hit broadening is built in:** "`enableSuggestedQueryResults` … When `enableSuggestedQueryResults` is set to `true` and the initial search returns no results, the service will attempt to find content using related suggested terms. If results are found using a suggested term, the response will include an additional field `actualSearchTerm`." Default off.
  - **Continuation tokens expire:** "Continuation Toke timeout … `continuationToken` is invalid or expired. Please regenerate it." — i.e. pagination cursors are time-bounded, so consumers must re-issue the search rather than resume indefinitely.
  - **Caller attribution:** `X-Seismic-Client-Details` "If provided, the search activity data can be available to customers for reporting and insight analytics purposes."
- **features_tools:** `searchFields` (name / description / body / properties), `returnFields` (9 returned by default: `repository`, `name`, `teamsiteId`, `id`, `versionId`, `type`, `applicationUrls`, `format`; 9 more opt-in incl. `properties`, `thumbnailUrl`, `pageThumbnailUrls`, `downloadUrl`, `publishDate`, `majorVersion`/`minorVersion`), `sort` fields, `pageSize` 0–100 (default 40), `filter` expression, `enableSuggestedQueryResults`. `applicationUrls` includes a "Workspace Share Link" — i.e. a per-deal shareable URL per content item.
- **extensibility:** The filter language is the extension contract and its limits are explicit and enforced: "currently the max filter depth is 2", "custom property supports `equal` operator" (with date/greater-than-lower-than also supported for date-typed custom properties), array equality semantics ("documents will match if and only if the two sets of terms are identical"), RFC 3339 dates, `X-Seismic-Client-Details` for per-app attribution, and an `x-ai-description` guardrail block on the operation telling agents "Apply filters only when explicitly requested by the user; do not infer extra constraints (especially date filters) unless requested."
- **sources:**
  - https://developer.seismic.com/seismicsoftware/reference/contentsearch
  - https://developer.seismic.com/seismicsoftware/reference/introduction-overview
- **evidence:**
  - "Seismic public search API provides our customers and partners a programmatic interface to search Seismic content. Developers can query all content that a user has access to using a search term, and further apply a content filter, specify return fields, and sort the search results."
  - "body — Text in the content. It can be normal text or text transformed from image, audio or video."
  - "A query not specifying a search term or simply providing a completely empty query body, such as empty or `{}` queries all content."
  - "If search results span multiple pages, the response will contain a `continuationToken` property of non-null value."
  - "thumbnailUrl … The token will expire in 1 day." / "pageThumbnailUrls … The tokens will expire in 1 day." / "downloadUrl … The token will expire in 1 day. For example, Google documents (e.g. GSlide, GDoc, GSheet) do not include this field as they cannot be downloaded."
  - "Use it when users ask to find decks, files, templates, or documents by keyword, metadata, and filters."
  - "**Note: currently the max filter depth is 2.**"

---

## 11. Take a room from draft to live and hand over the link

- **name:** Take a room from draft to live
- **user_flow:**
  1. In the Qwilr **editor** (or on the **Dashboard**), click the **Share** button.
  2. Use the drop-down in the pop-up to set the page **Live**.
  3. "That action automatically copies the public Page link to your clipboard, so you can paste it into a message to your buyer."
  4. If the link is needed later, click **Share** again → **Copy Shareable Link** (bottom of the pop-up).
  5. Optionally set link expiry, view limit, password, or identity verification in the same pop-up.
- **data_flow:** Draft page (public URL disabled) → user sets status `Live` → the page's public URL is enabled and the status flips in the dashboard model → the link is copied to the sender's clipboard for distribution. "Qwilr doesn't send emails natively because we know you want to be able to control and track that from your end" — so Qwilr hands over a URL, not an invitation.
- **data_sources:**
  - Qwilr **page** record (`status`, `published` boolean, `name`, `tags`, `ownerId`, `metadata`, `expiry`, `paymentSettings`)
  - Qwilr **owner/user** records (`GET /v1/users` returns "the list of users from your account, with roles and team names")
  - Qwilr **template** (a page converted to a template, never published itself)
  - Clipboard (client OS) as the delivery mechanism
- **apis_hit:**
  - `PUT https://api.qwilr.com/v1/pages/{pageId}` — Update page. "Updates the `published` field of the page, its link `expiry` settings and its Qwilr Pay `paymentSettings`." Body: `published`, `expiry`, `paymentSettings`, `substitutions`, `blocks`. Responses include 200, 400, 401, 403, 404, 409, 422, 429.
  - `GET https://api.qwilr.com/v1/pages?status=draft,live,accepting,accepted,disabled,declined&expand=metadata` — List pages, filterable by `status` ("`draft` selects the pages that you have not published. `live` and `accepting` are exclusive: `live` does not select a page that is `accepting`."), `tags` (case-sensitive; "An archived page has the `archived` tag, so `tags=archived` returns your archived pages and only those pages"), `folderId`, `ownerId`, `sortDirection`, `limit` (default 25), `cursor` (`nextCursor`).
  - `POST https://api.qwilr.com/v1/webhooks` with `event: "pageSetLive"` (and `"pageRevivedLive"`) — so a downstream system learns about the transition without polling. `GET /v1/webhooks` lists subscriptions; `DELETE /v1/webhooks/{subscriptionId}` cancels.
  - `POST https://api.qwilr.com/v1/pages` with `published: false` keeps the new page in Draft: "`published` boolean — Whether the page is publically available; `false` means the page will be in Draft status. Default is `false`."
- **automations:**
  - **Status transition on publish** — the status change is the system effect; it is also a webhook event (`pageSetLive`).
  - **Clipboard auto-copy** on Set Live.
  - **Expiry-driven status change** (cross-ref workflow 12): "On the expiration date, the page will automatically switch to a Declined status."
  - **Qwilr never auto-emails a page** — the user owns the send, and the vendor tracks the resulting `pageViewed` / `pageFirstViewed` events.
- **features_tools:** Share pop-up (status drop-down, Copy Shareable Link, Link expiry settings, Other settings, Access Settings), Dashboard page grid with per-page status badge, Search/filter box, Templates sidebar, Blocks library.
- **extensibility:** `metadata` on the page is the documented correlation field — "Data you provide, that will be returned as part of all Webhooks." Combined with `tags` and `ownerId`, a third party can round-trip a CRM deal id through page creation and receive it back on every event. `expand=metadata|acceptance|previewAcceptance` on `GET /v1/pages/{pageId}` controls payload. Rate limit status `429` is published. Templates are reusable: "Templates are made up of blocks, just like any other page. A template doesn't go 'live' and can't be shared with an unauthenticated user like a normal page."
- **sources:**
  - https://help.qwilr.com/article/87-sharing
  - https://help.qwilr.com/article/578-dashboard-page-statuses
  - https://docs.qwilr.com/api-reference
  - https://docs.qwilr.com/docs/getting-started/api-concepts
- **evidence:**
  - "Each new Qwilr Page starts in Draft status. The public link is deactivated. When you're ready for your client to see the Page, you'll set it to Live status." / "To do that, click the **Share** button, either from the Dashboard or from within the Page. Then, use the drop down menu to set the page Live." / "That action automatically copies the public Page link to your clipboard, so you can paste it into a message to your buyer."
  - "All new Qwilr Pages start out in Draft status. You can edit the page in this mode, but the public page URL is disabled. That way you can perfect your page without any danger of your client seeing it too soon."
  - "Pending and Live both indicate that the page is available to be shared publically on the web. The only difference between these two page statuses is that Pending indicates there is an accept block on the page."
  - "Qwilr doesn't send emails natively because we know you want to be able to control and track that from your end."
  - API: "`draft` selects the pages that you have not published. `live` and `accepting` are exclusive."
  - `metadata` — "Data you provide, that will be returned as part of all Webhooks."

---

## 12. Generate a personalised room programmatically from a template

- **name:** Generate a personalised room from a template
- **user_flow:**
  1. In the Qwilr editor, build a page with content and **convert it into a template**.
  2. In the Variables menu of the editor app bar, create a variable (e.g. `Hello World`).
  3. Insert the variable into content by typing `{{` and selecting from the dropdown (e.g. reference `hello_world`).
  4. Copy the **Template ID** from the editor URL: `https://app.qwilr.com/#/page/<template-id>`.
  5. Out-of-band, `POST https://api.qwilr.com/v1/pages` with `{ templateId, name, published, substitutions, metadata, tags }` and `Authorization: Bearer <token>`.
  6. Read the 201 Created response with the new page's fields; distribute the public link, or `PUT /v1/pages/{pageId}` later to publish/expiry it.
- **data_flow:** Template (blocks) + saved blocks + `substitutions` map (API reference keys → values) + `metadata` (arbitrary caller data echoed in webhooks) + `tags` + `ownerId` + `expiry` → `POST /v1/pages` → **new Qwilr page** with variables substituted in and a live/pending public link. Substitutions are hierarchical: page-level `substitutions` "can be overwritten if the same keys are defined in the block-level substitutions."
- **data_sources:**
  - Qwilr **template** (page converted to template; `templateId`)
  - Qwilr **saved blocks** library (`GET /v1/blocks/saved` — "Retrieve a list of saved blocks from your account, with block names. Useful in developer workflow for mapping block names to saved block IDs")
  - Qwilr **Account Variables** (the substitution keys)
  - Qwilr **CRM record** as the upstream value source when integrated (Salesforce/HubSpot/Pipedrive/Zoho/Dynamics; "All of these can be applied to CRM integrated Variables as well")
  - Qwilr **users** (`ownerId`)
- **apis_hit:**
  - `POST https://api.qwilr.com/v1/pages` — Create a page. Authorisation: exactly one of `{templateId}` or `{blocks[]}`. Attributes: `name`, `published` (default `false`), `substitutions`, `metadata`, `tags` (case-sensitive), `ownerId` (pattern `^[a-z0-9]{24}$`), `expiry`. Responses 201/400/401/403/404/422/429.
  - `GET https://api.qwilr.com/v1/blocks/saved` — Get saved blocks.
  - `GET https://api.qwilr.com/v1/pages/{pageId}?expand=metadata,acceptance,previewAcceptance` — Get a page.
  - `GET https://api.qwilr.com/v1/pages/{pageId}` doc note: "Each result is a summary of a page, not a full page. A summary has no blocks, acceptance data or payment settings. Get a page to read those fields."
  - `PUT https://api.qwilr.com/v1/pages/{pageId}` — full content replacement: "Supplying `blocks` performs a full content replacement of the page from saved blocks (the page keeps its id and links); only pages created via the saved-block method are supported."
  - `GET https://api.qwilr.com/v1/users` — "Retrieve the list of users from your account, with roles and team names. Useful for specifying the `ownerId` when creating a page."
  - `POST https://api.qwilr.com/v1/taxes`, `PATCH /v1/taxes/{taxId}`, `DELETE /v1/taxes/{taxId}`, `GET /v1/taxes`, `GET /v1/payment-gateways` — supporting quote/payment configuration.
  - Base URL `https://api.qwilr.com/v1`; `Authorization: Bearer <JWT>`; HTTPS only; "Your access token allows anyone to access your Qwilr pages and account. Be sure to keep it secret!"
- **automations:**
  - **Caller-supplied tax IDs are the external-master-data hook:** "You can also supply your own `id`, for example an ID from another system, to refer to the tax by a value that you control. … You cannot change the id after you create the tax. To use a different ID, delete the tax and create a new one."
  - **Publication is opt-in per call** via `published` (default `false`) — no implicit publish.
  - **Expiry is set at creation:** "The count of days starts when you publish the page. A draft page keeps the setting until you publish it."
  - Documented server-side batch ceiling for the CSV path: "You can build up to 50 Qwilr Pages from a single CSV."
- **features_tools:** Editor Variables menu (`{{` autocomplete), template conversion, Blocks library, Templates sidebar, page `Tags`, page `Metadata`, page `Owner`, `Link expiry` object, quote blocks with `quoteSettings` / `quoteSections` / `lineItems` (incl. `billingSchedule`, `featuresList`, `taxExempt`, `recommended`, `optional`, `quantityRange`) and `paymentSettings` (gateway, `partialPaymentConfig`, `recurringConfig`, `invoiceConfig`).
- **extensibility:** Three levels, all documented: (1) **template** (page shell, never published), (2) **saved blocks** (reusable fragments — "Saved blocks are duplicated into a new document, and don't retain any connection with the block created"), (3) **substitutions/variables** including repeating keys: `"repeating_variable_key": [ { "item_property": "value1" }, { "item_property": "value2" } ]`. Caller-defined IDs, caller-defined metadata, caller-defined owner, and template-scoped identity verification are the extension seams. Sibling docs cover CRM template tokens, conditional content, and image/video/map/button/alt-text tokens.
- **sources:**
  - https://docs.qwilr.com/api-reference
  - https://docs.qwilr.com/docs/getting-started/quick-start
  - https://docs.qwilr.com/docs/getting-started/api-concepts
  - https://help.qwilr.com/article/578-dashboard-page-statuses
- **evidence:**
  - "With our API you'll be able to generate Qwilr Pages programmatically. This means you can generate custom quotes, create pages when someone fills out a form, or anything else."
  - "Creates a page from saved blocks or template." — authorisation is "One of: Saved Blocks | Template"
  - "`substitutions` object — Mapping of variable API reference keys to substitution values used throughout the page. The values can be overwritten if the same keys are defined in the block-level substitutions."
  - "`metadata` object — Data you provide, that will be returned as part of all Webhooks."
  - "`expiry` object — The link expiry settings for the page. When you enable expiry, the link of the page stops working after the number of days that you set, and the status of the page becomes `declined`. The count of days starts when you publish the page. A draft page keeps the setting until you publish it."
  - "Grab your Template ID — Go to the Qwilr app and when editing your template, copy the ID that appears in the URL https://app.qwilr.com/#/page/template-id-here"
  - "Templates are made up of blocks, just like any other page. A template doesn't go 'live' and can't be shared with an unauthenticated user like a normal page."
  - "Variables are placeholders for incoming API data in templates or saved blocks. In the Qwilr API code and API documentation, these are referred to as substitutions."

---

## 13. Personalise room content with conditional rules

- **name:** Personalise room content with conditional rules
- **user_flow:**
  1. In the Qwilr **Templates** sidebar, create a new template or clone an existing one.
  2. In the template editor, open a block's top-left menu → **Add rules**.
  3. Click **+ Add condition** → the **Show block if** screen.
  4. Choose a Variable as the filter point and a modifier, e.g. *Region* **is** "Australia".
  5. Add more conditions; combine with **And** / **Or** (up to 10 Or per block; And unlimited).
  6. Create a page from the template and supply the variable values → blocks reveal or stay hidden.
  7. For sub-block granularity, add the **Rules widget** inside a block instead of a block-level rule.
- **data_flow:** Variable field values supplied at page creation (from Qwilr **Account Variables** or **CRM-imported Variables**) → evaluated against a condition tree `{variable, category (Text|Number|Any), modifier, value}` joined by `And` / `Or` → per-block boolean → the block is rendered or omitted from the generated page. This is a *generation-time* decision, not a viewer-time one.
- **data_sources:**
  - Qwilr **Account Variables** (native)
  - Qwilr **CRM Variables** — "You can use the Variables imported from your CRM integration with Qwilr. They will appear in the conditions list the same as the account Variable"; supported CRMs per docs: Salesforce, HubSpot, Pipedrive, Zoho (and Microsoft Dynamics 365 in the Automations category)
  - Qwilr **Templates** + **Blocks**
  - Underlying CRM record fields (the value source)
- **apis_hit:** Conditional rules are configured in the Qwilr editor, not the public REST API — no `/v1/rules` endpoint exists in the API reference I read. The adjacent API surface is the same as workflow 12: `POST /v1/pages` (`substitutions`, `metadata`, `tags`, `published`, `ownerId`, `expiry`) and `PUT /v1/pages/{pageId}` (`substitutions`, `blocks`). A CRM-integration article exists for Salesforce/HubSpot template-token mapping (`help.qwilr.com/article/344-salesforce-template-creation`, `/347-hubspot-template-creation`, `/374-crm-template-tokens`) but I did not read those pages, so I make no claims about their endpoints.
- **automations:**
  - **Rule evaluation at page creation**, from the data supplied by the caller or CRM.
  - **Incomplete rules fail open:** "What happens if I have a condition where I did not set a Variable? These conditions are considered incomplete, and will be ignored when the page is created. This means the block of content will appear on the new page."
  - **Empty and `"0"` are valid match values:** "Is '0' or an empty field still considered a valid condition? Yes. If a condition is created and '0' or no value is input into the value box, then this will be considered a valid condition and will be resolved when a page is created from a template."
  - **Saved Blocks silently drop rules:** "**Saved Blocks** cannot have rules setup and if a block with conditions on a template is saved to the **Saved Block Library**, it will not maintain the rule." — i.e. the extraction step is a rule-lossy boundary, a real integration hazard.
  - **Blocked on the accept block:** "All block types can have rules applied to them with the exception of the **Accept Block**."
- **features_tools:** Block menu → **Add rules**; **+ Add condition**; **Show block if** screen; condition categories **Text** (is / is not / contains / does not contain / starts with / ends with / includes / does not include), **Number** (equals / does not equal / is more than / is less than), **Any** (has no value / has any value); **And** / **Or** modifiers; trash icon to delete a condition; **Rules widget** for in-block conditional content.
- **extensibility:** Plan-gated (documented as Scale, extra cost) — a real constraint to note. Extensible along the CRM axis: any CRM whose Variables import into Qwilr become available condition sources. Documented non-goal: "Does the conditional logic work at the widget and/or content level? Not at this time, but we hope to implement it in the future." Documented limit: "You can utilize up to **10 OR conditions** for a rule."
- **sources:**
  - https://help.qwilr.com/article/849-conditional-content-with-qwilr-templates
  - https://help.qwilr.com/category/85-templates-and-variables
  - https://docs.qwilr.com/api-reference
- **evidence:**
  - "With Conditional Content, you can build rules using the Variables in your Qwilr templates to determine what content appears and disappears!"
  - "By leveraging either the existing account Variables in your Qwilr account or the Variables imported from your integrated CRM, you can setup rules on your templates to reveal or hide content based on information provided at page creation from those Variable fields."
  - "The rules are defined by **conditions** where a Variable field is selected as the filter point and the **condition** is set based on whether certain information appears or is entered in that field on page creation."
  - "Text: is / is not / contains / does not contain / starts with / ends with / includes / does not include. Number: equals / does not equal / is more than / is less than. Any: has no value / has any value."
  - "Qwilr allows multiple conditions to be applied in a block to define which blocks show up on the page. You can utilize either the **And** or **Or** modifier."
  - "You can utilize up to **10 OR conditions** for a rule."
  - "**Or** conditions are limited to 10 per block, while **AND** conditions do not have a limit."
  - "**Saved Blocks** cannot have rules setup and if a block with conditions on a template is saved to the **Saved Block Library**, it will not maintain the rule."
  - "All block types can have rules applied to them with the exception of the **Accept Block**."

---

## 14. Expire or cap access to a room

- **name:** Expire or cap access to a room
- **user_flow:**
  1. Click the **Share** button (from the dashboard or the page editor).
  2. Click **Link expiry settings** → enable **Enable Link Expiry** → type the number of days (or use the arrows).
  3. Optionally **Other settings** → enable **Restrict Number of Views** and enter the view count cap.
  4. *(optional)* Add password protection (`Add password protection`) for non-Growth/Scale plans.
  5. Observe: on the expiration date the page "will automatically switch to a **Declined** status" on the dashboard; when the view cap is reached the status reads **View Limit** while the page "will retain a Live status, although it's been disabled by the view limit."
  6. Buyers see a persistent message bottom-left when expiry is within 7 days, and an error message once expiry/limit is hit. To change or remove, reopen the same settings; to revive, **Share → Set Live**.
- **data_flow:** Room/page link + (expiry day count | view cap) → on reaching the cap: an error message replaces page content; on reaching the expiry date: status transition `live → declined` and the public link stops working → dashboard reflects the new state. Changeable at any time; revival via `Set Live` re-enables the link. Both are stored as first-class record fields, so `GET /v1/pages?status=…` and `?tags=archived` reflect them.
- **data_sources:**
  - Qwilr **page** record: `expiry { enabled, …days }`, view-limit setting, `status`
  - Qwilr **view counter** per page
  - Qwilr **dashboard status model** (Draft / Live / Pending / Accepted / Declined / Blueprint / View Limit)
  - No external IdP or CRM involved
- **apis_hit:**
  - `PUT https://api.qwilr.com/v1/pages/{pageId}` with `expiry` and `published` — "Updates the `published` field of the page, its link `expiry` settings and its Qwilr Pay `paymentSettings`."
  - `expiry` semantics: "When you enable expiry, the link of the page stops working after the number of days that you set, and the status of the page becomes `declined`. The count of days starts when you publish the page. A draft page keeps the setting until you publish it."
  - `GET https://api.qwilr.com/v1/pages?status=draft,live,accepting,accepted,disabled,declined` — filter by terminal status; `?tags=archived` for archived.
  - `POST https://api.qwilr.com/v1/pages` accepts `expiry` at creation.
  - Webhook events relevant to the revived case: `"pageRevivedLive"`, `"pageDeclined"` is **not** in the published event enum — the documented events are `"pageAccepted" "pagePartiallyAccepted" "pagePreviewAccepted" "pageViewed" "pageFirstViewed" "pageSetLive" "pageRevivedLive"`, so expiry-driven decline is a poll-only signal.
- **automations:**
  - **Automatic status transition on expiry:** "On the expiration date, the page will automatically switch to a Declined status."
  - **Automatic link death on cap:** "Once that number has been reached, your clients will see an error message when they view the page."
  - **Buyer-side imminent-expiry nudge:** "if the expiration date is within 7 days they'll see a persistent message in the bottom left corner."
  - **Status-on-expiry can be masked:** "The Page will retain a **Live status**, although it's been disabled by the view limit. If you'd rather mark the page as Declined, click the **Share** button…" — so `Live` does not imply accessible.
  - **Liferay's analogue** (different mechanism, same goal): per-person `Access Valid Until` with UTC cut-off and a 7-day warning banner (workflow 4), and per-room Archive/Restore (workflow 5).
- **features_tools:** Share pop-up → **Link expiry settings** (**Enable Link Expiry** + day stepper) and **Other settings** → **Restrict Number of Views**; dashboard status badges *Declined* and *View Limit*; **Share → Set Live** to revive; buyer-facing expiry banner and error page; password protection (lower plans).
- **extensibility:** Two orthogonal, independently toggleable constraints (time and count) that compose, plus a documented interaction to respect when reviving: "If your page reaches the view limit and then you manually set it as Declined, you can still manually set it Live later. If you do that, the view limit setting will still in place, so you'll want to use the steps above to remove it." Liferay supplies the third primitive (per-recipient expiry) that Qwilr lacks — a strong argument for supporting both in a room product.
- **sources:**
  - https://help.qwilr.com/article/768-setting-automatic-link-expiry-on-a-qwilr-page
  - https://help.qwilr.com/article/148-adding-view-limits
  - https://help.qwilr.com/article/578-dashboard-page-statuses
  - https://help.qwilr.com/article/151-adding-security
  - https://docs.qwilr.com/api-reference
  - https://learn.liferay.com/w/digital-sales-room/managing-rooms
- **evidence:**
  - "By default, Qwilr's public page URLs never expire. With the expiry setting, you can make the link active for a specific number of days. Once that time is up, the link will display an error message instead."
  - "On the expiration date, the page will automatically switch to a Declined status. You'll see that on your dashboard." / "You can always set the page Live once again if needed."
  - "When your client views the page, if the expiration date is within 7 days they'll see a persistent message in the bottom left corner."
  - "Enable the **Restrict Number of Views** switch, and enter the number of times your page can be viewed. Once that number has been reached, your clients will see an error message when they view the page."
  - "Once your client reaches the view limit, you'll see the status on your dashboard change to **View Limit**. The Page will retain a **Live status**, although it's been disabled by the view limit."
  - "You'll see this status on your dashboard for two reasons: The page has reached a link expiration you've set; You've manually set the page as Declined."
  - Liferay: "Access ends at the end of the expiration date in UTC. The person then no longer appears in the Who Has Access list."

---

## 15. Verify buyer identity and restrict by email domain

- **name:** Verify buyer identity and restrict by domain
- **user_flow:**
  1. Click **Share** (from the editor or by hovering the page on the dashboard).
  2. In the pop-up click **Access Settings**.
  3. Tick **Email Verification** (Growth/Scale). This also unlocks **Domain Security**.
  4. Optionally tick **Domain Security** and enter the approved domain(s), comma-separated, "You don't need to type the @ symbol".
  5. *(Lower plans)* prefer the softer **Identification** options — **Name** and/or **Email** — to collect identity for tracking "without needing them to login to their email to verify".
  6. *(Template-level)* apply the setting to a **template** so "identity verification is automatically applied to each page your team creates from it."
  7. Buyer opens the link → sees a form → enters name + email → receives a verification email → clicks the link → can then view the page. (If the buyer has a **Springboard** account they can "log in with their existing *Springboard Account* to populate their name and email.")
- **data_flow:** Buyer-supplied (or email-verified) identity → a session/gate in front of the page → after verification the identity is recorded and becomes visible in **page analytics** ("Once someone has verified their identity and viewed your page, you'll be able to see this information within your page analytics"). Domain allowlist is evaluated against the email domain; a non-matching domain is refused. This turns an anonymous link into an attributed, identity-bound room view.
- **data_sources:**
  - Qwilr **identity service** (login at `identity.qwilr.com/auth/v1/login`, used by Springboard)
  - **Qwilr Springboard account** (buyer identity store; same-email accounts are "connected in our system")
  - Qwilr **email delivery** (verification link)
  - Qwilr **page security settings** (Email Verification, Domain Security, Name/Email identification)
  - Qwilr **page analytics** (destination for the captured identity)
- **apis_hit:** No public REST endpoint for security settings — access is via the Share pop-up UI. The adjacent documented surfaces: `GET /v1/pages/{pageId}?expand=metadata,acceptance,previewAcceptance`; `PUT /v1/pages/{pageId}` (publish/expiry/payment only). Identity reuse is documented for the buyer: "If your client has a Qwilr Springboard account, they can choose to log in with their existing *Springboard Account* to populate their name and email." Identity verification can be inherited at template level: "For **Growth** and **Scale** plans you can also apply this setting to any template, so identity verification is automatically applied to each page your team creates from it."
- **automations:**
  - **Verification email round-trip** — "If your buyer chooses to authenticate with email, they'll enter their name and email address. Then they'll receive an email with a link to verify their email address. Once they've verified, they'll be able to view the Page."
  - **Verified identity flows into analytics automatically** — "Once someone has verified their identity and viewed your page, you'll be able to see this information within your page analytics."
  - **Verification state is inherited by generated pages** when set on a template.
  - **Enforcement can be made mandatory on the sign-off form** (adjacent): the Organization field can be shown, hidden, or "**enforce** that it must be filled out before the document can be accepted… Great for contracts or forms where you must collect company information from the signer."
- **features_tools:** Share pop-up → **Access Settings**; **Email Verification** checkbox; **Domain Security** checkbox + domain list; **Name** / **Email** identification options; buyer-facing verification form; template-level setting; Springboard login option; Page Analytics.
- **extensibility:** Three distinct assurance tiers documented (verify by email; verify + domain allowlist; identify-only), plus a template-level default so the control is enforced by policy rather than per-page discipline. Documented caveat to design around: bot/link-scanner traffic pollutes analytics — "In rare cases, you may receive analytics notifications that appear to be from real viewers but are instead triggered by automated systems or bots (such as preview services used by Microsoft, Outlook, or antivirus scanners) … As a workaround you can *Link* the Qwilr URL to a button or phrase (e.g. 'View Proposal') rather than pasting the raw URL." Also: "Currently, Qwilr does not support IP address blocklisting." And: "Qwilr pages that are embedded in other websites do not track analytics/page views consistently" — so identity + analytics both degrade when the room is iframed.
- **sources:**
  - https://help.qwilr.com/article/151-adding-security
  - https://help.qwilr.com/article/156-analytics-insights
  - https://help.qwilr.com/article/797-welcome-to-qwilr-springboard
  - https://help.qwilr.com/article/175-getting-documents-accepted
- **evidence:**
  - "click **Access Settings**, then click the **Email Verification** checkbox. This will also allow **Domain Security** to be selected if you want to limit the access from the domain an email user comes from."
  - "If you want to simply have them fill out their name or email address for tracking purposes in analytics, without needing them to login to their email to verify, you can check either of the two *Identification* options of *Name* and/or *Email*."
  - "restrict verification to viewers with a certain email domain, or a set of domains… check the box for **Domain Security**, and enter the approved domain(s). If you want to allow views from more than one domain, separate them with commas. **You don't need to type the @ symbol**."
  - "Once someone has verified their identity and viewed your page, you'll be able to see this information within your *page analytics*."
  - "For **Growth** and **Scale** plans you can also apply this setting to any template, so identity verification is automatically applied to each page your team creates from it."
  - "Some features are only available to Growth and Scale customers (or on legacy Enterprise Plans)."

---

## 16. Collect buyer sign-off with e-signature and an audit trail

- **name:** Collect buyer sign-off with e-signature
- **user_flow:**
  1. In the page editor, click the **+** icon at the top or bottom of a block → select **Accept** ("You can only add 1 Accept Block per Page").
  2. Pick an **Accept Preset**: single-click accept (no e-signature) / one e-signature / accept with payment collection / multiple e-signatures. (Changeable later via the **E-Signature** icon on the block.)
  3. Customise: context text above the button; button label/colour/alignment via **Button Options**; enable payments; add a **Custom Form**; enable **E-Signature**; add a **custom redirect** after acceptance; toggle **Organization Field** show/hide and **Require Organization field**; set **Language** (US English / UK English).
  4. *(Preview)* Click the Accept button while editing → "**stepping through the acceptance process in the preview won't accept your page**."
  5. Set the page **Live** — status becomes **Pending** ("Pending indicates there is an accept block on the page").
  6. Buyer clicks **Accept** → completes the form (+ e-signature) → clicks Accept again → page status flips to **Accepted**.
  7. Read the **Audit Trail** (dashboard hover → *Audit Trail*, or the acceptance email link) and download it as **PDF or JSON**; download the signed **Agreement** PDF.
- **data_flow:** Accept Block config (preset, form fields, e-signature mode, org-field requirement, language, post-accept redirect) + buyer input (name, email, organisation, signature, custom-form answers, payment) → status `live → pending → accepted` → on acceptance, Qwilr renders an on-page confirmation the buyer can save to Springboard, emails **both** the page creator and all acceptors, generates a signed **Agreement PDF** (emailed to everyone who signed) and an **Audit Trail** record + PDF containing creator, publish time, **IP address and timestamp of first visit**, acceptor contact details, signature / form responses / payment details, backup copies, and the record of notification emails. Timestamps are normalised: local time in the web Audit Trail, **UTC in the Audit Trail PDF**.
- **data_sources:**
  - Qwilr **Accept Block** configuration
  - Qwilr **E-Signature** service ("legally compliant *E-Signature* feature")
  - Qwilr **Audit Trail** store (audit details + stored backups)
  - Qwilr **Springboard** (buyer can save a copy of their acceptance)
  - Qwilr **email delivery** (acceptance emails; from Qwilr, containing the Agreement download link)
  - **Qwilr Pay** payment gateways (`GET /v1/payment-gateways`; `paymentSettings.gatewayId`, `requireOnAccept`, `partialPaymentConfig`, `recurringConfig`, `invoiceConfig`)
  - Buyer IP address (captured on first view, recorded in the audit trail)
- **apis_hit:**
  - `GET https://api.qwilr.com/v1/pages/{pageId}?expand=acceptance,previewAcceptance` — the acceptance payload is only available with an explicit expand.
  - `GET https://api.qwilr.com/v1/pages?status=…,accepting,…` — `accepting` is the Pending state; "`live` and `accepting` are exclusive: `live` does not select a page that is `accepting`"; the API also lists `"accepted"`.
  - `POST https://api.qwilr.com/v1/webhooks` with `event: "pageAccepted"` / `"pagePartiallyAccepted"` / `"pagePreviewAccepted"` — push notification of sign-off.
  - `PUT https://api.qwilr.com/v1/pages/{pageId}` with `paymentSettings` — "To switch payments on, send `enabled: true`. The page then needs a gateway, so send `gatewayId`, or omit it to use the default gateway of your account. Note: if you switch payments on for a page that has no gateway, the endpoint replaces all of the payment settings with the defaults of your account. It does not merge your values into them."
  - `POST /v1/taxes`, `PATCH /v1/taxes/{taxId}`, `DELETE /v1/taxes/{taxId}` — quote tax definitions referenced by `quoteSettings.taxIds`.
  - `metadata` — "Data you provide, that will be returned as part of all Webhooks" — the correlation hook for a sign-off.
- **automations:**
  - **Status flip on acceptance** — "Before your page is accepted, it will have a status of **Pending** on your Dashboard. Once it's been accepted you'll see the status switch to **Accepted** and you'll be able to view the Audit Trail."
  - **Emails on acceptance** — "Once your client has accepted the page, both you and they will receive an email. **The acceptance email is sent to the creator of the page and all acceptors.**"
  - **Signed Agreement PDF auto-generated and emailed** — "After a client accepts your page, Qwilr automatically emails a signed copy of the Agreement PDF to everyone who signed it. The email arrives from Qwilr and contains a download link for the signed Agreement — this is your formal signed record."
  - **Immutable after sign-off** — "once a live Qwilr Page has been accepted it **CANNOT** be **reversed, deleted, or edited**. If you need a new copy of the page for your buyer you can *clone* it, and add a note that any previous copies are classified as void."
  - **Springboard autofill** — "While accepting a page, if you're signed into your Springboard account, you will have the option to autofill your signature information. This will include your name, organization, and your last-used e-signature."
  - **Status downgrade blocked** — "**Note**: Pages cannot be manually declined by a Qwilr user if it contains an Accept block and/or quote block."
- **features_tools:** Accept Block + Accept Presets; E-Signature icon; **Button Options**; **Block Options → Organization Field / Require Organization field / Language**; Custom Form; payments toggle; custom post-acceptance redirect; agreement/accept form PDF layout ("**Compact layout:** Multiple forms (2+) fit on a single PDF page"); dashboard **Audit Trail** button with PDF/JSON download and backup copies; Acceptance emails.
- **extensibility:** Multi-signer presets ("Accept with multiple E-Signatures") plus a documented FAQ "FAQ: Can I collect multiple signatures?"; a `custom-redirect` destination after acceptance; a Custom Form on the acceptance step; a required-organisation-field toggle; a language switch; webhook fan-out on `pageAccepted`; and `metadata` round-trip. Audit Trail exportable as **JSON** (machine) or **PDF** (human) — the JSON path is the integration seam. Documented and useful separation of concerns: "The Agreement PDF is separate from the Audit Trail. The Audit Trail records the timeline of the acceptance process… It is not the same as the signed Agreement document itself."
- **sources:**
  - https://help.qwilr.com/article/175-getting-documents-accepted
  - https://help.qwilr.com/article/157-the-audit-trail
  - https://help.qwilr.com/article/578-dashboard-page-statuses
  - https://help.qwilr.com/category/410-the-acceptance-process
  - https://docs.qwilr.com/api-reference
  - https://help.qwilr.com/article/797-welcome-to-qwilr-springboard
- **evidence:**
  - "Use our legally compliant *E-Signature* feature to get sign-off from one or multiple buyers! Get notified when they sign, and even accept *payments* on the go."
  - "once a live Qwilr Page has been accepted it **CANNOT** be **reversed, deleted, or edited**."
  - "**Organization Field** … **Require Organization field** … Great for contracts or forms where you must collect company information from the signer."
  - "When your client clicks the Accept button, they'll be prompted to fill out the acceptance form. (If you've enabled e-Sign, they'll be prompted to sign as well.) Then they'll click the Accept button once again."
  - "The Audit Trail includes these details: Who created the project; When the project was published; **The IP address and timestamp of the first visit to the page**; Contact details of the acceptor, including name, email, and company; Digital signatures, custom form responses, or 'pay now' details…; Links to stored backups of the project and audit trail data; Record of the notification emails sent to all parties."
  - "you can download a backup copy of the Audit Trail in either PDF or JSON format. You can also download the Agreement directly from this page."
  - "in the Audit Trail PDF, the date and time are converted to UTC, which stands for Coordinated Universal Time."
  - "While editing a Qwilr Page, you can preview the acceptance process at any time… **stepping through the acceptance process in the preview won't accept your page**."

---

## 17. Sync room events to the CRM via webhooks and automations

- **name:** Sync room events to the CRM
- **user_flow:**
  *Webhook path:*
  1. `POST https://api.qwilr.com/v1/pages`-independent: create a subscription with `POST https://api.qwilr.com/v1/webhooks` body `{ "event": "pageAccepted", "targetUrl": "string" }`.
  2. "New events will be sent to the defined `targetUrl`." Store the returned `id` "in case you want to cancel the subscription later on."
  3. List with `GET /v1/webhooks`; cancel with `DELETE /v1/webhooks/{subscriptionId}`.
  *No-code CRM path (Salesforce):*
  4. Ensure the Salesforce integration is enabled in Qwilr's Salesforce integration settings (existing users must **disable and re-enable** first; "we only support Salesforce automations in production. Automations cannot be set up in a sandbox environment.").
  5. In the Qwilr dashboard sidebar open **Automations**; start from a **Recommended Automation** (e.g. "When a page is accepted by the client, sync page URLs", "…sync QwilrPay details", "When a page is viewed, sync and update view count") by clicking the `+` on the card.
  6. Or build one: **When** = trigger on a Qwilr page (e.g. page is accepted); **Do this** = action in Salesforce (e.g. update opportunity amount). Add more actions with the `+` beneath the first.
  7. Choose which **templates** the automation applies to (Templates library → Automation icon on Salesforce-integrated **Opportunity** templates). "Automations must be turned 'ON' via the toggle in the automation library to be assigned to a template."
  8. Name and describe it, click **Create**.
  9. Track execution in the **Activity Log** (success vs. errored, "and to track if they will need manual updating").
- **data_flow:** Room/page event (viewed / first viewed / accepted / partially accepted / preview accepted / set live / revived live) → either (a) HTTP POST to the customer's `targetUrl` carrying the page's `metadata`, or (b) Qwilr's automation engine writes to the **Salesforce Opportunity** (amount, close date, custom fields, page URLs, QwilrPay details, view count) → rep inspects the **Activity Log**. Page status values sent to the CRM are a documented fixed set: `draft`, `live`, `partially accepted`, `accepted`, `declined`.
- **data_sources:**
  - Qwilr **webhook subscriptions** (event + `targetUrl` + `subscriptionId`)
  - Qwilr **page** records (status, `metadata`, URLs, view count, QwilrPay details)
  - **Salesforce Opportunity** object and its fields (amount, close date, custom fields, URL-typed fields)
  - Qwilr **Automations** library + **Activity Log**
  - Qwilr **template → automation** assignment
  - Other CRMs covered by sibling articles: HubSpot, Pipedrive, Microsoft Dynamics 365, Zoho CRM
- **apis_hit:**
  - `POST https://api.qwilr.com/v1/webhooks` — Create a webhook event subscription. Body: `event` (enum: `pageAccepted`, `pagePartiallyAccepted`, `pagePreviewAccepted`, `pageViewed`, `pageFirstViewed`, `pageSetLive`, `pageRevivedLive`), `targetUrl` (required). `201 Subscribed`.
  - `GET https://api.qwilr.com/v1/webhooks` — Get a list of all webhook subscriptions.
  - `DELETE https://api.qwilr.com/v1/webhooks/{subscriptionId}` — Cancel a webhook event subscription (`204 Unsubscribed`).
  - `GET https://api.qwilr.com/v1/users` — "Retrieve the list of users from your account, with roles and team names. Useful for specifying the `ownerId` when creating a page."
  - Public Salesforce REST surface is not documented on the Qwilr pages I read, so I make **no claims** about specific Salesforce endpoint/method pairs. The Qwilr→Salesforce write is a product feature; the buyer-visible integration is described only in prose.
- **automations:** This workflow *is* automation. Documented triggers and constraints:
  - Triggers: page viewed, page first viewed, page accepted, page partially accepted, page preview accepted, page set live, page revived live (the complete published event enum).
  - "Automations can be triggered when specific events happen on your Qwilr pages (like a page being accepted), and in response, perform actions in Salesforce, like updating opportunity fields or syncing line items."
  - Template scoping: "Automations can only be applied to **Opportunity** templates with Salesforce at this time."
  - "We only support syncing percentage discounts of line items because Salesforce doesn't have a concept of a fixed discount. The discount field needs to be enabled in Salesforce **if discounts are used on the Qwilr page**, otherwise, the automation will fail."
  - Known field-type trap: "the Currency field in Qwilr is a text value (e.g. USD), while fields of Currency type in Salesforce expect a numeric value."
  - URL clickability trap: "You will need to ensure that you map the page links (e.g. live link, collaborator link) to a field that is of URL type in SF rather than just a text field."
  - Amount is derived, not writable, for products: "the **Amount** field of an opportunity that has products is auto-calculated from the Product line items, and hence it cannot be updated. We suggest in this case, you sync the **Page Value** in the **update opportunity fields from the Page Details** action to a custom field as a workaround."
  - Custom-field visibility is a permissions problem, surfaced in the UI: "If your custom Opportunity fields aren't appearing in Qwilr, it's likely due to a permissions issue in Salesforce… ensure the Salesforce user who connected the integration has visibility on the custom fields."
- **features_tools:** Qwilr dashboard **Automations** sidebar; **Recommended Automations** cards; automation editor (**When** / **Do this**, multi-action via `+`); template **Automation** icon (lights purple when assigned); automation name + description; **Activity Log** with expandable per-automation detail; **Connect app** modal that deep-links to Integrations settings; Qwilr **Share** / collaborator link sources.
- **extensibility:** Webhooks are the general-purpose seam (7 event types, arbitrary `targetUrl`, `metadata` round-trip, `DELETE` to unsubscribe, documented 429 rate limiting). Native automations are the low-code seam, currently limited to Opportunity templates on Salesforce, with sibling articles for HubSpot / Pipedrive / Dynamics 365 / Zoho and for Zapier (`help.qwilr.com/category/658-zapier`) and Make. Hard constraints to respect: production-only, requires integration re-enable, percentage-only discounts, and Salesforce field-type compatibility.
- **sources:**
  - https://docs.qwilr.com/api-reference
  - https://help.qwilr.com/article/846-automations-salesforce
  - https://help.qwilr.com/category/836-automations
- **evidence:**
  - "`event` … `pageAccepted` `pagePartiallyAccepted` `pagePreviewAccepted` `pageViewed` `pageFirstViewed` `pageSetLive` `pageRevivedLive`" / "Creates a new webhook subscription to an event type. New events will be sent to the defined `targetUrl`."
  - "With Qwilr's Salesforce Automations, you can automate actions between your Qwilr Pages and Salesforce CRM. Automations can be triggered when specific events happen on your Qwilr pages (like a page being accepted), and in response, perform actions in Salesforce, like updating opportunity fields or syncing line items."
  - "Each **Automation** you create can apply changes to your **Opportunity Record**… **When**: The trigger event on your Qwilr page (e.g., page is accepted). **Do this**: The action to perform in Salesforce (e.g., update opportunity amount)."
  - Presets: "When a page is accepted by the client, sync page URLs"; "When a page is accepted by the client, sync QwilrPay details"; "When a page is viewed, sync and update view count".
  - "You can track the status of your Automations via the Activity Log. You can use this to track if your Automation has been successful or has errored, and to track if they will need manual updating."
  - "Here is a list of Page Statuses Qwilr will send to your CRM: draft; live; partially accepted; accepted; declined"
  - "Please also note that we only support Salesforce automations in production. Automations cannot be set up in a sandbox environment."
  - "**Note**: Automations must be turned 'ON' via the toggle in the automation library to be assigned to a template."

---

## 18. White-label rooms on a custom domain

- **name:** White-label rooms on a custom domain
- **user_flow:**
  1. In your DNS host, add a **CNAME** record pointing to `custom-domains.qwilr.com` (Name: `proposals`; Type: CNAME; Value: `custom-domains.qwilr.com`).
  2. Wait for the CNAME to propagate ("It may take up to 24 hours").
  3. In the Qwilr dashboard: bottom-left sidebar → **Account Settings** → **Custom Domain**.
  4. Enter the domain — "It needs to be in a subdomain format", e.g. `proposals.acme.com` (not `acme.com`).
  5. Qwilr "will verify that the domain is available to use" → click **Save Custom Domain**.
  6. Optionally also set colours/fonts in **Brand Setup** and TypeKit or custom fonts.
  7. If using Cloudflare, set the CNAME to **DNS only** (turn off proxying) "in order to prevent the slow loading of your Qwilr pages."
- **data_flow:** Customer DNS CNAME → Qwilr custom-domain verifier → domain bound to the account → **all page links switch host** from `pages.qwilr.com` / `<account>.qwilr.com` to the customer's subdomain, with the page slug preserved and a mandatory security suffix appended: `proposals.acme.com/Proposal-Name-aB3xY9zK1q`. Existing links on the old host keep working. Re-pointing the custom domain later breaks previously shared links: "if you wish to change your custom domain again, all shared links would need to be reshared otherwise the links will appear broken."
- **data_sources:**
  - Customer **DNS host** (CNAME record)
  - `custom-domains.qwilr.com` (Qwilr's custom-domain target)
  - Qwilr **Account Settings → Custom Domain** (stored domain)
  - Qwilr **link secret** (per-page, non-removable URL suffix — "to the end of every page URL for security purposes")
  - Qwilr **brand settings** (colours, fonts, Adobe TypeKit)
- **apis_hit:** No public REST endpoint for custom domain or brand settings. The only documented URL-level guarantees are in the page API:
  - `GET /v1/pages/{pageId}` / `PUT /v1/pages/{pageId}` operate on `pageId` (pattern `^[a-z0-9]{24}$`), so host changes are transparent to API consumers.
  - A separate subdomain product exists for lower tiers: "Setting up a Custom Subdomain" (`help.qwilr.com/article/62-custom-subdomain`).
- **automations:**
  - **DNS-based activation:** the CNAME propagation delay is the only "job"; "You can still share the default **pages.qwilr.com** links during that time, and they'll continue to work."
  - **Automatic link-secret injection:** "Qwilr automatically appends a unique, non-removable identifier (*link secret*) to the end of every page URL for security purposes… This applies even after a custom domain is configured."
  - **Collaboration URLs bypass the custom domain:** "**Collaborator** page URLs won't use the custom domain, as they are for internal use only."
  - **Liferay's analogue** (different surface): a room's **Friendly URL** — "Determines the URL for this room. Changing it changes the room's address, so update any links that point to the old one."
- **features_tools:** DNS CNAME form; **Account Settings → Custom Domain**; domain-availability verification; **Save Custom Domain**; **Brand Setup** (colours & fonts); **Custom Subdomain**; Adobe TypeKit Fonts; Custom fonts; per-domain-provider DNS instruction links; Cloudflare proxying caveat; Qwilr's non-removable link secret.
- **extensibility:** Explicitly stated limit: "It does not produce fully custom slugs" — the vendor keeps a random suffix for share-link security, so white-labelling covers host + styling but not the final URL token. Documented cross-tenant hazard: changing the custom domain again invalidates all previously shared links, so a room product needs a domain-change migration path. Liferay adds the complementary primitives: **Friendly URL** for the readable slug plus **External Reference Code** as the stable integration key ("The *External Reference Code* is the key other systems use to find this room. Changing an existing value changes what those systems must send to reach the room. Confirm the new value with whoever maintains those systems before you save."), plus **Name**, and a read-only **Site ID**.
- **sources:**
  - https://help.qwilr.com/article/63-custom-domain
  - https://help.qwilr.com/category/544-setting-up-your-branding
  - https://learn.liferay.com/w/digital-sales-room/managing-rooms
- **evidence:**
  - "you can set up a Custom Domain so that your Qwilr Pages have a URL on your own domain — like **proposals.acme.com/xyz** (instead of qwilr.com/xyz or acme.qwilr.com/xyz)."
  - "A custom domain changes the domain portion of your Qwilr Page links… It does not produce fully custom slugs — Qwilr automatically appends a unique, non-removable identifier (*link secret*) to the end of every page URL for security purposes, e.g. proposals.acme.com/Proposal-Name-aB3xY9zK1q. This applies even after a custom domain is configured."
  - "add a new CNAME record and have it point to **custom-domains.qwilr.com**." / "It may take up to 24 hours for the CNAME redirect to fully work."
  - "Once you've added it, Qwilr will verify that the domain is available to use. Then click **Save Custom Domain.**"
  - "If you've previously shared any Qwilr Pages using our standard domain **pages.qwilr.com**, those links will continue working after your custom domain takes effect. However if you wish to change your custom domain again, all shared links would need to be reshared otherwise the links will appear broken."
  - "If you're using **Cloudflare**, make sure to turn off proxying for the CNAME entry and set it to **DNS only** in order to prevent the slow loading of your Qwilr pages."
  - Liferay: "**External Reference Code** — Enter a unique key for referencing the room from other systems." / "**Friendly URL** — Determines the URL for this room. Changing it changes the room's address, so update any links that point to the old one." / "**Site ID** — Shows Liferay's read-only identifier for the room's site."

---

## Gaps — things I could NOT fill from primary sources

1. **No vendor documents a "mutual action plan" / "mutual business plan" object.** Every vendor blog I found discussing MAPs is marketing content, not product documentation, and the closest documented artefacts are (a) Liferay's `Timeline Block` — "Displays a numbered sequence of steps, each with a secondary line for an estimate… *Current Step* marks how far the deal has progressed" (static markup, no task ownership, due dates, or buyer-acknowledgement) — and (b) Qwilr's `Accept Block` (sign-off on a document, not on a plan). **No primary source documents per-milestone owner, due date, completion state, or buyer sign-off on a shared plan.** Treat MAP/MBP as an unsourced design requirement.
2. **Interactive deck behaviour is unsourced.** Seismic's Content Search returns `pageThumbnailUrls` ("an array of page specific thumbnails") and `applicationUrls`, but no documented API exposes per-slide/per-page view telemetry, dwell time, or a slide-level model. Liferay ships a `PDF Preview Block` and a `Document Gallery Block` ("Each card shows a preview, the document's title, and a badge naming its file type") — a preview, not an interactive deck. Qwilr documents *embedding* third-party decks (Google Slides, SlideShare, Prezi) and explicitly warns that **embedded content does not track**: "Qwilr pages that are embedded in other websites do not track analytics/page views consistently. If you want to track analytics from other locations to your Qwilr page, you will want to set them up as external hyper-links to open in new tabs rather than embed them." So: web-based interactive presentation with per-slide tracking is **not documented anywhere I could reach**.
3. **Native video hosting is unsourced.** Liferay's `Video Block` embeds by URL ("URL points to the video and Width and Height set the player's dimensions. Autoplay is off by default") — hosting and transcoding are not part of the room. Qwilr's embedding catalogue covers TwentyThree (`/article/694-embedding-videos-from-twenty-three`), Google Slides, iFrame embeds, and an Embed Widget — all third-party embeds, not native hosting. No vendor API I read accepts a video upload for a room.
4. **Mobile responsiveness is not documented as a feature anywhere.** The only mobile-related evidence is observational, from Qwilr Analytics: "The visitor's location. The device and browser they're using. Mobile **Devices** will either list as Android or iOS; Desktop/Laptop **Devices** will either list as Apple or Windows." That is a report field, not a responsive-layout guarantee. No vendor doc I read states a breakpoint strategy, offline behaviour, or native mobile app for a room.
5. **Liferay's engagement backend is opaque.** LDP metrics are a hard prerequisite ("Every metric on this page requires that connection") but the query API, the metric definitions, the identity/visitor resolution, and the retention window are not published on the DSR pages. I could not find a LPD or Analytics Cloud query reference for DSR metrics.
6. **Liferay publishes no DSR REST/GraphQL API.** Room create/edit/archive/restore, Share-dialog invites, Who Has Access, and role grants are all documented **UI-only** ("You must activate the Digital Sales Room before managing rooms"). The only documented machine surfaces are the module identifiers (`com.liferay.site.dsr.site.initializer_[version]`), the room's `Site ID`, and its `External Reference Code`. Contrast with Seismic and Qwilr, which both publish full OpenAPI references.
7. **Liferay's room-fragment set is fixed for a buyer.** The 11 room fragments and 10 analytics fragments are enumerated with no documented mechanism for a third party to register a new DSR fragment set. Fragment *sets* are described as shipped, and extensibility is asserted only for the console chrome ("Administrators use these fragments to customize the Digital Sales Room Management console"). I did not find a documented fragment-set extension point.
8. **Qwilr's security settings have no API.** Email verification, domain security, password protection, view limits, and link expiry are documented **UI-only** (Share pop-up). The public API's `PUT /v1/pages/{pageId}` exposes `published`, `expiry`, `paymentSettings`, `substitutions`, `blocks` — no `security` object. So an OSS implementation cannot assume programmatic control of access policy.
9. **Qwilr conditional content has no API.** The API reference contains no `/rules` or conditions endpoint; rules are configured in the template editor. Its interaction with the API's full-content replacement (`PUT /v1/pages/{pageId}` with `blocks`, "only pages created via the saved-block method are supported") is undocumented, and the Saved-Block lossiness is documented but not its API equivalent.
10. **Qwilr webhooks are outbound only.** No inbound webhook, and no documented retry/signature/verification scheme for the `targetUrl` POSTs. The event enum has no `pageDeclined` / `pageExpired` / `pageArchived` / `viewLimitReached` event, so expiry-driven decline (a status change the UI performs automatically) is only observable by polling `GET /v1/pages?status=…`. Whether payloads are signed is not stated.
11. **Salesforce/CRM endpoints are unsourced.** Qwilr's automations are described in prose only ("update opportunity amount", "update opportunity close date", "update opportunity fields from page details"); no Salesforce REST method/URL pair appears on the pages I read, and I deliberately did not infer one. I also did not read the Salesforce/HubSpot template-token articles, the Zapier or Make app docs, or the Dynamics/Pipedrive/Zoho automation articles — so their trigger/action vocabularies are unverified.
12. **Seismic's `GetContentSyncStatus`, `PUT` add-new-version, `PUT` submit-into-workflow, `PUT` unpublish, and `GET` get-particular-workflow were referenced by name/verb in the docs I read but have no reference page I fetched.** I have their existence and (in the workflow case) their response shape, but not their full request schemas.
13. **Seismic reporting is 24h-stale by contract and explicitly non-interactive** ("updated no less than every 24 hours"; "not designed to be used in high-frequency, interactive use cases"). Any near-real-time room dashboard cannot be built on it — which is exactly why Liferay routes DSR metrics through LDP instead. Worth flagging as an architectural constraint.
14. **Only one vendor documents a DSR at all.** Liferay is the sole source I found with a real, documented Digital Sales Room product. Seismic and Qwilr document adjacent capabilities (content library; proposal/room pages) that *supply or stand in for* a room. There is no public, developer-documented DSR API from Dock, Consensus, Showboard, Highspot, Mindtickle, Trumpet, GetAccept, Akita, Distribute, or Showboard that I could reach — their sites expose marketing pages and gated sales content only.
