"""The vocabulary WF-121 accepts, and where each part of it came from.

This workflow restates a capability WF-018 and WF-037 already provide, so the
values and the rules behind them are **imported, never re-declared**:

* the revisit rule is WF-018's ``SESSION_GAP_SECONDS`` and its
  :func:`~dsr.pdf_analytics.metrics.group_sessions`, because a reading that
  resumes after a long pause is a new reading in both workflows;
* the per-page arithmetic is WF-018's
  :func:`~dsr.pdf_analytics.metrics.dwell_per_page` and
  :func:`~dsr.pdf_analytics.metrics.drop_off_per_page`;
* the engagement record a view becomes carries WF-037's canonical field names
  (:data:`~dsr.crm_engagement.vocabulary.CANONICAL_FIELDS`), so a field map that
  already routes a room event to the CRM routes this one with no new row;
* the expiry boundary and email normalisation are WF-069's
  :func:`~dsr.link_gating.rules.expiry_state` and
  :func:`~dsr.link_gating.rules.normalize_email`.

What is *not* reused is the authoring surface, because it is genuinely new: one
link a rep shares, the delivery rows naming who it reached, the alert the
tracked view raises, and the write-back of that view to the room's CRM
opportunity. Those live in :mod:`.rules` and :mod:`.book`.

Anything the research does not name the value of - the link lifetime, the
"n-th page view" threshold - is an inference, recorded in :mod:`.inferences` and
served beside the sourced half at ``/api/wf-121/inferences``.
"""

from __future__ import annotations

from typing import Any

from dsr.crm_engagement.vocabulary import CANONICAL_FIELDS, FIELD_SYNONYMS
from dsr.crm_integration.vocabulary import CRM_OBJECTS, CRM_SYSTEMS
from dsr.pdf_analytics.vocabulary import (
    ASSET_TYPES,
    AUDIENCES,
    SESSION_GAP_SECONDS,
    as_int,
    as_seconds,
    audience_of,
)

#: The collection names this workflow owns. Namespaced, because every feature
#: shares one ``records`` table and ``find()`` matches on collection first.
LINK_COLLECTION = "wf121_tracked_link"
VIEW_COLLECTION = "wf121_link_page_view"
DELIVERY_COLLECTION = "wf121_link_delivery"
ALERT_COLLECTION = "wf121_link_alert"

#: The room reference, carried in ``data`` rather than left to the envelope.
#: ``room_id`` is part of the record *envelope*, and
#: :meth:`dsr.db.audited.AuditedDatabase._insert_record` strips it out of
#: ``data`` before the dynamic index is built, so a link that stored its room
#: there could not be filtered by ``find()``. WF-069 keeps the same twin key for
#: the same reason.
ROOM_REF = "room_ref"

#: The states a tracked link can be in. ``active`` is the only one a view is
#: recorded against. ``expired`` and ``revoked`` answer with the same closed
#: page, so a buyer holding a forwarded URL cannot tell "this deal closed" from
#: "you were cut off" - the rule WF-069 researched.
LINK_STATES: tuple[str, ...] = ("active", "expired", "revoked")

#: What a link points at. Reuses WF-018's asset types: the link distributes one
#: library asset, and per-page dwell exists only for a multi-page PDF.
LINK_TARGET_KINDS: tuple[str, ...] = ASSET_TYPES

#: The revisit gap, reused rather than re-declared. Two page views by the same
#: viewer more than this far apart are two readings, not one.
REVISIT_GAP_SECONDS = SESSION_GAP_SECONDS

#: The event type a tracked view becomes in the room's engagement feed, and the
#: CRM write-back. One name, so a field map and the feed agree.
ENGAGEMENT_EVENT_TYPE = "tracked_deal_link_view"

#: The canonical engagement fields this workflow fills, checked against WF-037's
#: own vocabulary at import time so a rename there cannot silently detach this
#: workflow from the field maps that route it. ``buyer_crm_id`` is optional -
#: an email-gated viewer has none until WF-042 resolves it.
ENGAGEMENT_FIELDS: tuple[str, ...] = (
    "type",
    "occurred_at",
    "asset",
    "dwell_seconds",
    "buyer_email",
    "buyer_crm_id",
)

for _field in ENGAGEMENT_FIELDS:  # pragma: no cover - an import-time contract check
    if _field not in CANONICAL_FIELDS:
        raise RuntimeError(
            f"WF-121 uses canonical engagement field {_field!r}, which WF-037 no longer declares"
        )
del _field

#: The CRM object a tracked view is written back to. "deal" is the opportunity
#: in WF-042's three-object vocabulary; a contact or an account is not what the
#: researched flow names as the target of the write-back.
ENGAGEMENT_CRM_OBJECT = "deal"
if ENGAGEMENT_CRM_OBJECT not in CRM_OBJECTS:  # pragma: no cover - contract check
    raise RuntimeError("WF-042's object vocabulary no longer contains 'deal'")

#: The reasons a tracked view raises a rep alert. ``first_view`` is the ordinary
#: open; the other three are the researched automation, "auto-notify rep on N-th
#: page view or on return visit", plus a dwell threshold, which is this build's
#: reading of the same sentence and is recorded as an inference.
ALERT_TRIGGERS: tuple[str, ...] = (
    "first_view",
    "return_visit",
    "page_threshold",
    "dwell_threshold",
)

#: The alert tones the page renders with. Named here so the API and the page use
#: one spelling, and so a trigger added later arrives with its own presentation
#: rather than falling back to whatever the client guessed.
ALERT_TONES: dict[str, str] = {
    "first_view": "info",
    "return_visit": "insert",
    "page_threshold": "update",
    "dwell_threshold": "restore",
}

#: ``(input key, stored key)`` aliases accepted on write. The stored key is this
#: workflow's own; ``buyer_email`` and ``buyer_crm_id`` are WF-037's spellings
#: and need no alias.
_ALIASES: dict[str, str] = {
    "email": "viewer",
    "viewer_email": "viewer",
    "person": "viewer",
    "session": "session_id",
    "sessionid": "session_id",
    "is_internal": "is_internal",
    "internal": "is_internal",
    "asset": "asset_id",
    "document_id": "asset_id",
    "assetid": "asset_id",
    "share_url": "share_url",
    "shareurl": "share_url",
    "deal_id": "crm_deal_id",
    "opportunity_id": "crm_deal_id",
    "opportunity": "crm_deal_id",
    "crm_deal": "crm_deal_id",
    "crm_system": "crm_system",
    "vendor": "crm_system",
    "alert_on_page": "alert_on_page",
    "page_threshold": "alert_on_page",
    "notify_on_return": "notify_on_return",
    "lifetime_days": "lifetime_days",
    "expires_at": "expires_at",
    "expiresin": "lifetime_days",
    "tracking_enabled": "tracking_enabled",
    "require_viewer": "require_viewer",
    "timings": "timings",
    "pages": "timings",
    "crm_contact_id": "buyer_crm_id",
    "buyer_id": "buyer_crm_id",
}

#: The keys a viewer payload must never carry. The documented open-source
#: analogue for this workflow, DeckLens, "explicitly does not transmit document
#: bodies or raw form values", and the research calls that a privacy boundary
#: rather than an accident. Refusing the keys by name is what makes the boundary
#: a rule instead of a promise.
REFUSED_PAYLOAD_KEYS: tuple[str, ...] = (
    "body",
    "content",
    "document_body",
    "file",
    "file_bytes",
    "pages_content",
    "form_values",
    "raw_form",
    "text",
)

#: The researched spellings of the fields a *view* carries, which is what
#: :func:`normalise_view`` validates. Listed as data so the vocabulary route can
#: publish them.
VIEW_FIELDS: tuple[str, ...] = (
    "viewer",
    "session_id",
    "is_internal",
    "occurred_at",
    "timings",
    "crm_deal_id",
    "crm_system",
    "buyer_crm_id",
)

#: The fields a *link* carries.
LINK_FIELDS: tuple[str, ...] = (
    "title",
    "asset_id",
    "recipients",
    "expires_at",
    "lifetime_days",
    "alert_on_page",
    "notify_on_return",
    "tracking_enabled",
    "require_viewer",
    "crm_deal_id",
    "crm_system",
    "share_url",
)

#: Defaults that are inferences, exported so the publisher has one source.
DEFAULT_LINK_LIFETIME_DAYS = 30
DEFAULT_ALERT_PAGE_THRESHOLD = 3
DEFAULT_DWELL_ALERT_SECONDS = 120


def alias_for(key: Any) -> str:
    """The stored key for an input key, honouring the documented aliases."""
    text = str(key)
    return _ALIASES.get(text, _ALIASES.get(text.lower(), text))


def require_viewer(value: Any) -> str:
    """A viewer identity, or a refusal.

    The researched data source is "user identity (email gate or CRM-matched
    user)", so the value is an email address. It is normalised through WF-069's
    own rule, which case-folds the whole address: ``Buyer@`` and ``buyer@`` are
    one visitor, not two.
    """
    from dsr.link_gating import rules as gate_rules

    return gate_rules.normalize_email(value)


def require_crm_system(value: Any) -> str | None:
    """A CRM vendor name this house writes to, or ``None``."""
    if value is None or str(value).strip() == "":
        return None
    name = str(value).strip().lower()
    if name not in CRM_SYSTEMS:
        from .errors import LinkValidationError

        raise LinkValidationError(
            f"crm_system must be one of {list(CRM_SYSTEMS)}, got {value!r}",
            field="crm_system",
            allowed=list(CRM_SYSTEMS),
        )
    return name


def describe() -> dict[str, Any]:
    """The whole accepted vocabulary, as one payload.

    Served at ``GET /api/wf-121/vocabulary`` so the page renders its pickers
    from the same source the rules enforce against. The values shared with
    WF-018 and WF-037 are labelled as such, so a reader can see that the session
    gap and the engagement field names have exactly one definition.
    """
    return {
        "link_states": list(LINK_STATES),
        "link_target_kinds": list(LINK_TARGET_KINDS),
        "link_fields": list(LINK_FIELDS),
        "view_fields": list(VIEW_FIELDS),
        "audiences": list(AUDIENCES),
        "alert_triggers": list(ALERT_TRIGGERS),
        "alert_tones": dict(ALERT_TONES),
        "engagement_event_type": ENGAGEMENT_EVENT_TYPE,
        "engagement_crm_object": ENGAGEMENT_CRM_OBJECT,
        "engagement_fields": {name: CANONICAL_FIELDS.get(name, "") for name in ENGAGEMENT_FIELDS},
        "engagement_field_synonyms": {
            name: list(FIELD_SYNONYMS.get(name, ())) for name in ENGAGEMENT_FIELDS
        },
        "crm_systems": list(CRM_SYSTEMS),
        "crm_objects": list(CRM_OBJECTS),
        "refused_payload_keys": list(REFUSED_PAYLOAD_KEYS),
        "defaults": {
            "link_lifetime_days": DEFAULT_LINK_LIFETIME_DAYS,
            "alert_page_threshold": DEFAULT_ALERT_PAGE_THRESHOLD,
            "dwell_alert_seconds": DEFAULT_DWELL_ALERT_SECONDS,
        },
        "reused": {
            "revisit_gap_seconds": REVISIT_GAP_SECONDS,
            "revisit_gap_source": "WF-018 dsr.pdf_analytics.vocabulary.SESSION_GAP_SECONDS",
            "dwell_rules_source": "WF-018 dsr.pdf_analytics.metrics",
            "engagement_fields_source": "WF-037 dsr.crm_engagement.vocabulary.CANONICAL_FIELDS",
            "expiry_rule_source": "WF-069 dsr.link_gating.rules.expiry_state",
        },
        "value_helpers": {
            "as_seconds": "dsr.pdf_analytics.vocabulary.as_seconds",
            "as_int": "dsr.pdf_analytics.vocabulary.as_int",
            "audience_of": "dsr.pdf_analytics.vocabulary.audience_of",
        },
        "collections": {
            "links": LINK_COLLECTION,
            "views": VIEW_COLLECTION,
            "deliveries": DELIVERY_COLLECTION,
            "alerts": ALERT_COLLECTION,
        },
    }


__all__ = [
    "ALERT_COLLECTION",
    "ALERT_TONES",
    "ALERT_TRIGGERS",
    "ASSET_TYPES",
    "AUDIENCES",
    "CANONICAL_FIELDS",
    "CRM_OBJECTS",
    "CRM_SYSTEMS",
    "DEFAULT_ALERT_PAGE_THRESHOLD",
    "DEFAULT_DWELL_ALERT_SECONDS",
    "DEFAULT_LINK_LIFETIME_DAYS",
    "DELIVERY_COLLECTION",
    "ENGAGEMENT_CRM_OBJECT",
    "ENGAGEMENT_EVENT_TYPE",
    "ENGAGEMENT_FIELDS",
    "FIELD_SYNONYMS",
    "LINK_COLLECTION",
    "LINK_FIELDS",
    "LINK_STATES",
    "LINK_TARGET_KINDS",
    "REFUSED_PAYLOAD_KEYS",
    "REVISIT_GAP_SECONDS",
    "ROOM_REF",
    "SESSION_GAP_SECONDS",
    "VIEW_COLLECTION",
    "VIEW_FIELDS",
    "alias_for",
    "as_int",
    "as_seconds",
    "audience_of",
    "describe",
    "require_crm_system",
    "require_viewer",
]
