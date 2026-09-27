"""The retry policy: which classes clear on their own, and when to try again.

The researched automation is one sentence and it is the whole of this module's
mandate:

    Retry queue drains automatically for retryable classes (rate limit, locked) and
    waits for admin action for validation failures - the room classifies errors
    into retryable vs terminal.

What the research does **not** say, and what is therefore published as an
inference rather than built in quietly:

* **How many automatic attempts.** A drain that retries forever is not draining,
  and a row in a queue forever is a row nobody ever looks at. :data:`MAX_ATTEMPTS`
  is the number this build chose, and a row that reaches it moves to
  ``needs_action`` - the researched "waits for admin action" state - rather than
  cycling.
* **How long to wait between attempts.** :func:`backoff_seconds` is exponential
  from :data:`BASE_BACKOFF_SECONDS`, capped at :data:`MAX_BACKOFF_SECONDS`. An
  immediate retry into a rate limit is a second request the vendor will also
  refuse, and the room has no way to ask the vendor when it will be ready.
* **What happens on a retried row that fails differently.** The attempt counter
  does not reset. A row that has been refused six times is a row with a mapping
  problem, and forgetting the history would put it back in the queue as though it
  were new.
* **A manual retry is not capped.** The cap governs the *automatic* drain. An
  admin who has just fixed the mapping is answering a question the queue cannot,
  and refusing their explicit action because a counter ran out would make the
  researched "waits for admin action" state a dead end.

None of these are load-bearing decisions about the researched behaviour. They are
all bounds on it, all one PATCH away, and all named in
:mod:`dsr.partial_failures.inferences`.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.partial_failures.timestamps import parse_instant, plus_seconds

#: How many times the automatic queue will send a row before handing it to a person.
#:
#: Not sourced. The research says the queue drains for retryable classes and never
#: says when to stop, and a queue with no bound is a loop that spends quota and
#: produces a log nobody reads. Five is chosen because it spans the usual short
#: throttle windows with room to spare, and because it is a PATCH.
MAX_ATTEMPTS = 5

#: The first automatic wait, doubling each attempt, capped.
BASE_BACKOFF_SECONDS = 30
MAX_BACKOFF_SECONDS = 1800

#: The room's own label for a wait, so a client can render one schedule rather
#: than three slightly different ones.
BACKOFF_LABEL = "exponential from 30s, doubling, capped at 30m"


def backoff_seconds(attempt: int) -> int:
    """How long to wait before automatic attempt ``attempt + 1``.

    ``attempt`` is the number of attempts already made, so the first wait is
    :data:`BASE_BACKOFF_SECONDS`. Negative and zero attempts are read as the first,
    because a caller that lost count should still get a real wait rather than
    zero.
    """
    made = max(1, int(attempt))
    exponent = min(made - 1, 16)
    return int(min(BASE_BACKOFF_SECONDS * (2**exponent), MAX_BACKOFF_SECONDS))


def next_attempt_at(attempt: int, now) -> Any:
    """The instant the automatic queue would send this row again."""
    return plus_seconds(now, backoff_seconds(attempt))


def is_retryable(error: Mapping[str, Any] | None) -> bool:
    """Whether the room classified this error as one that clears on its own."""
    return bool(error) and bool(error.get("retryable"))


def disposition_for(retryable: bool) -> str:
    """Which of the two researched waiting states a row is in.

    The automation pairs them: a retryable class drains automatically, a terminal
    one waits for an admin. Deriving the disposition from the classification
    rather than storing both is deliberate - two fields that can disagree is a
    class of bug this workflow does not need, and the researched extension point
    only ever speaks about retrying or not.
    """
    return "queued" if retryable else "needs_action"


def plan(
    rows: Sequence[Mapping[str, Any]],
    *,
    max_attempts: int = MAX_ATTEMPTS,
    now=None,
) -> dict[str, Any]:
    """Sort failed rows into everything the retry queue has to do with them.

    Four lists, and the split matters because each one asks a different question of
    the next person to look at this:

    ``drain``
        The researched automation's work list right now: retryable, under the
        attempt bound, and past its backoff.
    ``scheduled``
        Retryable and under the bound, but not yet due. Sending one of these early
        is the immediate retry into a rate limit that produces a second refusal
        and another wait, so the schedule is a rule and not advice.
    ``expired``
        Retryable and past the bound. Each of these is about to become a person's
        problem, and the person needs to know which ones.
    ``waiting``
        Every terminal row, which no amount of draining touches - the researched
        "waits for admin action for validation failures".

    ``now`` is resolved once, so every row in one plan is judged against one
    instant; a queue whose members are each compared against a slightly different
    clock cannot be reasoned about.
    """
    from datetime import datetime, timezone

    moment = now or datetime.now(timezone.utc)

    drain: list[dict[str, Any]] = []
    scheduled: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    succeeded = 0

    for row in rows:
        status = str(row.get("status") or "")
        if status == "succeeded":
            succeeded += 1
            continue
        error = row.get("error") if isinstance(row.get("error"), Mapping) else {}
        attempts = int(row.get("attempts") or 1)
        entry = {
            "id": str(row.get("id") or row.get("row_key") or ""),
            "row_key": row.get("row_key"),
            "run_id": row.get("run_id"),
            "connector": row.get("connector"),
            "entity": row.get("entity"),
            "attempts": attempts,
            "code": error.get("code"),
            "message": error.get("message"),
            "field": error.get("field"),
            "field_basis": row.get("field_basis"),
            "retryable": bool(error.get("retryable")),
            "disposition": row.get("disposition"),
        }

        if not entry["retryable"]:
            waiting.append(entry)
            continue

        if attempts >= max(0, int(max_attempts)):
            entry["expired_at"] = next_attempt_at(attempts, moment)
            entry["expires_because"] = (
                f"the automatic queue has sent this row {attempts} time(s) and the bound is "
                f"{max_attempts}; the researched automation drains retryable classes, and a class "
                "that survives this many attempts is not a throttle"
            )
            expired.append(entry)
            continue

        due = parse_instant(row.get("next_retry_at"), required=False) or moment
        if due > moment:
            entry["next_attempt_at"] = due
            entry["due_in_seconds"] = int((due - moment).total_seconds())
            entry["scheduled_because"] = (
                f"the queue backs off {backoff_seconds(attempts)}s after an attempt, and this row is "
                f"not due until {due.isoformat(timespec='seconds')}"
            )
            scheduled.append(entry)
            continue

        entry["next_attempt_at"] = due
        entry["wait_seconds"] = backoff_seconds(attempts)
        drain.append(entry)

    return {
        "at": moment.isoformat(timespec="seconds"),
        "max_attempts": int(max_attempts),
        "backoff": BACKOFF_LABEL,
        "drain": drain,
        "scheduled": scheduled,
        "expired": expired,
        "waiting": waiting,
        "succeeded": succeeded,
        "counts": {
            "drain": len(drain),
            "scheduled": len(scheduled),
            "expired": len(expired),
            "waiting": len(waiting),
            "succeeded": succeeded,
        },
    }


def can_retry(row: Mapping[str, Any], *, max_attempts: int = MAX_ATTEMPTS, now=None) -> bool:
    """Whether the automatic queue would send this row again.

    Separate from :func:`plan` because the Sync log's per-row answer and the
    queue's work list are two questions, and a row that shows a "Retry" control
    the queue will never act on teaches a rep to distrust the control.
    """
    from datetime import datetime, timezone

    error = row.get("error") if isinstance(row.get("error"), Mapping) else {}
    if not error.get("retryable"):
        return False
    if int(row.get("attempts") or 1) >= max(0, int(max_attempts)):
        return False
    moment = now or datetime.now(timezone.utc)
    due = parse_instant(row.get("next_retry_at"), required=False)
    return due is None or due <= moment


__all__ = [
    "MAX_ATTEMPTS",
    "BASE_BACKOFF_SECONDS",
    "MAX_BACKOFF_SECONDS",
    "BACKOFF_LABEL",
    "backoff_seconds",
    "next_attempt_at",
    "is_retryable",
    "disposition_for",
    "plan",
    "can_retry",
]
