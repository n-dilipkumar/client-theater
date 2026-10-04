"""Every researched term WF-075 enforces against, with the evidence it came from.

The specification for this workflow is
``docs/research/digital-sales-room-workflows/wf/WF-075.md``, quoted in full in issue
150. Every value below is either quoted from that document or derived from a quote by
the arithmetic shown beside it. Nothing here is a house opinion.

The boundary this module exists to keep
--------------------------------------

WF-075 is a **read** path. The specification's data flow says every view writes a
``View`` row and updates a ``Visitor`` row, but this workflow *presents* those rows.
It does not own the write path that produces them. Recording that boundary here, at
the top of the vocabulary, is what stops the next agent from reading this issue as the
producer of view events.

The one sentence that governs every number
------------------------------------------

The specification is explicit that a viewer who hits two links is one visitor and two
views: "a viewer who hits two links in the same dataroom shows up once here, but twice
in ``papermark views list``". So the two counts below are named to keep them apart and
are never derived from one another.

The two counts, named apart
---------------------------

``Visitor`` is the persistent record, one row per email per dataroom, and it carries
``total_views``. A view is one event against one link. The evidence gives the
``Visitor`` schema verbatim: ``email``, ``verified: boolean``, ``dataroom_id``,
``invited_at``, ``total_views``, ``last_viewed_at``.

The geolocation vendor is not named
-----------------------------------

The specification's data sources mark it as an inference and quote themselves: "viewer
IP -> geolocation provider ``[inferred - the API returns location.country/city but
names no vendor]``". The marker is the specification's, not this build's, and it is
kept. No vendor name appears anywhere in this package.

Times are Unix milliseconds at the boundary
------------------------------------------

The user flow says the ``--since`` / ``--until`` bounds are in "Unix ms", and the
question the implementer notes ask is whether the same unit is used inside records.
It is, and this is the one place that is stated: every ``viewed_at``,
``downloaded_at`` and ``invited_at`` this package stores or reads is Unix
milliseconds. The ISO 8601 stamp beside it is for humans reading the log; the number
is the contract.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# Namespaced, because every feature shares one `records` table and `find()` matches
# on collection before it matches on anything else.

VISITOR_COLLECTION = "wf075_visitor"
VIEW_COLLECTION = "wf075_view"
AGENDA_COLLECTION = "wf075_page_dwell"

ALL_COLLECTIONS = (VISITOR_COLLECTION, VIEW_COLLECTION, AGENDA_COLLECTION)

# --------------------------------------------------------------------------- #
# The record shapes the specification quotes verbatim
# --------------------------------------------------------------------------- #
#
# `ViewAnalytics`: `viewer_email`, `viewed_at`,
# `page_durations[{page_number, duration_seconds}]`, `total_duration_seconds`,
# `location{country, city}`, `client{browser, os, device}`.
#
# `Visitor`: `email`, `verified: boolean`, `dataroom_id`, `invited_at`,
# `total_views`, `last_viewed_at`.
#
# Both shapes are reproduced here as field tuples so a projection and a test assert
# the same list. The names are the vendor's, so a caller can tell a Papermark
# record from one of this product's without a lookup table.

VIEWER_EMAIL = "viewer_email"
VIEWED_AT = "viewed_at"
PAGE_DURATIONS = "page_durations"
TOTAL_DURATION_SECONDS = "total_duration_seconds"
LOCATION = "location"
CLIENT = "client"

VIEW_ANALYTICS_FIELDS = (
    VIEWER_EMAIL,
    VIEWED_AT,
    PAGE_DURATIONS,
    TOTAL_DURATION_SECONDS,
    LOCATION,
    CLIENT,
)

EMAIL = "email"
VERIFIED = "verified"
DATAROOM_ID = "dataroom_id"
INVITED_AT = "invited_at"
TOTAL_VIEWS = "total_views"
LAST_VIEWED_AT = "last_viewed_at"

VISITOR_FIELDS = (EMAIL, VERIFIED, DATAROOM_ID, INVITED_AT, TOTAL_VIEWS, LAST_VIEWED_AT)

#: The nested shapes, as the evidence quotes them.
LOCATION_FIELDS = ("country", "city")
CLIENT_FIELDS = ("browser", "os", "device")

#: One entry of ``page_durations``. The evidence names both keys.
PAGE_DURATION_FIELDS = ("page_number", "duration_seconds")

# --------------------------------------------------------------------------- #
# The view record: what a single view event carries
# --------------------------------------------------------------------------- #
#
# The specification's data flow names these fields on the `View` row:
# "`View` row (link, document/dataroom, `viewer_email`, `view_type`, `viewed_at`,
# `downloaded_at`, `download_type`)".

VIEW_TYPE = "view_type"
DOWNLOADED_AT = "downloaded_at"
DOWNLOAD_TYPE = "download_type"

VIEW_FIELDS = (
    VIEWER_EMAIL,
    VIEW_TYPE,
    VIEWED_AT,
    DOWNLOADED_AT,
    DOWNLOAD_TYPE,
)

#: The three view types. The evidence names ``view_type`` but not its values, so this
#: is a derivation and it is recorded in :mod:`dsr.security_governance.engagement_rules`
#: as DERIVED_VIEW_TYPES.
VIEW_TYPE_DOCUMENT = "document"
VIEW_TYPE_DATAROOM = "dataroom"
VIEW_TYPE_LINK = "link"

VIEW_TYPES = (VIEW_TYPE_DOCUMENT, VIEW_TYPE_DATAROOM, VIEW_TYPE_LINK)

#: The download types. Same rule: the evidence names the field, not the values.
DOWNLOAD_TYPE_NONE = "none"
DOWNLOAD_TYPE_PDF = "pdf"
DOWNLOAD_TYPE_ORIGINAL = "original"

DOWNLOAD_TYPES = (DOWNLOAD_TYPE_NONE, DOWNLOAD_TYPE_PDF, DOWNLOAD_TYPE_ORIGINAL)

#: A view with no ``downloaded_at`` has this download type. Derived, and asserted by
#: the tests, because "no download happened" and "a download happened of an unknown
#: kind" must never render the same on the page.
NO_DOWNLOAD = "not_downloaded"

# --------------------------------------------------------------------------- #
# Verification: proven, not typed
# --------------------------------------------------------------------------- #
#
# The user flow's fifth step reads the flag "to confirm the identity was actually
# proven (not merely typed in)". So the vocabulary has three states and not two, and
# the third is the one that matters:
#
# * ``verified``: the identity was proven.
# * ``unverified``: a typed address, no proof.
# * ``unknown``: no answer was ever recorded for this row.
#
# A boolean that defaults to true on a typed address does not answer the question the
# page asks. The stored field stays a boolean where the evidence says it is a boolean
# (``verified: boolean``), and ``unknown`` is this build's name for "the field is
# absent", which is a real state a freshly written row can be in.

VERIFIED_TRUE = "verified"
VERIFIED_FALSE = "unverified"
VERIFIED_UNKNOWN = "unknown"

VERIFICATION_STATES = (VERIFIED_TRUE, VERIFIED_FALSE, VERIFIED_UNKNOWN)

#: The default. False, and the reason is the whole point of the three states above: a
#: typed email address is not a proven one, so a row with no recorded proof reads as
#: unverified rather than as verified.
DEFAULT_VERIFIED = False

# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
#
# The specification's second user-flow step pulls aggregate stats with `--since` /
# `--until` bounds in Unix ms. These are the fields an aggregate carries. The evidence
# names "total views, unique visitors, time spent, per-page engagement", and the four
# keys below are those four phrases.

TOTAL_VIEWS_FIELD = "total_views"
UNIQUE_VISITORS_FIELD = "unique_visitors"
TIME_SPENT_SECONDS_FIELD = "time_spent_seconds"
PER_PAGE_FIELD = "per_page"

#: Seconds in a minute, for rendering a dwell time as minutes and seconds. Arithmetic,
#: not a product decision.
SECONDS_PER_MINUTE = 60

#: How many rows one aggregate or one list returns before it is paged. The
#: specification calls the list endpoints "paginated" and the per-link view list
#: "cursor-paginated", so a bound is a researched requirement rather than a limit this
#: build invented. The number is derived and recorded as DERIVED_PAGE_SIZE.
DEFAULT_PAGE_SIZE = 50

#: The largest page a caller may ask for. Derived, and recorded as
#: DERIVED_PAGE_SIZE. A caller cannot ask the poller path for the whole table.
MAX_PAGE_SIZE = 200

# --------------------------------------------------------------------------- #
# The analytics cache
# --------------------------------------------------------------------------- #
#
# The specification says analytics are "cheap (cached aggregates)" and "Cache the
# response if you're polling." So an aggregate is cached, and the cache is what the
# note requires rather than an optimisation this build chose.
#
# The seconds below are derived: the automation note says "polling every minute or two
# is fine", so a cache that expires sooner than the fastest documented poll would
# defeat the point of caching at all. Recorded as DERIVED_CACHE_SECONDS.

DEFAULT_CACHE_SECONDS = 60

#: The field every aggregate response carries, naming the instant the numbers were
#: computed. A poller needs to know whether it is reading a fresh answer or a cached
#: one, and a response that does not say cannot be polled honestly.
COMPUTED_AT = "computed_at"

#: True when this response came out of the cache rather than off the records. The
#: poller path in the specification is the reason this exists.
CACHED_FIELD = "cached"

# --------------------------------------------------------------------------- #
# Served to the frontend
# --------------------------------------------------------------------------- #

COLLECTIONS_FIELD = "collections"
TIME_UNIT_FIELD = "time_unit"
TIME_UNIT_VALUE = "unix_ms"
VERIFICATION_FIELD = "verification"
