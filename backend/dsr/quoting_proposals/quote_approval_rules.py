"""WF-091: the rules a discounted quote goes through approval by, made executable.

Pricing, quoting and proposals. Every function here is pure: it takes values and
returns values, reads nothing and writes nothing. The vocabulary is
:mod:`dsr.quoting_proposals.quote_approval_vocabulary` and the writes are in
:mod:`dsr.quoting_proposals.quote_approval_engine`.

The rules the rest of the workflow leans on
-------------------------------------------

**Filters combine with AND.** The source says "*And these conditions are met*".
:func:`matches_filters` therefore requires every filter in a rule, and reports
which ones matched so a page can show why approval was required. The flow's "**View
approval conditions**" needs that report and it is stored on the enrolment.

**The property is a dotted JSON path.** The flow picks "**[Object] properties** ->
search and pick a property (e.g. quote amount, discount level, SKU)". The store
holds arbitrary JSON and a team may name any property, so
:func:`~quote_approval_vocabulary` declares no list of properties and this module
resolves a path against whatever the record carries. A missing path does not match.
A rule that names a property no quote carries is a rule that never fires, which is
what a seller wrote.

**The approver cap is enforced on the rule.** "Assign up to 10 approvers to review
quotes that match your configured filters." :func:`validate_rule` refuses an
eleventh, and refuses an empty list, because a rule with no approver cannot be
satisfied and would hold every matching quote forever.

**The creator is removed from the approver list at enrolment.** This is the reading
Jev chose in audit ``jev-20261005T064607-13024-67413``, and it is the only reading
that satisfies both sourced sentences at once:

* "If a designated approver creates a quote, and they're the only approver, the
  quote won't require approval. If there are multiple approvers, they'll be removed
  from the approval process."
* "Approvers can't approve their own quotes."

:func:`resolve_approvers` implements the first, and the second follows from it: a
creator who was removed has no vote to cast. The alternative was a rule where every
match needs approval and an emptied list is refused, which satisfies the second
sentence by contradicting the only sourced sentence that describes skipping approval.
That alternative would deadlock a room with one approver, which is a legitimate
configuration in a schema-flexible product where no field is required.

**The requirement is a count, not a position.** "All approvers required or At least
one approver required". :func:`requirement_met` counts approvals against the
resolved list, so an approver removed from the list never has to be waited for.

**Re-submission clears every decision.** "if there's more than one approver, and one
approver requests changes, every approver will need to approve the quote again when
the quote is re-submitted." Quoted whole. :func:`new_enrolment` starts each
submission with no decisions carried forward, which is that sentence, and the
single-approver case is recorded as a derived reading in the inferences module.

**Only an approved quote may be shared.** "only on approval can the quote be
**Share**d (state ``Shared``) and sent to the buyer." :func:`require_shareable`
raises rather than returning a boolean, so the caller cannot forget to check it.

**A published quote is locked, and three statuses release it.** "To modify any
properties after you've published a quote, you must first update the ``hs_status``
of the quote back to ``DRAFT``, ``PENDING_APPROVAL``, or ``REJECTED``."
:func:`require_editable` reads that sentence.

What this module does not decide
--------------------------------

Whether a discount is commercially reasonable, and whether an approver is the right
person. It matches properties against configured filters, resolves a list of
approvers, and counts votes. It never asserts that a price is right or that a
human should sign.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from dsr.quoting_proposals import quote_approval_vocabulary as vocab

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# Declared here and raised by nothing else in the product. That is what makes it
# safe for the feature module to map them: a handler for a shared type such as
# ``ValueError`` would intercept that exception across the whole application.


class ApprovalRefusal(ValueError):
    """A rule, a filter or a decision this workflow will not accept.

    Carries a code the feature module maps to a status and a field-keyed map,
    because a page puts each message beside the input that caused it rather than in
    one combined sentence.
    """

    def __init__(self, code: str, message: str | None = None, errors: Mapping[str, str] | None = None):
        status, detail = vocab.ERROR_CODES.get(code, (422, code))
        super().__init__(message or detail)
        self.code = code
        self.status = status
        self.detail = message or detail
        self.errors: dict[str, str] = dict(errors or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "detail": self.detail,
            "status": self.status,
            "errors": self.errors,
        }


class ApprovalNotFound(LookupError):
    """No such rule or no such enrolment.

    Its own type rather than the store's ``RecordNotFound``, for the same reason as
    every other workflow: a feature may only map error types it raises itself.
    """

    code = "unknown_approval_request"
    status = 404

    def __init__(self, code: str, label: str, record_id: str) -> None:
        super().__init__(f"{label} {record_id} not found")
        self.code = code
        self.record_id = record_id


def refuse(code: str, message: str | None = None, **errors: str) -> ApprovalRefusal:
    """A refusal of a known code, built at the call site that raised it."""
    return ApprovalRefusal(code, message, errors or None)


# --------------------------------------------------------------------------- #
# Instants
# --------------------------------------------------------------------------- #


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime | None = None) -> str:
    """An ISO 8601 instant in UTC, with the milliseconds kept."""
    value = moment or utcnow()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


def normalise_key(value: Any, field: str = "name") -> str:
    """A lower-cased identifier, checked for shape.

    A rule key is an API-facing name, so it is held to a shape rather than stored as
    anything at all. An empty or punctuated key would make a URL this product has to
    route on ambiguous.
    """
    text = str(value or "").strip().lower().replace(" ", "-")
    if not text:
        raise refuse("rule_needs_a_name", **({field: vocab.ERROR_CODES["rule_needs_a_name"][1]}))
    if not _SLUG_RE.match(text):
        raise refuse(
            "rule_needs_a_name",
            f"{text!r} is not a usable rule name.",
            **{field: "A rule name uses lower-case letters, digits, a hyphen or an underscore, and is between 2 and 64 characters."},
        )
    return text


def normalise_object(value: Any) -> str:
    """The object a filter reads.

    The three the research names are accepted and so is anything else, because the
    flow's dropdown is populated from the objects a deployment has and "Filters can
    target any object (quote, line item, deal) and any standard or custom property"
    says the set is not closed by this product. Normalising to a lower-case slug
    keeps a rule written by hand and a rule written by a page the same value.
    """
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not text:
        raise refuse("unknown_filter_object", **{"object": vocab.ERROR_CODES["unknown_filter_object"][1]})
    return text


def normalise_operator(value: Any) -> str:
    """One known operator, or a refusal naming the set."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text not in vocab.OPERATORS:
        raise refuse("unknown_operator", **{"operator": vocab.ERROR_CODES["unknown_operator"][1]})
    return text


def normalise_requirement(value: Any) -> str:
    """One of the two sourced requirements, or a refusal naming both."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in ("all_approvers_required", "all_approvers"):
        text = vocab.REQUIREMENT_ALL
    elif text in ("at_least_one_approver_required", "at_least_one", "any"):
        text = vocab.REQUIREMENT_ANY
    if text not in vocab.APPROVER_REQUIREMENTS:
        raise refuse("unknown_requirement", **{"requirement": vocab.ERROR_CODES["unknown_requirement"][1]})
    return text


def normalise_decision(value: Any) -> str:
    """``approve`` or ``request_changes``, or a refusal naming both."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in ("reject", "rejected", "changes", "request_changes"):
        text = vocab.DECISION_REQUEST_CHANGES
    if text not in vocab.DECISIONS:
        raise refuse("unknown_decision", **{"decision": vocab.ERROR_CODES["unknown_decision"][1]})
    return text


def normalise_trigger(value: Any) -> str:
    """One known trigger, or a refusal naming the three."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in ("request_approval", "submit", "submit_approval"):
        text = vocab.TRIGGER_SUBMIT
    if text not in vocab.TRIGGERS:
        raise refuse("unknown_trigger", **{"trigger": vocab.ERROR_CODES["unknown_trigger"][1]})
    return text


def normalise_channel(value: Any) -> str:
    """One known notification channel, or a refusal naming the five."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in ("bell", "in_app", "inapp"):
        text = vocab.CHANNEL_IN_APP
    if text not in vocab.NOTIFICATION_CHANNELS:
        raise refuse("unknown_channel", **{"channel": vocab.ERROR_CODES["unknown_channel"][1]})
    return text


# --------------------------------------------------------------------------- #
# Reading a property
# --------------------------------------------------------------------------- #


def read_path(payload: Any, path: str) -> Any:
    """Read one dotted JSON path, or ``None`` when any step of it is missing.

    ``None`` is a real answer and not an error. The store holds arbitrary JSON, so a
    rule may bind to a path this repository declares nothing about, and the only
    honest result for a path the record does not carry is that the record does not
    carry it.
    """
    current = payload
    for part in str(path or "").split("."):
        if isinstance(current, Mapping):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, (list, tuple)) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current


def _as_decimal(value: Any, field: str) -> Decimal:
    """One filter value as a decimal, refusing a boolean.

    A boolean is 1 in Python, and ``True > 0`` is a comparison that runs and means
    nothing, so it is refused rather than read as one.
    """
    if isinstance(value, bool):
        raise refuse(
            "numeric_filter_value_is_not_a_number",
            **{field: f"{field} must be a number. A boolean is not a number."},
        )
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, ValueError) as exc:
        raise refuse(
            "numeric_filter_value_is_not_a_number",
            **{field: f"{field} must be a number, not {value!r}."},
        ) from exc


def _as_number(value: Any, field: str) -> float | None:
    """A stored property value as a number, or ``None`` when it is not one.

    ``None`` rather than a refusal, because a property may hold text on some quotes
    and a number on others and a rule that compares numbers should simply not match
    the text ones. Refusing would make a filter un-saveable because one existing
    record has the wrong type.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_text(value: Any) -> str:
    """A stored property value as lower-cased text, for the word operators."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return " ".join(_as_text(entry) for entry in value)
    if isinstance(value, Mapping):
        return str(value.get("id") or value.get("name") or "")
    return str(value).strip().lower()


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #


def validate_filter(payload: Any, index: int = 0) -> dict[str, Any]:
    """One filter, checked and normalised.

    ``property`` is a dotted path and is not checked against a list of properties,
    because the flow picks one from the object's property list at runtime and this
    repository declares no such list. The three operators that need a list are
    checked for a list and the four that compare numbers are checked for a number,
    because those two mistakes make a rule that reads as configured and never fires.
    """
    where = f"filters[{index}]"
    if not isinstance(payload, Mapping):
        raise refuse("filter_needs_a_property", **{where: "Each filter is an object."})

    prop = str(payload.get("property") or "").strip()
    if not prop:
        raise refuse(
            "filter_needs_a_property",
            **{"property": f"{where} needs a property. Choose one from the object's property list."},
        )

    operator = normalise_operator(payload.get("operator") or vocab.OPERATOR_IS)
    value = payload.get("value")

    if operator in vocab.LIST_OPERATORS:
        if not isinstance(value, (list, tuple)) or not value:
            raise refuse(
                "list_operator_needs_a_list",
                **{"value": f"{where} uses {operator!r}, so it needs a non-empty list of values."},
            )
        values = [entry for entry in (_as_text(item) for item in value) if entry]
        if not values:
            raise refuse(
                "list_operator_needs_a_list",
                **{"value": f"{where} uses {operator!r} and every value it was given is empty."},
            )
        return {
            "object": normalise_object(payload.get("object") or vocab.FILTER_OBJECT_QUOTE),
            "property": prop,
            "operator": operator,
            "value": values,
        }

    if value in (None, ""):
        raise refuse(
            "filter_needs_a_value",
            **{"value": f"{where} needs a value to compare {prop} with."},
        )

    if operator in vocab.NUMERIC_OPERATORS:
        _as_decimal(value, "value")
        return {
            "object": normalise_object(payload.get("object") or vocab.FILTER_OBJECT_QUOTE),
            "property": prop,
            "operator": operator,
            "value": value,
        }

    return {
        "object": normalise_object(payload.get("object") or vocab.FILTER_OBJECT_QUOTE),
        "property": prop,
        "operator": operator,
        "value": _as_text(value),
    }


def filter_matches(one: Mapping[str, Any], quote: Mapping[str, Any], line_items: Sequence[Mapping[str, Any]] = (), deal: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Whether one filter holds for a quote, and what it read.

    The object the filter names decides the payload it reads: a line-item filter is
    satisfied when **any** line item satisfies it, because the sourced evidence is
    "Require approval on quotes where a specific line item is above a certain
    discount amount" and that sentence is about one line item, not about every one.
    A quote-level filter reads the quote and a deal filter reads the related deal.

    The answer carries ``actual`` and ``present``, because the flow's "**View
    approval conditions**" has to say "this filter did not match" as usefully as it
    says "this one did".
    """
    target = str(one.get("object") or vocab.FILTER_OBJECT_QUOTE)
    prop = str(one.get("property") or "")
    operator = str(one.get("operator") or vocab.OPERATOR_IS)
    expected = one.get("value")

    payloads: list[Any]
    if target == vocab.FILTER_OBJECT_LINE_ITEM:
        payloads = list(line_items)
    elif target == vocab.FILTER_OBJECT_DEAL:
        payloads = [deal if isinstance(deal, Mapping) else {}]
    else:
        payloads = [quote]

    if not payloads:
        return {
            "matched": False,
            "object": target,
            "property": prop,
            "operator": operator,
            "expected": expected,
            "actual": None,
            "present": False,
            "reason": f"This quote has no {target.replace('_', ' ')} to read {prop} from.",
        }

    results = [
        _compare(operator, read_path(payload, prop), expected) for payload in payloads
    ]
    # Any, not all: one line item over the threshold is enough to require approval.
    matched = any(result["matched"] for result in results)
    chosen = next((result for result in results if result["matched"]), results[0])
    return {
        "matched": matched,
        "object": target,
        "property": prop,
        "operator": operator,
        "expected": expected,
        "actual": chosen["actual"],
        "present": any(result["present"] for result in results),
        "considered": len(payloads),
    }


def _compare(operator: str, actual: Any, expected: Any) -> dict[str, Any]:
    """One operator applied to one value, and whether the value was there at all."""
    present = actual is not None
    if operator in vocab.NUMERIC_OPERATORS:
        left = _as_number(actual, "actual")
        right = _as_decimal(expected, "value")
        if left is None:
            return {"matched": False, "actual": actual, "present": present}
        matched = {
            vocab.OPERATOR_GREATER_THAN: left > float(right),
            vocab.OPERATOR_GREATER_THAN_OR_EQUAL: left >= float(right),
            vocab.OPERATOR_LESS_THAN: left < float(right),
            vocab.OPERATOR_LESS_THAN_OR_EQUAL: left <= float(right),
        }[operator]
        return {"matched": matched, "actual": actual, "present": present}

    if operator == vocab.OPERATOR_IS:
        return {"matched": present and _as_text(actual) == _as_text(expected), "actual": actual, "present": present}
    if operator == vocab.OPERATOR_IS_NOT:
        return {"matched": not present or _as_text(actual) != _as_text(expected), "actual": actual, "present": present}
    if operator == vocab.OPERATOR_CONTAINS:
        return {"matched": present and _as_text(expected) in _as_text(actual), "actual": actual, "present": present}
    if operator == vocab.OPERATOR_DOES_NOT_CONTAIN:
        return {"matched": not present or _as_text(expected) not in _as_text(actual), "actual": actual, "present": present}
    if operator == vocab.OPERATOR_IN:
        wanted = {_as_text(entry) for entry in (expected or [])}
        return {"matched": present and _as_text(actual) in wanted, "actual": actual, "present": present}
    if operator == vocab.OPERATOR_NOT_IN:
        unwanted = {_as_text(entry) for entry in (expected or [])}
        return {"matched": not present or _as_text(actual) not in unwanted, "actual": actual, "present": present}
    # The vocabulary is closed and validate_filter rejects anything else.
    return {"matched": False, "actual": actual, "present": present}  # pragma: no cover


def matches_filters(
    filters: Sequence[Mapping[str, Any]],
    quote: Mapping[str, Any],
    line_items: Sequence[Mapping[str, Any]] = (),
    deal: Mapping[str, Any] | None = None,
    mode: str = vocab.FILTER_MATCH_ALL,
) -> dict[str, Any]:
    """Whether a rule's filters hold, and the per-filter report.

    ``all`` is the sourced conjunction: "*And these conditions are met*". A rule
    with no filters returns ``matched: False`` rather than ``True``, because
    :func:`validate_rule` refuses to save such a rule and a rule read from the store
    that has no filter must not match every quote.
    """
    if not filters:
        return {"matched": False, "mode": mode, "filters": [], "matched_count": 0, "total": 0}

    reports = [filter_matches(one, quote, line_items, deal) for one in filters]
    matched = [report for report in reports if report["matched"]]
    satisfied = len(matched) == len(reports) if mode == vocab.FILTER_MATCH_ALL else bool(matched)
    return {
        "matched": satisfied,
        "mode": mode,
        "filters": reports,
        "matched_count": len(matched),
        "total": len(reports),
    }


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #


def validate_rule(payload: Any) -> dict[str, Any]:
    """One approval rule, checked and normalised.

    Every field the flow sets is validated here rather than at enrolment, so a
    seller finds out at save time that a rule names no approver rather than when a
    quote is stuck against it. A rule that is only half configured is the failure
    mode the research's own UI prevents by refusing to save.
    """
    if not isinstance(payload, Mapping):
        raise refuse("rule_needs_a_name")

    key = normalise_key(payload.get("key") or payload.get("name"))
    label = str(payload.get("label") or payload.get("name") or key).strip() or key

    raw_filters = payload.get("filters")
    if not isinstance(raw_filters, (list, tuple)) or not raw_filters:
        raise refuse("rule_needs_a_filter", **{"filters": vocab.ERROR_CODES["rule_needs_a_filter"][1]})
    filters = [validate_filter(one, index) for index, one in enumerate(raw_filters)]

    approvers = validate_approvers(payload.get("approvers") or payload.get("approverIds"))
    requirement = normalise_requirement(payload.get("requirement") or vocab.REQUIREMENT_ALL)
    match_mode = str(payload.get("matchMode") or vocab.FILTER_MATCH_ALL).strip().lower()
    if match_mode not in vocab.FILTER_MATCH_MODES:
        match_mode = vocab.FILTER_MATCH_ALL

    note = str(payload.get(vocab.APPROVAL_NOTE) or payload.get("approval_note") or "").strip()

    return {
        "key": key,
        "label": label,
        "enabled": bool(payload.get("enabled", True)),
        "filters": filters,
        "matchMode": match_mode,
        "approvers": approvers,
        "requirement": requirement,
        vocab.APPROVAL_NOTE: note,
        "channels": validate_channels(payload.get("channels")),
        "quote_object": str(payload.get("quoteObject") or payload.get("quote_object") or vocab.SOURCE_QUOTES),
    }


def validate_approvers(raw: Any) -> list[str]:
    """The approver list, deduplicated and held to the sourced cap.

    Deduped because "Assign up to 10 approvers" counts approvers, and the same
    person listed twice is one approver who would otherwise be waited for twice
    under an "All approvers required" rule.
    """
    if not isinstance(raw, (list, tuple)):
        raise refuse("rule_needs_at_least_one_approver", **{"approvers": vocab.ERROR_CODES["rule_needs_at_least_one_approver"][1]})
    seen: list[str] = []
    for entry in raw:
        # A user record or a bare id, because the flow's dropdown picks users.
        identity = str(
            entry.get("id") or entry.get("userId") or entry.get("email") or ""
            if isinstance(entry, Mapping)
            else entry
        ).strip()
        if not identity:
            continue
        if identity in seen:
            raise refuse(
                "approver_repeated",
                **{"approvers": f"{identity} is listed twice. One approver, one entry."},
            )
        seen.append(identity)
    if not seen:
        raise refuse("rule_needs_at_least_one_approver", **{"approvers": vocab.ERROR_CODES["rule_needs_at_least_one_approver"][1]})
    if len(seen) > vocab.MAX_APPROVERS:
        raise refuse(
            "too_many_approvers",
            **{
                "approvers": (
                    f"{len(seen)} approvers are listed and the cap is {vocab.MAX_APPROVERS}. "
                    f"{vocab.APPROVER_CAP_QUOTE}"
                )
            },
        )
    return seen


def validate_channels(raw: Any) -> list[str]:
    """The notification channels for a rule, defaulted and deduplicated.

    ``None`` means the two the flow names for every decision: the bell and the
    email. A caller that sends an empty list gets the default rather than silence,
    because a rule with no notification is a rule where an approver never finds out.
    """
    if raw in (None, ""):
        return [vocab.CHANNEL_IN_APP, vocab.CHANNEL_EMAIL]
    if not isinstance(raw, (list, tuple)):
        raise refuse("unknown_channel", **{"channels": vocab.ERROR_CODES["unknown_channel"][1]})
    channels: list[str] = []
    for entry in raw:
        name = normalise_channel(entry)
        if name not in channels:
            channels.append(name)
    return channels or [vocab.CHANNEL_IN_APP, vocab.CHANNEL_EMAIL]


# --------------------------------------------------------------------------- #
# Approvers after the creator is removed
# --------------------------------------------------------------------------- #


def resolve_approvers(configured: Sequence[str], creator: str | None) -> dict[str, Any]:
    """The approvers who must actually decide, and who was removed.

    This is the Jev-chosen reading, audit ``jev-20261005T064607-13024-67413``, of the
    two sourced sentences that do not agree.

    Both sentences are satisfied because they are answered at different moments.
    The first is about the list at enrolment, and this function is it. The second is
    about a vote, and a creator removed from the list has no vote to cast.

    ``exempt`` is the case the research states outright: "if they're the only
    approver, the quote won't require approval". It is true exactly when every
    configured approver is the creator.
    """
    author = str(creator or "").strip()
    kept = [identity for identity in configured if str(identity).strip() != author]
    removed = [identity for identity in configured if str(identity).strip() == author]
    return {
        "approvers": kept,
        "configured": list(configured),
        "creator": author or None,
        "creator_removed": bool(removed) and bool(author),
        "removed": removed,
        "exempt": author != "" and not kept,
        "requirement": None,  # filled in by the caller, which knows the rule
    }


def requirement_met(
    requirement: str,
    approvers: Sequence[str],
    approvals: Iterable[str],
    rejections: Iterable[str] = (),
) -> dict[str, Any]:
    """Whether the requirement is met, and by what.

    A rejection is reported separately from a non-approval, because "At least one
    approver required" and "one approver requested changes" are different events and
    conflating them would let a quote publish after somebody asked for a refund. The
    requirement is therefore met by approvals alone, and ``rejected`` is reported so
    the caller can refuse.

    This is the implementation of "if there's more than one approver, and one
    approver requests changes, every approver will need to approve the quote again
    when the quote is re-submitted" from the other side: the decisions are the
    count, and a re-submission is a fresh count with none of them carried forward.
    """
    needed = set(str(identity) for identity in approvers)
    approved = {str(identity) for identity in approvals} & needed
    rejected = {str(identity) for identity in rejections} & needed
    outstanding = needed - approved - rejected
    met = (
        bool(approved) and not rejected and not outstanding
        if requirement == vocab.REQUIREMENT_ALL
        else bool(approved) and not rejected
    )
    return {
        "requirement": requirement,
        "met": met,
        "needed": sorted(needed),
        "approved": sorted(approved),
        "rejected": sorted(rejected),
        "outstanding": sorted(outstanding),
        "needed_count": len(needed),
        "approved_count": len(approved),
    }


# --------------------------------------------------------------------------- #
# Enrolments
# --------------------------------------------------------------------------- #


def new_enrolment(
    rule: Mapping[str, Any],
    quote: Mapping[str, Any],
    *,
    creator: str | None,
    now: datetime | None = None,
    notes_to_approver: str | None = None,
    line_items: Sequence[Mapping[str, Any]] = (),
    deal: Mapping[str, Any] | None = None,
    trigger: str = vocab.TRIGGER_SUBMIT,
    matched_filters: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """The enrolment record for one matching quote.

    No decision is carried forward from any earlier enrolment. That is the
    re-submission sentence: "every approver will need to approve the quote again when
    the quote is re-submitted". An approval a previous round recorded is not counted
    here, because a rejection in the same round invalidated it.

    The matched filter report travels on the record rather than being recomputed on
    a read. The flow's "**View approval conditions**" is answered from the record as
    it was at enrolment, which is the only reading that explains why a quote was held
    at the time it was held.
    """
    moment = now or utcnow()
    resolved = resolve_approvers(rule.get("approvers") or [], creator)
    resolved["requirement"] = rule.get("requirement") or vocab.REQUIREMENT_ALL
    exempt = bool(resolved["exempt"])

    payload: dict[str, Any] = {
        "rule_id": rule.get("id") or rule.get("_rule_id"),
        "rule_key": rule.get("key"),
        "rule_label": rule.get("label"),
        "quote_id": quote.get("id") or quote.get("_quote_id"),
        "quote_name": quote.get("name") or quote.get("title") or quote.get("hs_title"),
        "creator": creator or None,
        "status": vocab.STATE_DRAFT if exempt else vocab.STATE_PENDING_APPROVAL,
        "requirement": resolved["requirement"],
        "approvers": resolved["approvers"],
        "configured_approvers": resolved["configured"],
        "creator_removed": resolved["creator_removed"],
        "removed_approvers": resolved["removed"],
        "exempt": exempt,
        "exemption_reason": vocab.ENROLMENT_EXEMPT_SOLE_APPROVER if exempt else None,
        vocab.APPROVAL_NOTE: str(rule.get(vocab.APPROVAL_NOTE) or ""),
        vocab.NOTES_TO_APPROVER: str(notes_to_approver or ""),
        "matched_filters": [dict(one) for one in matched_filters],
        vocab.MATCHED_RULES: [str(rule.get("id") or rule.get("_rule_id") or "")],
        "decisions": [],
        "requested_at": stamp(moment),
        "decided_at": None,
        "decided_by": None,
        "outcome": None,
        "trigger": trigger,
        "channels": list(rule.get("channels") or [vocab.CHANNEL_IN_APP, vocab.CHANNEL_EMAIL]),
        "line_item_count": len(line_items),
    }
    return payload


def evaluate_share(state: Any) -> dict[str, Any]:
    """Whether a quote may be shared, and why not when it may not.

    "only on approval can the quote be **Share**d (state ``Shared``) and sent to the
    buyer." :func:`require_shareable` raises on the same computation, so a caller
    cannot show the button without enforcing it.
    """
    text = str(state or vocab.STATE_DRAFT).strip().upper().replace("-", "_")
    allowed = text in vocab.SHAREABLE_STATES
    return {
        "state": text,
        "shareable": allowed,
        "reason": (
            f"A {text} quote may be shared."
            if allowed
            else f"{vocab.SHARE_ON_APPROVAL_QUOTE}. This quote is {text}."
        ),
        "shareable_states": list(vocab.SHAREABLE_STATES),
    }


def require_shareable(state: Any) -> str:
    """Return the state, or refuse the share."""
    outcome = evaluate_share(state)
    if not outcome["shareable"]:
        raise ApprovalRefusal(vocab.SHARE_ON_APPROVAL_QUOTE, outcome["reason"])
    return str(outcome["state"])


# --------------------------------------------------------------------------- #
# Locking
# --------------------------------------------------------------------------- #


def evaluate_locked(state: Any, locked: Any) -> dict[str, Any]:
    """Whether a quote is frozen, and which statuses release it.

    "``hs_locked`` ... Set to ``true``. To modify any properties after you've
    published a quote, you must first update the ``hs_status`` of the quote back to
    ``DRAFT``, ``PENDING_APPROVAL``, or ``REJECTED``." Quoted whole in
    :data:`~dsr.quoting_proposals.quote_approval_vocabulary.UNLOCK_TARGET_QUOTE`.
    """
    text = str(state or vocab.STATE_DRAFT).strip().upper().replace("-", "_")
    is_locked = bool(locked) or text in (vocab.STATE_APPROVED, vocab.STATE_SHARED, vocab.STATE_ACCEPTED)
    return {
        "state": text,
        "locked": is_locked,
        "locked_field": vocab.HS_LOCKED,
        "unlock_targets": list(vocab.UNLOCK_TARGET_STATES),
        "evidence": vocab.UNLOCK_TARGET_QUOTE,
        "reason": (
            f"A {text} quote is locked. Move hs_status to one of "
            f"{', '.join(vocab.UNLOCK_TARGET_STATES)} to edit it."
            if is_locked
            else f"A {text} quote is editable."
        ),
    }


def require_editable(state: Any, locked: Any) -> str:
    """Return the state, or refuse the edit with the three releases named."""
    outcome = evaluate_locked(state, locked)
    if outcome["locked"]:
        raise refuse("quote_not_approved_to_share", outcome["reason"])
    return str(outcome["state"])


# --------------------------------------------------------------------------- #
# Activities
# --------------------------------------------------------------------------- #


def activity_for(outcome: str, *, exempt: bool = False, share_refused: bool = False) -> dict[str, Any]:
    """The activity row name for one outcome.

    Three of the five are the research's own strings. Two are this build's, and are
    marked so in the vocabulary, because the flow describes both moments and the
    research names the log without naming them.
    """
    if share_refused:
        return {"activity": vocab.ACTIVITY_SHARE_REFUSED, "sourced": False}
    if exempt:
        return {"activity": vocab.ACTIVITY_ENROLLED_WITHOUT_APPROVAL, "sourced": False}
    name = {
        vocab.STATE_APPROVED: vocab.ACTIVITY_APPROVED,
        vocab.STATE_REJECTED: vocab.ACTIVITY_REJECTED,
    }.get(str(outcome))
    if name is None:
        return {"activity": vocab.ACTIVITY_REQUESTED, "sourced": True}
    return {"activity": name, "sourced": True}


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def conditions_report(enrolment: Mapping[str, Any]) -> dict[str, Any]:
    """The "**View approval conditions**" panel, as data.

    Step four of the flow: "hover **Request approval** -> **View approval conditions**
    to see why approval is required". Every matched filter is reported with what it
    read and what it compared against, so a seller sees the reason rather than a
    yes.
    """
    filters = list(enrolment.get(vocab.MATCHED_FILTERS) or [])
    return {
        "quote_id": enrolment.get("quote_id"),
        "required": not bool(enrolment.get("exempt")),
        "exempt": bool(enrolment.get("exempt")),
        "exemption_reason": enrolment.get("exemption_reason"),
        "rule_label": enrolment.get("rule_label"),
        "requirement": enrolment.get("requirement"),
        "approvers": list(enrolment.get("approvers") or []),
        "configured_approvers": list(enrolment.get("configured_approvers") or []),
        "removed_approvers": list(enrolment.get("removed_approvers") or []),
        "filters": filters,
        "matched_count": len([one for one in filters if one.get("matched")]),
        "total": len(filters),
        "self_approval_rule": vocab.SELF_APPROVAL_RULE_TEXT,
    }


def summary_counts(rules: Sequence[Mapping[str, Any]], enrolments: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The board a reviewer reads first: how much is waiting, and on whom."""
    by_state: dict[str, int] = {state: 0 for state in vocab.QUOTE_STATES}
    waiting = 0
    exempt = 0
    for record in enrolments:
        status = str(record.get("status") or "")
        if status in by_state:
            by_state[status] += 1
        if status == vocab.STATE_PENDING_APPROVAL:
            waiting += 1
        if record.get("exempt"):
            exempt += 1
    return {
        "rules": len(rules),
        "rules_enabled": len([rule for rule in rules if rule.get("enabled")]),
        "enrolments": len(enrolments),
        "by_state": by_state,
        "awaiting_an_approver": waiting,
        "exempted": exempt,
        "max_approvers": vocab.MAX_APPROVERS,
        "requirements": list(vocab.APPROVER_REQUIREMENTS),
        "self_approval_rule": vocab.SELF_APPROVAL_RULE_TEXT,
    }
