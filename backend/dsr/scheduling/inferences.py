"""Every judgement call in WF-064, in one inspectable place.

The research for WF-064 is explicit about some things and silent about others, and
the build brief asks for that distinction kept rather than blurred. Sourced
behaviour is in :mod:`dsr.scheduling.vocabulary` and
:mod:`dsr.scheduling.propagation`; this module is the assumed half, served at
``/api/wf-064/inferences`` so a reviewer can disagree with a *named* entry rather
than hunting through a diff.

A judgement call left as a comment in a function body is one nobody re-reads, and
a wrong one becomes product behaviour without anyone noticing. Each entry here is
named, traceable to what the research does and does not say, bounded by a ``value``
saying what this build chose, and carries a ``change_it`` so it can be changed
without editing a function body.
"""

from __future__ import annotations

from typing import Any

#: The line that governs the largest judgement here: where a link stops working.
EXPIRE_QUOTE = (
    "Expire Reschedule Link ... This setting allows you to decide if the reschedule link "
    "should expire after a meeting has happened. It can help with reporting purposes and "
    "tracking interactions with customers."
)

#: The line that governs the cancel's CRM side effect.
DELETE_EVENT_QUOTE = (
    "Delete Event You can define if the Salesforce Event will be deleted if the meeting is "
    "canceled from the Dashboard or deleted from your calendar provider."
)

#: The line that governs the two-step reschedule request.
REQUEST_QUOTE = (
    "Request to reschedule a booking. The booking will be cancelled and the attendee will "
    "receive an email with a link to reschedule."
)

#: The line that governs the reschedule source vocabulary.
SOURCE_QUOTE = (
    "Whenever a meeting is reassigned, we will display who rescheduled it, to whom, when, and "
    "the rescheduling source (Calendar event, ChiliCal Home, or Reschedule Link)."
)

#: The line that governs the availability recomputation.
SLOTS_QUOTE = (
    'bookingUidToReschedule=abc123def456 - "will ensure that the original booking time appears '
    'within the returned available slots when rescheduling."'
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "expiry-boundary-is-the-start",
        "topic": "which instant 'a meeting has happened' refers to",
        "basis": EXPIRE_QUOTE,
        "value": {
            "boundary": "start_at",
            "comparison": "now >= start_at",
            "end_at_also_required": True,
            "applies_to": "the reschedule link only",
        },
        "why": (
            "The setting names the start of a meeting's life, not its end, and a two-hour "
            "workshop is not a link an attendee should be able to move while it is running. The "
            "research's stated motive - reporting and tracking interactions - is about what "
            "happened, which begins at the start. The end time is still read, because a "
            "booking whose start was rescheduled out of the past while the link was open is a "
            "case the start alone cannot answer."
        ),
        "change_it": "dsr.scheduling.timeutil.has_happened, which is the only caller.",
        "blast_radius": "Every Expire Reschedule Link refusal. With the setting off, nothing reads it.",
    },
    {
        "id": "expire-setting-governs-reschedule-link-only",
        "topic": "whether Expire Reschedule Link also closes the cancel link",
        "basis": (
            "The setting is named for the reschedule link and the research documents the two "
            "tags, CP.Meeting.RescheduleUrl and CP.Meeting.CancelUrl, without ever saying the "
            "setting applies to the second one."
        ),
        "value": {
            "reschedule_link": "gated on expire_reschedule_link and the start having passed",
            "cancel_link": "gated only on the booking still being live",
            "applies_to_host_actions": False,
        },
        "why": (
            "A setting documented for one link is not evidence about the other, and widening it "
            "would be inventing a requirement. A cancel link on a meeting that has already "
            "happened does nothing harmful: it cancels a booking nobody is going to attend, the "
            "history records it, and a rep reading the history learns the meeting was "
            "cancelled - which is true and useful. The same toggle gating both would mean a "
            "vendor who switched it on silently lost the ability to tidy up after the fact."
        ),
        "change_it": "dsr.scheduling.links.state_for, where the RESCHEDULE branch is the only one that reads the setting.",
        "blast_radius": "Cancel links on past meetings, for meeting types with the setting on.",
    },
    {
        "id": "expiry-does-not-apply-to-host-actions",
        "topic": "whether a host in the Meetings Activity panel is blocked by an expired link",
        "basis": (
            "The setting is about a link: 'the reschedule link should expire after a meeting has "
            "happened'. Step 1 of the flow also offers the host the panel, which is not a link."
        ),
        "value": {
            "guarded_sources": ["reschedule_link"],
            "unguarded_sources": ["chilical_home", "calendar_event"],
            "enforced_on": "the write paths only",
            "reported_on": "every path, so a caller can see it coming",
        },
        "why": (
            "Enforcing it on a host action would turn a setting about an emailed URL into a "
            "lock on the product. The plan endpoint reports the state either way, so a caller "
            "that wants to behave strictly can, and the host panel never has to."
        ),
        "change_it": "MeetingChangeEngine._link_guard, which returns None for a non-link source.",
        "blast_radius": "Any deployment relying on the setting to stop all post-hoc moves.",
    },
    {
        "id": "expire-reschedule-link-default",
        "topic": "what Expire Reschedule Link is when a meeting type does not say",
        "basis": (
            "The research describes the setting as a decision an administrator makes and never "
            "names a default."
        ),
        "value": {"default": False, "in": "dsr.scheduling.meeting_types.DEFAULT_EXPIRE_RESCHEDULE_LINK"},
        "why": (
            "Off is the setting that changes nothing about the researched flow, so a meeting type "
            "that never mentions it behaves the way the flow describes before the setting is "
            "thought about. On by default would close a link nobody had decided to close."
        ),
        "change_it": "DEFAULT_EXPIRE_RESCHEDULE_LINK in meeting_types.py.",
        "blast_radius": "Meeting types created without the field. None of the seeded ones.",
    },
    {
        "id": "delete-event-default",
        "topic": "what Delete Event is when a meeting type does not say",
        "basis": (
            "The research describes the toggle as defining whether the Salesforce Event is "
            "deleted, and does not name a default."
        ),
        "value": {
            "default": True,
            "in": "dsr.scheduling.meeting_types.DEFAULT_DELETE_EVENT",
            "when_off": "the CRM Event is updated to cancelled and kept",
        },
        "why": (
            "Deletion is the toggle's own named behaviour, and the automation line - "
            "'cancellation is reported to the CRM per the Delete Event setting' - reads as the "
            "deletion being the ordinary outcome with a setting for the exception. Keeping a "
            "dead Event on a rep's CRM calendar is the failure mode a scheduling integration "
            "usually gets reported for. The other reading is defensible - preserve the Event so "
            "the CRM is a record of what was agreed - and it is one constant away."
        ),
        "change_it": "DEFAULT_DELETE_EVENT in meeting_types.py.",
        "blast_radius": "Cancellations on meeting types created without the field.",
    },
    {
        "id": "delete-event-soft-deletes",
        "topic": "what 'the Salesforce Event will be deleted' means here",
        "basis": DELETE_EVENT_QUOTE,
        "value": {
            "operation": "status=deleted on the CRM Event row, in the same transaction",
            "not_a_store_delete": True,
            "why_not": (
                "AuditedWriter offers create and update and deliberately no delete, so a real "
                "delete would have to be a second transaction"
            ),
        },
        "why": (
            "The researched sentence says the Salesforce Event 'will be deleted', and the "
            "fact a CRM needs is that the event is gone from the calendar. Recording that as a "
            "lifecycle state on the row is the same fact, keeps the row readable for the audit "
            "log, and - the reason it is the only option - joins the change's own transaction. A "
            "separate delete would leave a CRM event on somebody's calendar whenever it failed, "
            "which is the half-propagation this workflow exists to prevent."
        ),
        "change_it": "propagation.propagate_crm_event, the delete branch.",
        "blast_radius": "Cancellations on meeting types with Delete Event on.",
    },
    {
        "id": "request-reschedule-also-pushes-booking-cancelled",
        "topic": "what a request to reschedule pushes downstream",
        "basis": REQUEST_QUOTE,
        "value": {
            "cancels_the_booking": True,
            "runs_the_full_cancel_path": True,
            "pushes": ["BOOKING_CANCELLED", "Meeting Update type=Deleted"],
            "creates": "a pending reschedule request with its own token",
            "fires": "eventCancelled",
        },
        "why": (
            "The sentence says the booking *will be cancelled*, and the researched consequence "
            "of a cancelled booking is a cancelled calendar event, the Delete Event decision and "
            "BOOKING_CANCELLED. The alternative - cancel quietly and only send the attendee a "
            "link - would leave a meeting on two calendars that nobody intends to attend, which "
            "is the bug this workflow exists to prevent. The request record carries the reason "
            "and the link that brings the booking back, so a downstream consumer that keys on "
            "BOOKING_CANCELLED can tell this one from a real cancellation by following "
            "reschedule_request_id."
        ),
        "change_it": "MeetingChangeEngine.request_reschedule, which calls _cancel_one with a cause.",
        "blast_radius": "Every request-reschedule. A deployment that wants the quiet behaviour changes one call.",
    },
    {
        "id": "request-token-outlives-the-meeting",
        "topic": "whether a reschedule request's own link is closed by Expire Reschedule Link",
        "basis": (
            "The extensibility note says Expire Reschedule Link 'lets a vendor force a fresh "
            "booking after the fact for reporting purposes and tracking interactions'. A gate "
            "that closed the only remaining path to that fresh booking would make the setting "
            "unusable for its own stated purpose."
        ),
        "value": {
            "gated_on": "the request's own status (pending)",
            "not_gated_on": "the meeting type's expire_reschedule_link setting",
            "token": "minted by request-reschedule, distinct from the booking's own link",
        },
        "why": (
            "The two artefacts answer different questions. The booking's reschedule link is the "
            "self-service door on an upcoming meeting, and closing it after the fact is the "
            "point of the setting. The request's link is the reply to an invitation the app "
            "itself sent, and it is the mechanism the research names for getting the attendee "
            "back. Closing it would leave request-reschedule with no completion path."
        ),
        "change_it": "MeetingChangeEngine.link_state, the request branch, which never reads the setting.",
        "blast_radius": "Completion of every requested reschedule.",
    },
    {
        "id": "request-completion-writes-a-rescheduled-row",
        "topic": "what Events History says about a completed two-step reschedule",
        "basis": (
            "The flow writes 'an Events History audit row' for every change, and the "
            "extensibility note says rescheduleReason is shipped 'so a third party can build "
            "reschedule churn alerting'. Churn is a count of moves, and a two-step move that "
            "wrote only a cancellation would be invisible to it."
        ),
        "value": {
            "change_type": "rescheduled",
            "reschedule_source": "reschedule_link",
            "reschedule_uid": "the booking the request cancelled",
            "links_back_via": "reschedule_request_id and rescheduled_from_uid",
            "pushes": ["BOOKING_RESCHEDULED"],
        },
        "why": (
            "A reschedule and a cancellation look identical to a webhook consumer if only the "
            "cancellation is published, and the two are opposites: one releases a meeting, the "
            "other moves it. Publishing BOOKING_RESCHEDULED on completion is what lets churn "
            "alerting count moves rather than releases, and rescheduleUid naming the original "
            "is the researched field for exactly that link."
        ),
        "change_it": "MeetingChangeEngine.complete_request, which builds a CHANGE_RESCHEDULED row.",
        "blast_radius": "Every completed reschedule request.",
    },
    {
        "id": "reschedule-id-is-a-per-chain-sequence",
        "topic": "what rescheduleId counts",
        "basis": '"rescheduleId": 200, "rescheduleUid": "previous-booking-unique-id"',
        "value": {
            "starts_at": 1,
            "scoped_to": "the reschedule chain (chain_root), not the booking",
            "derived_from": "the count of history rows on the chain",
            "counted_not_stored": True,
        },
        "why": (
            "The field is named an id and its example is a round number, which is a sequence "
            "value rather than a constant. Scoping it to the chain is what makes it useful: a "
            "booking moved three times carries 1, 2 and 3 and a consumer can order the moves "
            "from the number alone. Counting the history rows rather than keeping a counter "
            "field means the ids cannot drift from the rows they are supposed to number, "
            "including after a restore."
        ),
        "change_it": "MeetingChangeEngine._next_chain_reschedule_id.",
        "blast_radius": "The rescheduleId on every new booking and on every BOOKING_RESCHEDULED payload.",
    },
    {
        "id": "past-target-refused-except-the-original-slot",
        "topic": "whether a reschedule may target a time that has already passed",
        "basis": (
            "The research says nothing about a past target. It does say the original booking "
            "time reappears as available when rescheduling, which is the one past slot a "
            "late reschedule can legitimately want."
        ),
        "value": {
            "refused": "a past slot that is not the original",
            "allowed": "the original booking time, because bookingUidToReschedule released it",
            "horizon_days": 400,
        },
        "why": (
            "Booking a meeting in the past produces a calendar event in the past and reminders "
            "scheduled to fire before they were created, both of which are silent nonsense. "
            "The original slot is the exception because the parameter that makes it available "
            "exists for exactly the attendee who opens the link late, and refusing it would make "
            "the researched rule useless in the case it was written for."
        ),
        "change_it": "MeetingChangeEngine._resolve_target, the in_past branch.",
        "blast_radius": "Late reschedules. The bounded horizon stops the same check being used to book next year.",
    },
    {
        "id": "reschedule-source-defaults-to-the-host-panel",
        "topic": "which rescheduling source a request that names none is",
        "basis": SOURCE_QUOTE,
        "value": {
            "default": "chilical_home",
            "when_link_token_given": "reschedule_link",
            "explicit_wins": True,
        },
        "why": (
            "A request that names no source arrived through the product's own host-facing "
            "surface, which is the Meetings Activity panel, and chiliCal Home is the sourced "
            "name for it. A request carrying a link token arrived through a link. An explicit "
            "value wins over both, because a link token can travel in a forwarded mail while the "
            "host's own action is the fact worth recording."
        ),
        "change_it": "MeetingChangeEngine._reschedule_source.",
        "blast_radius": "The source on every reschedule that did not name one.",
    },
    {
        "id": "cancel-scope-names",
        "topic": "what the cancel scope values are called",
        "basis": (
            ":bookingUid can be ... of an usual booking, individual recurrence or recurring "
            "booking to cancel all recurrences."
        ),
        "value": {"values": ["this", "all"], "default": "this", "all_on_a_non_series": "cancels just that booking"},
        "why": (
            "The research names the two cases in prose and never names a parameter, so the "
            "parameter is this build's. 'this' and 'all' are the shortest pair that cannot be "
            "confused with each other by a reader who has not read the sentence. Defaulting to "
            "'this' is the narrow one, because 'all' on a series is the destructive reading and "
            "a caller who did not ask for it should not get it."
        ),
        "change_it": "dsr.scheduling.vocabulary.CANCEL_SCOPES and the default in MeetingChangeEngine.cancel.",
        "blast_radius": "Every cancel that does not name a scope.",
    },
    {
        "id": "series-membership-travels-with-a-reschedule",
        "topic": "whether a rescheduled booking keeps its recurring group",
        "basis": (
            "The research is silent. The cancel sentence is about a series, so a reschedule that "
            "dropped the series membership would quietly remove the occurrence from any later "
            "cancel-all."
        ),
        "value": {"inherits": ["recurring_group", "recurrence_index"], "chain_root": "inherited"},
        "why": (
            "A reschedule moves an occurrence; it does not turn it into a one-off. Dropping the "
            "group would make a later 'cancel all recurrences' miss it, which is the researched "
            "operation quietly becoming wrong as a side effect of a different one."
        ),
        "change_it": "The optional-field loop in MeetingChangeEngine._new_booking_payload.",
        "blast_radius": "Reschedules of recurring bookings only.",
    },
    {
        "id": "events-are-keyed-to-the-chain",
        "topic": "what identifies a meeting's calendar and CRM events across moves",
        "basis": (
            "data_flow: 'calendar event moved/cancelled, CRM Event update/delete'. Both are "
            "written as one event changing, which presupposes there is one event to change."
        ),
        "value": {
            "keyed_by": "chain_root",
            "booking_uid": "rewritten to the booking that is now current",
            "event_id": "carried forward onto the new booking, so a subscriber keeps it",
            "one_calendar_event_per_chain": True,
            "one_crm_event_per_chain": True,
        },
        "why": (
            "Keying on the booking's uid would give a meeting moved twice two calendar events and "
            "two Salesforce events, and the attendee would see it twice - the precise failure this "
            "workflow exists to prevent on the room side, reproduced on the two systems it "
            "propagates to. The chain is what stays constant while the booking does not, so it is "
            "what identifies the meeting. Rewriting `booking_uid` on the row is what lets a rep "
            "still find the event by looking up the meeting in front of them."
        ),
        "change_it": (
            "The chain_root branch in MeetingChangeEngine._calendar_event and _crm_event, and the "
            "key in propagation.move_calendar_event and propagate_crm_event."
        ),
        "blast_radius": "Every event write. A booking that has never been moved is unaffected.",
    },
    {
        "id": "a-change-records-its-own-source-and-actor",
        "topic": "whose change a request claims to be, and who it is by",
        "basis": (
            "evidence: 'Whenever a meeting is reassigned, we will display who rescheduled it, to "
            "whom, when, and the rescheduling source (Calendar event, ChiliCal Home, or Reschedule "
            "Link).' The research names the four fields; it does not say how they are decided."
        ),
        "value": {
            "link_token_implies_attendee": True,
            "explicit_source_wins": True,
            "default_without_either": "chilical_home",
            "actor_email_falls_back_to": ["the attendee on the booking", "the host on the booking"],
            "actor_email_required": True,
        },
        "why": (
            "A link token can travel in a forwarded mail, so if a payload could overrule the "
            "provenance then the provenance would be worth nothing in the row that exists to record "
            "it - which is why a change arriving through a link is the attendee's whatever the "
            "payload says. An explicit source is the other way round: the panel is a signed-in "
            "surface and the caller's claim there is the only information there is. The email is "
            "required because `cancelledByEmail` is the researched field for it; a row with no who "
            "is a row nobody can follow up."
        ),
        "change_it": "MeetingChangeEngine._actor and _via_source.",
        "blast_radius": "The who and the source on every change row.",
    },
    {
        "id": "workflow-triggers-record-notifications-not-deliveries",
        "topic": "how far a rescheduleEvent or eventCancelled trigger is taken",
        "basis": (
            "automations: 'rescheduleEvent / eventCancelled workflow triggers fire re-sends and "
            "notifications; reminder state is recomputed (reminders were relative to the old "
            "time); cancellation is reported to the CRM per the Delete Event setting.' The "
            "research says a trigger fires a re-send and notifications; it does not say who "
            "delivers them, or how."
        ),
        "value": {
            "record_not_send": "one notification row per channel per recipient, status=delivered",
            "channels": ["email", "slack"],
            "recipients": ["the attendee", "the host"],
            "reminders_recomputed": True,
            "crm_reported_per_delete_event": True,
        },
        "why": (
            "The research names the channels ('notification channels (email/Slack)') and the "
            "effect ('fire re-sends and notifications') but documents no transport for either, "
            "and the product has neither an SMTP client nor a Slack token. Recording what was "
            "sent, to whom, through which channel and under which trigger is the part that is "
            "researched, and it is the part a rep needs to answer 'did they get the new time?'. "
            "The status is stored as a field rather than assumed, so the day a real transport "
            "lands the demo shows delivered-versus-failed instead of the history having claimed "
            "success all along. A real sender is a change to one function."
        ),
        "change_it": "propagation.record_notifications, which is the only place a notice is written.",
        "blast_radius": "Every notice this workflow sends. Nothing else reads the status.",
    },
    {
        "id": "one-notice-per-channel-per-recipient",
        "topic": "how many notices a trigger sends",
        "basis": (
            "automations: the triggers 'fire re-sends and notifications'. features_tools names "
            "'reschedule/cancel templates in Cal booking emails'. The research does not say "
            "whether the host is notified as well as the attendee."
        ),
        "value": {
            "per_change": "one notice per configured channel per recipient",
            "recipients": ["attendee", "host"],
            "recipient_role_field": "recipient_role",
            "default_channels": ["email", "slack"],
            "on_a_cancellation": "the same two, under the eventCancelled trigger",
            "skips_a_recipient_with_no_address": True,
        },
        "why": (
            "The host is the person who has to act on the new time, and the researched flow puts "
            "them in it - the Meetings Activity panel is where the change is made from - so a "
            "notification that only reached the attendee would leave the host with a meeting they "
            "believe moved and an attendee expecting one. Sending per channel rather than once "
            "follows the research's own enumeration of the channels. A recipient with no address "
            "is skipped rather than sent a blank one, because a notice addressed to nobody is "
            "indistinguishable from a failure in a log."
        ),
        "change_it": "propagation.record_notifications, and the channels on the Meeting Type.",
        "blast_radius": "The notification rows, and how many a reviewer sees per change.",
    },
    {
        "id": "no-email-template-rendering",
        "topic": "whether the researched email templates are rendered",
        "basis": (
            "features_tools names 'reschedule/cancel templates in Cal booking emails' and a "
            "'rescheduled template in Cal workflows', and data_sources names 'notification "
            "channels (email/Slack)'. The research names the templates; it does not publish "
            "their bodies."
        ),
        "value": {
            "rendered": False,
            "template_named_on_the_notice": True,
            "where_the_template_name_comes_from": "the researched template vocabulary",
        },
        "why": (
            "A template body is content, and the research published no content - inventing an "
            "email that goes to a customer on the strength of a template's name is writing the "
            "vendor's copy without their words. The template *name* is recorded on every notice, "
            "so a deployment that ships the real bodies has a row to join against and no field to "
            "add. The invite body is the one piece of copy this build does render, because the "
            "research documents the two tags that go in it and the surrounding text is the "
            "product's to write."
        ),
        "change_it": "propagation.record_notifications writes the template name; nothing renders it.",
        "blast_radius": "The notice rows carry a name and no body. A real sender is new code.",
    },
    {
        "id": "no-no-show-webhook",
        "topic": "why BOOKING_NO_SHOW_UPDATED is published but never pushed",
        "basis": (
            "BOOKING_NO_SHOW_UPDATED is listed among the webhooks this section pushes, and no "
            "step of the researched flow marks a no-show."
        ),
        "value": {
            "emitted": False,
            "published_at": "/api/wf-064/vocabulary under webhooks_not_emitted",
            "inference": "would require a no-show workflow the research does not describe",
        },
        "why": (
            "The list is of webhooks relevant to the section, not of webhooks this workflow "
            "produces; rescheduling and cancelling a meeting are the two operations researched, "
            "and neither marks anyone absent. Emitting it from a reschedule would tell every "
            "downstream system the attendee failed to show up to a meeting that has not "
            "happened. Publishing the decision is what makes the omission arguable rather than "
            "invisible."
        ),
        "change_it": "EMITTED_WEBHOOKS in vocabulary.py, which is where a new one would go.",
        "blast_radius": "Nothing. This is the record of a decision not to build something.",
    },
    {
        "id": "no-outbound-calendar-or-crm",
        "topic": "whether this feature calls a real calendar provider or CRM",
        "basis": (
            "The research documents Cal's and Chili Piper's request and response shapes. It "
            "documents no endpoint this product can reach, no credentials, and the product has "
            "no calendar or CRM client."
        ),
        "value": {
            "calls_outbound": False,
            "calendar_rows": "meeting_calendar_event, seeded and readable over HTTP",
            "crm_rows": "meeting_crm_event, carrying sobject=Event",
            "webhook_status": "delivered, because there is no transport that could fail",
        },
        "why": (
            "Writing a fake HTTP client at a real vendor would be a claim the product cannot "
            "back. The propagation logic is the researched part and it is exercised against "
            "real rows through the same path a connector would use, so a real transport is a "
            "change to two classes in propagation.py rather than a rewrite. The delivered "
            "status is stored rather than assumed so the difference is visible the day a "
            "transport lands."
        ),
        "change_it": "The four functions in propagation.py that write, and nothing else.",
        "blast_radius": "Nothing today. It is the seam a real connector would use.",
    },
    {
        "id": "reminders-are-not-invented",
        "topic": "what a booking's reminders are when nobody supplies any",
        "basis": (
            "'Reminder state is recomputed (reminders were relative to the old time)'. The "
            "research describes recomputation here; defining which reminders exist is WF-011."
        ),
        "value": {
            "default": "no reminders",
            "supplied_by": "the caller, or a meeting type's reminder_offsets",
            "recomputed": "scheduled_for = new_start - offset_minutes, offset preserved",
        },
        "why": (
            "A default pair of reminders would be a product decision made by a workflow whose "
            "job is moving meetings, and it would appear in every seeded booking as if the "
            "research had asked for it. An empty list makes the recompute rule a no-op when "
            "there is nothing to recompute, which is the honest behaviour - and a team that "
            "wants reminders supplies them or configures them on the meeting type."
        ),
        "change_it": "MeetingChangeEngine._reminders_for, which is the only place the empty list is produced.",
        "blast_radius": "Only bookings created with neither reminders nor a reminder_offsets on their type.",
    },
    {
        "id": "link-tokens-are-minted-not-derived",
        "topic": "how a link token is produced",
        "basis": (
            "The research says the tags are injected into the Description and never says what "
            "the URL contains."
        ),
        "value": {"bytes": 18, "scheme": "url-safe random", "minted_when": "the booking is created", "travels_on_reschedule": False},
        "why": (
            "A token derived from the booking uid would let anybody who could guess or enumerate "
            "a uid move somebody else's meeting, and the research's step 1 opens this door to "
            "attendees with nothing else. Tokens are not carried onto the new booking on a "
            "reschedule, so the invite the attendee receives after a move points at the meeting "
            "that is actually happening rather than at the one that moved."
        ),
        "change_it": "MeetingChangeEngine's token_factory, which is injectable for that reason.",
        "blast_radius": "Nothing behavioural. It is why the factory is a constructor argument.",
    },
    {
        "id": "no-room-annotation",
        "topic": "whether the change is annotated on the room row",
        "basis": (
            "The flow says 'an Events History audit row written'. It does not say the row is "
            "copied onto the room, and this product's own research for other workflows is where "
            "that instruction appears when it is meant."
        ),
        "value": {"room_annotation": False, "history_rows": "room-scoped records", "room_filtered_routes": "present"},
        "why": (
            "A duplicate array on a room is a poor place for match detail and a capped one "
            "cannot answer a question about the eleventh change. The history rows are already "
            "room-scoped on the envelope, so a rep reads one room's changes from one room's "
            "page with no second copy to keep in step."
        ),
        "change_it": "There is nothing to remove: no room write exists outside the booking create.",
        "blast_radius": "Nothing. It is a record of a decision not to duplicate the history.",
    },
    {
        "id": "the-link-path-is-ours-not-the-public-rooms",
        "topic": "which path a meeting link is served on",
        "basis": (
            "The research names 'CP.Meeting.RescheduleUrl' and 'CP.Meeting.CancelUrl' as Chili "
            "Piper's own dynamic tags, injected into the invite's Description. It does not fix "
            "a path on this host. This repository already has one: '/r/<slug>' is the public-room "
            "share link, matched by wf-017-white-label as '^/r/([^/]+)/?$'."
        ),
        "value": {"link_path": "booking", "public_room_path_left_alone": True},
        "why": (
            "A meeting link under /r/ matches the public-room pattern, so an attendee following "
            "the invite lands on a page that looks for a room whose name is a token, finds "
            "nothing, and concludes the link is broken. The collision is invisible until a real "
            "attendee follows a real invite, which is exactly the kind of defect that ships. The "
            "path is a named constant with the reasoning attached, and is overridable per call, "
            "so changing it is one line rather than a search."
        ),
        "change_it": "Set LINK_PATH in dsr/scheduling/links.py, or pass path= per call.",
        "blast_radius": (
            "The URLs in invite bodies and in the two link fields. Nothing else: the tokens, the "
            "expiry, the write paths and the audit trail are all independent of the path."
        ),
    },
)


def by_id(inference_id: str) -> dict[str, Any] | None:
    for entry in INFERENCES:
        if entry["id"] == inference_id:
            return entry
    return None


def describe() -> dict[str, Any]:
    """The whole registry, beside the half of the workflow that is sourced."""
    from dsr.scheduling import propagation
    from dsr.scheduling.vocabulary import (
        BOOKING_STATUSES,
        CANCEL_SCOPES,
        CHANGE_TYPES,
        CHILIPIPER_WEBHOOKS,
        EMITTED_WEBHOOKS,
        LINK_TAGS,
        MEETING_TYPE_SETTINGS,
        NOTIFICATION_TEMPLATES,
        RESCHEDULE_SOURCES,
        WORKFLOW_TRIGGERS,
        published_vocabulary,
    )

    return {
        "count": len(INFERENCES),
        "sourced_quote": EXPIRE_QUOTE,
        "sourced": {
            "rescheduling_sources": [entry["source"] for entry in RESCHEDULE_SOURCES],
            "cancel_scopes": [entry["scope"] for entry in CANCEL_SCOPES],
            "webhooks": [entry["webhook"] for entry in EMITTED_WEBHOOKS],
            "chilipiper_webhooks": [entry["webhook"] for entry in CHILIPIPER_WEBHOOKS],
            "workflow_triggers": [entry["trigger"] for entry in WORKFLOW_TRIGGERS],
            "link_tags": [entry["tag"] for entry in LINK_TAGS],
            "meeting_type_settings": [entry["setting"] for entry in MEETING_TYPE_SETTINGS],
            "change_types": [entry["type"] for entry in CHANGE_TYPES],
            "booking_statuses": [entry["status"] for entry in BOOKING_STATUSES],
            "notification_templates": [entry["template"] for entry in NOTIFICATION_TEMPLATES],
            "triggers_fired": dict(propagation.TRIGGER_FOR_CHANGE),
        },
        "inferences": [dict(entry) for entry in INFERENCES],
        "vocabulary_count": len(
            [
                key
                for key in published_vocabulary()
                if isinstance(published_vocabulary()[key], list)
            ]
        ),
    }
