"""The pure rules of WF-119.

Everything here takes plain data and an explicit moment. Nothing reads a clock,
nothing touches a store, and nothing imports from ``dsr``, so a rule is testable
without a database and cannot depend on when CI runs. The engine freezes time by
passing ``now``; a rule that needs the current moment takes it as an argument.

The two rules this workflow is named for live here:

* the documented envelope is validated before a contact is held, and the
  ``extras`` object that resumes it is verified against the stored hold rather
  than trusted; and
* a hold has a derived window, evaluated lazily, so a restart cannot lose the
  fact that a hold lapsed.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from . import vocabulary as vocab

_KEY_RE = re.compile(vocab.GATE_KEY_PATTERN)

#: The outcomes a resume can carry, in the order the page renders them.
RESUME_OUTCOMES: tuple[str, ...] = vocab.OUTCOMES

#: ``hold_decision`` results. Each names a distinct next step, so a caller
#: cannot fold one into another by accident.
DECISION_OK = "ok"
DECISION_REPLAY = "replay"
DECISION_CONFLICT = "conflict"
DECISION_EXPIRED = "expired"


# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #


def parse_time(value: Any) -> datetime | None:
    """Parse a moment from an ISO string, a datetime, or ``None``.

    A naive datetime is read as UTC rather than as local time, because every
    timestamp this project stores is UTC and reading one as local would make a
    test pass or fail depending on the machine's timezone.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def to_iso(moment: datetime) -> str:
    """A sortable, unambiguous timestamp, in the shape the store writes."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def expires_at(paused_at: Any, hold_seconds: int) -> str:
    """When a hold raised at ``paused_at`` lapses."""
    start = parse_time(paused_at) or datetime.now(timezone.utc)
    return to_iso(start + timedelta(seconds=int(hold_seconds)))


def is_expired(hold: Mapping[str, Any], now: Any) -> bool:
    """Has this hold lapsed, and is it still waiting?

    A resumed hold never expires: the decision is already recorded, and expiry
    would overwrite a fact with an inference.
    """
    data = dict(hold.get("data") or hold)
    if data.get("state") != vocab.STATE_HELD:
        return False
    deadline = parse_time(data.get("expires_at"))
    moment = parse_time(now)
    if deadline is None or moment is None:
        return False
    return moment >= deadline


# --------------------------------------------------------------------------- #
# Keys and the hold window
# --------------------------------------------------------------------------- #


def key_problem(key: Any) -> str | None:
    """Why this gate key cannot be used, or ``None``."""
    if not isinstance(key, str):
        return f"the gate key must be a string, not {type(key).__name__}"
    if not key:
        return "the gate key is required"
    if len(key) > vocab.GATE_KEY_MAX:
        return f"the gate key must be at most {vocab.GATE_KEY_MAX} characters"
    if not _KEY_RE.match(key):
        return (
            "the gate key must start and end with a letter or digit and may contain "
            "only lowercase letters, digits, underscores and hyphens"
        )
    return None


def hold_seconds_problem(value: Any) -> str | None:
    """Why this hold window is out of range, or ``None``."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "the hold window must be a number of seconds"
    seconds = int(value)
    if seconds < vocab.MIN_HOLD_SECONDS or seconds > vocab.MAX_HOLD_SECONDS:
        return (
            f"the hold window must be between {vocab.MIN_HOLD_SECONDS} and "
            f"{vocab.MAX_HOLD_SECONDS} seconds"
        )
    return None


def hold_seconds(gate_data: Mapping[str, Any]) -> int:
    """The window this gate holds a contact for, defaulting to the derived one."""
    value = dict(gate_data).get("hold_seconds")
    if value is None or hold_seconds_problem(value):
        return vocab.DEFAULT_HOLD_SECONDS
    return int(value)


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


def branch_map(gate_data: Mapping[str, Any]) -> dict[str, str]:
    """The gate's branch per outcome, with any missing or blank entry dropped."""
    raw = dict(gate_data).get(vocab.BRANCHES_KEY)
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, str] = {}
    for outcome in vocab.OUTCOMES:
        branch = raw.get(outcome)
        if isinstance(branch, str) and branch.strip():
            result[outcome] = branch.strip()
    return result


def branch_for(gate_data: Mapping[str, Any], outcome: str) -> str | None:
    """The branch the gate declares for one outcome, or ``None``."""
    return branch_map(gate_data).get(outcome)


def gate_errors(data: Mapping[str, Any]) -> list[str]:
    """Every problem with a gate payload, so an author fixes them in one pass."""
    problems: list[str] = []
    gate_key = data.get("key")
    problem = key_problem(gate_key)
    if problem:
        problems.append(problem)
    for field in ("workflow_id", "action_key"):
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"the gate {field} is required")
    pause = data.get(vocab.PAUSE_EXECUTION_KEY)
    if pause is not None and not isinstance(pause, bool):
        problems.append(f"{vocab.PAUSE_EXECUTION_KEY} must be true or false")
    branches = data.get(vocab.BRANCHES_KEY)
    if not isinstance(branches, Mapping):
        problems.append(f"the gate {vocab.BRANCHES_KEY} must be an object of outcome to branch")
    else:
        for outcome in vocab.OUTCOMES:
            branch = branches.get(outcome)
            if not isinstance(branch, str) or not branch.strip():
                problems.append(f"the gate needs a branch for the {outcome} outcome")
    problem = hold_seconds_problem(data.get("hold_seconds"))
    if problem:
        problems.append(problem)
    return problems


# --------------------------------------------------------------------------- #
# The documented envelope
# --------------------------------------------------------------------------- #


def extras_problems(extras: Any) -> list[str]:
    """Problems with the ``extras`` object, which is the correlation key."""
    if not isinstance(extras, Mapping):
        return [f"{vocab.ENVELOPE_EXTRAS} must be an object"]
    problems: list[str] = []
    for field in vocab.EXTRAS_KEYS:
        value = extras.get(field)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{vocab.ENVELOPE_EXTRAS}.{field} is required and must be a string")
    return problems


def normalise_extras(extras: Mapping[str, Any]) -> dict[str, str]:
    """The correlation key as three stripped strings, in a fixed order."""
    return {field: str(extras.get(field, "")).strip() for field in vocab.EXTRAS_KEYS}


def extras_where(extras: Mapping[str, Any]) -> dict[str, str]:
    """The correlation key as dotted query paths, for the dynamic index."""
    prefix = vocab.ENVELOPE_EXTRAS
    normalised = normalise_extras(extras)
    return {f"{prefix}.{field}": normalised[field] for field in vocab.EXTRAS_KEYS}


def envelope_problems(envelope: Any) -> list[str]:
    """Problems with the action POST body, before any contact is held."""
    if not isinstance(envelope, Mapping):
        return ["the action body must be an object"]
    problems: list[str] = []
    data = envelope.get(vocab.ENVELOPE_DATA)
    if data is not None and not isinstance(data, Mapping):
        problems.append(f"{vocab.ENVELOPE_DATA} must be an object when present")
    problems.extend(extras_problems(envelope.get(vocab.ENVELOPE_EXTRAS)))
    meta = envelope.get(vocab.ENVELOPE_META)
    if meta is not None:
        if not isinstance(meta, Mapping):
            problems.append(f"{vocab.ENVELOPE_META} must be an object when present")
        else:
            key = meta.get(vocab.META_KEY)
            if not isinstance(key, str) or not key.strip():
                problems.append(f"{vocab.ENVELOPE_META}.{vocab.META_KEY} is required")
    return problems


def normalise_meta(meta: Any) -> dict[str, Any]:
    """The ``meta`` object, with the version defaulted when it is absent."""
    if not isinstance(meta, Mapping):
        return {vocab.META_KEY: "", vocab.META_VERSION: vocab.DEFAULT_META_VERSION}
    version = meta.get(vocab.META_VERSION)
    return {
        vocab.META_KEY: str(meta.get(vocab.META_KEY, "")).strip(),
        vocab.META_VERSION: vocab.DEFAULT_META_VERSION if version is None else version,
    }


# --------------------------------------------------------------------------- #
# The resume
# --------------------------------------------------------------------------- #


def resolve_outcome(url_outcome: Any, body_outcome: Any) -> tuple[str | None, str | None]:
    """The outcome a resume means, and the problem if two sources disagree.

    The URL names the outcome and is authoritative. A body that names the same
    outcome is accepted; a body that names a different one is a refusal, because
    one of the two is wrong and nothing here can tell which.
    """
    if url_outcome not in vocab.OUTCOMES:
        return None, f"{url_outcome!r} is not a resume outcome"
    if body_outcome is None or body_outcome == "":
        return str(url_outcome), None
    if str(body_outcome) == str(url_outcome):
        return str(url_outcome), None
    return (
        None,
        f"the resume body names {body_outcome!r} but the URL names {url_outcome!r}",
    )


def resolve_branch(
    gate_data: Mapping[str, Any], outcome: str, body_branch: Any
) -> tuple[str | None, str | None]:
    """The branch for this outcome, verified against the gate.

    The branch is read from the resume body as the specification says, and then
    it must equal the branch the gate declares. A branch the gate never declared
    is refused rather than trusted, and a body that omits it takes the gate's.
    """
    expected = branch_for(gate_data, outcome)
    if expected is None:
        return None, f"the gate declares no branch for the {outcome} outcome"
    if body_branch is None or body_branch == "":
        return expected, None
    if str(body_branch) != expected:
        return None, f"the gate declares {expected!r} for {outcome}, not {body_branch!r}"
    return expected, None


def hold_decision(hold: Mapping[str, Any], outcome: str, branch: str, now: Any) -> str:
    """What a resume of this hold must do: ok, replay, conflict or expired.

    Kept pure and separate from the writes so the four cases are enumerated in
    one place and a route cannot collapse two of them into the same answer.
    """
    data = dict(hold.get("data") or hold)
    if is_expired(hold, now):
        return DECISION_EXPIRED
    if data.get("state") == vocab.STATE_RESUMED:
        if data.get("outcome") == outcome and data.get("branch") == branch:
            return DECISION_REPLAY
        return DECISION_CONFLICT
    return DECISION_OK


def extras_match(hold: Mapping[str, Any], extras: Mapping[str, Any]) -> bool:
    """Is this hold the one the correlation key names?

    All three extras fields must match. A partial match is not a match, which is
    what makes the key a key rather than a hint.
    """
    stored = dict(hold.get("data") or hold).get(vocab.ENVELOPE_EXTRAS)
    if not isinstance(stored, Mapping):
        return False
    wanted = normalise_extras(extras)
    return all(str(stored.get(field, "")).strip() == wanted[field] for field in vocab.EXTRAS_KEYS)


__all__ = [
    "DECISION_CONFLICT",
    "DECISION_EXPIRED",
    "DECISION_OK",
    "DECISION_REPLAY",
    "RESUME_OUTCOMES",
    "branch_for",
    "branch_map",
    "envelope_problems",
    "expires_at",
    "extras_match",
    "extras_problems",
    "extras_where",
    "gate_errors",
    "hold_decision",
    "hold_seconds",
    "hold_seconds_problem",
    "is_expired",
    "key_problem",
    "normalise_extras",
    "normalise_meta",
    "parse_time",
    "resolve_branch",
    "resolve_outcome",
    "to_iso",
]
