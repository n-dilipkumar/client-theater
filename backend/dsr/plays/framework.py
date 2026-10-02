"""Registering a Play framework, and what may be changed about one afterwards.

The research describes the request body in one sentence - "register a **Play**:
``POST https://api.salesloft.com/v2/integrations/signals/registrations/plays``
with ``signal_registration_id``, localized ``name``/``label``/``description``, the
``indicators[]`` that should trigger it, and ``attributes``" - and the attributes
in another. This module turns that into a validator, and everything it refuses
names the sentence that refuses it.

Two decisions here are readings rather than quotations, and both are recorded in
:mod:`dsr.plays.inferences`:

* **the attribute set is closed.** A key outside :data:`dsr.plays.vocabulary.ALL_ATTRIBUTE_KEYS`
  is refused rather than stored. A Play is a vendor contract with an enumerated
  body, so a typo in ``task_reminder_hours`` is a defect that should be visible at
  registration, and a team that needs a new member adds it to the vocabulary in
  this module - one file, no migration, and the acceptance test that the research
  quotes still holds.
* **what a live Play may be changed into is frozen.** See
  :func:`amendment_findings`.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from dsr.plays.errors import FrameworkError, UndeclaredTrigger
from dsr.plays.vocabulary import (
    ALL_ATTRIBUTE_KEYS,
    ATTRIBUTE_KEYS,
    DYNAMIC_FIELD_ATTRIBUTE,
    DYNAMIC_FIELD_EXEMPT_ATTRIBUTE,
    SUPPORTED_DYNAMIC_FIELDS,
    TASK_TYPE_SUBJECT_ATTRIBUTE,
    TASK_TYPES,
    UNSOURCED_ATTRIBUTE_KEYS,
    find_dynamic_fields,
    require_locale_map,
    require_task_type,
)

#: The researched request body. Nothing outside this set is accepted, and each
#: member is checked by its own rule below.
FRAMEWORK_FIELDS: tuple[str, ...] = (
    "signal_registration_id",
    "name",
    "label",
    "description",
    "indicators",
    "attributes",
)

#: The three localized fields. The research names all three together, so they are
#: required together and carried as a list wherever the code talks about "them".
LOCALIZED_FIELDS: tuple[str, ...] = ("name", "label", "description")

#: Bookkeeping this product owns. A caller may not set it, and a patch may not
#: carry it: the switch that turns this workflow on is reached through its own
#: routes, so the audit row says which request flipped it.
OWNED_FIELDS: tuple[str, ...] = ("enabled", "enabled_at", "enabled_by")

#: camelCase spellings folded onto the researched wire names. The researched API
#: spells these with underscores; a form in this product's UI does not, and
#: refusing a camelCase key would teach an integrator that the field is
#: unsupported rather than that it is spelled differently.
CANONICAL: dict[str, str] = {
    "signalRegistrationId": "signal_registration_id",
    "signal_registration": "signal_registration_id",
    "taskType": "task_type",
    "taskSubject": "task_subject",
    "taskReminderHours": "task_reminder_hours",
    "emailSubject": "email_subject",
    "emailTemplate": "email_template",
    "cadenceId": "cadence_id",
    "indicatorKeys": "indicators",
    "triggerIndicators": "indicators",
}

#: How a finding is described, so a caller reading one knows whether it is a typo,
#: a conflict with state that already exists, or a rule this build added.
FINDING_MEANING: dict[str, str] = {
    "unknown_field": "the field is not one the researched Play body carries",
    "activation_is_its_own_route": (
        "enabling and disabling are reached through their own routes, so the audit row "
        "names the request that flipped the switch"
    ),
    "registration_is_identity": (
        "signal_registration_id says which signals can fire this Play; moving a live "
        "template between registrations silently retargets an automation"
    ),
    "locale_removed": "a localized sentence a seller may already have read is gone",
    "frozen_while_live": (
        "the Play has already produced a task, so what fires and what is created is "
        "history; register a new Play for the new behaviour"
    ),
}


def canonical(payload: Mapping[str, Any], *, for_amendment: bool = False) -> dict[str, Any]:
    """Fold camelCase spellings onto the researched names, and refuse the rest.

    A field this build does not recognise is an error rather than a dropped key.
    The alternative - keeping it and storing it - would make a Play's contract
    unknowable from the record, which is the property this module exists to
    guarantee.

    ``for_amendment`` changes exactly one thing: a patch that tries to set one of
    :data:`OWNED_FIELDS` is passed through rather than refused, so
    :func:`amendment_findings` can report it alongside every other offending path.
    A registration has no such sibling, so it keeps the hard refusal.
    """
    if not isinstance(payload, Mapping):
        raise FrameworkError("a Play framework must be a JSON object")
    folded: dict[str, Any] = {}
    unknown: list[str] = []
    for key, value in payload.items():
        name = CANONICAL.get(str(key), str(key))
        if name in OWNED_FIELDS:
            if for_amendment:
                folded[name] = value
                continue
            raise FrameworkError(
                f"{name!r} is this product's own bookkeeping and is not accepted from a "
                'caller. A Play is registered disabled: "After registration, the registered '
                'Play must be enabled in the Salesloft UI." Use the enable route to switch '
                "it on, so the audit row names the request that did it."
            )
        if name not in FRAMEWORK_FIELDS:
            unknown.append(str(key))
            continue
        folded[name] = value
    if unknown:
        raise FrameworkError(
            f"unrecognised field(s) {', '.join(sorted(unknown))}. A Play body is "
            f'{", ".join(FRAMEWORK_FIELDS)}, from "signal_registration_id, localized '
            "name/label/description, the indicators[] that should trigger it, and "
            'attributes".'
        )
    return folded


def normalise_indicators(value: Any) -> list[str]:
    """The trigger list: at least one indicator key, deduplicated, order kept.

    A bare key or ``{"key": ...}`` is accepted for both, because the researched
    signal carries indicators as objects with metadata and a Play carries only the
    keys, and a person copying one into the other should not have to unwrap it.
    """
    if value is None:
        raise FrameworkError(
            "indicators is required: a Play needs the indicators[] that should trigger it"
        )
    if isinstance(value, (str, Mapping)):
        value = [value]
    if not isinstance(value, (list, tuple)) or not value:
        raise FrameworkError(
            "indicators must be a non-empty list of indicator keys, for example "
            '["spent_more_than_30s_on_site"]'
        )
    keys: list[str] = []
    for entry in value:
        if isinstance(entry, Mapping):
            entry = entry.get("key")
        if not isinstance(entry, str) or not entry.strip():
            raise FrameworkError(f"an indicator entry must be a non-empty key; got {entry!r}")
        text = entry.strip()
        if text not in keys:
            keys.append(text)
    return keys


def normalise_attributes(value: Any, warnings: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate ``attributes`` against the researched list.

    Attribute keys are folded with the same camelCase map as the body, because a
    form in this product's UI sends ``taskType`` and the researched API spells it
    ``task_type``, and refusing one of them teaches an integrator that the
    attribute is unsupported rather than that it is spelled differently.

    Every finding that is a warning rather than a refusal is appended to
    ``warnings`` in place, so the caller cannot lose one by forgetting to look.
    """
    if value is None:
        raise FrameworkError(
            'attributes is required: a Play is "an automation that generates a one-off '
            f'action", and it names what action through attributes.task_type '
            f"({', '.join(TASK_TYPES)})"
        )
    if not isinstance(value, Mapping) or not value:
        raise FrameworkError("attributes must be a non-empty object")

    folded: dict[str, Any] = {
        CANONICAL.get(str(key), str(key)): item for key, item in value.items()
    }
    unknown = [str(key) for key in folded if key not in ALL_ATTRIBUTE_KEYS]
    if unknown:
        raise FrameworkError(
            f"unrecognised attribute(s) {', '.join(sorted(unknown))}. The researched "
            f"attributes are {', '.join(ATTRIBUTE_KEYS)}"
            + (
                f", plus the unsourced {', '.join(UNSOURCED_ATTRIBUTE_KEYS)} (see "
                "/api/wf-028/vocabulary)."
                if set(unknown) & set(UNSOURCED_ATTRIBUTE_KEYS)
                else "."
            )
        )

    attributes: dict[str, Any] = {"task_type": require_task_type(folded.get("task_type"))}
    task_type = attributes["task_type"]

    if folded.get("task_reminder_hours") is not None:
        attributes["task_reminder_hours"] = _require_hours(folded["task_reminder_hours"])

    for key in ("task_subject", "email_subject", "email_template"):
        raw = folded.get(key)
        if raw is None:
            continue
        if not isinstance(raw, str) or not raw.strip():
            raise FrameworkError(f"attributes.{key} must be a non-empty string")
        attributes[key] = raw.strip()

    for key in ("task_subject", "email_subject", "email_template"):
        if key not in attributes:
            continue
        _check_dynamic_fields(key, attributes[key], warnings)

    cadence = folded.get("cadence_id")
    if cadence is None:
        if task_type == "add-to-cadence":
            warnings.append(
                {
                    "code": "no_cadence_named",
                    "severity": "warning",
                    "field": "attributes.cadence_id",
                    "detail": (
                        '"Add Person to a Cadence" names no attribute identifying which '
                        "cadence in the researched request body, so there is nowhere to add "
                        "anyone. This Play is registered and warned about rather than "
                        "refused, and the task it creates will say it has no cadence. Add "
                        "attributes.cadence_id to make it routable."
                    ),
                }
            )
    else:
        if not isinstance(cadence, str) or not cadence.strip():
            raise FrameworkError("attributes.cadence_id must be a non-empty string")
        attributes["cadence_id"] = cadence.strip()

    # The subject each task type needs. See the ``task-type-requires-its-own-subject``
    # inference: the researched list carries task_subject and email_subject as two
    # members, which only means something if each is required by the type that uses it.
    subject_key = TASK_TYPE_SUBJECT_ATTRIBUTE[task_type]
    if subject_key not in attributes:
        raise FrameworkError(
            f"attributes.{subject_key} is required for a {task_type} Play: a {task_type} "
            f"with no {subject_key} is an action with nothing to describe it. The "
            f"researched attributes list carries {subject_key} for this purpose."
        )
    if task_type != "email" and "email_subject" in attributes:
        warnings.append(
            {
                "code": "attribute_not_used_by_task_type",
                "severity": "warning",
                "field": "attributes.email_subject",
                "detail": (
                    f"a {task_type} Play does not send an email, so email_subject is "
                    "carried but never used"
                ),
            }
        )
    if task_type != "email" and "email_template" in attributes:
        warnings.append(
            {
                "code": "attribute_not_used_by_task_type",
                "severity": "warning",
                "field": "attributes.email_template",
                "detail": (
                    f"a {task_type} Play does not send an email, so email_template is "
                    "carried but never used"
                ),
            }
        )
    return attributes


def _require_hours(value: Any) -> int:
    """``task_reminder_hours`` as whole non-negative hours.

    Negative hours are a reminder in the past, which is not a reminder. The
    research names the field and not its bound, so the bound is this build's
    reading and is recorded as the ``reminder-hours-bound`` inference.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FrameworkError(
            f"attributes.task_reminder_hours must be a number of hours; got {value!r}"
        )
    if float(value) != int(value):
        raise FrameworkError(f"attributes.task_reminder_hours must be whole hours; got {value!r}")
    hours = int(value)
    if hours < 0:
        raise FrameworkError(f"attributes.task_reminder_hours must not be negative; got {hours}")
    return hours


def _check_dynamic_fields(key: str, text: str, warnings: list[dict[str, Any]]) -> None:
    """Enforce where a dynamic field is allowed, and which ones.

    The rule is about *where*, not *which*: "Dynamic Fields are not supported
    outside of email templates", with one named exception, ``task_subject`` supports
    ``name``. So a field inside ``email_template`` is not checked at all, a field
    inside ``task_subject`` must be ``name``, and a field anywhere else - including
    ``email_subject`` - is refused.
    """
    found = find_dynamic_fields(text)
    if not found:
        if "{" in text and "}" not in text:
            warnings.append(
                {
                    "code": "unclosed_brace",
                    "severity": "warning",
                    "field": f"attributes.{key}",
                    "detail": (
                        f"attributes.{key} contains an unclosed brace. A dynamic field is "
                        "written {name}; anything else is shown to the seller verbatim."
                    ),
                }
            )
        return
    if key == DYNAMIC_FIELD_ATTRIBUTE:
        return
    if key == DYNAMIC_FIELD_EXEMPT_ATTRIBUTE:
        unsupported = [name for name in found if name not in SUPPORTED_DYNAMIC_FIELDS]
        if not unsupported:
            return
        raise FrameworkError(
            f"attributes.{key} uses dynamic field(s) {', '.join(unsupported)}, which the "
            f'research does not support there. "The only exception here is that '
            f'{key} supports {", ".join(SUPPORTED_DYNAMIC_FIELDS)}" - a field it does '
            "not name would render empty in a seller's task list."
        )
    raise FrameworkError(
        f'attributes.{key} uses dynamic field(s) {", ".join(found)}. "At this time, '
        f'Dynamic Fields are not supported outside of email templates", so the only '
        f"place a field is rendered is attributes.{DYNAMIC_FIELD_ATTRIBUTE}."
    )


def normalise_framework(
    payload: Mapping[str, Any],
    *,
    declared_indicators: Iterable[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate one Play body, and return ``(record_data, warnings)``.

    ``declared_indicators`` is the indicator set of the signal registration the
    play names, when the caller could read it. ``None`` means the registration was
    not available, and the trigger check is skipped rather than failed - the caller
    that holds the registration (:class:`dsr.plays.engine.PlayEngine`) always passes
    the real set or refuses the play outright.
    """
    folded = canonical(payload)

    registration = folded.get("signal_registration_id")
    if not isinstance(registration, str) or not registration.strip():
        raise FrameworkError(
            "signal_registration_id is required: a Play is registered against a signal "
            'registration, and "After registering a signal (see #12), register a Play"'
        )

    warnings: list[dict[str, Any]] = []
    indicators = normalise_indicators(folded.get("indicators"))
    if declared_indicators is not None:
        declared = {str(key) for key in declared_indicators}
        undeclared = [key for key in indicators if key not in declared]
        if undeclared:
            raise UndeclaredTrigger(
                f"indicator(s) {', '.join(undeclared)} are not declared by signal "
                "registration "
                f'{registration.strip()!r}. "When a matching signal arrives, Salesloft '
                'creates the task", and a signal only carries indicators its '
                "registration declares, so a Play triggering on an undeclared one can "
                "never fire. Declared here: "
                f"{', '.join(sorted(declared)) or 'none'}."
            )

    data: dict[str, Any] = {
        "signal_registration_id": registration.strip(),
        "name": require_locale_map(folded.get("name"), "name"),
        "label": require_locale_map(folded.get("label"), "label"),
        "description": require_locale_map(folded.get("description"), "description"),
        "indicators": indicators,
        "attributes": normalise_attributes(folded.get("attributes"), warnings),
        # Registration is not activation. The two are separate moments in the
        # research, and conflating them is the one bug this feature cannot have:
        # a Play that fires on registration puts tasks in sellers' queues that
        # nobody asked for.
        "enabled": False,
        "enabled_at": None,
        "enabled_by": None,
    }
    return data, warnings


def amendment_findings(
    current: Mapping[str, Any], patch: Mapping[str, Any], *, task_count: int
) -> list[dict[str, Any]]:
    """Every reason a patch to a live Play is refused, at once.

    The research gives Plays an update endpoint and says nothing about what may
    change, so this is a reading, and the reading has one rule: *a Play that has
    already produced a task has a history, and the fields that decide what fires and
    what is created are part of it.* Adding a locale is still allowed, because
    adding cannot invalidate a task that already fired.

    A patch merges into a locale map rather than replacing it, so a patch simply
    omitting a locale cannot drop one - which means a caller who *means* to drop a
    sentence has to say so with an explicit ``null``, and that is the only thing
    this function reports as a removal. Silently losing a sentence a seller has read
    is the failure worth preventing, and it cannot happen by omission.

    One attempt should not have to be repeated once per field, so every offending
    path comes back in the same response.
    """
    folded = canonical(patch, for_amendment=True)
    findings: list[dict[str, Any]] = []

    for key in OWNED_FIELDS:
        if key in folded:
            findings.append(
                {
                    "path": key,
                    "change": FINDING_MEANING["activation_is_its_own_route"],
                    "detail": (
                        f"{key!r} is not patchable. Enable or disable the Play through its "
                        "own route so the audit row names the request that flipped it."
                    ),
                }
            )

    if "signal_registration_id" in folded and folded["signal_registration_id"] != current.get(
        "signal_registration_id"
    ):
        findings.append(
            {
                "path": "signal_registration_id",
                "change": FINDING_MEANING["registration_is_identity"],
                "detail": (
                    f"this Play is registered against "
                    f"{current.get('signal_registration_id')!r} and cannot be moved to "
                    f"{folded['signal_registration_id']!r}"
                ),
            }
        )

    for field in LOCALIZED_FIELDS:
        before = current.get(field) or {}
        after = folded.get(field)
        if after is None:
            continue
        if not isinstance(after, Mapping):
            raise FrameworkError(f"{field} must be a map of locale to text, or to null to drop one")
        for locale in sorted(locale for locale, text in after.items() if text is None):
            if locale in before:
                findings.append(
                    {
                        "path": f"{field}.{locale}",
                        "change": FINDING_MEANING["locale_removed"],
                        "detail": f"the {locale} text exists today and this patch drops it",
                    }
                )
        if task_count <= 0:
            continue
        rewritten = sorted(
            locale
            for locale, text in after.items()
            if locale in before and text is not None and before[locale] != text
        )
        for locale in rewritten:
            findings.append(
                {
                    "path": f"{field}.{locale}",
                    "change": FINDING_MEANING["frozen_while_live"],
                    "detail": (
                        f"this Play has produced {task_count} task(s); the {locale} text a "
                        "seller saw when one fired is history"
                    ),
                }
            )

    for field in ("indicators", "attributes"):
        if field not in folded or task_count <= 0:
            continue
        if folded[field] != current.get(field):
            findings.append(
                {
                    "path": field,
                    "change": FINDING_MEANING["frozen_while_live"],
                    "detail": (
                        f"this Play has produced {task_count} task(s), so {field} is frozen. "
                        "Register a new Play for the new behaviour."
                    ),
                }
            )

    return findings


def apply_amendment(current: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Merge a checked patch into a Play body.

    A locale map is merged rather than replaced, and an explicit ``null`` drops the
    locale it names - which :func:`amendment_findings` has already refused, so by
    the time this runs no ``null`` survives. Handling it here anyway keeps the two
    functions consistent if the rule is ever relaxed.
    """
    folded = canonical(patch, for_amendment=True)
    merged = dict(current)
    for key, value in folded.items():
        if key in LOCALIZED_FIELDS and isinstance(value, Mapping):
            merged[key] = {
                locale: text
                for locale, text in {**(current.get(key) or {}), **value}.items()
                if text is not None
            }
        else:
            merged[key] = value
    return merged
