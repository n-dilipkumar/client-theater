"""WF-122: the vocabulary and the recorded decisions of the tiered data room.

This module holds the constants every other module in the package imports, and
the one function, :func:`vocabulary`, that publishes them. It imports nothing
from the rest of the package, so it is the root of the dependency graph.

Every open question in the issue is decided here and written down, not buried in
code. A reviewer who cannot tell "we decided not to" from "we forgot to" has to
read the source, and that is a cost paid every time the question comes up. The
vocabulary route serves this dict, so the page and a reviewer read the same
thing.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections. One shared `records` table carries every feature, so each name is
# namespaced per ticket and `find()` matches on the collection before anything
# else.
# --------------------------------------------------------------------------- #

#: One tier policy per room: the tier labels and the default below-tier state.
POLICY_COLLECTION = "wf122_room_policy"

#: One section of the room tree. Carries the section's minimum tier.
SECTION_COLLECTION = "wf122_section"

#: One counterparty organisation, addressed by one access link.
RECIPIENT_COLLECTION = "wf122_recipient"

#: One section revealed to one recipient below their tier. Section-level access.
GRANT_COLLECTION = "wf122_section_grant"

#: One request for an on-request section, from a recipient who cannot see it yet.
REQUEST_COLLECTION = "wf122_access_request"

#: One unlock, revoke, rotation, grant or request. The spec's "events logged".
EVENT_COLLECTION = "wf122_disclosure_event"

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
# Tiers and the three below-tier states
# --------------------------------------------------------------------------- #

#: The tier every recipient starts at: the curated room the seller shares first.
PUBLIC_TIER = 0

#: The tier a recipient is created at when the payload is silent.
DEFAULT_RECIPIENT_TIER = PUBLIC_TIER

#: The viewer sees the section's contents.
FULL = "full"

#: The viewer sees nothing at all. The section is absent from the response.
HIDDEN = "hidden"

#: The viewer sees the section's name and teaser text, but not its contents.
TEASER = "teaser"

#: The viewer sees that the section exists and may ask for it to be unlocked.
ON_REQUEST = "on_request"

#: The three states a below-tier recipient can be shown. All three ship; see
#: ``DECISIONS["below_tier_states"]`` for why ``on_request`` is not deferred.
BELOW_TIER_STATES = (HIDDEN, TEASER, ON_REQUEST)

#: Every disclosure a rendered section can carry, including a full one.
DISCLOSURES = (FULL, HIDDEN, TEASER, ON_REQUEST)

#: A section without an explicit ``below_tier`` uses the room policy's default.
DEFAULT_BELOW_TIER = HIDDEN

#: The tier labels a room shows before the seller names its own. Integer tiers
#: are this repository's model, taken from Hyperportal's per-section ``minTier``.
DEFAULT_TIER_LABELS: dict[str, str] = {
    "0": "Curated room",
    "1": "Financials",
    "2": "Legal",
    "3": "Security",
}

# --------------------------------------------------------------------------- #
# Recipient link states
# --------------------------------------------------------------------------- #

#: The link works and discloses what its tier allows.
ACTIVE_STATE = "active"

#: The link was revoked. This is a stored state; it is set only by the revoke
#: route.
REVOKED_STATE = "revoked"

#: The link passed its ``expires_at``. This is *derived*, never stored, so time
#: alone cannot be written back as a change.
EXPIRED_STATE = "expired"

#: Every state a link can be in, whether stored or derived.
RECIPIENT_STATES = (ACTIVE_STATE, REVOKED_STATE, EXPIRED_STATE)

# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #

EVENT_RECIPIENT_CREATED = "recipient_created"
EVENT_TIER_UNLOCKED = "tier_unlocked"
EVENT_LINK_REVOKED = "link_revoked"
EVENT_LINK_ROTATED = "link_rotated"
EVENT_SECTION_GRANTED = "section_granted"
EVENT_SECTION_REQUESTED = "section_requested"

EVENT_KINDS = (
    EVENT_RECIPIENT_CREATED,
    EVENT_TIER_UNLOCKED,
    EVENT_LINK_REVOKED,
    EVENT_LINK_ROTATED,
    EVENT_SECTION_GRANTED,
    EVENT_SECTION_REQUESTED,
)

# --------------------------------------------------------------------------- #
# The access link. See DECISIONS["tokens"] for the reasoning.
# --------------------------------------------------------------------------- #

#: The cookie the redemption route sets. HttpOnly and SameSite=Lax, so a script
#: cannot read it and a cross-site request does not carry it.
TOKEN_COOKIE = "wf122_access"

#: The lifetime of the redemption cookie, in seconds. One day is long enough for
#: a diligence session and short enough that a shared browser does not keep it.
TOKEN_COOKIE_MAX_AGE = 60 * 60 * 24

# --------------------------------------------------------------------------- #
# The decisions the issue left open, each with the alternative rejected.
# --------------------------------------------------------------------------- #

DECISIONS: dict[str, str] = {
    "tier_model": (
        "Tier numbers are this repository's own model, taken from Hyperportal's "
        "per-section minTier. No commercial vendor in the research documents "
        "numeric tiers; they document role-based and page-based access instead."
    ),
    "below_tier_states": (
        "Three below-tier states ship: hidden, teaser and on_request. The spec's "
        "features_tools names teaser and hidden, and its evidence adds 'on "
        "request', so all three are built rather than deferring the third."
    ),
    "section_scope": (
        "Disclosure is decided per section, the unit Hyperportal calls a section. "
        "A document inside a section inherits the section's tier. Documents are "
        "not modelled here because this repository hosts no object storage."
    ),
    "verification": (
        "Every tier change writes through the audited store and then reads the "
        "record back. A write that does not read back raises a conflict rather "
        "than reporting success, matching the source CLI that verifies against "
        "the live site before reporting success."
    ),
    "tokens": (
        "One link is issued per counterparty organisation. Only the SHA-256 hash "
        "and a four-character hint of the token are stored. Redemption sets an "
        "HttpOnly, SameSite=Lax cookie and redirects, so the token does not live "
        "in browser history or a Referer header."
    ),
    "section_grants": (
        "A section grant reveals one section to one recipient below their tier. "
        "It is section-level access control, distinct from raising the "
        "recipient's whole tier."
    ),
    "expiry": (
        "Revocation is a stored state and expiry is derived from expires_at. Both "
        "make every section below-tier, so a revoked or expired link sees nothing "
        "rather than a stale room."
    ),
    "crm": (
        "The spec lists a CRM as a data source and as a surface that writes "
        "'diligence advanced'. This repository owns no CRM connection, so "
        "auto-create-on-stage-change and auto-unlock-on-milestone are not built. "
        "Nothing here reads or writes an external system."
    ),
    "page_acl": (
        "Walnut's page-level named-recipient access is a different but adjacent "
        "primitive. It is not in scope: this workflow authenticates one link per "
        "counterparty organisation and filters by numeric tier, not by a named "
        "recipient list."
    ),
    "object_storage": (
        "Object storage and version history are named in the spec and are not "
        "built. A section carries placeholder document names, because this "
        "repository stores records, not files."
    ),
}

#: What this build deliberately does not do, named so a reviewer does not have to
#: guess whether a gap is a decision or an oversight.
NOT_IMPLEMENTED: tuple[str, ...] = (
    "Object storage: a section carries placeholder document names, not uploaded files.",
    "Version history of a section's contents.",
    "CRM-driven room creation, deal-stage reads and auto-unlock on a milestone.",
    "Page-level named-recipient ACLs (Walnut), a different but adjacent primitive.",
    "Email delivery of an invite or revoke notice; the API returns the link instead.",
)


def vocabulary() -> dict[str, Any]:
    """The vocabulary this workflow enforces, and the decisions behind it.

    Served by the vocabulary route so the page reads its states from here rather
    than hard-coding them, and so a reviewer can read each decision without
    opening the source.
    """
    return {
        "public_tier": PUBLIC_TIER,
        "default_recipient_tier": DEFAULT_RECIPIENT_TIER,
        "default_below_tier": DEFAULT_BELOW_TIER,
        "tier_labels": dict(DEFAULT_TIER_LABELS),
        "disclosures": list(DISCLOSURES),
        "below_tier_states": list(BELOW_TIER_STATES),
        "recipient_states": list(RECIPIENT_STATES),
        "event_kinds": list(EVENT_KINDS),
        "token_cookie": TOKEN_COOKIE,
        "decisions": dict(DECISIONS),
        "not_implemented": list(NOT_IMPLEMENTED),
    }
