"""WF-121: tracked deal-link distribution with per-page dwell.

One link, shared with a buying committee, opened in an in-browser viewer. The
system records who viewed, which page, for how long, on which revisit; it alerts
the rep; and it writes the engagement record back to the room's CRM opportunity.

The specification is ``docs/research/digital-sales-room-workflows/wf/WF-121.md``,
quoted in full on issue #122. This package is that specification as code, and it
is deliberately small because two merged workflows already own most of it:

======================  =====================================================
Researched behaviour    Where it lives
======================  =====================================================
per-page dwell          :func:`~dsr.pdf_analytics.metrics.dwell_per_page`,
                        re-used by :mod:`.rules`
drop-off per page       :func:`~dsr.pdf_analytics.metrics.drop_off_per_page`
"which revisit"         :func:`~dsr.pdf_analytics.metrics.group_sessions` on
                        WF-018's ``SESSION_GAP_SECONDS``
link expiry boundary    :func:`~dsr.link_gating.rules.expiry_state`
email normalisation     :func:`~dsr.link_gating.rules.normalize_email`
CRM write-back          :class:`~dsr.crm_engagement.EngagementSync`
======================  =====================================================

What this package adds is the authoring surface and the missing joins: the link
a rep shares, the delivery rows naming who it reached, the per-revisit
attribution, the rep alert, and the engagement record that carries WF-042's
``deal`` object name and the room's opportunity id.

No migration, no typed column, no required field. Four open collections of
arbitrary JSON (links, page views, deliveries, alerts), filtered with ``find()``
over dotted paths. A team that adds ``pricingTier`` to a link filters on it the
same day.

The privacy boundary comes from the research. Its open-source analogue for this
workflow, DeckLens, "explicitly does not transmit document bodies or raw form
values", so :func:`~.rules.refuse_document_bodies` refuses those keys by name:
the ingest cannot accept a document body even if a caller sends one.

The judgement calls the research leaves open - the link lifetime, the n-th page
view, the dwell threshold - are collected in :mod:`.inferences` and served at
``GET /api/wf-121/inferences``.
"""

from __future__ import annotations

from .book import TrackedLinkBook
from .errors import (
    DistributionRefused,
    DwellRefused,
    LinkClosed,
    LinkNotFound,
    LinkValidationError,
    TrackedLinkError,
    ViewerRequired,
)
from .inferences import INFERENCES, describe as describe_inferences
from .rules import (
    MAX_LIFETIME_DAYS,
    MIN_LIFETIME_DAYS,
    alert_decisions,
    drop_off_per_page,
    engagement_record,
    group_revisits,
    link_state,
    normalise_link,
    normalise_view,
    page_dwell,
    refuse_document_bodies,
    require_open,
    revisit_index,
    summarise_view,
    timing_rows,
)
from .vocabulary import (
    ALERT_COLLECTION,
    ALERT_TONES,
    ALERT_TRIGGERS,
    DEFAULT_ALERT_PAGE_THRESHOLD,
    DEFAULT_DWELL_ALERT_SECONDS,
    DEFAULT_LINK_LIFETIME_DAYS,
    DELIVERY_COLLECTION,
    ENGAGEMENT_CRM_OBJECT,
    ENGAGEMENT_EVENT_TYPE,
    ENGAGEMENT_FIELDS,
    LINK_COLLECTION,
    LINK_STATES,
    REFUSED_PAYLOAD_KEYS,
    REVISIT_GAP_SECONDS,
    ROOM_REF,
    VIEW_COLLECTION,
    describe as describe_vocabulary,
)

__all__ = [
    "ALERT_COLLECTION",
    "ALERT_TONES",
    "ALERT_TRIGGERS",
    "DEFAULT_ALERT_PAGE_THRESHOLD",
    "DEFAULT_DWELL_ALERT_SECONDS",
    "DEFAULT_LINK_LIFETIME_DAYS",
    "DELIVERY_COLLECTION",
    "DistributionRefused",
    "DwellRefused",
    "ENGAGEMENT_CRM_OBJECT",
    "ENGAGEMENT_EVENT_TYPE",
    "ENGAGEMENT_FIELDS",
    "INFERENCES",
    "LINK_COLLECTION",
    "LINK_STATES",
    "LinkClosed",
    "LinkNotFound",
    "LinkValidationError",
    "MAX_LIFETIME_DAYS",
    "MIN_LIFETIME_DAYS",
    "REFUSED_PAYLOAD_KEYS",
    "REVISIT_GAP_SECONDS",
    "ROOM_REF",
    "TrackedLinkBook",
    "TrackedLinkError",
    "VIEW_COLLECTION",
    "ViewerRequired",
    "alert_decisions",
    "describe_inferences",
    "describe_vocabulary",
    "drop_off_per_page",
    "engagement_record",
    "group_revisits",
    "link_state",
    "normalise_link",
    "normalise_view",
    "page_dwell",
    "refuse_document_bodies",
    "require_open",
    "revisit_index",
    "summarise_view",
    "timing_rows",
]
