"""What the CRM's duplicate rule returns, and what the policy then does about it.

This module is the state machine the research describes, and nothing else. The
user flow is four steps and this is all of it:

    2. The connector sends the write with duplicate detection enabled.
    3. The CRM's matching/duplicate rule runs against existing rows and returns
       either a clean create, a duplicate alert with the matching record id, or
       a hard block.
    4. Depending on the admin's configured policy, the connector either (a)
       blocks the write and shows the existing record, (b) updates the existing
       record instead, or (c) creates the duplicate anyway with an
       acknowledgement.

The researched hard blocks, and why they are not policies
----------------------------------------------------------

The flow lists three policy outcomes but a hard block is *not* one of them, and
two rules override the policy entirely:

**More than one match.** "If the external ID matches multiple existing records,
then a 300 error is returned, and **no records are created or updated**." The
policy is never consulted. It is also consistent with step 3 naming exactly one
matching record id: a duplicate alert has a single subject, and a result with
several is by definition not that.

**Two different keys both matching.** Also a hard block, and this one is an
inference rather than a quote - see the ``ambiguous-key-match`` entry. A result
that matches one record by email and a different record by external ID is
exactly the ambiguity the previous rule refuses to guess about.

**A unique index forbids the duplicate.** "The `Unique` attribute prevents the
creation of duplicates." So policy ``allow``, which exists to create the
duplicate anyway, cannot create it through a unique index. This is the catch-all
the build brief asks for: a rule that does not fall through is a bug someone
hits in production, and an administrator who set a unique index and a permissive
policy has to be told which of the two won.

The merge escalation
--------------------

``merge`` is named in the research's extensibility note as the escalation target
for high-confidence duplicates, but the ``gaps`` section is explicit: Salesforce
auto-merge "lives in help.salesforce.com duplicate-management docs, which are
JS-rendered and unreadable; the merge action is therefore *not* claimed." So
``merge`` is a selectable policy that produces :data:`ESCALATED` - the decision
is logged, nothing is written, and it is marked as needing a human. Inventing a
merge would be claiming something the research declines to claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from dsr.dedupe.vocabulary import (
    DUPLICATE_RULE_HEADER_NAME,
    MULTIPLE_MATCH_STATUS,
    build_duplicate_rule_header,
    require_policy,
    serialise_duplicate_rule_header,
)

#: A write that went through as a new record.
CREATED = "created"
#: Policy (b): "updates the existing record instead".
UPDATED = "updated"
#: Policy (a): "blocks the write and shows the existing record".
BLOCKED = "blocked"
#: Policy (c): "creates the duplicate anyway with an acknowledgement".
CREATED_DUPLICATE = "created_duplicate"
#: The researched hard block. Nothing was written, whatever the policy said.
HARD_BLOCKED = "hard_blocked"
#: The merge escalation: logged, not performed.
ESCALATED = "escalated"

#: The six states a decision can land in. Published by
#: :func:`~dsr.dedupe.vocabulary.vocabulary` as ``outcomes``.
OUTCOMES: tuple[str, ...] = (CREATED, UPDATED, BLOCKED, CREATED_DUPLICATE, HARD_BLOCKED, ESCALATED)

#: Which outcome each policy reaches on a single clean match. Published so a
#: client can render a policy without hard-coding the table.
POLICY_OUTCOMES: Mapping[str, str] = {
    "block": BLOCKED,
    "update": UPDATED,
    "allow": CREATED_DUPLICATE,
    "merge": ESCALATED,
}

#: Outcomes that created nothing and need a person. The page shows this count,
#: the same way WF-016's activity log shows "needs manual update", so the two
#: features read the same way in one product.
NEEDS_HUMAN: frozenset[str] = frozenset({HARD_BLOCKED, ESCALATED})


@dataclass(frozen=True)
class DuplicateResult:
    """What the CRM's rule returned. Step 3 of the researched flow.

    ``matched`` holds the record payloads, which is the sourced ``300`` body:
    "The response body contains the list of matching records." They are present
    only when the header asked for them, so their absence is itself a faithful
    record of what was sent.
    """

    result: str
    matched: tuple[Mapping[str, Any], ...] = ()
    status: int | None = None
    match_key: str | None = None
    reason: str = ""

    @property
    def matched_ids(self) -> tuple[str, ...]:
        return tuple(str(record.get("id")) for record in self.matched if record.get("id"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "matched": [dict(record) for record in self.matched],
            "matched_ids": list(self.matched_ids),
            "status": self.status,
            "match_key": self.match_key,
            "reason": self.reason,
        }


@dataclass
class Decision:
    """The outcome of one inbound row, before anything is written.

    The engine fills this in and then acts on it, which keeps "what did the rule
    decide" separable from "what did we do about it" - the distinction the audit
    log needs, since a refusal and a write are different events.
    """

    outcome: str
    policy: str
    crm_result: str
    header: dict[str, bool] = field(default_factory=dict)
    match_key: str | None = None
    match_value: str | None = None
    matched_ids: list[str] = field(default_factory=list)
    matched: list[dict[str, Any]] = field(default_factory=list)
    status: int | None = None
    reason: str = ""
    detail: str = ""
    #: True when the rule's answer was a hard block rather than a policy choice.
    hard_block: bool = False
    #: True when ``allowSave`` was set, i.e. the duplicate was acknowledged.
    acknowledged: bool = False
    #: True when the local in-room check answered without calling the CRM.
    crm_called: bool = True

    @property
    def needs_human(self) -> bool:
        return self.outcome in NEEDS_HUMAN

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "policy": self.policy,
            "crm_result": self.crm_result,
            "header": dict(self.header),
            "header_name": DUPLICATE_RULE_HEADER_NAME,
            "header_wire": serialise_duplicate_rule_header(self.header),
            "match_key": self.match_key,
            "match_value": self.match_value,
            "matched_ids": list(self.matched_ids),
            "matched": [dict(record) for record in self.matched],
            "status": self.status,
            "reason": self.reason,
            "detail": self.detail,
            "hard_block": self.hard_block,
            "acknowledged": self.acknowledged,
            "needs_human": self.needs_human,
            "crm_called": self.crm_called,
        }


def clean(reason: str = "") -> DuplicateResult:
    """No row matched. The researched "clean create"."""
    return DuplicateResult(result="clean", reason=reason or "no existing record matched any configured key")


def matched(
    records: Sequence[Mapping[str, Any]],
    *,
    match_key: str,
    status: int | None = None,
    reason: str = "",
) -> DuplicateResult:
    """One row matched. The researched "duplicate alert with the matching record id"."""
    return DuplicateResult(
        result="match",
        matched=tuple(records),
        status=status,
        match_key=match_key,
        reason=reason or f"matched one existing record on {match_key}",
    )


def multiple(
    records: Sequence[Mapping[str, Any]],
    *,
    match_key: str | None = None,
    reason: str = "",
) -> DuplicateResult:
    """Several rows matched, or two keys disagreed. The researched hard block.

    ``status`` is 300 when the key is an external ID, because that is the one
    case the research gives a number for. A multi-match on email gets no
    invented status code.
    """
    is_external_id = match_key == "external_id"
    return DuplicateResult(
        result="multiple",
        matched=tuple(records),
        status=MULTIPLE_MATCH_STATUS if is_external_id else None,
        match_key=match_key,
        reason=reason
        or (
            f"the external ID matches {len(records)} existing records, so the CRM returns 300 and "
            "no records are created or updated"
            if is_external_id
            else f"{len(records)} existing records matched, which is a hard block rather than a "
            "duplicate alert"
        ),
    )


def decide(
    result: DuplicateResult,
    policy: str,
    *,
    unique_keys: Sequence[str] = (),
    match_key: str | None = None,
    crm_called: bool = True,
    run_as_current_user: bool = False,
) -> Decision:
    """Apply the researched policy to a CRM result. The whole state machine.

    Read the docstring of this module for why the two hard blocks and the unique
    index are not policies.
    """
    resolved = require_policy(policy)
    key = match_key or result.match_key
    header = build_duplicate_rule_header(resolved, run_as_current_user=run_as_current_user)
    ids = list(result.matched_ids)
    # includeRecordDetails is what makes the payloads present. Without the field
    # in the header the CRM returns ids only, so the decision records ids and
    # says the details were not asked for.
    details = "includeRecordDetails" in header
    payloads = [dict(record) for record in result.matched] if details else []

    base: dict[str, Any] = {
        "policy": resolved,
        "crm_result": result.result,
        "header": header,
        "match_key": key,
        "matched_ids": ids,
        "matched": payloads,
        "status": result.status,
        "crm_called": crm_called,
    }

    if result.result == "clean":
        return Decision(outcome=CREATED, reason=result.reason, **base)

    if result.result == "multiple":
        return Decision(
            outcome=HARD_BLOCKED,
            reason=result.reason,
            detail=(
                "Nothing was written. The researched 300 case is not a policy option: 'no records "
                "are created or updated' holds whatever the connection is configured to do."
            ),
            hard_block=True,
            **base,
        )

    if resolved == "block":
        return Decision(
            outcome=BLOCKED,
            reason=result.reason,
            detail=(
                "Nothing was written. The policy is to block the write and show the existing "
                "record, which is why includeRecordDetails was requested."
            ),
            **base,
        )

    if resolved == "update":
        return Decision(
            outcome=UPDATED,
            reason=result.reason,
            detail=(
                "The existing record is updated instead of a second one being written, which is "
                "HubSpot's 'upsert by idProperty so a repeat write updates rather than duplicates'."
            ),
            **base,
        )

    if resolved == "allow":
        if key in set(unique_keys):
            return Decision(
                outcome=HARD_BLOCKED,
                reason=f"policy 'allow' cannot create a duplicate: {key} carries a unique index",
                detail=(
                    "'The Unique attribute prevents the creation of duplicates.' A unique index "
                    "outranks a permissive policy, so the duplicate is refused and the "
                    "administrator has to widen the policy or drop the index."
                ),
                hard_block=True,
                **base,
            )
        return Decision(
            outcome=CREATED_DUPLICATE,
            reason=result.reason,
            detail=(
                "The duplicate was created anyway. allowSave was set, which is the header's own "
                "description: allow the user to acknowledge the alert and save the duplicate record."
            ),
            acknowledged=True,
            **base,
        )

    # merge. Reached only because every other policy returned above.
    return Decision(
        outcome=ESCALATED,
        reason=result.reason,
        detail=(
            "Auto-merge is the research's documented escalation target, but the merge action is "
            "explicitly not claimed: the Salesforce duplicate-management page that documents it is "
            "JS-rendered and was not readable. Nothing was written; this needs a human."
        ),
        **base,
    )


def outcome_table() -> dict[str, Any]:
    """The policy-to-outcome table, served as data."""
    return {
        "outcomes": list(OUTCOMES),
        "policy_outcomes": dict(POLICY_OUTCOMES),
        "needs_human": sorted(NEEDS_HUMAN),
        "multiple_match_status": MULTIPLE_MATCH_STATUS,
    }
