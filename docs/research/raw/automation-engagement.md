# Domain 7 — Automation & Engagement (raw research)

Scope: drip/nurture sequences, triggered follow-up tasks, email + in-app notifications, multi-channel
reminders, automated seller task creation, escalation rules, SLA timers, behavioural triggers
(repeated visits, dwell time, idleness).

Every workflow below is traced to a vendor product doc / API reference that was actually read during
this pass. Vendors used as primary sources: **Intercom**, **HighLevel (GoHighLevel)**, **HubSpot**,
**Customer.io**, **Pendo**.

Search-tool note: the `websearch` tool was returning `HTTP 401` for the whole session, so discovery was
done by fetching vendor doc indexes (`developers.hubspot.com/docs/llms.txt`, `docs.customer.io/llms.txt`,
Intercom/Pendo/HighLevel help-centre section pages) and HTML search fallbacks. Gaps recorded at the end.

---

## 1. Drip a multi-channel nurture sequence with in-app → email fallback

- **name:** Drip a multi-channel nurture sequence with channel fallback
- **user_flow:**
  1. Go to `Outbound > Series` and click **+ New Series**; pick *Start from scratch* or a template.
  2. Drag a **rule block** onto the canvas and set the entry filters ("Who will enter", audience preview count).
  3. Drag a **content block** (Post/Chat/Email/Tour/Push/Carousel/Banner), write the copy, and set a
     per-block **Goal** and **Scheduling**.
  4. Set the block's **send window** ("Wait up to 30d") so it is retried while the buyer is offline.
  5. Add a fallback **email** block on the *If not online after 30d* branch.
  6. Add a rule block on *When delivered → Clicked link* with `Try to match for 3d`.
  7. Wire the divergent branches to converge on a shared next block (e.g. a product tour).
  8. Click **Set series live**.
- **data_flow:** Contact attributes / company attributes / event counts (from Intercom's contact data
  model) → rule-block filter evaluation → block entry → message dispatched to a channel (in-app post,
  email, mobile push, SMS) → engagement stat events (`receipt`, `open`, `click`, `goal_success`,
  `reply`) emitted back into the contact record → next rule block re-evaluates → engagement score /
  segment membership updates → further blocks.
- **data_sources:** Intercom Contact object + `custom_attributes`; Company object (for company predicates);
  Content Stat objects (`content_stat.series`, `content_stat.email`); Tag table; Segments.
- **apis_hit:**
  - `GET /export/workflows/{id}` — `https://api.intercom.io/export/workflows/{id}` — exports the full
    workflow definition (steps, targeting rules, attributes) for a given workflow.
  - `POST /messages` — `https://api.intercom.io/messages` — the programmatic equivalent of a content
    block; `message_type` is one of `in_app` | `email` | `whatsapp`.
  - Subscription types: `POST /messages/status` and `GET /messages/whatsapp/status` return per-delivery
    status for a ruleset.
  - Webhooks: `content_stat.series` topic fires `receipt`, `goal_success`, `series_completion`,
    `series_disengagement`, `series_exit`.
- **automations:** The Series itself is the automation. No user action is required after going live.
  Entry rule blocks gate entry; per-block send windows gate delivery; rule blocks gate progression;
  tag blocks and goals close the loop. Series evaluates eligibility on a polling cycle: *"Series checks
  every 30 minutes whether a customer matches each rule, message, wait, or tag block."*
- **features_tools:** Series visual canvas; block types Rule / Content / Wait / Tag; per-message
  **Goal** + **Scheduling** tabs; "Set series live"; Series templates; `content_stat` reporting.
- **extensibility:** Content blocks can include Apps from the Intercom App Store; a **Webhook** block can
  be dropped onto the canvas to POST/PUT buyer data to any external URL; Series can be exported
  (`GET /export/workflows/{id}`) for the EU Data Act; `GET /export/workflows/{id}` also exposes
  `targeting`, `snapshot`, `attributes`, `embedded_rules`.
- **sources:**
  - https://www.intercom.com/help/en/articles/4425207-series-explained
  - https://developers.intercom.com/docs/references/rest-api/api.intercom.io/workflows
  - https://developers.intercom.com/docs/references/webhooks/webhook-models
  - https://developers.intercom.com/docs/references/rest-api/api.intercom.io/messages
- **evidence:**
  - "A Series is made up of 'blocks'. There are four kinds of block" — rule / content / wait / tag.
  - "For each message block, you can define how long a message should *try* to send for, as your
    customers might not be online to receive it right away."
  - "If the person still hasn't come online after 30d, they will leave this path" (tooltip on a Post block's
    `Wait up to 30d` setting).
  - "'Try to match for x time' means it will wait **up to that amount of time** for the person to match
    the rule." / "'Try to match once' means that if the person does not match the rules **as soon as they
    reach that block** they will leave the path".
  - `content_stat.series` events: "receipt · goal_success · series_completion · series_disengagement ·
    series_exit".

---

## 2. Re-enter a recurring series and tag the contact on completion

- **name:** Re-enter a recurring nurture series and tag on completion
- **user_flow:**
  1. Open the Series, select the starting **rule block**, and enable **Re-enter Series** in the block settings.
  2. Build the path: message → rule → **tag block**.
  3. In the tag block, pick an existing workspace tag (e.g. `Upgraded after Series`) and title the block.
  4. Open **Show settings** → **Goal** tab; choose the outcome filter (e.g. `Plan is Premium`) and the
     match window (e.g. `Customers have to match within 30 Days`).
  5. Open **Show settings** → **Exit rules** tab and add exclusion filters (e.g. `Tag is ExitUpgradeSeries`,
     `Marked email as spam is true`).
  6. Publish. On the *next* qualifying event the contact re-enters automatically.
- **data_flow:** Recurring domain event (e.g. subscription expiring, new purchase) → contact matches the
  starting rule block's filters again → contact is re-enrolled into the live Series → the contact
  traverses the path → the tag block writes a Tag onto the Contact object → Tags become reusable
  entry/exit conditions for other Series, report filters, segments and inbox views.
- **data_sources:** Intercom Contact + Tag objects; Segments; `contact.user.tag.created` /
  `contact.user.tag.deleted` webhook topics.
- **apis_hit:**
  - `POST /tags` and `POST /tags/{id}/users` are the documented tag surface (Intercom Tags resource);
    `GET /contacts/{id}/tags` is listed on the Contacts reference.
  - Webhooks: `contact.user.tag.created`, `contact.user.tag.deleted`, `content_stat.series`.
- **automations:** Re-entry is the automation. It is data-driven, not user-driven. Series is polled
  every 30 minutes to evaluate entry and progression. Exit rules fire automatically when a contact's
  data changes to match them.
- **features_tools:** Starting rule block "Re-enter Series" toggle; Tag block; `Show settings` panel with
  **Goal** and **Exit rules** tabs; tag-based segment building; "Tags can then also be used to enter or
  exit customers from other Series".
- **extensibility:** Add a Webhook block after the tag block to push the new state to an external system;
  use `GET /export/workflows/{id}` to export/inspect the whole definition; external systems can subscribe
  to `contact.user.tag.created` to react to the tag being applied.
- **sources:**
  - https://www.intercom.com/help/en/articles/4425207-series-explained
  - https://developers.intercom.com/docs/references/webhooks/webhook-models
  - https://developers.intercom.com/docs/references/rest-api/api.intercom.io/tags
- **evidence:**
  - "Customers can re-enter the same Series multiple times if you enable 'Re-enter Series' on the starting
    rules block. This is useful when you've got a set of messages you want to send to customers when a
    recurring event happens such as a subscription expiring, or a purchase being made."
  - "A tag block lets you automatically apply tags to customers as they complete their journey through a
    series."
  - "You won't be able to create a new tag here, but can use any existing tag from the workspace."
  - "If a customer's data is updated to match the filters in this exit rule they will exit the series
    completely, and cannot re-enter."

---

## 3. Trigger outreach when a prospect repeatedly browses a high-intent page

- **name:** Trigger outreach on high-intent page visits
- **user_flow:**
  1. Go to `Fin AI Agent > Workflows` and click **New workflow** → **Create from scratch**.
  2. Choose the trigger **"When customer visits a page"**.
  3. For outbound workflows set **Show workflow until** to one of: `Seen` (default — fire once),
     `Any interaction happens`, or `Engaged with` (re-shows each new session until they engage).
  4. Configure the trigger's `When to send`, `Where to send`, `Audience`, `Scheduling`, `Goal` panes.
  5. Add path blocks: a welcome message with video/apps, then branch on the buyer's answer
     (e.g. "Yes, let's talk about upgrading" → share a booking app; "Not right now" → share a webinar
     and close).
  6. Save and close, then build the Workflow and set it live.
- **data_flow:** Website page view (URL / UTM / time-on-page) collected by the Intercom JS snippet →
  matched against the workflow's page-URL targeting rules and audience attributes → workflow is shown
  in the Messenger as an in-app message → buyer interaction (path selection / Messenger open / dismissal)
  is recorded as a `content_stat` receipt/goal/click event → hidden for the remainder of the session
  (for `Engaged with`), then eligible again next session.
- **data_sources:** Intercom JS snippet on the buyer's site (page views, UTM, referrer); Intercom Contact
  + Company attributes; Segments; `content_stat` events.
- **apis_hit:**
  - `POST /messages` (`message_type: in_app`) is the API equivalent of the in-app block.
  - `GET /export/workflows/{id}` for workflow definition export.
  - `content_stat.chat` / `content_stat.post` webhook topics for receipt/goal/open/click.
- **automations:** Triggered entirely by buyer behaviour (page visit / time on page / visited URL) — no
  seller action. `Seen` / `Engaged with` frequency modes implement a de-facto "repeated visit" damper.
- **features_tools:** Workflow builder trigger list; `Show workflow until` control; page-URL targeting
  rules article; Audience/Scheduling/Goal trigger panes; path branching; Messenger apps.
- **extensibility:** A Data Connector action inside the workflow can make authenticated HTTP calls to
  external APIs at the moment the trigger fires; a `Wait for Webhook` action can hand off to a third
  party and resume on response.
- **sources:**
  - https://www.intercom.com/help/en/articles/2499085-when-a-customer-visits-your-site
  - https://www.intercom.com/help/en/articles/7434613-how-to-trigger-a-workflow
  - https://developers.intercom.com/docs/references/rest-api/api.intercom.io/messages
  - https://developers.intercom.com/docs/references/rest-api/api.intercom.io/data-connectors
- **evidence:**
  - "Automatically presents a bot workflow in the Messenger when a customer meets certain conditions
    i.e. time on page or visited URL." (description of the `Customer visits a page` trigger)
  - "**Seen (default)** — Send the Workflow to customers once, then not again, whether or not they
    interact with or dismiss it." / "**Engaged with** — Send the Workflow to customers consistently until
    they engage with it by selecting a Workflow path."
  - "If they dismiss the Workflow or open the Messenger, it will be hidden for the remainder of their
    session. When they start a new session, the Workflow will be shown again, until they engage with it."
  - "Data connectors allow workflows and AI agents to make HTTP requests to external APIs."
  - "Book meetings with high-intent customers: Automatically invite users to book a meeting with your
    accounts team after they show intent to act, such as viewing your upgrade page."

---

## 4. Chase unresponsive buyers and reroute conversations from unresponsive reps

- **name:** Chase unresponsive buyers and reroute unattended conversations
- **user_flow:**
  1. `Fin AI Agent > Workflows` → **New workflow** → **Create from scratch**; pick the trigger
     **"If customer has been unresponsive"**.
  2. Set the inactivity timer (e.g. 10 minutes) and the trigger's `Channels`, `Audience`, `Scheduling`,
     `Goal`.
  3. Add a **message** block ("Just checking if you are still there? Let us know if you need any help").
  4. Add a **Wait** block; configure the duration and which interruption events cancel the wait
     (teammate and customer messages).
  5. Add a closing message block, then a **Close** conversation action, then **Tag conversation**.
  6. Build a second workflow with the trigger **"If teammate has been unresponsive"**, add a message or
     a **Show expected reply time** step (uses office hours), then **Mark as priority** + **Tag
     conversation** ('delayed response') and **Assign conversation** to another inbox.
  7. Save both and set live.
- **data_flow:** Last-message timestamp on the Conversation object → inactivity elapsed → trigger fires →
  message part written to the conversation (creating a conversation if none exists) → Wait/Snooze timer
  runs and can be interrupted by a new customer/teammate message → Close action transitions the
  conversation to a closed state → Tag written to the conversation part → assignment moves the
  conversation between teams.
- **data_sources:** Intercom Conversation object + conversation parts; Admin/Teammate presence; Office
  hours configuration; Tags; `conversation.admin.*` / `conversation.user.*` webhooks.
- **apis_hit:**
  - `POST /messages` with `create_conversation_without_contact_reply: true` opens a conversation in the
    inbox without a customer reply.
  - `POST /tickets/{ticket_id}/reply` — add admin notes / replies to the underlying ticket.
  - `POST /tickets/{ticket_id}/tags` and `DELETE /tickets/{ticket_id}/tags/{tag_id}` for ticket tagging.
  - Webhooks: `conversation.admin.snoozed`, `conversation.admin.unsnoozed`, `conversation.admin.closed`,
    `conversation.user.replied`, `conversation.read`.
- **automations:** Two purely time-based, automatic triggers (no seller action). Constraints documented:
  duration must be "> 30 seconds and shorter than 14 days"; the teammate-idle timer is "evaluated
  against customer's first message"; the workflow "can only trigger once per customer message".
  Intercom also offers a global auto-close setting under `Settings > AI & Automation`, but "Any workflow
  containing a Wait or Snooze action will take precedence".
- **features_tools:** Workflow triggers list; **Wait** action (interruption events configurable);
  **Snooze** action; **Show expected reply time** step; Mark as priority / Tag / Assign / Close actions;
  Office hours settings page.
- **extensibility:** `POST /messages` lets an external service replay the same behaviour; Data Connectors
  let the workflow call an internal API on the reroute step; `X-Hub-Signature`-signed webhooks let an
  external system observe the close/assign events.
- **sources:**
  - https://www.intercom.com/help/en/articles/7155449-automations-of-snoozed-conversations
  - https://www.intercom.com/help/en/articles/7434613-how-to-trigger-a-workflow
  - https://developers.intercom.com/docs/references/rest-api/api.intercom.io/messages
  - https://developers.intercom.com/docs/references/rest-api/api.intercom.io/tickets
- **evidence:**
  - "Set trigger timer to 10 minutes - meaning that the workflow will only trigger 10 minutes after
    there's been no response from the customer."
  - "The duration must be longer than 30 seconds and shorter than 14 days."
  - "is evaluated against customer's first message. This means if the customer sends 3 messages in a row,
    the timer will be set against their first message, not last."
  - "Then add an action to **Assign conversation** to reroute the conversation to the desired Inbox."
  - "This workflow won't trigger for conversations created via our REST API."
  - "create_conversation_without_contact_reply — Whether a conversation should be opened in the inbox for
    the message without the contact replying. Defaults to false if not provided."

---

## 5. Run SLA response timers that respect office hours and pause rules

- **name:** Run SLA response timers with office-hour awareness
- **user_flow:**
  1. Go to `Settings > Inbox > SLAs`, click **New SLA**, name it, and set targets; click **Save**.
     (Alternatively create one inline from the Workflow builder's **Apply SLA** step.)
  2. Create a Workflow with trigger **"Customer opens a new conversation in the Messenger"** or
     **"A ticket is created"**.
  3. Add the **Apply SLA** step and set `First Reply Time (FRT)`, `Next Reply Time (NRT)`,
     `Time to Close (TTC)`, and — for tickets — `Time to Resolution (TTR)`.
  4. Toggle pause rules: pause the timer when the conversation is `snoozed` or `waiting on customer`.
  5. Add **conditional branches** with And/Or filters so a VIP plan gets a faster SLA.
  6. Link the office-hours schedule; **Save and set live**.
  7. Monitor: the inbox shows a countdown badge (grey > 5 min, orange < 5 min, red overdue); sort the
     inbox by *Next SLA target*; review the SLAs report filtered by SLA name and team.
- **data_flow:** Message/reply timestamp on the Conversation or Ticket → SLA policy (FRT/NRT/TTC/TTR
  targets + linked office-hours calendar + pause flags) → a live timer per metric → the timer's state is
  written back to the inbox conversation row (badge + nearest target) and to the conversation event log
  (`[SLA name] was applied by [Workflow name]`) → aggregate hit/miss/fixed rates flow into the SLAs report.
- **data_sources:** Intercom Conversation and Ticket objects; Office hours configuration; SLA policy
  objects; Teams; report/event store.
- **apis_hit:**
  - `GET /tickets`, `GET /tickets/{id}` to read ticket state; `PUT /tickets/{id}` to update ticket fields.
  - `GET /tickets/{ticket_id}/state` / ticket-state resources; `POST /tickets/{id}/reply` to close the loop.
  - Webhooks: `ticket.created`, `ticket.state.updated`, `ticket.resolved`, `ticket.closed`,
    `ticket.admin.snoozed`, `ticket.admin.unsnoozed`, `conversation.admin.snoozed`.
  - Intercom exposes SLA **metrics & attributes** for custom reports (linked from the SLA doc).
- **automations:** The SLA timer is a continuous background timer, not a discrete job. Policy changes
  ("all new conversations immediately use the updated definition") and Workflow matching apply the SLA
  automatically. Only one active SLA per conversation: "Intercom supports only one active SLA per
  conversation, so when a Workflow applies a new SLA later, the existing SLA is automatically removed."
- **features_tools:** `Settings > Inbox > SLAs` (create / edit / clone / archive, with a **Used by**
  column listing dependent workflows); Workflow builder **Apply SLA** step; conditional branches with
  And/Or rules; Inbox SLA badge + "Next SLA target" sort; Details sidebar metrics; SLAs report; custom
  reports.
- **extensibility:** Webhook topics (`ticket.state.updated`, `ticket.closed`) let an external system
  mirror SLA state; an external system can drive the ticket lifecycle via `PUT /tickets/{id}` and
  `POST /tickets/{id}/reply`; custom reports can be built on SLA metrics.
- **sources:**
  - https://www.intercom.com/help/en/articles/6546152-set-slas-for-conversations-and-tickets
  - https://www.intercom.com/help/en/articles/2672572-slas-report
  - https://developers.intercom.com/docs/references/rest-api/api.intercom.io/tickets
  - https://developers.intercom.com/docs/references/webhooks/webhook-models
- **evidence:**
  - "SLAs are managed from `Settings > Inbox > SLAs`, where you can create, edit, clone, and view which
    workflows depend on each one."
  - "Set time-based targets for your Sales team to reply to high-value leads."
  - "SLA rules take your office hours into consideration. For example, if your office hours are set to
    9am - 6pm, and your SLA first response time is 15 minutes, a message received at 5:50pm will have an
    expected response time of 9:05am on the next working day."
  - "Note: If an SLA is missed while a conversation is snoozed, the conversation will be automatically
    unsnoozed and re-opened."
  - "**Red**: Overdue. **Orange**: Less than 5 minutes left on the timer. **Grey**: More than 5 minutes
    left on the timer."
  - "The Live tab includes a Used by column showing how many workflows reference each SLA."

---

## 6. Escalate just before / when an SLA target is breached

- **name:** Escalate on SLA breach with a pre-warning head start
- **user_flow:**
  1. `Automation > Workflows` → create a new Workflow.
  2. Trigger category **During a conversation** → select **Before SLA breach**.
  3. Choose the **SLA target** to watch (First Response Time / Next Response Time / Resolution Time /
     Time to Close).
  4. Set the **lead time** (e.g. 10 minutes) — must be shorter than the target.
  5. Add actions: assign the conversation, notify a teammate or Slack channel, add a tag, leave a note.
  6. Set the workflow live. Optionally add a second workflow with the **When SLA breaches** trigger
     (no lead time).
- **data_flow:** Live SLA countdown on the conversation → Intercom's ~1-minute SLA check → at
  (target − lead time) the workflow fires, or at breach the workflow fires → actions mutate the
  conversation (assignment, tag, internal note) and/or send a Slack message → the inbox conversation is
  escalated in priority.
- **data_sources:** SLA policy objects and live timers; Conversation object; Team/Admin assignment;
  Tags; Slack (via Slack app / Data Connector).
- **apis_hit:**
  - `POST /messages` / `POST /tickets/{id}/reply` for internal notes.
  - `POST /tickets/{id}/tags` for tagging; `PUT /tickets/{id}` to change team/assignee.
  - Data Connectors: `GET /data_connectors`, `POST /data_connectors`, `GET /data_connectors/{id}`,
    `PATCH /data_connectors/{id}`, `DELETE /data_connectors/{id}`,
    `GET /data_connectors/{id}/execution_results`, `GET /data_connectors/{id}/execution_results/{id}` —
    for the HTTP call that posts the Slack alert.
  - Webhook `data_connector.execution.completed` reports the outcome of the alert call.
- **automations:** Fully automatic, timer-driven. Two triggers, `Before SLA breach` and `When SLA
  breaches`, both under the **During a conversation** trigger category. Timing is approximate:
  "Intercom checks SLAs about once a minute". Gated: "A Workflow runs while the conversation is still open
  and the SLA is still active. If a teammate replies, snoozes, or closes the conversation before the
  trigger point, the Workflow doesn't run."
- **features_tools:** Workflow builder trigger picker; SLA target selector; lead-time field; assignment /
  notify / tag / note actions; Data Connector action; `data_connector.execution.completed` webhook with
  `error_type` categorisation.
- **extensibility:** Data Connectors are the documented extension point: "Data connectors allow workflows
  and AI agents to make HTTP requests to external APIs", and each execution emits
  `source_type: "workflow"` plus `success`, `http_status`, `error_type`, `execution_time_ms`.
- **sources:**
  - https://www.intercom.com/help/en/articles/15362781-trigger-a-workflow-before-or-when-an-sla-breaches
  - https://www.intercom.com/help/en/articles/6546152-set-slas-for-conversations-and-tickets
  - https://developers.intercom.com/docs/references/rest-api/api.intercom.io/data-connectors
  - https://developers.intercom.com/docs/references/webhooks/webhook-models
- **evidence:**
  - "**Before SLA breach** — runs a chosen amount of time before an SLA target is due to breach." /
    "**When SLA breaches** — runs the moment an SLA target is missed."
  - "For the **Before SLA breach** trigger, the lead time must be **shorter than the SLA target's
    duration**, or the Workflow won't run."
  - "Intercom checks SLAs about once a minute, so a Workflow runs within roughly a minute of the target
    time, rather than to the exact second."
  - "Note: SLA Workflow triggers are available on the **Expert** plan and are currently in beta."
  - `data_connector.execution.completed` → `"source_type": "workflow"`, `"success": false`,
    `"http_status": 400`, `"error_type": "request_configuration_error"`.
  - "SLA Workflow triggers … The SLA-based Workflow trigger is not supported for Back-office Tickets or
    Tracker Tickets."

---

## 7. Escalate a conversation converted into a ticket to the owning team

- **name:** Escalate a converted conversation into an owned ticket
- **user_flow:**
  1. Create a Workflow with the trigger **"A ticket is created"**.
  2. In the audience/conditions, narrow to the relevant ticket type (e.g. escalation tickets).
  3. Add actions: notify the responsible team in Slack, change the assigned team, apply an SLA, tag the
     linked conversation.
  4. Optionally add a second Workflow on **"Teammate changes the state of a ticket"** to fire when the
     ticket is closed.
  5. Save and set live.
- **data_flow:** A conversation is converted to a ticket (or a ticket is created via API / inbound) →
  Ticket object is created with a ticket type, state, and (if converted) a linked conversation →
  the `ticket is created` trigger fires → actions fire: Slack channel message, `ticket.team.assigned`
  update, SLA applied, tag written → the ticket's state transitions emit
  `ticket.state.updated` / `ticket.resolved` / `ticket.closed` webhooks for downstream systems.
- **data_sources:** Intercom Ticket object (`ticket_type`, `ticket_state`, `contacts`); Conversation
  (when converted); Teams; Tags; Ticket type attributes; Slack.
- **apis_hit:**
  - `POST /tickets` — create a ticket.
  - `POST /enqueue/tickets` — "Enqueue create ticket".
  - `POST /tickets/{id}/reply` — admin reply / internal note on the ticket.
  - `POST /tickets/{ticket_id}/tags`, `DELETE /tickets/{ticket_id}/tags/{tag_id}`.
  - `POST /tickets/{id}/link_conversation`, `DELETE /tickets/{id}/unlink_conversation`.
  - `POST /tickets/change_type` — change ticket type.
  - `POST /tickets/search` — find tickets.
  - Webhooks: `ticket.created`, `ticket.team.assigned`, `ticket.admin.assigned`, `ticket.state.updated`,
    `ticket.closed`, `ticket.resolved`, `ticket.contact.attached`.
- **automations:** Triggered by ticket lifecycle events, no seller action needed. Documented limits:
  "Only background actions are available under this trigger"; "Tag conversation, Apply SLA, and Set CDA
  actions will only work if the ticket acted upon was converted from a conversation"; "While using the
  workflow trigger 'ticket is created' the workflow will not trigger from tracker ticket creation."
- **features_tools:** Workflow trigger list; background-only action set; team assignment; Slack app in
  Intercom; ticket type attributes; reusable bots to reach actions unavailable on ticket triggers.
- **extensibility:** The ticket API is fully public, so an external system can create tickets
  (`POST /tickets`), change types, attach contacts, and consume all `ticket.*` webhooks. Note the
  `ticket.closed` vs `ticket.resolved` payload-shape difference in v2.15+.
- **sources:**
  - https://www.intercom.com/help/en/articles/7434613-how-to-trigger-a-workflow
  - https://developers.intercom.com/docs/references/rest-api/api.intercom.io/tickets
  - https://developers.intercom.com/docs/references/webhooks/webhook-models
- **evidence:**
  - "1) When a support conversation is converted to an operational ticket send a notification to the
    responsible team Slack channel 2) When a frontline teammate convert the conversation to a specific
    ticket type, auto escalate to the responsible team (e.g change the team assigned) 3) When a new
    escalation ticket is created send a notification to the responsible team Slack channel."
  - "Tag conversation and Set CDA are not available on ticket triggers, but can be accessed through
    reusable bots."
  - "**ticket.resolved** - Fires when a ticket's state transitions to 'resolved'. This is a state-based
    event." / "**ticket.closed** - Fires when a ticket conversation is closed. This is separate from state
    transitions."
  - "`ticket.closed` — the Ticket is nested under a `ticket` field on `data.item`".

---

## 8. Send a multi-channel reminder: push notification plus in-app inbox fallback

- **name:** Send a multi-channel reminder with an in-app inbox fallback
- **user_flow:**
  1. `Settings > In-App` → **Notification Inbox** tab → **Create an inbox**, give it a name.
  2. Click **Edit in Design Studio** → *Patterns → Inbox*; set the icon, appearance, unread indicator,
     and position; set dark-mode styles; **Save Updates**.
  3. Return to inbox settings and click **Publish** (or **Set to draft** to unpublish).
  4. Compose a **push notification** in an automation / broadcast / transactional message; enable
     "send a copy of the push notification to the notification inbox" so buyers who dismissed the push,
     have no push-capable device, or had a delivery failure can still find it.
  5. Optionally also add an inbox message in the workflow builder and send it to the inbox.
- **data_flow:** A person/device event (or automation step) → Customer.io push provider send → device
  token lookup → OS-level push (iOS lock screen / Android notification panel) → a duplicate
  `inbox` message is written to the workspace's notification inbox → the app's SDK fetches and renders
  the inbox (floating icon + unread badge) → open/click metrics recorded against the person.
- **data_sources:** Customer.io Person profile + device tokens; `default app` definition for mobile;
  Inbox message store; JavaScript/iOS/Android SDK inbox module; push provider (APNs/FCM).
- **apis_hit:**
  - Customer.io **App API** (bearer auth) — "Trigger transactional messages: send receipts, password reset
    requests, and important notifications"; rate limit "10 requests per second" with `429` +
    `Retry-After` on the App API.
  - **Transactional messages** endpoint: "3,000 requests per 3 seconds. Transactional sends go through the
    same ingress as the Track and Pipelines APIs and share their soft limit."
  - **SDK** surface: "listen for messages with the SDK's `inbox()` method" for the build-your-own-inbox path.
- **automations:** The workflow/automation/transactional message is the automation; delivery and inbox
  rendering are automatic. Constraint documented: "You can have **one notification inbox per workspace**",
  and the visual inbox "only serves your workspace's **default app** and your website".
- **features_tools:** Workspace Settings > In-App > Notification Inbox; Design Studio *Patterns → Inbox*;
  publish/draft toggle; push notification builder with notification-inbox copy toggle; in-app message
  builder; delivery/metrics dashboards.
- **extensibility:** "Build your own inbox" path renders inbox + messages yourself, with messages
  "Sent and delivered as JSON payloads"; you can target different apps in the same workspace via that
  path; the App API lets a backend trigger the whole chain.
- **sources:**
  - https://docs.customer.io/messaging/channels/in-app/inbox/get-started.md
  - https://docs.customer.io/messaging/channels/push/send-push.md
  - https://docs.customer.io/integrations/api/customerio-apis.md
- **evidence:**
  - "A notification inbox is a container that displays messages to your audience that they access at
    their leisure."
  - "You can also send a copy of a push notification as an inbox message. This helps people find your
    message if they dismissed the push, don't have a device that can receive the push, or the push
    delivery failed."
  - "**Build your own inbox** … You render the inbox and messages yourself … Sent and delivered as JSON
    payloads; listen for messages with the SDK's `inbox()` method."
  - "You can have **one notification inbox per workspace**." / "The visual notification inbox only serves
    one mobile app per workspace."
  - "1. **Create an inbox** in your workspace settings and give it a name. 2. **Style the inbox** in
    Design Studio … 3. **Publish the inbox** … 4. **Compose and send messages** to the inbox from an
    automation, broadcast, or transactional message."

---

## 9. Notify the internal team in Slack from a buyer signal

- **name:** Notify the internal team in Slack from a buyer signal
- **user_flow:**
  1. `Workspace Settings` → **Get Started** next to Slack Message → **Enable** → authorise the Slack app
     with the **bot** token scope → select a workspace (and a channel, required at connect time).
  2. Open/create an automation, drag the **Slack** block into the workflow builder.
  3. In the composer, write the message, use `{{customer.channel}}` / `{{customer.username}}` Liquid tags,
     `<@U012AB3CD>` to mention a user, and `<https://…|label>` to embed a link.
  4. Choose the destination channel per message.
  5. Open the **Send test** modal, pick a test channel/user, run it, and confirm the message lands.
- **data_flow:** A trigger event (segment/attribute change, event, form submission, object update, webhook)
  → automation evaluates the trigger → Slack block renders Liquid against the person/company profile →
  message is posted through the authorised Slack bot to a channel or DM → the trigger data / person
  identity is delivered to the internal team, and the Slack action is excluded from conversion
  attribution.
- **data_sources:** Customer.io Person + Company profiles and their attributes; Segments; Trigger events;
  Slack workspace OAuth (bot scope).
- **apis_hit:**
  - **App API** (bearer auth) to trigger transactional messages / broadcasts; documented rate limits:
    "App API 10 requests per second", "API-triggered broadcasts 1 request every 10 seconds".
  - Slack token type referenced directly: "provide **Slack credentials with the 'bot' scope**, letting
    Customer.io post to your Slack workspace".
  - Slack formatting surface linked: `https://api.slack.com/reference/surfaces/formatting` (mentions,
    link embedding).
- **automations:** Triggered by the automation's own trigger, or — unusually — by a **webhook**-triggered
  automation, which otherwise cannot message anyone: "you can trigger a slack message. This provides a
  way to notify a group of people via Slack when this workflow is triggered."
- **features_tools:** Workspace Settings integration screen; Slack block in the workflow builder; Liquid
  tags; mention + link-embed syntax; **Send test** modal.
- **extensibility:** Channel can be a private channel (invite the bot with `/invite @Customer.io`); you
  can build a custom app on the App API that posts to Slack programmatically; Liquid +
  `{{customer.*}}` lets an internal notification be personalised per buyer.
- **sources:**
  - https://docs.customer.io/messaging/channels/slack/get-started.md
  - https://docs.customer.io/messaging/send/automations/data-workflows/webhook-triggered-automations.md
  - https://docs.customer.io/integrations/api/customerio-apis.md
- **evidence:**
  - "Customer.io's Slack Action helps your teams work better together by passing information directly
    into Slack triggered by user behavior in your app."
  - "When you configure Slack in Customer.io, you need to provide **Slack credentials with the 'bot'
    scope**, letting Customer.io post to your Slack workspace."
  - "To mention specific users in your slack message, follow this format: `Hey <@U012AB3CD>, thanks for
    submitting your report.`"
  - "If you want to post messages to a private channel, you must invite your bot to a channel with the
    `/invite @Customer.io` command."
  - "Slack and *Create or update person* actions are often internal or used for analytics purposes;
    they don't always send messages to end-users. For that reason, we don't attach conversions to them."

---

## 10. Ingest third-party data by webhook and fan it out as events

- **name:** Ingest third-party data by webhook and fan it out as events
- **user_flow:**
  1. Go to **Automations** → **Create Automation** → click **Webhook**.
  2. Copy the generated **Webhook URL** and give it to the external service (or trigger it once so
     Customer.io receives representative data).
  3. **Save & Next**.
  4. Drag in **Send Event** (or **Create or Update Person**, or **Batch Update**, or **Send data**) and
     map trigger attributes / Liquid / JavaScript to the outgoing event `name` + `data`.
  5. Optionally append a **Slack** block, because webhook automations have no person to message.
  6. **Review items** → **Start Automation**.
- **data_flow:** External service (e.g. commerce platform, data platform, Zapier/Segment replacement)
  → `POST` JSON of any shape to the generated Webhook URL → Customer.io parses it into a **Trigger data**
  object → Liquid (`{{trigger.identifiers.id}}`) or JavaScript (`return triggers.identifiers.id;`)
  transforms fields (e.g. `purchased_at | date: %s`) → `Send Event` writes a person-scoped event
  (`name` + `data`) into the workspace, or `Batch Update` fans attributes/events out to up to 1000 people
  matched by an identifier → that event triggers downstream event-triggered automations that send
  messages → reporting webhooks can push delivery/engagement data out to a downstream system.
- **data_sources:** External HTTP source (arbitrary JSON); Customer.io Person (identifier type: id, email,
  phone, or custom); Objects & relationships; warehouse/downstream webhook target.
- **apis_hit:**
  - Generated **Webhook URL** (inbound `POST`) as the entry point.
  - **Send data** action forwards a webhook out of Customer.io ("Use the **Send data** action to send a
    webhook. This forwards data to another service outside Customer.io").
  - **Pipelines API** (recommended ingress; `POST` only, `/track` endpoint with semantic event names such
    as `Delete Person` / `Suppress Person`, plus `/group` for objects and relationships) — the documented
    path for "set data on a person" and batch fan-out.
  - **Reporting webhooks** spec: `https://docs.customer.io/files/journeys-webhooks.json`.
  - Rate limits: Track/Pipelines 3,000 req / 3 s (soft); App API 10 req/s; transactional 3,000 req / 3 s.
- **automations:** Entirely automatic once started; external systems call the URL, no operator action.
  Hard constraint: "webhook-triggered automations don't typically send messages directly"; "Any attempt
  to reference customer data will return `""`".
- **features_tools:** Webhook automation trigger + generated URL; Send Event / Create or Update Person /
  Batch Update / Send data blocks; Liquid and JavaScript value types; downstream automation triggers;
  Data-out integrations (reporting webhooks, warehouse syncs).
- **extensibility:** The webhook automation is explicitly a bridge: "Another service—using Customer.io as
  a bridge between data platforms, like what Zapier and Segment do!" Objects + relationships give
  account-level fan-out; `Batch Update` handles "a group of up to 1000 people"; a `Send data` action
  returns the data to another system, closing the loop.
- **sources:**
  - https://docs.customer.io/messaging/send/automations/data-workflows/webhook-triggered-automations.md
  - https://docs.customer.io/integrations/api/customerio-apis.md
  - https://docs.customer.io/llms.txt
- **evidence:**
  - "Customer.io generates a *Webhook URL*. You'll provide this URL to the service or platform that you
    capture data from and set up rules determining when that service or platform will call your webhook,
    sending data to Customer.io."
  - "You cannot reference existing customer attributes in webhook-triggered automations because people
    aren't the subject of the automation: the webhook is."
  - "In a webhook automation, you can use the *Batch Update* action to update one or more people based on
    an identifying value in your incoming webhook—like all people associated with an account."
  - "A **batch update** lets you associate your incoming data with a group of up to 1000 people."
  - "This is the API that most of our integrations are based on" (Pipelines API recommendation).
  - "the Pipelines API *only* uses `POST` operations. You'll perform deletes and other operations by
    sending events with specific names."

---

## 11. Start a workflow from an external system's webhook

- **name:** Start a workflow from an external system's webhook
- **user_flow:**
  1. In HighLevel, open or create a workflow and select the trigger **Inbound Webhook**.
  2. Copy the generated unique Webhook URL.
  3. In the external app (Zapier, Integromat, Stripe, …) create a trigger that sends a `POST` request
     with a JSON body to that URL.
  4. Send a test request to validate the integration.
  5. Back in HighLevel, map the incoming data to fields/variables in the workflow.
  6. Click **Save Trigger**, then add downstream actions and publish.
- **data_flow:** External system event (form submission, payment, feed) → `POST/GET/PUT` JSON to the
  per-trigger webhook URL → HighLevel parses the payload into workflow data → mapping step binds payload
  keys to workflow variables/filters → downstream HighLevel actions (e.g. update contact field, create a
  contact-less task, post to Google Sheets) execute.
- **data_sources:** External HTTP source; HighLevel sub-account (Location) CRM records; Google Sheets
  (via the Google Sheets action); Slack; workflow variable store.
- **apis_hit:**
  - Per-trigger generated **HighLevel Webhook URL** accepting `POST`/`GET`/`PUT` — "A unique Webhook URL
    will be generated for your workflow."
  - HighLevel v2 REST base host is visible in the Marketplace trigger docs as
    `https://services.leadconnectorhq.com/...` (the services host used for workflow internals).
  - Companion outbound surface: the **Webhook/Custom Webhook** action ("Sends data from HighLevel to
    external applications or services") and the **Google Sheets** action.
- **automations:** Webhook-driven trigger, no HighLevel user action. Documented payload example
  `{"customer_id": "CUST_001", "amount": 100, "currency": "USD", "status": "successful"}` feeding
  "Update the lead's status to 'Paid.'", "Send a confirmation email to the customer", "Trigger additional
  follow-up actions based on the payment status".
- **features_tools:** Workflows builder trigger menu; webhook URL generator; data-mapping UI; Webhook and
  Google Sheets actions; inbound-webhook trigger also premium-tiered ("Workflow Premium Trigger").
- **extensibility:** Pair inbound webhooks with **Marketplace Workflow Triggers** for a fully
  bidirectional integration: HighLevel subscribes to the trigger's `targetUrl`, and the app's
  `Subscription URL` receives `CREATED` / `UPDATED` / `DELETED` events. Outbound **Custom Webhook**
  action can be used to push HighLevel data back to the same third party.
- **sources:**
  - https://help.gohighlevel.com/support/solutions/articles/155000003147-workflow-trigger-inbound-webhook
  - https://help.gohighlevel.com/support/solutions/articles/155000002292-a-list-of-workflow-triggers
  - https://help.gohighlevel.com/support/solutions/articles/155000002294-what-are-workflow-actions-complete-list-
  - https://marketplace.gohighlevel.com/docs/marketplace-modules/CustomTriggers/
- **evidence:**
  - "The Inbound Webhook Trigger captures incoming POST/GET/PUT requests sent to a HighLevel Webhook URL
    (unique for each trigger)."
  - "Generate Webhook URL: A unique Webhook URL will be generated for your workflow. Copy this URL for
    use in external applications."
  - "**Inbound Webhook:** Fires when data is received at the workflow's webhook URL."
  - "**Scheduler:** Fires on a time-based schedule without needing a contact."
  - "**Webhook/Custom Webhook:** Sends data from HighLevel to external applications or services."

---

## 12. Publish custom workflow triggers and actions to an app marketplace

- **name:** Publish custom workflow triggers and actions to an app marketplace
- **user_flow:**
  1. Sign up at `marketplace.gohighlevel.com`; log in and open **My Apps**.
  2. Under **Modules → Workflow**, click **Create Trigger** (or **Create Action**).
  3. Define **Name**, an immutable **Key** (e.g. `mycustomtrigger`), **Icon**, **Short Description**,
     **Summary**.
  4. Paste a **sample JSON payload** — this is what powers filters and custom variables in the workflow UI.
  5. Define **Filters** (String / Select / Multi-Select / Dynamic, with option sources: Constants,
     Internal Reference, External API `GET`).
  6. Define **Custom Variables** mapped to keys in the sample payload.
  7. Set the **Subscription URL** (`POST`) that HighLevel will call on trigger `CREATED` / `UPDATED` /
     `DELETED`.
  8. **Submit for Review** with a changelog; version the module; on approval it is available to all
     sub-accounts.
- **data_flow:** Builder authors the trigger module (key, sample JSON, filters, dynamic filter API) →
  HighLevel Marketplace review → published trigger appears in every sub-account's "add a trigger" menu →
  an operator configures filters in a workflow → HighLevel `POST`s the full trigger config
  (including `targetUrl`) to the app's Subscription URL → the app then fires on the event and HighLevel
  resolves filters against the payload; the app's Custom Action `POST`s execution data
  (`{data, extras:{locationId, contactId, workflowId}, meta:{key, version}}`) to its own API, returns
  sample-shaped JSON, and HighLevel binds that to custom variables / arrays / custom code / webhooks.
- **data_sources:** HighLevel sub-account (Location), Contact, Company, Custom Object, Opportunity data;
  app developer API (for Dynamic filter options and Action execution); app's own database.
- **apis_hit:**
  - Trigger `targetUrl` of the form
    `https://services.leadconnectorhq.com/workflows-marketplace/triggers/execute/abc/def` — where the app
    signals trigger satisfaction.
  - App's **Subscription URL** (`POST`) receiving `triggerData` with `eventType` ∈
    `CREATED | UPDATED | DELETED`, `filters[]`, `targetUrl`, plus `meta` and
    `extras: {locationId, workflowId, companyId}`.
  - Trigger **Dynamic filter** URL (`POST`) returning
    `{"filters":[{"field":..,"title":..,"fieldType":..,"required":..}]}` or
    `{"options":[{"label":..,"value":..}]}`.
  - Action **Execution** URL (`POST`) or **Custom code** (JS) returning an object/array.
  - Action **Pause** resume webhook when Pause Execution is enabled.
- **automations:** The published module becomes a reusable automation primitive: filters evaluate
  continuously, and action execution is performed by the app at the moment the workflow reaches that
  node. Documented caveat: "Any workflows using the deleted trigger will skip its execution."
- **features_tools:** Developer Portal (My Apps, Create App, Pricing, White-Label vs Standard listing,
  Distribution Type Agency vs Sub-Account, "Get Marketplace Share Link" + IP Protection); Modules →
  Workflow → Create Trigger / Create Action; field/filter/branch builders; multi-branch support
  (`Allow New Branches`, `Is Predefined Branches Editable`, `Show Branches Section`); validation rules
  (pre-defined, regex, arrow function); version management and Submit for Review.
- **extensibility:** This *is* the extension mechanism. A third party can define their own trigger
  (`Key`, `sample JSON`, dynamic filters) and their own action (fields, dynamic fields, hidden fields,
  branches, pause/resume, custom code), version them, and get them reviewed for distribution to all
  sub-accounts. Documented: "Only one Dynamic type can be created per trigger" / "per action".
- **sources:**
  - https://marketplace.gohighlevel.com/docs/marketplace-modules/CustomTriggers/
  - https://marketplace.gohighlevel.com/docs/marketplace-modules/CustomActions/
  - https://marketplace.gohighlevel.com/docs/marketplace-modules/WorkflowActionsAndTriggers/
  - https://marketplace.gohighlevel.com/docs/marketplace-modules/Snapshots
- **evidence:**
  - "Assign a unique identifier (e.g., `mycustomtrigger`). This key is immutable and used to reference
    the trigger within workflows."
  - "The Subscription URL is an API endpoint that receives trigger configuration details whenever the
    trigger is created, updated, or deleted in a workflow."
  - `"targetUrl": "https://services.leadconnectorhq.com/workflows-marketplace/triggers/execute/abc/def"`.
  - "Workflow can run contactless without any Contact data dependency so you can send any payload data
    via Marketplace Triggers and use it in workflow."
  - "The Multi-Branch Feature enables the creation of branches that can dynamically adjust based on various
    predefined conditions."
  - "Once approved, the version submitted for review will be published live to all Sub-accounts."

---

## 13. Create an automated seller follow-up task with a reminder

- **name:** Create an automated seller follow-up task with a reminder
- **user_flow:**
  1. In a HighLevel workflow, add the **Add Task** action.
  2. Choose whether the task attaches to an existing contact or is created **contact-less**
     (documented: "creates a contact-less task if the workflow is running without a contact. Pair with
     Inbound Webhook to create internal tasks even when no contact exists").
  3. Set the task title/body, due date and reminder time.
  4. Optionally add a **Send Internal Notification** action to the assigned user alongside the task.
  5. Publish the workflow; the task appears in the assignee's task list / conversation timeline.
  6. (HubSpot equivalent, if that is the system of record: `POST /crm/v3/objects/tasks` with
     `hs_timestamp` = due date, `hs_task_reminders` = reminder timestamp, `hubspot_owner_id` = assignee,
     plus an `associations` array linking the contact/deal.)
- **data_flow:** Engagement signal (task added, task reminder due, contact tag added, engagement score
  rule met, opportunity stage changed, inbound webhook) → workflow branch/filter evaluates → `Add Task`
  writes a Task record (due date + reminder timestamp) and attaches it to the Contact (or writes a
  standalone internal task) → the assignee's dashboard surfaces it; the `Task Reminder` trigger re-fires
  the workflow at the reminder time → `Task Completed` trigger can fire the next-step automation.
- **data_sources:** HighLevel Contact / Opportunity / Custom Object; workflow engine task store; assignee
  (user) records. HubSpot side: CRM Task object, CRM Owner (`hubspot_owner_id`), CRM associations.
- **apis_hit:**
  - HubSpot `POST /crm/v3/objects/tasks` — create a task; required property
    `hs_timestamp` ("This field marks the task's due date"), optional `hs_task_body`,
    `hs_task_subject`, `hs_task_status` (`COMPLETED` / `NOT_STARTED`), `hs_task_priority`
    (`LOW` / `MEDIUM` / `HIGH`), `hs_task_type` (`EMAIL` / `CALL` / `TODO`), `hubspot_owner_id`, and
    `hs_task_reminders` ("The timestamp for when to send a reminder for the due date of the task. You must
    use Unix timestamp in milliseconds").
  - `GET /crm/v3/objects/tasks/{taskId}`, `GET /crm/v3/objects/tasks`, `PATCH /crm/v3/objects/tasks/{taskId}`,
    `DELETE /crm/v3/objects/tasks/{taskId}`.
  - `PUT /crm/v3/objects/tasks/{taskId}/associations/{toObjectType}/{toObjectId}/{associationTypeId}` and
    the matching `DELETE` to link/unlink a task to a record.
  - `DELETE /crm/v3/objects/tasks/{taskId}` "will add the task to the recycling bin".
  - Scopes: `crm.objects.contacts.read`, `crm.objects.contacts.write`.
  - HighLevel trigger names: **Task Added**, **Task Reminder**, **Task Completed**.
- **automations:** `Task Added` / `Task Reminder` / `Task Completed` are the documented triggers, so task
  lifecycle drives further automation with no user action. In HubSpot, the `Create task` workflow action
  can be fed variables "from different object sources" and can reference "records previously created in
  your workflow".
- **features_tools:** HighLevel `Add Task` action; `Send Internal Notification` action; workflow
  Task triggers; HighLevel "If/Else" with "Task ID appears in task-related workflows, such as task triggers
  or after an Add Task step". HubSpot: `Create task` action, data panel variables, task timeline,
  `hs_pinned_engagement_id` pinning.
- **extensibility:** A third party can create tasks in HubSpot via the public Tasks API and control the
  assignee via `hubspot_owner_id`; tasks can be pinned to the record timeline by writing the task `id` into
  `hs_pinned_engagement_id` on the object APIs. On HighLevel, a Marketplace Custom Action can create the
  task in an external system and pause/resume the workflow around it.
- **sources:**
  - https://help.gohighlevel.com/support/solutions/articles/155000002294-what-are-workflow-actions-complete-list-
  - https://help.gohighlevel.com/support/solutions/articles/155000002292-a-list-of-workflow-triggers
  - https://developers.hubspot.com/docs/api-reference/crm-tasks-v3/basic/post-crm-v3-objects-tasks
  - https://knowledge.hubspot.com/workflows/create-workflows
- **evidence:**
  - "**Add Task:** Creates a task related to a contact. Attaches to the contact if one exists; **creates
    a contact-less task** if the workflow is running without a contact. Pair with Inbound Webhook to
    create internal tasks even when no contact exists."
  - "**Task Reminder:** Fires when the task's reminder time is reached." /
    "**Task Completed:** Fires when a task for the contact is marked completed."
  - "`hs_task_reminders` | The timestamp for when to send a reminder for the due date of the task. You
    must use Unix timestamp in milliseconds."
  - "For certain actions, such as the *Create task* action, you can also use the data panel to insert
    data variables from different object sources."
  - "Task ID appears in task-related workflows, such as task triggers or after an Add Task step."

---

## 14. Sweep stale opportunities by an inactivity rule and escalate

- **name:** Sweep stale opportunities and escalate the pipeline
- **user_flow:**
  1. Create a workflow and choose the trigger **Stale Opportunities**.
  2. Define the inactivity/stale rule (age since last activity / stage entry) and add filters
     (e.g. `Pipeline Stage Changed` context, `Pipeline Stage Changed` = specific stage, opportunity value
     band).
  3. Add actions: **Create/Update Opportunity** (move stage or update value), **Add Task** for the owner,
     **Send Internal Notification** to the rep/manager, optionally **Send Email** to the buyer, and
     **Remove from Workflow** so the sweep does not re-fire on the same record.
  4. Publish. Re-fire interval is controlled by the stale rule; add a **Wait** step to add a
     graduated cadence (e.g. nudge at 7 days, escalate at 21 days).
- **data_flow:** Opportunity record (stage, `date entered stage`, `last activity date`, value, owner) →
  stale-rule evaluation finds records exceeding the inactivity threshold → optional If/Else branch on
  stage/value → Opportunity field or pipeline-stage write + Task creation for the owner + internal
  notification to the rep's team → the opportunity's `Pipeline Stage Changed` and
  `Opportunity Changed` events can re-enter other workflows.
- **data_sources:** HighLevel Opportunity object and pipeline stages; Contact; assigned user; internal
  notification recipients; Google Sheets/Slack for reporting.
- **apis_hit:**
  - HighLevel workflow actions used: `Create/Update Opportunity` (writes to the pipeline),
    `Remove Opportunity`, `Add Task`, `Send Internal Notification`, `Send Email`, `Custom Webhook`,
    `Google Sheets`, `Wait Step`, `If Else`, `Remove from Workflow`, `Goal Event`, `Split`, `Arrays`,
    `Text Formatter`, `Custom Code`.
  - No public HighLevel REST endpoint for opportunity objects is documented on the pages read; the
    automation surface is the workflow builder. (Recorded as a gap below.)
- **automations:** `Stale Opportunities` is explicitly a scheduled-style trigger — "Fires when
  opportunities meet your inactivity/stale rule" — plus a family of related automatic triggers:
  `Opportunity Status Changed`, `Opportunity Created`, `Opportunity Changed`, `Pipeline Stage Changed`.
  `Remove from Workflow` is the documented idempotency control.
- **features_tools:** Workflow builder trigger menu; If/Else branch; Wait Step; Opportunity actions;
  Add Task; Send Internal Notification; Split (A/B on the pipeline); Google Sheets for reporting; custom
  code.
- **extensibility:** `Custom Webhook` action pushes the stale opportunity payload to any external URL;
  `Google Sheets` action writes/looks up rows; `Custom Code` and `Arrays` allow local transformation
  before the action; a Marketplace Custom Action can call an internal scoring service and pause/resume
  the workflow on its response.
- **sources:**
  - https://help.gohighlevel.com/support/solutions/articles/155000002292-a-list-of-workflow-triggers
  - https://help.gohighlevel.com/support/solutions/articles/155000002294-what-are-workflow-actions-complete-list-
  - https://help.gohighlevel.com/support/solutions/articles/155000002471-workflow-action-if-else
  - https://marketplace.gohighlevel.com/docs/marketplace-modules/CustomActions/
- **evidence:**
  - "**Stale Opportunities:** Fires when opportunities meet your inactivity/stale rule."
  - "**Opportunity Status Changed:** Fires when an opportunity's status changes (e.g., Open →
    Won/Lost)." / "**Pipeline Stage Changed:** Fires when an opportunity moves to a different pipeline
    stage."
  - "**Create/Update Opportunity:** Creates or updates an opportunity in the pipeline."
  - "**Remove from Workflow:** Removes contacts from a workflow. … Useful: Helps manage communication
    and actions by removing contacts from specific workflows, ensuring targeted interactions."
  - "**Wait Step** — Delays the workflow for a specific time. … Useful: scheduling actions or
    communications at a later time."

---

## 15. Order and throttle in-app guide prompts, and re-prompt after dismissal

- **name:** Order, throttle and repeat in-app guide prompts
- **user_flow:**
  1. In a guide's **Settings** tab, find the **Activation** card → **Edit display options**.
  2. Turn on **Repeat guide display**; set **Show every** = 1–10,000 (Day or Hour); under
     **Stop showing after** choose **Never** or **A set number of dismissals** (minimum 2).
  3. Optionally turn on **Ignore guide throttling** for genuinely critical guides, and/or
     **Delay guide display** (up to 60 s) so the prompt does not fire on landing.
  4. Go to `Guides > Guides` → **Ordering** tab, select the application, and set **Guide Throttling** to
     e.g. *1 guide every 1 day*.
  5. Reorder guides with the row arrows or drag-and-drop; guides that ignore throttling appear first.
  6. Filter the ordering table by Product Area, category, status and Page location; publish.
- **data_flow:** Visitor session + product-usage analytics (Pendo's tagged Pages / Features) → eligibility
  evaluation against the guide's segment and page/element anchoring → the guide renders in the app →
  visitor interaction is recorded (view, step advance, `pendo.onGuideAdvanced()`,
  `pendo.onGuideDismissed()`, Esc key) → dismissal restarts both the repeat timer and the global
  throttling countdown → the next guide in the Order list becomes eligible.
- **data_sources:** Pendo product-analytics data (tagged Pages, tagged Features, segments, visitor
  identity); guide definitions and their Order list; device type; Pendo Web SDK 2.8.0+ / Mobile SDK 2+;
  guide content/metrics store.
- **apis_hit:**
  - Pendo Web SDK programmatic activation: `https://web-sdk.pendo.io/public/guides#showguidebyidid-reason`
    — "With the web SDK API".
  - In-page callbacks: `pendo.onGuideDismissed()`, `pendo.onGuideAdvanced()`.
  - Pendo **Engage API / Location API** for URL transformation (noted as a guide-preview gotcha).
  - Guide permalinks as an activation surface: "Create a guide permalink".
- **automations:** Entirely automatic. Two distinct mechanisms: an **app-level throttle** ("Throttling
  determines the minimum amount of time that passes between when a visitor receives one automatic guide
  and when the next one launches. The countdown for the next guide launch begins after the previous guide
  is dismissed or completed.") and a **per-guide repeat** ("Repeat guide display relaunches a guide
  automatically after an eligible visitor has dismissed it, at an interval you define").
- **features_tools:** Guides > Guides > **Ordering** tab; Guide Throttling toggle with Day/Minute/Hour
  interval; Order list with drag-and-drop and top/up/down/bottom arrows; Activation card → **Edit display
  options**; Ignore guide throttling; Delay guide display; Repeat guide display (+ "Stop showing after");
  Snooze Guide button action; guide categories; Guide Goal; guide metrics; staging vs Public status.
- **extensibility:** Web SDK `showGuideById(id, reason)` for programmatic launch (from a Resource
  Center, another guide, or a permalink); `pendo.onGuideDismissed()` / `pendo.onGuideAdvanced()` hooks
  from custom code blocks; guide permalinks for deep links; Mobile SDK supports ordering + throttling
  (though "you can't override throttling for a single mobile guide").
- **sources:**
  - https://support.pendo.io/hc/en-us/articles/360031864452-Order-and-throttle-your-guides
  - https://support.pendo.io/hc/en-us/articles/360031864692-Overlay-guide-activation-options
  - https://support.pendo.io/hc/en-us/articles/360031864612-Define-guide-settings
  - https://support.pendo.io/hc/en-us/articles/360032203891-Create-a-guide-permalink
- **evidence:**
  - "Guide throttling controls the rate at which these guides are shown to visitors in each application,
    distributing them to limit how frequently visitors see automatic guides."
  - "Throttling determines the minimum amount of time that passes between when a visitor receives one
    automatic guide and when the next one launches. The countdown for the next guide launch begins after
    the previous guide is dismissed or completed."
  - "**Repeat guide display** relaunches a guide automatically after an eligible visitor has dismissed
    it, at an interval you define." / "You can set the interval to a number between 1 and 10,000, then
    choose either **Day** or **Hour**."
  - "**A set number of dismissals.** Enter how many times an eligible visitor must dismiss the guide
    before it stops displaying for them. The minimum is two dismissals, which includes guide completion."
  - "Dismissals occur when: … The visitor uses the keyboard **Esc** key. `pendo.onGuideDismissed()` is
    invoked in a code block or in your application's code."
  - "**Delay guide display** lets you set a wait period of up to 60 seconds before the guide appears, so
    you can avoid interrupting visitors the moment they land on a page."
  - "Requires … Pendo Web SDK version 2.8.0 or greater or Mobile SDK version 2 or greater."

---

## 16. Gate an automation on an external human approval, then resume

- **name:** Gate an automation on external human approval and resume
- **user_flow:**
  1. In the HighLevel workflow, add a **Marketplace Custom Action** from a Marketplace app that
     implements an approval step.
  2. In the action's **Configure** section, turn **Pause Execution** on.
  3. Set the action's **URL (POST)** to your internal approval service and define the fields/body it
     should receive.
  4. Click **Show API details** to copy the two resume-webhook URLs (Success Execution / Failed
     Execution).
  5. Have the human approver act in the external system; the approver's tool calls the resume webhook
     with the `extras` object as the body.
  6. On resume, use **Multi-Branch** so the contact is routed down the approved or rejected branch
     (`branchId` in the sync case; branch ID posted to the webhook in the async case).
  7. Add downstream actions (task, notification, email) and publish.
- **data_flow:** Workflow reaches the approval action node → the action `POST`s the mapped field payload
  plus `extras: {locationId, contactId, workflowId}` and `meta: {key, version}` to the internal approval
  service → the contact is **held** at this node ("the contact will be held at this action unless resume
  webhook is requested") → a human approves/rejects → the resume webhook is called with the `extras`
  body → the workflow continues on the branch selected by the returned `branchId` → downstream
  notification/task/CRM writes fire.
- **data_sources:** HighLevel sub-account/Location and Contact IDs; the internal approval service
  (queue + approver identity); the Marketplace app's API; response payload used to build custom variables.
- **apis_hit:**
  - Action execution URL (`POST`) with the documented envelope
    `{"data": {...}, "extras": {"locationId","contactId","workflowId"}, "meta": {"key","version"}}`.
  - The action's **resume webhook** for Success Execution and Failed Execution (revealed by
    "Show API details") — "the provided `extras` object needs to be passed as body payload for resume
    workflow endpoint".
  - `targetUrl` on the trigger side:
    `https://services.leadconnectorhq.com/workflows-marketplace/triggers/execute/abc/def`.
  - HubSpot analogue: a **Custom code action** or an inbound webhook enrollment
    (`When a webhook is received`) can be used to resume a paused flow externally.
- **automations:** Pause/resume is fully automatic on resume; the human step happens in the external
  system. Documented semantics:
  - **Sync:** "When the pause execution is turned off along with branching support, the contact will be
    moved to provided branch using `branchId` property from API response".
  - **Async:** "When the pause execution is turned off, the branch ID needs to be sent to the webhook for
    resuming".
- **features_tools:** Marketplace Custom Action builder; Pause Execution toggle; Show API details;
  Multi-Branch settings; Action "Custom code" mode (with mandatory **Test Code** step and
  "Test Result Success/Failed"); Custom Variables mapped from the sample Response Data; "Arrays" for
  list post-processing.
- **extensibility:** This is the documented pattern for a third-party app to participate in a workflow
  asynchronously. The app can also be published to the marketplace for all sub-accounts, versioned
  (`+ New Version`, `Submit for Review`), and use `Dynamic` fields to load forms from its own API. On
  the HubSpot side, `POST` to the workflow's webhook URL is the resume mechanism.
- **sources:**
  - https://marketplace.gohighlevel.com/docs/marketplace-modules/CustomActions/
  - https://marketplace.gohighlevel.com/docs/marketplace-modules/CustomTriggers/
  - https://knowledge.hubspot.com/workflows/set-when-a-webhook-is-received-workflow-triggers
- **evidence:**
  - "This toggle is used the contact will be held at this action unless resume webhook is requested. If
    this toggle is true then provided extras object needs to be passed as body payload for resume workflow
    endpoint."
  - "**Sync:** When the pause execution is turned off along with branching support, the contact will be
    moved to provided branch using `branchId` property from API response or from Custom Code using return
    statement."
  - "**Async:** When the pause execution is turned off, the branch ID needs to be sent to the webhook for
    resuming which is present in 'show API details' button."
  - "Testing the code is a mandatory step, if the test is not done then user will not be able to use the
    output of the code in the subsequent steps."
  - "In the workflow editor, in the left panel, under *Advanced options*, click **When a webhook is
    received**."

---

## 17. Enroll records on a schedule with re-enrollment control and backfill

- **name:** Enroll records on a schedule with re-enrollment control
- **user_flow:**
  1. `Automation > Workflows` → **Create workflow** → **From scratch** (or **With AI** using Breeze
     Assistant, or **From template**).
  2. In the **Trigger enrollment for [object]** card, choose the **Based on a schedule** trigger type
     (or filter-based / event-based / webhook-based).
  3. In the left panel **Settings** tab, toggle **Re-enroll** on and choose the re-enrollment triggers;
     optionally set **unenrollment criteria**.
  4. Add actions via the `+` icon (e.g. *Create task*, *Send internal notification*), inserting data
     variables from different object sources.
  5. Review with **Review and publish**; choose either *"Yes, enroll existing [objects] which meet the
     trigger criteria as of now"* or *"No, only enroll [objects] which meet the trigger criteria after
     turning the workflow on"*.
  6. Review **Timing & Notifications** and **Connections**, then **Turn on workflow**.
  7. Monitor via the **workflow history**.
- **data_flow:** CRM records (contacts/companies/deals/tickets/quotes/custom objects) are scanned on the
  workflow's schedule → records matching the enrollment filter are enrolled (either existing matches at
  publish time = backfill, or only future changes) → each enrolled record is held in an enrollment queue
  for the configured delay → actions fire in order (task creation, internal notification, field updates,
  associated-record updates) → the record's re-enrollment / unenrollment state and enrollment history are
  written to the workflow log, which in turn powers workflow-based filters.
- **data_sources:** HubSpot CRM objects (contact, company, deal, ticket, quote, custom object); custom
  properties (including properties with "Require unique values"); owner records; workflow enrollment
  history store.
- **apis_hit:**
  - `GET /crm/v3/objects/tasks/{taskId}` etc. (the action targets, documented above).
  - Webhook enrollment: HubSpot issues a **Webhook URL** per workflow; "You can create up to 100 unique
    webhook events"; the URL is `POST`ed with `application/json` only.
  - "The workflow will only trigger if there is an existing record with a corresponding unique property
    value in your HubSpot account."
- **automations:** The scheduled enrollment trigger itself is the job. Documented semantics:
  - "By default, records will only enroll in a workflow the first time they meet the enrollment triggers."
  - "Records that are currently enrolled in a workflow cannot be re-enrolled into that same workflow
    until they complete the workflow."
  - "For scheduled workflows, if you choose not to enroll existing records at activation, those records
    are not enrolled immediately, but they can still be enrolled the next time the workflow runs on its
    schedule if they still meet the enrollment criteria."
  - Retention disclosure: "90 days: all workflow action log data … 6 months: workflow enrollment history
    … 2 years+: historical data of enrollments … to enable workflow-based filters."
- **features_tools:** Workflows editor; trigger panel (filter-based / event-based / schedule / webhook);
  Settings tab with Re-enroll and unenrollment; minimap; placeholder actions; `Review and publish` panel
  (backfill choice, Timing & Notifications, Connections); workflow history; workflow template library
  (community + HubSpot Marketplace templates with "installs" count).
- **extensibility:** "Received a webhook from an external app" under **Custom events & external events**
  lets an external system start the workflow; **custom code** actions and data variables transform payloads;
  the HubSpot Marketplace distributes workflow templates; permission model gates it
  (Edit/Publish Workflows permissions, or Super Admin).
- **sources:**
  - https://knowledge.hubspot.com/workflows/create-workflows
  - https://knowledge.hubspot.com/workflows/set-when-a-webhook-is-received-workflow-triggers
  - https://developers.hubspot.com/docs/api-reference/crm-tasks-v3/basic/post-crm-v3-objects-tasks
- **evidence:**
  - "When setting up enrollment triggers, you can use the following enrollment trigger types:
    Filter-based, Event-based, Based on a schedule, Based on webhooks."
  - "Turning on re-enrollment does not automatically enroll records into the workflow multiple times.
    A record can only re-enroll if it has completed the workflow, and is enrolled again."
  - "For scheduled workflows, if you choose not to enroll existing records at activation, those records are
    not enrolled immediately, but they can still be enrolled the next time the workflow runs on its
    schedule if they still meet the enrollment criteria."
  - "You can create up to 100 unique webhook events."
  - "Webhook triggers only support incoming data sent with the *application/json* content-type."
  - "HubSpot will store workflow action logs data within the below time periods: 90 days … 6 months …
    2 years+."

---

## Gaps / could not fill from primary sources

1. **`websearch` tool was unavailable for the entire session** (`HTTP 401 Web search authentication
   failed`). Discovery relied on vendor doc indexes (`developers.hubspot.com/docs/llms.txt`,
   `docs.customer.io/llms.txt`), help-centre section pages, and HTML search fallbacks. Anything that could
   only be found by keyword search was not reachable.
2. **No public vendor documentation for automation/engagement features was found for actual
   digital-sales-room competitors** (Walnut, Attention, Repzo, Recapped, Dykata, Crank, Intro,
   Dealfront). This is a real hole for competitive parity: their engagement features are described only
   in marketing copy, not in primary docs, so nothing in this file comes from them. This was attempted
   via search only and could not be closed.
3. **HighLevel REST API surface for CRM objects (contacts, opportunities, tasks) was not obtainable.**
   The pages read document the workflow-builder action/trigger names and the Marketplace
   `services.leadconnectorhq.com` trigger `targetUrl`, but no public endpoint reference for
   `contacts`/`opportunities`/`tasks` was read. `apis_hit` for workflow 14 is therefore action-based
   rather than endpoint-based, and is flagged inline.
4. **HubSpot Sequences (Sales Hub) were not sourced.** The Sequences v4 OpenAPI spec
   (`automation-sequences-v4`) exists in the HubSpot spec index, but I did not read the guide page, and
   the knowledge-base pages I attempted (`/sales/create-sales-sequences`) returned 404. A dedicated
   "multi-step seller sequence with automatic enrollment" workflow is therefore **missing** from this
   file and should be sourced in a follow-up pass.
5. **HubSpot Service Hub SLAs were not sourced** (`/service-management/set-up-and-manage-slas` returned
   404). SLA coverage in this file is Intercom-only.
6. **Freshdesk / Zendesk escalation rules were not read from a primary source.** DuckDuckGo returned
   only secondary summaries (crmcurator.com, freshworks.com marketing page) and Freshdesk's own support
   article could not be located before the search fallback was rate-limited. Escalation coverage here is
   Intercom + HighLevel only.
7. **Klaviyo Flows** returned `HTTP 429` on the first fetch and was not retried; Klaviyo drip coverage is
   absent.
8. **Intercom `GET /export/workflows/{id}` is the only documented workflow-definition endpoint.** There is
   no documented public create/update endpoint for Series or Workflows, so "programmatically author a drip
   sequence" is **not** traceable to a primary source today. Likewise the Series webhook block's outbound
   payload shape is only documented in the help article, not in the REST reference.
9. **Pendo behavioural dwell-time targeting** is expressed as *delay before display* and *repeat after
   dismissal* in the docs read. An explicit "show after N seconds of dwell on page" rule was not found in
   the pages read; the dwell notion in workflow 15 is built from throttling + repeat + delay, and is
   labelled as such.
10. **Customer.io transactional-message endpoint paths** are referenced by link
    (`/integrations/api/app/tag/send-messages/`) in the pages read, but the concrete HTTP method/path was
    not captured; only the rate limits and the three-API model were quoted. The `apis_hit` field for
    workflows 8 and 9 reflects that gap.
