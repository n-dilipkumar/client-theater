"""Every researched term WF-085 enforces against, with the evidence it came from.

The specification for this workflow is
``docs/research/digital-sales-room-workflows/wf/WF-085.md``, quoted in full in issue
200. Every value below is either quoted from that document or derived from a quote by
the arithmetic shown beside it. Nothing here is a house opinion, and every derivation
is recorded with its alternative in
:mod:`dsr.security_governance.privacy_inferences`.

The five controls the specification says an open-source room must ship
------------------------------------------------------------------------

It names them itself, in the order this module is organised in:

1. "(1) a documented residency region per deployment" - :data:`REGIONS`.
2. "(2) hard retention limits with no longer-lived side channels" -
   :data:`RETENTION_CLASSES` and :data:`RETENTION_SCOPE`.
3. "(3) a consent gate that fails **closed** (deny => unique ID per page view, no
   cookies) rather than merely degrading" - :data:`CONSENT_*` and the gate in
   :mod:`dsr.security_governance.privacy_rules`.
4. "(4) a DSAR path that can delete an identifiable visitor's records" -
   :data:`PERSONAL_DATA` and the erasure path in the engine.
5. "(5) role separation, since blocking changes are admin-only" - the vocabulary
   here names no role at all. The role comes from :mod:`dsr.permissions`, which is the
   repository's own surface, and inventing a second one is the mistake the
   specification's note warns about.

What this module will not hold
------------------------------

**No certification.** The evidence quotes a vendor page claiming "SOC 2 Type II, ISO
27001, ISO 27701, GDPR, and the EU AI Act". That is a claim about a vendor. It is kept
in :data:`VENDOR_CLAIMS` as an unverified claim with the wording it was quoted with,
and nothing in this package renders a badge, a tick or a "compliant" flag.

**No vendor entity as this deployment's controller.** The evidence says "Clarity
customers in the EU are contracting with **Microsoft Ireland Operations Limited
(MIOL)**". That describes the vendor's customers, not this room. So
:data:`TRANSFER_MECHANISMS` names *mechanisms* only, and the entity is not stored
anywhere.

**No fixed enforcement date.** The evidence gives one: "Starting October 31, 2025,
Clarity begins enforcing consent signal requirements for page visits originating from
the EEA, UK, and Switzerland". It is recorded in :data:`VENDOR_CLAIMS` as provenance and
nothing in the gate reads a clock to decide whether to enforce. The derivation is
``DERIVED_CONSENT_GATE_IS_A_REGION_LIST_NOT_A_DATE``.

**No DNT.** The evidence says "Clarity doesn't currently respond to browser DNT
signals", so :data:`UNSUPPORTED_SIGNALS` names it and the gate ignores it.

The retention classes, and the mapping this repository had to choose
------------------------------------------------------------------

The evidence gives three windows in one sentence: recordings "up to 30 days from the
time of recording", with favourites and a random sample up to 9 months; heatmaps up to
9 months. It does not say which record type carries which class, because the record
types are the vendor's and this repository's are different. The mapping is
:data:`RETENTION_SCOPE`, and it is a derivation, recorded as
``DERIVED_RETENTION_CLASS_MAPPING``.

The nine months is written as 273 days below, and the arithmetic is stated there. It is
an approximation and it is labelled one: the evidence says "up to 9 months" and not "273
days", and a window that is three days short would purge a record the evidence says may
be kept for nine calendar months.
"""

from __future__ import annotations

from dsr.security_governance import engagement as wf075

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# Namespaced, because every feature shares one `records` table and `find()` matches on
# collection before it matches on anything else.

RESIDENCY_COLLECTION = "wf085_residency"
CONSENT_COLLECTION = "wf085_consent"
DSAR_COLLECTION = "wf085_dsar_request"
RETENTION_RUN_COLLECTION = "wf085_retention_run"

ALL_COLLECTIONS = (
    RESIDENCY_COLLECTION,
    CONSENT_COLLECTION,
    DSAR_COLLECTION,
    RETENTION_RUN_COLLECTION,
)

# --------------------------------------------------------------------------- #
# Residency: (1) a documented residency region per deployment
# --------------------------------------------------------------------------- #
#
# The evidence names the control - "Region-based data residency control" - and no
# region. So the region list below is a derivation, and the one that matters is the
# jurisdiction each region belongs to, because that is what the consent gate reads and
# what a legal question is actually about. A region is a deployment choice; a
# jurisdiction is what a data-protection answer has to name.

JURISDICTION_EEA = "eea"
JURISDICTION_UK = "uk"
JURISDICTION_CH = "ch"
JURISDICTION_US = "us"
JURISDICTION_CA = "ca"
JURISDICTION_APAC = "apac"

JURISDICTIONS: tuple[str, ...] = (
    JURISDICTION_EEA,
    JURISDICTION_UK,
    JURISDICTION_CH,
    JURISDICTION_US,
    JURISDICTION_CA,
    JURISDICTION_APAC,
)

#: Human labels. A label is not a legal determination and is rendered as prose.
JURISDICTION_LABELS: dict[str, str] = {
    JURISDICTION_EEA: "European Economic Area",
    JURISDICTION_UK: "United Kingdom",
    JURISDICTION_CH: "Switzerland",
    JURISDICTION_US: "United States",
    JURISDICTION_CA: "Canada",
    JURISDICTION_APAC: "Asia-Pacific",
}

#: The regions a deployment may be pinned to, each naming the jurisdiction it sits in.
#:
#: These are deployment region identifiers with a jurisdiction attached. Nothing here
#: says a named cloud provider operates them, and no provider is named anywhere in this
#: package, because the evidence's only provider sentence is about where the *vendor*
#: stores its own data.
REGIONS: tuple[dict[str, str], ...] = (
    {"id": "eu-west", "label": "EU West", "jurisdiction": JURISDICTION_EEA},
    {"id": "eu-central", "label": "EU Central", "jurisdiction": JURISDICTION_EEA},
    {"id": "eu-north", "label": "EU North", "jurisdiction": JURISDICTION_EEA},
    {"id": "uk-south", "label": "UK South", "jurisdiction": JURISDICTION_UK},
    {"id": "ch-north", "label": "CH North", "jurisdiction": JURISDICTION_CH},
    {"id": "us-east", "label": "US East", "jurisdiction": JURISDICTION_US},
    {"id": "us-west", "label": "US West", "jurisdiction": JURISDICTION_US},
    {"id": "ca-central", "label": "CA Central", "jurisdiction": JURISDICTION_CA},
    {"id": "ap-southeast", "label": "AP Southeast", "jurisdiction": JURISDICTION_APAC},
)

REGION_IDS: tuple[str, ...] = tuple(region["id"] for region in REGIONS)
REGION_LABELS: dict[str, str] = {region["id"]: region["label"] for region in REGIONS}
REGION_JURISDICTIONS: dict[str, str] = {region["id"]: region["jurisdiction"] for region in REGIONS}

#: The key this workflow writes on every record it touches, and the one it re-stamps
#: when the region changes. Ordinary JSON in ``records.data``, so a team that wants
#: ``data_residency`` beside it needs no migration and no coordination with anyone.
RESIDENCY_FIELD = "residency_region"

# --------------------------------------------------------------------------- #
# Cross-border transfer
# --------------------------------------------------------------------------- #

TRANSFER_NONE = "none"
TRANSFER_SCC = "standard_contractual_clauses"

TRANSFER_MECHANISMS: tuple[str, ...] = (TRANSFER_NONE, TRANSFER_SCC)

TRANSFER_LABELS: dict[str, str] = {
    TRANSFER_NONE: "No cross-border transfer declared",
    TRANSFER_SCC: "Standard contractual clauses",
}

#: Why the mechanism list names mechanisms and not entities. The evidence names an
#: entity for the vendor's own EU customers; this workflow stores no entity, because
#: storing one would assert a controller this repository is not.
TRANSFER_MECHANISM_NOTE = (
    "The mechanism names a lawful-transfer instrument. It is not a statement that any "
    "transfer has been assessed, and no contracting entity is stored, because the "
    "researched entity describes a vendor's customers and not this deployment."
)

# --------------------------------------------------------------------------- #
# Retention: (2) hard limits with no longer-lived side channels
# --------------------------------------------------------------------------- #

CLASS_SESSION_RECORDING = "session_recording"
CLASS_FAVOURITE_OR_SAMPLE = "favourite_or_sample"
CLASS_HEATMAP = "heatmap"

RETENTION_CLASSES: tuple[str, ...] = (
    CLASS_SESSION_RECORDING,
    CLASS_FAVOURITE_OR_SAMPLE,
    CLASS_HEATMAP,
)

#: Days in a calendar month under the arithmetic this workflow uses: 365/12 = 30.4167,
#: rounded to 30 for the short classes. The nine-month figure is the one that matters
#: and it is an approximation, so it is recorded as an approximation below rather than
#: presented as a researched number.
DAYS_PER_MONTH = 30
DAYS_PER_NINE_MONTHS = 273

#: The researched windows, in days. Each carries the sentence it came from.
#:
#: The evidence is "The data is retained for the webmaster's consumption up to 30 days
#: from the time of recording" for the recording class, and "favourites and a random
#: sample up to 9 months; heatmaps up to 9 months" for the other two. Nine months is
#: recorded as 273 days because 9 x 30 = 270 under :data:`DAYS_PER_MONTH` and 273 is the
#: calendar figure; 273 is the longer of the two, so the approximation can only ever
#: keep a record slightly past the researched window and never purge one early.
RETENTION_DAYS: dict[str, int] = {
    CLASS_SESSION_RECORDING: 30,
    CLASS_FAVOURITE_OR_SAMPLE: DAYS_PER_NINE_MONTHS,
    CLASS_HEATMAP: DAYS_PER_NINE_MONTHS,
}

RETENTION_CLASS_LABELS: dict[str, str] = {
    CLASS_SESSION_RECORDING: "Session recordings",
    CLASS_FAVOURITE_OR_SAMPLE: "Favourites and the random sample",
    CLASS_HEATMAP: "Heatmaps",
}

RETENTION_EVIDENCE: dict[str, str] = {
    CLASS_SESSION_RECORDING: ('recordings "up to 30 days from the time of recording"'),
    CLASS_FAVOURITE_OR_SAMPLE: "favourites and a random sample up to 9 months",
    CLASS_HEATMAP: "heatmaps up to 9 months",
}

#: The ceiling a configured window can never exceed.
#:
#: The researched figures are maxima, not targets: the evidence says "up to". So a
#: configured window may be shorter - a deployment can promise less than the ceiling -
#: and a request for a longer one is refused rather than clamped silently. Clamping
#: would leave an operator believing a longer window was agreed when it was not.
MAX_RETENTION_DAYS: dict[str, int] = dict(RETENTION_DAYS)

#: Which collection carries which class. This is the mapping the specification left open.
#:
#: * ``wf075_view`` is a viewing session, so it takes the 30-day recording class. It is
#:   the strictest window in the table and a session is the row that most closely
#:   matches a recording.
#: * ``wf075_visitor`` is the persistent, identifiable row, so it takes the 9-month
#:   class with the sample. A shorter window would empty the viewers list and the DSAR
#:   surface together, and the DSAR surface is what this workflow has to be able to
#:   answer.
#: * The consent record and the DSAR request take the 9-month class because both are
#:   compliance evidence: consent is relied on for as long as the room keeps engaging
#:   that visitor, and a DSAR request is the evidence the erasure happened.
#: * The retention run record and the residency record carry no personal data and are
#:   deliberately not in this table. A record that is not in the table is not aged by
#:   this workflow, and :func:`~dsr.security_governance.privacy_rules.unmapped_collections`
#:   names every live collection the table does not cover, so the gap is visible rather
#:     than assumed away. That is the "no longer-lived side channels" requirement: a
#:   side channel is a collection holding personal data that nothing ages.
RETENTION_SCOPE: dict[str, str] = {
    wf075.VIEW_COLLECTION: CLASS_SESSION_RECORDING,
    wf075.VISITOR_COLLECTION: CLASS_FAVOURITE_OR_SAMPLE,
    CONSENT_COLLECTION: CLASS_FAVOURITE_OR_SAMPLE,
    DSAR_COLLECTION: CLASS_FAVOURITE_OR_SAMPLE,
}

#: The field on a stored record that says when the record was made.
#:
#: The engagement rows carry Unix milliseconds and this workflow's own rows carry an
#: ISO 8601 UTC instant, because the two collections were written by two different
#: workflows. :func:`~dsr.security_governance.privacy_rules.coerce_instant` accepts
#: both, so the retention clock reads one format per collection and compares them
#: correctly. Two fields rather than one guess is the honest answer here.
INSTANT_FIELDS: dict[str, str] = {
    wf075.VIEW_COLLECTION: "viewed_at",
    wf075.VISITOR_COLLECTION: "last_viewed_at",
    CONSENT_COLLECTION: "recorded_at",
    DSAR_COLLECTION: "opened_at",
}

#: A record whose instant field is absent or unreadable cannot be aged.
#:
#: It is reported, not deleted and not treated as fresh. Treating an undated record as
#: fresh would let an unreadable row outlive its window forever, which is the exact
#: side channel the specification forbids; deleting it would destroy data because a
#: clock field was missing.
UNDATED_POLICY = "reported_not_aged"

#: The unit every window in this workflow is expressed in.
WINDOW_UNIT = "days"

# --------------------------------------------------------------------------- #
# Personal data: the surface a DSAR has to reach
# --------------------------------------------------------------------------- #
#
# The evidence names the surface twice. From the API list: "the personal-data surface
# subject to a DSAR (viewer email, country/city, client fingerprint, per-page dwell)" and
# "the identifiable-buyer surface (`email`, `verified`, `total_views`)". Those are the
# two collections, and the fields below are the ones those sentences name.
#
# "client fingerprint" is read as the three parsed parts the engagement workflow stores,
# which is what its vocabulary records verbatim as `client{browser, os, device}`. No
# opaque fingerprint string is invented, because none is stored and a DSAR plan that
# listed a field no record carries could not be fulfilled.
PERSONAL_DATA: dict[str, tuple[str, ...]] = {
    wf075.VIEW_COLLECTION: (
        "viewer_email",
        "location.country",
        "location.city",
        "client.browser",
        "client.os",
        "client.device",
        "page_durations",
        "viewed_at",
    ),
    wf075.VISITOR_COLLECTION: (
        "email",
        "verified",
        "total_views",
        "invited_at",
        "last_viewed_at",
    ),
    CONSENT_COLLECTION: (
        "subject",
        "region",
        "recorded_at",
    ),
    DSAR_COLLECTION: (
        "subject",
        "opened_at",
    ),
}

#: The fields a subject is matched on. Discovery is by address, because that is the only
#: identifier the evidence names: `email` on the visitor and `viewer_email` on the view.
SUBJECT_FIELDS: dict[str, tuple[str, ...]] = {
    wf075.VIEW_COLLECTION: ("viewer_email",),
    wf075.VISITOR_COLLECTION: ("email",),
    CONSENT_COLLECTION: ("subject",),
    DSAR_COLLECTION: ("subject",),
}

#: Screen text is suppressed by default. Recorded because a legal answer needs it and
#: because it is the reason a view event in this repository is not a transcript.
SCREEN_TEXT_DEFAULT = "suppressed"

# --------------------------------------------------------------------------- #
# Consent: (3) a gate that fails closed
# --------------------------------------------------------------------------- #

#: The evidence's jurisdiction list, verbatim: "explicit user consent is required
#: before placing cookies on their devices" for "the European Economic Area (EEA), UK,
#: and Switzerland".
CONSENT_JURISDICTIONS: tuple[str, ...] = (
    JURISDICTION_EEA,
    JURISDICTION_UK,
    JURISDICTION_CH,
)

CONSENT_ENFORCEMENT_QUOTE = (
    "Why does Clarity require explicit consent in the European Economic Area (EEA), UK, "
    "and Switzerland? To comply with local regulations explicit user consent is required "
    "before placing cookies on their devices."
)

#: The only value that grants. Everything that is not this exact word denies.
#:
#: Fail-closed is a property of the vocabulary, not of a branch somewhere: there is one
#: grant word and the gate compares against it, so a new signal cannot be added by
#: accident and an unreadable one cannot slip through.
CONSENT_GRANTED = "granted"
CONSENT_DENIED = "denied"

CONSENT_DECISIONS: tuple[str, ...] = (CONSENT_GRANTED, CONSENT_DENIED)

#: Opt-out signals the evidence says are honoured automatically: "Clarity supports
#: Global Privacy Control (GPC)" and the DAA opt-out list. They can deny and they cannot
#: grant - "gpc is false" means nobody opted out, not that anybody consented.
OPT_OUT_SIGNALS: tuple[str, ...] = ("gpc", "daa")

#: The evidence records DNT as unsupported: "Clarity doesn't currently respond to
#: browser DNT signals." The gate therefore ignores it, and
#: :func:`~dsr.security_governance.privacy_rules.gate` does not read it.
UNSUPPORTED_SIGNALS: tuple[str, ...] = ("dnt",)

#: The two outcomes, and the deny one is described by the evidence: "If consent is not
#: granted, Clarity assigns a unique ID per page view and does not use cookies to
#: persist session data."
GATE_TRACK = "track"
GATE_DENY = "deny"

GATE_OUTCOMES: tuple[str, ...] = (GATE_TRACK, GATE_DENY)

#: What a deny does, as the two booleans a page needs. ``cookies`` is False rather than
#: absent so a client cannot read a missing key as "unknown".
DENY_EFFECT: dict[str, object] = {
    "outcome": GATE_DENY,
    "identifier": "unique_per_page_view",
    "persistent_identifier": False,
    "cookies": False,
    "tracking": False,
    "recording": False,
}

GRANT_EFFECT: dict[str, object] = {
    "outcome": GATE_TRACK,
    "identifier": "persistent",
    "persistent_identifier": True,
    "cookies": True,
    "tracking": True,
    "recording": True,
}

#: A region the gate does not enforce. Not a third outcome: the gate has two outcomes
#: and an unenforced region is reported as ``not_required`` alongside them, so a client
#: never has to infer it from a missing field.
CONSENT_NOT_REQUIRED = "not_required"

#: Revocation is immediate: ``clarity('consent', false)`` "clears the Clarity cookies
#: from the user's browser and prevent further tracking until new consent is granted."
REVOKED = "revoked"
ACTIVE = "active"

CONSENT_STATES: tuple[str, ...] = (ACTIVE, REVOKED, CONSENT_DENIED)

# --------------------------------------------------------------------------- #
# The DSAR: (4) per-subject deletion, not per-project
# --------------------------------------------------------------------------- #
#
# The evidence names the vendor's limitation as the hard part: "You need to delete the
# entire project to delete user's data." So the request states are about a subject and a
# count, and there is no state meaning "the whole room went".

DSAR_OPENED = "opened"
DSAR_FULFILLED = "fulfilled"
DSAR_PARTIAL = "partial"
DSAR_NOTHING_FOUND = "nothing_found"

DSAR_STATES: tuple[str, ...] = (DSAR_OPENED, DSAR_FULFILLED, DSAR_PARTIAL, DSAR_NOTHING_FOUND)

#: The field a request response carries its state vocabulary on, so a page renders the
#: words from the server rather than from its own copy.
DSAR_STATES_FIELD = "dsar_states"

#: Days a request is given before it must be fulfilled.
#:
#: The evidence says "corporate privacy-request tickets" are a data source and names no
#: deadline, so this number is a derived default rather than a researched figure. Thirty
#: days is the interval the response reports in ``deadline_at`` so an operator can see
#: the window it is working to instead of assuming one.
DSAR_WINDOW_DAYS = 30

#: Why a row survived an erasure. The audit trail is the product guarantee this
#: repository is built on, and a row describing an erasure is the compliance evidence
#: an erasure is judged by. So it is retained, counted, and named - never silently
#: dropped and never quietly forgotten.
RESIDUE_AUDIT_TRAIL = "audit_trail"

#: The erasure request itself. Its own row names the subject and it is the evidence that
#: the erasure happened, so it is kept for the same reason the audit rows are. Erasing it
#: would delete the record of the act while leaving the act's effects, which is a worse
#: answer than reporting it as residue.
RESIDUE_ERASURE_RECORD = "erasure_record"

#: The JSONL audit mirror. The audited wrapper writes it and a feature cannot reach it,
#: because ``dsr/db/audited.py`` is a shared file this workflow may not edit. Recorded
#: so the limitation is stated rather than implied by its absence.
RESIDUE_AUDIT_MIRROR = "audit_mirror"

RESIDUE_REASONS: tuple[str, ...] = (
    RESIDUE_AUDIT_TRAIL,
    RESIDUE_ERASURE_RECORD,
    RESIDUE_AUDIT_MIRROR,
)

RESIDUE_NOTES: dict[str, str] = {
    RESIDUE_AUDIT_TRAIL: (
        "The audit trail keeps before and after snapshots of every row this erasure "
        "removed. Removing them would falsify the log, so they stay and the erasure "
        "itself is recorded against them."
    ),
    RESIDUE_ERASURE_RECORD: (
        "The data-subject request row names the subject and is the evidence that the "
        "erasure happened, so the request that performed the erasure keeps itself."
    ),
    RESIDUE_AUDIT_MIRROR: (
        "The JSONL audit mirror holds the same rows on disk. It is written by the "
        "audited database wrapper and no feature can shorten or reach it."
    ),
}

# --------------------------------------------------------------------------- #
# Vendor claims, kept as claims
# --------------------------------------------------------------------------- #

#: Quoted claims about a vendor. Recorded so the research is not lost, and carried with
#: ``verified: False`` so nothing in this product can render them as a fact about
#: itself. The second entry is a deployment-enforcement date that this repository does
#: not implement; the third is a supported-signal statement this repository treats as a
#: reason to ignore DNT.
VENDOR_CLAIMS: tuple[dict[str, object], ...] = (
    {
        "claim": "SOC 2 Type II, ISO 27001, ISO 27701, GDPR, and the EU AI Act",
        "subject": "a vendor",
        "verified": False,
        "note": (
            "A marketing claim on a vendor's own page. This repository holds no such "
            "certificate and renders no badge for it."
        ),
    },
    {
        "claim": (
            "Starting October 31, 2025, Clarity begins enforcing consent signal "
            "requirements for page visits originating from the EEA, UK, and Switzerland"
        ),
        "subject": "a vendor's enforcement date",
        "verified": False,
        "note": (
            "A vendor deadline. This repository gates on a configured region list "
            "instead; see DERIVED_CONSENT_GATE_IS_A_REGION_LIST_NOT_A_DATE."
        ),
    },
    {
        "claim": "Clarity doesn't currently respond to browser DNT signals.",
        "subject": "a vendor's supported signals",
        "verified": False,
        "note": (
            "The reason DNT is not a consent signal here. Recorded as unsupported so "
            "the absence is a decision and not an oversight."
        ),
    },
)

# --------------------------------------------------------------------------- #
# Served to the frontend
# --------------------------------------------------------------------------- #

COLLECTIONS_FIELD = "collections"
WINDOW_UNIT_FIELD = "window_unit"
RESIDENCY_FIELD_NAME = "residency"
TRANSFER_MECHANISM_FIELD = "transfer_mechanism"
INSTANT_FORMAT_FIELD = "instant_formats"
#: The two accepted instant encodings, served so a page states the unit rather than
#: guessing it from a sample value.
INSTANT_FORMATS: tuple[str, ...] = ("iso8601_utc", "unix_ms")
