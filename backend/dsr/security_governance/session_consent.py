"""WF-083: every researched term this workflow enforces against.

The specification is ``docs/research/digital-sales-room-workflows/wf/WF-083.md``, quoted
in full in issue 191. Every value below is either quoted from that document or derived
from a quote by the arithmetic shown beside it. Nothing here is a house opinion.

The workflow in one sentence
----------------------------

The specification's own "Why it matters" line reads: "Consent-gated recording.
Conditional: a legal gate wherever session recording exists." So every constant below
describes a legal gate, and the module that uses them must fail **closed**. A recording
that should not exist must not exist, and a recording that does exist must never carry
content the owner masked.

The three axes, which are three axes and not one flag
----------------------------------------------------

The specification names the consent API as "a two-axis ``granted|denied`` decision (ads
vs analytics storage)". The evidence gives the call verbatim:
``window.clarity('consentv2', {ad_Storage: 'granted'|'denied', analytics_Storage:
'granted'|'denied'})``. Two axes, four combinations, and they are independent: a room
that allows analytics while refusing ad storage is an ordinary and legal setting, not an
edge case. The third axis is the owner's own switch, :data:`CONSENT_GATE`, which the user
flow describes as step two: "Owner enables **cookie consent** for the project so the
consent gate applies."

Default suppression is total
---------------------------

The evidence says "By default, Clarity suppresses the client's entire content." So an
unmasked value in a stored record is the exception, and this product treats it as one:
:data:`DEFAULT_MASKING_MODE` is ``suppress_all``, ``DefaultMasker`` suppresses every
scalar it is given, and the HTTP layer refuses to store a frame that still contains a
suppressed value.

Consent is revocable at runtime
------------------------------

The evidence describes denial as destructive, not as a skip: "When a user rejects the
Clarity cookie, Clarity deletes any existing cookie for the website, ends the current
session, and restarts tracking in no-consent mode." So :data:`REVOKE_SIGNAL` exists as a
named value, :data:`DESTROYED_ON_DENIAL` is ``True``, and :func:`consent_call` of
:mod:`dsr.security_governance.session_consent_rules` turns a revocation into a delete,
not into a suppression.

The four limits that are sourced and must fail loudly
------------------------------------------------------

* "you can't delete or download specific recordings". Deletion is project-granularity,
  so ``purge`` is the only delete this workflow offers, and a single-recording delete is
  a refusal, not a partial purge.
* "Clarity only supports IPv4 addresses. We do not support IPv6 or dynamic IP addresses
  (for example, VPN)." An IPv6 range is refused, and the refusal is recorded.
* "A recording takes at most 5 labels." The sixth label is refused.
* "Favorite recordings and randomly selected sample of recordings are retained for up to
  9 months." A favourite outlives the ordinary 30-day window, which is why retention is a
  function of two inputs and not a single field.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# Namespaced, because every feature shares one ``records`` table and ``find()`` matches
# on collection before it matches on anything else.

PROJECT_COLLECTION = "wf083_recording_project"
VISIT_COLLECTION = "wf083_recorder_visit"
RECORDING_COLLECTION = "wf083_session_recording"
LABEL_COLLECTION = "wf083_recording_label"
SHARE_COLLECTION = "wf083_recording_share"
IP_COLLECTION = "wf083_blocked_ip_range"

ALL_COLLECTIONS = (
    PROJECT_COLLECTION,
    VISIT_COLLECTION,
    RECORDING_COLLECTION,
    LABEL_COLLECTION,
    SHARE_COLLECTION,
    IP_COLLECTION,
)

# --------------------------------------------------------------------------- #
# The consent signal
# --------------------------------------------------------------------------- #
#
# The API name and the two axis names are quoted from the specification's "APIs touched"
# section, so a caller can tell a recorded consent call from one this product invented.

CONSENT_CALL = "consentv2"
LEGACY_CONSENT_CALL = "consent"

#: The two axes, named as the specification names them. Case is the vendor's, because
#: ``ad_Storage`` and ``analytics_Storage`` are the JSON keys of the vendor's call.
AD_STORAGE = "ad_storage"
ANALYTICS_STORAGE = "analytics_storage"

CONSENT_AXES = (AD_STORAGE, ANALYTICS_STORAGE)

#: The two values each axis may hold. The specification writes them as
#: ``'granted'|'denied'`` in the signature itself.
GRANTED = "granted"
DENIED = "denied"

CONSENT_VALUES = (GRANTED, DENIED)

#: The signal a room emits before it asks a visitor to choose. It is not a consent value
#: and must never be stored as one: a stored signal says "a choice was requested", and a
#: stored value says "a choice was made". Those are different facts.
CONSENT_SIGNAL = "signal"

#: The owner's switch. User flow step two: "Owner enables cookie consent for the project
#: so the consent gate applies." When it is off, the room emits
#: :data:`CONSENT_SIGNAL` and nothing else, and no visitor can grant anything.
CONSENT_GATE = "consent_gate_enabled"

#: Denied means teardown, not skip. Evidence: "Clarity deletes any existing cookie for the
#: website, ends the current session, and restarts tracking in no-consent mode."
DESTROYED_ON_DENIAL = True

# --------------------------------------------------------------------------- #
# Masking
# --------------------------------------------------------------------------- #
#
# Evidence: "Is masked data uploaded to Clarity? No." and "By default, Clarity suppresses
# the client's entire content. The website admin controls the content sent to Clarity,
# and website owners should use their dashboard settings to block confidential content."

SUPPRESS_ALL = "suppress_all"
ELEMENT_SELECTOR = "element_selector"
SELECT_TEXT = "select_text"

MASKING_MODES = (SUPPRESS_ALL, ELEMENT_SELECTOR, SELECT_TEXT)

#: The default is the documented one. An unmasked frame is the exception, so a project
#: that never configures masking stores no content at all.
DEFAULT_MASKING_MODE = SUPPRESS_ALL

#: The placeholder written in place of content that must never be uploaded.
MASK = "*"

#: What the specification calls the before-upload step.
SCRUBBED = "scrubbed"

# --------------------------------------------------------------------------- #
# IP blocking
# --------------------------------------------------------------------------- #
#
# Evidence: "**Recordings:** No sessions from visitors on the list are recorded.";
# "Clarity only supports IPv4 addresses. We do not support IPv6 or dynamic IP
# addresses (for example, VPN)."; "To set up IP exclusion, you need to be an
# *administrator* for your project."

IPV4_ONLY = True

#: The console message the vendor prints when an excluded visitor is not collected.
#: Quoted verbatim, because it is the verification signal the product surfaces in the UI.
BLOCKED_SIGNAL = (
    "Data from this session isn't being collected by Microsoft Clarity due to your "
    "configured project settings."
)

#: Evidence: "Wait about 15 minutes for the changes to come into effect." Recorded as a
#: named value because the UI states it beside every IP blocking control.
IP_BLOCKLIST_PROPAGATION_MINUTES = 15

#: The role the evidence requires. "To set up IP exclusion, you need to be an
#: administrator for your project."
IP_BLOCKING_ROLE = "admin"

# --------------------------------------------------------------------------- #
# Roles
# --------------------------------------------------------------------------- #
#
# The specification names two project roles and no others: "Admin vs Team member roles".
# It also rules out identity-provider authentication in this workflow's own words:
# "Microsoft Clarity doesn't support authentication via your company's AAD instance".
# There is no SSO path here and :data:`AUTHENTICATION` says so in every response that
# reports a role.

ADMIN = "admin"
TEAM_MEMBER = "team_member"
PROJECT_ROLES = (ADMIN, TEAM_MEMBER)

# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #
#
# Two quoted windows and no third.

#: "Clarity retains recordings for 30 days from the time of recording."
ORDINARY_RETENTION_DAYS = 30

#: "Favorite recordings and randomly selected sample of recordings are retained for up to
#: 9 months."
FAVOURITE_RETENTION_DAYS = 270

#: 9 months is stated in months, not days, so the day count is an inference and is
#: recorded as one in :mod:`dsr.security_governance.session_consent_inferences`.
MONTHS_TO_DAYS = 30

# --------------------------------------------------------------------------- #
# Labels
# --------------------------------------------------------------------------- #
#
# Evidence: "labels (max 5 per recording)".

MAX_LABELS_PER_RECORDING = 5

# --------------------------------------------------------------------------- #
# Recording lifecycle
# --------------------------------------------------------------------------- #
#
# The states a recording passes through. ``blocked`` and ``scrubbed`` are outcomes the
# visitor never sees; they are recorded so a reviewer can tell a suppressed visitor from a
# visitor who never arrived.
RECORDED = "recorded"
BLOCKED = "blocked"
SCRUBBED = "scrubbed"

#: The evidence names two storage kinds under denial: "a unique ID per page view" and
#: "fragmented sessions". The second is an inference, recorded as one.
PER_PAGE_VIEW = "per_page_view"
FRAGMENTED = "fragmented"

RECORDING_STATES = (RECORDED, BLOCKED, SCRUBBED)

# --------------------------------------------------------------------------- #
# Share links
# --------------------------------------------------------------------------- #
#
# Evidence: "sharing (guest links expire, team links don't)".

GUEST = "guest"
TEAM = "team"
LINK_KINDS = (GUEST, TEAM)

#: Evidence: "You need to delete the entire project to delete user's data."
PROJECT_GRANULARITY = "project"

#: Evidence: "you can't delete or download specific recordings".
PER_RECORDING_DELETE = "per_recording"
PER_RECORDING_DOWNLOAD = "per_recording"
SINGLE_RECORDING_DELETE_SUPPORTED = False
SINGLE_RECORDING_DOWNLOAD_SUPPORTED = False

# --------------------------------------------------------------------------- #
# Segments
# --------------------------------------------------------------------------- #
#
# The Recordings tab can "filter to a segment". The specification does not enumerate the
# segments, so :data:`SEGMENT_DIMENSIONS` records the dimensions this build filters on
# and :mod:`dsr.security_governance.session_consent_inferences` records the derivation.

SEGMENT_DIMENSIONS = ("page_path", "region", "date")

# --------------------------------------------------------------------------- #
# Region-scoped enforcement
# --------------------------------------------------------------------------- #
#
# Evidence: "Starting October 31, 2025, Clarity begins enforcing consent signal
# requirements for page visits originating from the European Economic Area (EEA), United
# Kingdom (UK), and Switzerland (CH)."

CONSENT_ENFORCEMENT_START = "2025-10-31"

CONSENT_ENFORCED_REGIONS = ("EEA", "UK", "CH")

#: The date is a named value, and so is the fact that it is a boundary rather than a
#: permanent state. A visit before the date is not enforced by the vendor; a visit on or
#: after it is. Whether this product enforces on the same schedule is an inference.
ENFORCEMENT_IS_DATE_SCOPED = True

#: The vendor uses IP-based geolocation: "Clarity uses IP address-based geolocation to
#: determine user location. Consent should be obtained for users in the EEA, UK, and
#: Switzerland." No vendor is named, so none is named here.
GEOGRAPHY_SOURCE = (
    "visitor IP to geolocation provider [inferred - the API returns location.country/city "
    "but names no vendor]"
)

# --------------------------------------------------------------------------- #
# What this workflow refuses to build
# --------------------------------------------------------------------------- #
#
# The evidence states the limit directly, so the limit is a named value rather than a
# sentence in a docstring: "Microsoft Clarity doesn't support authentication via your
# company's AAD instance." No SSO path exists in this package, and every response that
# reports a role carries :data:`AUTHENTICATION`.

AUTHENTICATION = "email_invite"

#: The governance ceiling the specification names under Extensibility.
MAX_SESSIONS_PER_PROJECT_PER_DAY = 100_000