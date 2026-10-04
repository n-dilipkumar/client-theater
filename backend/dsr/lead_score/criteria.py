"""The criterion: what a contact's score is moved by, and whether an event moves it.

This is the heart of WF-029, and it is the part the research pins hardest. Three
sentences between them fix the entire surface:

* "Click Add criteria for either positive or negative scores."
* "Scroll down until you see the Dock options in the lead score system. Choose the
  Dock property you want to score against."
* "Setup filters and assign score."

plus the two filter recommendations:

* "For activities like Clicks, Downloads, Interactions and Views, we recommend using
  the 'Occurred' filter as a baseline. From there, you can add more refinement around
  the link name or file name."
* "For MAP activity, you can also refine by 'Occurred', but then also refine by task
  name to give certain tasks more weight than others."

:func:`~dsr.lead_score.vocabulary.describe` publishes the resulting matrix,
:func:`parse_criterion` validates against it, and this module's :func:`matches` is
the only thing that decides. The editor a client builds and the evaluator therefore
cannot disagree about what is legal.

Three outcomes, not two
-----------------------

:func:`matches` distinguishes **matched**, **did not match**, and **could not be
checked**, and the third is not folded into the second.

* A filter the event simply does not satisfy is a miss. ``occurred`` outside the
  window, a ``link_name`` that is not the one named, a ``file_name`` that differs.
* A filter whose input the event **does not carry** cannot be checked. A download
  filter refined by ``file_name`` evaluated against an event with no file name is not
  a "no". It is an unanswered question.

A match needs positive evidence, so an unverifiable filter does not match - and the
reason says so. The alternative, letting it pass, is a rule that scores a contact on
evidence nobody supplied, and a "score a contact who downloads the pricing pack" rule
that fires because the event happened to omit a field is worse than one that visibly
never fires. Letting it fail silently is the other bad option: a team that has not yet
wired ``link_name`` onto its activity rows would see a score that quietly never moves,
with no way to find out why. Naming the reason is what makes the edge debuggable.

:func:`lint_criterion` is the other half. It reports what a criterion *does* score
before a single buyer has done anything - a filter with no ``Occurred`` baseline
scores a contact on any activity of any age, which is a real and usually unintended
thing to build.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.lead_score.activity import criterion_task_name, occurred_window, parse_timestamp
from dsr.lead_score.errors import InvalidScoreValue, LeadScoreError
from dsr.lead_score.vocabulary import (
    ALL_REFINEMENTS,
    BASELINE_QUOTE,
    BASELINE_REFINEMENT,
    REFINEMENTS,
    require_bucket,
    require_family,
    require_refinement,
    require_score_property,
)

#: Why a criterion reached its verdict. Served with every decision so a caller can
#: read the reasoning rather than trust a boolean.
REASONS: dict[str, str] = {
    "matched": "The event satisfies every filter of this criterion.",
    "family_mismatch": (
        "The event is a different Dock property. The research offers five and a criterion "
        "scores against one of them."
    ),
    "action_unclassified": (
        "The event's action is in no published Dock property, so no criterion can match it."
    ),
    "occurred_before_window": "The event is older than the criterion's 'Occurred' window.",
    "occurred_after_window": "The event is newer than the criterion's 'Occurred' window.",
    "link_name_mismatch": "The event's link name is not the one the criterion names.",
    "file_name_mismatch": "The event's file name is not the one the criterion names.",
    "task_name_mismatch": "The event's task name is not the one the criterion names.",
    "refinement_unverifiable": (
        "The event does not carry the field this criterion filters on, so it could not be "
        "checked. It is reported rather than counted as a miss, and it does not match: a "
        "score change needs positive evidence."
    ),
    "malformed_criterion": "The criterion's value could not be read as a filter.",
}

SEVERITIES = ("info", "warning")

#: Keys a criterion carries that are not filters.
#:
#: This exists because :func:`parse_criterion` also accepts the *flat* spelling, where
#: the filters sit beside the other fields rather than under ``refinements``. Without an
#: explicit list, ``bucket`` and ``score`` would be read as filter names and refused --
#: and, worse, a caller who nests something under a key nobody expected would have it
#: silently treated as a filter. Every key not in this set is a filter name, and a
#: filter name the family does not publish is refused. A typo in a filter name is
#: therefore loud rather than ignored, which is the whole point.
CRITERION_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "action_family",
        "bucket",
        "criterion_id",
        "created_at",
        "enabled",
        "family",
        "id",
        "integration_id",
        "label",
        "lint",
        "note",
        "refinements",
        "score",
        "score_property",
    }
)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def parse_score_value(value: Any) -> int:
    """Read the score value as a positive whole number.

    The researched order is bucket first, then value: "Click Add criteria for either
    positive or negative scores" and then "assign score". So the value is a magnitude
    and the bucket carries the sign. A negative number is refused rather than read as
    a bucket, because a criterion stored two ways is a criterion whose bucket a reader
    cannot tell from the list.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidScoreValue(
            f"a criterion needs a whole-number score value; got {value!r}. 'assign score "
            f"value' is a magnitude, and the bucket carries the sign."
        )
    if isinstance(value, float) and not float(value).is_integer():
        raise InvalidScoreValue(
            f"a criterion needs a whole-number score value; got {value!r}. A contact score "
            f"is counted in points, and a fractional point is not one the CRM can store."
        )
    whole = int(value)
    if whole <= 0:
        raise InvalidScoreValue(
            f"a criterion needs a score value above zero; got {whole}. Choose the negative "
            f"bucket instead of a negative number, so the stored criterion says which way "
            f"its points move."
        )
    return whole


def parse_criterion(payload: Any) -> dict[str, Any]:
    """Validate one criterion against the researched surface, or refuse it.

    The order of the checks matters and is the researched flow in order: the Dock
    property first, because nothing about a filter is meaningful without one; then the
    filter names against that property's published row; then the filter *values*,
    because a filter the property publishes can still be given a value that is not a
    filter; then the bucket; then the score value, which is the step the research
    reaches last ("Setup filters and assign score").
    """
    if not isinstance(payload, Mapping):
        raise LeadScoreError(
            "a criterion must be a JSON object naming a Dock property, a bucket and a score"
        )

    family = require_family(payload.get("family") or payload.get("action_family"))

    raw_filters = payload.get("refinements")
    if raw_filters is None:
        # Also accept the flat spelling, because a client building a form will produce
        # one or the other and neither is wrong. Anything that is not a known
        # criterion field is taken to be a filter name, and an unpublished one is
        # refused - so a mistyped filter is reported rather than dropped.
        raw_filters = {
            key: value for key, value in payload.items() if key not in CRITERION_METADATA_KEYS
        }
    if not isinstance(raw_filters, Mapping):
        raise LeadScoreError("refinements must be an object")

    refinements: dict[str, Any] = {}
    for key, value in raw_filters.items():
        if value is None:
            continue
        name = require_refinement(family, key)
        if name == "occurred" and occurred_window(value) is None:
            raise LeadScoreError(
                f"the 'Occurred' filter of {family} needs a UTC date, a timestamp, or a "
                f"{{from, to}} object; got {value!r}"
            )
        if name in ("link_name", "file_name", "task_name"):
            if not isinstance(value, str) or not value.strip():
                raise LeadScoreError(
                    f"the {name!r} filter of {family} needs the exact text to match; got {value!r}"
                )
        refinements[name] = value

    bucket = require_bucket(payload.get("bucket"))
    score = parse_score_value(payload.get("score"))
    score_property = require_score_property(payload.get("score_property"))

    return {
        "family": family,
        "label": str(payload.get("label") or "").strip(),
        "refinements": refinements,
        "bucket": bucket,
        "score": score,
        "score_property": score_property,
        "property_resolved": score_property == _published_property(),
    }


def _published_property() -> str:
    """The one contact property this build writes to.

    A local import would be a cycle risk for no benefit: the value is a constant in
    :mod:`dsr.lead_score.vocabulary`, and this wrapper exists so
    ``property_resolved`` reads as a question rather than as a comparison.
    """
    from dsr.lead_score.vocabulary import SCORE_PROPERTY

    return SCORE_PROPERTY


# --------------------------------------------------------------------------- #
# Lint
# --------------------------------------------------------------------------- #


def lint_criterion(criterion: Mapping[str, Any]) -> list[dict[str, Any]]:
    """What this criterion scores before a single buyer has done anything.

    The interesting ones are ``no_occurred_filter`` and ``unresolved_property``. The
    research *recommends* the ``Occurred`` baseline rather than requiring it, so this
    is a warning and the criterion still saves - the difference matters, because a
    refusal here would block a criterion the sources permit.

    ``no_refinement`` is the blunter version of the same fact: a criterion with no
    filter at all scores a contact on any activity of any age in that property, which
    is almost never what somebody meant.
    """
    warnings: list[dict[str, Any]] = []
    family = str(criterion.get("family") or "")
    refinements = criterion.get("refinements") or {}

    if not refinements:
        warnings.append(
            {
                "code": "no_refinement",
                "severity": "info",
                "field": None,
                "message": (
                    f"Nothing narrows this {family} criterion, so it scores every {family} "
                    f"event in the room, of any age. The research lets you filter it by "
                    f"{', '.join(REFINEMENTS.get(family, ())) or 'nothing'}."
                ),
            }
        )

    if BASELINE_REFINEMENT not in refinements:
        warnings.append(
            {
                "code": "no_occurred_filter",
                "severity": "warning",
                "field": BASELINE_REFINEMENT,
                "message": (
                    f"Without an 'Occurred' filter this criterion scores activity of any age, "
                    f"including events from before the workspace was connected to the "
                    f"deal. The research recommends it as a baseline: {BASELINE_QUOTE}"
                ),
            }
        )

    if criterion.get("property_resolved") is False:
        warnings.append(
            {
                "code": "unresolved_property",
                "severity": "warning",
                "field": "score_property",
                "message": (
                    f"This criterion writes to contact property "
                    f"{criterion.get('score_property')!r}, which this build does not provision. "
                    f"The criterion is saved and its points are computed, but no HubSpot "
                    f"property of that name is written by this build. The research's "
                    f"extensibility note says third parties write their own contact "
                    f"properties, so the name is kept rather than refused."
                ),
            }
        )

    score = criterion.get("score")
    if isinstance(score, int) and score > 50:
        warnings.append(
            {
                "code": "large_score_value",
                "severity": "info",
                "field": "score",
                "message": (
                    f"{score} points for one matching event is a large weight. The research "
                    f"says 'assign score' and gives no scale, so this build neither caps the "
                    f"value nor judges it. Check it is the weight you meant."
                ),
            }
        )

    return warnings


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


def _norm(value: str) -> str:
    return " ".join(value.split()).strip().lower()


def _check_refinement(name: str, value: Any, event: Mapping[str, Any]) -> tuple[str | None, str]:
    """One filter against one event.

    Returns ``(reason, detail)`` where ``reason`` is ``None`` when the filter holds,
    and otherwise a key of :data:`REASONS`. The detail is always human-readable,
    because these strings end up in a UI next to a criterion a person is trying to
    debug.
    """
    if name == "occurred":
        window = occurred_window(value)
        if window is None:  # already refused at parse time; belt and braces
            return "malformed_criterion", f"the Occurred filter {value!r} is unreadable"
        at = parse_timestamp(event.get("occurred_at"))
        if at is None:
            return (
                "refinement_unverifiable",
                "the event carries no readable timestamp, so the 'Occurred' filter cannot "
                "be checked against it",
            )
        start, end = window
        if start is not None and at < start:
            return (
                "occurred_before_window",
                f"the event happened at {event.get('occurred_at')}, before the filter's window "
                f"opens",
            )
        if end is not None and at > end:
            return (
                "occurred_after_window",
                f"the event happened at {event.get('occurred_at')}, after the filter's window "
                f"closes",
            )
        return None, f"the event at {event.get('occurred_at')} is inside the filter's window"

    if name == "link_name":
        actual = event.get("link_name")
        if not actual:
            return (
                "refinement_unverifiable",
                "the event carries no link name, so a link-name filter cannot be checked",
            )
        if str(actual).strip() != str(value).strip():
            return "link_name_mismatch", f"the event's link name is {actual!r}, not {value!r}"
        return None, f"the event's link name is {actual!r}"

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

    if name == "task_name":
        actual = event.get("task_name") or event.get("activity_text")
        if not actual:
            return (
                "refinement_unverifiable",
                "the event carries no task name, so a task-name filter cannot be checked",
            )
        wanted = criterion_task_name(value)
        named = event.get("task_name") or actual
        if wanted is not None and _norm(str(named)) == _norm(wanted):
            return None, f"the event is {actual!r}"
        # No documented shape: compare the raw text, so a filter this product did not
        # document still works rather than being refused for being unfamiliar.
        if wanted is None and _norm(str(actual)) == _norm(str(value)):
            return None, f"the event's task text is {actual!r}"
        return "task_name_mismatch", f"the event's task name is {actual!r}, not {value!r}"

    # Unreachable while the vocabulary and this function agree; kept honest rather
    # than assumed, because a filter added to the matrix without a checker here would
    # otherwise fall through and report "matched".
    return (
        "malformed_criterion",
        f"no matcher exists for the {name!r} filter; it was accepted by the vocabulary and "
        f"cannot be evaluated",
    )


def matches(criterion: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    """Does this event satisfy this criterion? With the reasoning.

    Every filter is evaluated and reported, including the ones that held, so a caller
    can see which part of a compound filter did the work. A criterion with no filters
    still has to be in the right Dock property - "choose the Dock property you want to
    score against" is a statement about one property, not about all activity.
    """
    family = str(criterion.get("family") or "")
    refinements = criterion.get("refinements") or {}

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


#: How informative each "did not match" reason is, most informative first. Shared
#: with :mod:`dsr.lead_score.engine`, which samples the same reasons for a response
#: body.
MISS_PRIORITY: tuple[str, ...] = (
    "refinement_unverifiable",
    "link_name_mismatch",
    "file_name_mismatch",
    "task_name_mismatch",
    "occurred_before_window",
    "occurred_after_window",
    "malformed_criterion",
    "action_unclassified",
    "family_mismatch",
    "no_events",
)


def tally_reasons(misses: Any) -> str:
    """``family_mismatch x4, link_name_mismatch x1`` - the shape of a non-match.

    Counted rather than sampled, because the interesting fact about a criterion that
    moved nobody's score is usually "almost everything was the wrong Dock property, and
    one thing was the right property with the wrong link" - which a list of rows states
    far less clearly than two counts do.
    """
    counts: dict[str, int] = {}
    for row in misses or ():
        if not isinstance(row, Mapping):
            continue
        reason = str(row.get("reason") or "")
        if not reason:
            continue
        counts[reason] = counts.get(reason, 0) + 1
    ordered = sorted(
        counts.items(),
        key=lambda item: (
            MISS_PRIORITY.index(item[0]) if item[0] in MISS_PRIORITY else len(MISS_PRIORITY)
        ),
    )
    return ", ".join(f"{reason} x{count}" for reason, count in ordered) or "no events at all"


def describe_matcher() -> dict[str, Any]:
    """The matcher's own vocabulary, for the feature's ``/vocabulary`` route."""
    return {
        "match_reasons": dict(REASONS),
        "refinements": list(ALL_REFINEMENTS),
        "unverifiable_policy": (
            "A filter the event does not carry is reported refinement_unverifiable and does "
            "not match. A score change needs positive evidence, and an unanswerable question "
            "must not move a score - but it is reported separately from a miss so a criterion "
            "that quietly does nothing can be diagnosed from the reason."
        ),
        "baseline_policy": (
            "The research recommends 'Occurred' as a baseline, so a criterion without one is "
            "warned about and still saves. A recommendation is not a requirement, and refusing "
            "here would block a criterion the sources permit."
        ),
    }
