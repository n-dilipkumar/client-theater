"""The pure rules for an in-room qualifying form. No store, no framework.

Everything here is a function of its arguments. That is deliberate: the scoring,
the banding and the payload validation are the parts a reviewer most needs to be
able to check without a database, and a rule that reads the store is a rule that
has to be tested through one.

Each rule cites the sentence it implements, so the behaviour can be checked
against the specification without reading the tests first.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from dsr.qualification_capture import vocabulary as v
from dsr.qualification_capture.errors import FormValidationError

# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #


def parse_timestamp(value: Any, field: str = "at") -> int | None:
    """Read a moment as Unix epoch milliseconds, or ``None`` for an empty one.

    Accepts a number, a numeric string, an ISO 8601 string, or a date. A boolean
    is refused rather than read as 0 or 1, because ``isinstance(True, int)`` is
    true in Python and a ``true`` that silently becomes a timestamp is a defect
    waiting to happen.
    """

    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise FormValidationError({field: "must be a timestamp, not a boolean"})
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FormValidationError({field: f"is not a readable timestamp: {value!r}"}) from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def to_iso(ms: int | None) -> str | None:
    """Render epoch milliseconds back as an ISO 8601 string, or ``None``."""

    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds")


# --------------------------------------------------------------------------- #
# The form schema
# --------------------------------------------------------------------------- #


def normalise_form_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a form definition and fill its defaults.

    A form selects which of the four researched dimensions it asks about and
    which of those are required. The default is all four, because the research
    names all four; a form that asks fewer is a deliberate narrowing, not an
    omission.
    """

    errors: dict[str, str] = {}
    payload = dict(payload or {})

    name = str(payload.get("name") or "").strip()
    if not name:
        errors["name"] = "a form needs a name"
    elif len(name) > 120:
        errors["name"] = "must be 120 characters or fewer"

    dimensions = _clean_dimensions(payload.get("dimensions"), errors)
    required = _clean_dimensions(
        payload.get("required_dimensions", dimensions), errors, field="required_dimensions"
    )
    if not errors.get("required_dimensions"):
        stray = [dimension for dimension in required if dimension not in dimensions]
        if stray:
            errors["required_dimensions"] = (
                f"must be a subset of the form's dimensions; got {stray}"
            )

    routing = _clean_routing(payload.get("routing"), errors)

    status = str(payload.get("status") or v.FORM_STATUS_DRAFT).strip().lower()
    if status not in v.FORM_STATUSES:
        errors["status"] = f"must be one of {', '.join(v.FORM_STATUSES)}"

    intro = payload.get("intro")
    if intro is not None and len(str(intro)) > 500:
        errors["intro"] = "must be 500 characters or fewer"

    if errors:
        raise FormValidationError(errors)

    return {
        "name": name,
        "intro": str(intro).strip() if intro else None,
        "dimensions": dimensions,
        "required_dimensions": required,
        "routing": routing,
        "status": status,
        "deal_ref": _clean_ref(payload.get("deal_ref")),
        "account_ref": _clean_ref(payload.get("account_ref")),
    }


def _clean_dimensions(
    value: Any, errors: dict[str, str], *, field: str = "dimensions"
) -> list[str]:
    """A unique, ordered subset of the four researched dimensions."""

    if value is None:
        return list(v.DIMENSIONS)
    if not isinstance(value, (list, tuple, set)):
        errors[field] = "must be a list of dimensions"
        return []
    unknown = [str(item) for item in value if str(item) not in v.DIMENSIONS]
    if unknown:
        errors[field] = f"unknown dimension(s) {unknown}; known: {', '.join(v.DIMENSIONS)}"
        return []
    chosen = {str(item) for item in value}
    if not chosen:
        errors[field] = "a form must ask about at least one dimension"
        return []
    # Preserve the researched order rather than the caller's, so two forms with
    # the same set render identically.
    return [dimension for dimension in v.DIMENSIONS if dimension in chosen]


def _clean_routing(value: Any, errors: dict[str, str]) -> dict[str, str]:
    """An optional per-band override of the routing target."""

    if value is None:
        return {}
    if not isinstance(value, Mapping):
        errors["routing"] = "must be an object mapping a band to a target"
        return {}
    cleaned: dict[str, str] = {}
    for band, target in value.items():
        band_name = str(band).strip().lower()
        if band_name not in v.BANDS:
            errors["routing"] = f"unknown band {band!r}; known: {', '.join(v.BANDS)}"
            return {}
        if not str(target).strip():
            errors["routing"] = f"the target for band {band_name!r} is empty"
            return {}
        cleaned[band_name] = str(target).strip()
    return cleaned


def form_dimensions(form: Mapping[str, Any]) -> list[str]:
    """The dimensions a form asks about, defaulting to all four."""

    data = _data(form)
    chosen = data.get("dimensions")
    if isinstance(chosen, (list, tuple)) and chosen:
        return [str(item) for item in chosen if str(item) in v.DIMENSIONS]
    return list(v.DIMENSIONS)


def required_dimensions(form: Mapping[str, Any]) -> list[str]:
    """The dimensions an answer must carry before it can be submitted."""

    data = _data(form)
    required = data.get("required_dimensions")
    if isinstance(required, (list, tuple)):
        return [str(item) for item in required if str(item) in form_dimensions(form)]
    return form_dimensions(form)


def routing_target(band: str, form: Mapping[str, Any] | None = None) -> str:
    """Where a band routes: the form's override, or the researched default map."""

    override = {}
    if form is not None:
        raw = _data(form).get("routing")
        if isinstance(raw, Mapping):
            override = {str(key).lower(): str(value) for key, value in raw.items()}
    return override.get(band) or v.ROUTE_TARGET_BY_BAND.get(band, v.TARGET_NURTURE)


def form_is_live(form: Mapping[str, Any]) -> bool:
    return str(_data(form).get("status") or "").strip().lower() == v.FORM_STATUS_LIVE


# --------------------------------------------------------------------------- #
# The answers
# --------------------------------------------------------------------------- #


def normalise_answers(
    form: Mapping[str, Any],
    raw: Any,
    *,
    require_all: bool = False,
    field: str = "answers",
) -> dict[str, str]:
    """Read a submission's answers against the form's own dimensions.

    An unknown dimension or an option outside a dimension's closed set is
    refused by name, so a buyer typing a value the form never offered is told
    which answer was wrong rather than handed a generic failure.
    """

    errors: dict[str, str] = {}
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise FormValidationError({field: "must be an object of dimension answers"})

    allowed = set(form_dimensions(form))
    cleaned: dict[str, str] = {}
    for dimension, option in raw.items():
        dimension_name = str(dimension).strip()
        if dimension_name not in allowed:
            errors[f"{field}.{dimension_name}"] = f"this form does not ask about {dimension_name!r}"
            continue
        value = str(option).strip().lower()
        if value not in v.ANSWER_OPTIONS[dimension_name]:
            errors[f"{field}.{dimension_name}"] = (
                f"must be one of {', '.join(v.ANSWER_OPTIONS[dimension_name])}"
            )
            continue
        cleaned[dimension_name] = value

    if require_all:
        missing = [dimension for dimension in required_dimensions(form) if dimension not in cleaned]
        for dimension in missing:
            errors[f"{field}.{dimension}"] = "this answer is required to submit"

    if errors:
        raise FormValidationError(errors)
    return cleaned


def missing_required(form: Mapping[str, Any], answers: Mapping[str, Any]) -> list[str]:
    """The required dimensions still unanswered, in researched order."""

    return [dimension for dimension in required_dimensions(form) if dimension not in answers]


def weight_of(dimension: str, option: str) -> int:
    """The score one answer contributes. An unknown pair is worth nothing."""

    return v.ANSWER_WEIGHTS.get(dimension, {}).get(option, v.WEIGHT_NONE)


def max_score(form: Mapping[str, Any]) -> int:
    """The highest score the form's dimensions can reach."""

    return v.DIMENSION_CEILING * len(form_dimensions(form))


def score_answers(form: Mapping[str, Any], answers: Mapping[str, str]) -> int:
    """Sum the weights of the answered dimensions. Unanswered dimensions add zero."""

    return sum(
        weight_of(dimension, str(option))
        for dimension, option in answers.items()
        if dimension in form_dimensions(form)
    )


def band_for(score: int, ceiling: int) -> str:
    """The qualification band for a score, as a share of the reachable maximum.

    Thresholds are ratios rather than fixed integers because a form may enable
    fewer than four dimensions, and a fixed cut would mark a perfect answer on a
    two-dimension form as unqualified.
    """

    if ceiling <= 0:
        return v.BAND_UNQUALIFIED
    ratio = score / ceiling
    if ratio >= v.BAND_QUALIFIED_RATIO:
        return v.BAND_QUALIFIED
    if ratio >= v.BAND_DEVELOPING_RATIO:
        return v.BAND_DEVELOPING
    return v.BAND_UNQUALIFIED


def qualify(form: Mapping[str, Any], answers: Mapping[str, str]) -> dict[str, Any]:
    """Score a set of answers against a form and name its band."""

    ceiling = max_score(form)
    score = score_answers(form, answers)
    band = band_for(score, ceiling)
    return {
        "score": score,
        "max_score": ceiling,
        "band": band,
        "band_label": v.BAND_LABELS[band],
        "answered": dict(answers),
        "missing": missing_required(form, answers),
        "route_target": routing_target(band, form),
    }


def _data(record: Mapping[str, Any]) -> dict[str, Any]:
    """Read a form's payload whether it is a record or the payload alone."""

    payload = record.get("data")
    if isinstance(payload, Mapping):
        return dict(payload)
    return dict(record)


def _clean_ref(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


__all__ = [
    "band_for",
    "form_dimensions",
    "form_is_live",
    "max_score",
    "missing_required",
    "normalise_answers",
    "normalise_form_payload",
    "parse_timestamp",
    "qualify",
    "required_dimensions",
    "routing_target",
    "score_answers",
    "to_iso",
    "weight_of",
]
