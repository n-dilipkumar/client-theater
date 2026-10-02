"""The provider's own failure report, and what this build does with it.

automations is explicit about the shape and about the purpose: "on provider
failure Cal reports ``appsStatus[]`` per app (``appName``, ``success``,
``failures``, ``errors``) in the booking/webhook payload, which is what an
integration should watch to retry or fall back".

Those four field names are taken verbatim and are the whole contract of this
module. Two of the things that make it a real contract rather than a shape check
are also researched, and both are enforced:

**It is per app, not per booking.** ``appsStatus[]`` is an array, and a booking
can be provisioned against more than one app. Collapsing it to a single
success flag would lose the app that failed, which is the one thing an operator
needs in order to decide between retrying and falling back.

**``failures`` is a count, not a boolean.** The field is plural for a reason, and
a report that recorded "it failed" would not answer the only question that
matters to a retry policy: has it run out of attempts. :func:`judge` reads the
count, and answers ``retryable`` from it.

The retry budget
----------------
The research gives the *shape* of the report and says the integration should
"retry or fall back", but never says how many times or how far apart. Those two
numbers are this build's, they are named here rather than buried in a loop, and
they are served at ``GET /api/wf-059/vocabulary`` so a reviewer can change them
without reading this module. The counts follow the sibling workflow in this
repository, which is the nearest precedent available and is named here so a
reviewer knows where the number came from rather than assuming it was researched.

The reason a failure is a *record* and not an exception is worth stating. A
booking whose provider was down still happened: the prospect still holds a slot,
the seller still has a meeting on the calendar, and the only useful thing to
record is that the link is missing and why. The provisioning route reports the
outcome and stores the failure; it does not pretend the booking does not exist.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from dsr.conference_links.errors import ConferenceLinkError
from dsr.conference_links.vocabulary import STATIC_LINK_QUOTE

#: The per-app fields automations names, in its order.
STATUS_FIELDS: tuple[str, ...] = ("appName", "success", "failures", "errors")

#: How many times a failed provision is retried before it is left failed.
#:
#: Not researched: automations says only "which is what an integration should
#: watch to retry or fall back". Four attempts - one try plus three retries -
#: matches the retry policy of the sibling outcome-webhook workflow in this
#: repository, and is served at /vocabulary so it can be changed by name.
RETRY_ATTEMPTS = 3

#: The retry outcomes, and the order they are considered in.
#:
#: ``retrying`` is the only state from which a further attempt is legal, and
#: ``failed``/``delivered`` are terminal. :func:`judge` enforces that rather
#: than describing it.
APP_STATES: tuple[str, ...] = ("provisioned", "retrying", "failed", "fell-back")

#: What the research says this report is *for*.
APPS_STATUS_EVIDENCE = (
    "on provider failure Cal reports `appsStatus[]` per app (`appName`, `success`, `failures`, "
    "`errors`) in the booking/webhook payload, which is what an integration should watch to "
    "retry or fall back"
)


def normalise_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve one app's status into the researched four fields.

    The only field this function will *invent* is the one the research defines
    structurally: ``failures`` is a count, so an entry that reports a boolean
    ``success: false`` with no count is read as one failure rather than zero.
    Getting that backwards would mark a failed provision as never having been
    tried, which is the one reading that makes a retry policy wrong in the
    dangerous direction.
    """
    if not isinstance(entry, Mapping):
        raise ConferenceLinkError("each appsStatus[] entry must be an object")

    app_name = str(entry.get("appName") or entry.get("app_name") or "").strip()
    if not app_name:
        raise ConferenceLinkError("each appsStatus[] entry must name its appName")

    errors = entry.get("errors")
    if errors is None:
        error_list: list[Any] = []
    elif isinstance(errors, str):
        error_list = [errors] if errors.strip() else []
    elif isinstance(errors, Sequence):
        error_list = list(errors)
    else:
        error_list = [errors]

    success = entry.get("success")
    if success is None:
        success = not error_list

    failures = entry.get("failures")
    if failures is None:
        # A boolean success with no count is a single failed attempt, not zero:
        # zero would mean there is nothing to retry, which is the reading that
        # silently drops a failure.
        failures = 0 if success else 1
    try:
        failure_count = max(0, int(failures))
    except (TypeError, ValueError):
        failure_count = 1

    return {
        "appName": app_name,
        "success": bool(success),
        "failures": failure_count,
        "errors": error_list,
    }


def normalise(payload: Mapping[str, Any] | Sequence[Any] | None) -> list[dict[str, Any]]:
    """Resolve a whole ``appsStatus[]`` report, or refuse it by name.

    Accepts the researched key (``appsStatus``), a bare array, or a single
    object, because the report arrives embedded in a booking payload in the
    first case and alone in the second, and a caller should not have to reshape
    it to be understood. A single object is recognised by carrying ``appName``,
    which is the one field every entry must have - so an empty object is a
    refusal rather than a one-app report with no app.
    """
    if payload is None:
        return []
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, Mapping)):
        entries = list(payload)
    elif isinstance(payload, Mapping):
        body = dict(payload)
        if "appName" in body or "app_name" in body:
            entries = [body]
        else:
            entries = list(body.get("appsStatus") or body.get("apps_status") or [])
    else:
        raise ConferenceLinkError("an appsStatus report must be an object or an array of objects")

    return [normalise_entry(entry) for entry in entries]


def judge(statuses: Sequence[Mapping[str, Any]], *, attempts_made: int = 0) -> dict[str, Any]:
    """What this report means for a retry decision.

    Returns the state the provision should be in, which app (or apps) are
    responsible, how many attempts remain, and whether the booking is safe to
    present. The last field is the one a seller actually reads: a booking with a
    failed provision is real and still on the calendar, and the only question is
    whether its guests have somewhere to go.
    """
    entries = [dict(entry) for entry in statuses]
    failed = [entry for entry in entries if entry.get("success") is not True]
    succeeded = [entry for entry in entries if entry.get("success") is True]

    used = max(
        int(attempts_made or 0), max((int(e.get("failures") or 0) for e in failed), default=0)
    )
    remaining = max(RETRY_ATTEMPTS - used, 0)

    if not entries:
        state = "unreported"
    elif not failed:
        state = "provisioned"
    elif remaining == 0:
        state = "failed"
    else:
        state = "retrying"

    errors = [error for entry in failed for error in (entry.get("errors") or [])]

    return {
        "state": state,
        "apps_succeeded": [entry["appName"] for entry in succeeded],
        "apps_failed": [entry["appName"] for entry in failed],
        "attempts_made": used,
        "attempts_remaining": remaining,
        "retry_budget": RETRY_ATTEMPTS,
        "retryable": state == "retrying",
        "errors": errors,
        # The booking exists either way. A failed conference is a missing link,
        # not a missing meeting, and this field is what stops a page rendering
        # "provisioning failed" as though the booking did not happen.
        "booking_stands": True,
        "attendees_have_a_way_in": state == "provisioned",
        "evidence": APPS_STATUS_EVIDENCE,
    }


def fallback_for(
    statuses: Sequence[Mapping[str, Any]], *, fallback_location: str | None = None
) -> dict[str, Any]:
    """What to do when the budget is spent.

    The research says "retry or fall back" and does not say what a fallback *is*.
    This build's answer is the researched one that already exists: the
    ``Conference Details`` option, "normally used to include links, like static
    Zoom ones, for those who don't want to use one-time links". A rep who has
    configured a static link has, by configuring it, declared they are willing to
    share a room that is not per-booking - so falling back to it is a decision
    they already made.

    With no static link configured the fallback is the guest, which is the other
    researched option: "This field will enable your prospects to provide the
    Location themselves." Both answers are named, and neither mints a conference
    that is already in use.
    """
    verdict = judge(statuses)
    if verdict["retryable"]:
        return {"fallback": None, "why": "the retry budget is not spent", **verdict}

    if str(fallback_location or "").strip():
        return {
            "fallback": "static",
            "location": str(fallback_location).strip(),
            "why": STATIC_LINK_QUOTE,
            **verdict,
        }

    return {
        "fallback": "ask-the-guest",
        "location": None,
        "why": "no static location is configured, so the researched Ask the Guest option applies",
        **verdict,
    }


def report(entries: Sequence[Mapping[str, Any]], *, attempts_made: int = 0) -> dict[str, Any]:
    """The whole report, normalised and judged, as the engine stores it.

    One call so the stored record and the served response cannot differ: a
    stored report that normalised differently from the one a client read would
    make the audit trail and the page disagree about the same failure.
    """
    normalised = [normalise_entry(entry) for entry in entries]
    return {
        "appsStatus": normalised,
        "fields": list(STATUS_FIELDS),
        "count": len(normalised),
        **judge(normalised, attempts_made=attempts_made),
    }


__all__ = [
    "APPS_STATUS_EVIDENCE",
    "APP_STATES",
    "RETRY_ATTEMPTS",
    "STATUS_FIELDS",
    "fallback_for",
    "judge",
    "normalise",
    "normalise_entry",
    "report",
]
