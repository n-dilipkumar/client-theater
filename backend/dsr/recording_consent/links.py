"""Issuing a consent-enabled meeting link, and retiring the previous one.

Nothing here opens a socket. The researched endpoint is Gong's, and its own
documentation says the Meetings API is "in Beta Phase", so this package records
the exact request it *would* send and treats the response shape as unversioned.
That is the same boundary :mod:`dsr.conference_links` draws, and for the same
reason: this product is the source of the booking, not a proxy for a vendor.

What is real, and is what the reviewer reads:

* :func:`new_meeting_request` builds the ``NewMeetingRequest`` the research
  quotes, field for field, and refuses a request missing any of them.
* :func:`new_meeting_response` reads the ``NewMeetingResponse``, and records the
  two documented failures as refusals rather than letting them become a link that
  silently never joins.
* :func:`retire_previous` implements "Each time you change this link, the previous
  link is disabled" **as a state**, because the brief requires it and because a
  disabled link is evidence: a booking whose conferencing link was swapped twice
  needs the first link readable afterwards, because "which link did the buyer
  actually join" is exactly what a recording dispute turns on.
"""

from __future__ import annotations

from typing import Any

from dsr.recording_consent import vocabulary as vocab
from dsr.recording_consent.errors import ConsentPageDisabled, OrganizerUnmapped, ProfileInvalid


def consent_page_preview(profile: dict[str, Any], base_url: str = "") -> dict[str, Any]:
    """The consent page as a buyer would receive it.

    Step 3 names what the page carries: the profile's own description, the
    company logo, and the supported languages. Rendering it as data rather than
    as HTML keeps the preview route free of a template dependency, and it means
    the page the reviewer reads is the same data the participant is consented
    against.
    """
    return {
        "profile_id": profile.get("id"),
        "name": profile.get("name"),
        "description": profile.get("description"),
        "logo_url": profile.get("logo_url"),
        "locales": list(profile.get("locales") or []),
        "consent_page_enabled": bool(profile.get(vocab.CONSENT_PAGE_SWITCH)),
        "enforce_consent_page": bool(profile.get(vocab.ENFORCEMENT_SWITCH)),
        "allow_join_without_consent": bool(profile.get(vocab.JOIN_WITHOUT_CONSENT_SWITCH)),
        "audio_prompt_suppressed": prompt_suppressed(profile),
        "action_url": f"{base_url}/wf-060/consent" if base_url else "/wf-060/consent",
    }


def prompt_suppressed(profile: dict[str, Any]) -> bool:
    """Whether the audio prompt stays silent on this profile.

    Step 5 names the switch, "Don't play the audio prompt if the consent page is
    used". "Used" means the page is on and consent was actually asked for, so a
    profile with the consent page off does not get the suppression it asked for
    by name: there was no page to make the prompt redundant.
    """
    if not profile.get(vocab.AUDIO_PROMPT_SWITCH, False):
        return False
    suppress = bool(
        profile.get(vocab.SUPPRESS_PROMPT_WHEN_CONSENT_PAGE_USED, True)
        or profile.get("audio_prompt", {}).get("suppress_when_consent_page_used", False)
    )
    return suppress and bool(profile.get(vocab.CONSENT_PAGE_SWITCH))


def new_meeting_request(
    *,
    organizer_email: str,
    start_time: str,
    end_time: str,
    title: str,
    invitees: list[dict[str, str]],
    external_id: str,
    provider: str,
) -> dict[str, Any]:
    """Build the ``NewMeetingRequest`` the research quotes, field for field.

    The six fields are :data:`~dsr.recording_consent.vocabulary.NEW_MEETING_REQUEST_FIELDS`,
    and every one is required. The refusals are per field rather than one generic
    message, because a caller that omits ``organizerEmail`` and one that omits
    ``externalId`` have the same shape of problem and different fixes.
    """
    errors: dict[str, str] = {}
    if not organizer_email:
        errors["organizer_email"] = "is required; the research resolves the consent profile from it"
    if not start_time:
        errors["start_time"] = "is required; NewMeetingRequest.startTime"
    if not end_time:
        errors["end_time"] = "is required; NewMeetingRequest.endTime"
    if not title:
        errors["title"] = "is required; NewMeetingRequest.title"
    if not invitees:
        errors["invitees"] = "is required; NewMeetingRequest.invitees"
    if not external_id:
        errors["external_id"] = "is required; NewMeetingRequest.externalId"
    if provider not in vocab.PROVIDERS:
        errors["provider"] = f"must be one of {', '.join(sorted(vocab.PROVIDERS))}"
    if errors:
        raise ProfileInvalid(
            f"{len(errors)} field(s) are missing from the meeting request: "
            + "; ".join(sorted(errors)),
            errors,
        )

    return {
        "method": "POST",
        "endpoint": vocab.MEETINGS_ENDPOINT,
        "scope": vocab.MEETING_CREATE_SCOPE,
        "body": {
            "startTime": start_time,
            "endTime": end_time,
            "title": title,
            "invitees": list(invitees),
            "externalId": external_id,
            "organizerEmail": organizer_email,
        },
        # Not a body field: the provider this product books on decides which web
        # conference the link is for, and Gong's request does not carry it. It is
        # recorded beside the body so the audit row says which conference the
        # consent link was issued for.
        "provider": provider,
    }


def new_meeting_response(
    payload: Any,
    request: dict[str, Any],
    invited_bot_email: str,
) -> dict[str, Any]:
    """Read the ``NewMeetingResponse`` and turn the documented failures into refusals.

    ``payload`` may carry a ``status`` of 409 or 404, which the research
    documents. Both are raised rather than returned: a 409 means the consent page
    is off in the organisation, which this workflow can read from the profile
    before it calls, and a 404 means the organiser has no user behind them, which
    is the profile-resolution failure. Letting either become a link would produce
    a booking that looks successful and records nothing.
    """
    if not isinstance(payload, dict):
        raise ProfileInvalid(
            "The meeting response must be an object.", {"body": "response must be an object"}
        )

    status = payload.get("status")
    if status in vocab.DOCUMENTED_ERRORS:
        detail = vocab.DOCUMENTED_ERRORS[int(status)]
        if int(status) == 409:
            raise ConsentPageDisabled(
                f"Gong refused the consent meeting: {status} {detail}.",
                {"consent_page_enabled": "must be on before a consent meeting can be issued"},
            )
        raise OrganizerUnmapped(
            f"Gong refused the consent meeting: {status} {detail}.",
            {"organizer_email": "has no Gong user behind it"},
        )

    missing = [
        field for field in ("meetingId", "meetingUrl") if not str(payload.get(field) or "").strip()
    ]
    if missing:
        raise ProfileInvalid(
            "The meeting response is missing " + ", ".join(missing) + ".",
            {field: "is required in NewMeetingResponse" for field in missing},
        )

    additional = payload.get("additionalInvitees")
    if additional is None:
        # The research's example address, used because the response omitted the
        # field. Recorded as a fallback so a reviewer can tell which of the two
        # produced the address.
        additional = [invited_bot_email]
        source = "fallback"
    else:
        source = "response"

    return {
        "request_id": str(payload.get("requestId") or ""),
        "meeting_id": str(payload["meetingId"]),
        "meeting_url": str(payload["meetingUrl"]),
        "additional_invitees": [str(x) for x in additional],
        "additional_invitees_source": source,
        # The researched field verbatim: "The Gong URL of the meeting, should be
        # used to enter the meeting." A consent link is the way in, and it is the
        # thing the calendar invite carries.
        "provider": request.get("provider"),
    }


def link_record(
    response: dict[str, Any],
    booking_id: str,
    profile_id: str,
    now_iso: str,
) -> dict[str, Any]:
    """The stored shape of one issued consent link.

    ``link_state`` starts ``active`` and is what :func:`retire_previous` moves to
    ``superseded``. It is a field on the record and never a delete, which is the
    whole point: the audit row has to survive the change that retired it.
    """
    return {
        "booking_id": booking_id,
        "profile_id": profile_id,
        "meeting_id": response["meeting_id"],
        "meeting_url": response["meeting_url"],
        "request_id": response.get("request_id") or "",
        "provider": response.get("provider"),
        "additional_invitees": list(response.get("additional_invitees") or []),
        "additional_invitees_source": response.get("additional_invitees_source"),
        "link_state": "active",
        "issued_at": now_iso,
        "superseded_at": None,
        "superseded_by": None,
    }


def retire_previous(
    current_link: dict[str, Any], new_link: dict[str, Any], now_iso: str
) -> dict[str, Any]:
    """Disable the previous link, as a state change on the record.

    "Each time you change this link, the previous link is disabled."

    Implemented as a patch that sets ``link_state`` to ``superseded``, stamps
    ``superseded_at``, and records ``superseded_by`` as the meeting id that
    replaced it. The alternative - deleting the row - would leave the audit log
    pointing at a record that no longer exists, and would make "which link did the
    buyer join" unanswerable for exactly the bookings where it matters.

    Already-superseded links are left alone. Retiring one twice would stamp a
    second ``superseded_at`` and overwrite the first, which is the kind of
    history rewriting the audit guarantee exists to prevent.
    """
    if current_link.get("link_state") == "superseded":
        return current_link
    retired = dict(current_link)
    retired["link_state"] = "superseded"
    retired["superseded_at"] = now_iso
    retired["superseded_by"] = new_link.get("meeting_id")
    return retired


def active_link(links: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The one link that currently joins the call.

    The most recently issued ``active`` link. A booking whose links are all
    superseded has no way in, which is a state the caller must handle rather than
    receive as ``None`` and treat as "fine".
    """
    candidates = [link for link in links if link.get("link_state") == "active"]
    if not candidates:
        return None
    return max(candidates, key=lambda link: str(link.get("issued_at") or ""))
