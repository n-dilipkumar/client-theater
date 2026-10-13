"""CRM entities: the four object types, the sync cadence, and both permission gates.

The issue requires the source of the account, contact, deal and lead records to be
confirmed before this workflow depends on it. A survey of the shipped code found
no collection holding any of them, so Jev decided this workflow owns its own
(audit ``jev-20261013T111405-12092-45997``, confidence 1.00) rather than reading a
collection another feature might reshape.

Four researched rules are enforced here rather than described, because a
description does not stop a call.

**The Engage licence gate.** "The selected user must have an Engage license. If
they don't, the automation won't run." :func:`assert_engaged` refuses an assignee
without the licence.

**The parent-account gate.** "If the automation selects a contact without a parent
account, the automation will fail to run." :func:`assert_parent_account` refuses a
contact with no account behind it.

**The personal-flow gate.** "Personal flows cannot be used with automations."
:func:`assert_not_personal` refuses a personal flow offered to an automation.

**The cadence rule.** "we recommend updating between every 1-5 minutes", and
``modificationDate`` is mandatory on updates. :func:`validate_sync_run` enforces
both: a cadence outside the range is refused, and an update with no modification
date is refused.

Nothing here opens a socket. The Gong endpoints are recorded on the sync run so a
reviewer can read the researched call without a network request having happened.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.meeting_followup import vocabulary as vocab
from dsr.meeting_followup.errors import (
    AssigneeNotLicensed,
    ContactWithoutParentAccount,
    CrmRecordInvalid,
    PersonalFlowRefused,
)


def normalise_object_type(raw: Any) -> str:
    """Fold a caller-supplied object type into the researched four.

    Case-folded and stripped, because the evidence spells the types in caps
    ("ACCOUNT|CONTACT|DEAL|LEAD") and a form will not.

    Raises :class:`~dsr.meeting_followup.errors.CrmRecordInvalid` for anything
    else, rather than defaulting: a typo'd object type that silently became a
    Lead would put a call in the wrong place.
    """
    text = str(raw or "").strip().upper()
    if not text:
        raise CrmRecordInvalid(
            "object_type is required",
            {"object_type": f"one of: {', '.join(vocab.OBJECT_TYPES)}"},
        )
    if text not in vocab.OBJECT_TYPES:
        raise CrmRecordInvalid(
            f"object_type {text!r} is not one of the researched object types",
            {"object_type": f"one of: {', '.join(vocab.OBJECT_TYPES)}"},
        )
    return text


def collection_for(object_type: str) -> str:
    """The collection one object type reads.

    Resolved from :data:`~dsr.meeting_followup.vocabulary.COLLECTION_BY_OBJECT_TYPE`
    rather than built from a string, so a caller cannot name a collection this
    workflow does not own.
    """
    return vocab.COLLECTION_BY_OBJECT_TYPE[normalise_object_type(object_type)]


def normalise_record(object_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one CRM entity payload into a normalised dict.

    The researched mandatory field is enforced here rather than at the route, so a
    record created by the seed cannot skip a rule the HTTP path enforces.
    """
    kind = normalise_object_type(object_type)
    data = dict(payload or {})
    external = str(data.get("crm_id") or data.get("id") or "").strip()
    if not external:
        raise CrmRecordInvalid(
            "crm_id is required: the Gong object id is how an upload is matched",
            {"crm_id": "required"},
        )

    modification_date = str(data.get(vocab.MODIFICATION_DATE_FIELD) or "").strip()
    if data.get("operation") == vocab.MODIFICATION_DATE_MANDATORY_ON and not modification_date:
        raise CrmRecordInvalid(
            f"{vocab.MODIFICATION_DATE_FIELD} is mandatory on updates",
            {vocab.MODIFICATION_DATE_FIELD: "required on an update"},
        )

    body: dict[str, Any] = {
        "crm_id": external,
        "object_type": kind,
        "name": str(data.get("name") or "").strip(),
        "fields": dict(data.get("fields") or {}),
    }
    if modification_date:
        body[vocab.MODIFICATION_DATE_FIELD] = modification_date
    if kind == vocab.CONTACT_OBJECT_TYPE:
        # Read by the parent-account gate, and stored even when empty so a missing
        # account is visible as an absent field rather than as an absent record.
        body[vocab.PARENT_ACCOUNT_FIELD] = str(
            data.get(vocab.PARENT_ACCOUNT_FIELD) or data.get("account_id") or ""
        ).strip()
    return body


# --------------------------------------------------------------------------- #
# Gate one: the Engage licence
# --------------------------------------------------------------------------- #


def has_engage_license(user: Mapping[str, Any]) -> bool:
    """Whether a directory user holds an Engage licence.

    Absent means no. The research's sentence is a refusal: "If they don't, the
    automation won't run", so a user record that does not say yes has not said
    yes.
    """
    return bool(user.get(vocab.ENGAGE_LICENSE_FIELD))


def assert_engaged(user: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Refuse an assignee with no Engage licence.

    Returns the user unchanged when the gate passes, so a caller can write
    ``assignee = assert_engaged(find_user(email))`` and read it as one step.
    """
    if user is None:
        raise AssigneeNotLicensed(
            f"No directory user for that assignee. {vocab.ENGAGE_LICENSE_QUOTE}",
            {"assignee": "must name a user with an Engage license"},
        )
    if not has_engage_license(user):
        email = str(user.get("email") or "(no email)")
        raise AssigneeNotLicensed(
            f"{email} has no Engage license, so the automation will not run. "
            f"{vocab.ENGAGE_LICENSE_QUOTE}",
            {"assignee": "must have an Engage license"},
        )
    return user


# --------------------------------------------------------------------------- #
# Gate two: the parent account
# --------------------------------------------------------------------------- #


def has_parent_account(record: Mapping[str, Any]) -> bool:
    """Whether a contact names a parent account."""
    return bool(str(record.get(vocab.PARENT_ACCOUNT_FIELD) or "").strip())


def assert_parent_account(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """Refuse a contact with no parent account.

    Only meaningful for a contact. An account, a deal and a lead have no parent,
    so the gate is skipped for them rather than passing a field that does not
    exist.
    """
    if normalise_object_type(record.get("object_type")) != vocab.CONTACT_OBJECT_TYPE:
        return record
    if has_parent_account(record):
        return record
    raise ContactWithoutParentAccount(
        "The selected contact has no parent account, so the automation will fail "
        f"to run. {vocab.PARENT_ACCOUNT_QUOTE}",
        {"crm_id": str(record.get("crm_id") or ""), vocab.PARENT_ACCOUNT_FIELD: "required"},
    )


# --------------------------------------------------------------------------- #
# Gate three: personal flows
# --------------------------------------------------------------------------- #


def is_personal(flow: Mapping[str, Any]) -> bool:
    """Whether a flow is a personal flow."""
    return bool(flow.get(vocab.PERSONAL_FLOW_FIELD))


def assert_not_personal(flow: Mapping[str, Any]) -> Mapping[str, Any]:
    """Refuse a personal flow offered to an automation.

    Unlike the other two gates this one checks the flow rather than the assigner,
    so it is called from the automation create and the flow assign alike.
    """
    if not is_personal(flow):
        return flow
    raise PersonalFlowRefused(
        f"{vocab.PERSONAL_FLOW_QUOTE} Flow {flow.get('flow_id') or flow.get('id')!r} is personal.",
        {"flow_id": "must not be a personal flow"},
    )


# --------------------------------------------------------------------------- #
# The sync cadence and the modification date
# --------------------------------------------------------------------------- #


def validate_sync_run(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one CRM sync run.

    Both researched rules land here. The cadence is refused outside one to five
    minutes, because "we recommend updating between every 1-5 minutes" is the only
    statement about frequency the evidence makes and a run at a cadence nobody
    recommended should not read as one this workflow performed.

    The modification date is mandatory on an update and forbidden to be absent on
    a create, which is what the evidence says: "modificationDate is mandatory on
    updates".
    """
    operation = str(payload.get("operation") or "update").strip().lower()
    if operation not in ("create", vocab.MODIFICATION_DATE_MANDATORY_ON):
        raise CrmRecordInvalid(
            f"operation {operation!r} is not create or update",
            {"operation": "create or update"},
        )

    try:
        cadence = int(payload.get("cadence_minutes"))
    except (TypeError, ValueError) as exc:
        raise CrmRecordInvalid(
            "cadence_minutes must be a whole number of minutes",
            {"cadence_minutes": "required"},
        ) from exc
    low, high = vocab.CRM_SYNC_RECOMMENDED_RANGE
    if cadence < low or cadence > high:
        raise CrmRecordInvalid(
            f"cadence_minutes must be between {low} and {high}; the vendor "
            "recommends updating between every 1-5 minutes",
            {"cadence_minutes": f"{low} to {high}"},
        )

    modification_date = str(payload.get(vocab.MODIFICATION_DATE_FIELD) or "").strip()
    if operation == vocab.MODIFICATION_DATE_MANDATORY_ON and not modification_date:
        raise CrmRecordInvalid(
            f"{vocab.MODIFICATION_DATE_FIELD} is mandatory on updates",
            {vocab.MODIFICATION_DATE_FIELD: "required on an update"},
        )
    if operation == "create" and modification_date:
        raise CrmRecordInvalid(
            f"{vocab.MODIFICATION_DATE_FIELD} is a field of an update, not a create",
            {vocab.MODIFICATION_DATE_FIELD: "omit on a create"},
        )

    records = payload.get("records") or []
    if not isinstance(records, list):
        raise CrmRecordInvalid(
            "records must be a list",
            {"records": "a list of entity payloads"},
        )

    return {
        "operation": operation,
        "cadence_minutes": cadence,
        "object_type": normalise_object_type(payload.get("object_type")),
        **({vocab.MODIFICATION_DATE_FIELD: modification_date} if modification_date else {}),
        "records": [normalise_record(payload.get("object_type"), item) for item in records],
        "endpoint": vocab.CRM_ENTITIES_ENDPOINT,
    }


def entities_request(run: Mapping[str, Any]) -> dict[str, Any]:
    """The outbound request a sync run would carry.

    Recorded on the run so a reviewer can read the researched call without a
    network request having happened. ``modificationDate`` appears on an update and
    not on a create, which is the rule :func:`validate_sync_run` enforced.
    """
    body: dict[str, Any] = {
        "records": list(run.get("records") or []),
    }
    if run.get(vocab.MODIFICATION_DATE_FIELD):
        body[vocab.MODIFICATION_DATE_FIELD] = run[vocab.MODIFICATION_DATE_FIELD]
    return {
        "method": "POST",
        "endpoint": f"{vocab.CRM_ENTITIES_ENDPOINT}?objectType={run['object_type']}",
        "headers": {"Content-Type": "application/json"},
        "body": body,
        "sent": False,
        "note": (
            "This product is not a Gong client. The request is recorded so the "
            "researched call is reviewable, and it is not sent."
        ),
    }
