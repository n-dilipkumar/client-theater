"""The workflow definition: the eight steps of the researched flow, minus the clicking.

Steps 2 to 8 of the research are one data structure, and this module is it:

1. *(not here - see* :mod:`dsr.crm_workflows.engine`*: the integration and the
   workspace-to-deal link are step 1, and they are a separate record because
   several workflows share one integration)*
2. "create a Workflow" → a ``name``
3. "Choose **Contact based**" → ``enrollment_type``, which is ``contact`` and
   nothing else
4. "Trigger: **When filter criteria is met** → search 'Dock' or open the
   **Integration** section → select **Dock**" → ``trigger.mode`` and
   ``trigger.integration``
5. + 6. "Pick from the five filter families ... Refine filters" →
   ``trigger.criteria``, validated by :mod:`dsr.crm_workflows.criteria`
7. "Add actions" → ``actions``
8. "Publish the workflow; DSR activity then drives it with no further setup" →
   ``status``

What this module enforces, and why each is a refusal rather than a warning, is in
its own docstrings. The short version is that every one of them is a sentence the
research actually states, and a stored definition that violates one would sit in
the workflow list looking armed.

Why published is immutable
--------------------------

Not a quotation. The research says "Publish the workflow; DSR activity then drives
it with no further setup" and says nothing about editing afterwards. The argument
for refusing is that a published definition is the thing already firing: contacts
may have been enrolled by the version being edited, and their enrollments name
this workflow's ``revision``. Editing in place would rewrite what those rows claim
happened. Unpublish, change, republish is the safe reading, and it is recorded as
the ``amend-published-workflow`` inference in
:mod:`dsr.crm_workflows.inferences` so it can be argued with by name.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.crm_workflows.criteria import lint_criteria, parse_criteria_list
from dsr.crm_workflows.errors import (
    NoActions,
    NotContactBased,
    WorkflowError,
)
from dsr.crm_workflows.vocabulary import (
    ACTION_KINDS,
    DELIVERY_PATHS,
    DEFAULT_INTEGRATION,
    ENROLLMENT_TYPES,
    STAGE_KINDS,
    require_trigger_mode,
)

# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #

DRAFT = "draft"
PUBLISHED = "published"
WITHDRAWN = "withdrawn"

STATUSES: tuple[str, ...] = (DRAFT, PUBLISHED, WITHDRAWN)


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #


def normalise_actions(payload: Any) -> list[dict[str, Any]]:
    """Validate the action list, keeping any kind this build does not resolve.

    "send emails, slack notifications, update fields, change stages **and more!**"
    - so an unrecognised kind is stored with ``resolved: false`` and a reason, and
    is *never* dropped. Dropping it would be the one outcome that cannot be
    recovered from: the person who added it would see a workflow that looks
    complete and is not. This is also where the schema-flexibility rule shows up
    concretely: a team that adds an action kind server-side, or in the CRM, gets it
    stored and reported without a migration or a change to this file.

    What *is* refused is an action with no ``kind`` at all, and a ``change_stage``
    with no stage, because those are not kinds this build does not recognise - they
    are fields with nothing in them.
    """
    if payload is None:
        raise NoActions(
            "a workflow needs at least one action. The research is explicit that the "
            "filter is the trigger and the actions are what it is for: 'send emails, "
            "slack notifications, update fields, change stages and more!'"
        )
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
        raise WorkflowError("actions must be a list")

    actions: list[dict[str, Any]] = []
    for index, entry in enumerate(payload):
        if not isinstance(entry, Mapping):
            raise WorkflowError(f"action {index} must be an object")
        kind = entry.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            raise WorkflowError(f"action {index} needs a kind")
        name = kind.strip().lower()
        action: dict[str, Any] = {
            key: value for key, value in entry.items() if key not in ("index",)
        }
        action["kind"] = name
        action["index"] = index
        action["resolved"] = name in ACTION_KINDS

        if not action["resolved"]:
            action["reason"] = (
                f"{name!r} is not one of the four action kinds the research names "
                f"({', '.join(ACTION_KINDS)}). It is stored and reported unresolved, "
                f"because the source says 'and more!' - it is not dropped."
            )

        if name == "update_field":
            field_name = action.get("field")
            if not isinstance(field_name, str) or not field_name.strip():
                raise WorkflowError(
                    f"action {index} is an update_field and needs the contact property "
                    f"to write, as 'field'"
                )
            action["field"] = field_name.strip()
        elif name == "change_stage":
            stage = action.get("stage")
            if not isinstance(stage, str) or not stage.strip():
                raise WorkflowError(
                    f"action {index} is a change_stage and needs a stage, as 'stage'"
                )
            action["stage"] = stage.strip()
            kind_of_stage = str(action.get("stage_kind") or "deal_stage").strip().lower()
            if kind_of_stage not in STAGE_KINDS:
                raise WorkflowError(
                    f"action {index} has stage_kind {kind_of_stage!r}; expected one of "
                    f"{', '.join(STAGE_KINDS)}. A lifecyclestage obeys the forward-only "
                    f"rule and a deal_stage does not, so the two cannot be guessed."
                )
            action["stage_kind"] = kind_of_stage
        elif name == "send_email":
            action.setdefault("template", "")
        elif name == "slack_notification":
            action.setdefault("channel", "")

        actions.append(action)

    if not actions:
        raise NoActions(
            "a workflow needs at least one action. A filter with nothing to do would "
            "enrol contacts and do nothing."
        )
    return actions


# --------------------------------------------------------------------------- #
# The definition
# --------------------------------------------------------------------------- #


def normalise_workflow(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a workflow definition as written, or refuse it.

    The checks are in the order the research describes them: the name, then the
    contact-based rule (step 3), then the trigger (step 4), then the criteria
    (steps 5 and 6), then the actions (step 7). Refusing in that order means the
    message a caller gets is about the first thing that is wrong, which is the one
    they are looking at.

    **Keys this function does not know are kept.** That is the schema-flexibility
    rule, and a validator that dropped them would break it in the most expensive
    way: a team adds ``owner_team`` to its workflow, sees the value echoed back in
    the response, and then finds it missing from the stored record. So the researched
    keys are normalised *over* the caller's payload rather than beside it, and an
    unrecognised key rides along untouched - filterable the moment it is written,
    because ``find()`` resolves it through the dynamic index.
    """
    if not isinstance(payload, Mapping):
        raise WorkflowError("a workflow must be a JSON object")

    name = str(payload.get("name") or "").strip()
    if not name:
        raise WorkflowError("a workflow needs a name")

    enrollment_type = str(payload.get("enrollment_type") or "contact").strip().lower()
    if enrollment_type not in ENROLLMENT_TYPES:
        raise NotContactBased(
            f"{enrollment_type!r} is not a supported workflow type. The research states "
            f"the constraint directly: 'Dock only supports Contact based workflows "
            f"since the activities are tied to the contact record.' Every one of the "
            f"five filter families reads a contact's own activity, so a workflow that "
            f"is not contact-based has nothing to evaluate."
        )

    raw_trigger = payload.get("trigger")
    if not isinstance(raw_trigger, Mapping):
        raise WorkflowError("a workflow needs a trigger")
    mode = require_trigger_mode(raw_trigger.get("mode"))
    integration = str(raw_trigger.get("integration") or DEFAULT_INTEGRATION).strip().lower()
    if not integration:
        raise WorkflowError("the trigger needs an integration to filter on")

    criteria = parse_criteria_list(raw_trigger.get("criteria"))
    if not criteria:
        raise WorkflowError(
            "a workflow needs at least one filter. The trigger is 'When filter criteria "
            "is met', so a workflow with no criteria is a rule that fires on all "
            "activity, which is not what the research describes."
        )

    actions = normalise_actions(payload.get("actions"))

    lookback = payload.get("lookback_days")
    if lookback is not None:
        try:
            lookback_days = int(lookback)
        except (TypeError, ValueError) as exc:
            raise WorkflowError("lookback_days must be a whole number of days") from exc
        if lookback_days < 0:
            raise WorkflowError("lookback_days cannot be negative")
    else:
        lookback_days = None

    return {
        # Start from the caller's payload so an unrecognised key is kept, then
        # overwrite every key this package owns with its normalised value.
        **{
            key: value
            for key, value in payload.items()
            if key
            not in ("status", "published_at", "enrollments", "last_enrolled_at", "revision")
        },
        "name": name,
        "description": str(payload.get("description") or "").strip(),
        "enrollment_type": enrollment_type,
        "status": DRAFT,
        "trigger": {
            **{
                key: value
                for key, value in raw_trigger.items()
                if key not in ("mode", "integration", "criteria")
            },
            "mode": mode,
            "integration": integration,
            "criteria": criteria,
        },
        "actions": actions,
        # None means "no limit": the research says the filter fires continuously on
        # matching activity and never mentions a window, so the build does not
        # invent one. A definition may narrow it, which is a choice rather than a
        # default. Recorded as the `lookback` inference.
        "lookback_days": lookback_days,
        "delivery": str(payload.get("delivery") or "filter").strip().lower(),
        "rooms": [str(room) for room in (payload.get("rooms") or []) if str(room).strip()],
        "enrollments": 0,
        "last_enrolled_at": None,
        "published_at": None,
    }


def amendable_fields() -> frozenset[str]:
    """The fields a PATCH may touch.

    Not enforced by refusing unknown keys - payloads are arbitrary JSON and a team
    adding a field must not need a change to this file. It is served so a client
    can show what an edit means, and so a caller that sends a lifecycle field
    (``status``, ``published_at``) can be told that publishing is its own route
    rather than a field to write.
    """
    return frozenset(
        {
            "name",
            "description",
            "enrollment_type",
            "trigger",
            "actions",
            "lookback_days",
            "delivery",
            "rooms",
        }
    )


def apply_patch(current: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Merge a patch into a definition and re-validate the whole thing.

    Re-validating rather than patching field by field is deliberate: an amendment
    that changes the trigger has to be checked against the whole trigger, and a
    merge that validated only the changed key would let a caller switch a
    contact-based workflow to something else by editing one nested field.
    """
    if not isinstance(patch, Mapping):
        raise WorkflowError("a patch must be a JSON object")

    reserved = {
        "id",
        "status",
        "published_at",
        "enrollments",
        "last_enrolled_at",
        "revision",
        "created_at",
        "updated_at",
        # Derived by the read path, not part of the written definition.
        "warnings",
        "flagged",
    }
    for key in patch:
        if key in reserved:
            raise WorkflowError(
                f"{key!r} is not an amendable field. Publishing and unpublishing are "
                f"their own routes, because they are what makes a workflow start or "
                f"stop firing."
            )

    merged = {key: value for key, value in current.items() if key not in reserved}
    for key, value in patch.items():
        if key == "trigger" and isinstance(value, Mapping) and isinstance(current.get("trigger"), Mapping):
            merged["trigger"] = {**current["trigger"], **value}
        else:
            merged[key] = value
    # `status` and the counters are not part of the written definition; they are
    # added back so a re-validated definition keeps them.
    merged["status"] = current.get("status", DRAFT)
    merged["published_at"] = current.get("published_at")
    merged["enrollments"] = int(current.get("enrollments") or 0)
    merged["last_enrolled_at"] = current.get("last_enrolled_at")
    return normalise_workflow(merged)


def lint_workflow(definition: Mapping[str, Any], integration: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Everything about a definition a person would want to know before publishing.

    The integration findings are the researched step 1 - "Verify the HubSpot
    integration is on and the workspace is connected to a deal/account" - turned
    into something a definition can carry, so a workflow whose integration is off
    says so rather than sitting in a list looking ready.

    Publishing is still *refused* when the integration is missing or off; the lint
    exists so a client can show the problem before the caller trips over it, and so
    a draft can be inspected.
    """
    warnings: list[dict[str, Any]] = []

    if integration is None:
        warnings.append(
            {
                "code": "integration_missing",
                "severity": "warning",
                "field": "trigger.integration",
                "message": (
                    f"step 1 of the researched flow is to verify the integration is on, "
                    f"and no integration is registered as "
                    f"{definition.get('trigger', {}).get('integration')!r}."
                ),
            }
        )
    elif not integration.get("enabled"):
        warnings.append(
            {
                "code": "integration_disabled",
                "severity": "warning",
                "field": "trigger.integration",
                "message": (
                    f"integration {integration.get('id')} is registered but switched off, "
                    f"so this workflow cannot be published."
                ),
            }
        )

    for criteria in definition.get("trigger", {}).get("criteria") or []:
        warnings.extend(lint_criteria(criteria))

    for action in definition.get("actions") or []:
        if not action.get("resolved"):
            warnings.append(
                {
                    "code": "action_unresolved",
                    "severity": "warning",
                    "field": f"actions.{action.get('index')}",
                    "message": str(action.get("reason") or "this action kind is not resolved by this build."),
                }
            )
        elif action.get("kind") == "update_field" and not action.get("value"):
            warnings.append(
                {
                    "code": "action_writes_empty",
                    "severity": "info",
                    "field": f"actions.{action.get('index')}",
                    "message": (
                        f"this action writes {action.get('field')!r} with no value and no "
                        f"source, so it would clear the property."
                    ),
                }
            )

    if definition.get("delivery") not in DELIVERY_PATHS:
        warnings.append(
            {
                "code": "unknown_delivery_path",
                "severity": "warning",
                "field": "delivery",
                "message": (
                    f"delivery {definition.get('delivery')!r} is not one of "
                    f"{', '.join(DELIVERY_PATHS)}; the researched paths are the filter "
                    f"and the documented webhook alternative."
                ),
            }
        )

    return warnings


def requires_integration(definition: Mapping[str, Any]) -> str:
    """The integration a definition's trigger depends on."""
    return str((definition.get("trigger") or {}).get("integration") or DEFAULT_INTEGRATION)
