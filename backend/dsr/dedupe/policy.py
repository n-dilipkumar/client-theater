"""The per-connection dedupe policy: the enum the research says is per connection.

"Dedupe policy is a per-connection enum (block / update / merge), so a
deployment can escalate to 'auto-merge' for high-confidence cases."

That sentence is why a connection is a record rather than a field on the room.
Two connections to two CRMs can disagree about what to do with a duplicate, and
the right answer is not a product-wide constant. It is also why the matching
keys live on the connection: which key a CRM treats as unique is a property of
the CRM's schema (HubSpot's ``hasUniqueValue`` properties, Dataverse's alternate
keys, a Salesforce unique index), not of this product.

The research names three policies in the extensibility note and three in step 4
of the user flow, and they are not the same three:

* step 4 says block, update, and "creates the duplicate anyway with an
  acknowledgement";
* extensibility says block, update, and merge.

``allow`` is the acknowledged create, which is the researched Salesforce
``allowSave`` behaviour and the one policy that maps onto a documented header
field. ``merge`` is the escalation target, which the research names but whose
implementation is explicitly not claimed. All four are therefore published, and
``rules.decide`` handles each of them - including the two that end in a refusal
rather than a write.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.dedupe.errors import DedupeError
from dsr.dedupe.rules import (
    BLOCKED,
    CREATED_DUPLICATE,
    ESCALATED,
    NEEDS_HUMAN,
    UPDATED,
)
from dsr.dedupe.vocabulary import (
    DEFAULT_POLICY,
    DEFAULT_UNIQUE_KEYS,
    POLICIES,
    require_keys,
    require_policy,
    require_vendor,
)

#: Each policy, in the words of the research, and what a rep sees when it fires.
POLICY_DETAILS: Mapping[str, dict[str, Any]] = {
    "block": {
        "summary": "Block the write and show the existing record",
        "sourced_from": "user_flow step 4(a): 'blocks the write and shows the existing record'",
        "writes": False,
        "outcome_on_match": BLOCKED,
        "header": {"includeRecordDetails": True},
        "reversible": True,
    },
    "update": {
        "summary": "Update the existing record instead",
        "sourced_from": "user_flow step 4(b): 'updates the existing record instead'",
        "writes": True,
        "outcome_on_match": UPDATED,
        "header": {"includeRecordDetails": True},
        "reversible": False,
    },
    "allow": {
        "summary": "Create the duplicate anyway, with an acknowledgement",
        "sourced_from": (
            "user_flow step 4(c): 'creates the duplicate anyway with an acknowledgement'. This is "
            "the researched Salesforce allowSave option: 'allow the user to acknowledge the alert "
            "and save the duplicate record'."
        ),
        "writes": True,
        "outcome_on_match": CREATED_DUPLICATE,
        "header": {"allowSave": True},
        "reversible": True,
    },
    "merge": {
        "summary": "Escalate to a human for high-confidence duplicates",
        "sourced_from": (
            "extensibility: 'a deployment can escalate to auto-merge for high-confidence cases'. "
            "The merge action itself is not claimed - the research records that the Salesforce "
            "auto-merge documentation was JS-rendered and unreadable."
        ),
        "writes": False,
        "outcome_on_match": ESCALATED,
        "header": {"includeRecordDetails": True},
        "reversible": True,
    },
}


def describe(policy: str) -> dict[str, Any]:
    """One policy, in full, for a picker."""
    resolved = require_policy(policy)
    detail = dict(POLICY_DETAILS[resolved])
    detail["policy"] = resolved
    detail["needs_human"] = detail["outcome_on_match"] in NEEDS_HUMAN
    return detail


def catalogue() -> dict[str, Any]:
    """Every policy, with the vocabulary of outcomes beside it."""
    return {
        "count": len(POLICIES),
        "default": DEFAULT_POLICY,
        "policies": [describe(policy) for policy in POLICIES],
        "note": (
            "Two of the four policies end in a refusal rather than a write: 'block' by choice, and "
            "'merge' because the research explicitly does not claim the merge action. Both are "
            "marked so a picker can say so before an administrator saves one."
        ),
    }


def normalise_connection(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate a connection spec into the shape the engine stores.

    Split out from the engine so the HTTP layer and the tests exercise the same
    validation, and so a malformed connection is refused before any row exists.
    """
    body = dict(payload or {})
    if not str(body.get("name") or "").strip():
        raise DedupeError("name is required")
    keys = require_keys(body.get("keys"))
    min_score = body.get("min_score")
    if min_score in ("", None):
        min_score = None
    else:
        try:
            min_score = float(min_score)
        except (TypeError, ValueError) as exc:
            raise DedupeError("min_score must be a number between 0 and 1, or absent") from exc
        if not 0.0 <= min_score <= 1.0:
            raise DedupeError("min_score must be between 0 and 1")
    unique = body.get("unique_keys")
    if unique is None:
        unique_keys = tuple(key for key in DEFAULT_UNIQUE_KEYS if key in keys)
    else:
        if isinstance(unique, str):
            raise DedupeError("unique_keys must be a list of matching-key names, not a string")
        requested = {str(name) for name in unique}
        for name in sorted(requested):
            require_keys([name])
        unknown = sorted(requested - set(keys))
        if unknown:
            raise DedupeError(
                f"unique_keys {', '.join(unknown)} must also be configured keys; a key cannot be "
                f"unique without being matched on. Configured keys: {', '.join(keys)}"
            )
        unique_keys = tuple(key for key in keys if key in requested)
    return {
        "name": str(body["name"]).strip(),
        "vendor": require_vendor(body.get("vendor")),
        "policy": require_policy(body.get("policy")),
        "keys": list(keys),
        "unique_keys": list(unique_keys),
        "min_score": min_score,
        "room_id": body.get("room_id"),
    }
