"""The reschedule and cancel links in the invite body, and the one that expires.

Step 1 of the flow is an attendee opening a URL that was injected into the invite:
"Chili Piper injects ``CP.Meeting.RescheduleUrl`` / ``CP.Meeting.CancelUrl`` into
the Description". Those two tags are the only unauthenticated door this workflow
has, so they are modelled as first-class objects: a token per booking per kind,
resolvable, checkable, and reported on before anything is written.

Step 2 then says what happens to one of them: "If ``Expire Reschedule Link`` is
on, the link stops working once the meeting has happened." That is the whole of
the rule, and it is deliberately narrow - it names *the reschedule link*, and it
names *the meeting happening*, and this build does not widen either. What the
research is silent about, and what therefore becomes a named inference, is listed
in :mod:`dsr.scheduling.inferences` rather than decided quietly here:

* the boundary is the meeting's **start**, not its end
  (:data:`~dsr.scheduling.timeutil.has_happened`);
* the setting governs the **reschedule** link only, so a cancel link is gated on
  the booking still being live and on nothing else;
* a host acting through the panel is not acting through a link, so expiry never
  applies to the ``chilical_home`` and ``calendar_event`` sources.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping

from dsr.scheduling.errors import LinkExpired, MeetingChangeError, MeetingNotFound
from dsr.scheduling.meeting_types import expire_reschedule_link
from dsr.scheduling.timeutil import has_happened
from dsr.scheduling.vocabulary import CANCEL, CANCEL_URL_TAG, RESCHEDULE, RESCHEDULE_URL_TAG

#: Which tag resolves to which intent. The research writes them as a pair in one
#: sentence, so the pairing is sourced rather than chosen.
TAG_FOR_KIND: dict[str, str] = {RESCHEDULE: RESCHEDULE_URL_TAG, CANCEL: CANCEL_URL_TAG}
KIND_FOR_TAG: dict[str, str] = {tag: kind for kind, tag in TAG_FOR_KIND.items()}

#: The field on the booking each kind's token is stored in.
TOKEN_FIELD: dict[str, str] = {RESCHEDULE: "reschedule_token", CANCEL: "cancel_token"}

#: Where a meeting link is served, relative to the app's base URL.
#:
#: Not ``/r/``. That path is the public-room share link - ``wf-017-white-label``
#: matches ``^/r/([^/]+)/?$`` against it and renders a room - and a link token is
#: not a room slug, so a meeting link under ``/r/`` lands on a page that goes
#: looking for a room by that name and finds nothing. The research names these two
#: URLs as Chili Piper's own (``CP.Meeting.RescheduleUrl``, ``CP.Meeting.CancelUrl``)
#: and does not fix a path on *this* host, so the path is a product decision. It
#: is named here, where a reviewer can see it and change it in one line, rather
#: than spelled into the template.
LINK_PATH = "booking"

#: How the invite's dynamic tags are resolved into a URL. ``{base}`` is supplied
#: by the caller so the product is not hard-coded to a hostname, which is what
#: would make a demo of the link unusable on any other host. ``{path}`` is
#: :data:`LINK_PATH` unless a caller overrides it, so the template carries the
#: decision rather than burying it in a format string.
URL_TEMPLATE = "{base}/{path}/{token}"


def mint_token() -> str:
    """A fresh, unguessable token.

    18 url-safe bytes. The token is the only credential on this door, so it is
    generated rather than derived from the booking uid - a derived token would let
    anybody who can guess or enumerate a booking uid move somebody's meeting.
    """
    return secrets.token_urlsafe(18)


def mint_pair(uid: str, *, factory: Callable[[], str] = mint_token) -> dict[str, str]:
    """The two tokens a booking carries, from one factory.

    A factory rather than two calls so a test can make them reproducible and a
    seed can produce a stable demo link.
    """
    if not str(uid or "").strip():
        raise MeetingChangeError("a link needs a booking uid")
    return {RESCHEDULE: factory(), CANCEL: factory()}


def build_url(base: str, token: str, *, path: str = LINK_PATH) -> str:
    """The absolute link for one token.

    ``path`` defaults to :data:`LINK_PATH` and is overridable per call, so a
    deployment that serves the door somewhere else does not have to fork this.
    """
    return URL_TEMPLATE.format(
        base=str(base or "").rstrip("/"),
        path=str(path or LINK_PATH).strip("/"),
        token=token,
    )


@dataclass(frozen=True)
class LinkState:
    """What a link resolves to, and whether it still works.

    Returned rather than raised, because the interesting call is the read: a rep
    opening the Meetings Activity panel needs to know the link is dead *before*
    telling an attendee to use it, and a dead link is a fact, not an exception.
    The write paths turn ``expired`` into a :class:`~dsr.scheduling.errors.LinkExpired`.
    """

    token: str
    kind: str
    tag: str
    booking_uid: str
    expired: bool
    reason: str
    checked_at: str
    booking: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "kind": self.kind,
            "tag": self.tag,
            "booking_uid": self.booking_uid,
            "expired": self.expired,
            "reason": self.reason,
            "checked_at": self.checked_at,
        }


def token_for(booking: Mapping[str, Any], kind: str) -> str:
    field = TOKEN_FIELD.get(kind)
    if not field:
        raise MeetingChangeError(f"unknown link kind {kind!r}")
    return str(dict(booking.get("data") or {}).get(field) or "")


def find_booking_by_token(store: Any, token: str) -> tuple[dict[str, Any], str]:
    """The booking a token names, and the kind of link it is.

    Found by a query on each token field rather than by scanning, because the
    dynamic index resolves a JSON path and the token *is* one. Two queries, both
    indexed, and a token can only be in one of them.
    """
    needle = str(token or "").strip()
    if not needle:
        raise MeetingNotFound("no link token was given")
    for kind, field in TOKEN_FIELD.items():
        found = store.find("booking", {field: needle}, limit=2)
        if found:
            return found[0], kind
    raise MeetingNotFound(f"link {needle} does not resolve to a booking")


def state_for(
    booking: Mapping[str, Any],
    kind: str,
    meeting_type: Mapping[str, Any] | None,
    now: datetime,
) -> LinkState:
    """Is this link still usable, and if not, why.

    Two independent ways to be closed, and they are kept apart in the reason
    because the remedy is different: a link on a cancelled booking is closed for
    good, and a link closed by ``Expire Reschedule Link`` is the setting working
    exactly as an administrator asked.
    """
    data = dict(booking.get("data") or {})
    uid = str(data.get("uid") or booking.get("id") or "")
    token = token_for(booking, kind)
    stamp = now.isoformat(timespec="seconds")
    status = str(data.get("status") or "")

    if not token:
        return LinkState(
            token=token,
            kind=kind,
            tag=TAG_FOR_KIND[kind],
            booking_uid=uid,
            expired=True,
            reason=f"this booking carries no {kind} link",
            checked_at=stamp,
            booking=data,
        )

    if status and status != "booked":
        return LinkState(
            token=token,
            kind=kind,
            tag=TAG_FOR_KIND[kind],
            booking_uid=uid,
            expired=True,
            reason=(
                f"the meeting is {status}, so its {kind} link is closed"
                if status != "rescheduled"
                else f"the meeting was rescheduled, so this {kind} link has moved to its new booking"
            ),
            checked_at=stamp,
            booking=data,
        )

    if (
        kind == RESCHEDULE
        and expire_reschedule_link(meeting_type or {})
        and has_happened(data.get("start_at"), data.get("end_at"), now)
    ):
        return LinkState(
            token=token,
            kind=kind,
            tag=TAG_FOR_KIND[kind],
            booking_uid=uid,
            expired=True,
            reason=(
                "Expire Reschedule Link is on for this meeting type, and the meeting has already "
                "happened"
            ),
            checked_at=stamp,
            booking=data,
        )

    return LinkState(
        token=token,
        kind=kind,
        tag=TAG_FOR_KIND[kind],
        booking_uid=uid,
        expired=False,
        reason=f"the {kind} link is open",
        checked_at=stamp,
        booking=data,
    )


def require_open(state: LinkState) -> None:
    """Turn a closed link into the refusal the write paths answer with.

    A :class:`~dsr.scheduling.errors.LinkExpired` (410) rather than a 400, because
    the link was valid and the answer to "what now" is that somebody has to act.
    """
    if state.expired:
        raise LinkExpired(state.reason)


def invite_body(
    booking: Mapping[str, Any],
    *,
    base: str,
    meeting_type: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    path: str = LINK_PATH,
) -> dict[str, Any]:
    """The invite's Description, with the two researched dynamic tags resolved.

    The template is a team field rather than a product constant: the research says
    the tags are "injected into the Description" but does not say what the rest of
    the Description says, and a team that has written its own invitation copy
    should keep it. A team that has not gets a usable default.
    """
    from dsr.scheduling.timeutil import utcnow

    data = dict(booking.get("data") or {})
    moment = now or utcnow()
    states = {kind: state_for(booking, kind, meeting_type, moment) for kind in TAG_FOR_KIND}
    tags = {
        RESCHEDULE_URL_TAG: build_url(base, states[RESCHEDULE].token, path=path),
        CANCEL_URL_TAG: build_url(base, states[CANCEL].token, path=path),
    }
    template = str(
        (meeting_type or {}).get("title_template")
        or "{title}\n\nWhen: {start_at} ({timezone})\nWith: {host_email}\nWhere: {location}\n"
        "Need a different time? {reschedule_url}\nCan't make it? {cancel_url}"
    )
    description = template.format(
        title=data.get("title") or "Your meeting",
        start_at=data.get("start_at"),
        timezone=(meeting_type or {}).get("timezone") or "UTC",
        host_email=data.get("host_email") or (meeting_type or {}).get("host_email"),
        location=data.get("location") or "to be confirmed",
        reschedule_url=tags[RESCHEDULE_URL_TAG],
        cancel_url=tags[CANCEL_URL_TAG],
    )
    return {
        "booking_uid": data.get("uid") or booking.get("id"),
        "description": description,
        "tags": tags,
        "links": {kind: state.to_dict() for kind, state in states.items()},
        "template": template,
    }
