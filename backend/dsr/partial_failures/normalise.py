"""Turning three vendor error shapes into one room error model.

The research's data flow for this workflow is a transformation, and this module
*is* it: "failing row -> vendor per-item error object (``errors[]`` with
``message``/``code``/``context``, or Dataverse ``{"error":{"code","message"}}``
with ``HelpLink`` annotations) -> normalised room-side error record keyed to the
row -> retry queue. Transformation: vendor-specific error shapes collapse into
one room error model."

The room error model is the five keys the extensibility note names - ``{retryable,
field, code, message, docLink}`` - and everything else on
:class:`NormalisedError` is provenance, there so that a decision can be audited
rather than taken on trust:

``basis``
    Why this error was classified the way it was, quoting the source where there
    is one. This is the field that makes the classification table reviewable
    instead of a list of booleans, and it is asserted in the tests.
``matched_rule``
    Which classification entry decided ``retryable``, or ``None`` for the default.

Four hard facts this module refuses to get wrong, three of them quoted:

1. **A Dataverse ``$batch`` answers ``200 OK`` whether or not requests inside it
   failed.** Reading 200 as success reports a clean sync for a batch where
   nothing was written, so :func:`normalise` reads the body whatever the status.
2. **HubSpot's ``207 Multi-Status`` is a success status.** It means there are
   different statuses in the batch. Treating it as an error would log every
   partially-successful sync as a failure.
3. **A row the response says nothing about is not a success.** It was sent, and
   nothing says it was accepted. It is recorded as failed under
   ``NO_PER_RECORD_OUTCOME`` and waits for a person.
4. **A result that cannot be keyed to a row is not dropped.** The research asks
   for the error record to be "keyed to the row", and the same sentence means
   that a result arriving with no ``objectWriteTraceId`` - or with one naming a
   row that was not in the batch - is kept, unattributed, on the run. A dropped
   error is a row the log will never show anyone fixing.

What is *not* claimed: a correlation key for Dataverse or Salesforce. The source
set names only HubSpot's ``objectWriteTraceId``, so those two fall back to request
order, every row records which basis its correlation rests on, and the fact is
published as an inference rather than presented as sourced. The error *count* to
reconcile against is likewise HubSpot's: ``numErrors`` is the only count the
research quotes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as _field
from typing import Any, Mapping, Sequence

from dsr.partial_failures.errors import InvalidPayload
from dsr.partial_failures.vocabulary import FIELD_SOURCE, require_connector
from dsr.throttle import classify as throttle_classify

# --------------------------------------------------------------------------- #
# The room error model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class NormalisedError:
    """One vendor failure, as the room sees it.

    The first five fields are the researched model, in the research's order. The
    rest is provenance, so a reader can tell which of two defensible readings
    produced a row.
    """

    retryable: bool
    field: str | None
    code: str
    message: str
    doc_link: str | None

    vendor: str = ""
    scope: str = "row"
    http_status: int | None = None
    category: str | None = None
    correlation: str | None = None
    basis: str = ""
    matched_rule: str | None = None
    related: tuple[dict[str, Any], ...] = ()
    raw: Mapping[str, Any] = _field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "retryable": self.retryable,
            "field": self.field,
            "code": self.code,
            "message": self.message,
            "doc_link": self.doc_link,
            "vendor": self.vendor,
            "scope": self.scope,
            "http_status": self.http_status,
            "category": self.category,
            "correlation": self.correlation,
            "basis": self.basis,
            "matched_rule": self.matched_rule,
            "related": [dict(item) for item in self.related],
            "raw": dict(self.raw),
        }


@dataclass(frozen=True)
class RowOutcome:
    """What became of one input row, once the vendor has answered.

    ``trace_id`` is the value the room sent. ``correlation`` is the value the
    vendor sent back, and ``correlation_basis`` says which researched key it came
    from - or that it did not come from one at all.
    """

    row_key: str
    index: int
    status: str
    entity: str = ""
    trace_id: str | None = None
    correlation: str | None = None
    correlation_basis: str = "sent"
    error: NormalisedError | None = None
    vendor_record_id: str | None = None


@dataclass(frozen=True)
class BatchOutcome:
    """The whole of a batch result, normalised.

    ``per_record`` answers the researched step two - did the connector actually
    get per-record outcomes - and it is what a caller needs before it trusts
    ``rows``. A batch answered without per-record outcomes has one verdict applied
    to everything, which is precisely the failure this workflow exists to surface.
    """

    connector: str
    http_status: int
    per_record: bool
    rows: tuple[RowOutcome, ...]
    request: NormalisedError | None = None
    unattributed: tuple[NormalisedError, ...] = ()
    reported_errors: int | None = None
    described_errors: int = 0
    consistent: bool | None = None
    notes: tuple[str, ...] = ()

    @property
    def succeeded(self) -> int:
        return sum(1 for row in self.rows if row.status == "succeeded")

    @property
    def failed(self) -> int:
        return sum(1 for row in self.rows if row.status == "failed")

    def as_dict(self) -> dict[str, Any]:
        return {
            "connector": self.connector,
            "http_status": self.http_status,
            "per_record": self.per_record,
            "rows": len(self.rows),
            "succeeded": self.succeeded,
            "failed": self.failed,
            "request_error": self.request.as_dict() if self.request else None,
            "unattributed": [error.as_dict() for error in self.unattributed],
            "reported_errors": self.reported_errors,
            "described_errors": self.described_errors,
            "consistent": self.consistent,
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ClassificationEntry:
    """One row of the retryable-versus-terminal table.

    Every criterion left empty matches anything, and the **first** matching entry
    wins, so the table is ordered most specific first. ``basis`` is mandatory: an
    entry nobody can trace to a sentence is a guess with a boolean attached.
    """

    id: str
    retryable: bool
    basis: str
    connector: str = ""
    code: str = ""
    http_status: int | None = None


#: The researched codes, then the transport-level classes, then the default.
#:
#: Read top to bottom. The first five entries cite the source set; the rest are
#: the reading that goes from "rate limit and locked are the retryable classes"
#: to an actual code, and each one says in its ``basis`` that it is doing that.
#:
#: **The two rate-limit rows are not written here.** Which vendor answers are a
#: throttle is :mod:`dsr.throttle.classify`'s question, and it is answered in one
#: table that WF-046's own queue and ``dsr.integ_monitor``'s dashboard read as
#: well. The ``status`` and ``code`` below are therefore read from that table
#: rather than typed in: a second copy of "429 is a throttle" is a second answer
#: to the same question, and the ids, the ``retryable`` values and the ordering are
#: unchanged, so this table classifies exactly what it classified before.
_REQUEST_LIMIT = throttle_classify.signal_for(
    "salesforce",
    throttle_classify.REQUEST_LIMIT_HTTP_STATUS,
    throttle_classify.REQUEST_LIMIT_EXCEEDED,
)
_THROTTLED = next(signal for signal in throttle_classify.SIGNALS if signal.id == "throttled")
assert _REQUEST_LIMIT is not None, "the request-limit row must exist for this table to be complete"

CLASSIFICATION: tuple[ClassificationEntry, ...] = (
    ClassificationEntry(
        id="salesforce-request-limit-exceeded",
        retryable=True,
        basis=(
            "Quoted: \"403 ... If the error code is REQUEST_LIMIT_EXCEEDED, you've exceeded API "
            'request limits in your org." An exceeded request limit is the rate-limit class the '
            "researched automation names, and it clears on its own."
        ),
        connector=_REQUEST_LIMIT.vendor,
        code=_REQUEST_LIMIT.code,
        http_status=_REQUEST_LIMIT.status,
    ),
    ClassificationEntry(
        id="dataverse-validation",
        retryable=False,
        basis=(
            "Quoted: Dataverse answers a validation error with 0x80044331 and a message naming the "
            "attribute. A value that violates a configured rule does not become valid by being sent "
            "again, which is the researched split between the automatic queue and the admin."
        ),
        connector="dataverse",
        code="0x80044331",
    ),
    ClassificationEntry(
        id="dataverse-concurrency-mismatch",
        retryable=False,
        basis=(
            'Quoted: "412 Precondition Failed Expect this status code for the following types of '
            'errors: ConcurrencyVersionMismatch - DuplicateRecord". A version mismatch means the '
            "row was written against a version that has moved, and the fix is to re-read it, not to "
            "send it again."
        ),
        connector="dataverse",
        code="ConcurrencyVersionMismatch",
        http_status=412,
    ),
    ClassificationEntry(
        id="dataverse-duplicate-record",
        retryable=False,
        basis=(
            "Quoted alongside ConcurrencyVersionMismatch under 412. A duplicate does not clear on "
            "its own, and WF-008 is the workflow that decides what to do about one."
        ),
        connector="dataverse",
        code="DuplicateRecord",
        http_status=412,
    ),
    ClassificationEntry(
        id="lock-mismatch",
        retryable=True,
        basis=(
            'Inferred from the researched automation, which names "locked" as one of the two '
            "retryable classes. The source set names no code for it: Dataverse documents 412 for "
            "ConcurrencyVersionMismatch and DuplicateRecord, which are the two *other* conditions "
            "under the same status and are treated as terminal here. A lock is the one 412 whose "
            "holder releases it. See the lock-versus-precondition inference."
        ),
        code="LockMismatch",
        http_status=412,
    ),
    ClassificationEntry(
        id="throttled",
        retryable=True,
        basis=(
            'Inferred from the researched automation, which names "rate limit" as a retryable '
            "class without naming a code for it. HTTP 429 is the transport-level signal all three "
            "vendors use for it, and putting a throttled row in a human's queue would defeat the "
            "automation the research describes."
        ),
        http_status=_THROTTLED.status,
    ),
    ClassificationEntry(
        id="bad-request",
        retryable=False,
        basis=(
            "Quoted twice. Salesforce: \"400 The request couldn't be understood, usually because "
            'the JSON or XML body contains an error." Dataverse: "400 BadRequest Expect this '
            'status code when an argument is invalid." Neither improves by being sent again.'
        ),
        http_status=400,
    ),
    ClassificationEntry(
        id="server-fault",
        retryable=True,
        basis=(
            "Inferred: a 5xx is the one class where the identical request is expected to succeed "
            "unchanged. Nothing in the source set mentions it, so this is a reading - and it is one "
            "PATCH away from being terminal, because the automatic drain bounds it."
        ),
        http_status=500,
    ),
    ClassificationEntry(
        id="unauthenticated",
        retryable=False,
        basis=(
            "Inferred: a 401 is a credential, and a credential does not change between two attempts "
            "a minute apart. The research names no status under 401."
        ),
        http_status=401,
    ),
    ClassificationEntry(
        id="forbidden",
        retryable=False,
        basis=(
            "Inferred: a 403 that is not REQUEST_LIMIT_EXCEEDED is a permission, and the research "
            "names only the request-limit case under 403."
        ),
        http_status=403,
    ),
)

#: What an error nobody has a rule for is classified as.
#:
#: Terminal, and the reason matters: the researched automation drains the queue
#: "for retryable classes" and waits for "admin action for validation failures". A
#: code the room has never heard of is not a class it has been told clears on its
#: own, and the researched extension point exists precisely so a deployment can say
#: otherwise. The alternative - guessing retryable - turns an unrecognised
#: validation error into a row that is re-sent until the attempt limit and is
#: still never fixed.
DEFAULT_CLASSIFICATION = "unrecognised"
DEFAULT_BASIS = (
    "No classification rule matched, so this is treated as terminal. The research names exactly two "
    "classes that clear on their own - rate limit and locked - and everything else waits for a "
    "person. A deployment can add a rule for this code through the extensibility point without "
    "changing the transport."
)


def classify(
    *,
    connector: str,
    code: str = "",
    http_status: int | None = None,
    field: str | None = None,
    category: str | None = None,
    message: str = "",
    overrides: Sequence[Mapping[str, Any]] = (),
) -> tuple[bool, str, str | None]:
    """Is this error retryable, on what basis, and by which rule?

    A deployment's routing rules are consulted first and the **last** matching one
    wins, so a deployment can add a narrow rule and then a general one without
    either silently shadowing the other. With no rule in play the built-in table
    answers, and with nothing in the table either the default applies.
    """
    subject: dict[str, Any] = {
        "connector": str(connector or "").strip().lower(),
        "code": str(code or "").strip(),
        "http_status": http_status,
        "field": _text_or_none(field),
        "category": _text_or_none(category),
        "message": str(message or ""),
    }
    override = match_overrides(overrides, subject)
    if override is not None:
        rule, retryable = override
        return retryable, str(rule.get("basis") or DEFAULT_BASIS), str(rule.get("id") or "")

    needle = subject["code"].lower()
    for entry in CLASSIFICATION:
        if entry.connector and entry.connector != subject["connector"]:
            continue
        if entry.code and entry.code.lower() != needle:
            continue
        if entry.http_status is not None and entry.http_status != http_status:
            continue
        return entry.retryable, entry.basis, entry.id

    if http_status is not None and 500 <= int(http_status) <= 599:
        entry = next(item for item in CLASSIFICATION if item.id == "server-fault")
        return entry.retryable, entry.basis, entry.id

    return False, DEFAULT_BASIS, DEFAULT_CLASSIFICATION


#: The keys a routing rule's ``when`` may test. A fixed set, so a typo is refused
#: rather than becoming a rule that matches nothing and looks deliberate.
OVERRIDE_MATCH_KEYS: tuple[str, ...] = (
    "connector",
    "code",
    "code_prefix",
    "field",
    "category",
    "http_status",
    "message_contains",
)

#: The keys a routing rule's ``then`` may set. One key on purpose: the researched
#: extension is "route ... to a manual-review queue instead of retrying", and the
#: disposition follows from ``retryable``, so there is no second knob that could
#: disagree with the first.
OVERRIDE_THEN_KEYS: tuple[str, ...] = ("retryable",)


def match_overrides(
    overrides: Sequence[Mapping[str, Any]], subject: Mapping[str, Any]
) -> tuple[Mapping[str, Any], bool] | None:
    """The last routing rule that matches this error, and the value it sets.

    All keys in a rule's ``when`` must match, so a rule narrows rather than
    widens. A rule with an empty ``when`` is skipped rather than treated as a
    match: a rule that matched everything would be a way to disable classification
    by accident, and it is refused at write time anyway.
    """
    winner: tuple[Mapping[str, Any], bool] | None = None
    for rule in overrides or ():
        when = rule.get("when")
        then = rule.get("then")
        if not isinstance(when, Mapping) or not when:
            continue
        if not isinstance(then, Mapping) or "retryable" not in then:
            continue
        if all(_when_matches(key, expected, subject) for key, expected in when.items()):
            winner = (rule, bool(then["retryable"]))
    return winner


def _when_matches(key: str, expected: Any, subject: Mapping[str, Any]) -> bool:
    if expected in (None, ""):
        return False
    if key == "code_prefix":
        return str(subject.get("code") or "").lower().startswith(str(expected).lower())
    if key == "message_contains":
        return str(expected).lower() in str(subject.get("message") or "").lower()
    if key == "http_status":
        return str(subject.get("http_status")) == str(expected)
    if key in OVERRIDE_MATCH_KEYS:
        return str(subject.get(key) or "").strip().lower() == str(expected).strip().lower()
    return False


# --------------------------------------------------------------------------- #
# The entry point
# --------------------------------------------------------------------------- #


def normalise(
    connector: str,
    status: Any,
    body: Any,
    rows: Sequence[Mapping[str, Any]],
    *,
    overrides: Sequence[Mapping[str, Any]] = (),
) -> BatchOutcome:
    """One vendor response, in this product's error model.

    ``rows`` is what the connector sent, in order, each with a ``row_key`` and
    optionally a ``trace_id``. The row key is required: the research asks for the
    error record to be "keyed to the row", and a row with no key cannot be keyed.
    """
    name = require_connector(connector)
    http_status = _as_int(status)
    if http_status is None:
        raise InvalidPayload(f"the vendor status is required and must be a number; got {status!r}")
    prepared = _prepare_rows(rows)
    return _HANDLERS[name](name, http_status, body, prepared, overrides)


def _prepare_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise InvalidPayload(f"rows must be a list; got {type(rows).__name__}")
    prepared: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise InvalidPayload(f"row {index} must be a JSON object; got {type(row).__name__}")
        key = str(row.get("row_key") or "").strip()
        if not key:
            raise InvalidPayload(
                f"row {index} has no row_key; a vendor result can only be keyed back to a row that "
                "says which row it is"
            )
        if key in seen:
            raise InvalidPayload(
                f"two rows share the key {key!r}; a result keyed to it would be ambiguous"
            )
        seen.add(key)
        trace = row.get("trace_id")
        prepared.append(
            {
                "row_key": key,
                "index": index,
                "entity": str(row.get("entity") or ""),
                "trace_id": str(trace) if trace not in (None, "") else None,
                "values": row.get("values") if isinstance(row.get("values"), Mapping) else {},
            }
        )
    return prepared


# --------------------------------------------------------------------------- #
# HubSpot: 207 Multi-Status, correlated by objectWriteTraceId
# --------------------------------------------------------------------------- #

MULTI_STATUS = 207


def _hubspot(
    connector: str,
    status: int,
    body: Any,
    rows: list[dict[str, Any]],
    overrides: Sequence[Mapping[str, Any]],
) -> BatchOutcome:
    notes: list[str] = []
    envelope = body if isinstance(body, Mapping) else {}
    results = envelope.get("results")
    # numErrors is the only error count the research quotes, and it is HubSpot's.
    reported = _as_int(envelope.get("numErrors"))

    if status == MULTI_STATUS and not _is_sequence(results):
        notes.append(
            "the response was 207 Multi-Status and carried no results array, so there was no "
            "per-record outcome to key anything to; every row is recorded as failed on that basis"
        )
        return _all_rows_failed(
            connector,
            status,
            rows,
            overrides,
            notes=notes,
            code="MULTI_STATUS_WITHOUT_RESULTS",
            message=(
                "HubSpot answered 207 Multi-Status with no results array. The researched contract "
                "puts the per-record outcomes in that array, so nothing in this batch can be "
                "attributed to a row and nothing can be assumed to have been written."
            ),
            category="VALIDATION_ERROR",
            reported=reported,
        )

    per_record = _is_sequence(results)
    if per_record:
        if status != MULTI_STATUS:
            notes.append(
                f"the response answered {status} and still carried a results array; the per-record "
                "outcomes were read, and the status alone would not have shown the failures"
            )
    elif reported:
        notes.append(
            f"the vendor reported {reported} error(s) and returned no per-record outcomes to "
            "attribute them to, so the failures cannot be assigned to rows"
        )
    elif status < 400:
        notes.append(
            "a successful response with no results array means every row was accepted; the log "
            "records the successes and the per-row detail is the vendor's own record id"
        )

    if not per_record:
        if status >= 400:
            return _all_rows_failed(
                connector,
                status,
                rows,
                overrides,
                notes=notes,
                code=str(envelope.get("category") or envelope.get("code") or f"HTTP_{status}"),
                message=str(
                    envelope.get("message") or _first_text(envelope, ("error", "detail")) or ""
                ),
                category=_text_or_none(envelope.get("category")),
                reported=reported,
            )
        return _all_rows_succeeded(connector, status, rows, notes=notes, reported=reported)

    outcomes, unattributed, described = _match_results(
        connector,
        status,
        rows,
        results,
        overrides,
        notes,
        # No positional fallback: objectWriteTraceId is the researched correlation
        # for this vendor, and a result without one names no row. Guessing its
        # position would be a key the source set never promised.
        positional=None,
        describe=_hubspot_error,
        failed=_hubspot_failed,
        correlation_of=_hubspot_trace_id,
    )
    return BatchOutcome(
        connector=connector,
        http_status=status,
        per_record=True,
        rows=outcomes,
        unattributed=unattributed,
        reported_errors=reported,
        described_errors=described,
        consistent=_consistent(reported, described),
        notes=_finalise(notes, reported, described, len(unattributed)),
    )


def _hubspot_error(
    connector: str,
    status: int,
    result: Mapping[str, Any],
    overrides: Sequence[Mapping[str, Any]],
    scope: str,
) -> NormalisedError:
    return _build_error(
        connector=connector,
        status=status,
        code=str(result.get("category") or result.get("code") or f"HTTP_{status}"),
        message=_hubspot_message(result),
        field=_hubspot_field(result),
        doc_link=None,
        category=_text_or_none(result.get("category")),
        correlation=_hubspot_trace_id(result),
        related=_hubspot_related(result),
        raw=result,
        overrides=overrides,
        scope=scope,
    )


def _hubspot_failed(result: Mapping[str, Any], status: int | None = None) -> bool:
    state = str(result.get("status") or "").strip().lower()
    if state:
        return state in ("error", "failed", "failure")
    return _is_sequence(result.get("errors")) and bool(result.get("errors"))


def _hubspot_message(result: Mapping[str, Any]) -> str:
    message = _text_or_none(result.get("message"))
    if message:
        return message
    errors = result.get("errors")
    if _is_sequence(errors) and errors:
        first = errors[0]
        if isinstance(first, Mapping):
            return str(first.get("message") or "")
    return ""


def _hubspot_field(result: Mapping[str, Any]) -> str | None:
    """The offending property, from wherever this vendor put it.

    The research says a per-item error object carries ``message``, ``code`` and
    ``context`` and does not name the property key, so this reads the keys HubSpot
    has used and returns ``None`` when none matches rather than guessing. A
    ``None`` here is not a dead end: the store layer falls back to the room's own
    validation metadata for the property and records which of the two it used.
    """
    errors = result.get("errors")
    if _is_sequence(errors):
        for entry in errors:
            if isinstance(entry, Mapping):
                found = _first_text(entry, FIELD_SOURCE["hubspot"]["keys"])
                if found:
                    return found
    return _first_text(result, FIELD_SOURCE["hubspot"]["keys"])


def _hubspot_related(result: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Every per-item error object beyond the first, kept rather than merged.

    One row carries one normalised error, because the Sync log lists one offending
    property per row. A result whose ``errors`` array names three properties is
    still one failed row, and dropping two of them would be the same silent loss
    as dropping the row.
    """
    errors = result.get("errors")
    if not _is_sequence(errors):
        return ()
    related: list[dict[str, Any]] = []
    for entry in list(errors)[1:]:
        if isinstance(entry, Mapping):
            related.append(
                {
                    "code": _text_or_none(entry.get("code")),
                    "message": str(entry.get("message") or ""),
                    "field": _first_text(entry, FIELD_SOURCE["hubspot"]["keys"]),
                }
            )
    return tuple(related)


def _hubspot_trace_id(result: Mapping[str, Any]) -> str | None:
    """``context.objectWriteTraceId``, which the researched response is quoted with.

    It arrives as a list; the first entry is the room's own per-input value, and
    the whole list stays in ``raw`` so nothing is lost if a vendor ever sends more.
    """
    context = result.get("context")
    if isinstance(context, Mapping):
        value = context.get("objectWriteTraceId")
        if _is_sequence(value) and value:
            return str(value[0])
        found = _text_or_none(value)
        if found:
            return found
    return _text_or_none(result.get("objectWriteTraceId"))


# --------------------------------------------------------------------------- #
# Dataverse: 200 OK, failures in the body, HelpLink under an annotation
# --------------------------------------------------------------------------- #

#: The attribute named in the researched refusal, in the researched wording:
#: "The length of the 'subject' attribute of the 'task' entity exceeded the
#: maximum allowed length of '200'." Dataverse names no property key at all, so the
#: quoted attribute in the message is the only place the property can come from.
_ATTRIBUTE_PATTERNS = (
    re.compile(r"'([^']+)'\s+attribute"),
    re.compile(r"attribute\s+'([^']+)'"),
    re.compile(r"'([^']+)'\s+(?:property|column|field)"),
)

HELP_LINK_ANNOTATION = "@Microsoft.PowerApps.CDS.HelpLink"


def dataverse_field_from_message(message: str) -> str | None:
    """The property Dataverse named inside its own error message, or ``None``."""
    for pattern in _ATTRIBUTE_PATTERNS:
        found = pattern.search(message or "")
        if found:
            return found.group(1)
    return None


def help_link(value: Any) -> str | None:
    """The HelpLink annotation, whichever shape it arrived in.

    The researched annotation is "a URL that might direct you to specific
    guidance", and the surrounding object may carry a description beside it, so a
    mapping is read for its URL-ish members and a bare string is taken as-is.
    """
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, Mapping):
        for key in ("HelpLink", "Url", "URL", "uri", "value", "Link"):
            found = _text_or_none(value.get(key))
            if found:
                return found
    return None


def _dataverse(
    connector: str,
    status: int,
    body: Any,
    rows: list[dict[str, Any]],
    overrides: Sequence[Mapping[str, Any]],
) -> BatchOutcome:
    notes: list[str] = []
    envelope = body if isinstance(body, Mapping) else {}
    # Dataverse is not documented here to report an error count, so there is
    # nothing to reconcile its per-item errors against.
    reported = None

    items: list[Any] | None = None
    if _is_sequence(body):
        items = list(body)
    elif _is_sequence(envelope.get("responses")):
        items = list(envelope["responses"])

    top_level = envelope.get("error") if isinstance(envelope, Mapping) else None

    if items is None and top_level is None:
        # A 200 that is not the per-request array and is not an error object is a
        # shape the researched contract does not describe, so the room cannot claim
        # the requests succeeded. Same reasoning as HubSpot's 207 without a results
        # array: the connector asked for per-record outcomes and did not get them.
        notes.append(
            "the batch answered 200 OK with neither per-request responses nor an error object, so "
            "there was no per-record outcome to read; every row is recorded as failed on that basis"
        )
        return _all_rows_failed(
            connector,
            status,
            rows,
            overrides,
            notes=notes,
            code="BATCH_WITHOUT_OUTCOMES",
            message=(
                "Dataverse answered with a body the room could not read as a batch result. The "
                "researched contract puts the per-request outcomes in that body, so nothing in this "
                "batch can be assumed to have been written."
            ),
            category=None,
            reported=reported,
        )

    if top_level is not None:
        if items is not None and status < 400:
            notes.append(
                "the batch carried an error object at the top level *and* per-item responses, which "
                "continue-on-error should not produce; the per-item responses were read and the "
                "top-level error is reported as an unattributed failure"
            )
            outcomes, unattributed, described = _match_results(
                connector,
                status,
                rows,
                items,
                overrides,
                notes,
                positional="request_order",
                describe=_dataverse_item_error,
                failed=_dataverse_failed,
            )
            unattributed = (
                *_dataverse_top_level_error(connector, status, top_level, envelope, overrides),
                *unattributed,
            )
            described += 1
            return BatchOutcome(
                connector=connector,
                http_status=status,
                per_record=True,
                rows=outcomes,
                unattributed=unattributed,
                reported_errors=reported,
                described_errors=described,
                consistent=_consistent(reported, described),
                notes=_finalise(notes, reported, described, len(unattributed)),
            )

        error = _dataverse_top_level_error(connector, status, top_level, envelope, overrides)[0]
        notes.append(
            "the batch answered with an error object at the top level rather than per item, so one "
            "verdict covers the whole batch"
        )
        return _all_rows_failed(
            connector,
            status,
            rows,
            overrides,
            notes=notes,
            code=error.code,
            message=error.message,
            category=error.category,
            reported=reported,
            error=error,
        )

    if items is None:
        notes.append(
            "the batch answered with neither per-item responses nor an error object, so there was "
            "no per-record outcome; every row is recorded as failed on that basis"
        )
        return _all_rows_failed(
            connector,
            status,
            rows,
            overrides,
            notes=notes,
            code="BATCH_WITHOUT_OUTCOMES",
            message=(
                "Dataverse answered with a body the room could not read as a batch result. The "
                "researched contract puts the per-request outcomes in that body, so nothing in this "
                "batch can be assumed to have been written."
            ),
            category=None,
            reported=reported,
        )
    if status < 400:
        notes.append(
            "a Dataverse $batch answers 200 OK whether or not requests inside it failed; the "
            "failures are in the body, and a caller that read 200 as success would report this "
            "batch as clean"
        )

    outcomes, unattributed, described = _match_results(
        connector,
        status,
        rows,
        items,
        overrides,
        notes,
        positional="request_order",
        describe=_dataverse_item_error,
        failed=_dataverse_failed,
    )
    return BatchOutcome(
        connector=connector,
        http_status=status,
        per_record=True,
        rows=outcomes,
        unattributed=unattributed,
        reported_errors=reported,
        described_errors=described,
        consistent=_consistent(reported, described),
        notes=_finalise(notes, reported, described, len(unattributed)),
    )


def _dataverse_failed(result: Mapping[str, Any], status: int | None = None) -> bool:
    """Whether one per-request response in the batch reported a failure.

    A per-item response carries its own status; when it does not, the batch's
    status is the only signal there is, and continue-on-error means it is 200 even
    for a refused request - which is why the body's error object counts too.
    """
    if not isinstance(result, Mapping):
        return False
    item_status = _as_int(result.get("status"))
    if item_status is not None:
        if item_status >= 400:
            return True
    body = result.get("body") if isinstance(result.get("body"), Mapping) else {}
    if isinstance(body.get("error"), Mapping):
        return True
    if item_status is None and status is not None and status >= 400:
        return True
    return False


def _dataverse_item_error(
    connector: str,
    status: int,
    item: Mapping[str, Any],
    overrides: Sequence[Mapping[str, Any]],
    scope: str,
) -> NormalisedError:
    item_status = _as_int(item.get("status")) or status
    item_body = item.get("body") if isinstance(item.get("body"), Mapping) else {}
    error_body = item_body.get("error") if isinstance(item_body.get("error"), Mapping) else None
    code = str((error_body or item_body).get("code") or f"HTTP_{item_status}")
    message = str((error_body or item_body).get("message") or "")
    return _build_error(
        connector=connector,
        status=item_status,
        code=code,
        message=message,
        field=dataverse_field_from_message(message),
        # The annotation rides on the response body beside the error object, not
        # inside it, so both are searched - and the item itself, because
        # odata.include-annotations is a response-level header and where the
        # annotation lands is not pinned down by the researched page.
        doc_link=_dataverse_help_link(item, error_body, item_body),
        category=_text_or_none((error_body or item_body).get("category")),
        correlation=None,
        related=(),
        raw=item,
        overrides=overrides,
        scope=scope,
    )


def _dataverse_help_link(*candidates: Mapping[str, Any]) -> str | None:
    for body in candidates:
        if not isinstance(body, Mapping):
            continue
        found = help_link(body.get(HELP_LINK_ANNOTATION))
        if found:
            return found
        annotations = body.get("annotations")
        if isinstance(annotations, Mapping):
            found = help_link(annotations.get(HELP_LINK_ANNOTATION))
            if found:
                return found
    return None


def _dataverse_top_level_error(
    connector: str,
    status: int,
    top_level: Any,
    envelope: Mapping[str, Any],
    overrides: Sequence[Mapping[str, Any]],
) -> tuple[NormalisedError, ...]:
    body = top_level if isinstance(top_level, Mapping) else {}
    message = str(body.get("message") or "")
    return (
        _build_error(
            connector=connector,
            status=status,
            code=str(body.get("code") or f"HTTP_{status}"),
            message=message,
            field=dataverse_field_from_message(message),
            doc_link=_dataverse_help_link(body, envelope),
            category=_text_or_none(body.get("category")),
            correlation=None,
            related=(),
            raw=top_level,
            overrides=overrides,
            scope="request",
        ),
    )


# --------------------------------------------------------------------------- #
# Salesforce: sObject Collections, per-record errors, 400 and 403 at request level
# --------------------------------------------------------------------------- #


def _salesforce(
    connector: str,
    status: int,
    body: Any,
    rows: list[dict[str, Any]],
    overrides: Sequence[Mapping[str, Any]],
) -> BatchOutcome:
    notes: list[str] = []
    # Salesforce is not documented here to report an error count either.
    reported = None

    if status >= 400:
        code, message, category = _salesforce_request_error(body, status)
        notes.append(
            "the collection answered at request level, so one verdict covers every row in the batch"
        )
        return _all_rows_failed(
            connector,
            status,
            rows,
            overrides,
            notes=notes,
            code=code,
            message=message,
            category=category,
            reported=reported,
        )

    results: list[Any] | None = None
    if isinstance(body, Mapping) and _is_sequence(body.get("results")):
        results = list(body["results"])
    elif _is_sequence(body):
        results = list(body)

    if results is None:
        notes.append(
            "the collection answered successfully with no per-record results, so every row was "
            "accepted and there is no per-row detail to record beyond the batch itself"
        )
        return _all_rows_succeeded(connector, status, rows, notes=notes, reported=reported)

    outcomes, unattributed, described = _match_results(
        connector,
        status,
        rows,
        results,
        overrides,
        notes,
        positional="results_order",
        describe=_salesforce_record_error,
        failed=_salesforce_failed,
        correlation_of=_salesforce_correlation,
    )
    return BatchOutcome(
        connector=connector,
        http_status=status,
        per_record=True,
        rows=outcomes,
        unattributed=unattributed,
        reported_errors=reported,
        described_errors=described,
        consistent=_consistent(reported, described),
        notes=_finalise(notes, reported, described, len(unattributed)),
    )


def _salesforce_failed(result: Mapping[str, Any], status: int | None = None) -> bool:
    success = result.get("success")
    if success is None:
        return _is_sequence(result.get("errors")) and bool(result.get("errors"))
    return not bool(success)


def _salesforce_correlation(result: Mapping[str, Any]) -> str | None:
    """The record id a result carries, which is a stronger key than its position."""
    return _text_or_none(_record_id_of(result))


def _salesforce_record_error(
    connector: str,
    status: int,
    result: Mapping[str, Any],
    overrides: Sequence[Mapping[str, Any]],
    scope: str,
) -> NormalisedError:
    errors = result.get("errors")
    first: Mapping[str, Any] = {}
    if _is_sequence(errors) and errors and isinstance(errors[0], Mapping):
        first = errors[0]
    return _build_error(
        connector=connector,
        status=_as_int(first.get("statusCode")) or status,
        code=str(first.get("errorCode") or f"HTTP_{status}"),
        message=str(first.get("message") or ""),
        field=_first_text(first, FIELD_SOURCE["salesforce"]["keys"]),
        doc_link=None,
        category=_text_or_none(first.get("errorCode")),
        correlation=_salesforce_correlation(result),
        related=_related_from_sequence(errors, FIELD_SOURCE["salesforce"]["keys"]),
        raw=result,
        overrides=overrides,
        scope=scope,
    )


def _salesforce_request_error(body: Any, status: int) -> tuple[str, str, str | None]:
    """The code and message from a 400 or 403, in either body shape.

    Salesforce answers a request-level failure with a bare object, and some
    request-level failures with a one-element array of the same shape. Both are
    read, because a caller that handles only one of them shows an empty message
    for half the request-level failures.
    """
    candidates: list[Mapping[str, Any]] = []
    if isinstance(body, Mapping):
        candidates.append(body)
    elif _is_sequence(body):
        candidates.extend(entry for entry in body if isinstance(entry, Mapping))

    for entry in candidates:
        code = _text_or_none(entry.get("errorCode")) or _text_or_none(entry.get("error_code"))
        if code:
            return code, str(entry.get("message") or ""), code
    for entry in candidates:
        return f"HTTP_{status}", str(entry.get("message") or ""), None
    return f"HTTP_{status}", "", None


# --------------------------------------------------------------------------- #
# Matching vendor results back onto rows
# --------------------------------------------------------------------------- #


def _match_results(
    connector: str,
    status: int,
    rows: list[dict[str, Any]],
    results: Sequence[Any],
    overrides: Sequence[Mapping[str, Any]],
    notes: list[str],
    *,
    positional: str | None,
    describe: Any,
    failed: Any,
    correlation_of: Any = None,
) -> tuple[tuple[RowOutcome, ...], tuple[NormalisedError, ...], int]:
    """Key every vendor result back to a row, or record why it could not be.

    Order of preference, and why:

    1. a correlation the vendor returned that matches a row's own ``trace_id`` -
       the researched HubSpot path, and the strongest key available;
    2. otherwise the position in the response, matched to the position it was
       sent - the only option Dataverse and Salesforce are documented to leave.

    ``positional`` is ``None`` for a vendor whose documented contract *does* carry
    a correlation key, because for that vendor a result without one names no row
    and guessing its position would be a key the source set never promised.
    Otherwise it names the fallback in the vendor's own words, so the row records
    which basis its outcome rests on.

    A result that matches neither is kept as an unattributed error rather than
    dropped, and a row nothing matched becomes ``NO_PER_RECORD_OUTCOME``.
    """
    by_trace = {row["trace_id"]: row for row in rows if row["trace_id"]}
    outcomes: dict[str, RowOutcome] = {}
    unattributed: list[NormalisedError] = []
    described = 0

    for position, result in enumerate(results):
        if not isinstance(result, Mapping):
            notes.append(
                f"result {position} was not a JSON object and could not be attributed to a row"
            )
            continue

        correlation = correlation_of(result) if correlation_of else None
        row = by_trace.get(correlation) if correlation else None
        if row is not None:
            basis = "correlation"
        elif positional:
            row = rows[position] if position < len(rows) else None
            basis = positional
        else:
            basis = "unattributed"

        is_failed = failed(result, status)
        error = None
        if is_failed:
            described += 1
            error = describe(connector, status, result, overrides, "row")

        if row is None:
            if error is not None:
                unattributed.append(error)
            elif correlation:
                notes.append(
                    f"result {position} carries the correlation {correlation!r}, which names no row in "
                    "this batch, so a success could not be attached to a row"
                )
            else:
                notes.append(
                    f"result {position} could not be matched to a row: it carries no correlation "
                    "this connector documents, and its position is past the rows that were sent"
                )
            continue

        if row["row_key"] in outcomes:
            if error is not None:
                unattributed.append(error)
            else:
                notes.append(
                    f"row {row['row_key']!r} was described more than once in one response; the extra "
                    "description was not applied"
                )
            continue

        outcomes[row["row_key"]] = RowOutcome(
            row_key=row["row_key"],
            index=row["index"],
            entity=row["entity"],
            trace_id=row["trace_id"],
            correlation=correlation if basis == "correlation" else None,
            correlation_basis=basis,
            status="failed" if is_failed else "succeeded",
            error=error,
            vendor_record_id=_text_or_none(_record_id_of(result)),
        )

    for row in rows:
        if row["row_key"] not in outcomes:
            outcomes[row["row_key"]] = _no_outcome(connector, status, row, overrides, notes)

    return _ordered(outcomes, rows), tuple(unattributed), described


# --------------------------------------------------------------------------- #
# Shared shaping
# --------------------------------------------------------------------------- #


def _build_error(
    *,
    connector: str,
    status: int | None,
    code: str,
    message: str,
    field: str | None,
    doc_link: str | None,
    category: str | None,
    correlation: str | None,
    related: tuple[dict[str, Any], ...],
    raw: Any,
    overrides: Sequence[Mapping[str, Any]],
    scope: str,
) -> NormalisedError:
    retryable, basis, matched = classify(
        connector=connector,
        code=code,
        http_status=status,
        field=field,
        category=category,
        message=message,
        overrides=overrides,
    )
    return NormalisedError(
        retryable=retryable,
        field=_text_or_none(field),
        code=code or f"HTTP_{status}",
        message=message,
        doc_link=_text_or_none(doc_link),
        vendor=connector,
        scope=scope,
        http_status=status,
        category=category,
        correlation=_text_or_none(correlation),
        basis=basis,
        matched_rule=matched,
        related=related,
        raw=raw if isinstance(raw, Mapping) else {"value": raw},
    )


def _no_outcome(
    connector: str,
    status: int,
    row: Mapping[str, Any],
    overrides: Sequence[Mapping[str, Any]],
    notes: list[str],
) -> RowOutcome:
    """A row the batch said nothing about.

    Not a success. The row was in the request, the response did not describe it,
    and a row the log shows as written when it was not is the one error this whole
    workflow exists to prevent. Also not a retry: the vendor's silence is not
    evidence of a rate limit, so it waits for a person, and the basis says exactly
    what was observed.
    """
    notes.append(
        f"row {row['row_key']!r} was in the request and the response did not describe it; it is "
        "recorded as failed rather than assumed written"
    )
    error = _build_error(
        connector=connector,
        status=status,
        code="NO_PER_RECORD_OUTCOME",
        message=(
            "The batch did not return an outcome for this row. It was sent, and nothing in the "
            "response says it was accepted, so the room does not record it as written."
        ),
        field=None,
        doc_link=None,
        category=None,
        correlation=None,
        related=(),
        raw={},
        overrides=overrides,
        scope="row",
    )
    return RowOutcome(
        row_key=str(row["row_key"]),
        index=int(row["index"]),
        entity=str(row.get("entity") or ""),
        trace_id=row.get("trace_id"),
        status="failed",
        error=error,
    )


def _all_rows_succeeded(
    connector: str,
    status: int,
    rows: list[dict[str, Any]],
    *,
    notes: list[str],
    reported: int | None,
) -> BatchOutcome:
    return BatchOutcome(
        connector=connector,
        http_status=status,
        per_record=False,
        rows=tuple(
            RowOutcome(
                row_key=row["row_key"],
                index=row["index"],
                entity=row["entity"],
                trace_id=row["trace_id"],
                status="succeeded",
                correlation_basis="batch",
            )
            for row in rows
        ),
        reported_errors=reported,
        described_errors=0,
        consistent=_consistent(reported, 0),
        notes=tuple(notes),
    )


def _all_rows_failed(
    connector: str,
    status: int,
    rows: list[dict[str, Any]],
    overrides: Sequence[Mapping[str, Any]],
    *,
    notes: list[str],
    code: str,
    message: str,
    category: str | None,
    reported: int | None,
    error: NormalisedError | None = None,
) -> BatchOutcome:
    """One verdict, applied to every row in the batch.

    Correct for a transport-level refusal: a 400 the vendor could not understand
    and a 403 for an exceeded request limit both happen before any row is written,
    so every row genuinely failed and re-sending the whole batch is the right
    retry. ``per_record`` stays false, because nothing in this response was a
    per-record outcome and claiming otherwise would defeat the point of the field.
    """
    request_error = error or _build_error(
        connector=connector,
        status=status,
        code=code,
        message=message,
        field=None,
        doc_link=None,
        category=category,
        correlation=None,
        related=(),
        raw={},
        overrides=overrides,
        scope="request",
    )
    return BatchOutcome(
        connector=connector,
        http_status=status,
        per_record=False,
        rows=tuple(
            RowOutcome(
                row_key=row["row_key"],
                index=row["index"],
                entity=row["entity"],
                trace_id=row["trace_id"],
                status="failed",
                error=request_error,
                correlation_basis="batch",
            )
            for row in rows
        ),
        request=request_error,
        reported_errors=reported,
        described_errors=0,
        consistent=_consistent(reported, 0),
        notes=tuple(notes),
    )


def _ordered(
    outcomes: Mapping[str, RowOutcome], rows: Sequence[Mapping[str, Any]]
) -> tuple[RowOutcome, ...]:
    """Outcomes in the order the rows were sent, whatever order the vendor used."""
    position = {str(row["row_key"]): int(row["index"]) for row in rows}
    return tuple(sorted(outcomes.values(), key=lambda item: position.get(item.row_key, item.index)))


def _consistent(reported: int | None, described: int) -> bool | None:
    """Does the vendor's own count agree with what it described?

    ``None`` when the vendor stated no count, because "the vendor did not say" is
    not the same as "the vendor was wrong", and conflating them would train a
    reader to ignore the flag.
    """
    if reported is None:
        return None
    return reported == described


def _finalise(
    notes: list[str], reported: int | None, described: int, unattributed: int
) -> tuple[str, ...]:
    if unattributed:
        notes.append(
            f"{unattributed} error(s) in this response could not be keyed to a row; they are kept "
            "on the run rather than dropped, because a dropped error is a row nobody will ever fix"
        )
    if reported is None:
        return tuple(notes)
    if reported == described:
        return tuple(notes)
    if described < reported:
        notes.append(
            f"the vendor reported {reported} error(s) and described {described}; "
            f"{reported - described} error(s) in this response are unaccounted for"
        )
    else:
        notes.append(
            f"the vendor reported {reported} error(s) and described {described}; "
            f"{described - reported} described error(s) were not in the vendor's count"
        )
    return tuple(notes)


def _related_from_sequence(errors: Any, field_keys: Sequence[str]) -> tuple[dict[str, Any], ...]:
    if not _is_sequence(errors):
        return ()
    related: list[dict[str, Any]] = []
    for entry in list(errors)[1:]:
        if isinstance(entry, Mapping):
            related.append(
                {
                    "code": _text_or_none(entry.get("code"))
                    or _text_or_none(entry.get("errorCode")),
                    "message": str(entry.get("message") or ""),
                    "field": _first_text(entry, field_keys),
                }
            )
    return tuple(related)


def _record_id_of(item: Any) -> Any:
    if not isinstance(item, Mapping):
        return None
    for key in ("id", "recordId"):
        value = item.get(key)
        if value not in (None, ""):
            return value
    body = item.get("body")
    if isinstance(body, Mapping):
        for key in ("id", "recordId"):
            value = body.get(key)
            if value not in (None, ""):
                return value
    return None


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, Mapping))


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _text_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


#: The vendor table, built after the handlers are defined. ``normalise`` reads it
#: at call time, so the order of the module does not matter to a reader following
#: the data flow from the top.
_HANDLERS: dict[str, Any] = {
    "hubspot": _hubspot,
    "dataverse": _dataverse,
    "salesforce": _salesforce,
}


def _first_text(mapping: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, (list, tuple)):
            for entry in value:
                found = _text_or_none(entry)
                if found:
                    return found
            continue
        found = _text_or_none(value)
        if found:
            return found
    return None


__all__ = [
    "NormalisedError",
    "RowOutcome",
    "BatchOutcome",
    "ClassificationEntry",
    "CLASSIFICATION",
    "DEFAULT_CLASSIFICATION",
    "DEFAULT_BASIS",
    "OVERRIDE_MATCH_KEYS",
    "OVERRIDE_THEN_KEYS",
    "HELP_LINK_ANNOTATION",
    "MULTI_STATUS",
    "classify",
    "match_overrides",
    "normalise",
    "dataverse_field_from_message",
    "help_link",
]
