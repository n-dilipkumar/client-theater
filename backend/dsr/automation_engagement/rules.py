"""The rules: the one-inbox constraint, the draft rule, and the push decision.

Everything here is a rule, not a framework. It reads a mapping and returns a mapping, raises
one of the three errors in :mod:`dsr.automation_engagement.errors`, and knows nothing about
HTTP, SQLite or a request. That is what makes every rule below testable without a server, and
it is why the HTTP layer in ``dsr.features.wf111_multi_channel_reminder`` is a thin translation
of what happens here.

The three rules the research fixes exactly
------------------------------------------

**One inbox per workspace.** "You can have **one notification inbox per workspace**" and, in
the same research, "The visual notification inbox only serves one mobile app per workspace".
:func:`assert_first_inbox` refuses a second inbox for a room. This is a constraint, not a
default, and it is enforced on create rather than left to the page to respect.

**A draft inbox receives nothing.** The user flow is "click **Publish** (or **Set to draft** to
unpublish)", so publish and draft are two states of *one* inbox. :func:`assert_published` is
called before any write into the inbox, so a draft inbox cannot receive a message and therefore
cannot render one. Refusing the write is better than accepting it and hiding it: a seller who
sent into a draft needs to know the message went nowhere.

**The inbox copy is unconditional.** The data flow says "a duplicate `inbox` message is written
to the workspace's notification inbox", and the reason the copy exists is quoted: "This helps
people find your message if they dismissed the push, don't have a device that can receive the
push, or the push delivery failed." So :func:`should_copy_to_inbox` depends on the toggle alone
and takes no notice of what the push did. A failed push must not suppress the copy - the failed
push is one of the three reasons the copy exists.

The push decision, and what it is not
-------------------------------------

:func:`decide_push` answers the data flow's "device token lookup -> OS-level push" step. It
reads the device tokens on record and returns :data:`~dsr.automation_engagement.vocabulary.
PUSH_DELIVERY_MODES`.

**It is a recorded stand-in, not a delivery.** No APNs or FCM call is made anywhere in this
build. :func:`decide_push` decides from tokens that exist in the store and returns an outcome
that says which, and the caller writes that onto the reminder row alongside
:data:`~dsr.automation_engagement.vocabulary.PUSH_STAND_IN_KEY`. This follows the standard
:func:`dsr.headless_booking.assets.meeting_link` sets: a demo that shows a plausible sent push
without saying so would be worse than one that shows a derived one.

The inbox copy is the part that is **real**. It is an audited record, a buyer can read it, and
:data:`~dsr.automation_engagement.vocabulary.INBOX_COPY_IS_REAL` is served on the vocabulary
route so the page can say so.

What this module will not do
----------------------------

**It will not observe a dismissal.** "They dismissed the push" is one of the three reasons the
copy exists, and no send-path signal reports it. :func:`fallback_reason` therefore returns only
from :data:`~dsr.automation_engagement.vocabulary.OBSERVABLE_FALLBACK_REASONS` and says why the
other two are not invented, rather than guessing at one and reporting it as fact.

**It will not enforce the rate limits.** Both are quoted with their numbers and both are served
on the vocabulary route, but this build makes no App API or transactional call, so there is
nothing to throttle. Asserting a limit nothing sends through would be a limit that only ever
refuses.

**It will not resolve an identity.** The research's person is a Customer.io Person profile. This
build records a person key the caller supplies and does not ask anyone to prove anything.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from dsr.automation_engagement import vocabulary as vocab
from dsr.automation_engagement.errors import InboxStateRefused, ReminderRefused

# --------------------------------------------------------------------------- #
# Instants: every time this workflow handles is derived, never a literal
# --------------------------------------------------------------------------- #

#: The ISO form this build writes and reads, with a ``Z`` rather than ``+00:00``. One function
#: owns it so no call site hand-rolls the suffix and a round-trip cannot change shape.
def to_iso(moment: datetime) -> str:
    """An instant as ISO-8601 UTC with a ``Z`` designator."""

    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_instant(value: Any) -> datetime | None:
    """Parse an ISO-8601 instant, requiring a UTC designator.

    Returns ``None`` for anything unparsable rather than guessing, because a reminder's time is
    the one value in this workflow where a wrong guess is worse than no value: an instant with
    no designator is ambiguous, and :func:`reminder_offset` refuses it by name.
    """

    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(timezone.utc)


def reminder_offset(minutes: Any) -> timedelta:
    """The delay between the send and the reminder firing.

    A reminder is a *time*, so this is the one value in the workflow that must never be a
    literal in a test payload: a test that hardcodes a moment breaks the day that moment passes.
    Every caller derives it from an injected clock and this function turns it into a delay, so
    the stored value is a duration and stays correct however long the test lives.

    Negative and zero offsets are refused, because a reminder due before it was composed, or at
    the instant it was composed, is not a reminder - and the research's send is a trigger from
    an event, which is not a moment in the future by definition.
    """

    if minutes is None:
        raise ReminderRefused(
            "send_at_minutes is required: a reminder is a time, not an immediate push",
            {"send_at_minutes": "required"},
        )
    try:
        value = float(minutes)
    except (TypeError, ValueError) as exc:
        raise ReminderRefused(
            "send_at_minutes must be a number of minutes from now",
            {"send_at_minutes": "not a number"},
        ) from exc
    if value <= 0:
        raise ReminderRefused(
            "send_at_minutes must be a positive number of minutes from now",
            {"send_at_minutes": "must be positive"},
        )
    if value > vocab.MAX_LEAD_MINUTES:
        raise ReminderRefused(
            f"send_at_minutes must be at most {vocab.MAX_LEAD_MINUTES} minutes from now",
            {"send_at_minutes": f"at most {vocab.MAX_LEAD_MINUTES}"},
        )
    return timedelta(minutes=value)


# --------------------------------------------------------------------------- #
# The inbox: one per workspace, published or draft
# --------------------------------------------------------------------------- #


def validate_inbox(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The research's step one: "Create an inbox and give it a name."

    The name is the only required field, and the five styling knobs from the design studio step
    are carried through untouched. They are stored, not validated against a style sheet, because
    this build ships no editor: see
    :data:`~dsr.automation_engagement.vocabulary.PRESENTATION_FIELDS`.
    """

    name = str(payload.get("name") or "").strip()
    if not name:
        raise ReminderRefused(
            "an inbox needs a name: the research's first step is to give it one",
            {"name": "required"},
        )

    inbox: dict[str, Any] = {
        "name": name,
        "state": vocab.STATE_DRAFT,
        "one_per_workspace": vocab.INBOXES_PER_WORKSPACE,
        "serves_app": vocab.DEFAULT_APP,
    }
    for field in vocab.PRESENTATION_FIELDS:
        if field["field"] in payload:
            inbox[field["field"]] = payload[field["field"]]
    if "dark_mode_styles" in inbox:
        # Stored, and said not to render. The product is light-theme only and locked to it, so
        # recording this without a note would leave a caller believing it does something.
        inbox["dark_mode_rendered"] = vocab.DARK_MODE_RENDERED
    return inbox


def assert_first_inbox(existing: Iterable[Any]) -> None:
    """One inbox per workspace, enforced.

    The research states the constraint twice, so it is a rule and not a preference: "You can
    have **one notification inbox per workspace**" and "The visual notification inbox only
    serves one mobile app per workspace."
    """

    if any(True for _ in existing):
        raise InboxStateRefused(
            "this room already has its one notification inbox",
            reason="inbox_already_exists",
            remedy=vocab.reminder_failures()["inbox_already_exists"]["remedy"],
            inboxes_per_workspace=vocab.INBOXES_PER_WORKSPACE,
        )


def assert_published(inbox: Mapping[str, Any]) -> None:
    """A draft inbox receives nothing, so it can render nothing.

    "A notification inbox is a container that displays messages to your audience that they
    access at their leisure" - a container in draft has nothing in it, because the research's
    third step has not happened yet.
    """

    state = str(inbox.get("state") or vocab.STATE_DRAFT)
    if state != vocab.STATE_PUBLISHED:
        raise InboxStateRefused(
            f"the inbox is {state}, so it cannot receive or render messages",
            reason="inbox_not_published",
            remedy=vocab.reminder_failures()["inbox_not_published"]["remedy"],
            inbox_state=state,
        )


def assert_transition(state: str, target: str) -> None:
    """Publish and draft are two states of one inbox, and both moves are legal from both.

    The research's user flow is "click **Publish** (or **Set to draft** to unpublish)", so
    there are two moves and no others. Anything else is refused by name.
    """

    from dsr.automation_engagement.errors import InboxStateRefused

    if target not in vocab.INBOX_STATES:
        raise ReminderRefused(
            f"inbox state {target!r} is not one of the published states: "
            f"{', '.join(vocab.INBOX_STATES)}",
            {"state": "not a published inbox state"},
        )
    if state == target:
        raise InboxStateRefused(
            f"the inbox is already {target}",
            reason="inbox_already_in_state",
            remedy=vocab.reminder_failures()["inbox_already_in_state"]["remedy"],
            inbox_state=state,
        )


# --------------------------------------------------------------------------- #
# The push: a recorded stand-in, and the device tokens it reads
# --------------------------------------------------------------------------- #


def device_tokens(payload: Mapping[str, Any]) -> list[str]:
    """The device tokens on record for this person.

    The data flow's third clause is "device token lookup", and the research names "Person
    profile + device tokens" as a data source. A caller supplies the tokens it holds; this build
    stores no directory and authenticates nobody, which
    :data:`~dsr.automation_engagement.inferences.DERIVED_CALLER_SUPPLIES_DEVICE_TOKENS` records.
    """

    raw = payload.get("device_tokens")
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, Sequence):
        raise ReminderRefused(
            "device_tokens must be a list of device tokens",
            {"device_tokens": "not a list"},
        )
    return [str(token).strip() for token in raw if str(token).strip()]


def decide_push(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Decide what the push channel did, from the tokens on record.

    Returns a mapping carrying the outcome, the mode and the tokens it looked at - and nothing
    that claims a message reached a device.

    **This is a recorded stand-in.** The data flow says "Customer.io push provider send ->
    device token lookup -> OS-level push (iOS lock screen / Android notification panel)", and
    this build calls no provider. What it does instead is decide from the tokens present:

    * no tokens, or every token rejected -> ``unreachable``, covering the research's "don't
      have a device that can receive the push" and "the push delivery failed";
    * at least one token -> ``delivered``.

    The caller writes this onto the reminder row next to
    :data:`~dsr.automation_engagement.vocabulary.PUSH_STAND_IN_KEY`, so the row itself says the
    send was recorded rather than performed. See :func:`dsr.headless_booking.assets.meeting_link`
    for the standard this follows.
    """

    tokens = device_tokens(payload)
    mode = vocab.PUSH_DELIVERY_MODES[0] if tokens else vocab.PUSH_DELIVERY_MODES[1]
    return {
        "outcome": mode,
        "mode": mode,
        "tokens_looked_up": len(tokens),
        vocab.PUSH_STAND_IN_KEY: vocab.PUSH_STAND_IN_VALUE,
        vocab.PUSH_SENT_KEY: vocab.PUSH_SENT_VALUE,
        "provider": payload.get("provider"),
        "note": vocab.PUSH_STAND_IN_NOTE,
    }


def should_copy_to_inbox(payload: Mapping[str, Any], channels: Sequence[str]) -> bool:
    """Whether this send writes the duplicate inbox copy.

    The toggle alone decides it, and the push's outcome is not consulted. The data flow says the
    duplicate "is written", and the research's reason for it is that it helps people who
    dismissed the push, could not receive it, **or whose delivery failed**. Suppressing the copy
    on a failed push would suppress it in exactly the case it exists for.
    """

    toggle = payload.get("copy_to_inbox")
    return bool(toggle) and "push" in channels


def fallback_reason(push: Mapping[str, Any]) -> str | None:
    """The one fallback reason this build can observe, or ``None``.

    "They dismissed the push" and "don't have a device that can receive the push" are both
    named by the research, and neither has a signal on the send path: a dismissed push and a
    push that never arrived look identical to the sender. So this returns only
    ``delivery_failed``, and only when the push came back ``unreachable`` - and even that is a
    signal rather than proof, because "no device could receive it" is the same observation from
    here. See :data:`~dsr.automation_engagement.inferences.DERIVED_DISMISSAL_IS_NOT_OBSERVED`.
    """

    if push.get("outcome") == vocab.PUSH_DELIVERY_MODES[1]:
        return vocab.OBSERVABLE_FALLBACK_REASONS[0]
    return None


# --------------------------------------------------------------------------- #
# The reminder
# --------------------------------------------------------------------------- #


def validate_reminder(payload: Mapping[str, Any], *, channels: Sequence[str]) -> dict[str, Any]:
    """Validate a composed reminder and return its normalised configuration.

    Order matters and is the rule: the person first, because every metric lands on one; then
    the channels, because the copy toggle needs a push to copy; then the push's own required
    field. A caller therefore never has a reminder recorded against nobody.
    """

    person = str(payload.get("person_id") or "").strip()
    if not person:
        raise ReminderRefused(
            "a reminder is addressed to a person, and its metrics are recorded against one",
            {"person_id": "required"},
        )

    if not channels:
        raise ReminderRefused(
            "name at least one channel: push, or inbox",
            {"channels": "required"},
        )

    if "push" in channels:
        title = str(payload.get("title") or "").strip()
        if not title:
            raise ReminderRefused(
                "a push notification needs a title",
                {"title": "required for the push channel"},
            )
        if payload.get("provider"):
            vocab.require_provider(payload.get("provider"))

    if payload.get("copy_to_inbox") and "push" not in channels:
        raise ReminderRefused(
            "the copy toggle copies a push, and this reminder has no push channel to copy",
            {
                "copy_to_inbox": (
                    "requires the push channel; send the inbox channel on its own, or compose "
                    "the push with the copy toggle on"
                )
            },
        )

    config: dict[str, Any] = {
        "person_id": person,
        "channels": list(channels),
        "copy_to_inbox": should_copy_to_inbox(payload, channels),
        "copy_toggle_label": vocab.COPY_TOGGLE_LABEL,
    }
    for key in ("title", "body", "provider", "deep_link", "source_event"):
        if payload.get(key) not in (None, ""):
            config[key] = payload[key]
    return config


def due_at(now: datetime, lead: timedelta) -> str:
    """When this reminder fires, derived from the injected clock.

    Nothing in this workflow reads a wall clock. Every time a record carries comes from a
    ``now`` a caller injected, which is what lets a test pin the moment without a fixed date in
    a payload and without a sleep.
    """

    return to_iso(now + lead)


def is_due(fires_at: str | None, now: datetime) -> bool:
    """Whether a reminder's time has arrived.

    A reminder whose ``fires_at`` cannot be parsed is due rather than not: a message whose time
    is unreadable should reach the buyer, and hiding it would be the worse failure.
    """

    moment = parse_instant(fires_at)
    if moment is None:
        return True
    return now >= moment


# --------------------------------------------------------------------------- #
# The build-your-own-inbox JSON contract
# --------------------------------------------------------------------------- #


def inbox_payload(
    message: Mapping[str, Any],
    *,
    inbox: Mapping[str, Any],
    push: Mapping[str, Any] | None = None,
    unread: bool = True,
) -> dict[str, Any]:
    """The JSON a caller renders an inbox from.

    The research is explicit that this path is JSON: messages are "Sent and delivered as JSON
    payloads" and a caller "listen[s] for messages with the SDK's `inbox()` method". So this
    returns the message as data, with the fields a renderer needs and nothing that pretends to
    be a platform push.

    ``duplicate_of`` carries the reminder id when this message is the copy, so a caller can tell
    the duplicate from a message composed in the builder.
    """

    body: dict[str, Any] = {
        "message_id": message.get("id"),
        "inbox_id": inbox.get("id"),
        "inbox_name": inbox.get("name"),
        "room_id": message.get("room_id"),
        "person_id": message.get("person_id"),
        "kind": message.get("kind"),
        "title": message.get("title"),
        "body": message.get("body"),
        "deep_link": message.get("deep_link"),
        "created_at": message.get("created_at"),
        "read_at": message.get("read_at"),
        "unread": unread and message.get("read_at") is None,
        "serves_app": vocab.DEFAULT_APP,
        "duplicate_of": message.get("duplicate_of"),
        "fallback_reason": message.get("fallback_reason"),
    }
    if push is not None:
        body["push"] = {
            "outcome": push.get("outcome"),
            "mode": push.get("mode"),
            vocab.PUSH_STAND_IN_KEY: push.get(vocab.PUSH_STAND_IN_KEY),
            vocab.PUSH_SENT_KEY: push.get(vocab.PUSH_SENT_KEY),
        }
    return body


def engagement_row(event: str, person_id: str, *, reminder_id: str | None, now: datetime) -> dict[str, Any]:
    """One open or click, recorded against the person.

    The data flow ends with "open/click metrics recorded against the person", so the row's
    subject is the person and the message is carried beside it. Recording it against the message
    would make the person unreachable without replaying every message they touched.
    """

    return {
        "event": vocab.require_engagement_event(event),
        "person_id": person_id,
        "reminder_id": reminder_id,
        "recorded_at": to_iso(now),
        "target": vocab.METRICS_TARGET,
    }
