"""WF-134: score a deal from in-room engagement, detect risk, coach the rep.

This module is the whole domain of the workflow. It holds the vocabulary, the
error types, the pure rules and the engine that applies them to stored state.

What the workflow does
----------------------

The platform continuously scores a deal from three kinds of evidence:

1. **Engagement decay.** Room engagement events are a time series. The newest
   event sets the recency; the weighted volume in a rolling window sets the
   depth. A deal the buyer stopped opening decays.
2. **Unreviewed key material.** Each key asset carries whether it must be
   reviewed. A required asset nobody reviewed is a risk reason and a "which
   content do I still owe them" prompt for the rep.
3. **Missing stakeholders.** The buying group is a list of people. Coverage is
   how many of them engaged against a stated target. A role that never engaged
   is a coverage gap.

The three factors are weighted into a score from zero to one hundred. A score
in the at-risk band raises a risk alert. A score that falls by the drop
threshold since the last reading raises a drop alert. Each active risk reason
also raises one coaching cue for the rep. A cue is closed by a recorded
outcome, and the outcomes are the readiness data the closed loop feeds.

Where the rules live
--------------------

Every pure rule is a function of its arguments. None touches the store, a
framework, the network or a route. :class:`DealHealthEngine` is the only place
a rule meets stored state, and it names no route: every write takes a ``source``
argument from its caller and the feature module builds that string from its own
router.

The module imports the store and nothing else from ``dsr``. It opens no
database connection and imports no framework.

The researched specification is
``docs/research/digital-sales-room-workflows/wf/WF-134.md`` and it is quoted in
full in issue 184. The decisions the research left open are recorded in
:data:`DECISIONS` and served by the vocabulary route, so a reviewer reads the
decision rather than the source.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Collections. One shared `records` table carries every feature, so each name is
# namespaced per ticket and `find()` matches on the collection before anything
# else.
# --------------------------------------------------------------------------- #

#: One deal being scored, inside one room.
DEAL_COLLECTION = "wf134_deal"

#: One in-room engagement event (view, dwell, download, comment, share,
#: question, meeting). This is the workflow's own event intake.
EVENT_COLLECTION = "wf134_engagement_event"

#: One known member of the buying group, engaged or not.
STAKEHOLDER_COLLECTION = "wf134_stakeholder"

#: One key asset with whether it must be reviewed and whether it has been.
CONTENT_COLLECTION = "wf134_key_content"

#: One recorded score snapshot: the value, the band, the factors and the reasons.
SCORE_COLLECTION = "wf134_score"

#: One risk alert raised when the band is at risk or the score dropped.
ALERT_COLLECTION = "wf134_risk_alert"

#: One coaching cue for the rep, derived from one active risk reason.
COACHING_COLLECTION = "wf134_coaching_cue"

#: One recorded coaching outcome. The closed loop's readiness data.
OUTCOME_COLLECTION = "wf134_coaching_outcome"

#: The payload-side twin of the envelope's `room_id`. `room_id` is stripped from
#: `data` before the dynamic index is built, so a payload that stored its room
#: there would be unfilterable by `find()`.
ROOM_REF = "room_ref"

#: The payload-side link to the deal a scoped record belongs to.
DEAL_REF = "deal_ref"

#: Keys the store owns. They are stripped from a payload before it is written, so
#: a request that sets them changes nothing.
RESERVED_KEYS = frozenset(
    {"id", "collection", "room_id", "revision", "created_at", "updated_at", "deleted_at"}
)

# --------------------------------------------------------------------------- #
# Engagement event kinds and their weights
# --------------------------------------------------------------------------- #

#: A passer-by opening a page.
KIND_VIEW = "view"
#: Reading a page for a measured time.
KIND_DWELL = "dwell"
#: Taking an asset away.
KIND_DOWNLOAD = "download"
#: Writing a remark on the room.
KIND_COMMENT = "comment"
#: Passing an asset on to a colleague.
KIND_SHARE = "share"
#: Asking the seller a question.
KIND_QUESTION = "question"
#: A live meeting, the strongest single signal.
KIND_MEETING = "meeting"

EVENT_KINDS = (
    KIND_VIEW,
    KIND_DWELL,
    KIND_DOWNLOAD,
    KIND_COMMENT,
    KIND_SHARE,
    KIND_QUESTION,
    KIND_MEETING,
)

#: What one event of each kind counts for when the volume of engagement is
#: measured. A meeting counts four times a bare view, because a meeting is four
#: times the buyer's time. The numbers are this workflow's derivation; the
#: research names the events and gives no weights.
EVENT_WEIGHTS: dict[str, float] = {
    KIND_VIEW: 0.5,
    KIND_DWELL: 1.0,
    KIND_DOWNLOAD: 1.0,
    KIND_COMMENT: 1.5,
    KIND_SHARE: 1.0,
    KIND_QUESTION: 1.5,
    KIND_MEETING: 2.0,
}

#: The default weight of an event this workflow does not recognise. A stored
#: kind outside the list still counts, at the lowest weight, rather than
#: crashing a read.
UNKNOWN_EVENT_WEIGHT = 0.5

# --------------------------------------------------------------------------- #
# The score: weights, windows, targets and bands. See DECISIONS["score_formula"].
# --------------------------------------------------------------------------- #

#: How much each factor contributes to the score. The three sum to one.
WEIGHTS: dict[str, float] = {"engagement": 0.5, "content": 0.3, "stakeholder": 0.2}

#: The rolling window, in days, over which engagement is measured. Derived from
#: the seeded demo's own 21-day activity window and a typical room's review
#: cadence. A room quiet for longer than this has decayed.
DECAY_WINDOW_DAYS = 21

#: The weighted event volume that counts as full engagement. Six weighted events
#: is roughly one viewed asset, one dwell and one meeting.
TARGET_EVENT_WEIGHT = 6.0

#: How the engagement factor splits between recency and volume.
RECENCY_SHARE = 0.6
VOLUME_SHARE = 0.4

#: The engagement factor below which the deal is reported as decaying.
ENGAGEMENT_FLOOR = 0.5

#: The share of the buying group that must have engaged for coverage to count as
#: full. The research names missing stakeholders as a risk and gives no target.
COVERAGE_TARGET = 0.6

#: The fall, in points, since the last reading that raises a drop alert.
DROP_THRESHOLD = 10

#: The score is healthy at or above this value.
HEALTHY_FLOOR = 70

#: The score is on watch at or above this value, and at risk below it.
WATCH_FLOOR = 45

#: The deal is in good shape.
BAND_HEALTHY = "healthy"
#: The deal needs watching.
BAND_WATCH = "watch"
#: The deal is in trouble, and this is the band that raises an alert.
BAND_AT_RISK = "at_risk"

BANDS = (BAND_HEALTHY, BAND_WATCH, BAND_AT_RISK)

# --------------------------------------------------------------------------- #
# Risk reasons. Each is a stable machine token, so the page branches on it
# instead of parsing an English sentence.
# --------------------------------------------------------------------------- #

REASON_NO_ENGAGEMENT = "no_engagement"
REASON_ENGAGEMENT_DECAY = "engagement_decay"
REASON_UNREVIEWED_KEY_MATERIAL = "unreviewed_key_material"
REASON_MISSING_STAKEHOLDERS = "missing_stakeholders"
REASON_BELOW_TARGET = "below_health_target"
REASON_SCORE_DROP = "score_drop"

RISK_REASONS = (
    REASON_NO_ENGAGEMENT,
    REASON_ENGAGEMENT_DECAY,
    REASON_UNREVIEWED_KEY_MATERIAL,
    REASON_MISSING_STAKEHOLDERS,
    REASON_BELOW_TARGET,
    REASON_SCORE_DROP,
)

#: One sentence per reason, so a card explains itself without the page owning the
#: wording.
REASON_SENTENCES: dict[str, str] = {
    REASON_NO_ENGAGEMENT: "No engagement is recorded in the room.",
    REASON_ENGAGEMENT_DECAY: "Engagement decayed: the newest event is too old or too light.",
    REASON_UNREVIEWED_KEY_MATERIAL: "Required key material is still unreviewed.",
    REASON_MISSING_STAKEHOLDERS: "Part of the buying group never engaged.",
    REASON_BELOW_TARGET: "Deal health is in the at-risk band.",
    REASON_SCORE_DROP: "Deal health fell since the previous reading.",
}

# --------------------------------------------------------------------------- #
# Alerts and coaching
# --------------------------------------------------------------------------- #

#: Raised when the score is in the at-risk band.
ALERT_BAND = "band"
#: Raised when the score fell by the drop threshold.
ALERT_DROP = "drop"

ALERT_KINDS = (ALERT_BAND, ALERT_DROP)

#: The cue is open and waiting for an outcome.
CUE_OPEN = "open"

#: One coaching action per risk reason. Rule-based, not generated by a model. See
#: DECISIONS["coaching_scope"].
COACHING_ACTIONS: dict[str, str] = {
    REASON_NO_ENGAGEMENT: (
        "Ask for a first working session. The room has no recorded engagement yet."
    ),
    REASON_ENGAGEMENT_DECAY: (
        "Open the room with the buyer again. Send the newest asset and ask for a review."
    ),
    REASON_UNREVIEWED_KEY_MATERIAL: (
        "Walk the buyer through the key material that is still unreviewed."
    ),
    REASON_MISSING_STAKEHOLDERS: (
        "Bring the stakeholder who never engaged onto the next call, or name the missing role."
    ),
    REASON_BELOW_TARGET: "Book the next mutual action step before the next milestone.",
    REASON_SCORE_DROP: "Ask what changed since the last reading before the next milestone.",
}

#: The three outcomes a coaching cue can be closed with.
OUTCOME_ACKNOWLEDGED = "acknowledged"
OUTCOME_ACTIONED = "actioned"
OUTCOME_DISMISSED = "dismissed"

OUTCOME_STATES = (OUTCOME_ACKNOWLEDGED, OUTCOME_ACTIONED, OUTCOME_DISMISSED)

# --------------------------------------------------------------------------- #
# Qualification frameworks. See DECISIONS["framework"].
# --------------------------------------------------------------------------- #

FRAMEWORK_MEDDICC = "meddicc"
FRAMEWORK_BANT = "bant"
FRAMEWORK_VALUE_SELLING = "value_selling"

FRAMEWORKS = (FRAMEWORK_MEDDICC, FRAMEWORK_BANT, FRAMEWORK_VALUE_SELLING)

#: The framework a deal carries when the payload is silent.
DEFAULT_FRAMEWORK = FRAMEWORK_MEDDICC

#: The criteria each framework names, so the score card can show the chosen
#: framework beside the evidence. The score itself is framework-neutral: the
#: three factors are the same whichever framework the rep qualified against.
FRAMEWORK_CRITERIA: dict[str, tuple[str, ...]] = {
    FRAMEWORK_MEDDICC: (
        "Metrics",
        "Economic buyer",
        "Decision criteria",
        "Decision process",
        "Identify pain",
        "Champion",
        "Competition",
    ),
    FRAMEWORK_BANT: ("Budget", "Authority", "Need", "Timeline"),
    FRAMEWORK_VALUE_SELLING: (
        "Business drivers",
        "Value hypothesis",
        "Quantified impact",
        "Proof",
        "Mutual plan",
    ),
}

#: How many factors the score is built from.
FACTOR_COUNT = 3

# --------------------------------------------------------------------------- #
# Error types. Three types are declared here and nothing else in the product
# raises them. The feature host refuses a second feature that registers a
# handler for the same type, so a collision is a load failure rather than a
# silent shadow. None is a Python builtin: a handler registered for `ValueError`
# directly would intercept that exception across the whole product.
# --------------------------------------------------------------------------- #


class DealHealthError(ValueError):
    """A deal, an event, a stakeholder or an asset this workflow will not accept.

    Carries a field-keyed map, because a rep filling in a form needs the message
    beside the input that caused it. The HTTP layer renders the map as
    ``errors``.
    """

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})


class DealHealthNotFound(LookupError):
    """No such deal, event, stakeholder, asset, score, alert or cue.

    Its own type rather than the store's ``RecordNotFound``, because a feature
    may map only the error types it raises itself. Registering a handler for the
    shared type would intercept that exception across the whole product.
    """


class DealHealthRefused(PermissionError):
    """A change the deal's current state will not accept.

    Raised when a write does not read back as written. ``reason`` is a stable
    machine token, so the page can branch on it instead of parsing an English
    sentence.
    """

    #: A write was made but did not read back. The room must not report success.
    REASON_WRITE_NOT_VISIBLE = "write_not_visible"

    #: A cue was already closed, so a second outcome would rewrite history.
    REASON_ALREADY_CLOSED = "cue_already_closed"

    _MESSAGES = {
        REASON_WRITE_NOT_VISIBLE: "The change was written but did not read back.",
        REASON_ALREADY_CLOSED: "That coaching cue already has an outcome.",
    }

    def __init__(self, reason: str, detail: str | None = None) -> None:
        super().__init__(detail or self._MESSAGES.get(reason, "That change was refused."))
        self.reason = reason


# --------------------------------------------------------------------------- #
# Coercion
# --------------------------------------------------------------------------- #


def read_int(value: Any, default: int | None = None) -> int | None:
    """Read a whole number, or ``default`` when the value is not one.

    A JSON body carries counts as numbers, but a query string arrives as text,
    so a numeric string is read as a number. A bool is refused: ``True`` is not
    a count.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def read_float(value: Any, default: float | None = None) -> float | None:
    """Read a decimal number, or ``default`` when the value is not one."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def read_bool(value: Any, default: bool) -> bool:
    """Read a flag. A missing flag takes ``default``; text reads true or false."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "1"):
            return True
        if lowered in ("false", "no", "0", ""):
            return False
    return default


def as_datetime(value: Any) -> datetime | None:
    """Read an ISO date or timestamp as an aware datetime, or ``None``.

    Returns ``None`` rather than raising for a malformed value, because one
    unreadable event timestamp must not stop a deal from scoring. An event with
    an unreadable timestamp simply does not count.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def strip_reserved(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """A payload without the keys the store owns.

    The envelope is the only fixed vocabulary the product allows, so a caller
    may not set it through a `data` payload and expect it to stick.
    """
    return {key: value for key, value in dict(payload or {}).items() if key not in RESERVED_KEYS}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def validate_deal(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalise a deal, or raise with a message for every bad field.

    A field this workflow has never heard of is kept. Teams add fields without a
    migration or a typed column, so validation normalises the fields it knows
    and passes the rest through untouched.
    """
    data = strip_reserved(payload)
    errors: dict[str, str] = {}

    name = str(data.get("name") or "").strip()
    if not name:
        errors["name"] = "Name the deal. A score belongs to a named deal."

    framework = str(data.get("framework") or DEFAULT_FRAMEWORK).strip().lower()
    if framework not in FRAMEWORKS:
        errors["framework"] = "framework must be one of " + ", ".join(FRAMEWORKS) + "."

    amount = data.get("amount")
    if amount is not None and read_float(amount, None) is None:
        errors["amount"] = "amount must be a number."
    if amount is not None and str(amount).strip() == "":
        amount = None

    if errors:
        raise DealHealthError("The deal could not be read.", errors)

    normalised = dict(data)
    normalised["name"] = name
    normalised["framework"] = framework
    for field in ("account", "stage", "rep", "currency"):
        if normalised.get(field) is not None:
            normalised[field] = str(normalised[field])
    if amount is not None:
        normalised["amount"] = read_float(amount)
    return normalised


def validate_event(
    payload: Mapping[str, Any] | None, *, at_default: datetime | None = None
) -> dict[str, Any]:
    """Normalise one engagement event, or raise with a message per bad field.

    ``at`` defaults to the engine's clock, so an event recorded now is placed at
    now rather than at an unset time.
    """
    data = strip_reserved(payload)
    errors: dict[str, str] = {}

    kind = str(data.get("kind") or "").strip().lower()
    if kind not in EVENT_KINDS:
        errors["kind"] = "kind must be one of " + ", ".join(EVENT_KINDS) + "."

    person = str(data.get("person") or "").strip()
    if not person:
        errors["person"] = "Name the person the engagement came from."

    at = data.get("at")
    if at in (None, ""):
        at = at_default.isoformat() if at_default is not None else None
    elif as_datetime(at) is None:
        errors["at"] = "at must be an ISO 8601 date or timestamp."

    seconds = read_int(data.get("seconds"), 0)
    if seconds is None or seconds < 0:
        errors["seconds"] = "seconds must be a whole number of zero or more."
        seconds = 0

    if errors:
        raise DealHealthError("The engagement event could not be read.", errors)

    normalised = dict(data)
    normalised["kind"] = kind
    normalised["person"] = person
    normalised["seconds"] = seconds
    if at:
        normalised["at"] = str(at)
    if normalised.get("asset") is not None:
        normalised["asset"] = str(normalised["asset"])
    return normalised


def validate_stakeholder(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalise one buying-group member, or raise with a message per bad field."""
    data = strip_reserved(payload)
    errors: dict[str, str] = {}

    name = str(data.get("name") or "").strip()
    if not name:
        errors["name"] = "Name the stakeholder. Coverage counts named people."

    engaged_at = data.get("engaged_at")
    if engaged_at not in (None, "") and as_datetime(engaged_at) is None:
        errors["engaged_at"] = "engaged_at must be an ISO 8601 date or timestamp."

    count = read_int(data.get("engagement_count"), 0)
    if count is None or count < 0:
        errors["engagement_count"] = "engagement_count must be zero or more."
        count = 0

    if errors:
        raise DealHealthError("The stakeholder could not be read.", errors)

    normalised = dict(data)
    normalised["name"] = name
    normalised["engagement_count"] = count
    for field in ("role", "email", "influence"):
        if normalised.get(field) is not None:
            normalised[field] = str(normalised[field])
    if engaged_at not in (None, ""):
        normalised["engaged_at"] = str(engaged_at)
    return normalised


def validate_content(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalise one key asset, or raise with a message per bad field."""
    data = strip_reserved(payload)
    errors: dict[str, str] = {}

    name = str(data.get("name") or "").strip()
    if not name:
        errors["name"] = "Name the key material."

    kind = str(data.get("kind") or "document").strip().lower()

    if errors:
        raise DealHealthError("The key material could not be read.", errors)

    normalised = dict(data)
    normalised["name"] = name
    normalised["kind"] = kind
    normalised["required"] = read_bool(data.get("required"), True)
    if data.get("reviewed") is not None:
        normalised["reviewed"] = read_bool(data.get("reviewed"), False)
    if normalised.get("url") is not None:
        normalised["url"] = str(normalised["url"])
    return normalised


# --------------------------------------------------------------------------- #
# The three factors
# --------------------------------------------------------------------------- #


def event_weight(kind: Any) -> float:
    """What one event of ``kind`` counts for. An unknown kind counts least."""
    return EVENT_WEIGHTS.get(str(kind or "").strip().lower(), UNKNOWN_EVENT_WEIGHT)


def engagement_factor(
    events: Iterable[Mapping[str, Any]],
    now: datetime,
    *,
    window_days: int = DECAY_WINDOW_DAYS,
    target_weight: float = TARGET_EVENT_WEIGHT,
) -> dict[str, Any]:
    """How much live engagement the room shows, from zero to one.

    Recency is the share of the window left since the newest event. Volume is
    the weighted count inside the window against the target. An event with an
    unreadable timestamp does not count, so one bad row cannot inflate the
    score.

    The result carries its own working, so a card can explain the number rather
    than assert it.
    """
    dated: list[tuple[datetime, Mapping[str, Any]]] = []
    for event in events:
        moment = as_datetime(event.get("at"))
        if moment is not None:
            dated.append((moment, event))

    if not dated:
        return {
            "factor": 0.0,
            "events": 0,
            "events_in_window": 0,
            "weighted": 0.0,
            "recency": 0.0,
            "volume": 0.0,
            "days_since_last": None,
            "last_at": None,
            "window_days": window_days,
        }

    last = max(moment for moment, _ in dated)
    days_since_last = max(0.0, (now - last).total_seconds() / 86400.0)

    in_window = [
        (moment, event)
        for moment, event in dated
        if max(0.0, (now - moment).total_seconds() / 86400.0) <= window_days
    ]
    weighted = sum(event_weight(event.get("kind")) for _, event in in_window)

    recency = max(0.0, min(1.0, 1.0 - days_since_last / window_days))
    volume = min(weighted / target_weight, 1.0) if target_weight > 0 else 0.0
    factor = round(RECENCY_SHARE * recency + VOLUME_SHARE * volume, 4)

    return {
        "factor": factor,
        "events": len(dated),
        "events_in_window": len(in_window),
        "weighted": round(weighted, 4),
        "recency": round(recency, 4),
        "volume": round(volume, 4),
        "days_since_last": round(days_since_last, 4),
        "last_at": last.isoformat(),
        "window_days": window_days,
    }


def content_factor(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The share of required key material that was reviewed, from zero to one.

    Only required material counts. An optional asset nobody opened is not a
    risk. A deal with no required material owes nothing, so its factor is one.
    """
    required = [item for item in items if read_bool(item.get("required"), True)]
    unreviewed = [str(item.get("name")) for item in required if not item.get("reviewed_at")]
    reviewed = len(required) - len(unreviewed)
    factor = 1.0 if not required else round(reviewed / len(required), 4)
    return {
        "factor": factor,
        "required": len(required),
        "reviewed": reviewed,
        "unreviewed": unreviewed,
    }


def is_engaged(stakeholder: Mapping[str, Any]) -> bool:
    """Did this member of the buying group engage at all?"""
    if stakeholder.get("engaged_at"):
        return True
    return (read_int(stakeholder.get("engagement_count"), 0) or 0) > 0


def coverage_factor(
    stakeholders: Iterable[Mapping[str, Any]], *, target: float = COVERAGE_TARGET
) -> dict[str, Any]:
    """How much of the buying group engaged, from zero to one.

    Coverage is the engaged share divided by the target, capped at one. A deal
    with no stakeholder named has no coverage at all, which is why the empty
    case scores zero rather than one: a buying group nobody recorded is the
    "missing stakeholders" risk, not a pass.
    """
    people = list(stakeholders)
    engaged = [person for person in people if is_engaged(person)]
    total = len(people)
    coverage = round(len(engaged) / total, 4) if total else 0.0
    factor = 0.0 if not total or target <= 0 else round(min(coverage / target, 1.0), 4)
    return {
        "factor": factor,
        "total": total,
        "engaged": len(engaged),
        "coverage": coverage,
        "target": target,
        "not_engaged": [str(person.get("name")) for person in people if not is_engaged(person)],
    }


def band_for(value: Any) -> str:
    """The band a score falls in.

    A value this workflow cannot read is at risk, not healthy. The fail-safe
    direction matters: an unreadable score must not silence an alert.
    """
    score = read_int(value, None)
    if score is None:
        return BAND_AT_RISK
    if score >= HEALTHY_FLOOR:
        return BAND_HEALTHY
    if score >= WATCH_FLOOR:
        return BAND_WATCH
    return BAND_AT_RISK


def reason_sentence(reason: str) -> str:
    """The sentence for one risk reason, or a neutral fallback."""
    return REASON_SENTENCES.get(reason, "This deal needs attention.")


def score_deal(
    deal: Mapping[str, Any],
    events: Iterable[Mapping[str, Any]],
    stakeholders: Iterable[Mapping[str, Any]],
    content: Iterable[Mapping[str, Any]],
    now: datetime,
    *,
    previous: Any = None,
) -> dict[str, Any]:
    """Score one deal from its evidence, and explain the score.

    The result is plain JSON, so it is stored as a score record without a
    translation step. It carries the three factors, their weights and the
    reasons, because a score with no reasons cannot be coached against.
    """
    engagement = engagement_factor(events, now)
    material = content_factor(content)
    coverage = coverage_factor(stakeholders)

    value = int(
        round(
            100
            * (
                WEIGHTS["engagement"] * engagement["factor"]
                + WEIGHTS["content"] * material["factor"]
                + WEIGHTS["stakeholder"] * coverage["factor"]
            )
        )
    )
    band = band_for(value)
    prior = read_int(previous, None)
    delta = None if prior is None else value - prior

    reasons: list[str] = []
    if engagement["events"] == 0:
        reasons.append(REASON_NO_ENGAGEMENT)
    elif engagement["factor"] < ENGAGEMENT_FLOOR:
        reasons.append(REASON_ENGAGEMENT_DECAY)
    if material["required"] and material["unreviewed"]:
        reasons.append(REASON_UNREVIEWED_KEY_MATERIAL)
    if coverage["total"] == 0 or coverage["coverage"] < COVERAGE_TARGET:
        reasons.append(REASON_MISSING_STAKEHOLDERS)
    if band == BAND_AT_RISK:
        reasons.append(REASON_BELOW_TARGET)
    if delta is not None and delta <= -DROP_THRESHOLD:
        reasons.append(REASON_SCORE_DROP)

    return {
        "value": value,
        "band": band,
        "factors": {
            "engagement": engagement,
            "content": material,
            "stakeholder": coverage,
        },
        "weights": dict(WEIGHTS),
        "reasons": reasons,
        "reason_sentences": [reason_sentence(reason) for reason in reasons],
        "previous": prior,
        "delta": delta,
        "framework": str(deal.get("framework") or DEFAULT_FRAMEWORK),
        "measured_at": now.isoformat(),
    }


def alert_candidates(score: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The alerts one score justifies, before any dedupe against open alerts.

    There are two: the band is at risk, and the score fell by the threshold.
    """
    out: list[dict[str, Any]] = []
    if score.get("band") == BAND_AT_RISK:
        out.append(
            {
                "kind": ALERT_BAND,
                "reason": REASON_BELOW_TARGET,
                "severity": "high",
                "detail": (f"Deal health is {score.get('value')} and the band is at risk."),
            }
        )
    if REASON_SCORE_DROP in (score.get("reasons") or []):
        fell = abs(read_int(score.get("delta"), 0) or 0)
        out.append(
            {
                "kind": ALERT_DROP,
                "reason": REASON_SCORE_DROP,
                "severity": "medium",
                "detail": f"Deal health fell {fell} points to {score.get('value')}.",
            }
        )
    return out


def coaching_cues(score: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One cue per active risk reason, in the score's own order.

    Rule-based and deterministic: no model, no transcript. See
    DECISIONS["coaching_scope"] for why.
    """
    return [
        {"reason": reason, "action": COACHING_ACTIONS[reason]}
        for reason in (score.get("reasons") or [])
        if reason in COACHING_ACTIONS
    ]


def readiness(outcomes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The closed loop's readiness data from recorded coaching outcomes.

    ``readiness`` is the actioned share. ``participation`` is the share that was
    acknowledged or actioned, so a dismissed cue counts against participation.
    An empty list reports ``None`` rather than zero: no coachable cue is not the
    same as a rep who ignored every cue.
    """
    rows = list(outcomes)
    total = len(rows)
    actioned = sum(1 for row in rows if row.get("state") == OUTCOME_ACTIONED)
    acknowledged = sum(1 for row in rows if row.get("state") == OUTCOME_ACKNOWLEDGED)
    dismissed = sum(1 for row in rows if row.get("state") == OUTCOME_DISMISSED)
    return {
        "outcomes": total,
        "actioned": actioned,
        "acknowledged": acknowledged,
        "dismissed": dismissed,
        "readiness": round(actioned / total, 4) if total else None,
        "participation": round((actioned + acknowledged) / total, 4) if total else None,
    }


# --------------------------------------------------------------------------- #
# Presenters. The store returns whole records; these flatten the payload.
# --------------------------------------------------------------------------- #


def data_of(record: Mapping[str, Any]) -> dict[str, Any]:
    """The payload of a record, as a fresh mutable dict."""
    return dict(record.get("data") or {})


def room_of(record: Mapping[str, Any]) -> Any:
    """The room a record belongs to, from the envelope or the payload."""
    return record.get("room_id") or data_of(record).get(ROOM_REF)


def deal_of(record: Mapping[str, Any]) -> Any:
    """The deal a record belongs to, from the payload."""
    return data_of(record).get(DEAL_REF)


def present_deal(record: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": record["id"], "room_id": room_of(record), **data_of(record)}


def present_event(record: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": record["id"], "deal_id": deal_of(record), **data_of(record)}


def present_stakeholder(record: Mapping[str, Any]) -> dict[str, Any]:
    data = data_of(record)
    return {
        "id": record["id"],
        "deal_id": deal_of(record),
        **data,
        "engaged": is_engaged(data),
    }


def present_content(record: Mapping[str, Any]) -> dict[str, Any]:
    data = data_of(record)
    return {
        "id": record["id"],
        "deal_id": deal_of(record),
        **data,
        "required": read_bool(data.get("required"), True),
        "reviewed": bool(data.get("reviewed_at")),
    }


def present_score(record: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": record["id"], "deal_id": deal_of(record), **data_of(record)}


def present_alert(record: Mapping[str, Any]) -> dict[str, Any]:
    data = data_of(record)
    return {
        "id": record["id"],
        "deal_id": deal_of(record),
        **data,
        "open": not data.get("resolved_at"),
    }


def present_cue(record: Mapping[str, Any]) -> dict[str, Any]:
    data = data_of(record)
    return {
        "id": record["id"],
        "deal_id": deal_of(record),
        **data,
        "open": str(data.get("state") or CUE_OPEN) == CUE_OPEN,
    }


def present_outcome(record: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": record["id"], "deal_id": deal_of(record), **data_of(record)}


# --------------------------------------------------------------------------- #
# The engine. The only place a rule meets stored state.
# --------------------------------------------------------------------------- #


class DealHealthEngine:
    """Reads and writes deals, their evidence, their score and their coaching.

    ``now`` is injectable so a test can pin the clock. The production route
    leaves it at the real clock, because a caller that could pass its own ``now``
    could make a decayed deal look live.

    Every write takes a ``source`` argument and it has no default. A write
    cannot be reached without naming the route that served it, which is what
    keeps the audit log honest.
    """

    def __init__(self, store: RecordStore, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self._now = now or _utcnow

    # -- reads of records --------------------------------------------------- #

    def _require(self, collection: str, record_id: str, label: str) -> dict[str, Any]:
        record = self.store.get(record_id)
        if record is None or record.get("collection") != collection:
            raise DealHealthNotFound(f"No such {label}: {record_id}")
        return record

    def _scoped(
        self,
        collection: str,
        *,
        deal_id: str | None = None,
        room_id: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if deal_id:
            where[DEAL_REF] = deal_id
        if room_id:
            where[ROOM_REF] = room_id
        rows = (
            self.store.find(collection, where, limit=limit)
            if where
            else self.store.list(collection, limit=limit)
        )
        # The order is explicit and total: `created_at` alone is not a total
        # order, because one call raises several cues or writes several events
        # inside the same microsecond. Without the id as a tiebreaker, two
        # identical requests could return the same rows in different orders, and
        # a caller that paired two responses would pair the wrong rows. The id is
        # arbitrary but fixed per run, which is all determinism needs.
        return sorted(
            rows,
            key=lambda row: (str(row.get("created_at") or ""), str(row.get("id") or "")),
        )

    def _verify_visible(
        self, collection: str, record_id: str, label: str, expected: Mapping[str, Any]
    ) -> None:
        """Read a written record back, and refuse if the write did not land."""
        fresh = self.store.get(record_id)
        if fresh is None or fresh.get("collection") != collection:
            raise DealHealthRefused(DealHealthRefused.REASON_WRITE_NOT_VISIBLE)
        data = data_of(fresh)
        for key, value in expected.items():
            if data.get(key) != value:
                raise DealHealthRefused(
                    DealHealthRefused.REASON_WRITE_NOT_VISIBLE,
                    f"The {label} was written but {key} did not read back.",
                )

    # -- deals -------------------------------------------------------------- #

    def create_deal(
        self, room_id: str, payload: Mapping[str, Any] | None, *, source: str, actor: str
    ) -> dict[str, Any]:
        """Add a deal to a room. The score follows from the evidence."""
        if not str(room_id or "").strip():
            raise DealHealthError("A deal belongs to a room.", {"room_id": "Name the room."})
        normalised = validate_deal(payload)
        data = {**normalised, ROOM_REF: room_id}
        record = self.store.create(
            DEAL_COLLECTION, data, room_id=room_id, source=source, actor=actor
        )
        self._verify_visible(DEAL_COLLECTION, record["id"], "deal", {"name": normalised["name"]})
        return present_deal(record)

    def list_deals(self, room_id: str | None = None) -> list[dict[str, Any]]:
        return [present_deal(row) for row in self._scoped(DEAL_COLLECTION, room_id=room_id)]

    def read_deal(self, deal_id: str) -> dict[str, Any]:
        return present_deal(self._require(DEAL_COLLECTION, deal_id, "deal"))

    def update_deal(
        self, deal_id: str, patch: Mapping[str, Any] | None, *, source: str, actor: str
    ) -> dict[str, Any]:
        """Edit a deal's name, framework, stage, amount or rep."""
        record = self._require(DEAL_COLLECTION, deal_id, "deal")
        merged = {**data_of(record), **strip_reserved(patch)}
        normalised = validate_deal(merged)
        normalised[ROOM_REF] = room_of(record)
        self.store.update(deal_id, normalised, source=source, actor=actor)
        self._verify_visible(DEAL_COLLECTION, deal_id, "deal", {"name": normalised["name"]})
        return self.read_deal(deal_id)

    # -- engagement events -------------------------------------------------- #

    def record_event(
        self, deal_id: str, payload: Mapping[str, Any] | None, *, source: str, actor: str
    ) -> dict[str, Any]:
        """Record one engagement event, then score the deal again.

        Scoring on every event is the automation the research names. The score
        and any alert it raises come back with the event, so one call leaves the
        deal consistent.
        """
        deal = self._require(DEAL_COLLECTION, deal_id, "deal")
        normalised = validate_event(payload, at_default=self._now())
        data = {**normalised, DEAL_REF: deal_id, ROOM_REF: room_of(deal)}
        record = self.store.create(
            EVENT_COLLECTION, data, room_id=room_of(deal), source=source, actor=actor
        )
        self._verify_visible(EVENT_COLLECTION, record["id"], "event", {"kind": normalised["kind"]})
        scored = self.score(deal_id, source=source, actor=actor)
        return {
            "event": present_event(record),
            "score": scored["score"],
            "alerts": scored["alerts"],
            "coaching": scored["coaching"],
        }

    def list_events(self, deal_id: str) -> list[dict[str, Any]]:
        self._require(DEAL_COLLECTION, deal_id, "deal")
        return [present_event(row) for row in self._scoped(EVENT_COLLECTION, deal_id=deal_id)]

    # -- the buying group --------------------------------------------------- #

    def add_stakeholder(
        self, deal_id: str, payload: Mapping[str, Any] | None, *, source: str, actor: str
    ) -> dict[str, Any]:
        """Add one member of the buying group, engaged or not."""
        deal = self._require(DEAL_COLLECTION, deal_id, "deal")
        normalised = validate_stakeholder(payload)
        data = {**normalised, DEAL_REF: deal_id, ROOM_REF: room_of(deal)}
        record = self.store.create(
            STAKEHOLDER_COLLECTION, data, room_id=room_of(deal), source=source, actor=actor
        )
        self._verify_visible(
            STAKEHOLDER_COLLECTION, record["id"], "stakeholder", {"name": normalised["name"]}
        )
        return present_stakeholder(record)

    def list_stakeholders(self, deal_id: str) -> list[dict[str, Any]]:
        self._require(DEAL_COLLECTION, deal_id, "deal")
        return [
            present_stakeholder(row)
            for row in self._scoped(STAKEHOLDER_COLLECTION, deal_id=deal_id)
        ]

    def update_stakeholder(
        self,
        deal_id: str,
        contact_id: str,
        patch: Mapping[str, Any] | None,
        *,
        source: str,
        actor: str,
    ) -> dict[str, Any]:
        """Edit a member: their role, their email, or that they engaged."""
        self._require(DEAL_COLLECTION, deal_id, "deal")
        record = self._require(STAKEHOLDER_COLLECTION, contact_id, "stakeholder")
        if deal_of(record) != deal_id:
            raise DealHealthNotFound("No such stakeholder on this deal.")
        merged = {**data_of(record), **strip_reserved(patch)}
        normalised = validate_stakeholder(merged)
        normalised[DEAL_REF] = deal_id
        normalised[ROOM_REF] = room_of(record)
        self.store.update(contact_id, normalised, source=source, actor=actor)
        self._verify_visible(
            STAKEHOLDER_COLLECTION, contact_id, "stakeholder", {"name": normalised["name"]}
        )
        return present_stakeholder(self._require(STAKEHOLDER_COLLECTION, contact_id, "stakeholder"))

    def coverage_map(self, deal_id: str) -> dict[str, Any]:
        """Who engaged and who has not: the stakeholder coverage map."""
        deal = self.read_deal(deal_id)
        people = [data_of(row) for row in self._scoped(STAKEHOLDER_COLLECTION, deal_id=deal_id)]
        rows = [
            present_stakeholder(row)
            for row in self._scoped(STAKEHOLDER_COLLECTION, deal_id=deal_id)
        ]
        factor = coverage_factor(people)
        return {
            "deal_id": deal_id,
            "framework": deal.get("framework"),
            "coverage": factor["coverage"],
            "target": factor["target"],
            "engaged_count": factor["engaged"],
            "total": factor["total"],
            "met": factor["coverage"] >= factor["target"],
            "engaged": [row for row in rows if row["engaged"]],
            "not_engaged": [row for row in rows if not row["engaged"]],
        }

    # -- key material ------------------------------------------------------- #

    def add_content(
        self, deal_id: str, payload: Mapping[str, Any] | None, *, source: str, actor: str
    ) -> dict[str, Any]:
        """Add one key asset, required or optional, and whether it was reviewed."""
        deal = self._require(DEAL_COLLECTION, deal_id, "deal")
        normalised = validate_content(payload)
        now = self._now()
        if read_bool(normalised.get("reviewed"), False):
            normalised.setdefault("reviewed_at", now.isoformat())
        normalised.pop("reviewed", None)
        data = {**normalised, DEAL_REF: deal_id, ROOM_REF: room_of(deal)}
        record = self.store.create(
            CONTENT_COLLECTION, data, room_id=room_of(deal), source=source, actor=actor
        )
        self._verify_visible(
            CONTENT_COLLECTION, record["id"], "key material", {"name": normalised["name"]}
        )
        return present_content(record)

    def list_content(self, deal_id: str) -> list[dict[str, Any]]:
        self._require(DEAL_COLLECTION, deal_id, "deal")
        return [present_content(row) for row in self._scoped(CONTENT_COLLECTION, deal_id=deal_id)]

    def review_content(
        self,
        deal_id: str,
        asset_id: str,
        *,
        reviewed: bool = True,
        source: str,
        actor: str,
    ) -> dict[str, Any]:
        """Mark one asset reviewed, or unreviewed, and score the deal again."""
        self._require(DEAL_COLLECTION, deal_id, "deal")
        record = self._require(CONTENT_COLLECTION, asset_id, "key material")
        if deal_of(record) != deal_id:
            raise DealHealthNotFound("No such key material on this deal.")
        data = dict(data_of(record))
        # The store's update is a merge patch, so a key cannot be removed, only
        # set. Unreviewing therefore writes an explicit null rather than deleting
        # the timestamp, and every reader treats a falsy timestamp as unreviewed.
        data["reviewed_at"] = self._now().isoformat() if reviewed else None
        self.store.update(asset_id, data, source=source, actor=actor)
        self._verify_visible(
            CONTENT_COLLECTION, asset_id, "key material", {"reviewed_at": data["reviewed_at"]}
        )
        self.score(deal_id, source=source, actor=actor)
        return present_content(self._require(CONTENT_COLLECTION, asset_id, "key material"))

    # -- scoring ------------------------------------------------------------ #

    def _evidence(self, deal_id: str) -> tuple[dict[str, Any], list[dict], list[dict], list[dict]]:
        deal = self._require(DEAL_COLLECTION, deal_id, "deal")
        events = [data_of(row) for row in self._scoped(EVENT_COLLECTION, deal_id=deal_id)]
        people = [data_of(row) for row in self._scoped(STAKEHOLDER_COLLECTION, deal_id=deal_id)]
        material = [data_of(row) for row in self._scoped(CONTENT_COLLECTION, deal_id=deal_id)]
        return deal, events, people, material

    def latest_score_record(self, deal_id: str) -> dict[str, Any] | None:
        """The newest recorded score, or ``None`` when none was recorded yet."""
        rows = self._scoped(SCORE_COLLECTION, deal_id=deal_id)
        return rows[-1] if rows else None

    def score_card(self, deal_id: str) -> dict[str, Any]:
        """A live reading of the deal, with the last recorded one beside it.

        The card is computed from the evidence every time, because the research
        describes a platform that scores continuously: a stored reading would go
        stale the moment an asset was added or a stakeholder engaged. The last
        recorded reading travels with it under ``last_recorded``, and the whole
        time series is served by :meth:`history`.

        A read never writes, so this leaves no audit row and does not move the
        drop baseline.
        """
        record = self.latest_score_record(deal_id)
        deal, events, people, material = self._evidence(deal_id)
        computed = score_deal(
            {**data_of(deal), "id": deal_id},
            events,
            people,
            material,
            self._now(),
            previous=data_of(record).get("value") if record else None,
        )
        return {
            "recorded": record is not None,
            "score": computed,
            "last_recorded": present_score(record) if record is not None else None,
        }

    def history(self, deal_id: str) -> list[dict[str, Any]]:
        self._require(DEAL_COLLECTION, deal_id, "deal")
        return [present_score(row) for row in self._scoped(SCORE_COLLECTION, deal_id=deal_id)]

    def score(self, deal_id: str, *, source: str, actor: str) -> dict[str, Any]:
        """Score a deal, record the reading, and raise what the score justifies.

        This is the whole automation in one place: score, alert on risk, and
        generate the rep's next steps. It writes the score, then dedupes the
        alerts and the cues against the ones already open for the deal, so a
        deal that stays at risk does not grow a new alert on every event.
        """
        deal, events, people, material = self._evidence(deal_id)
        previous_record = self.latest_score_record(deal_id)
        previous = data_of(previous_record).get("value") if previous_record else None

        computed = score_deal(
            {**data_of(deal), "id": deal_id},
            events,
            people,
            material,
            self._now(),
            previous=previous,
        )
        data = {**computed, DEAL_REF: deal_id, ROOM_REF: room_of(deal)}
        stored = self.store.create(
            SCORE_COLLECTION, data, room_id=room_of(deal), source=source, actor=actor
        )
        score_row = present_score(stored)
        alerts = self._raise_alerts(deal, score_row, source=source, actor=actor)
        cues = self._raise_cues(deal, score_row, source=source, actor=actor)
        return {"score": score_row, "alerts": alerts, "coaching": cues}

    def _alert_records(self, deal_id: str) -> list[dict[str, Any]]:
        return self._scoped(ALERT_COLLECTION, deal_id=deal_id)

    def _raise_alerts(
        self, deal: Mapping[str, Any], score: Mapping[str, Any], *, source: str, actor: str
    ) -> list[dict[str, Any]]:
        deal_id = str(deal["id"])
        open_kinds = {
            str(data_of(row).get("kind"))
            for row in self._alert_records(deal_id)
            if not data_of(row).get("resolved_at")
        }
        raised: list[dict[str, Any]] = []
        for candidate in alert_candidates(score):
            if candidate["kind"] in open_kinds:
                continue
            data = {
                **candidate,
                "value": score.get("value"),
                "band": score.get("band"),
                "score_id": score.get("id"),
                "raised_at": self._now().isoformat(),
                DEAL_REF: deal_id,
                ROOM_REF: room_of(deal),
            }
            record = self.store.create(
                ALERT_COLLECTION, data, room_id=room_of(deal), source=source, actor=actor
            )
            raised.append(present_alert(record))
        return raised

    def list_alerts(self, deal_id: str) -> list[dict[str, Any]]:
        self._require(DEAL_COLLECTION, deal_id, "deal")
        return [present_alert(row) for row in self._alert_records(deal_id)]

    def resolve_alert(self, alert_id: str, *, source: str, actor: str) -> dict[str, Any]:
        """Close one alert, so a settled risk stops being reported as open."""
        record = self._require(ALERT_COLLECTION, alert_id, "risk alert")
        data = {**data_of(record), "resolved_at": self._now().isoformat()}
        self.store.update(alert_id, data, source=source, actor=actor)
        self._verify_visible(
            ALERT_COLLECTION, alert_id, "risk alert", {"resolved_at": data["resolved_at"]}
        )
        return present_alert(self._require(ALERT_COLLECTION, alert_id, "risk alert"))

    # -- coaching and the closed loop --------------------------------------- #

    def _raise_cues(
        self, deal: Mapping[str, Any], score: Mapping[str, Any], *, source: str, actor: str
    ) -> list[dict[str, Any]]:
        deal_id = str(deal["id"])
        open_reasons = {
            str(data_of(row).get("reason"))
            for row in self._scoped(COACHING_COLLECTION, deal_id=deal_id)
            if str(data_of(row).get("state") or CUE_OPEN) == CUE_OPEN
        }
        raised: list[dict[str, Any]] = []
        for cue in coaching_cues(score):
            if cue["reason"] in open_reasons:
                continue
            data = {
                **cue,
                "state": CUE_OPEN,
                "severity": "high" if score.get("band") == BAND_AT_RISK else "medium",
                "score_id": score.get("id"),
                "value": score.get("value"),
                "rep": str(data_of(deal).get("rep") or ""),
                "raised_at": self._now().isoformat(),
                DEAL_REF: deal_id,
                ROOM_REF: room_of(deal),
            }
            record = self.store.create(
                COACHING_COLLECTION, data, room_id=room_of(deal), source=source, actor=actor
            )
            raised.append(present_cue(record))
        return raised

    def list_coaching(self, deal_id: str, *, open_only: bool = False) -> list[dict[str, Any]]:
        self._require(DEAL_COLLECTION, deal_id, "deal")
        rows = [present_cue(row) for row in self._scoped(COACHING_COLLECTION, deal_id=deal_id)]
        return [row for row in rows if row["open"]] if open_only else rows

    def record_outcome(
        self, cue_id: str, payload: Mapping[str, Any] | None, *, source: str, actor: str
    ) -> dict[str, Any]:
        """Close one coaching cue with an outcome, and write the readiness row.

        This is the loop the research calls closed: the coaching feeds back into
        readiness data. A cue that already has an outcome is refused rather than
        rewritten, because history is a record and not a state.
        """
        cue = self._require(COACHING_COLLECTION, cue_id, "coaching cue")
        if str(data_of(cue).get("state") or CUE_OPEN) != CUE_OPEN:
            raise DealHealthRefused(DealHealthRefused.REASON_ALREADY_CLOSED)
        payload = dict(payload or {})
        state = str(payload.get("state") or "").strip().lower()
        if state not in OUTCOME_STATES:
            raise DealHealthError(
                "The coaching outcome could not be read.",
                {"state": "state must be one of " + ", ".join(OUTCOME_STATES) + "."},
            )
        now = self._now()
        closed = {**data_of(cue), "state": state, "closed_at": now.isoformat()}
        self.store.update(cue_id, closed, source=source, actor=actor)
        self._verify_visible(COACHING_COLLECTION, cue_id, "coaching cue", {"state": state})

        outcome_data = {
            "cue_ref": cue_id,
            "state": state,
            "reason": str(data_of(cue).get("reason") or ""),
            "rep": str(data_of(cue).get("rep") or "") or str(payload.get("rep") or ""),
            "note": str(payload.get("note") or ""),
            "recorded_at": now.isoformat(),
            DEAL_REF: deal_of(cue),
            ROOM_REF: room_of(cue),
        }
        outcome = self.store.create(
            OUTCOME_COLLECTION,
            outcome_data,
            room_id=room_of(cue),
            source=source,
            actor=actor,
        )
        return {
            "cue": present_cue(self._require(COACHING_COLLECTION, cue_id, "coaching cue")),
            "outcome": present_outcome(outcome),
        }

    def readiness(self, rep: str | None = None) -> dict[str, Any]:
        """The readiness index over recorded coaching outcomes, by rep."""
        rows = [data_of(row) for row in self._scoped(OUTCOME_COLLECTION)]
        if rep:
            rows = [row for row in rows if str(row.get("rep") or "") == str(rep)]
        return {"rep": rep, **readiness(rows)}

    # -- the board ---------------------------------------------------------- #

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        """The headline numbers: how many deals sit in each band."""
        deals = self._scoped(DEAL_COLLECTION, room_id=room_id)
        bands = {BAND_HEALTHY: 0, BAND_WATCH: 0, BAND_AT_RISK: 0}
        scored = 0
        for deal in deals:
            deal_id = str(deal["id"])
            record = self.latest_score_record(deal_id)
            if record is None:
                continue
            scored += 1
            bands[band_for(data_of(record).get("value"))] += 1

        open_alerts = 0
        open_cues = 0
        for deal in deals:
            deal_id = str(deal["id"])
            open_alerts += sum(
                1 for row in self._alert_records(deal_id) if not data_of(row).get("resolved_at")
            )
            open_cues += sum(
                1
                for row in self._scoped(COACHING_COLLECTION, deal_id=deal_id)
                if str(data_of(row).get("state") or CUE_OPEN) == CUE_OPEN
            )
        outcomes = self.readiness()
        return {
            "room_id": room_id,
            "deals": len(deals),
            "scored": scored,
            "healthy": bands[BAND_HEALTHY],
            "watch": bands[BAND_WATCH],
            "at_risk": bands[BAND_AT_RISK],
            "open_alerts": open_alerts,
            "open_coaching_cues": open_cues,
            "coaching_outcomes": outcomes["outcomes"],
            "readiness": outcomes["readiness"],
            "decay_window_days": DECAY_WINDOW_DAYS,
            "coverage_target": COVERAGE_TARGET,
            "weight_percent": {key: int(round(value * 100)) for key, value in WEIGHTS.items()},
            "generated_at": self._now().isoformat(),
        }


# --------------------------------------------------------------------------- #
# The decisions the issue left open, each with the alternative rejected, and
# what this build deliberately does not do.
# --------------------------------------------------------------------------- #

DECISIONS: dict[str, str] = {
    "framework": (
        "All three frameworks carry: MEDDICC, BANT and Value Selling. MEDDICC is "
        "the default. The score itself is framework-neutral, because the three "
        "evidence factors are the same whichever framework the rep qualified "
        "against, and the chosen framework is stored on the deal and shown beside "
        "the score. The alternative, one framework only, was rejected because the "
        "issue lists three and the research names each as a built-in."
    ),
    "score_formula": (
        "The score is a weighted sum of three factors, each from zero to one, "
        "times one hundred. Engagement is 50 percent, key material 30 percent, "
        "stakeholder coverage 20 percent. Engagement splits 60 percent recency "
        "and 40 percent weighted volume. Bands are healthy at 70, watch at 45, at "
        "risk below 45. The issue states the shape and no formula, so this "
        "derivation is recorded rather than hidden."
    ),
    "decay_window": (
        "The decay window is 21 days, the target weighted volume is 6 events, and "
        "the coverage target is 60 percent of the buying group. The issue names "
        "engagement decay, unreviewed key material and missing stakeholders but "
        "gives no numbers. The window matches the seeded demo's own 21-day "
        "activity window, the volume target is roughly one asset, one dwell and "
        "one meeting, and the coverage target is a simple majority."
    ),
    "event_weights": (
        "Each engagement event carries a weight: a view counts 0.5, a dwell, a "
        "download or a share counts 1, a comment or a question counts 1.5, and a "
        "meeting counts 2. The research lists the events and gives no weights, so "
        "the ordering follows the buyer's time: a meeting is worth four views."
    ),
    "coaching_scope": (
        "Coaching is in scope as deterministic, rule-based cues. Each active risk "
        "reason maps to one written action, so a cue is reproducible and "
        "reviewable and needs no model, no transcript and no recording. The "
        "alternative, a language model over a call transcript, was rejected: the "
        "research itself states no open-source project ships a coaching loop and "
        "says coaching would need a model and a transcription pipeline this "
        "repository does not have."
    ),
    "live_coaching": (
        "Live coaching during a call is out of scope. The research hedges it with "
        "'in some platforms', and it needs the telephony, the recording and the "
        "transcription pipeline above. The rep gets the cue after the engagement "
        "event is recorded, not during the call."
    ),
    "event_intake": (
        "This workflow owns its own engagement intake: room engagement events are "
        "written through POST /api/wf-134/deals/{deal_id}/events, and the score is "
        "recorded with them. No other workflow in the shard packet provisions "
        "these events, so a score cannot be computed from data that does not "
        "exist. The alternative, reading another feature's collection, was "
        "rejected because no such collection is declared."
    ),
    "automations": (
        "Built: auto-score on every recorded event, auto-alert on risk, and "
        "auto-generate the rep's next steps as coaching cues. Not built: "
        "auto-document calls, one-time data entry fanned out to the CRM, and "
        "auto-flagging revenue risk to leadership. The first two need a "
        "telephony and a CRM connection this repository does not own, and the "
        "third needs a notification channel it does not have."
    ),
    "vendor_claims": (
        "The page repeats no vendor number. The research quotes written guarantees "
        "from one vendor about quota attainment and forecast accuracy. This "
        "product has no evidence for either claim, so neither is shown."
    ),
    "key_material": (
        "Key material is a named asset with a required flag and a reviewed "
        "timestamp. The research says 'key materials remain unreviewed' and names "
        "no list, so the required flag is the rep's own statement of what the "
        "buyer must open."
    ),
    "stakeholder_coverage": (
        "The buying group is the named people on the deal. Coverage is the share "
        "that engaged against 60 percent, and a deal with no named stakeholder "
        "scores zero on coverage rather than one, because a buying group nobody "
        "recorded is the missing-stakeholders risk."
    ),
}

#: What this build deliberately does not do, named so a reviewer does not have to
#: guess whether a gap is a decision or an oversight.
NOT_IMPLEMENTED: tuple[str, ...] = (
    "Live coaching during a call: needs telephony, recording and transcription.",
    "Transcription of a call and a model that summaries it.",
    "Auto-documentation of a call.",
    "CRM opportunity fields as a data source, and a CRM update after a score.",
    "A notification channel, so a risk is not pushed to leadership by email or chat.",
    "The vendor guarantees quoted in the research about quota and forecast accuracy.",
)


def vocabulary() -> dict[str, Any]:
    """The vocabulary this workflow enforces, and every decision behind it.

    Served by the vocabulary route, so the page reads its states from here
    rather than hard-coding them, and so a reviewer can read each decision
    without opening the source.
    """
    return {
        "ticket": "WF-134",
        "event_kinds": list(EVENT_KINDS),
        "event_weights": dict(EVENT_WEIGHTS),
        "weights": dict(WEIGHTS),
        "bands": list(BANDS),
        "healthy_floor": HEALTHY_FLOOR,
        "watch_floor": WATCH_FLOOR,
        "drop_threshold": DROP_THRESHOLD,
        "decay_window_days": DECAY_WINDOW_DAYS,
        "target_event_weight": TARGET_EVENT_WEIGHT,
        "coverage_target": COVERAGE_TARGET,
        "engagement_floor": ENGAGEMENT_FLOOR,
        "risk_reasons": list(RISK_REASONS),
        "reason_sentences": dict(REASON_SENTENCES),
        "alert_kinds": list(ALERT_KINDS),
        "outcome_states": list(OUTCOME_STATES),
        "frameworks": list(FRAMEWORKS),
        "default_framework": DEFAULT_FRAMEWORK,
        "framework_criteria": {key: list(value) for key, value in FRAMEWORK_CRITERIA.items()},
        "collections": [
            DEAL_COLLECTION,
            EVENT_COLLECTION,
            STAKEHOLDER_COLLECTION,
            CONTENT_COLLECTION,
            SCORE_COLLECTION,
            ALERT_COLLECTION,
            COACHING_COLLECTION,
            OUTCOME_COLLECTION,
        ],
        "decisions": dict(DECISIONS),
        "not_implemented": list(NOT_IMPLEMENTED),
    }
