"""The filter and sort engine for a saved view.

The research fixes the *capability* - "You can **filter** and **sort** views by
owner, workspace creation date, recent client activity, CRM stage, workspace type"
- and names two API parameters that partition the filters:
``workspaceFilters`` and ``workspaceDomainFilters``. It does not define the
matching semantics, the operator set, or what happens to a filter nobody can
read, so those are this build's decisions and are listed in
:mod:`dsr.triage.inferences`.

The partition
-------------
A view carries two groups, and the split is between a value the workspace record
carries and a value that arrived from a joined resource:

``workspace_filters``
    Predicates over ``dock.*`` and ``engagement.*`` - things the workspace knows
    about itself.
``workspace_domain_filters``
    Predicates over ``salesforce.*``, ``hubspot.*`` and ``order_form.*`` - things
    that only exist because a CRM object or an order form was joined in.

Each group joins its own conditions with ``and`` or ``or``, and ``match``
decides how the two groups combine: ``all`` requires both, ``any`` requires
either. **Active Pipeline needs ``any``**, because its two arms are alternatives
and a rule that does not fall through is a bug somebody hits in production: a
``Sales`` workspace with no CRM connection is in the pipeline, and so is a
``General`` workspace with a connected opportunity.

Failing in the right direction
------------------------------
A condition this module cannot read is **dropped and reported**, never silently
ignored and never fatal. Dropping a condition can only widen the result set, and
a widened set is visible - the row count is wrong in a way a reader can see - so
the failure is recoverable. The alternative, failing the whole view, would mean
one typo empties a team's pipeline table.

A *sort* is the opposite and is refused rather than approximated: a caller asked
for an order, and answering with a different one is a wrong answer, not a partial
one. That asymmetry is deliberate and is recorded as
``unknown-filter-is-dropped-unknown-sort-is-refused``.

Unknown *fields*, on the other hand, are evaluated, not refused. The research's
own extensibility claim is that "the column/filter set is user-defined so new CRM
fields flow through automatically", so a field nobody has catalogued yet has to
be filterable; it is compared without a declared type, using the value's own
shape. What is refused is a *known* field paired with an operator its type cannot
support - ``contains`` on a count - because that is a mistake in the request
rather than a field that has not arrived yet.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime
from typing import Any

from dsr.triage import vocabulary as vocab
from dsr.triage.errors import TriageError
from dsr.triage.fields import as_number, as_text, days_ago, now, parse_time

# --------------------------------------------------------------------------- #
# Problems
# --------------------------------------------------------------------------- #

#: The ways a condition can be unreadable. Named, because a client renders the
#: message and a reviewer argues with the behaviour by name.
PROBLEM_UNKNOWN_OPERATOR = "unknown_operator"
PROBLEM_BAD_ARITY = "bad_arity"
PROBLEM_MISSING_VALUE = "missing_value"
PROBLEM_INAPPLICABLE_OPERATOR = "inapplicable_operator"
PROBLEM_UNRESOLVED_ME = "unresolved_me"
PROBLEM_INVALID_WINDOW = "invalid_window"

#: How the two groups combine.
MATCH_ALL = vocab.MATCH_ALL
MATCH_ANY = vocab.MATCH_ANY
MATCH_MODES = vocab.MATCH_MODES

#: The two groups, as a filter set addresses them. The stored record uses the
#: researched parameter names; this engine calls them ``workspace`` and
#: ``domain`` because that is what they mean.
WORKSPACE = "workspace"
DOMAIN = "domain"


@dataclass
class Group:
    """One filter group: a join and the conditions it applies."""

    side: str
    join: str
    conditions: list[dict[str, Any]] = dataclass_field(default_factory=list)
    #: Conditions that survived compilation, in the order they were written.
    usable: list[dict[str, Any]] = dataclass_field(default_factory=list)

    def matches(self, row: Mapping[str, Any], context: FilterContext) -> bool:
        """Does this group hold for ``row``?

        An empty group matches. That is what makes **All Workspaces** the
        catch-all the other four are a narrowing of: no conditions means no
        restriction, not no rows.
        """
        if not self.usable:
            return True
        outcomes = [evaluate(condition, row, context) for condition in self.usable]
        if self.join == "or":
            return any(outcomes)
        return all(outcomes)

    def to_dict(self) -> dict[str, Any]:
        return {"join": self.join, "conditions": [dict(condition) for condition in self.conditions]}


@dataclass
class FilterContext:
    """Everything an operator may consult besides the row itself.

    ``actor`` is the user whose view this is, which is what ``$me`` resolves to.
    ``reference`` is "now" for a recency filter, injectable so a test - or a
    replay of a stored decision - does not depend on the wall clock.
    """

    actor: str | None = None
    reference: datetime | None = None

    def anchor(self) -> datetime:
        """The instant a recency window is measured back from.

        Injectable so a test - or a replay of a stored decision - does not depend
        on the wall clock, which is what makes "in the last 7 days" assertable.
        """
        return self.reference or now()


@dataclass
class FilterPlan:
    """A compiled view filter: two groups, how they combine, what went wrong."""

    workspace: Group
    domain: Group
    match: str = MATCH_ALL
    problems: list[dict[str, Any]] = dataclass_field(default_factory=list)

    def matches(self, row: Mapping[str, Any], context: FilterContext) -> bool:
        if self.match == MATCH_ANY:
            return self.workspace.matches(row, context) or self.domain.matches(row, context)
        return self.workspace.matches(row, context) and self.domain.matches(row, context)

    def to_dict(self) -> dict[str, Any]:
        return {
            "match": self.match,
            "workspace_filters": self.workspace.to_dict(),
            "workspace_domain_filters": self.domain.to_dict(),
            "problems": [dict(problem) for problem in self.problems],
        }


# --------------------------------------------------------------------------- #
# Compilation
# --------------------------------------------------------------------------- #


def compile_group(side: str, raw: Any, problems: list[dict[str, Any]]) -> Group:
    """Turn a stored filter group into something evaluable, reporting what broke.

    Never raises. A filter that cannot be read must not take the whole view down
    with it, so every refusal lands in ``problems`` and the condition is left out
    of the compiled group.
    """
    group = Group(side=side, join="and", conditions=[])
    if raw is None:
        return group
    if not isinstance(raw, Mapping):
        problems.append(
            {
                "kind": "malformed_group",
                "group": side,
                "detail": f"{side} filters must be an object with join and conditions",
            }
        )
        return group

    join = as_text(raw.get("join"), "and").lower()
    if join not in ("and", "or"):
        problems.append(
            {
                "kind": "malformed_group",
                "group": side,
                "detail": f"join must be 'and' or 'or', got {raw.get('join')!r}",
            }
        )
        join = "and"
    group.join = join

    conditions = raw.get("conditions")
    if conditions is None:
        return group
    if not isinstance(conditions, Sequence) or isinstance(conditions, (str, bytes)):
        problems.append(
            {
                "kind": "malformed_group",
                "group": side,
                "detail": "conditions must be a list",
            }
        )
        return group

    for index, condition in enumerate(conditions):
        if not isinstance(condition, Mapping):
            problems.append(
                {
                    "kind": "malformed_condition",
                    "group": side,
                    "index": index,
                    "detail": "each condition must be an object",
                }
            )
            continue
        compiled = _compile_condition(side, index, condition, problems)
        if compiled is not None:
            group.conditions.append(dict(condition))
            group.usable.append(compiled)
    return group


def _compile_condition(
    side: str, index: int, condition: Mapping[str, Any], problems: list[dict[str, Any]]
) -> dict[str, Any] | None:
    field = as_text(condition.get("field"))
    op = as_text(condition.get("op")).lower()
    value = condition.get("value")

    if not field:
        problems.append(
            {
                "kind": PROBLEM_MISSING_VALUE,
                "group": side,
                "index": index,
                "detail": "condition has no field",
            }
        )
        return None

    spec = vocab.OPERATORS_BY_OP.get(op)
    if spec is None:
        # The one refusal that is about the *operator* rather than the field.
        problems.append(
            {
                "kind": PROBLEM_UNKNOWN_OPERATOR,
                "group": side,
                "index": index,
                "field": field,
                "op": op,
                "detail": f"{op!r} is not an operator this build implements; the condition was left out",
            }
        )
        return None

    known = vocab.column(field)
    if known is not None and spec["applies_to"] and known["type"] not in spec["applies_to"]:
        problems.append(
            {
                "kind": PROBLEM_INAPPLICABLE_OPERATOR,
                "group": side,
                "index": index,
                "field": field,
                "op": op,
                "detail": (f"{op!r} does not apply to {field}, which is a {known['type']} field"),
            }
        )
        return None

    if spec["arity"] == 0:
        if value is not None:
            problems.append(
                {
                    "kind": PROBLEM_BAD_ARITY,
                    "group": side,
                    "index": index,
                    "field": field,
                    "op": op,
                    "detail": f"{op!r} takes no value, but one was given; the value was ignored",
                }
            )
    elif spec["arity"] == 1:
        if value is None and op != "is_not_empty" and op != "is_empty":
            problems.append(
                {
                    "kind": PROBLEM_MISSING_VALUE,
                    "group": side,
                    "index": index,
                    "field": field,
                    "op": op,
                    "detail": f"{op!r} needs a value; the condition was left out",
                }
            )
            return None
        if op == "in_the_last" and (as_number(value) is None or as_number(value) < 0):
            problems.append(
                {
                    "kind": PROBLEM_INVALID_WINDOW,
                    "group": side,
                    "index": index,
                    "field": field,
                    "op": op,
                    "detail": f"in_the_last needs a non-negative number of days, got {value!r}",
                }
            )
            return None
    elif spec["arity"] == -1:
        if not isinstance(value, (list, tuple, set)):
            problems.append(
                {
                    "kind": PROBLEM_BAD_ARITY,
                    "group": side,
                    "index": index,
                    "field": field,
                    "op": op,
                    "detail": f"{op!r} needs a list of values, got {value!r}",
                }
            )
            return None

    return {
        "field": field,
        "op": op,
        "value": list(value) if isinstance(value, (tuple, set)) else value,
    }


def compile_filters(
    workspace_filters: Any = None,
    workspace_domain_filters: Any = None,
    match: Any = None,
) -> FilterPlan:
    """Compile a view's whole filter set.

    ``match`` defaults to ``all``. An unrecognised value is reported and treated
    as ``all``, because ``all`` is the restrictive reading: guessing ``any``
    would put rows in a view that never asked for them.
    """
    problems: list[dict[str, Any]] = []
    mode = as_text(match, MATCH_ALL).lower()
    if mode not in MATCH_MODES:
        problems.append(
            {
                "kind": "malformed_match",
                "detail": f"match must be one of {list(MATCH_MODES)}, got {match!r}; treated as 'all'",
            }
        )
        mode = MATCH_ALL
    return FilterPlan(
        workspace=compile_group(WORKSPACE, workspace_filters, problems),
        domain=compile_group(DOMAIN, workspace_domain_filters, problems),
        match=mode,
        problems=problems,
    )


def normalise_filter_set(payload: Mapping[str, Any]) -> tuple[Any, Any, Any]:
    """Read a filter set out of an arbitrary payload, accepting either spelling.

    ``workspace_filters`` / ``workspace_domain_filters`` is this build's
    snake_case; the researched ``workspaceFilters`` / ``workspaceDomainFilters``
    are the camelCase the source API uses. A client written against the source
    spelling works unchanged, which is the same accommodation WF-016 makes for
    ``target_url`` / ``targetUrl``.
    """
    workspace = payload.get("workspace_filters")
    if workspace is None:
        workspace = payload.get("workspaceFilters")
    domain = payload.get("workspace_domain_filters")
    if domain is None:
        domain = payload.get("workspaceDomainFilters")
    return workspace, domain, payload.get("match")


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


def _is_absent(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return False


def _equal(left: Any, right: Any, kind: str | None) -> bool:
    """Equality that respects the field's declared type where there is one.

    Dates compare as instants, except that when either side is a bare calendar
    date the comparison falls to the calendar day - so filtering
    ``deal_closed_date is 2026-11-15`` matches a stored ``2026-11-15T09:30:00Z``
    rather than silently missing it.
    """
    if kind == vocab.DATE:
        left_time, right_time = parse_time(left), parse_time(right)
        if left_time is not None and right_time is not None:
            if _is_bare_date(left) or _is_bare_date(right):
                return left_time.date() == right_time.date()
            return left_time == right_time
    if kind == vocab.NUMBER:
        left_number, right_number = as_number(left), as_number(right)
        if left_number is not None and right_number is not None:
            return left_number == right_number
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) == bool(right)
    left_number, right_number = as_number(left), as_number(right)
    if left_number is not None and right_number is not None:
        return left_number == right_number
    return as_text(left).casefold() == as_text(right).casefold()


def _is_bare_date(value: Any) -> bool:
    if isinstance(value, datetime):
        return False
    text = as_text(value)
    return len(text) == 10 and text.count("-") == 2


def _ordered(left: Any, right: Any, kind: str | None) -> int | None:
    """``-1`` / ``0`` / ``1``, or ``None`` when the two are not comparable."""
    if kind == vocab.DATE or (kind is None and _is_bare_date(left) and _is_bare_date(right)):
        left_time, right_time = parse_time(left), parse_time(right)
        if left_time is None or right_time is None:
            return None
        return (left_time > right_time) - (left_time < right_time)
    left_number, right_number = as_number(left), as_number(right)
    if left_number is not None and right_number is not None:
        return (left_number > right_number) - (left_number < right_number)
    if kind == vocab.TEXT:
        left_text, right_text = as_text(left).casefold(), as_text(right).casefold()
        return (left_text > right_text) - (left_text < right_text)
    left_text, right_text = as_text(left).casefold(), as_text(right).casefold()
    if not left_text or not right_text:
        return None
    return (left_text > right_text) - (left_text < right_text)


def evaluate(condition: Mapping[str, Any], row: Mapping[str, Any], context: FilterContext) -> bool:
    """Does one compiled condition hold for this row?"""
    field = condition["field"]
    op = condition["op"]
    expected = condition.get("value")
    known = vocab.column(field)
    kind = known["type"] if known is not None else None
    actual = row.get(field)

    if op == "is_empty":
        return _is_absent(actual)
    if op == "is_not_empty":
        return not _is_absent(actual)
    if op == "is":
        return not _is_absent(actual) and _equal(actual, expected, kind)
    if op == "is_not":
        # Requires a value on both sides. "Stage is not Closed Lost" must not
        # sweep in every workspace that has no CRM stage at all, which is what a
        # plain negation would do.
        return (
            not _is_absent(actual)
            and not _is_absent(expected)
            and not _equal(actual, expected, kind)
        )
    if op == "contains":
        return as_text(expected).casefold() in as_text(actual).casefold()
    if op == "in":
        return not _is_absent(actual) and any(
            _equal(actual, candidate, kind) for candidate in expected
        )
    if op == "not_in":
        return not _is_absent(actual) and not any(
            _equal(actual, candidate, kind) for candidate in expected
        )
    if op in ("gt", "gte", "lt", "lte"):
        if _is_absent(actual):
            return False
        order = _ordered(actual, expected, kind)
        if order is None:
            return False
        if op == "gt":
            return order > 0
        if op == "gte":
            return order >= 0
        if op == "lt":
            return order < 0
        return order <= 0
    if op == "in_the_last":
        window = as_number(expected)
        if window is None or window < 0:
            return False
        moment = parse_time(actual)
        if moment is None:
            return False
        anchor = context.anchor()
        return days_ago(window, anchor) <= moment <= anchor
    return False  # unreachable: compilation removed anything unknown


def unresolved_me_problems(plan: FilterPlan) -> list[dict[str, Any]]:
    """Report any ``$me`` still standing in a compiled condition.

    ``$me`` is resolved when a view is *created* from the My Workspaces default,
    so a stored condition should never carry it. A stored one can only come from
    a hand-written record, and silently matching everything for it would be the
    one failure mode this module must not have.
    """
    found: list[dict[str, Any]] = []
    for group in (plan.workspace, plan.domain):
        for condition in group.usable:
            if _mentions_me(condition.get("value")):
                found.append(
                    {
                        "kind": PROBLEM_UNRESOLVED_ME,
                        "group": group.side,
                        "field": condition["field"],
                        "op": condition["op"],
                        "detail": (
                            f"{vocab.ME!r} was never resolved to a user, so this condition "
                            "matches nothing"
                        ),
                    }
                )
    return found


def _mentions_me(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip() == vocab.ME
    if isinstance(value, (list, tuple, set)):
        return any(_mentions_me(item) for item in value)
    return False


def substitute_me_in_raw(raw: Any, actor: str | None) -> Any:
    """Return a copy of a raw filter group with every ``$me`` replaced.

    Applied to the *stored* condition list, not to the compiled one, so that a
    condition this module could not read is still saved verbatim and still
    reports its problem on the next read. Compiling first and saving the result
    would silently tidy away the mistakes it is supposed to surface.

    ``actor`` of ``None`` leaves the group alone: the sentinel then survives to
    storage and :func:`unresolved_me_problems` reports it on read, which is the
    honest outcome for a view with no owner to resolve "mine" against.
    """
    if actor is None or not isinstance(raw, Mapping):
        return raw
    conditions = raw.get("conditions")
    if not isinstance(conditions, (list, tuple)):
        return raw
    swapped = []
    for condition in conditions:
        if isinstance(condition, Mapping) and "value" in condition:
            swapped.append({**condition, "value": _swap_me(condition["value"], actor)})
        else:
            swapped.append(condition)
    return {**raw, "conditions": swapped}


def _swap_me(value: Any, actor: str) -> Any:
    if isinstance(value, str):
        return actor if value.strip() == vocab.ME else value
    if isinstance(value, (list, tuple, set)):
        return [_swap_me(item, actor) for item in value]
    return value


# --------------------------------------------------------------------------- #
# Sorting
# --------------------------------------------------------------------------- #

ASCENDING = "asc"
DESCENDING = "desc"
DIRECTIONS = (ASCENDING, DESCENDING)


def validate_sort(raw: Any) -> dict[str, Any]:
    """Normalise and check a sort spec. Raises rather than approximating.

    The one place in this module that refuses rather than reports, and the
    asymmetry with filters is deliberate: see the module docstring.
    """
    if raw is None:
        return {"field": "dock.name", "direction": ASCENDING}
    if not isinstance(raw, Mapping):
        raise TriageError("sort must be an object with a field and a direction")

    field = as_text(raw.get("field"))
    if not field:
        raise TriageError("sort.field is required")
    if vocab.column(field) is None:
        raise TriageError(
            f"{field!r} is not a published column, so it cannot be sorted by. "
            f"Published columns: {', '.join(vocab.ALL_COLUMN_KEYS)}"
        )

    direction = as_text(raw.get("direction"), ASCENDING).lower()
    if direction not in DIRECTIONS:
        raise TriageError(f"sort.direction must be one of {list(DIRECTIONS)}, got {direction!r}")
    return {"field": field, "direction": direction}


def _sort_component(value: Any, kind: str | None) -> tuple[int, Any] | None:
    """A comparable key for one cell, or ``None`` when the cell has no value.

    ``None`` is kept out of the comparison entirely rather than mapped to a
    sentinel, so a text column sorting alphabetically and a numeric column
    sorting numerically both work without a magic constant per type.
    """
    if _is_absent(value):
        return None
    if kind == vocab.DATE:
        moment = parse_time(value)
        return (0, moment.timestamp()) if moment is not None else None
    if kind == vocab.NUMBER:
        number = as_number(value)
        return (0, number) if number is not None else None
    moment = parse_time(value)
    if moment is not None:
        return (0, moment.timestamp())
    number = as_number(value)
    if number is not None:
        return (0, number)
    return (1, as_text(value).casefold())


def sort_rows(
    rows: Iterable[Any],
    sort: Mapping[str, Any],
    value_of: Callable[[Any], Any],
    key_of: Callable[[Any], str],
) -> list[Any]:
    """Order joined rows for display.

    Generic over the row type on purpose: the engine is pure and knows nothing
    about :class:`~dsr.triage.rows.WorkspaceRow`, so the caller says how to read
    a cell and how to identify a row. That keeps this function testable with a
    plain list of dicts and keeps the join out of the comparison logic.

    **Rows with no value for the sort field always go last, in both
    directions.** A workspace nobody has viewed should not top a table sorted by
    Last Client View because "no value" happened to compare small, and it should
    not sink to the bottom either when the direction flips - a rep scanning for
    the next call to make wants the empty ones out of the way, not at the top of
    the list.

    Ties break on the row id so the order is stable across requests; a triage
    table that reshuffles equal rows on every refresh cannot be read.
    """
    field = sort["field"]
    descending = sort.get("direction", ASCENDING) == DESCENDING
    kind = _sort_kind(field)

    # Two passes, and the order matters. Rows are first put in id order, then
    # stably sorted by the sort field. Python's sort is stable, so rows with equal
    # values keep the id order from the first pass in *both* directions - which a
    # single `reverse=True` pass would not give, because it reverses the tie-break
    # along with the field.
    by_id = sorted(rows, key=key_of)
    with_value: list[Any] = []
    without_value: list[Any] = []
    for row in by_id:
        component = _sort_component(value_of(row), kind)
        if component is None:
            without_value.append(row)
        else:
            with_value.append((row, component))

    with_value.sort(key=lambda pair: pair[1], reverse=descending)
    return [row for row, _component in with_value] + without_value


def _sort_kind(field: str) -> str | None:
    """The declared type of a sort field, or ``None`` for one not catalogued."""
    known = vocab.column(field)
    return known["type"] if known is not None else None


def dedupe_columns(keys: Iterable[Any]) -> tuple[list[str], list[str]]:
    """Order-preserving de-duplication of a column list.

    "Edit and rearrange columns" is an ordered list, so position is meaningful and
    a repeat is a no-op rather than a second column of the same field. Returns
    ``(kept, dropped)`` so the caller can say what it removed.
    """
    kept: list[str] = []
    seen: set[str] = set()
    dropped: list[str] = []
    for raw in keys:
        key = as_text(raw)
        if not key:
            continue
        if key in seen:
            dropped.append(key)
            continue
        seen.add(key)
        kept.append(key)
    return kept, dropped
