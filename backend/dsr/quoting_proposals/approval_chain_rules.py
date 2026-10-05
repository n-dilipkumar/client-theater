"""WF-092: the branch, the priority ordering and the chain, as pure functions.

Every rule here is a statement about a number, a name or a state. None of them reads
or writes anything. The engine beside this module turns them into records. This module
is what a test can call with two lists and a clock and check.

The rules the rest of the workflow leans on
-------------------------------------------

**A branch is one property, one operator, one threshold.** "Branch -> select **One
property or action output** -> choose the **Quote amount** property -> set the branch
condition (e.g. greater than 5,000)". :func:`evaluate_branch` answers three ways, and
the third is not a "no": a branch whose property the quote does not carry **could not
be checked**, which is an unanswered question rather than a refusal. Letting it pass
would fire an approval on evidence nobody supplied. Letting it fail silently would
make a quote that simply has no amount look rejected with no way to find out why.

**The caps are hard.** "You can add up to five sequences, and ten approvers per
sequence." :func:`validate_sequence` refuses a sixth sequence and an eleventh approver
at the boundary, not after the row is written.

**Sequential means every approver at the current priority.** "Sequential approvals
require approval by every approver at each priority step." :func:`advance` is that
sentence: it returns the next priority only when every approver at the current one has
approved. One rejection ends the chain at any priority.

**A chain with no step auto-approves.** "if a quote approval step hasn't been added
above this action, quotes will be auto-approved." :func:`auto_approve` is that valve, and
it is deliberately the opposite of the usual instinct: a quote that matched a branch
but collected no approver is **approved**, not blocked. A sales rep is not left waiting
on a chain that does not exist.

**The last decision writes the final state.** "the last decision writes
`APPROVED`/`REJECTED` and the quote becomes publishable". :func:`final_state` answers
with the status and the publishability together, because the evidence states them as
one outcome and a caller that reads only the status would miss half of it.

What this module does not decide
--------------------------------

Whether a notification actually arrived, and whether an approver the directory names
still works here. It decides which approver is next and what the chain state becomes.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from dsr.quoting_proposals import approval_chain_vocabulary as vocab

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class ApprovalRuleError(ValueError):
    """A researched rule refused the input.

    Raised before any row is written, so a refusal never leaves a half-built chain
    behind.
    """


# --------------------------------------------------------------------------- #
# The branch predicate
# --------------------------------------------------------------------------- #

#: Why a branch reached its verdict. Served with every decision so a caller reads the
#: reasoning rather than trusting a boolean.
REASONS: dict[str, str] = {
    "matched": "The quote satisfies this branch condition.",
    "not_matched": "The quote does not satisfy this branch condition.",
    "unverifiable": (
        "The quote does not carry the property this branch reads, so the condition "
        "could not be checked. An unanswered question is not a refusal."
    ),
    "invalid_operator": "The branch names an operator the workflow does not support.",
    "no_amount": "The quote carries no usable quote amount.",
}

_OPERATORS = {
    "greater_than": lambda left, right: left > right,
    "greater_than_or_equal": lambda left, right: left >= right,
    "less_than": lambda left, right: left < right,
    "less_than_or_equal": lambda left, right: left <= right,
    "equals": lambda left, right: left == right,
}


def read_property(quote: Mapping[str, Any], prop: str) -> Any:
    """Read one quote property, resolving a dotted path.

    The branch engine reads "one property or action output", and a client may name a
    nested one. A dotted path is how a nested property is reached without a schema,
    which is the point of storing payloads as open JSON.
    """
    current: Any = quote
    for part in prop.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return None
    return current


def _as_number(value: Any) -> float | None:
    """A property is comparable only if it is a real number.

    A boolean is refused even though ``bool`` is a subclass of ``int`` in Python,
    because a flag compared against 5000 is a caller error rather than a comparison.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def evaluate_branch(quote: Mapping[str, Any], branch: Mapping[str, Any]) -> dict[str, Any]:
    """Decide whether a quote qualifies for one branch.

    Returns ``{"outcome", "reason", "left", "threshold", "observed"}``. ``outcome`` is
    one of :data:`~dsr.quoting_proposals.approval_chain_vocabulary.OUTCOME_QUALIFIED`,
    ``"not_qualified"`` or ``"unverifiable"``.
    """
    prop = str(branch.get("property") or vocab.PROPERTY_QUOTE_AMOUNT)
    operator = str(branch.get("operator") or "greater_than")
    threshold = _as_number(branch.get("threshold"))
    if threshold is None:
        threshold = vocab.DEFAULT_BRANCH_THRESHOLD

    compare = _OPERATORS.get(operator)
    if compare is None:
        return {
            "outcome": "not_qualified",
            "reason": REASONS["invalid_operator"],
            "operator": operator,
            "threshold": threshold,
        }

    observed = read_property(quote, prop)
    left = _as_number(observed)
    if left is None:
        return {
            "outcome": "unverifiable",
            "reason": REASONS["unverifiable"] if observed is None else REASONS["no_amount"],
            "property": prop,
            "threshold": threshold,
            "observed": observed,
        }

    return {
        "outcome": "qualified" if compare(left, threshold) else "not_qualified",
        "reason": REASONS["matched"] if compare(left, threshold) else REASONS["not_matched"],
        "property": prop,
        "operator": operator,
        "left": left,
        "threshold": threshold,
        "observed": observed,
    }


def evaluate_branches(quote: Mapping[str, Any], branches: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Evaluate every branch against one quote.

    A quote qualifies when **at least one** branch matches. The user flow has a
    **None met** path, which is what the no-branch case is, and a quote with two
    qualifying branches takes both approval steps because each qualifying branch
    pushes one.
    """
    results = [evaluate_branch(quote, branch) for branch in branches]
    qualified = [result for result in results if result["outcome"] == vocab.OUTCOME_QUALIFIED]
    return {
        "qualified": bool(qualified),
        "qualifying_branches": qualified,
        "results": results,
        "reason": (
            REASONS["matched"] if qualified else REASONS["not_matched"]
        ),
    }


# --------------------------------------------------------------------------- #
# Caps and validation
# --------------------------------------------------------------------------- #


def validate_sequence(
    sequences: Sequence[Mapping[str, Any]],
    existing_sequences: int = 0,
) -> dict[str, Any]:
    """Refuse a sequence that breaks either researched cap.

    ``existing_sequences`` counts the sequences already stored, so the sixth is
    refused at the boundary. :data:`~dsr.quoting_proposals.approval_chain_vocabulary.MAX_SEQUENCES`
    and ``MAX_APPROVERS_PER_SEQUENCE`` are the two numbers the research gives.
    """
    added = len(sequences)
    total = existing_sequences + added
    if total > vocab.MAX_SEQUENCES:
        raise ApprovalRuleError(
            f"a quote approval workflow supports at most {vocab.MAX_SEQUENCES} sequences, "
            f"and {total} were requested"
        )
    for index, sequence in enumerate(sequences):
        approvers = sequence.get("approvers") or []
        if len(approvers) > vocab.MAX_APPROVERS_PER_SEQUENCE:
            raise ApprovalRuleError(
                f"sequence {index + 1} names {len(approvers)} approvers, and a "
                f"sequence supports at most {vocab.MAX_APPROVERS_PER_SEQUENCE}"
            )
        if not approvers:
            raise ApprovalRuleError(f"sequence {index + 1} names no approver")
        priority = sequence.get("priority")
        if not isinstance(priority, int) or isinstance(priority, bool) or priority < 1:
            raise ApprovalRuleError(f"sequence {index + 1} must carry a priority of 1 or more")
        requirement = sequence.get("requirement")
        if requirement is not None and requirement not in vocab.APPROVER_REQUIREMENTS:
            raise ApprovalRuleError(
                f"sequence {index + 1} names requirement {requirement!r}, which is not one of "
                + ", ".join(vocab.APPROVER_REQUIREMENTS)
            )
    return {
        "sequences": total,
        "approvers": [len(sequence.get("approvers") or []) for sequence in sequences],
        "within_caps": True,
    }


def rank_sequences(sequences: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Order sequences by priority, lowest first.

    "The sales director won't need to approve the quote until the sales manager has
    completed their approval" is the sentence this ordering encodes. The sort is stable
    on the approver list so two equal priorities keep the order they were configured in.
    """
    ordered = sorted(
        (dict(sequence) for sequence in sequences),
        key=lambda sequence: int(sequence.get("priority") or 0),
    )
    return ordered


# --------------------------------------------------------------------------- #
# The chain
# --------------------------------------------------------------------------- #


def group_by_priority(steps: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Group approver steps into priority levels, lowest priority first.

    One level is one dict: ``{"priority", "approvers", "requirement"}``. The group is
    the unit the sequential rule is written against.
    """
    levels: dict[int, dict[str, Any]] = {}
    for step in steps:
        priority = int(step.get(vocab.PRIORITY_KEY) or 0)
        level = levels.setdefault(
            priority,
            {
                vocab.PRIORITY_KEY: priority,
                "approvers": [],
                "requirement": step.get("requirement") or vocab.REQUIREMENT_SEQUENTIAL,
            },
        )
        approver = step.get(vocab.APPROVER_KEY)
        if approver:
            level["approvers"].append(approver)
    return sorted(levels.values(), key=lambda level: level[vocab.PRIORITY_KEY])


def active_priority(enrolment: Mapping[str, Any], levels: Sequence[Mapping[str, Any]]) -> int | None:
    """The priority whose approvers may decide now, or ``None`` when the chain is done.

    The researched sequential rule is the whole of this function: a lower-priority
    approver is not notified until every approver at the current priority has decided,
    so the active priority only moves when the current level is fully approved.
    """
    for level in levels:
        priority = level[vocab.PRIORITY_KEY]
        approvers = level["approvers"]
        decided = enrolment.get("decisions") or {}
        requirement = level.get("requirement") or vocab.REQUIREMENT_SEQUENTIAL
        approvals = [approver for approver in approvers if decided.get(approver) == vocab.DECISION_APPROVED]
        rejections = [approver for approver in approvers if decided.get(approver) == vocab.DECISION_REJECTED]

        if rejections:
            return None
        if requirement == vocab.REQUIREMENT_ALL:
            satisfied = len(approvals) == len(approvers)
        elif requirement == vocab.REQUIREMENT_ANY:
            satisfied = bool(approvals)
        else:  # sequential
            satisfied = len(approvals) == len(approvers)
        if not satisfied:
            return priority
    return None


def is_complete(enrolment: Mapping[str, Any], levels: Sequence[Mapping[str, Any]]) -> bool:
    """Has every level been satisfied?"""
    decided = enrolment.get("decisions") or {}
    for level in levels:
        approvers = level["approvers"]
        requirement = level.get("requirement") or vocab.REQUIREMENT_SEQUENTIAL
        approvals = [approver for approver in approvers if decided.get(approver) == vocab.DECISION_APPROVED]
        rejections = [approver for approver in approvers if decided.get(approver) == vocab.DECISION_REJECTED]
        if rejections:
            return True
        if requirement == vocab.REQUIREMENT_ANY:
            if not approvals:
                return False
        elif len(approvals) != len(approvers):
            return False
    return True


def final_state(enrolment: Mapping[str, Any], levels: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The state the chain writes when it finishes.

    The evidence states two things at once: "the last decision writes
    `APPROVED`/`REJECTED` and the quote becomes publishable", so both are returned and
    neither is derived by the caller.
    """
    decided = enrolment.get("decisions") or {}
    rejected = [
        approver
        for level in levels
        for approver in level["approvers"]
        if decided.get(approver) == vocab.DECISION_REJECTED
    ]
    if rejected:
        return {
            "state": vocab.STATE_REJECTED,
            vocab.QUOTE_STATUS_KEY: vocab.DECISION_REJECTED,
            "publishable": False,
            "decided_by": rejected,
        }
    return {
        "state": vocab.STATE_APPROVED,
        vocab.QUOTE_STATUS_KEY: vocab.DECISION_APPROVED,
        "publishable": True,
        "decided_by": sorted(
            approver
            for level in levels
            for approver in level["approvers"]
            if decided.get(approver) == vocab.DECISION_APPROVED
        ),
    }


def advance(
    enrolment: Mapping[str, Any],
    levels: Sequence[Mapping[str, Any]],
    approver: str,
    decision: str,
) -> dict[str, Any]:
    """Apply one approver's decision and report what the chain does next.

    Returns ``{"outcome", "reason", "decisions", "state", "next_priority", "final"}``.
    ``outcome`` is one of ``"decided"``, ``"already_decided"``, ``"not_an_approver"``,
    ``"complete"`` or ``"auto_approved"``.

    An abstention records the decision and reports the same active priority. It does
    not advance a sequential level on its own, because the researched rule counts
    approvals and not decisions.
    """
    if decision not in vocab.DECISIONS_ALLOWED:
        raise ApprovalRuleError(
            f"decision {decision!r} is not one of " + ", ".join(vocab.DECISIONS_ALLOWED)
        )

    if not levels:
        return {
            "outcome": "auto_approved",
            "reason": (
                "No approval step sits above the start action, so this quote is "
                "auto-approved."
            ),
            "decisions": dict(enrolment.get("decisions") or {}),
            "state": vocab.STATE_APPROVED,
            vocab.QUOTE_STATUS_KEY: vocab.DECISION_APPROVED,
            "publishable": True,
            "next_priority": None,
            "final": final_state({"decisions": {}}, []),
        }

    every = sorted(approver_name for level in levels for approver_name in level["approvers"])
    if approver not in every:
        return {
            "outcome": "not_an_approver",
            "reason": f"{approver} is not an approver on this chain.",
            "approvers": every,
        }

    decisions = dict(enrolment.get("decisions") or {})
    if approver in decisions:
        return {
            "outcome": "already_decided",
            "reason": f"{approver} already decided {decisions[approver]}.",
            "decisions": decisions,
            "next_priority": active_priority(enrolment, levels),
        }

    decisions[approver] = decision
    updated = {**enrolment, "decisions": decisions}

    if decision == vocab.DECISION_REJECTED:
        final = final_state(updated, levels)
        return {
            "outcome": "complete",
            "reason": "One approver rejected, so the chain ends at this priority.",
            "decisions": decisions,
            "state": final["state"],
            vocab.QUOTE_STATUS_KEY: final[vocab.QUOTE_STATUS_KEY],
            "publishable": final["publishable"],
            "next_priority": None,
            "final": final,
        }

    if is_complete(updated, levels):
        final = final_state(updated, levels)
        return {
            "outcome": "complete",
            "reason": "Every approver has approved, so the last decision wrote the final state.",
            "decisions": decisions,
            "state": final["state"],
            vocab.QUOTE_STATUS_KEY: final[vocab.QUOTE_STATUS_KEY],
            "publishable": final["publishable"],
            "next_priority": None,
            "final": final,
        }

    return {
        "outcome": "decided",
        "reason": (
            REASONS["matched"]
            if decision == vocab.DECISION_APPROVED
            else "An abstention is recorded and the level is unchanged."
        ),
        "decisions": decisions,
        "state": vocab.STATE_IN_REVIEW,
        "next_priority": active_priority(updated, levels),
        "final": None,
    }


def auto_approve(quote_id: str, branch_results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The researched safety valve, as a value.

    "if a quote approval step hasn't been added above this action, quotes will be
    auto-approved." A quote that matched a branch but collected no step is approved,
    not blocked.
    """
    return {
        "quote_id": quote_id,
        "outcome": vocab.OUTCOME_AUTO_APPROVED,
        "reason": (
            "No approval step was added above the start action, so the quote is "
            "auto-approved."
        ),
        "state": vocab.STATE_APPROVED,
        vocab.QUOTE_STATUS_KEY: vocab.DECISION_APPROVED,
        "publishable": True,
        "branches": [result.get("reason") for result in branch_results],
    }


# --------------------------------------------------------------------------- #
# The message template
# --------------------------------------------------------------------------- #

_TEMPLATE_PATTERN = re.compile(
    re.escape(vocab.TEMPLATE_OPEN)
    + r"\s*"
    + re.escape(vocab.TEMPLATE_ROOT)
    + r"\.([A-Za-z0-9_.]+)\s*"
    + re.escape(vocab.TEMPLATE_CLOSE)
)


def render_message(template: str, quote: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve ``{{quote.property}}`` against a quote record.

    An unknown variable is left in place and named in ``missing``, rather than
    rendered as an empty string. A message that says "quote  is over your limit"
    because a field was renamed is worse than a message that says which field is
    missing.
    """
    text = str(template or "")
    missing: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        value = read_property(quote, match.group(1))
        if value is None:
            missing.append(match.group(1))
            return match.group(0)
        return str(value)

    rendered = _TEMPLATE_PATTERN.sub(_replace, text)
    return {
        "text": rendered,
        "missing": missing,
        "complete": not missing,
    }


# --------------------------------------------------------------------------- #
# Re-enrolment
# --------------------------------------------------------------------------- #


def re_enrol(enrolment: Mapping[str, Any]) -> dict[str, Any]:
    """Derive the re-enrolment transition.

    The research gives the switch and its purpose. "Re-enrollment can be toggled for
    quotes that need re-approval after edits." The state machine behind that sentence is
    not specified, so this is the derivation: re-enrolling returns the chain to
    ``pending_approval`` at priority 1, clears the decisions, and increments a run
    counter, because the prior decision history is what the counter preserves. The
    alternative, leaving the decisions in place, would make a re-approval pass
    instantly because every level already reads approved.
    """
    return {
        "state": vocab.STATE_PENDING,
        "active_priority": 1,
        "decisions": {},
        "run": int(enrolment.get("run") or 0) + 1,
        "previous_run": int(enrolment.get("run") or 0),
    }