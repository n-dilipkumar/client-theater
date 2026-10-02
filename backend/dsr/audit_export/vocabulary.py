"""WF-079: the integer action-code vocabulary an audit export is queried by.

The research makes one point sharply about this workflow's central extension
point, and it is the point most likely to be got wrong:

    "The action enum is the critical extension point - a sales room that adds its
    own events (NDA accepted, link revoked, watermark toggled) needs a stable,
    additive integer vocabulary rather than free-text strings, because
    compliance reviews query by code."
    -- docs/research/digital-sales-room-workflows/wf/WF-079.md

Three things follow, and they are the whole design of this module.

**Codes are integers and they are stable.** A compliance review is a query like
``action in (43, 51)``. Free text breaks that the first time somebody rewords a
label, so nothing here is keyed on a name.

**The vocabulary is additive, not authoritative.** This application does not own
the vendor's enum. It owns a documented subset plus a documented band for its own
events, and :func:`describe_code` answers for *every* integer - including the ones
no source names - rather than raising or silently rewriting an unknown code to
something it recognises. A trail that drops or renames a row it does not recognise
is worse than useless to a reviewer: it looks complete.

**The gaps are load-bearing.** The research enumerates 1, 6, 8, 12, 13, 18, 43,
47-54, 55-58 and 69-70. It does not enumerate 2-5, 7, 9-11 or 14-42, and that is
not an oversight in the source - those codes exist in the vendor's full enum and
this workflow simply does not cite them. Declaring them free would let a team mint
code 9 for "NDA accepted" and then collide with the vendor's code 9 on the first
upgrade. :func:`declared_gaps` publishes exactly which integers are unclaimed so
the next team does not have to re-derive it, and :data:`WORKSPACE_CODE_FLOOR` puts
this application's own codes above every vendor band so the collision cannot
happen by accident.

Sourced
-------
* ``docs/research/digital-sales-room-workflows/wf/WF-079.md`` for the lifecycle
  codes, the two verification codes the source names individually, the three
  bands, and the additive-vocabulary requirement.
* ``docs/research/raw/security-governance.md`` section 11 for the two codes whose
  members this build can name: "``47`` | recipient verification with kba passed",
  "``51`` | recipient verification with kba failed", "``69`` | recipient
  verification with email otp passed", "``70`` | recipient verification with email
  otp failed", and for the four verification methods (passcode, SMS OTP, KBA, ID
  check) and the two gates (``before_open`` / ``before_sign``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

#: The code carried by an audited change this workflow could not classify.
#:
#: It is a real code rather than ``None`` because a compliance reviewer filters on
#: integers: "every row whose code is not in my list" has to be expressible, and a
#: null would silently exclude them from every query. :data:`UNCLASSIFIED` keeps
#: those rows in the result set and flags them.
UNCLASSIFIED = 0

#: Where codes below this number may come from.
#:
#: The vendor's bands top out at 70 in the research, so 1000 leaves room for the
#: rest of the vendor enum to be filled in without colliding with anything this
#: application mints. A local code at 9 would be safe today and a defect after the
#: vendor publishes 9.
WORKSPACE_CODE_FLOOR = 1000

#: Origins, in the order a reader cares about them.
ORIGIN_VENDOR = "pandadoc"  # named individually by a cited source
ORIGIN_ALLOCATED = "allocated"  # a member of a sourced band this build placed
ORIGIN_BAND = "band"  # a member of a sourced band no source names individually
ORIGIN_WORKSPACE = "workspace"  # minted by this application, at or above the floor
ORIGIN_UNDECLARED = "undeclared"  # nobody has claimed this integer

#: Categories a code can belong to. A reviewer filters on these far more often
#: than on individual codes, so they are part of the published vocabulary.
CATEGORY_LIFECYCLE = "lifecycle"
CATEGORY_VERIFICATION = "verification"
CATEGORY_QES = "qes"
CATEGORY_EXPORT = "export"
CATEGORY_UNKNOWN = "unknown"


@dataclass(frozen=True)
class ActionCode:
    """One integer in the vocabulary, with everything a reviewer needs to read it."""

    code: int
    name: str
    category: str
    origin: str
    description: str
    method: str | None = None
    outcome: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _code(
    code: int,
    name: str,
    description: str,
    *,
    category: str = CATEGORY_LIFECYCLE,
    origin: str = ORIGIN_VENDOR,
    method: str | None = None,
    outcome: str | None = None,
) -> ActionCode:
    return ActionCode(
        code=code,
        name=name,
        category=category,
        origin=origin,
        description=description,
        method=method,
        outcome=outcome,
    )


# --------------------------------------------------------------------------- #
# Lifecycle codes, named individually by the research.
# --------------------------------------------------------------------------- #

DOCUMENT_CREATED = 1
DOCUMENT_SENT = 6
DOCUMENT_VIEWED = 8
DOCUMENT_FORWARDED = 12
DOCUMENT_EXPIRED = 13
DOCUMENT_COMPLETED_MANUALLY = 18
DOCUMENT_DECLINED = 43

#: The lifecycle codes, exactly as the research names them: "action is a stable
#: integer enum covering the full lifecycle (1 new document, 6 sent, 8 viewed, 12
#: forwarded, 13 expired, 18 completed manually, 43 declined, ...)".
NAMED_LIFECYCLE: dict[int, ActionCode] = {
    DOCUMENT_CREATED: _code(
        DOCUMENT_CREATED,
        "document_created",
        "A new document entered the room.",
    ),
    DOCUMENT_SENT: _code(
        DOCUMENT_SENT,
        "document_sent",
        "The document was sent to a recipient.",
    ),
    DOCUMENT_VIEWED: _code(
        DOCUMENT_VIEWED,
        "document_viewed",
        "A recipient opened the document.",
    ),
    DOCUMENT_FORWARDED: _code(
        DOCUMENT_FORWARDED,
        "document_forwarded",
        "The document was forwarded on to somebody else.",
    ),
    DOCUMENT_EXPIRED: _code(
        DOCUMENT_EXPIRED,
        "document_expired",
        "The document reached its expiry and closed.",
    ),
    DOCUMENT_COMPLETED_MANUALLY: _code(
        DOCUMENT_COMPLETED_MANUALLY,
        "document_completed_manually",
        "The document was completed by a person rather than by every signer.",
    ),
    DOCUMENT_DECLINED: _code(
        DOCUMENT_DECLINED,
        "document_declined",
        "A recipient declined to sign or open the document.",
    ),
}

#: A ``status`` on a document record resolves to the lifecycle code the research
#: names. Used by :func:`dsr.audit_export.entries.derive_action_code` when the
#: writing feature did not declare an ``event_code`` of its own.
#:
#: ``completed`` is listed alongside ``completed_manually`` because code 18 is the
#: only completion code the research names, and a record whose status is simply
#: "completed" is describing the same event. Inference, recorded here because it
#: changes which code a row gets.
LIFECYCLE_STATUS_CODES: dict[str, int] = {
    "sent": DOCUMENT_SENT,
    "viewed": DOCUMENT_VIEWED,
    "forwarded": DOCUMENT_FORWARDED,
    "expired": DOCUMENT_EXPIRED,
    "completed": DOCUMENT_COMPLETED_MANUALLY,
    "completed_manually": DOCUMENT_COMPLETED_MANUALLY,
    "declined": DOCUMENT_DECLINED,
}


# --------------------------------------------------------------------------- #
# Verification codes: 47-54, and the two named members at 47 and 51.
# --------------------------------------------------------------------------- #

VERIFICATION_PASS_FLOOR = 47
VERIFICATION_FAIL_FLOOR = 51
VERIFICATION_BAND = (47, 54)
QES_BAND = (55, 58)
EMAIL_OTP_BAND = (69, 70)

#: The four verification methods, in the order their codes run.
#:
#: The research names the set - passcode, SMS one-time password, knowledge-based
#: authentication, ID check - and names exactly two of the eight codes: 47 is
#: "recipient verification with kba passed" and 51 is "recipient verification
#: with kba failed". Those two facts fix the band's *shape*: KBA occupies the first
#: slot of each half, so the eight codes are four pass codes followed by four fail
#: codes, not four interleaved pairs (an interleaved layout would put KBA's failure
#: at 48, not 51).
#:
#: What the two facts do not fix is the order of the other three methods within
#: their block, so this tuple is an inference. It is deterministic and published,
#: which is what an additive vocabulary needs; it is not sourced. A team that cares
#: can reassign it here without touching a row of data, because the codes are
#: carried as integers on the records rather than looked up by name at read time.
VERIFICATION_METHODS: tuple[str, ...] = ("kba", "passcode", "sms", "id")

VERIFICATION_METHOD_LABELS: dict[str, str] = {
    "kba": "knowledge-based authentication",
    "passcode": "typed passcode",
    "sms": "SMS one-time password",
    "id": "government-issued ID check",
}

PASS = "pass"
FAIL = "fail"

#: The two email-OTP codes the source names individually, and the band they sit in.
EMAIL_OTP_PASSED = 69
EMAIL_OTP_FAILED = 70

EMAIL_OTP_CODES: dict[str, ActionCode] = {
    EMAIL_OTP_PASSED: _code(
        EMAIL_OTP_PASSED,
        "verification_email_otp_passed",
        "Recipient proved identity with an emailed one-time password.",
        category=CATEGORY_VERIFICATION,
        method="email_otp",
        outcome=PASS,
    ),
    EMAIL_OTP_FAILED: _code(
        EMAIL_OTP_FAILED,
        "verification_email_otp_failed",
        "Recipient failed the emailed one-time password check.",
        category=CATEGORY_VERIFICATION,
        method="email_otp",
        outcome=FAIL,
    ),
}

EMAIL_OTP_METHOD = "email_otp"


def _build_verification_codes() -> dict[int, ActionCode]:
    """Lay 47-54 out as four pass codes then four fail codes.

    Derived rather than tabulated so that :data:`VERIFICATION_METHODS` is the only
    place the ordering lives: adding a fifth method is a one-line change here and
    the codes follow.
    """
    codes: dict[int, ActionCode] = {}
    for index, method in enumerate(VERIFICATION_METHODS):
        label = VERIFICATION_METHOD_LABELS.get(method, method)
        passed = VERIFICATION_PASS_FLOOR + index
        failed = VERIFICATION_FAIL_FLOOR + index
        codes[passed] = _code(
            passed,
            f"verification_{method}_passed",
            f"Recipient passed {label}.",
            category=CATEGORY_VERIFICATION,
            # Only the KBA codes are named by a source. The other three are this
            # build's placement, and the origin says so rather than implying a
            # citation that does not exist.
            origin=ORIGIN_VENDOR if method == "kba" else ORIGIN_ALLOCATED,
            method=method,
            outcome=PASS,
        )
        codes[failed] = _code(
            failed,
            f"verification_{method}_failed",
            f"Recipient failed {label}.",
            category=CATEGORY_VERIFICATION,
            origin=ORIGIN_VENDOR if method == "kba" else ORIGIN_ALLOCATED,
            method=method,
            outcome=FAIL,
        )
    return codes


VERIFICATION_CODES: dict[int, ActionCode] = _build_verification_codes()

#: Codes 55-58 are the QES (qualified electronic signature) lifecycle. The research
#: names the band and nothing inside it, so every member shares one name and says
#: so. Naming them individually would be inventing four event definitions.
QES_CODES: dict[int, ActionCode] = {
    code: _code(
        code,
        "qes_lifecycle",
        (
            "A qualified-electronic-signature lifecycle event. The research names "
            "this band (55-58) without naming its members, so this code is "
            "identified by its integer and nothing else."
        ),
        category=CATEGORY_QES,
        origin=ORIGIN_BAND,
    )
    for code in range(QES_BAND[0], QES_BAND[1] + 1)
}


# --------------------------------------------------------------------------- #
# This application's own codes.
# --------------------------------------------------------------------------- #

REPORT_REQUESTED = WORKSPACE_CODE_FLOOR
REPORT_GENERATED = WORKSPACE_CODE_FLOOR + 1
REPORT_DELIVERED = WORKSPACE_CODE_FLOOR + 2
IP_ATTRIBUTED = WORKSPACE_CODE_FLOOR + 3

WORKSPACE_CODES: dict[int, ActionCode] = {
    REPORT_REQUESTED: _code(
        REPORT_REQUESTED,
        "audit_report_requested",
        "A compliance lead asked for a CSV report over a date range.",
        category=CATEGORY_EXPORT,
        origin=ORIGIN_WORKSPACE,
    ),
    REPORT_GENERATED: _code(
        REPORT_GENERATED,
        "audit_report_generated",
        "The requested CSV was built and its rows hashed into the export chain.",
        category=CATEGORY_EXPORT,
        origin=ORIGIN_WORKSPACE,
    ),
    REPORT_DELIVERED: _code(
        REPORT_DELIVERED,
        "audit_report_delivered",
        "A download link for the finished CSV was emailed to the recipient.",
        category=CATEGORY_EXPORT,
        origin=ORIGIN_WORKSPACE,
    ),
    IP_ATTRIBUTED: _code(
        IP_ATTRIBUTED,
        "audit_ip_attributed",
        "A client address was attributed to an audited change.",
        category=CATEGORY_EXPORT,
        origin=ORIGIN_WORKSPACE,
    ),
}


#: Everything this module knows how to describe.
KNOWN_CODES: dict[int, ActionCode] = {
    **NAMED_LIFECYCLE,
    **VERIFICATION_CODES,
    **QES_CODES,
    **EMAIL_OTP_CODES,
    **WORKSPACE_CODES,
}

#: The sourced bands, published so a caller can tell "in a band we know about"
#: from "an integer nobody has claimed".
SOURCED_BANDS: tuple[tuple[int, int, str], ...] = (
    (VERIFICATION_BAND[0], VERIFICATION_BAND[1], "recipient identity verification"),
    (QES_BAND[0], QES_BAND[1], "qualified electronic signature lifecycle"),
    (EMAIL_OTP_BAND[0], EMAIL_OTP_BAND[1], "email one-time password verification"),
)


# --------------------------------------------------------------------------- #
# Reading a code
# --------------------------------------------------------------------------- #


def in_workspace_band(code: int) -> bool:
    """Whether this application minted ``code`` rather than a source naming it."""
    return code >= WORKSPACE_CODE_FLOOR


def describe_code(code: Any) -> ActionCode:
    """Describe any integer, including ones nothing has claimed.

    This never raises and never guesses. An unrecognised code comes back as
    ``undeclared`` with its integer intact, because the alternative - failing the
    whole export because one row carries a code from a feature that has since been
    removed - would make the export less trustworthy than the thing it describes.
    """
    try:
        number = int(code)
    except (TypeError, ValueError):
        return _code(
            UNCLASSIFIED,
            "unclassified",
            "The change carried no usable action code.",
            category=CATEGORY_UNKNOWN,
            origin=ORIGIN_UNDECLARED,
        )
    known = KNOWN_CODES.get(number)
    if known is not None:
        return known
    return _code(
        number,
        "undeclared",
        (
            "No source or team has claimed this integer. It is reported verbatim "
            "so a compliance review can see the row rather than lose it; it is "
            "never rewritten to a code that is merely similar."
        ),
        category=CATEGORY_UNKNOWN,
        origin=ORIGIN_UNDECLARED,
    )


def verification_code(method: Any, outcome: Any) -> int | None:
    """The verification code for a method and outcome, or ``None`` if unallocable.

    ``None`` means "this method has no slot in the band" - it does not mean the
    attempt did not happen. A caller that gets ``None`` must still report the
    outcome; losing the fact because the code could not be minted would be exactly
    the failure the research warns about when it insists that "both success and
    failure produce audit actions, so a rejected attempt is as visible as a
    successful one".
    """
    normalised_method = str(method or "").strip().lower().replace("-", "_")
    normalised_outcome = str(outcome or "").strip().lower()
    # The two email-OTP codes are named individually by a source (69, 70) and sit in
    # their own band, so they are resolved before the 47-54 band is consulted. The
    # empty string maps here too: a record that reports an outcome and no method is
    # an email-OTP attempt in every source that names one.
    if normalised_method in ("", "email", "email_code", "otp", EMAIL_OTP_METHOD):
        normalised_method = EMAIL_OTP_METHOD
    if normalised_outcome in ("pass", "passed", "success", "succeeded", "verified", "ok"):
        normalised_outcome = PASS
    elif normalised_outcome in ("fail", "failed", "failure", "rejected", "denied"):
        normalised_outcome = FAIL
    if normalised_outcome not in (PASS, FAIL):
        return None
    if normalised_method == EMAIL_OTP_METHOD:
        return EMAIL_OTP_PASSED if normalised_outcome == PASS else EMAIL_OTP_FAILED
    if normalised_method not in VERIFICATION_METHODS:
        return None
    index = VERIFICATION_METHODS.index(normalised_method)
    if normalised_outcome == PASS:
        return VERIFICATION_PASS_FLOOR + index
    return VERIFICATION_FAIL_FLOOR + index


def declared_gaps() -> list[dict[str, Any]]:
    """Integers inside the vendor's used range that nothing has claimed.

    Published rather than left implicit so the next feature does not mint code 9
    for its own event and discover the collision when the vendor's enum is
    completed. Each entry is a closed interval; adjacent intervals are merged.
    """
    claimed = {code for code in KNOWN_CODES if not in_workspace_band(code)}
    gaps: list[tuple[int, int]] = []
    for code in range(1, WORKSPACE_CODE_FLOOR):
        if code not in claimed:
            if gaps and gaps[-1][1] == code - 1:
                gaps[-1] = (gaps[-1][0], code)
            else:
                gaps.append((code, code))
    return [
        {
            "from": start,
            "to": end,
            "reason": (
                "Reserved. The research enumerates the codes this workflow needs "
                "and leaves this band to the vendor's full enum; nothing here may "
                "mint a code in it."
            ),
        }
        for start, end in gaps
    ]


def declared_event_code(before: Any, after: Any) -> int | None:
    """An ``event_code`` a writing feature stamped onto the record it wrote.

    The extension point the research asks for, and it needs no coordination: a
    team adding "NDA accepted" writes ``event_code`` into the payload it was
    already writing, the audited row carries it in ``after_state``, and the export
    reports it. No migration, no typed column, no shared file.

    A value that is not an integer is ignored rather than coerced, so a record
    carrying ``event_code: "nda"`` degrades to the derived code instead of raising
    on a string that happens to start with digits.
    """
    for payload in (after, before):
        if not isinstance(payload, dict):
            continue
        raw = payload.get("event_code")
        if isinstance(raw, bool) or raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return None


def verification_facts(before: Any, after: Any) -> dict[str, Any]:
    """The pass/fail fact carried by an audited change, if it carries one.

    Reads the shapes WF-077 and its neighbours use rather than only the codes, so
    that a rejected attempt stays visible even when its method has no slot in the
    47-54 band.
    """
    for payload in (after, before):
        if not isinstance(payload, dict):
            continue
        outcome = payload.get("outcome") or payload.get("verification_outcome")
        method = payload.get("method") or payload.get("verification_method")
        if outcome is None and method is None:
            continue
        # Both spellings, because the payload decides: this workflow's own seed
        # writes "pass"/"fail", while the vendor field documentation says
        # "passed"/"failed". Accepting only one of them made a rejection export as
        # outcome null, which is the one loss the research explicitly forbids.
        normalised = str(outcome or "").strip().lower()
        if normalised in ("pass", "passed", "success", "succeeded", "verified", "ok"):
            resolved = PASS
        elif normalised in ("fail", "failed", "failure", "rejected", "denied"):
            resolved = FAIL
        else:
            resolved = None
        return {
            "outcome": resolved,
            "method": str(method).strip().lower() if method else None,
            "reported": outcome is not None,
        }
    return {"outcome": None, "method": None, "reported": False}


def vocabulary_payload() -> dict[str, Any]:
    """Everything the vocabulary asserts, for a reviewer and for the page.

    Served by ``GET /api/wf-079/vocabulary`` so the claims a UI makes about the
    enum can be checked against the table that enforces it, rather than restated
    from prose.
    """
    return {
        "unclassified_code": UNCLASSIFIED,
        "workspace_code_floor": WORKSPACE_CODE_FLOOR,
        "origins": {
            ORIGIN_VENDOR: "named individually by a cited source",
            ORIGIN_ALLOCATED: "a member of a sourced band this build placed",
            ORIGIN_BAND: "a member of a sourced band no source names individually",
            ORIGIN_WORKSPACE: "minted by this application at or above the floor",
            ORIGIN_UNDECLARED: "nobody has claimed this integer",
        },
        "codes": [code.to_dict() for code in sorted(KNOWN_CODES.values(), key=lambda c: c.code)],
        "bands": [
            {"from": low, "to": high, "meaning": meaning} for low, high, meaning in SOURCED_BANDS
        ],
        "reserved_gaps": declared_gaps(),
        "verification_methods": [
            {
                "id": method,
                "label": VERIFICATION_METHOD_LABELS[method],
                "passed_code": VERIFICATION_PASS_FLOOR + index,
                "failed_code": VERIFICATION_FAIL_FLOOR + index,
            }
            for index, method in enumerate(VERIFICATION_METHODS)
        ],
        "inferences": INFERENCES,
    }


#: Every place this build had to choose, stated in the payload rather than only in
#: a commit message. A reviewer reads these; they are the difference between a
#: researched build and a plausible one.
INFERENCES: list[dict[str, str]] = [
    {
        "claim": "Verification codes 47-54 run four passes then four fails, kba first in each block.",
        "basis": (
            "Derived from the only two codes a source names individually - 47 is "
            "'recipient verification with kba passed' and 51 is 'recipient "
            "verification with kba failed'. Their distance of four fixes the "
            "band's shape and fixes KBA as the first method in each half. The "
            "order of the remaining three methods is NOT sourced and is this "
            "build's choice."
        ),
    },
    {
        "claim": "Codes 55-58 share one name rather than four invented ones.",
        "basis": (
            "The research names the QES band and no member of it. Naming four "
            "events that no source describes would invent requirements; the codes "
            "are still carried as integers so a row using one is not lost."
        ),
    },
    {
        "claim": "This application's own codes start at 1000.",
        "basis": (
            "The research's highest cited code is 70. A local code at 9 would be "
            "safe today and a collision the day the vendor's full enum is "
            "published, so local codes sit above every band a source names."
        ),
    },
    {
        "claim": "A document status of 'completed' resolves to code 18.",
        "basis": (
            "Code 18 is named 'completed manually' and is the only completion code "
            "the research cites. A record whose status is simply 'completed' is "
            "describing the same event; this is an inference and it changes which "
            "code such a row receives."
        ),
    },
    {
        "claim": "An unrecognised code is reported verbatim, never rewritten.",
        "basis": (
            "A compliance review filters on integers. Silently mapping 900 onto 8 "
            "because it looks similar would answer a question the reviewer did not "
            "ask, and dropping the row would hide it."
        ),
    },
]


def codes_in(entries: Iterable[dict[str, Any]]) -> dict[int, int]:
    """Histogram of action codes across export entries, for a summary endpoint."""
    histogram: dict[int, int] = {}
    for entry in entries:
        action = entry.get("action")
        if not isinstance(action, dict):
            continue
        try:
            code = int(action.get("code", UNCLASSIFIED))
        except (TypeError, ValueError):
            code = UNCLASSIFIED
        histogram[code] = histogram.get(code, 0) + 1
    return dict(sorted(histogram.items()))
