"""Every judgement call WF-040 rests on, in one inspectable place.

The research for this workflow is unusually specific about its evidence: it quotes
three vendors' own status codes, two exact error bodies, two request headers, and
one annotation name, and it states its own gap. That half is the product, and it
is quoted in the modules that implement it - :mod:`dsr.partial_failures.vocabulary`
for the per-record-outcome requests, :mod:`dsr.partial_failures.normalise` for the
statuses and the error bodies.

What the research does **not** say falls in two piles, and this build is explicit
about which pile each decision came from.

*Sourced but not implemented here.* HubSpot enforces admin-configured validation
rules on all CRM write paths from the ``/2026-09/`` GA release. The room does not
fetch those rules: the property validation API needs a portal session this product
does not have, so the enforcement is reported as a fact and the room's own
pre-flight rules are what a team can actually change.

*Genuinely open.* The retryable-versus-terminal boundary for everything except two
named classes. The two named classes are rate limit and locked, and the research
names no vendor code for either. Everything below is the reading that goes from
those two words to an actual decision, and each entry says so in its own
``basis``.

Every entry is:

* **named**, so it can be argued with by name rather than found in a diff;
* **traceable** - ``basis`` quotes what the research does and does not say;
* **bounded** - ``value`` is what this build chose and ``change_it`` says how to
  change it without editing a function body;
* **visible** - :func:`describe` is served at ``/api/wf-040/inferences``.

This is a list of ordinary JSON. It is not a migration, not a typed column, and
not a mechanism that enforces anything: a disagreement with an entry is settled by
reading the research, and then either the entry or the code moves.
"""

from __future__ import annotations

from typing import Any

#: The two quotations the whole classification rests on, kept here so the entries
#: below can point at them rather than paraphrase them five times.
SOURCED_AUTOMATION = (
    "Retry queue drains automatically for retryable classes (rate limit, locked) and waits for "
    "admin action for validation failures - the room classifies errors into retryable vs terminal."
)

SOURCED_EXTENSIBILITY = (
    "The room's error model is the extension point - a connector maps vendor codes into "
    "{retryable, field, code, message, docLink}. A deployment can add a rule (\"route records "
    "missing `email` to a manual-review queue instead of retrying\") without changing the "
    "transport."
)

#: The research's own statement of what it does not know. Carried verbatim
#: because it is the reason two entries below are inferences rather than
#: implementations.
SOURCED_GAP = (
    "HubSpot's per-object multi-status detail is only documented for batch create endpoints in the "
    "page read; whether batch/upsert and batch/update accept objectWriteTraceId was not confirmed "
    "from that page."
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "unknown-codes-are-terminal",
        "topic": "what happens to a vendor code this build has never heard of",
        "basis": (
            "Sourced: the automation names two classes that clear on their own - rate limit and "
            "locked - and says everything else waits for admin action. Not sourced: any policy for "
            "a code outside those two, and the research names no vendor code for either of them."
        ),
        "value": {"default": "terminal", "matched_rule": "unrecognised", "reason_in_row": True},
        "why": (
            "The researched sentence is a whitelist, and reading it as a blacklist inverts it. A "
            "code nobody recognises is not a class anyone has said clears on its own. The "
            "alternative - guessing retryable - turns an unrecognised validation error into a row "
            "re-sent until the attempt bound and still never fixed, and it spends the org's request "
            "quota doing it. The cost is that a genuinely transient error a vendor invented last "
            "month waits for a person, which is the same as a terminal error and a much cheaper "
            "mistake. The row carries the reason it was classified this way, so the queue's "
            "contents are auditable without reading this file."
        ),
        "change_it": 'PATCH /api/wf-040/rules with {"routing": [{"id": "...", "when": {"code": "X"}, "then": {"retryable": true}, "basis": "..."}]}.',
        "effect_on_default": "an unlisted vendor code waits for an admin and says why.",
    },
    {
        "id": "rate-limit-is-http-429",
        "topic": "which transport signal counts as the researched \"rate limit\" class",
        "basis": (
            "Sourced: \"rate limit\" is named as a retryable class, and Salesforce's "
            "REQUEST_LIMIT_EXCEEDED is quoted as the one vendor-specific code. Not sourced: any "
            "statement that 429 is the signal HubSpot or Dataverse use for it."
        ),
        "value": {"status": 429, "retryable": True, "codes": ["REQUEST_LIMIT_EXCEEDED"]},
        "why": (
            "The research names the class and one instance of it, and 429 is the transport-level "
            "signal all three vendors use for throttling. The instance is coded from the quotation "
            "and the class from the transport, and the row's basis says which is which. Without "
            "this, a throttled HubSpot batch lands in a human's queue, which defeats the "
            "automation the research describes."
        ),
        "change_it": "The classification table entry `throttled` in dsr/partial_failures/normalise.py, or a routing rule.",
        "effect_on_default": "a 429 is queued for the automatic drain; a 403 is not, unless it is REQUEST_LIMIT_EXCEEDED.",
    },
    {
        "id": "lock-versus-precondition",
        "topic": "the research's \"locked\" against Dataverse's 412 conditions",
        "basis": (
            "Sourced on both sides, and in tension. The automation names \"locked\" as a retryable "
            "class. Dataverse documents 412 Precondition Failed for ConcurrencyVersionMismatch and "
            "DuplicateRecord. The source set names no code for a lock."
        ),
        "value": {"retryable_412": ["LockMismatch"], "terminal_412": ["ConcurrencyVersionMismatch", "DuplicateRecord"]},
        "why": (
            "Taken literally, the two statements contradict each other: 412 is terminal, and locked "
            "is retryable. Read as one, they are consistent. A duplicate record never clears on its "
            "own - something has to be changed about the row - and a version mismatch means the row "
            "was written against a version that has moved, so re-sending it unchanged repeats the "
            "same mismatch. A lock is the 412 whose holder lets go. So the two *sourced* codes are "
            "terminal, and the class the research named as retryable is coded to the one condition "
            "that behaves like a lock. The honest cost: LockMismatch is a code this build supplies, "
            "not one the source set quotes, so a deployment whose Dataverse spells a lock "
            "differently needs a routing rule."
        ),
        "change_it": "The classification table entry `lock-mismatch`, or a routing rule keyed on the code your tenant returns.",
        "effect_on_default": "412 ConcurrencyVersionMismatch and DuplicateRecord wait for a person; 412 LockMismatch drains.",
    },
    {
        "id": "server-faults-are-retryable",
        "topic": "whether a 5xx is a class that clears on its own",
        "basis": (
            "Not sourced. The research's two retryable classes are rate limit and locked, and no "
            "source in the set mentions a 5xx at all."
        ),
        "value": {"status_range": "500-599", "retryable": True},
        "why": (
            "A 5xx is the one class where the identical request is expected to succeed unchanged, "
            "which is the property the automatic drain relies on. It is bounded by max_attempts, so "
            "a vendor that answers 503 forever produces a row that becomes a person's problem "
            "after five sends rather than a loop. If a reviewer disagrees, one routing rule with "
            "when: {\"http_status\": 503} and then: {\"retryable\": false} reverses it."
        ),
        "change_it": "The classification table entry `server-fault`, or a routing rule on http_status.",
        "effect_on_default": "a 503 is queued for the automatic drain, up to max_attempts.",
    },
    {
        "id": "dataverse-correlation-is-positional",
        "topic": "how a Dataverse per-item result is matched to the row that caused it",
        "basis": (
            "Sourced: the header, the 200 OK, the error body and the HelpLink annotation. Not "
            "sourced: any correlation key. The research says individual response errors are "
            "included in the batch response body and names nothing that ties one back to a request."
        ),
        "value": {"basis": "request_order", "prefers": "a returned id matching a row's trace_id", "row_records_basis": True},
        "why": (
            "Position is the only option the documented contract leaves. It is correct for a batch "
            "the connector built and sent in one go, and wrong the moment a vendor reorders, drops "
            "or inserts an item - which is exactly when a human is looking at this log. So the "
            "correlation basis is stored on every row, a result that matches no row is kept as an "
            "unattributed error rather than assigned to a neighbour, and a row with no result is "
            "failed rather than assumed written. The failure mode is therefore visible in three "
            "places rather than silent in one."
        ),
        "change_it": "Row data field `correlation_basis`; a connector that returns a per-item id overrides the positional match automatically.",
        "effect_on_default": "rows correlate by position and say so; Salesforce's returned record id wins when present.",
    },
    {
        "id": "the-property-falls-back-to-the-rooms-own-rules",
        "topic": "which property the Sync log names when the vendor names none",
        "basis": (
            "Sourced: the log must show \"a human-readable reason and the offending property\", and "
            "the detail view shows \"which property, what was sent, what was expected\". Not "
            "sourced: any vendor key for the property. Salesforce's per-item error is quoted with "
            "fields; HubSpot's and Dataverse's are not."
        ),
        "value": {"vendor_first": True, "fallback": "the room's own pre-flight rule for that field", "recorded_as": "field_basis"},
        "why": (
            "A detail view with no property in it is the thing this workflow exists to prevent, and "
            "reading the property out of the room's validation metadata is honest - it is a real "
            "rule that says what that field should be. The two sources can disagree, so the row "
            "records which one it used. A row that failed a validation the room has no rule for is "
            "marked as a gap, which is the symptom of a configuration that needs extending and is "
            "otherwise invisible until someone reads the diff."
        ),
        "change_it": "PATCH /api/wf-040/rules with a preflight entry; the fallback and the gap flag both follow from it.",
        "effect_on_default": "a HubSpot result with no property key still shows the property, and says the room supplied it.",
    },
    {
        "id": "validation-rules-ship-with-three",
        "topic": "which pre-flight rules this build starts with",
        "basis": (
            "Sourced: the HubSpot enforcement statement, the quoted Dataverse refusal naming "
            "'subject' and 200, and the extension example naming a record \"missing `email`\". Not "
            "sourced: any other field, limit, or the fact that these three are the ones a team "
            "needs."
        ),
        "value": {
            "rules": [
                "hubspot-contact-email-required",
                "dataverse-task-subject-length",
                "salesforce-contact-email-required",
            ],
            "kinds": ["required", "max_length", "min_length"],
        },
        "why": (
            "Three rules is enough that /validate does something on a fresh deployment and few "
            "enough that each one is traceable to a string the research quotes. min_length is the "
            "only kind not evidenced anywhere; it is the same measurement as the sourced "
            "max_length read the other way, and a deployment that wants only the sourced half "
            "deletes it with one PATCH. Everything else a CRM will validate - enums, patterns, "
            "types, cross-field rules, plug-ins - is deliberately not implemented, because "
            "guessing at a rule the source does not state produces a refusal nobody can act on."
        ),
        "change_it": "PATCH /api/wf-040/rules with a preflight entry, or without one to remove a shipped rule.",
        "effect_on_default": "a HubSpot contact with no email and a Dataverse task with a 300-character subject are refused before the batch is sent.",
    },
    {
        "id": "an-unenforced-rule-is-refused-not-ignored",
        "topic": "what happens to a rule this build cannot enforce",
        "basis": (
            "Not sourced. The research says HubSpot enforces admin-configured validation rules and "
            "that they can be retrieved with the property validation API, so rule kinds beyond the "
            "three here exist in the world; it does not enumerate them."
        ),
        "value": {"unknown_kind": "400", "unknown_key": "400", "message": "names the accepted kinds"},
        "why": (
            "A stored rule that is silently unenforced looks exactly like a rule that passes, and "
            "the failure it hides is the expensive one: a mapping the team believes is checked, and "
            "a CRM call it believes is being saved. The same reasoning applies to a typo like "
            "maxLen. Refusing the patch means the failure happens at the moment someone is looking "
            "at the configuration, not the first time a live deal is written wrong."
        ),
        "change_it": "PREFLIGHT_KINDS in dsr/partial_failures/validation.py, plus a branch in check_row.",
        "effect_on_default": "a patch naming an unknown kind is a 400 listing the three that are enforced.",
    },
    {
        "id": "the-automatic-drain-is-bounded",
        "topic": "how many automatic attempts, and how long between them",
        "basis": (
            "Sourced: the drain exists and is automatic, and only for retryable classes. Not "
            "sourced: any attempt bound, any backoff, or what happens when a retryable class "
            "stops being retryable."
        ),
        "value": {"max_attempts": 5, "backoff": "exponential from 30s, doubling, capped at 30m", "on_expiry": "needs_action"},
        "why": (
            "A queue that retries forever is not draining, and a row in a queue forever is a row "
            "nobody ever looks at. The bound is what makes the researched automation terminate: a "
            "row that survives five sends is not a throttle, so it moves to the researched "
            "\"waits for admin action\" state instead of cycling. The counter does not reset on a "
            "further failure, because a row that has been refused six times has a mapping problem "
            "and forgetting that would put it back in the queue as though it were new. A manual "
            "retry is deliberately not capped: an admin who has just fixed the mapping is "
            "answering a question the queue cannot."
        ),
        "change_it": 'PATCH /api/wf-040/rules with {"max_attempts": N}.',
        "effect_on_default": "the fifth automatic attempt is followed by needs_action, not by a sixth send.",
    },
    {
        "id": "a-row-with-no-outcome-is-failed",
        "topic": "what a row the response says nothing about becomes",
        "basis": (
            "Sourced: the connector asks for per-record outcomes so that a partial failure is "
            "visible per row. Not sourced: the case where the response is shorter than the request, "
            "which none of the three documents describes."
        ),
        "value": {"code": "NO_PER_RECORD_OUTCOME", "status": "failed", "disposition": "needs_action"},
        "why": (
            "Three readings were available - treat it as a success, treat it as a failure, or drop "
            "it. A success is the one that does damage: the log would show a row as written when "
            "nothing says it was, which is the exact failure this workflow exists to surface. "
            "Dropping it is the second worst, because a dropped row is a row nobody will ever fix. "
            "So it is failed, under a code of the room's own making that says exactly what was "
            "observed, and it waits for a person because the vendor's silence is not evidence of a "
            "rate limit."
        ),
        "change_it": "No knob. It is the outcome of NO_PER_RECORD_OUTCOME in normalise.py and is asserted in the tests.",
        "effect_on_default": "a response with fewer results than rows produces an explicit failed row for each missing one.",
    },
    {
        "id": "preflight-refuses-and-writes-nothing",
        "topic": "whether a locally-refused row appears in the Sync log",
        "basis": (
            "Sourced: the title's \"reject invalid writes before commit\", and the flow's \"Admin "
            "fixes the mapping or the data, and clicks Retry failed rows only\". Not sourced: "
            "whether a pre-flight refusal is itself a loggable event."
        ),
        "value": {"writes_rows": False, "writes_audit_rows": False, "endpoint": "POST /api/wf-040/validate"},
        "why": (
            "It cannot be a row. A row in the Sync log means a result came back, and no request was "
            "sent - so a row there would claim a vendor interaction that never happened, and "
            "\"Retry failed rows only\" would then offer to re-send a row that was never refused by "
            "anyone. The refusal is returned to the caller in full, which is where the admin is "
            "standing when they need it, and the batch that may be sent is returned alongside. "
            "POST /validate therefore leaves no audit rows, which the suite asserts."
        ),
        "change_it": "No knob. check_batch is pure; the log records only what a vendor answered.",
        "effect_on_default": "a refused row costs no CRM call and does not appear in the log.",
    },
    {
        "id": "the-drain-is-the-connector-not-the-server",
        "topic": "who actually sends the retried rows",
        "basis": (
            "Sourced: the request headers, the statuses, the error bodies, the automation, the "
            "extensibility point. Not sourced: any statement that this product holds vendor "
            "credentials, and the research never claims it does."
        ),
        "value": {
            "room_transports_nothing": True,
            "drain_accepts": "the responses the connector collected",
            "without_responses": "reports the batch it would send, by row and trace id",
        },
        "why": (
            "Every apis_hit in the research is a call a connector makes, and this product has no "
            "portal, no OAuth client and no per-tenant secret. Inventing an HTTP client here would "
            "put a token store in a workflow whose subject is a log and a policy. So the drain is "
            "driven by the connector: it is handed the rows that are due, sends them, and hands "
            "back the responses through the same normaliser. Called with no responses it reports "
            "what it would have sent, which is what a scheduler needs to know and what makes the "
            "automation inspectable without inventing a job."
        ),
        "change_it": "No knob. The endpoints accept the vendor response and never open a socket.",
        "effect_on_default": "POST /api/wf-040/queue/drain with no body returns the batch; with a body it applies it.",
    },
    {
        "id": "numErrors-is-hubspots-to-reconcile",
        "topic": "which vendor's error count the room checks itself against",
        "basis": (
            "Sourced: the HubSpot response contains \"numErrors\": 1 and an errors array. Not "
            "sourced: any count from Dataverse or Salesforce."
        ),
        "value": {"reconciled_for": ["hubspot"], "mismatch_is": "a note on the run, not a refusal", "silent_when": "absent"},
        "why": (
            "Only HubSpot is documented to state a count, so only HubSpot is reconciled. A mismatch "
            "is reported rather than treated as a failure: the vendor's count may include something "
            "the room does not model, and refusing a log because a count disagrees would throw away "
            "the per-row detail that is still there. Absence is not disagreement and says nothing - "
            "the field is null, not true - because a flag that is on by default trains a reader to "
            "ignore it."
        ),
        "change_it": "BatchOutcome.consistent, computed in normalise.py; the note is in notes[].",
        "effect_on_default": "a HubSpot batch whose numErrors disagrees with its errors array is flagged, not rejected.",
    },
)


def describe() -> dict[str, Any]:
    """The whole registry, served verbatim at ``/api/wf-040/inferences``."""
    return {
        "sourced_automation": SOURCED_AUTOMATION,
        "sourced_extensibility": SOURCED_EXTENSIBILITY,
        "sourced_gap": SOURCED_GAP,
        "count": len(INFERENCES),
        "inferences": [dict(entry) for entry in INFERENCES],
        "ids": [str(entry["id"]) for entry in INFERENCES],
    }


def by_id(inference_id: str) -> dict[str, Any] | None:
    return next((entry for entry in INFERENCES if entry["id"] == inference_id), None)


__all__ = [
    "SOURCED_AUTOMATION",
    "SOURCED_EXTENSIBILITY",
    "SOURCED_GAP",
    "INFERENCES",
    "describe",
    "by_id",
]
