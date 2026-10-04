"""Every judgement call this workflow rests on, and how to change each one.

The research fixes a great deal: the token bucket per connection, the header names,
the 429 and the 423, the two-second lock floor, the ``Retry-After`` header on a
477, and the promise that a retry reuses its idempotency key. It is silent about
the rest, and the silence is where the bugs live. Each entry below names the gap,
the choice, why, and the one thing to change to get a different answer.

Served at ``GET /api/wf-046/inferences`` rather than kept in a diff, because a
reader who has to reconstruct a design decision from a commit is reading the wrong
artefact.
"""

from __future__ import annotations

from typing import Any

from dsr.throttle.backoff import BASE_SECONDS, JITTER_RATIO, MAX_ATTEMPTS, MAX_SECONDS
from dsr.throttle.classify import LOCK_FLOOR_SECONDS, MIGRATION_STATUS, RETRY_AFTER_CAP_SECONDS

#: The decisions, in the order a reader meets them.
INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "backoff-base-is-30s-and-it-shipped-that-way",
        "topic": "how long the first automatic wait is",
        "basis": (
            'Not sourced. The research says the throttle "applies exponential backoff with '
            'jitter" and never states a base or a ceiling.'
        ),
        "value": {"base_seconds": BASE_SECONDS, "max_seconds": MAX_SECONDS},
        "why": (
            "This build did not choose it. WF-040 shipped a 30s base doubling to a 30 minute cap "
            "and its tests pin every rung, so moving the ladder into this package moved the number "
            "rather than re-deciding it. A new value here would change retry behaviour that has "
            "already run in production, which is a different decision and a different review."
        ),
        "change_it": "The BASE_SECONDS and MAX_SECONDS constants in dsr/throttle/backoff.py, plus "
        "WF-040's tests, which pin the rungs.",
        "effect_on_default": "attempt 1 waits 30s, attempt 5 waits 480s, and every attempt from "
        "the eighth waits 1800s.",
    },
    {
        "id": "jitter-is-derived-not-drawn",
        "topic": "where the jitter comes from",
        "basis": (
            'Sourced in one half only: the research says "exponential backoff with jitter". Not '
            "sourced: the distribution, or the seed."
        ),
        "value": {
            "ratio": JITTER_RATIO,
            "seed": "the batch's idempotency key",
            "distribution": "equal",
        },
        "why": (
            "The room stores the wait on the batch and a person reads it, so the wait has to be the "
            "same number every time it is computed. A random draw would make the schedule in the "
            "record a different schedule on every read, and hash() is salted per process, so it "
            "would change on every restart. Equal jitter keeps half the ladder fixed, so a retry "
            "never beats the floor the vendor stated and never exceeds the ceiling."
        ),
        "change_it": "The JITTER_RATIO constant, or the seed passed to dsr.throttle.backoff.schedule.",
        "effect_on_default": "two batches with different keys spread out; one batch always waits the "
        "same number of seconds.",
    },
    {
        "id": "retry-after-cap-is-24h",
        "topic": "the longest wait the room will take on its own",
        "basis": (
            'Quoted: HubSpot "will return a Retry-After response header indicating how many seconds '
            'to wait before retrying the request (typically up to 24 hours)". The research names '
            "the header and no cap."
        ),
        "value": {
            "cap_seconds": RETRY_AFTER_CAP_SECONDS,
            "cap_source": "the vendor's own upper word",
        },
        "why": (
            "Twenty-four hours is the longest wait the vendor itself describes as typical, so it is "
            "the last wait that is still a schedule rather than an absence. Beyond it the batch goes "
            "to needs_action and a person decides, because a queue that will not move for a day is "
            "not a queue."
        ),
        "change_it": "retry_after_cap_seconds on the connection's policy, or the "
        "RETRY_AFTER_CAP_SECONDS constant.",
        "effect_on_default": f"a Retry-After above {RETRY_AFTER_CAP_SECONDS}s is reported as "
        "beyond_cap and nothing is scheduled.",
    },
    {
        "id": "retry-after-is-a-floor-not-a-ceiling",
        "topic": "what happens when Retry-After and the lock floor disagree",
        "basis": (
            'Sourced: the 423 floor ("at least 2 seconds") and the 477 header are separate '
            "sentences from the same vendor. Not sourced: what a room does when a response carries "
            "both."
        ),
        "value": {"rule": "the larger of the two applies", "except": "a 477 is taken verbatim"},
        "why": (
            "A vendor's advice and its own documented floor are both promises about how long the "
            "condition lasts. Taking the shorter one is a bet that the condition has already ended, "
            "and the cost of being wrong is another refused call. A 477 is the exception because the "
            "vendor is describing a migration it is performing, not a throttle, and it says how long "
            "the migration will take."
        ),
        "change_it": "The retry_after branch in dsr.throttle.backoff.schedule.",
        "effect_on_default": f"a 423 that also carries Retry-After: 1 waits "
        f"{LOCK_FLOOR_SECONDS}s, not 1s.",
    },
    {
        "id": "an-unsourced-limit-is-not-a-zero-limit",
        "topic": "what a connection with no published numbers does",
        "basis": (
            "Sourced: Salesforce's limit names and Dataverse's 429. Explicitly not sourced: "
            "Salesforce's per-edition DailyApiRequests allocation and every Dataverse numeric "
            "limit, because the tables were not fetched and the Service Protection page was not "
            "found at a readable URL."
        ),
        "value": {"burst": None, "behaviour": "count and defer on the vendor's own refusal"},
        "why": (
            "A bucket with no capacity is the case where a rate limiter most easily lies. Reporting "
            "zero would refuse every call forever. Reporting the vendor's published average would be "
            "a number nobody can check. So the room counts what it sent, says the connection is "
            "pre-emptively blind, and stops on the 429 it actually receives."
        ),
        "change_it": "PATCH the connection's policy with burst and sustained, or register a policy "
        "for the vendor.",
        "effect_on_default": "Salesforce and Dataverse connections defer on a vendor refusal; HubSpot "
        "refuses a call before it is sent.",
    },
    {
        "id": "a-vendor-without-a-policy-still-runs",
        "topic": "what happens on a connection to a vendor this build has not read about",
        "basis": (
            'Sourced, as an extensibility claim: "adding a vendor means filling in a policy '
            'object, not writing a new backoff algorithm".'
        ),
        "value": {
            "refused": False,
            "policy": "the unsourced policy",
            "registered_by": "policies.register",
        },
        "why": (
            "If an unknown vendor were refused, the extensibility note would be false. The unsourced "
            "policy has no numbers, so the bucket cannot pre-empt, and the record says "
            "sourced: false so nobody reads the row as a researched one."
        ),
        "change_it": "dsr.throttle.policies.register(policy), or POST the connection with a policy.",
        "effect_on_default": "the connection runs on the ladder and the vendor's own refusals.",
    },
    {
        "id": "tokens-are-spent-after-the-vendor-answers",
        "topic": "when the bucket is decremented",
        "basis": "Not sourced. The research says the bucket is sized from the vendor's limits and "
        "never says when a token leaves it.",
        "value": {"order": "decide, send, then spend", "never": "spend optimistically"},
        "why": (
            "A token the room spent and the vendor refused still came off the limit. Spending only "
            "on success would make the room's own count of the budget drift upward every time a "
            "batch was throttled, which is precisely when the count matters most."
        ),
        "change_it": "dsr.throttle.bucket.spend, and where engine.observe calls it.",
        "effect_on_default": "a refused batch costs the connection its tokens.",
    },
    {
        "id": "an-attempt-bound-is-not-a-dead-end",
        "topic": "what happens when a batch has been attempted MAX_ATTEMPTS times",
        "basis": (
            'Sourced, from the sibling workflow: the researched automation "waits for admin action '
            'for validation failures". Not sourced here: how many attempts an automatic drain gets.'
        ),
        "value": {
            "max_attempts": MAX_ATTEMPTS,
            "after": "needs_action",
            "manual_retry": "uncapped",
        },
        "why": (
            "The bound keeps a class that is not a throttle - a mapping error, say - from cycling "
            "until somebody notices. It is not a refusal: a person who has just fixed the cause "
            "retries by hand, and that retry is not capped, because refusing an explicit action "
            "because a counter ran out would turn the researched waiting state into a dead end."
        ),
        "change_it": "The MAX_ATTEMPTS constant, or max_attempts on the submit payload.",
        "effect_on_default": "five automatic attempts, then needs_action.",
    },
    {
        "id": "a-477-is-taken-at-face-value",
        "topic": "why a migration retry is not also laddered",
        "basis": (
            'Quoted: HubSpot returns a Retry-After "typically up to 24 hours" on a 477. Not '
            "sourced: whether the room may send earlier."
        ),
        "value": {"status": MIGRATION_STATUS, "apply_floor": False},
        "why": (
            "The ladder exists to avoid hammering a limit that resets on a known schedule. A "
            "migration has no such schedule, and the vendor has told the room when it will be over. "
            "Doubling a 12-hour answer into 24 would be sending a request the vendor has already "
            "said it is not ready for."
        ),
        "change_it": 'The `kind != "migration"` test in dsr.throttle.backoff.schedule.',
        "effect_on_default": "a 477 waits exactly what the vendor said, up to the cap.",
    },
)


def describe() -> dict[str, Any]:
    """The whole register, for ``GET /inferences``."""
    return {
        "count": len(INFERENCES),
        "inferences": [dict(entry) for entry in INFERENCES],
        "sourced": [entry["id"] for entry in INFERENCES if entry["basis"].startswith("Quoted")],
        "inferred": [
            entry["id"] for entry in INFERENCES if not entry["basis"].startswith("Quoted")
        ],
    }


__all__ = ["INFERENCES", "describe"]
