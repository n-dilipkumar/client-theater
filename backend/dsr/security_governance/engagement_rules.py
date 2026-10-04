"""The rules WF-075 enforces: counting, bounds, dwell and verification.

Every rule here is the researched specification for WF-075 made executable. The
specification is ``docs/research/digital-sales-room-workflows/wf/WF-075.md``, quoted in
full in issue 150, and the docstring on each rule names the evidence it came from.

The four rules the rest of this workflow leans on
-------------------------------------------------

**A visitor and a view are different things, and neither count is derived from the
other.** The specification is explicit: "a viewer who hits two links in the same
dataroom shows up once here, but twice in ``papermark views list``". So
:func:`count_visitors` reads the visitor rows and :func:`count_views` reads the view
rows, and neither calls the other. A summary that derived unique visitors by
collapsing views would answer a question the rep did not ask.

**Anonymous views stay reachable.** The specification says views "never tied to a
``Visitor`` record are still reachable per-link via ``GET /v1/links/{id}/views``". A
view row with no visitor email is therefore a normal row, not an error and not
something to filter out of a total.

**``verified`` means proven, not typed.** The user flow reads the flag "to confirm the
identity was actually proven (not merely typed in)". :func:`verification_state` has
three answers, and the absent field is one of them. A row whose ``verified`` key is
missing reads as ``unknown``, which is the honest answer and is not the same as
``unverified``.

**Times are Unix milliseconds.** The user flow names the ``--since`` / ``--until``
bounds as "Unix ms", and the same unit is used inside the records. :func:`coerce_ms`
is the only place that converts, so there is exactly one unit check in the workflow.

What this module does not decide
--------------------------------

Whether a view event happened. This workflow is a read path. The specification's data
flow says every view "writes a ``View`` row" and "updates a ``Visitor`` row", but that
producer is not this ticket, so nothing here creates a view out of nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from dsr.security_governance import engagement as vocab


class EngagementError(ValueError):
    """A bound or a filter this workflow will not accept.

    Carries a field-keyed map, because the page puts each message beside the input
    that caused it rather than in one combined sentence.
    """

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})


class VisitorNotFound(LookupError):
    """No such visitor, or it was never a visitor this workflow owns.

    Its own type rather than the store's ``RecordNotFound``: a feature may only map
    error types it raises itself, because a handler for a shared type would intercept
    that exception across the whole product.
    """


class ViewNotFound(LookupError):
    """No such view. Same reasoning as :class:`VisitorNotFound`."""


# --------------------------------------------------------------------------- #
# Time: Unix milliseconds, and only Unix milliseconds
# --------------------------------------------------------------------------- #


def coerce_ms(value: Any, field: str) -> int | None:
    """One time bound as Unix milliseconds, or ``None`` when the caller gave none.

    ``None`` is a real answer rather than an error: an omitted ``since`` means "from
    the beginning" and an omitted ``until`` means "up to now", which is what a poller
    that asks for the whole retained window means.

    A string is accepted because the CLI spells the bounds as ``--since 1700000000000``
    and a caller pasting that value into a query parameter sends text. A float is
    accepted because a millisecond timestamp divided in JavaScript is a float, and
    refusing the result of the caller's own arithmetic would be pedantic.

    A value that is not a number at all is a validation failure, not a silent zero.
    ``since=0`` means the epoch and is a legitimate bound, so it cannot stand in for
    "unparseable".
    """

    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise EngagementError(
            f"{field} must be a Unix millisecond timestamp.",
            {field: f"{field} must be a Unix millisecond timestamp."},
        )
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise EngagementError(
            f"{field} must be a Unix millisecond timestamp.",
            {field: f"{field} must be Unix milliseconds, for example 1759600000000."},
        ) from exc
    if number != number or number in (float("inf"), float("-inf")):  # NaN and infinity
        raise EngagementError(
            f"{field} must be a Unix millisecond timestamp.",
            {field: f"{field} must be Unix milliseconds, for example 1759600000000."},
        )
    return int(number)


def window(since: Any = None, until: Any = None) -> dict[str, int | None]:
    """The two bounds as one mapping, validated.

    The order is checked rather than swapped. ``since`` after ``until`` names an empty
    range, and returning it as an empty range would show a rep a board of zeros for a
    window that cannot contain a view, which reads as "nobody engaged" rather than as
    the mistake it is.
    """

    low = coerce_ms(since, "since")
    high = coerce_ms(until, "until")
    if low is not None and high is not None and low > high:
        raise EngagementError(
            "The window ends before it begins.",
            {"since": f"since ({low}) is later than until ({high})."},
        )
    return {"since": low, "until": high}


def in_window(value: Any, bounds: Mapping[str, int | None]) -> bool:
    """Whether one stored ``viewed_at`` falls inside ``bounds``.

    A row with no ``viewed_at`` is inside an unbounded window and outside a bounded
    one. A view with no timestamp cannot be placed in a window the caller asked for, so
    it is excluded from that window rather than guessed into it. That keeps a
    ``--since`` answer honest: it describes views that are known to fall after the
    bound.
    """

    moment = coerce_ms(value, "viewed_at")
    if moment is None:
        return bounds.get("since") is None and bounds.get("until") is None
    low = bounds.get("since")
    high = bounds.get("until")
    if low is not None and moment < low:
        return False
    if high is not None and moment > high:
        return False
    return True


# --------------------------------------------------------------------------- #
# Verification: three states, and the third one matters
# --------------------------------------------------------------------------- #


def verification_state(data: Mapping[str, Any]) -> str:
    """What a visitor row says about proof of identity.

    Three answers, and the absent field is one of them. The evidence fixes the stored
    type as ``verified: boolean``, so this does not invent a third stored value: it
    reports the three states a stored boolean plus an absent key can be in.

    ``unverified`` is the default rather than ``verified`` because the user flow reads
    the flag "to confirm the identity was actually proven (not merely typed in)". A
    boolean defaulting to true on a typed address would answer the question wrongly on
    every row that has not been through a proof step, which is most of them.
    """

    if vocab.VERIFIED not in data:
        return vocab.VERIFIED_UNKNOWN
    value = data[vocab.VERIFIED]
    if value is None:
        return vocab.VERIFIED_UNKNOWN
    if isinstance(value, bool):
        return vocab.VERIFIED_TRUE if value else vocab.VERIFIED_FALSE
    # A JSON round trip through a client that stored 1 or 0 for a boolean. Only 0 and
    # 1 are accepted, so "2" stays a non-boolean rather than becoming true.
    if isinstance(value, int) and value in (0, 1):
        return vocab.VERIFIED_TRUE if value == 1 else vocab.VERIFIED_FALSE
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "verified", "yes", "proven"):
            return vocab.VERIFIED_TRUE
        if lowered in ("false", "unverified", "no"):
            return vocab.VERIFIED_FALSE
    return vocab.VERIFIED_UNKNOWN


# --------------------------------------------------------------------------- #
# Counting: the two counts stay apart
# --------------------------------------------------------------------------- #


def count_views(views: Sequence[Mapping[str, Any]], bounds: Mapping[str, int | None] | None = None):
    """How many view events are in ``views``, optionally inside a window.

    Reads the view rows only. It never calls :func:`count_visitors`, and that is the
    specification's sentence made into a rule: a viewer who hits two links shows up
    once in the viewers list and twice in the views list.
    """

    if not bounds:
        return len(views)
    return sum(1 for view in views if in_window(view.get(vocab.VIEWED_AT), bounds))


def unique_visitors(views: Iterable[Mapping[str, Any]]) -> list[str]:
    """The distinct viewer addresses behind ``views``, sorted.

    This is the only place a distinct count is derived from views, and it exists for
    the one case the specification describes: unique visitors *within a window of
    views*. The visitors list itself is counted from visitor rows, because a visitor
    who was invited and never opened a link is still a row a rep can read.
    """

    addresses = {
        str(view.get(vocab.VIEWER_EMAIL)).strip().lower()
        for view in views
        if str(view.get(vocab.VIEWER_EMAIL) or "").strip()
    }
    return sorted(addresses)


def tally(rows: Iterable[Mapping[str, Any]], key: str) -> dict[str, int]:
    """How many rows carry each value for ``key``, largest first then alphabetical.

    Sorted here so the page and the API agree on the order without either of them
    re-sorting, which is the kind of small disagreement that becomes a flaky test.
    An absent key counts as ``unknown`` rather than being dropped, because a row with
    no ``view_type`` is a state the board should be able to name.
    """

    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


# --------------------------------------------------------------------------- #
# Dwell time
# --------------------------------------------------------------------------- #


def page_durations(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The per-page dwell list, normalised and sorted by page number.

    The evidence gives the shape verbatim: ``page_durations[{page_number,
    duration_seconds}]``. Sorting by page number rather than trusting arrival order
    matters because the page renders this as a timeline, and a timeline that runs
    backwards is the kind of thing a rep reads as a viewer reading backwards.

    A negative duration is dropped rather than clamped to zero. A negative number
    means the client's clock moved between two readings, and reporting it as zero
    would invent a dwell of no time; reporting it at all would be a lie of a different
    kind.
    """

    raw = data.get(vocab.PAGE_DURATIONS)
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    rows: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            continue
        number = coerce_ms(entry.get("page_number"), "page_number")
        seconds = coerce_ms(entry.get("duration_seconds"), "duration_seconds")
        if number is None or seconds is None or seconds < 0:
            continue
        rows.append(
            {
                "page_number": number,
                "duration_seconds": seconds,
                "order": index,
            }
        )
    rows.sort(key=lambda row: (row["page_number"], row["order"]))
    return [
        {"page_number": row["page_number"], "duration_seconds": row["duration_seconds"]}
        for row in rows
    ]


def total_duration(data: Mapping[str, Any]) -> int:
    """The whole-view dwell in seconds.

    The stored total is used when it is present and it is at least the sum of the
    pages, and the sum is used otherwise. Two reasons for the first branch: the
    evidence gives ``total_duration_seconds`` as its own field, so it is the source
    when the source has one, and a client that stops sending after the viewer leaves
    still has a total the per-page list never adds up to.

    When neither is usable the answer is zero rather than a guess. Time spent that is
    not known is not time spent.
    """

    pages = page_durations(data)
    summed = sum(row["duration_seconds"] for row in pages)
    stored = coerce_ms(data.get(vocab.TOTAL_DURATION_SECONDS), vocab.TOTAL_DURATION_SECONDS)
    if stored is not None and stored >= summed:
        return stored
    return summed


def location_of(data: Mapping[str, Any]) -> dict[str, str]:
    """``{country, city}``, with each part present as a key even when it is unknown.

    The vendor is not named anywhere. The specification marks it as an inference and
    says so itself: "viewer IP -> geolocation provider ``[inferred - the API returns
    location.country/city but names no vendor]``". A key that is missing entirely and a
    key whose value is the empty string are different answers, and the page renders
    them differently, so both keys are always present here.
    """

    raw = data.get(vocab.LOCATION)
    source = raw if isinstance(raw, Mapping) else {}
    return {
        "country": str(source.get("country") or "") or "unknown",
        "city": str(source.get("city") or "") or "unknown",
    }


def client_of(data: Mapping[str, Any]) -> dict[str, str]:
    """``{browser, os, device}``, each part present as a key even when it is unknown.

    The user-agent string is the sourced input; the three parsed parts are the
    evidence's output shape. Parsing a user agent is not this workflow's job, so the
    parts are read from the record and a record that carries only the raw string
    reports the parts as unknown rather than claiming a parse this build did not do.
    """

    raw = data.get(vocab.CLIENT)
    source = raw if isinstance(raw, Mapping) else {}
    result: dict[str, str] = {}
    for field in vocab.CLIENT_FIELDS:
        result[field] = str(source.get(field) or "") or "unknown"
    return result


# --------------------------------------------------------------------------- #
# Downloads
# --------------------------------------------------------------------------- #


def download_of(data: Mapping[str, Any]) -> dict[str, Any]:
    """What the view record says about a download, in the shape the page renders.

    The distinction the specification's fourth user-flow step is built on is between a
    view that downloaded nothing and a view that downloaded something this build cannot
    name. :data:`~dsr.security_governance.engagement.NO_DOWNLOAD` is that first answer,
    and it is not a ``download_type`` value, so it cannot be stored by accident.
    """

    kind = data.get(vocab.DOWNLOAD_TYPE)
    at = coerce_ms(data.get(vocab.DOWNLOADED_AT), vocab.DOWNLOADED_AT)
    if not kind:
        return {
            "downloaded": False,
            "download_type": vocab.NO_DOWNLOAD,
            "downloaded_at": None,
        }
    return {
        "downloaded": at is not None,
        "download_type": str(kind),
        "downloaded_at": at,
    }


# --------------------------------------------------------------------------- #
# Paging
# --------------------------------------------------------------------------- #


def page_size(value: Any = None) -> int:
    """How many rows one read returns, clamped to the researched bounds.

    The specification calls the list endpoints paginated, so a page size is part of the
    contract rather than an implementation detail. The default and the maximum are
    derived and recorded as ``DERIVED_PAGE_SIZE``; the clamp is what stops a poller
    asking for the whole table in one request and then reading it as a page.
    """

    if value is None or value == "":
        return vocab.DEFAULT_PAGE_SIZE
    number = coerce_ms(value, "limit")
    if number is None:
        return vocab.DEFAULT_PAGE_SIZE
    if number < 1:
        raise EngagementError(
            "A page size must be at least 1.",
            {"limit": "A page size must be at least 1."},
        )
    return min(number, vocab.MAX_PAGE_SIZE)


def reverse_chronological(
    views: Sequence[Mapping[str, Any]], limit: int | None = None
) -> list[Mapping[str, Any]]:
    """The views newest first, which is the order the specification names.

    "Rep lists view records for a specific link in reverse-chronological order", so
    the sort is the contract and not a display choice. Rows with no ``viewed_at`` go
    last rather than first, because an undated view is not the newest thing that
    happened and putting it at the top would tell a rep it is.
    """

    ordered = sorted(
        views,
        key=lambda view: (
            coerce_ms(view.get(vocab.VIEWED_AT), vocab.VIEWED_AT) is None,
            -(coerce_ms(view.get(vocab.VIEWED_AT), vocab.VIEWED_AT) or 0),
        ),
    )
    if limit is None:
        return ordered
    return ordered[:limit]


# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #
#
# Two formats, and the distinction is deliberate rather than accidental.
#
# Every value this workflow stores or accepts at a boundary is Unix **milliseconds**,
# because the specification puts the ``--since`` / ``--until`` bounds there. The ISO
# 8601 stamp below is for a human reading the audit log or the record list, where a
# raw millisecond number is unreadable. :func:`utcnow_ms` is the only place the two are
# produced together, so no caller has to remember which one it wanted.


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_ms() -> int:
    """The current instant as Unix milliseconds, the unit this workflow stores."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def stamp(moment: datetime | None = None) -> str:
    """An ISO 8601 instant in UTC, with the milliseconds kept.

    The milliseconds are kept because two views can land inside one second and a log
    that rounds them together cannot tell which came first.
    """

    return (moment or utcnow()).astimezone(timezone.utc).isoformat(timespec="milliseconds")
