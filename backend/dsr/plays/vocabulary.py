"""The published vocabularies for Play registration.

Everything the research fixes by name lives here and is served as data at
``GET /api/wf-028/vocabulary``, so a client renders its pickers from the same
source the validator enforces against. A value added here reaches every client at
once and cannot drift away from what the API accepts.

The four sets that are quoted in the research, with the sentence each comes from:

* the task types - "These are the available Play task types: Call, Email, Add
  Person to a Cadence." The wire spellings are the ones the researched request
  body uses (``call``, ``email``, ``add-to-cadence``), and the labels are the
  sentence's capitalised forms, because a page has to show a seller the words
  the vendor's own documentation uses.
* the assignment precedence - "For task assignment, we will use an object
  precedence order of User, Content, Person, Account."
* the event types - the twelve the researched webhook documentation names, of
  which this workflow tracks four: "Track outcomes via Salesloft webhooks
  (``task_created``, ``task_completed``, ``step_created``, ``success_created``)."
* the delivery retry policy - "A failing webhook is retried three additional
  times, spaced 15 seconds apart, before being marked as failed."

And two sets that are *not* fully sourced, marked as such in the payload they
appear in, so a reader can tell a quotation from a decision:

* :data:`UNSOURCED_ATTRIBUTE_KEYS` - the researched ``attributes`` list has no
  member that identifies *which* cadence an ``add-to-cadence`` play adds a person
  to, which would make one of the three researched task types unroutable. The key
  is therefore accepted, optional, and flagged as unsourced wherever it appears.
* the dynamic-field rule - the research states a limitation in prose ("Dynamic
  Fields are not supported outside of email templates. The only exception here is
  that ``task_subject`` supports ``name``") without publishing the field names the
  vendor recognises, so :data:`SUPPORTED_DYNAMIC_FIELDS` is the one field that
  sentence names and nothing else.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from dsr.plays.errors import FrameworkError, PlayError

#: "These are the available Play task types: Call, Email, Add Person to a Cadence."
TASK_TYPES: tuple[str, ...] = ("call", "email", "add-to-cadence")

#: The labels the research's own sentence uses, in the same order. A seller reads
#: "Add Person to a Cadence", not "add-to-cadence".
TASK_TYPE_LABELS: dict[str, str] = {
    "call": "Call",
    "email": "Email",
    "add-to-cadence": "Add Person to a Cadence",
}

#: What each task type does, in the workflow's own terms. Used as a hint beside a
#: picker so a person configuring a Play can see what they are choosing.
TASK_TYPE_MEANING: dict[str, str] = {
    "call": "a one-off call task assigned to the seller the precedence resolved",
    "email": "a one-off email, rendered from the named template and sent by the seller",
    "add-to-cadence": "a step added to a cadence, so the buyer is pursued by a sequence",
}

#: The subject attribute each task type needs, and why.
#:
#: The researched attribute list carries ``task_subject`` and ``email_subject`` as
#: two separate members, which only means something if a call has a subject and an
#: email has its own. A call with no subject is a task titled nothing. This is a
#: reading of the attribute list rather than a quotation from it, recorded as the
#: ``task-type-requires-its-own-subject`` inference.
TASK_TYPE_SUBJECT_ATTRIBUTE: dict[str, str] = {
    "call": "task_subject",
    "add-to-cadence": "task_subject",
    "email": "email_subject",
}

#: The attributes the researched request body names.
ATTRIBUTE_KEYS: tuple[str, ...] = (
    "task_type",
    "task_subject",
    "task_reminder_hours",
    "email_subject",
    "email_template",
)

#: An attribute the research does not name, accepted so the researched
#: ``add-to-cadence`` task type can be usable at all.
#:
#: "Add Person to a Cadence" has to name a cadence, and no researched member of
#: ``attributes`` does. Requiring an invented field would be worse than accepting
#: an unsourced one, and pretending the gap is not there would be worse still, so
#: it is accepted, optional, published with ``sourced: false``, and warned about
#: when it is absent. Recorded as the ``cadence-identifier-is-unsourced``
#: inference.
UNSOURCED_ATTRIBUTE_KEYS: tuple[str, ...] = ("cadence_id",)

#: Every attribute the API accepts. A key outside this set is refused rather than
#: silently dropped, so a typo in a vendor contract is visible at registration.
ALL_ATTRIBUTE_KEYS: tuple[str, ...] = ATTRIBUTE_KEYS + UNSOURCED_ATTRIBUTE_KEYS

#: "At this time, Dynamic Fields are not supported outside of email templates. The
#: only exception here is that ``task_subject`` supports ``name``."
#:
#: The one field name the sentence names is ``name``. Nothing else is supported
#: outside ``email_template``, so ``{company}`` in a subject is a refusal rather
#: than a field that silently renders empty in a seller's task list.
SUPPORTED_DYNAMIC_FIELDS: tuple[str, ...] = ("name",)

#: The only attribute outside ``email_template`` where a dynamic field is allowed,
#: and the only field allowed there.
DYNAMIC_FIELD_EXEMPT_ATTRIBUTE: str = "task_subject"

#: The researched attribute where dynamic fields are supported in full.
DYNAMIC_FIELD_ATTRIBUTE: str = "email_template"

#: "For task assignment, we will use an object precedence order of User, Content,
#: Person, Account." The attribution key each object arrives on is the spelling
#: the signal carries, which is WF-027's researched attribution vocabulary.
ASSIGNMENT_PRECEDENCE: tuple[str, ...] = (
    "user_guid",
    "email_tracked_content_id",
    "person_id",
    "account_id",
)

#: The Salesloft object each precedence entry names.
ASSIGNMENT_OBJECT: dict[str, str] = {
    "user_guid": "User",
    "email_tracked_content_id": "Content",
    "person_id": "Person",
    "account_id": "Account",
}

#: The rule name each precedence entry resolves to, apart from Account which has a
#: two-step rule of its own. A table rather than a derived string, because a
#: resolver that computes its own rule names is a resolver whose names drift.
ASSIGNMENT_RULE: dict[str, str] = {
    "user_guid": "user_precedence",
    "email_tracked_content_id": "content_precedence",
    "person_id": "person_precedence",
    "account_id": "account_engagement_score",
}

#: "The most engaged Person on the Account in the Last 30 days (Highest Buyer
#: Engagement Score)." The window is stated as a span, not a date, so it is applied
#: as a span.
ENGAGEMENT_WINDOW_DAYS: int = 30

#: The event types the researched webhook documentation lists. The full set, so a
#: subscription may name one this workflow does not itself track.
EVENT_TYPES: tuple[str, ...] = (
    "task_created",
    "task_updated",
    "task_completed",
    "step_created",
    "step_updated",
    "success_created",
    "email_updated",
    "conversation_created",
    "conversation_recording_created",
    "call_created",
    "meeting_booked",
    "link_swap",
)

#: "Track outcomes via Salesloft webhooks (``task_created``, ``task_completed``,
#: ``step_created``, ``success_created``)." The four this workflow follows.
PLAY_EVENT_TYPES: tuple[str, ...] = (
    "task_created",
    "task_completed",
    "step_created",
    "success_created",
)

#: "A failing webhook is retried three additional times, spaced 15 seconds apart,
#: before being marked as failed."
WEBHOOK_RETRY_ATTEMPTS: int = 3
WEBHOOK_RETRY_SPACING_SECONDS: int = 15

#: The UI path the research names, served so a page can point a seller at the
#: switch that actually turns this workflow on. This product's own routes are the
#: DSR's stand-in for it, and saying so is the honest thing to do in a page that
#: otherwise looks like the vendor's Settings screen.
ACTIVATION_PATH: str = "Settings → Workflow → Plays → Edit Play"

#: The workflow's own subject sentence, on every task and every summary.
#:
#: "This workflow *is* the automation - signal → task with no human in the loop
#: until the seller acts." The first half is what makes this feature dangerous to
#: get wrong, and the second half is the limit of it.
AUTOMATION_NOTE: str = (
    "This Play is the automation: a matching signal creates the task with no human "
    "in the loop. Nobody is asked to approve it. The only human in the loop is the "
    "seller who then acts on the task, and until the Play is enabled in the UI it "
    "creates nothing at all."
)

#: What a registered-but-disabled Play is. Registered is not enabled, and the
#: research is explicit that the two are different moments.
DISABLED_NOTE: str = (
    'Registered, not enabled. "After registration, the registered Play must be '
    "enabled in the Salesloft UI. You can do so by going to "
    f'{ACTIVATION_PATH}." Until then it creates nothing.'
)

#: The locale assumed when a caller does not ask for one.
DEFAULT_LOCALE = "en"

#: The locale a localized field falls back to.
LOCALE_FALLBACK = "en"

_FIELD = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def require_task_type(value: Any) -> str:
    """Normalise and check a task type.

    Case and surrounding space are forgiven, because this is a value a person
    picks in a form; the vocabulary itself is not loosened. The vendor's own label
    spellings are accepted too, so ``Add Person to a Cadence`` and
    ``add-to-cadence`` are one value rather than two that look alike.
    """
    if not isinstance(value, str) or not value.strip():
        raise FrameworkError(
            f"attributes.task_type is required: a Play generates one of {', '.join(TASK_TYPES)}"
        )
    text = value.strip().lower().replace("_", "-").replace(" ", "-")
    for name in TASK_TYPES:
        if text == name or text == TASK_TYPE_LABELS[name].lower().replace(" ", "-"):
            return name
    raise FrameworkError(
        f"attributes.task_type must be one of {', '.join(TASK_TYPES)}; got {value!r}. The "
        'research names exactly three: "Call, Email, Add Person to a Cadence."'
    )


def require_event_type(value: Any) -> str:
    """Normalise and check a webhook event type."""
    if not isinstance(value, str) or not value.strip():
        raise PlayError(f"event_types entries must be non-empty strings; got {value!r}")
    text = value.strip().lower()
    if text not in EVENT_TYPES:
        raise PlayError(
            f"event type {value!r} is not one this workflow documents. Known types: "
            f"{', '.join(EVENT_TYPES)}"
        )
    return text


def normalise_event_types(value: Any) -> list[str]:
    """A deduplicated event-type list, ordered by the published vocabulary.

    Ordered by the vocabulary rather than by the caller, so two subscriptions
    naming the same set are byte-identical and a diff between them is meaningful.
    A comma-separated string is accepted because that is how the types read in the
    research's own sentence, and a reader copying them out of it should not have to
    reformat them.
    """
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",") if part.strip()]
    if not isinstance(value, (list, tuple)) or not value:
        raise PlayError(
            "a webhook subscription must name at least one event type, for example "
            '{"event_types": ["task_created", "task_completed"]}'
        )
    seen = {require_event_type(entry) for entry in value}
    return [name for name in EVENT_TYPES if name in seen]


def find_dynamic_fields(text: Any) -> list[str]:
    """The dynamic field names a string contains, in order of first appearance.

    Only the simple ``{name}`` form is recognised. An unclosed brace is not a field
    and is not reported here - it is a defect in the author's template, and
    :func:`dsr.plays.framework.normalise_attributes` names it.
    """
    if not isinstance(text, str):
        return []
    found: list[str] = []
    for name in _FIELD.findall(text):
        if name not in found:
            found.append(name)
    return found


def normalise_locale(value: Any) -> str:
    """Accept ``en``, ``en-GB``, ``EN_gb`` and store one spelling.

    Region tags are lowercased and the separator folded, so ``en-GB`` and ``en_GB``
    are one locale rather than two that happen to look alike.
    """
    if value is None:
        return DEFAULT_LOCALE
    if not isinstance(value, str) or not value.strip():
        raise PlayError(f"locale must be a string; got {value!r}")
    text = value.strip().replace("_", "-")
    parts = text.split("-")
    return "-".join([parts[0].lower(), *[part.upper() for part in parts[1:]]])


def require_locale_map(value: Any, what: str) -> dict[str, str]:
    """Coerce a localized field into ``{locale: text}``.

    A bare string is accepted as a single ``en`` entry. That is a convenience, not
    a loosening: the research requires a localized name, label and description,
    and this still produces a localized field - it just produces one locale.
    """
    if isinstance(value, str) and value.strip():
        return {DEFAULT_LOCALE: value.strip()}
    if not isinstance(value, Mapping) or not value:
        raise PlayError(
            f'{what} must be a non-empty map of locale to text, for example {{"en": "..."}}'
        )
    result: dict[str, str] = {}
    for locale, text in value.items():
        if not isinstance(text, str) or not text.strip():
            raise PlayError(f"{what} for locale {locale!r} must be a non-empty string")
        result[normalise_locale(locale)] = text.strip()
    return result


def resolve_locale(requested: str | None, available: Mapping[str, Any]) -> tuple[str | None, bool]:
    """Pick the locale to render in, and say whether it was a fallback.

    The chain is exact tag, then the bare language, then the fallback locale.
    Returning the flag rather than hiding it matters: a seller reading a German
    Play because that is the only German one registered is fine, and a seller
    reading English without knowing why is not.
    """
    wanted = normalise_locale(requested)
    keys = {normalise_locale(key) for key in available}
    if wanted in keys:
        return wanted, False
    language = wanted.split("-")[0]
    if language in keys:
        return language, True
    if LOCALE_FALLBACK in keys:
        return LOCALE_FALLBACK, True
    first = sorted(keys)[0] if keys else None
    return first, first != wanted


def locale_view(field: Mapping[str, Any], requested: str | None) -> dict[str, Any]:
    """One localized field, rendered for a locale, with the fallback flagged."""
    chosen, fell_back = resolve_locale(requested, field)
    return {
        "locale": chosen,
        "fell_back": fell_back,
        "available": sorted(field),
        "text": field.get(chosen) if chosen else None,
    }


def describe() -> dict[str, Any]:
    """The whole published vocabulary, for clients and for the page's pickers."""
    return {
        "task_types": [
            {
                "value": name,
                "label": TASK_TYPE_LABELS[name],
                "meaning": TASK_TYPE_MEANING[name],
                "subject_attribute": TASK_TYPE_SUBJECT_ATTRIBUTE[name],
            }
            for name in TASK_TYPES
        ],
        "attributes": [
            {
                "key": key,
                "sourced": key in ATTRIBUTE_KEYS,
                "required_for": [
                    name for name in TASK_TYPES if TASK_TYPE_SUBJECT_ATTRIBUTE[name] == key
                ],
            }
            for key in ALL_ATTRIBUTE_KEYS
        ],
        "dynamic_fields": {
            "supported": list(SUPPORTED_DYNAMIC_FIELDS),
            "exempt_attribute": DYNAMIC_FIELD_EXEMPT_ATTRIBUTE,
            "supported_attribute": DYNAMIC_FIELD_ATTRIBUTE,
            "rule": (
                '"At this time, Dynamic Fields are not supported outside of email '
                "templates. The only exception here is that task_subject supports "
                'name." Any other field outside email_template is refused rather '
                "than rendered empty in a seller's task list."
            ),
        },
        "assignment_precedence": [
            {
                "attribution": key,
                "object": ASSIGNMENT_OBJECT[key],
                "precedence": index,
            }
            for index, key in enumerate(ASSIGNMENT_PRECEDENCE)
        ],
        "engagement_window_days": ENGAGEMENT_WINDOW_DAYS,
        "event_types": [
            {"value": name, "tracked_by_this_workflow": name in PLAY_EVENT_TYPES}
            for name in EVENT_TYPES
        ],
        "play_event_types": list(PLAY_EVENT_TYPES),
        "webhook_retries": {
            "additional_attempts": WEBHOOK_RETRY_ATTEMPTS,
            "total_attempts": WEBHOOK_RETRY_ATTEMPTS + 1,
            "spacing_seconds": WEBHOOK_RETRY_SPACING_SECONDS,
            "rule": (
                "A failing webhook is retried three additional times, spaced 15 seconds "
                "apart, before being marked as failed."
            ),
        },
        "activation_path": ACTIVATION_PATH,
        "automation_note": AUTOMATION_NOTE,
        "disabled_note": DISABLED_NOTE,
        "default_locale": DEFAULT_LOCALE,
        "locale_fallback": LOCALE_FALLBACK,
    }
