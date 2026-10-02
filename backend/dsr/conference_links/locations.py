"""The Meeting Type's Location setting, and the wire shape it produces.

This is researched user_flow step 1: "Admin sets the **Location** on the
Meeting Type". Seven options, several of them, one of them the default.

Why several, and why one default
--------------------------------
features_tools names "Meeting Type `Location` picker (multiple locations with a
'Set as Default')". That is a list, not a field, and it is what makes step 4 of
the flow possible at all - "If the meeting moves to a different tool ... the
location of the existing booking is updated" only makes sense if a Meeting Type
offered more than one tool to begin with. So a Location is a *named option on a
Meeting Type*, and the Meeting Type's ``is_default`` flag is what a booking
inherits when it names no Location itself.

The one-default rule is enforced, not merely encouraged. A picker with two
defaults silently answers "which one?" with whichever row the store returned
last, and a picker with none has no answer at all. Both are refused.

The wire shape
--------------
A Location is stored in this product's own terms - a ``kind`` plus whatever that
kind needs - and *renders* to a Cal-shaped location object through
:func:`wire_location`. Two reasons for keeping them apart. The researched
vocabulary is a union of two vendors': Chili Piper's seven picker options and
Cal's eight location types plus thirty integration values. Storing one and
deriving the other means a new kind is one entry rather than a new stored shape,
and it means the moment where "what would we actually send?" is a function a
test can call rather than a fact scattered through a provisioning routine.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dsr.conference_links import vocabulary as vocab
from dsr.conference_links.errors import LocationError, UnknownLocationKind

#: The keys a Location option may carry, beyond the ones the store owns.
#:
#: Not a schema. The store is schema-flexible and a team adding a field must not
#: need to coordinate with anyone, so nothing here *rejects* an unknown key. This
#: tuple exists for the other direction: normalising says which of the researched
#: keys are absent, so a picker can show "this one needs a static link" before
#: someone presses save.
RESEARCHED_KEYS: tuple[str, ...] = (
    "kind",
    "name",
    "is_default",
    "conference_details",
    "custom_text",
    "attendee_prompt",
    "connection_id",
)


def normalise_kind(raw: Any) -> str:
    """Resolve a Location kind, refusing anything outside the researched seven.

    Accepts the picker label as well as the wire slug, because the researched
    options are named in prose - "Google Meet", "Ask the Guest (Provide My Own)"
    - and a client that has the label should not have to know the slug. The
    lookup is case- and separator-insensitive, and a label may be abbreviated
    to its opening words: "Ask the Guest" is the same option as "Ask the Guest
    (Provide My Own)", and treating them as two answers and a typo would make the
    picker's own copy unselectable.
    """
    if raw is None:
        raise UnknownLocationKind(_unknown_message(raw))
    text = str(raw).strip()
    if not text:
        raise UnknownLocationKind(_unknown_message(raw))

    lowered = text.lower()
    if lowered in vocab.LOCATION_KINDS:
        return lowered

    slug = _slug(lowered)
    if slug in vocab.LOCATION_KINDS:
        return slug

    for kind, label in vocab.LOCATION_LABELS.items():
        label_slug = _slug(label.lower())
        if label_slug == slug or label_slug.startswith(f"{slug}-"):
            return kind

    raise UnknownLocationKind(_unknown_message(raw))


def _slug(text: str) -> str:
    """Lowercase, with every run of space, underscore or bracket folded to a dash.

    Folding the brackets as well as the separators is what makes
    ``"Ask the Guest (Provide My Own)"`` and ``"Ask the Guest"`` one option: the
    parenthetical is a clarification the picker adds, not part of the name.
    """
    cleaned = (
        text.replace("(", " ")
        .replace(")", " ")
        .replace("  ", " ")
        .strip()
        .replace(" ", "-")
        .replace("_", "-")
    )
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned


def _unknown_message(raw: Any) -> str:
    """One message for every unresolvable kind, and it always names the seven.

    Including the list on the empty case as well as the typo case is deliberate:
    "a Location must name one of the researched options" without the options
    makes a caller open the spec to find out what the seven are, and a refusal
    that does not say what to send instead is a refusal every caller gets wrong
    once.
    """
    shown = "an empty value" if raw is None or not str(raw).strip() else repr(str(raw))
    return (
        f"{shown} is not a researched Location option; "
        f"choose one of {', '.join(vocab.LOCATION_KINDS)}"
    )


def needs_conference(kind: str) -> bool:
    """Does this kind mint a fresh conference for each booking?

    True for exactly the three the evidence describes: "generates a one-time
    Google Meet link", "generates a one-time Zoom link", "generates a one-time
    Gong link". False for the other four, and that is a sourced answer rather
    than an absence - ``Conference Details`` is explicitly "for those who don't
    want to use one-time links", ``In-Person Meeting`` and ``Custom`` describe
    a place rather than a call, and ``Ask the Guest`` defers the decision.
    """
    return normalise_kind(kind) in vocab.ONE_TIME_KINDS


def provider_for(kind: str) -> str | None:
    """The provider a one-time kind needs connected, or ``None``.

    ``None`` for the four kinds that mint nothing, which is what lets a caller
    skip the connection check rather than inventing a provider to check. The
    provider is the kind itself: all three one-time kinds are their own
    provider, and conflating them with the thirty-value Cal enum is a separate
    concern that :func:`wire_location` handles.
    """
    resolved = normalise_kind(kind)
    return resolved if needs_conference(resolved) else None


def expected_outcome(kind: str) -> str:
    """The provisioning outcome a Location of this kind always reaches.

    Sourced, not inferred, for all four. The three one-time kinds reach
    ``conference-provisioned``; ``Conference Details`` reaches ``static`` because
    it is a text field the admin filled in; ``In-Person Meeting`` and ``Custom``
    reach ``in-person`` because they describe a place; and ``Ask the Guest``
    reaches ``awaiting-guest`` because "This field will enable your prospects to
    provide the Location themselves".

    Having the answer as a function of the kind is what lets the provisioning
    route report what it did *and* what it was always going to do, so a static
    link that "provisioned" nothing reads as a decision rather than a silence.
    """
    resolved = normalise_kind(kind)
    if resolved in vocab.ONE_TIME_KINDS:
        return "conference-provisioned"
    if resolved == "conference-details":
        return "static"
    if resolved in ("in-person", "custom"):
        return "in-person"
    return "awaiting-guest"


def missing_for(kind: str, body: Mapping[str, Any]) -> list[str]:
    """What a Location body leaves out that this kind cannot work without.

    Reported rather than enforced for the two text kinds, and this is the
    judgement worth naming: the research says Conference Details "is a text
    field where you can manually enter the Location details" without saying a
    blank one is refused. An admin saving an empty Conference Details and
    filling it in later is a real workflow, and a hard refusal would block it -
    so the gap is reported on the record and the page, and the booking it
    produces is visibly a static location with nothing in it.

    The one thing that *is* enforced is that a one-time kind names the
    connection it will be provisioned through, because the connection is
    mandatory and a Location that cannot be provisioned is not a Location.
    """
    resolved = normalise_kind(kind)
    gaps: list[str] = []

    if not str(body.get("name") or "").strip():
        gaps.append("name")

    if resolved == "conference-details" and not str(body.get("conference_details") or "").strip():
        gaps.append("conference_details")
    if resolved == "custom" and not str(body.get("custom_text") or "").strip():
        gaps.append("custom_text")
    if resolved in vocab.ONE_TIME_KINDS and not str(body.get("connection_id") or "").strip():
        gaps.append("connection_id")
    if resolved in vocab.ONE_TIME_KINDS and not vocab.LOCATION_TYPE_WIRE.get(resolved):
        gaps.append("wire_location_type")

    return gaps


def wire_location(
    kind: str, body: Mapping[str, Any] | None = None, **overrides: Any
) -> dict[str, Any]:
    """Render a Location as the Cal-shaped ``location`` object it would send.

    The researched wire is ``{"type": <one of eight>, "integration": <one of
    thirty>}``, with ``integration`` present only for integration locations, and
    a ``link`` value on a link location. This is a pure function of the stored
    Location, exposed as a route so a reviewer can see the exact object a
    booking would produce without provisioning anything.

    ``google-meet`` carries ``createRequest: {"requestId": ...}`` because that
    is the field Google documents for minting a conference. The ``request_id``
    passed in is the *conference identity*, not the booking uid, so the value
    here and the value in :func:`~dsr.conference_links.minting.outbound_request`
    are the same string - one name, one meaning, and a reviewer comparing the
    two rendered bodies is not left wondering why the same field differs.
    The reuse prohibition itself is enforced on the provisioned conference, by
    :func:`~dsr.conference_links.minting.claim`, rather than by pretending a
    field name can enforce it.
    """
    resolved = normalise_kind(kind)
    body = dict(body or {})
    location_type = vocab.LOCATION_TYPE_WIRE.get(resolved)
    if not location_type:
        raise LocationError(f"no researched location type for {resolved!r}")

    wire: dict[str, Any] = {"type": location_type}

    if location_type == "integration":
        integration = vocab.PROVIDER_WIRE.get(resolved)
        if not integration:
            raise LocationError(f"no researched integration value for {resolved!r}")
        wire["integration"] = integration
        if resolved == "google-meet":
            request_id = str(
                overrides.get("request_id")
                or body.get("request_id")
                or body.get("booking_uid")
                or ""
            )
            if request_id:
                wire[vocab.GOOGLE_CONFERENCE_CREATE_FIELD] = {"requestId": request_id}
        if resolved == "gong":
            # Gong is not a Cal integration and Gong redirects to Zoom, so the
            # rendered object carries what the redirect is for. Named, because a
            # field that changes the wire shape without a name in the research
            # is a field a reviewer cannot evaluate.
            wire["redirects_to"] = "zoom"

    if location_type == "link":
        value = str(
            overrides.get("conference_details") or body.get("conference_details") or ""
        ).strip()
        if value:
            wire["link"] = value

    if location_type == "address":
        value = str(overrides.get("custom_text") or body.get("custom_text") or "").strip()
        if value:
            wire["address"] = value

    if location_type == "attendeeDefined":
        prompt = str(body.get("attendee_prompt") or "").strip()
        if prompt:
            wire["attendeePrompt"] = prompt

    for key, value in overrides.items():
        if key not in ("request_id", "conference_details", "custom_text"):
            wire[key] = value

    return wire


def describe_kind(kind: str) -> dict[str, Any]:
    """Everything the research says about one Location option, in one object.

    The label, whether it mints a conference, the provider it needs, the
    outcome it always reaches, the wire type it produces, and the sentence the
    research uses. Served from ``GET /api/wf-059/location-kinds/{kind}`` so the
    admin page can render "why do I need to connect Zoom?" without hard-coding
    a tooltip.
    """
    resolved = normalise_kind(kind)
    return {
        "kind": resolved,
        "label": vocab.LOCATION_LABELS.get(resolved, resolved),
        "one_time": needs_conference(resolved),
        "guest_supplied": resolved in vocab.GUEST_SUPPLIED_KINDS,
        "provider": provider_for(resolved),
        "connection_required": needs_conference(resolved),
        "outcome": expected_outcome(resolved),
        "wire_location_type": vocab.LOCATION_TYPE_WIRE.get(resolved),
        "integration_value": vocab.PROVIDER_WIRE.get(resolved),
        "text_key": {
            "conference-details": "conference_details",
            "custom": "custom_text",
            "attendee-defined": "attendee_prompt",
        }.get(resolved),
        "researched": _quotes_for(resolved),
    }


def _quotes_for(kind: str) -> str:
    """The evidence sentence behind one option, or an honest blank."""
    return {
        "google-meet": "This option generates a one-time Google Meet link to be displayed in the Location.",
        "zoom": "This one generates a one-time Zoom link.",
        "gong": vocab.GONG_REDIRECT_QUOTE,
        "conference-details": vocab.STATIC_LINK_QUOTE,
        "in-person": "In-Person Meeting.",
        "custom": "Custom.",
        "attendee-defined": "This field will enable your prospects to provide the Location themselves.",
    }.get(kind, "")


def catalogue() -> dict[str, Any]:
    """All seven options, with the full detail for each.

    Served from ``GET /api/wf-059/location-kinds`` and rendered by the Location
    picker. The ``count`` is carried so a client that renders from a list can
    check it got them all without counting in the browser.
    """
    kinds = [describe_kind(kind) for kind in vocab.LOCATION_KINDS]
    return {
        "count": len(kinds),
        "default": "google-meet"
        if "google-meet" in vocab.LOCATION_KINDS
        else vocab.LOCATION_KINDS[0],
        "kinds": kinds,
    }


__all__ = [
    "RESEARCHED_KEYS",
    "catalogue",
    "describe_kind",
    "expected_outcome",
    "missing_for",
    "needs_conference",
    "normalise_kind",
    "provider_for",
    "wire_location",
]
