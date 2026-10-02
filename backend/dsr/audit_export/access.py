"""WF-079: the administrator gate and the sandbox key.

Two rules the research states about the export surface, both of which are about who
is allowed to read rather than about what is read.

**The export is administrator-only.** The source is unambiguous:

    "Retrieves the full audit trail for a specified document... **This endpoint is
    accessible to authorized workspace administrators only.**"
    -- https://developers.pandadoc.com/reference/list-document-audit-trail.md

The gate fails closed. A caller that presents no role, an unknown role, or the
lowest of the four roles this product knows is refused, because an export carries
who did what and from where - it is the most sensitive read in the product, and a
gate that defaults to open would be a defect rather than a convenience.

The role vocabulary is not invented here. It is
:mod:`dsr.permissions`' - ``viewer``, ``content_contributor``,
``room_collaborator``, ``instance_admin`` - and ``instance_admin`` is the tier the
source's "authorized workspace administrator" maps onto. Four other features
already import that module, so the export does not create a second role system that
could disagree with the first.

**A sandbox key masks the address.** "The IP address from which the action was
performed. If a sandbox API key is used, this will be ``\"hidden\"``." A sandbox
key is a property of the *caller*, so it is a per-request flag; the environment
variable :data:`SANDBOX_ENV` is the stand-in this deployment has, read at call time
rather than at import time so a test can flip it and so a long-lived process does
not capture the value that was set when it booted.

The masked value is what the export carries *and* what the digest hashes, so a
digest stays reproducible from the export alone. The alternative - hashing the real
address and masking it only on the way out - produces a digest nobody holding the
export can recompute, which defeats the purpose of shipping one.
"""

from __future__ import annotations

import os
from typing import Any

from dsr.permissions import INSTANCE_ADMIN, ROLE_LABELS, ROLES, normalise_role

#: Where a sandbox key is declared for this deployment. Read at call time.
SANDBOX_ENV = "DSR_AUDIT_EXPORT_SANDBOX"

#: The literal a sandbox key produces, verbatim from the source's field
#: documentation. It is a string and not a null, and not an empty string, because
#: the source says a sandbox caller *receives* this value - a client that
#: distinguishes "masked" from "we have no address" needs the two to be different.
SANDBOX_IP = "hidden"

#: The role that opens the export. Named here rather than spelled at the call site
#: so there is one answer to "who may read this".
AUDIT_READER_ROLE = INSTANCE_ADMIN

#: How a caller is refused, and what the response says so a page can explain it
#: rather than rendering an empty table.
DENIED_CODE = "administrator_required"
DENIED_DETAIL = (
    "The audit trail export is accessible to authorized workspace administrators "
    "only. Present an administrator role."
)
DENIED_REMEDIATION = f"Pass role={AUDIT_READER_ROLE!r}."

_TRUTHY = frozenset({"1", "true", "yes", "on", "sandbox", "hidden"})


class AccessDenied(PermissionError):
    """A caller that is not allowed to read the export.

    A ``PermissionError`` so it cannot be confused with the refusals the store
    raises, and so a caller catching ``PermissionError`` gets the right
    behaviour. It carries its own wire shape because the researched error needs to
    say which role would have worked - a 403 with no remediation is a support
    ticket.
    """

    code = DENIED_CODE

    def __init__(self, detail: str = DENIED_DETAIL, remediation: str = DENIED_REMEDIATION) -> None:
        super().__init__(detail)
        self.detail = detail
        self.remediation = remediation

    def to_dict(self, presented_role: Any = None) -> dict[str, Any]:
        """The body, naming the role that was presented and the one that would work."""
        return {
            "error": self.code,
            "detail": self.detail,
            "remediation": self.remediation,
            "presented_role": normalise_role(presented_role),
            "required_role": AUDIT_READER_ROLE,
        }


def require_administrator(role: Any) -> str:
    """Return the caller's resolved role, or refuse.

    Fails closed on everything: no role at all, an empty string, a role this
    product does not recognise, and a recognised role below the administrator
    tier. ``normalise_role`` already collapses an unknown role to ``viewer``, so
    the check is a single equality against the administrator tier and there is no
    branch here that can be talked into widening it.
    """
    resolved = normalise_role(role)
    if resolved != AUDIT_READER_ROLE:
        raise AccessDenied()
    return resolved


def sandbox_enabled(explicit: bool | None = None) -> bool:
    """Whether the caller presented a sandbox key.

    The explicit flag wins over the environment, because a per-request property
    should not be silently overridden by a process-wide one: a deployment that has
    set the variable for its staging environment must still be able to ask for a
    production-shaped export in a test, and vice versa.
    """
    if explicit is not None:
        return bool(explicit)
    return str(os.environ.get(SANDBOX_ENV, "")).strip().lower() in _TRUTHY


def gate_payload(role: Any = None) -> dict[str, Any]:
    """The gate's vocabulary, published so the page can render a role switcher.

    Roles come from :mod:`dsr.permissions` rather than from a list written here,
    so a role added to the product appears on this page without anyone editing
    either file.
    """
    return {
        "reader_role": AUDIT_READER_ROLE,
        "reader_label": ROLE_LABELS.get(AUDIT_READER_ROLE, AUDIT_READER_ROLE),
        "resolved_role": normalise_role(role),
        "roles": [
            {
                "id": candidate,
                "label": ROLE_LABELS.get(candidate, candidate),
                "may_read": candidate == AUDIT_READER_ROLE,
            }
            for candidate in ROLES
        ],
        "sandbox": {
            "env": SANDBOX_ENV,
            "masked_ip": SANDBOX_IP,
        },
        "denied": {
            "error": DENIED_CODE,
            "detail": DENIED_DETAIL,
            "remediation": DENIED_REMEDIATION,
        },
    }
