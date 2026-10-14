"""WF-071: stamp a dynamic per-viewer watermark on every page of a tracked link.

A rep turns a watermark on for a link and writes its text. At view time the
renderer substitutes the identity tokens with values bound to *that view* - the
viewer's own email, the date and time, the link's public URL and the viewer's IP
address - and composites the overlay onto every page. Nothing is burned into a
stored document; the watermark exists only in the delivered view.

The specification is ``docs/research/digital-sales-room-workflows/wf/WF-071.md``,
quoted in full on issue #133. This package is that specification as code, and it
is deliberately small because WF-121 already owns most of what it needs:

=========================  ==================================================
Researched behaviour       Where it lives
=========================  ==================================================
the shareable link          :data:`dsr.tracked_deal_link.LINK_COLLECTION`
the viewer's page optics    :data:`dsr.tracked_deal_link.VIEW_COLLECTION`
viewer identity             :func:`dsr.tracked_deal_link.vocabulary.require_viewer`,
                            which is WF-069's ``normalize_email``
the link-open boundary      :func:`dsr.tracked_deal_link.rules.require_open`
plan entitlement            :mod:`dsr.workspace_roles` ``PLAN_RANK`` /
                            ``PLAN_GATE_QUOTE``
=========================  ==================================================

What is new here is the authoring surface and the per-view render: the seven
validated config fields, the five identity tokens, the nine anchors, and the
overlay a viewer sees on every page.

Storage is three open collections of arbitrary JSON - the watermark config, the
stamp a view produces, and nothing else. No migration, no typed column, no
required field. The config lives in its own namespaced collection keyed by the
WF-121 link id rather than being written onto the link row itself; the reason is
recorded in :mod:`.inferences` and was chosen by Jev
(``jev-20261013T231129-7936-89700``): WF-121's own ``update_link`` re-normalises
the link payload and would silently drop a key it does not know.

The watermark is a deterrent and a trace, not an access control. Nothing in this
package gates access to a document.
"""

from __future__ import annotations

from .book import WatermarkBook
from .errors import (
    ViewerWatermarkError,
    WatermarkLinkNotFound,
    WatermarkPlanError,
    WatermarkValidationError,
)
from .inferences import INFERENCES, describe as describe_inferences
from .rules import (
    DEFAULT_CONFIG,
    interpolate,
    normalise_config,
    plan_allows,
    render_pages,
    require_plan,
    tokens_used,
)
from .vocabulary import (
    COLLECTION,
    CONFIG_FIELD,
    ENABLE_FIELD,
    LINK_COLLECTION,
    POSITIONS,
    REQUIRED_FIELDS,
    ROOM_REF,
    ROTATIONS,
    STAMP_COLLECTION,
    TOKENS,
    VIEW_COLLECTION,
    describe as describe_vocabulary,
)

__all__ = [
    "COLLECTION",
    "CONFIG_FIELD",
    "DEFAULT_CONFIG",
    "ENABLE_FIELD",
    "INFERENCES",
    "LINK_COLLECTION",
    "POSITIONS",
    "REQUIRED_FIELDS",
    "ROOM_REF",
    "ROTATIONS",
    "STAMP_COLLECTION",
    "TOKENS",
    "VIEW_COLLECTION",
    "ViewerWatermarkError",
    "WatermarkBook",
    "WatermarkLinkNotFound",
    "WatermarkPlanError",
    "WatermarkValidationError",
    "describe_inferences",
    "describe_vocabulary",
    "interpolate",
    "normalise_config",
    "plan_allows",
    "render_pages",
    "require_plan",
    "tokens_used",
]
