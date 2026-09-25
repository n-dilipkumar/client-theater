# Domain 4 — Scheduling & Meetings (raw research)

Scope: embedded calendars, meeting booking + routing, calendar availability sync, video-conference
integration, mutual action plan approvals with e-signature, reminders/nudges, post-meeting follow-up.

All 18 workflows below are traceable to vendor product docs, vendor API reference docs, or first-party
platform API docs that were actually read. Quotes in `evidence` are verbatim where quoted.

Primary vendor corpora used:
- **Chili Piper** (routing + scheduling platform) — `help.chilipiper.com` (Zendesk help center, public)
- **Gong** (revenue intelligence / Engage / consent meetings) — `help.gong.io` (public, incl. `apidocs/`)
- **Cal.com** (open-source scheduler) — `cal.com/docs` (public, llms.txt-indexed OpenAPI reference)
- **Documenso** (open-source e-signature) — `docs.documenso.com`
- **Google Calendar API v3** — `developers.google.com`
- **Microsoft Graph v1.0** — `learn.microsoft.com`

---

## 1. Route and book a demo request inline from a web form

- **name**: Route-and-book a web form request inline
- **user_flow**:
  1. Marketing/admin builds a **Concierge Router** in Chili Piper (`Concierge` → `Concierge Router` → `Create Concierge Flow`). First node is always `Trigger`; enables `Webform is submitted`, `In-app`, and/or `Router Link`.
  2. Admin maps the webform fields to Chili Piper **Data Fields** in the Trigger node (via `Find Form` → `Map Fields` automap, or `Add Mapping` manual mapping).
  3. Admin adds `Routing Rule` / `Catch All` nodes. Rules are either **CRM Ownership** rules (check Lead/Contact/Account owner against a **Team**) or **Without Ownership** rules (CRM values or Data Field values).
  4. On a rule match, admin adds a `Display Calendar` node choosing **Owner**, **Round-Robin**, or **Individual user**, plus the **Meeting Type(s)** to offer.
  5. The router is published and deployed (embedded/deployed to web form, in-app button, or a router link).
  6. *Prospect* fills the webform → is qualified and routed automatically → the router shows the correct calendar to "instantly book a time — all in seconds". Prospect picks a slot and submits a guest form.
  7. The router's default post-booking nodes fire (e.g. `Create Event`, `Update Field`, `Update Ownership`) and/or `Redirect To` a thank-you page.
- **data_flow**: webform POST → Chili Piper Trigger (form-field → Data Field mapping) → routing rule evaluation against (a) Chili Piper Data Fields and (b) live CRM object values (Lead/Contact/Account/Opportunity/Case, incl. Salesforce Lead-to-Account matching) → chosen calendar/Meeting Type/assignee → availability engine (Google/Outlook calendars) → slot list rendered in the `Display Calendar` modal → prospect submits guest form → meeting record created → calendar invites issued → CRM writeback nodes write Event/fields/owner. Guests become the `primaryGuestDataFields` payload in webhooks.
- **data_sources**: marketing webform (HTML field names); Chili Piper **Data Fields** (Command Center, mapped to CRM fields); CRM objects — Salesforce `Lead`, `Contact`, `Account`, `Opportunity`, `Case`, `Campaign`, `Event`; HubSpot `Contact`, `Company`, `Deal`, `Ticket`; Google/Outlook calendars; Slack (optional notify channel).
- **apis_hit**:
  - `POST https://fire.chilipiper.com/api/fire-edge/v1/org/concierge/routers/[routerSlug]/rest` — route/qualify a lead; returns `routeId`, `routingLink`, `schedulingAllowed`, `assignment{userId,type}`.
  - `POST .../concierge/routing/[routeId]/schedule-simple` — commit the chosen slot; returns `meetingId`.
  - Edge API token with `Concierge.schedule` scope, generated in `Command Center > Credentials`.
  - `Custom API` tab inside the router's `Booking Page` settings surfaces a copyable endpoint URL + starter request body.
- **automations**: rules fire on form submit; the `Display Calendar` node has a **Time Elapsed** timer — when it expires "the meeting will be considered not scheduled, and you can notify your rep to follow up"; `Not Scheduled` paths run `Assign To` (distribute the prospect) and `Send Notification` (email or Slack); `Redirect To` has its own countdown timer before bouncing the prospect.
- **features_tools**: Concierge Flow Builder, Trigger node, Data Fields, Routing Rule / Catch All nodes, Node Combination shortcuts, `Display Calendar`, `Redirect To`, `Assign To`, `Send Notification`, Router Deployment, `Customization` tab (Scheduling/Confirmation modal look & feel), workspace/Command Center Branding.
- **extensibility**: Data Fields are reusable across Chili Piper products (Chat, ChiliCal); rules can mix Data Field conditions with CRM object conditions; **Assignment Tables** let one path serve every territory ("one path can serve every territory"); admins can predefine CRM-sync behaviours ("as these settings are applied to all users in your org"); the same router is invocable headlessly via Edge API and MCP (`concierge-route-by-slug`, `concierge-schedule`).
- **sources**:
  - https://help.chilipiper.com/hc/en-us/articles/28522554434323-Creating-a-Concierge-Flow
  - https://help.chilipiper.com/hc/en-us/articles/30935152032275-Using-Concierge-via-the-Edge-API
  - https://help.chilipiper.com/hc/en-us/articles/50860790980627-How-do-I-Schedule-Chili-Piper-Meetings-Programmatically
- **evidence**:
  - "A Concierge Router is an online scheduler that can be integrated with your form or application or shared via a link. Once a prospect submits a form, the Concierge automatically qualifies them, routes them to the correct salesperson, and displays a calendar to instantly book a time — all in seconds."
  - "Trigger will **always** be your first node in a Concierge Router, and it indicates which action will make your router be triggered"
  - "Each router **must** end with a '**Catch All**' path to make sure you define the routing and acknowledge all inbound Leads."
  - Edge API response sample: `"routeId": "9413f879-...", "routingLink": "https://your-tenant.chilipiper.com/concierge-router/[routerSlug]/routing/9413f879-...", "schedulingAllowed": true, "assignment": { "userId": "...", "type": "user" }`

---

## 2. Qualify a lead without offering any calendar

- **name**: Qualify-and-assign without scheduling
- **user_flow**:
  1. Admin/engineer calls the Concierge router Edge API **without** an `interval` in the body.
  2. Response returns the resolved assignee (`assignment.userId`, `assignment.type`) and `schedulingAllowed`, plus a `routingLink` the lead can be redirected to later.
  3. Backend decides whether to surface a scheduler at all — or simply writes the qualification result into the CRM.
  4. "No routing session is consumed until scheduling occurs." (i.e. this call is side-effect-free with respect to booking.)
- **data_flow**: form/lead payload → routing-rule evaluation (CRM + Data Fields) → qualification verdict + proposed owner → either (a) `routingLink` returned to caller for a later redirect, or (b) a direct CRM write. The `routeId` is returned but no slot list is computed and no session is consumed.
- **data_sources**: same as #1 (webform, Data Fields, CRM objects, calendars) — but availability is *not* queried.
- **apis_hit**: `POST https://fire.chilipiper.com/api/fire-edge/v1/org/concierge/routers/[routerSlug]/rest` **without** `interval` (the docs call this the "Return a booking URL" / embed pattern). Distinguishing factor: "the difference is whether you pass an `interval`."
- **automations**: none fire beyond rule evaluation — deliberately. No reminder, no assign, no calendar side effects.
- **features_tools**: Edge API `Custom API` tab; `routingLink`; CRM writeback nodes available separately on `Not Scheduled` / `Disqualified` paths.
- **extensibility**: lets an AI assistant or backend agent screen and rank leads cheaply, then only open the scheduler for qualified ones; `schedulingAllowed` is the signal to gate on.
- **sources**:
  - https://help.chilipiper.com/hc/en-us/articles/30935152032275-Using-Concierge-via-the-Edge-API
- **evidence**:
  - "## Qualify a Lead Without Scheduling — Confirm a lead is routable; See which user they would be assigned to; Decide whether to surface a scheduler. No routing session is consumed until scheduling occurs."
  - Access-pattern table: "Return a booking URL | You can redirect the lead to a Chili Piper page to complete scheduling | `form` data (no `interval`) | A `routingLink` to redirect the lead to" vs "Schedule programmatically | ... | `form` data **plus** `interval` | Available `startTimes` plus a `routingId` for the second call".
  - "The Edge API lets you run a Concierge router from outside the Chili Piper UI ... There are two ways to use it: Return a booking URL that you redirect the lead to (the embed pattern); Schedule a meeting programmatically without any UI (the no-UI pattern)."

---

## 3. Route a booking to the owner of the CRM record

- **name**: Book with the CRM record owner
- **user_flow**:
  1. Admin creates an **Ownership** scheduling link (link type `Ownership`).
  2. Prospect lands on the link (or a backend calls the API), and Chili Piper resolves the owner of the matching CRM record — "lead, contact, or account owner, resolved at booking time".
  3. For the API path, the caller **must** pass `guestEmail` in the init call "so Chili Piper can resolve the owner from your CRM".
  4. Availability is read from that owner's connected calendar, the prospect books, and the owner gets the meeting.
  5. Alternatively a **CRM Ownership** routing rule in a Concierge router can check whether a rep from Team A owns the Lead, Contact **or** Account, and route to that rep.
- **data_flow**: guest email (or CRM record id) → CRM lookup for owner id → owner's calendar availability → slot list → booking → (optionally) `Update Ownership` node reassigns the CRM record to the rep who got the meeting.
- **data_sources**: CRM `Lead`/`Contact`/`Account` owner fields; Google/Outlook calendar of the resolved owner; Chili Piper workspace + Team (Distribution) records; Salesforce Lead-to-Account (L2A) matching setting.
- **apis_hit**:
  - `POST https://fire.chilipiper.com/api/fire-edge/v1/org/schedulingLinks/init-simple` with `{ "link": { "type": "Ownership", "linkId": "..." }, "guestEmail": "lead@example.com", "interval": {...} }`
  - `POST .../schedulingLinks/routing/[routeId]/schedule-simple` with `{ "startTime", "guestEmail" }`
  - Discovery: `scheduling-link-list-ownership` (Edge API operation id) to enumerate Ownership links programmatically.
- **automations**: `Update Ownership` node — "Set Chili Piper to update the owner of a record in Salesforce to the rep who got the meeting". Explicit guardrail: "you should not use this node in **Ownership** paths. If you have any Assign To nodes in Ownership-related paths, this could prevent your Router from being published."
- **features_tools**: Scheduling Links (types: Personal / Admin (one-on-one) / Round Robin / Group / Ownership), `Display Calendar` node, Teams/Distributions, Command Center > Data Fields.
- **extensibility**: Teams + Distributions can hold many owners; routes may be pre-resolved from your own CRM ("a lead-owner link resolved from your CRM") rather than letting Chili Piper do the lookup.
- **sources**:
  - https://help.chilipiper.com/hc/en-us/articles/50860790980627-How-do-I-Schedule-Chili-Piper-Meetings-Programmatically
  - https://help.chilipiper.com/hc/en-us/articles/28522554434323-Creating-a-Concierge-Flow
- **evidence**:
  - "**Ownership** – routes to the owner of the guest's CRM record (lead, contact, or account owner), resolved at booking time."
  - "For **Ownership** links, also pass `guestEmail` in the init call – it is required so Chili Piper can resolve the owner from your CRM."
  - "The rule example below checks whether one of the reps from Team A owns the Lead, Contact, **or** Account object associated with the prospect."
  - "For Lead-to-Account (L2A) Matching in **Salesforce**, we will use the existing settings defined in your workspace."

---

## 4. Spread bookings across a team by availability-weighted round robin

- **name**: Distribute bookings across a team by round robin
- **user_flow**:
  1. Admin creates a Team and a **Round Robin** distribution (Strict = equal turns, or Flexible = weighted by availability) with per-member weights and credits. Link type is `RoundRobin`.
  2. Prospect opens the team link (or `POST .../schedulingLinks/init-simple` with `{"link":{"type":"RoundRobin","linkId":...}}`).
  3. Chili Piper evaluates the distribution and returns a single combined availability window.
  4. Prospect books; the chosen member is credited, and the distribution advances.
  5. If a rep no-shows, an admin marks the prospect No-Show in Meetings Activity so credits can be credited back.
- **data_flow**: team membership + weights/credits → distribution algorithm → union/intersection of member calendars → slot list → booking → credit consumed on the selected member (or credited back on no-show) → the same `Distribution` context is reused later for reassignment.
- **data_sources**: Chili Piper Team + Distribution records (weights, credits, round-robin type), member Google/Outlook calendars, Meetings Activity no-show flags, CRM owner fields.
- **apis_hit**: `POST /api/fire-edge/v1/org/schedulingLinks/init-simple`; `POST /api/fire-edge/v1/org/schedulingLinks/routing/[routeId]/schedule-simple`; discovery op `scheduling-link-list-round-robin`.
- **automations**: distribution state advances on each booking; no-show credit-back is admin-triggered but the Meeting Type flag ("if the Distribution associated with the meeting is set to credit back assignees for No-Shows") makes it a standing rule.
- **features_tools**: `Display Calendar` → "Team or Team Member via Round Robin" option, Distributions (Team, Round Robin type, member weights/credits), `Assign To` node record distribution, Meetings Activity dashboard, Concierge Analytics.
- **extensibility**: separate discovery endpoints per link type let a third-party build a custom routing UI over Chili Piper; Distributions are reusable assets independent of the router.
- **sources**:
  - https://help.chilipiper.com/hc/en-us/articles/50860790980627-How-do-I-Schedule-Chili-Piper-Meetings-Programmatically
  - https://help.chilipiper.com/hc/en-us/articles/28522554434323-Creating-a-Concierge-Flow
  - https://help.chilipiper.com/hc/en-us/articles/31428605286931
- **evidence**:
  - "**Round Robin** – a team link that rotates assignment across members, either strict (equal turns) or flexible (weighted by availability)"
  - "**Team or Team Member via Round Robin:** You can route through your team members using a Round Robin distribution. Select this option and add a distribution: each distribution consists of a Team, Round Robin type (Strict or Flexible), and Team member's weights and credits."
  - "**All Team Members or Individuals you have assigned on this path must have a Concierge license assigned to them. Otherwise, if any prospects match to an unlicensed user, they will not be able to book a meeting and route to the Not Scheduled path.**"
  - "**Mark as No-Show** ... This is especially useful if the Distribution associated with the meeting is set to credit back assignees for No-Shows."

---

## 5. Hand a lead off from an SDR scheduler to an AE

- **name**: Handoff-schedule a lead from SDR to AE
- **user_flow**:
  1. Admin builds a **Handoff Router** in the workspace, defining routing paths (e.g. region → AE pod, product line → AE).
  2. An SDR opens the Handoff scheduler (MyApp / ChiliCal) and enters the guest's email, or the CRM record id.
  3. Chili Piper evaluates the router and returns **one or more routing paths, each with its own `pathId` and `startTimes`**.
  4. SDR picks a path and a slot; the meeting is booked with the AE and the SDR is the booker.
- **data_flow**: guest email or `{type:"CrmRequest", id}` → Handoff router rule evaluation (Handoff/ChiliCal user controls + Distributions) → per-path availability → SDR selects `routingId`/`routerId`/`pathId`/`startTime` → meeting created with SDR as Booker and AE as Assignee → optional extra invitees.
- **data_sources**: Chili Piper workspace, routers, paths, Distributions, user records; CRM Lead/Contact record for the `CrmRequest` form; member calendars.
- **apis_hit**:
  - `POST /api/fire-edge/v1/org/handoff/workspace/{workspaceId}/booker/{userId}/init-simple` with either `{type:"GuestEmailRequest", guestEmail, interval}` or `{type:"CrmRequest", id, interval}`, plus optional `routerId` and `crmExplicits` (extra CRM context for routing rules). Response: `routingId` + `routers[].pathResults[].startTimes`.
  - `POST /api/fire-edge/v1/org/handoff/routing/{routingId}/router/{routerId}/path/{pathId}/booker/{userId}/schedule-simple` with `{startTime}`.
  - Lookup ops: `workspace-list`, `user-find`.
- **automations**: routing path selection is automatic; reassignment later respects "your Handoff/ChiliCal User controls and the Distribution settings of the meeting booked".
- **features_tools**: Handoff Scheduler, Handoff router paths, Distributions, `Additional Invitee(s)` (e.g. always invite an SE or manager; toggle `Required` to include their availability).
- **extensibility**: `crmExplicits` lets an integrator pass arbitrary CRM context into routing rules; workspaces partition SDR/AE pods so a third party can run one router per pod.
- **sources**:
  - https://help.chilipiper.com/hc/en-us/articles/50860790980627-How-do-I-Schedule-Chili-Piper-Meetings-Programmatically
  - https://help.chilipiper.com/hc/en-us/articles/42613941250835-Reassigning-Meetings
  - https://help.chilipiper.com/hc/en-us/articles/28522554434323-Creating-a-Concierge-Flow
- **evidence**:
  - "**Handoff** | **When to use:** a rep is handing off a lead to another team member – for example, an SDR booking a discovery call with an AE. Handoff uses the rules in a Handoff router to evaluate availability and returns time slots per routing path."
  - "The init response returns one or more routing paths, each with its own `pathId` and `startTimes`. Pick a path and a slot, then pass the corresponding `routingId`, `routerId`, `pathId`, and `startTime` to the schedule call."
  - "`crmExplicits` – additional CRM context to pass through to routing rules."
  - "Chili Piper will not consider their availability while displaying the calendar unless you toggle the Required button."

---

## 6. Book a meeting with no scheduling UI (headless / AI agent)

- **name**: Book headlessly via API or MCP
- **user_flow**:
  1. Admin generates a scoped API token in `Command Center > Credentials` (`Generate Token`), choosing the `Schedule` permission for the relevant section (Concierge / Scheduling-links / Handoff) plus `Read` where listing assets is needed. Token is shown once. Admins only.
  2. Backend / custom frontend / AI assistant makes call #1: **discover or route** — returns a `routeId` and a list of `startTimes` under `schedulingData`.
  3. Caller picks a `startTime` and makes call #2: **book** — passes `routeId` + `startTime`; response includes a `meetingId`.
  4. "Calendar invites are sent immediately."
  5. If step 2's session expired or the slot was taken, the caller re-runs step 1 with a fresh session (sessions are single-use and short-lived).
- **data_flow**: lead/link/handoff payload + `interval{startsAt,duration}` → routing + availability engine → `routeId` + slot list → `startTime` commit → meeting record + calendar invites + optional CRM writeback → webhook `Created` push to downstream systems.
- **data_sources**: Chili Piper routers/scheduling links/workspaces; CRM (for CRM-matched routing and `guestEmail` ownership resolution); Google/Outlook calendars; Zoom/GMeet/Gong providers for the meeting link; customer webhook endpoint.
- **apis_hit**:
  - Concierge: `POST /api/fire-edge/v1/org/concierge/routers/[routerSlug]/rest` → `POST /api/fire-edge/v1/org/concierge/routing/[routeId]/schedule-simple`
  - Links: `POST /api/fire-edge/v1/org/schedulingLinks/init-simple` → `POST /api/fire-edge/v1/org/schedulingLinks/routing/[routeId]/schedule-simple`
  - Handoff: `POST .../handoff/workspace/{workspaceId}/booker/{userId}/init-simple` → `POST .../handoff/routing/{routingId}/router/{routerId}/path/{pathId}/booker/{userId}/schedule-simple`
  - MCP tools mirror these: `concierge-route-by-slug`, `concierge-schedule`, `scheduling-link-init`, `scheduling-link-schedule`, `handoff-init`, `handoff-schedule`, plus list discovery tools.
  - `workspace-list`, `user-find`, `scheduling-link-list-round-robin|ownership|group|admin-one-on-one`.
- **automations**: none required on the caller side; the two-step session has a server-side TTL (`timeoutInMS` for Concierge, per-router-path; server-side TTL for links/handoff). Bookings immediately emit the `For New Meeting` webhook.
- **features_tools**: Command Center `Credentials` page; router `Custom API` tab with `Copy` for URL + starter body and a `Share Instructions` button for developers; MCP connection setup guide.
- **extensibility**: this is *the* documented extension seam — build custom routing/scheduling/booking flows, or an AI assistant that "find[s] the right scheduling link and book[s] a slot this week". Choose MCP "when you want an assistant to pick the tool dynamically; choose Edge when you are writing deterministic integration code."
- **sources**:
  - https://help.chilipiper.com/hc/en-us/articles/50860790980627-How-do-I-Schedule-Chili-Piper-Meetings-Programmatically
  - https://help.chilipiper.com/hc/en-us/articles/30935152032275-Using-Concierge-via-the-Edge-API
  - https://help.chilipiper.com/hc/en-us/articles/50430350863635-How-do-I-connect-Chili-Piper-via-MCP
- **evidence**:
  - "**Programmatic scheduling** means booking a Chili Piper meeting without sending the lead through any Chili Piper UI. Your own code – a backend process, a custom frontend, an AI assistant – calls the Chili Piper API, picks a time, and books the meeting directly."
  - "1. **Discover or route** – a first call returns a session with a list of available time slots and an identifier for the session (`routeId`); 2. **Book** – a second call passes the `routeId` and a chosen `startTime` to commit the meeting. Calendar invites are sent immediately."
  - "**Sessions are single-use.** On a schedule failure, do not retry the schedule call with the same `routeId` – start again from the discover or route step"
  - "**Slot times are UTC.** The `startTime` in responses is ISO-8601 UTC; pass it back verbatim on the book call."
  - "Only users with the **Admin** role can generate API tokens in Command Center. Workspace Managers do not have access to the credentials page."

---

## 7. Find a time that works for a multi-person panel

- **name**: Find a shared slot across several calendars
- **user_flow**:
  1. User opens a "find a time" surface (in a sales room, a CRM record, or a scheduling page) and picks a set of participants + a date range.
  2. The app collects the attendees' email addresses and location constraints (room / "suggest a location").
  3. The app calls each attendee's calendar free/busy service (Google) or Graph `findMeetingTimes` (Microsoft).
  4. Ranked candidate slots are returned, each with a confidence percentage and a human-readable reason; the user picks one.
  5. On pick, the app creates the event on the organizer's calendar (optionally creating a fresh conference).
- **data_flow**: attendee emails + `timeConstraint{activityDomain, timeSlots}` + `locationConstraint` + `meetingDuration` + `minAttendeePercentage` → provider free/busy read → per-attendee availability (free=100%, unknown=49%, busy=0%) → averaged **confidence** score, sorted high→low then chronologically → `meetingTimeSuggestions[]` with `suggestionReason` → chosen slot → calendar event created.
- **data_sources**: Google Calendar free/busy for a set of calendars/groups; Microsoft Graph primary calendars of organizer + attendees; Graph resource/room directory; identity provider (delegated `Calendars.Read.Shared` for Graph; OAuth scopes for Google).
- **apis_hit**:
  - `POST https://www.googleapis.com/calendar/v3/freeBusy` (Google) — body `{timeMin, timeMax, timeZone, groupExpansionMax, calendarExpansionMax, items[{id}]}`; returns `calendars[key].busy[]`; scope `https://www.googleapis.com/auth/calendar.events.freebusy` suffices.
  - `POST /me/findMeetingTimes` or `POST /users/{id|userPrincipalName}/findMeetingTimes` (Graph) — least-privileged delegated permission `Calendars.Read.Shared`; header `Prefer: outlook.timezone`; returns `meetingTimeSuggestionsResult` with `emptySuggestionsReason`.
  - `POST https://www.googleapis.com/calendar/v3/calendars/{calendarId}/events` (Google) — commit the chosen slot.
- **automations**: none required; but `emptySuggestionsReason` is documented as the signal to re-call with adjusted parameters ("Based on this value, you can better adjust the parameters and call **findMeetingTimes** again"), and Google notes suggestions are "fine-tuned from time to time" so test environments may drift.
- **features_tools**: slot-picker UI, confidence badge, `suggestionReason` tooltip, `returnSuggestionReasons` toggle, group expansion for whole distribution lists.
- **extensibility**: free/busy is decoupled from slot data — an integrator can layer their own scoring/ranking, house rules (no Friday afternoons, no back-to-back), or book into a room resource. `groupExpansionMax` (max 100) and `calendarExpansionMax` (max 50) are explicit capacity knobs.
- **sources**:
  - https://learn.microsoft.com/en-us/graph/api/user-findmeetingtimes?view=graph-rest-1.0
  - https://developers.google.com/workspace/calendar/api/v3/reference/freebusy/query
  - https://developers.google.com/workspace/calendar/api/v3/reference/events/insert
- **evidence**:
  - "Suggest meeting times and locations based on organizer and attendee availability, and time or location constraints specified as parameters."
  - "For each attendee, a free status for a specified meeting time period corresponds to 100% chance of attendance, unknown status 49%, and busy status 0%."
  - "If **findMeetingTimes** cannot return any meeting suggestions, the response would indicate a reason in the **emptySuggestionsReason** property."
  - `"suggestionReason": "Suggested because it is one of the nearest times when all attendees are available."`
  - Google: "Returns free/busy information for a set of calendars." / "Maximal number of calendars for which FreeBusy information is to be provided. Optional. Maximum value is 50."

---

## 8. Embed a bookable calendar inside the sales room / app

- **name**: Embed an in-room booking calendar
- **user_flow**:
  1. Builder installs the embeddable booking components and stands up an OAuth client so the app can act on behalf of a scheduling user.
  2. The sales room renders a **Booker** (and optionally an **Availability** calendar, **Event Type** editor, calendar-connect buttons for Google/Outlook/Apple, and a payment form).
  3. For a bespoke flow, the app "intercepts a booking to introduce your custom flow and then submit the booking" — e.g. show a qualification form, hold the slot, then submit.
  4. Builder optionally uses a **custom slot selection flow** with `handleSlotReservation` for slot-hold semantics.
  5. The prospect books entirely in-room; no Chili-Piper-like external page is shown.
- **data_flow**: OAuth access token → Booker component calls Cal API v2 for event types/schedules/availability → slot grid rendered → optional slot reservation (`POST /v2/slots/reservations` returning `reservationUid`, `reservationDuration`, `reservationUntil`) → `POST /v2/bookings` with `attendee`, `start`, `eventTypeId|slug+username|teamSlug`, `bookingFieldsResponses`, `metadata` → booking record + calendar event + video link.
- **data_sources**: Cal.com `EventType`, `Schedule`/availability, `Booking` tables, connected Google/Outlook/Apple calendars, CRM (for routing result / owner), Stripe (optional payment), Zoom/Google Meet/Teams/Webex (conference).
- **apis_hit**:
  - `GET /v2/slots?eventTypeId=…&start=…&end=…&timeZone=…` (also `eventTypeSlug`+`username`+`organizationSlug`, or `usernames=alice,bob` for dynamic multi-person slots, or `teamSlug` for team events) — header `cal-api-version: 2024-09-04`.
  - `POST /v2/slots/reservations` — reserve a slot; `reservationDuration` default 5 minutes.
  - `GET /v2/slots/reservation/{reservationUid}` / `PATCH` / `DELETE` (Get/Update/Delete a reserved slot).
  - `POST /v2/bookings` — header `cal-api-version: 2026-02-25`; supports standard, recurring (`recurrenceCount`, max 32), and instant (`"instant": true`, team events only) bookings.
  - `GET /v2/routing-forms/slots` (Calculate slots based on routing form response) — "It will not actually save the response just return the routed event type and slots when it can be booked."
  - Embed SDK packages: `@calcom/atoms` (Booker, Availability, EventType, CalendarView, Google/Outlook/Apple calendar connect, Stripe Connect, PaymentForm, BookerEmbed, OnboardingEmbed) + `CalOAuthProvider`.
  - Embed events + CSS custom properties for styling: `/docs/atoms/hooks/bookings-hooks.md`, `/docs/atoms/hooks/calendar-hooks.md`, `/docs/developing/guides/embeds/embed-events.md`, `/docs/developing/guides/embeds/customize-embed-css-variables.md`.
- **automations**: no user action needed for the hold to expire — a reservation auto-expires after `reservationDuration`. On success, `BOOKING_CREATED` webhook fires; downstream automations can chain (see #16).
- **features_tools**: Booker, Booker Embed, Availability settings, Calendar view, Event Type config, calendar-connect buttons, Troubleshooter, custom toasts (`replacing-toasts.md`), booking fields (prefill / read-only), team event types, seated events.
- **extensibility**: first-class seams: (a) `handleSlotReservation` for custom slot-selection, (b) `createBooking`/`bookings hooks` to intercept submit, (c) `bookingFieldsResponses` + `metadata` (≤50 keys, ≤40 chars/key, ≤500 chars/value) to carry deal-room context into every booking, (d) a full app-store model (`how-to-build-an-app.md`), (e) self-hosting (private/HTTP webhook URLs and internal IPs allowed on self-hosted).
- **sources**:
  - https://cal.com/docs/atoms/introduction
  - https://cal.com/docs/api-reference/v2/slots/get-available-time-slots-for-an-event-type
  - https://cal.com/docs/api-reference/v2/slots/reserve-a-slot
  - https://cal.com/docs/api-reference/v2/bookings/create-a-booking
  - https://cal.com/docs/llms.txt
- **evidence**:
  - "Cal.com Atoms are customizable React components that let you integrate Cal.com scheduling functionality directly into your application."
  - "**Custom booking flow**: Learn how to intercept a booking to introduce your custom flow and then submit the booking." / "**Custom slot selection flow**: Learn how to use handleSlotReservation for custom slot selection flows."
  - "Checking slots by usernames is used mainly for dynamic events where there is no specific event but we just want to know when 2 or more people are available." / "**Note:** You can fetch slots by `usernames` … `usernames=alice,bob`"
  - "Make a slot not available for others to book for a certain period of time … you can also specify custom duration for how long the slot should be reserved for (defaults to 5 minutes)."
  - "`bookingUidToReschedule`: When rescheduling an existing booking, provide the booking's unique identifier to exclude its time slot from busy time calculations."
  - "Metadata must have at most 50 keys, each key up to 40 characters, and string values up to 500 characters."
  - "`@calcom/atoms` is in maintenance mode … the next generation of Atoms will be distributed as copy-and-paste components built on coss ui and API v2."

---

## 9. Provision a per-booking video-conference link (Meet / Zoom / Teams / Gong)

- **name**: Generate and swap the video meeting link
- **user_flow**:
  1. Admin sets the **Location** on the Meeting Type: `Google Meet` (one-time link), `Zoom` (one-time link), `Gong` (one-time Gong link that redirects to Zoom), `Conference Details` (static link / text), `In-Person Meeting`, `Custom`, or `Ask the Guest (Provide My Own)`.
  2. Connecting the provider in `Integrations` is mandatory for Meet/Zoom/Gong ("Connecting Zoom on the Integrations tab is mandatory for this one to work").
  3. On booking, the system creates a fresh conference and writes it into the invite's Location field; the invite body can embed reschedule/cancel URLs via dynamic tags.
  4. If the meeting moves to a different tool (or a rep's integration is swapped), the location of the existing booking is updated and the conference link is re-provisioned; attendees are emailed the change.
- **data_flow**: Meeting Type location setting → provider OAuth connection → conference created (Google: `conferenceData.createRequest` with `conferenceDataVersion=1`; Cal: `location.type=integration`, `integration=<enum>`) → URL written to booking `location` / meeting `meetingLocation` → calendar event updated via organizer's connected credentials → attendee notification email; `BOOKING_LOCATION_UPDATED` webhook carries `previousLocation` + new `location`.
- **data_sources**: Meeting Type / Event Type location config; Google Calendar (`conferenceData`), Zoom/Google Meet/Teams/Webex/Gong/Salesroom/Whereby/Jitsi… (Cal's documented `integration` enum); OAuth tokens per host; calendar event.
- **apis_hit**:
  - `POST https://www.googleapis.com/calendar/v3/calendars/{calendarId}/events?conferenceDataVersion=1` — body `conferenceData.createRequest`; docs warn "always generate a unique conference for each event by using the `createRequest` field."
  - `PATCH /v2/bookings/{bookingUid}/location` (Cal) — "For integration locations (e.g. Zoom, Google Meet, Cal Video), the endpoint also provisions a conference link. Attendees are notified of the location change by email." Header `cal-api-version: 2024-08-13`, scope `BOOKING_WRITE`.
  - Cal documented integration enum: `cal-video, google-meet, zoom, whereby-video, whatsapp-video, webex-video, telegram-video, tandem, sylaps-video, skype-video, sirius-video, signal-video, shimmer-video, salesroom-video, roam-video, riverside-video, ping-video, office365-video, mirotalk-video, jitsi, jelly-video, jelly-conferencing, huddle, facetime-video, element-call-video, eightxeight-video, discord-video, demodesk-video, campfire-video`.
  - Location types accepted on booking create: `address, attendeeAddress, attendeeDefined, attendeePhone, integration, link, phone, organizersDefaultApp`.
  - `POST https://fire.chilipiper.com/api/.../schedule-simple` (booking that mints the one-time link) and the `For New Meeting` webhook whose `meetingLocation` is e.g. `https://example.zoom.us/j/1234567890`.
- **automations**: automatic on booking ("generates a one-time Zoom link"); on provider failure Cal reports `appsStatus[]` per app (`appName`, `success`, `failures`, `errors`) in the booking/webhook payload, which is what an integration should watch to retry or fall back.
- **features_tools**: Meeting Type `Location` picker (multiple locations with a "Set as Default"), `Integrations` tab, `Conference Details`, dynamic tags `CP.Meeting.RescheduleUrl` / `CP.Meeting.CancelUrl`, `meetingLocation` icon in Meetings Activity, Gong org-level connection + user mapping.
- **extensibility**: providers are swappable per Meeting Type; static links are supported for anyone who doesn't want one-time links; `Ask the Guest` lets the buyer dial in; Cal exposes ~30 video integrations plus a `link` escape hatch; Gong's redirect behaviour means one setting can cover both recording and conferencing.
- **sources**:
  - https://help.chilipiper.com/hc/en-us/articles/27994909516563-Meeting-Types-in-MyApp
  - https://cal.com/docs/api-reference/v2/bookings/update-booking-location-for-an-existing-booking
  - https://cal.com/docs/api-reference/v2/bookings/create-a-booking
  - https://developers.google.com/workspace/calendar/api/v3/reference/events/insert
  - https://help.chilipiper.com/hc/en-us/articles/39402611091475-Custom-Webhook-Configuration
- **evidence**:
  - "**Google Meet:** This option generates a one-time Google Meet link to be displayed in the Location." / "**Zoom:** This one generates a one-time Zoom link." / "**Gong**: This one generates a one-time Gong link; however, when clicked, Gong will redirect you to Zoom."
  - "**Conference Details:** This is a text field where you can manually enter the Location details. This option is normally used to include links, like static Zoom ones, for those who don't want to use one-time links"
  - "**Ask the Guest (Provide My Own):** This field will enable your prospects to provide the Location themselves."
  - Google: "`conferenceData` … To create new conference details use the `createRequest` field. To persist your changes, remember to set the `conferenceDataVersion` request parameter to `1` for all event modification requests. **Warning:** Reusing Google Meet conference data across different events can cause access issues and expose meeting details to unintended users."
  - Cal webhook: "`BOOKING_LOCATION_UPDATED` … The payload mirrors the standard booking payload, with one addition: `previousLocation` holds the location before the change and existing `location` holds the new location."

---

## 10. Auto-join and record the meeting, gated by recording consent

- **name**: Auto-join and record with consent
- **user_flow**:
  1. Tech admin creates a **consent profile** in `Admin center > Data capture > Recording consent` (`Add Profile`), naming it and describing it.
  2. Admin turns the **Consent page** on, then checks **Enforce use of consent page** so "Gong only records meetings where consent was explicitly given via the consent page to record the call." Optionally enables **Allow participants to join without giving consent (recording will be canceled)**.
  3. Admin adds **web conference providers** (Zoom, Google Meet, Microsoft Teams, Webex) and per-provider **link settings**: `Dynamic link`, `Static link`, or `Let host decide`. Sets a default provider, company logo, and supported languages for the consent page.
  4. Admin switches on the **pre-call email** and customises subject/body/signature/legal footer; Gong "Automatically send[s] pre-call emails to external invitees between 10 and 20 minutes before the call".
  5. Admin configures the **audio prompt** ("We're recording this call for better-note taking, following up and training purposes"), including "Don't play the audio prompt if the consent page is used".
  6. Admin assigns the profile to users; the profile is set as default for new team members.
  7. At booking time, the platform can create a consent-enabled meeting link via API; the recording bot joins automatically (Gong returns `additionalInvitees` to add, e.g. the Gong assistant). If consent is not given, the call is not recorded.
- **data_flow**: meeting booked (organizer email + start/end + invitees + externalId) → Gong resolves the user's consent profile → Gong issues a consent-meeting `meetingUrl` (+ `meetingId`, `additionalInvitees` for the recording bot) → link is written into the calendar invite → at T-10..20 min a pre-call email with variables (sender name/company, meeting title, meeting hour) is sent → participant opens consent page → grants or declines → recording proceeds or is cancelled → audio prompt plays at join.
- **data_sources**: Gong user directory + per-user settings (import emails, record-by-Gong flag, "if the invitation of this user to a web conference will prevent its recording"); consent profiles; web-conference provider link config; meeting invite metadata; Gong recording store.
- **apis_hit**:
  - `POST /v2/meetings` (Gong, "Meetings (in Beta Phase)") — scope `api:meetings:user:create`; request `NewMeetingRequest{startTime, endTime, title, invitees[], externalId, organizerEmail}`; response `NewMeetingResponse{requestId, meetingId, meetingUrl, additionalInvitees[]}`. Documented errors include `409 Conflict, e.g. consent page is not enabled in your company` and `404 No Gong user found corresponding to the provided organizer email`.
  - `PATCH /v2/meetings/{meetingId}` — update a Consent Meeting's details.
  - `DELETE /v2/meetings` — delete a Consent Meeting.
  - `GET /v2/meetings/integration/status` — validate Gong meeting integration.
  - "List all Licensed Users in the Gong instance and their Consent Page status".
  - `organizerEmail` doc: "The email address of the user creating the meeting, the Gong consent page link will be used according to the settings of this user."
- **automations**: pre-call email auto-sent 10–20 min before external calls ("The email is sent from the Gong email server for all recorded calls, including calls recorded with native Zoom recording, calls scheduled via the Gong homepage, and calls recorded by adding the Gong assistant."); consent page enforcement cancels recording when declined; audio prompt fires on first guest with audio on (or on every guest); stale-link invalidation — "Each time you change this link, the previous link is disabled."
- **features_tools**: Recording consent profiles, consent page + preview, per-provider link type, Set as default provider, Send consent instructions, pre-call email editor with variable table, audio prompt with voice/language picker, "Send me a sample email", Outlook/Google add-ons.
- **extensibility**: multiple providers per profile so reps pick per call; per-user profile assignment; the Meetings API lets a third-party scheduler (Chili Piper, Cal.com, custom) request the consent link itself — Chili Piper's Meeting Type `Location` = `Gong` is exactly this integration and *requires* the org-level Gong connection plus user mapping.
- **sources**:
  - https://help.gong.io/docs/create-consent-profiles
  - https://help.gong.io/apidocs/create-a-new-gong-meeting-v2meetings.md
  - https://help.chilipiper.com/hc/en-us/articles/27994909516563-Meeting-Types-in-MyApp
- **evidence**:
  - "**Enforce use of consent page**: Check to ensure Gong only records meetings where consent was explicitly given via the consent page to record the call. If additional meeting links are included in the invitation, the call may not be recorded."
  - "Automatically send pre-call emails to external invitees between 10 and 20 minutes before the call, reminding them about the call and letting them know that it will be recorded."
  - "These compliance settings are applied to all web conference providers you add to the consent profile."
  - `"meetingUrl": "The Gong URL of the meeting, should be used to enter the meeting."` / `"additionalInvitees": "Attendees the requesting party should add to the invitation, this should support adding email addresses such as coordinator@gong.io for Gong to schedule the recording of the meeting."`
  - `"409": { "description": "Conflict, e.g. consent page is not enabled in your company" }`
  - "**Gong**: This one generates a one-time Gong link … one of your Admins needs to establish Gong's connection for your org and ensure users are properly mapped."

---

## 11. Send conditional pre- and post-meeting reminders and SMS nudges

- **name**: Send conditional meeting reminders and nudges
- **user_flow**:
  1. Admin/host opens a Meeting Type → `Reminders and Messages` → `Add Reminder` (or picks a template).
  2. Chooses **Reminder Type**: `Email` or `SMS`.
  3. Configures delivery: `Send Email To` (Primary Guest or All Guests), `Send Email From` (Host address / Booker address / No-reply address on a custom domain), `Send Replies To` (Host / Booker / Assignee(s)), and for SMS `Send SMS From` (any number or a local area number).
  4. Chooses the firing condition: `Before the Meeting`; `Before the Meeting - if the Primary Guest did not respond`; or `After the Meeting` (for follow-up emails) — with a minutes/hours/days/weeks offset.
  5. Composes subject + body, using dynamic tags such as `CP.Guest.FirstName`, `CP.Meeting.RescheduleUrl`, `CP.Meeting.CancelUrl`.
  6. Adds advanced gates: `Send only if meeting starts on` (weekday checkboxes) and `Send if the meeting was booked more than selected timeframe`.
  7. Admin audits delivery later in `Meetings Activity` → open a meeting → per-reminder status.
  8. For a portable implementation, the same logic is modelled as a Cal.com **Workflow**: trigger + ordered steps with templates.
- **data_flow**: booking record (start time, duration, primary guest, all guests, booker, host, assignees, guest phone, responseStatus) → reminder rules evaluated on a schedule → condition checks (response status, lead time, weekday) → email via configured sender OR SMS via the org's Twilio connection → delivered/skip-reason recorded on the meeting; inbound SMS replies forwarded by email to selected roles.
- **data_sources**: Meeting/booking record; guest form phone field; calendar invite `responseStatus` (accepted/declined/needsAction); Twilio (org-level, Command Center > Integrations) for SMS; custom email domain for no-reply sending; Slack (Chili Piper's other notify channel).
- **apis_hit**:
  - Chili Piper (config surface, not REST): Meeting Type → `Reminders and Messages`; Twilio setup article `Configuring Twilio for your Organization`; `POST /api/fire-edge/v1/org/.../schedule-simple` is what creates the record these reminders hang off.
  - `POST /v2/workflows` (Cal) — `CreateEventTypeWorkflowDto{name, activation{isActiveOnAllEventTypes, activeOnEventTypeIds}, trigger, steps[]}`; header `cal-api-version: 2024-08-13`; `GET/PATCH/DELETE /v2/workflows[/{id}]`.
  - Cal trigger enum: `beforeEvent, eventCancelled, newEvent, afterEvent, rescheduleEvent, afterHostsCalVideoNoShow, afterGuestsCalVideoNoShow, bookingRejected, bookingRequested, bookingPaymentInitiated, bookingPaid, bookingNoShowUpdated`; `offset{value, unit: hour|minute|day}`.
  - Cal step actions: `email_host, email_attendee, email_address, sms_attendee, sms_number, whatsapp_attendee, whatsapp_number, cal_ai_phone_call`; step `template` enum `reminder, custom, rescheduled, completed, rating, cancelled`; step outputs also model `paths` / `filter` / `delay` / `lead_enrichment` steps.
  - Cal message templating tokens: `{EVENT_NAME}`, `{ORGANIZER}`, `{ATTENDEE}`, `{LOCATION}`, `{MEETING_URL}`, `{START_TIME_h:mma}`, `{TIMEZONE}`, `{EVENT_DATE_ddd, MMM D, YYYY h:mma}`.
  - Cal booking field `attendee.phoneNumber` — "becomes required when SMS reminders are enabled for the event type".
- **automations**: this *is* the automation. Time-based firing with response-conditional gating and lead-time gating; reminders are reusable assets attachable to many Meeting Types (`Remove from Meeting Type` vs `Delete`); statuses `Scheduled` / `Sent` / `Skipped` with documented skip reasons (`Reminder schedule time in the past`, `Reminder condition not satisfied`, `Recipient not found`, `Phone not found`, `Violated restriction`).
- **features_tools**: Reminders and Messages page, reminder templates, Preview pane, Dynamic Tags, Meetings Activity reminder status, Cal.com Workflow canvas with paths/filters/delays/lead-enrichment, `includeCalendarEvent` (.ics in the email), `skipNoShowAttendees`, `autoTranslateEnabled` + `sourceLocale`, `phoneRequired` on the booking.
- **extensibility**: reusable reminder assets; org-level brand-safe sending from a no-reply address on a custom domain; multi-channel (email/SMS/WhatsApp/AI phone call) as workflow steps; condition groups with `matches: all|any|none`; delays between steps; a `lead_enrichment` step that "looks the attendee up with the lead enrichment provider when the workflow runs; the steps after it can use the result in their conditions and messages".
- **sources**:
  - https://help.chilipiper.com/hc/en-us/articles/28030135101971-Reminders-and-Messages
  - https://cal.com/docs/api-reference/v2/workflows/create-a-workflow
- **evidence**:
  - "**Before the Meeting - if the Primary Guest did not respond:** the Reminder will be sent before the meeting at a pre-defined time, however, only if the Primary Guest has not responded to the invite (Accepted or Declined)."
  - "**After the Meeting:** the reminder will be sent after the meeting at a pre-defined time. This is a useful option for follow-up emails, for example."
  - "You can see two options: **No Restriction** … **Send only if meeting starts on:** When this option is selected, you will see the weekdays and checkboxes"
  - "While enabled, this setting determines whether the reminder should be sent only within a specific timeframe. For example … the reminder will be sent only if the meeting is booked one week in advance from the current booking date."
  - "For SMS reminders … A Chili Piper Admin must connect Twilio to your company's Command Center Integrations page." / "You must have a Phone field in your **Guest Form**"
  - "When a guest replies to an SMS reminder, Chili Piper forwards the text to your team by email." + "⚠️ **Warning:** Forwarding SMS replies requires your company's own Twilio account to be connected in Command Center."
  - Cal: "allowed triggers are beforeEvent,eventCancelled,newEvent,afterEvent,rescheduleEvent,afterHostsCalVideoNoShow,afterGuestsCalVideoNoShow,bookingRejected,bookingRequested,bookingPaymentInitiated,bookingPaid,bookingNoShowUpdated"
  - Cal: "The step that looks the attendee up with the lead enrichment provider when the workflow runs; the steps after it can use the result in its conditions and messages."

---

## 12. Route a requested slot for host approval before confirming

- **name**: Hold a meeting request pending host approval
- **user_flow**:
  1. Admin enables **requires confirmation** on the event type (or the booking is created as a request).
  2. Prospect/agent requests a slot → booking is created in `PENDING` status with a `oneTimePassword` and `requiresConfirmation: true`.
  3. `BOOKING_REQUESTED` webhook fires; the sales room / rep is notified and can surface the request.
  4. Host **confirms** the booking (status → `ACCEPTED`) or **declines** with a reason.
  5. On decline, `BOOKING_REJECTED` fires carrying `rejectionReason` (e.g. "The organizer is no longer available at this time.") and the attendee is notified.
- **data_flow**: slot request (`POST /v2/bookings` with `requiresConfirmation` semantics) → booking record `status: PENDING`, `oneTimePassword` issued, attendee email sent → host decision (confirm / decline, optionally with `reason`) → status `ACCEPTED` / `REJECTED` → calendar event created or released → `BOOKING_REJECTED` payload with `rejectionReason` propagates to CRM/room.
- **data_sources**: event type `requiresConfirmation` config; booking table; host identity/availability; attendee email/phone; email verification records; calendar event (created only on confirm).
- **apis_hit**:
  - `POST /v2/bookings` — the request body carries `emailVerificationCode` ("Email verification code required when event type has email verification enabled"); response `BookingOutput` includes `requiresConfirmation`, `oneTimePassword`, `status` (`cancelled|accepted|rejected|pending`), `rejectionReason`.
  - `POST /v2/bookings/{bookingUid}/confirm` — "Confirm a booking … The provided authorization header refers to the owner of the booking."
  - `POST /v2/bookings/{bookingUid}/decline` — "Decline a booking … The provided authorization header refers to the owner of the booking."
  - Email-verification triad: `GET/POST /v2/bookings/email-verification/...` → "Check if email verification is required", "Send email verification code", "Verify email with code".
  - Webhooks `BOOKING_REQUESTED`, `BOOKING_REJECTED`.
- **automations**: `bookingRequested` is itself a valid **workflow trigger**, so a request can immediately kick off a rep-facing to-do/SMS/email; `bookingRejected` can trigger re-routing. Unattended approvals can be bypassed server-side for trusted hosts by the newer bypass flags.
- **features_tools**: requires-confirmation toggle on the event type, host confirm/decline action, reason capture, email verification, workflow `bookingRequested`/`bookingRejected` triggers, `skipContactOwner` on routing.
- **extensibility**: bypass flags exist for privileged callers — `allowConflicts`, `allowBookingOutOfBounds`, `skipBookingLimits` (only honoured for an authenticated user who passes the event-owner/host/team-admin/org-admin checks, and only on API versions 2026-02-25 / 2026-05-01). Email verification is a separate optional gate.
- **sources**:
  - https://cal.com/docs/api-reference/v2/bookings/create-a-booking
  - https://cal.com/docs/developing/guides/automation/webhooks
  - https://cal.com/docs/_llms/api-v2-reference.md
- **evidence**:
  - Webhook `BOOKING_REQUESTED` payload: `"requiresConfirmation": true, "oneTimePassword": "00000000-0000-0000-0000-000000000000", "status": "PENDING"`
  - Webhook `BOOKING_REJECTED` payload: `"rejectionReason": "The organizer is no longer available at this time."`, `"status": "REJECTED"`
  - API reference index: "Confirm a booking — The provided authorization header refers to the owner of the booking." / "Decline a booking — The provided authorization header refers to the owner of the booking."
  - "When true, availability conflict checks are bypassed for an authenticated user who has access through the existing event owner, host, assigned user, team admin or organization admin checks."

---

## 13. Reassign a booked meeting to a different host

- **name**: Reassign a booked meeting to another host
- **user_flow**:
  1. Admin opens `Reporting > Meetings Activity` → **Upcoming** (or **Past**) tab, filters by Meeting Type / Assignee / Booker / Status / product source, and clicks **Open** on the meeting.
  2. In the right-hand details panel, clicks the icon next to the host's name → popup offers **Edit Meeting** or **Reassign Meeting**.
  3. If the target person is known and free, pick them and hit **Reassign**; otherwise choose **Edit Meeting** to open the scheduler for the original Distribution.
  4. In the scheduler, change the **Distribution**, **Team**, or **Individual**, pick a new slot, and book. "You cannot change the Meeting Type or Workspace."
  5. The invite is updated with the new assignee's name, links and details.
  6. End users can do the same from **MyApp**'s Schedule view; and a **Reassign** button appears directly in Google Calendar for Chili Piper-booked meetings (requires the ChiliCal Chrome extension). Admins can also put a custom Reassign button on the Salesforce `Event` record.
  7. "Note: Reassignment does **not** take into account the minimum scheduling notice or the maximum availability range."
- **data_flow**: meeting record (hostId, Distribution, Team, Meeting Type) → reassignment request → scheduler reopens the *same* Distribution context (respecting "whether you allow rescheduling with any team member or not") → new host's availability → new booking → old invite updated/retired → `Meeting Update` webhook with `type: "Updated"` → `Events History` audit row (who, to whom, when, source: Meetings Activity or ChiliCal Home).
- **data_sources**: Meeting record + `Events History`; Distribution/Team/credit state; new host's calendar; Google Calendar (add-on surface); Salesforce `Event` (custom button surface); CRM owner fields.
- **apis_hit**:
  - Chili Piper UI: `Reporting > Meetings Activity` → `Open` → host-name icon → `Reassign Meeting` / `Edit Meeting`; `For Meeting Update` custom webhook fires ("Triggered whenever a meeting is updated, for example if it's reassigned or rescheduled"), with payload `type: "Updated"`.
  - Cal equivalents: `POST /v2/bookings/{bookingUid}/reassign/auto` ("Reassign a booking to auto-selected host … Currently only supports reassigning host for round robin bookings"), `POST /v2/bookings/{bookingUid}/reassign/{userId}` ("Reassign a booking to a specific host").
  - `BOOKING_REASSIGNED` webhook — payload adds `addedHostUserIds`, `removedHostUserIds`, and `organizer` reflects the new host; "Fires when a round-robin booking's host is reassigned (automatic or manual)."
- **automations**: round-robin credit state moves with the host; Cal can auto-select the replacement host (`reassign/auto`); `BOOKING_REASSIGNED` lets a CRM re-point ownership automatically; no-show credit-back interacts with reassignment.
- **features_tools**: Meetings Activity (Upcoming/Past, filters, Export to CSV), Events History tab, MyApp Schedule view, Google Calendar `Reassign` button, custom Salesforce Reassign button, Meeting conference-link icon, `Mark as No-Show`.
- **extensibility**: four documented entry points (admin dashboard, end-user app, calendar add-on, custom CRM button) plus two APIs; Distribution/Team editable but Meeting Type and Workspace locked; the reassign flow intentionally ignores min-notice/max-range so it can always rescue a stale booking.
- **sources**:
  - https://help.chilipiper.com/hc/en-us/articles/42613941250835-Reassigning-Meetings
  - https://help.chilipiper.com/hc/en-us/articles/31428605286931
  - https://cal.com/docs/developing/guides/automation/webhooks
  - https://cal.com/docs/_llms/api-v2-reference.md
- **evidence**:
  - "When Reassigning a meeting with Chili Piper, you are booking a new meeting for another user. Reassignment will take into account your Handoff/ChiliCal User controls and the Distribution settings of the meeting booked."
  - "You can change the **Distribution, Team,** or **Individual**. **You cannot change the Meeting Type or Workspace.**"
  - "Chili Piper should update the invite accordingly with the new assignee's name, links, and other details that possibly changed from one assignee to another."
  - "Note: Reassignment does **not** take into account the minimum scheduling notice or the maximum availability range."
  - "This option is available in Google Calendar if the meeting was booked with Chili Piper. You must have ChiliCal's extension installed and be logged in there."
  - `BOOKING_REASSIGNED`: "`addedHostUserIds` lists the user IDs of the host(s) newly assigned to the booking and `removedHostUserIds` lists the user IDs of the host(s) removed. `organizer` reflects the new host."
  - Events History: "**Reassignments** Whenever a meeting is reassigned, we will display who reassigned it, to whom, when, and the reassignment source (Meetings Activity or ChiliCal Home)."

---

## 14. Reschedule or cancel a meeting and propagate the change

- **name**: Reschedule or cancel a booked meeting
- **user_flow**:
  1. Attendee opens the reschedule/cancel URL embedded in the invite body (Chili Piper injects `CP.Meeting.RescheduleUrl` / `CP.Meeting.CancelUrl` into the Description), or the host uses the Meetings Activity panel (**Update Date/Time**, `Cancel Meeting`, or the guest's own reschedule link).
  2. For reschedule, the app re-opens the same Distribution/Meeting Type context; availability is recomputed. If `Expire Reschedule Link` is on, the link stops working once the meeting has happened.
  3. For cancel, an optional cancellation reason is captured; the meeting is released and (per admin config) the Salesforce `Event` is deleted too.
  4. Downstream systems are updated: calendar event moved/cancelled, `Delete Event` behaviour applied, `BOOKING_RESCHEDULED` / `BOOKING_CANCELLED` / `BOOKING_LOCATION_UPDATED` webhooks pushed, and an Events History audit row written.
  5. Cal's host-side equivalents: `POST /v2/bookings/{bookingUid}/reschedule` (immediate) or `POST /v2/bookings/{bookingUid}/request-reschedule` ("The booking will be cancelled and the attendee will receive an email with a link to reschedule"), and `POST /v2/bookings/{bookingUid}/cancel` (cancels a single recurrence or all recurrences).
- **data_flow**: old booking record → reschedule/cancel intent (+ reason) → new slot lookup (excluding the original slot via `bookingUidToReschedule`, so "the original booking time appears as available") → new booking with `rescheduledFromUid`/`rescheduledToUid`/`rescheduleId`/`rescheduleReason` → calendar event update/delete → CRM Event update/delete → webhook fan-out (`Meeting Update` / `Meeting Update` + `type: "Deleted"`) → audit in Events History.
- **data_sources**: booking record; calendar provider; CRM `Event` object; Chili Piper Events History; notification channels (email/Slack); attendee email for reschedule link.
- **apis_hit**:
  - `GET /v2/slots?...&bookingUidToReschedule=abc123def456` — "will ensure that the original booking time appears within the returned available slots when rescheduling."
  - `POST /v2/bookings/{bookingUid}/reschedule`; `POST /v2/bookings/{bookingUid}/request-reschedule`; `POST /v2/bookings/{bookingUid}/cancel`.
  - Webhooks `BOOKING_RESCHEDULED` (carries `rescheduleId`, `rescheduleUid`, `rescheduleStartTime`, `rescheduleEndTime`), `BOOKING_CANCELLED` (carries `cancellationReason`, `cancelledByEmail`), `BOOKING_NO_SHOW_UPDATED`, `BOOKING_LOCATION_UPDATED`.
  - Chili Piper: `For Meeting Update` and `For Canceled Meeting` webhooks ("Triggers when a user or prospect cancels the meeting from any via source"); `Create Event` node toggle `Delete Event` ("You can define if the Salesforce Event will be deleted if the meeting is canceled from the Dashboard or deleted from your calendar provider.").
  - Cal workflow triggers `rescheduleEvent`, `eventCancelled`.
- **automations**: `rescheduleEvent` / `eventCancelled` workflow triggers fire re-sends and notifications; reminder state is recomputed (reminders were relative to the old time); cancellation is reported to the CRM per the `Delete Event` setting.
- **features_tools**: reschedule/cancel URLs in the invite body (dynamic tags), `Expire Reschedule Link` per Meeting Type, Meetings Activity `Update Date/Time` + `Cancel Meeting`, Events History, `rescheduled` template in Cal workflows, reschedule/cancel templates in Cal booking emails.
- **extensibility**: `rescheduleReason` is captured and shipped in the webhook, so a third party can build "reschedule churn" alerting; recurring bookings can be cancelled per-instance or wholesale; `Expire Reschedule Link` lets a vendor force a fresh booking after the fact "for reporting purposes and tracking interactions".
- **sources**:
  - https://cal.com/docs/api-reference/v2/slots/get-available-time-slots-for-an-event-type
  - https://cal.com/docs/developing/guides/automation/webhooks
  - https://cal.com/docs/_llms/api-v2-reference.md
  - https://help.chilipiper.com/hc/en-us/articles/31428605286931
  - https://help.chilipiper.com/hc/en-us/articles/27994909516563-Meeting-Types-in-MyApp
- **evidence**:
  - "Reschedule a booking or seated booking." / "Request to reschedule a booking. The booking will be cancelled and the attendee will receive an email with a link to reschedule." / "`:bookingUid` can be … of an usual booking, individual recurrence or recurring booking to cancel all recurrences."
  - `BOOKING_CANCELLED` payload: `"cancellationReason": "I am no longer able to attend this session."`
  - `BOOKING_RESCHEDULED` payload: `"rescheduleId": 200, "rescheduleUid": "previous-booking-unique-id", "rescheduleStartTime": "2024-01-05T14:30:00Z"`
  - "**Expire Reschedule Link** … This setting allows you to decide if the reschedule link should expire after a meeting has happened. It can help with reporting purposes and tracking interactions with customers."
  - "**Delete Event** You can define if the Salesforce Event will be deleted if the meeting is canceled from the Dashboard or deleted from your calendar provider."
  - "**Reschedule** Whenever a meeting is reassigned, we will display who rescheduled it, to whom, when, and the rescheduling source (Calendar event, ChiliCal Home, or Reschedule Link)."

---

## 15. Write the booking back into the CRM

- **name**: Write the meeting back to the CRM
- **user_flow**:
  1. On a scheduled / not-scheduled / disqualified path in the router, admin adds a `Create or Update Record` node (must precede the other CRM nodes).
  2. Chooses whether to **update the matched record** (Update matched Contact or Lead / Only update matched Lead) and whether to **create** (Create Contact or Lead / Create Lead / Always create Lead).
  3. Adds downstream nodes: `Create Event` (Salesforce) or `Create Engagement` (HubSpot), plus optional **Related Object** (Account, Case, Opportunity, Campaign / Deal, Ticket), `Update Field`/`Update Property`, `Add to Campaign`, `Update Ownership`.
  4. Optionally configures `Create child Event` per additional guest, and the `Delete Event` behaviour.
  5. Admin later retries any failed CRM Event from Meetings Activity → Events History.
- **data_flow**: meeting + guest form Data Fields → matched/created CRM record (Lead or Contact, matched by email; Salesforce L2A matching applied) → Event/Engagement written and related to Account / the open Case with the most recent creation / the Opportunity with the nearest Close Date → selected fields updated (e.g. `Contact.Status = "Sales Qualified"`) → CampaignMember created/updated with status `Booked` → record Owner reassigned to the assignee.
- **data_sources**: Salesforce `Lead`, `Contact`, `Account`, `Opportunity`, `Case`, `Campaign`, `CampaignMember`, `Event`; HubSpot `Contact`, `Company`, `Deal`, `Ticket`, engagement; Chili Piper Data Fields; Meetings Activity Events History.
- **apis_hit**:
  - Chili Piper: router nodes `Create or Update Record`, `Create Event`, `Update Field`, `Add to Campaign`, `Update Ownership` (Salesforce) and `Create or Update Contact`, `Create Engagement`, `Update Property`, `Update Ownership` (HubSpot). Global Salesforce connection required for Event details in Events History.
  - Cal: Salesforce integration guide — "Configure how Cal.com syncs booking data with Salesforce contacts, leads, and events" (`/docs/developing/guides/appstore-and-integration/salesforce.md`); `GET /v2/event-types/{id}/crm-sync-errors` ("List CRM sync errors for an event type"); booking `routing.skipContactOwner` ("Whether to skip contact owner assignment from CRM integration") and `crmAppSlug` / `crmOwnerRecordType` / `crmRecordOwnerFallbackMode` (`relationship` | `attributeRules`).
- **automations**: writes fire on the scheduled, not-scheduled and disqualified paths automatically; ownership can be transferred to whoever took the meeting; Events History surfaces Event-creation failures with detailed errors and a retry.
- **features_tools**: router Flow Builder node palette, Command Center > Data Fields, Meeting Type `Sync Meeting Type to the CRM` toggle ("your links will follow this pre-defined behavior, as these settings are applied to all users in your org"), Meetings Activity Events History, HubSpot `Activity Assigned to` (Host / Booker / Assignee — "This changes who hosts the Engagement inside the activity tab and can be useful for reporting purposes"), Export to CSV.
- **extensibility**: Data Fields can be mapped to custom CRM fields; the Salesforce Package (`Concierge / Handoff for Salesforce Package`) ships pre-built components; Cal exposes CRM owner fallback strategies and per-event-type CRM sync-error reporting; HubSpot engagement ownership is configurable.
- **sources**:
  - https://help.chilipiper.com/hc/en-us/articles/28522554434323-Creating-a-Concierge-Flow
  - https://help.chilipiper.com/hc/en-us/articles/31428605286931
  - https://help.chilipiper.com/hc/en-us/articles/27994909516563-Meeting-Types-in-MyApp
  - https://cal.com/docs/llms.txt
  - https://cal.com/docs/_llms/api-v2-reference.md
- **evidence**:
  - "Note this node must precede the **Create Event**, **Update Field**, **Add to Campaign**, and **Update Ownership** nodes"
  - "All created Events will be related to the Contact or Lead by default. If we have found a contact, you can additionally relate the Event to an **Account**, **Case**, **Opportunity**, or **Campaign**. 💡 For **Cases**, we will relate with the most recently created Open one, and for **Opportunities**, we will relate with the one that has the nearest Close Date"
  - "**Activity Assigned to** … It passes the email of the booker or the assignee to the 'Activity Assigned to' field inside the engagement created in Hubspot for the booked meeting. This changes who hosts the Engagement inside the activity tab and can be useful for reporting purposes."
  - "**Sync Meeting Type to the CRM** … your Admins can define other behaviors to be taken when a meeting is booked via Personal Scheduling Links, such as creating Events, which Object will be associated with an Event, and many more."
  - "If the Event is successfully created, we will show when it happened. If the Event failed to be created, we will also show when it happened, alongside the detailed error."

---

## 16. Push meeting lifecycle events to downstream systems

- **name**: Fan out meeting events via signed webhooks
- **user_flow**:
  1. Admin opens `Command Center > Integrations > Webhooks` → **Create Custom Webhook** and picks a type: **For New Meeting**, **For Meeting Update**, or **For Canceled Meeting**.
  2. Pastes the subscriber URL and clicks Create, then sets the row's status to **Enabled**.
  3. Optionally emails support to obtain the tenant's HMAC signing secret (not shown in the UI).
  4. Subscriber recomputes `HMAC-SHA256(secret, "{timestamp}.{raw_body}")`, compares to `X-Chili-Signature` in constant time, and rejects if the `X-Chili-Timestamp` is outside a freshness window.
  5. A portable equivalent: create webhooks at the event-type or user level, subscribe to individual triggers, optionally attach a **Secret** and a **Custom Payload** template.
- **data_flow**: meeting created/updated/canceled → Chili Piper serialises a meeting payload (`meetingIdChili`, title/description/location, start/end, `primaryGuestTimeZone`, host/assignee/booker identities + `*IdChili`, `primaryGuestDataFields`, `additionalGuests[]`, `workspaceId/Name`, `productFeatureType/Name/Id`, `distributionName/Id`, `meetingTypeName/Id`, `type: Created|Updated|Deleted`) → HMAC-SHA256 signature + timestamp headers → POST to subscriber → subscriber updates warehouse / triggers automations.
- **data_sources**: Chili Piper meeting + asset records; customer webhook endpoint; (optionally) data warehouse / reporting store; Cal-com equivalent: booking record, attendees, `videoCallData`, `appsStatus`, `responses`, `metadata`.
- **apis_hit**:
  - Chili Piper: `POST <subscriber URL>` with `X-Chili-Signature` (HMAC-SHA256 hex) and `X-Chili-Timestamp` (unix seconds); documented payload fields listed above; `productFeatureType` values `ConciergeRouter, HandoffRouter, RoundRobinSchedulingLink, ChatPlaybook, DistroRouter, OwnershipSchedulingLink`.
  - Cal: `POST /v2/webhooks`, `GET/PATCH/DELETE /v2/webhooks[/{id}]` (scopes `WEBHOOK_READ` / `WEBHOOK_WRITE`); per-event-type variants under `/v2/event-types/{id}/webhooks` (`EVENT_TYPE_READ` / `EVENT_TYPE_WRITE`) and team variants.
  - Cal trigger list: `Booking Cancelled, Booking Created, Booking Rescheduled, Booking Rejected, Booking Requested, Booking Payment Initiated, Booking Paid, Meeting Started, Recording Ready, Form Submitted, Meeting Ended, Instant Meeting Created, Instant Meeting Accepted, Booking No-show Updated, Booking Location Updated, Booking Reassigned, After Hosts Didn't Join Cal Video, After Guests Didn't Join Cal Video, Wrong Assignment Report, Delegation Credential Error, Delegation Credential Secret Rotated, Delegation Credential Secret Rotation Failed, Delegation Credential Rotation Required`.
  - Cal envelope: `{"triggerEvent", "createdAt", "payload": {...}}`, version in `x-cal-webhook-version` header; `MEETING_STARTED` / `MEETING_ENDED` use a **flat** payload with no `payload` wrapper.
- **automations**: this is the automation surface. `Meeting Started` and `Meeting Ended` "fire automatically at the booking's scheduled start [or end] time" — a free meeting-lifecycle heartbeat for a sales room. `Recording Ready` / `RECORDING_TRANSCRIPTION_GENERATED` push a `downloadLink` / `downloadLinks` (json, srt, txt, vtt) for post-meeting follow-up.
- **features_tools**: Command Center Integrations > Webhooks table (status toggle, per-row delete, ordering); Cal `/settings/developer/webhooks` UI (Subscriber URL, Event triggers, Secret, Custom Payload); Zapier integration as a no-code alternative; per-event-type webhook scoping.
- **extensibility**: "You are not limited by the number of webhooks you have" and "multiple webhook types [may] have the same webhook URL, and multiple webhook URLs for the same type". Self-hosted Cal accepts HTTP and private IPs for internal webhooks while SaaS enforces HTTPS and blocks cloud metadata endpoints. Custom payload templates let a third party reshape the body. Replay protection is left to the consumer (`MAX_AGE_SECONDS = 300`).
- **sources**:
  - https://help.chilipiper.com/hc/en-us/articles/39402611091475-Custom-Webhook-Configuration
  - https://cal.com/docs/developing/guides/automation/webhooks
  - https://cal.com/docs/_llms/api-v2-reference.md
- **evidence**:
  - "A wehhook is a way for Chili Piper to communicate with another system. Without that system having to constantly ask for updates, Chili Piper will actively push data to that system in order for it to be used in reporting, data warehouses, or for triggering automations."
  - "Chili Piper allows you to send booked meeting data to webhooks of your choice. You are not limited by the number of webhooks you have."
  - "**X-Chili-Signature** | HMAC-SHA256 signature of the payload (hex-encoded)" / "**X-Chili-Timestamp** | Unix timestamp (seconds) when the request was signed"
  - "Step 2: Construct the signed payload by concatenating the timestamp and the raw request body, separated by a period: `{timestamp}.{raw_request_body}`"
  - "Signature mismatch | Ensure you're verifying against the raw request body, not a re-serialized/parsed JSON object."
  - Cal: "`MEETING_STARTED` and `MEETING_ENDED` are exceptions — they use a flat payload where booking fields are at the top level alongside `triggerEvent`, with no `payload` wrapper."
  - Cal: "Webhook payloads are versioned. The version of the payload sent is included in the `x-cal-webhook-version` HTTP header."
  - Cal subscriber URL rules: "**Cal.com SaaS**: Only HTTPS URLs are accepted. HTTP, private/internal IP addresses (e.g., `10.x.x.x`, `192.168.x.x`, `127.0.0.1`), and `localhost` are blocked. **Self-hosted**: Both HTTP and HTTPS URLs are accepted, and private IP addresses are allowed for internal webhooks."

---

## 17. Collect mutual-action-plan approval by e-signature and track it

- **name**: Send a mutual action plan for e-signature approval
- **user_flow**:
  1. Seller builds the MAP/agreement **Template** (a PDF) with recipient roles and fields. Roles include `SIGNER`, **`APPROVER`** ("Must approve before signers can sign"), `CC`, `VIEWER`, `ASSISTANT`.
  2. Seller creates the envelope via the API in one call: `POST /api/v2/envelope/create` (multipart: `payload` JSON + `files`), supplying `recipients[]` each with `email`, `name`, `role` and `fields[]` (`type` `SIGNATURE`/`NAME`/`DATE`/…, `page`, `positionX`, `positionY`, `width`, `height` as percentages, `identifier` = file index).
     - For an in-room experience with no email invite, create a **Direct Link** for the template (`POST /api/v2/template/direct/create` with `{templateId, directRecipientId}`) and either iframe `https://app.documenso.com/embed/direct/{token}` or redirect to `https://app.documenso.com/d/{token}`, optionally with `?externalId=<deal-room-id>` and a `redirectUrl` for the return hop.
  3. Seller distributes: `POST /api/v2/envelope/distribute` with `{envelopeId}` → status `DRAFT` → `PENDING`, recipients get a signing link.
  4. Buyer opens → `DOCUMENT_OPENED`; buyer signs/approves → `DOCUMENT_SIGNED` + `DOCUMENT_RECIPIENT_COMPLETED` with `signedAt`; when all recipients are done → `DOCUMENT_COMPLETED` with `completedAt`.
  5. A buyer can instead **reject** (`DOCUMENT_REJECTED`, `rejectionReason`) or let the link **expire** (`RECIPIENT_EXPIRED`, `expiresAt` / `expirationNotifiedAt`); unsent recipients get `DOCUMENT_REMINDER_SENT` nudges.
  6. The sales room consumes `DOCUMENT_COMPLETED` (or polls `GET /api/v2/envelope?source=TEMPLATE_DIRECT_LINK` and matches `externalId`) to flip the MAP milestone to *Approved*, advance the plan, and notify the owner.
- **data_flow**: PDF template + field coordinates + recipient roles → envelope (`envelopeId`, `externalId`, `documentMeta{subject, message, signingOrder: PARALLEL|SEQUENTIAL, redirectUrl, distributionMethod}`) → distribute → per-recipient tokenised signing URLs → `readStatus`/`signingStatus`/`sendStatus` transitions → webhook events → sales-room MAP state + owner notification. `externalId` is the join key back to the deal room.
- **data_sources**: PDF/document store; Documenso envelope + recipient + field tables; team/org settings; branding (logo, primary colour, email customisation); the sales room's own MAP record (the `externalId` join key).
- **apis_hit**:
  - `POST https://app.documenso.com/api/v2/envelope/create` (multipart/form-data; `payload` + `files`) — creates recipients + fields in one request; base URLs `https://app.documenso.com/api/v2` (prod) / `https://stg-app.documenso.com/api/v2`.
  - `POST https://app.documenso.com/api/v2/envelope/distribute` `{ "envelopeId": "envelope_abc123" }`.
  - `GET /api/v2/envelope` (list; filter `?source=TEMPLATE_DIRECT_LINK`).
  - `POST /api/v2/envelope/cancel` (used by `DOCUMENT_CANCELLED`).
  - `POST /api/v2/template/direct/create` `{templateId, directRecipientId}` → `{id, token, templateId, directTemplateRecipientId, enabled, createdAt}`; URL `https://app.documenso.com/d/{token}`, embed `https://app.documenso.com/embed/direct/{token}`.
  - `POST /api/v2/template/direct/toggle` `{templateId, enabled}`; `POST /api/v2/template/direct/delete` `{templateId}`.
  - `POST /api/v2/template/update` with `meta.redirectUrl`, `data.globalAccessAuth: ['ACCOUNT']`, and `meta.typedSignatureEnabled` / `drawSignatureEnabled` / `uploadSignatureEnabled`.
  - Webhooks: verify `X-Documenso-Secret`; events `DOCUMENT_CREATED, DOCUMENT_SENT, DOCUMENT_OPENED, DOCUMENT_SIGNED, DOCUMENT_RECIPIENT_COMPLETED, DOCUMENT_COMPLETED, DOCUMENT_REJECTED, DOCUMENT_CANCELLED, RECIPIENT_EXPIRED, DOCUMENT_REMINDER_SENT, TEMPLATE_CREATED, TEMPLATE_UPDATED, TEMPLATE_DELETED, TEMPLATE_USED`.
  - Full OpenAPI: `https://openapi.documenso.com/`
- **automations**: per-recipient `signingOrder` + `PARALLEL`/`SEQUENTIAL`; reminder emails to non-completing recipients on a configurable schedule (`DOCUMENT_REMINDER_SENT`); recipient signing deadline (`expiresAt`) with expiry notification; background jobs handle email delivery, document processing and webhook dispatch; sales-room reaction to `DOCUMENT_COMPLETED`/`DOCUMENT_REJECTED`.
- **features_tools**: Templates + Organisation Templates, recipient roles, field types & PDF placeholders, `Developer Mode` (to "debug field IDs, recipient IDs, coordinates"), direct links + iframe embed, CSS variables for white-labelling the signing surface, branding, signing reminders, recipient expiration, AI recipient/field detection, official TypeScript/Python/Go SDKs, plus a REST API.
- **extensibility**: fully API-driven and self-hostable; `externalId` is an explicit "track which document belongs to which transaction in your system" hook; embeddable signing means a MAP approval can live **inside** the sales room; the API cannot sign on a recipient's behalf ("recipients must sign themselves"), which is the intended guardrail for a mutual (not unilateral) approval.
- **sources**:
  - https://docs.documenso.com/docs/developers/getting-started/first-api-call
  - https://docs.documenso.com/docs/developers/webhooks/events
  - https://docs.documenso.com/docs/developers/embedding/direct-links
  - https://docs.documenso.com/llms.txt
- **evidence**:
  - "**APPROVER** | Must approve before signers can sign"
  - "After distribution, recipients receive an email with a link to sign the document. The document status changes from `DRAFT` to `PENDING`."
  - "`positionX` | Horizontal position from left edge (0 = left, 100 = right)" / "`identifier` | Index of the file (0 for first file, 1 for second, etc.)"
  - "The API cannot: Sign documents on behalf of recipients (recipients must sign themselves) … Retrieve the signed PDF until all recipients have completed signing"
  - "`DOCUMENT_SIGNED` | Recipient signs document | Recipient `signingStatus: "SIGNED"`, `signedAt` set" / "`DOCUMENT_RECIPIENT_COMPLETED` | Recipient completes their action"
  - "`RECIPIENT_EXPIRED` | Recipient signing deadline passes | Recipient `expiresAt` passed, `expirationNotifiedAt` set"
  - "**External ID** — Track which document belongs to which transaction in your system: `https://app.documenso.com/d/abc123xyz?externalId=order-12345` … The external ID is stored with the created document and included in webhook payloads."
  - "Verify the signature — Check the `X-Documenso-Secret` header matches your configured secret" / "Process idempotently — Webhooks may be retried, so handle duplicate events"
  - "**Signing Reminders**: Automatically email recipients who have not yet signed on a configurable schedule."

---

## 18. Prepare for the meeting, then run the post-meeting follow-up sequence

- **name**: Prepare for and follow up on a meeting
- **user_flow**:
  1. Rep opens Gong → homepage `Conversations` → **Prepare for meetings** (or quick-access `Upcoming`, keyboard `Shift+M`, the Account console, the Activity calendar, a daily/weekly digest email, or the mobile app push notification "30 minutes before each external meeting").
  2. Rep opens the AI meeting prep page: pre-meeting summary (objectives, key topics, prior discussion highlights, **Action items for you**, suggested questions, reminders), participant list with drill-down activity timelines, **Ask anything** about the account/deal, and a recent-activity feed (calls, emails, digital interactions, CRM changes, texts).
  3. Rep optionally runs **Dry run** to rehearse with an AI participant built from the meeting context, chooses a voice, gets feedback, and iterates with `Go again`.
  4. Rep adds notes, comments and @-tags peers in the collaboration area; decides whether to record; shares the call with the customer; moves it to Library / Listen later.
  5. *After the call*, Gong/Engage turns signals into work: the Engage flow system assigns to-dos and emails. A **flow automation** (`Assets > Automations`, When/If/Then) enrolls contacts/leads when: a CRM entity (Lead/Contact/Account/**Opportunity**) `is created` / `is updated with` / `is created or updated with` / (Opportunity only) `current date is`; or an **AI tracker** detects a concept on a call.
  6. Assignee is chosen from an **Assignee** field; the selected user "will own the flow to-dos and be the sender for any emails included in the flow". To-dos are grouped into a queue ("1/5") and carry flow context ("the flow reference appears with the activity").
  7. Rep updates CRM fields in-place from the Engage CRM tab (Pipeline / Accounts / Person view / while emailing or dialling) and ships follow-up from the same workspace.
- **data_flow**: calendar invite + imported emails/calls + CRM entities (uploaded to Gong via the CRM API) → AI meeting prep page (summary, actions, questions) → meeting held & recorded (subject to consent) → post-call AI trackers/CRM changes → automation rule evaluation (When/If/Then) → prospect enrolled in a company Engage flow → flow instance (`flowInstanceId`, owner, status `Running|Pending|Paused`) → ordered to-dos + emails/calls → rep completes to-dos in a grouped queue → CRM fields updated in Gong.
- **data_sources**: Gong Calls (audio/video/media), imported Emails, calendar meetings, Gong AI Trackers, Gong Engage Flows + to-dos; CRM entities (`Business User`, `Account`, `Contact`, `Deal`, `Lead`, `Stage`) uploaded to Gong; Gong Data Privacy (email/phone references); Slack (Forecast notifications).
- **apis_hit**:
  - `POST /v2/flows/prospects/assign` (Gong) — scope `api:flows:write`; body `AssignFlowRequestV2{crmProspectsIds[], flowId, flowInstanceOwnerEmail}`; up to 200 prospects per request; response splits `prospectsAssigned[]` (`flowInstanceId`, `flowInstanceStatus` ∈ `Running|Pending|Paused`, `exclusive`, `workspaceId`) vs `prospectsNotAssigned[]` (`errorCode` ∈ `InvalidArgument|InvalidState|UnexpectedError`).
  - `GET /v2/flows` (list flows), `GET /v2/flows/folders`, and the Engage Flows capability list ("View a list of relevant Gong Engage flows to choose from and the current flow assignment status").
  - Gong CRM API (for the data Gong reads): `PUT v2/crm/integrations` (register integration → `integrationId`), `POST /v2/crm/entity-schema` (add custom fields), `POST /v2/crm/entities?objectType=ACCOUNT|CONTACT|DEAL|LEAD` (first-time/incremental upload; "we recommend updating between every 1-5 minutes"; `modificationDate` mandatory on updates).
  - Gong Digital Interactions API — push engagement events into the timeline: "Content Shared" / "Content Viewed" / "Custom Action" (`/v2/customer-engagement/content/shared|viewed`, `/v2/customer-engagement/action`).
  - `POST /v2/meetings`, `PATCH /v2/meetings/{meetingId}`, `GET /v2/meetings/integration/status` (meeting link provisioning, see #10).
  - Cal equivalents for the follow-up half: `POST /v2/workflows` with trigger `afterEvent` (offset) and template `completed` / `rating`; `GET /v2/bookings/{bookingUid}/recordings`, `/real-time-transcript` download links; `POST /v2/bookings/{bookingUid}/mark-absence`; webhooks `MEETING_ENDED`, `RECORDING_READY`, `RECORDING_TRANSCRIPTION_GENERATED`, `BOOKING_NO_SHOW_UPDATED`.
- **automations**: flow automations on CRM entity events, Opportunity date fields ("start a flow 20 days before a renewal date or 30 days after a contract is signed"), and AI tracker detections on calls; "**Date-based triggers run on a daily schedule and are evaluated each morning. They do not run immediately when an automation is created or edited.**"; run history for auditing; Cal `afterEvent` workflows with `delay` steps and conditional `paths`/`filter` gates; reminders to non-signers; `DOCUMENT_REMINDER_SENT` in the e-signature flow; Gong Forecast reminders (in-app 24h before due, Slack 1h before to AEs, Slack summary 2h after to managers).
- **features_tools**: AI meeting prep page, Dry run, Ask anything, participant drill-down, Activity page calendar, daily/weekly digest emails, mobile push, Engage flows + automations + run history, grouped to-do queue, context panel, email composer with `Insert Content`, CRM tab with `Show/Hide Fields`, Gong dialer, Gong mobile app, Gong Forecast.
- **extensibility**: `/v2/flows/prospects/assign` lets an external system (a sales room, a routing engine, an AI agent) start a multi-touch follow-up sequence for up to 200 CRM prospects with a named owner — this is the documented programmatic seam for post-meeting follow-up. Failures are returned per-prospect with an error code rather than failing the batch. Personal flows cannot be used with automations, and an assignee without an Engage licence stops the automation ("If they don't, the automation won't run"), which is an explicit permission gate a third party must handle.
- **sources**:
  - https://help.gong.io/docs/get-ready-for-meetings-with-ai-powered-meeting-prep
  - https://help.gong.io/docs/set-up-automated-engage-flows
  - https://help.gong.io/apidocs/assign-prospects-contacts-or-leads-to-an-engage-flow-v2flowsprospectsassign-1.md
  - https://help.gong.io/docs/update-your-crm-from-engage
  - https://help.gong.io/apidocs/create-a-new-gong-meeting-v2meetings.md
  - https://cal.com/docs/developing/guides/automation/webhooks
  - https://cal.com/docs/_llms/api-v2-reference.md
- **evidence**:
  - "Flow automations automatically add contacts and leads to flows when predefined conditions are met. Triggers can be based on CRM updates, scheduled dates, or AI AI tracker detections."
  - "Common use cases include: Starting onboarding flows when an opportunity is marked Closed Won. Triggering renewal flows before a renewal date. Adding contacts to follow-up flows when specific concepts are detected on calls."
  - "**Date-based triggers**: Date-based triggers run on a daily schedule and are evaluated each morning. They do not run immediately when an automation is created or edited."
  - "When the trigger is **Lead** or **Contact**: Select a field from the **Assignee** dropdown … The selected user will own the flow to-dos and be the sender for any emails included in the flow. The selected user must have an Engage license. If they don't, the automation won't run."
  - "If the automation selects a contact without a parent account, the automation will fail to run."
  - "You can assign up to 200 prospects to a flow in a single request. Required authorizaton scope: 'api:flows:write'."
  - "`flowInstanceStatus` … enum: `Running`, `Pending`, `Paused`" / `AssignedFlowFailure.errorCode` enum: `InvalidArgument`, `InvalidState`, `UnexpectedError`
  - "The meeting prep page appears slightly differently in the mobile app and does not display recent activities." / "Push notifications … 30 minutes before each external meeting"
  - "To-dos are grouped into a queue that shows your position as you move through them. For example, 1/5 means you're working on the first of five to-dos in that queue."
  - Cal: `RECORDING_READY` payload includes `"downloadLink": "https://app.cal.com/api/video/recording?token=…"`; `RECORDING_TRANSCRIPTION_GENERATED` includes `downloadLinks.transcription[]` in `json`/`srt`/`txt`/`vtt`.

---

## Gaps / things I could not substantiate from primary sources

- **Salesforce-native meeting scheduling.** I could not find a Salesforce *product documentation* page for meeting booking/routing in a sales room. Salesforce's own content on "mutual action plan" that surfaced in search was blog/partner marketing, not docs, so I deliberately did not build a workflow on it. Chili Piper's and Cal.com's documented **Salesforce** nodes/integrations stand in for the CRM writeback half (#15).
- **HubSpot.** `knowledge.hubspot.com/meetings-tool` 404s and the developer reference redirected to a legacy `crm/activities/meetings` page; I did not get a clean, quotable HubSpot meetings-tool page, so HubSpot appears only where Chili Piper documents its own HubSpot nodes (`Create Engagement`, `Update Property`, `Update Ownership`, `Activity Assigned to`). I did not verify HubSpot's own Meetings API endpoint shapes.
- **DocuSign / Dropbox Sign.** `developers.docusign.com/docs/esign-rest-api/reference/envelopes/envelopes/create/` returned no body. I used **Documenso** instead (open-source, self-hostable, full public OpenAPI) for #17. A DocuSign-envelope-based MAP approval workflow is therefore *not* evidenced here.
- **Zoom / Teams / Meet meeting-creation endpoints.** I did not read Zoom's `POST /users/{userId}/meetings` or Graph `POST /me/events` reference. The video-link workflow (#9) is evidenced via Google Calendar `conferenceData` + Cal.com's documented integration enum + Chili Piper's Meeting Type location options, not via Zoom's own API.
- **Calendar push/subscription sync.** I read `freebusy.query` (pull) but not Google `events.watch` / Graph change notifications, so "availability sync" in #7 is documented as a *pull* free/busy read. I did not verify a push-based availability invalidation flow.
- **Buyer-side embedded calendar inside a *third-party* sales room product.** The embedding evidence is Cal.com Atoms (and the note that Atoms is in maintenance mode in favour of copy-and-paste components on API v2). I did not find first-party docs from a dedicated digital-sales-room vendor (Dock, Consensus, Recapped, Tropic, etc.) that embeds a calendar; Dock's MAP page is marketing, not API/UX docs.
- **Availability-aware "flexible" scheduling** (Chili Piper "Priority Scheduling" showing flexible events as bookable, and weighted "flexible" round robin) is referenced from secondary links inside the Meeting Types article but I did not read the dedicated Priority Scheduling article, so I only assert the round-robin Strict/Flexible distinction, not the priority-link behaviour in detail.
- **Mutual action plan as a first-class object with per-milestone approvals** (owner, timeline, success criteria) is asserted by vendor marketing pages (Highspot, Salesforce blog, Dock) that I did not read as primary docs. #17 is therefore scoped to *e-signature approval of a MAP document with role-based gating and signed-status webhooks*, not to milestone-level MAP state machines.
- **Seating / multi-attendee per-slot bookings** (`seatsPerTimeSlot`, `attendeesCount`, `seatUid`, `Add guests … Maximum 10 guests per request, with a limit of 30 total guests per booking`) exist in the Cal.com schema I read, but I did not find sales-room-relevant usage guidance, so I did not build a workflow around them.
- **Meeting-cost / ROI dashboards** (show-rate, no-show rate by rep) — Cal.com exposes an Insights API (`/v2/insights/*` including `get-failed-bookings-by-routing-field` and `get-routed-to-users-per-period`) and Chili Piper has "Concierge Analytics" and "Workspace's Distribution Reporting", but I only read the endpoint *list*, not the UI or semantics, so I did not claim a workflow for it.
