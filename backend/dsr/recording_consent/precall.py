"""Step 4's pre-call email: when it fires, who it goes to, and what it says.

"Gong automatically sends pre-call emails to external invitees between 10 and 20
minutes before the call, reminding them about the call and letting them know that
it will be recorded."

Three things the research fixes and this module implements literally:

* the window is a **range**, 10 to 20 minutes, not a point;
* the audience is **external invitees**, so an invitee on the organiser's own
  domain is not sent one;
* the email **carries the variables the research names** and no others, so an
  unknown ``{{token}}`` is refused rather than shipped.

The window is the part worth reading. Firing "at T-15" would be a cleaner
implementation and would be wrong twice over: it would invent a precision the
source does not claim, and it would make the recorded lead time a constant
rather than a measurement. :func:`should_send` therefore takes the clock from the
caller and records which end of the window it fired at.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from dsr.recording_consent import directory, vocabulary as vocab
from dsr.recording_consent.errors import ProfileInvalid

_TOKEN = re.compile(r"\{\{[^{}]+\}\}")

#: The disclosure the research quotes the pre-call email as making: it reminds the
#: participant about the call "and lets them know that it will be recorded".
RECORDING_DISCLOSURE = "This call will be recorded."


def _render(template: str, variables: dict[str, str]) -> str:
    """Substitute the researched variables, and leave anything else alone.

    ``validate_providers``'s caller already refused an unknown token at save
    time, so an unresolved token here means the profile changed under us. It is
    rendered as the token itself rather than as an empty string, because an email
    with a hole in it reads as a working email.
    """

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        return str(variables.get(token, token))

    return _TOKEN.sub(replace, template)


def is_external(invitee_email: str, organizer_email: str) -> bool:
    """Whether an invitee counts as external.

    Same domain means internal. That is a heuristic and it is the one the research
    implies, because it says "external invitees" without defining external; a
    company with two domains has to say which is the company.

    An address with no ``@`` has no domain to compare, so it is not external. A
    bare string splits to itself, which would compare unequal to any real domain
    and make a malformed address the *most* external thing in the list - so a
    booking carrying one would be sent a disclosure to nobody.
    """
    invitee = str(invitee_email or "").strip().lower()
    organizer = str(organizer_email or "").strip().lower()
    if "@" not in invitee or "@" not in organizer:
        return False
    invitee_domain = invitee.rsplit("@", 1)[-1]
    organizer_domain = organizer.rsplit("@", 1)[-1]
    if not invitee_domain or not organizer_domain:
        return False
    return invitee_domain != organizer_domain


def recipients(invitees: list[dict[str, str]], organizer_email: str) -> tuple[list[str], list[str]]:
    """Split the invitees into (external recipients, internal invitees).

    Both lists are returned rather than only the recipients, because an operator
    asking "why did Bob not get the email" needs the internal list to answer it.
    """
    external: list[str] = []
    internal: list[str] = []
    for invitee in invitees if isinstance(invitees, list) else []:
        address = directory.invitee_address(invitee)
        if not address:
            continue
        (external if is_external(address, organizer_email) else internal).append(address)
    return sorted(set(external)), sorted(set(internal))


def window(
    start_time: datetime, minutes: tuple[int, int] = vocab.PRECALL_WINDOW_MINUTES
) -> tuple[datetime, datetime]:
    """The absolute send window for one meeting.

    The research says "between 10 and 20 minutes before the call", and the lower
    bound is the later of the two instants: 20 minutes before is the window's
    opening edge and 10 minutes before is its closing edge. The bounds are sorted
    rather than trusted so that a caller passing them the other way round gets a
    window rather than an empty one.
    """
    early, late = sorted(minutes)
    return start_time - timedelta(minutes=late), start_time - timedelta(minutes=early)


def should_send(
    now: datetime, start_time: datetime, already_sent: bool = False
) -> tuple[bool, str]:
    """Whether the pre-call email is due, and why.

    ``already_sent`` is in the signature rather than read from the store, so this
    stays a pure function that a test can drive across the whole window. A second
    send is refused with the reason ``already_sent`` rather than silently skipped,
    so the caller records why not.

    The window's edges are inclusive on both ends. "Between 10 and 20 minutes"
    reads as inclusive, and the alternative - excluding the closing edge - would
    make a planner that polls once a minute miss exactly the minute the research
    describes.

    The reason string carries the measured lead time rather than naming an edge.
    A caller that named the edge would be asserting something the research does not
    claim: there is no "preferred" minute inside a 10-to-20-minute range, and the
    minute the planner actually fired at is the only fact worth keeping.
    """
    if already_sent:
        return False, "already_sent"
    opens, closes = window(start_time)
    if now < opens:
        return False, "before_window"
    if now > closes:
        return False, "after_window"
    minutes_out = round((start_time - now).total_seconds() / 60, 1)
    return True, f"in_window_at_t_minus_{minutes_out:g}min"


def render(
    profile: dict[str, Any],
    *,
    sender_name: str,
    sender_company: str,
    meeting_title: str,
    meeting_hour: str,
    to: list[str],
    now_iso: str,
) -> dict[str, Any]:
    """Compose the pre-call email and record the variables it was rendered from.

    The rendered body and the variable map are both stored. The rendered body is
    what a participant received and is the evidence; the variable map is what a
    reviewer needs to reproduce it.
    """
    email = profile.get("precall_email") or {}
    prompt = profile.get("audio_prompt") or {}

    subject_template = str(email.get("subject") or "")
    body_template = str(email.get("body") or "")
    if not subject_template or not body_template:
        raise ProfileInvalid(
            "The pre-call email has no subject or no body.",
            {"precall_email.subject": "is required", "precall_email.body": "is required"},
        )

    variables = {
        "{{sender_name}}": sender_name,
        "{{sender_company}}": sender_company,
        "{{meeting_title}}": meeting_title,
        "{{meeting_hour}}": meeting_hour,
    }
    unknown = sorted(set(_TOKEN.findall(subject_template + body_template)) - set(variables))
    if unknown:
        raise ProfileInvalid(
            "The pre-call email uses variables this workflow does not render: "
            + ", ".join(unknown),
            {"precall_email.tokens": "unsupported variable: " + ", ".join(unknown)},
        )

    signature = str(email.get("signature") or "")
    legal_footer = str(email.get("legal_footer") or "")
    body = _render(body_template, variables)
    if signature:
        body = f"{body}\n\n{signature}"
    if legal_footer:
        body = f"{body}\n\n{legal_footer}"

    return {
        "profile_id": profile.get("id"),
        "to": list(to),
        "subject": _render(subject_template, variables),
        "body": body,
        "variables": variables,
        "disclosure": RECORDING_DISCLOSURE,
        "audio_prompt_text": prompt.get("text") or "",
        "sent_at": now_iso,
        "status": "sent",
    }
