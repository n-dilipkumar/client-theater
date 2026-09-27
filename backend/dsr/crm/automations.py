"""Automations: the no-code CRM path.

Sourced behaviour, from the research:

* An automation is **When** (the trigger event) and **Do this** (the action),
  with more actions added beneath the first.
* The library holds the automations, each with a name and a description, and a
  toggle: "Automations must be turned 'ON' via the toggle in the automation
  library to be assigned to a template."
* A Recommended Automation is a starting point you add with a click.
* "Automations can only be applied to Opportunity templates with Salesforce at
  this time", so the CRM and its object matter and are recorded.
* "We only support Salesforce automations in production. Automations cannot be
  set up in a sandbox environment." So ``environment: sandbox`` is rejected
  rather than warned about: the product the research describes cannot honour
  it, and a rule that can never run is worse than a rule that will not save.

Inferred, not sourced
---------------------
The action vocabulary. The research names actions in prose ("update
opportunity amount", "sync page URLs", "update opportunity fields from the Page
Details") and explicitly makes no claim about any Salesforce endpoint, so there
is no sourced way to *execute* a write. What this module does instead is
resolve the field mapping against the room's facts and record exactly what
would be written, in the Activity Log. The mapping is arbitrary JSON, so a team
that does have a CRM client can add a real action kind without a migration.

``source`` on every write
-------------------------
Like the subscription book, every method that writes takes a required
keyword-only ``source`` naming the route that served the request. The branch
hardcoded ``"automation.create"``, ``"automation.update"``,
``"automation.delete"`` and ``"automation.run"``, which name an internal verb
rather than anything a reader of the audit log can follow back to a request.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.crm.errors import CrmError
from dsr.crm.vocabulary import PRESETS, lint_mapping, require_event, resolve_mapping
from dsr.db.audited import RecordNotFound
from dsr.store import RecordStore

COLLECTION = "crm_automation"

# The only environment automations are supported in. Documented as a hard
# product constraint rather than a deployment preference.
SUPPORTED_ENVIRONMENTS = ("production",)

# CRMs the research has a help article for. Sibling articles exist for each.
KNOWN_CRMS = (
    "salesforce",
    "hubspot",
    "pipedrive",
    "dynamics365",
    "zoho",
)

# Action kinds this build knows how to resolve. A kind outside this set is
# stored and reported as skipped rather than dropped, so a team that adds one
# server-side can still create the rule from the generic records API.
RESOLVABLE_ACTIONS = ("update_fields",)


class AutomationError(CrmError):
    """Raised when an automation cannot be created as written."""


def _validate(spec: Mapping[str, Any]) -> dict[str, Any]:
    name = str(spec.get("name") or "").strip()
    if not name:
        raise AutomationError("name is required")

    trigger = dict(spec.get("trigger") or {})
    require_event(trigger.get("event"))

    environment = str(spec.get("environment") or "production").strip().lower()
    if environment not in SUPPORTED_ENVIRONMENTS:
        raise AutomationError(
            f"automations cannot be set up in {environment!r}; "
            f"supported environments: {', '.join(SUPPORTED_ENVIRONMENTS)}"
        )

    actions = spec.get("actions") or []
    if not isinstance(actions, list) or not actions:
        raise AutomationError("an automation needs at least one action")
    for action in actions:
        if not isinstance(action, Mapping) or not action.get("kind"):
            raise AutomationError("every action needs a kind")

    return {
        "name": name,
        "description": str(spec.get("description") or ""),
        "crm": str(spec.get("crm") or "salesforce").strip().lower(),
        "environment": environment,
        "enabled": bool(spec.get("enabled", True)),
        "trigger": {
            "event": trigger["event"],
            # Template scoping. Empty means "every room", which is how an
            # automation behaves before anyone assigns it to a template.
            "template_ids": [str(t) for t in (trigger.get("template_ids") or [])],
        },
        "actions": [dict(action) for action in actions],
        "preset_id": spec.get("preset_id") or None,
        "runs": 0,
        "errors": 0,
        "last_run_at": None,
    }


def lint(
    automation: Mapping[str, Any],
    field_types: Mapping[str, str],
    hidden_fields: frozenset[str] = frozenset(),
    facts: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Compatibility warnings for an automation's field mappings.

    Cheap enough to run on every read, so the editor and the Activity Log can
    both show the same list without a second source of truth. Pass ``facts`` to
    include the per-room ``unresolved_fact`` check; omit it for a mapping-only
    view that does not need a room to exist.
    """
    warnings: list[dict[str, Any]] = []
    for action in automation.get("actions") or []:
        if action.get("kind") not in RESOLVABLE_ACTIONS:
            warnings.append(
                {
                    "code": "unknown_action_kind",
                    "severity": "warning",
                    "target": str(action.get("kind") or ""),
                    "field": None,
                    "message": (
                        f"No handler is registered for action kind {action.get('kind')!r}, "
                        "so this action will be skipped. Create the rule anyway if the "
                        "handler is coming from another team."
                    ),
                }
            )
            continue
        warnings.extend(
            lint_mapping(
                action.get("fields") or {},
                field_types,
                facts,
                hidden_fields=hidden_fields,
            )
        )
    return warnings


def run(
    automation: Mapping[str, Any],
    facts: Mapping[str, Any],
    field_types: Mapping[str, str] | None = None,
    hidden_fields: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Resolve an automation's actions against a room's facts.

    Returns ``{resolved, skipped, warnings}``. Nothing is written anywhere: the
    resolved payload is what the Activity Log records, and it is what a CRM
    client would send.
    """
    resolved: dict[str, Any] = {}
    skipped: list[dict[str, Any]] = []

    for action in automation.get("actions") or []:
        kind = action.get("kind")
        if kind not in RESOLVABLE_ACTIONS:
            skipped.append({"kind": kind, "reason": "no handler registered for this action kind"})
            continue
        fields = action.get("fields") or {}
        for target, value in resolve_mapping(fields, facts).items():
            resolved[target] = value

    return {
        "resolved": resolved,
        "skipped": skipped,
        "warnings": lint(automation, field_types or {}, hidden_fields, facts),
    }


class AutomationBook:
    """The automations library over the audited store."""

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    def create(
        self, spec: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        return self.store.create(COLLECTION, _validate(spec), actor=actor, source=source)

    def list(self) -> list[dict[str, Any]]:
        return self.store.list(COLLECTION, limit=200)

    def get(self, automation_id: str) -> dict[str, Any] | None:
        record = self.store.get(automation_id)
        if record is None or record["collection"] != COLLECTION:
            return None
        return record

    def update(
        self,
        automation_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Update an automation, or flip its on/off toggle.

        The toggle lives in the same record as the rule because the research
        ties the two together: an automation is only assigned to its templates
        while it is on, so "off" is a property of the rule, not a separate flag
        that can drift out of step with it.
        """
        current = self.get(automation_id)
        if current is None:
            raise RecordNotFound(automation_id)

        merged = {**current["data"], **dict(patch)}
        # Re-validate so a patch cannot smuggle in a bad event or a sandbox
        # environment the way a fresh create could not.
        _validate(merged)
        return self.store.update(automation_id, patch, actor=actor, source=source)

    def delete(
        self, automation_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        if self.get(automation_id) is None:
            raise RecordNotFound(automation_id)
        return self.store.delete(automation_id, actor=actor, source=source)

    def matching(self, event: str, *, template_id: str | None = None) -> list[dict[str, Any]]:
        """Enabled automations that should run for this event.

        Template scoping is the documented assignment rule: a scoped automation
        only runs for the templates it names, and a disabled one never runs.
        """
        results = []
        for record in self.list():
            data = record["data"]
            if not data.get("enabled", True):
                continue
            if (data.get("trigger") or {}).get("event") != event:
                continue
            scoped = (data.get("trigger") or {}).get("template_ids") or []
            if scoped and (template_id is None or str(template_id) not in scoped):
                continue
            results.append(record)
        return results

    def record_outcome(
        self, automation_id: str, *, ok: bool, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        from dsr.db.audited import utcnow

        current = self.get(automation_id)
        if current is None:
            raise RecordNotFound(automation_id)
        data = current["data"]
        return self.store.update(
            automation_id,
            {
                "runs": int(data.get("runs") or 0) + 1,
                "errors": int(data.get("errors") or 0) + (0 if ok else 1),
                "last_run_at": utcnow(),
            },
            actor=actor,
            source=source,
        )


def from_preset(preset_id: str, *, name: str | None = None) -> dict[str, Any]:
    """Build a createable spec from a Recommended Automation."""
    for preset in PRESETS:
        if preset["id"] == preset_id:
            return {
                "name": name or preset["name"],
                "description": preset["description"],
                "crm": preset["crm"],
                "trigger": dict(preset["trigger"]),
                "actions": [dict(action) for action in preset["actions"]],
                "preset_id": preset["id"],
            }
    raise AutomationError(
        f"unknown preset {preset_id!r}; see the vocabulary endpoint for the ones that exist"
    )


def summarise(
    record: Mapping[str, Any],
    field_types: Mapping[str, str] | None = None,
    hidden_fields: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """The list shape, including the compatibility warnings a rep needs."""
    data = record.get("data") or {}
    trigger = data.get("trigger") or {}
    return {
        "id": record.get("id"),
        "name": data.get("name"),
        "description": data.get("description", ""),
        "crm": data.get("crm"),
        "environment": data.get("environment", "production"),
        "enabled": bool(data.get("enabled", True)),
        "preset_id": data.get("preset_id"),
        "trigger": trigger,
        "event": trigger.get("event"),
        "template_ids": trigger.get("template_ids") or [],
        "actions": data.get("actions") or [],
        "runs": int(data.get("runs") or 0),
        "errors": int(data.get("errors") or 0),
        "last_run_at": data.get("last_run_at"),
        "updated_at": record.get("updated_at"),
        "warnings": lint(data, field_types or {}, hidden_fields),
    }
