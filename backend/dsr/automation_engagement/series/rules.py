"""The rules: filters, entry, the sticky exit, the goal, and the tag block.

Everything here is a rule, not a framework. It reads a mapping and returns a
mapping, raises one of the errors in :mod:`dsr.automation_engagement.series.errors`,
and knows nothing about HTTP, SQLite or a request. That is what makes every rule
below testable without a server, and it is why the HTTP layer in
``dsr.features.wf105_reenter_series_and_tag`` is a thin translation of what
happens here.

The four rules the research fixes exactly
-----------------------------------------

**Re-entry is data-driven.** "Customers can re-enter the same Series multiple
times if you enable 'Re-enter Series' on the starting rules block." So
:func:`entry_decision` is the automation: a qualifying event is evaluated
against the starting rule block, and the toggle decides whether a contact that
already completed the series may enter again.

**The exit rule is sticky.** "If a customer's data is updated to match the
filters in this exit rule they will exit the series completely, and cannot
re-enter." So a contact whose enrollment state is ``exited`` is refused re-entry
before the starting rule is even consulted. Re-entry can never override an exit.

**The tag block cannot create a tag.** "You won't be able to create a new tag
here, but can use any existing tag from the workspace." So
:func:`select_tag` resolves the name against the workspace vocabulary and refuses
a name that is not already there, rather than creating one silently.

**The Goal tab is a second, separate filter.** The user flow's step four opens a
*Goal* tab carrying "an outcome filter ... and the match window", which is not the
starting rule block of step one. So :func:`goal_met` reads ``goal`` and never
``start_filters``; the two are separate keys in the data model, and a contact can
enter on one filter set and satisfy the goal on another.

Why an instant is always derived
--------------------------------

A goal's match window is a *duration*, so this module stores durations and never
absolute dates. Every instant a caller supplies comes from an injected clock, and
:func:`parse_instant` refuses a timestamp with no UTC designator rather than
guessing the host's zone. That is what lets a test pin the moment without a fixed
date in a payload and without a sleep.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from dsr.automation_engagement.series import vocabulary as vocab
from dsr.automation_engagement.series.errors import (
    SeriesConflict,
    SeriesRefused,
    TagNotInWorkspace,
)

# --------------------------------------------------------------------------- #
# Instants
# --------------------------------------------------------------------------- #


def to_iso(moment: datetime) -> str:
    """An instant as ISO-8601 UTC with a ``Z`` designator."""

    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_instant(value: Any) -> datetime | None:
    """Parse an ISO-8601 instant, requiring a UTC designator.

    Returns ``None`` for anything unparsable rather than guessing, because an
    instant with no designator is ambiguous and guessing the host's zone would
    make the same series behave differently on two machines.
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


# --------------------------------------------------------------------------- #
# Filters: dotted JSON paths, all conditions must match
# --------------------------------------------------------------------------- #

_MISSING = object()


def resolve_path(payload: Mapping[str, Any], dotted: str) -> Any:
    """Read a dotted JSON path out of a mapping, or the missing sentinel.

    The store indexes ``data`` by dotted path, so a filter names a path the same
    way a ``?where=`` query does. A missing path is not an error: it is a filter
    that does not match, which is the correct reading of "this contact has no
    such field yet".
    """

    current: Any = payload
    for part in dotted.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            try:
                current = current[int(part)]
            except (IndexError, ValueError):
                return _MISSING
        else:
            return _MISSING
    return current


def matches(contact: Mapping[str, Any], filters: Sequence[Mapping[str, Any]]) -> bool:
    """True when every filter matches the contact.

    A filter matches on equality. Booleans compare as booleans, numbers as
    numbers, and everything else as text, which is the same affinity rule the
    dynamic index uses.
    """

    for condition in filters:
        expected = condition.get("value")
        found = resolve_path(contact, str(condition.get("field")))
        if found is _MISSING:
            return False
        if isinstance(expected, bool):
            if bool(found) is not expected:
                return False
        elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
            if not isinstance(found, (int, float)) or isinstance(found, bool):
                return False
            if float(found) != float(expected):
                return False
        elif str(found) != str(expected):
            return False
    return True


def normalise_filters(raw: Any, field: str) -> list[dict[str, Any]]:
    """Validate a filter list into ``[{"field", "value"}]``.

    Accepts the list form the user flow describes (``[{"field": "plan", "value":
    "premium"}]``) and a plain mapping (``{"plan": "premium"}``). Anything else is
    refused by field, so a caller learns which input was wrong.
    """

    if raw in (None, ""):
        return []
    items: list[Any]
    if isinstance(raw, Mapping):
        items = [{"field": key, "value": value} for key, value in raw.items()]
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        items = list(raw)
    else:
        raise SeriesRefused(
            f"{field} must be a list of filters or an object of field/value pairs",
            {field: "not a filter list"},
        )

    out: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise SeriesRefused(
                f"{field}[{index}] must be an object with a field and a value",
                {field: "filter must be an object"},
            )
        name = str(item.get("field") or "").strip()
        if not name:
            raise SeriesRefused(
                f"{field}[{index}] needs a field",
                {field: "filter is missing its field"},
            )
        if "value" not in item:
            raise SeriesRefused(
                f"{field}[{index}] needs a value",
                {field: "filter is missing its value"},
            )
        out.append({"field": name, "value": item["value"]})
    return out


def normalise_goal(raw: Any) -> dict[str, Any] | None:
    """Validate the Goal tab: an outcome filter plus a match window.

    The window is a number of days and defaults to the research's 30. It is kept
    distinct from the starting rule block, which is what the issue requires: a
    contact enters on the start filter and satisfies the goal on this one.
    """

    if raw in (None, "", {}):
        return None
    if not isinstance(raw, Mapping):
        raise SeriesRefused("goal must be an object", {"goal": "not an object"})
    outcome_filters = normalise_filters(raw.get("outcome_filters"), "goal.outcome_filters")
    window_raw = raw.get("match_window_days", 30)
    try:
        window = int(window_raw)
    except (TypeError, ValueError) as exc:
        raise SeriesRefused(
            "goal.match_window_days must be a number of days",
            {"goal.match_window_days": "not a number"},
        ) from exc
    if window <= 0:
        raise SeriesRefused(
            "goal.match_window_days must be a positive number of days",
            {"goal.match_window_days": "must be positive"},
        )
    return {
        "outcome_filters": outcome_filters,
        "match_window_days": window,
    }


# --------------------------------------------------------------------------- #
# The workspace tag vocabulary and the tag block
# --------------------------------------------------------------------------- #


def tag_names(workspace_tags: Iterable[Any]) -> list[str]:
    """The names in the workspace tag vocabulary, in order.

    Accepts either a plain name, a mapping with a ``name`` key, or a stored tag
    record whose name lives under ``data``. That last shape is what the engine
    passes, and resolving it here keeps every caller from knowing the record
    envelope.
    """

    names: list[str] = []
    for entry in workspace_tags:
        if isinstance(entry, Mapping):
            if "name" in entry:
                name: Any = entry["name"]
            else:
                data = entry.get("data")
                name = data.get("name") if isinstance(data, Mapping) else None
        else:
            name = entry
        text = str(name or "").strip()
        if text:
            names.append(text)
    return names


def select_tag(block: Mapping[str, Any], workspace_tags: Iterable[Any]) -> str:
    """Resolve a tag block's name against the workspace vocabulary.

    The research forbids creating a tag here, so a name that is not already in
    the workspace is refused by name rather than created. The comparison is
    case-insensitive so "Upgraded after Series" and "upgraded after series" name
    one tag, and the canonical workspace spelling is returned.
    """

    names = tag_names(workspace_tags)
    wanted = str(block.get("tag") or "").strip()
    if not wanted:
        raise TagNotInWorkspace(
            "a tag block must name an existing workspace tag",
            {"tag": "required"},
        )
    for name in names:
        if name.lower() == wanted.lower():
            return name
    raise TagNotInWorkspace(
        f"tag {wanted!r} is not in the workspace; a tag block cannot create one",
        {"tag": f"not a workspace tag; known tags: {', '.join(names) or 'none'}"},
    )


# --------------------------------------------------------------------------- #
# The series definition
# --------------------------------------------------------------------------- #


def _title_for(kind: str) -> str:
    return {"message": "Message", "rule": "Rule", "tag": "Tag"}.get(kind, kind.title())


def validate_path(raw: Any, workspace_tags: Iterable[Any]) -> list[dict[str, Any]]:
    """Validate the path the user flow builds: message -> rule -> tag block.

    A path is a list of blocks. Every block carries a kind; a tag block must name
    an existing workspace tag. The order is the caller's to choose, because the
    research fixes only that the tag block sits at the end of the flow it
    describes, not that every series has exactly three blocks.
    """

    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise SeriesRefused(
            "a series needs a path of at least one block",
            {"path": "required"},
        )
    blocks: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise SeriesRefused(
                f"path[{index}] must be an object",
                {"path": "block must be an object"},
            )
        kind = str(item.get("kind") or "").strip().lower()
        try:
            kind = _require_block_kind(kind)
        except ValueError as exc:
            raise SeriesRefused(str(exc), {"path": f"block {index}: unknown kind"}) from exc
        block: dict[str, Any] = {
            "kind": kind,
            "title": str(item.get("title") or "").strip() or _title_for(kind),
        }
        if kind == vocab.BLOCK_TAG:
            block["tag"] = select_tag(item, workspace_tags)
        for key, value in item.items():
            if key not in {"kind", "title", "tag"}:
                block[key] = value
        blocks.append(block)
    return blocks


def _require_block_kind(value: str) -> str:
    if value not in vocab.BLOCK_KINDS:
        raise ValueError(f"block kind must be one of: {', '.join(vocab.BLOCK_KINDS)}")
    return value


def validate_series(payload: Mapping[str, Any], workspace_tags: Iterable[Any]) -> dict[str, Any]:
    """Validate a series definition and return its normalised form.

    The name and the starting rule block are required, because the entry rule is
    the automation and a series without one can never enrol anyone. The exit
    filters and the goal are optional. Unknown keys are carried through
    untouched, so a team adding a field needs no coordination.
    """

    name = str(payload.get("name") or "").strip()
    if not name:
        raise SeriesRefused("a series needs a name", {"name": "required"})

    start_filters = normalise_filters(payload.get("start_filters"), "start_filters")
    if not start_filters:
        raise SeriesRefused(
            "the starting rule block needs at least one filter",
            {"start_filters": "required"},
        )

    series: dict[str, Any] = {
        "name": name,
        "state": vocab.STATE_DRAFT,
        "reenter": bool(payload.get("reenter")),
        "start_filters": start_filters,
        "exit_filters": normalise_filters(payload.get("exit_filters"), "exit_filters"),
        "goal": normalise_goal(payload.get("goal")),
        "path": validate_path(payload.get("path"), workspace_tags),
        "poll_interval_minutes": vocab.POLL_INTERVAL_MINUTES,
    }
    for key, value in payload.items():
        if key not in series:
            series[key] = value
    return series


def validate_tag(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a workspace tag. A tag is shared vocabulary, not a per-series one."""

    name = str(payload.get("name") or "").strip()
    if not name:
        raise SeriesRefused("a tag needs a name", {"name": "required"})
    tag: dict[str, Any] = {"name": name}
    for key, value in payload.items():
        if key != "name":
            tag[key] = value
    return tag


# --------------------------------------------------------------------------- #
# Entry: the starting rule block, the toggle, and the sticky exit
# --------------------------------------------------------------------------- #


def entry_decision(
    series: Mapping[str, Any],
    contact: Mapping[str, Any],
    prior: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Decide what a qualifying event does to one contact.

    Returns an outcome in :data:`~dsr.automation_engagement.series.vocabulary.
    ENTRY_OUTCOMES`. An enrolling outcome is one of ``enrolled`` or ``reentered``;
    everything else is an honest skip the poll records rather than raises.

    Order is the rule:

    1. A series that is not live cannot enrol. Draft and paused are states, not
       errors.
    2. A contact that has exited is refused **before** the starting rule is read,
       because the researched exit is sticky and re-entry can never override it.
    3. A contact already active is skipped, so a series cannot enrol it twice.
    4. The starting rule block decides the rest.
    5. A contact that completed before re-enters only when the toggle is on.
    """

    if str(series.get("state")) != vocab.STATE_LIVE:
        return {
            "outcome": vocab.OUTCOME_NOT_LIVE,
            "reason": f"the series is {series.get('state')}, so it is not enrolling",
        }

    prior_state = str((prior or {}).get("state") or "")
    if prior_state == vocab.EXITED:
        return {
            "outcome": vocab.OUTCOME_EXITED_STICKY,
            "reason": "the contact exited this series and the exit rule is sticky",
        }
    if prior_state == vocab.ENROLLED:
        return {
            "outcome": vocab.OUTCOME_ALREADY_ACTIVE,
            "reason": "the contact is already enrolled in this series",
        }

    if not matches(contact, series.get("start_filters", [])):
        return {
            "outcome": vocab.OUTCOME_NO_MATCH,
            "reason": "the contact does not match the starting rule block",
        }

    if prior_state == vocab.COMPLETED:
        if not series.get("reenter"):
            return {
                "outcome": vocab.OUTCOME_REENTER_DISABLED,
                "reason": "the contact completed this series and Re-enter Series is off",
            }
        return {"outcome": vocab.OUTCOME_REENTERED, "reason": "the contact matches again"}

    return {"outcome": vocab.OUTCOME_ENROLLED, "reason": "the contact matches the starting rule"}


def exit_matches(series: Mapping[str, Any], contact: Mapping[str, Any]) -> bool:
    """Whether the contact's data matches any exit rule.

    Any single filter matching is enough, because the exit-rules tab lists
    separate exclusion rules and the researched example lists two of them. An
    empty exit-filter list matches nothing, so a series with no exit rules never
    exits a contact; an empty list must not be read as "matches everything",
    which would exit every contact at once.
    """

    filters = series.get("exit_filters") or []
    if not filters:
        return False
    return any(matches(contact, [condition]) for condition in filters)


# --------------------------------------------------------------------------- #
# The Goal tab: a second filter set plus a match window
# --------------------------------------------------------------------------- #


def goal_met(
    series: Mapping[str, Any],
    contact: Mapping[str, Any],
    enrollment: Mapping[str, Any],
    at: datetime,
) -> dict[str, Any]:
    """Whether the contact satisfied the Goal tab within its match window.

    The goal is an outcome filter set plus a window measured from the
    enrollment's own entry instant, so it reads only ``goal`` and never
    ``start_filters``. Returns the verdict with a reason, rather than a bare
    boolean, so the page and the tests can say *why* a goal was not met.
    """

    goal = series.get("goal")
    if not goal:
        return {"met": False, "reason": "the series declares no goal"}
    if not matches(contact, goal.get("outcome_filters", [])):
        return {"met": False, "reason": "the contact does not match the goal outcome filter"}
    window = int(goal.get("match_window_days") or 0)
    entered = parse_instant(enrollment.get("entered_at"))
    if window and entered and at - entered > timedelta(days=window):
        return {
            "met": False,
            "reason": f"the outcome matched after the {window}-day window",
        }
    return {"met": True, "reason": "the contact matched the goal within the window"}


# --------------------------------------------------------------------------- #
# The poll
# --------------------------------------------------------------------------- #


def due_for_poll(series: Mapping[str, Any], last_polled_at: Any, now: datetime) -> bool:
    """Whether a series is due for another poll.

    The research polls every 30 minutes. A series never polled is due at once; a
    series with an unreadable ``last_polled_at`` is also due, because re-reading a
    series is cheap and skipping one is not.
    """

    last = parse_instant(last_polled_at)
    if last is None:
        return True
    interval = int(series.get("poll_interval_minutes") or vocab.POLL_INTERVAL_MINUTES)
    return now - last >= timedelta(minutes=interval)


def suggest_exit_reason(series: Mapping[str, Any], contact: Mapping[str, Any]) -> str:
    """The exit reason to record, or a conflict when the exit rule does not match.

    Recorded as a rule rather than left to the caller, so an exit can never be
    written for a contact whose data does not match the researched condition: a
    sticky exit is permanent, and writing one by mistake cannot be undone by
    re-entry.
    """

    if not exit_matches(series, contact):
        raise SeriesConflict(
            "the contact does not match this series' exit rules",
            code="exit_rule_not_matched",
        )
    return vocab.EXIT_RULE
