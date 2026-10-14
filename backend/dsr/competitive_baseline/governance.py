"""WF-137: governance rules for versioning, brand control and the closed-won handoff.

This module is the whole domain of one workflow: a room that a governed template
built, every publish of it snapshotted, the brand rules applied to it, and the
room repurposed at closed-won into a phased onboarding portal. It imports the
store and nothing else from this project. It opens no database connection, it
names no route and it imports no framework, so every rule below is checkable at
the instant it is stated.

The researched specification is
``docs/research/digital-sales-room-workflows/wf/WF-137.md``, section 17 of
``docs/research/raw/competitive-baseline.md``. Three capabilities are built,
because those are the three the brief names and one agent can ship them:

* **Versioning.** Every publish is snapshotted into an immutable version. A
  restore writes a new version and never edits an old one, so the history reads
  forward and a bad edit can be previewed, restored or branched from.
* **Brand governance.** One brand token set per organisation holds the tokens a
  room may carry. Applying it to a room writes the approved tokens and reports
  every finding, so an off-brand room is a named finding rather than a guess.
* **Closed-won continuity.** At closed-won the room is repurposed, not rebuilt:
  the same record keeps its id, and a phased onboarding plan is instantiated on
  it. The buyer keeps one link for the whole relationship.

The research left several decisions open. Each one is decided in
:data:`DECISIONS` and :data:`INFERENCES` and is served by the vocabulary and
inferences routes, so a reviewer reads the decision rather than the source.

A write is verified before it reports success. Every write takes a ``source``
string from its caller and the feature module builds that string from its own
router, so an audit row names a route the app actually serves.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Errors. One handler on the base type covers the hierarchy, so the feature
# module registers one handler and the host cannot refuse it as a duplicate.
#
# None of the three is a Python builtin on its own. ``GovernanceRefused`` also
# subclasses ``PermissionError`` and ``GovernanceNotFound`` also subclasses
# ``LookupError``, which is the convention the sibling errors module records: the
# semantic builtin is inherited, and the handler is registered by this module's
# own name so it can never intercept an unrelated error elsewhere in the product.
# --------------------------------------------------------------------------- #


class GovernanceError(ValueError):
    """The base type of every refusal this workflow makes.

    Carries a field-keyed map, because a seller filling in a form needs the
    message beside the input that caused it. The HTTP layer renders the map as
    ``errors``.
    """

    #: The HTTP status every refusal of this type answers with.
    status_code = 422

    #: The stable machine token the page branches on.
    code = "invalid_governance"

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})

    def payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": self.code, "detail": str(self)}
        if self.errors:
            body["errors"] = self.errors
        return body


class GovernanceNotFound(GovernanceError, LookupError):
    """No such room, brand set, snapshot, plan, phase or retention policy."""

    status_code = 404
    code = "governance_not_found"


class GovernanceRefused(PermissionError, GovernanceError):
    """A change the room's current state will not accept.

    ``reason`` is a stable machine token, so the page can branch on it instead
    of parsing an English sentence. The three kinds of refusal this workflow
    makes are named by the reasons below: an archived room accepts no write, a
    repurposed room is not repurposed twice, and a phase with an open milestone
    does not advance.
    """

    status_code = 409
    code = "governance_refused"

    #: The room was archived or deleted, so it accepts no further write.
    REASON_LIFECYCLE_CLOSED = "lifecycle_closed"

    #: The room is already an onboarding portal. It is repurposed once.
    REASON_ALREADY_REPURPOSED = "already_repurposed"

    #: The room has no onboarding plan yet, so there is no phase to move.
    REASON_NOT_REPURPOSED = "not_repurposed"

    #: Every phase of the plan is complete, so there is no next phase.
    REASON_PHASES_COMPLETE = "phases_complete"

    #: The active phase holds an open milestone, so it cannot advance.
    REASON_MILESTONES_OPEN = "milestones_open"

    #: A write was made but did not read back. The room must not report success.
    REASON_WRITE_NOT_VISIBLE = "write_not_visible"

    #: The retention record is not in the state the requested change needs.
    REASON_RETENTION_STATE = "retention_state"

    _MESSAGES = {
        REASON_LIFECYCLE_CLOSED: "This room was archived or deleted. It accepts no write.",
        REASON_ALREADY_REPURPOSED: "This room is already an onboarding portal.",
        REASON_NOT_REPURPOSED: "This room has no onboarding plan yet.",
        REASON_PHASES_COMPLETE: "Every phase of this plan is complete.",
        REASON_MILESTONES_OPEN: "The active phase holds an open milestone.",
        REASON_RETENTION_STATE: "The retention record is not in the state that change needs.",
        REASON_WRITE_NOT_VISIBLE: "The change was written but did not read back.",
    }

    def __init__(self, reason: str, detail: str | None = None) -> None:
        super().__init__(detail or self._MESSAGES.get(reason, "That change was refused."))
        self.reason = reason

    def payload(self) -> dict[str, Any]:
        # Built here rather than through ``GovernanceError.payload``: this type
        # subclasses ``PermissionError`` first, so its own ``__init__`` runs and
        # the base error's ``errors`` map is never populated.
        return {
            "error": self.code,
            "detail": str(self),
            "reason": self.reason,
        }


# --------------------------------------------------------------------------- #
# Collections. One shared `records` table carries every feature, so each name is
# namespaced per ticket and `find()` matches on the collection before anything
# else. Every payload here is ordinary JSON in `data`: no migration, no typed
# column, and a team adding a field needs no coordination with anyone.
# --------------------------------------------------------------------------- #

#: The governed brand token set. There is one per organisation in this build.
BRAND_SET_COLLECTION = "wf137_brand_set"

#: One governance row per room: its lifecycle state, applied brand and head.
ROOM_STATE_COLLECTION = "wf137_room"

#: One immutable published version of a room. Written once, never updated.
SNAPSHOT_COLLECTION = "wf137_snapshot"

#: One onboarding plan template: phases with their milestones.
PLAN_COLLECTION = "wf137_plan"

#: One phase of a plan, instantiated on one room at closed-won.
PHASE_COLLECTION = "wf137_phase"

#: One retention policy per room: the windows, the clock start and the state.
RETENTION_COLLECTION = "wf137_retention"

#: The payload-side twin of the envelope's `room_id`. `room_id` is stripped from
#: `data` before the dynamic index is built, so a payload that stored its room
#: there would be unfilterable by `find()`.
ROOM_REF = "room_ref"

#: Keys the store owns. They are stripped from a payload before it is written, so
#: a request that sets them changes nothing.
RESERVED_KEYS = frozenset(
    {"id", "collection", "room_id", "revision", "created_at", "updated_at", "deleted_at"}
)

# --------------------------------------------------------------------------- #
# The room lifecycle, and the retention axis beside it.
#
# The two are separate on purpose. The lifecycle is what the room *is*: a sales
# room, an onboarding portal, a finished one. The retention axis is what happens
# to its rows over time. A room can be `onboarding` and `archived` at once, and
# collapsing the two into one field would lose that.
# --------------------------------------------------------------------------- #

#: The room is still selling. This is where every room starts.
LIFECYCLE_SALES = "sales"

#: The deal closed and the room became a phased onboarding portal.
LIFECYCLE_ONBOARDING = "onboarding"

#: Every phase of the onboarding plan is complete.
LIFECYCLE_ONBOARDED = "onboarded"

#: The retention schedule archived the room. It accepts no write.
LIFECYCLE_ARCHIVED = "archived"

#: The retention schedule deleted the room's governance rows.
LIFECYCLE_DELETED = "deleted"

#: Every lifecycle state, in the order a room moves through them.
LIFECYCLE_STATES = (
    LIFECYCLE_SALES,
    LIFECYCLE_ONBOARDING,
    LIFECYCLE_ONBOARDED,
    LIFECYCLE_ARCHIVED,
    LIFECYCLE_DELETED,
)

#: The lifecycle states that accept no write at all.
CLOSED_LIFECYCLE_STATES = (LIFECYCLE_ARCHIVED, LIFECYCLE_DELETED)

#: The retention states, in order.
RETENTION_LIVE = "live"
RETENTION_ARCHIVED = "archived"
RETENTION_DELETED = "deleted"
RETENTION_STATES = (RETENTION_LIVE, RETENTION_ARCHIVED, RETENTION_DELETED)

# --------------------------------------------------------------------------- #
# Snapshots: the triggers and the immutability rule.
# --------------------------------------------------------------------------- #

#: A publish. The spec's automation names this one: "auto-snapshot on every
#: publish".
TRIGGER_PUBLISH = "publish"

#: An editing session checkpoint. Walnut's KB names the second trigger: "Every
#: publish and editing session is snapshotted".
TRIGGER_SESSION = "session"

#: A restore, which writes the restored state as a new version.
TRIGGER_RESTORE = "restore"

#: A branch, which seeds a new room from an earlier version.
TRIGGER_BRANCH = "branch"

#: Every trigger, in the order they appear in the vocabulary route.
SNAPSHOT_TRIGGERS = (TRIGGER_PUBLISH, TRIGGER_SESSION, TRIGGER_RESTORE, TRIGGER_BRANCH)

#: The first version number of any history line.
FIRST_VERSION = 1

# --------------------------------------------------------------------------- #
# Phases and milestones.
# --------------------------------------------------------------------------- #

#: A phase that has not started.
PHASE_PENDING = "pending"

#: The one phase a room is working through.
PHASE_ACTIVE = "active"

#: A phase that finished.
PHASE_COMPLETE = "complete"
PHASE_STATES = (PHASE_PENDING, PHASE_ACTIVE, PHASE_COMPLETE)

#: A milestone that is not done yet.
MILESTONE_PENDING = "pending"

#: A milestone that is done.
MILESTONE_COMPLETE = "complete"
MILESTONE_STATES = (MILESTONE_PENDING, MILESTONE_COMPLETE)

# --------------------------------------------------------------------------- #
# The brand token set.
#
# The token names are the room branding fields the product already carries:
# `theme`, `accent` and `logo` come from the core room record, and `font` is the
# display face the design system names. The approved values of the house set are
# read from `docs/DESIGN-SYSTEM.md`, which is the in-repo authority: one accent,
# light theme only, Schibsted Grotesk for display. The research names a
# "brand-token service" with no schema, so the shape is derived and recorded.
# --------------------------------------------------------------------------- #

#: The tokens a room may carry. A room with any of these off-brand is a finding.
BRAND_TOKENS = ("theme", "accent", "logo", "font")

#: The one accent in the product.
HOUSE_ACCENT = "#10506f"

#: The one theme in the product.
HOUSE_THEME = "light"

#: The display face the design system names.
HOUSE_FONT = "Schibsted Grotesk"

#: The house brand set, used until a team writes its own.
DEFAULT_BRAND_SET: dict[str, Any] = {
    "name": "Client Theater house brand",
    "theme": HOUSE_THEME,
    "accent": HOUSE_ACCENT,
    "logo": "client-theater.svg",
    "font": HOUSE_FONT,
    "required_tokens": list(BRAND_TOKENS),
    "allowed_values": {
        "theme": [HOUSE_THEME],
        "accent": [HOUSE_ACCENT],
        "font": [HOUSE_FONT],
    },
    "owner": "enablement",
}

#: The finding codes the brand check reports.
FINDING_MISSING_TOKEN = "missing_token"
FINDING_UNAPPROVED_VALUE = "unapproved_value"
FINDING_UNAPPROVED_THEME = "unapproved_theme"
FINDING_UNAPPROVED_FONT = "unapproved_font"
BRAND_FINDING_CODES = (
    FINDING_MISSING_TOKEN,
    FINDING_UNAPPROVED_VALUE,
    FINDING_UNAPPROVED_THEME,
    FINDING_UNAPPROVED_FONT,
)

# --------------------------------------------------------------------------- #
# The derived retention windows.
#
# The spec names "the compliance/retention policy" as a data source and
# "auto-archive and delete on the retention schedule" as an automation. It gives
# no period, no legal basis and no deletion test. Both windows below are house
# rules derived for this build and recorded as inferences, with the alternative
# rejected and the constant to change. A room's own policy can override either
# one, and the override is ordinary JSON in `records.data`.
# --------------------------------------------------------------------------- #

#: Days after the lifecycle ends before a room is archived.
ARCHIVE_AFTER_DAYS = 180

#: Days after the archive before a room's governance rows are deleted.
DELETE_AFTER_DAYS = 365

#: The widest window a policy may name, in days. Ten years.
MAX_WINDOW_DAYS = 3650

# --------------------------------------------------------------------------- #
# The default onboarding plan.
#
# Dock's sitemap confirms the post-sale surface is a first-class product line and
# names `/templates/customer-onboarding-plan`, `/templates/mutual-action-plan`
# and `/templates/implementation-plan`. It gives no phase list, so the four
# phases below are derived from the plan names the sitemap does carry and are
# recorded as an inference. A team writes its own plan through the plan route.
# --------------------------------------------------------------------------- #

#: The default plan: four phases, each with the milestones a handoff needs.
DEFAULT_PLAN: dict[str, Any] = {
    "key": "house-onboarding",
    "name": "House onboarding plan",
    "description": (
        "The phased handoff a closed-won room becomes. Four phases, each with the "
        "milestones that close it."
    ),
    "phases": [
        {
            "key": "kickoff",
            "name": "Kickoff",
            "milestones": [
                {"key": "introductions", "name": "Introductions and access"},
                {"key": "success_criteria", "name": "Success criteria agreed"},
            ],
        },
        {
            "key": "implementation",
            "name": "Implementation",
            "milestones": [
                {"key": "plan_signed", "name": "Implementation plan signed"},
                {"key": "access_provisioned", "name": "Buyer access provisioned"},
            ],
        },
        {
            "key": "enablement",
            "name": "Enablement",
            "milestones": [
                {"key": "training_delivered", "name": "Training content delivered"},
                {"key": "admin_handover", "name": "Admin handover complete"},
            ],
        },
        {
            "key": "steady_state",
            "name": "Steady state",
            "milestones": [
                {"key": "value_review", "name": "First value review held"},
                {"key": "owner_named", "name": "Ongoing owner named"},
            ],
        },
    ],
}

# --------------------------------------------------------------------------- #
# The decisions the research left open, each with the alternative rejected.
# --------------------------------------------------------------------------- #

DECISIONS: dict[str, str] = {
    "snapshot_triggers": (
        "Two triggers snapshot a room, because the evidence names two: Walnut's "
        "KB says every publish and editing session is snapshotted. Publish is "
        "automatic through the publish route, and an editing session is a "
        "checkpoint a seller takes on purpose. A third and fourth trigger exist "
        "for a restore and a branch, both of which write a new version rather "
        "than edit an old one."
    ),
    "immutability": (
        "A snapshot is written once and never updated. There is no route that "
        "patches a snapshot. A restore writes the restored state as the next "
        "version and records which version it came from, so the history reads "
        "forward and the past stays as it was published."
    ),
    "branch_target": (
        "A branch starts a new room record rather than a second history line on "
        "the same room. The research describes branching from an earlier state "
        "to take the work somewhere else, and a new record keeps the two lines "
        " separable in every read."
    ),
    "brand_tokens": (
        "The token shape is derived: theme, accent, logo and font, the fields "
        "the product already carries. The research names a brand-token service "
        "and gives no schema, so the shape is this build's own and the brand "
        "route serves it."
    ),
    "brand_values": (
        "The approved values of the house set are read from "
        "docs/DESIGN-SYSTEM.md: one accent, light theme only, Schibsted Grotesk "
        "for display. That file is the in-repo authority for the house brand, so "
        "the brand check enforces it rather than inventing a palette."
    ),
    "brand_scope": (
        "A brand check reads a room's own branding fields and reports findings. "
        "Applying the brand writes the approved tokens onto the room. The check "
        "covers no messaging and no content wording, because messaging control "
        "is a content-library concern and this build owns the token set."
    ),
    "synced_sections": (
        "A live push into every live room is not built. The refresh is a "
        "read-through: a room carries the brand-set version it was applied "
        "against, the brand route reports a room whose version is behind, and "
        "applying the brand again brings it current. The alternative rejected is "
        "Dock's Synced Sections, which pushes a change into every active deal at "
        "once and would let one edit rewrite rooms that other sellers are "
        "editing."
    ),
    "repurpose": (
        "The room is repurposed, not rebuilt. The same record keeps its id and "
        "its published history, and a phased onboarding plan is instantiated on "
        "it. The buyer keeps one link for the whole relationship, which Mindtickle "
        "lists as a requirement rather than a nicety."
    ),
    "phase_milestones": (
        "A phase completes only through the advance route, and the advance is "
        "refused while the phase holds an open milestone. The alternative "
        "rejected is auto-completing a phase when its last milestone closes, "
        "which would hide a milestone that closed for the wrong reason."
    ),
    "retention_windows": (
        "Both windows are house rules, because the research gives no period, no "
        "legal basis and no deletion test. Archive 180 days after the lifecycle "
        "ends, delete 365 days after the archive. A room's own policy can "
        "override either window, and the override is stored as ordinary JSON."
    ),
    "retention_deletion": (
        "Deletion is a soft delete of the room's governance rows, and the "
        "retention row stays as the tombstone. A hard delete would remove the "
        "audit trail that describes what was deleted, and that trail is the "
        "guarantee the product is built on."
    ),
    "lifecycle_end": (
        "The retention clock starts when the lifecycle ends: the day onboarding "
        "completed, or the close date when the room never completed onboarding. "
        "A room that is still onboarding is never archive-due, because its "
        "lifecycle has not ended."
    ),
}

#: What this build deliberately does not do, named so a reviewer does not have
#: to guess whether a gap is a decision or an oversight.
NOT_IMPLEMENTED: tuple[str, ...] = (
    "A live push of refreshed content into every live room. The refresh is a read-through.",
    "An LMS embed. Mindtickle, Highspot and Allego are named as integrations and the spec does not ask for the embed.",
    "An e-signature step for the plan. The spec names it as an API the flow touches, not a surface to build.",
    "A content library or DAM connection. This workflow owns the brand token set, not the assets.",
    "A CRM close event. The close arrives through this workflow's own close route, because this repository owns no CRM connection.",
    "Messaging control. The brand check enforces tokens, not wording.",
)


def vocabulary() -> dict[str, Any]:
    """The vocabulary this workflow enforces, and the decisions behind it.

    Served by the vocabulary route so the page reads its states from here rather
    than hard-coding them, and so a reviewer can read each decision without
    opening the source.
    """
    return {
        "ticket": "WF-137",
        "collections": {
            "brand_set": BRAND_SET_COLLECTION,
            "room_state": ROOM_STATE_COLLECTION,
            "snapshot": SNAPSHOT_COLLECTION,
            "plan": PLAN_COLLECTION,
            "phase": PHASE_COLLECTION,
            "retention": RETENTION_COLLECTION,
        },
        "lifecycle_states": list(LIFECYCLE_STATES),
        "retention_states": list(RETENTION_STATES),
        "snapshot_triggers": list(SNAPSHOT_TRIGGERS),
        "phase_states": list(PHASE_STATES),
        "milestone_states": list(MILESTONE_STATES),
        "brand_tokens": list(BRAND_TOKENS),
        "brand_finding_codes": list(BRAND_FINDING_CODES),
        "defaults": {
            "brand_set": dict(DEFAULT_BRAND_SET),
            "archive_after_days": ARCHIVE_AFTER_DAYS,
            "delete_after_days": DELETE_AFTER_DAYS,
            "first_version": FIRST_VERSION,
        },
        "decisions": dict(DECISIONS),
        "not_implemented": list(NOT_IMPLEMENTED),
    }


#: The open decisions, each with the change that would alter it.
INFERENCES: tuple[dict[str, str], ...] = (
    {
        "id": "retention_windows",
        "decision": (
            "Archive a room 180 days after its lifecycle ends, and delete its "
            "governance rows 365 days after the archive."
        ),
        "why": (
            "The research names a retention policy as a data source and an "
            "auto-archive-and-delete automation, and gives no period, no legal "
            "basis and no deletion test. Both windows are a house rule."
        ),
        "change_it_by": (
            "Change ARCHIVE_AFTER_DAYS or DELETE_AFTER_DAYS, or set a room's own "
            "windows through the retention route."
        ),
    },
    {
        "id": "snapshot_triggers",
        "decision": (
            "A snapshot is written on a publish (automatic), on an editing "
            "session (a checkpoint a seller takes), on a restore and on a branch."
        ),
        "why": (
            "The evidence names two triggers, publish and editing session, and "
            "they are not the same event. Restore and branch are recorded as "
            "their own triggers so a history line explains itself."
        ),
        "change_it_by": (
            "Add a trigger to SNAPSHOT_TRIGGERS and pass it to the snapshot "
            "call. The publish route keeps the automatic one."
        ),
    },
    {
        "id": "brand_token_shape",
        "decision": (
            "The token set carries theme, accent, logo and font, with an "
            "allowed-value list per token and a required-token list."
        ),
        "why": (
            "The research names a brand-token service and gives no schema. The "
            "four tokens are the branding fields the product already carries."
        ),
        "change_it_by": "Write a brand set with its own tokens through the brand-set route.",
    },
    {
        "id": "synced_sections",
        "decision": (
            "The refresh is a read-through, not a live push. A room reports a "
            "stale brand-set version, and applying the brand brings it current."
        ),
        "why": (
            "A live push would let one edit rewrite rooms that other sellers are "
            "editing. The read-through keeps every write attributable to the "
            "seller who asked for it."
        ),
        "change_it_by": (
            "Add a push over the rooms a brand-set read returns. It is not built "
            "and is recorded here rather than hidden."
        ),
    },
    {
        "id": "default_plan",
        "decision": (
            "The default onboarding plan has four phases: kickoff, "
            "implementation, enablement and steady state."
        ),
        "why": (
            "Dock's sitemap confirms the post-sale surface and names onboarding "
            "plan, implementation plan and mutual action plan templates. It gives "
            "no phase list, so the four phases are derived from those names."
        ),
        "change_it_by": "Write a plan with its own phases through the plan route.",
    },
)


def inferences() -> dict[str, Any]:
    """Every decision the research does not make, and how to change each one."""
    return {"count": len(INFERENCES), "inferences": [dict(row) for row in INFERENCES]}


# --------------------------------------------------------------------------- #
# Coercion helpers. Pure functions of their arguments: no clock, no store, no
# framework.
# --------------------------------------------------------------------------- #


def data_of(record: Mapping[str, Any] | None) -> dict[str, Any]:
    """The payload of a record, as a fresh mutable dict."""
    return dict((record or {}).get("data") or {})


def room_of(record: Mapping[str, Any]) -> Any:
    """The room a record belongs to, from the envelope or the payload."""
    return record.get("room_id") or data_of(record).get(ROOM_REF)


def read_int(value: Any, default: int | None = None) -> int | None:
    """Read a whole number, or ``default`` when the value is not one.

    A JSON body carries numbers as numbers, but a version or a window can arrive
    as a numeric string. A bool is refused: ``True`` is not a version.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def as_datetime(value: Any) -> datetime | None:
    """Read an ISO date or timestamp as an aware datetime, or ``None``.

    Returns ``None`` rather than raising for a malformed value, because one
    unreadable close date on one room must not stop a room from rendering.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_instant(value: Any, *, field: str = "at") -> datetime:
    """Read an instant, or raise a validation error naming the field.

    Unlike :func:`as_datetime` this raises, because a write that carries an
    unreadable timestamp must not be stored as though it were readable.
    """
    parsed = as_datetime(value)
    if parsed is None:
        raise GovernanceError(
            "That value is not a readable timestamp.",
            {field: "Give a timestamp in ISO form, for example 2026-08-30T00:00:00Z."},
        )
    return parsed


def is_true(value: Any) -> bool:
    """Read a stored flag. A JSON body carries booleans or their words."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes", "on"}


def coerce_days(value: Any, field: str) -> int:
    """Read a window in whole days, inside the bounds this workflow accepts."""
    days = read_int(value, None)
    if days is None or days < 1 or days > MAX_WINDOW_DAYS:
        raise GovernanceError(
            "That window could not be read.",
            {
                field: (
                    f"Give a whole number of days between 1 and {MAX_WINDOW_DAYS}. "
                    f"{value!r} was given."
                )
            },
        )
    return days


def token(value: Any) -> str:
    """A stripped string. ``None`` becomes the empty string."""
    return str(value if value is not None else "").strip()


# --------------------------------------------------------------------------- #
# Validation of the payloads this workflow accepts.
# --------------------------------------------------------------------------- #


def normalise_brand_set(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Read a brand token set, or raise a validation error naming the field.

    The defaults fill the gaps, so a write that names only the accent keeps the
    rest of the house set. An unknown token is refused rather than stored,
    because a token the check does not read is a token that never gets checked.
    """
    row = dict(payload or {})
    name = token(row.get("name")) or str(DEFAULT_BRAND_SET["name"])
    errors: dict[str, str] = {}

    values = row.get("allowed_values") or {}
    if not isinstance(values, Mapping):
        raise GovernanceError(
            "The allowed values could not be read.",
            {"allowed_values": "Give an object keyed by token name."},
        )
    allowed: dict[str, list[str]] = {}
    for key in BRAND_TOKENS:
        listed = values.get(key)
        if listed is None:
            continue
        if isinstance(listed, str):
            listed = [listed]
        if not isinstance(listed, (list, tuple)) or not listed:
            errors[key] = f"Give a list of approved {key} values."
            continue
        cleaned = [token(item) for item in listed if token(item)]
        if not cleaned:
            errors[key] = f"Give a list of approved {key} values."
            continue
        allowed[key] = cleaned

    required = row.get("required_tokens")
    if required is None:
        required_tokens = list(BRAND_TOKENS)
    elif isinstance(required, str):
        required_tokens = [part.strip() for part in required.split(",") if part.strip()]
    elif isinstance(required, (list, tuple)):
        required_tokens = [token(item) for item in required]
    else:
        errors["required_tokens"] = "Give a list of token names."
        required_tokens = list(BRAND_TOKENS)
    unknown = [item for item in required_tokens if item not in BRAND_TOKENS]
    if unknown:
        errors["required_tokens"] = f"Unknown token name(s): {', '.join(sorted(unknown))}."

    if errors:
        raise GovernanceError("The brand set could not be read.", errors)

    # A token the caller did not name keeps its approved house value, so a set
    # that overrides the accent still carries the theme the product ships with.
    for key in BRAND_TOKENS:
        if key not in allowed:
            allowed[key] = [token(DEFAULT_BRAND_SET.get(key))]

    return {
        "name": name,
        "owner": token(row.get("owner")) or str(DEFAULT_BRAND_SET["owner"]),
        "required_tokens": required_tokens,
        "allowed_values": allowed,
        "tokens": {key: allowed[key][0] for key in BRAND_TOKENS},
    }


def normalise_snapshot(payload: Mapping[str, Any] | None, *, now: datetime) -> dict[str, Any]:
    """Read a publish or an editing-session checkpoint.

    The label and the summary are the words a seller reads in the history. The
    content is arbitrary JSON, because a room's body is a team's own shape and
    this product stores records rather than files.
    """
    row = dict(payload or {})
    content = row.get("content")
    if content is None:
        content = {}
    if not isinstance(content, (Mapping, list, str)):
        raise GovernanceError(
            "The content could not be read.",
            {"content": "Give an object, a list or a string."},
        )
    at = parse_instant(row.get("at"), field="at") if row.get("at") else now
    return {
        "label": token(row.get("label")),
        "summary": token(row.get("summary")),
        "content": dict(content) if isinstance(content, Mapping) else content,
        "sections": [str(item) for item in (row.get("sections") or [])],
        "at": at,
    }


def normalise_plan(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Read an onboarding plan: ordered phases, each with its milestones."""
    row = dict(payload or {})
    key = token(row.get("key"))
    name = token(row.get("name"))
    errors: dict[str, str] = {}
    if not key:
        errors["key"] = "Give the plan a key."
    if not name:
        errors["name"] = "Give the plan a name."

    raw_phases = row.get("phases")
    if not isinstance(raw_phases, (list, tuple)) or not raw_phases:
        errors["phases"] = "Give at least one phase."
        raw_phases = []

    phases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, raw in enumerate(raw_phases):
        if not isinstance(raw, Mapping):
            errors["phases"] = "Every phase is an object."
            continue
        phase_key = token(raw.get("key"))
        phase_name = token(raw.get("name"))
        if not phase_key:
            errors["phases"] = f"Phase {position + 1} has no key."
            continue
        if phase_key in seen:
            errors["phases"] = f"Phase key {phase_key!r} is used twice."
            continue
        seen.add(phase_key)
        milestones: list[dict[str, Any]] = []
        milestone_seen: set[str] = set()
        raw_milestones = raw.get("milestones") or []
        if not isinstance(raw_milestones, (list, tuple)):
            errors["milestones"] = f"Phase {phase_key!r} needs a list of milestones."
            raw_milestones = []
        for raw_milestone in raw_milestones:
            if not isinstance(raw_milestone, Mapping):
                errors["milestones"] = f"Phase {phase_key!r} has a milestone that is not an object."
                continue
            milestone_key = token(raw_milestone.get("key"))
            if not milestone_key or milestone_key in milestone_seen:
                errors["milestones"] = f"Phase {phase_key!r} has a milestone key in use twice."
                continue
            milestone_seen.add(milestone_key)
            milestones.append(
                {"key": milestone_key, "name": token(raw_milestone.get("name")) or milestone_key}
            )
        phases.append({"key": phase_key, "name": phase_name or phase_key, "milestones": milestones})

    if errors:
        raise GovernanceError("The plan could not be read.", errors)
    return {
        "key": key,
        "name": name,
        "description": token(row.get("description")),
        "phases": phases,
    }


def normalise_retention(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Read a retention override. Both windows are optional; the clock is not.

    A window the caller does not name keeps the derived default, so a policy can
    set only the clock start and still archive on the house schedule.
    """
    row = dict(payload or {})
    archive_days = (
        coerce_days(row["archive_after_days"], "archive_after_days")
        if row.get("archive_after_days") is not None
        else ARCHIVE_AFTER_DAYS
    )
    delete_days = (
        coerce_days(row["delete_after_days"], "delete_after_days")
        if row.get("delete_after_days") is not None
        else DELETE_AFTER_DAYS
    )
    return {
        "archive_after_days": archive_days,
        "delete_after_days": delete_days,
        "lifecycle_ended_at": row.get("lifecycle_ended_at"),
        "legal_basis": token(row.get("legal_basis")),
        "note": token(row.get("note")),
    }


# --------------------------------------------------------------------------- #
# The brand rules.
# --------------------------------------------------------------------------- #


def check_brand(branding: Mapping[str, Any] | None, brand_set: Mapping[str, Any]) -> list[dict]:
    """Every finding that keeps a room's branding from the brand set.

    A finding names the token, the code and the value that caused it, so a
    seller reads which field to change rather than a sentence to interpret. A
    room that satisfies the set produces an empty list.
    """
    row = dict(branding or {})
    allowed = brand_set.get("allowed_values") or {}
    required = brand_set.get("required_tokens") or list(BRAND_TOKENS)
    findings: list[dict[str, Any]] = []

    for name in required:
        value = token(row.get(name))
        if not value:
            findings.append(
                {
                    "code": FINDING_MISSING_TOKEN,
                    "token": name,
                    "message": f"The {name} token is required and is not set.",
                }
            )
            continue
        approved = allowed.get(name)
        if not approved:
            continue
        if value not in list(approved):
            if name == "theme":
                code = FINDING_UNAPPROVED_THEME
            elif name == "font":
                code = FINDING_UNAPPROVED_FONT
            else:
                code = FINDING_UNAPPROVED_VALUE
            findings.append(
                {
                    "code": code,
                    "token": name,
                    "value": value,
                    "approved": list(approved),
                    "message": (
                        f"The {name} value {value!r} is not approved. "
                        f"Approved: {', '.join(str(item) for item in approved)}."
                    ),
                }
            )
    return findings


def is_compliant(findings: list[dict] | None) -> bool:
    """A room is compliant when the brand check reports nothing."""
    return not findings


def merge_branding(brand_set: Mapping[str, Any]) -> dict[str, str]:
    """The approved tokens a room carries, one value per token.

    Where a set allows several values the first is used, because a room needs
    one value per token and a choice between approved values is a separate
    decision this build does not model.
    """
    tokens = brand_set.get("tokens") or {}
    return {name: token(tokens.get(name)) for name in BRAND_TOKENS if token(tokens.get(name))}


def brand_state_of(
    room_state: Mapping[str, Any] | None, brand_set: Mapping[str, Any]
) -> dict[str, Any]:
    """The brand half of a room's governance row, as a page reads it.

    ``stale`` is the read-through refresh: a room is stale when the brand-set
    version it was applied against is behind the version the set now carries.
    """
    row = dict(room_state or {})
    branding = dict(row.get("branding") or {})
    findings = check_brand(branding, brand_set)
    return {
        "brand_set": row.get("brand_set") or "",
        "brand_set_version": read_int(row.get("brand_set_version"), 0) or 0,
        "brand_applied_at": row.get("brand_applied_at"),
        "branding": branding,
        "findings": findings,
        "compliant": is_compliant(findings),
    }


def is_stale(room_state: Mapping[str, Any] | None, brand_set_version: Any) -> bool:
    """Is the room behind the brand-set version it was applied against?"""
    applied = read_int((dict(room_state or {})).get("brand_set_version"), 0) or 0
    current = read_int(brand_set_version, 0) or 0
    return applied < current


# --------------------------------------------------------------------------- #
# The version rules.
# --------------------------------------------------------------------------- #


def next_version(rows: list[Mapping[str, Any]]) -> int:
    """The version number the next snapshot of this line takes.

    The highest existing number plus one, and one when there is no history. A
    row count would restart the numbering after a soft delete, which would hand
    a restored room a version number it has already published.
    """
    highest = FIRST_VERSION
    for row in rows:
        number = read_int(data_of(row).get("version"), None)
        if number is not None and number >= highest:
            highest = number + 1
    return highest


def history_order(rows: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """A history line, oldest first, so version one reads first."""
    return sorted(rows, key=lambda row: read_int(data_of(row).get("version"), 0) or 0)


def diff_states(current: Mapping[str, Any], snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    """What a restore would change, one row per differing field.

    A row is emitted only for a field that actually differs, so a preview of the
    current version reports no change rather than a list of identical values.
    """
    rows: list[dict[str, Any]] = []
    for field in ("label", "summary", "content", "sections", "branding"):
        before = (current or {}).get(field)
        after = (snapshot or {}).get(field)
        if before != after:
            rows.append({"field": field, "from": before, "to": after})
    return rows


# --------------------------------------------------------------------------- #
# The phase rules.
# --------------------------------------------------------------------------- #


def plan_order(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The plan's phases in the order they carry, with their milestones."""
    phases = plan.get("phases") or []
    rows: list[dict[str, Any]] = []
    for position, phase in enumerate(phases):
        row = dict(phase)
        row["order"] = read_int(row.get("order"), position) or position
        row["milestones"] = list(row.get("milestones") or [])
        rows.append(row)
    rows.sort(key=lambda row: row["order"])
    return rows


def active_phase(phases: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The one phase a room is working through, or ``None`` when none is active."""
    for row in phases:
        if data_of(row).get("state") == PHASE_ACTIVE:
            return row
    return None


def open_milestones(phase: Mapping[str, Any]) -> list[str]:
    """The keys of a phase's milestones that are not complete."""
    return [
        str(row.get("key"))
        for row in (data_of(phase).get("milestones") or [])
        if row.get("state") != MILESTONE_COMPLETE
    ]


def phase_progress(phases: list[Mapping[str, Any]]) -> dict[str, int]:
    """The phase and milestone counts a summary reads."""
    complete = sum(1 for row in phases if data_of(row).get("state") == PHASE_COMPLETE)
    active = sum(1 for row in phases if data_of(row).get("state") == PHASE_ACTIVE)
    milestones = 0
    milestones_done = 0
    for row in phases:
        for milestone in data_of(row).get("milestones") or []:
            milestones += 1
            if milestone.get("state") == MILESTONE_COMPLETE:
                milestones_done += 1
    return {
        "phases": len(phases),
        "phases_complete": complete,
        "phases_active": active,
        "phases_pending": len(phases) - complete - active,
        "milestones": milestones,
        "milestones_complete": milestones_done,
    }


# --------------------------------------------------------------------------- #
# The retention rules.
# --------------------------------------------------------------------------- #


def lifecycle_end(room_state: Mapping[str, Any] | None) -> datetime | None:
    """When the room's lifecycle ended: onboarding finished, or the deal closed.

    ``None`` when neither is known, because a room whose lifecycle has not ended
    is never archive-due and answering that needs no date at all.
    """
    row = dict(room_state or {})
    for field in ("onboarded_at", "closed_at", "repurposed_at"):
        parsed = as_datetime(row.get(field))
        if parsed is not None:
            return parsed
    return None


def retention_window(
    policy: Mapping[str, Any] | None, room_state: Mapping[str, Any] | None
) -> dict[str, Any]:
    """The archive instant and the delete instant a policy names.

    Both are derived from the lifecycle end, which is the clock start the
    retention decision records. A policy that carries no lifecycle end has no
    window, and its board row reports that rather than inventing a date.
    """
    row = dict(policy or {})
    archive_days = read_int(row.get("archive_after_days"), ARCHIVE_AFTER_DAYS) or ARCHIVE_AFTER_DAYS
    delete_days = read_int(row.get("delete_after_days"), DELETE_AFTER_DAYS) or DELETE_AFTER_DAYS
    started = as_datetime(row.get("lifecycle_ended_at")) or lifecycle_end(room_state)
    archive_at = started + timedelta(days=archive_days) if started else None
    anchored = as_datetime(row.get("archived_at")) or archive_at
    delete_at = anchored + timedelta(days=delete_days) if anchored else None
    return {
        "archive_after_days": archive_days,
        "delete_after_days": delete_days,
        "lifecycle_ended_at": started.isoformat() if started else None,
        "archive_at": archive_at.isoformat() if archive_at else None,
        "delete_at": delete_at.isoformat() if delete_at else None,
    }


def retention_due(
    policy: Mapping[str, Any] | None, room_state: Mapping[str, Any] | None, *, now: datetime
) -> dict[str, Any]:
    """Whether the retention schedule owes this room an archive or a delete.

    The two are ordered: a room is archived first and deleted only from the
    archived state, so a delete can never skip the archive step and lose the
    record of what was withdrawn.
    """
    row = dict(policy or {})
    state = token(row.get("state")) or RETENTION_LIVE
    window = retention_window(row, room_state)
    archive_due = state == RETENTION_LIVE and _reached(window["archive_at"], now)
    delete_due = state == RETENTION_ARCHIVED and _reached(window["delete_at"], now)
    return {
        "state": state,
        "archive_due": archive_due,
        "delete_due": delete_due,
        "due": archive_due or delete_due,
        **window,
    }


def _reached(at: str | None, now: datetime) -> bool:
    """Has ``now`` reached the instant ``at``? An unknown instant never arrives."""
    parsed = as_datetime(at)
    return parsed is not None and now >= parsed


# --------------------------------------------------------------------------- #
# The book: the only place the rules above meet the store.
# --------------------------------------------------------------------------- #


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GovernanceBook:
    """Reads and writes a room's governance rows through the audited store.

    ``now`` is injectable so a test can pin the clock. The production route
    leaves it at the real clock, because a caller that could pass its own ``now``
    could make a retention window read as though it had not opened yet.

    Every write takes ``source`` from its caller, and the feature module builds
    that string from its own router. A literal route name here would leave the
    audit log naming a path the app stopped serving.
    """

    def __init__(self, store: RecordStore, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self._now = now or _utcnow

    # -- reads of one record ------------------------------------------------ #

    def _require(self, collection: str, record_id: str, label: str) -> dict[str, Any]:
        record = self.store.get(record_id)
        if record is None or record.get("collection") != collection:
            raise GovernanceNotFound(f"No such {label}: {record_id}")
        return record

    def require_room(self, room_id: str) -> dict[str, Any]:
        """The room record, or a not-found error. Rooms live in the core table."""
        if not token(room_id):
            raise GovernanceNotFound("No such room.")
        record = self.store.get(room_id)
        if record is None or record.get("collection") != "room":
            raise GovernanceNotFound(f"No such room: {room_id}")
        return record

    def _list(
        self,
        collection: str,
        room_id: str | None = None,
        *,
        limit: int = 500,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        if room_id:
            return self.store.find(
                collection, {ROOM_REF: room_id}, limit=limit, include_deleted=include_deleted
            )
        return self.store.list(collection, limit=limit, include_deleted=include_deleted)

    def _refuse_closed(self, room_state: Mapping[str, Any]) -> None:
        """Refuse a write to a room the retention schedule has closed."""
        if token((room_state or {}).get("lifecycle_state")) in CLOSED_LIFECYCLE_STATES:
            raise GovernanceRefused(GovernanceRefused.REASON_LIFECYCLE_CLOSED)

    def _write_room_state(
        self, room_id: str, patch: Mapping[str, Any], *, source: str, actor: str | None
    ) -> dict[str, Any]:
        """Create the room's governance row, or merge the patch into it."""
        record = self.store.find(ROOM_STATE_COLLECTION, {ROOM_REF: room_id}, limit=1)
        if record:
            return self.store.update(record[0]["id"], patch, source=source, actor=actor)
        return self.store.create(
            ROOM_STATE_COLLECTION,
            {ROOM_REF: room_id, "lifecycle_state": LIFECYCLE_SALES, **dict(patch)},
            room_id=room_id,
            source=source,
            actor=actor,
        )

    def _write_verified(
        self,
        record_id: str,
        patch: Mapping[str, Any],
        *,
        expect: Mapping[str, Any],
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """Write, read back, and refuse to report success if it did not land."""
        self.store.update(record_id, patch, source=source, actor=actor)
        fresh = self.store.get(record_id)
        if fresh is None:
            raise GovernanceRefused(
                GovernanceRefused.REASON_WRITE_NOT_VISIBLE,
                f"record {record_id} vanished after a write.",
            )
        data = data_of(fresh)
        for key, value in expect.items():
            if data.get(key) != value:
                raise GovernanceRefused(
                    GovernanceRefused.REASON_WRITE_NOT_VISIBLE,
                    f"{key} did not read back as written.",
                )
        return fresh

    # -- the brand set ------------------------------------------------------ #

    def read_brand_set(self) -> dict[str, Any]:
        """The brand set, or the house defaults when none was written yet."""
        record = self.store.list(BRAND_SET_COLLECTION, limit=1)
        if not record:
            return {**normalise_brand_set(None), "version": FIRST_VERSION}
        data = data_of(record[0])
        return {
            **normalise_brand_set(data),
            "version": read_int(data.get("version"), FIRST_VERSION) or FIRST_VERSION,
            "updated_at": record[0].get("updated_at"),
        }

    def write_brand_set(
        self, payload: Mapping[str, Any] | None, *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Replace the brand token set. Each write is a new version.

        The version is what makes a stale room visible: a room carries the
        version it was applied against, and a room applied against an older
        version is reported as stale rather than silently out of date.
        """
        normalised = normalise_brand_set(payload)
        record = self.store.list(BRAND_SET_COLLECTION, limit=1)
        if record:
            version = (read_int(data_of(record[0]).get("version"), FIRST_VERSION) or 0) + 1
        else:
            version = FIRST_VERSION
        data = {**normalised, "version": version}
        if record:
            self.store.update(record[0]["id"], data, source=source, actor=actor)
        else:
            self.store.create(BRAND_SET_COLLECTION, data, source=source, actor=actor)
        return self.read_brand_set()

    # -- the room's governance row ------------------------------------------ #

    def room_state(self, room_id: str) -> dict[str, Any]:
        """The governance row of a room, or an empty one before the first write."""
        record = self.store.find(ROOM_STATE_COLLECTION, {ROOM_REF: room_id}, limit=1)
        if not record:
            return {ROOM_REF: room_id, "lifecycle_state": LIFECYCLE_SALES, "head_version": 0}
        return data_of(record[0])

    def brand_state(self, room_id: str) -> dict[str, Any]:
        """What the brand rules say about one room, and what applying them writes."""
        room = self.require_room(room_id)
        brand_set = self.read_brand_set()
        record = self.store.find(ROOM_STATE_COLLECTION, {ROOM_REF: room_id}, limit=1)
        state_data = data_of(record[0]) if record else None
        state = brand_state_of(state_data, brand_set)
        # A room that has never had the brand applied still carries whatever
        # branding the core room record holds. Reading it here is what makes an
        # off-brand room a finding rather than a room with no tokens at all, and
        # it is the state "auto-apply brand rules to every new room" exists to
        # clear. ``branding_source`` says which record the findings came from.
        branding_source = "room_state"
        if not state["branding"]:
            core = data_of(room).get("branding")
            if isinstance(core, Mapping) and core:
                findings = check_brand(core, brand_set)
                state["branding"] = {name: token(core.get(name)) for name in core}
                state["findings"] = findings
                state["compliant"] = is_compliant(findings)
                branding_source = "room"
        return {
            "room_id": room_id,
            "room_name": data_of(room).get("name") or "",
            "lifecycle_state": token((state_data or {}).get("lifecycle_state")) or LIFECYCLE_SALES,
            "brand_set_version": brand_set["version"],
            "stale": is_stale(state_data, brand_set["version"]),
            "required_tokens": list(brand_set.get("required_tokens") or []),
            "branding_source": branding_source,
            **state,
        }

    def apply_brand(self, room_id: str, *, actor: str | None, source: str) -> dict[str, Any]:
        """Apply the brand rules to a room: write the approved tokens.

        The write is idempotent. A second apply of an unchanged brand set writes
        nothing and reports the same state, so a page that re-applies on every
        load does not grow an audit trail.
        """
        self.require_room(room_id)
        brand_set = self.read_brand_set()
        branding = merge_branding(brand_set)
        current = self.room_state(room_id)
        self._refuse_closed(current)
        if current.get("branding") == branding and (
            read_int(current.get("brand_set_version"), 0) or 0
        ) == (read_int(brand_set.get("version"), 0) or 0):
            return self.brand_state(room_id)
        now = self._now()
        self._write_room_state(
            room_id,
            {
                "brand_set": brand_set["name"],
                "brand_set_version": brand_set["version"],
                "branding": branding,
                "brand_findings": check_brand(branding, brand_set),
                "brand_applied_at": now.isoformat(),
            },
            source=source,
            actor=actor,
        )
        return self.brand_state(room_id)

    def _room_name(self, room_id: str) -> str:
        record = self.store.get(room_id)
        if record and record.get("collection") == "room":
            return token(data_of(record).get("name"))
        return ""

    def brand_board(self) -> dict[str, Any]:
        """Every governed room's brand state, for the control surface.

        A read-through refresh, not a live push: a room reports that it is stale
        when the brand-set version it was applied against is behind the current
        one. Applying the brand again brings it current.
        """
        brand_set = self.read_brand_set()
        rooms: list[dict[str, Any]] = []
        for record in self.store.list(ROOM_STATE_COLLECTION, limit=1000):
            data = data_of(record)
            room_id = token(data.get(ROOM_REF))
            state = brand_state_of(data, brand_set)
            rooms.append(
                {
                    "room_id": room_id,
                    "room_name": self._room_name(room_id),
                    "lifecycle_state": token(data.get("lifecycle_state")) or LIFECYCLE_SALES,
                    "stale": is_stale(data, brand_set["version"]),
                    **state,
                }
            )
        return {
            "brand_set": brand_set,
            "count": len(rooms),
            "non_compliant": sum(1 for row in rooms if not row["compliant"]),
            "stale": sum(1 for row in rooms if row["stale"]),
            "rooms": rooms,
        }

    # -- versioning: publish, checkpoint, restore, branch ------------------- #

    def _snapshots(self, room_id: str, *, include_deleted: bool = False) -> list[dict[str, Any]]:
        """A room's history, oldest version first."""
        return history_order(
            self._list(SNAPSHOT_COLLECTION, room_id, include_deleted=include_deleted)
        )

    def _find_version(self, room_id: str, version: int) -> dict[str, Any]:
        for row in self._snapshots(room_id):
            if read_int(data_of(row).get("version"), None) == version:
                return row
        raise GovernanceNotFound(f"No version {version} on room {room_id}.")

    def _snapshot_view(self, row: Mapping[str, Any]) -> dict[str, Any]:
        data = data_of(row)
        return {
            "id": row.get("id"),
            "version": read_int(data.get("version"), 0) or 0,
            "trigger": token(data.get("trigger")) or TRIGGER_PUBLISH,
            "label": token(data.get("label")),
            "summary": token(data.get("summary")),
            "sections": list(data.get("sections") or []),
            "at": data.get("at"),
            "author": token(data.get("author")),
            "restored_from": read_int(data.get("restored_from"), None),
            "branched_from": read_int(data.get("branched_from"), None),
            "branched_from_room": token(data.get("branched_from_room")) or None,
            "content": data.get("content"),
        }

    def _head(self, room_id: str) -> dict[str, Any] | None:
        """The head snapshot view, or ``None`` before the first publish."""
        head = read_int(self.room_state(room_id).get("head_version"), 0) or 0
        if not head:
            return None
        try:
            return self._snapshot_view(self._find_version(room_id, head))
        except GovernanceNotFound:
            return None

    def _record_snapshot(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None,
        *,
        trigger: str,
        actor: str | None,
        source: str,
        restored_from: int | None = None,
        branched_from: int | None = None,
        branched_from_room: str | None = None,
    ) -> dict[str, Any]:
        """Write one immutable version and move the room's head to it."""
        room = self.require_room(room_id)
        current = self.room_state(room_id)
        self._refuse_closed(current)
        if trigger not in SNAPSHOT_TRIGGERS:
            raise GovernanceError(
                "That snapshot trigger is not one this workflow records.",
                {"trigger": f"Use one of: {', '.join(SNAPSHOT_TRIGGERS)}."},
            )
        normalised = normalise_snapshot(payload, now=self._now())
        content = normalised["content"]
        if not content:
            content = dict(data_of(room))
        version = next_version(self._snapshots(room_id, include_deleted=True))
        data: dict[str, Any] = {
            "room_ref": room_id,
            "version": version,
            "trigger": trigger,
            "label": normalised["label"] or f"{trigger} v{version}",
            "summary": normalised["summary"],
            "content": dict(content) if isinstance(content, Mapping) else content,
            "sections": normalised["sections"],
            "at": normalised["at"].isoformat(),
            "author": token(actor),
        }
        if restored_from is not None:
            data["restored_from"] = restored_from
        if branched_from is not None:
            data["branched_from"] = branched_from
        if branched_from_room:
            data["branched_from_room"] = branched_from_room
        record = self.store.create(
            SNAPSHOT_COLLECTION, data, room_id=room_id, source=source, actor=actor
        )
        patch: dict[str, Any] = {"head_version": version, "head_snapshot_id": record["id"]}
        if trigger == TRIGGER_PUBLISH:
            patch["published_at"] = data["at"]
        self._write_room_state(room_id, patch, source=source, actor=actor)
        view = self._snapshot_view(record)
        view["room_id"] = room_id
        return view

    def publish(
        self, room_id: str, payload: Mapping[str, Any] | None = None, *, actor=None, source: str
    ) -> dict[str, Any]:
        """Snapshot a publish. The spec's "auto-snapshot on every publish"."""
        return self._record_snapshot(
            room_id, payload, trigger=TRIGGER_PUBLISH, actor=actor, source=source
        )

    def checkpoint(
        self, room_id: str, payload: Mapping[str, Any] | None = None, *, actor=None, source: str
    ) -> dict[str, Any]:
        """Snapshot an editing session. The evidence's second, distinct trigger."""
        return self._record_snapshot(
            room_id, payload, trigger=TRIGGER_SESSION, actor=actor, source=source
        )

    def history(self, room_id: str) -> dict[str, Any]:
        """Every version of a room, oldest first, with the head marked."""
        self.require_room(room_id)
        state = self.room_state(room_id)
        rows = self._snapshots(room_id)
        return {
            "room_id": room_id,
            "count": len(rows),
            "head_version": read_int(state.get("head_version"), 0) or 0,
            "lifecycle_state": token(state.get("lifecycle_state")) or LIFECYCLE_SALES,
            "published_at": state.get("published_at"),
            "versions": [self._snapshot_view(row) for row in rows],
        }

    def preview(self, room_id: str, version: int) -> dict[str, Any]:
        """What restoring a version would change, field by field."""
        room = self.require_room(room_id)
        target = self._snapshot_view(self._find_version(room_id, version))
        head = self._head(room_id)
        current = head or {
            "version": 0,
            "label": "",
            "summary": "",
            "content": dict(data_of(room)),
            "sections": [],
            "branding": None,
        }
        changes = diff_states(current, target)
        return {
            "room_id": room_id,
            "version": version,
            "head_version": (head or {}).get("version", 0),
            "matches_head": not changes,
            "changes": changes,
            "target": target,
        }

    def restore(self, room_id: str, version: int, *, actor=None, source: str) -> dict[str, Any]:
        """Restore a version by writing it as the next version, never editing one."""
        target = self._snapshot_view(self._find_version(room_id, version))
        return self._record_snapshot(
            room_id,
            {
                "content": target["content"],
                "label": target["label"],
                "summary": target["summary"],
                "sections": target["sections"],
            },
            trigger=TRIGGER_RESTORE,
            actor=actor,
            source=source,
            restored_from=version,
        )

    def branch(
        self,
        room_id: str,
        version: int,
        payload: Mapping[str, Any] | None = None,
        *,
        actor=None,
        source: str,
    ) -> dict[str, Any]:
        """Seed a new room from an earlier version, so the two lines stay separate."""
        room = self.require_room(room_id)
        target = self._snapshot_view(self._find_version(room_id, version))
        row = dict(payload or {})
        base_name = token(data_of(room).get("name")) or "Room"
        name = token(row.get("name")) or f"{base_name} (branch v{version})"
        new_data = dict(data_of(room))
        new_data.update({"name": name, "branched_from": room_id, "branched_from_version": version})
        new_room = self.store.create("room", new_data, source=source, actor=actor)
        snapshot = self._record_snapshot(
            new_room["id"],
            {
                "content": target["content"],
                "label": target["label"],
                "summary": target["summary"],
                "sections": target["sections"],
            },
            trigger=TRIGGER_BRANCH,
            actor=actor,
            source=source,
            branched_from=version,
            branched_from_room=room_id,
        )
        return {
            "room_id": new_room["id"],
            "source_room_id": room_id,
            "source_version": version,
            "snapshot": snapshot,
        }

    # -- plans and the closed-won handoff ----------------------------------- #

    def _resolve_plan(self, payload: Mapping[str, Any] | None) -> dict[str, Any]:
        row = dict(payload or {})
        if row.get("phases"):
            return normalise_plan(row)
        key = token(row.get("plan_key"))
        if key:
            found = self.store.find(PLAN_COLLECTION, {"key": key}, limit=1)
            if not found:
                raise GovernanceNotFound(f"No such plan: {key}")
            return normalise_plan(data_of(found[0]))
        return normalise_plan(DEFAULT_PLAN)

    def write_plan(
        self, payload: Mapping[str, Any] | None, *, actor=None, source: str
    ) -> dict[str, Any]:
        """Create or replace an onboarding plan template by its key."""
        plan = normalise_plan(payload)
        existing = self.store.find(PLAN_COLLECTION, {"key": plan["key"]}, limit=1)
        data = dict(plan)
        if existing:
            record = self.store.update(existing[0]["id"], data, source=source, actor=actor)
        else:
            record = self.store.create(PLAN_COLLECTION, data, source=source, actor=actor)
        return {**plan, "id": record["id"]}

    def list_plans(self) -> list[dict[str, Any]]:
        out = []
        for record in self.store.list(PLAN_COLLECTION, limit=500):
            out.append({**normalise_plan(data_of(record)), "id": record["id"]})
        return out

    def _phase_view(self, row: Mapping[str, Any]) -> dict[str, Any]:
        data = data_of(row)
        milestones = [dict(m) for m in (data.get("milestones") or [])]
        return {
            "id": row.get("id"),
            "phase_key": token(data.get("phase_key")),
            "name": token(data.get("name")),
            "order": read_int(data.get("order"), 0) or 0,
            "state": token(data.get("state")) or PHASE_PENDING,
            "milestones": milestones,
            "open_milestones": [
                str(m.get("key")) for m in milestones if m.get("state") != MILESTONE_COMPLETE
            ],
            "started_at": data.get("started_at"),
            "completed_at": data.get("completed_at"),
        }

    def _phases(self, room_id: str) -> list[dict[str, Any]]:
        rows = self._list(PHASE_COLLECTION, room_id, limit=500)
        rows.sort(key=lambda row: read_int(data_of(row).get("order"), 0) or 0)
        return rows

    def plan_state(self, room_id: str) -> dict[str, Any]:
        """The phased onboarding plan instantiated on a room, and its progress."""
        self.require_room(room_id)
        state = self.room_state(room_id)
        rows = self._phases(room_id)
        views = [self._phase_view(row) for row in rows]
        current = next((view for view in views if view["state"] == PHASE_ACTIVE), None)
        open_now = (current or {}).get("open_milestones") or []
        return {
            "room_id": room_id,
            "lifecycle_state": token(state.get("lifecycle_state")) or LIFECYCLE_SALES,
            "plan_key": token(state.get("plan_key")) or None,
            "plan_name": token(state.get("plan_name")) or None,
            "repurposed_at": state.get("repurposed_at"),
            "closed_at": state.get("closed_at"),
            "onboarded_at": state.get("onboarded_at"),
            "phases": views,
            "active_phase": current,
            "next_milestone": open_now[0] if open_now else None,
            "progress": phase_progress(rows),
        }

    def repurpose(
        self, room_id: str, payload: Mapping[str, Any] | None = None, *, actor=None, source: str
    ) -> dict[str, Any]:
        """Repurpose the room at closed-won: keep the record, add the phases."""
        self.require_room(room_id)
        state = self.room_state(room_id)
        self._refuse_closed(state)
        lifecycle = token(state.get("lifecycle_state")) or LIFECYCLE_SALES
        if lifecycle in (LIFECYCLE_ONBOARDING, LIFECYCLE_ONBOARDED):
            raise GovernanceRefused(GovernanceRefused.REASON_ALREADY_REPURPOSED)
        row = dict(payload or {})
        plan = self._resolve_plan(row)
        now = self._now()
        closed = (
            parse_instant(row.get("closed_at"), field="closed_at") if row.get("closed_at") else now
        )
        for position, phase in enumerate(plan_order(plan)):
            milestones = [
                {
                    "key": milestone["key"],
                    "name": milestone["name"],
                    "state": MILESTONE_PENDING,
                    "completed_at": None,
                }
                for milestone in (phase.get("milestones") or [])
            ]
            data = {
                "room_ref": room_id,
                "plan_key": plan["key"],
                "plan_name": plan["name"],
                "phase_key": phase["key"],
                "name": phase.get("name") or phase["key"],
                "order": position,
                "state": PHASE_ACTIVE if position == 0 else PHASE_PENDING,
                "milestones": milestones,
                "started_at": now.isoformat() if position == 0 else None,
                "completed_at": None,
            }
            self.store.create(PHASE_COLLECTION, data, room_id=room_id, source=source, actor=actor)
        self._write_room_state(
            room_id,
            {
                "lifecycle_state": LIFECYCLE_ONBOARDING,
                "plan_key": plan["key"],
                "plan_name": plan["name"],
                "repurposed_at": now.isoformat(),
                "closed_at": closed.isoformat(),
            },
            source=source,
            actor=actor,
        )
        return self.plan_state(room_id)

    def complete_milestone(
        self, room_id: str, phase_key: str, milestone_key: str, *, actor=None, source: str
    ) -> dict[str, Any]:
        """Close one milestone of one phase."""
        self.require_room(room_id)
        match = next(
            (
                row
                for row in self._phases(room_id)
                if token(data_of(row).get("phase_key")) == phase_key
            ),
            None,
        )
        if match is None:
            raise GovernanceNotFound(f"No phase {phase_key} on room {room_id}.")
        milestones = [dict(m) for m in (data_of(match).get("milestones") or [])]
        found = False
        for milestone in milestones:
            if token(milestone.get("key")) == milestone_key:
                milestone["state"] = MILESTONE_COMPLETE
                milestone["completed_at"] = self._now().isoformat()
                found = True
        if not found:
            raise GovernanceNotFound(f"No milestone {milestone_key} on phase {phase_key}.")
        self._write_verified(
            match["id"],
            {"milestones": milestones},
            expect={"milestones": milestones},
            source=source,
            actor=actor,
        )
        return self._phase_view(self.store.get(match["id"]))

    def advance_phase(self, room_id: str, *, actor=None, source: str) -> dict[str, Any]:
        """Close the active phase and start the next, if its milestones are done."""
        self.require_room(room_id)
        state = self.room_state(room_id)
        self._refuse_closed(state)
        lifecycle = token(state.get("lifecycle_state")) or LIFECYCLE_SALES
        if lifecycle == LIFECYCLE_SALES:
            raise GovernanceRefused(GovernanceRefused.REASON_NOT_REPURPOSED)
        if lifecycle == LIFECYCLE_ONBOARDED:
            raise GovernanceRefused(GovernanceRefused.REASON_PHASES_COMPLETE)
        rows = self._phases(room_id)
        active = next((row for row in rows if data_of(row).get("state") == PHASE_ACTIVE), None)
        if active is None:
            raise GovernanceRefused(GovernanceRefused.REASON_PHASES_COMPLETE)
        open_keys = open_milestones(active)
        if open_keys:
            raise GovernanceRefused(
                GovernanceRefused.REASON_MILESTONES_OPEN,
                "The active phase holds open milestones: " + ", ".join(open_keys),
            )
        now = self._now()
        self._write_verified(
            active["id"],
            {"state": PHASE_COMPLETE, "completed_at": now.isoformat()},
            expect={"state": PHASE_COMPLETE},
            source=source,
            actor=actor,
        )
        pending = next((row for row in rows if data_of(row).get("state") == PHASE_PENDING), None)
        if pending is None:
            self._write_room_state(
                room_id,
                {"lifecycle_state": LIFECYCLE_ONBOARDED, "onboarded_at": now.isoformat()},
                source=source,
                actor=actor,
            )
        else:
            self._write_verified(
                pending["id"],
                {"state": PHASE_ACTIVE, "started_at": now.isoformat()},
                expect={"state": PHASE_ACTIVE},
                source=source,
                actor=actor,
            )
        return self.plan_state(room_id)

    # -- retention ---------------------------------------------------------- #

    def retention_state(self, room_id: str) -> dict[str, Any]:
        """The retention window of a room and whether it owes an archive or delete."""
        self.require_room(room_id)
        room_state = self.room_state(room_id)
        record = self.store.find(RETENTION_COLLECTION, {ROOM_REF: room_id}, limit=1)
        policy = data_of(record[0]) if record else {}
        due = retention_due(policy, room_state, now=self._now())
        return {
            "room_id": room_id,
            "lifecycle_state": token(room_state.get("lifecycle_state")) or LIFECYCLE_SALES,
            "has_policy": bool(record),
            "state": due["state"],
            "legal_basis": token(policy.get("legal_basis")),
            "note": token(policy.get("note")),
            "archive_after_days": due["archive_after_days"],
            "delete_after_days": due["delete_after_days"],
            "lifecycle_ended_at": due["lifecycle_ended_at"],
            "archive_at": due["archive_at"],
            "delete_at": due["delete_at"],
            "archive_due": due["archive_due"],
            "delete_due": due["delete_due"],
            "due": due["due"],
        }

    def write_retention(
        self, room_id: str, payload: Mapping[str, Any] | None = None, *, actor=None, source: str
    ) -> dict[str, Any]:
        """Set or override a room's retention windows, stored as ordinary JSON."""
        self.require_room(room_id)
        self._refuse_closed(self.room_state(room_id))
        normalised = normalise_retention(payload)
        record = self.store.find(RETENTION_COLLECTION, {ROOM_REF: room_id}, limit=1)
        if record:
            existing = token(data_of(record[0]).get("state")) or RETENTION_LIVE
            if existing in (RETENTION_ARCHIVED, RETENTION_DELETED):
                raise GovernanceRefused(
                    GovernanceRefused.REASON_RETENTION_STATE,
                    "The retention record has already archived or deleted this room.",
                )
            self.store.update(record[0]["id"], normalised, source=source, actor=actor)
        else:
            data = {ROOM_REF: room_id, "state": RETENTION_LIVE, **normalised}
            self.store.create(
                RETENTION_COLLECTION, data, room_id=room_id, source=source, actor=actor
            )
        return self.retention_state(room_id)

    def apply_retention(self, room_id: str, *, actor=None, source: str) -> dict[str, Any]:
        """Apply what the schedule owes: archive, then a later soft delete."""
        self.require_room(room_id)
        record = self.store.find(RETENTION_COLLECTION, {ROOM_REF: room_id}, limit=1)
        if not record:
            raise GovernanceNotFound(f"No retention policy for room {room_id}.")
        now = self._now()
        due = retention_due(data_of(record[0]), self.room_state(room_id), now=now)
        if due["archive_due"]:
            self._write_room_state(
                room_id,
                {"lifecycle_state": LIFECYCLE_ARCHIVED, "archived_at": now.isoformat()},
                source=source,
                actor=actor,
            )
            self._write_verified(
                record[0]["id"],
                {"state": RETENTION_ARCHIVED, "archived_at": now.isoformat()},
                expect={"state": RETENTION_ARCHIVED},
                source=source,
                actor=actor,
            )
            return {
                "room_id": room_id,
                "applied": "archived",
                "retention": self.retention_state(room_id),
            }
        if due["delete_due"]:
            ids = [row["id"] for row in self._snapshots(room_id)]
            ids += [row["id"] for row in self._phases(room_id)]
            state_row = self.store.find(ROOM_STATE_COLLECTION, {ROOM_REF: room_id}, limit=1)
            if state_row:
                ids.append(state_row[0]["id"])
            if ids:
                # A soft delete, so the audit trail that describes what was
                # withdrawn survives the withdrawal. The retention row is the
                # tombstone and stays.
                self.store.bulk_delete(ids, room_id=room_id, source=source, actor=actor)
            self._write_verified(
                record[0]["id"],
                {"state": RETENTION_DELETED, "deleted_at": now.isoformat()},
                expect={"state": RETENTION_DELETED},
                source=source,
                actor=actor,
            )
            return {
                "room_id": room_id,
                "applied": "deleted",
                "deleted_rows": len(ids),
                "retention": self.retention_state(room_id),
            }
        raise GovernanceRefused(
            GovernanceRefused.REASON_RETENTION_STATE,
            "Nothing is due on this room's retention schedule yet.",
        )

    def retention_board(self) -> dict[str, Any]:
        """Every retention policy and what the schedule owes it."""
        now = self._now()
        rows = []
        for record in self.store.list(RETENTION_COLLECTION, limit=1000):
            data = data_of(record)
            room_id = token(data.get(ROOM_REF))
            due = retention_due(data, self.room_state(room_id), now=now)
            rows.append(
                {
                    "room_id": room_id,
                    "room_name": self._room_name(room_id),
                    "state": due["state"],
                    "archive_at": due["archive_at"],
                    "delete_at": due["delete_at"],
                    "archive_due": due["archive_due"],
                    "delete_due": due["delete_due"],
                    "due": due["due"],
                }
            )
        return {"count": len(rows), "due": sum(1 for row in rows if row["due"]), "policies": rows}

    # -- the summary the page reads ----------------------------------------- #

    def summary(self) -> dict[str, Any]:
        """Counts for the whole governance surface."""
        room_states = self.store.list(ROOM_STATE_COLLECTION, limit=1000)
        snapshots = self.store.list(SNAPSHOT_COLLECTION, limit=1000)
        phases = self.store.list(PHASE_COLLECTION, limit=1000)
        policies = self.store.list(RETENTION_COLLECTION, limit=1000)
        brand_set = self.read_brand_set()
        lifecycle: dict[str, int] = {}
        for record in room_states:
            key = token(data_of(record).get("lifecycle_state")) or LIFECYCLE_SALES
            lifecycle[key] = lifecycle.get(key, 0) + 1
        now = self._now()
        due = 0
        for record in policies:
            data = data_of(record)
            if retention_due(data, self.room_state(token(data.get(ROOM_REF))), now=now)["due"]:
                due += 1
        return {
            "rooms_tracked": len(room_states),
            "snapshots": len(snapshots),
            "plans": len(self.store.list(PLAN_COLLECTION, limit=500)),
            "phases": len(phases),
            "phases_complete": sum(
                1 for row in phases if data_of(row).get("state") == PHASE_COMPLETE
            ),
            "retention_policies": len(policies),
            "retention_due": due,
            "brand_set_version": brand_set["version"],
            "brand_stale_rooms": sum(
                1 for record in room_states if is_stale(data_of(record), brand_set["version"])
            ),
            "lifecycle": lifecycle,
        }
