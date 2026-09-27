"""The filter criteria: what the integration can filter on, and whether an event matches.

This is the heart of WF-030, and it is the part the research pins hardest. Two
sentences between them fix the entire surface:

* "**Downloads:** filter by date and/or file name. **Views:** filter by date."
* "Clicks/Interactions by date or link URL" (user flow, step 6)

plus, for the fifth family, "Filter by activity text - e.g. ``completed task \"Sign
up for free account\"``". :func:`~dsr.crm_workflows.vocabulary.describe` publishes
the resulting matrix, :func:`parse_criteria` validates against it, and this
module's :func:`matches` is the only thing that decides. The editor a client
builds and the evaluator therefore cannot disagree about what is legal.

A criteria is ``{"family": <one of five>, "refinements": {...}}``. A workflow may
carry several, and they are ANDed - which is what "filter criteria is met" means
and what the CRM this was researched on does. A single criteria is exactly the
researched shape; a list is the same shape, repeated.

Three outcomes, not two
-----------------------

:func:`matches` distinguishes **matched**, **did not match**, and **could not be
checked**, and the third is not folded into the second.

* A refinement the event simply does not satisfy is a miss. ``occurred`` outside
  the window, a ``link_url`` that is not the one named, a ``file_name`` that
  differs.
* A refinement whose input the event **does not carry** cannot be checked. A
  download filter refined by ``file_name`` evaluated against an event with no file
  name is not a "no". It is an unanswered question.

A match needs positive evidence, so an unverifiable refinement does not match -
and the reason says so. The alternative, letting it pass, is a rule that fires on
evidence nobody supplied, and a "change stages based on onboarding tasks" workflow
that enrols a contact because the event happened to omit a field is worse than one
that visibly never fires. Letting it fail silently is the other bad option: a
team that has not yet wired ``link_url`` onto its activity rows would see a
workflow that quietly does nothing with no way to find out why. Naming the reason
is what makes the edge debuggable, which is the whole point of the third outcome.

:func:`lint_criteria` is the other half. It reports what a criteria *does* match
without anybody having to wait for a buyer's activity to find out - a filter with
no refinement matches every event of its family, which is a real and usually
unintended thing to build.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.crm_workflows.activity import criterion_activity_name, occurred_window, parse_timestamp
from dsr.crm_workflows.errors import WorkflowError
from dsr.crm_workflows.vocabulary import (
    ALL_REFINEMENTS,
    REFINEMENTS,
    require_family,
    require_refinement,
)

#: Why a criteria reached its verdict. Served with every decision so a caller can
#: read the reasoning rather than trust a boolean.
REASONS: dict[str, str] = {
    "matched": "The event satisfies every refinement of this filter.",
    "family_mismatch": (
        "The event belongs to a different filter family. The research offers five "
        "families and a filter is on one of them."
    ),
    "action_unclassified": (
        "The event's action is in no published family, so no filter can match it."
    ),
    "occurred_before_window": "The event is older than the filter's 'Occurred' window.",
    "occurred_after_window": "The event is newer than the filter's 'Occurred' window.",
    "link_url_mismatch": "The event's link URL is not the one the filter names.",
    "file_name_mismatch": "The event's file name is not the one the filter names.",
    "activity_text_mismatch": "The event's activity text is not the one the filter names.",
    "refinement_unverifiable": (
        "The event does not carry the field this filter refines on, so the criterion "
        "could not be checked. It is reported rather than counted as a miss, and it "
        "does not match: a rule needs positive evidence to fire."
    ),
    "malformed_criterion": "The criterion's value could not be read as a filter.",
}

SEVERITIES = ("info", "warning")


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def parse_criteria(payload: Any) -> dict[str, Any]:
    """Validate one criteria against the researched surface, or refuse it.

    The order of the checks matters and is the researched surface in order: the
    family first, because nothing about a refinement is meaningful without one,
    then the refinement names against that family's published row, then the
    refinement *values*, because a refinement the family publishes can still be
    given a value that is not a filter.
    """
    if not isinstance(payload, Mapping):
        raise WorkflowError("a filter must be a JSON object with a family")

    family = require_family(payload.get("family"))

    raw_refinements = payload.get("refinements")
    if raw_refinements is None:
        # Also accept the flat spelling, because a client building a form will
        # produce one or the other and neither is wrong.
        raw_refinements = {
            key: value
            for key, value in payload.items()
            if key not in ("family", "action_family", "id", "label", "note")
        }
    if not isinstance(raw_refinements, Mapping):
        raise WorkflowError("refinements must be an object")

    refinements: dict[str, Any] = {}
    for key, value in raw_refinements.items():
        if value is None:
            continue
        name = require_refinement(family, key)
        if name == "occurred" and occurred_window(value) is None:
            raise WorkflowError(
                f"the 'occurred' refinement of {family} needs a UTC date, a timestamp, "
                f"or a {{from, to}} object; got {value!r}"
            )
        if name in ("link_url", "file_name", "activity_text"):
            if not isinstance(value, str) or not value.strip():
                raise WorkflowError(
                    f"the {name!r} refinement of {family} needs the exact text to match; "
                    f"got {value!r}"
                )
        refinements[name] = value

    return {
        "family": family,
        "label": str(payload.get("label") or "").strip(),
        "refinements": refinements,
    }


def parse_criteria_list(payload: Any) -> list[dict[str, Any]]:
    """Accept one criteria or a list of them, and validate each.

    A list is ANDed at evaluation time. Nothing here caps the length: the source's
    "five different options" is a count of the *families the integration offers*,
    not a limit on how many filters a workflow may combine, and inventing a cap
    would refuse something the research never says is impossible.
    """
    if payload is None:
        return []
    if isinstance(payload, Mapping):
        return [parse_criteria(payload)]
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        if not payload:
            return []
        return [parse_criteria(entry) for entry in payload]
    raise WorkflowError("criteria must be an object or a list of objects")


# --------------------------------------------------------------------------- #
# Lint
# --------------------------------------------------------------------------- #


def lint_criteria(criteria: Mapping[str, Any]) -> list[dict[str, Any]]:
    """What this criteria matches before a single buyer has done anything.

    The interesting one is ``no_refinement``: a filter on ``views`` with nothing
    narrowing it enrols a contact on their first page view, and the person who
    built it almost certainly meant "views this week" or "views of the pricing
    pack". That is a fact about the definition, visible the moment it is saved.
    """
    warnings: list[dict[str, Any]] = []
    family = str(criteria.get("family") or "")
    refinements = criteria.get("refinements") or {}

    if not refinements:
        warnings.append(
            {
                "code": "no_refinement",
                "severity": "info",
                "field": None,
                "message": (
                    f"Nothing narrows this {family} filter, so it matches every "
                    f"{family} event in the room. The research lets you refine by "
                    f"{', '.join(REFINEMENTS.get(family, ())) or 'nothing'}; a filter "
                    f"with no refinement is rarely the one intended."
                ),
            }
        )

    if "occurred" not in refinements:
        warnings.append(
            {
                "code": "no_occurred_filter",
                "severity": "info",
                "field": "occurred",
                "message": (
                    "Without an 'Occurred' filter this criteria matches activity of any "
                    "age, including events from before the workspace was connected to the "
                    "deal."
                ),
            }
        )

    return warnings


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


def _norm(value: str) -> str:
    return " ".join(value.split()).strip().lower()


def _check_refinement(
    name: str, value: Any, event: Mapping[str, Any]
) -> tuple[str | None, str]:
    """One refinement against one event.

    Returns ``(reason, detail)`` where ``reason`` is ``None`` when the refinement
    holds, and otherwise a key of :data:`REASONS`. The detail is always
    human-readable, because these strings end up in a UI next to a workflow a
    person is trying to debug.
    """
    if name == "occurred":
        window = occurred_window(value)
        if window is None:  # already refused at parse time; belt and braces
            return "malformed_criterion", f"the occurred refinement {value!r} is unreadable"
        at = parse_timestamp(event.get("occurred_at"))
        if at is None:
            return (
                "refinement_unverifiable",
                "the event carries no readable timestamp, so the 'Occurred' filter "
                "cannot be checked against it",
            )
        start, end = window
        if start is not None and at < start:
            return (
                "occurred_before_window",
                f"the event happened at {event.get('occurred_at')}, before the filter's window opens",
            )
        if end is not None and at > end:
            return (
                "occurred_after_window",
                f"the event happened at {event.get('occurred_at')}, after the filter's window closes",
            )
        return None, f"the event at {event.get('occurred_at')} is inside the filter's window"

    if name == "link_url":
        actual = event.get("link_url")
        if not actual:
            return (
                "refinement_unverifiable",
                "the event carries no link URL, so a link-URL filter cannot be checked",
            )
        if str(actual).strip() != str(value).strip():
            return "link_url_mismatch", f"the event's link URL is {actual!r}, not {value!r}"
        return None, f"the event's link URL is {actual!r}"

    if name == "file_name":
        actual = event.get("file_name")
        if not actual:
            return (
                "refinement_unverifiable",
                "the event carries no file name, so a file-name filter cannot be checked",
            )
        if _norm(str(actual)) != _norm(str(value)):
            return "file_name_mismatch", f"the event's file name is {actual!r}, not {value!r}"
        return None, f"the event's file name is {actual!r}"

    if name == "activity_text":
        actual = event.get("activity_text")
        if not actual:
            return (
                "refinement_unverifiable",
                "the event carries no activity text, so a MAP-activity filter cannot be checked",
            )
        wanted = criterion_activity_name(value)
        named = event.get("task_name") or actual
        if wanted is not None and _norm(named) == _norm(wanted):
            return None, f"the event is {actual!r}"
        # No documented shape: compare the raw text, so a literal criterion still
        # works rather than being refused for being unfamiliar.
        if wanted is None and _norm(str(actual)) == _norm(str(value)):
            return None, f"the event's activity text is {actual!r}"
        return "activity_text_mismatch", f"the event's activity text is {actual!r}, not {value!r}"

    # Unreachable while the vocabulary and this function agree; kept honest rather
    # than assumed, because a refinement added to the matrix without a checker
    # here would otherwise fall through and report "matched".
    return (
        "malformed_criterion",
        f"no matcher exists for the {name!r} refinement; it was accepted by the "
        f"vocabulary and cannot be evaluated",
    )


def matches(criteria: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    """Does this event satisfy this filter? With the reasoning.

    Every refinement is evaluated and reported, including the ones that held, so a
    caller can see which part of a compound filter did the work. A criteria with
    no refinements still has to be in the right family - "When filter criteria is
    met" is a statement about this family of activity, not about all activity.
    """
    family = str(criteria.get("family") or "")
    refinements = criteria.get("refinements") or {}

    if event.get("action_family") is None:
        return {
            "matched": False,
            "family": family,
            "reason": "action_unclassified",
            "detail": REASONS["action_unclassified"],
            "checked": [],
            "failed": [],
            "unverifiable": [],
        }

    if str(event.get("action_family")) != family:
        return {
            "matched": False,
            "family": family,
            "reason": "family_mismatch",
            "detail": REASONS["family_mismatch"],
            "checked": [],
            "failed": [],
            "unverifiable": [],
        }

    checked: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    unverifiable: list[dict[str, Any]] = []

    for name in ALL_REFINEMENTS:
        if name not in refinements:
            continue
        reason, detail = _check_refinement(name, refinements[name], event)
        row = {"refinement": name, "value": refinements[name], "detail": detail}
        if reason is None:
            checked.append(row)
        elif reason == "refinement_unverifiable":
            unverifiable.append({**row, "reason": reason, "meaning": REASONS[reason]})
        else:
            failed.append({**row, "reason": reason, "meaning": REASONS[reason]})

    if unverifiable:
        # Reported before the failures, and deliberately a miss: see this module's
        # docstring. An unanswered question is not an answer of "no", and it is
        # certainly not an answer of "yes".
        return {
            "matched": False,
            "family": family,
            "reason": "refinement_unverifiable",
            "detail": "; ".join(entry["detail"] for entry in unverifiable),
            "checked": checked,
            "failed": failed,
            "unverifiable": unverifiable,
        }
    if failed:
        return {
            "matched": False,
            "family": family,
            "reason": str(failed[0]["reason"]),
            "detail": "; ".join(entry["detail"] for entry in failed),
            "checked": checked,
            "failed": failed,
            "unverifiable": [],
        }

    return {
        "matched": True,
        "family": family,
        "reason": "matched",
        "detail": REASONS["matched"],
        "checked": checked,
        "failed": [],
        "unverifiable": [],
    }


def evaluate(
    criteria_list: Sequence[Mapping[str, Any]], event: Mapping[str, Any]
) -> dict[str, Any]:
    """Every criteria against **one** event, ANDed.

    Kept because it is the right shape for a caller holding a single event, and
    because it is what the per-event reporting is built on. The workflow evaluator
    does *not* use it, because the research says the filter "evaluates criteria per
    contact": see :func:`evaluate_contact` for the shape a contact-level
    evaluation needs, and the reason the two differ.
    """
    decisions = [matches(criteria, event) for criteria in criteria_list]
    failing = [decision for decision in decisions if not decision["matched"]]
    return {
        "matched": bool(criteria_list) and not failing,
        "criteria_count": len(criteria_list),
        "failed_count": len(failing),
        "reason": "matched" if criteria_list and not failing else (
            str(failing[0]["reason"]) if failing else "no_criteria"
        ),
        "detail": (
            "Every filter of this workflow is satisfied by the event."
            if criteria_list and not failing
            else (
                "; ".join(f"{decision['family']}: {decision['detail']}" for decision in failing)
                or "the workflow declares no filter, so nothing can match it"
            )
        ),
        "criteria": decisions,
    }


def evaluate_contact(
    criteria_list: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Every criteria against **a contact's** activity, and which events satisfied it.

    This is the shape the researched data flow calls for: "the integration's
    filter evaluates criteria per contact". The distinction is not pedantic - it
    decides whether a two-filter workflow can ever fire.

    A per-event AND says one event must satisfy every filter. That makes a workflow
    filtering both ``downloads`` and ``views`` unfireable, because a single event is
    never both, and the person who built it would see a rule that silently does
    nothing. A per-contact AND says each filter must be satisfied by *some* event in
    the contact's history - which is what a reader of "when filter criteria is met"
    understands, and what a CRM this was researched on does.

    Both readings are defensible from the prose, and the choice is recorded as the
    ``criteria-are-evaluated-per-contact`` inference. Per-candidate is taken because
    the alternative refuses a legitimate rule without saying so.

    Returns, per criteria, which activity ids satisfied it and why the others did
    not, so a workflow that enrolled nobody names which filter fell short and over
    what history.
    """
    if not criteria_list:
        return {
            "matched": False,
            "criteria_count": 0,
            "reason": "no_criteria",
            "detail": "the workflow declares no filter, so nothing can match it",
            "criteria": [],
            "hit_activity_ids": [],
            "miss_count": 0,
        }

    rows: list[dict[str, Any]] = []
    hit_ids: list[str] = []
    for index, criteria in enumerate(criteria_list):
        hits: list[Mapping[str, Any]] = []
        misses: list[dict[str, Any]] = []
        for event in events:
            verdict = matches(criteria, event)
            if verdict["matched"]:
                hits.append(event)
            else:
                misses.append({"event": event, "verdict": verdict})
        for event in hits:
            if event.get("id"):
                hit_ids.append(str(event["id"]))
        rows.append(
            {
                "index": index,
                "family": str(criteria.get("family") or ""),
                "refinements": dict(criteria.get("refinements") or {}),
                "matched": bool(hits),
                "match_count": len(hits),
                "reason": "matched" if hits else (str(misses[0]["verdict"]["reason"]) if misses else "no_events"),
                "detail": (
                    f"{len(hits)} of the contact's {len(events)} event(s) satisfied this "
                    f"filter."
                    if hits
                    else (
                        f"none of the contact's {len(events)} event(s) satisfied this "
                        f"filter. The reasons are {_reason_tally(misses)}"
                    )
                    if misses
                    else (
                        f"the contact has no activity in this room, so this "
                        f"{criteria.get('family')} filter cannot be satisfied"
                    )
                ),
                "miss_count": len(misses),
            }
        )

    failing = [row for row in rows if not row["matched"]]
    return {
        "matched": not failing,
        "criteria_count": len(rows),
        "reason": "matched" if not failing else str(failing[0]["reason"]),
        "detail": (
            f"every one of the workflow's {len(rows)} filter(s) is satisfied by this "
            f"contact's activity"
            if not failing
            else "; ".join(
                f"{row['family']}: {row['detail']}" for row in failing
            )
        ),
        "criteria": rows,
        "hit_activity_ids": hit_ids,
        "miss_count": sum(row["miss_count"] for row in rows),
    }


#: How informative each "did not match" reason is, most informative first. Shared
#: with :mod:`dsr.crm_workflows.engine`, which samples the same reasons for a
#: response body.
MISS_PRIORITY: tuple[str, ...] = (
    "refinement_unverifiable",
    "link_url_mismatch",
    "file_name_mismatch",
    "activity_text_mismatch",
    "occurred_before_window",
    "occurred_after_window",
    "malformed_criterion",
    "action_unclassified",
    "family_mismatch",
    "no_events",
)


def _reason_tally(misses: Sequence[Mapping[str, Any]]) -> str:
    """``family_mismatch x4, link_url_mismatch x1`` - the shape of a non-match.

    Counted rather than sampled, because the interesting fact about a rule that
    enrolled nobody is usually "almost everything was the wrong family, and one
    thing was the right family with the wrong URL" - which a list of rows states far
    less clearly than two counts do.
    """
    counts: dict[str, int] = {}
    for row in misses:
        reason = str(row["verdict"]["reason"])
        counts[reason] = counts.get(reason, 0) + 1
    ordered = sorted(
        counts.items(),
        key=lambda item: MISS_PRIORITY.index(item[0])
        if item[0] in MISS_PRIORITY
        else len(MISS_PRIORITY),
    )
    return ", ".join(f"{reason} x{count}" for reason, count in ordered) or "no events at all"


#: Public alias: the tally is part of what the matcher reports, not an internal.
reason_tally = _reason_tally


def describe_vocabulary() -> dict[str, Any]:
    """The matcher's own vocabulary, for the feature's ``/vocabulary`` route."""
    return {
        "match_reasons": dict(REASONS),
        "refinements": list(ALL_REFINEMENTS),
        "unverifiable_policy": (
            "A refinement the event does not carry is reported "
            "refinement_unverifiable and does not match. A match needs positive "
            "evidence, and an unanswerable question must not fire a rule - but it is "
            "reported separately from a miss so a workflow that quietly does nothing "
            "can be diagnosed from the reason."
        ),
        "combination": (
            "Several filters on one workflow are ANDed, which is what 'When filter "
            "criteria is met' means. There is no cap on how many, because the source's "
            "'five different options' counts the families the integration offers, not "
            "the filters a workflow may combine."
        ),
    }
