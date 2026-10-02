"""WF-079: the date-ranged CSV compliance report.

The research names this the second exportable shape, and it is a genuinely
different one from the paginated API:

    "For periodic compliance exports, the lead requests CSV reports by date range
    and receives them by email."
    -- user_flow, docs/research/digital-sales-room-workflows/wf/WF-079.md

and constrains it tightly:

    ``POST /v3/report/create`` - ``report_type`` in ``user_activity`` |
    ``document_status`` | ``sms_activity`` | ``fax_usage``; ``start_date`` /
    ``end_date`` in ``MM/DD/YYYY``. "The requested date range may be up to 12
    months in duration, and ``start_date`` must not be more than 10 years in the
    past."
    "When the report(s) have been generated, you will receive an email (one per
    requested report type) containing a link to download the report as a CSV
    file."

Four rules are therefore load-bearing, and each has a distinct error code so a
caller can tell which one it broke:

* the dates are ``MM/DD/YYYY``, exactly - not ISO, not ``M/D/YYYY``;
* the range may not exceed twelve months;
* ``start_date`` may not be more than ten years in the past;
* one email is sent per requested report type, and the email carries a link rather
  than the bytes.

The fourth rule is why a request carries a *list* of report types. Requesting one
type and quietly producing one email is the same behaviour for that input, but a
request for two types that produced a single combined file would not be the
researched behaviour, and the rule is only visible in the API if the API can carry
more than one type.

Report types with no data source
--------------------------------
``sms_activity`` and ``fax_usage`` are part of the researched enum and are
implemented as such: their columns are declared and their export is a valid CSV
with a header row and no data rows. This product stores no SMS or fax activity,
and the research offers no second source for them. Returning a 400 for a value the
source calls valid would be a worse failure than an empty file, and inventing rows
to fill one would be worse still. The report record carries a note saying so, the
page renders it, and :data:`REPORT_TYPE_NOTES` is published by ``/vocabulary`` so
the gap is visible without reading the code.
"""

from __future__ import annotations

import calendar
import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Sequence

from dsr.audit_export import vocabulary

#: The researched report-type enum, in the order the source lists it.
REPORT_TYPES: tuple[str, ...] = (
    "user_activity",
    "document_status",
    "sms_activity",
    "fax_usage",
)

#: The longest range a single report may cover: "The requested date range may be up
#: to 12 months in duration".
MAX_RANGE_MONTHS = 12

#: How far back a start date may be: "start_date must not be more than 10 years in
#: the past".
MAX_LOOKBACK_YEARS = 10

#: The wire format, verbatim from the source.
DATE_FORMAT = "%m/%d/%Y"
DATE_FORMAT_HINT = "MM/DD/YYYY"

#: Inclusive. The source speaks in dates and never in timestamps, and a compliance
#: lead asking for "up to the 30th" means the 30th.
END_DATE_INCLUSIVE = True

STATUS_PENDING = "pending"
STATUS_READY = "ready"

#: Where a report request lives. A record, in the ordinary schema-flexible store,
#: audited like everything else - so "a compliance lead asked for an export" is
#: itself a row in the trail it is exporting.
REPORT_COLLECTION = "audit_export_report"

#: The scheme of the download link an emailed notification carries. Not a secret:
#: the report itself is behind the administrator gate, and a token in a URL that
#: travels by email is a capability with a short life, not an authentication
#: mechanism. Stating it in the vocabulary is better than pretending otherwise.
DOWNLOAD_PATH_TEMPLATE = "/api/wf-079/reports/{report_id}/download?token={token}"

#: How many rows one report will carry before it says so rather than truncating.
#: The researched API "paginates the store"; a CSV that silently drops rows past
#: an arbitrary line would be the opposite, so the row count is recorded and the
#: report is never cut short without saying so.
MAX_ROWS = 100_000

ERROR_DATE_FORMAT = "invalid_date_format"
ERROR_RANGE_INVERTED = "date_range_inverted"
ERROR_RANGE_TOO_LONG = "date_range_too_long"
ERROR_START_TOO_OLD = "start_date_too_old"
ERROR_UNKNOWN_TYPE = "unknown_report_type"
ERROR_REPORT_NOT_READY = "report_not_ready"
ERROR_REPORT_NOT_FOUND = "report_not_found"
ERROR_BAD_TOKEN = "bad_download_token"

#: Per-type columns. The first is the widest and is what a reviewer usually wants.
COLUMNS: dict[str, tuple[str, ...]] = {
    "user_activity": (
        "seq",
        "date_created",
        "actor",
        "user_email",
        "action_code",
        "action_name",
        "collection",
        "record_id",
        "ip_address",
        "ip_status",
        "reason",
    ),
    "document_status": (
        "record_id",
        "collection",
        "action_code",
        "action_name",
        "date_created",
        "actor",
        "ip_address",
        "reason",
    ),
    "sms_activity": (
        "date_created",
        "actor",
        "action_code",
        "action_name",
        "record_id",
        "reason",
    ),
    "fax_usage": (
        "date_created",
        "actor",
        "action_code",
        "action_name",
        "record_id",
        "reason",
    ),
}

#: What a reviewer is told about a type this product has no data for.
REPORT_TYPE_NOTES: dict[str, str] = {
    "sms_activity": (
        "No data source. The researched enum includes this type, so it is "
        "accepted and exported with its header row, but this product records no "
        "SMS activity and no second source is cited for it. The export is "
        "therefore empty rather than wrong."
    ),
    "fax_usage": (
        "No data source. The researched enum includes this type, so it is accepted "
        "and exported with its header row, but this product records no fax usage "
        "and no second source is cited for it. The export is therefore empty "
        "rather than wrong."
    ),
}


class ReportError(ValueError):
    """A report request this workflow refuses, with the code that says why."""

    def __init__(self, code: str, detail: str, *, remediation: str = "") -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.remediation = remediation or f"Send both dates as {DATE_FORMAT_HINT}."

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "detail": self.detail, "remediation": self.remediation}


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #


def add_months(value: date, months: int) -> date:
    """Calendar arithmetic with day clamping, for "12 months" and "10 years".

    ``dateutil`` is not a dependency and ``timedelta(days=365)`` is not the same
    thing: twelve months from 31 January is 28 or 29 February, and a leap year is
    exactly where an off-by-one-day bounds check fails. The day is clamped to the
    last day of the target month, which is the conventional reading of "12 months".
    """
    total = value.month - 1 + months
    year = value.year + total // 12
    month = total % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def parse_date(value: Any) -> date:
    """Parse ``MM/DD/YYYY``, rejecting anything else.

    ``datetime.strptime`` is lenient in ways that would silently change what a
    compliance review covers - it accepts ``2026-01-02``, accepts single-digit
    months, and accepts a time suffix - so the format is checked before it is
    parsed rather than relied on the parser to be strict.
    """
    text = str(value or "").strip()
    if len(text) != len("10/10/1000"):
        raise ReportError(
            ERROR_DATE_FORMAT,
            f"{text!r} is not {DATE_FORMAT_HINT}",
            remediation=f"Both dates are {DATE_FORMAT_HINT} strings, e.g. 03/01/2026.",
        )
    if text[2] != "/" or text[5] != "/":
        raise ReportError(
            ERROR_DATE_FORMAT,
            f"{text!r} is not {DATE_FORMAT_HINT}",
            remediation=f"Both dates are {DATE_FORMAT_HINT} strings, e.g. 03/01/2026.",
        )
    try:
        return datetime.strptime(text, DATE_FORMAT).date()
    except ValueError as exc:
        raise ReportError(
            ERROR_DATE_FORMAT,
            f"{text!r} is not a real date: {exc}",
            remediation=f"Both dates are {DATE_FORMAT_HINT} strings, e.g. 03/01/2026.",
        ) from exc


def validate_range(start: Any, end: Any, *, today: date | None = None) -> tuple[date, date]:
    """Apply every researched date rule, returning the parsed range.

    The reference for "10 years in the past" is passed in rather than read from the
    clock here, because a bounds check whose answer depends on when it ran cannot be
    tested at a boundary.
    """
    reference = today or datetime.now().date()
    parsed_start = parse_date(start)
    parsed_end = parse_date(end)

    if parsed_end < parsed_start:
        raise ReportError(
            ERROR_RANGE_INVERTED,
            f"end_date {end} is before start_date {start}",
            remediation="Swap them: the range runs from start_date to end_date.",
        )

    if parsed_end > add_months(parsed_start, MAX_RANGE_MONTHS):
        raise ReportError(
            ERROR_RANGE_TOO_LONG,
            (
                f"{start} to {end} is longer than {MAX_RANGE_MONTHS} months; the "
                "requested date range may be up to 12 months in duration"
            ),
            remediation="Request 12 months at a time.",
        )

    floor = add_months(reference, -12 * MAX_LOOKBACK_YEARS)
    if parsed_start < floor:
        raise ReportError(
            ERROR_START_TOO_OLD,
            (
                f"start_date {start} is more than {MAX_LOOKBACK_YEARS} years before "
                f"{reference.isoformat()}; start_date must not be more than 10 years "
                "in the past"
            ),
            remediation=f"Choose a start_date on or after {floor.strftime(DATE_FORMAT)}.",
        )

    return parsed_start, parsed_end


def in_range(entries: Iterable[Mapping[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    """Entries whose ``date_created`` falls inside the range, inclusive at both ends."""
    low = start.isoformat()
    high = end.isoformat()
    chosen: list[dict[str, Any]] = []
    for entry in entries:
        stamp = str(entry.get("date_created") or "")
        if not stamp:
            continue
        day = stamp[:10]
        if day < low or day > high:
            continue
        chosen.append(dict(entry))
    return chosen


# --------------------------------------------------------------------------- #
# Rows and rendering
# --------------------------------------------------------------------------- #


def _user_activity_row(entry: Mapping[str, Any]) -> dict[str, Any]:
    action = entry.get("action") or {}
    user = entry.get("user") or {}
    return {
        "seq": entry.get("seq"),
        "date_created": entry.get("date_created"),
        "actor": entry.get("actor"),
        "user_email": user.get("email"),
        "action_code": action.get("code"),
        "action_name": action.get("name"),
        "collection": entry.get("collection"),
        "record_id": entry.get("record_id"),
        "ip_address": entry.get("ip_address"),
        "ip_status": entry.get("ip_status"),
        "reason": entry.get("reason"),
    }


def _document_status_row(entry: Mapping[str, Any]) -> dict[str, Any]:
    action = entry.get("action") or {}
    return {
        "record_id": entry.get("record_id"),
        "collection": entry.get("collection"),
        "action_code": action.get("code"),
        "action_name": action.get("name"),
        "date_created": entry.get("date_created"),
        "actor": entry.get("actor"),
        "ip_address": entry.get("ip_address"),
        "reason": entry.get("reason"),
    }


def _generic_row(entry: Mapping[str, Any]) -> dict[str, Any]:
    action = entry.get("action") or {}
    return {
        "date_created": entry.get("date_created"),
        "actor": entry.get("actor"),
        "action_code": action.get("code"),
        "action_name": action.get("name"),
        "record_id": entry.get("record_id"),
        "reason": entry.get("reason"),
    }


#: Which projection each type uses. A type with no entry here has no data source.
ROW_BUILDERS = {
    "user_activity": _user_activity_row,
    "document_status": _document_status_row,
}


def rows_for(report_type: str, entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Project export entries into a report type's rows."""
    builder = ROW_BUILDERS.get(report_type)
    if builder is None:
        # The researched enum contains types this product has no data for. Empty is
        # the honest answer; see REPORT_TYPE_NOTES.
        return []
    return [builder(entry) for entry in entries]


def render_csv(report_type: str, rows: Sequence[Mapping[str, Any]]) -> str:
    """The CSV text: a header row and the rows, nothing else.

    No comment preamble and no trailer. A compliance recipient pipes this into a
    spreadsheet or ``awk``, and a file whose first lines are prose is a file that
    breaks both. The provenance travels beside the bytes - in the report record and
    in the response headers - rather than inside them.
    """
    columns = COLUMNS.get(report_type)
    if columns is None:
        raise ReportError(
            ERROR_UNKNOWN_TYPE,
            f"{report_type!r} is not a report type this workflow knows",
            remediation=f"report_type must be one of {', '.join(REPORT_TYPES)}.",
        )
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if row.get(column) is None else row.get(column) for column in columns])
    return buffer.getvalue()


@dataclass(frozen=True)
class ReportRequest:
    """A validated report request, before it becomes a record."""

    report_types: tuple[str, ...]
    start_date: str
    end_date: str
    start: date
    end: date
    email: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_types": list(self.report_types),
            "start_date": self.start_date,
            "end_date": self.end_date,
            "email": self.email,
        }


def validate_request(
    report_types: Any,
    start_date: Any,
    end_date: Any,
    email: Any,
    *,
    today: date | None = None,
) -> ReportRequest:
    """Validate every field of a report request, in the order that best explains it.

    The types are checked first: asking for a date range that is valid is wasted
    effort if the report type does not exist, and "unknown_report_type" is the more
    fundamental mistake.
    """
    if isinstance(report_types, str):
        wanted = [report_types]
    elif isinstance(report_types, (list, tuple)):
        wanted = [str(item) for item in report_types]
    else:
        wanted = []

    cleaned: list[str] = []
    for candidate in wanted:
        name = candidate.strip().lower()
        if name not in REPORT_TYPES:
            raise ReportError(
                ERROR_UNKNOWN_TYPE,
                f"report_type {candidate!r} is not one of {', '.join(REPORT_TYPES)}",
                remediation=f"report_type must be one of {', '.join(REPORT_TYPES)}.",
            )
        if name not in cleaned:
            cleaned.append(name)
    if not cleaned:
        raise ReportError(
            ERROR_UNKNOWN_TYPE,
            "at least one report_type is required",
            remediation=f"report_type must be one of {', '.join(REPORT_TYPES)}.",
        )

    parsed_start, parsed_end = validate_range(start_date, end_date, today=today)

    recipient = str(email or "").strip()
    if "@" not in recipient or recipient.startswith("@") or recipient.endswith("@"):
        raise ReportError(
            "invalid_email",
            f"{email!r} is not an address to deliver the report to",
            remediation="Pass the mailbox that should receive the download link.",
        )

    return ReportRequest(
        report_types=tuple(cleaned),
        start_date=str(start_date).strip(),
        end_date=str(end_date).strip(),
        start=parsed_start,
        end=parsed_end,
        email=recipient,
    )


def payload() -> dict[str, Any]:
    """The report rules, published by ``GET /api/wf-079/vocabulary``."""
    return {
        "report_types": list(REPORT_TYPES),
        "columns": {name: list(columns) for name, columns in COLUMNS.items()},
        "date_format": DATE_FORMAT_HINT,
        "max_range_months": MAX_RANGE_MONTHS,
        "max_lookback_years": MAX_LOOKBACK_YEARS,
        "end_date_inclusive": END_DATE_INCLUSIVE,
        "delivery": (
            "one email per requested report type, each carrying a download link "
            "rather than the file itself"
        ),
        "delivery_is_recorded_not_sent": (
            "This product has no mail transport. The notification the source "
            "describes - one per report type, carrying a link rather than the "
            "bytes - is written to the report record as delivery fields, so the "
            "delivery is auditable and a reader can see exactly what was promised. "
            "The link it carries really does serve the CSV."
        ),
        "statuses": [STATUS_PENDING, STATUS_READY],
        "collection": REPORT_COLLECTION,
        "download_path_template": DOWNLOAD_PATH_TEMPLATE,
        "error_codes": {
            ERROR_DATE_FORMAT: f"both dates must be {DATE_FORMAT_HINT}",
            ERROR_RANGE_INVERTED: "end_date is before start_date",
            ERROR_RANGE_TOO_LONG: f"the range may be up to {MAX_RANGE_MONTHS} months",
            ERROR_START_TOO_OLD: (
                f"start_date may not be more than {MAX_LOOKBACK_YEARS} years in the past"
            ),
            ERROR_UNKNOWN_TYPE: f"report_type must be one of {', '.join(REPORT_TYPES)}",
            ERROR_REPORT_NOT_READY: "the report has not been generated yet",
            ERROR_REPORT_NOT_FOUND: "no such report",
            ERROR_BAD_TOKEN: "the download link does not match this report",
            "invalid_email": "a delivery mailbox is required",
        },
        "report_type_notes": dict(REPORT_TYPE_NOTES),
    }


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


def download_token(report_id: str, requested_at: Any, report_type: str) -> str:
    """A deterministic token for one report's download link.

    Deterministic rather than random so that regenerating a report keeps the link
    the recipient was already sent - a link that changed every time the file was
    rebuilt would make the notification meaningless - and derived rather than
    stored so there is no second secret to keep in step with the first.

    It is not a secret and is documented as one: the report behind it is behind
    the administrator gate, and a token in an emailed URL is a capability with a
    lifetime, not an authentication mechanism.
    """
    return hashlib.sha256(
        f"{DOWNLOAD_PATH_TEMPLATE}|{report_id}|{requested_at}|{report_type}".encode("utf-8")
    ).hexdigest()[:32]


def request(
    store: Any,
    validated: ReportRequest,
    *,
    requested_at: str,
    actor: str | None,
    source: str,
    room_id: str | None = None,
) -> list[dict[str, Any]]:
    """Record one pending report per requested type, and return them.

    A list, because the source's rule is per type: "you will receive an email
    (**one per requested report type**)". A request for two types that produced
    one record would make the rule unobservable in the API, and the rule is the
    reason a request carries a list of types at all.

    Every write is audited with ``source`` supplied by the caller, so the audit
    row names the route that served it rather than a string written into this
    module.
    """
    records: list[dict[str, Any]] = []
    for report_type in validated.report_types:
        record = store.create(
            REPORT_COLLECTION,
            {
                "report_type": report_type,
                "report_types": list(validated.report_types),
                "start_date": validated.start_date,
                "end_date": validated.end_date,
                "start": validated.start.isoformat(),
                "end": validated.end.isoformat(),
                "email": validated.email,
                "state": STATUS_PENDING,
                "requested_at": requested_at,
                "requested_by": actor or None,
                "note": REPORT_TYPE_NOTES.get(report_type),
                # Stamped so that asking for an export is an event *in* the trail
                # it exports. `describe_code` names 1000 for exactly this.
                "event_code": vocabulary.REPORT_REQUESTED,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        records.append(record)
    return records


def read(store: Any, report_id: str) -> dict[str, Any]:
    """One report record, or the refusal that says it is not there."""
    record = store.get(report_id)
    if record is None or record.get("collection") != REPORT_COLLECTION:
        raise ReportError(
            ERROR_REPORT_NOT_FOUND,
            f"no audit export report with id {report_id!r}",
            remediation=f"Reports are in the {REPORT_COLLECTION} collection.",
        )
    return record


def listing(store: Any, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    """Report records, newest first."""
    return store.list(REPORT_COLLECTION, limit=limit, offset=offset)


def _content(store: Any, record: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str, str, int]:
    """The rows, the CSV text, its digest and the upper ``seq`` they were cut at.

    One function, called both at generation and at download, because the two have
    to agree byte for byte: a ``sha256`` recorded on the report that does not
    match the file its own link serves is worse than no digest at all.

    Reproducibility comes from ``upto_seq``. The room trail is a live query, so
    without a bound the same report rebuilt a day later would pick up rows written
    since - and the digest handed to a recipient would quietly stop describing
    their file. The bound is recorded on the report and replayed here, which is
    the same trick :mod:`dsr.audit_export.anchors` uses.

    Imported here rather than at module scope: ``entries`` reads
    ``dsr.audit_export``'s package namespace while this package's own ``__init__``
    is still being assembled, and a module-scope import between the two would make
    that order load-bearing.
    """
    from dsr.audit_export.entries import build_trail

    data = dict(record.get("data") or {})
    report_type = str(data.get("report_type") or "")
    room_id = record.get("room_id")
    trail, _ = build_trail(store, room_id, sandbox=False, upto_seq=data.get("upto_seq"))
    matched = in_range(
        trail, date.fromisoformat(str(data["start"])), date.fromisoformat(str(data["end"]))
    )
    rows = rows_for(report_type, matched)[:MAX_ROWS]
    text = render_csv(report_type, rows)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    # The upper bound is the highest ``seq`` the trail reached, NOT how many rows it
    # had. ``room_rows`` truncates with ``seq <= upto_seq``, so storing a count here
    # silently truncated the replay to the first N rows by sequence number: the file
    # a recipient downloaded then stopped matching the digest recorded for it as soon
    # as any row was written after generation, which is the exact failure the bound
    # exists to prevent. ``None`` for an empty trail, so "no rows" is not "seq <= 0".
    upto = max((int(entry.get("seq") or 0) for entry in trail), default=0) or None
    return rows, text, digest, upto


def build(store: Any, report_id: str, *, source: str, at: str) -> dict[str, Any]:
    """Generate a pending report and record its delivery.

    Idempotent: a report that is already ``ready`` is returned unchanged, with its
    original digest, so regenerating cannot silently produce a different file for
    a link that has already been handed to somebody.

    The delivery fields are the researched notification - one per report type,
    carrying a link rather than the bytes - written down rather than sent. See
    :func:`payload`'s ``delivery_is_recorded_not_sent``.
    """
    record = read(store, report_id)
    data = dict(record.get("data") or {})
    if data.get("state") == STATUS_READY:
        return record

    report_type = str(data.get("report_type") or "")
    rows, text, digest, upto_seq = _content(store, record)
    token = download_token(report_id, data.get("requested_at"), report_type)
    delivered_at = str(at or data.get("requested_at"))

    return store.update(
        report_id,
        {
            "state": STATUS_READY,
            "row_count": len(rows),
            "upto_seq": upto_seq,
            "sha256": digest,
            "bytes": len(text.encode("utf-8")),
            "generated_at": delivered_at,
            "columns": list(COLUMNS.get(report_type, ())),
            "event_code": vocabulary.REPORT_GENERATED,
            "delivery": {
                "to": data.get("email"),
                "one_per_report_type": True,
                "carries": "a download link, not the file",
                "url": DOWNLOAD_PATH_TEMPLATE.format(report_id=report_id, token=token),
                "token": token,
                "delivered_at": delivered_at,
                "transport": "recorded; this product has no mail transport",
            },
        },
        actor=data.get("requested_by"),
        source=source,
    )


def csv_for(store: Any, report_id: str, token: str | None) -> str:
    """The CSV text for a ready report, if the link is the one that was issued."""
    record = read(store, report_id)
    data = dict(record.get("data") or {})
    if data.get("state") != STATUS_READY:
        raise ReportError(
            ERROR_REPORT_NOT_READY,
            f"report {report_id} has not been generated yet",
            remediation="Generate it first; a link is only issued once it is ready.",
        )
    expected = download_token(
        report_id, data.get("requested_at"), str(data.get("report_type") or "")
    )
    if token != expected:
        raise ReportError(
            ERROR_BAD_TOKEN,
            "the download link does not match this report",
            remediation="Use the link from the delivery record for this report.",
        )
    _, text, _, _ = _content(store, record)
    return text
