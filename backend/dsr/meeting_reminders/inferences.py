"""Every judgement call in WF-061, in one inspectable place.

The research for this workflow is specific in most places - five skip reasons,
three firing conditions, four offset units, a quoted response rule, a quoted
Cal enum - and open in others. The build brief asks for the distinction to be
kept rather than blurred, and this module is the "open" half, served at
``/api/wf-061/inferences`` so a reviewer can disagree with a *named* entry
instead of reverse-engineering it from a diff.

A judgement call left as a comment in a function body is one nobody re-reads,
and a wrong one becomes product behaviour without anyone noticing. Each entry
here is named, states what the research does and does not say, is bounded by a
``value`` saying what this build chose, and carries a ``change_it`` so it can be
changed without editing a function body.

The entries, in rough order of how much damage a reviewer disagreeing would do:

* ``skip-reason-precedence`` - which reason is recorded when more than one gate
  fails. Every skip in the product depends on this and nothing in the research
  decides it.
* ``response-gate-is-condition-not-restriction`` - the mapping from the research's
  own wording ("condition", "restriction") onto the skip-reason vocabulary.
* ``after-meeting-anchors-on-the-end`` - where a follow-up is measured from.
* ``timezone-is-a-fixed-offset`` - why the product carries minutes and not a
  named zone.
* ``no-outbound-send`` - whether this feature opens a socket.
* ``translation-is-not-claimed`` - the two Cal switches, and the catalogue that
  is not there.
"""

from __future__ import annotations

from typing import Any

#: The quote that decides the conditional pre-meeting reminder's gate. Parenthetical
#: included, because it is the parenthetical that makes Declined a response.
NO_RESPONSE_QUOTE = (
    "**Before the Meeting - if the Primary Guest did not respond:** the Reminder will be sent "
    "before the meeting at a pre-defined time, however, only if the Primary Guest has not "
    "responded to the invite (Accepted or Declined)."
)

#: The quote that names the product's own label for the absence of a restriction,
#: which is why a failing step-6 gate is "Violated restriction" and not
#: "Reminder condition not satisfied".
RESTRICTION_QUOTE = (
    "You can see two options: **No Restriction** … **Send only if meeting starts on:** When this "
    "option is selected, you will see the weekdays and checkboxes"
)

#: The research names the five reasons in this order and never says the order
#: means anything. It is the only ordering the text offers.
SKIP_REASONS_QUOTE = (
    "statuses `Scheduled` / `Sent` / `Skipped` with documented skip reasons (`Reminder schedule "
    "time in the past`, `Reminder condition not satisfied`, `Recipient not found`, `Phone not "
    "found`, `Violated restriction`)"
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "skip-reason-precedence",
        "topic": "which skip reason is recorded when more than one gate fails",
        "basis": (
            "The research enumerates five skip reasons and never says which applies when several "
            "would. " + SKIP_REASONS_QUOTE
        ),
        "value": {
            "order": [
                "schedule_in_past (at planning)",
                "condition_not_satisfied (the response gate)",
                "violated_restriction (the advanced gates)",
                "recipient_not_found / phone_not_found (the recipients)",
            ],
            "first_failure_wins": True,
            "every_gate_is_evaluated": "recorded in checks[] even when an earlier gate already failed",
        },
        "why": (
            "A booking can fail several gates at once - a guest who declined an invite that was "
            "booked two days before a Thursday meeting on a Wednesday-only reminder. Recording the "
            "last failure would make the reason depend on evaluation order, and recording all of "
            "them would put a value in a field the research types as a single reason. So the first "
            "gate that refuses is the one recorded, in the order the flow applies them: the "
            "response gate is part of the firing condition, the advanced gates are the "
            "administrator's restrictions, and the recipients are the last thing worth learning. "
            "Every gate's verdict is still recorded in `checks`, so a reviewer can see the rest "
            "rather than infer that they passed."
        ),
        "change_it": "The order of the four blocks in conditions.decide, and conditions.plan.",
        "blast_radius": "The `reason` on every skipped delivery, and the summary's by-reason counts.",
    },
    {
        "id": "response-gate-is-a-condition-not-a-restriction",
        "topic": "which skip reason a failed response gate produces",
        "basis": (
            "The research calls the response test part of the firing *condition* in step 4, and "
            "calls the two step-6 gates a *restriction* in step 6, where the alternative is "
            "labelled '**No Restriction**'. The skip-reason vocabulary has exactly one "
            "'condition' reason and one 'restriction' reason."
        ),
        "value": {
            "before_if_no_response_failed": "condition_not_satisfied",
            "weekday_failed": "violated_restriction",
            "lead_time_failed": "violated_restriction",
        },
        "why": (
            "The two step-6 gates share one reason, which is the product's own word for them. The "
            "mapping is not a free choice: the research's step headings and its skip-reason names "
            "line up, and reading the response gate as a restriction would leave "
            "'Reminder condition not satisfied' reachable by nothing at all."
        ),
        "change_it": "vocab.RULE_SKIP_REASON, and the reason passed in conditions.decide.",
        "blast_radius": "Which skip reason a response-gated reminder records.",
    },
    {
        "id": "declined-counts-as-responding",
        "topic": "whether a guest who declines the invite gets the 'did not respond' reminder",
        "basis": NO_RESPONSE_QUOTE,
        "value": {
            "accepted": "responded",
            "declined": "responded",
            "needsAction": "not responded",
            "absent": "not responded",
        },
        "why": (
            "The parenthetical '(Accepted or Declined)' is the whole sentence. A guest who has "
            "declined knows they declined, and a 'you have not responded' email to someone who "
            "said no is the single worst thing this feature could do. The comparison is also "
            "case-insensitive, because the field arrives from a calendar provider and the research "
            "spells it `needsAction` while a JSON payload may carry anything."
        ),
        "change_it": "conditions.RESPONSED_STATUSES via conditions.has_not_responded.",
        "blast_radius": "Every reminder using the conditional pre-meeting condition.",
    },
    {
        "id": "weeks-offset-converts-to-days",
        "topic": "what a weeks offset becomes in a Cal.com workflow",
        "basis": (
            "Step 4 offers a 'minutes/hours/days/weeks offset'. Cal's "
            "`offset{value, unit: hour|minute|day}` has no week, so the two citations do not "
            "cover the same set and the research does not say how to reconcile them."
        ),
        "value": {"cal_unit": "day", "multiplier": 7, "exact": True, "refused": False},
        "why": (
            "One week is exactly seven days, so the conversion is arithmetic rather than an "
            "approximation - and a refusal would mean a 'one week before' reminder, the most "
            "ordinary offset in the product, could not be moved to Cal at all. It is still a "
            "judgement: a deployment that would rather see the offset refused than silently "
            "reinterpreted has a one-line change here."
        ),
        "change_it": "cal.cal_offset, and CAL_UNIT_FROM in vocabulary.py.",
        "blast_radius": "The Cal projection of any reminder whose offset is in weeks.",
    },
    {
        "id": "after-meeting-anchors-on-the-end",
        "topic": "what an 'After the Meeting' reminder is measured from",
        "basis": (
            "user_flow step 4: '**After the Meeting:** the reminder will be sent after the meeting "
            "at a pre-defined time. This is a useful option for follow-up emails, for example.' The "
            "data_flow lists the booking's fields as 'start time, duration, ...' - the duration is "
            "there for something, and this is the only researched rule that needs it."
        ),
        "value": {"anchor": "start + durationMinutes", "fallback": "0 when the booking has no duration"},
        "why": (
            "A follow-up follows the meeting. Anchoring on the start would put a 'one hour after' "
            "reminder into the middle of a forty-five minute call, and the research calls the rule "
            "'after the meeting', not 'after the meeting starts'. The duration being in the "
            "enumerated booking fields is corroboration rather than proof, but there is no other "
            "consumer of it in this workflow."
        ),
        "change_it": "conditions.fire_at, and the anchor table vocab.CONDITION_ANCHOR.",
        "blast_radius": "Every after_meeting reminder's fire time, and its Cal projection.",
    },
    {
        "id": "schedule-in-the-past-is-a-planning-question",
        "topic": "when 'Reminder schedule time in the past' is decided",
        "basis": (
            "The research names the reason and nothing else about it. It does not say whether it "
            "is checked when a reminder is attached to a meeting or when a scheduler reaches it."
        ),
        "value": {
            "checked_at": "planning, when the reminder is attached to a booking",
            "at_run_time": "the fire time is always in the past, so it is not re-checked",
        },
        "why": (
            "Testing it at run time would skip every reminder that ever fired, because a "
            "scheduler that runs a reminder has necessarily reached its fire time. Testing it at "
            "planning time gives the reason its meaning: a 'two hours before' reminder attached to "
            "a meeting starting in thirty minutes has no moment at which it could have been sent. "
            "This is also the only reading under which the reason is ever recorded at all, which is "
            "what makes it worth publishing."
        ),
        "change_it": "conditions.plan, which is separate from conditions.decide for this reason.",
        "blast_radius": "Whether a scheduled delivery is created at all.",
    },
    {
        "id": "timezone-is-a-fixed-offset",
        "topic": "how a booking's timezone is represented",
        "basis": (
            "The research names a `{TIMEZONE}` token and a weekday gate, and the data_sources name "
            "a calendar invite. It does not name a timezone source, a database, or a resolution "
            "rule."
        ),
        "value": {
            "carried_as": "timezoneOffsetMinutes on the booking",
            "named_zone_is": "a label, rendered in messages and shown in the UI",
            "default": "UTC",
            "resolved_with": "datetime.timezone with a whole-minute offset",
        },
        "why": (
            "The weekday gate asks what day the meeting starts *for the guest*, so the answer has "
            "to be the guest's local day. Python's `zoneinfo` needs a timezone database the "
            "platform does not ship, and a host without one resolves a named zone to UTC - which "
            "would fire a Monday-only reminder on a Sunday for every guest east of UTC, silently. "
            "An explicit offset is exact, portable, and needs no database; a deployment that wants "
            "IANA names writes them into the offset at booking time. The weekday is also computed "
            "Monday-zero to match the researched row of weekday checkboxes, and a silent "
            "off-by-one there would fire every weekend reminder on a weekday."
        ),
        "change_it": "conditions.local_timezone and conditions.MAX_TZ_OFFSET_MINUTES.",
        "blast_radius": "The weekday gate, and every local time rendered into a message.",
    },
    {
        "id": "sms-requires-a-connected-twilio-account",
        "topic": "what stops an SMS reminder being switched on",
        "basis": (
            "For SMS reminders … A Chili Piper Admin must connect Twilio to your company's Command "
            "Center Integrations page."
        ),
        "value": {
            "at_configuration_time": "an SMS reminder is refused without a connected Twilio account",
            "at_delivery_time": "a connected account is read as the sending number",
            "reply_forwarding": "additionally requires own_account, per the research's own warning",
        },
        "why": (
            "The sentence is a statement about whether the feature can be turned on, not about what "
            "happens when it fails, so it is enforced when the reminder is saved. The research then "
            "flags reply forwarding separately with a warning glyph - 'Forwarding SMS replies "
            "requires your company's own Twilio account' - so there are two requirements, and a "
            "connection record carries both `connected` and `own_account` rather than one boolean "
            "that would conflate them."
        ),
        "change_it": "engine.validate_reminder, and the org record's two flags.",
        "blast_radius": "Which SMS reminders can be created, and which can forward replies.",
    },
    {
        "id": "phone-required-when-sms-is-enabled",
        "topic": "what 'attendee.phoneNumber becomes required' means for a booking",
        "basis": (
            "Cal booking field `attendee.phoneNumber` - \"becomes required when SMS reminders are "
            "enabled for the event type\"."
        ),
        "value": {
            "meeting_type_reports": "phone_required, true when any attached reminder is an enabled SMS one",
            "booking_time": "a booking without a guest phone is refused against such a meeting type",
            "legacy_bookings": "an SMS delivery for a booking with no phone is skipped: Phone not found",
        },
        "why": (
            "The researched statement is about the booking *form*, so the enforcement belongs where "
            "a booking is created rather than where a reminder fires. But a booking that predates "
            "the reminder being attached has no phone and cannot be refused retroactively, so that "
            "case gets the researched skip reason instead - which is one of the two recipient "
            "reasons and is exactly what it is for."
        ),
        "change_it": "engine.booking_is_valid_for, and the phone_required read on a meeting type.",
        "blast_radius": "Booking creation against a meeting type with an SMS reminder.",
    },
    {
        "id": "no-outbound-send",
        "topic": "whether this feature opens a socket",
        "basis": (
            "The research documents Chili Piper's configuration surface, a Twilio setup article, and "
            "Cal's `POST /v2/workflows` with its required version header. It documents no endpoint "
            "this product can reach, no credential, and the product has no messaging client."
        ),
        "value": {
            "calls_outbound": False,
            "email_and_sms_recorded": "a delivery row carrying the composed message and recipients",
            "cal_workflow_recorded": "the exact DTO a POST would carry, plus the version header",
        },
        "why": (
            "Writing a fake HTTP client to a real vendor would be a claim the product cannot back. "
            "The decision logic is the researched part and it is exercised against real rows, so "
            "swapping in a transport later is a change to one class rather than a rewrite. The "
            "projection into Cal's DTO is deliberately *complete* - the shape, the enums, the header "
            "- so that what a real transport would send is reviewable without a network."
        ),
        "change_it": "The transport seam in engine.py. Nothing else in the package knows.",
        "blast_radius": "Nothing today. It is the seam a real transport would use.",
    },
    {
        "id": "translation-is-not-claimed",
        "topic": "what autoTranslateEnabled and sourceLocale do here",
        "basis": (
            "features_tools names `autoTranslateEnabled` + `sourceLocale`. The research names no "
            "translation catalogue, no provider, and no supported language list."
        ),
        "value": {
            "switches_are": "real, validated, and recorded on the delivery",
            "translation_performed": False,
            "a_reminder_renders": "in its source locale",
            "delivered_locale_is_recorded": True,
        },
        "why": (
            "The two settings are part of the researched vocabulary, so dropping them would lose a "
            "documented field. Inventing a translation catalogue would be claiming a capability "
            "nothing in the research supports, and a wrong translation sent to a customer is worse "
            "than an honest one in the author's language. So the switches exist, validate, and are "
            "recorded - and the delivery says which locale it went out in, which is the part a "
            "reviewer and a support conversation both need."
        ),
        "change_it": "tags.render_translation, and the locale recorded on a delivery.",
        "blast_radius": "The locale on any delivery with autoTranslateEnabled set.",
    },
    {
        "id": "detach-is-not-delete",
        "topic": "'Remove from Meeting Type' versus 'Delete'",
        "basis": (
            "automations: 'reminders are reusable assets attachable to many Meeting Types "
            "(`Remove from Meeting Type` vs `Delete`)'."
        ),
        "value": {
            "remove_from_meeting_type": "detaches the attachment; the reminder asset and every other attachment survive",
            "delete": "soft-deletes the reminder asset; its attachments and delivery history remain readable",
        },
        "why": (
            "The research puts the two in the same parenthetical because they are the two things an "
            "administrator can do and they are not the same action. A reminder attached to six "
            "meeting types must survive being removed from the seventh, and a deleted reminder's "
            "deliveries must stay auditable, which is why the delete is a soft delete and the "
            "history is never rewritten."
        ),
        "change_it": "engine.detach_reminder and engine.delete_reminder.",
        "blast_radius": "Every attachment and every delivery row.",
    },
    {
        "id": "unresolved-tags-are-reported-not-blanked",
        "topic": "what happens to a dynamic tag the booking cannot fill in",
        "basis": (
            "user_flow step 5 names three tags with 'such as', so the set is open. The research does "
            "not say what an unknown or unfillable tag renders as."
        ),
        "value": {
            "unknown_tag": "left in the message, and reported in `missing`",
            "unfillable_tag": "left in the message, and reported in `missing`",
            "never": "replaced with an empty string",
        },
        "why": (
            "A body that reads 'Hi , see you soon' goes out wrong and nobody notices; a preview "
            "that still shows `CP.Guest.FirstName` is a problem someone can see. Both the unknown "
            "and the unfillable case are reported, because an administrator who typed "
            "`{CUSTOMER.TIER}` and an administrator whose booking has no phone are both looking for "
            "an answer in the same place."
        ),
        "change_it": "tags.render, and the CP_TAGS / CAL_TOKENS registries.",
        "blast_radius": "Every composed message, and the preview pane.",
    },
    {
        "id": "sms-goes-to-the-primary-guest",
        "topic": "whether an SMS reminder can go to all guests",
        "basis": (
            "user_flow step 3 offers 'Send Email To (Primary Guest or All Guests)' and, separately, "
            "'for SMS Send SMS From (any number or a local area number)'. There is no 'Send SMS To' "
            "at all. The guest form has a single Phone field."
        ),
        "value": {"sms_recipients": "the primary guest only", "all_guests_applies_to": "email only"},
        "why": (
            "The email recipient choice is named and the SMS one is not, and adding an "
            "'all guests' SMS option would be inventing a control the research does not describe. "
            "The Skip reasons carry both 'Recipient not found' and 'Phone not found', so a missing "
            "phone on the primary guest is a distinct, documented outcome rather than an empty list."
        ),
        "change_it": "conditions.resolve_recipients, the SMS branch.",
        "blast_radius": "Every SMS delivery's recipient list.",
    },
    {
        "id": "weekday-gate-evaluates-the-meeting-not-the-send",
        "topic": "which timestamp the weekday restriction is tested against",
        "basis": (
            "evidence: '**Send only if meeting starts on:** When this option is selected, you will "
            "see the weekdays and checkboxes'. The subject of the sentence is the meeting's start."
        ),
        "value": {
            "tested_against": "the meeting start, in the booking's local time",
            "not": "the moment the reminder fires",
        },
        "why": (
            "'meeting starts on' is unambiguous about whose day it is. Testing the send time would "
            "be a different rule - a reminder sent on a Tuesday for a Thursday meeting would pass a "
            "Tuesday-only gate, which is the opposite of what an administrator ticking 'Thursday' "
            "asked for. The timezone is the booking's, for the reason in `timezone-is-a-fixed-offset`."
        ),
        "change_it": "conditions.weekday_allowed.",
        "blast_radius": "Every reminder with a weekday restriction.",
    },
    {
        "id": "lead-time-gate-fails-closed",
        "topic": "what a lead-time gate does when the booking has no booking date",
        "basis": (
            "evidence: 'While enabled, this setting determines whether the reminder should be sent "
            "only within a specific timeframe.' The word is *only*."
        ),
        "value": {"no_bookedAt": "the gate fails", "the_reminder_is": "skipped: Violated restriction"},
        "why": (
            "A gate that passes when it cannot be checked is not a gate. The option says send *only* "
            "if, and a booking with no booking date cannot be shown to satisfy it, so the honest "
            "answer is to decline rather than to guess. Failing open here would send reminders to "
            "exactly the last-minute bookings an administrator was trying to suppress."
        ),
        "change_it": "conditions.booked_far_enough.",
        "blast_radius": "Reminders with a lead-time restriction applied to bookings lacking bookedAt.",
    },
)


def by_id(inference_id: str) -> dict[str, Any] | None:
    for entry in INFERENCES:
        if entry["id"] == inference_id:
            return entry
    return None


def describe() -> dict[str, Any]:
    """The whole registry, beside the half of the workflow that is sourced."""
    from dsr.meeting_reminders import conditions as cond
    from dsr.meeting_reminders import tags
    from dsr.meeting_reminders import vocabulary as vocab

    return {
        "count": len(INFERENCES),
        "sourced_quote": NO_RESPONSE_QUOTE,
        "sourced": {
            "channels": list(vocab.CHANNELS),
            "conditions": list(vocab.CONDITIONS),
            "offset_units": list(vocab.UNITS),
            "statuses": list(vocab.STATUSES),
            "skip_reasons": dict(vocab.SKIP_REASONS),
            "weekdays": list(vocab.WEEKDAYS),
            "match_modes": list(vocab.MATCH_MODES),
            "responded_statuses": sorted(vocab.RESPONSED_STATUSES),
            "cal_triggers": list(vocab.CAL_TRIGGERS),
            "cal_offset_units": list(vocab.CAL_OFFSET_UNITS),
            "cal_step_actions": list(vocab.CAL_STEP_ACTIONS),
            "cal_step_templates": list(vocab.CAL_STEP_TEMPLATES),
            "restriction_quote": RESTRICTION_QUOTE,
            "skip_reasons_quote": SKIP_REASONS_QUOTE,
        },
        "inferences": [dict(entry) for entry in INFERENCES],
        "rule_kinds": list(cond.RULE_KINDS),
        "condition_anchors": dict(vocab.CONDITION_ANCHOR),
        "tag_counts": {
            "chili_piper": len(tags.CP_TAGS),
            "cal": len(tags.CAL_TOKENS),
            "named_in_research": list(tags.NAMED_CP_TAGS),
        },
    }
