"""Threshold evaluation: the one place a measurement becomes a buying signal.

The research names five threshold inputs and quotes one number for one of them.
This module turns a set of measurements into the crossings, and it is pure: no
database, no clock, no framework. That is what makes the rule set testable by
reading it, and it is why the engine in :mod:`dsr.intent_routing.engine` can call
it without knowing where the numbers came from.

The shape of the answer matters as much as the shape of the question. A crossing
says what was observed, what it had to beat, and which of the five inputs it came
from, so a rep reading an alert can see the arithmetic rather than being told that
something happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from dsr.intent_routing.errors import InvalidEngagement, UnknownEngagementField
from dsr.intent_routing.vocabulary import (
    THRESHOLD_KINDS,
    THRESHOLDS,
    THRESHOLDS_TO_ALERT,
    WINDOW_HOURS,
)

#: Every field an engagement observation may carry. The five measured quantities,
#: plus the fields that say who, where and when, plus one display-only label.
ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "company_key",
        "room_id",
        "pages",
        "dwell_seconds",
        "total_dwell_seconds",
        "revisits",
        "downloads",
        "demo_interactions",
        "stakeholder",
        "stakeholder_role",
        "room_label",
        "last_seen_at",
        "window_hours",
    }
)

#: ``room_label`` is the one allowed field that no threshold reads. It is the name a
#: caller has for a room, and it exists so an alert subject can name the room rather
#: than printing an id at a rep. It is deliberately kept in the observation rather
#: than looked up from the room record, because a rep reads the subject in a
#: notification and an id there is noise.
DISPLAY_ONLY_FIELDS: tuple[str, ...] = ("room_label",)

#: The five measured quantities, and the ones that must be whole numbers of at
#: least zero. ``pages`` is excluded because it is a list, not a count.
COUNT_FIELDS: tuple[str, ...] = (
    "dwell_seconds",
    "total_dwell_seconds",
    "revisits",
    "downloads",
    "demo_interactions",
)

#: The fields without which no crossing can be routed. A company says who, a room
#: says where, and a moment says whether the reading is inside the window.
REQUIRED_FIELDS: tuple[str, ...] = ("company_key", "room_id")


@dataclass(frozen=True)
class Engagement:
    """What one company did in one room inside one evaluation window.

    Frozen because a threshold evaluation that could be changed halfway through by
    a caller is not reproducible, and the evaluation window is precisely the kind
    of thing two readers of the same alert will disagree about.
    """

    company_key: str
    room_id: str
    pages: tuple[str, ...] = ()
    dwell_seconds: int = 0
    total_dwell_seconds: int = 0
    revisits: int = 0
    downloads: int = 0
    demo_interactions: int = 0
    stakeholder: str = ""
    stakeholder_role: str = ""
    room_label: str = ""
    last_seen_at: datetime | None = None
    window_hours: int = WINDOW_HOURS
    crossed: tuple["Crossing", ...] = field(default=(), compare=False)

    @property
    def qualifies(self) -> bool:
        """Whether this engagement is worth telling anyone about.

        One crossing is enough. The research describes a single qualifying action
        raising a signal, and a rule that made a rep clear several bars would lose
        the moment the research is describing.
        """
        return len(self.crossed) >= THRESHOLDS_TO_ALERT

    @property
    def company_name_key(self) -> str:
        return self.company_key.lower()

    def measurements(self) -> dict[str, int]:
        """The five inputs as numbers, for the alert payload and for the tests.

        ``pages`` is reported as its count rather than as its list, because the
        threshold is on how many pages there were and the list itself travels in
        the payload under its own name.
        """
        return {
            "pages": len(self.pages),
            "dwell_seconds": self.dwell_seconds,
            "revisits": self.revisits,
            "downloads": self.downloads,
            "demo_interactions": self.demo_interactions,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_key": self.company_key,
            "room_id": self.room_id,
            "pages": list(self.pages),
            "dwell_seconds": self.dwell_seconds,
            "total_dwell_seconds": self.total_dwell_seconds,
            "revisits": self.revisits,
            "downloads": self.downloads,
            "demo_interactions": self.demo_interactions,
            "stakeholder": self.stakeholder,
            "stakeholder_role": self.stakeholder_role,
            "room_label": self.room_label,
            "last_seen_at": _iso(self.last_seen_at),
            "window_hours": self.window_hours,
            "measurements": self.measurements(),
            "crossed": [entry.as_dict() for entry in self.crossed],
            "qualifies": self.qualifies,
        }


@dataclass(frozen=True)
class Crossing:
    """One threshold that was met or beaten.

    ``observed`` and ``threshold`` are both reported because an alert that says
    only "a signal fired" cannot be argued with, and a rep who cannot check the
    arithmetic stops reading the alerts.
    """

    kind: str
    label: str
    unit: str
    observed: int
    threshold: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "unit": self.unit,
            "observed": self.observed,
            "threshold": self.threshold,
            "margin": self.observed - self.threshold,
        }


#: The measure each threshold kind reads, and how to read it out of an
#: engagement. Kept beside the evaluation so a new threshold is one entry rather
#: than an edit in three places.
_MEASURES: dict[str, tuple[str, str]] = {
    "dwell": ("dwell_seconds", "int"),
    "pages": ("pages", "count"),
    "revisit": ("revisits", "int"),
    "download": ("downloads", "int"),
    "demo_interaction": ("demo_interactions", "int"),
}


def _iso(moment: datetime | None) -> str:
    return moment.isoformat() if moment else ""


def _as_int(value: Any, field_name: str) -> int:
    """Read a whole number of at least zero.

    A float is refused rather than rounded. A dwell reading of 89.7 seconds is a
    real measurement, and rounding it up to 90 would fire the one threshold this
    workflow actually sourced.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidEngagement(f"{field_name} must be a number of seconds or of events")
    if isinstance(value, float) and not value.is_integer():
        raise InvalidEngagement(f"{field_name} must be a whole number, not {value!r}")
    number = int(value)
    if number < 0:
        raise InvalidEngagement(f"{field_name} must not be negative, got {number}")
    return number


def _as_text(value: Any, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise InvalidEngagement(f"{field_name} must be text")
    return value.strip()


def parse_engagement(payload: Any, *, now: datetime | None = None) -> Engagement:
    """Read an engagement observation, or refuse it by name.

    The field list is closed. A caller that sends a field nobody thresholds on is
    refused, because the measured value of a crossing is the whole of this
    workflow's output and an unthresholded field on the record reads later like a
    field that was.
    """
    if not isinstance(payload, dict):
        raise InvalidEngagement("an engagement observation must be an object")

    unknown = sorted(set(payload) - ALLOWED_FIELDS)
    if unknown:
        raise UnknownEngagementField(
            f"the observation carries field(s) {', '.join(unknown)}; "
            f"this workflow thresholds on {', '.join(sorted(COUNT_FIELDS))} and pages"
        )

    for name in REQUIRED_FIELDS:
        if not _as_text(payload.get(name), name):
            raise InvalidEngagement(f"{name} is required")

    raw_pages = payload.get("pages") or []
    if isinstance(raw_pages, str) or not isinstance(raw_pages, (list, tuple)):
        raise InvalidEngagement("pages must be a list of paths")
    pages: list[str] = []
    for entry in raw_pages:
        path = _as_text(entry, "pages")
        if not path:
            raise InvalidEngagement("pages must not contain a blank path")
        if not path.startswith("/"):
            raise InvalidEngagement(f"a page is a path beginning with a slash, got {path!r}")
        if path not in pages:
            # Distinct pages, in the order the buyer reached them. The order is
            # kept because "which pages" is one of the three researched facts and
            # a set would lose the reading order that makes it legible.
            pages.append(path)

    window_hours = _as_int(payload.get("window_hours", WINDOW_HOURS), "window_hours")
    if window_hours <= 0:
        raise InvalidEngagement("window_hours must be at least 1")

    counts = {
        name: _as_int(payload.get(name, 0), name)
        for name in COUNT_FIELDS
        if name not in ("dwell_seconds", "total_dwell_seconds")
    }
    dwell = _as_int(payload.get("dwell_seconds", 0), "dwell_seconds")
    total_dwell = _as_int(payload.get("total_dwell_seconds", dwell), "total_dwell_seconds")
    if dwell > total_dwell:
        raise InvalidEngagement(
            f"dwell_seconds ({dwell}) cannot exceed total_dwell_seconds ({total_dwell})"
        )

    last_seen = _as_moment(payload.get("last_seen_at"), now)

    return Engagement(
        company_key=_as_text(payload["company_key"], "company_key"),
        room_id=_as_text(payload["room_id"], "room_id"),
        pages=tuple(pages),
        dwell_seconds=dwell,
        total_dwell_seconds=total_dwell,
        stakeholder=_as_text(payload.get("stakeholder"), "stakeholder"),
        stakeholder_role=_as_text(payload.get("stakeholder_role"), "stakeholder_role"),
        room_label=_as_text(payload.get("room_label"), "room_label"),
        last_seen_at=last_seen,
        window_hours=window_hours,
        **counts,
    )


def _as_moment(value: Any, now: datetime | None) -> datetime:
    """Read an ISO 8601 moment, defaulting to now.

    A moment without an offset is refused for the same reason the
    visitor-identification capture refuses one: this workflow compares it against a
    window, and an unzoned time has no place in a window.
    """
    reference = now or datetime.now(timezone.utc)
    if value is None or value == "":
        return reference
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError as exc:
            raise InvalidEngagement(f"last_seen_at is not an ISO 8601 moment: {exc}") from exc
    else:
        raise InvalidEngagement("last_seen_at must be an ISO 8601 string")
    if moment.tzinfo is None:
        raise InvalidEngagement("last_seen_at carries no timezone offset")
    return moment.astimezone(timezone.utc)


def evaluate(engagement: Engagement, *, now: datetime | None = None) -> Engagement:
    """Return the engagement with its crossings filled in.

    The caller gets an ``Engagement`` back rather than a list of crossings alone,
    because a threshold evaluation that answers "yes" without saying what it read
    is the one thing a rep cannot use. The measurements travel with it.

    Only crossings whose moment falls inside the window are reported. A dwell
    reading from last month is not evidence about today, and the research's window
    is what makes the difference between a live signal and a stale one.
    """
    reference = now or datetime.now(timezone.utc)
    cutoff = reference - timedelta(hours=engagement.window_hours)
    if engagement.last_seen_at is not None and engagement.last_seen_at < cutoff:
        return engagement

    crossings: list[Crossing] = []
    for entry in THRESHOLDS:
        measure, kind_of_measure = _MEASURES[entry["kind"]]
        if kind_of_measure == "count":
            observed = len(engagement.pages)
        else:
            observed = int(getattr(engagement, measure, 0))
        threshold = int(entry["threshold"])
        if observed >= threshold:
            crossings.append(
                Crossing(
                    kind=str(entry["kind"]),
                    label=str(entry["label"]),
                    unit=str(entry["unit"]),
                    observed=observed,
                    threshold=threshold,
                )
            )
    return _replace(engagement, crossed=tuple(crossings))


def _replace(engagement: Engagement, **changes: Any) -> Engagement:
    """Rebuild a frozen engagement with some fields changed.

    ``dataclasses.replace`` cannot set ``crossed`` here because it is declared with
    ``compare=False`` and a default of an empty tuple, which is fine, but doing it
    explicitly keeps the one field that is derived visibly derived.
    """
    values = {
        "company_key": engagement.company_key,
        "room_id": engagement.room_id,
        "pages": engagement.pages,
        "dwell_seconds": engagement.dwell_seconds,
        "total_dwell_seconds": engagement.total_dwell_seconds,
        "revisits": engagement.revisits,
        "downloads": engagement.downloads,
        "demo_interactions": engagement.demo_interactions,
        "stakeholder": engagement.stakeholder,
        "stakeholder_role": engagement.stakeholder_role,
        "room_label": engagement.room_label,
        "last_seen_at": engagement.last_seen_at,
        "window_hours": engagement.window_hours,
    }
    values.update(changes)
    return Engagement(**values)


def threshold_table() -> list[dict[str, Any]]:
    """The five inputs with their thresholds and how each one was arrived at.

    Served as data so a client renders its explanation from the same numbers the
    evaluation uses, rather than from a second list that can drift.
    """
    return [
        {
            **entry,
            "reads_crossed_at_or_above": True,
        }
        for entry in THRESHOLDS
    ]


__all__ = [
    "ALLOWED_FIELDS",
    "COUNT_FIELDS",
    "DISPLAY_ONLY_FIELDS",
    "Crossing",
    "Engagement",
    "REQUIRED_FIELDS",
    "THRESHOLD_KINDS",
    "THRESHOLDS_TO_ALERT",
    "WINDOW_HOURS",
    "evaluate",
    "parse_engagement",
    "threshold_table",
]
