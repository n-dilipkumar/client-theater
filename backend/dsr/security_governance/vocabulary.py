"""Every researched term WF-073 enforces against, with the evidence it came from.

This is the researched specification for WF-073 made executable, and it is the only
place a constant named after the specification lives. The specification is
``docs/research/digital-sales-room-workflows/wf/WF-073.md``, quoted in full in issue
176. Every value below is either quoted from that document or derived from a quote by
the arithmetic shown beside it. Nothing here is a house opinion.

The one sentence that governs the whole workflow
-------------------------------------------------

The specification's own criticality note reads: "Screenshot blocking is largely
unenforceable from a browser; treat as deterrence, and do not sell it as protection."
That is why there is no control in this module called ``protect``, why
:data:`EFFECT` says ``deterrent`` and not ``protection``, and why the two flags are
named ``enable_confidential_view`` and ``enable_screenshot_protection`` - those are
the vendor's field names, quoted from the OpenAPI document, and reproducing them is
what lets a caller tell a Papermark link from one of this product's without a lookup
table. The vendor named the second field ``protection``. This product does not adopt
the claim, only the name.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# Namespaced, because every feature shares one `records` table and `find()` matches
# on collection before it matches on anything else.

LINK_COLLECTION = "wf073_confidential_link"
PRESET_COLLECTION = "wf073_confidential_preset"
BAND_COLLECTION = "wf073_focus_band"
ATTEMPT_COLLECTION = "wf073_capture_attempt"

ALL_COLLECTIONS = (LINK_COLLECTION, PRESET_COLLECTION, BAND_COLLECTION, ATTEMPT_COLLECTION)

# --------------------------------------------------------------------------- #
# The two flags
# --------------------------------------------------------------------------- #
#
# The specification's data flow names the two fields on the Link row and gives their
# types: "Two booleans on the ``Link`` row." The OpenAPI evidence names them exactly:
# "`enable_screenshot_protection` (boolean, default `false`),
# `enable_confidential_view` (boolean, default `false`)".

CONFIDENTIAL_VIEW = "enable_confidential_view"
SCREENSHOT_PROTECTION = "enable_screenshot_protection"

#: The two flags, in the order the specification's user flow enables them.
FLAGS = (CONFIDENTIAL_VIEW, SCREENSHOT_PROTECTION)

# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #
#
# Decided by the source, not by this product. The OpenAPI `CreateLinkRequest`
# evidence quotes both: "enable_screenshot_protection (boolean, default false)" and
# "enable_confidential_view (boolean, default false)".

DEFAULT_CONFIDENTIAL_VIEW = False
DEFAULT_SCREENSHOT_PROTECTION = False

DEFAULTS = {
    CONFIDENTIAL_VIEW: DEFAULT_CONFIDENTIAL_VIEW,
    SCREENSHOT_PROTECTION: DEFAULT_SCREENSHOT_PROTECTION,
}

# --------------------------------------------------------------------------- #
# The link access-controls panel
# --------------------------------------------------------------------------- #
#
# The specification marks this surface as inferred, not sourced, and quotes itself:
# "Link access-controls panel (Confidential view, Screenshot protection)
# `[inferred from field set]`". The panel is therefore a derivation, recorded in
# dsr.security_governance.inferences as INFERRED_ACCESS_CONTROLS_PANEL, and this
# table is only the labels it renders. The vendor's two field names become two
# controls because two booleans with those names are what the evidence gives, and
# nothing about the panel's layout, order or placement was researched.

PANEL_TITLE = "Link access controls"

# --------------------------------------------------------------------------- #
# What each flag actually does, in the specification's own words
# --------------------------------------------------------------------------- #
#
# The CLI flag table is quoted in the specification's evidence verbatim, so these are
# the vendor's descriptions rather than this product's paraphrase of them. The panel
# below renders them rather than repeating them, so the page says what the vendor's
# documentation says and a later edit to one cannot leave the other stale.

CONFIDENTIAL_VIEW_DESCRIPTION = (
    "Reveal only a narrow band of each page at a time; rest is blurred (anti-screenshot)"
)
SCREENSHOT_PROTECTION_DESCRIPTION = (
    "Block common screenshot / screen-recording shortcuts while viewing"
)

DESCRIPTIONS = {
    CONFIDENTIAL_VIEW: CONFIDENTIAL_VIEW_DESCRIPTION,
    SCREENSHOT_PROTECTION: SCREENSHOT_PROTECTION_DESCRIPTION,
}

PANEL_CONTROLS = (
    {
        "field": CONFIDENTIAL_VIEW,
        "label": "Confidential view",
        "default": DEFAULT_CONFIDENTIAL_VIEW,
        "cli_flag": "--confidential-view",
        "effect": "reveal_band",
        "summary": CONFIDENTIAL_VIEW_DESCRIPTION,
    },
    {
        "field": SCREENSHOT_PROTECTION,
        "label": "Screenshot protection",
        "default": DEFAULT_SCREENSHOT_PROTECTION,
        "cli_flag": "--screenshot-protection",
        "effect": "block_shortcuts",
        "summary": SCREENSHOT_PROTECTION_DESCRIPTION,
    },
)

PANEL_CONTROL_FIELDS = tuple(control["field"] for control in PANEL_CONTROLS)

# --------------------------------------------------------------------------- #
# What these controls are, and are not
# --------------------------------------------------------------------------- #
#
# The criticality note governs this table directly. The word used for both controls
# is `deterrent`, because that is what the specification says they are, and the
# `not` entries are the failure modes the specification's extensibility note warns
# about. A page that renders this table is describing the product accurately.

#: Both controls raise the cost of an ordinary capture. Neither prevents it.
EFFECT = "deterrent"

#: Rendered on the page beside the controls, and asserted by the tests so a later
#: edit cannot quietly upgrade the wording into a protection claim.
LIMITATION = (
    "Screenshot blocking is largely unenforceable from a browser. Both controls raise "
    "the cost of an ordinary capture. Neither one prevents it. A camera pointed at the "
    "screen is outside anything this page can govern."
)

NOT_PROTECTION = (
    "A determined capture is outside this product's reach. The page reports what the "
    "viewer did, not what the viewer was able to see."
)

# --------------------------------------------------------------------------- #
# Confidential view: the focus band
# --------------------------------------------------------------------------- #
#
# The specification names the shape twice and the numbers neither time. It says
# "only \"a narrow band of each page at a time\" renders sharp" and, in the user flow,
# "content only resolves inside the focus band, so a single screenshot cannot capture
# a whole page". "A narrow band" is the whole of the sourced specification, so the two
# numbers below are derived rather than quoted, and the derivation is recorded in
# dsr.security_governance.inferences as DERIVED_BAND_FRACTION and
# DERIVED_BAND_OVERLAP.
#
# The derivation, in short. A band has to be narrow enough that a screen's worth of
# page is never sharp at once, and it has to be tall enough that a reader can read a
# line of text without the line being split. The fraction is a fifth of the page's
# height, and the overlap is a third of the band, which keeps the sharp text
# continuous as the band moves rather than cutting a word in half at the edge.

#: Fraction of a page's height that one focus band covers. Derived, not sourced.
BAND_FRACTION = 0.2

#: Fraction of a band that overlaps its neighbour, so the sharp text stays continuous
#: as the band moves. Derived, not sourced.
BAND_OVERLAP = 1 / 3

#: A page shorter than this is delivered whole. Derived, not sourced, and recorded as
#: SMALL_PAGE_PIXELS in the inferences: a band narrower than the render target is not
#: a band, it is a rendering failure, so a short page is treated as having no
#: out-of-band region at all.
SMALL_PAGE_PIXELS = 200

#: The rendering shapes the specification names. "Delivery shape is named but not
#: chosen", so exactly one is built and the choice is recorded in the inferences as
#: DERIVED_DELIVERY_SHAPE. The two are the two halves of the extensibility quote:
#: "(a) a viewport-driven tile/shard renderer for confidential view and (b) a
#: `keydown`/`visibilitychange` capture guard plus a `blur` on out-of-viewport tiles
#: for screenshot protection".
RENDER_SHAPE_VIEWPORT_BANDS = "viewport_bands"
RENDER_SHAPE_TILE_SHARDS = "tile_shards"

RENDER_SHAPES = (RENDER_SHAPE_VIEWPORT_BANDS, RENDER_SHAPE_TILE_SHARDS)

#: The one this build uses. Recorded, not assumed: see
#: dsr.security_governance.inferences.DERIVED_DELIVERY_SHAPE.
CHOSEN_RENDER_SHAPE = RENDER_SHAPE_VIEWPORT_BANDS

# --------------------------------------------------------------------------- #
# Screenshot protection: the shortcuts to intercept
# --------------------------------------------------------------------------- #
#
# The specification says the control blocks "common screenshot / screen-recording
# shortcuts while viewing" and does not enumerate them. The list below is therefore
# the browser's own default bindings, taken from the keyboard events each OS
# browser sends, and it is recorded in the inferences as
# DERIVED_SHORTCUT_LIST. `print_screen` cannot be blocked from a web page at all,
# which is the concrete reason the criticality note says the control is deterrent
# rather than protective, and the reason this is in the list: the list is what the
# viewer is told is being watched, not a claim that every entry is stopped.

#: Print Screen. Included precisely because no web page can intercept it. A control
#: that listed only the blockable keys would understate what a buyer can do, and this
#: entry is what makes the honest scope visible.
PRINT_SCREEN = "print_screen"

#: Keys a page can intercept before the event reaches the screen capture path.
SHORTCUT_SHIFTS = ("shift", "ctrl", "alt", "meta")

SHORTCUT_KEYS = (
    {
        "keys": ("meta", "shift", "3"),
        "action": "capture_fullscreen",
        "name": "Command-Shift-3",
        "platform": "macos",
    },
    {
        "keys": ("meta", "shift", "4"),
        "action": "capture_selection",
        "name": "Command-Shift-4",
        "platform": "macos",
    },
    {
        "keys": ("meta", "shift", "5"),
        "action": "capture_toolbar",
        "name": "Command-Shift-5",
        "platform": "macos",
        # Command-Shift-5 is the macOS screenshot panel, and it is also how
        # screen recording starts there. This is the one entry that is both a
        # screenshot and a recording shortcut.
        "also_recording": True,
    },
    {
        "keys": ("ctrl", "shift", "s"),
        "action": "capture_selection",
        "name": "Control-Shift-S",
        "platform": "other",
    },
    {
        "keys": ("meta", "alt", "r"),
        "action": "start_recording",
        "name": "Command-Alt-R",
        "platform": "other",
    },
    {
        "keys": ("print_screen",),
        "action": "capture_fullscreen",
        "name": "Print Screen",
        "platform": "other",
        "blockable": False,
    },
)

#: Every action the list covers. Used by the served vocabulary and by the summary.
CAPTURE_ACTIONS = (
    "capture_fullscreen",
    "capture_selection",
    "capture_toolbar",
    "start_recording",
)

# --------------------------------------------------------------------------- #
# Capture attempts
# --------------------------------------------------------------------------- #
#
# The specification's data sources list "client browser input events" as the source
# for screenshot protection. So an attempt is something the viewer's browser tells
# this product that it saw, and it is recorded as a report rather than as a block:
# the distinction is the whole reason the criticality note says "deterrent". A page
# cannot prove what a buyer did off-screen, so nothing in this package claims to.

ATTEMPT_BLOCKED = "blocked"
ATTEMPT_REPORTED = "reported"

ATTEMPT_STATES = (ATTEMPT_BLOCKED, ATTEMPT_REPORTED)

# --------------------------------------------------------------------------- #
# Presets
# --------------------------------------------------------------------------- #
#
# The specification's extensibility section says: "Both flags are `preset_id`-coverable,
# so they can be governance baselines" and, in the automation section, that they "are
# inherited by every link created from a preset". The other sixteen covered fields
# belong to other workflows' domains and are listed in dsr.link_gating.rules; this
# package interprets only the two that are its own.

PRESET_COVERED_FIELDS = (CONFIDENTIAL_VIEW, SCREENSHOT_PROTECTION)

# --------------------------------------------------------------------------- #
# Served to the frontend
# --------------------------------------------------------------------------- #

#: The value every response this workflow produces carries, so a caller cannot read a
#: confidentiality control without also reading what it is worth.
EFFECT_FIELD = "effect"
EFFECT_VALUE = EFFECT
LIMITATION_FIELD = "limitation"
LIMITATION_VALUE = LIMITATION
