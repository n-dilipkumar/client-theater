"""WF-020 - Extract DSR viewing sessions (dwell time + geography) for BI.

Domain layer for the researched workflow. It owns the rules and nothing else:
the extraction window, the shape of a landed viewing session, the merge that an
incremental ``modifiedAt`` sweep implies, the rollups the BI layer is described as
computing, and the machine-readable contract an ETL job is generated from.

Sourced behaviour
-----------------
Everything below marked *sourced* is taken from
``docs/research/digital-sales-room-workflows/wf/WF-020.md`` and its five cited
Seismic sources. The researched API field names are camelCase; this codebase is
snake_case throughout, so ``digitalSalesRoomId`` is stored as
``digital_sales_room_id``. The vocabulary is unchanged - only the casing is - and
:func:`contract` publishes the mapping, so the join between the two halves of an
extraction is auditable rather than folklore.

* **Incremental extraction, ``modifiedAt`` preferred.** "modifiedAtStartTime /
  EndTime (preferred for all use cases) - This is a data modifiedAt time and has
  no 'business' meaning. It is meant entirely for machines to know what rows may
  have changed so that it can pull the updates and merge them into existing data
  sets." So the default filter kind is ``modified``, and the alternative
  ``sessionStartedAtTime`` / ``sessionEndedAtTime`` pair is supported but never
  silently substituted for it.
* **Merge, not append.** That same sentence is the reason a re-landed row
  *updates* the row already in the store. An append-only landing zone would
  double-count a session every time the vendor corrected it, which is exactly
  what "merge them into existing data sets" rules out.
* **A 24 hour refresh SLA.** "The data that is available through our reporting
  APIs is updated no less than every 24 hours", and the APIs are "not designed to
  be used in high-frequency, interactive use cases." The sweep therefore pages by
  the SLA, and a window narrower than the SLA is annotated rather than refused:
  the research says the data is not fresher, not that a narrower pull is illegal.
* **Tab-level granularity.** "Each row represents a single session in a DSR link
  from a single user in a single browser tab." Every rollup here therefore counts
  ``sessions`` (rows) and ``visitors`` (distinct viewers) separately. Treating the
  row count as a visitor count is the one mistake this vocabulary exists to
  prevent.
* **The room join.** "Join each row to GET /reporting/v2/digitalSalesRooms on
  digitalSalesRoomId to resolve the room." Both sides store that identifier as
  ``digital_sales_room_id``, so the join is a same-name equality through the
  dynamic index, and it is resolved on read so a renamed room never leaves a
  stale label inside a session row.
* **Two output formats.** ``Accept: application/json`` or ``Accept: text/csv``,
  the latter for "flat-file loads" into a lake.
* **A published dictionary and star schema.** Seismic publishes a "Data
  Dictionary" and a "Recommended Schema"; "Most modern business intelligence
  platforms require a form of star schema." :func:`contract` publishes the
  documented fields, and :data:`STAR_SCHEMA` publishes the shape this feature
  lands.

Design inferences
-----------------
These are **not** in the research. They are decisions, recorded here so a
reviewer can disagree with them.

1. **This feature does not call Seismic.** The researched user flow has the pull
   running "from an ETL job", and the product's own step is to "land rows in a
   data warehouse / lake and compute ... in BI". So the feature is the landing
   zone, the extraction contract, and the BI read surface. An outbound
   ``urllib`` call to ``api.seismic.com`` with a bearer token would add a
   network dependency, a credential store with no consumer, and a code path no
   test could exercise. :func:`contract` publishes the request an ETL job must
   make instead, and ``POST /api/wf-020/extract`` receives the rows it pulls.
2. **No credential is stored.** Step 1 of the flow is "Generate a Seismic JWT /
   API token in the Seismic admin console", and the header requirement is
   ``Authorization: Bearer <JWT>`` on every call. Because this feature makes no
   call, storing a token here would be a secret with nothing reading it. The
   contract publishes the header the job must send; the token stays in the ETL
   job's own credential store.
3. **The window is half-open, ``[start, end)``.** The research does not state
   boundary inclusivity. Back-to-back nightly windows with both ends closed
   double-count one instant and with both ends open lose it; a half-open interval
   is the only choice that neither repeats nor drops. The merge above makes a
   repeat harmless anyway, so the rule is belt and braces.
4. **One filter kind per window.** The flow offers ``modifiedAt`` "(or
   ``sessionStartedAtTime`` ...)". Two independent filter pairs combined have no
   documented semantics, so mixing them is a :class:`WindowError` rather than a
   silent precedence rule.
5. **Request windows demand an explicit offset; row timestamps do not.** A
   machine asking for a window must be explicit, so a naive bound is rejected.
   A *row* that arrives with a naive timestamp is landed, read as UTC, and
   flagged ``timestamp_assumed_utc`` - dropping it would contradict "land rows".
6. **The session key is derived, because the API publishes no session id.** The
   documented field list carries no session identifier, and "merge them into
   existing data sets" cannot be implemented without one, so the key is
   ``(digital_sales_room_id, viewer, session_started_at)``. Two genuine sessions
   that share a user, a room and a start second would collide; that limitation is
   stated here rather than hidden, and it disappears if the vendor ever returns
   an explicit id (``:func:`session_key` prefers one the moment it sees it).
7. **A row outside the declared window is landed and flagged.** The job already
   filtered, so dropping the row would hide a vendor-side paging bug; landing it
   under ``outside_window`` keeps the evidence.
8. **A row is never dropped for bad data.** Duration disagreements, reversed
   timestamps, missing geography and unknown rooms all become quality flags.
   Rows are only rejected when they are not objects at all.
9. **Raw IPs are not returned by the read surfaces.** The research's Appendix B
   records that "the sources read do not document a consent gate, IP
   anonymisation, or a data-retention policy for DSR engagement events". The rows
   are stored exactly as the API returned them - an ETL must not mangle its input
   - but the aggregate and list endpoints omit ``ip_address`` unless the caller
   asks for it, and a ``redact_ip`` option on the landing zone stores a masked
   form instead. That is a decision about an acknowledged gap, not a sourced
   rule.
10. **A row that re-lands byte-identical is not written again.** ``modifiedAt``
    is documented as "meant entirely for machines to know what rows *may* have
    changed"; a row that comes back unchanged should not churn the audit log.
11. **The watermark is derived from the runs, never stored as a singleton.** A
    stored high-water mark is state that can drift from the runs that advanced
    it; :func:`watermark` reads the runs instead.

Storage
-------
Every write goes through :class:`~dsr.store.RecordStore`, a facade over
:class:`~dsr.db.audited.AuditedDatabase`. There is no other write path here and
no migration: every field is arbitrary JSON in ``records.data``, so a team can
add one without coordinating with anyone. Filtering goes through ``find()``,
which resolves dotted JSON paths through the dynamic index.

Every method that writes takes a **required** ``source``. The HTTP layer builds it
from ``router.prefix``; this module never hard-codes a path it might stop
serving, which is the defect the port brief names.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Collections
#
# Ordinary strings, not schema. They are conventions this feature uses; the
# generic /api/records endpoints accept any collection name.
# --------------------------------------------------------------------------- #

SESSIONS = "dsr_viewing_session"
ROOMS = "dsr_room_inventory"
RUNS = "dsr_reporting_run"

# --------------------------------------------------------------------------- #
# Sourced constants
# --------------------------------------------------------------------------- #

#: "The data that is available through our reporting APIs is updated no less
#: than every 24 hours." The sweep pages by this, and it is the interval at which
#: the same rows come back.
REFRESH_SLA_HOURS = 24

#: "modifiedAtStartTime / EndTime (preferred for all use cases)". Anything
#: narrower or different is the documented alternative, never the default.
PREFERRED_KIND = "modified"

#: The two filter pairs documented for digitalSalesRoomViewingSessions.
SESSION_KINDS: dict[str, tuple[str, str]] = {
    "modified": ("modifiedAtStartTime", "modifiedAtEndTime"),
    "session": ("sessionStartedAtTime", "sessionEndedAtTime"),
}

#: The two filter pairs documented for digitalSalesRooms. ``created`` is
#: documented as a parameter; ``modified`` stays preferred for the reason quoted
#: in Sourced behaviour.
ROOM_KINDS: dict[str, tuple[str, str]] = {
    "modified": ("modifiedAtStartTime", "modifiedAtEndTime"),
    "created": ("createdAtStartTime", "createdAtEndTime"),
}

#: Every documented filter name across both endpoints, so a name that belongs to
#: the *other* endpoint can be refused rather than silently ignored.
_ALL_KINDS: dict[str, tuple[str, str]] = {**SESSION_KINDS, **ROOM_KINDS}

ACCEPT_JSON = "application/json"
ACCEPT_CSV = "text/csv"
ACCEPT_FORMATS = (ACCEPT_JSON, ACCEPT_CSV)

#: Reported dwell and the elapsed time between the two timestamps are both kept.
#: The API's own example has ``roomDurationSeconds: 1``, and a row where the two
#: disagree beyond this is flagged rather than corrected.
DURATION_TOLERANCE_SECONDS = 2

#: The documented query parameter for page size, on both endpoints. No default is
#: invented here: omitting it leaves the server default in charge.
LIMIT_PARAM = "limit"

#: Cap on the per-row rejection detail returned by a landing, so one malformed
#: batch cannot turn into a megabyte of response.
REJECTION_DETAIL_LIMIT = 20


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class ReportingError(RuntimeError):
    """Base for this feature's domain errors.

    Deliberately **not** an ``AuditError``: ``dsr/api.py`` registers a handler
    for that type which picks 409-vs-400 by searching the message for the words
    "conflict" or "exists", which makes a status code a function of prose. These
    types are mapped here instead, so the code is a property of the decision and
    not of how it was worded.
    """


class WindowError(ReportingError):
    """The requested extraction window cannot be used. HTTP 422."""


class PayloadError(ReportingError):
    """The request body is not something this workflow can land. HTTP 422."""


class SweepNotDue(ReportingError):
    """A sweep was asked for before the refresh SLA allows it. HTTP 409."""


class RoomUnknown(ReportingError):
    """No such room in the core store. HTTP 404."""


# --------------------------------------------------------------------------- #
# Field dictionary
#
# The researched names are preserved verbatim, because an ETL job generated from
# this dictionary has to send exactly what the API documents. ``field`` is where
# the value is stored in this codebase.
# --------------------------------------------------------------------------- #


def _field(name: str, field: str, kind: str, description: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "field": field, "type": kind, "description": description, **extra}


#: "Response fields include ..." - the documented set for
#: ``GET /reporting/v2/digitalSalesRoomViewingSessions``. The word "include"
#: matters: an undocumented field in a payload is preserved under ``extra`` rather
#: than dropped, so a field Seismic adds tomorrow reaches the lake without a code
#: change here.
VIEWING_SESSION_FIELDS: tuple[dict[str, Any], ...] = (
    _field(
        "digitalSalesRoomId",
        "digital_sales_room_id",
        "string",
        "The room the session happened in. Join key to digitalSalesRooms.",
        join_key=True,
    ),
    _field(
        "roomDurationSeconds",
        "room_duration_seconds",
        "integer",
        "Dwell time for this session, in seconds. One row is one tab session, so "
        "this is tab dwell, not visit dwell.",
        unit="seconds",
    ),
    _field(
        "engagementUserEmail",
        "engagement_user_email",
        "string",
        "Email of the engagement user who opened the room.",
        pii=True,
    ),
    _field(
        "isEngagementUserInternal",
        "is_engagement_user_internal",
        "boolean",
        "True when the engagement user is internal. Buyer engagement is the "
        "external half of this split.",
    ),
    _field(
        "sessionStartedAt",
        "session_started_at",
        "timestamp",
        "When the session started. Also the lower bound the sessionStartedAtTime "
        "filter matches on.",
    ),
    _field(
        "sessionEndedAt",
        "session_ended_at",
        "timestamp",
        "When the session ended.",
    ),
    _field("city", "city", "string", "City derived from the session IP address."),
    _field("state", "state", "string", "State or region derived from the session IP address."),
    _field("country", "country", "string", "Country derived from the session IP address."),
    _field(
        "ipAddress",
        "ip_address",
        "string",
        "IP address the session came from. Stored verbatim, omitted from read "
        "surfaces unless explicitly requested.",
        pii=True,
    ),
    _field(
        "geolocationLatitude",
        "geolocation_latitude",
        "number",
        "Latitude of the session's IP address.",
        pii=True,
    ),
    _field(
        # The capital L in "geoLocation" is the API's own spelling and is kept.
        # "geolocationLatitude" and "geoLocationLongitude" are the two names the
        # research quotes, and a normaliser that "fixed" the second one would
        # silently stop reading a documented field.
        "geoLocationLongitude",
        "geo_location_longitude",
        "number",
        "Longitude of the session's IP address. Note the capital L: the API "
        "spells this one 'geoLocationLongitude'.",
        pii=True,
        also_accepted=("geolocationLongitude",),
    ),
    _field(
        "userId",
        "user_id",
        "string",
        "Seismic user id. Present for a signed-in engagement user; the "
        "email is the fallback identity when it is not.",
    ),
)

#: The documented response fields for ``GET /reporting/v2/digitalSalesRooms``.
#: The resource's own ``id`` is the join key, and it is stored under the same
#: name the session side uses, so "join on digitalSalesRoomId" becomes a
#: same-name equality.
ROOM_FIELDS: tuple[dict[str, Any], ...] = (
    _field(
        "id",
        "digital_sales_room_id",
        "string",
        "The room's identifier. This is the join key from a viewing session.",
        join_key=True,
    ),
    _field("name", "name", "string", "Room name as the reporting API reports it."),
    _field(
        "digitalSalesRoomTemplateId",
        "digital_sales_room_template_id",
        "string",
        "Template the room was built from.",
    ),
    _field(
        "digitalSalesRoomTemplateVersionId",
        "digital_sales_room_template_version_id",
        "string",
        "Version of that template.",
    ),
    _field(
        "createdBy",
        "created_by",
        "string",
        "Seismic id of the user who created the room.",
        pii=True,
    ),
    _field(
        "createdByUsername",
        "created_by_username",
        "string",
        "Username of the user who created the room.",
        pii=True,
    ),
    _field("createdAt", "created_at", "timestamp", "When the room was created."),
    _field(
        "modifiedAt",
        "modified_at",
        "timestamp",
        "Data-modified time. The incremental filter matches on this, and it is "
        "the field that advances the extraction watermark.",
    ),
    _field(
        "userModifiedAt",
        "user_modified_at",
        "timestamp",
        "When a user last changed the room. Kept separately from modifiedAt "
        "because the API reports them independently; the watermark follows "
        "modifiedAt, which is the documented incremental key.",
    ),
)

SESSION_FIELD_NAMES: tuple[str, ...] = tuple(f["field"] for f in VIEWING_SESSION_FIELDS)
ROOM_FIELD_NAMES: tuple[str, ...] = tuple(f["field"] for f in ROOM_FIELDS)

#: API name -> stored field, for the two resources. Published by ``/contract`` so
#: the casing translation is inspectable instead of implied.
SESSION_FIELD_MAP: dict[str, str] = {f["name"]: f["field"] for f in VIEWING_SESSION_FIELDS}
ROOM_FIELD_MAP: dict[str, str] = {f["name"]: f["field"] for f in ROOM_FIELDS}

#: Fields withheld from a read response unless the caller asks for them. These
#: are the ones Appendix B records as having no documented consent, anonymisation
#: or retention policy behind them.
SENSITIVE_SESSION_FIELDS: tuple[str, ...] = (
    "ip_address",
    "geolocation_latitude",
    "geo_location_longitude",
    "engagement_user_email",
)


# --------------------------------------------------------------------------- #
# Coercion helpers
# --------------------------------------------------------------------------- #


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)


def _integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if float(value).is_integer() else None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _boolean(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in ("true", "1", "yes", "y"):
        return True
    if text in ("false", "0", "no", "n"):
        return False
    return None


def parse_instant(raw: Any, *, field: str, require_offset: bool = True) -> datetime | None:
    """Read an ISO-8601 instant as UTC.

    ``require_offset`` is the difference between a *request window* and a *row
    timestamp*. A machine asking for a window must be explicit, so a naive bound
    is rejected. A row that arrives naive is landed, read as UTC, and flagged -
    dropping it would contradict the instruction to land the rows.
    """
    text = _text(raw)
    if text is None:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WindowError(f"{field} is not an ISO-8601 timestamp: {text!r}") from exc
    if moment.tzinfo is None:
        if require_offset:
            raise WindowError(
                f"{field} must carry a UTC offset (for example {text!r}Z or "
                f"{text!r}+00:00); a window with no offset is ambiguous"
            )
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def format_instant(moment: datetime | None) -> str | None:
    return None if moment is None else moment.astimezone(timezone.utc).isoformat()


def _row_instant(raw: Mapping[str, Any], camel: str, snake: str) -> tuple[datetime | None, bool]:
    """Read one row timestamp, and report whether an offset had to be assumed.

    The naive case is not an error here. The instruction is to land the rows, and
    a row whose timestamp omitted an offset is still a row; it is read as UTC and
    flagged, so a consumer can separate the rows whose instant was stated from the
    ones that were assumed.
    """
    value = raw.get(camel)
    assumed = False
    if value is None:
        value = raw.get(snake)
    if value is None:
        return None, False
    text = _text(value)
    if text is None:
        return None, False
    tail = text[10:] if len(text) > 10 else ""
    if "+" not in tail and not text.upper().endswith("Z"):
        assumed = True
    return parse_instant(text, field=camel, require_offset=False), assumed


def _fingerprint(raw: Mapping[str, Any]) -> str:
    """A stable short digest of a row, for a key when no timestamp survived."""
    canonical = json.dumps(raw, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# The extraction window
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Window:
    """A half-open ``[start, end)`` instant range over one filter kind."""

    kind: str
    start: datetime | None
    end: datetime | None
    resource: str = "viewing_sessions"

    @property
    def bounded(self) -> bool:
        return self.start is not None or self.end is not None

    @property
    def hours(self) -> float | None:
        if self.start is None or self.end is None:
            return None
        return round((self.end - self.start).total_seconds() / 3600.0, 6)

    @property
    def within_sla(self) -> bool | None:
        """False when the window is narrower than the refresh SLA.

        Not an error. The research says the data is "updated no less than every
        24 hours", so a narrower pull cannot return anything fresher - it is
        annotated, not refused.
        """
        span = self.hours
        if span is None:
            return None
        return span >= REFRESH_SLA_HOURS

    def kinds(self) -> dict[str, tuple[str, str]]:
        return SESSION_KINDS if self.resource == "viewing_sessions" else ROOM_KINDS

    def param_names(self) -> tuple[str, str]:
        try:
            return self.kinds()[self.kind]
        except KeyError as exc:
            raise WindowError(
                f"unknown {self.resource} filter kind {self.kind!r}; "
                f"expected one of {sorted(self.kinds())}"
            ) from exc

    def as_query(self) -> dict[str, str]:
        """The exact query parameters to send to the reporting API."""
        start_param, end_param = self.param_names()
        query: dict[str, str] = {}
        if self.start is not None:
            query[start_param] = format_instant(self.start) or ""
        if self.end is not None:
            query[end_param] = format_instant(self.end) or ""
        return query

    def classify(self, moment: datetime | None) -> str:
        """``"in"``, ``"out"`` or ``"unknown"`` for one instant.

        ``unknown`` is a real answer, not a shrug: the ``modified`` filter
        matches on a data-modified time the documented session field list does
        not include, so a row can be inside its window and still be impossible to
        check. The run counts those separately.
        """
        if moment is None:
            return "unknown"
        if self.start is not None and moment < self.start:
            return "out"
        if self.end is not None and moment >= self.end:
            return "out"
        return "in"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "start": format_instant(self.start),
            "end": format_instant(self.end),
            "bounded": self.bounded,
            "hours": self.hours,
            "within_sla": self.within_sla,
            "half_open": True,
            "resource": self.resource,
            "query": self.as_query(),
        }


def build_window(
    spec: Mapping[str, Any] | None,
    *,
    resource: str = "viewing_sessions",
) -> Window:
    """Turn a request body or query object into a validated :class:`Window`.

    Accepts either the explicit form (``{"kind": "modified", "start": ...,
    "end": ...}``) or the API's own parameter names (``modifiedAtStartTime`` and
    friends), because the ETL job already speaks the second vocabulary. A body
    with no window at all is legal and means "everything", which is what a first
    backfill is.
    """
    kinds = SESSION_KINDS if resource == "viewing_sessions" else ROOM_KINDS
    spec = dict(spec or {})

    kind = _text(spec.get("kind"))
    supplied: dict[str, tuple[Any, Any]] = {}
    for candidate, (start_param, end_param) in kinds.items():
        start = spec.get(start_param)
        end = spec.get(end_param)
        if start is not None or end is not None:
            supplied[candidate] = (start, end)

    # A filter that belongs to the *other* endpoint is not silently dropped. The
    # room inventory has no session filter and the session feed has no created
    # filter, and quietly ignoring one of them would land a whole extraction
    # against the wrong window - the kind of mistake a warehouse never notices.
    foreign = sorted(
        name
        for name, (start_param, end_param) in _ALL_KINDS.items()
        if name not in kinds
        and (spec.get(start_param) is not None or spec.get(end_param) is not None)
    )
    if foreign:
        detail = ", ".join(
            f"{_ALL_KINDS[name][0]}/{_ALL_KINDS[name][1]} belongs to "
            f"{'viewing_sessions' if name in SESSION_KINDS else 'digital_sales_rooms'}"
            for name in foreign
        )
        raise WindowError(
            f"{detail}, which {resource} does not accept. The documented filters "
            f"for {resource} are {sorted(kinds)}"
        )

    if len(supplied) > 1:
        named = ", ".join(
            f"{name} ({kinds[name][0]}/{kinds[name][1]})" for name in sorted(supplied)
        )
        raise WindowError(
            f"one extraction window only: {named} were all supplied. The "
            "documented filters are alternatives, and the API publishes no "
            "semantics for combining them"
        )
    if kind is not None and kind not in kinds:
        raise WindowError(
            f"unknown {resource} filter kind {kind!r}; expected one of {sorted(kinds)}"
        )

    if supplied:
        inferred = next(iter(supplied))
        if kind is not None and kind != inferred:
            raise WindowError(
                f"kind={kind!r} contradicts the supplied parameters, which are "
                f"the {inferred} filter"
            )
        kind = inferred
        start_raw, end_raw = supplied[inferred]
    else:
        kind = kind or PREFERRED_KIND
        start_raw = spec.get("start")
        end_raw = spec.get("end")

    if not kind:
        raise WindowError(
            "an extraction needs a filter kind; modifiedAt is preferred for all use cases"
        )

    label = kinds[kind]
    start = parse_instant(start_raw, field=label[0]) if start_raw is not None else None
    end = parse_instant(end_raw, field=label[1]) if end_raw is not None else None

    if start is not None and end is not None and start >= end:
        raise WindowError(
            f"{label[0]} ({format_instant(start)}) must be earlier than "
            f"{label[1]} ({format_instant(end)}); the window is half-open, "
            "[start, end)"
        )

    window = Window(kind=kind, start=start, end=end, resource=resource)
    return window


def window_from_run(run: Mapping[str, Any]) -> Window:
    """Rebuild a run's window from the window stored on the run record."""
    stored = dict(run.get("data", {}).get("window") or {})
    return build_window(
        {
            "kind": stored.get("kind"),
            "start": stored.get("start"),
            "end": stored.get("end"),
        },
        resource=str(run.get("data", {}).get("resource") or "viewing_sessions"),
    )


# --------------------------------------------------------------------------- #
# Row normalisation
# --------------------------------------------------------------------------- #


def viewer_of(raw: Mapping[str, Any]) -> tuple[str, str]:
    """The identity a session is attributed to, and how it was derived.

    ``userId`` first, because it is the vendor's own identifier; the engagement
    email is the fallback, case-folded so casing cannot fork one buyer into two
    viewers. A row with neither is still landed - it is a real viewing session by
    somebody - and is counted as ``unattributed``.
    """
    user_id = _text(raw.get("userId") or raw.get("user_id"))
    if user_id:
        return f"user:{user_id}", "user_id"
    email = _text(raw.get("engagementUserEmail") or raw.get("engagement_user_email"))
    if email:
        return f"email:{email.casefold()}", "email"
    return "anonymous", "anonymous"


def _window_moment(raw: Mapping[str, Any], kind: str) -> datetime | None:
    """The instant on a row that the window's filter kind matches on."""
    if kind == "session":
        return parse_instant(
            raw.get("sessionStartedAt") or raw.get("session_started_at"),
            field="sessionStartedAt",
            require_offset=False,
        )
    if kind == "created":
        return parse_instant(
            raw.get("createdAt") or raw.get("created_at"),
            field="createdAt",
            require_offset=False,
        )
    # The documented filter is modifiedAt for every resource, and modifiedAt is
    # not among the documented response fields for a viewing session. Absent, this
    # is a row whose window membership cannot be checked here.
    return parse_instant(
        raw.get("modifiedAt") or raw.get("modified_at"),
        field="modifiedAt",
        require_offset=False,
    )


def session_key(
    row: Mapping[str, Any],
    *,
    room_id: str | None,
    viewer: str,
    started_at: str | None,
    fingerprint: str | None = None,
) -> str:
    """The merge identity for one landed session.

    An explicit session identifier wins the moment the API returns one, which is
    the documented field list's most conspicuous absence. Failing that the key is
    room + viewer + start instant, and a row with no usable start instant falls
    back to a digest of the row so that two identical corrupt rows merge and two
    different ones do not.
    """
    explicit = _text(
        row.get("viewingSessionId")
        or row.get("viewing_session_id")
        or row.get("sessionId")
        or row.get("session_id")
    )
    if explicit:
        return f"session:{explicit}"
    if started_at:
        return f"{room_id or '-'}|{viewer}|{started_at}"
    return f"{room_id or '-'}|{viewer}|unparsed:{fingerprint or _fingerprint(row)}"


def normalise_session(
    raw: Mapping[str, Any],
    *,
    window: Window | None = None,
    redact_ip: bool = False,
) -> dict[str, Any]:
    """Turn one API row into the payload this feature stores.

    Every documented field is always present, so a shallow merge on update
    cannot leave a stale value behind: a ``city`` that the vendor cleared arrives
    as ``None`` and is written as ``None``.
    """
    if not isinstance(raw, Mapping):
        raise PayloadError(f"a viewing session row must be an object, got {type(raw).__name__}")

    flags: list[str] = []
    payload: dict[str, Any] = {}

    room_id = _text(raw.get("digitalSalesRoomId") or raw.get("digital_sales_room_id"))
    payload["digital_sales_room_id"] = room_id
    if room_id is None:
        flags.append("room_missing")

    duration = _integer(raw.get("roomDurationSeconds") or raw.get("room_duration_seconds"))
    payload["room_duration_seconds"] = duration
    if duration is None:
        flags.append("duration_missing")
    elif duration < 0:
        flags.append("duration_negative")

    payload["engagement_user_email"] = _text(
        raw.get("engagementUserEmail") or raw.get("engagement_user_email")
    )
    payload["is_engagement_user_internal"] = _boolean(
        raw.get("isEngagementUserInternal")
        if "isEngagementUserInternal" in raw
        else raw.get("is_engagement_user_internal")
    )
    payload["user_id"] = _text(raw.get("userId") or raw.get("user_id"))

    started, started_assumed = _row_instant(raw, "sessionStartedAt", "session_started_at")
    ended, ended_assumed = _row_instant(raw, "sessionEndedAt", "session_ended_at")
    if started_assumed or ended_assumed:
        flags.append("timestamp_assumed_utc")
    payload["session_started_at"] = format_instant(started)
    payload["session_ended_at"] = format_instant(ended)
    modified, _ = _row_instant(raw, "modifiedAt", "modified_at")
    payload["modified_at"] = format_instant(modified)

    elapsed: int | None = None
    if started is not None and ended is not None:
        elapsed = int(round((ended - started).total_seconds()))
        payload["elapsed_duration_seconds"] = elapsed
        if elapsed < 0:
            flags.append("session_end_before_start")
    else:
        payload["elapsed_duration_seconds"] = None

    if elapsed is not None and duration is not None and elapsed >= 0:
        delta = elapsed - duration
        payload["duration_delta_seconds"] = delta
        if abs(delta) > DURATION_TOLERANCE_SECONDS:
            flags.append("duration_mismatch")
    else:
        payload["duration_delta_seconds"] = None

    payload["city"] = _text(raw.get("city"))
    payload["state"] = _text(raw.get("state"))
    payload["country"] = _text(raw.get("country"))

    ip = _text(raw.get("ipAddress") or raw.get("ip_address"))
    if redact_ip and ip:
        payload["ip_address"] = mask_ip(ip)
        flags.append("ip_redacted")
    else:
        payload["ip_address"] = ip

    latitude = _number(raw.get("geolocationLatitude") or raw.get("geolocation_latitude"))
    longitude = _number(
        raw.get("geoLocationLongitude")
        or raw.get("geolocationLongitude")
        or raw.get("geo_location_longitude")
    )
    payload["geolocation_latitude"] = latitude
    payload["geo_location_longitude"] = longitude

    present_geo = [
        value for value in (payload["city"], payload["state"], payload["country"]) if value
    ]
    if not present_geo and latitude is None and longitude is None:
        flags.append("geography_missing")
    elif len(present_geo) != 3 or latitude is None or longitude is None:
        # Some of the geography arrived and some did not. Worth a flag either way:
        # a country with no city still rolls up, but a city with no country cannot
        # be attributed, and a BI consumer cannot tell the two apart without this.
        flags.append("geography_partial")

    viewer, viewer_kind = viewer_of(raw)
    payload["viewer_key"] = viewer
    payload["viewer_kind"] = viewer_kind
    if viewer_kind == "anonymous":
        flags.append("unattributed")

    payload["session_key"] = session_key(
        raw, room_id=room_id, viewer=viewer, started_at=payload["session_started_at"]
    )

    if window is not None:
        verdict = window.classify(_window_moment(raw, window.kind))
        payload["window_kind"] = window.kind
        payload["window_verdict"] = verdict
        if verdict == "out":
            # The job already filtered, so a row outside the window it was told
            # to pull is a paging bug worth surfacing on the row itself.
            flags.append("outside_window")
        # ``unknown`` is deliberately *not* a quality flag. A row with no
        # ``modifiedAt`` cannot be checked against a ``modified`` window, and the
        # documented session field list does not include one, so that is the normal
        # case rather than a defect. It is counted on the run instead, where
        # "how much of this batch could not be verified" is the question an operator
        # is actually asking.

    # Anything the dictionary does not name is kept, not dropped: the dictionary
    # is documented as the fields the response "include"s, and an ETL that
    # discards the rest silently loses a field the vendor added.
    known = set(SESSION_FIELD_MAP) | {
        "digital_sales_room_id",
        "session_key",
        "viewer_key",
        "viewer_kind",
        "elapsed_duration_seconds",
        "duration_delta_seconds",
        "modified_at",
        "window_kind",
        "window_verdict",
    }
    extra = {
        key: value for key, value in raw.items() if key not in known and not key.startswith("_")
    }
    if extra:
        payload["extra"] = extra
        flags.append("undocumented_fields")

    payload["quality_flags"] = sorted(set(flags))
    payload["has_quality_flags"] = bool(payload["quality_flags"])
    return payload


def mask_ip(value: str) -> str:
    """The address with its host part removed, for the redaction option.

    Deliberately coarse: it preserves "same network" and loses "which machine",
    which is the trade a warehouse consumer actually has to make.
    """
    text = str(value).strip()
    if ":" in text:  # IPv6
        groups = text.split(":")
        return ":".join(groups[: max(1, len(groups) // 2)]) + "::"
    parts = text.split(".")
    if len(parts) == 4:
        return ".".join(parts[:2] + ["0", "0"])
    return text


def normalise_room(
    raw: Mapping[str, Any],
    *,
    window: Window | None = None,
    bound_room_id: str | None = None,
) -> dict[str, Any]:
    """Turn one ``digitalSalesRooms`` row into the payload this feature stores."""
    if not isinstance(raw, Mapping):
        raise PayloadError(f"a room inventory row must be an object, got {type(raw).__name__}")

    flags: list[str] = []
    room_id = _text(
        raw.get("id") or raw.get("digitalSalesRoomId") or raw.get("digital_sales_room_id")
    )
    if room_id is None:
        raise PayloadError("a room inventory row needs an id: it is the join key for sessions")

    payload: dict[str, Any] = {
        "digital_sales_room_id": room_id,
        "name": _text(raw.get("name")),
        "digital_sales_room_template_id": _text(
            raw.get("digitalSalesRoomTemplateId") or raw.get("digital_sales_room_template_id")
        ),
        "digital_sales_room_template_version_id": _text(
            raw.get("digitalSalesRoomTemplateVersionId")
            or raw.get("digital_sales_room_template_version_id")
        ),
        "created_by": _text(raw.get("createdBy") or raw.get("created_by")),
        "created_by_username": _text(
            raw.get("createdByUsername") or raw.get("created_by_username")
        ),
        "created_at": format_instant(
            parse_instant(
                raw.get("createdAt") or raw.get("created_at"),
                field="createdAt",
                require_offset=False,
            )
        ),
        "modified_at": format_instant(
            parse_instant(
                raw.get("modifiedAt") or raw.get("modified_at"),
                field="modifiedAt",
                require_offset=False,
            )
        ),
        "user_modified_at": format_instant(
            parse_instant(
                raw.get("userModifiedAt") or raw.get("user_modified_at"),
                field="userModifiedAt",
                require_offset=False,
            )
        ),
    }
    payload["room_key"] = room_id
    if bound_room_id:
        payload["bound_room_id"] = bound_room_id
    if payload["name"] is None:
        flags.append("name_missing")
    if (
        payload["modified_at"]
        and payload["user_modified_at"]
        and (payload["user_modified_at"] != payload["modified_at"])
    ):
        # Not an error: the API reports a data-modified time and a user-modified
        # time separately. Recorded so a BI consumer can tell a template refresh
        # from a seller edit.
        flags.append("user_modified")

    if window is not None:
        verdict = window.classify(_window_moment(raw, window.kind))
        payload["window_verdict"] = verdict
        if verdict == "out":
            flags.append("outside_window")

    known = set(ROOM_FIELD_MAP) | {
        "digital_sales_room_id",
        "room_key",
        "bound_room_id",
        "window_verdict",
    }
    extra = {
        key: value for key, value in raw.items() if key not in known and not key.startswith("_")
    }
    if extra:
        payload["extra"] = extra
        flags.append("undocumented_fields")

    payload["quality_flags"] = sorted(set(flags))
    payload["has_quality_flags"] = bool(payload["quality_flags"])
    return payload


# --------------------------------------------------------------------------- #
# Landing
# --------------------------------------------------------------------------- #


def _rejection(index: int, exc: Exception) -> dict[str, Any]:
    return {"index": index, "error": f"{type(exc).__name__}: {exc}"}


def _tally(rejections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return rejections[:REJECTION_DETAIL_LIMIT]


def require_rows(rows: Any, *, what: str) -> Sequence[Any]:
    """Check a batch before anything is written.

    Split out from the landing functions on purpose: the HTTP layer validates
    *before* it opens a run, because a run that is opened and then abandoned by a
    bad request would sit in ``requested`` forever and block every later sweep
    with ``in_flight``. A request that cannot be attempted must not leave a trace
    that looks like a job in progress.
    """
    if not isinstance(rows, (list, tuple)):
        raise PayloadError(f"rows must be a list of {what} objects, got {type(rows).__name__}")
    if not rows:
        raise PayloadError(
            f"rows is empty: an extraction with no {what} is a request that cannot be attempted"
        )
    return rows


def _existing(
    store: RecordStore, collection: str, key_field: str, key: str
) -> dict[str, Any] | None:
    found = store.find(collection, {key_field: key}, limit=1, include_deleted=True)
    return found[0] if found else None


def land_sessions(
    store: RecordStore,
    window: Window | None,
    rows: Any,
    *,
    redact_ip: bool = False,
    actor: str | None = None,
    source: str,
) -> dict[str, Any]:
    """Land a batch of viewing-session rows, merging on the session key.

    Every row is merged, never appended, because the incremental filter exists so
    the job "can pull the updates and merge them into existing data sets". A row
    is only skipped when it is not an object at all; a row with impossible data
    is landed with quality flags, because an ETL that quietly drops rows is worse
    than one that flags them.
    """
    require_rows(rows, what="session")

    counters: dict[str, Any] = {
        "received": len(rows),
        "created": 0,
        "updated": 0,
        "restored": 0,
        "unchanged": 0,
        "rejected": 0,
        "flagged": 0,
        "unresolved_rooms": 0,
        "outside_window": 0,
        "unverifiable_window": 0,
        "duplicate_keys": 0,
        "dwell_seconds": 0,
    }
    rejections: list[dict[str, Any]] = []
    seen: dict[str, int] = {}

    for index, raw in enumerate(rows):
        try:
            payload = normalise_session(raw, window=window, redact_ip=redact_ip)
        except (PayloadError, WindowError) as exc:
            counters["rejected"] += 1
            rejections.append(_rejection(index, exc))
            continue

        key = str(payload["session_key"])
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            # The same key twice in one batch is one session reported twice; the
            # merge makes the second an update, and the count says so.
            counters["duplicate_keys"] += 1

        room_id = payload.get("digital_sales_room_id")
        inventory = _existing(store, ROOMS, "room_key", room_id) if room_id else None
        if inventory is None or inventory.get("deleted_at"):
            payload["room_unresolved"] = True
            counters["unresolved_rooms"] += 1
            flags = set(payload["quality_flags"]) | {"room_unresolved"}
            payload["quality_flags"] = sorted(flags)
            payload["has_quality_flags"] = True
        else:
            payload["room_unresolved"] = False

        if payload["has_quality_flags"]:
            counters["flagged"] += 1
        if "outside_window" in payload["quality_flags"]:
            counters["outside_window"] += 1
        if payload.get("window_verdict") == "unknown":
            counters["unverifiable_window"] += 1
        if isinstance(payload.get("room_duration_seconds"), int):
            counters["dwell_seconds"] += max(0, int(payload["room_duration_seconds"]))

        existing = _existing(store, SESSIONS, "session_key", key)
        if existing is None:
            store.create(SESSIONS, payload, actor=actor, source=source)
            counters["created"] += 1
        elif existing.get("deleted_at"):
            # A retracted row that came back is restored rather than duplicated,
            # so the warehouse does not carry two rows with one key.
            store.restore(existing["id"], actor=actor, source=source)
            store.update(existing["id"], payload, actor=actor, source=source)
            counters["restored"] += 1
        elif dict(existing.get("data") or {}) == payload:
            # Nothing changed. Writing it would bump the revision and add an audit
            # row for work that did not happen.
            counters["unchanged"] += 1
        else:
            store.update(existing["id"], payload, actor=actor, source=source)
            counters["updated"] += 1

    counters["landed"] = counters["created"] + counters["updated"] + counters["restored"]
    counters["rejections"] = _tally(rejections)
    if len(rejections) > REJECTION_DETAIL_LIMIT:
        counters["rejections_truncated"] = len(rejections) - REJECTION_DETAIL_LIMIT
    return counters


def land_rooms(
    store: RecordStore,
    window: Window | None,
    rows: Any,
    *,
    actor: str | None = None,
    source: str,
) -> dict[str, Any]:
    """Land a batch of ``digitalSalesRooms`` rows, merging on the room key.

    This is a separate pull from a separate endpoint, so it is a separate route
    and a separate run: the session join is only as good as the inventory behind
    it, and keeping the two apart means a failed inventory pull is visible on its
    own run rather than hidden inside a session run.
    """
    require_rows(rows, what="room inventory")

    counters: dict[str, Any] = {
        "received": len(rows),
        "created": 0,
        "updated": 0,
        "restored": 0,
        "unchanged": 0,
        "rejected": 0,
        "flagged": 0,
        "bound": 0,
        "duplicate_keys": 0,
        "outside_window": 0,
        "dwell_seconds": 0,
    }
    rejections: list[dict[str, Any]] = []
    seen: dict[str, int] = {}

    for index, raw in enumerate(rows):
        bound = (
            _text(raw.get("bound_room_id") or raw.get("boundRoomId"))
            if isinstance(raw, Mapping)
            else None
        )
        try:
            payload = normalise_room(raw, window=window, bound_room_id=bound)
        except (PayloadError, WindowError) as exc:
            counters["rejected"] += 1
            rejections.append(_rejection(index, exc))
            continue

        key = str(payload["room_key"])
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            counters["duplicate_keys"] += 1
        if payload.get("bound_room_id"):
            counters["bound"] += 1
        if "outside_window" in payload["quality_flags"]:
            counters["outside_window"] += 1
        if payload["has_quality_flags"]:
            counters["flagged"] += 1

        existing = _existing(store, ROOMS, "room_key", key)
        if existing is None:
            store.create(ROOMS, payload, actor=actor, source=source)
            counters["created"] += 1
        elif existing.get("deleted_at"):
            store.restore(existing["id"], actor=actor, source=source)
            store.update(existing["id"], payload, actor=actor, source=source)
            counters["restored"] += 1
        elif dict(existing.get("data") or {}) == payload:
            counters["unchanged"] += 1
        else:
            store.update(existing["id"], payload, actor=actor, source=source)
            counters["updated"] += 1

    counters["landed"] = counters["created"] + counters["updated"] + counters["restored"]
    counters["rejections"] = _tally(rejections)
    if len(rejections) > REJECTION_DETAIL_LIMIT:
        counters["rejections_truncated"] = len(rejections) - REJECTION_DETAIL_LIMIT
    return counters


# --------------------------------------------------------------------------- #
# Runs, the watermark, and the sweep
# --------------------------------------------------------------------------- #

RUN_STATES = ("requested", "landed")
RUN_KINDS = ("sweep", "extract")


def list_runs(
    store: RecordStore, *, resource: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    runs = store.list(
        RUNS, limit=min(max(1, int(limit)), 1000), order_by="created_at", descending=True
    )
    if resource:
        runs = [run for run in runs if (run.get("data") or {}).get("resource") == resource]
    return runs


def latest_landed(store: RecordStore, *, resource: str | None = None) -> dict[str, Any] | None:
    for run in list_runs(store, resource=resource, limit=1000):
        data = run.get("data") or {}
        if data.get("state") == "landed" and not run.get("deleted_at"):
            return run
    return None


def outstanding_run(store: RecordStore, *, resource: str | None = None) -> dict[str, Any] | None:
    """A run that was opened by a sweep and never landed.

    An ETL that is mid-flight, or that failed between the pull and the landing, is
    exactly the state an operator needs to see; a sweep that silently issues a
    second window on top of it would double-count the day.
    """
    for run in list_runs(store, resource=resource, limit=1000):
        data = run.get("data") or {}
        if data.get("state") == "requested" and not run.get("deleted_at"):
            return run
    return None


def watermark(
    store: RecordStore, *, resource: str = "viewing_sessions", pending_end: Any = None
) -> str | None:
    """The high-water mark of landed ``modified`` windows.

    Derived from the runs rather than stored, so the mark cannot drift from the
    runs that advanced it. Only the preferred kind counts: a ``session``-filtered
    pull says nothing about what has changed since, so it must not move the mark.

    ``pending_end`` is for the one caller that cannot yet see its own run as
    landed - :func:`close_run`, which has to write the mark into the same update
    that marks the run landed. Everything else reads the runs as they are.
    """
    best: datetime | None = None
    for run in list_runs(store, resource=resource, limit=1000):
        data = run.get("data") or {}
        if data.get("state") != "landed" or run.get("deleted_at"):
            continue
        window = data.get("window") or {}
        if window.get("kind") != PREFERRED_KIND or not window.get("end"):
            continue
        moment = parse_instant(window["end"], field="modifiedAtEndTime")
        if moment is not None and (best is None or moment > best):
            best = moment
    if pending_end is not None:
        moment = (
            pending_end
            if isinstance(pending_end, datetime)
            else parse_instant(pending_end, field="modifiedAtEndTime")
        )
        if moment is not None and (best is None or moment > best):
            best = moment
    return format_instant(best)


def open_run(
    store: RecordStore,
    *,
    window: Window,
    resource: str,
    run_kind: str,
    fmt: str,
    limit: int | None,
    started: datetime,
    actor: str | None = None,
    source: str,
) -> dict[str, Any]:
    """Open a run. ``state`` starts at ``requested`` and becomes ``landed``."""
    if run_kind not in RUN_KINDS:
        raise PayloadError(f"unknown run kind {run_kind!r}; expected one of {list(RUN_KINDS)}")
    if fmt not in ACCEPT_FORMATS:
        raise PayloadError(f"unsupported Accept {fmt!r}; expected one of {list(ACCEPT_FORMATS)}")
    payload = {
        "run_kind": run_kind,
        "state": "requested",
        "resource": resource,
        "format": fmt,
        "limit": _integer(limit),
        "window": window.to_dict(),
        "sla_hours": REFRESH_SLA_HOURS,
        "requested_at": format_instant(started),
        "landed_at": None,
        "watermark_before": watermark(store, resource=resource),
        "watermark_after": None,
        "counters": {},
    }
    return store.create(RUNS, payload, actor=actor, source=source)


def close_run(
    store: RecordStore,
    run_id: str,
    counters: Mapping[str, Any],
    *,
    finished: datetime,
    actor: str | None = None,
    source: str,
) -> dict[str, Any]:
    """Mark a run landed, recording the counters and the advanced watermark."""
    run = store.get(run_id)
    if run is None or run.get("collection") != RUNS:
        raise PayloadError(f"no extraction run {run_id!r} to land")
    data = dict(run.get("data") or {})
    if data.get("state") == "landed":
        raise PayloadError(f"extraction run {run_id} was already landed; open a new one")
    previous = watermark(store, resource=str(data.get("resource") or "viewing_sessions"))
    own_end = (data.get("window") or {}).get("end")
    patch = {
        "state": "landed",
        "landed_at": format_instant(finished),
        "counters": dict(counters),
        "watermark_before": data.get("watermark_before") or previous,
        # This run's own end is passed explicitly: the update below is what makes
        # it a landed run, so a plain read here would not yet see it.
        "watermark_after": watermark(
            store,
            resource=str(data.get("resource") or "viewing_sessions"),
            pending_end=own_end,
        ),
    }
    return store.update(run_id, patch, actor=actor, source=source)


def due_state(
    store: RecordStore,
    *,
    now: datetime,
    resource: str = "viewing_sessions",
    hours: float = REFRESH_SLA_HOURS,
) -> dict[str, Any]:
    """Whether a sweep is due, and why."""
    last = latest_landed(store, resource=resource)
    pending = outstanding_run(store, resource=resource)
    landed_at = (last or {}).get("data", {}).get("landed_at") if last else None
    if pending is not None:
        return {
            "due": False,
            "reason": "in_flight",
            "detail": (
                f"run {pending['id']} was requested at "
                f"{(pending.get('data') or {}).get('requested_at')} and has not landed"
            ),
            "sla_hours": hours,
            "due_in_hours": 0.0,
            "last_landed_at": landed_at,
            "pending_run_id": pending["id"],
            "watermark": watermark(store, resource=resource),
        }
    if last is None:
        return {
            "due": True,
            "reason": "never_run",
            "detail": "no extraction has landed yet, so the first sweep backfills",
            "sla_hours": hours,
            "due_in_hours": 0.0,
            "last_landed_at": None,
            "pending_run_id": None,
            "watermark": None,
        }
    landed_moment = parse_instant(landed_at, field="landed_at", require_offset=False) or now
    ready_at = landed_moment + timedelta(hours=hours)
    remaining = (ready_at - now).total_seconds() / 3600.0
    return {
        "due": remaining <= 0,
        "reason": "sla_elapsed" if remaining <= 0 else "within_sla",
        "detail": (
            f"the reporting data is refreshed no less than every {hours:g}h, and the "
            f"last extraction landed at {landed_at}"
        ),
        "sla_hours": hours,
        "due_in_hours": round(max(0.0, remaining), 4),
        "ready_at": format_instant(ready_at),
        "last_landed_at": landed_at,
        "pending_run_id": None,
        "watermark": watermark(store, resource=resource),
    }


def next_window(
    store: RecordStore,
    *,
    now: datetime,
    resource: str = "viewing_sessions",
    hours: float = REFRESH_SLA_HOURS,
    limit: int | None = None,
    fmt: str = ACCEPT_JSON,
) -> dict[str, Any]:
    """The window the next extraction should pull, and the query to send.

    The page is one SLA long, because the data only changes that often. A first
    run leaves the lower bound open - "large portions of data" - and always closes
    the upper bound at ``now``, so the watermark can advance from the very first
    extraction instead of waiting for a second one to notice.
    """
    if hours <= 0:
        raise WindowError(f"page size must be greater than zero hours, got {hours!r}")
    mark = watermark(store, resource=resource)
    start = parse_instant(mark, field="watermark") if mark else None
    end = start + timedelta(hours=hours) if start is not None else now
    window = Window(kind=PREFERRED_KIND, start=start, end=end, resource=resource)
    state = due_state(store, now=now, resource=resource, hours=hours)

    pages_behind = 1
    if start is not None:
        behind = (now - end).total_seconds() / 3600.0
        pages_behind = max(1, int(math.ceil(behind / hours))) if behind > 0 else 1

    query = window.as_query()
    if limit is not None:
        query[LIMIT_PARAM] = str(limit)

    notes = [
        f"One page is {hours:g}h because the reporting data is updated no less than "
        "every 24 hours; a narrower pull cannot return anything fresher.",
        "modifiedAtStartTime/modifiedAtEndTime is the documented incremental filter "
        "and the only kind that advances the watermark.",
    ]
    if state["reason"] == "never_run":
        notes.append(
            "This is the first extraction, so the lower bound is open: it backfills "
            "everything up to now, which is what 'large portions of data' means."
        )
    if pages_behind > 1:
        notes.append(
            f"{pages_behind} SLA-sized pages are outstanding; land this one, then ask "
            "again for the next."
        )
    if not state["due"]:
        notes.append(f"A sweep is not due yet: {state['detail']}.")

    return {
        "resource": resource,
        "window": window.to_dict(),
        "query": query,
        "format": fmt,
        "accept": list(ACCEPT_FORMATS),
        "sweep": state,
        "pages_behind": pages_behind,
        "notes": notes,
    }


# --------------------------------------------------------------------------- #
# Reads: the BI surface
# --------------------------------------------------------------------------- #


def _data(record: Mapping[str, Any]) -> dict[str, Any]:
    return dict(record.get("data") or {})


def _visible(payload: Mapping[str, Any], *, include_sensitive: bool) -> dict[str, Any]:
    if include_sensitive:
        return dict(payload)
    return {key: value for key, value in payload.items() if key not in SENSITIVE_SESSION_FIELDS}


def room_index(store: RecordStore) -> dict[str, dict[str, Any]]:
    """The landed inventory, keyed by the identifier sessions join on."""
    return {
        str((_data(record)).get("digital_sales_room_id")): _data(record)
        for record in store.list(ROOMS, limit=1000, order_by="created_at", descending=False)
        if _data(record).get("digital_sales_room_id")
    }


def bound_dsr_rooms(store: RecordStore, room_id: str) -> list[str]:
    """Seismic room identifiers a core room is bound to.

    Room-scoped paths stay room-scoped by resolving the binding at read time
    rather than storing a copy of it on the session: a binding made after a
    session landed must not leave that session stranded outside its own room.
    """
    if store.get(room_id) is None:
        raise RoomUnknown(f"room {room_id} not found")
    return [
        str(_data(record).get("digital_sales_room_id"))
        for record in store.find(ROOMS, {"bound_room_id": room_id}, limit=1000)
        if _data(record).get("digital_sales_room_id")
    ]


def sessions_for(
    store: RecordStore,
    *,
    room_ids: Sequence[str] | None = None,
    limit: int = 200,
    include_sensitive: bool = False,
) -> list[dict[str, Any]]:
    """The landed sessions, optionally scoped to a set of Seismic room ids.

    ``room_ids=None`` means every room; ``room_ids=[]`` means *this* room has
    nothing bound to it, and must return nothing. The distinction is load-bearing:
    a falsy check here would answer an unbound room with the whole warehouse.

    Filtering goes through ``find()`` so it resolves dotted JSON paths through the
    dynamic index; the join and the flag projection are applied on read.
    """
    capped = min(max(1, int(limit)), 1000)
    if room_ids is not None:
        rows: list[dict[str, Any]] = []
        for room_id in room_ids:
            rows.extend(store.find(SESSIONS, {"digital_sales_room_id": room_id}, limit=capped))
        rows.sort(
            key=lambda record: str(_data(record).get("session_started_at") or ""), reverse=True
        )
    else:
        rows = store.list(SESSIONS, limit=capped, order_by="created_at", descending=True)

    rooms = room_index(store)
    projected: list[dict[str, Any]] = []
    for record in rows:
        payload = _data(record)
        room = rooms.get(str(payload.get("digital_sales_room_id")) or "", {})
        enriched = _visible(payload, include_sensitive=include_sensitive)
        enriched["id"] = record.get("id")
        enriched["revision"] = record.get("revision")
        enriched["updated_at"] = record.get("updated_at")
        enriched["room_name"] = room.get("name")
        enriched["room_bound_room_id"] = room.get("bound_room_id")
        enriched["room_resolved"] = bool(room)
        projected.append(enriched)
    return projected


def _rollup(rows: Iterable[Mapping[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get(key) or "unknown"), []).append(row)
    return grouped


def _dwell_total(rows: Sequence[Mapping[str, Any]]) -> int:
    total = 0
    for row in rows:
        value = row.get("room_duration_seconds")
        if isinstance(value, int) and not isinstance(value, bool):
            total += max(0, value)
    return total


def _first_last(rows: Sequence[Mapping[str, Any]]) -> tuple[str | None, str | None]:
    stamps = sorted(
        str(row.get("session_started_at")) for row in rows if row.get("session_started_at")
    )
    return (stamps[0], stamps[-1]) if stamps else (None, None)


def summarise(
    rows: Sequence[Mapping[str, Any]], *, sla_hours: int = REFRESH_SLA_HOURS
) -> dict[str, Any]:
    """The headline numbers, with sessions and visitors kept apart.

    "Each row represents a single session in a DSR link from a single user in a
    single browser tab", so a row count is a session count. Reporting it as a
    visitor count is the one error this vocabulary exists to prevent.
    """
    total = len(rows)
    dwell = _dwell_total(rows)
    internal = [row for row in rows if row.get("is_engagement_user_internal") is True]
    external = [row for row in rows if row.get("is_engagement_user_internal") is False]
    unknown = [row for row in rows if row.get("is_engagement_user_internal") is None]
    viewers = {str(row.get("viewer_key")) for row in rows}
    rooms = {
        str(row.get("digital_sales_room_id")) for row in rows if row.get("digital_sales_room_id")
    }
    countries = {str(row.get("country")) for row in rows if row.get("country")}
    cities = {
        (str(row.get("country") or ""), str(row.get("state") or ""), str(row.get("city") or ""))
        for row in rows
        if row.get("city")
    }
    first, last = _first_last(rows)
    flags: dict[str, int] = {}
    for row in rows:
        for flag in row.get("quality_flags") or []:
            flags[str(flag)] = flags.get(str(flag), 0) + 1

    durations = [
        int(row["room_duration_seconds"])
        for row in rows
        if isinstance(row.get("room_duration_seconds"), int)
        and not isinstance(row.get("room_duration_seconds"), bool)
    ]
    return {
        "sessions": total,
        "visitors": len(viewers),
        "dwell_seconds": dwell,
        "dwell_minutes": round(dwell / 60.0, 2),
        "mean_dwell_seconds": round(dwell / total, 2) if total else None,
        "longest_session_seconds": max(durations) if durations else None,
        "shortest_session_seconds": min(durations) if durations else None,
        "rooms": len(rooms),
        "countries": len(countries),
        "cities": len(cities),
        "internal": {
            "sessions": len(internal),
            "visitors": len({str(row.get("viewer_key")) for row in internal}),
            "dwell_seconds": _dwell_total(internal),
        },
        "external": {
            "sessions": len(external),
            "visitors": len({str(row.get("viewer_key")) for row in external}),
            "dwell_seconds": _dwell_total(external),
        },
        "internal_unknown": len(unknown),
        "unattributed_sessions": sum(
            1 for row in rows if "unattributed" in (row.get("quality_flags") or [])
        ),
        "unresolved_room_sessions": sum(
            1
            for row in rows
            if row.get("room_unresolved") or "room_unresolved" in (row.get("quality_flags") or [])
        ),
        "flagged_sessions": sum(1 for row in rows if row.get("has_quality_flags")),
        "quality_flags": dict(sorted(flags.items())),
        "first_session_at": first,
        "last_session_at": last,
        "sla_hours": sla_hours,
        "granularity": "one row is one session in one browser tab, not one visit",
    }


def dwell_rollup(
    rows: Sequence[Mapping[str, Any]], *, group_by: str = "room"
) -> list[dict[str, Any]]:
    """Dwell per room, or per viewer.

    A total is tab-seconds, because that is what a row is; the label is carried in
    the response so a dashboard cannot quietly call it visit time.
    """
    key = "digital_sales_room_id" if group_by == "room" else "viewer_key"
    rooms = room_index_of(rows)
    buckets = _rollup(rows, key)
    out: list[dict[str, Any]] = []
    for name, group in buckets.items():
        first, last = _first_last(group)
        dwell = _dwell_total(group)
        out.append(
            {
                "group": name,
                "label": rooms.get(name) if group_by == "room" else name,
                "sessions": len(group),
                "visitors": len({str(row.get("viewer_key")) for row in group}),
                "dwell_seconds": dwell,
                "dwell_minutes": round(dwell / 60.0, 2),
                "mean_dwell_seconds": round(dwell / len(group), 2) if group else None,
                "internal_sessions": sum(
                    1 for row in group if row.get("is_engagement_user_internal") is True
                ),
                "first_session_at": first,
                "last_session_at": last,
            }
        )
    out.sort(key=lambda entry: (-entry["dwell_seconds"], entry["group"]))
    return out


def room_index_of(rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Room label lookup derived from the rows themselves.

    Read surfaces project the room name from the inventory before calling this;
    the rollup stays a pure function of the rows it was handed, so it can be
    tested without a store.
    """
    return {
        str(row.get("digital_sales_room_id")): str(row.get("room_name") or "")
        for row in rows
        if row.get("digital_sales_room_id")
    }


def geography_rollup(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Country / state / city rollup with a centroid.

    A centroid is the mean of whatever coordinates arrived. The research documents
    no geocoding service and none is invented here; rows without coordinates are
    counted so the centroid is never mistaken for complete.
    """
    buckets = _rollup(rows, "country")
    out: list[dict[str, Any]] = []
    for country, group in buckets.items():
        states: list[dict[str, Any]] = []
        for state, state_rows in _rollup(group, "state").items():
            cities: list[dict[str, Any]] = []
            for city, city_rows in _rollup(state_rows, "city").items():
                cities.append(
                    {
                        "city": city,
                        "sessions": len(city_rows),
                        "visitors": len({str(row.get("viewer_key")) for row in city_rows}),
                        "dwell_seconds": _dwell_total(city_rows),
                    }
                )
            cities.sort(key=lambda entry: (-entry["dwell_seconds"], entry["city"]))
            states.append(
                {
                    "state": state,
                    "sessions": len(state_rows),
                    "visitors": len({str(row.get("viewer_key")) for row in state_rows}),
                    "dwell_seconds": _dwell_total(state_rows),
                    "cities": cities,
                }
            )
        states.sort(key=lambda entry: (-entry["dwell_seconds"], entry["state"]))
        centroid = _centroid(group)
        out.append(
            {
                "country": country,
                "sessions": len(group),
                "visitors": len({str(row.get("viewer_key")) for row in group}),
                "dwell_seconds": _dwell_total(group),
                "rows_with_coordinates": centroid["rows_with_coordinates"],
                "centroid": centroid["centroid"],
                "states": states,
            }
        )
    out.sort(key=lambda entry: (-entry["dwell_seconds"], entry["country"]))
    return out


def _centroid(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    latitudes = [
        float(row["geolocation_latitude"])
        for row in rows
        if isinstance(row.get("geolocation_latitude"), (int, float))
        and not isinstance(row.get("geolocation_latitude"), bool)
    ]
    longitudes = [
        float(row["geo_location_longitude"])
        for row in rows
        if isinstance(row.get("geo_location_longitude"), (int, float))
        and not isinstance(row.get("geo_location_longitude"), bool)
    ]
    if not latitudes or not longitudes or len(latitudes) != len(longitudes):
        return {"centroid": None, "rows_with_coordinates": min(len(latitudes), len(longitudes))}
    return {
        "centroid": {
            "latitude": round(sum(latitudes) / len(latitudes), 6),
            "longitude": round(sum(longitudes) / len(longitudes), 6),
        },
        "rows_with_coordinates": len(latitudes),
    }


def viewer_rollup(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Per-user engagement: sessions, dwell, rooms, first and last seen.

    "internal-vs-external" is carried per viewer, because a seller previewing a
    room and a buyer reading it are the same shape of event with a different
    meaning, and collapsing them is how a dashboard reports a team's own time
    spent as buyer engagement.
    """
    out: list[dict[str, Any]] = []
    for viewer, group in _rollup(rows, "viewer_key").items():
        first, last = _first_last(group)
        internal = any(row.get("is_engagement_user_internal") is True for row in group)
        external = any(row.get("is_engagement_user_internal") is False for row in group)
        dwell = _dwell_total(group)
        out.append(
            {
                "viewer_key": viewer,
                "viewer_kind": str(group[0].get("viewer_kind") or "anonymous"),
                "engagement_user_email": group[0].get("engagement_user_email"),
                "user_id": group[0].get("user_id"),
                "is_internal": internal,
                "is_external": external,
                "sessions": len(group),
                "dwell_seconds": dwell,
                "dwell_minutes": round(dwell / 60.0, 2),
                "rooms": sorted({str(row.get("digital_sales_room_id")) for row in group}),
                "first_session_at": first,
                "last_session_at": last,
                "countries": sorted({str(row["country"]) for row in group if row.get("country")}),
            }
        )
    out.sort(key=lambda entry: (-entry["dwell_seconds"], entry["viewer_key"]))
    return out


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ";".join(_csv_cell(item) for item in value)
    if isinstance(value, Mapping):
        return json.dumps(value, sort_keys=True, default=str)
    return str(value)


def to_csv(rows: Sequence[Mapping[str, Any]], fields: Sequence[str] | None = None) -> str:
    """Render rows as RFC 4180 CSV, which is what ``Accept: text/csv`` buys.

    Defaults to the documented session fields so a flat-file load lands the same
    vocabulary the dictionary publishes, in the same order.
    """
    columns = list(fields or SESSION_FIELD_NAMES)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_csv_cell(row.get(column)) for column in columns])
    return buffer.getvalue()


def negotiate_format(accept: str | None, requested: str | None = None) -> str:
    """Resolve the output format from ``Accept``, with a query-string fallback.

    The header is the documented mechanism ("Set ``Accept: application/json`` or
    ``Accept: text/csv``"); ``?format=`` exists because a browser link cannot set
    one, which is the only reason it is here.
    """
    if requested:
        text = str(requested).strip().casefold()
        if text in ("csv", "text/csv"):
            return ACCEPT_CSV
        if text in ("json", "application/json"):
            return ACCEPT_JSON
        raise PayloadError(f"unsupported format {requested!r}; expected csv or json")

    header = (accept or "").casefold()
    if "text/csv" in header:
        return ACCEPT_CSV
    if (
        header
        and "application/json" not in header
        and "*/*" not in header
        and "text/*" not in header
    ):
        # Something asked for a representation this workflow does not produce.
        raise PayloadError(
            f"unsupported Accept {accept!r}; this extraction produces {ACCEPT_JSON} or {ACCEPT_CSV}"
        )
    return ACCEPT_JSON


# --------------------------------------------------------------------------- #
# The published contract
# --------------------------------------------------------------------------- #

#: The shape this feature lands, aligned to the star schema Seismic recommends for
#: the consuming BI layer ("Most modern business intelligence platforms require a
#: form of star schema"). The fact grain is a tab session, which is the only
#: grain the documented endpoint actually offers.
STAR_SCHEMA: dict[str, Any] = {
    "fact": {
        "table": SESSIONS,
        "grain": "one row per viewing session: one user, one DSR link, one browser tab",
        "key": ["digital_sales_room_id", "viewer_key", "session_started_at"],
        "measures": ["room_duration_seconds", "elapsed_duration_seconds", "sessions"],
        "time": ["session_started_at", "session_ended_at", "modified_at"],
    },
    "dimensions": [
        {
            "table": ROOMS,
            "key": ["digital_sales_room_id"],
            "attributes": [
                "name",
                "digital_sales_room_template_id",
                "digital_sales_room_template_version_id",
                "created_by_username",
            ],
        },
        {
            "name": "viewer",
            "derived_from": [
                "viewer_key",
                "engagement_user_email",
                "user_id",
                "is_engagement_user_internal",
            ],
            "note": "internal-versus-external is a dimension, not a filter: a seller "
            "previewing a room and a buyer reading it share a shape.",
        },
        {
            "name": "geography",
            "derived_from": [
                "country",
                "state",
                "city",
                "geolocation_latitude",
                "geo_location_longitude",
            ],
            "note": "ip_address is stored but withheld from read surfaces unless asked for.",
        },
        {
            "name": "extraction_run",
            "table": RUNS,
            "key": ["window.kind", "window.start", "window.end"],
            "attributes": ["state", "counters", "watermark_after", "format"],
            "note": "the incremental lineage: which window produced which rows",
        },
    ],
}

#: Verbatim evidence from the research, published so a generated ETL job and a
#: reviewer can both check the contract against its sources.
EVIDENCE: dict[str, str] = {
    "granularity": (
        "Each row represents a single session in a DSR link from a single user in "
        "a single browser tab."
    ),
    "etl_intent": (
        "The reporting APIs are built with data extraction (ETL) in mind. They are "
        "intended to allow large portions of data to be extracted to an external "
        "database/data lake/data warehouse."
    ),
    "refresh_sla": (
        "The data that is available through our reporting APIs is updated no less "
        "than every 24 hours."
    ),
    "refresh_sla_emphasis": "no less than every 24 hours",
    "modified_at_semantics": (
        "modifiedAtStartTime / EndTime (preferred for all use cases) - This is a data "
        "modifiedAt time and has no 'business' meaning. It is meant entirely for "
        "machines to know what rows may have changed so that it can pull the updates "
        "and merge them into existing data sets."
    ),
    "star_schema": "Most modern business intelligence platforms require a form of star schema.",
    "high_frequency_warning": "are not designed to be used in high-frequency, interactive use cases.",
    "viewing_sessions_summary": (
        "Provides the list of viewing sessions by DSR recipients including the "
        "datetime that the session started and ended."
    ),
    "response_example": (
        '{"roomDurationSeconds": 1, "engagementUserEmail": "test engagementUserEmail", '
        '"isEngagementUserInternal": true, "city": "test city", "ipAddress": "test '
        'ipAddress", "geolocationLatitude": 4}'
    ),
}

SOURCES: tuple[str, ...] = (
    "https://api.seismic.com/reporting/v2/digitalSalesRoomViewingSessions",
    "https://api.seismic.com/reporting/v2/digitalSalesRooms",
    "https://developer.seismic.com/seismicsoftware/reference/h1-reporting-api-overview",
    "https://developer.seismic.com/seismicsoftware/reference/reporting-digitalsalesroomsget",
    "https://developer.seismic.com/seismicsoftware/reference/reporting-digitalsalesroomviewingsessionsget",
)


def contract() -> dict[str, Any]:
    """The machine-readable extraction contract, published over HTTP.

    This is the artefact an ETL job is generated from: the endpoints, the
    authentication header, the ``Accept`` values, the documented query parameters
    with their semantics, the documented response fields, the camelCase-to-
    snake_case mapping this feature stores them under, the refresh SLA, the row
    granularity, the room join, and the star shape the rows are landed in.
    """
    return {
        "ticket": "WF-020",
        "name": "Extract DSR viewing sessions (dwell time + geography) for BI",
        "base_url": "https://api.seismic.com",
        "pulled_by": "an external ETL job; this service lands what the job pulls",
        "authentication": {
            "scheme": "Bearer",
            "header": "Authorization",
            "value": "Bearer <JWT>",
            "note": (
                "A Seismic JWT is generated in the Seismic admin console and sent on "
                "every call. It is deliberately not stored here: this service makes no "
                "call, so a stored token would have no consumer."
            ),
        },
        "accept": list(ACCEPT_FORMATS),
        "endpoints": [
            {
                "name": "digitalSalesRoomViewingSessions",
                "method": "GET",
                "path": "/reporting/v2/digitalSalesRoomViewingSessions",
                "summary": EVIDENCE["viewing_sessions_summary"],
                "query": {
                    LIMIT_PARAM: {
                        "required": False,
                        "type": "integer",
                        "note": "Page size. Omit it to take the server default; no "
                        "default is invented here.",
                    },
                    "modifiedAtStartTime": {
                        "required": False,
                        "type": "timestamp",
                        "preferred": True,
                        "semantics": EVIDENCE["modified_at_semantics"],
                    },
                    "modifiedAtEndTime": {
                        "required": False,
                        "type": "timestamp",
                        "preferred": True,
                        "bound": "exclusive in this feature",
                    },
                    "sessionStartedAtTime": {
                        "required": False,
                        "type": "timestamp",
                        "preferred": False,
                        "note": "The documented alternative to the modifiedAt "
                        "pair. Alternatives are not combined: the API "
                        "publishes no semantics for that.",
                    },
                    "sessionEndedAtTime": {
                        "required": False,
                        "type": "timestamp",
                        "preferred": False,
                    },
                },
                "fields": [dict(field) for field in VIEWING_SESSION_FIELDS],
            },
            {
                "name": "digitalSalesRooms",
                "method": "GET",
                "path": "/reporting/v2/digitalSalesRooms",
                "summary": "Room inventory, joined onto every session row by digitalSalesRoomId.",
                "query": {
                    LIMIT_PARAM: {"required": False, "type": "integer"},
                    "modifiedAtStartTime": {
                        "required": False,
                        "type": "timestamp",
                        "preferred": True,
                        "semantics": EVIDENCE["modified_at_semantics"],
                    },
                    "modifiedAtEndTime": {
                        "required": False,
                        "type": "timestamp",
                        "preferred": True,
                    },
                    "createdAtStartTime": {
                        "required": False,
                        "type": "timestamp",
                        "preferred": False,
                    },
                    "createdAtEndTime": {
                        "required": False,
                        "type": "timestamp",
                        "preferred": False,
                    },
                },
                "fields": [dict(field) for field in ROOM_FIELDS],
            },
        ],
        "date_filter": {
            "preferred": PREFERRED_KIND,
            "kinds": {
                "viewing_sessions": {name: list(params) for name, params in SESSION_KINDS.items()},
                "digital_sales_rooms": {name: list(params) for name, params in ROOM_KINDS.items()},
            },
            "boundary": "half-open [start, end): start inclusive, end exclusive",
            "one_kind_per_window": True,
        },
        "granularity": {
            "one_row_is": "a single session in a DSR link from a single user in a single browser tab",
            "consequence": "a row count is a session count, not a visitor or visit count",
            "quote": EVIDENCE["granularity"],
        },
        "join": {
            "on": "digitalSalesRoomId",
            "to": "/reporting/v2/digitalSalesRooms",
            "resolved": "on read, from the landed inventory, so a renamed room never "
            "leaves a stale label inside a session row",
            "unresolved_rows": "landed and flagged room_unresolved; the row is never dropped",
        },
        "merge": {
            "key": ["digital_sales_room_id", "viewer_key", "session_started_at"],
            "behaviour": "a re-landed row updates the row already stored",
            "why": EVIDENCE["modified_at_semantics"],
            "limitation": "the documented field list carries no session identifier, so the "
            "key is derived; two sessions sharing a user, a room and a start "
            "second would collide. An explicit id is preferred the moment one "
            "arrives.",
        },
        "refresh": {
            "sla_hours": REFRESH_SLA_HOURS,
            "quote": EVIDENCE["refresh_sla"],
            "emphasis": EVIDENCE["refresh_sla_emphasis"],
            "warning": EVIDENCE["high_frequency_warning"],
            "consequence": "the sweep pages by the SLA, and a window narrower than it is "
            "annotated as not fresher rather than refused",
        },
        "fields": {
            "digitalSalesRoomViewingSessions": [dict(field) for field in VIEWING_SESSION_FIELDS],
            "digitalSalesRooms": [dict(field) for field in ROOM_FIELDS],
        },
        "field_mapping": {
            "digitalSalesRoomViewingSessions": dict(SESSION_FIELD_MAP),
            "digitalSalesRooms": dict(ROOM_FIELD_MAP),
        },
        "star_schema": STAR_SCHEMA,
        "compute_in_bi": [
            "dwell time",
            "geography",
            "internal-versus-external",
            "per-user engagement",
        ],
        "evidence": dict(EVIDENCE),
        "sources": list(SOURCES),
    }
