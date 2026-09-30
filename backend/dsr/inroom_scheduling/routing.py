"""Routing forms: ``GET /v2/routing-forms/slots``, and what it must not do.

One sentence from the research governs this module, and it is a sentence about
what does *not* happen:

    ``GET /v2/routing-forms/slots`` (Calculate slots based on routing form
    response) - "It will not actually save the response just return the routed
    event type and slots when it can be booked."

So this module has no write path at all. Not "writes nothing by default", not
"writes nothing when read-only" - there is no function here that touches the
store. A prospect filling in a routing form is answering a question to be shown
the right calendar, and a stored answer they never submitted is a record of
something they did not do. The endpoint is a read, it is audited as a read if it
is audited at all, and a test asserts that driving it leaves the record count
unchanged.

The catch-all
-------------

The build brief is explicit that a rule which does not fall through is a bug
someone will hit in production, and this workflow is exactly where that bites: a
routing form whose rules do not match a response either books nobody - and the
prospect is left on a form that appears broken - or books with whatever event
type happened to be first, which is worse, because the meeting goes to the wrong
host and nobody notices until the call.

So the fall-through is explicit and total:

1. rules are evaluated in order, and the first match wins;
2. a response matching no rule falls through to the form's ``fallback``, which is
   a required field of the form;
3. a form with no fallback cannot be created.

There is no fourth case. A routing form in this product always answers with an
event type, and if the answer is "this one, because nothing else matched" the
response says so in ``matched_rule: null`` with the fallback named - rather than
looking like a rule matched.

The other decision here is the operator set. The research never says what a
routing rule looks like, so :data:`OPERATORS` is this build's, published and
registered as an inference. They are the comparisons a sales form can actually
need, and each carries the shape of its arguments so a client renders a rule
editor from the same list the evaluator uses.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.inroom_scheduling.errors import RoutingError
from dsr.inroom_scheduling.schedules import iso
from dsr.inroom_scheduling.vocabulary import ROUTING_SLOTS_QUOTE

#: The comparisons a routing rule can make. This build's, not the research's - see
#: the ``routing-operators`` inference. Each entry names the shape of its
#: arguments so a client can render a rule editor without hard-coding a list.
OPERATORS: Mapping[str, str] = {
    "equals": "the answer equals the rule's value, case-insensitively",
    "not_equals": "the answer differs from the rule's value",
    "contains": "the rule's value appears anywhere in the answer",
    "starts_with": "the answer begins with the rule's value",
    "greater_than": "the answer is a number above the rule's value",
    "less_than": "the answer is a number below the rule's value",
    "in": "the answer is one of a list of values",
    "exists": "the answer is present and not blank, whatever it says",
}

#: The order rules are evaluated in, and the only comparison a rule can make that
#: needs no value: a first match wins, so a form author can see the shape of their
#: own form by reading it top to bottom.
def require_operator(operator: str) -> str:
    if operator not in OPERATORS:
        raise RoutingError(
            f"unknown routing operator {operator!r}; the published set is "
            + ", ".join(sorted(OPERATORS))
        )
    return operator


def normalise_form(spec: Mapping[str, Any], *, field: str = "form") -> dict[str, Any]:
    """A routing form, validated.

    Three things are required, and the third is the one the brief's fall-through
    rule turns into a hard requirement:

    * at least one ``rules`` entry, so the form is a form;
    * each rule names a field, an operator from :data:`OPERATORS`, and an
      ``eventTypeId`` to route to;
    * a ``fallbackEventTypeId``, so a response matching no rule still reaches a
      host.

    The fallback is not optional here, and refusing to create a form without one is
    the whole point: a form that can match nothing and route nowhere is a form
    that loses a prospect, and the failure is invisible until somebody reports
    that "the routing form does nothing".
    """
    body = dict(spec or {})
    name = str(body.get("name") or "").strip()
    if not name:
        raise RoutingError(f"{field}.name is required")

    raw_rules = body.get("rules")
    if not isinstance(raw_rules, Sequence) or isinstance(raw_rules, (str, bytes)) or not raw_rules:
        raise RoutingError(
            f"{field}.rules must be a non-empty list; a routing form with no rules matches nothing"
        )

    rules: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_rules):
        if not isinstance(raw, Mapping):
            raise RoutingError(f"{field}.rules[{index}] must be an object")
        field_name = str(raw.get("field") or "").strip()
        if not field_name:
            raise RoutingError(f"{field}.rules[{index}].field is required; a rule needs a question to answer")
        operator = require_operator(str(raw.get("operator") or "").strip())
        event_type_id = str(raw.get("eventTypeId") or raw.get("event_type_id") or "").strip()
        declines = raw.get("fallback") is True
        if not event_type_id and not declines:
            raise RoutingError(
                f"{field}.rules[{index}] needs an eventTypeId. A rule that routes nowhere has to say so "
                'with "fallback": true, which is a decision somebody made; an empty eventTypeId on a '
                "rule that does not decline would be a typo."
            )
        rule: dict[str, Any] = {
            "field": field_name,
            "operator": operator,
            "eventTypeId": event_type_id or None,
            "label": raw.get("label"),
        }
        if operator not in ("exists",):
            if "value" not in raw or raw.get("value") in (None, ""):
                raise RoutingError(
                    f"{field}.rules[{index}] needs a value for the {operator!r} operator; "
                    "only 'exists' matches without one"
                )
            rule["value"] = raw.get("value")
        if raw.get("fallback") is not None:
            # A rule may opt out of the form's fallback and route to nothing, which
            # is how a form expresses "we do not take meetings of this shape".
            # Recorded as an explicit choice rather than inferred from an empty
            # eventTypeId, so a deliberate decline and a typo stay distinguishable.
            rule["fallback"] = bool(raw.get("fallback"))
        rules.append(rule)

    fallback_event_type_id = str(
        body.get("fallbackEventTypeId") or body.get("fallback_event_type_id") or ""
    ).strip()
    if not fallback_event_type_id:
        raise RoutingError(
            f"{field}.fallbackEventTypeId is required. A routing form must end in a catch-all: a "
            "response that matches no rule still has to reach a host, or the prospect is left on a "
            f"form that appears broken. {ROUTING_SLOTS_QUOTE}"
        )

    # ``fields`` is the form's own list of questions, which may be plain names or
    # objects with a name and a label. Both shapes are accepted because a form
    # author writing JSON by hand writes strings and a page rendering an editor
    # writes objects, and the list is presentation rather than behaviour.
    fields: set[str] = set()
    for entry in body.get("fields") or []:
        name_of_field = str(entry.get("name") if isinstance(entry, Mapping) else entry)
        if name_of_field:
            fields.add(name_of_field)

    return {
        "name": name,
        "formId": body.get("formId") or body.get("form_id") or body.get("id"),
        "description": body.get("description"),
        "rules": rules,
        "fallbackEventTypeId": fallback_event_type_id,
        "fields": sorted(fields),
    }


def _as_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def rule_matches(rule: Mapping[str, Any], responses: Mapping[str, Any]) -> bool:
    """Does one rule match a set of answers?

    String comparisons are case-insensitive and whitespace-trimmed, because a
    routing answer comes from a free-text input and "North America" and " north
    america " are the same answer. The numeric operators refuse rather than
    coercing: a form answering "a few" to a headcount question should route on
    ``exists``, not silently fail a numeric comparison and drop to the fallback.
    """
    field = str(rule.get("field"))
    if field not in responses:
        return False
    answer = responses[field]
    operator = str(rule.get("operator"))
    expected = rule.get("value")

    if operator == "exists":
        return answer not in (None, "") and not str(answer).strip() == ""

    if operator == "greater_than":
        actual, target = _as_number(answer), _as_number(expected)
        return actual is not None and target is not None and actual > target
    if operator == "less_than":
        actual, target = _as_number(answer), _as_number(expected)
        return actual is not None and target is not None and actual < target

    text = str(answer).strip().lower()
    if operator == "equals":
        return text == str(expected).strip().lower()
    if operator == "not_equals":
        return text != str(expected).strip().lower()
    if operator == "contains":
        return str(expected).strip().lower() in text
    if operator == "starts_with":
        return text.startswith(str(expected).strip().lower())
    if operator == "in":
        options = expected if isinstance(expected, (list, tuple)) else str(expected).split(",")
        return text in {str(option).strip().lower() for option in options}
    # Unreachable: normalise_form refuses an unknown operator, and an operator
    # added to OPERATORS without a branch here would be caught by that same
    # refusal at form creation. Raised rather than defaulted to False, so a future
    # operator is a loud gap and not a rule that silently never matches.
    raise RoutingError(f"operator {operator!r} is published but not implemented")


def route(
    form: Mapping[str, Any], responses: Mapping[str, Any]
) -> dict[str, Any]:
    """Route a set of answers to an event type. Total, always.

    Three outcomes, and there is no fourth:

    * a rule matches and does not opt out - ``matched_rule`` is that rule;
    * a rule matches and opts out of the fallback - ``routed`` is false, and the
      form author has said deliberately that this shape is not taken;
    * nothing matches - the fallback, with ``matched_rule: null``.

    The distinction between the last two is the point of the module. Both leave
    the prospect without a calendar, but one is a decision somebody made and the
    other is the catch-all doing its job, and a page that cannot tell them apart
    will show the same dead end for both.
    """
    answers = {str(key): value for key, value in dict(responses or {}).items()}
    for index, rule in enumerate(form.get("rules") or []):
        if not rule_matches(rule, answers):
            continue
        if rule.get("fallback") is True:
            return {
                "routed": False,
                "eventTypeId": None,
                "matched_rule": index,
                "matched_field": rule.get("field"),
                "reason": "rule_declined",
                "detail": (
                    f"rule {index} matched on {rule.get('field')!r} and opted out of the "
                    "fallback, so this answer is not routed"
                ),
            }
        return {
            "routed": True,
            "eventTypeId": rule.get("eventTypeId"),
            "matched_rule": index,
            "matched_field": rule.get("field"),
            "reason": "rule_matched",
            "detail": f"rule {index} matched on {rule.get('field')!r}",
        }

    return {
        "routed": True,
        "eventTypeId": form.get("fallbackEventTypeId"),
        "matched_rule": None,
        "matched_field": None,
        "reason": "fallback",
        "detail": (
            "no rule matched, so the form's catch-all routed this answer; every routing form "
            "carries one so an unmatched answer still reaches a host"
        ),
    }


def routed_slots_response(
    routing: Mapping[str, Any], slots: Sequence[Mapping[str, Any]], *, form_id: str
) -> dict[str, Any]:
    """The ``GET /v2/routing-forms/slots`` body.

    Named for what the research says it does: it returns the routed event type and
    the slots "when it can be booked". When the route declines, the slots are empty
    and ``routed`` is false - the honest answer, rather than the slots of an event
    type the prospect was not routed to.
    """
    return {
        "formId": form_id,
        "routed": bool(routing.get("routed")),
        "eventTypeId": routing.get("eventTypeId"),
        "matched_rule": routing.get("matched_rule"),
        "matched_field": routing.get("matched_field"),
        "reason": routing.get("reason"),
        "detail": routing.get("detail"),
        "slot_count": len(slots) if routing.get("routed") else 0,
        "slots": list(slots) if routing.get("routed") else [],
        "saved": False,
        "note": ROUTING_SLOTS_QUOTE,
    }


def response_at(now: Any) -> str:
    """The moment a routing response was evaluated, for the response body.

    The response is not saved, so this exists only to say when the answer was
    computed - which is the one thing about a read that is worth recording and the
    one thing about it that is not persistence.
    """
    return iso(now)
