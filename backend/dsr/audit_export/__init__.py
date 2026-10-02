"""WF-079: export a tamper-evident audit trail with IP and verification outcomes.

The domain rules live here rather than in the feature module so the folder stays
small and every rule is testable without a request. The feature module is the
router; this package is the workflow.

Modules
-------
``vocabulary``
    The integer action codes a compliance review queries by, the reserved bands,
    and every place this build had to infer rather than cite.
``integrity``
    The tamper-evidence: what is hashed, in what order, and how a third party
    reproduces the check from the export alone.
``access``
    The administrator gate and the sandbox key. The two rules the research states
    about who may read the export and what a sandbox caller is shown.
``entries``
    The projection of an audited write into exportable evidence, including client
    addresses, the sandbox masking rule, and the per-document trail.
``anchors``
    Pinning a trail's chain head so a later read can falsify it, rather than only
    agreeing with itself.
``reports``
    The date-ranged CSV compliance report, every researched constraint on it, and
    the recorded-notification delivery the source describes.

There is deliberately no ``engine`` module. The first draft put the role gate and
the route operations in one, which made it a second place for the gate to live; the
gate belongs to :mod:`dsr.audit_export.access` and the operations to the domain
modules that own them, so nothing sits between the router and the rules.
"""

from __future__ import annotations

from dsr.audit_export.access import (
    AUDIT_READER_ROLE,
    DENIED_CODE,
    DENIED_DETAIL,
    DENIED_REMEDIATION,
    SANDBOX_ENV,
    SANDBOX_IP,
    AccessDenied,
    gate_payload,
    require_administrator,
    sandbox_enabled,
)
from dsr.audit_export.anchors import (
    ANCHOR_COLLECTION,
    MAX_ANCHORED_ENTRIES,
    scope_of,
)
from dsr.audit_export.entries import (
    EMAIL_UNRESOLVED,
    IP_HIDDEN,
    IP_NOT_CAPTURED,
    IP_OBSERVATION_COLLECTION,
    IP_OBSERVED,
    ObservationRefused,
    actor_email,
    build_trail,
    derive_action_code,
    document_trail,
    evidence_pack,
    observe_ip,
    peer_address,
    summarise,
)
from dsr.audit_export.integrity import (
    ALGORITHM,
    CHAIN_PREFIX,
    HASHED_FIELDS,
    chain_seed,
    chain_step,
    document_hash,
    document_hash_basis,
    integrity_payload,
    seal,
    verify,
)
from dsr.audit_export.reports import (
    COLUMNS as REPORT_COLUMNS,
    DOWNLOAD_PATH_TEMPLATE,
    END_DATE_INCLUSIVE,
    MAX_LOOKBACK_YEARS,
    MAX_RANGE_MONTHS,
    REPORT_COLLECTION,
    REPORT_TYPES,
    STATUS_PENDING,
    STATUS_READY,
    ReportError,
    render_csv,
    validate_range,
)
from dsr.audit_export.vocabulary import (
    UNCLASSIFIED,
    WORKSPACE_CODE_FLOOR,
    ActionCode,
    describe_code,
    verification_code,
    vocabulary_payload,
)

__all__ = [
    "ALGORITHM",
    "ANCHOR_COLLECTION",
    "AUDIT_READER_ROLE",
    "CHAIN_PREFIX",
    "COLUMNS",
    "DENIED_CODE",
    "DENIED_DETAIL",
    "DENIED_REMEDIATION",
    "DOWNLOAD_PATH_TEMPLATE",
    "EMAIL_UNRESOLVED",
    "END_DATE_INCLUSIVE",
    "HASHED_FIELDS",
    "IP_HIDDEN",
    "IP_NOT_CAPTURED",
    "IP_OBSERVATION_COLLECTION",
    "IP_OBSERVED",
    "MAX_ANCHORED_ENTRIES",
    "MAX_LOOKBACK_YEARS",
    "MAX_RANGE_MONTHS",
    "REPORT_COLLECTION",
    "REPORT_COLUMNS",
    "REPORT_TYPES",
    "SANDBOX_ENV",
    "SANDBOX_IP",
    "STATUS_PENDING",
    "STATUS_READY",
    "UNCLASSIFIED",
    "WORKSPACE_CODE_FLOOR",
    "AccessDenied",
    "ActionCode",
    "ObservationRefused",
    "ReportError",
    "actor_email",
    "build_trail",
    "chain_seed",
    "chain_step",
    "describe_code",
    "derive_action_code",
    "document_hash",
    "document_hash_basis",
    "document_trail",
    "download_path",
    "evidence_pack",
    "gate_payload",
    "integrity_payload",
    "observe_ip",
    "peer_address",
    "render_csv",
    "require_administrator",
    "sandbox_enabled",
    "scope_of",
    "seal",
    "summarise",
    "validate_range",
    "verification_code",
    "verify",
    "vocabulary_payload",
]

#: ``COLUMNS`` is the per-report-type column map from :mod:`dsr.audit_export.reports`.
COLUMNS = REPORT_COLUMNS


def download_path(report_id: str, token: str) -> str:
    """The link a delivery record carries, built from the one template.

    Exported as a function rather than left as string formatting at the call site
    because the link is the researched artefact - "an email ... containing a link
    to download the report as a CSV file" - and a link assembled two different ways
    is a link that works in one response and not the other.
    """
    return DOWNLOAD_PATH_TEMPLATE.format(report_id=report_id, token=token)
