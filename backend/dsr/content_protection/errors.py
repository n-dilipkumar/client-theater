"""The errors WF-131 raises, and nothing else in the product does.

Every type here is declared in this module and raised by no other workflow, which
is what makes it safe for the feature module to map them to responses: the host
refuses a second feature registering a handler for the same type, and a handler
for ``ValueError`` or ``PermissionError`` would intercept those exceptions across
the whole product.

Three of the four bundled protections already map their own errors: WF-069 maps
``link_gating.rules.GateError`` / ``GateDenied`` / ``LinkNotFound``, WF-070 maps
its own, WF-072 maps ``view_only_access.rules.ViewOnlyAccessError`` and WF-071
maps ``viewer_watermark.errors``. This workflow therefore never re-raises those
and never re-maps them. When it needs a protection's decision it calls that
workflow's rule and, where the rule raises a mapped type, lets it through - the
host's existing handler answers with the researched shape.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Stable machine tokens for the reasons a per-recipient policy refuses a viewer.
#: A token rather than a sentence, so the frontend branches on the reason and
#: never string-matches English.


class ContentProtectionError(ValueError):
    """A policy, an action or a setting this workflow will not accept.

    Carries a field-keyed map, because a seller filling in a form needs the
    message beside the input that caused it and not one combined sentence. The
    same shape :mod:`dsr.link_gating.rules` and :mod:`dsr.view_only_access.rules`
    use, and the HTTP layer renders it as ``errors``.
    """

    code = "invalid_content_protection"

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "detail": str(self), "errors": dict(self.errors)}


class PolicyNotFound(LookupError):
    """No such per-recipient policy, or no such item this workflow owns.

    Its own type rather than the store's ``RecordNotFound``, because a feature
    may only map error types it raises itself: registering a handler for a shared
    type would intercept that exception across the whole product.
    """


class ProtectionDenied(PermissionError):
    """A viewer a per-recipient policy will not let through.

    The request is well formed, the policy exists, and the answer is no. The
    decision that produced the refusal rides on the error so the response can
    name which protection withheld the content rather than only that it was
    withheld - which is the whole reason a bundle needs one evaluation.
    """

    code = "protection_denied"

    #: Stable reason tokens. Each names one protection, so a page can say which
    #: control refused and a test can assert it without reading a sentence.
    REASON_CLOSED = "link_closed"
    REASON_EXPIRED = "policy_expired"
    REASON_REVOKED = "policy_revoked"
    REASON_IP = "ip_not_allowed"
    REASON_EMAIL = "email_required"
    REASON_PASSCODE = "passcode_required"
    REASON_NDA = "nda_not_accepted"

    #: What a viewer is told for each. The two closure reasons share wording on
    #: purpose: WF-069 requires a revoked link to be indistinguishable from an
    #: expired one from the outside, and a bundle does not get to weaken that.
    _CLOSED = "This link has expired. Ask the sender for a new one."

    MESSAGES = {
        REASON_CLOSED: _CLOSED,
        REASON_EXPIRED: _CLOSED,
        REASON_REVOKED: _CLOSED,
        REASON_IP: "This link cannot be opened from your network. Ask the sender.",
        REASON_EMAIL: "Confirm the email address this link was sent to.",
        REASON_PASSCODE: "Enter the link passcode to continue.",
        REASON_NDA: "Accept the agreement to continue.",
    }

    def __init__(self, reason: str, decision: Mapping[str, Any] | None = None) -> None:
        super().__init__(self.MESSAGES.get(reason, "This link could not be opened."))
        self.reason = reason
        self.decision: dict[str, Any] = dict(decision or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "reason": self.reason,
            "detail": str(self),
            "decision": dict(self.decision),
        }


class ActionRefused(ContentProtectionError):
    """An action this workflow will not record.

    A rejected attempt is still an action WF-079 wants visible, so only the
    action's *shape* is refused here - an unknown verb, a missing recipient - not
    the fact that a viewer was denied. A denial is recorded, not raised.
    """
