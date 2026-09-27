"""Rejecting invalid writes before the batch leaves, not after it comes back.

This is the "and reject invalid writes before commit" half of the ticket. The
researched flow puts the other half after: a batch runs, rows fail, the log lists
them, an admin fixes the mapping. Both halves are real and they are not
alternatives:

* **Before.** HubSpot "will enforce admin-configured validation rules on all CRM
  API write paths" from the ``/2026-09/`` GA release on September 8 2026, and
  Dataverse answers a batch with ``0x80044331`` and a message naming the
  attribute that was too long. Those are refusals the room can read *before* it
  spends a call, and a refusal that costs no call is a row that never appears in
  the Sync log at all.
* **After.** Admin-configured rules, plug-in errors, permissions, and every
  validation rule nobody told the room about still come back per-row. That is
  what the Sync log and the retry queue exist for.

So the room holds *its own* validation rules as a record, checks a proposed
batch against them, and reports which rows must not be sent. :func:`check_batch`
writes nothing: refusing a row before the commit is a pure function of the
payload and the rules, which is why ``POST /validate`` leaves no audit rows and
why a refused row has no row in the log.

The three rule kinds are deliberately small:

``required``
    A value is present and not empty. Grounded in the research's own extension
    example, "route records missing ``email`` to a manual-review queue instead of
    retrying", which names a field a record can be missing.
``max_length`` / ``min_length``
    The shape of the one concrete validation failure the research quotes:
    "The length of the 'subject' attribute of the 'task' entity exceeded the
    maximum allowed length of '200'."

An unrecognised ``kind`` is **refused** on a PATCH rather than stored and
ignored. A rule that silently does nothing is the failure mode this repository
guards against everywhere else, and it is the worse one here: a rule that does
nothing looks exactly like a rule that passes.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.partial_failures.errors import InvalidPayload, InvalidRule
from dsr.partial_failures.vocabulary import CONNECTORS

#: The rule kinds this build enforces. ``min_length`` is not quoted by the
#: research; it is the same measurement as ``max_length`` read the other way, and
#: a deployment that wants only the sourced half can remove it with one PATCH.
PREFLIGHT_KINDS: tuple[str, ...] = ("required", "max_length", "min_length")

#: Matches any connector, or any entity, in a rule.
ANY = "*"

#: How much of a rejected value a violation carries back. The value itself is
#: echoed verbatim up to this many characters, and its true length and type are
#: always reported, so a caller that wants the whole thing still has it - it sent
#: it - and a 5 MB text property cannot turn a refusal into a 5 MB response.
SENT_PREVIEW_CHARS = 500

_ALLOWED_RULE_KEYS = frozenset(
    {"id", "connector", "entity", "field", "kind", "value", "message", "basis"}
)

#: The rules this build ships with, each traceable to a string the research
#: quotes. A deployment adds to this list with one PATCH; it does not have to
#: edit a function body, and it does not need a migration.
DEFAULT_PREFLIGHT_RULES: tuple[dict[str, Any], ...] = (
    {
        "id": "hubspot-contact-email-required",
        "connector": "hubspot",
        "entity": "contact",
        "field": "email",
        "kind": "required",
        "value": None,
        "message": "A contact cannot be created without an email address.",
        "basis": (
            "The research's own extensibility example: \"route records missing `email` to a "
            "manual-review queue instead of retrying\". It names email as a field a record can be "
            "missing, which is a presence rule the room can check before it sends anything."
        ),
    },
    {
        "id": "dataverse-task-subject-length",
        "connector": "dataverse",
        "entity": "task",
        "field": "subject",
        "kind": "max_length",
        "value": 200,
        "message": None,
        "basis": (
            "The quoted Dataverse refusal, in full: \"A validation error occurred. The length of the "
            "'subject' attribute of the 'task' entity exceeded the maximum allowed length of "
            "'200'.\" Entity, field and limit are all in the sentence."
        ),
    },
    {
        "id": "salesforce-contact-email-required",
        "connector": "salesforce",
        "entity": "contact",
        "field": "email",
        "kind": "required",
        "value": None,
        "message": "A contact cannot be created without an email address.",
        "basis": (
            "The same extension example, applied to the third vendor. The rule it describes is "
            "connector-agnostic, and a presence rule that held for one CRM should hold for the two "
            "that behave the same way."
        ),
    },
)


# --------------------------------------------------------------------------- #
# Rule validation
# --------------------------------------------------------------------------- #


def validate_rule(rule: Any) -> dict[str, Any]:
    """Check one pre-flight rule, or say precisely what is wrong with it.

    Unknown keys are refused. A patch that says ``{"kind": "maxLen"}`` and a patch
    that says ``{"kind": "max_length"}`` look identical to whoever typed them, and
    only one of them does anything.
    """
    if not isinstance(rule, Mapping):
        raise InvalidRule(f"a pre-flight rule must be a JSON object; got {type(rule).__name__}")

    unknown = sorted(set(rule) - _ALLOWED_RULE_KEYS)
    if unknown:
        raise InvalidRule(
            f"unknown rule key(s) {', '.join(unknown)}; a rule carries "
            f"{', '.join(sorted(_ALLOWED_RULE_KEYS))}"
        )

    identifier = str(rule.get("id") or "").strip()
    if not identifier:
        raise InvalidRule("every pre-flight rule needs an id; it is how a deployment refers to it")

    connector = _wildcard(rule.get("connector"), "connector")
    if connector != ANY and connector not in CONNECTORS:
        raise InvalidRule(
            f"rule {identifier}: unknown connector {connector!r}; expected {ANY} or one of "
            f"{', '.join(CONNECTORS)}"
        )

    field = str(rule.get("field") or "").strip()
    if not field:
        raise InvalidRule(f"rule {identifier}: field is required; a rule that names no property checks nothing")

    kind = str(rule.get("kind") or "").strip()
    if kind not in PREFLIGHT_KINDS:
        raise InvalidRule(
            f"rule {identifier}: unknown kind {kind!r}; this build enforces "
            f"{', '.join(PREFLIGHT_KINDS)}. A stored rule that does nothing looks exactly like a "
            "rule that passes, so an unknown kind is refused rather than ignored."
        )

    value = rule.get("value")
    if kind == "required":
        if value not in (None, ""):
            raise InvalidRule(
                f"rule {identifier}: a required rule has no value; the absence of the property is "
                "the whole condition"
            )
        value = None
    else:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidRule(
                f"rule {identifier}: a {kind} rule needs a whole-number limit; got {value!r}"
            )
        if float(value) != int(value):
            raise InvalidRule(f"rule {identifier}: a {kind} limit must be a whole number; got {value!r}")
        if int(value) < 0:
            raise InvalidRule(f"rule {identifier}: a {kind} limit cannot be negative; got {value!r}")
        value = int(value)

    checked: dict[str, Any] = {
        "id": identifier,
        "connector": connector,
        "entity": _wildcard(rule.get("entity"), "entity"),
        "field": field,
        "kind": kind,
        "value": value,
    }
    message = rule.get("message")
    if message:
        checked["message"] = str(message)
    basis = rule.get("basis")
    if basis:
        checked["basis"] = str(basis)
    return checked


def validate_rules(rules: Any) -> list[dict[str, Any]]:
    """Check a whole pre-flight rule list, refusing duplicates by id."""
    if not isinstance(rules, Sequence) or isinstance(rules, (str, bytes)):
        raise InvalidRule(f"preflight must be a list of rules; got {type(rules).__name__}")
    checked = [validate_rule(rule) for rule in rules]
    seen: set[str] = set()
    for rule in checked:
        if rule["id"] in seen:
            raise InvalidRule(
                f"two pre-flight rules share the id {rule['id']!r}; ids are how a deployment "
                "replaces one rule, so they have to be unique"
            )
        seen.add(rule["id"])
    return checked


def merge_rules(base: Sequence[Mapping[str, Any]], patch: Any) -> list[dict[str, Any]]:
    """Overlay a pre-flight patch on the current list, keyed on rule id.

    A patch entry whose id is already present **replaces** that rule; an entry
    with a new id is added. Replacing by id rather than appending is what lets a
    deployment retune the shipped ``dataverse-task-subject-length`` limit to 120
    without restating the rule and without creating a second rule that shadows the
    first.
    """
    if not isinstance(patch, Sequence) or isinstance(patch, (str, bytes)):
        raise InvalidRule(f"preflight must be a list of rules; got {type(patch).__name__}")

    merged: dict[str, dict[str, Any]] = {str(rule["id"]): dict(rule) for rule in base}
    for entry in patch:
        rule = validate_rule(entry)
        merged[rule["id"]] = rule
    return validate_rules(list(merged.values()))


def defaults() -> list[dict[str, Any]]:
    """The shipped pre-flight rules, freshly copied so a caller cannot mutate them."""
    return [validate_rule(rule) for rule in DEFAULT_PREFLIGHT_RULES]


# --------------------------------------------------------------------------- #
# Checking a batch
# --------------------------------------------------------------------------- #


def expectation_text(kind: str, limit: int | None) -> str:
    """What was expected, in the words the detail view shows."""
    if kind == "required":
        return "a value is required"
    if kind == "max_length":
        return f"at most {limit} characters"
    if kind == "min_length":
        return f"at least {limit} characters"
    return kind


def check_row(
    row: Mapping[str, Any],
    rules: Sequence[Mapping[str, Any]],
    *,
    connector: str,
) -> list[dict[str, Any]]:
    """Every violation one row has against the rules that apply to it.

    A rule applies when its connector and entity both match, either by name or by
    the ``*`` wildcard. Length rules only measure strings: a numeric property has
    a length in digits but no documented bound on it, and the room does not
    invent one.
    """
    values = row.get("values")
    if not isinstance(values, Mapping):
        raise InvalidPayload(
            f"row {row.get('row_key')!r}: values must be a JSON object of the mapped properties; "
            f"got {type(values).__name__}"
        )
    entity = str(row.get("entity") or "")
    violations: list[dict[str, Any]] = []

    for rule in rules:
        if not _matches(rule.get("connector", ANY), connector):
            continue
        if not _matches(rule.get("entity", ANY), entity):
            continue
        sent = values.get(rule["field"], _ABSENT)
        empty = sent is _ABSENT or sent is None or (isinstance(sent, str) and not sent.strip())

        if rule["kind"] == "required":
            if empty:
                violations.append(
                    _violation(
                        rule,
                        sent=None if sent is _ABSENT else sent,
                        present=not empty,
                        note="not sent" if sent is _ABSENT else "sent empty",
                    )
                )
            continue

        if empty or not isinstance(sent, str):
            # Nothing to measure. A length rule on a value that is absent is not
            # a violation, and a length rule on a number is not one either.
            continue

        limit = int(rule["value"] or 0)
        length = len(sent)
        if rule["kind"] == "max_length" and length > limit:
            violations.append(_violation(rule, sent=sent, present=True, note=f"{length} characters"))
        elif rule["kind"] == "min_length" and length < limit:
            violations.append(_violation(rule, sent=sent, present=True, note=f"{length} characters"))

    return violations


def check_batch(
    rows: Sequence[Mapping[str, Any]],
    rules: Sequence[Mapping[str, Any]],
    *,
    connector: str,
) -> dict[str, Any]:
    """The pre-flight verdict for a proposed batch.

    The whole of the "before commit" behaviour: which rows may be sent, which must
    not be, and for each refusal which property was wrong, what was sent, and what
    was expected. A row with no violations is ``accepted``; a row with any is
    ``refused``, and it must not be in the batch the connector sends.
    """
    accepted: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []

    for row in rows:
        violations = check_row(row, rules, connector=connector)
        entry = {
            "row_key": row.get("row_key"),
            "entity": row.get("entity"),
            "trace_id": row.get("trace_id"),
            "fields": sorted({str(v["field"]) for v in violations}),
            "violations": violations,
            "accepted": not violations,
        }
        (accepted if entry["accepted"] else refused).append(entry)

    applicable = sorted(
        {str(rule["id"]) for rule in rules if _matches(rule.get("connector", ANY), connector)}
    )
    return {
        "connector": connector,
        "rows": len(rows),
        "accepted": len(accepted),
        "refused": len(refused),
        "ok": not refused,
        "send": [entry["row_key"] for entry in accepted],
        "hold": [entry["row_key"] for entry in refused],
        "verdicts": accepted + refused,
        "rules_applied": applicable,
        "rules_available": len(rules),
    }


def expectations_for(
    rules: Sequence[Mapping[str, Any]],
    *,
    connector: str,
    entity: str,
    field: str | None,
) -> list[dict[str, Any]]:
    """What the room expected of one property, for the field-level error detail.

    Returned for the whole row when the vendor named no property, because a detail
    view with nothing to show is how a rule gap stays invisible.
    """
    found: list[dict[str, Any]] = []
    for rule in rules:
        if not _matches(rule.get("connector", ANY), connector):
            continue
        if not _matches(rule.get("entity", ANY), entity):
            continue
        if field is not None and not _matches(rule.get("field"), field):
            continue
        found.append(
            {
                "field": rule["field"],
                "kind": rule["kind"],
                "rule_id": rule["id"],
                "limit": rule["value"],
                "expected": expectation_text(str(rule["kind"]), rule["value"]),
            }
        )
    return found


def covers(rules: Sequence[Mapping[str, Any]], *, connector: str, entity: str, field: str | None) -> bool:
    """Does any rule speak about this property at all?

    A validation-class failure the room has no rule for is the visible symptom of
    a gap in this configuration, and the row says so rather than pretending the
    refusal is unexplainable.
    """
    return bool(expectations_for(rules, connector=connector, entity=entity, field=field))


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #

#: Distinguishes "the property was not in the payload" from "the property was
#: present and its value was null". The detail view states which, because the fix
#: is different: add the property, or clear the value.
class _Absent:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<absent>"

    def __bool__(self) -> bool:
        return False


_ABSENT = _Absent()


def _violation(rule: Mapping[str, Any], *, sent: Any, present: bool, note: str) -> dict[str, Any]:
    kind = str(rule["kind"])
    limit = rule.get("value")
    text = str(sent) if isinstance(sent, str) else sent
    truncated = isinstance(text, str) and len(text) > SENT_PREVIEW_CHARS

    message = rule.get("message")
    if not message:
        if kind == "required":
            message = f"{rule['field']} is required and was {note}."
        elif kind == "max_length":
            message = (
                f"{rule['field']} exceeded the maximum allowed length of '{limit}' "
                f"({note} sent)."
            )
        else:
            message = f"{rule['field']} is shorter than the minimum of '{limit}' characters ({note} sent)."

    return {
        "field": rule["field"],
        "kind": kind,
        "rule_id": rule["id"],
        "message": message,
        "expected": expectation_text(kind, limit),
        "limit": limit,
        "sent": text[:SENT_PREVIEW_CHARS] if isinstance(text, str) else text,
        "sent_truncated": truncated,
        "sent_length": len(text) if isinstance(text, str) else None,
        "sent_present": present,
    }


def _matches(pattern: Any, value: Any) -> bool:
    text = str(pattern or ANY).strip().lower()
    return text == ANY or text == str(value or "").strip().lower()


def _wildcard(value: Any, what: str) -> str:
    if value in (None, ""):
        return ANY
    text = str(value).strip()
    if not text:
        return ANY
    return text.lower() if what == "connector" else text


__all__ = [
    "ANY",
    "PREFLIGHT_KINDS",
    "SENT_PREVIEW_CHARS",
    "DEFAULT_PREFLIGHT_RULES",
    "validate_rule",
    "validate_rules",
    "merge_rules",
    "defaults",
    "expectation_text",
    "check_row",
    "check_batch",
    "expectations_for",
    "covers",
]
