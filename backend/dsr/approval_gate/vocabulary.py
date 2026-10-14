"""WF-119: gate an automation on external human approval and resume.

Every name, ceiling and judgement call this workflow enforces lives here, so the
page and the tests read the rules from one place instead of restating them. The
feature module serves this module over ``/api/wf-119/vocabulary`` and the frontend
renders it, so a panel cannot tell a reader one rule while the validator enforces
another.

The GitHub issue quoted the research specification in full. Where the
specification is silent, :func:`decisions` records the reading taken, the
alternative rejected, and - for the one decision that was put to Jev - the audit
id and its verdict. :data:`NOT_BUILT` records what this build deliberately leaves
out, so a reviewer reads the omissions rather than assuming they were missed.

This module imports nothing from ``dsr``. No framework and no store.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #

TICKET = "WF-119"

#: The prefix this workflow owns. The host refuses a colliding one, so every
#: concrete path below is under it and can shadow nobody.
PREFIX = "/api/wf-119"

# --------------------------------------------------------------------------- #
# Collections
#
# Record payloads are ordinary JSON in ``records.data``. No migration, no typed
# column, so a team can add a field without coordinating with anyone.
# --------------------------------------------------------------------------- #

#: One row per pause node: the pause toggle, the two resume webhook URLs, the
#: branch each outcome selects, and the hold window.
GATES = "wf119_approval_gate"

#: One row per held contact. This is the state that has to survive a process
#: restart, so it is a record rather than an in-memory value.
HOLDS = "wf119_hold"

#: One row per resume decision. Append-only: a replay writes no second row.
RESUMES = "wf119_resume"

#: One row per pause, resume, expiry or replay. A sequence rather than an
#: overwritten flag, so a review can replay how a hold reached its state.
EVENTS = "wf119_gate_event"

COLLECTIONS: dict[str, str] = {
    "gates": GATES,
    "holds": HOLDS,
    "resumes": RESUMES,
    "events": EVENTS,
}

# --------------------------------------------------------------------------- #
# The documented envelope
#
# "Action execution URL (POST) with the documented envelope
# {"data": {...}, "extras": {"locationId","contactId","workflowId"},
# "meta": {"key","version"}}."
# --------------------------------------------------------------------------- #

ENVELOPE_DATA = "data"
ENVELOPE_EXTRAS = "extras"
ENVELOPE_META = "meta"
ENVELOPE_KEYS: tuple[str, ...] = (ENVELOPE_DATA, ENVELOPE_EXTRAS, ENVELOPE_META)

#: The correlation key. "the provided extras object needs to be passed as body
#: payload for resume workflow endpoint". It identifies the held contact, and it
#: is verified on resume rather than trusted.
EXTRAS_LOCATION = "locationId"
EXTRAS_CONTACT = "contactId"
EXTRAS_WORKFLOW = "workflowId"
EXTRAS_KEYS: tuple[str, ...] = (EXTRAS_LOCATION, EXTRAS_CONTACT, EXTRAS_WORKFLOW)

META_KEY = "key"
META_VERSION = "version"
META_KEYS: tuple[str, ...] = (META_KEY, META_VERSION)

#: The specification names ``version`` but prints no value when it is absent.
DEFAULT_META_VERSION = 1

# --------------------------------------------------------------------------- #
# Outcomes, states and the branch
# --------------------------------------------------------------------------- #

OUTCOME_APPROVED = "approved"
OUTCOME_REFUSED = "refused"
OUTCOMES: tuple[str, ...] = (OUTCOME_APPROVED, OUTCOME_REFUSED)

#: "Two resume-webhook URLs (Success Execution / Failed Execution)". The URLs are
#: two entry points; the outcome each one means is derived here and recorded as a
#: decision. Success carries approval, Failed carries refusal.
SUCCESS_URL_OUTCOME = OUTCOME_APPROVED
FAILED_URL_OUTCOME = OUTCOME_REFUSED

#: The path segment each resume URL serves, mapped to the outcome it means.
URL_OUTCOMES: dict[str, str] = {
    OUTCOME_APPROVED: OUTCOME_APPROVED,
    OUTCOME_REFUSED: OUTCOME_REFUSED,
}

#: The body key that carries the branch. "the branch ID needs to be sent to the
#: webhook for resuming". It is read from the body and verified against the
#: gate's own branch for that outcome.
BRANCH_KEY = "branchId"

#: "This toggle is used the contact will be held at this action unless resume
#: webhook is requested."
PAUSE_EXECUTION_KEY = "pause_execution"

#: The gate's branch map: one branch id per outcome.
BRANCHES_KEY = "branches"

STATE_HELD = "held"
STATE_RESUMED = "resumed"
STATE_EXPIRED = "expired"
STATES: tuple[str, ...] = (STATE_HELD, STATE_RESUMED, STATE_EXPIRED)

# --------------------------------------------------------------------------- #
# The hold window
#
# The specification gives no timeout, no expiry and no cancellation rule for a
# held contact. A held record with no expiry is a stuck record, so this build
# derives one. The derivation is recorded in :func:`decisions`.
# --------------------------------------------------------------------------- #

#: Three days. See ``HOLD_WINDOW_DERIVATION``.
DEFAULT_HOLD_SECONDS = 259_200

#: Five minutes. Below this an operator cannot read the approval and answer it.
MIN_HOLD_SECONDS = 300

#: Thirty days. Above this the gate is a trap rather than a pause.
MAX_HOLD_SECONDS = 2_592_000

HOLD_WINDOW_DERIVATION = (
    "The research prints no window, so it is derived from the shape of the step "
    "rather than invented: an approval is a business decision taken by a person "
    "in working hours. 72 hours covers a two-day weekend plus one full working "
    "day, so a hold raised late on a Friday still has a whole working day on "
    "Monday before it lapses. The floor of five minutes is the shortest time in "
    "which a person can read the request and answer it, and the ceiling of "
    "thirty days keeps a forgotten gate from being an unbounded trap. The window "
    "is per gate and configurable, and expiry is evaluated lazily on every read "
    "and every resume, so a restart cannot lose the fact that a hold lapsed."
)

# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #

EVENT_PAUSED = "paused"
EVENT_RESUMED = "resumed"
EVENT_EXPIRED = "expired"
EVENT_REPLAY = "replay"
EVENTS_ALL: tuple[str, ...] = (EVENT_PAUSED, EVENT_RESUMED, EVENT_EXPIRED, EVENT_REPLAY)

# --------------------------------------------------------------------------- #
# The resume mode
#
# The research gives two modes and does not choose. Chosen: async. Recorded with
# the rejected alternative and the Jev audit that selected it.
# --------------------------------------------------------------------------- #

RESUME_MODE = "async"
RESUME_MODE_REJECTED = "sync"
RESUME_MODE_AUDIT = "jev-20261014T105812-18180-92928"
RESUME_MODE_CONFIDENCE = 1.00

RESUME_MODE_QUOTES: dict[str, str] = {
    "sync": (
        "When the pause execution is turned off along with branching support, the "
        "contact will be moved to provided branch using `branchId` property from API "
        "response or from Custom Code using return statement."
    ),
    "async": (
        "When the pause execution is turned off, the branch ID needs to be sent to the "
        "webhook for resuming which is present in 'show API details' button."
    ),
    "hold": (
        "This toggle is used the contact will be held at this action unless resume "
        "webhook is requested. If this toggle is true then provided extras object needs "
        "to be passed as body payload for resume workflow endpoint."
    ),
}

# --------------------------------------------------------------------------- #
# The keys this workflow serves
# --------------------------------------------------------------------------- #

#: Gate keys are chosen by a person and address a gate in a URL, so they are
#: constrained to characters that survive a path segment unchanged.
GATE_KEY_MAX = 64
GATE_KEY_PATTERN = r"^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$"

#: The resume token is generated, not chosen. 32 hex characters name one gate.
TOKEN_BYTES = 16


# --------------------------------------------------------------------------- #
# Sources and the record of what was and was not built
# --------------------------------------------------------------------------- #

SOURCES: tuple[str, ...] = (
    "https://marketplace.gohighlevel.com/docs/marketplace-modules/CustomActions/",
    "https://marketplace.gohighlevel.com/docs/marketplace-modules/CustomTriggers/",
    "https://knowledge.hubspot.com/workflows/set-when-a-webhook-is-received-workflow-triggers",
)

DECISIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "RESUME_MODE_IS_ASYNC",
        "question": "The spec gives two resume modes and does not choose.",
        "choice": (
            "The resume webhook carries the branch id in its body, and the held record "
            "stores the branch the gate configured for that outcome. The decision is "
            "written to records.data when it arrives."
        ),
        "rejected": (
            "Sync: the automation resumes from the ``branchId`` in the synchronous pause "
            "response. Rejected because the human decides in an external system at an "
            "unbounded later time, so no response is left open to carry it, and a restart "
            "between the pause and the approval would lose the response."
        ),
        "gate": "Jev option gate, gate='choice', pass_option='resume_mode', pass_values=['async']",
        "audit_id": RESUME_MODE_AUDIT,
        "confidence": RESUME_MODE_CONFIDENCE,
        "verdict": "pass; chose 'async'",
    },
    {
        "id": "HOLD_WINDOW_IS_DERIVED",
        "question": (
            "The spec gives no timeout, no expiry and no cancellation rule for a held contact."
        ),
        "choice": HOLD_WINDOW_DERIVATION,
        "rejected": (
            "Never expire. Rejected because an undecided hold then blocks the contact for "
            "ever, which the issue names as a stuck record. Expiry is derived from the "
            "step's shape rather than from a number the research does not print."
        ),
        "audit_id": None,
        "confidence": None,
        "verdict": "derived; recorded as an inference, not a Jev decision",
    },
    {
        "id": "EXTRAS_ARE_THE_CORRELATION_KEY_AND_ARE_VERIFIED",
        "question": (
            "The spec names ``extras: {locationId, contactId, workflowId}`` as the resume body."
        ),
        "choice": (
            "The three extras fields are the correlation key. A resume resolves the hold "
            "by all three and the stored hold must match, so the body is verified rather "
            "than trusted."
        ),
        "rejected": (
            "Trusting the body and resuming whatever it names. Rejected because the resume "
            "webhook is called by an external system, and an unverified body would let any "
            "caller resume any contact."
        ),
        "audit_id": None,
        "confidence": None,
        "verdict": "derived; recorded as an inference",
    },
    {
        "id": "THE_GATE_DOES_NOT_REQUIRE_AN_APPROVER_IDENTITY",
        "question": (
            "The spec gives no approver identity in the resume body. ``data_sources`` names "
            "'queue + approver identity' as an approval-service concern, not as a field."
        ),
        "choice": (
            "The gate does not require an approver identity. When the resume body carries "
            "one it is recorded on the decision row, so the queue can name the approver "
            "without the workflow refusing the documented body."
        ),
        "rejected": (
            "Requiring an approver. Rejected because the documented resume body is the "
            "``extras`` object alone, so a required field would refuse the body the "
            "research specifies."
        ),
        "audit_id": None,
        "confidence": None,
        "verdict": "derived; recorded as an inference",
    },
    {
        "id": "A_RESUME_IS_IDEMPOTENT_FOR_THE_SAME_OUTCOME",
        "question": (
            "Neither quote defines what happens on a duplicate resume call; both say the "
            "contact is held 'unless resume webhook is requested'."
        ),
        "choice": (
            "A second resume carrying the same outcome and branch is an idempotent replay: "
            "it returns the recorded result and writes no new decision. A second resume "
            "carrying a different outcome is refused as a conflict."
        ),
        "rejected": (
            "Letting a later resume overwrite an earlier one. Rejected because a retried "
            "webhook would then be able to flip a decision a human already made. Refusing "
            "every repeat instead was also rejected: a retry is normal traffic."
        ),
        "audit_id": None,
        "confidence": None,
        "verdict": "derived; recorded as an inference",
    },
    {
        "id": "THE_URL_NAMES_THE_OUTCOME_AND_THE_BODY_NAMES_THE_BRANCH",
        "question": (
            "'Two resume-webhook URLs (Success Execution / Failed Execution)' are named and "
            "the spec does not say which one carries the branch."
        ),
        "choice": (
            "The URL names the outcome: the success URL means approved and the failed URL "
            "means refused. The branch is read from ``branchId`` in the body and must equal "
            "the gate's branch for that outcome. A body outcome that disagrees with the URL "
            "is refused rather than one source silently winning."
        ),
        "rejected": (
            "Deriving the branch from the URL alone. Rejected because the spec says the "
            "branch id is sent to the webhook, and deriving it from the URL would make the "
            "documented body field decorative and unverifiable."
        ),
        "audit_id": None,
        "confidence": None,
        "verdict": "derived; recorded as an inference",
    },
)

NOT_BUILT: tuple[dict[str, str], ...] = (
    {
        "item": "A mandatory 'Test Code' step",
        "why": (
            "The evidence quotes the vendor's rule about a custom-code action, that 'Testing "
            "the code is a mandatory step'. It is a rule about their builder, not about this "
            "repository, so no gate is built from it."
        ),
    },
    {
        "item": "A caller against the marketplace trigger targetUrl",
        "why": (
            "'https://services.leadconnectorhq.com/workflows-marketplace/triggers/execute/"
            "abc/def' is a documentation example, not a live endpoint, so nothing here calls it."
        ),
    },
    {
        "item": "Publishing this action to a marketplace",
        "why": (
            "The extensibility note describes versioning and submitting an app for review. "
            "That is a marketplace surface, and this workflow stops at the pause and resume."
        ),
    },
    {
        "item": "Downstream task, notification and email actions",
        "why": (
            "The user flow adds them after the branch is chosen. They fire on the selected "
            "branch and are other workflows' surfaces; this one records the branch it selected."
        ),
    },
)


def resume_mode_record() -> dict[str, Any]:
    """The resume-mode decision as one readable record."""
    return {
        "mode": RESUME_MODE,
        "rejected": RESUME_MODE_REJECTED,
        "audit_id": RESUME_MODE_AUDIT,
        "confidence": RESUME_MODE_CONFIDENCE,
        "quotes": dict(RESUME_MODE_QUOTES),
    }


def decisions() -> list[dict[str, Any]]:
    """Every judgement call this build recorded, in issue order."""
    return [dict(entry) for entry in DECISIONS]


def not_built() -> list[dict[str, str]]:
    """What this build deliberately leaves out, and why."""
    return [dict(entry) for entry in NOT_BUILT]


def vocabulary() -> dict[str, Any]:
    """The whole vocabulary, served from one place so a page cannot drift."""
    return {
        "ticket": TICKET,
        "prefix": PREFIX,
        "collections": dict(COLLECTIONS),
        "envelope": {
            "keys": list(ENVELOPE_KEYS),
            "extras_keys": list(EXTRAS_KEYS),
            "meta_keys": list(META_KEYS),
            "default_meta_version": DEFAULT_META_VERSION,
            "branch_key": BRANCH_KEY,
        },
        "outcomes": list(OUTCOMES),
        "states": list(STATES),
        "events": list(EVENTS_ALL),
        "pause_execution_key": PAUSE_EXECUTION_KEY,
        "hold_window": {
            "default_seconds": DEFAULT_HOLD_SECONDS,
            "min_seconds": MIN_HOLD_SECONDS,
            "max_seconds": MAX_HOLD_SECONDS,
            "derivation": HOLD_WINDOW_DERIVATION,
        },
        "resume": {
            "mode": RESUME_MODE,
            "rejected_mode": RESUME_MODE_REJECTED,
            "audit_id": RESUME_MODE_AUDIT,
            "success_url_outcome": SUCCESS_URL_OUTCOME,
            "failed_url_outcome": FAILED_URL_OUTCOME,
            "idempotent_on_same_outcome": True,
        },
        "gate_key": {"max_length": GATE_KEY_MAX, "pattern": GATE_KEY_PATTERN},
        "decisions": decisions(),
        "not_built": not_built(),
        "sources": list(SOURCES),
    }


__all__ = [
    "BRANCHES_KEY",
    "BRANCH_KEY",
    "COLLECTIONS",
    "DECISIONS",
    "DEFAULT_HOLD_SECONDS",
    "DEFAULT_META_VERSION",
    "ENVELOPE_DATA",
    "ENVELOPE_EXTRAS",
    "ENVELOPE_KEYS",
    "ENVELOPE_META",
    "EVENTS",
    "EVENTS_ALL",
    "EVENT_EXPIRED",
    "EVENT_PAUSED",
    "EVENT_REPLAY",
    "EVENT_RESUMED",
    "EXTRAS_CONTACT",
    "EXTRAS_KEYS",
    "EXTRAS_LOCATION",
    "EXTRAS_WORKFLOW",
    "FAILED_URL_OUTCOME",
    "GATES",
    "GATE_KEY_MAX",
    "GATE_KEY_PATTERN",
    "HOLDS",
    "HOLD_WINDOW_DERIVATION",
    "MAX_HOLD_SECONDS",
    "META_KEY",
    "META_KEYS",
    "META_VERSION",
    "MIN_HOLD_SECONDS",
    "NOT_BUILT",
    "OUTCOMES",
    "OUTCOME_APPROVED",
    "OUTCOME_REFUSED",
    "PAUSE_EXECUTION_KEY",
    "PREFIX",
    "RESUMES",
    "RESUME_MODE",
    "RESUME_MODE_AUDIT",
    "RESUME_MODE_QUOTES",
    "RESUME_MODE_REJECTED",
    "SOURCES",
    "STATES",
    "STATE_EXPIRED",
    "STATE_HELD",
    "STATE_RESUMED",
    "SUCCESS_URL_OUTCOME",
    "TICKET",
    "TOKEN_BYTES",
    "URL_OUTCOMES",
    "decisions",
    "not_built",
    "resume_mode_record",
    "vocabulary",
]
