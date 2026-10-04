"""Rule evaluation: the researched two-source match.

The researched ``data_flow`` reads "routing rule evaluation against (a) Chili
Piper Data Fields and (b) live CRM object values (Lead/Contact/Account/
Opportunity/Case, incl. Salesforce Lead-to-Account matching)". So a rule reads
two different things and this module keeps them apart rather than flattening them
into one bag of values, because the difference is the research's own split:
Data Field conditions come from what the prospect typed, and CRM object
conditions come from what the CRM already holds.

The CRM half reads ``crm_read_record``, the collection WF-042 provisions. This
package does not own those records and does not write them. A rule that finds no
CRM record simply does not match on that condition, which is the researched
behaviour for a prospect whose email the CRM has never seen.
"""

from __future__ import annotations

from typing import Any

from dsr.concierge_router import vocabulary
from dsr.concierge_router.errors import NoRuleMatched
from dsr.concierge_router.nodes import (
    CRM_OBJECT_SOURCE,
    CRM_OWNERSHIP_FIELDS,
    DATA_FIELD_SOURCE,
)

#: The collection WF-042 provisions. Read only. See the module docstring.
CRM_RECORDS = "crm_read_record"

#: The fields on a CRM record this module reads. ``owner_id`` is WF-042's own
#: lifted column; ``owner_team`` has no native key on a WF-042 record, so it is
#: read from the documented ``crm_owner.team`` path and tolerated as absent.
CRM_OWNER_ID = "owner_id"
CRM_OWNER_TEAM = "owner_team"
CRM_TEAM_PATH = ("crm_owner", "team")
CRM_TEAM_FLAT = "team"

#: The fallback the research names for a bare domain: "incl. Salesforce
#: Lead-to-Account matching". Applied only when the CRM holds no record at the
#: guest's own domain, so a real contact record always wins over a domain match.
LEAD_TO_ACCOUNT = "lead_to_account"


def _nested(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def crm_team(data: dict[str, Any]) -> str:
    """The team on a CRM record, or an empty string when it carries none.

    WF-042 defines no team field, so a team is whatever the importing team put
    there. Both documented spellings are read: the nested ``crm_owner.team`` the
    WF-042 test suite pins, and a flat ``team``. Absence is normal, not a fault:
    a record with no team does not match a team-scoped rule, which is the same
    outcome as a record owned by somebody on another team.
    """
    for path in (CRM_TEAM_PATH, (CRM_TEAM_FLAT,)):
        found = _nested(data, path)
        if found:
            return str(found).strip()
    return ""


def crm_domain(email: str) -> str:
    """The part after the ``@``, lower-cased, or an empty string."""
    text = str(email or "").strip().lower()
    _, separator, domain = text.partition("@")
    return domain.strip() if separator else ""


def find_crm_records(
    store: Any,
    email: str,
    objects: tuple[str, ...] | None = None,
    room_id: str | None = None,
) -> list[dict[str, Any]]:
    """The CRM records matching a guest address, best match first.

    Three passes, in the order the research implies. An exact address match on
    the guest's own object set is the strongest evidence. A Salesforce
    Lead-to-Account match follows: the research names it explicitly, "incl.
    Salesforce Lead-to-Account matching", and it works by domain because that is
    how a Lead is associated with an Account before it converts. Anything else is
    returned in the store's own order.
    """
    wanted = str(email or "").strip().lower()
    domain = crm_domain(wanted)
    rows = store.find(CRM_RECORDS, {}, limit=1000)
    scoped = [row for row in rows if room_id is None or row.get("room_id") == room_id]
    wanted_objects = set(objects or vocabulary.CRM_FIELD_SOURCES)

    def object_name(row: dict[str, Any]) -> str:
        return str((row.get("data") or {}).get("object") or "").strip().lower()

    def row_email(row: dict[str, Any]) -> str:
        return str((row.get("data") or {}).get("email") or "").strip().lower()

    exact = [
        row for row in scoped if row_email(row) == wanted and object_name(row) in wanted_objects
    ]
    lead_to_account = [
        row
        for row in scoped
        if row_email(row) == domain and object_name(row) in wanted_objects and domain
    ]
    rest = [
        row
        for row in scoped
        if row not in exact and row not in lead_to_account and object_name(row) in wanted_objects
    ]
    return [*exact, *lead_to_account, *rest]


def _resolve_crm_value(field: str, records: list[dict[str, Any]]) -> Any:
    """The value a CRM condition reads off the matched records.

    ``None`` when no record carries it, which the comparator treats as a
    condition that did not match. That is deliberate: a rule that read an absent
    value as empty string would match a prospect the CRM knows nothing about.
    """
    for row in records:
        data = row.get("data") or {}
        if field == CRM_OWNER_ID:
            value = data.get(CRM_OWNER_ID)
        elif field == CRM_OWNER_TEAM:
            value = crm_team(data)
        else:
            value = _nested(data.get("fields") or {}, (field,))
            if value is None:
                value = data.get(field)
        if value not in (None, ""):
            return value
    return None


def _compare(left: Any, operator: str, right: Any) -> bool:
    """Apply one researched comparison.

    Every comparison folds to text except ``in``, which asks whether the left
    value is one of a list. Folding is what lets a hand-written rule match a CRM
    value the vendor stored as a code and a form value the prospect typed.
    """
    if operator == "in":
        options = right if isinstance(right, (list, tuple, set)) else [right]
        return str(left).strip().lower() in {str(option).strip().lower() for option in options}
    if left is None:
        return False
    left_text = str(left).strip().lower()
    right_text = str(right if right is not None else "").strip().lower()
    if operator == "equals":
        return left_text == right_text
    if operator == "not_equals":
        return left_text != right_text
    if operator == "contains":
        return right_text in left_text
    return False


def evaluate_condition(
    condition: dict[str, Any],
    data_fields: dict[str, Any],
    crm_records: list[dict[str, Any]],
) -> bool:
    """Whether one condition holds.

    A Data Field condition reads the mapped webform values. A CRM condition reads
    the guest's CRM records. The two never mix, so a rule cannot accidentally
    match a Data Field value against a CRM value.
    """
    source = str(condition.get("source") or DATA_FIELD_SOURCE)
    field = str(condition.get("field") or "")
    operator = str(condition.get("operator") or "equals")

    if source == CRM_OBJECT_SOURCE:
        left = _resolve_crm_value(field, crm_records)
    else:
        left = data_fields.get(field)
    return _compare(left, operator, condition.get("value"))


def evaluate_rule(
    node: dict[str, Any],
    data_fields: dict[str, Any],
    crm_records: list[dict[str, Any]],
) -> bool:
    """Whether a rule node matches.

    A named rule matches only when *every* one of its conditions holds. A rule
    with no conditions matches everything, which is what makes the Catch All
    "a path to make sure you define the routing and acknowledge all inbound
    Leads" rather than an exception.
    """
    conditions = node.get("conditions") or []
    return all(evaluate_condition(condition, data_fields, crm_records) for condition in conditions)


def matched_rule(
    rule_chain: list[dict[str, Any]],
    data_fields: dict[str, Any],
    crm_records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """The first rule that matches, or ``None`` when the chain ran out.

    ``None`` is unreachable for a router that saved, because
    :func:`~dsr.concierge_router.nodes.require_catch_all` refuses a chain that
    can run out. A router row carrying a broken chain from outside this package
    can still land here, which is why :class:`NoRuleMatched` stays a distinct
    error: "your saved router has no catch-all" is a different sentence for an
    operator than "the prospect reached the deal desk".
    """
    for node in rule_chain:
        if evaluate_rule(node, data_fields, crm_records):
            return node
    return None


def calendar_for(
    rule: dict[str, Any], declared: list[dict[str, Any]] | None = None
) -> dict[str, Any] | None:
    """The ``Display Calendar`` node a matched rule leads to, or ``None``.

    "On a rule match, admin adds a ``Display Calendar`` node", so a rule's
    ``calendar`` names the node. Two cases resolve differently:

    * A rule that carries its own calendar uses it.
    * A rule that does not falls back to the router's own ``Display Calendar``
      node, which is the node a catch-all is reached through. The research makes
      the catch-all "a path to make sure you define the routing and acknowledge
      all inbound Leads", and a lead acknowledged with no calendar offered is the
      ``rule_declined`` outcome the vocabulary names, not a booking.

    Returns ``None`` only when the rule deliberately offers no calendar, which the
    research distinguishes from one that merely does not carry its own.
    """
    if not rule:
        return None
    if str(rule.get("offer_calendar", "")).strip().lower() in {"false", "no", "0"}:
        return None
    own = rule.get("calendar")
    if isinstance(own, dict):
        return own
    if str(rule.get("type") or "") == vocabulary.CATCH_ALL:
        for node in declared or []:
            if node.get("type") in vocabulary.CALENDAR_NODE_TYPES:
                return node
    return None


def raise_if_no_rule(rule: dict[str, Any] | None) -> dict[str, Any]:
    """Refuse a chain that ran out, naming the defect rather than the prospect."""
    if rule is None:
        raise NoRuleMatched(
            "no routing rule matched and this router's chain carries no Catch All path, "
            "so the inbound lead would reach the end of the router unacknowledged"
        )
    return rule


__all__ = [
    "CRM_OWNERSHIP_FIELDS",
    "CRM_OWNER_ID",
    "CRM_OWNER_TEAM",
    "CRM_RECORDS",
    "DATA_FIELD_SOURCE",
    "LEAD_TO_ACCOUNT",
    "calendar_for",
    "crm_domain",
    "crm_team",
    "evaluate_condition",
    "evaluate_rule",
    "find_crm_records",
    "matched_rule",
    "raise_if_no_rule",
]
