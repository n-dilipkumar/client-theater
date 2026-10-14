"""WF-131: per-recipient content protection and auditability.

The specification is ``docs/research/digital-sales-room-workflows/wf/WF-131.md``,
quoted in full on issue #167. It is a **bundle**: its own Why field says so -
"Bundles protections already classified: WF-071, WF-072, WF-070 and the critical
WF-079" - and every one of those four workflows is merged on ``main``. So this
package adds the two things none of them has and composes the rest.

Modules
-------
``vocabulary``
    Every term, with the workflow each one came from. Seven of the nine policy
    fields are another workflow's by name; only the recipient binding and the IP
    allow-list are this workflow's own.
``rules``
    The pure rules: the policy matrix, the allow-list, the evaluation that
    composes the four protections, and the bulk-download alert.
``engine``
    The reads and the writes, over the shared audited store. Two collections:
    the policy rows and the action log.
``errors``
    This workflow's own error types, so the feature module can map them without
    claiming a handler another workflow owns.

What is reused, and why that is the whole design
------------------------------------------------

The four bundled protections keep their own rows and their own rules. This
package reads them by their published field and collection names and evaluates
them with their own pure functions:

* the gate and its expiry are WF-069 ``link_gating.rules``;
* the NDA acceptance is WF-070 ``link_gating.agreement``;
* the watermark is WF-071 ``viewer_watermark``;
* the download AND is WF-072 ``view_only_access.rules``;
* the plan gate is WF-077 ``workspace_roles`` (through WF-071's rule);
* the action codes are WF-079 ``audit_export.vocabulary``.

It never imports ``dsr.api`` and never opens SQLite. Every read and write goes
through the :class:`~dsr.store.RecordStore` the HTTP layer hands in, so the audit
row is written in the same transaction as the change.

What is not implemented, stated plainly
---------------------------------------

The research warns that the headline capability has no open-source reference:
"**No open-source project implements a full audit log with revocation
semantics.** Coneshare is the closest." The audit surface here is therefore this
workflow's design, not a port, and :data:`vocabulary.LIMITATION` says so on every
response. Three of the spec's ``features_tools`` - end-to-end encryption,
certifications, and RBAC - are declined and named in
:data:`vocabulary.DECLINED`, because a capability named without a source is a
hypothesis and shipping it would be a claim this branch cannot keep.
"""

from __future__ import annotations

from .engine import ProtectionBook
from .errors import (
    ActionRefused,
    ContentProtectionError,
    PolicyNotFound,
    ProtectionDenied,
)
from .rules import (
    acceptance_ok,
    action_code_checked,
    bulk_download_alert,
    effective_settings,
    evaluate,
    ip_allowed,
    normalize_allowlist,
    normalize_policy,
    normalize_recipient,
    validate_action,
)
from .vocabulary import (
    ACTION_CODES,
    ACTION_COLLECTION,
    ACTION_DOWNLOAD,
    ACTION_REVOKE,
    ACTION_SHARE,
    ACTION_VIEW,
    ACTIONS,
    DECISION_ALLOW,
    DECISION_DENY,
    DECLINED,
    DEFAULT_BULK_ALERT_THRESHOLD,
    DEFAULT_BULK_ALERT_WINDOW_MINUTES,
    DEFAULT_LINK_COLLECTION,
    DENYING_PROTECTIONS,
    EVENT_CODE_FIELD,
    FIELD_AGREEMENT_ID,
    FIELD_ALLOW_DOWNLOAD,
    FIELD_EMAIL_AUTHENTICATED,
    FIELD_EMAIL_PROTECTED,
    FIELD_ENABLE_AGREEMENT,
    FIELD_ENABLE_WATERMARK,
    FIELD_EXPIRES_AT,
    FIELD_IP_ALLOWLIST,
    LIMITATION,
    OWNED_FIELDS,
    PLAN_FEATURE,
    PLAN_REQUIRED,
    POLICY_COLLECTION,
    POLICY_FIELDS,
    PROTECTION_DOWNLOAD,
    PROTECTION_EMAIL,
    PROTECTION_EXPIRY,
    PROTECTION_IP,
    PROTECTION_NDA,
    PROTECTION_PASSCODE,
    PROTECTION_REVOCATION,
    PROTECTION_WATERMARK,
    RECIPIENT,
    REPORTED_PROTECTIONS,
    REQUIRE_PASSCODE,
    REUSED_FIELDS,
    action_code,
    describe,
    honesty,
)

__all__ = [
    "ACTION_CODES",
    "ACTION_COLLECTION",
    "ACTIONS",
    "ACTION_DOWNLOAD",
    "ACTION_REVOKE",
    "ACTION_SHARE",
    "ACTION_VIEW",
    "DECISION_ALLOW",
    "DECISION_DENY",
    "DECLINED",
    "DEFAULT_BULK_ALERT_THRESHOLD",
    "DEFAULT_BULK_ALERT_WINDOW_MINUTES",
    "DEFAULT_LINK_COLLECTION",
    "DENYING_PROTECTIONS",
    "EVENT_CODE_FIELD",
    "FIELD_ALLOW_DOWNLOAD",
    "FIELD_AGREEMENT_ID",
    "FIELD_EMAIL_AUTHENTICATED",
    "FIELD_EMAIL_PROTECTED",
    "FIELD_ENABLE_AGREEMENT",
    "FIELD_ENABLE_WATERMARK",
    "FIELD_EXPIRES_AT",
    "FIELD_IP_ALLOWLIST",
    "LIMITATION",
    "OWNED_FIELDS",
    "PLAN_FEATURE",
    "PLAN_REQUIRED",
    "POLICY_COLLECTION",
    "POLICY_FIELDS",
    "PROTECTION_DOWNLOAD",
    "PROTECTION_EMAIL",
    "PROTECTION_EXPIRY",
    "PROTECTION_IP",
    "PROTECTION_NDA",
    "PROTECTION_PASSCODE",
    "PROTECTION_REVOCATION",
    "PROTECTION_WATERMARK",
    "RECIPIENT",
    "REPORTED_PROTECTIONS",
    "REQUIRE_PASSCODE",
    "REUSED_FIELDS",
    "ActionRefused",
    "ContentProtectionError",
    "PolicyNotFound",
    "ProtectionBook",
    "ProtectionDenied",
    "acceptance_ok",
    "action_code",
    "action_code_checked",
    "bulk_download_alert",
    "describe",
    "effective_settings",
    "evaluate",
    "honesty",
    "ip_allowed",
    "normalize_allowlist",
    "normalize_policy",
    "normalize_recipient",
    "validate_action",
]
