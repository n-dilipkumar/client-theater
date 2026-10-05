"""The researched vocabulary for WF-111, and nothing else.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-111.md`` and section 8 of
``docs/research/raw/automation-engagement.md``, from the three sources the research cites.
Every constant here is either quoted from one of those sources or is an explicitly *unsourced*
choice, and the two are kept apart by the ``sourced`` flag rather than left to the reader's
judgement.

What the research pins down
---------------------------

* The four setup steps, in order: "1. **Create an inbox** in your workspace settings and give
  it a name. 2. **Style the inbox** in Design Studio. 3. **Publish the inbox**. 4. **Compose
  and send messages** to the inbox from an automation, broadcast, or transactional message."
* The one-per-workspace constraint, twice: "You can have **one notification inbox per
  workspace**" and "The visual notification inbox only serves one mobile app per workspace."
  This is a hard constraint on creation, not a default.
* The two states one inbox has. The user flow says "click **Publish** (or **Set to draft** to
  unpublish)", so publish and draft are two states of *one* inbox and not two inboxes.
* The three failure modes the inbox copy exists for, quoted: "This helps people find your
  message if they dismissed the push, don't have a device that can receive the push, or the
  push delivery failed."
* The unconditional copy. The data flow says "a duplicate `inbox` message is written to the
  workspace's notification inbox", so a failed push does not suppress the copy.
* The rate limits with their numbers: the App API is "10 requests per second" and answers
  ``429`` with ``Retry-After``; the transactional endpoint is "3,000 requests per 3 seconds.
  Transactional sends go through the same ingress as the Track and Pipelines APIs and share
  their soft limit."
* The JSON contract of the build-your-own-inbox path: messages are "Sent and delivered as JSON
  payloads", and a caller "listen[s] for messages with the SDK's `inbox()` method".
* Where the metrics land: the data flow ends with "open/click metrics recorded against the
  person", so an engagement is recorded against a person and not against a message.
* The push providers: "push provider (APNs/FCM)".

What the research leaves open
----------------------------

**The design studio.** Step 2 of the user flow describes a hosted visual editor - icon,
appearance, unread indicator, position, dark-mode styles - and a hosted SDK that renders the
inbox on iOS, Android and the web. None of that is buildable from this specification and this
build does not ship a fake one. What it stores instead is recorded in
:data:`PRESENTATION_FIELDS`, and the reasoning is the inference
``DERIVED_NO_HOSTED_DESIGN_STUDIO``.

**Device token shape and delivery latency.** The research names "device token lookup" as a
data source and "delivery failed" as a failure mode but publishes no token format and no
timeout. :data:`PUSH_OUTCOMES` and :data:`PUSH_DELIVERY_MODES` are this build's, and both say
so.

**What a *dismissed* push means to the sender.** The research names three reasons the inbox
copy helps and does not say the sender can observe any of them, so
:data:`FALLBACK_REASONS` records all three and
:data:`~dsr.automation_engagement.inferences.DERIVED_DISMISSAL_IS_NOT_OBSERVED` records that
this build does not pretend to observe one.

A rule with a published vocabulary is a rule a client can send, and a client that sends the
wrong one gets a message naming the published set. That is the whole reason these tuples are
exported rather than inlined at their call sites.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections this workflow owns
# --------------------------------------------------------------------------- #

#: One row per workspace's single notification inbox. There is at most one per room, and
#: :func:`~dsr.automation_engagement.rules.assert_first_inbox` is what holds it to one.
INBOX_COLLECTION = "wf111_notification_inbox"

#: One row per reminder send. Carries the push attempt's outcome and the inbox copy's id.
REMINDER_COLLECTION = "wf111_reminder"

#: One row per inbox message. A duplicate of a push is written here unconditionally when the
#: toggle is on; an in-app message composed in the builder is written here too.
INBOX_MESSAGE_COLLECTION = "wf111_inbox_message"

#: One row per open or click. Recorded against the **person**, per the data flow's last clause.
ENGAGEMENT_COLLECTION = "wf111_person_engagement"

# --------------------------------------------------------------------------- #
# The inbox: one per workspace, published or draft
# --------------------------------------------------------------------------- #

#: The two states one inbox has. The user flow's "click **Publish** (or **Set to draft** to
#: unpublish)" is one inbox moving between two states, not two kinds of inbox.
INBOX_STATES: tuple[str, ...] = ("draft", "published")

#: The state an inbox must be in to receive or render messages. A draft inbox "must not
#: receive or render messages" - see :func:`~dsr.automation_engagement.rules.assert_published`
#: and the note on :data:`STATE_PUBLISHED`.
STATE_DRAFT = "draft"
STATE_PUBLISHED = "published"

#: The hard constraint, quoted. "You can have **one notification inbox per workspace**."
INBOXES_PER_WORKSPACE = 1

#: The same constraint stated a second time, about the mobile app rather than the inbox. Kept
#: as its own constant because the research words it separately: "The visual notification
#: inbox only serves one mobile app per workspace." This build has no mobile app, so it
#: records the constraint and serves no app selector.
APPS_PER_WORKSPACE = 1

#: The workspace default app the visual inbox serves, as the research names it.
DEFAULT_APP = "default app"

# --------------------------------------------------------------------------- #
# What this build stores in place of the hosted design studio
# --------------------------------------------------------------------------- #

#: The five styling knobs the research's Design Studio step names, and where each one is
#: stored here. **None of these is rendered by a hosted editor.** The research describes a
#: visual editor this specification does not define and this build does not fake; what it
#: keeps is the named configuration as data, on the inbox record, in ``records.data``.
#:
#: See :data:`~dsr.automation_engagement.inferences.DERIVED_NO_HOSTED_DESIGN_STUDIO`.
PRESENTATION_FIELDS: tuple[dict[str, str], ...] = (
    {
        "field": "icon",
        "studio_control": "set the icon",
        "sourced": "Style the inbox in Design Studio; set the icon, appearance, unread indicator, and position.",
    },
    {
        "field": "appearance",
        "studio_control": "set the appearance",
        "sourced": "Style the inbox in Design Studio; set the icon, appearance, unread indicator, and position.",
    },
    {
        "field": "unread_indicator",
        "studio_control": "set the unread indicator",
        "sourced": "Style the inbox in Design Studio; set the icon, appearance, unread indicator, and position.",
    },
    {
        "field": "position",
        "studio_control": "set the position",
        "sourced": "Style the inbox in Design Studio; set the icon, appearance, unread indicator, and position.",
    },
    {
        "field": "dark_mode_styles",
        "studio_control": "set dark-mode styles",
        "sourced": "set dark-mode styles; Save Updates",
    },
)

#: The product is light-theme only and locked to it, so the fifth knob above has nowhere to
#: render. Recorded rather than dropped silently: a caller who sends
#: ``dark_mode_styles`` has it stored on the record and is told the product will not render it.
DARK_MODE_RENDERED = False

#: The Design Studio route the research's user flow names. It is published so a page can name
#: the step that this build does not implement, rather than inventing an editor it does not
#: have.
DESIGN_STUDIO_ROUTE = "Patterns -> Inbox"

# --------------------------------------------------------------------------- #
# Channels
# --------------------------------------------------------------------------- #

#: The two channels this workflow writes. The push channel is what the seller composes; the
#: inbox channel is the fallback the copy toggle creates. "Optionally also add an inbox message
#: in the workflow builder" is the third path, and it writes the same inbox channel.
CHANNELS: tuple[str, ...] = ("push", "inbox")

#: The push providers the research names: "push provider (APNs/FCM)". This build calls none of
#: them, exactly as WF-056 calls no calendar or conferencing provider.
PUSH_PROVIDERS: tuple[str, ...] = ("apns", "fcm")

#: The two ways a push can resolve, per the data flow's "device token lookup -> OS-level push".
#: ``delivered`` is the recorded stand-in for a real OS-level delivery; ``unreachable`` covers
#: both of the research's other two reasons. Which of the two stands for which failure mode is
#: this build's choice and is recorded as
#: :data:`~dsr.automation_engagement.inferences.DERIVED_TWO_PUSH_DELIVERY_MODES`.
PUSH_DELIVERY_MODES: tuple[str, ...] = ("delivered", "unreachable")

#: What a push attempt can end as.
#:
#: ``delivered``
#:     A device token was found and the provider accepted the send. **This is a recorded
#:     stand-in, not a delivery.** No APNs or FCM call is made; the attempt is decided by this
#:     build from the device tokens on record. See
#:     :data:`~dsr.automation_engagement.inferences.DERIVED_PUSH_SENDS_ARE_RECORDED`.
#: ``unreachable``
#:     No device token could accept the send. This covers the research's "don't have a device
#:     that can receive the push" and "the push delivery failed".
#: ``recorded``
#:     The push channel is not configured on this reminder, so no push was attempted at all.
PUSH_OUTCOMES: tuple[str, ...] = ("delivered", "unreachable", "recorded")

#: The key on every reminder and every inbox message that says the push was not really sent.
#: The research's push goes through "Customer.io push provider send"; this build calls no
#: provider, so the row carries the truth in the row rather than only in a docstring. This is
#: the same standard as ``meeting_link_is_derived`` in
#: :mod:`dsr.headless_booking.assets`, which says so on the meeting record itself.
PUSH_SENT_KEY = "push_sent"
PUSH_SENT_VALUE = False

#: The note attached to a data-flow step that is *recorded as* a push but did not
#: call a provider. The research's push goes through "Customer.io push provider send";
#: this build calls no provider, so the row carries the stand-in truth.
PUSH_STAND_IN_NOTE = (
    "No push provider is called. The send is recorded as a stand-in for a real"
    " OS-level delivery, exactly as PUSH_SENT_VALUE says the attempt is decided by"
    " this build rather than APNs or FCM."
)

#: The key naming *which* send is a stand-in, so a page can say it rather than infer it.
PUSH_STAND_IN_KEY = "push_send_is_recorded"
PUSH_STAND_IN_VALUE = True

#: What is *not* a stand-in in this workflow. The inbox copy is a real, audited record: it is
#: written through the same store as everything else, and a buyer really can read it. Only the
#: push is recorded. Stating this is the point - the fallback is the part that is real.
INBOX_COPY_IS_REAL = True

# --------------------------------------------------------------------------- #
# The three failure modes the fallback exists for
# --------------------------------------------------------------------------- #

#: The sentence the research gives, quoted exactly, with the three modes it names.
FALLBACK_SENTENCE = (
    "This helps people find your message if they dismissed the push, don't have a device that "
    "can receive the push, or the push delivery failed."
)

#: The three modes, one per clause of the sentence above. These are the reasons a copy exists.
#: They are *not* three push outcomes: the research says the copy is what makes the message
#: findable, and says nothing about which one happened. What this build observes is
#: ``unreachable``, which is the only one of the three with a signal on the send path.
FALLBACK_REASONS: tuple[dict[str, str], ...] = (
    {
        "id": "dismissed",
        "summary": "The buyer saw the push and dismissed it.",
        "sourced": "This helps people find your message if they dismissed the push...",
        "observable_by_the_sender": "no",
    },
    {
        "id": "no_push_device",
        "summary": "The buyer has no device that can receive a push.",
        "sourced": "...don't have a device that can receive the push...",
        "observable_by_the_sender": "no",
    },
    {
        "id": "delivery_failed",
        "summary": "The push delivery failed.",
        "sourced": "...or the push delivery failed.",
        "observable_by_the_sender": "partly",
    },
)

#: The only reason this build can observe on the send path, and why the other two are not
#: invented. ``delivery_failed`` is "partly" because an ``unreachable`` push is a *signal* of
#: it, not proof: a device that cannot receive a push and a delivery that failed are the same
#: observation from here.
OBSERVABLE_FALLBACK_REASONS: tuple[str, ...] = ("delivery_failed",)

# --------------------------------------------------------------------------- #
# The copy toggle, and the unconditional write
# --------------------------------------------------------------------------- #

#: The toggle's label, as the research writes it.
COPY_TOGGLE_LABEL = "Send a copy of this push notification to the notification inbox"

#: The inbox copy is written **unconditionally** when the toggle is on. The data flow says "a
#: duplicate `inbox` message is written to the workspace's notification inbox", and the three
#: failure modes are the reason: a push that failed must not suppress the copy, because the
#: failed push is one of the reasons the copy exists.
COPY_IS_UNCONDITIONAL = True

#: Why the inbox copy is a *duplicate* rather than the original. Quoted, for the record.
DUPLICATE_QUOTE = (
    "You can also send a copy of a push notification as an inbox message."
)

# --------------------------------------------------------------------------- #
# Metrics: recorded against the person
# --------------------------------------------------------------------------- #

#: The two interactions the data flow names at its end: "open/click metrics recorded against the
#: person". They are recorded on the person, not on the message.
ENGAGEMENT_EVENTS: tuple[str, ...] = ("open", "click")

#: The data flow's closing clause, quoted.
METRICS_TARGET = "person"

# --------------------------------------------------------------------------- #
# The build-your-own-inbox path: JSON in, JSON out
# --------------------------------------------------------------------------- #

#: The research's exact words for the JSON contract, quoted.
JSON_PAYLOAD_QUOTE = "Sent and delivered as JSON payloads"
SDK_INBOX_METHOD = "inbox"
SDK_INBOX_QUOTE = "listen for messages with the SDK's `inbox()` method"

#: The message kinds a caller may POST. ``push_copy`` is the duplicate the toggle writes;
#: ``in_app`` is the research's optional second message, "Optionally also add an inbox message
#: in the workflow builder and send it to the inbox."
MESSAGE_KINDS: tuple[str, ...] = ("push_copy", "in_app")

# --------------------------------------------------------------------------- #
# Rate limits, quoted with their numbers
# --------------------------------------------------------------------------- #

#: The App API's limit, quoted: "10 requests per second", answering ``429`` with
#: ``Retry-After``. Published rather than enforced - this build makes no App API call - so a
#: reader can see the number the research gives rather than an invented one.
APP_API_RATE_LIMIT: dict[str, Any] = {
    "requests_per_second": 10,
    "status_on_exceeded": 429,
    "retry_after_header": True,
    "sourced": "10 requests per second on the App API, with 429 and Retry-After.",
    "enforced_by_this_build": False,
}

#: The transactional endpoint's limit, quoted: "3,000 requests per 3 seconds. Transactional
#: sends go through the same ingress as the Track and Pipelines APIs and share their soft
#: limit."
TRANSACTIONAL_RATE_LIMIT: dict[str, Any] = {
    "requests": 3_000,
    "window_seconds": 3,
    "shared_ingress_with": ["Track API", "Pipelines API"],
    "limit_kind": "soft",
    "sourced": (
        "3,000 requests per 3 seconds. Transactional sends go through the same ingress as the "
        "Track and Pipelines APIs and share their soft limit."
    ),
    "enforced_by_this_build": False,
}

# --------------------------------------------------------------------------- #
# The four setup steps, and the whole flow
# --------------------------------------------------------------------------- #

#: The research's own four numbered steps, verbatim. The board renders this order, because the
#: third step is the one that decides whether a send can land at all.
SETUP_STEPS: tuple[dict[str, str], ...] = (
    {
        "n": "1",
        "id": "create_inbox",
        "name": "Create an inbox",
        "detail": "Create an inbox in your workspace settings and give it a name.",
        "route": "POST /api/wf-111/inboxes",
    },
    {
        "n": "2",
        "id": "style_inbox",
        "name": "Style the inbox",
        "detail": "Style the inbox in Design Studio.",
        "route": "not built; the named configuration is stored as data",
        "built": "false",
    },
    {
        "n": "3",
        "id": "publish_inbox",
        "name": "Publish the inbox",
        "detail": "Publish the inbox. Set it back to draft to unpublish.",
        "route": "POST /api/wf-111/inboxes/{inbox_id}/publish",
    },
    {
        "n": "4",
        "id": "send_messages",
        "name": "Compose and send messages",
        "detail": "Compose and send messages to the inbox from an automation, broadcast, or transactional message.",
        "route": "POST /api/wf-111/reminders",
    },
)

#: The data flow, clause by clause, so a page can show where it is in the chain.
DATA_FLOW: tuple[dict[str, str], ...] = (
    {
        "n": "1",
        "stage": "event",
        "detail": "A person/device event (or automation step)",
        "recorded": "yes",
    },
    {
        "n": "2",
        "stage": "provider_send",
        "detail": "Customer.io push provider send",
        "recorded": "stand_in",
        "note": PUSH_STAND_IN_NOTE,
    },
    {
        "n": "3",
        "stage": "device_token_lookup",
        "detail": "device token lookup",
        "recorded": "yes",
    },
    {
        "n": "4",
        "stage": "os_push",
        "detail": "OS-level push (iOS lock screen / Android notification panel)",
        "recorded": "stand_in",
        "note": PUSH_STAND_IN_NOTE,
    },
    {
        "n": "5",
        "stage": "inbox_copy",
        "detail": "a duplicate `inbox` message is written to the workspace's notification inbox",
        "recorded": "yes",
    },
    {
        "n": "6",
        "stage": "sdk_render",
        "detail": "the app's SDK fetches and renders the inbox (floating icon + unread badge)",
        "recorded": "partial",
        "note": (
            "The unread badge is counted and served as JSON. The floating icon and the "
            "platform render are a hosted SDK this build does not ship."
        ),
    },
    {
        "n": "7",
        "stage": "metrics",
        "detail": "open/click metrics recorded against the person",
        "recorded": "yes",
    },
)

#: The one sentence every stand-in in this workflow carries, quoted from the standard this
#: build follows. ``meeting_link`` in :mod:`dsr.headless_booking.assets` says it about a
#: conferencing link, and this says it about a push.
PUSH_STAND_IN_NOTE = (
    "This build calls no push provider. The push outcome is recorded on the reminder row and "
    "the row says so; the inbox copy is a real, audited record."
)


def require_channel(channel: Any) -> str:
    """Normalise and validate a channel against the published two."""

    from dsr.automation_engagement.errors import ReminderRefused

    value = str(channel or "").strip().lower().replace("-", "_")
    if value not in CHANNELS:
        raise ReminderRefused(
            f"channel {channel!r} is not one of the published channels: {', '.join(CHANNELS)}",
            {"channel": "not a published channel"},
        )
    return value


def require_provider(provider: Any) -> str:
    """Normalise and validate a push provider against the published two.

    Note that accepting a provider here does not mean one is called. See
    :data:`PUSH_STAND_IN_NOTE`.
    """

    from dsr.automation_engagement.errors import ReminderRefused

    value = str(provider or "").strip().lower()
    if value not in PUSH_PROVIDERS:
        raise ReminderRefused(
            f"provider {provider!r} is not one of the published providers: "
            f"{', '.join(PUSH_PROVIDERS)}",
            {"provider": "not a published provider"},
        )
    return value


def require_message_kind(kind: Any) -> str:
    """Normalise and validate an inbox message kind against the published two."""

    from dsr.automation_engagement.errors import ReminderRefused

    value = str(kind or "").strip().lower().replace("-", "_")
    if value not in MESSAGE_KINDS:
        raise ReminderRefused(
            f"message kind {kind!r} is not one of the published kinds: {', '.join(MESSAGE_KINDS)}",
            {"kind": "not a published message kind"},
        )
    return value


def require_engagement_event(event: Any) -> str:
    """Normalise and validate an engagement event against the published two.

    Both are named in the data flow's last clause, so an engagement this workflow will not
    accept is refused by name rather than stored under an invented name.
    """

    from dsr.automation_engagement.errors import ReminderRefused

    value = str(event or "").strip().lower()
    if value not in ENGAGEMENT_EVENTS:
        raise ReminderRefused(
            f"engagement event {event!r} is not one of the published events: "
            f"{', '.join(ENGAGEMENT_EVENTS)}",
            {"event": "not a published engagement event"},
        )
    return value


def published_vocabulary() -> dict[str, Any]:
    """Everything a client needs to drive this workflow, in one response.

    A client renders its setup rail, its channel picker and its status labels from this, and
    its error handling from ``reminder_failures``, so the terms a caller sends and the terms it
    branches on cannot drift apart.
    """
    return {
        "collections": {
            "inbox": INBOX_COLLECTION,
            "reminder": REMINDER_COLLECTION,
            "inbox_message": INBOX_MESSAGE_COLLECTION,
            "engagement": ENGAGEMENT_COLLECTION,
        },
        "inbox_states": list(INBOX_STATES),
        "state_published": STATE_PUBLISHED,
        "state_draft": STATE_DRAFT,
        "inboxes_per_workspace": INBOXES_PER_WORKSPACE,
        "apps_per_workspace": APPS_PER_WORKSPACE,
        "default_app": DEFAULT_APP,
        "channels": list(CHANNELS),
        "push_providers": list(PUSH_PROVIDERS),
        "push_delivery_modes": list(PUSH_DELIVERY_MODES),
        "push_outcomes": list(PUSH_OUTCOMES),
        "message_kinds": list(MESSAGE_KINDS),
        "engagement_events": list(ENGAGEMENT_EVENTS),
        "metrics_target": METRICS_TARGET,
        "setup_steps": [dict(step) for step in SETUP_STEPS],
        "data_flow": [dict(step) for step in DATA_FLOW],
        "fallback": {
            "sentence": FALLBACK_SENTENCE,
            "reasons": [dict(reason) for reason in FALLBACK_REASONS],
            "observable_by_this_build": list(OBSERVABLE_FALLBACK_REASONS),
            "copy_is_unconditional": COPY_IS_UNCONDITIONAL,
            "copy_toggle_label": COPY_TOGGLE_LABEL,
            "duplicate_quote": DUPLICATE_QUOTE,
        },
        "stand_ins": {
            "push_send_is_recorded": PUSH_STAND_IN_VALUE,
            "push_sent": PUSH_SENT_VALUE,
            "note": PUSH_STAND_IN_NOTE,
            "inbox_copy_is_real": INBOX_COPY_IS_REAL,
            "standard": (
                "Same standard as meeting_link in dsr.headless_booking.assets: a derived or "
                "recorded stand-in says so on the record itself."
            ),
        },
        "presentation": {
            "fields": [dict(field) for field in PRESENTATION_FIELDS],
            "design_studio_route": DESIGN_STUDIO_ROUTE,
            "dark_mode_rendered": DARK_MODE_RENDERED,
            "note": (
                "The Design Studio step of the research's user flow is a hosted visual editor "
                "this build does not implement and does not fake. The named configuration is "
                "stored on the inbox record in records.data."
            ),
        },
        "json_contract": {
            "payload_quote": JSON_PAYLOAD_QUOTE,
            "sdk_method": SDK_INBOX_METHOD,
            "sdk_quote": SDK_INBOX_QUOTE,
        },
        "rate_limits": {
            "app_api": dict(APP_API_RATE_LIMIT),
            "transactional": dict(TRANSACTIONAL_RATE_LIMIT),
        },
        "reminder_failures": reminder_failures(),
    }


def reminder_failures() -> dict[str, dict[str, str]]:
    """Every refusal this workflow can return, by published reason code.

    A refusal with no published code is a refusal a client has to read prose to act on, so
    every one of these is named, and each carries the sentence it comes from where the research
    has one. Served by the router at ``GET /api/wf-111/vocabulary`` so the page cannot drift
    from the rules behind it.
    """
    return {
        "inbox_name_required": {
            "status": 400,
            "summary": "An inbox needs a name; the research's first step is to 'give it a name'.",
            "sourced": "Create an inbox in your workspace settings and give it a name.",
        },
        "inbox_already_exists": {
            "status": 409,
            "summary": "This room already has its one notification inbox.",
            "sourced": "You can have one notification inbox per workspace.",
            "remedy": "Read the existing inbox, or unpublish it, rather than creating a second.",
        },
        "inbox_not_found": {
            "status": 404,
            "summary": "No such notification inbox.",
            "sourced": "n/a",
        },
        "inbox_not_published": {
            "status": 409,
            "summary": "The inbox is a draft, so it cannot receive or render messages.",
            "sourced": "Publish the inbox. Set it back to draft to unpublish.",
            "remedy": "Publish the inbox, then send again.",
        },
        "inbox_already_in_state": {
            "status": 409,
            "summary": "The inbox is already in that state.",
            "sourced": "n/a",
            "remedy": "Read the inbox to see which state it is in.",
        },
        "inbox_republished": {
            "status": 409,
            "summary": "A published inbox cannot be published again.",
            "sourced": "n/a",
            "remedy": "No action needed; the inbox is already published.",
        },
        "person_required": {
            "status": 400,
            "summary": "A reminder is addressed to a person, and metrics are recorded against one.",
            "sourced": "open/click metrics recorded against the person",
        },
        "title_required": {
            "status": 400,
            "summary": "A push notification needs a title.",
            "sourced": "n/a",
        },
        "channel_required": {
            "status": 400,
            "summary": "Name a channel: push, or inbox.",
            "sourced": "n/a",
        },
        "push_not_configured": {
            "status": 400,
            "summary": "The copy toggle needs a push to copy; this reminder has no push channel.",
            "sourced": "You can also send a copy of a push notification as an inbox message.",
        },
        "copy_toggle_without_push": {
            "status": 400,
            "summary": (
                "The copy toggle copies a push, and there is no push to copy. Compose the push "
                "channel with the copy toggle on, or send the inbox message on its own."
            ),
            "sourced": "You can also send a copy of a push notification as an inbox message.",
        },
        "unknown_channel": {
            "status": 400,
            "summary": "The channel is not one of the published two.",
            "sourced": "n/a",
        },
        "unknown_provider": {
            "status": 400,
            "summary": "The push provider is not one of the published two.",
            "sourced": "push provider (APNs/FCM)",
        },
        "unknown_message_kind": {
            "status": 400,
            "summary": "The inbox message kind is not one of the published two.",
            "sourced": "n/a",
        },
        "unknown_engagement_event": {
            "status": 400,
            "summary": "The engagement event is not one of the published two: open, or click.",
            "sourced": "open/click metrics recorded against the person",
        },
        "reminder_not_found": {
            "status": 404,
            "summary": "No such reminder.",
            "sourced": "n/a",
        },
        "message_not_found": {
            "status": 404,
            "summary": "No such inbox message.",
            "sourced": "n/a",
        },
        "engagement_not_recorded": {
            "status": 409,
            "summary": "This person has no reminder to open or click yet.",
            "sourced": "open/click metrics recorded against the person",
            "remedy": "Send the reminder first, then record the interaction.",
        },
    }
