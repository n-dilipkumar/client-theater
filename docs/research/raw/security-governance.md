# Domain 5 — Security, Access Control & Governance

Raw primary-source research for an open-source Digital Sales Room.

**Method note.** Every workflow below is traced to a vendor product doc, API reference, or machine-readable
OpenAPI spec that was actually fetched. The primary spine is **Papermark** (open-source dataroom / document
sharing platform with a public REST API, CLI, MCP server and published OpenAPI spec) because it is the
closest public analogue to a Digital Sales Room. Secondary sources: **PandaDoc** and **Dropbox Sign** for
identity verification, agreement expiry and tamper-evident audit trails; **Microsoft Purview** for
identity-bound dynamic watermarks and usage-right download controls; **Microsoft Clarity** for consent-gated
session recording; **WorkOS** for staff SSO + SCIM; **Seismic** and **Highspot** for enterprise role scoping
and data residency claims.

UI surfaces are named only where the source names them. Where a surface is inferred from API semantics
(there is no vendor UI to read), it is explicitly marked `[inferred]`.

---

## 1. Gate each buyer link with a password, an expiry and email verification

- **name:** Gate a buyer link with password + expiry + email verification
- **user_flow:**
  1. Rep opens the team's document library (Papermark dashboard) or runs the CLI.
  2. Rep opens the link-creation surface and sets: a password, an `expires_at` timestamp, and email gating.
  3. For a higher-assurance deal, the rep turns on **email authentication** instead of plain email gating, so the buyer must prove ownership of the inbox with a one-time code.
  4. Rep saves; Papermark mints a public view URL and returns the link id + URL.
  5. Buyer opens the URL. Papermark serves an email-entry step, then a password step, then the document inline.
  6. After `expires_at`, the URL returns a friendly "this link has expired" page; the document itself is not deleted.
- **data_flow:** Password hash + expiry timestamp + gate flags are written onto the `Link` row at creation. On each viewer request the browser-supplied email is collected, a one-time code is dispatched to that address, the code is verified server-side, then the password is compared; only then is the document content streamed to the viewer. The verified email is persisted as a `Visitor` row (`email`, `verified: true`) and stamped onto every subsequent view event.
- **data_sources:** Papermark `Link` table; `Visitor` table (rows in the Viewer table); S3 (document bytes); transactional email provider for the one-time code `[inferred — the docs name the OTP but not the delivery vendor]`; client browser.
- **apis_hit:**
  - `POST /v1/links` — create the gated link (fields: `document_id` | `dataroom_id`, `password`, `expires_at`, `email_protected`, `email_authenticated`).
  - `PATCH /v1/links/{id}` — rotate password or change expiry in place.
  - `DELETE /v1/links/{id}` — revoke.
  - `POST /v1/documents/upload-url` → `PUT` to S3 → `POST /v1/documents` — the document upload flow.
  - `GET /v1/visitors/{id}` — read back the persisted, verified buyer identity.
- **automations:**
  - Expiry is evaluated on every viewer request: "After `expires_at`, the URL returns a friendly 'this link has expired' page."
  - Link deletion takes effect immediately: "Works immediately. Anyone with the URL gets the expired page on their next request."
  - `enable_notification` defaults to on, so the team is notified on each view of the link.
- **features_tools:** Link creation settings panel (password, expiry, email protection, email authentication) `[inferred from the documented field set]`; viewer-side email prompt, password prompt and in-viewer document view; Papermark CLI `papermark links create --password --expires --email-protected --email-authenticated`; CLI `links update` with tri-state `on|off` booleans; OpenAPI spec `https://www.papermark.com/docs/openapi.json`.
- **extensibility:** Three documented integration surfaces share one token model — REST API, `papermark` CLI (npm) and `@papermark/mcp-server` (stdio + remote) — so an agent or CI pipeline can create identical gated links. Link **presets** (`preset_id`) let a platform seed every link from a governed baseline: "Every preset-controlled field becomes the default; any field you also pass explicitly overrides the preset. Covered fields: `password`, `expires_at`, `email_protected`, `email_authenticated`, `allow_download`, `allow_list`, `deny_list`, `enable_watermark`, `watermark_config`, `enable_screenshot_protection`, `enable_confidential_view`, `enable_agreement`, `agreement_id`, `welcome_message`, `enable_notification`, `show_banner`, and `custom_fields`." Verified custom domains (`domain` + `slug`) put the room on the seller's own hostname.
- **sources:**
  - https://www.papermark.com/docs/llms.txt
  - https://www.papermark.com/docs/guides/share-password-protected-link.mdx
  - https://www.papermark.com/docs/cli/commands/links.mdx
  - https://www.papermark.com/docs/openapi.json
- **evidence:**
  - CLI flag table: "`--email-authenticated` | off | Viewer must verify their email with a one-time code (stronger than `--email-protected`)"; "`--email-protected` | off | Viewer must enter their email before viewing"; "`--expires <iso>` | never | ISO 8601 datetime".
  - Guide, "What the recipient sees": "1. They open the URL. 2. If `email_protected: true`, they enter their email. 3. They enter the password. 4. They see the document inline (no download by default…)".
  - OpenAPI `CreateLinkRequest`: `email_protected` (boolean, default `true`), `email_authenticated` (boolean, default `false`), `expires_at` (nullable date-time, "Expiry timestamp. Pass null to override a preset's expiry with none."), `password` (nullable string, minLength 1).

---

## 2. Allow or deny specific emails and domains on a link

- **name:** Allow/deny specific emails and domains on a link
- **user_flow:**
  1. Rep opens the link's access-controls panel.
  2. Rep enters an allow list of buyer emails/domains (e.g. `@acme.com,bob@x.com`); everyone else is blocked.
  3. Optionally rep enters a deny list (e.g. `@rival.com`) to keep named competitors out even if the link is forwarded.
  4. Rep saves. Papermark evaluates both lists against the email the buyer supplied at the gate.
- **data_flow:** Two string arrays are persisted on the `Link` row. At viewer entry, the submitted email is normalized and matched against `allow_list` (fail-closed: no match ⇒ no view) and `deny_list` (match ⇒ no view). Domain entries accept bare (`acme.com`) or `@`-prefixed forms on the group-equivalent surface; the link lists are plain strings matched the same way `[inferred — the CLI documents comma-separated emails/domains; exact normalization is documented for group `domains` but not repeated for link lists]`.
- **data_sources:** Papermark `Link.allow_list` / `Link.deny_list`; the buyer-entered email captured by the `email_protected` gate.
- **apis_hit:**
  - `POST /v1/links` — set `allow_list: string[]`, `deny_list: string[]` (both default `[]`).
  - `PATCH /v1/links/{id}` — change the lists without re-notifying viewers ("The URL stays the same; viewers don't need to be re-notified").
  - `GET /v1/links/{id}` — read current lists back.
- **automations:** Enforced server-side on every viewer request; no scheduled job. Because the lists are read at request time, a rep can widen or narrow the audience without minting a new URL.
- **features_tools:** Link access-controls panel (allow list / deny list) `[inferred from field set]`; viewer email gate; CLI `--allow-list` / `--deny-list` (comma-separated); same behaviour exposed on `create_link` / `update_link` MCP tools.
- **extensibility:** Feed the lists from the CRM — for example, sync the allow list from the opportunity's contact roles at mint time, or use a **group** instead (workflow 7) so the audience and its permissions are reusable objects rather than per-link strings. Presets can carry both lists as a governed default.
- **sources:**
  - https://www.papermark.com/docs/cli/commands/links.mdx
  - https://www.papermark.com/docs/openapi.json
  - https://www.papermark.com/docs/llms.txt
- **evidence:**
  - CLI: "`--allow-list <items>` | none | Comma-separated emails/domains allowed to view, e.g. `@acme.com,bob@x.com` (everyone else is blocked)"; "`--deny-list <items>` | none | Comma-separated emails/domains blocked from viewing, e.g. `@rival.com`".
  - OpenAPI: `allow_list` (`type: array, items: {type: string}, default: []`), `deny_list` (same shape).
  - Guide: the same lists are covered by `preset_id` seeding, "Plan entitlements are still enforced on the resulting settings."

---

## 3. Require NDA acceptance before the buyer sees anything

- **name:** Require NDA acceptance before viewing
- **user_flow:**
  1. Rep creates/selects an Agreement (NDA) object in the dataroom.
  2. On the link's access-controls panel, rep enables the agreement gate and selects the agreement id — the docs call the flag `enable_agreement` / `agreement_id`, described in the spec as "Require viewers to accept an agreement (NDA) before viewing."
  3. Buyer opens the URL and, before any document renders, is shown the NDA and must accept it.
  4. Only after acceptance does the room's content load.
  5. Rep can later turn the gate on/off with `links update --enable-agreement on|off --agreement-id <id>`.
- **data_flow:** The gate flag + agreement reference live on the `Link` row. On viewer entry, the agreement body is served instead of room content; the buyer's acceptance is recorded against the viewer's session `[inferred — the docs state the gate and the id, but do not publish an acceptance-record endpoint]`, then room content is released. The gate is a property of the *link*, so multiple links can point at different NDAs for different buyers.
- **data_sources:** Papermark Agreement object (NDA text) `[inferred — the spec exposes only `agreement_id`, no Agreement resource is published]`; Papermark `Link` row; viewer session.
- **apis_hit:**
  - `POST /v1/links` with `enable_agreement: true` + `agreement_id`.
  - `PATCH /v1/links/{id}` — toggle the gate, set or clear `agreement_id`.
  - `GET /v1/links/{id}` — confirm the gate state.
- **automations:** None scheduled. The gate is a synchronous precondition in the viewer request path. The CLI convenience flag collapses the pair: "`--agreement <id>` | none | Require viewers to accept this agreement (NDA) before viewing. The single flag both enables the gate and sets the agreement id".
- **features_tools:** Link access-controls panel (NDA / agreement gate) `[inferred from field set]`; viewer-side agreement acceptance screen; CLI `--agreement <id>`, `--enable-agreement`, `--agreement-id`; `update_link` MCP tool.
- **extensibility:** Combine the NDA gate with a preset so every seller-generated link inherits the corporate NDA by default, and override per-deal. Because the gate is link-scoped, a platform can require an NDA for external audiences and skip it for internal-only rooms. Plan-gated: "Available on the Data Rooms plan and above."
- **sources:**
  - https://www.papermark.com/docs/openapi.json
  - https://www.papermark.com/docs/cli/commands/links.mdx
  - https://www.papermark.com/docs/llms.txt
- **evidence:**
  - OpenAPI `CreateLinkRequest.enable_agreement`: "Require viewers to accept an agreement (NDA) before viewing. Set together with `agreement_id`. Available on the Data Rooms plan and above." `agreement_id`: "ID of the Agreement viewers must accept. Required when `enable_agreement` is true."
  - CLI: "`--agreement <id>` … The single flag both enables the gate and sets the agreement id"; update table: "`--enable-agreement <on\|off>` | Toggle the agreement (NDA) gate. When turning on, set `--agreement-id` (or have one already on the link)".

---

## 4. Stamp a dynamic, per-viewer watermark on every page

- **name:** Stamp a dynamic per-viewer watermark
- **user_flow:**
  1. Rep enables the watermark on the link and sets the text, e.g. `Confidential — {{email}} — {{date}} — {{link}}`.
  2. Rep chooses tiling vs. a single anchored position, rotation (0/30/45/90/180), colour, font size (1–96) and opacity (0–1).
  3. Buyer opens the document. Papermark interpolates the tokens **per view** and renders the watermark on every page at view time.
  4. If the buyer's screenshot leaks, the stamped email/date/IP identifies the leak.
  5. Rep can later edit just the text (full config replace), or clear the config without disabling the toggle.
- **data_flow:** A `WatermarkConfig` object is stored on the `Link` row. At view time the renderer substitutes `{{email}}`, `{{date}}`, `{{time}}`, `{{link}}` and `{{ipAddress}}` with values bound to that specific view (viewer email, team-locale date/time, the link's public URL, the viewer's IP) and composites the overlay onto the rasterised page in the viewer. Nothing is burned into the stored PDF — the watermark exists only in the delivered view.
- **data_sources:** Papermark `Link.watermark_config` + `enable_watermark`; the viewer's email and IP (the same values used by the `email_protected` gate and the analytics pipeline); document bytes from S3.
- **apis_hit:**
  - `POST /v1/links` — `enable_watermark: true` + `watermark_config: WatermarkConfig`.
  - `PATCH /v1/links/{id}` — replace the config, or send `{"watermark_config": null}` to clear it without flipping the toggle.
  - `GET /v1/links/{id}` — the response "always includes `watermark_config` (or `null`) so you can render the current settings back to the user."
- **automations:** Interpolation is per-request, not scheduled. Plan entitlement is enforced at the API boundary: "The API returns `403 forbidden_plan_feature` on create/update when the team's plan does not cover it; existing links keep working after a downgrade and only re-trigger the gate when a request flips the toggle from off to on."
- **features_tools:** Watermark configuration panel (text with token picker, tiled toggle, 9 anchor positions, rotation, colour, font size, opacity) `[inferred from the documented config schema]`; CLI's seven `--watermark-*` flags, which "must be passed together — the CLI validates the set before contacting the API"; `create_link` / `update_link` MCP tools.
- **extensibility:** Two independent, documented watermarking models to choose from or combine:
  - **Server-side, per-viewer (Papermark):** identity tokens include the viewer's IP, which the Open-Source layer controls entirely.
  - **Client-app, identity-bound (Microsoft Purview dynamic watermarks):** "When a user accesses a file with this sensitivity label applied, by default, their Universal Principal Name (UPN) is dynamically inserted as a watermark on each page of the file… This watermark is highly visible when viewing the file on a device, and persists when printed, although not when exported. This watermarking is more secure than the standard content markings for a label, because the user can't easily manually remove or change it." Purview also sets the operational expectation: "we also recommend that the encryption option of **Allow offline access** is set to **Never**."
- **sources:**
  - https://www.papermark.com/docs/guides/watermark-link-views.mdx
  - https://www.papermark.com/docs/openapi.json
  - https://learn.microsoft.com/en-us/purview/encryption-sensitivity-labels
- **evidence:**
  - Guide: "Dynamic watermarks render the viewer's identity onto every page at view time — email, date, time, link, IP address — so a screenshot that leaks out of the deal carries the audit trail with it." Token table: "`{{email}}` The viewer's email (requires `email_protected`). … `{{ipAddress}}` The viewer's IP address." "Use `{{email}}` and `{{ipAddress}}` together to make leaks identifiable down to a single viewer." Defaults: "`Confidential` at `middle-center`, 45°, black, 24pt, 0.5 opacity".
  - OpenAPI `WatermarkConfig`: `text` (tokens), `is_tiled`, `position` (9-value enum), `rotation` (const 0/30/45/90/180), `color` (`^#([0-9A-Fa-f]{3}){1,2}$`), `font_size` (1–96), `opacity` (0–1) — **all seven required**.

---

## 5. Enforce view-only access and block bulk download

- **name:** Enforce view-only access and block bulk download
- **user_flow:**
  1. Rep leaves `allow_download` off (the documented default) on the link, so the viewer gets inline rendering with no save affordance.
  2. For a partner round, rep opens dataroom settings and turns on **bulk download**, which "Allow[s] viewers to download every document as a zip" — a deliberate, room-wide escalation.
  3. Item-level download is granted separately in the audience's permission set via `can_download` (see workflow 7), so download rights are per-item, not just per-room.
  4. Any download attempt that succeeds is recorded as a view event with `downloaded_at` and `download_type`.
- **data_flow:** Three independent controls compose: room-level `bulkDownload` setting, link-level `allow_download` boolean (default `false`), and per-item `can_download` in the permission table. The viewer service checks all three before serving file bytes; a successful file fetch writes a `View` row with `downloaded_at` and `download_type`, which is what the audit trail reads. The CLI documents the AND semantics explicitly: "`--download <on|off>` | off | Allow downloading (**also needs `--allow-download` on the link**)."
- **data_sources:** Papermark `Dataroom.settings.bulkDownload`; `Link.allow_download`; `PermissionEntry.can_download`; S3 file bytes; `View` rows (`downloaded_at`, `download_type`).
- **apis_hit:**
  - `PATCH /v1/datarooms/{id}` — set room settings (`bulkDownload`, `conversations`, `agents`).
  - `POST /v1/links` / `PATCH /v1/links/{id}` — `allow_download: boolean` (default `false`).
  - `PUT /v1/datarooms/{id}/groups/{gid}/permissions` — `can_download` per item.
  - `GET /v1/links/{id}/views` — read back download events.
- **automations:** None scheduled; enforcement is synchronous on the download path. Download events are captured automatically without seller action, so a leak-by-download is auditable.
- **features_tools:** Dataroom settings panel (Conversations / Agents / Bulk download) `[inferred from the documented settings triple]`; viewer's inline viewer with the download control hidden; CLI `datarooms update --bulk-download on|off`, `links create --allow-download`, and `groups permissions set --download on|off`.
- **extensibility:** Because download is a three-way AND, an open-source implementation can mirror it exactly: a room-level policy, a link-level policy, and per-item ACLs, all expressed as booleans on existing resources with no new API surface. Presets can lock `allow_download` to `false` by default so it must be explicitly overridden.
- **sources:**
  - https://www.papermark.com/docs/cli/commands/datarooms.mdx
  - https://www.papermark.com/docs/cli/commands/links.mdx
  - https://www.papermark.com/docs/openapi.json
- **evidence:**
  - Dataroom settings: "`--bulk-download on|off` | Allow viewers to download every document as a zip".
  - CLI link flags: "`--allow-download` | off | Viewer can download the file (**default is view-only**)".
  - CLI permissions: "`--download <on|off>` … Allow downloading (also needs `--allow-download` on the link)".
  - OpenAPI `View` schema: `downloaded_at` (nullable date-time), `download_type` (nullable string).

---

## 6. Apply confidential view and block screenshot / screen-record shortcuts

- **name:** Apply confidential view and block screenshot shortcuts
- **user_flow:**
  1. Rep enables **confidential view** on the link: only "a narrow band of each page at a time" renders sharp, the rest stays blurred.
  2. Rep also enables **screenshot protection**, which blocks "common screenshot / screen-recording shortcuts while viewing".
  3. Buyer scrolls the document; content only resolves inside the focus band, so a single screenshot cannot capture a whole page.
  4. Rep toggles either control later with `links update --confidential-view on|off` / `--screenshot-protection on|off` — the URL and existing viewers are unaffected.
- **data_flow:** Two booleans on the `Link` row. Confidential view is a *rendering* transformation applied in the viewer: the page band in focus is delivered sharp, the remainder is delivered blurred, so the full-resolution page never reaches the client for out-of-band regions. Screenshot protection is a *client input* control that intercepts capture and screen-recording shortcuts during the view session. Both are properties of the link, so a single change applies to every future view of that link.
- **data_sources:** Papermark `Link.enable_confidential_view`, `Link.enable_screenshot_protection`; document bytes from S3; client browser input events.
- **apis_hit:**
  - `POST /v1/links` — `enable_confidential_view: true`, `enable_screenshot_protection: true`.
  - `PATCH /v1/links/{id}` — toggle either flag in place.
  - Both flags are `preset_id`-coverable, so they can be governance baselines.
- **automations:** No scheduled jobs. Both controls are continuous for the life of the view session and are listed among the preset-controlled fields, so they are inherited by every link created from a preset.
- **features_tools:** Link access-controls panel (Confidential view, Screenshot protection) `[inferred from field set]`; CLI `--confidential-view`, `--screenshot-protection` (tri-state `on|off` on update); the in-viewer rendering and key-capture handling.
- **extensibility:** Complementary to watermarking rather than a replacement: workflow 4 identifies a leak after the fact, this one reduces what can be captured in the first place. Both are expressible in an open-source stack as (a) a viewport-driven tile/shard renderer for confidential view and (b) a `keydown`/`visibilitychange` capture guard plus a `blur` on out-of-viewport tiles for screenshot protection.
- **sources:**
  - https://www.papermark.com/docs/cli/commands/links.mdx
  - https://www.papermark.com/docs/openapi.json
  - https://www.papermark.com/docs/llms.txt
- **evidence:**
  - CLI create flags: "`--confidential-view` | off | Reveal only a narrow band of each page at a time; rest is blurred (anti-screenshot)"; "`--screenshot-protection` | off | Block common screenshot / screen-recording shortcuts while viewing".
  - OpenAPI `CreateLinkRequest`: `enable_screenshot_protection` (boolean, default `false`), `enable_confidential_view` (boolean, default `false`).
  - Guide text on presets lists `enable_screenshot_protection` and `enable_confidential_view` among the covered fields.

---

## 7. Scope visibility to an audience with per-item view and download permissions

- **name:** Scope visibility to an audience with per-item permissions
- **user_flow:**
  1. Rep opens the dataroom's **Groups** surface and creates a named audience (e.g. "Co-investors"), optionally listing email domains that count as members implicitly.
  2. Rep adds explicit member emails (up to 500 per call, idempotent, no invitation emails sent).
  3. Rep grants access item by item — documents and folders — with separate **view** and **download** flags. Anything without an entry is invisible to that audience.
  4. Rep mints one link scoped to the group. Papermark forces email protection on group links and admits only group members.
  5. Later changes to the group's members or permissions apply to the existing link immediately — "no re-sharing".
  6. For a one-off audience, the rep sets the same permissions directly on a single link instead of creating a group.
- **data_flow:** Four linked records: the group (`DataroomGroup`: `name`, `allow_all`, `domains[]`, `member_count`, `link_count`), its memberships (`DataroomGroupMember`, keyed by email), its per-item ACL (`DataroomGroupPermission` rows of `item_id` + `item_type` + `can_view` + `can_download`), and the group-scoped `Link` (`audience_type: "group"`, `group_id`). At viewer entry the requester's email is matched against memberships (explicit email, then domain, then `allow_all`); the resolved permission set filters the dataroom tree server-side before any bytes are sent. Ancestor folders of any visible item are auto-opened so the tree stays navigable.
- **data_sources:** Papermark `DataroomGroup`, `DataroomGroupMember`, `DataroomGroupPermission` (and the parallel `LinkPermission` set); `DataroomDocument` join rows (`ddoc_…`) and dataroom folder rows as the `item_id` targets; buyer-supplied email from the link's email gate.
- **apis_hit:**
  - `POST /v1/datarooms/{id}/groups` — create audience (`name`, `allow_all`, `domains[]` max 100).
  - `POST /v1/datarooms/{id}/groups/{gid}/members` — add members (`emails[]`, max 500, idempotent).
  - `DELETE /v1/datarooms/{id}/groups/{gid}/members/{mid}` — remove one member.
  - `PUT /v1/datarooms/{id}/groups/{gid}/permissions` — upsert per-item ACL (delta semantics, max 1000 entries).
  - `GET /v1/datarooms/{id}/documents` and `GET /v1/datarooms/{id}/folders` — resolve `item_id`s.
  - `POST /v1/links` with `audience_type: "group"` + `group_id` — the group link.
  - `PUT /v1/links/{id}/permissions` — one-off per-link ACL (full-replace).
  - `GET /v1/links/{id}/permissions` — read the link ACL.
- **automations:**
  - Group membership is re-evaluated on every view, so revoking a member or flipping a permission takes effect on their next request with no link reissue.
  - Ancestor-folder auto-visibility is applied server-side whenever an item becomes visible, so the folder tree never needs a manual fix-up.
  - Domain matching is implicit and continuous: "Domain members are implicit: anyone @sequoia.com counts without being listed."
- **features_tools:** Dataroom Groups surface (members, domains, `allow_all`, permissions grid); Dataroom folder tree; link creation with a group audience picker; CLI `datarooms groups create|members add|permissions set`, `links create --group`; MCP tools `create_dataroom_group`, `add_dataroom_group_members`, `set_dataroom_group_permissions`, `list_dataroom_documents`, `list_dataroom_folders`.
- **extensibility:** The two ACL scopes are deliberately distinct and their conflict is resolved by an explicit, documented rule, which is the pattern worth copying: group permissions use **delta** upsert semantics ("entries you send are upserted, entries you omit keep their current state"), link permissions use **full-replace** ("the payload is the complete desired state, items not listed lose their override, and an empty array clears everything"), and a group link rejects link overrides outright — "Rejected with `422` on links with `audience_type: "group"` — their group determines visibility; switch the link to `audience_type: "general"` first."
- **sources:**
  - https://www.papermark.com/docs/guides/share-dataroom-with-group.mdx
  - https://www.papermark.com/docs/cli/commands/datarooms.mdx
  - https://www.papermark.com/docs/cli/commands/links.mdx
  - https://www.papermark.com/docs/openapi.json
- **evidence:**
  - Guide: "A new group sees **nothing** until you grant permissions. Group links are always email-gated; a viewer must be a member (by email or domain) to get in, unless the group has `allow_all`." "Later changes to the group's permissions or members apply to the existing link immediately, no re-sharing."
  - OpenAPI `CreateDataroomGroupRequest.domains`: "Email domains whose addresses are automatically treated as members (e.g. `@acme.com` admits `jane@acme.com`). Accepts bare (`acme.com`) or `@`-prefixed (`@acme.com`) domains; both are lowercased and normalized to `@acme.com`. Duplicates are removed."
  - `AddDataroomGroupMembersRequest.emails`: "Viewers are created for unknown addresses; already-present members are skipped, so the call is idempotent. **No invitation emails are sent.**"
  - `UpdateDataroomGroupPermissionsRequest.permissions`: "Items not listed keep their current state (delta semantics — matching the dashboard). Ancestor folders of any item made visible are automatically set to `can_view: true` so the folder tree stays navigable."
  - `UpdateLinkPermissionsRequest.permissions`: "The complete desired permission state for this link (full-replace semantics…) An empty array clears all overrides, which hides every item on links relying on this permission set."
  - `PermissionEntry`: required `item_id`, `item_type` (`dataroom_document` | `dataroom_folder`), `can_view`, `can_download`; `additionalProperties: false`.
  - `DataroomGroup.allow_all`: "When true, anyone who passes the link's other access gates is treated as a group member — the email/domain membership check is skipped."

---

## 8. Review engagement: verification state, per-page dwell, geo, device and downloads

- **name:** Review who engaged, where and for how long
- **user_flow:**
  1. Rep opens the dataroom's **viewers** list to see one row per buyer email (`First Seen`, `Last Seen`), or filters to a single address.
  2. Rep pulls aggregate stats for the room (total views, unique visitors, time spent, per-page engagement), with `--since` / `--until` bounds in Unix ms.
  3. Rep drills into a single view event to get per-page dwell times plus the viewer's country/city and browser/OS/device.
  4. Rep lists view records for a specific link in reverse-chronological order to see `view_type`, `viewed_at`, `downloaded_at`, `download_type`.
  5. Rep reads the buyer's persisted `verified` flag to confirm the identity was actually proven (not merely typed in).
- **data_flow:** Every view writes a `View` row (link, document/dataroom, `viewer_email`, `view_type`, `viewed_at`, `downloaded_at`, `download_type`) and updates a `Visitor` row (email, `verified`, `dataroom_id`, `invited_at`, `total_views`, `last_viewed_at`). These are rolled into a Tinybird-backed analytics layer which serves aggregates and, per view, page-level `duration_seconds` plus `location{country,city}` and `client{browser,os,device}`. Anonymous views never tied to a `Visitor` record are still reachable per-link via `GET /v1/links/{id}/views`.
- **data_sources:** Papermark `View` and `Visitor` tables; Tinybird (analytics backing store, per the docs); viewer IP → geolocation provider `[inferred — the API returns `location.country`/`city` but names no vendor]`; client user-agent string.
- **apis_hit:**
  - `GET /v1/visitors` — paginated persistent visitors.
  - `GET /v1/visitors/{id}` — one visitor with `verified`, `dataroom_id`, `total_views`.
  - `GET /v1/visitors/{id}/views` — that visitor's view history.
  - `GET /v1/analytics/datarooms/{id}`, `GET /v1/analytics/documents/{id}`, `GET /v1/analytics/links/{id}` — aggregates.
  - `GET /v1/analytics/views/{id}` — per-view breakdown (page durations, location, client).
  - `GET /v1/links/{id}/views` — cursor-paginated view records, reverse-chronological.
- **automations:**
  - View capture is automatic: "Notify the team on each view of this link. Defaults to on when omitted."
  - Analytics are pre-aggregated, so polling is cheap — "They're cheap (cached aggregates) so polling every minute or two is fine."
  - Analytics endpoints carry a **stricter rate limit** than the rest of the API: "Subject to a tighter per-minute rate limit."
- **features_tools:** Dataroom viewers table; dataroom/document/link analytics dashboards; per-view drill-down with page-duration timeline; CLI `datarooms viewers`, `datarooms stats --since --until`, `views list --link <id>`; MCP `search_documents` / analytics tools.
- **extensibility:** Because webhooks are explicitly not yet shipped, the documented integration path is polling: "Webhooks are not part of the public API yet… Until then: Poll the analytics endpoints." The planned contract is spelled out for forward compatibility — events `document.viewed`, `document.downloaded`, `link.created` / `link.deleted`, `dataroom.member.added` / `dataroom.member.removed`, each "a signed POST with HMAC-SHA256, one event per request, exponential retries up to 24 hours."
- **sources:**
  - https://www.papermark.com/docs/cli/commands/datarooms.mdx
  - https://www.papermark.com/docs/cli/commands/links.mdx
  - https://www.papermark.com/docs/api/webhooks.mdx
  - https://www.papermark.com/docs/openapi.json
- **evidence:**
  - CLI: "Lists persistent visitors (one row per email) tied to this dataroom. Different from per-link views: a viewer who hits two links in the same dataroom shows up once here, but twice in `papermark views list`." `stats` is "rate-limited more strictly than the rest of the surface (per-token cap is lower than the default 60 RPM). Cache the response if you're polling."
  - `ViewAnalytics` schema: `viewer_email`, `viewed_at`, `page_durations[{page_number, duration_seconds}]`, `total_duration_seconds`, `location{country, city}`, `client{browser, os, device}`.
  - `Visitor` schema: `email`, `verified: boolean`, `dataroom_id`, `invited_at`, `total_views`, `last_viewed_at`.
  - API reference summary: "Per-view breakdown: Page-by-page duration, viewer location, and client info for a single view event. Backed by Tinybird — tighter rate limit."

---

## 9. Revoke access early and keep the audit row

- **name:** Revoke access early and keep the audit row
- **user_flow:**
  1. Deal goes cold, or a document is embargoed. Rep runs `papermark links delete <id>` (or `DELETE /v1/links/{id}`).
  2. The public URL stops resolving immediately; anyone holding it gets the expired page on their next request. The link row is retained for audit.
  3. To cut a whole audience at once, rep deletes the dataroom group — which also deletes its memberships, permissions and **every** link pointing at it.
  4. To stop one buyer, rep removes just that group membership, or flips the item's `view`/`download` flags off.
  5. To pull a single file without restructuring, rep detaches the document from the dataroom — the team-library document and its attachments to other datarooms are left intact.
  6. For irreversible content removal, rep deletes the dataroom; deletion cascades to every link and folder and is unrecoverable, while library documents survive.
- **data_flow:** Revocation is a soft delete on the link: the public URL ceases to resolve but the row is kept in the database for audit, and a custom-domain slug is freed by renaming it so the original can be reused. Group deletion is a hard cascade across memberships, permissions and links. Membership removal keeps the underlying visitor record — "The underlying viewer is kept — only their membership in this group is removed." Detach is a join-row delete. A frozen dataroom refuses attaches, detaches and moves.
- **data_sources:** Papermark `Link` (soft-delete flag), `DataroomGroup`, `DataroomGroupMember`, `DataroomGroupPermission`, `DataroomDocument` join rows, verified custom-domain slug registry.
- **apis_hit:**
  - `DELETE /v1/links/{id}` — soft-delete a share link.
  - `DELETE /v1/datarooms/{id}/groups/{gid}` — delete group + memberships + permissions + all its links.
  - `DELETE /v1/datarooms/{id}/groups/{gid}/members/{mid}` — remove one member.
  - `PUT /v1/datarooms/{id}/groups/{gid}/permissions` — hide an item (`view off`, `download off`).
  - `DELETE /v1/datarooms/{id}/documents/{ddId}` — detach a document from one dataroom.
  - `DELETE /v1/datarooms/{id}` — delete the dataroom (cascades to links and folders).
- **automations:** Revocation is request-time, not scheduled — in-flight viewers are cut on their next request. There is no grace period and no cached-copy recall: the guarantee is "the public URL stops resolving immediately."
- **features_tools:** Link list with revoke action; dataroom group list with destructive delete; document context menu (detach); CLI `links delete`, `datarooms groups delete`, `datarooms groups members remove`, `datarooms detach`, `datarooms delete`.
- **extensibility:** The soft-delete-with-retained-row design is the piece worth adopting for compliance: access is cut instantly while the evidentiary record survives. Note the documented irreversibility so the UI can gate it: group delete "matches the dashboard's behavior and cannot be undone", and dataroom delete "cascades to every link and folder and is unrecoverable; documents stay in the team library."
- **sources:**
  - https://www.papermark.com/docs/api/reference/links/v1/links/id/delete.mdx
  - https://www.papermark.com/docs/api/reference/datarooms/v1/datarooms/id/groups/gid/delete.mdx
  - https://www.papermark.com/docs/api/reference/datarooms/v1/datarooms/id/groups/gid/members/mid/delete.mdx
  - https://www.papermark.com/docs/cli/commands/datarooms.mdx
  - https://www.papermark.com/docs/llms.txt
- **evidence:**
  - API reference: "Revoke a share link (soft delete) — Soft-deletes the link. The public URL stops resolving immediately; **the row is kept in the database for audit.** If the link used a custom-domain slug, the slug is renamed so the original can be reused."
  - "Delete a dataroom group — Deletes the group, its memberships, its permissions, AND every share link pointing at it — active group links stop resolving immediately."
  - "Remove a group member — The underlying viewer is kept — only their membership in this group is removed."
  - Dataroom CLI: "`groups delete` … Active group links stop resolving immediately. This matches the dashboard and cannot be undone."; "`delete` … Deletion cascades to every link and folder and is unrecoverable; documents stay in the team library."
  - Attach endpoint: "The document must belong to the same team as the dataroom; **cross-team attaches are refused. Frozen datarooms refuse new attachments.**" (same freeze language repeated on detach and move).

---

## 10. Manage internal workspace roles and least-privilege integration scopes

- **name:** Manage internal roles and least-privilege integration scopes
- **user_flow:**
  1. Admin opens the workspace's member list and changes a member's role (built-in `Admin` / `Manager` / `Member` / `Collaborator`, or a workspace-defined custom role).
  2. System enforces guard rails: the workspace owner's role and the last admin's role cannot be changed; a `Guest` promoted to a non-`Collaborator` role is auto-upgraded to a Full seat.
  3. Integrator creates an API token with only the scopes the integration needs, chosen à la carte (e.g. `links.write` only, or `documents.read` + `analytics.read`).
  4. Admin wires directory-driven SSO so staff authenticate through the company IdP rather than local credentials.
- **data_flow:** Internal role changes are written to the workspace membership record and take effect on the member's next request; destructive/invalid transitions are rejected with a business-rule error. Machine access is separate and strictly scope-gated: every endpoint declares its required scope(s), and a request from a token missing that scope returns `403 forbidden` ("The token is valid, but doesn't have the scope the endpoint requires *or* isn't authorized to act on the team you're addressing"). Crucially there is **no implicit hierarchy**: "`documents.write` does **not** imply `documents.read`. Each scope is independent… it lets you mint write-only tokens for systems that push data in but shouldn't be able to read it back out (e.g., an ingestion worker)." Seismic documents the complementary rule for its enablement APIs: "Scopes are not intended to override a users defined permissions. For example, a business user cannot upload content to content manager when using an auth token with `seismic.library.manage` scope."
- **data_sources:** PandaDoc organization / workspace / member records and custom roles; Papermark API tokens and team membership; OAuth2 authorization server (PandaDoc `app.pandadoc.com/oauth2/authorize`, `api.pandadoc.com/oauth2/access_token`); Seismic auth tokens and user permissions; corporate IdP (SAML/OIDC metadata).
- **apis_hit:**
  - `PATCH /public/v1/workspaces/{workspace_id}/members/{member_id}/role` — change a member's role.
  - `GET /public/v1/members`, `GET /public/v1/users`, `GET /public/v1/workspaces` — enumerate memberships/roles (org-admin only).
  - `POST /public/v1/access-token` — OAuth2 code→token exchange (`read` / `write` scopes).
  - Papermark: any endpoint with `security: [{ bearerAuth: [<scope>] }]`; `403 forbidden` on scope miss.
  - Seismic: scopes of the form `seismic.<object>.<view|manage>`, e.g. `seismic.library.view` (read, and "Ability to download content is also included"), `seismic.delivery` ("Access to all delivery methods including email, generated livesend links, and custom delivery options").
- **automations:**
  - Role/scope checks are enforced on every request (no caching window documented).
  - Plan entitlement is enforced independently of scopes: enabling a gated feature returns `403 forbidden_plan_feature` on create/update, while existing links keep working after a downgrade.
  - Request throttling is a governance control with a reset signal: `429 rate_limit_exceeded` with `X-RateLimit-Reset` ("Your token has exceeded its per-minute budget").
- **features_tools:** Workspace members panel with role dropdown; developer dashboard API-token creation UI ("The dashboard's token-creation UI shows which endpoints each scope unlocks"); OAuth "Authorize Application" consent screen; Seismic auth-token configuration surface.
- **extensibility:** The pattern is directly portable: a per-endpoint scope declaration plus a closed error catalogue. Papermark additionally publishes machine-readable request schemas with `additionalProperties: false` on every `*Request` component, so "generated SDKs should already reject unknown fields client-side", and an error envelope whose `code` is byte-stable — "Safe to switch on." PandaDoc's own docs recommend delegating to a 21 CFR Part 11 mode for regulated workspaces, and gate its audit endpoint by role: "This endpoint is accessible to authorized workspace administrators only."
- **sources:**
  - https://developers.pandadoc.com/reference/changememberrole.md
  - https://www.papermark.com/docs/api/scopes.mdx
  - https://www.papermark.com/docs/api/errors.mdx
  - https://developer.seismic.com/seismicsoftware/docs/scopes-1
  - https://developers.pandadoc.com/reference/list-document-audit-trail.md
- **evidence:**
  - PandaDoc: "You must be an organization admin, a workspace admin, or hold a role with permission to edit member roles… The `role` field accepts either a built-in role name (`Admin`, `Manager`, `Member`, `Collaborator`) or the name of a custom role… The role of the workspace owner cannot be changed. The role of the last member with admin privileges in the workspace cannot be changed."
  - Papermark scopes: "Pick these à la carte for least-privilege tokens."; "`apis.read` / `apis.all` — Forward-compatible coarse grants."; "Don't request `*` or wildcards; they're not supported. The token endpoint will reject unknown scopes."
  - Papermark errors: "**HTTP 403.** The token is valid, but doesn't have the scope the endpoint requires *or* isn't authorized to act on the team you're addressing."
  - Seismic: "Scopes are defined in the following format: `seismic.object.permission`… The permission level is either **view** for read-only access, or **manage** for read/write access."

---

## 11. Require recipient identity verification before opening or signing

- **name:** Require recipient identity verification before open or sign
- **user_flow:**
  1. Sender sets `verification_settings` on a recipient — either at document creation (Create Document) or on a live document (Update Recipient).
  2. Sender picks a method: **passcode** (6–100 chars, at least one letter and one digit), **SMS one-time password** (E.164 number), **knowledge-based authentication** (public-record questions), or **ID check** (government-issued ID).
  3. Sender picks the moment: `verification_place: before_open` (before the recipient can *view*) or `before_sign` (signers only, before signing).
  4. Recipient is prompted, passes the check, then proceeds.
  5. Every attempt — pass or fail — is written to the document's audit trail.
- **data_flow:** Verification config is persisted on the recipient record. At the chosen gate, the recipient's identity is proven out-of-band (typed passcode, SMS code, KBA answers, or ID document) and the result is stamped onto the recipient's session; the document body is withheld until it clears. Both success and failure produce audit actions, so a rejected attempt is as visible as a successful one.
- **data_sources:** PandaDoc template/document + recipient records; recipient phone number (`+1555667890` E.164); public-record data source for KBA `[inferred — the docs name `kba_verification` as "identity questions generated from public records" but no vendor]`; ID-verification provider for `id_verification` `[inferred]`; PandaDoc audit trail store.
- **apis_hit:**
  - `POST /public/v1/documents` (create from template) — `recipients[].verification_settings`.
  - `PATCH` Update Recipient — add/change `verification_settings` on a live document.
  - `GET /public/v2/documents/{document_id}/audit-trail` — read the verification outcomes.
  - Dropbox Sign equivalents: `POST /v3/signature_request/send` and `/send_with_template` with `signers[].sms_phone_number` + `sms_phone_number_type: "authentication"`; or Admin Console "Signer authentication" with **Access code** and/or **Text message**.
- **automations:**
  - Verification is re-asserted by the gate on every attempt; there is no "verify once, remember forever" behaviour in these flows.
  - For the Dropbox Sign SMS path, the code is sent on request and "they can request the code to be sent again if needed."
  - Audit codes are emitted automatically per outcome, including failures (`47`–`54`, `69`, `70`).
- **features_tools:** Recipient settings panel in the PandaDoc editor (verification method + timing) `[inferred from the documented `verification_settings` object]`; signer-side verification screens; Dropbox Sign Admin Console → Settings → Signature Requests → "Signer authentication" with Access code / Text message toggles.
- **extensibility:** Two composable designs to borrow: (a) *method as a discriminated union on the recipient* (passcode / phone / KBA / ID) with an independent *timing* axis (`before_open` vs `before_sign`) — the same recipient object can be verified differently for viewing and signing; (b) Dropbox Sign's separation of **authentication** from **delivery** via `sms_phone_number_type`, so an SMS can be an auth factor, a delivery channel, or both. Dropbox Sign also publishes explicit integration guidance on who owns authentication: "Ultimately, however, you are solely responsible for making sure that your signer/end user authentication process is sufficient and complies with any and all applicable laws and regulations."
- **sources:**
  - https://developers.pandadoc.com/docs/enable-verification.md
  - https://developers.pandadoc.com/reference/list-document-audit-trail.md
  - https://developers.hellosign.com/docs/guides/sms-tools.md
  - https://developers.hellosign.com/docs/guides/app-approval/signer-auth.md
- **evidence:**
  - PandaDoc: "Add `verification_settings` to a recipient either at document creation… or on an existing document." Method table: `passcode_verification.passcode` ("It must be 6-100 characters with at least one letter and one digit"), `phone_verification.phone_number` ("must be in international format (e.g., `+1555667890`)"), `kba_verification.enabled` ("The recipient answers identity questions generated from public records"), `id_verification.enabled` ("The recipient verifies identity with a government-issued ID"). Timing: "`before_open` | Before the recipient can view the document | All recipients" / "`before_sign` | Before the recipient can sign | Signers only".
  - Audit action enum: "`47` | recipient verification with kba passed … `51` | recipient verification with kba failed … `69` | recipient verification with email otp passed | `70` | recipient verification with email otp failed".
  - Dropbox Sign: "The SMS authentication feature provides the option to include a unique SMS authentication code that is sent to the signer once the signature request is submitted. In order for the signer to open and complete their portion of the document, they'll need to provide the unique code sent via SMS."; "They can then select the 'Send code' button to receive a 6-digit code via text message… they can request the code to be sent again if needed."; "SMS authentication and SMS delivery cannot be tested in `test_mode`."

---

## 12. Export a tamper-evident audit trail with IP and verification outcomes

- **name:** Export a tamper-evident audit trail
- **user_flow:**
  1. Compliance lead opens the API dashboard or calls the audit endpoint for a given document.
  2. Server checks the caller's role — the endpoint is admin-only ("accessible to authorized workspace administrators only").
  3. Lead pages through results with `limit` / `offset` and reads per-entry: who (`user.id`, `user.email`), what (`action` code), when (`date_created`), from where (`ip_address`), and why (`reason`).
  4. For e-signature flows, the same evidence is also embedded in the final PDF as an audit-trail section, so it travels with the document.
  5. For periodic compliance exports, the lead requests CSV reports by date range and receives them by email.
- **data_flow:** Every document action is appended to an append-only audit store as `{id, user, action, reason, date_created, ip_address}`. `action` is a stable integer enum covering the full lifecycle (1 new document, 6 sent, 8 viewed, 12 forwarded, 13 expired, 18 completed manually, 43 declined, 47–54 verification pass/fail, 55–58 QES lifecycle, 69–70 email-OTP pass/fail). The API paginates the store; a sandbox key masks the IP (`"hidden"`). Dropbox Sign's model is complementary: the audit trail is "a detailed log of all events from creation to completion… embedded in the final PDF as additional pages", including a SHA-256 document hash and per-interaction signer IPs, and is "**Tamper-evident: any modification invalidates the audit trail**."
- **data_sources:** PandaDoc audit-trail store (per document); Dropbox Sign signature-request audit trail + SHA-256 document hash; signer IPs and user agents; mailbox of the reporting recipient for CSV delivery.
- **apis_hit:**
  - `GET /public/v2/documents/{document_id}/audit-trail?limit=&offset=` — paginated audit entries.
  - `POST /v3/report/create` — request CSV reports; `report_type` ∈ `user_activity` | `document_status` | `sms_activity` | `fax_usage`; `start_date` / `end_date` in `MM/DD/YYYY`, "The requested date range may be up to 12 months in duration, and `start_date` must not be more than 10 years in the past."
  - `GET /v3/signature_request/{id}` — read status/audit-adjacent state.
  - `GET /v3/signature_request/files` — retrieve the completed PDF **with** the audit-trail pages merged in.
- **automations:**
  - Report generation is asynchronous: "When the report(s) have been generated, you will receive an email (one per requested report type) containing a link to download the report as a CSV file."
  - Audit entries are written synchronously by the action itself; no separate audit job.
  - Dropbox Sign records a fixed event set automatically, including "Signer viewed | Timestamp, IP address, user agent", "Signer authenticated | PIN or SMS verification timestamp", "Request expired | Expiration timestamp", and "Signer delegated".
- **features_tools:** API-dashboard audit view; audit-trail endpoint; Dropbox Sign Admin Console Reports / CSV export; the merged audit-trail pages inside the downloaded PDF.
- **extensibility:** Two exportable shapes to support: a paginated JSON API keyed by document, and a date-ranged CSV report delivered out-of-band by email. The action enum is the critical extension point — a sales room that adds its own events (NDA accepted, link revoked, watermark toggled) needs a stable, additive integer vocabulary rather than free-text strings, because compliance reviews query by code.
- **sources:**
  - https://developers.pandadoc.com/reference/list-document-audit-trail.md
  - https://developers.hellosign.com/api/manual-reference-pages/glossary/security-compliance.md
  - https://developers.hellosign.com/api/report/create.md
- **evidence:**
  - PandaDoc: "Retrieves the full audit trail for a specified document. The audit trail includes detailed user actions such as sending, viewing, signing, and editing, along with metadata like timestamps, IP addresses, and user identity. **This endpoint is accessible to authorized workspace administrators only.**" Field doc: "The IP address from which the action was performed. If a sandbox API key is used, this will be `\"hidden\"`."
  - Dropbox Sign: "Every Signature Request generates an Audit Trail… It is embedded in the final PDF as additional pages and provides legal evidence of the signing process. Automatically generated and cannot be modified."; "**Tamper-evident: any modification invalidates the audit trail**"; "Includes SHA-256 document hash proving the document wasn't altered after signing"; "Captures signer IP addresses at each interaction."
  - Report create: "When the report(s) have been generated, you will receive an email (one per requested report type) containing a link to download the report as a CSV file."

---

## 13. Download the executed agreement from the e-vault, webhook-driven

- **name:** Download the executed agreement from the e-vault
- **user_flow:**
  1. Integrator creates a webhook subscription for `document_completed_pdf_ready` (via the developer dashboard, or `POST /public/v1/webhook-subscriptions`).
  2. All signers complete; PandaDoc generates the PDF and "securely saved" it to its e-vault, then fires the webhook.
  3. The handler extracts the document `id` from the payload and calls the protected download endpoint.
  4. If the file is still being produced, the endpoint returns `202` with a `Retry-After` header; the client retries.
  5. The retrieved PDF is a digitally sealed, verifiable artifact. Note the deliberate trade-off: the sealed PDF is immutable, whereas the plain download endpoint allows watermark customization.
- **data_flow:** Completion → async PDF generation → e-vault storage → `document_completed_pdf_ready` event → client `GET /download-protected` → binary PDF returned (`application/pdf`), or `202` + `Retry-After` while generation finishes. The sealed variant is byte-stable; the customizable variant is where a watermark or branding can be applied.
- **data_sources:** PandaDoc e-vault (generated final PDFs); document status store; webhook subscription record with a shared key `[inferred — the subscription model has a shared key per the docs index, e.g. "Update Webhook Subscription Shared Key"]`; the integrator's storage.
- **apis_hit:**
  - `POST /public/v1/webhook-subscriptions` — create subscription with `triggers: ["document_completed_pdf_ready"]`.
  - `GET /public/v1/webhook-subscriptions` / `GET|PATCH|DELETE /public/v1/webhook-subscriptions/{uuid}` — manage.
  - `GET /public/v1/documents/{id}/download-protected` — digitally sealed PDF (production key only; `401` on a sandbox key).
  - `GET /public/v1/documents/{id}/download` — plain PDF, supports watermarks/customization.
- **automations:**
  - The webhook is the trigger: "This webhook is triggered when a document has been completed and a PDF has been generated and securely saved to PandaDoc's e-vault."
  - `202` + `Retry-After` is a documented back-pressure signal: "The signed document file is not ready yet… Retry after the indicated number of seconds. No response body is returned."
  - Debug support exists: webhook history, per-event detail endpoints, and a dedup header `X-PandaDoc-Webhook-Event-Id` "to process each webhook notification once… even when PandaDoc retries delivery."
- **features_tools:** Developer dashboard → API Configuration → Webhooks (with a "Create webhook" flow and an events checklist including "PDF of completed document available for download"); Webhooks History tab; download buttons in the PandaDoc UI.
- **extensibility:** This is the reference pattern for a webhook-driven artifact pipeline: subscribe to the *ready* event rather than polling status, handle `202` with `Retry-After` as a first-class case, and dedupe on a delivery id. Environment gating is explicit and worth copying — the sealed endpoint is production-only, so sandbox-based integration tests must use the plain download endpoint.
- **sources:**
  - https://developers.pandadoc.com/reference/download-protected-document.md
  - https://developers.pandadoc.com/docs/how-to-download-completed-document.md
  - https://developers.pandadoc.com/reference/pdfofcompleteddocumentavailablefordownload.md
- **evidence:**
  - Reference: "Download a signed PDF of a completed document"; "🚧 Production key only — This endpoint only works with a Production key. You'll get a 401 Unauthorized error when trying to use a Sandbox key."; `202` description as quoted above; `429` → `throttled`.
  - Guide: "**For Protected PDF (recommended):** `/download-protected` — Returns digitally sealed PDF"; "**Note:** The `/download-protected` endpoint always returns the same digitally sealed PDF file, while `/download` allows for watermark customization."
  - Webhook reference: "This webhook is triggered when a document has been completed and a PDF has been generated and securely saved to PandaDoc's e-vault."

---

## 14. Expire an agreement and drive pre-expiry reminders

- **name:** Expire an agreement and drive pre-expiry reminders
- **user_flow:**
  1. Sender sets an expiry when sending (`expires_at`, an epoch timestamp) — supported on send, create_embedded, update, and unclaimed-draft create.
  2. Papermark-style equivalent for a shared room link: the seller sets `expires_at` on the link and the viewer's URL starts refusing access after the deadline.
  3. Signer sees the expiry date in the banner next to the required-field count, in their own timezone.
  4. System sends reminders **3 and 7 days** before expiry, and skips the reminder if the signer was already reminded within 24 hours.
  5. On expiry, unsigned signatures flip to `expired`; further signing attempts "receive an error stating that the signature request is closed." Completed signers stay `signed`.
  6. Notifications: non-embedded flows email every signer and the requester; embedded flows send a `signature_request_expired` event and **muting all email** ("Emails are muted in all embedded signing flows. Integrations using embedded signing must consume the `signature_request_expired` event.").
- **data_flow:** Expiry is a stored timestamp evaluated server-side. As the deadline approaches, a reminder scheduler fires 3- and 7-day notices (with a 24-hour dedupe window). At the deadline, each incomplete `signature` record transitions to `status_code: "expired"` and the overall request becomes a final state. Crucially the document **remains accessible** afterwards: "All parties to the signature request will still have access to the document including audit trail, similar to `declined` signature requests. They will not be able to sign or modify the signature request."
- **data_sources:** Signature-request record (`expires_at`, `signatures[].status_code`); signer email/preferred timezone; reminder scheduler state; Dropbox Sign event stream; Papermark `Link.expires_at` for the room-link analogue.
- **apis_hit:**
  - `POST /v3/signature_request/send` / `send_with_template` / `create_embedded`, `PUT /v3/signature_request/update`, `POST /v3/unclaimed_draft/create` — set `expires_at`.
  - `GET /v3/signature_request/{id}` — read `expires_at` and per-signature `status_code`.
  - `signature_request_expired` webhook event (embedded flows).
  - Papermark: `POST /v1/links` / `PATCH /v1/links/{id}` — `expires_at`.
- **automations:**
  - Reminder schedule: "Signature request reminder emails will be sent to the signer 3 and 7 days before the signature request expires, this is in addition to our other current automated reminders. If a signer was already reminded within 24 hours, we will skip the automated reminder."
  - Expiry sweep marks incomplete signatures `expired`; "Once a signature request has expired, it is considered to be in a final status like `declined` and `completed` signature requests."
  - Audit writes an `expired` audit event "with the expiration date listed along with all the signers who did not sign by the expiration date."
- **features_tools:** Send/expire settings in the signing request form; signer-app expiry banner; reminder email templates; the expired-notification email; the Events/callbacks surface for embedded integrators; Dropbox Sign Documents page and API Dashboard, which "you can filter by expired status".
- **extensibility:** The boundary rules are the transferable part: (a) only requests that *explicitly* set an expiry expire ("By default signature requests do not expire"); (b) validation is strict and normalized — "must be an integer epoch timestamp in seconds between 1-90 days in the future" and "will be rounded down to the nearest hour"; (c) expiry is a **closing** of the mutation path, not a deletion of the record, so the audit trail and read access survive. For room links, Papermark takes the same shape: the URL returns a friendly expired page and "The document itself isn't deleted; you can mint a new link any time without re-uploading."
- **sources:**
  - https://developers.hellosign.com/api/manual-reference-pages/expiration.md
  - https://developers.hellosign.com/docs/guides/sms-tools.md
  - https://www.papermark.com/docs/guides/share-password-protected-link.mdx
- **evidence:**
  - Dropbox Sign: "`expires_at` must be an integer epoch timestamp in seconds between 1-90 days in the future."; "`expires_at` will be rounded down to the nearest hour."; "Only signature requests that explicitly set an `expires_at` will expire. By default signature requests do not expire."; "During signing, the signer will see the signature request expiration date in the banner next to the number of required fields… If they attempt to sign the signature request past the expiration date, they will receive an error stating that the signature request is closed."; "**All parties to the signature request will still have access to the document including audit trail**, similar to `declined` signature requests."
  - SMS delivery reminders: "if enabled on your account, they will see the 3 and 7 day signature request reminders via text message as well."
  - Papermark: "After `expires_at`, the URL returns a friendly 'this link has expired' page. The document itself isn't deleted; you can mint a new link any time without re-uploading."

---

## 15. Verify and IP-allowlist inbound provider webhooks

- **name:** Verify and IP-allowlist inbound provider webhooks
- **user_flow:**
  1. Integrator registers a callback URL at the account level (`callback_url` on `/account`) or per API app (`callback_url` on `/api_app/{client_id}`), and it must be HTTPS.
  2. Provider POSTs events as `multipart/form-data` with the payload in a field named `json`.
  3. Handler acknowledges with HTTP `200` and the body `Hello API Event Received`; anything else is treated as a failed callback.
  4. Handler verifies authenticity three ways: source-IP allowlist from a published JSON range file, the `Content-Sha256` header (base64 SHA-256 of the JSON payload keyed with the API key), and recomputing the `event_hash` HMAC over `event_time` + `event_type`.
  5. Handler de-duplicates on event id, and tolerates the retry schedule (6 retries, 5 min → 20 h 15 min); after 10 consecutive failures the provider clears the callback URL.
- **data_flow:** Provider → HTTP POST (multipart) → handler validates IP against the published range list, validates `Content-Sha256`, recomputes `HMAC-SHA256(api_key, event_time + event_type)` and compares to `event_hash`, then parses the `json` field into `{event:{event_time, event_type, event_hash, event_metadata{related_signature_id, reported_for_account_id, reported_for_app_id}}, signature_request:{…}}`. Only then does the handler act. Filters key off `event_type` — `signature_request_all_signed` vs `signature_request_downloadable`, with the explicit warning that final document generation lags signing, so "If you plan to download the final files, wait for `signature_request_downloadable`."
- **data_sources:** Dropbox Sign event payloads; published webhook IP range JSON (`https://dropbox-sign-api-config.s3.amazonaws.com/ip-ranges.json`, "automatically updated if the IP addresses change"); the account's API key (HMAC secret); the handler's own dedupe store.
- **apis_hit:**
  - `PUT /v3/account` with `callback_url` — set the account callback.
  - `PUT /v3/api_app/{client_id}` with `callback_url` — set an app-scoped callback.
  - Inbound: `POST` to the callback URL (multipart/form-data, `json` field), headers `User-Agent: Dropbox Sign API` and `Content-Sha256`.
  - Machine-readable error catalogues in the OpenAPI spec: root `x-error-codes` (keyed by `error_name`, with `http_status`, `cause`, `remediation`, `retryable`, `backoff`) and `x-error-events` for asynchronous webhook errors.
- **automations:**
  - Retry ladder: "we will retry POSTing the event up to **6 times**, with each retry interval being longer than the previous one" — First 5 minutes, Second 15 minutes, Third 45 minutes, Fourth 2 h 15 m, Fifth 6 h 45 m, Sixth 20 h 15 m. "After **10 consecutive failures**, your callback URL will be automatically cleared."
  - 30-second provider timeout: "our requests will timeout after **30 seconds**, so callbacks will fail if your server takes longer than that to respond."
  - TLS enforcement with a hard date: "we will require callback URLs to use **HTTPS** starting **November 30, 2024**… Any callback URL not using HTTPS on December 1, 2024, will stop receiving Sign callback events."
  - SDK helpers (`EventCallbackHelper.isValid` in PHP/Java/Python/Ruby/JS/.NET) perform the same verification in one call.
- **features_tools:** Account/App settings pages with a **test** button that fires a synthetic event ("clicking the **test** button next to the field"); SDK event-callback helpers; the machine-readable `x-error-codes` / `x-error-events` catalogs for retry logic "instead of scraping the human-readable page".
- **extensibility:** Directly portable: publish an IP range file, sign payloads with a keyed HMAC over two stable fields (`event_time`, `event_type`), require a magic-string 200 acknowledgement, document the exact retry ladder and self-disable threshold, and ship SDK helpers plus machine-readable error catalogues. Papermark's own roadmap shows the same shape being adopted for its planned events — "Each delivery will be a signed POST with HMAC-SHA256, one event per request, exponential retries up to 24 hours."
- **sources:**
  - https://developers.hellosign.com/docs/guides/events-and-callbacks/walkthrough.md
  - https://developers.hellosign.com/llms.txt
  - https://www.papermark.com/docs/api/webhooks.mdx
- **evidence:**
  - "Every event payload contains an `event_hash` that can be used to verify the event is coming from Dropbox Sign… `echo -n $event_time$event_type | openssl dgst -sha256 -hmac $apikey`".
  - "`Content-Sha256` | A base64 encoded SHA256 signature of the request's JSON payload, generated using your API key. `echo -n $json | openssl dgst -sha256 -hmac $apiKey`".
  - "We have made a JSON file containing the full list of IP addresses that webhook events may come from available for download… We recommend checking this list periodically to ensure your callback handler is secure."
  - "the callback url must return an HTTP `200` with a response body that contains the string `Hello API Event Received`. If no response is received, Dropbox Sign considers that a failed callback and the event will be sent again later."

---

## 16. Record buyer sessions behind a consent gate, masked, IP-excluded and auto-purged

- **name:** Record buyer sessions behind a consent gate
- **user_flow:**
  1. Site owner instruments the room with the tracking snippet in `<head>`.
  2. Owner enables **cookie consent** for the project so the consent gate applies.
  3. On each visit, the owner's CMP fires and the page calls `window.clarity('consentv2', {ad_Storage, analytics_Storage})` with `granted` or `denied` before cookies are set.
  4. If granted, the session is recorded as a DOM/action replay and the owner can watch it in the **Recordings** tab, filter to a segment, label it, and share it (with an expiry for guest links).
  5. If denied, the tool switches to no-consent mode: no cookies, limited tracking, a unique ID per page view, and fragmented sessions.
  6. Owner configures masking so confidential content is never transmitted, and adds internal IP ranges under **Settings → IP blocking** so internal viewers are excluded from recordings and heatmaps.
  7. Recordings age out automatically; deletion is at project granularity.
- **data_flow:** Page DOM snapshots + user actions (mouse movements, clicks, scrolls) are captured asynchronously and shipped to the analytics service, which reconstructs a replay ("Clarity records all the page information a user sees (the DOM content) and the actions they take as they browse your site"). Consent state gates cookie setting: granted ⇒ first- and third-party cookies set and cross-session tracking enabled; denied ⇒ "Clarity deletes any existing cookie for the website, ends the current session, and restarts tracking in no-consent mode". Masking is applied before upload ("Is masked data uploaded to Clarity? No."). IP blocklist membership is evaluated at ingest: "No sessions from visitors on the list are recorded."
- **data_sources:** The buyer's browser DOM + interaction events; the visitor's IP (used both for blocklisting and for IP-based geolocation to decide which regions require consent); the CMP consent signal; first-party cookie for cross-session identity; the analytics backend.
- **apis_hit:**
  - `window.clarity('consentv2', { ad_Storage: 'granted'|'denied', analytics_Storage: 'granted'|'denied' })` — consent API v2.
  - `window.clarity('consent', false)` — erase cookies and start a new session.
  - No REST API is required for this flow; configuration is dashboard-driven, and the docs explicitly state "Microsoft Clarity doesn't support authentication via your company's AAD instance" (project membership is email-invite based, Admin vs Team member roles).
- **automations:**
  - **Consent enforcement is enforced server-side by region**: "Starting October 31, 2025, Clarity begins enforcing consent signal requirements for page visits originating from the European Economic Area (EEA), United Kingdom (UK), and Switzerland (CH)."
  - **Retention is automatic**: "The data is retained for the webmaster's consumption up to 30 days from the time of recording."; "Clarity retains recordings for 30 days from the time of recording. However, Favorite recordings and randomly selected sample of recordings are retained for up to 9 months."
  - **IP blocklist propagation**: "Wait about 15 minutes for the changes to come into effect."
  - Consent is also revocable at runtime: "When a user rejects the Clarity cookie, Clarity deletes any existing cookie for the website, ends the current session, and restarts tracking in no-consent mode."
- **features_tools:** Clarity dashboard — Settings → IP blocking (with "Block my current IP" and CIDR ranges, IPv4 only); Settings → masking mode (verified by a "Masking mode set" confirmation); Recordings tab with segments, labels (max 5 per recording), sharing (guest links expire, team links don't); consent mode / CMP integration (CookieYes etc.); an in-page JS console message to verify blocking: "Data from this session isn't being collected by Microsoft Clarity due to your configured project settings."
- **extensibility:** The consent-API contract is the piece to copy for any in-room analytics: a two-axis `granted|denied` decision (ads vs analytics storage) passed *before* cookie setting, plus a documented revoke call, plus region-scoped enforcement. Governance limits to be aware of when choosing a recorder: 100,000 sessions recorded per project per day, "you can't delete or download specific recordings", and per-user deletion requires deleting the whole project.
- **sources:**
  - https://learn.microsoft.com/en-us/clarity/clarity-consent-api-v2
  - https://learn.microsoft.com/en-us/clarity/ip-exclusion
  - https://learn.microsoft.com/en-us/clarity/faq
- **evidence:**
  - Consent API: "Starting October 31, 2025, Clarity begins enforcing consent signal requirements for page visits originating from the European Economic Area (EEA), United Kingdom (UK), and Switzerland (CH)."; "If consent is not granted, Clarity assigns a **unique ID per page view** and does not use cookies to persist session data."; "`consentv2` is the latest and recommended method… It replaces the [older Consent API] which is planned for deprecation."
  - IP blocking: "**Recordings:** No sessions from visitors on the list are recorded."; "**Heatmaps:** Site visitors on the list are excluded from having their activity included on click maps or scroll maps."; "Clarity only supports IPv4 addresses. We do not support IPv6 or dynamic IP addresses (for example, VPN)."; "To set up IP exclusion, you need to be an *administrator* for your project."
  - FAQ: "By default, Clarity suppresses the client's entire content. The website admin controls the content sent to Clarity, and website owners should use their dashboard settings to block confidential content."; "Clarity uses IP address-based geolocation to determine user location. Consent should be obtained for users in the EEA, UK, and Switzerland."; "you can't delete or download specific recordings"; "You need to delete the entire project to delete user's data."

---

## 17. Federate staff SSO and auto-provision / deprovision via SCIM

- **name:** Federate staff SSO and auto-provision via SCIM
- **user_flow:**
  1. IT admin connects the company's identity provider (SAML or OIDC) to the app; the app is configured with a redirect URI in the dashboard's **Redirects** tab.
  2. Staff hit the app's login surface, which redirects to the IdP using the organization's identifier (or a specific connection / provider).
  3. After IdP authentication the app exchanges the authorization code for a profile, then **validates the profile's organization id** before granting access.
  4. Separately, the admin enables Directory Sync: the directory provider is the source of truth, and user/group changes are pushed into the app.
  5. Joiners, movers and leavers are handled by the directory — a new hire is provisioned, a role change updates the account, a leaver is deprovisioned — with directory groups mapped to app access rules.
- **data_flow:** Staff request → authorization URL built with `organization` / `connection` / `provider` + `redirect_uri` → IdP authenticates → redirect back with a short-lived `code` ("The authorization code is valid for 10 minutes") → `getProfileAndToken(code)` returns a normalized + raw Profile → the app asserts `profile.organizationId` matches the expected tenant and only then creates a session. In parallel, the directory provider emits SCIM `Users` / `Groups` resources; the app receives them as Directory / Directory user / Directory group records and reconciles its own user table, so access is a function of directory state rather than manual admin action.
- **data_sources:** Corporate IdP (SAML/OIDC metadata, IdP-initiated `RelayState`); the app's user table; directory provider / HRIS behind SCIM (Okta, Microsoft AD, Workday, Google Workspace named as supported); directory groups used as access-rule inputs.
- **apis_hit:**
  - SSO authorization URL: `sso.getAuthorizationUrl({ organization | connection | provider, redirectUri, clientId })`.
  - Callback: `sso.getProfileAndToken({ code, clientId })` → `{ profile }`.
  - Directory Sync: "Directory updates can be delivered to you via **webhooks** or retrieved using the **Events API**"; admin objects Directory, Directory user, Directory group with configurable attributes; Admin Portal for self-service setup.
- **automations:**
  - Deprovisioning is the headline automation: "Deprovisioning is a process of removing a user from an app."; SCIM handles "Provisioning an identity for a user (account creation)", "When a user's attribute has changed (account update)", and "Deprovisioning a user from your app (account deletion)".
  - Group→rule mapping is continuous: "Directories enable IT contacts to activate and deactivate accounts, **create groups that inform access rules**, accelerate adoption of new tools."
  - Manual-drift risk is the stated motivation: "All future changes to this employee's data and access are manually entered by IT contacts. This is error-prone and can lead to security vulnerabilities where users get unauthorized access to resources."
- **features_tools:** App login surface; IdP connection settings; dashboard **Redirects** tab; Admin Portal (self-service Directory Sync + SSO setup); Test SSO page in staging ("Head to the *Test SSO* page in the WorkOS Dashboard to get started with testing common login flows"); directory-sync webhook endpoint / Events API consumer.
- **extensibility:** Copy the tenant assertion, not the email domain. The docs are explicit: "When adding your callback endpoint, it is important to always validate the returned profile's organization ID. **It's unsafe to validate using email domains as organizations might allow email addresses from outside their corporate domain (e.g. for guest users).**" Also support IdP-initiated flows: the customer "can specify a separate redirect URI to be used for all their IdP-initiated sessions as a `RelayState` parameter in the SAML settings on their side."
- **sources:**
  - https://workos.com/docs/sso
  - https://workos.com/docs/directory-sync/overview
- **evidence:**
  - SSO: "This service is compatible with any IdP that supports either the **SAML** or **OIDC** protocols."; "The authorization code is valid for 10 minutes."; "You can also use the connection parameter for SAML or OIDC connections… The provider parameter is used for OAuth connections"; "Multi-tenant apps will typically have a single redirect URI specified. You can set multiple redirect URIs for single-tenant apps."
  - Directory Sync: "A directory is the source of truth for your customer's user and group lists."; "**Directory group** — A collection of users within an organization who have been provisioned with access to your app. Directory groups are mapped from directory provider groups. Directory groups are most often used to categorize a collection of users based on shared traits."; "Directory updates can be delivered to you via webhooks or retrieved using the Events API. Your app stores a mapping between your customer and their directory."

---

## 18. Meet GDPR / CCPA: region residency, retention limits, DSAR and consent tooling

- **name:** Meet GDPR / CCPA: residency, retention, DSAR
- **user_flow:**
  1. Legal asks where deal-room content and engagement data are processed. Vendor answers with region-level controls: "Region-based data residency control" (Highspot), or the platform is pinned to a specific Azure region with a documented cross-border mechanism.
  2. Legal asks what personal data is collected about buyers. The answer covers engagement telemetry, the recorded fields (page dwell, country/city, device), and the fact that screen text is suppressed by default.
  3. Legal asks about retention. Answer: recordings "up to 30 days from the time of recording", with favourites and a random sample up to 9 months; heatmaps up to 9 months.
  4. Legal asks about cross-border transfers. Answer: for EU customers, contracting entity is Microsoft Ireland Operations Limited with SCCs to Microsoft Corporation.
  5. Legal asks for a data-subject request. Answer: DSAR tooling exists at the platform level; at the tool level, per-user deletion is coarse — "You need to delete the entire project to delete user's data."
  6. Legal asks about consent capture. Answer: explicit consent is required for EEA/UK/CH users and is enforced by the vendor from a fixed date; international transfer tools (GPC, opt-out registries) are honoured.
- **data_flow:** Residency is a deployment-time choice that fixes the jurisdiction of both content and engagement data. Retention is enforced by the analytics backend's ageing rules, not by user discipline. Consent is captured at the page (per visitor, per region), and a negative signal actively tears down cookies and the current session. DSAR fulfilment is a privileged, project-scoped administrative operation.
- **data_sources:** Vendor regional data stores (Highspot regions; Microsoft Azure for Clarity); buyer engagement telemetry; consent signals from the CMP and from GPC / DAA opt-out registries; corporate privacy-request tickets.
- **apis_hit:**
  - `GET /v1/analytics/views/{id}` and the other analytics endpoints — the personal-data surface subject to a DSAR (viewer email, country/city, client fingerprint, per-page dwell).
  - `GET /v1/visitors`, `GET /v1/visitors/{id}` — the identifiable-buyer surface (`email`, `verified`, `total_views`).
  - `window.clarity('consentv2', …)` — the consent capture call.
  - Highspot: "Real-time audit API integrations" and "DSAR & consent management tools" are named capabilities; no public endpoint is documented in the pages read.
- **automations:**
  - Consent enforcement by region and date: "Starting October 31, 2025, Clarity begins enforcing consent signal requirements for page visits originating from the EEA, UK, and Switzerland"; "for all incoming requests from end users located within the EEA, UK, and Switzerland and for websites targeting the EEA, UK, and Switzerland, Clarity webmasters need to convey end user consent status to Microsoft through the methods outlined in the Clarity client API documentation."
  - Retention ageing: "The data is retained for the webmaster's consumption up to 30 days from the time of recording."; "Clarity records up to 100,000 sessions per project per day."
  - Opt-out honoured automatically: "Clarity supports Global Privacy Control (GPC)" and the DAA opt-out list.
  - Revocation is immediate: `clarity('consent', false)` "clears the Clarity cookies from the user's browser and prevent further tracking until new consent is granted."
- **features_tools:** Regional tenant selection (Highspot region-based residency); Clarity consent-mode configuration + CMP integrations (CookieYes) + GCM; project-level deletion as the DSAR primitive; retention/purge notice in the dashboard; vendor certifications (SOC 2 Type II, ISO 27001, ISO 27701).
- **extensibility:** The controls an open-source room must ship: (1) a documented residency region per deployment; (2) hard retention limits with no longer-lived side channels; (3) a consent gate that fails **closed** (deny ⇒ unique ID per page view, no cookies) rather than merely degrading; (4) a DSAR path that can delete an identifiable visitor's records — which the tool-level behaviour highlights as the hard part, since per-subject deletion forced whole-project deletion; (5) role separation, since blocking changes are admin-only ("you need to be an *administrator* for your project").
- **sources:**
  - https://www.highspot.com/product/security/
  - https://learn.microsoft.com/en-us/clarity/faq
  - https://learn.microsoft.com/en-us/clarity/clarity-consent-api-v2
- **evidence:**
  - Highspot security page: "Highspot is certified to meet global standards including **SOC 2 Type II, ISO 27001, ISO 27701, GDPR, and the EU AI Act**."; features at a glance: "Customer-controlled encryption (HYOK)", "**Region-based data residency control**", "Role-aware access and encryption", "Metadata-based content governance", "**DSAR & consent management tools**", "Real-time audit API integrations".
  - Clarity FAQ: "Clarity is **GDPR-compliant as a data controller**."; "Clarity processes data in compliance with the **CCPA**."; "Your data is stored in the Microsoft Azure cloud service."; "Clarity customers in the EU are contracting with **Microsoft Ireland Operations Limited (MIOL)**, which has a special contract (SCCs) with Microsoft Corporation (in the United States) allowing cross-border data transfers between those affiliate entities."; "The data is retained for the webmaster's consumption up to 30 days from the time of recording."; "**You need to delete the entire project to delete user's data.**"; "Clarity doesn't currently respond to browser DNT signals."; "Why does Clarity require explicit consent in the European Economic Area (EEA), UK, and Switzerland? To comply with local regulations explicit user consent is required before placing cookies on their devices."
  - Consent API: "If consent is not granted, Clarity assigns a **unique ID per page view** and does not use cookies to persist session data."; "Without explicit consent, Clarity cookies can't be used. This means that some functionalities, like funnel tracking and session recordings, might be impacted."

---

## Source inventory (all fetched)

| Vendor / project | URLs read |
| --- | --- |
| Papermark (open-source dataroom) | `https://www.papermark.com/docs`, `/docs/llms.txt`, `/docs/openapi.json`, `/docs/guides/share-password-protected-link.mdx`, `/docs/guides/watermark-link-views.mdx`, `/docs/guides/share-dataroom-with-group.mdx`, `/docs/api/scopes.mdx`, `/docs/api/webhooks.mdx`, `/docs/api/errors.mdx`, `/docs/cli/commands/links.mdx`, `/docs/cli/commands/datarooms.mdx`, plus endpoint reference pages under `/docs/api/reference/**` |
| PandaDoc | `https://developers.pandadoc.com/llms.txt`, `/docs/enable-verification.md`, `/docs/how-to-download-completed-document.md`, `/reference/download-protected-document.md`, `/reference/list-document-audit-trail.md`, `/reference/changememberrole.md` |
| Dropbox Sign | `https://developers.hellosign.com/llms.txt`, `/docs/overview.md`, `/docs/guides/events-and-callbacks/walkthrough.md`, `/docs/guides/sms-tools.md`, `/docs/guides/app-approval/signer-auth.md`, `/api/manual-reference-pages/expiration.md`, `/api/manual-reference-pages/glossary/security-compliance.md`, `/api/report/create.md` |
| Microsoft Purview | `https://learn.microsoft.com/en-us/purview/encryption-sensitivity-labels` |
| Microsoft Clarity | `https://learn.microsoft.com/en-us/clarity/faq`, `/clarity/clarity-consent-api-v2`, `/clarity/ip-exclusion` |
| WorkOS | `https://workos.com/docs/sso`, `https://workos.com/docs/directory-sync/overview` |
| Seismic | `https://developer.seismic.com/seismicsoftware/reference/introduction-overview`, `/reference/scopes` → `/docs/scopes-1` |
| Highspot | `https://www.highspot.com/product/security/` |
