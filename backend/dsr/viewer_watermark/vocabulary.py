"""The vocabulary WF-071 accepts, and where each part of it came from.

This workflow restates a capability WF-121 already provisions, so the values and
the rules behind them are **imported, never re-declared**:

* the shareable link is WF-121's ``wf121_tracked_link`` row, and its record id is
  what a watermark config is keyed by;
* the viewer's per-page telemetry and the ``viewer`` identity are WF-121's, and
  :func:`~dsr.tracked_deal_link.vocabulary.require_viewer` is itself WF-069's
  ``normalize_email``;
* the link-open boundary is WF-121's re-export of WF-069's ``expiry_state``;
* plan entitlement is :mod:`dsr.workspace_roles`, because the researched gate
  ("``403 forbidden_plan_feature`` on create/update, while existing links keep
  working after a downgrade") is that workflow's rule quoted in full.

What is *not* reused is the authoring surface, because it is genuinely new: the
seven validated config fields, the five identity tokens, the nine anchor
positions and the five rotations the OpenAPI ``WatermarkConfig`` schema names.

Anything the research does not name the value of - the tiled default, the tier a
watermark requires, the text cap - is an inference, recorded in
:mod:`.inferences` and served beside the sourced half at ``/api/wf-071/inferences``.
"""

from __future__ import annotations

import re
from typing import Any

from dsr.tracked_deal_link import LINK_COLLECTION, ROOM_REF, VIEW_COLLECTION
from dsr.workspace_roles.vocabulary import PLAN_BUSINESS, PLAN_GATE_QUOTE, PLAN_RANK, PLANS

#: The collections this workflow owns. Namespaced, because every feature shares
#: one ``records`` table and ``find()`` matches on collection first.
COLLECTION = "wf071_watermark"
STAMP_COLLECTION = "wf071_watermark_stamp"

#: The two researched link fields. The name is the OpenAPI
#: ``CreateLinkRequest`` spelling, and WF-069 already accepts and carries both
#: through untouched on a gated link: "``enable_watermark``, ``watermark_config``".
ENABLE_FIELD = "enable_watermark"
CONFIG_FIELD = "watermark_config"

#: The payload key naming the WF-121 link a config belongs to. A payload key
#: rather than the record envelope, for the same reason WF-121 keeps the room in
#: ``data``: the dynamic index does not cover the envelope, so a config stored
#: only in the envelope could not be filtered by ``find()``.
LINK_REF = "link_id"

#: The nine anchor positions. Named as the OpenAPI enum names them, so a client
#: reads the same word the server validates against.
POSITIONS: tuple[str, ...] = (
    "top-left",
    "top-center",
    "top-right",
    "middle-left",
    "middle-center",
    "middle-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
)

#: The rotation is a ``const`` set, not a free angle: "rotation (const
#: 0/30/45/90/180)".
ROTATIONS: tuple[int, ...] = (0, 30, 45, 90, 180)

#: The five identity tokens, in the researched spelling. ``ipAddress`` is
#: camelCase in the token table and is kept verbatim, because a token is a
#: literal a rep types into the text.
TOKENS: tuple[str, ...] = ("email", "date", "time", "link", "ipAddress")

#: ``{{token}}``. Whitespace inside the braces is tolerated, because a rep typing
#: by hand adds it.
TOKEN_RE = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9_]*)\s*\}\}")

#: What an identity token resolves to when the view carries no value for it. The
#: alternative - refusing the view - would break the researched anonymous link,
#: and a silently blanked token would erase the trace the watermark exists to
#: carry. The response names every substituted token in ``unresolved_tokens``.
PLACEHOLDERS: dict[str, str] = {
    "email": "anonymous",
    "ipAddress": "unknown",
    "link": "link not set",
    "date": "unknown date",
    "time": "unknown time",
}

#: The seven fields the OpenAPI schema marks required, in schema order. A config
#: always carries all seven after :func:`~.rules.normalise_config`, because a
#: missing one is filled from the documented default rather than stored absent.
REQUIRED_FIELDS: tuple[str, ...] = (
    "text",
    "is_tiled",
    "position",
    "rotation",
    "color",
    "font_size",
    "opacity",
)

#: ``^#([0-9A-Fa-f]{3}){1,2}$``, quoted from the schema. Three or six hex digits.
COLOR_RE = re.compile(r"^#([0-9A-Fa-f]{3}){1,2}$")

#: The researched bounds. ``font_size`` 1-96 and ``opacity`` 0-1 are the schema's;
#: the text cap is this build's and is recorded in :mod:`.inferences`.
FONT_SIZE_MIN = 1
FONT_SIZE_MAX = 96
OPACITY_MIN = 0.0
OPACITY_MAX = 1.0
TEXT_MIN_CHARS = 1
TEXT_MAX_CHARS = 200

#: The documented defaults: "``Confidential`` at ``middle-center``, 45°, black,
#: 24pt, 0.5 opacity". ``is_tiled`` is not among them; see :mod:`.inferences`.
DEFAULT_TEXT = "Confidential"
DEFAULT_TILED = False
DEFAULT_POSITION = "middle-center"
DEFAULT_ROTATION = 45
DEFAULT_COLOR = "#000000"
DEFAULT_FONT_SIZE = 24
DEFAULT_OPACITY = 0.5

#: The tier a watermark requires. The researched gate names the response and not
#: the tier, so this reuses ``claims``-grade vocabulary: the same ``business``
#: tier ``tokens`` requires, and the quote is WF-077's own.
PLAN_FEATURE = "watermark"
PLAN_REQUIRED = PLAN_BUSINESS


def default_config() -> dict[str, Any]:
    """The documented defaults as one config, always all seven fields."""
    return {
        "text": DEFAULT_TEXT,
        "is_tiled": DEFAULT_TILED,
        "position": DEFAULT_POSITION,
        "rotation": DEFAULT_ROTATION,
        "color": DEFAULT_COLOR,
        "font_size": DEFAULT_FONT_SIZE,
        "opacity": DEFAULT_OPACITY,
    }


def describe() -> dict[str, Any]:
    """The whole accepted vocabulary, as one payload.

    Served at ``GET /api/wf-071/vocabulary`` so the panel renders its pickers
    from the same source the rules enforce against. The values shared with WF-121
    and WF-077 are labelled with their workflow, so a reader can see the link and
    the plan gate have exactly one definition.
    """
    return {
        "ticket": "WF-071",
        "positions": list(POSITIONS),
        "rotations": list(ROTATIONS),
        "tokens": list(TOKENS),
        "token_syntax": "{{token}}",
        "required_fields": list(REQUIRED_FIELDS),
        "limits": {
            "font_size_min": FONT_SIZE_MIN,
            "font_size_max": FONT_SIZE_MAX,
            "opacity_min": OPACITY_MIN,
            "opacity_max": OPACITY_MAX,
            "text_min_chars": TEXT_MIN_CHARS,
            "text_max_chars": TEXT_MAX_CHARS,
        },
        "color_pattern": COLOR_RE.pattern,
        "defaults": default_config(),
        "placeholders": dict(PLACEHOLDERS),
        "plan": {
            "feature": PLAN_FEATURE,
            "requires": PLAN_REQUIRED,
            "plans": list(PLANS),
            "rank": dict(PLAN_RANK),
            "rule": PLAN_GATE_QUOTE,
        },
        "reused": {
            "link_collection": LINK_COLLECTION,
            "link_collection_source": "WF-121 dsr.tracked_deal_link.LINK_COLLECTION",
            "view_collection": VIEW_COLLECTION,
            "view_collection_source": "WF-121 dsr.tracked_deal_link.VIEW_COLLECTION",
            "room_ref": ROOM_REF,
            "viewer_source": "WF-121 dsr.tracked_deal_link.vocabulary.require_viewer (WF-069 normalize_email)",
            "open_rule_source": "WF-121 dsr.tracked_deal_link.rules.require_open (WF-069 expiry_state)",
            "plan_gate_source": "WF-077 dsr.workspace_roles.vocabulary.PLAN_RANK",
        },
        "collections": {"configs": COLLECTION, "stamps": STAMP_COLLECTION},
    }


__all__ = [
    "COLLECTION",
    "COLOR_RE",
    "CONFIG_FIELD",
    "DEFAULT_COLOR",
    "DEFAULT_FONT_SIZE",
    "DEFAULT_OPACITY",
    "DEFAULT_POSITION",
    "DEFAULT_ROTATION",
    "DEFAULT_TEXT",
    "DEFAULT_TILED",
    "ENABLE_FIELD",
    "FONT_SIZE_MAX",
    "FONT_SIZE_MIN",
    "LINK_COLLECTION",
    "LINK_REF",
    "OPACITY_MAX",
    "OPACITY_MIN",
    "PLACEHOLDERS",
    "PLAN_FEATURE",
    "PLAN_REQUIRED",
    "POSITIONS",
    "REQUIRED_FIELDS",
    "ROOM_REF",
    "ROTATIONS",
    "STAMP_COLLECTION",
    "TEXT_MAX_CHARS",
    "TEXT_MIN_CHARS",
    "TOKENS",
    "TOKEN_RE",
    "VIEW_COLLECTION",
    "default_config",
    "describe",
]
