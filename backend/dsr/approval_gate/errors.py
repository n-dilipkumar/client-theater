"""The WF-119 refusal hierarchy.

FastAPI takes exception handlers on the application object only, never on a
router, so the feature module exports ``EXCEPTION_HANDLERS`` and the plugin host
attaches it. One handler covers the whole family and dispatches on the concrete
class, because a feature may map an error type no other feature has mapped, and
two features may not map the same type.

Every refusal carries the HTTP status and the machine code its next step implies,
so the mapping lives with the error rather than in the route that raises it.
"""

from __future__ import annotations

#: A request that was understood but conflicts with current state: a duplicate
#: gate key, a second hold for a contact, a resume of an expired hold, or a
#: second resume that disagrees with the first.
STATUS_CONFLICT = 409

#: A malformed payload: a bad key, a missing extras field, a branch the gate does
#: not declare, or a body outcome that disagrees with the URL.
STATUS_UNPROCESSABLE = 422

#: The identifier does not resolve to a live record.
STATUS_NOT_FOUND = 404


class ApprovalGateRefusal(RuntimeError):
    """Base class for every refusal this feature raises."""

    status = STATUS_UNPROCESSABLE
    code = "approval_gate_refused"


class GateNotFound(ApprovalGateRefusal):
    """The gate key or the resume token does not resolve."""

    status = STATUS_NOT_FOUND
    code = "gate_not_found"


class HoldNotFound(ApprovalGateRefusal):
    """No held contact matches the correlation key."""

    status = STATUS_NOT_FOUND
    code = "hold_not_found"


class GateInvalid(ApprovalGateRefusal):
    """One or more validation rules rejected the gate payload."""

    status = STATUS_UNPROCESSABLE
    code = "gate_invalid"

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems) or "the gate payload is invalid")


class KeyTaken(ApprovalGateRefusal):
    """A live gate already holds this key."""

    status = STATUS_CONFLICT
    code = "gate_key_taken"


class EnvelopeInvalid(ApprovalGateRefusal):
    """The action envelope does not carry the documented shape."""

    status = STATUS_UNPROCESSABLE
    code = "envelope_invalid"

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems) or "the action envelope is invalid")


class PauseDisabled(ApprovalGateRefusal):
    """The gate was paused off, so a contact is not held at it."""

    status = STATUS_CONFLICT
    code = "pause_disabled"


class HoldConflict(ApprovalGateRefusal):
    """The contact is already held at this gate."""

    status = STATUS_CONFLICT
    code = "already_held"


class HoldExpired(ApprovalGateRefusal):
    """The hold lapsed before the resume arrived, so it cannot be resumed."""

    status = STATUS_CONFLICT
    code = "hold_expired"


class ResumeConflict(ApprovalGateRefusal):
    """A resume disagrees with the decision already recorded for this hold."""

    status = STATUS_CONFLICT
    code = "resume_conflict"


class BranchUnknown(ApprovalGateRefusal):
    """The branch in the resume body is not the branch the gate declares.

    The branch is verified against the gate rather than trusted from the body.
    """

    status = STATUS_UNPROCESSABLE
    code = "branch_unknown"


class OutcomeConflict(ApprovalGateRefusal):
    """The body names an outcome that disagrees with the resume URL."""

    status = STATUS_UNPROCESSABLE
    code = "outcome_conflict"


#: Type -> (status, code). The route handler dispatches on the concrete class, so
#: the base class needs no per-instance override.
REFUSAL_STATUS: dict[type[ApprovalGateRefusal], tuple[int, str]] = {
    GateNotFound: (STATUS_NOT_FOUND, "gate_not_found"),
    HoldNotFound: (STATUS_NOT_FOUND, "hold_not_found"),
    GateInvalid: (STATUS_UNPROCESSABLE, "gate_invalid"),
    KeyTaken: (STATUS_CONFLICT, "gate_key_taken"),
    EnvelopeInvalid: (STATUS_UNPROCESSABLE, "envelope_invalid"),
    PauseDisabled: (STATUS_CONFLICT, "pause_disabled"),
    HoldConflict: (STATUS_CONFLICT, "already_held"),
    HoldExpired: (STATUS_CONFLICT, "hold_expired"),
    ResumeConflict: (STATUS_CONFLICT, "resume_conflict"),
    BranchUnknown: (STATUS_UNPROCESSABLE, "branch_unknown"),
    OutcomeConflict: (STATUS_UNPROCESSABLE, "outcome_conflict"),
}


__all__ = [
    "ApprovalGateRefusal",
    "BranchUnknown",
    "EnvelopeInvalid",
    "GateInvalid",
    "GateNotFound",
    "HoldConflict",
    "HoldExpired",
    "HoldNotFound",
    "KeyTaken",
    "OutcomeConflict",
    "PauseDisabled",
    "REFUSAL_STATUS",
    "ResumeConflict",
    "STATUS_CONFLICT",
    "STATUS_NOT_FOUND",
    "STATUS_UNPROCESSABLE",
]
