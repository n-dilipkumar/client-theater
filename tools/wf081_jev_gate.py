"""Put WF-081's shape decision to Jev, and write this ticket's own audit file.

The task forbids appending to orchestration/decisions/jev-audit.jsonl, because
four agents appending to one file collide on every merge. This writes to
orchestration/decisions/wf-081-jev-audit.jsonl instead.

The question is the one this finished change actually raises. A gate on a design
document answers a different question. The issue offered two shapes of expiry and
asked the implementer to choose and record the choice, so that is what is put.

Run from the repository root:

    C:\\Users\\Dilip\\dsrvenv\\Scripts\\python.exe tools/wf081_jev_gate.py
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from jev import Jev  # noqa: E402

AUDIT = ROOT / "orchestration" / "decisions" / "wf-081-jev-audit.jsonl"

DECISION = (
    "WF-081 expires a signature request through an explicit POST sweep route and "
    "reminders evaluated by a request-time route, rather than through a background "
    "scheduler thread that fires on its own."
)

OPTIONS = {
    "explicit_route": (
        "Keep the deadline as data. Evaluate the 3-and-7-day cadence and the 24-hour "
        "dedupe as pure functions inside a POST route a caller invokes, and close "
        "expired agreements in a second explicit POST sweep route. Nothing runs on a "
        "timer. Every write names the route that served it, so every audit row names a "
        "route the app serves. The engine takes an injected clock, so every boundary is "
        "testable at an exact instant."
    ),
    "background_thread": (
        "Start a scheduler thread at import time. It wakes on a cadence, evaluates the "
        "reminder windows, sends what is due, and sweeps the expired requests with no "
        "caller involved."
    ),
}

CONTEXT = {
    "stack": "Python + FastAPI, SQLite through AuditedDatabase, records as arbitrary JSON",
    "ticket": "WF-081",
    "issue": 182,
    "spec": "docs/research/digital-sales-room-workflows/wf/WF-081.md",
    "domain": "security-governance",
    "shape_built": "The signature-request shape, not the Papermark room-link shape.",
    "evidence_from_the_research": [
        "Signature request reminder emails will be sent to the signer 3 and 7 days "
        "before the signature request expires, this is in addition to our other current "
        "automated reminders.",
        "If a signer was already reminded within 24 hours, we will skip the automated "
        "reminder.",
        "expires_at must be an integer epoch timestamp in seconds between 1-90 days in "
        "the future.",
        "expires_at will be rounded down to the nearest hour.",
        "Only signature requests that explicitly set an expires_at will expire. By "
        "default signature requests do not expire.",
        "On expiry, unsigned signatures flip to expired. Completed signers stay signed.",
        "Once a signature request has expired, it is considered to be in a final status "
        "like declined and completed signature requests.",
        "All parties to the signature request will still have access to the document "
        "including audit trail, similar to declined signature requests. They will not be "
        "able to sign or modify the signature request.",
        "Emails are muted in all embedded signing flows. Integrations using embedded "
        "signing must consume the signature_request_expired event.",
        "Audit writes an expired audit event with the expiration date listed along with "
        "all the signers who did not sign by the expiration date.",
    ],
    "what_the_research_does_not_say": [
        "It describes a reminder scheduler but never says what triggers it, so the "
        "trigger is a judgement call rather than a sourced requirement.",
        "It names reminder scheduler state as a data source but never says how that "
        "state is stored.",
        "It mentions SMS reminders as conditional on the sender's account setting and "
        "does not say whether this workflow owns that setting. This build sends email "
        "and events only, and records no SMS claim.",
    ],
    "cost_of_the_chosen_option": (
        "A deadline that passes is not acted on until somebody calls the sweep route. A "
        "seller who never calls it sees an agreement still pending past its own deadline. "
        "That gap is visible rather than silent, and it is the price paid for audit rows "
        "that name real routes and for a clock a test can move."
    ),
    "cost_of_the_rejected_option": (
        "A thread needs a clock the tests cannot move, so none of the six researched "
        "boundaries would be testable at an exact instant. Its writes arrive with no "
        "request to attribute them to, so every audit row it writes would either name no "
        "route or name a route that served nothing. It also makes the suite "
        "order-dependent: a thread writing during a test run is a flake nobody can "
        "reproduce."
    ),
    "measured_before_the_decision": {
        "domain_tests_added": 130,
        "http_tests_added": 51,
        "own_files_alone": "181 passed, 1 skipped on a host with no timezone database",
        "ruff_check": "pass",
        "ruff_format_check": "pass",
        "frontend_suite": "751 passed across 28 files",
        "shared_files_touched": 0,
        "seed_string_cp1252": "encodes",
    },
}


def main() -> int:
    client = Jev()
    decision = client.choose_approach(
        problem=DECISION,
        options=OPTIONS,
        context=CONTEXT,
    )
    print(decision.verdict, decision.selected)
    print(decision.reason)

    record = {
        "ticket": "WF-081",
        "issue": 182,
        "audit_id": getattr(decision, "audit_id", ""),
        "decision": DECISION,
        "options": OPTIONS,
        "verdict": decision.verdict,
        "selected": decision.selected,
        "reason": decision.reason,
        "confidence": getattr(decision, "confidence", None),
    }
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    print(f"wrote {AUDIT.relative_to(ROOT)}")
    return 0 if decision.verdict == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())