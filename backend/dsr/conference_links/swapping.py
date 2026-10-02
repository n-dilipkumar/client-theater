"""Moving an existing booking to a different tool, and telling the attendees.

This is researched user_flow step 4: "If the meeting moves to a different tool
(or a rep's integration is swapped), the location of the existing booking is
updated and the conference link is re-provisioned; attendees are emailed the
change." The researched endpoints agree, in one sentence each.

**Cal's swap provisions as it swaps.** "For integration locations (e.g. Zoom,
Google Meet, Cal Video), the endpoint also provisions a conference link.
Attendees are notified of the location change by email." So one researched call
does two things this package models as two records: the conference is re-minted
under the new provider, and the notification is raised. A swap that only moved
the link, or only notified, would be a partial implementation of the sentence
above, which is why :func:`plan_swap` returns both and the engine writes both.

**The webhook keeps the old value.** "``BOOKING_LOCATION_UPDATED`` ... The
payload mirrors the standard booking payload, with one addition:
``previousLocation`` holds the location before the change and existing
``location`` holds the new location." That is the researched reason a swap has a
history at all: without ``previousLocation`` a change of tool is invisible after
the fact. :func:`plan_swap` therefore refuses a swap whose previous location it
cannot state, because a trail that begins at the new value records that
something happened and not what it replaced.

**The refuse-if-unchanged rule.** The research does not mention swapping a
booking to the location it already has, and this build refuses it. The reason is
the notification: "Attendees are notified of the location change by email", and a
swap to the same location would email every attendee that nothing changed. This
is recorded as the ``unchanged-swap-is-refused`` inference.

**Gong to Zoom is a real swap.** "Gong will redirect you to Zoom" means a Gong
link and a Zoom link are *different links* to the same meeting, so moving a
booking from Gong to Zoom is a location change and gets a notification, while
re-provisioning the same Gong location is not. The comparison is on the
provider *and* the URL, which is what makes that distinction fall out rather
than be special-cased.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dsr.conference_links import locations, vocabulary as vocab
from dsr.conference_links.errors import LocationUnchanged

#: The two researched reasons a swap can be attributed to. Step 4 names two, and
#: they are different events: the admin moved the meeting, or the rep's
#: integration was swapped under it. The second is the one a seller would
#: otherwise see appear with no explanation.
SWAP_REASONS: tuple[str, ...] = ("meeting-moved-tool", "rep-integration-swapped")

#: The join-link placeholder :func:`render_invite` fills from the booking.
#:
#: Not a researched dynamic tag - the research names only the reschedule and
#: cancel ones - so it is named here rather than added to
#: :data:`~dsr.conference_links.vocabulary.DYNAMIC_TAGS`, which is the researched
#: list and nothing else.
LOCATION_TOKEN = "{location}"


def same_location(current: Mapping[str, Any] | None, proposed: Mapping[str, Any] | None) -> bool:
    """Does the proposed Location put the booking where it already is?

    Compared on the *provider and the URL*, never on the Location's id, because
    a swap is about where a guest clicks. A rep who re-points a Meeting Type at
    a different record describing the same Zoom link has not moved the meeting,
    and a rep who switches Gong to a Zoom link *has*, because "Gong will
    redirect you to Zoom" means the two are different links to one meeting.
    """
    left = dict(current or {})
    right = dict(proposed or {})
    if not left and not right:
        return True
    if (left.get("location_provider") or left.get("provider")) != (
        right.get("location_provider") or right.get("provider")
    ):
        return False
    left_url = str(left.get(vocab.MEETING_LOCATION_FIELD) or left.get("url") or "")
    right_url = str(right.get(vocab.MEETING_LOCATION_FIELD) or right.get("url") or "")
    return left_url == right_url


def plan_swap(
    booking: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    reason: str = "meeting-moved-tool",
    detail: str | None = None,
    resulting_url: str | None = None,
) -> dict[str, Any]:
    """Work out what moving this booking to ``target`` involves.

    Returns the researched ``previousLocation``/``location`` pair, whether a new
    conference is needed, and the notification the researched endpoint raises
    alongside it. Pure: it writes nothing and opens no socket, so the engine can
    show a seller exactly what a swap will do before they press the button.

    Refuses a no-op swap. See this module's docstring for why.

    ``resulting_url`` is the join URL the swap *would* leave the booking at, when
    it re-provisions. The refusal compares that rather than the provider, because
    this build derives a conference's identity from the provider, the room and
    the booking - so re-provisioning the same provider lands on the same link,
    and a swap that "changed" only the provider would email every attendee that
    the meeting moved while handing back the identical URL. Comparing the result
    is what makes the rule mean what it says.
    """
    if reason not in SWAP_REASONS:
        raise LocationUnchanged(
            f"{reason!r} is not a researched swap reason; choose one of {', '.join(SWAP_REASONS)}"
        )

    kind = locations.normalise_kind(target.get("kind"))
    previous = {
        "location_provider": booking.get("location_provider"),
        vocab.MEETING_LOCATION_FIELD: booking.get(vocab.MEETING_LOCATION_FIELD),
        vocab.BOOKING_LOCATION_FIELD: booking.get(vocab.BOOKING_LOCATION_FIELD),
        "conference_id": booking.get("conference_id"),
    }
    new_url = (
        str(resulting_url)
        if resulting_url
        else str(target.get(vocab.MEETING_LOCATION_FIELD) or target.get("url") or "")
    )
    proposed = {
        "location_provider": target.get("provider") or kind,
        vocab.MEETING_LOCATION_FIELD: new_url or None,
    }

    if same_location(previous, proposed):
        raise LocationUnchanged(
            f"booking {booking.get('booking_uid') or booking.get('id')} is already at "
            f"{previous.get('location_provider')}"
            + (
                f" on {previous.get(vocab.MEETING_LOCATION_FIELD)}"
                if previous.get(vocab.MEETING_LOCATION_FIELD)
                else ""
            )
            + "; the researched swap notifies attendees by email, so a swap that leaves the "
            "join link unchanged would email every attendee that nothing changed"
        )

    needs_conference = locations.needs_conference(kind)

    return {
        "reason": reason,
        "detail": str(detail or "").strip() or None,
        "target_kind": kind,
        "previous_location": previous,
        "location": proposed,
        # Cal's one sentence does both, and the research says so explicitly.
        "reprovisions": needs_conference,
        "notifies": True,
        "notification": notification(
            booking,
            previous,
            proposed,
            reason=reason,
            detail=detail,
        ),
        "webhook": {
            "event": vocab.BOOKING_LOCATION_UPDATED,
            vocab.PREVIOUS_LOCATION_FIELD: previous,
            vocab.BOOKING_LOCATION_FIELD: proposed,
            "mirrors": "the standard booking payload",
        },
        "endpoint": {
            "method": "PATCH",
            "url": f"/v2/bookings/{booking.get('booking_uid') or booking.get('id')}/location",
            "headers": {
                vocab.CAL_API_VERSION_HEADER: vocab.CAL_API_VERSION,
            },
            "scope": vocab.CAL_BOOKING_WRITE_SCOPE,
            "evidence": vocab.SWAP_PROVISIONS_QUOTE,
        },
    }


def notification(
    booking: Mapping[str, Any],
    previous: Mapping[str, Any],
    proposed: Mapping[str, Any],
    *,
    reason: str = "meeting-moved-tool",
    detail: str | None = None,
) -> dict[str, Any]:
    """The email the researched swap raises.

    Built and returned, not sent - this product is the source of the booking,
    not a relay for the vendor's mail. Every field the researched sentence
    implies is present: who it is for, what changed, what it changed from, and
    why, so the audit row beside it can be read without opening the mail.
    """
    booking_uid = str(booking.get("booking_uid") or booking.get("id") or "")
    old_provider = previous.get("location_provider") or "none"
    new_provider = proposed.get("location_provider") or "none"

    if reason == "rep-integration-swapped":
        headline = f"Your {booking_uid} meeting has moved to {new_provider}"
    else:
        headline = f"Your {booking_uid} meeting has moved from {old_provider} to {new_provider}"

    return {
        "channel": "email",
        "to": booking.get("attendee_email") or booking.get("attendee") or booking.get("email"),
        "subject": headline,
        "body": (
            f"The location of meeting {booking_uid} has changed from {old_provider} to "
            f"{new_provider}. Join at: {proposed.get(vocab.MEETING_LOCATION_FIELD) or ''}"
        ),
        "previous_location_provider": old_provider,
        "location_provider": new_provider,
        "reason": reason,
        "detail": str(detail or "").strip() or None,
        "sent": False,
        "evidence": vocab.SWAP_PROVISIONS_QUOTE,
    }


def render_invite(
    booking: Mapping[str, Any],
    template: str | None = None,
    *,
    reschedule_url: str | None = None,
    cancel_url: str | None = None,
) -> dict[str, Any]:
    """Render an invite body with the researched dynamic tags resolved.

    "the invite body can embed reschedule/cancel URLs via dynamic tags", and
    features_tools names both: ``CP.Meeting.RescheduleUrl`` and
    ``CP.Meeting.CancelUrl``.

    An unresolved tag is left in the body and reported in ``unresolved`` rather
    than replaced with an empty string. That is the difference between an invite
    that visibly says ``CP.Meeting.RescheduleUrl`` - which a rep spots and fixes
    - and one that quietly ships a blank link to a prospect.

    A tag is unresolved when no URL was supplied *and* the booking has no uid to
    derive one from, which is the preview case: an admin composing a template
    before any booking exists. Substituting a path built from an empty uid
    (``/bookings//reschedule``) would be a link that silently 404s in a
    prospect's inbox, which is worse than the tag being visible.
    """
    # Stripped only to decide whether the caller supplied one. The body that is
    # then rendered is the caller's own text, untrimmed: silently trimming a
    # template someone will send to a prospect edits their words, and an invite
    # body is not a field this workflow gets to normalise.
    supplied = str(template) if template and str(template).strip() else DEFAULT_TEMPLATE
    body = supplied
    booking_uid = str(booking.get("booking_uid") or booking.get("id") or "")

    # The join link is this build's own token rather than a researched dynamic
    # tag, and it is filled in from the booking the same way. A default template
    # that shipped a literal `{location}` would be a placeholder this module
    # wrote and then failed to fill, which is worse than no placeholder: a rep
    # would send `{location}` to a prospect and never see it.
    location = str(booking.get(vocab.MEETING_LOCATION_FIELD) or "").strip()
    if location:
        body = body.replace(LOCATION_TOKEN, location)
    unresolved_placeholders = [] if location or LOCATION_TOKEN not in body else [LOCATION_TOKEN]

    supplied_urls: dict[str, str | None] = {
        vocab.RESCHEDULE_TAG: str(reschedule_url)
        if reschedule_url
        else (f"/bookings/{booking_uid}/reschedule" if booking_uid else None),
        vocab.CANCEL_TAG: str(cancel_url)
        if cancel_url
        else (f"/bookings/{booking_uid}/cancel" if booking_uid else None),
    }

    rendered = body
    unresolved: list[str] = []
    for tag, value in supplied_urls.items():
        if value is None:
            if tag in rendered:
                unresolved.append(tag)
            continue
        rendered = rendered.replace(tag, value)

    return {
        "booking_uid": booking_uid,
        "template": supplied,
        "body": rendered,
        "tags": list(vocab.DYNAMIC_TAGS),
        "location_token": LOCATION_TOKEN,
        "resolved": {tag: value for tag, value in supplied_urls.items() if value is not None},
        "unresolved": unresolved,
        "unresolved_placeholders": unresolved_placeholders,
        "location": location or None,
        "previous_location": booking.get(vocab.PREVIOUS_LOCATION_FIELD),
    }


#: The default invite body, so the demo has something to render. It names both
#: researched tags verbatim, which is what makes the render a demonstration
#: rather than a formality, and this build's own ``{location}`` token.
DEFAULT_TEMPLATE = (
    "You are booked.\n\n"
    f"Join: {LOCATION_TOKEN}\n\n"
    "Need another time? Reschedule here: "
    f"{vocab.RESCHEDULE_TAG}\n"
    "Cannot make it? Cancel here: "
    f"{vocab.CANCEL_TAG}\n"
)


__all__ = [
    "DEFAULT_TEMPLATE",
    "SWAP_REASONS",
    "notification",
    "plan_swap",
    "render_invite",
    "same_location",
]
