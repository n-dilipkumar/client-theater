"""Pure rules: a page view in, a show decision and a path out.

This module is the part of the workflow a reader can check by reading. It has no
store, no framework and no clock: the moment and the visit history are both
arguments, so a test can ask the same question twice and get the same answer. The
engine in :mod:`dsr.page_outreach.engine` supplies those two things and does the
writing.

What the rules do, in the order the researched data flow does them::

    one page view
      -> does it match this workflow's URL rules?        (match_rule)
      -> has the buyer now made enough matching visits    (repeat_count)
      -> does the frequency mode still allow a show       (frequency_decision)
      -> has this session already hidden the block        (session_decision)
      -> show, or say exactly which of the four stopped it

Every step reports why it stopped, because the honest answer to "why did the buyer
not see the block" is usually one of four numbers, and a workflow that answers only
"no" cannot be tuned by the seller who has to live with it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from dsr.page_outreach.errors import (
    InvalidPageView,
    UnknownPageViewField,
)
from dsr.page_outreach.vocabulary import (
    DWELL_SECONDS,
    ENGAGEMENT_INTERACTIONS,
    FREQUENCY_MODES,
    REPEAT_VISITS,
    REPEAT_WINDOW_DAYS,
    SESSION_HIDING_INTERACTIONS,
    TARGET_RULE_KINDS,
    URL_MATCH_MODES,
)

#: Every field a page view may carry: the signals the evidence names, plus who, when
#: and which session. A visitor key is required because the damper is per buyer, and
#: a session id is required because the researched damper is per session.
ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "workflow_id",
        "visitor_key",
        "path",
        "url",
        "dwell_seconds",
        "utm_source",
        "utm_campaign",
        "referrer",
        "session_id",
        "visited_at",
        "company_key",
    }
)

#: The fields without which no decision can be made. The workflow says which rules
#: apply, the path says whether the URL signal matched, the visitor says whose damper
#: applies and the session says which session the damper is in force for.
REQUIRED_FIELDS: tuple[str, ...] = ("workflow_id", "visitor_key", "path", "session_id")

#: Fields that are recorded but no rule reads. ``url`` is the full address the
#: snippet saw, kept so a seller can see what was actually captured when a path looks
#: wrong. ``referrer`` and ``company_key`` are kept for the same reason.
DISPLAY_ONLY_FIELDS: tuple[str, ...] = ("url", "referrer", "company_key", "session_id")


@dataclass(frozen=True)
class PageView:
    """One website page view, as the research says the snippet captures it.

    Frozen because a trigger decision that could be changed halfway through by a
    caller is not reproducible, and the repeat window is precisely the kind of thing
    two readers of the same show will disagree about.
    """

    workflow_id: str
    visitor_key: str
    path: str
    session_id: str
    url: str = ""
    dwell_seconds: int = 0
    utm_source: str = ""
    utm_campaign: str = ""
    referrer: str = ""
    company_key: str = ""
    visited_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "visitor_key": self.visitor_key,
            "path": self.path,
            "session_id": self.session_id,
            "url": self.url,
            "dwell_seconds": self.dwell_seconds,
            "utm_source": self.utm_source,
            "utm_campaign": self.utm_campaign,
            "referrer": self.referrer,
            "company_key": self.company_key,
            "visited_at": _iso(self.visited_at),
        }


@dataclass(frozen=True)
class RuleMatch:
    """One targeting rule, and whether this page view satisfied it.

    ``observed`` is reported beside ``expected`` so a seller can see the arithmetic.
    A block that stays hidden because a dwell reading was 41 rather than 60 cannot be
    tuned by a seller who cannot see the 41.
    """

    kind: str
    mode: str
    expected: str
    observed: str
    met: bool
    sourced: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "mode": self.mode,
            "expected": self.expected,
            "observed": self.observed,
            "met": self.met,
            "sourced": self.sourced,
        }


@dataclass(frozen=True)
class ShowDecision:
    """Whether this page view shows the block, and why.

    ``stopped_by`` is one of five strings rather than free text, because a client
    renders the reason and a free-text reason cannot be styled, counted or tested.
    The five are: ``matched_below_repeat``, ``rules_not_matched``, ``frequency_mode``,
    ``hidden_for_session`` and ``show``.
    """

    show: bool
    stopped_by: str
    reason: str
    matching_visits: int
    visits_required: int
    window_days: int
    session_id: str
    rule_matches: tuple[RuleMatch, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "show": self.show,
            "stopped_by": self.stopped_by,
            "reason": self.reason,
            "matching_visits": self.matching_visits,
            "visits_required": self.visits_required,
            "window_days": self.window_days,
            "session_id": self.session_id,
            "rule_matches": [entry.as_dict() for entry in self.rule_matches],
        }


def _iso(moment: datetime | None) -> str:
    return moment.isoformat() if moment else ""


def _text(value: Any, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise InvalidPageView(f"{field_name} must be text")
    return value.strip()


def _as_int(value: Any, field_name: str) -> int:
    """Read a whole number of at least zero.

    A float is refused rather than rounded. A dwell reading of 59.7 seconds is a real
    measurement, and rounding it up to 60 would fire a threshold this workflow derived
    rather than sourced.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidPageView(f"{field_name} must be a whole number, not {value!r}")
    if isinstance(value, float) and not value.is_integer():
        raise InvalidPageView(f"{field_name} must be a whole number, not {value!r}")
    number = int(value)
    if number < 0:
        raise InvalidPageView(f"{field_name} must not be negative, got {number}")
    return number


def _as_moment(value: Any, now: datetime | None) -> datetime:
    """Read an ISO 8601 moment with an offset, defaulting to now.

    An unzoned moment is refused for the same reason the visitor-identification
    capture refuses one: this workflow compares it against a repeat window, and an
    unzoned time has no place in a window.
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
            raise InvalidPageView(f"visited_at is not an ISO 8601 moment: {exc}") from exc
    else:
        raise InvalidPageView("visited_at must be an ISO 8601 string")
    if moment.tzinfo is None:
        raise InvalidPageView("visited_at carries no timezone offset")
    return moment.astimezone(timezone.utc)


def parse_page_view(payload: Any, *, now: datetime | None = None) -> PageView:
    """Read a page view, or refuse it by name.

    The field list is closed, for the reason
    :class:`~dsr.page_outreach.errors.UnknownPageViewField` gives.
    """
    if not isinstance(payload, dict):
        raise InvalidPageView("a page view must be an object")

    unknown = sorted(set(payload) - ALLOWED_FIELDS)
    if unknown:
        raise UnknownPageViewField(
            f"the page view carries field(s) {', '.join(unknown)}; this workflow reads "
            "path, dwell_seconds, utm_source and utm_campaign"
        )

    for name in REQUIRED_FIELDS:
        if not _text(payload.get(name), name):
            raise InvalidPageView(f"{name} is required")

    return PageView(
        workflow_id=_text(payload["workflow_id"], "workflow_id"),
        visitor_key=_text(payload["visitor_key"], "visitor_key"),
        session_id=_text(payload["session_id"], "session_id"),
        path=_text(payload["path"], "path"),
        url=_text(payload.get("url"), "url"),
        dwell_seconds=_as_int(payload.get("dwell_seconds", 0), "dwell_seconds"),
        utm_source=_text(payload.get("utm_source"), "utm_source"),
        utm_campaign=_text(payload.get("utm_campaign"), "utm_campaign"),
        referrer=_text(payload.get("referrer"), "referrer"),
        company_key=_text(payload.get("company_key"), "company_key"),
        visited_at=_as_moment(payload.get("visited_at"), now),
    )


# --------------------------------------------------------------------------- #
# URL matching
# --------------------------------------------------------------------------- #


def normalise_path(value: str) -> str:
    """A comparable path: leading slash, no query, no fragment, no trailing slash.

    A seller writes ``/pricing`` and the snippet posts
    ``https://example.com/pricing?utm_source=google``. Both have to reach the same
    string, otherwise the rule that a seller can see is not the rule that runs.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        parts = urlsplit(raw)
        raw = parts.path or "/"
    raw = raw.split("?", 1)[0].split("#", 1)[0]
    if not raw.startswith("/"):
        raw = "/" + raw
    if len(raw) > 1 and raw.endswith("/"):
        raw = raw.rstrip("/") or "/"
    return raw


def path_matches(pattern: str, path: str, mode: str = "prefix") -> bool:
    """Does ``path`` satisfy one URL rule?

    ``prefix`` respects a slash boundary, so ``/pricing`` matches ``/pricing/plans``
    and not ``/pricing-archive``. That boundary is the whole reason this is a
    function and not a ``startswith``: a seller who targets ``/pricing`` does not mean
    the archive.
    """
    if mode not in URL_MATCH_MODES:
        return False
    left = normalise_path(pattern)
    right = normalise_path(path)
    if not left or not right:
        return False
    if mode == "exact":
        return left == right
    if mode == "contains":
        return left in right
    if left == right:
        return True
    return right.startswith(left) and right[len(left) :].startswith("/")


def _wildcards(pattern: str) -> re.Pattern[str] | None:
    """Compile a pattern with ``*`` wildcards, or return None when it has none."""
    if "*" not in pattern:
        return None
    parts = [re.escape(part) for part in pattern.split("*")]
    return re.compile("^" + ".*".join(parts) + "$")


def utm_matches(pattern: str, observed: str) -> bool:
    """Match a UTM value, case-insensitively, with ``*`` as a wildcard."""
    left = str(pattern or "").strip().lower()
    right = str(observed or "").strip().lower()
    if not left:
        return False
    if left == right:
        return True
    wildcard = _wildcards(left)
    return bool(wildcard and wildcard.match(right))


def match_rule(
    rule: dict[str, Any], view: PageView, *, dwell_seconds: int = DWELL_SECONDS
) -> RuleMatch:
    """Evaluate one targeting rule against one page view.

    ``dwell_seconds`` is passed in rather than read from the module so a workflow can
    carry its own dwell threshold. A workflow whose rule says ``dwell: "45"`` fires at
    45 and not at the module default, and a rule that names no number uses the
    derived default from the vocabulary.
    """
    kind = str(rule.get("kind") or "").strip()
    mode = str(rule.get("mode") or "").strip()
    value = str(rule.get("value") or "").strip()

    if kind == "url":
        met = path_matches(value, view.path, mode or "prefix")
        return RuleMatch(
            kind=kind,
            mode=mode or "prefix",
            expected=value,
            observed=view.path,
            met=met,
        )
    if kind == "dwell":
        wanted = _rule_dwell(value, dwell_seconds)
        return RuleMatch(
            kind=kind,
            mode="at_or_above",
            expected=str(wanted),
            observed=str(view.dwell_seconds),
            met=view.dwell_seconds >= wanted,
            sourced=bool(rule.get("sourced")),
        )
    if kind == "utm_source":
        return RuleMatch(
            kind=kind,
            mode="equals",
            expected=value,
            observed=view.utm_source,
            met=utm_matches(value, view.utm_source),
        )
    if kind == "utm_campaign":
        return RuleMatch(
            kind=kind,
            mode="equals",
            expected=value,
            observed=view.utm_campaign,
            met=utm_matches(value, view.utm_campaign),
        )
    return RuleMatch(
        kind=kind or "unknown",
        mode=mode or "none",
        expected=value,
        observed="",
        met=False,
    )


def _rule_dwell(value: str, fallback: int) -> int:
    """Read the dwell number out of a dwell rule, falling back to the default.

    A rule value is text because it lives in the same list as a path, so it is read
    rather than typed. A value that is not a number uses the derived default instead
    of refusing, because the save path has already refused a dwell rule with no
    number at all.
    """
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


def match_rules(
    rules: list[dict[str, Any]], view: PageView, *, dwell_seconds: int = DWELL_SECONDS
) -> tuple[RuleMatch, ...]:
    """Evaluate every rule on the workflow.

    The rule list is an AND: every rule has to hold, because a seller who writes a
    URL rule and a dwell rule means both. A workflow with no rules matches everything
    within its repeat window, and the repeat threshold is then the only gate, which
    is the honest reading of a workflow whose seller named no page at all.
    """
    return tuple(match_rule(rule, view, dwell_seconds=dwell_seconds) for rule in rules)


def rules_matched(matches: tuple[RuleMatch, ...]) -> bool:
    """Whether every rule held. An empty rule list matches."""
    return all(entry.met for entry in matches)


def count_matching_visits(
    history: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    window_days: int = REPEAT_WINDOW_DAYS,
) -> int:
    """How many matching visits the buyer already has inside the window.

    The caller passes the views it has already recorded for this workflow and this
    visitor. This function does the window arithmetic, because doing it in the engine
    would put a date comparison next to a write and there would be no way to test the
    arithmetic on its own.

    Only views that matched the rules count. A view of a page the workflow does not
    target is not evidence of interest in it, and counting those would let a buyer who
    browsed a site for an hour earn a block they never qualified for.
    """
    reference = now or datetime.now(timezone.utc)
    cutoff = reference - timedelta(days=window_days)
    total = 0
    for entry in history:
        moment = _entry_moment(entry)
        if moment is None or moment < cutoff or moment > reference:
            continue
        if entry.get("matched") is False:
            continue
        total += 1
    return total


def _entry_moment(entry: dict[str, Any]) -> datetime | None:
    raw = entry.get("visited_at")
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else None
    if not isinstance(raw, str) or not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return moment if moment.tzinfo else None


# --------------------------------------------------------------------------- #
# The frequency modes and the session damper
# --------------------------------------------------------------------------- #

#: What each mode reads, from the vocabulary. Kept beside the decision so the rule
#: and the published quote cannot drift apart.
_FREQUENCY_RULES: dict[str, str] = {
    str(entry["mode"]): str(entry["stops_on"]) for entry in FREQUENCY_MODES
}


def mode_stops_on(mode: str) -> str:
    """The event that ends ``mode``, or ``""`` when the mode is not published."""
    return _FREQUENCY_RULES.get(mode, "")


def frequency_decision(
    mode: str,
    *,
    shown: int,
    interacted: int,
    engaged: int,
    session_interacted: bool = False,
) -> tuple[bool, str]:
    """Does the frequency mode still allow a show? Returns ``(allow, reason)``.

    ``seen`` counts shows, not interactions, because the quote says once "whether or
    not they interact with or dismiss it". ``any_interaction`` stops on the first
    interaction of any kind. ``engaged_with`` stops only on an engagement, which the
    quote defines as selecting a Workflow path, so a Messenger open leaves it free to
    show again in the next session.
    """
    stops_on = mode_stops_on(mode)
    if not stops_on:
        return (False, f"{mode} is not a published frequency mode")
    if stops_on == "shown":
        if shown >= 1:
            return (False, "the Seen mode shows the block once and never again")
        return (True, "the Seen mode has not fired for this buyer yet")
    if stops_on == "interaction":
        if interacted >= 1:
            return (False, "any interaction has already happened for this buyer")
        return (True, "no interaction has happened for this buyer yet")
    if stops_on == "path_selected":
        if engaged >= 1:
            return (False, "this buyer has engaged by selecting a path")
        return (True, "this buyer has not engaged by selecting a path yet")
    return (False, f"the mode stops on {stops_on}, which this workflow does not implement")


def session_decision(session_interactions: list[str]) -> tuple[bool, str]:
    """Does the researched session damper hide the block for the rest of this session?

    The quote: "If they dismiss the Workflow or open the Messenger, it will be hidden
    for the remainder of their session. When they start a new session, the Workflow
    will be shown again, until they engage with it."

    So a dismissal or a Messenger open hides it, and nothing else does. The damper is
    read from the interactions recorded against this session only, which is what makes
    it expire at the session boundary rather than needing a timer.
    """
    for kind in SESSION_HIDING_INTERACTIONS:
        if kind in session_interactions:
            if kind == "dismissed":
                return (False, "the buyer dismissed the block in this session")
            return (False, "the buyer opened the Messenger in this session")
    return (True, "nothing in this session has hidden the block")


def is_engagement(kind: str) -> bool:
    """Does this interaction count as engaging for the ``engaged_with`` mode?"""
    return kind in ENGAGEMENT_INTERACTIONS


def is_session_hiding(kind: str) -> bool:
    """Does this interaction hide the block for the remainder of the session?"""
    return kind in SESSION_HIDING_INTERACTIONS


# --------------------------------------------------------------------------- #
# The decision
# --------------------------------------------------------------------------- #


def decide(
    view: PageView,
    *,
    rules: list[dict[str, Any]],
    frequency: str = "seen",
    shown: int = 0,
    interacted: int = 0,
    engaged: int = 0,
    session_interactions: list[str] | None = None,
    dwell_seconds: int = DWELL_SECONDS,
    matching_visits: int = 0,
    visits_required: int = REPEAT_VISITS,
    window_days: int = REPEAT_WINDOW_DAYS,
    now: datetime | None = None,
) -> ShowDecision:
    """Decide whether this page view shows the block, and say why.

    The four gates run in the researched order and the first one to stop is the one
    reported. Rules first, because a page the workflow does not target is not a
    candidate at any count. Then the repeat threshold, because the ticket is about a
    buyer who returns. Then the frequency mode, which is a property of the buyer
    rather than of the page. Then the session damper, which is the finest grain and
    the last thing standing between a buyer and a block they have already closed.

    ``matching_visits`` is the caller's count of visits that satisfied the rules
    *including* this view, and ``visits_required`` is the workflow's own threshold.
    The engine records this view before calling here, so the count a seller reads
    includes the visit that triggered the decision.
    """
    matches = match_rules(rules, view, dwell_seconds=dwell_seconds)
    matching = rules_matched(matches)

    def decision(show: bool, stopped_by: str, reason: str) -> ShowDecision:
        return ShowDecision(
            show=show,
            stopped_by=stopped_by,
            reason=reason,
            matching_visits=matching_visits,
            visits_required=visits_required,
            window_days=window_days,
            session_id=view.session_id,
            rule_matches=matches,
        )

    if not matching:
        unmet = ", ".join(
            f"{entry.kind} expected {entry.expected} and saw {entry.observed}"
            for entry in matches
            if not entry.met
        )
        return decision(
            False,
            "rules_not_matched",
            f"no targeting rule held: {unmet}" if unmet else "no targeting rule held",
        )

    if matching_visits < visits_required:
        return decision(
            False,
            "matched_below_repeat",
            (
                f"{matching_visits} matching visit(s) in the last {window_days} day(s), "
                f"and this workflow needs {visits_required}"
            ),
        )

    allowed, reason = frequency_decision(
        frequency,
        shown=shown,
        interacted=interacted,
        engaged=engaged,
    )
    if not allowed:
        return decision(False, "frequency_mode", reason)

    visible, session_reason = session_decision(session_interactions or [])
    if not visible:
        return decision(False, "hidden_for_session", session_reason)

    return decision(True, "show", f"shown: {reason}, {session_reason}")


def rule_table() -> list[dict[str, Any]]:
    """The rule kinds and match modes a workflow may use, with what each one reads."""
    return [
        {
            "kind": "url",
            "modes": list(URL_MATCH_MODES),
            "reads": "The visited path, compared after the query string and trailing slash are removed.",
            "sourced": True,
        },
        {
            "kind": "dwell",
            "modes": ["at_or_above"],
            "reads": f"Seconds spent on the page, at or above the rule's number or {DWELL_SECONDS}.",
            "sourced": True,
        },
        {
            "kind": "utm_source",
            "modes": ["equals"],
            "reads": "The utm_source the snippet captured. Asterisks match any run of characters.",
            "sourced": True,
        },
        {
            "kind": "utm_campaign",
            "modes": ["equals"],
            "reads": "The utm_campaign the snippet captured. Asterisks match any run of characters.",
            "sourced": True,
        },
    ]


def supported_rule_kinds() -> tuple[str, ...]:
    """The rule kinds the parser implements, in the vocabulary's order."""
    return tuple(entry["kind"] for entry in rule_table())


def known_rule_kinds() -> tuple[str, ...]:
    """The rule kinds the vocabulary publishes, which is what save-time validation uses."""
    return TARGET_RULE_KINDS


__all__ = [
    "ALLOWED_FIELDS",
    "DISPLAY_ONLY_FIELDS",
    "REQUIRED_FIELDS",
    "PageView",
    "RuleMatch",
    "ShowDecision",
    "count_matching_visits",
    "decide",
    "frequency_decision",
    "is_engagement",
    "is_session_hiding",
    "known_rule_kinds",
    "match_rule",
    "match_rules",
    "mode_stops_on",
    "normalise_path",
    "parse_page_view",
    "path_matches",
    "rule_table",
    "rules_matched",
    "session_decision",
    "supported_rule_kinds",
    "utm_matches",
]
