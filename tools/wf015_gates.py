"""Ad-hoc Jev gates for WF-015. Not part of the test suite."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from jev import Jev  # noqa: E402

client = Jev()

# ------------------------------------------------------------------ #
# 1. How is the verification session represented?
# ------------------------------------------------------------------ #
session = client.choose_approach(
    problem=(
        "For WF-015 (verify buyer identity, restrict by email domain), how should a buyer's "
        "gate session be represented in a system whose only storage guarantee is that every "
        "SQLite write goes through an audited wrapper and whose schema-flexibility rule forbids "
        "adding a typed column or a migration for a team field?"
    ),
    options={
        "server_side_record": (
            "A real `access_session` record whose record id IS the opaque token the buyer "
            "carries. Create/update/soft-delete all go through the audited wrapper, so the "
            "session is revocable, expirable, filterable with find(), and every state change "
            "produces an audit row with a before/after diff. Cost: the token is visible in the "
            "audit log, and sessions accumulate as rows."
        ),
        "stateless_signed_token": (
            "An HMAC-signed self-contained token (room id, email, expiry, signature) with nothing "
            "stored server-side until the buyer actually verifies. Only verified identities are "
            "persisted. Cost: revocation requires a denylist, which reintroduces stored state; "
            "attempts cannot be counted; 'who has seen this room' needs a separate write path; "
            "and there is no per-session audit row."
        ),
        "both": (
            "Stateless signed token for the pending round trip, promoted to a server-side record "
            "on verification. Cost: two code paths and two mental models for the same concept, "
            "and the pending half is still unaudited and uncountable."
        ),
    },
    context={
        "workflow": "WF-015 - verify buyer identity and restrict by email domain",
        "product_promise": "The audit log is complete; the audit row is written in the same transaction as the change.",
        "sourced_requirement": (
            "Buyer enters name + email, receives an email with a link, clicks it, then can view "
            "the page. Identity becomes visible in page analytics afterwards."
        ),
        "constraints": [
            "no new tables, no migrations, no typed columns",
            "all writes audited",
            "sessions must be revocable and expirable to be a real control",
        ],
    },
)
print("=== Q1 session representation ===")
print(session.summary())

# ------------------------------------------------------------------ #
# 2. How is the verification email delivered in an install with no mail server?
# ------------------------------------------------------------------ #
delivery = client.choose_approach(
    problem=(
        "WF-015 needs the buyer to receive 'an email with a link to verify their email address'. "
        "The research describes only the vendor's own email delivery and explicitly records that "
        "there is no public REST endpoint for the security settings. This is an open-source "
        "install that ships no SMTP credentials and cannot assume a mail server exists. How "
        "should the verification message be delivered?"
    ),
    options={
        "outbox_record_seam": (
            "Write the message to a `verification_outbox` record (to, subject, link, status) and "
            "surface the link in the seller UI to copy. One call to replace later with SMTP. "
            "Cost: on a real deployment the seller must copy the link by hand until a transport is "
            "plugged in, and a buyer cannot self-serve."
        ),
        "require_smtp_or_fail": (
            "Require SMTP configuration; refuse to start the verify_email tier without it, and "
            "fail the request loudly if mail cannot be sent. Cost: the workflow cannot be run, "
            "demoed, or tested on a default install, which contradicts the project's rule that a "
            "feature must be verifiable locally."
        ),
        "log_to_stdout": (
            "Print the verification link to the server log. Cost: not queryable through the API, "
            "invisible to the seller, disappears on restart, and cannot be surfaced in the UI."
        ),
    },
    context={
        "workflow": "WF-015 - verify buyer identity and restrict by email domain",
        "deployment": "open source, self-hosted, no bundled mail infrastructure",
        "hard_requirement": "The round trip must be demonstrable and testable in a local install with no mail server.",
        "vendor_behaviour": "vendor sends the mail itself; no API documented",
    },
)
print()
print("=== Q2 verification delivery ===")
print(delivery.summary())

print()
print(f"Q1 passed={session.passed} selected={session.selected}")
print(f"Q2 passed={delivery.passed} selected={delivery.selected}")
