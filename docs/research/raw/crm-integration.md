# Domain 3 — CRM Integration & Data Synchronisation: 18 Verified Workflows

Research notes for an open-source Digital Sales Room. Every workflow below is reconstructed **only** from vendor
developer documentation that was actually fetched and read. Each `sources` block lists the exact URL read, and each
`evidence` line is a quote or a close paraphrase of that page. Where a workflow is grounded in one vendor and the
others could not be sourced from a readable primary page, that is stated explicitly in `gaps`.

**Vendors covered:** Salesforce (REST API, Composite/SObject Collections, Change Data Capture, Bulk API 2.0),
Microsoft Dataverse / Dynamics 365 (Web API OData, alternate keys, UpsertMultiple, $batch change sets, change
tracking), HubSpot (CRM v3 object APIs, Properties, Files, Exports, Events, Workflows webhooks, OAuth, limits).

**Note on source retrieval:** `developer.salesforce.com` blocks direct HTTP fetches (403) and several Salesforce
guides (Metadata API, Identity/OAuth flows, Pub/Sub overview, sandbox types) are client-rendered and returned only a
cookie banner / 404 shell. Those pages are therefore **not** cited below. Only Salesforce pages that returned real
body text through the read path are cited.

---

## 1. Connect a CRM org to the sales room (OAuth 2.0 authorization code)

- **name:** Connect a CRM org to the sales room
- **user_flow:**
  1. Admin opens the sales-room admin console → **Integrations → Add CRM connection** and picks Salesforce / HubSpot / Dynamics.
  2. Admin clicks **Authorize**; the app sends the browser to the vendor's authorization URL with `client_id`, `scope`, `redirect_uri`.
  3. The vendor consent screen asks the admin to choose the org/account and grant scopes; admin approves.
  4. Vendor redirects back to the sales room's `redirect_uri` with a `code` query parameter.
  5. Sales room exchanges the code server-side for an access token (+ refresh token) and stores the refresh token
     in the integration's credential vault, keyed by the org/account id.
  6. Admin clicks **Test connection**; the sales room calls a low-cost authenticated endpoint to verify the token.
- **data_flow:** `client_id` + `scope` + `redirect_uri` → vendor authorization endpoint → consent → `?code=…` on the
  sales-room callback → server-to-server token exchange → access token (bearer) stored encrypted → every later
  `Authorization: Bearer <token>` header. Transformation: the code→token exchange converts an ephemeral redirect code
  into a long-lived refresh credential; nothing is written to CRM records in this workflow.
- **data_sources:** CRM org / HubSpot portal (identity provider of record); sales-room integration settings table
  (tenant, connection status, encrypted refresh token); vendor consent screen.
- **apis_hit:**
  - Salesforce: OAuth 2.0 authorization flow over an **external client app** (new) or connected app (legacy).
    Every REST call then uses `Authorization: Bearer token`.
  - HubSpot: `GET https://app.hubspot.com/oauth/authorize?client_id=…&scope=…&redirect_uri=…` (authorize screen);
    callback `https://example.com/?code=xxxx`; then the token endpoint for initial access + refresh tokens.
  - Dataverse: `Authorization: Bearer <access token>` on `https://<org>.api.crm.dynamics.com/api/data/v9.2/…`.
- **automations:** Token refresh before expiry. HubSpot states the app is responsible for TTL storage and refresh:
  "`Unauthorized (401)` requests are not a valid indicator that a new access token must be retrieved."
  Connection-health polling is driven by the sales room's own scheduler (not vendor-side).
- **features_tools:** Salesforce **Setup → External Client Apps** (or legacy Connected App) policy screen; HubSpot
  **Developer Platform → app Auth page** (client ID / client secret) and the app-install consent screen; sales-room
  **Integrations** settings surface; a "Test connection" button.
- **extensibility:** A third party adds a new vendor by implementing the same 4 interfaces — authorize-URL builder,
  code→token exchange, token refresh, and a bearer-authenticated request executor. Per-tenant credentials are
  already isolated in the integration record, so a connector is a plug-in, not a fork. HubSpot's OAuth is the
  mandated path for multi-account distribution ("Any app designed for installation by multiple HubSpot accounts or
  listing on the HubSpot Marketplace must use OAuth"), which sets the shape of the interface.
- **sources:**
  - https://developer.salesforce.com/docs/platform/api-rest/guide/intro-oauth-and-connected-apps.html
  - https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/requests_composite.htm
  - https://developers.hubspot.com/docs/apps/developer-platform/build-apps/authentication/oauth/working-with-oauth
  - https://developers.hubspot.com/docs/developer-tooling/platform/usage-guidelines
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/execute-batch-operations-using-web-api?view=dataverse-latest
- **evidence:**
  - "For a client application to access REST API resources, it must be authorized as a safe visitor. To implement this
    authorization, use either an external client app or a connected app and an OAuth 2.0 authorization flow."
  - "Creating connected apps is restricted as of Spring '26. You can continue to use existing connected apps during
    and after Spring '26. However, we recommend using external client apps instead."
  - "OAuth authorization flows grant a client app restricted access to REST API resources on a resource server …
    1. To initiate an authorization flow, a connected app on behalf of a client app requests access to a REST API
    resource. 2. In response, an authorizing server grants access tokens … 3. A resource server validates these
    access tokens and approves access to the protected REST API resource."
  - HubSpot: "Any app designed for installation by multiple HubSpot accounts or listing on the HubSpot Marketplace
    must use OAuth." and "Users installing apps in their HubSpot account must either be a Super Admin or have
    HubSpot Marketplace Access permissions."
  - HubSpot: "Apps are responsible for storing time-to-live (TTL) data and refreshing user access tokens in
    accordance with this protocol. When an access token is generated, it will include an `expires_in` parameter
    indicating how long it can be used to make API calls before refreshing."
- **gaps:** The Salesforce OAuth 2.0 **flow detail pages** (web-server flow, PKCE, refresh-token rotation) live in
  `help.salesforce.com`, which serves a JS shell and could not be read. The Dataverse auth page
  (`webapi/authenticate-web-api`) was reachable (HTTP 200) but its body was not extracted before the research budget
  ran out, so no Dataverse-specific auth quote is claimed.

---

## 2. Map sales-room fields onto CRM fields and define the sync key

- **name:** Map sales-room fields onto CRM fields
- **user_flow:**
  1. Admin opens **Integrations → <connection> → Field mapping**.
  2. Admin picks the CRM object the room writes to (e.g. Contact, custom Engagement object, lead).
  3. For each sales-room field, admin picks the CRM property/column, its direction (in / out / both), and a transform
     (lowercase email, ISO-8601 date, picklist label → internal option value, number coercion).
  4. Admin picks the **sync key** — the CRM property that carries the sales-room's own row id, and marks it
     unique so the CRM itself rejects collisions.
  5. Admin clicks **Validate mapping**; the sales room reads the CRM's property/type metadata and flags unknown
     properties, wrong types, and unsupported option values before any data is written.
- **data_flow:** sales-room column definitions → mapping table (per field: source column, target property, direction,
  transform) → transform functions applied to values → target CRM property names validated against live CRM
  metadata (`/crm/properties/…`, `EntityDefinitions`, Salesforce field names) → mapping saved, sync key pinned.
- **data_sources:** HubSpot Properties API metadata (`type`, `fieldType`, `groupName`); Dataverse
  `EntityDefinitions` table-definition metadata; sales-room field dictionary; Dataverse `$metadata` CSDL document.
- **apis_hit:**
  - HubSpot: `GET /crm/properties/2026-09/{objectType}` and `GET /crm/properties/2026-09/{object}/{propertyName}` —
    discover target properties, types and option sets.
  - HubSpot: `POST /crm/properties/2026-09/{objectType}` — create the sync-key property with
    `"hasUniqueValue": true`.
  - Dataverse: `GET /api/data/v9.2/EntityDefinitions?$select=DisplayName,IsKnowledgeManagementEnabled,EntitySetName`
    and `GET /api/data/v9.2/EntityDefinitions?$select=SchemaName&$expand=Keys($select=KeyAttributes)`.
  - Dataverse: `GET /api/data/v9.2/$metadata` for the CSDL schema document.
- **automations:** None required at write time — the mapping is evaluated per record on every sync cycle. Dataverse
  can flag a schema change so a cached mapping is auto-invalidated (see W17).
- **features_tools:** HubSpot **Settings → Properties** (create/edit property, validation rules, property groups);
  HubSpot **Settings → Object settings**; Dataverse `EntityDefinitions` metadata queries; sales-room field-mapping
  grid with per-row validation badges.
- **extensibility:** Transforms are named, versioned functions registered in the connector (e.g. `email.normalize`,
  `picklist.map`, `date.iso8601`), so a deployment can add a transform without touching the sync engine. Because
  mapping is stored per connection (not per deployment), a self-hosted room can ship a *default* mapping per vendor
  and let tenants override individual fields.
- **sources:**
  - https://developers.hubspot.com/docs/api-reference/latest/crm/properties/guide
  - https://developers.hubspot.com/docs/api-reference/latest/crm/using-object-apis
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/define-alternate-keys-entity?view=dataverse-latest
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/query-metadata-web-api
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/web-api-service-documents
- **evidence:**
  - HubSpot: "When creating or updating properties, both `type` and `fieldType` values are required. The `type` value
    determines the type of the property, i.e. a string or a number. The `fieldType` property determines how the
    property will appear in HubSpot or on a form, i.e. as a plain text field, a dropdown menu, or a date picker."
  - HubSpot: "You can have up to ten unique ID properties per object. To create a property requiring unique values via
    API: 1. Make a `POST` request to `/crm/properties/2026-09/{objectType}`. 2. In your request body, for the
    `hasUniqueValue` field, set the value to `true`."
  - HubSpot: "When including enumeration properties, you must use internal names to set values. The internal name
    stays the same even if you've changed a default value's label."
  - Dataverse: "Alternate keys in Microsoft Dataverse let you uniquely identify table rows by using business columns
    instead of only a GUID primary key … By using alternate keys, you can define a column in a Dataverse table to
    correspond to a unique identifier (or unique combination of columns) used by the external data store."
  - Dataverse: "Only include columns of the following types in alternate key table definitions: DecimalAttributeMetadata
    … StringAttributeMetadata … DateTimeAttributeMetadata … LookupAttributeMetadata … PicklistAttributeMetadata" and
    "A table in a Dataverse instance can have up to ten alternate key table definitions."
  - Dataverse: "GET [Organization URI]/api/data/v9.2/EntityDefinitions?$select=SchemaName&$expand=Keys($select=KeyAttributes)"
    returns the key columns per table.
- **gaps:** Salesforce's *Object Reference* and *Metadata API* field pages are client-rendered and unreadable via
  the read path, so the Salesforce-specific half of "create the external-ID field" is not cited.

---

## 3. Provision the sales-room engagement object and its fields into the CRM

- **name:** Provision the engagement object into the CRM
- **user_flow:**
  1. Admin runs **Integrations → <connection> → Install integration package** (or CI job calls the same endpoint).
  2. The sales room compares its own object definition against the CRM's live schema: read `GET /` (service document)
     / `GET /crm/schemas/…` / `EntityDefinitions` to list what already exists.
  3. The sales room **creates only what is missing** — the engagement object plus each property — never destructively
     renaming or dropping existing fields.
  4. Creation is idempotent: re-running the installer is a no-op for already-present properties.
  5. Result: the room now has a first-class, CRM-native object to write engagement rows into, instead of shoving
     them into free-text notes.
- **data_flow:** sales-room object descriptor (name, labels, field types, option sets) → schema diff computed against
  CRM metadata → `POST` create requests for missing object/properties → CRM persists new object + properties →
  sales room records the mapping of room-object-id → CRM-object-id for subsequent syncs.
- **data_sources:** sales-room package manifest (versioned); HubSpot Schemas API; Dataverse table-definition
  metadata (`EntityDefinitions`, `EntityKeyMetadata`); Dataverse `$metadata`.
- **apis_hit:**
  - HubSpot: `POST /crm/properties/2026-09/{objectType}` (create a property) and the Schemas API for custom objects
    (`/crm-object-schemas/2026-09/schemas`, `/crm/v3/schemas/…`) to define the custom object.
  - Dataverse: `GET /api/data/v9.2/$metadata` and `GET /api/data/v9.2/EntityDefinitions…` for the diff; table and
    column creation via the Web API table-definition surface.
  - Dataverse: `EntityKeyMetadata` + `CreateEntityKey` to create the sync key on the new table.
- **automations:** None vendor-side. The installer is idempotent by construction so it can be run on every deploy and
  on every new tenant without a human.
- **features_tools:** HubSpot **Settings → Objects → Create custom object**; HubSpot **Settings → Custom objects**;
  Dataverse `EntityKeyMetadata` / `EntityMetadata.AsyncJob` for background index creation; sales-room
  package-installer surface with a dry-run diff view.
- **extensibility:** The room's object descriptor is data, not code — a deployment ships its own manifest, so
  "install CRM fields for rooms" is a config change. Dataverse documents background index creation
  (`EntityKeyMetadata.AsyncJob`, `EntityKeyIndexStatus` = Pending / In Progress / Active / Failed) plus
  `ReactivateEntityKey`, which is the extension point for repairing a half-provisioned key.
- **sources:**
  - https://developers.hubspot.com/docs/api-reference/latest/crm/objects/custom-objects/guide
  - https://developers.hubspot.com/docs/api-reference/latest/crm/properties/guide
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/define-alternate-keys-entity?view=dataverse-latest
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/web-api-service-documents
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/query-metadata-web-api
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/introduction-solutions
- **evidence:**
  - HubSpot: "Once you've defined a custom object, use the objects API to create and manage the custom object's
    records." and "Learn how to define custom objects with the schemas API or within HubSpot."
  - HubSpot property creation required fields: "`groupName` … `name`: the internal name of the property (e.g.,
    favorite_food). `label` … `type`: the type of property. `fieldType`: the field type."
  - Dataverse: "To define alternate keys programmatically, first create an object of type `EntityKeyMetadata` … After
    you set the key columns, use `CreateEntityKey` to create the keys for a table."
  - Dataverse: "If a table has many existing records, creating an index can take a long time. To make the customization
    UI and solution import more responsive, create the index in a background process. … `EntityKeyMetadata.AsyncJob` …
    `EntityKeyMetadata.EntityKeyIndexStatus` … Pending / In Progress / Active / Failed."
  - Dataverse: "The system validates the key, including that the total key size doesn't violate SQL-based index
    constraints like 900 bytes per key and 16 columns per key."
- **gaps:** The Salesforce half (Metadata API `CustomObject` / `CustomField` deploy) could not be sourced — the
  Metadata API guide returns a cookie banner only. HubSpot and Dataverse carry this workflow in the evidence.

---

## 4. Log a single buyer engagement event into the CRM

- **name:** Log a buyer engagement event into the CRM
- **user_flow:**
  1. Buyer opens a room asset (doc, pricing page, deck) or answers a CTA.
  2. The sales room records a row in its own `engagement` table and enqueues a CRM write.
  3. A background worker resolves the buyer's CRM record using the field mapping / sync key.
  4. Worker calls the CRM create endpoint with mapped properties.
  5. On success the worker writes the returned CRM record id into its local row (`crm_record_id`) and marks the event
     as synced; on failure it retries with backoff and surfaces the failure in the admin **Sync log** panel.
- **data_flow:** sales-room event (type, timestamp, asset, dwell time, buyer identity) → mapping transform →
  CRM create payload (`properties` object, or SOQL-shaped field body) → CRM object row created → response returns
  the new record id (HubSpot `id`; Dataverse returns the URI in the `OData-EntityId` header) → room stores id for
  future updates.
- **data_sources:** sales-room `engagement` table; buyer identity (CRM record id or email from W1/W2); CRM object
  (HubSpot contact/deal/custom object; Dataverse table; Salesforce custom object).
- **apis_hit:**
  - HubSpot: `POST /crm/v3/objects/contacts` (or the room's engagement custom object) to create one record.
  - Dataverse: `POST [Organization URI]/api/data/v9.2/accounts` style create on the target entity set; the created
    entity URI is returned in the `OData-EntityId` response header.
  - Salesforce: `POST /services/data/vXX.X/sobjects/{ObjectName}` (201 Created on success).
- **automations:** Fully user-action-triggered (a room event), but the *write* is asynchronous: the room's queue
  worker fires it without further user input, with retry on failure.
- **features_tools:** Room **Analytics / Engagement feed**; room **Sync log / Errors** admin panel; Dataverse
  `Prefer: odata.include-annotations` for enriched error detail; Salesforce `201`/`204` success codes.
- **extensibility:** New event types are rows in the room's event catalogue mapped by the field map, so adding
  "download", "pricing-view", "cta-click" needs a mapping row, not a code path. Optional
  `Prefer: return=representation` / `respond-async` style preferences let a connector opt into returning created data.
- **sources:**
  - https://developers.hubspot.com/docs/api-reference/legacy/crm/objects/contacts
  - https://developers.hubspot.com/docs/api-reference/latest/crm/using-object-apis
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/create-entity-web-api?view=dataverse-latest
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/compose-http-requests-handle-errors?view=dataverse-latest
  - https://developer.salesforce.com/docs/platform/api-rest/guide/errorcodes.html
- **evidence:**
  - Dataverse: "Send a `POST` request to the Web API entityset resource to create a table row (entity record) in
    Microsoft Dataverse." Response: "`HTTP/1.1 204 No Content` … `OData-EntityId:
    [Organization URI]/api/data/v9.2/accounts(00aa00aa-…)`".
  - Dataverse: "In Power Apps, when viewing a list of tables, select Advanced > Tools. Select **Copy set name** to copy
    the entity set name for the table. You can also select **API link to table data** to view the top 10 rows of data
    in your browser."
  - Dataverse: "`return=representation` — Use this preference to return data on create (POST) or update (PATCH)
    operations for entities. When you apply this preference to a POST request, a successful response has status
    201 Created. … Without this preference, both operations return status 204 No Content."
  - Salesforce: "`201` — 'Created' success code, for POST requests and some PATCH requests." and "`204` — 'No Content'
    success code, for DELETE requests and some PATCH requests."
  - HubSpot: "To create one contact, make a `POST` request to `/crm/v3/objects/contacts`."
- **gaps:** Dataverse and Salesforce `POST` semantics are fully quoted. No equivalent single-create page was read for
  Salesforce `sobjects` specifically (the `resources-sobjects` pages 404'd), so the Salesforce claim rests on the
  status-code reference plus the general resource model, not a `sobjects` create page.

---

## 5. Batch-upsert engagement rows keyed on the external ID

- **name:** Batch-upsert engagement rows on the external ID
- **user_flow:**
  1. The room's queue accumulates up to 200 pending engagement rows (or the nightly backlog).
  2. Admin-triggered **Sync now** (or the scheduled job) opens **Sync → Run upsert**.
  3. The connector chunks rows into batches (200 for Salesforce collections, 100 for HubSpot) and sends one upsert
     per chunk, keyed on the sync key chosen in W2.
  4. Each row either **updates** the existing CRM record (key found) or **creates** a new one (key not found).
  5. Per-row outcomes are written back to the room; failures appear in the sync log with the row's error text.
- **data_flow:** room engagement rows → chunked payloads (up to 200, single object type, `attributes.type` per item,
  **no `id` field**, external-ID field only) → CRM upsert → per-item `success` flag + `errors` array in the response →
  room updates `synced_at` / `crm_record_id` per row.
- **data_sources:** room `engagement` table; CRM object keyed on the external/alternate/unique-ID field.
- **apis_hit:**
  - Salesforce: `PATCH /services/data/vXX.X/composite/sobjects/{SobjectName}/{ExternalIdFieldName}` — sObject
    Collections upsert, up to 200 records, `allOrNone` parameter.
  - Salesforce: `PATCH /services/data/vXX.X/sobjects/{sObject}/{fieldName}/{fieldValue}` — single-row upsert on
    external ID, with optional `updateOnly=true`.
  - HubSpot: `POST /crm/v3/objects/contacts/batch/upsert` with `idProperty` (e.g. `email` or a custom unique
    identifier property).
  - Dataverse: `POST [Organization URI]/api/data/v9.2/{entityset}/Microsoft.Dynamics.CRM.UpsertMultiple` with a
    `Targets` collection where each item carries `@odata.type` and `@odata.id` using the alternate key.
- **automations:** Nightly/backlog upsert runs on the room's scheduler; the room may also upsert opportunistically
  when the queue exceeds N rows. Nothing runs inside the CRM in this workflow.
- **features_tools:** Room **Sync → Run upsert** with progress bar; per-row error list; Dataverse
  `@odata.id` alternate-key addressing; HubSpot `idProperty` selector.
- **extensibility:** The batch size, key field, and `allOrNone` policy are per-connection settings. A third party can
  register a vendor-specific "bulk capability" (max batch size, supported key types) and the scheduler adapts —
  e.g. it auto-falls back from `UpsertMultiple` to per-row `PATCH` for tables that don't support bulk upsert.
- **sources:**
  - https://developer.salesforce.com/docs/platform/api-rest/guide/resources-composite-sobjects-collections-upsert.html
  - https://developer.salesforce.com/docs/platform/api-rest/guide/resources-sobject-upsert-patch.html
  - https://developers.hubspot.com/docs/api-reference/latest/crm/using-object-apis
  - https://developers.hubspot.com/docs/api-reference/legacy/crm/objects/contacts
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/reference/upsertmultiple?view=dataverse-latest
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/define-alternate-keys-entity?view=dataverse-latest
- **evidence:**
  - Salesforce: "Use a `PATCH` request with sObject Collections to either create or update (upsert) up to 200 records
    based on an external ID field. This method returns a list of `UpsertResult` objects. You can choose whether to
    roll back the entire request when an error occurs."
  - Salesforce: "The list can contain up to 200 objects. The list can contain objects only of the type indicated in
    the request URI. Each object in the request body must contain an attributes map. The map must contain a value for
    `type`. … Objects are created or updated in the order they're listed in the request body. The `UpsertResult`
    objects are returned in the same order. … **Only external ids are supported. Don't use record ids.**"
  - Salesforce: "If the external ID matches multiple existing records, then a 300 error is returned, and no records
    are created or updated." and "If the external ID doesn't match an existing record, then a new record is created
    according to the request body. To prevent a new record from being created, use the `updateOnly` parameter."
  - HubSpot: "To upsert records, make a `POST` request to `/crm/objects/2026-09/{objectTypeId}/batch/upsert`. In your
    request body, include the `idProperty` parameter to identify the unique identifier property you're using."
    "Partial upserts are not supported when using `email` as the `idProperty` for contacts."
  - HubSpot contacts: "**Limits** — Batch operations are limited to 100 records at a time."
  - Dataverse: "`Upsert` (Create or Update) multiple records of same type in a single request." … "You must specify
    the `@odata.type` annotation with every item in the `Targets` parameter. The `UpsertMultiple` action returns
    `204 NoContent`." … "These requests identify the records using an alternate key defined using a column named
    `sample_keyattribute`. The `@odata.id` annotation identifies the record with a relative URL."
- **gaps:** Salesforce's warning that using an email address as the External ID path segment can 404 on TLD/extension
  collisions is documented (e.g. `example@email.inc` → "404 not found") but the documented workarounds
  (different External ID field, custom Apex REST endpoint) were not cross-verified against a second source.

---

## 6. Write account + contact + opportunity as one atomic transaction

- **name:** Commit a related record set atomically
- **user_flow:**
  1. Admin clicks **Sync → Create opportunity bundle** for a buyer whose CRM account may not exist yet.
  2. The connector builds a single request containing Account → Contact → Opportunity subrequests in dependency order.
  3. The connector sets the rollback policy (strict / partial) for that request.
  4. The CRM executes subrequests in order, capturing each created record id.
  5. Later subrequests reference earlier ones by id, so the Opportunity is created against the *just-created*
     Account rather than a stale one.
  6. On any failure the whole bundle rolls back (strict mode) and the room shows a single actionable error.
- **data_flow:** room bundle (account, contact, opportunity + field map) → one request body containing ordered
  subrequests with `referenceId` / `Content-ID` placeholders → CRM resolves `$1`/`@{refAccount.id}` into real record
  URIs as it creates each row → related rows linked → all-or-nothing (or partial) commit.
- **data_sources:** room deal bundle; CRM account, contact, opportunity tables; sales-room bundle table.
- **apis_hit:**
  - Salesforce: `POST /services/data/vXX.X/composite` with `allOrNone` and `collateSubrequests`, subrequests
    carrying `method`, `url`, `referenceId`, `body`; reference syntax `@{referenceId.FieldName}`.
  - Salesforce: `POST /services/data/vXX.X/composite/tree/{sObjectName}` — sObject Tree, up to 200 records across all
    trees, up to five levels deep, "If an error occurs while creating a record, the entire request fails."
  - Dataverse: `POST [Organization URI]/api/data/v9.2/$batch` with `Content-Type: multipart/mixed`, a `changeset_*`
    boundary making the operations atomic, and `Content-ID: 1/2/3` + `$1` reference URIs.
  - HubSpot: `POST /crm/v3/objects/contacts/batch/create` with an `associations` array, or
    `PUT /crm/v3/objects/{objectTypeId}/{recordId}/associations/{toObjectType}/{toObjectId}/{associationTypeId}`.
- **automations:** None vendor-side. Room-side this can be triggered by an event (e.g. buyer accepts pricing) or a
  scheduled job, but it is a single explicit request either way.
- **features_tools:** Room **Sync → bundle preview** showing the subrequest order; `collateSubrequests` toggle to
  force execution order; Dataverse change sets; HubSpot associations API.
- **extensibility:** The dependency graph is declared as data (ordered subrequests with id references), so a
  deployment can add a 4th record type without touching the transport. Salesforce's documented collation caveat
  (implicit vs explicit dependencies) is exactly the knob a connector exposes to let a tenant trade speed for
  ordering guarantees.
- **sources:**
  - https://developer.salesforce.com/docs/platform/api-rest/guide/requests-composite.html
  - https://developer.salesforce.com/docs/platform/api-rest/guide/resources-composite-sobject-tree.html
  - https://developer.salesforce.com/docs/platform/api-rest/guide/resources-composite-allornone.html
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/execute-batch-operations-using-web-api?view=dataverse-latest
  - https://developers.hubspot.com/docs/api-reference/latest/crm/using-object-apis
  - https://developers.hubspot.com/docs/api-reference/legacy/crm/objects/contacts
- **evidence:**
  - Salesforce: "`allOrNone` | Boolean | Specifies what to do when an error occurs while processing a subrequest. If
    the value is `true`, the entire composite request is rolled back. … If the value is `false`, the remaining
    subrequests that don't depend on the failed subrequest are executed. Dependent subrequests aren't executed."
  - Salesforce: "Collation can cause issues if there are implicit but not explicit dependencies between items. For
    example, consider a request that creates an Account, a Contact related to the Account, and a custom object that has
    a trigger dependent on the account name. … If you have relationships like this where you need to control the order
    of execution, set `collateSubrequests` to `false`."
  - Salesforce: "You can have up to 25 subrequests in a single call. Up to 5 of these subrequests can be sObject
    Collections or query operations, including Query and QueryAll requests." (The `referenceId` supports
    `@{NewAccount.BillingAddress.city}` and `@{AccountInfo.recentItems[0].Id}`.)
  - Salesforce sObject Tree: "The request can contain the following: Up to a total of 200 records across all trees /
    Up to five records of different types / sObject trees up to five levels deep … If an error occurs while creating
    a record, the entire request fails. … The entire request counts as a single call toward your API limits."
  - Dataverse: "You can group requests for operations together so that they're included as a single transaction by
    using Change sets. … When multiple operations are contained in a change set, all the operations are considered
    *atomic*. An atomic operation means that if any one of the operations fails, the batch request rolls back any
    completed operations." and "Batch requests can contain up to 1,000 individual requests".
  - Dataverse: "Within changesets, you can use `$parameter` such as `$1`, `$2` … to reference URIs returned for new
    entities created earlier in the same changeset." (e.g. `"originatingleadid@odata.bind":"$1"`).
  - HubSpot: "To associate a record with other records or an activity, make a `PUT` request to
    `/crm/objects/2026-09/{objectTypeId}/{fromRecordId}/associations/{toObjectTypeId}/{toRecordId}`."
- **gaps:** Salesforce `allOrNone` interaction between the outer Composite flag and inner sObject Collections flags
  has four documented cases; the doc's own note is that the outer `true` overrides the inner value. The response-body
  example for Case 4 was image-only in the fetched markdown, so the exact rollback body is not quoted.

---

## 7. Surface partial failures and reject invalid writes before commit

- **name:** Surface partial failures and reject invalid writes
- **user_flow:**
  1. A sync batch runs and some rows fail.
  2. Instead of failing the whole batch, the connector asks for **per-record outcomes** (multi-status / continue-on-error).
  3. The room's **Sync log** lists each failed row with a human-readable reason and the offending property.
  4. Admin clicks a failed row → **Field-level error detail** (which property, what was sent, what was expected).
  5. Admin fixes the mapping or the data, and clicks **Retry failed rows only** — successes are not re-sent.
- **data_flow:** failing row → vendor per-item error object (`errors[]` with `message`/`code`/`context`, or Dataverse
  `{"error":{"code","message"}}` with `HelpLink` annotations) → normalised room-side error record keyed to the row →
  retry queue. Transformation: vendor-specific error shapes collapse into one room error model.
- **data_sources:** sync-run result payloads; CRM validation metadata; room error table.
- **apis_hit:**
  - HubSpot: `207 Multi-Status` for batch create with `objectWriteTraceId` per input; response has `numErrors` and an
    `errors` array with `context.objectWriteTraceId`; `Prefer`-style validation enforcement on CRM write paths.
  - Dataverse: `Prefer: odata.continue-on-error` on `$batch`; validation error `0x80044331`; plug-in error detail
    annotations under `Prefer: odata.include-annotations="*"`.
  - Salesforce: HTTP `400` for malformed body, `403 REQUEST_LIMIT_EXCEEDED`, and the per-item `errors` array from
    sObject Collections.
- **automations:** Retry queue drains automatically for retryable classes (rate limit, locked) and waits for admin
  action for validation failures — the room classifies errors into retryable vs terminal.
- **features_tools:** Room **Sync log** with per-row status chips; **Retry failed rows** action; HubSpot
  `objectWriteTraceId` correlation; Dataverse `HelpLink` annotation; Salesforce `errorCode` + `message` in body.
- **extensibility:** The room's error model is the extension point — a connector maps vendor codes into
  `{retryable, field, code, message, docLink}`. A deployment can add a rule ("route records missing `email` to a
  manual-review queue instead of retrying") without changing the transport.
- **sources:**
  - https://developers.hubspot.com/docs/api-reference/error-handling
  - https://developers.hubspot.com/docs/api-reference/latest/crm/properties/guide
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/execute-batch-operations-using-web-api?view=dataverse-latest
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/compose-http-requests-handle-errors?view=dataverse-latest
  - https://developer.salesforce.com/docs/platform/api-rest/guide/errorcodes.html
- **evidence:**
  - HubSpot: "`207 Multi-Status` | Returned when there are different statuses (e.g., errors and successes), which
    occurs when you've enabled multi-status error handling for the object API batch create endpoints."
  - HubSpot: "For the object APIs' batch create endpoints, you can enable multi status responses that include partial
    failures. … include a unique `objectWriteTraceId` value for each input in your request." Response contains
    `"numErrors": 1` and `"context": { "objectWriteTraceId": ["549b1c2a9351"] }`.
  - HubSpot: "Starting with the GA release of API version `/2026-09/` on September 8, 2026, HubSpot will enforce
    admin-configured validation rules on all CRM API write paths. You can retrieve or manage your validation rules using
    the property validation API, or by reviewing rules via the property settings page."
  - Dataverse: "If you add the `Prefer: odata.continue-on-error` request header, you can specify that the server
    processes more requests when errors occur. The batch request returns `200 OK`, and individual response errors are
    included in the batch response body." Example error body:
    `{"error":{"code":"0x80044331","message":"A validation error occurred. The length of the 'subject' attribute of the 'task' entity exceeded the maximum allowed length of '200'."}}`
  - Dataverse: "`412 Precondition Failed` Expect this status code for the following types of errors:
    - `ConcurrencyVersionMismatch` - `DuplicateRecord`" and "`400 BadRequest` Expect this status code when an argument
    is invalid."
  - Dataverse: "When a request includes the `Prefer: odata.include-annotations="*"` header, the response includes all
    the annotations that contain more details about errors and a URL that might direct you to specific guidance",
    including `@Microsoft.PowerApps.CDS.HelpLink`.
  - Salesforce: "`400` The request couldn't be understood, usually because the JSON or XML body contains an error."
    "`403` … If the error code is `REQUEST_LIMIT_EXCEEDED`, you've exceeded API request limits in your org."
- **gaps:** HubSpot's per-object multi-status detail is only documented for **batch create** endpoints in the page
  read; whether `batch/upsert` and `batch/update` accept `objectWriteTraceId` was not confirmed from that page.

---

## 8. Detect and block duplicate records during sync

- **name:** Detect and block duplicate records during sync
- **user_flow:**
  1. A new lead/contact arrives from the room (form fill, CTA, file download by an unknown email).
  2. The connector sends the write with duplicate detection enabled.
  3. The CRM's matching/duplicate rule runs against existing rows and returns either a clean create, a
     **duplicate alert with the matching record id**, or a hard block.
  4. Depending on the admin's configured policy, the connector either (a) blocks the write and shows the existing
     record, (b) updates the existing record instead, or (c) creates the duplicate anyway with an acknowledgement.
  5. The decision and the matched record id are logged on the room row.
- **data_flow:** inbound row → duplicate-check request (header-driven duplicate-rule options / unique external-ID
  column) → CRM duplicate result (clean / 1 match / N matches) → policy decision → create, update, or reject →
  room row annotated with match id and outcome.
- **data_sources:** CRM duplicate management rules; CRM matching keys (email, domain, external ID, account number);
  CRM account/contact tables; room dedupe policy setting.
- **apis_hit:**
  - Salesforce: REST request header `Duplicate Rule Header` with fields `allowSave`, `includeRecordDetails`,
    `runAsCurrentUser` (API version 52.0+). Applies to "the record that is being created, updated, or upserted".
  - Salesforce: `300` response — "The value returned when an external ID exists in more than one record. The response
    body contains the list of matching records."
  - HubSpot: unique-ID enforcement via a property created with `hasUniqueValue: true`; upsert by `idProperty` so a
    repeat write updates rather than duplicates.
  - Dataverse: alternate keys — "Alternate keys use database indexes to enforce uniqueness and optimize lookup
    performance"; a duplicate key write fails.
- **automations:** The CRM evaluates the rule on **every** create/update/upsert without the caller asking. HubSpot's
  own automatic dedupe treats email as the primary unique identifier.
- **features_tools:** Salesforce **Duplicate Management** settings (duplicate rules, matching rules, auto-merge);
  REST `Duplicate Rule Header`; HubSpot property **hasUniqueValue** + **Settings → Duplicate management**;
  Dataverse **Table → Keys**; room dedupe policy selector.
- **extensibility:** Dedupe policy is a per-connection enum (block / update / merge), so a deployment can escalate
  to "auto-merge" for high-confidence cases. A third party can register additional matchers (fuzzy domain + name)
  evaluated in the room *before* calling the CRM, reducing wasted API calls.
- **sources:**
  - https://developer.salesforce.com/docs/platform/api-rest/guide/headers-duplicaterules.html
  - https://developer.salesforce.com/docs/platform/api-rest/guide/headers.html
  - https://developer.salesforce.com/docs/platform/api-rest/guide/errorcodes.html
  - https://developer.salesforce.com/docs/platform/api-rest/guide/resources-sobject-upsert-patch.html
  - https://developers.hubspot.com/docs/api-reference/latest/crm/properties/guide
  - https://developers.hubspot.com/docs/api-reference/legacy/crm/objects/contacts
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/define-alternate-keys-entity?view=dataverse-latest
- **evidence:**
  - Salesforce: "Configure options for duplicate rules. Salesforce uses duplicate rules to see if the record that is
    being created, updated, or upserted is a duplicate of an existing record. Duplicate rules are part of Duplicate
    Management. This header is available in API version 52.0 and later." Fields: `allowSave` (true — "allow the user
    to acknowledge the alert and save the duplicate record"), `includeRecordDetails` (true — "return all fields in the
    duplicate record"), `runAsCurrentUser` (true — "use the current user's sharing rules"). "The default value for all
    fields is `false`."
  - Salesforce: "`300` The value returned when an external ID exists in more than one record. The response body
    contains the list of matching records." and "If the external ID matches multiple existing records, then a 300 error
    is returned, and no records are created or updated."
  - Salesforce: "If you're upserting a record for an object that has a custom field with both the `External ID` and
    `Unique` attributes selected (a unique index), you don't need any special permissions. The `Unique` attribute
    prevents the creation of duplicates."
  - HubSpot: "When a record is created in HubSpot, a unique ID (`hs_object_id`) is automatically generated … For
    contacts and companies, there are additional unique identifiers, including a contact's email address (`email`)
    and a company's domain name (`domain`)."
  - HubSpot contacts: "It's recommended to always include `email`, because email address is the primary unique
    identifier to avoid duplicate contacts in HubSpot."
  - Dataverse: "Alternate keys use database indexes to enforce uniqueness and optimize lookup performance. If a table
    has many existing records, creating an index can take a long time."
- **gaps:** Salesforce **auto-merge** (merging duplicates automatically) lives in `help.salesforce.com`
  duplicate-management docs, which are JS-rendered and unreadable; the merge action is therefore *not* claimed.
  HubSpot's automatic-deduplication Knowledge Base article was referenced by the contacts page but not itself read.

---

## 9. Pull CRM deal, account and contact data into the room for display

- **name:** Pull CRM records into the room for display
- **user_flow:**
  1. Buyer opens the room; the room needs deal name, stage, amount, primary contact, account industry.
  2. Room resolves the buyer's CRM identity (from W1/W2 mapping or a signed token).
  3. Room issues a **read-only, field-scoped** CRM query: only the mapped columns, paged.
  4. Room caches the result per buyer with a short TTL and renders the deal panel.
  5. Room requests the *display labels* for option columns (stage, status) so the panel shows "Proposal sent", not
     the integer `2`.
- **data_flow:** room view request → identity resolution → CRM query (select specific properties, filter by id/owner,
  order, limit) → paged results with `totalSize` / `done` / `nextRecordsUrl` (or `@odata.nextLink` /
  `paging.next.after`) → normalise into the room's view model → cache + render.
- **data_sources:** CRM account/contact/deal tables; room view-model; room cache.
- **apis_hit:**
  - Salesforce: `GET /services/data/vXX.X/query?q=<SOQL>` — returns `totalSize`, `done`, `nextRecordsUrl`, `records`;
    continue with `resources-query-more-results`. "up to 2,000 records can be returned at a time in a synchronous request."
  - Dataverse: `GET [Organization URI]/api/data/v9.2/accounts?$select=…&$orderby=…&$top=1` with
    `Prefer: odata.include-annotations="OData.Community.Display.V1.FormattedValue"` for display labels; `$filter`
    supports comparison/logical/string functions and ~60 Dataverse functions.
  - HubSpot: `POST /crm/v3/objects/{objectType}/batch/read` (by Record ID, email, or custom unique id via
    `idProperty`); `GET /crm/v3/objects/contacts/{email}?idProperty=email` for a single record by email.
- **automations:** Read-through cache refresh on a room scheduler; nothing pushes. Dataverse also supports a
  change-tracking mode for the same table (see W10) if the room prefers push.
- **features_tools:** Room **Deal panel** / **Pricing** view; `Prefer` display-annotation header; HubSpot
  `properties` and `propertiesWithHistory` parameters; Dataverse `$top`/`$count` paging.
- **extensibility:** The read set is derived from the *same* field map as writes, so a deployment that adds a field
  automatically gets it in the room's deal panel with no extra API code. Vendors expose a capability flag for
  "display-label annotations" that the room uses when available and falls back to its own option-set map when not.
- **sources:**
  - https://developer.salesforce.com/docs/platform/api-rest/guide/resources-query.html
  - https://developer.salesforce.com/docs/platform/api-rest/guide/headers.html
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/query/overview?view=dataverse-latest
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/query/filter-rows?view=dataverse-latest
  - https://developers.hubspot.com/docs/api-reference/legacy/crm/objects/contacts
  - https://developers.hubspot.com/docs/api-reference/latest/crm/search-the-crm
- **evidence:**
  - Salesforce: "Runs the specified SOQL query. When a SOQL query is executed, up to 2,000 records can be returned at
    a time in a synchronous request. However, to optimize performance, the returned batch can include fewer records
    than the limit … the response contains the first batch of records, a `false` value for `done`, and a query
    locator. You can use the query locator with the Query More Results resource to retrieve the next batch of
    records." Response fields: "`totalSize`, … `done`, … `nextRecordsUrl`, and … `records`."
  - Salesforce: "**Query Options Header** Specifies options used in a query, such as the query results batch size.
    Use this request header with the Query resource."
  - Dataverse: "Limiting the columns returned by using `$select` … Ordering results by using `$orderby` … Limiting the
    rows returned by using `$top` … Showing formatted values by using the request header:
    `Prefer: odata.include-annotations="OData.Community.Display.V1.FormattedValue"`." Response shows
    `"statecode@OData.Community.Display.V1.FormattedValue": "Active"`.
  - Dataverse: "Without this limit, Dataverse returns up to 5,000 standard table rows and 500 elastic table rows."
    and "The length of a URL in a `GET` request is limited to 32 KB (32,768 characters) … You can execute a `$batch`
    operation by using a `POST` request as a way to move the OData query options out of the URL … URLs up to 64 KB
    (65,536 characters)."
  - Dataverse: "You can include up to 500 total conditions in a query. Otherwise, you see this error message:
    `Number of conditions in query exceeded maximum limit.`"
  - HubSpot: "To retrieve contacts in batches, make a `POST` request to `crm/v3/objects/contacts/batch/read`. … By
    default, the `id` values in the request refer to the Record ID, so the `idProperty` parameter is not required when
    retrieving by Record ID, but always required when retrieving by email or a custom unique ID property." Also:
    "You can retrieve up to 100 contacts in one request." and "`propertiesWithHistory` | A comma separated list of the
    current and historical properties to be returned".
  - HubSpot search limits: "The search endpoints are rate limited to five requests per second per account. The maximum
    number of supported objects per page is 200. A query can contain a maximum of 3,000 characters … The search
    endpoints are limited to 10,000 total results for any given query."
- **gaps:** The Dataverse "unsupported query options" list (`$skip`, `$search`, `$format`) and the FetchXml
  fallbacks were noted in the source but are not needed for the room's read path and are not expanded here.

---

## 10. Stream CRM record changes into the room in near real time

- **name:** Stream CRM record changes into the room
- **user_flow:**
  1. An operator enables Change Data Capture for the objects the room cares about, and picks a subscription channel
     (or a custom channel if several consumers need different field sets).
  2. The room's subscriber client opens a long-lived subscription (Pub/Sub API, or a delta-link poll loop).
  3. On each event the room checks `changeType`, buffers the change under its `transactionKey`, and only commits to
     the room's local replica when the key changes.
  4. The room refreshes the affected buyer's deal panel.
  5. If the room needs an unchanged field (e.g. the external ID) to resolve the record, that field is added as an
     **enriched** field on the channel.
- **data_flow:** CRM record change → change event (`changeType`, `transactionKey`, `sequenceNumber`,
  `commitTimestamp`, `changedFields`, record payload) → subscriber deserialisation (Avro for Pub/Sub, JSON for CometD)
  → per-transaction buffer → commit → room replica / UI invalidation. Transformation: event payload is mapped
  through the same field map; enriched fields fill the "unchanged but needed" gaps.
- **data_sources:** CRM change event stream (`/data/ChangeEvents` and per-entity channels); `PlatformEventUsageMetric`
  for delivery usage; room replica tables.
- **apis_hit:**
  - Salesforce: **Change Data Capture** change events; **Pub/Sub API** `Subscribe` RPC (gRPC/HTTP2, bidirectional
    streaming, `FetchRequest` sets how many events are requested); CometD (Streaming API); event relays.
  - Dataverse: `GET /api/data/v9.2/accounts?$select=…` with `Prefer: odata.track-changes` → response carries
    `@odata.deltaLink`; the next call is `GET` on that delta link; `GET /accounts/$count?$deltatoken=…` for change counts.
  - HubSpot: workflow **webhook** action, or the app **webhook subscriptions** surface.
- **automations:** This workflow is entirely automated. Salesforce fires it on record create/update/delete/undelete.
  Dataverse change tracking is enabled per table and the delta link advances automatically. HubSpot workflow webhook
  actions fire from workflow enrollment: "Webhooks can be triggered as an action in any workflow, so you can use any
    workflow starting conditions as the criteria."
- **features_tools:** Salesforce **Change Data Capture** setup + **custom channel** + event enrichment; room
  **Live activity** / **Realtime** panel; Dataverse `Track changes` table property ("In Power Apps, select Data > Tables
  and the specific table. Under Advanced options, you find the Track changes property"); HubSpot
  **Automation → Workflows**.
- **extensibility:** Channel choice is the multi-tenant extension point: Salesforce documents custom channels and
  enrichment isolation so "other subscribers that receive change events on the standard channel don't receive
  unchanged fields that they don't expect." A room deployment can create its own channel and add enrichment without
  disturbing other consumers. Pub/Sub buffer sizing is also tunable ("We recommend you set the buffer size to 3 MB").
- **sources:**
  - https://developer.salesforce.com/docs/platform/change-data-capture/guide/cdc-intro.html
  - https://developer.salesforce.com/docs/platform/change-data-capture/guide/cdc-subscribe.html
  - https://developer.salesforce.com/docs/platform/change-data-capture/guide/cdc-subscribe-pubsub-api.html
  - https://developer.salesforce.com/docs/platform/change-data-capture/guide/cdc-enrich-intro.html
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/use-change-tracking-synchronize-data-external-systems?view=dataverse-latest
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/compose-http-requests-handle-errors?view=dataverse-latest
  - https://developers.hubspot.com/docs/developer-tooling/platform/usage-guidelines
  - https://knowledge.hubspot.com/workflows/how-do-i-use-webhooks-with-hubspot-workflows
- **evidence:**
  - Salesforce: "Receive near-real-time changes of Salesforce records, and synchronize corresponding records in an
    external data store. … Changes include creation of a new record, updates to an existing record, deletion of a
    record, and undeletion of a record." "Available in: **Enterprise**, **Performance**, **Unlimited**, and
    **Developer** editions."
  - Salesforce: "Use Change Data Capture to update data in an external system instead of doing periodic exports and
    imports of data or repeated API calls."
  - Salesforce: "A subscription channel is a stream of change events that correspond to one or more entities. … Change
    Data Capture provides predefined standard channels and you can create your own custom channels. … The channel name
    is case-sensitive."
  - Salesforce Pub/Sub: "The `Subscribe` method uses bidirectional streaming, enabling the client to request more events
    as it consumes events. The client can control the flow of events received by setting the number of requested events
    in the `FetchRequest` parameter." … "We recommend you set the buffer size to 3 MB."
  - Salesforce enrichment: "Event enrichment is supported for subscribers that use Pub/Sub API, CometD (Streaming API),
    or event relays. Fields that you select for enrichment are included in change events for update and delete
    operations. Enriched fields aren't included in change events for create and undelete operations because these events
    contain all the populated fields."
  - Salesforce: "We recommend that you configure event enrichment on a custom channel and not the standard
    `/data/ChangeEvents` channel. This way, other subscribers that receive change events on the standard channel don't
    receive unchanged fields that they don't expect."
  - Dataverse: "You can track changes made in tables by using Web API requests that include the
    `Prefer: odata.track-changes` header. This header requests that a *delta link* is returned, which you can later use
    to retrieve table changes." … "After you enable change tracking for a table, you can't disable it."
  - Dataverse: "`$filter`, `$orderby`, `$expand`, and `$top` aren't supported when you use the
    `Prefer: odata.track-changes` header … If you use these query options … you get an error message:
    `The "${filter|orderby|expand|top}" query parameter isn't supported when Change Tracking is enabled.`"
  - Dataverse: "The entity sets that represent tables where change tracking is enabled have this annotation:
    `<Annotation Term="Org.OData.Capabilities.V1.ChangeTracking"><Record><PropertyValue Property="Supported" Bool="true" />`"
  - HubSpot: "Webhook calls made via workflows do not count towards the API rate limit." and "You can create up to
    1,000 webhook subscriptions per app."
- **gaps:** HubSpot's `webhook subscriptions` **REST** guide page (`/docs/api-reference/latest/webhooks`) is
  client-rendered and its body could not be read; the subscription endpoints/methods are therefore **not** claimed.
  Only the workflow-webhook action (KB) and the limit/header facts are cited for HubSpot.

---

## 11. Emit a webhook out of the CRM when a deal stage changes

- **name:** Emit a webhook out of the CRM on a deal stage change
- **user_flow:**
  1. Rep sets up the automation **in the CRM**, not in the sales room: in HubSpot, **Automation → Workflows → edit
     workflow → + → Data ops → Send a webhook**; in Salesforce, an outbound message / flow HTTP callout.
  2. Rep adds workflow start conditions (e.g. deal stage becomes "Contract Sent", or a contact property changes).
  3. Rep picks method **POST**, enters the room's HTTPS webhook URL, and configures authentication (request
     signature, API key in query params or a request header, or `Authorization: Bearer` secret).
  4. Rep chooses the body: **Include all [object] properties**, or **Customize request body** (add HubSpot properties
     as keys/values, and/or static values).
  5. Rep clicks **Save**, then **Publish**, then uses the built-in **Test** control to send a sample payload.
  6. The room's endpoint verifies the signature, resolves the record, and updates the buyer's room state.
- **data_flow:** CRM property/stage change → workflow evaluates start conditions → outbound HTTP request built from
  the object + selected properties (plus static fields) → signed POST to the room endpoint → room authenticates
  (signature / bearer) → maps payload onto the room model → updates deal panel and notifies the rep.
- **data_sources:** CRM deal/contact record (trigger source); room webhook endpoint; shared secret or app id for
  signature verification.
- **apis_hit:**
  - HubSpot: workflow action **Send a webhook** (POST or GET) — the workflow's outbound HTTP call; authentication via
    `Include request signature in header` + HubSpot App ID, or `API key` (query params or request header), or
    `Authorization` request header set to a HubSpot secret in the form `Bearer [YOUR_TOKEN]`.
  - HubSpot inbound for the room: the room's own HTTP endpoint (not a HubSpot API).
  - Salesforce: Outbound Messages / Flow HTTP callout — *documented location known but page not readable*; not claimed.
- **automations:** 100% CRM-side automation, no user action at fire time. HubSpot separates webhook traffic from
  other workflow processing "to streamline workflow and webhook performance". Workflows must be **published** to go
  live.
- **features_tools:** HubSpot **Automation → Workflows** editor, **Data ops → Send a webhook** panel, method /
  URL / auth / body controls, built-in **Test** send, **Publish** button; room **Webhook settings** page showing the
  endpoint URL and secret.
- **extensibility:** The room exposes one inbound endpoint per tenant with a versioned payload contract, so any
  number of CRM-side automations can target it. Because the room can verify the request signature, it does not need
  a per-workflow secret. Request-signature verification is a documented HubSpot capability the room can lean on
  instead of inventing its own HMAC scheme.
- **sources:**
  - https://knowledge.hubspot.com/workflows/how-do-i-use-webhooks-with-hubspot-workflows
  - https://developers.hubspot.com/docs/api-reference/error-handling
  - https://developers.hubspot.com/docs/developer-tooling/platform/usage-guidelines
- **evidence:**
  - HubSpot: "You can send both POST and GET requests using workflows. HubSpot regulates webhook traffic separately
    from other workflow processes. This is done to streamline workflow and webhook performance. When a webhook is
    slow or times out, the workflow action may take longer than expected to execute."
  - HubSpot: "In the workflow editor, click the + plus icon to add an action. In the left panel, in the **Data ops**
    section, select **Send a webhook**. Click the **Method** dropdown menu and select POST. Enter the webhook URL.
    Webhook URLs are restricted to a secure protocol and must begin with HTTPS."
  - HubSpot: "To use a request signature in your webhook header: Click the **Authentication type** dropdown menu.
    Then, select **Include request signature in header**. Then, enter your HubSpot App ID."
  - HubSpot: "If you're making a request to HubSpot APIs: … The secret value must be in the format
    `Bearer [YOUR_TOKEN]`. Set the value of API key name to `Authorization`. Set the value of API key location to
    `Request Header`."
  - HubSpot: "To include all properties, select **Include all [object] properties**. To include only specific
    properties: Select **Customize request body** … To customize the request body using a HubSpot property, enter the
    Key and select a property … To add a static field, enter the Key and Value. To add another property, click **Add
    static value**."
  - HubSpot (permissions): "To set up webhook actions in workflows, users must have Edit permissions for workflows or
    Super Admin permissions. To publish workflows, users must have Publish permissions for workflows."
  - HubSpot: "You can create up to 1,000 webhook subscriptions per app." and "Webhook calls made via workflows do not
    count towards the API rate limit."
- **gaps:** **Salesforce outbound messages and Flow HTTP callout could not be sourced** — the Metadata API guide
  page (`meta_outbound_messages`) returned only a cookie banner. Salesforce's equivalent of this workflow is
  therefore *not* documented here. Microsoft Dataverse's outbound webhook mechanism is likewise unsourced (no
  readable primary page was found for Dataverse webhooks / Service Bus notifications under the developer guide).

---

## 12. Backfill historical records on a schedule with a resumable cursor

- **name:** Backfill history on a resumable cursor
- **user_flow:**
  1. Admin opens **Sync → Backfill** and picks a date range / full-history option.
  2. Room asks the CRM for an asynchronous extract job (large volumes) or a delta/paged read (moderate volumes).
  3. Admin clicks **Start**; the room stores the job id or the delta token as a **cursor**.
  4. The room polls job status / delta link; the CRM returns result batches, and the room writes rows in pages.
  5. If the job or the room crashes mid-run, the room restarts from the stored cursor — no duplicates, no gaps.
  6. Progress is shown as a percentage in **Sync → Backfill** with a per-run log.
- **data_flow:** date range → async job create (Salesforce) or delta-token read (Dataverse) or export request
  (HubSpot) → job result file / paged rows → transform via field map → upsert into room replica (or push into CRM for
  a reverse backfill) → advance cursor → completion marker.
- **data_sources:** CRM historical records; job/extract storage (Salesforce Bulk API 2.0, HubSpot export files);
  room replica tables; cursor store.
- **apis_hit:**
  - Salesforce: **Bulk API 2.0** — asynchronous insert/update/upsert/delete and bulk queries; "optimized for working
    with large sets of data … You submit a request and come back for the results later. Salesforce processes the
    request in the background."
  - Dataverse: `Prefer: odata.track-changes` delta links and `RetrieveEntityChanges` (`DataToken`); documented
    paging via `PagingInfo { Count = 5000, PageNumber }` + `PagingCookie`.
  - HubSpot: `POST /crm/exports/2026-09/export/async` (scopes `crm.export`, Super Admin required to grant the scope)
    to produce a file, then read export status and the download URL.
- **automations:** Backfill is a scheduled job in the room. Dataverse's change-tracking token and Salesforce's job
  queue both require polling, so the room runs a poller on a fixed interval. HubSpot notes the daily limit "resets at
  midnight based on your time zone setting" — relevant when sizing a backfill against remaining quota.
- **features_tools:** Room **Sync → Backfill** wizard with range picker + progress; Salesforce Bulk API job
  monitoring; Dataverse delta-link / `DataToken` store; HubSpot **Settings → Export log**; HubSpot **Account
  information** API for remaining daily calls.
- **extensibility:** The cursor is an interface, so a vendor-specific cursor (Bulk job id, delta link, `DataToken`)
  is swapped for a standard `{vendor, connectionId, cursor, updatedAt}` record. This is also what makes backfill
  idempotent under retry — a third party can add a new vendor by implementing only "create job" and "read page".
- **sources:**
  - https://developer.salesforce.com/docs/platform/api-asynch/
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/use-change-tracking-synchronize-data-external-systems?view=dataverse-latest
  - https://developers.hubspot.com/docs/api-reference/latest/crm/exports/guide
  - https://developers.hubspot.com/docs/api-reference/latest/account/account-information/guide
  - https://developers.hubspot.com/docs/developer-tooling/platform/usage-guidelines
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/query/overview?view=dataverse-latest
- **evidence:**
  - Salesforce: "Both Salesforce Bulk APIs are based on REST principles and are optimized for working with large sets
    of data. Use them to insert, update, upsert, or delete many records *asynchronously*. You submit a request and come
    back for the results later. Salesforce processes the request in the background."
  - Salesforce: "Any data operation that includes more than 2,000 records is a good candidate for Bulk API 2.0 to
    successfully prepare, execute, and manage an *asynchronous* workflow that uses the Bulk framework. Jobs with fewer
    than 2,000 records should involve 'bulkified' *synchronous* calls in REST (for example, Composite) or SOAP."
  - Salesforce: "Because both Bulk APIs are asynchronous, Salesforce doesn't guarantee a service level agreement."
  - Dataverse: "The first time you use this message, it returns all records for the table. You can use that data to
    populate the external storage. The message also returns a version number that you send back with the next use of the
    `RetrieveEntityChanges` message so that only data for those changes that occurred since that version is returned."
  - Dataverse: "Changes are returned if the last token is within a default value of seven days. The value of the
    **Organization** table `ExpireChangeTrackingInDays` column controls this duration and can be changed. If unprocessed
    changes are older than the configured value, the system throws an exception."
  - Dataverse: "If the new or updated item collection is greater than 5,000, the user can page through the
    collection." and "If a table has many existing records, creating an index can take a long time."
  - HubSpot: "To start an export, make a `POST` request to `/crm/exports/2026-09/export/async`. Your request body
    should specify information such as the file format, the object and properties you want to export, and the type of
    export you're completing (e.g., exporting an object view or a list)." … "For standard objects, you can use the
    object's name (e.g., `CONTACT`), but for custom objects, you must use the `objectTypeId` value."
  - HubSpot: "When using an **OAuth** access token to authenticate requests to the exports API, the user installing the
    app must be a **Super Admin** to grant the `crm.export` scope."
  - HubSpot: "The **daily** limit resets at midnight based on your time zone setting." and "You can also check the
    number of calls used during the current day using this endpoint."
- **gaps:** Salesforce Bulk API 2.0's concrete endpoints (`POST /services/data/vXX.X/jobs/ingest/…`,
  `…/query`) live on sub-pages under the Bulk API guide that returned 404 for every candidate path tried; only the
  guide index page was readable, so endpoint URIs are not quoted. Salesforce's
  `DailyBulkV2QueryJobs` / `DailyBulkApiBatches` / `DailyBulkV2QueryFileStorageMB` limit *names* are known from the
  Limits resource but the numeric allocations live in per-edition tables that were not fetched.

---

## 13. Throttle and retry under vendor API rate limits

- **name:** Throttle and retry under API rate limits
- **user_flow:**
  1. The room's connector keeps a per-connection token bucket sized from the vendor's published limits.
  2. Every response is inspected for the vendor's quota headers and written to the room's **Quota** meter.
  3. On a throttling response the connector applies exponential backoff with jitter, honours `Retry-After` where
     present, and defers the batch.
  4. For HubSpot's high-volume sync locks (`423`), the room inserts a delay of at least 2 seconds between requests.
  5. Admin sees remaining budget and the throttle log in **Integrations → <connection> → Quota**, and can manually
     pause/resume a connection.
- **data_flow:** request → connector token bucket → vendor quota headers on every response
  (`Sforce-Limit-Info`, `X-HubSpot-RateLimit-*`) or a `429`/`423` → backoff decision → deferred queue → retry with
  the same idempotency key (so retries never duplicate CRM rows).
- **data_sources:** vendor limit metadata (`GET /services/data/vXX.X/limits/` for Salesforce; HubSpot account
  information API and response headers); room quota store; retry queue.
- **apis_hit:**
  - Salesforce: `GET /services/data/vXX.X/limits/` (requires **View Setup and Configuration**) returning
    `DailyApiRequests`, `DailyBulkApiBatches`, `SingleEmail`, `ConcurrentSyncReportRuns`, `DataStorageMB`,
    `MaxContentDocumentsLimit`, etc. Response header `Sforce-Limit-Info: api-usage=10018/100000; api-bursts=1/750`.
  - Salesforce: `403` with `REQUEST_LIMIT_EXCEEDED`; `429`-class throttling is surfaced as `REQUEST_LIMIT_EXCEEDED`.
  - HubSpot: `X-HubSpot-RateLimit-Daily`, `-Daily-Remaining`, `-Interval-Milliseconds`, `-Max`, `-Remaining`;
    `429` body with `errorType: "RATE_LIMIT"`, `policyName: "DAILY"`, `correlationId`; `423 Locked`; `477 Migration in
    Progress` with `Retry-After`; `502/504/503/521/522/523/524/525/526` transient classes.
  - Dataverse: `429 Too Many Requests` "Expect this status code when API limits are exceeded."
- **automations:** Fully automatic. The throttle runs inside the queue worker; no user action. Quota telemetry is
  pushed to the room's metrics on an interval.
- **features_tools:** Room **Quota / Usage** dashboard; HubSpot **Development → Monitoring → API call usage** and
  **Logs** tabs; HubSpot **Development → Legacy apps → <app> → Logs**; Salesforce **Setup → Monitor → API Usage**;
  per-connection pause switch.
- **extensibility:** Rate-limit policy is a per-connector declaration (`{burst, sustained, daily, retryHints}`),
  matching the vendor's own shape — so adding a vendor means filling in a policy object, not writing a new
  backoff algorithm. The room's own idempotency layer (external ID / cursor) is what makes blind retries safe.
- **sources:**
  - https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/resources_limits.htm
  - https://developer.salesforce.com/docs/platform/api-rest/guide/headers-api-usage.html
  - https://developer.salesforce.com/docs/platform/api-rest/guide/errorcodes.html
  - https://developers.hubspot.com/docs/developer-tooling/platform/usage-guidelines
  - https://developers.hubspot.com/docs/api-reference/error-handling
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/compose-http-requests-handle-errors?view=dataverse-latest
- **evidence:**
  - Salesforce: "`GET /services/data/vXX.X/limits/` … For each limit, this resource returns the maximum allocation
    and the remaining allocation based on usage. Tabulated limits returned by the API are accurate within five minutes
    of resource consumption. For consistent values from this resource, avoid concurrent or rapid requests. … This
    resource is available in REST API version 29.0 and later for API users with the **View Setup and Configuration**
    permission."
  - Salesforce: "`DailyApiRequests` | Daily API calls" and "`DailyBulkApiBatches` (API version 49.0 and later) …
    Daily Bulk API and Bulk API 2.0 batches. In Bulk API, batches are used by both ingest and query operations. In Bulk
    API 2.0, batches are used only by ingest operations."
  - Salesforce: "**Field name**: `Sforce-Limit-Info` … **Field values**: `api-usage`—Specifies the daily API usage for
    the organization against which the call was made. The first number is the number of API calls used, and the
    second number is the API limit for the organization. … **Example**: `Sforce-Limit-Info: api-usage=10018/100000;
    api-bursts=1/750`"
  - Salesforce: "`403` The request has been refused. … If the error code is `REQUEST_LIMIT_EXCEEDED`, you've exceeded
    API request limits in your org."
  - HubSpot: "For legacy public apps, and apps on the latest versions of the developer platform (2025.2 and 2026.03)
    using **OAuth authentication** distributed via the HubSpot marketplace, each HubSpot account that installs your app
    is limited to **110 requests every 10 seconds**. This excludes the CRM Search API."
  - HubSpot (private apps): "Free and Starter | 100 / app | 250,000 / account · Professional | 190 / app | 625,000 /
    account · Enterprise | 190 / app | 1,000,000 / account … with **API Limit Increase** … 250 / app | 1,000,000 /
    account on top of your base subscription."
  - HubSpot: "Any app or integration exceeding its rate limits will receive a `429` error response for all subsequent
    API calls. Requests resulting in an error response shouldn't exceed 5% of your total daily requests." Body example:
    `{"status":"error","message":"You have reached your daily limit.","errorType":"RATE_LIMIT","correlationId":"…","policyName":"DAILY","requestId":"…"}`
  - HubSpot: "`423 Locked` | Returned when attempting to sync a large volume of data (e.g., upserting thousands of
    company records in a very short period of time). Locks will last for 2 seconds, so if you receive a `423` error,
    you should include a delay of at least 2 seconds between your API requests."
  - HubSpot: "`477 Migration in Progress` … HubSpot will return a `Retry-After` response header indicating how many
    seconds to wait before retrying the request (typically up to 24 hours)."
  - HubSpot: "To view API usage for your apps built on the new developer platform: 1. In your HubSpot account, navigate
    to **Development** … 2. In the left sidebar menu, navigate to **Monitoring** > **API call usage**."
  - HubSpot: "**Exemptions** … A high number of requests may result in `5xx` errors. These can be addressed the same as
    you would `429` errors." and "If your site or app uses data from HubSpot on each page load, that data should be
    cached and loaded from that cache instead of being requested from the HubSpot APIs each time."
  - Dataverse: "`429 Too Many Requests` Expect this status code when API limits are exceeded. For more information,
    see Service Protection API Limits."
- **gaps:** The Dataverse **Service Protection API Limits** page (per-service numeric limits) was not located at a
  readable URL — every candidate path 404'd — so no Dataverse numeric limit is quoted. Salesforce's per-edition
  numeric `DailyApiRequests` allocations likewise live in tables that were not fetched; only the *limit names* and
  the usage-header format are cited.

---

## 14. Mirror room documents into CRM files

- **name:** Mirror room documents into CRM files
- **user_flow:**
  1. Rep uploads a deck or proposal into the room, or an asset is generated (e.g. a personalised pricing PDF).
  2. Rep toggles **Push to CRM** on the asset (or the connection is configured to mirror all room documents).
  3. The room uploads the binary to the CRM files endpoint with `multipart/form-data`, then writes the returned
     file id into a CRM record — as a `file`-type property value, or into a note attached to the record.
  4. The room stores `crm_file_id` on the asset row, so re-uploads are skipped and revisions create a new file.
  5. Rep sees the CRM attachment from the record's Files panel / note timeline.
- **data_flow:** room asset (bytes + filename + mime) → `multipart/form-data` upload to the CRM files API with
  optional `folderId` and `fileName` → CRM stores the file and returns a file id → room writes that id into a
  `file`-type property (or attaches it to a note) → room records the id for idempotency.
- **data_sources:** room file/object storage (asset bytes); CRM file storage; CRM record (contact/deal) that owns the
  attachment; room asset table.
- **apis_hit:**
  - HubSpot: `POST files/2026-09/files` — "Files can be uploaded using a multipart/form-data `POST` request to
    `files/2026-09/files` with the following fields" including `file` (and optional folder / name options);
    "While a specific folder ID is not required at upload, it's recommended to upload files into a folder and not the
    root directory."
  - HubSpot: file properties — fieldType `file` "Allows for a file to be uploaded on a record or via a form. Stores
    a file ID." Attaching a file to a record is done through the **notes APIs**.
  - Salesforce: binary content can be submitted base64-encoded in an sObject body (Binary Data / blob page was
    reachable) — used for small generated assets.
  - Dataverse: no file-column page was readable; file/annotation handling is unsourced here.
- **automations:** Mirror is event-driven (asset created/updated) and can be a scheduled reconciliation pass
  ("find room assets with no `crm_file_id` and push them").
- **features_tools:** Room **Documents** page with a "Push to CRM" status chip per file; CRM **Files** panel;
  HubSpot **Content → Files**; HubSpot property `fieldType: file`.
- **extensibility:** Because the CRM side is just "store a file, get an id, put the id in a property", a
  third-party connector can support any file-capable CRM by implementing a two-method interface
  (`upload(bytes) -> fileId`, `attach(fileId, recordId)`). The room's asset table holds the vendor-agnostic
  `crm_file_id` slot, so file sync state survives connector swaps.
- **sources:**
  - https://developers.hubspot.com/docs/api-reference/latest/files/guide
  - https://developers.hubspot.com/docs/api-reference/latest/crm/properties/guide
  - https://developer.salesforce.com/docs/platform/api-rest/guide/dome-sobject-insert-update-blob.html
- **evidence:**
  - HubSpot: "Use HubSpot's files tool to store files in HubSpot for both internal and external use. For example, you
    can use the files tools to: * Insert uploaded images into content such as emails, blog posts, and website pages. *
    **Attach files to records using the notes APIs**. * Store branding assets, such as logos, icons, and banners."
  - HubSpot: "## Upload a file — Files can be uploaded using a multipart/form-data `POST` request to
    `files/2026-09/files` with the following fields. While a specific folder ID is not required at upload, it's
    recommended to upload files into a folder and not the root directory. Folder requirements at upload are subject to
    change. | `file` | The file to upload. Uploaded files are subject to the limits and supported formats …"
  - HubSpot property types: "`file` | Allows for a file to be uploaded on a record or via a form. **Stores a file ID**."
  - Salesforce: "Using sObject Collections to Insert a Collection of Blob Records" and "Using sObject Collections to
    insert blob data requires more values in the attributes map." (The blob page was readable only as a link index;
    no payload example is quoted here.)
- **gaps:** **Salesforce Files (`ContentVersion` / `ContentDocument` / `ContentDocumentLink`) could not be sourced** —
  every candidate `resources-content*` URL returned 404. **Microsoft Dataverse file/image columns and
  `annotation`/`attachment` tables could not be sourced** — the corresponding doc pages returned 404. Only HubSpot's
  Files API is fully documented for this workflow.

---

## 15. Log room engagement onto the CRM activity timeline

- **name:** Log room engagement onto the CRM activity timeline
- **user_flow:**
  1. Admin defines a custom event (name, and the properties the room will send) in the CRM's event tooling.
  2. Admin copies the event name / property keys into the room's **Integrations → <connection> → Events** mapping.
  3. Buyer performs the tracked behaviour in the room (viewed pricing page, completed a room, attended a session).
  4. The room sends an event occurrence to the CRM with the mapped properties and the buyer's CRM identity.
  5. The occurrence appears on the CRM record's timeline, and can be used in the CRM's own reporting/attribution.
- **data_flow:** room behaviour event + timestamp → event-definition name + mapped properties + contact/email →
  CRM event occurrence payload → CRM writes the occurrence onto the record's engagement history → visible on the
  record timeline.
- **data_sources:** CRM event definition; CRM contact/deal record; room event catalogue; room event rows.
- **apis_hit:**
  - HubSpot: `POST /events/v3/send` (send custom event occurrences) — "Custom events allow you to track advanced
    activity via a JavaScript or HTTP API. The Events API can be used to get details about your events."
  - HubSpot: `GET /events/v3/definitions` / event-management surface for defining the event; required scopes listed
    on the send-event-data guide.
  - HubSpot: custom code workflow action for richer server-side transforms ("HubSpot will reattempt to execute your
    action for up to three days" on 429/5xx).
- **automations:** Fires without user action — the room's own event collector posts occurrences on a debounce/flush
  timer so a burst of views becomes a small number of writes.
- **features_tools:** Room **Analytics → Events**; CRM record **Timeline / Activity** panel; HubSpot event definition
  UI; HubSpot property `type: json` for structured payloads.
- **extensibility:** The event name is configuration, so a deployment can add "Attended webinar", "Downloaded
  security whitepaper" etc. without code. HubSpot's `json` property type ("A text value stored as formatted JSON")
  lets a room send a semi-structured payload where the CRM has no typed field yet, which is the escape hatch for
  fast-moving engagement categories.
- **sources:**
  - https://developers.hubspot.com/docs/api-reference/latest/events/send-event-data/guide
  - https://developers.hubspot.com/docs/api-reference/error-handling
  - https://developers.hubspot.com/docs/api-reference/latest/crm/properties/guide
  - https://developers.hubspot.com/docs/developer-tooling/platform/usage-guidelines
- **evidence:**
  - HubSpot: "Custom events allow you to track advanced activity via a JavaScript or HTTP API. The Events API can be
    used to get details about your events."
  - HubSpot custom event limits: "There is a limit of 500 unique event definitions per account. There is a limit of
    30 million event completions per month. The send custom event completions endpoint supports up to 1250 requests per
    second. The custom event completion batch endpoint supports batches of 500."
  - HubSpot: "If you're creating a **custom code action** in a workflow, and an API call in your action fails due to a
    rate limiting error, or a `429` or `5XX` error from `axios` or `@hubspot/api-client`, HubSpot will reattempt to
    execute your action for up to three days, starting one minute after failure. Subsequent failures will be retried at
    increasing intervals, with a maximum gap of eight hours between tries."
  - HubSpot property type: "`json` | A text value stored as formatted JSON, used only for internal properties."
  - HubSpot: "Timeline events in a legacy public app are subject to the following limits: You can create up to 750
    timeline event types per public app. You can create up to 500 properties per timeline event type." (per-event size
    limits: "500 bytes for the event instance ID / 510 KB per property/token / 1 MB in total size for the event
    instance").
- **gaps:** This workflow is **HubSpot-only** in the evidence. Salesforce's equivalent (writing a `Task`/`Event` or
  a custom `Engagement__c` row, or `ActivityHistory`) and Dataverse's (`activitypointer`, `email`, `appointment`
  tables) were not sourced from a readable primary page.

---

## 16. Validate the connector against a sandbox or test account

- **name:** Validate the connector against a sandbox
- **user_flow:**
  1. Admin opens **Integrations → <connection> → Test environment** and points the connection at a non-production org.
  2. HubSpot: admin (or CI) creates a **configurable test account** that simulates a specific subscription/tier.
  3. Dataverse/Power Platform: admin converts or creates a **Sandbox** environment (copy + reset supported).
  4. The room runs **Run test sync** — the full W3→W6 pipeline against the sandbox with synthetic buyers.
  5. The room asserts: object created, dedupe key honoured, rollback fired on an intentionally bad row, and quota
     headers behaved.
  6. Only after green does the admin switch the connection to production.
- **data_flow:** integration config → environment switch (sandbox base URL / separate OAuth client) → test buyer
  dataset → same mapping + same code path as production → assertion report → promote or revert.
- **data_sources:** sandbox CRM org / HubSpot test account; room test-fixture dataset; room assertion results.
- **apis_hit:**
  - Power Platform: environment types — **Sandbox** ("nonproduction environments, which offer features like copy and
    reset"), **Default**, **Trial**, **Developer**, plus Production.
  - HubSpot: configurable test accounts created from the CLI (`hs test-account create`) or from a config file, on
    platform version `2025.2`+ and CLI `8.3.0`+; "This can be particularly useful for automated testing workflows, as
    you can define a consistent automated test account creation flow."
  - Room: the same integration endpoints as production, just pointed at a different base URL / token.
- **automations:** CI/CD — GitHub Actions creates the test account from a config file on every push, then the sync
  smoke test runs. This is the vendor-documented automation path, not an invention.
- **features_tools:** Room **Integrations → Test environment** panel; HubSpot CLI **test-account create**; Power
  Platform **Environments** admin surface; CI job in the deployment pipeline.
- **extensibility:** Because the connector is parameterised by base URL + credentials, a test environment is just
  another connection row — the open-source room can ship a "self-test" command that creates both connection rows and
  runs the fixture, making sandbox validation a first-class feature rather than an ops chore.
- **sources:**
  - https://learn.microsoft.com/en-us/power-platform/admin/environments-overview
  - https://developers.hubspot.com/docs/developer-tooling/local-development/configurable-test-accounts
  - https://developers.hubspot.com/docs/developer-tooling/local-development/agent-cli/guide
- **evidence:**
  - Power Platform: "**Sandbox** — These are nonproduction environments, which offer features like **copy and reset**.
    Sandbox environments are used for development and testing, separate from production. Provisioning sandbox
    environments can be restricted to admins (because production environment creation can be blocked), but converting
    from a production to a sandbox environment can't be blocked. Full control. If used for testing, only user access is
    needed. Developers require environment maker access to create resources."
  - Power Platform: "**Default** — This is a predefined type of environment intended for experimentation, exploration,
    and lightweight, app trial development. The default environment doesn't provide any backup guarantees and shouldn't
    be used for production workloads." and "**Trial** — Trial environments … expire after 30 days and are limited to
    one per user."
  - HubSpot: "When developing HubSpot apps, being able to test in a realistic and fully isolated environment helps
    improve app quality and minimize unexpected behavior. You can use configurable test accounts to simulate different
    HubSpot subscription and tier combinations, enabling you to test your apps more comprehensively before rolling
    changes out to production."
  - HubSpot: "Configurable test accounts can be created and managed in HubSpot or from the CLI. You can also choose to
    create your configurable test accounts from scratch or by uploading a config file, which enables fully automated
    CI/CD workflows." and "To create a configurable test account, you'll need to be developing a project on platform
    version `2025.2` or later and using CLI version `8.3.0` or later." and "In the terminal run the command below:
    `hs test-account create`".
- **gaps:** **Salesforce sandbox types and scratch orgs could not be sourced** — `meta_sandbox_types.htm` returned a
  cookie banner and all `salesforce-dx` guide paths tried returned 404. (One tangential Salesforce sandbox mention
  was readable in the Limits page — "Entitlement usage is computed only for production orgs. It's not available in
  sandbox or trial orgs" — but that is not a sandbox-lifecycle reference and is not used as the basis for this
  workflow.)

---

## 17. Monitor integration health and remaining API quota

- **name:** Monitor integration health and quota
- **user_flow:**
  1. Operator opens the room's **Integrations → <connection> → Monitoring** dashboard.
  2. Dashboard shows remaining daily + burst quota, sync success rate, mean latency, error-class breakdown
     (validation / throttle / auth / vendor-5xx), and the live change-stream lag.
  3. Quota is read from the vendor's own surfaces: Salesforce `GET /limits/` + the `Sforce-Limit-Info` header on every
     call; HubSpot response headers + the **Development → Monitoring → API call usage** page and the app **Logs** tab.
  4. If a connector is starved, the operator lowers its concurrency or pauses it from the same page.
- **data_flow:** vendor quota metadata (headers + limits endpoints) + connector telemetry → room metrics store →
  monitoring dashboard. Transformation: heterogeneous vendor quota models (daily+burst, per-10s, per-app, per-account)
  normalise into one "remaining today / remaining this window" pair.
- **data_sources:** Salesforce Limits resource; Salesforce response headers; HubSpot rate-limit headers; HubSpot
  account-information usage endpoint; Dataverse `PlatformEventUsageMetric` analogue (`EntityDefinitions`
  `ChangeTrackingEnabled`); room metrics store.
- **apis_hit:**
  - Salesforce: `GET /services/data/vXX.X/limits/`; `Sforce-Limit-Info` response header on every REST call.
  - Salesforce: `PlatformEventUsageMetric` object for event-delivery usage — "query the `PlatformEventUsageMetric`
    object. In API 58.0 and later, enable and use Enhanced Usage Metrics to get granular usage data for various time
    segments. If Enhanced Usage Metrics isn't enabled, usage data is available for the last 24 hours."
  - HubSpot: `X-HubSpot-RateLimit-{Max,Remaining,Interval-Milliseconds,Daily,Daily-Remaining}`; the account
    information API "provide[s] information about a given HubSpot account, including the account settings, and the
    **daily API usage and limits**".
  - HubSpot UI: **Development → Monitoring → API call usage**, **Development → Monitoring → Logs**,
    **Development → Legacy apps → <app> → Logs**.
  - Dataverse: `GET /api/data/v9.2/EntityDefinitions?$select=SchemaName&$filter=ChangeTrackingEnabled eq true` to audit
    which tables are being tracked.
- **automations:** Quota polling on a fixed interval; alert rules fire when remaining budget crosses a threshold or
  when the change-stream lag exceeds N seconds. Dataverse's `Microsoft.Dynamics.CRM.globalmetadataversion` annotation
  is the schema-drift signal ("The value changes when any schema change occurs, indicating that you might need to
  refresh any schema data that your application cached").
- **features_tools:** Room **Monitoring** dashboard; HubSpot **Development → Monitoring**; Salesforce
  **Setup → Monitor → API Usage**; room alert rules (Slack/email/webhook).
- **extensibility:** Because monitoring is fed by a connector-declared quota policy (the same object used in W13),
  there is exactly one place to teach the system a new vendor's numbers. A deployment can also self-host the
  dashboard and export metrics in any format, which is the difference between an open-source room and a
  vendor-locked one.
- **sources:**
  - https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/resources_limits.htm
  - https://developer.salesforce.com/docs/platform/api-rest/guide/headers-api-usage.html
  - https://developer.salesforce.com/docs/platform/change-data-capture/guide/cdc-intro.html
  - https://developers.hubspot.com/docs/developer-tooling/platform/usage-guidelines
  - https://developers.hubspot.com/docs/api-reference/latest/account/account-information/guide
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/compose-http-requests-handle-errors?view=dataverse-latest
- **evidence:**
  - Salesforce: "List information about limits in your org. For each limit, this resource returns the maximum
    allocation and the remaining allocation based on usage. Tabulated limits returned by the API are accurate within
    five minutes of resource consumption. For consistent values from this resource, avoid concurrent or rapid
    requests."
  - Salesforce: "**Limit Info Header** — This response header is returned in each request to REST API (except for
    calls to the Versions URI, `/`, which do not count towards your org's limit). You can use the information to
    monitor API limits."
  - Salesforce: "Monitor Change Event Publishing and Delivery Usage … query the `PlatformEventUsageMetric` object …
    PlatformEventUsageMetric is available in API version 50.0 and later."
  - Salesforce (Limits resource): "`DataStorageMB` | Data storage (MB). The API user must have the **Manage Users**
    permission." and "`SingleEmail` | Daily number of single emails that are sent to external email addresses."
  - HubSpot: "**To view API usage for your apps built on the new developer platform:** 1. In your HubSpot account,
    navigate to **Development** in the main navigation bar. 2. In the left sidebar menu, navigate to **Monitoring** >
    **API call usage**. 3. Your API usage across all your apps will be listed at the top of the page."
  - HubSpot: "**To view API usage for a public app using OAuth:** 1. … navigate to **Development** … 2. In the left
    sidebar menu, navigate to **Monitoring**, then select **Logs**. 3. At the top, select the **name** of the app. 4. Use
    the **tabs** to view different types of requests being made to or from the app."
  - HubSpot: "The following **Header** | **Description** table: `X-HubSpot-RateLimit-Interval-Milliseconds` | The window
    of time that the `X-HubSpot-RateLimit-Max` and `X-HubSpot-RateLimit-Remaining` headers apply to. … a value of 10000
    would be a window of 10 seconds." … "`X-HubSpot-RateLimit-Daily` | The number of API requests that are allowed
    per day. Note that this header is not included in the response to API requests authorized using **OAuth**."
  - HubSpot: "Super Admins can review API call usage." and "HubSpot's account information API endpoints provide
    account configuration and usage data … the account settings, and the daily API usage and limits for legacy private
    apps."
  - Dataverse: "`Microsoft.Dynamics.CRM.globalmetadataversion` | You can cache this annotation in your application. The
    value changes when any schema change occurs, indicating that you might need to refresh any schema data that your
    application cached."
- **gaps:** Dataverse's per-service numeric limit table (Service Protection API Limits) was not found at a readable
  URL. HubSpot's newer GraphQL/`account-information` usage fields were not enumerated in the page read.

---

## 18. Reconcile gaps and overflows after a dropped change stream

- **name:** Reconcile gaps after a dropped change stream
- **user_flow:**
  1. The room's subscriber notices a **gap event** (`GAP_CREATE` / `GAP_UPDATE` / `GAP_DELETE` / `GAP_UNDELETE`) — a
     change the CRM could not emit normally.
  2. Room marks the affected record **dirty** and stops applying incremental change events for that record.
  3. Room makes a full CRM API read of that record id and reconciles its replica; then clears the dirty flag.
  4. If the stream instead reports an **overflow event** (`GAP_OVERFLOW`, >100,000 changes in one transaction), the
     room unsubscribes, stores the overflow event's replay id, and re-reads the whole entity.
  5. Room re-applies the new/updated/undeleted set, then derives the deleted set by set-difference (or by querying
     `isDeleted=true`) and deletes from the replica.
  6. Room resubscribes and records a reconciliation event in the sync log for audit.
- **data_flow:** change-event header (`changeType`, `transactionKey`, `commitTimestamp`, `recordIds`) → gap/overflow
  classification → dirty-set / replay-id store → full entity read (or per-record read) → replica overwrite + delete
  diff → resubscribe. Transformation: change events are *deltas*; reconciliation is a *full re-read*, so the room
  needs a read path (W9) as the repair mechanism.
- **data_sources:** CDC change event stream; CRM entity tables (incl. Recycle Bin / soft-deleted); room replica;
  dirty-set and replay-id store.
- **apis_hit:**
  - Salesforce: CDC **gap events** and **overflow events**; replay id on the event; per-record re-read via the REST
    Query resource; `isDeleted=true` query against the Recycle Bin.
  - Dataverse: delta-link response includes deleted entities — `"@odata.context":"…/accounts/$deletedEntity"`,
    `"id": "2e451703-…"`, `"reason": "deleted"` — so deletes arrive through the same cursor.
- **automations:** Entirely automatic and event-driven. The overflow path includes a **resubscribe** step and a
  bounded replan; the gap path is a per-record repair. Dataverse's token expiry (default 7 days) is the hard deadline
  after which recovery must fall back to a full re-read.
- **features_tools:** Room **Sync log → reconciliation events**; room **Data health** view showing dirty records;
  Dataverse delta-link store; Salesforce replay-id store.
- **extensibility:** Because reconciliation is expressed as "re-read + set-difference", the same code serves three
  cases: a single gap, an overflow, and a full reconnect. A third party can add a "clock skew detector"
  (compare `commitTimestamp` against local apply time) to detect a *silently* missed window — the same primitive the
  docs use to decide whether a new change is older than the reconciliation.
- **sources:**
  - https://developer.salesforce.com/docs/platform/change-data-capture/guide/cdc-replication-steps.html
  - https://developer.salesforce.com/docs/platform/change-data-capture/guide/cdc-intro.html
  - https://learn.microsoft.com/en-us/power-apps/developer/data-platform/use-change-tracking-synchronize-data-external-systems?view=dataverse-latest
- **evidence:**
  - Salesforce: "Gap events are generated when change events can't be generated. They inform subscribers about errors or
    operations done outside of Salesforce application servers. Gap events don't contain record data, but they contain
    the record ID, which enables you to retrieve the record from Salesforce. … The `changeType` field in the gap event
    header identifies the gap event and the associated operation, and can take one of these values: `GAP_CREATE`,
    `GAP_UPDATE`, `GAP_DELETE`, `GAP_UNDELETE`."
  - Salesforce: "Overflow events are generated when a single transaction involves more than 100,000 changes. The first
    100,000 changes generate change events. The set of changes beyond that amount generates one overflow event for each
    entity type included in that set. Overflow events include header fields but no record data and no record ID. … The
    `changeType` field header value is `GAP_OVERFLOW`."
  - Salesforce: "If you choose not to use a transaction-based replication process, your replicated data can be
    incomplete if your subscription stops. For example, if your subscription stops in the middle of an event stream for
    one transaction, only part of the transaction's changes are replicated in your system."
  - Salesforce: "For the gap event, mark the corresponding record as dirty locally as of the date of the gap event. …
    If you receive change events for new changes for the same record before the data has been reconciled, don't process
    them. … To ensure that the change is after the gap event, compare the `commitTimestamp` fields of both events. To
    ensure that the change occurred before the data is reconciled, compare the `LastModifiedDate` fields on the change
    event and the record retrieved in the next step. … Reconcile the data for record C. Make a Salesforce API call, such
    as a REST API call, to retrieve the full data for record C, and save it in your system. Then clear the dirty flag
    on that record."
  - Salesforce overflow procedure: "1. After you receive an overflow event in your subscriber, unsubscribe from the
    channel, and stop processing further events. … 2. Store the Replay ID of the overflow event. This ID is the
    starting point for the data reconciliation. 3. Reconcile the data for new, updated, and undeleted records. …
    4. Reconcile the data for deleted records by performing one of the following steps: a. Get the non-deleted records
    from Salesforce, and synchronize. … b. Or get the deleted records from Salesforce, and synchronize. … Query all
    records for the entity with `isDeleted=true`. You get all the soft-deleted records for that entity that are in the
    Recycle Bin."
  - Salesforce: "Each change event contains a transaction key in the header that uniquely identifies the transaction
    that the change is part of. Each change event also contains a sequence number that identifies the sequence of the
    change within a transaction. … If not all objects involved in a transaction are enabled for Change Data Capture,
    there will be a gap in the sequence numbers. We recommend that you replicate all the changes in one transaction as
    a single commit in your system."
  - Dataverse: the delta-link response surfaces deletes inline:
    `{"@odata.context":"…/api/data/v9.0/$metadata#accounts(name,telephone1,fax)/$delta","@odata.deltaLink":"…","value":[{…},{"@odata.context":"…/accounts/$deletedEntity","id":"2e451703-c686-e711-80e5-00155db19e6d","reason":"deleted"}]}`
  - Dataverse: "Changes are returned if the last token is within a default value of seven days. … If unprocessed
    changes are older than the configured value, the system throws an exception."
- **gaps:** The overflow `Replay ID` mechanism is described for the Salesforce Pub/Sub/CometD subscriber model; the
  equivalent resumable-position primitive for Dataverse (delta link) is a different shape (opaque token, no per-entity
  replay ids) — the two were not reconciled in a single source. HubSpot has no documented gap/overflow analogue
  (it uses webhook redelivery instead, see W11's retry evidence), so this workflow is Salesforce- and
  Dataverse-specific.

---

## Appendix — Source index (all URLs read for this document)

**Salesforce (developer.salesforce.com)**
1. https://developer.salesforce.com/docs/platform/api-rest/guide/
2. https://developer.salesforce.com/docs/platform/api-rest/guide/intro-oauth-and-connected-apps.html
3. https://developer.salesforce.com/docs/platform/api-rest/guide/intro-rest-resources.html
4. https://developer.salesforce.com/docs/platform/api-rest/guide/errorcodes.html
5. https://developer.salesforce.com/docs/platform/api-rest/guide/headers.html
6. https://developer.salesforce.com/docs/platform/api-rest/guide/headers-api-usage.html
7. https://developer.salesforce.com/docs/platform/api-rest/guide/headers-duplicaterules.html
8. https://developer.salesforce.com/docs/platform/api-rest/guide/requests-composite.html
9. https://developer.salesforce.com/docs/platform/api-rest/guide/resources-composite-allornone.html
10. https://developer.salesforce.com/docs/platform/api-rest/guide/resources-composite-sobject-tree.html
11. https://developer.salesforce.com/docs/platform/api-rest/guide/resources-composite-sobjects-collections-upsert.html
12. https://developer.salesforce.com/docs/platform/api-rest/guide/resources-sobject-upsert.html
13. https://developer.salesforce.com/docs/platform/api-rest/guide/resources-sobject-upsert-patch.html
14. https://developer.salesforce.com/docs/platform/api-rest/guide/resources-query.html
15. https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/requests_composite.htm
16. https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/resources_limits.htm
17. https://developer.salesforce.com/docs/platform/change-data-capture/guide/cdc-intro.html
18. https://developer.salesforce.com/docs/platform/change-data-capture/guide/cdc-subscribe.html
19. https://developer.salesforce.com/docs/platform/change-data-capture/guide/cdc-subscribe-pubsub-api.html
20. https://developer.salesforce.com/docs/platform/change-data-capture/guide/cdc-replication-steps.html
21. https://developer.salesforce.com/docs/platform/change-data-capture/guide/cdc-enrich-intro.html
22. https://developer.salesforce.com/docs/platform/api-asynch/
23. https://developer.salesforce.com/docs/platform/api-rest/guide/dome-sobject-insert-update-blob.html

**Microsoft (learn.microsoft.com)**
24. https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/reference/upsertmultiple?view=dataverse-latest
25. https://learn.microsoft.com/en-us/power-apps/developer/data-platform/define-alternate-keys-entity?view=dataverse-latest
26. https://learn.microsoft.com/en-us/power-apps/developer/data-platform/use-change-tracking-synchronize-data-external-systems?view=dataverse-latest
27. https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/query/overview?view=dataverse-latest
28. https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/query/filter-rows?view=dataverse-latest
29. https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/execute-batch-operations-using-web-api?view=dataverse-latest
30. https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/create-entity-web-api?view=dataverse-latest
31. https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/compose-http-requests-handle-errors?view=dataverse-latest
32. https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/web-api-service-documents
33. https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/query-metadata-web-api
34. https://learn.microsoft.com/en-us/power-platform/admin/environments-overview
35. https://learn.microsoft.com/en-us/power-apps/developer/data-platform/introduction-solutions
36. https://learn.microsoft.com/en-us/power-apps/developer/data-platform/bulk-operations?view=dataverse-latest
37. https://learn.microsoft.com/en-us/power-apps/developer/data-platform/use-upsert-insert-update-record?view=dataverse-latest
38. https://learn.microsoft.com/en-us/power-apps/developer/data-platform/use-alternate-key-reference-record?view=dataverse-latest
39. https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/authenticate-web-api?view=dataverse-latest
40. https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/web-api-types-operations?view=dataverse-latest

**HubSpot (developers.hubspot.com / knowledge.hubspot.com)**
41. https://developers.hubspot.com/docs/llms.txt
42. https://developers.hubspot.com/docs/api-reference/legacy/crm/objects/contacts
43. https://developers.hubspot.com/docs/api-reference/latest/crm/objects/custom-objects/guide
44. https://developers.hubspot.com/docs/api-reference/latest/crm/properties/guide
45. https://developers.hubspot.com/docs/api-reference/latest/crm/using-object-apis
46. https://developers.hubspot.com/docs/api-reference/latest/crm/search-the-crm
47. https://developers.hubspot.com/docs/api-reference/latest/crm/exports/guide
48. https://developers.hubspot.com/docs/api-reference/latest/crm/owners/guide
49. https://developers.hubspot.com/docs/api-reference/latest/files/guide
50. https://developers.hubspot.com/docs/api-reference/latest/events/send-event-data/guide
51. https://developers.hubspot.com/docs/api-reference/latest/account/account-information/guide
52. https://developers.hubspot.com/docs/api-reference/error-handling
53. https://developers.hubspot.com/docs/developer-tooling/platform/usage-guidelines
54. https://developers.hubspot.com/docs/developer-tooling/local-development/configurable-test-accounts
55. https://developers.hubspot.com/docs/apps/developer-platform/build-apps/authentication/oauth/working-with-oauth
56. https://knowledge.hubspot.com/workflows/how-do-i-use-webhooks-with-hubspot-workflows

## Appendix — Consolidated gap list

- **Salesforce Metadata API** (custom object/field deploy, `meta_*` pages) — client-rendered, cookie banner only.
  Blocks the Salesforce half of W3.
- **Salesforce Identity/OAuth flow detail** (PKCE, web-server flow, refresh rotation) — in `help.salesforce.com`,
  JS shell. Salesforce OAuth is covered only at the conceptual level in W1.
- **Salesforce Outbound Messages / Flow HTTP callout** — unreadable. W11 is HubSpot-only in evidence.
- **Salesforce Files** (`ContentVersion`/`ContentDocument`/`ContentDocumentLink`) — all candidate URLs 404'd. W14 is
  HubSpot-only in evidence.
- **Salesforce Sandbox / Scratch Org** — unreadable. W16 is HubSpot + Power Platform only.
- **Salesforce Bulk API 2.0 endpoint URIs** — only the guide index was readable; sub-pages 404'd. W12 quotes the
  intro, not endpoints.
- **HubSpot webhook subscriptions REST guide** — `/docs/api-reference/latest/webhooks` is client-rendered and the
  published spec URLs 404'd. Webhook *endpoints/methods* are not claimed; only workflow-webhook action + limits.
- **Dataverse webhooks / Service Bus notifications** — no readable page found.
- **Dataverse file & image columns / annotation & attachment tables** — candidate URLs 404'd.
- **Dataverse Service Protection API Limits** (per-service numeric limits) — not located at a readable URL.
- **Dataverse `ActivityPointer` / activity timeline** — not sourced; W15 is HubSpot-only.
- **MS Learn doc-body extraction** for items 36–40 was not performed (pages confirmed reachable, bodies not read) —
  no claims in this document rest on them.
