"""Every term WF-131 enforces against, and which workflow each one came from.

This workflow is a **bundle**. Its own Why field says so: "Bundles protections
already classified: WF-071, WF-072, WF-070 and the critical WF-079." So the
vocabulary here is almost entirely *imported*, and the only names this module
declares are the two things a bundle genuinely adds:

* the **binding** of those protections to a recipient (``recipient``), because
  none of the four workflows has a per-recipient matrix;
* the **IP allow-list** (``ip_allowlist``), which the spec names as a seller's
  policy setting ("the seller can allow-list IPs or revoke a link").

Everything else is a name another workflow already publishes, held here as a
constant equal to that workflow's own constant rather than as a new literal:

===========================  ==================================================
Field                        Owner
===========================  ==================================================
``email_protected``          WF-069 ``link_gating``
``email_authenticated``      WF-069 ``link_gating``
``expires_at``               WF-069 ``link_gating``
``enable_agreement``         WF-070 ``link_gating.agreement``
``agreement_id``             WF-070 ``link_gating.agreement``
``enable_watermark``         WF-071 ``viewer_watermark``
``allow_download``           WF-072 ``view_only_access``
action integer codes         WF-079 ``audit_export``
plan rank and tiers          WF-077 ``workspace_roles``
===========================  ==================================================

:func:`describe` publishes that table, and a test asserts each reused name is a
member of WF-069's own ``PRESET_COVERED_FIELDS`` - the list WF-069 quotes
verbatim from the research. A name that drifted out of that list would fail the
test rather than silently becoming this workflow's private vocabulary.

What this workflow does not do, stated plainly
----------------------------------------------

The research is explicit and this build repeats it rather than implying parity:

    "No open-source project implements a full audit log with revocation
    semantics. Coneshare is the closest."
    -- docs/research/digital-sales-room-workflows/wf/WF-131.md

So the audit surface here is this workflow's design, not a port, and
:data:`LIMITATION` says so on every response. Three items in the spec's
``features_tools`` are declined and named in :data:`DECLINED`, because a
capability named without a source is a hypothesis and shipping it as a feature
would be a claim this branch cannot keep.
"""

from __future__ import annotations

from typing import Any

from dsr.audit_export.vocabulary import (
    DOCUMENT_EXPIRED,
    DOCUMENT_FORWARDED,
    DOCUMENT_VIEWED,
    WORKSPACE_CODE_FLOOR,
)
from dsr.link_gating import rules as gate_rules
from dsr.link_gating.agreement import (
    AGREEMENT_COLLECTION,
    AGREEMENT_REF_FIELD,
    ENABLE_FIELD as AGREEMENT_ENABLE_FIELD,
)
from dsr.view_only_access import vocabulary as download_vocab
from dsr.viewer_watermark import vocabulary as watermark_vocab
from dsr.workspace_roles.vocabulary import PLAN_BUSINESS, PLAN_RANK, PLANS

# --------------------------------------------------------------------------- #
# Collections. Namespaced, because every feature shares one `records` table and
# `find()` matches on collection before it matches on anything else.
# --------------------------------------------------------------------------- #

#: The per-recipient policy matrix. One row per (link, recipient).
POLICY_COLLECTION = "wf131_recipient_policy"

#: The action log: view / download / share / revoke, one row per action.
ACTION_COLLECTION = "wf131_protection_action"

ALL_COLLECTIONS = (POLICY_COLLECTION, ACTION_COLLECTION)

# --------------------------------------------------------------------------- #
# The two names WF-131 adds, and the two references that keep a policy findable.
# --------------------------------------------------------------------------- #

#: The recipient an action or a policy belongs to. An email, normalised through
#: WF-069's own rule so that ``Buyer@`` and ``buyer@`` are one recipient here and
#: in every workflow this bundles.
RECIPIENT = "recipient"

#: The link a policy is bound to. A payload key and not the record envelope, for
#: the same reason WF-072 keeps its room in ``data``: the envelope's ``room_id``
#: is stripped out of ``data`` before the dynamic index is built, so a policy that
#: stored its link in the envelope could not be filtered by ``find()``.
LINK_REF = "link_id"

#: Which workflow's link collection ``LINK_REF`` names. The bundle's gate is a
#: WF-069 link - WF-069 is the workflow that decides who sees anything - so that
#: is the default. The field is here rather than hard-wired because a deployment
#: that gates a WF-121 tracked link the same way should not need a code change.
LINK_COLLECTION_FIELD = "link_collection"
DEFAULT_LINK_COLLECTION = gate_rules.LINK_COLLECTION

#: The payload-side twin of the envelope's ``room_id``. See WF-069's own note on
#: ``room_ref`` for why it is not called ``room_id``.
ROOM_REF = gate_rules.ROOM_REF

# --------------------------------------------------------------------------- #
# The per-recipient policy fields.
#
# Seven of them are names another workflow already publishes, held equal to that
# workflow's constant. Two are WF-131's own: the passcode requirement (WF-069
# stores one password for the whole link; requiring it per recipient is this
# workflow's binding) and the IP allow-list.
# --------------------------------------------------------------------------- #

#: WF-069: the viewer must give an email address.
FIELD_EMAIL_PROTECTED = "email_protected"

#: WF-069: the viewer must prove the inbox with a one-time code.
FIELD_EMAIL_AUTHENTICATED = "email_authenticated"

#: WF-069: a passcode exists on the link. The requirement is per recipient here.
FIELD_PASSWORD = "password"
REQUIRE_PASSCODE = "require_passcode"

#: WF-070: the NDA gate, and the agreement the viewer must accept.
FIELD_ENABLE_AGREEMENT = AGREEMENT_ENABLE_FIELD
FIELD_AGREEMENT_ID = AGREEMENT_REF_FIELD

#: WF-069: when the policy stops being open. A per-recipient expiry may tighten
#: the link's; it never extends it.
FIELD_EXPIRES_AT = "expires_at"

#: WF-071: the watermark toggle. A trace, never an access control.
FIELD_ENABLE_WATERMARK = watermark_vocab.ENABLE_FIELD

#: WF-072: the download switch, a three-way AND with the room and the item.
FIELD_ALLOW_DOWNLOAD = download_vocab.ALLOW_DOWNLOAD

#: WF-131's own: the addresses a recipient may open the link from. Empty means
#: open. An entry is an exact address or a CIDR block.
FIELD_IP_ALLOWLIST = "ip_allowlist"

#: The fields this workflow interprets, in the order a form presents them.
POLICY_FIELDS: tuple[str, ...] = (
    FIELD_EMAIL_PROTECTED,
    FIELD_EMAIL_AUTHENTICATED,
    REQUIRE_PASSCODE,
    FIELD_ENABLE_AGREEMENT,
    FIELD_AGREEMENT_ID,
    FIELD_EXPIRES_AT,
    FIELD_ENABLE_WATERMARK,
    FIELD_ALLOW_DOWNLOAD,
    FIELD_IP_ALLOWLIST,
)

#: The subset a second workflow publishes by exactly this name. A test asserts
#: every one of these is in WF-069's ``PRESET_COVERED_FIELDS``, which is the list
#: WF-069 quotes verbatim from the research's extensibility note.
REUSED_FIELDS: tuple[str, ...] = (
    FIELD_EMAIL_PROTECTED,
    FIELD_EMAIL_AUTHENTICATED,
    FIELD_EXPIRES_AT,
    FIELD_ENABLE_AGREEMENT,
    FIELD_AGREEMENT_ID,
    FIELD_ENABLE_WATERMARK,
    FIELD_ALLOW_DOWNLOAD,
)

#: WF-131's own two, so the boundary is a value rather than a comment.
OWNED_FIELDS: tuple[str, ...] = (REQUIRE_PASSCODE, FIELD_IP_ALLOWLIST)

#: Where each field came from. Served so the page can label a borrowed switch
#: with the workflow that owns it rather than implying this one invented it.
FIELD_SOURCES: dict[str, str] = {
    FIELD_EMAIL_PROTECTED: "WF-069 dsr.link_gating",
    FIELD_EMAIL_AUTHENTICATED: "WF-069 dsr.link_gating",
    FIELD_EXPIRES_AT: "WF-069 dsr.link_gating",
    FIELD_ENABLE_AGREEMENT: "WF-070 dsr.link_gating.agreement",
    FIELD_AGREEMENT_ID: "WF-070 dsr.link_gating.agreement",
    FIELD_ENABLE_WATERMARK: "WF-071 dsr.viewer_watermark",
    FIELD_ALLOW_DOWNLOAD: "WF-072 dsr.view_only_access",
    REQUIRE_PASSCODE: "WF-131 (per-recipient binding of WF-069's link password)",
    FIELD_IP_ALLOWLIST: "WF-131 (the spec's seller allow-list)",
}

#: Human labels, so a switch is never a bare field name in the UI.
FIELD_LABELS: dict[str, str] = {
    FIELD_EMAIL_PROTECTED: "Email verification",
    FIELD_EMAIL_AUTHENTICATED: "Email one-time code",
    REQUIRE_PASSCODE: "Passcode",
    FIELD_ENABLE_AGREEMENT: "NDA acceptance",
    FIELD_AGREEMENT_ID: "Agreement to accept",
    FIELD_EXPIRES_AT: "Expires at",
    FIELD_ENABLE_WATERMARK: "Watermark each view",
    FIELD_ALLOW_DOWNLOAD: "Allow download",
    FIELD_IP_ALLOWLIST: "IP allow-list",
}

#: One line of why each field exists, quoted or derived from the research.
FIELD_SUMMARIES: dict[str, str] = {
    FIELD_EMAIL_PROTECTED: "The room presents email verification before a buyer sees anything.",
    FIELD_EMAIL_AUTHENTICATED: (
        "A one-time code proves the inbox, stronger than email verification alone."
    ),
    REQUIRE_PASSCODE: "The room presents a passcode before a buyer sees anything.",
    FIELD_ENABLE_AGREEMENT: "The room presents an NDA to accept before content loads.",
    FIELD_AGREEMENT_ID: "Which agreement this recipient accepts.",
    FIELD_EXPIRES_AT: (
        "A per-recipient expiry. It tightens the link's own expiry and never extends it."
    ),
    FIELD_ENABLE_WATERMARK: "Each viewer's session is watermarked with their identity.",
    FIELD_ALLOW_DOWNLOAD: (
        "Downloads can be disabled entirely or allowed per file, over WF-072's three-way AND."
    ),
    FIELD_IP_ALLOWLIST: "The seller can allow-list IPs; a request from elsewhere is refused.",
}

# --------------------------------------------------------------------------- #
# The four logged actions.
#
# The spec lists them once, inside a parenthetical: "action log
# (view/download/share/revoke)". Three of the four verbs have an integer code
# WF-079 already publishes, so they carry that code verbatim. Download does not:
# WF-072 records a download on a view row's ``downloaded_at`` rather than as its
# own event, and WF-079's named lifecycle table has no download code. Minting one
# at or above WF-079's ``WORKSPACE_CODE_FLOOR`` is the extension point WF-079
# documents, so the export reads a real integer rather than an unclassified zero.
# --------------------------------------------------------------------------- #

ACTION_VIEW = "view"
ACTION_DOWNLOAD = "download"
ACTION_SHARE = "share"
ACTION_REVOKE = "revoke"

ACTIONS: tuple[str, ...] = (ACTION_VIEW, ACTION_DOWNLOAD, ACTION_SHARE, ACTION_REVOKE)

#: The action WF-079's export queries by, per action.
#:
#: ``share`` is logged as its own action rather than derived from ``view``, and
#: that is a recorded decision. The spec writes four verbs in one parenthetical;
#: three of them are distinct events a compliance reviewer asks about separately,
#: and a seller re-sharing a link is not a recipient viewing it. Deriving share
#: from view would make "who forwarded this on" unanswerable, which is exactly the
#: question a leak investigation opens with.
ACTION_CODES: dict[str, int] = {
    ACTION_VIEW: DOCUMENT_VIEWED,
    ACTION_DOWNLOAD: WORKSPACE_CODE_FLOOR + 10,
    ACTION_SHARE: DOCUMENT_FORWARDED,
    ACTION_REVOKE: DOCUMENT_EXPIRED,
}

#: Where each code came from, so the export page can say which are borrowed.
ACTION_CODE_SOURCES: dict[str, str] = {
    ACTION_VIEW: "WF-079 audit_export.vocabulary.DOCUMENT_VIEWED",
    ACTION_DOWNLOAD: (
        f"WF-131, minted at WORKSPACE_CODE_FLOOR+10 ({WORKSPACE_CODE_FLOOR + 10}); "
        "WF-079 names no download code"
    ),
    ACTION_SHARE: "WF-079 audit_export.vocabulary.DOCUMENT_FORWARDED",
    ACTION_REVOKE: (
        "WF-079 audit_export.vocabulary.DOCUMENT_EXPIRED; WF-069 states "
        "'Revocation is expiry, not disappearance'"
    ),
}

#: The payload key WF-079's export reads a declared code from. Carried on every
#: action row so the export needs no coordination with this workflow.
EVENT_CODE_FIELD = "event_code"

# --------------------------------------------------------------------------- #
# The decision's own states.
# --------------------------------------------------------------------------- #

#: A request the whole matrix permitted.
DECISION_ALLOW = "allow"

#: A request at least one protection withheld.
DECISION_DENY = "deny"

DECISIONS: tuple[str, ...] = (DECISION_ALLOW, DECISION_DENY)

#: The per-protection short names a decision reports, so a page can say which
#: control refused rather than only that something did.
PROTECTION_ACCESS = "access"
PROTECTION_NDA = "nda"
PROTECTION_EMAIL = "email"
PROTECTION_PASSCODE = "passcode"
PROTECTION_IP = "ip"
PROTECTION_EXPIRY = "expiry"
PROTECTION_REVOCATION = "revocation"
PROTECTION_DOWNLOAD = "download"
PROTECTION_WATERMARK = "watermark"

#: The protections that can withhold content, in the order a viewer meets them.
#: ``download`` withholds a file, not the room, and ``watermark`` never withholds
#: anything - it is reported because a reader wants the whole matrix in one place.
DENYING_PROTECTIONS: tuple[str, ...] = (
    PROTECTION_REVOCATION,
    PROTECTION_EXPIRY,
    PROTECTION_IP,
    PROTECTION_EMAIL,
    PROTECTION_PASSCODE,
    PROTECTION_NDA,
)

REPORTED_PROTECTIONS: tuple[str, ...] = (
    *DENYING_PROTECTIONS,
    PROTECTION_DOWNLOAD,
    PROTECTION_WATERMARK,
)

# --------------------------------------------------------------------------- #
# Bulk download is an alert, not a block.
# --------------------------------------------------------------------------- #

#: The research's automation is "auto-alert on a bulk-download pattern", which is
#: distinct from WF-072's "block bulk download". A bundle that ran the two
#: together would turn every alert into a blockage, so the two are separate rules
#: and separate switches: WF-072 blocks, this alerts.
DEFAULT_BULK_ALERT_THRESHOLD = 5
DEFAULT_BULK_ALERT_WINDOW_MINUTES = 60

# --------------------------------------------------------------------------- #
# Plan entitlement, reused from WF-077 / WF-071
# --------------------------------------------------------------------------- #

#: Enabling a watermark through this bundle meets WF-071's plan gate, which is
#: WF-077's rule quoted in full. Declared here so the vocabulary can publish it.
PLAN_FEATURE = watermark_vocab.PLAN_FEATURE
PLAN_REQUIRED = PLAN_BUSINESS

# --------------------------------------------------------------------------- #
# Honesty, as data.
# --------------------------------------------------------------------------- #

#: Rendered on the page and carried in every response, because the cheap way to
#: keep a claim honest is to make it part of the data rather than a line of copy
#: somebody can delete.
LIMITATION = (
    "This bundles four protections and one action log. The audit surface is this "
    "workflow's own design, not a port: the research states that no open-source "
    "project implements a full audit log with revocation semantics, and Coneshare "
    "is the closest. A watermark deters and traces; it does not stop a photograph. "
    "Revocation is evaluated on every request rather than at the edge, because this "
    "product has no edge to invalidate a token at."
)

SCOPE_OWNER = (
    "WF-131 owns the per-recipient policy matrix and the action log. The gate is "
    "WF-069's, the NDA acceptance is WF-070's, the watermark is WF-071's, the "
    "download AND is WF-072's, the plan gate is WF-077's and the action codes are "
    "WF-079's; this workflow composes them and re-implements none of them."
)

READ_SOURCE = (
    "The four protections are read as data, by their published field names and "
    "collection names, and evaluated with their own pure rules. This package never "
    "imports another workflow's feature module and never writes another workflow's "
    "collection."
)

#: The spec's three unsourced items, and this build's answer to each.
DECLINED: list[dict[str, str]] = [
    {
        "claim": "end-to-end encryption",
        "why": (
            "Named in features_tools with no supporting API, no source and no "
            "evidence anywhere in the block, and the repository holds no document "
            "bytes to encrypt. Shipping a switch would be a claim, not a control."
        ),
    },
    {
        "claim": "certifications (SOC 2 / ISO 27001 / GDPR / HIPAA / CCPA)",
        "why": (
            "A certification is an audit outcome about an organisation, not a "
            "feature a branch can implement. The research quotes them from a "
            "competitor's marketing page."
        ),
    },
    {
        "claim": "RBAC",
        "why": (
            "It is WF-077's surface and WF-077 is merged. Re-declaring a role model "
            "here would give the product two, which is exactly the drift this "
            "bundle's reuse rule exists to prevent."
        ),
    },
]


def action_code(action: Any) -> int:
    """The integer code for an action name, or ``None`` sentinel ``-1`` if unknown.

    Returns ``-1`` rather than raising so a caller can report an unrecognised verb
    without losing the row; :func:`~dsr.content_protection.rules.validate_action`
    is where an unknown verb is actually refused at authoring time.
    """
    return ACTION_CODES.get(str(action or "").strip().lower(), -1)


def describe() -> dict[str, Any]:
    """The whole vocabulary, as one payload, with every source named.

    Served at ``GET /api/wf-131/vocabulary`` so the panel renders its switches
    from the same table the rules validate against, and a reviewer can see that
    seven of the nine fields are another workflow's by name.
    """
    return {
        "ticket": "WF-131",
        "fields": [
            {
                "field": field,
                "label": FIELD_LABELS.get(field, field),
                "summary": FIELD_SUMMARIES.get(field, ""),
                "source": FIELD_SOURCES.get(field, "WF-131"),
                "reused": field in REUSED_FIELDS,
            }
            for field in POLICY_FIELDS
        ],
        "reused_fields": list(REUSED_FIELDS),
        "owned_fields": list(OWNED_FIELDS),
        "actions": [
            {
                "action": action,
                "code": ACTION_CODES[action],
                "code_source": ACTION_CODE_SOURCES[action],
            }
            for action in ACTIONS
        ],
        "event_code_field": EVENT_CODE_FIELD,
        "protections": list(REPORTED_PROTECTIONS),
        "denying_protections": list(DENYING_PROTECTIONS),
        "decisions": list(DECISIONS),
        "bulk_alert": {
            "threshold": DEFAULT_BULK_ALERT_THRESHOLD,
            "window_minutes": DEFAULT_BULK_ALERT_WINDOW_MINUTES,
            "note": (
                "An alert, not a block. WF-072 blocks a bulk download; this "
                "workflow only reports the pattern."
            ),
        },
        "plan": {
            "feature": PLAN_FEATURE,
            "requires": PLAN_REQUIRED,
            "plans": list(PLANS),
            "rank": dict(PLAN_RANK),
            "gate_source": "WF-071 dsr.viewer_watermark.rules.require_plan (WF-077's rule)",
        },
        "reused": {
            "link_collection": DEFAULT_LINK_COLLECTION,
            "agreement_collection": AGREEMENT_COLLECTION,
            "watermark_fields": [FIELD_ENABLE_WATERMARK, watermark_vocab.CONFIG_FIELD],
            "download_fields": [
                download_vocab.BULK_DOWNLOAD,
                download_vocab.ALLOW_DOWNLOAD,
                download_vocab.CAN_DOWNLOAD,
            ],
            "action_codes_source": "WF-079 dsr.audit_export.vocabulary",
            "workspace_code_floor": WORKSPACE_CODE_FLOOR,
            "preset_covered_fields": list(gate_rules.PRESET_COVERED_FIELDS),
        },
        "collections": {"policies": POLICY_COLLECTION, "actions": ACTION_COLLECTION},
        SCOPE_OWNER_KEY: SCOPE_OWNER,
        READ_SOURCE_KEY: READ_SOURCE,
        LIMITATION_KEY: LIMITATION,
        "declined": [dict(item) for item in DECLINED],
    }


#: The keys every response carries, so a caller cannot read the matrix without
#: also reading what it is worth and which part is not sourced.
SCOPE_OWNER_KEY = "scope_owner"
READ_SOURCE_KEY = "read_source"
LIMITATION_KEY = "limitation"
HONESTY_FIELDS = (SCOPE_OWNER_KEY, READ_SOURCE_KEY, LIMITATION_KEY)


def honesty() -> dict[str, Any]:
    """The three honesty sentences as a payload, for a response or a page."""
    return {
        SCOPE_OWNER_KEY: SCOPE_OWNER,
        READ_SOURCE_KEY: READ_SOURCE,
        LIMITATION_KEY: LIMITATION,
        "declined": [dict(item) for item in DECLINED],
    }


__all__ = [
    "ACTION_CODES",
    "ACTION_COLLECTION",
    "ACTION_CODE_SOURCES",
    "ACTION_DOWNLOAD",
    "ACTION_REVOKE",
    "ACTION_SHARE",
    "ACTION_VIEW",
    "ACTIONS",
    "ALL_COLLECTIONS",
    "DECISIONS",
    "DECISION_ALLOW",
    "DECISION_DENY",
    "DECLINED",
    "DEFAULT_BULK_ALERT_THRESHOLD",
    "DEFAULT_BULK_ALERT_WINDOW_MINUTES",
    "DEFAULT_LINK_COLLECTION",
    "DENYING_PROTECTIONS",
    "EVENT_CODE_FIELD",
    "FIELD_AGREEMENT_ID",
    "FIELD_ALLOW_DOWNLOAD",
    "FIELD_EMAIL_AUTHENTICATED",
    "FIELD_EMAIL_PROTECTED",
    "FIELD_ENABLE_AGREEMENT",
    "FIELD_ENABLE_WATERMARK",
    "FIELD_EXPIRES_AT",
    "FIELD_IP_ALLOWLIST",
    "FIELD_LABELS",
    "FIELD_PASSWORD",
    "FIELD_SOURCES",
    "FIELD_SUMMARIES",
    "LINK_COLLECTION_FIELD",
    "LINK_REF",
    "LIMITATION",
    "LIMITATION_KEY",
    "OWNED_FIELDS",
    "PLAN_FEATURE",
    "PLAN_REQUIRED",
    "POLICY_COLLECTION",
    "POLICY_FIELDS",
    "PROTECTION_ACCESS",
    "PROTECTION_DOWNLOAD",
    "PROTECTION_EMAIL",
    "PROTECTION_EXPIRY",
    "PROTECTION_IP",
    "PROTECTION_NDA",
    "PROTECTION_PASSCODE",
    "PROTECTION_REVOCATION",
    "PROTECTION_WATERMARK",
    "READ_SOURCE",
    "READ_SOURCE_KEY",
    "RECIPIENT",
    "REPORTED_PROTECTIONS",
    "REQUIRE_PASSCODE",
    "REUSED_FIELDS",
    "ROOM_REF",
    "SCOPE_OWNER",
    "SCOPE_OWNER_KEY",
    "action_code",
    "describe",
    "honesty",
]
