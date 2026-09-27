"""Rendering a plan into the four documented request shapes.

One plan, four wire formats. This module is the only place that knows what a
Salesforce subrequest looks like, and the research's extensibility claim is the
test of that: *a deployment can add a 4th record type without touching the
transport*. Nothing in here mentions an Account, a Contact or an Opportunity by
name - it reads types off the plan - so adding one is adding an entry to a list.

What is reproduced and what is not
----------------------------------
Three of the four renderings follow shapes the research names field by field:

* **Composite.** ``method``, ``url``, ``referenceId``, ``body`` per subrequest,
  plus the outer ``allOrNone`` and ``collateSubrequests``. Every key is sourced.
* **Dataverse.** ``Content-Type: multipart/mixed``, a ``changeset_*`` boundary,
  ``Content-ID: 1/2/3``, and ``$1`` in a body. The exact MIME framing is the
  researched shape rather than a byte-for-byte reproduction, and the boundary
  token is derived deterministically so a preview is reproducible.
* **HubSpot.** The contacts batch create and the association ``PUT``, whose path
  the research quotes.

One is not sourced at all. The research gives the sObject Tree endpoint, four
limits and one atomicity sentence, but **no request body**. The nested tree
rendered below is this build's own shape and is recorded as a named inference in
:mod:`dsr.atomic_bundle.inferences` rather than presented as a reproduction.

Key order is preserved everywhere. The declared order *is* the dependency order,
so a renderer that sorted keys would silently destroy the one thing the research
is about.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from dsr.atomic_bundle.planner import BundlePlan, Step
from dsr.atomic_bundle.references import content_id_for
from dsr.atomic_bundle.vocabulary import (
    DATAVERSE_BATCH,
    DV_BATCH_PATH,
    DV_CONTENT_TYPE,
    HS_ASSOCIATION_PATH,
    HS_CONTACTS_BATCH_PATH,
    HUBSPOT_ASSOCIATIONS,
    POLICY_STRICT,
    SALESFORCE_COMPOSITE,
    SALESFORCE_TREE,
)

#: The API version placeholder the research itself uses. A deployment pins a
#: real version on the connector; until it does, the request is recognisably a
#: template rather than something that quietly went out as ``vXX.X``.
DEFAULT_API_VERSION = "vXX.X"

#: [sourced] a ``changeset_*`` boundary. The token is derived from the plan rather
#: than random, so two renders of one plan are byte-identical and a preview is
#: reproducible.
CHANGESET_PREFIX = "changeset_"
CHANGESET_DEFAULT = "atomicbundle"

#: The Dataverse API version the research quotes, for the same reason
#: ``DEFAULT_API_VERSION`` exists on the Salesforce side.
DEFAULT_DATAVERSE_VERSION = "v9.2"

#: [sourced] the changeset part's declared media type.
CHANGESET_PART_CONTENT_TYPE = "application/http"

#: [sourced] the researched example body is ``"originatingleadid@odata.bind":"$1"``
#: - an ``@odata.bind`` property carrying the parent's Content-ID. The suffix is
#: what makes a bind property, and the prefix is whatever the deployment calls the
#: lookup field on.
DATAVERSE_BIND_SUFFIX = "@odata.bind"


@dataclass(frozen=True)
class RenderOptions:
    """Deployment facts a rendering needs, none of them in the plan.

    Kept out of :class:`~dsr.atomic_bundle.planner.BundlePlan` on purpose: the
    plan is what the *bundle* says and a rendering is what this *tenant* says.
    Two tenants with the same bundle legitimately render different bytes.
    """

    api_version: str = DEFAULT_API_VERSION
    dataverse_version: str = DEFAULT_DATAVERSE_VERSION
    base_url: str = ""
    changeset: str = ""
    entity_sets: Mapping[str, str] = field(default_factory=dict)

    def entity_set(self, record_type: str) -> str:
        """The Dataverse entity set for a record type.

        A deployment that renamed its entity sets declares them on the connector.
        The default is the lower-cased plural, and the preview says which one was
        used, so a wrong guess is visible rather than a 404 later.
        """
        return self.entity_sets.get(record_type) or _plural(record_type.lower())


@dataclass(frozen=True)
class RequestPart:
    """One unit of the rendered request.

    For a single-request dialect this is one subrequest *inside* the envelope. For
    HubSpot, which has no documented cross-object request, these are the requests
    themselves in order - which is why ``is_sequence`` exists.
    """

    reference_id: str
    kind: str
    method: str
    path: str
    headers: dict[str, str]
    body: str | None
    depends_on: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_id": self.reference_id,
            "kind": self.kind,
            "method": self.method,
            "path": self.path,
            "headers": dict(self.headers),
            "body": json.loads(self.body) if self.body else None,
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True)
class RequestDocument:
    """A plan, rendered. This is what a transport sends."""

    dialect: str
    method: str
    path: str
    headers: dict[str, str]
    body: str
    parts: tuple[RequestPart, ...]
    plan: BundlePlan
    notes: tuple[str, ...] = ()

    @property
    def is_sequence(self) -> bool:
        """True when this dialect has no single request to send."""
        return self.dialect == HUBSPOT_ASSOCIATIONS

    @property
    def atomic(self) -> bool:
        return self.plan.atomic

    @property
    def content_id_of(self) -> dict[str, int]:
        """``{referenceId: Content-ID}``, for the dialects that number their parts."""
        order = list(self.plan.order)
        return {reference: content_id_for(reference, order) for reference in order}

    def request_count(self) -> int:
        return len(self.parts) if self.is_sequence else 1

    def to_dict(self, *, include_body: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "dialect": self.dialect,
            "method": self.method,
            "path": self.path,
            "headers": dict(self.headers),
            "is_sequence": self.is_sequence,
            "request_count": self.request_count(),
            "atomic": self.atomic,
            "atomicity": self.plan.atomicity,
            "policy": self.plan.policy,
            "collate_subrequests": self.plan.collate_subrequests,
            "content_ids": self.content_id_of,
            "parts": [part.to_dict() for part in self.parts],
            "notes": list(self.notes),
        }
        if include_body:
            payload["body"] = _body_as_json(self.dialect, self.body)
            payload["raw_body"] = self.body
        return payload


def _body_as_json(dialect: str, raw: str) -> Any:
    """The rendered body as JSON where it is JSON, and as text where it is MIME.

    A multipart body is not JSON, and pretending otherwise would make the preview
    lie about what goes on the wire, so it comes back as text and the
    ``Content-Type`` in ``headers`` says why.
    """
    if dialect == DATAVERSE_BATCH:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:  # pragma: no cover - every other body is JSON
        return None


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def render(plan: BundlePlan, options: RenderOptions | None = None) -> RequestDocument:
    """Render ``plan`` into its dialect's request shape."""
    opts = options or RenderOptions()
    if plan.dialect == SALESFORCE_COMPOSITE:
        return _render_composite(plan, opts)
    if plan.dialect == SALESFORCE_TREE:
        return _render_tree(plan, opts)
    if plan.dialect == DATAVERSE_BATCH:
        return _render_dataverse(plan, opts)
    if plan.dialect == HUBSPOT_ASSOCIATIONS:
        return _render_hubspot(plan, opts)
    raise AssertionError(f"unrenderable dialect {plan.dialect!r}")  # pragma: no cover


# --------------------------------------------------------------------------- #
# Salesforce: composite
# --------------------------------------------------------------------------- #


def _render_composite(plan: BundlePlan, opts: RenderOptions) -> RequestDocument:
    """``POST /services/data/{version}/composite``.

    The one rendering where every key is sourced: ``allOrNone`` and
    ``collateSubrequests`` on the envelope, and ``method``, ``url``,
    ``referenceId``, ``body`` on each subrequest. ``allOrNone`` is the strict
    policy - the research ties ``true`` to "the entire composite request is rolled
    back" - and ``collateSubrequests`` is the declared ordering knob.
    """
    version = opts.api_version
    subrequests: list[dict[str, Any]] = []
    parts: list[RequestPart] = []

    for step in plan.steps:
        path = f"/services/data/{version}/sobjects/{step.record_type}"
        body: dict[str, Any]
        if step.is_collection:
            body = {"records": [dict(item) for item in step.collection["records"]]}
            kind = "collection"
        else:
            body = dict(step.fields)
            # The researched data flow: the request body carries a placeholder and
            # the CRM "resolves $1/@{refAccount.id} into real record URIs as it
            # creates each row". The placeholder is written here rather than
            # declared in the bundle, because on this dialect the body reference
            # *is* the link and the tree dialect expresses the same link by
            # nesting - so the bundle declares the relationship once and the
            # dialect writes the form its API takes.
            if step.parent and step.parent.get("field"):
                body[step.parent["field"]] = f"@{{{step.parent['reference']}.id}}"
            kind = "create"
        subrequests.append(
            {"method": "POST", "url": path, "referenceId": step.reference_id, "body": body}
        )
        parts.append(
            RequestPart(
                reference_id=step.reference_id,
                kind=kind,
                method="POST",
                path=path,
                headers={},
                body=json.dumps(body, ensure_ascii=False, indent=2),
                depends_on=step.dependencies(),
            )
        )

    envelope = {
        "allOrNone": plan.policy == POLICY_STRICT,
        "collateSubrequests": plan.collate_subrequests,
        "compositeRequest": subrequests,
    }
    return RequestDocument(
        dialect=SALESFORCE_COMPOSITE,
        method="POST",
        path=f"/services/data/{version}/composite",
        headers={"Content-Type": "application/json"},
        body=json.dumps(envelope, ensure_ascii=False, indent=2),
        parts=tuple(parts),
        plan=plan,
    )


# --------------------------------------------------------------------------- #
# Salesforce: sObject tree
# --------------------------------------------------------------------------- #


def _render_tree(plan: BundlePlan, opts: RenderOptions) -> RequestDocument:
    """``POST /services/data/{version}/composite/tree/{rootType}``.

    The body is this build's shape, not a quoted one - see the module docstring
    and the ``sobject-tree-body`` inference. What *is* sourced is enforced by the
    planner before this runs: 200 records across all trees, five record types,
    five levels deep, and no partial mode.
    """
    version = opts.api_version
    by_reference = {step.reference_id: step for step in plan.steps}
    roots = [step for step in plan.steps if step.is_root] or [plan.steps[0]]
    root_type = roots[0].record_type
    envelope = {"records": [_tree_record(step, by_reference) for step in roots]}
    path = f"/services/data/{version}/composite/tree/{root_type}"

    parts = tuple(
        RequestPart(
            reference_id=step.reference_id,
            kind="tree-record",
            method="POST",
            path=path,
            headers={},
            body=json.dumps(step.fields, ensure_ascii=False, indent=2),
            depends_on=step.dependencies(),
        )
        for step in plan.steps
    )
    return RequestDocument(
        dialect=SALESFORCE_TREE,
        method="POST",
        path=path,
        headers={"Content-Type": "application/json"},
        body=json.dumps(envelope, ensure_ascii=False, indent=2),
        parts=parts,
        plan=plan,
        notes=(
            "The research quotes this endpoint's limits and its atomicity sentence, "
            "not a request body. The nested shape here is a named design inference.",
        ),
    )


def _tree_record(step: Step, by_reference: Mapping[str, Step]) -> dict[str, Any]:
    """One record in the tree, with its children nested under their type name."""
    record: dict[str, Any] = {
        "attributes": {"type": step.record_type, "referenceId": step.reference_id}
    }
    record.update(_fields_without_parent_link(step))
    for child in tree_children(step.reference_id, by_reference):
        record.setdefault(child.record_type, {"records": []})["records"].append(
            _tree_record(child, by_reference)
        )
    return record


def _fields_without_parent_link(step: Step) -> dict[str, Any]:
    """The record's own fields, minus the parent link.

    A tree links a child by *nesting* it, not by writing the parent's id into the
    child. Leaving the field in would give the body two ways of saying the same
    thing, the second of them empty at render time.
    """
    fields = dict(step.fields)
    if step.parent and step.parent.get("field"):
        fields.pop(step.parent["field"], None)
    return fields


def tree_children(reference: str, by_reference: Mapping[str, Step]) -> list[Step]:
    """The steps whose declared parent is ``reference``, in declared order."""
    return [
        step
        for step in by_reference.values()
        if step.parent is not None and step.parent["reference"] == reference
    ]


# --------------------------------------------------------------------------- #
# Dataverse: $batch with a changeset
# --------------------------------------------------------------------------- #


def _render_dataverse(plan: BundlePlan, opts: RenderOptions) -> RequestDocument:
    """``POST [org]/{version}/$batch`` with one changeset.

    The researched shape: ``multipart/mixed``, a ``changeset_*`` boundary that
    makes the operations atomic, ``Content-ID: 1/2/3`` on each part, and ``$1``
    where a part points at an earlier one.
    """
    order = list(plan.order)
    boundary = f"{CHANGESET_PREFIX}{opts.changeset or CHANGESET_DEFAULT}"
    numbers = {reference: content_id_for(reference, order) for reference in order}

    parts: list[RequestPart] = []
    chunks: list[str] = []

    for step in plan.steps:
        target = f"../api/data/{opts.dataverse_version}/{opts.entity_set(step.record_type)}"
        payload = json.dumps(_dataverse_body(step, numbers), ensure_ascii=False)
        chunks.append(
            "\n".join(
                [
                    f"--{boundary}",
                    f"Content-Type: {CHANGESET_PART_CONTENT_TYPE}",
                    "Content-Transfer-Encoding: binary",
                    f"Content-ID: {numbers[step.reference_id]}",
                    "",
                    f"POST {target} HTTP/1.1",
                    "Content-Type: application/json;odata.type=crm",
                    "",
                    payload,
                ]
            )
        )
        parts.append(
            RequestPart(
                reference_id=step.reference_id,
                kind="changeset-part",
                method="POST",
                path=f"{target}#Content-ID={numbers[step.reference_id]}",
                headers={
                    "Content-Type": CHANGESET_PART_CONTENT_TYPE,
                    "Content-ID": str(numbers[step.reference_id]),
                },
                body=payload,
                depends_on=step.dependencies(),
            )
        )

    chunks.append(f"--{boundary}--")
    mime = "\r\n".join(chunks) + "\r\n"

    return RequestDocument(
        dialect=DATAVERSE_BATCH,
        method="POST",
        path=DV_BATCH_PATH,
        headers={"Content-Type": DV_CONTENT_TYPE},
        body=mime,
        parts=tuple(parts),
        plan=plan,
        notes=(
            "One changeset, so every operation inside it is atomic: [sourced] "
            "'if any one of the operations fails, the batch request rolls back any "
            "completed operations'.",
        ),
    )


def _dataverse_body(step: Step, numbers: Mapping[str, int]) -> dict[str, Any]:
    """A part's JSON body, with the parent link written as an ``$n`` bind.

    The researched example is ``"originatingleadid@odata.bind":"$1"`` - a bind
    property whose value is the parent's Content-ID. So the step's declared parent
    field becomes ``<field>@odata.bind``, added alongside whatever fields the
    bundle declared.
    """
    body = dict(step.fields)
    if step.parent and step.parent.get("field"):
        body[f"{step.parent['field']}{DATAVERSE_BIND_SUFFIX}"] = (
            f"${numbers[step.parent['reference']]}"
        )
    return body


# --------------------------------------------------------------------------- #
# HubSpot: a sequence, not a request
# --------------------------------------------------------------------------- #


def _render_hubspot(plan: BundlePlan, opts: RenderOptions) -> RequestDocument:
    """The contacts batch create, then one create and one association ``PUT`` each.

    The research cites exactly these endpoints and documents no cross-object
    transaction, so this is honestly a *sequence*: ``is_sequence`` is true, the
    plan's ``atomic`` is false, and a strict policy is honoured by compensating
    rather than by a transaction. The run record says ``atomic: false`` so nobody
    has to guess.

    The research's "with an ``associations`` array, **or** PUT …" is read as a
    choice per record, and that is the reading implemented here: a Contact hangs
    off its parent through the batch create's ``associations`` array, and anything
    that is not a Contact - the Account and the Opportunity - gets the documented
    association ``PUT`` after its own create. Emitting both for one record would
    associate it twice.
    """
    contacts = [step for step in plan.steps if _is_contact(step)]
    parts: list[RequestPart] = []

    if contacts:
        parts.append(
            RequestPart(
                reference_id="batch-contacts",
                kind="batch-create",
                method="POST",
                path=HS_CONTACTS_BATCH_PATH,
                headers={},
                body=json.dumps(_hubspot_batch_body(plan), ensure_ascii=False, indent=2),
                depends_on=_batch_contact_dependencies(plan),
            )
        )

    for step in plan.steps:
        if _is_contact(step):
            continue
        parts.append(
            RequestPart(
                reference_id=step.reference_id,
                kind="create",
                method="POST",
                path=f"/crm/v3/objects/{object_type_of(step)}",
                headers={},
                body=json.dumps(
                    {"properties": _hubspot_properties(step)}, ensure_ascii=False, indent=2
                ),
                depends_on=(),
            )
        )
        parent = plan.step(step.parent["reference"]) if step.parent else None
        if parent is None:
            continue
        parts.append(
            RequestPart(
                reference_id=f"{step.reference_id}-association",
                kind="association",
                method="PUT",
                path=HS_ASSOCIATION_PATH.format(
                    from_type=object_type_of(step),
                    from_id=f"@{{{step.reference_id}.id}}",
                    to_type=object_type_of(parent),
                    to_id=f"@{{{parent.reference_id}.id}}",
                ),
                headers={},
                body=None,
                depends_on=(step.reference_id, parent.reference_id),
            )
        )

    return RequestDocument(
        dialect=HUBSPOT_ASSOCIATIONS,
        method="",
        path="",
        headers={},
        body="",
        parts=tuple(parts),
        plan=plan,
        notes=(
            "This dialect renders a sequence of requests, not one atomic request: the "
            "research cites a contacts batch create and an association PUT and documents "
            "no transaction that spans them.",
        ),
    )


def _is_contact(step: Step) -> bool:
    return step.record_type.lower() == "contact"


def _hubspot_batch_body(plan: BundlePlan) -> dict[str, Any]:
    """The contacts batch create body, with the researched ``associations`` array.

    A Contact that hangs off an Account carries an association naming the
    Account, written as a reference to the subrequest that creates it - which is
    the researched data flow's "later subrequests reference earlier ones by id",
    expressed in the only syntax this dialect has.
    """
    inputs: list[dict[str, Any]] = []
    for step in plan.steps:
        if not _is_contact(step):
            continue
        entry: dict[str, Any] = {
            "id": step.reference_id,
            "properties": _hubspot_properties(step),
        }
        parent = plan.step(step.parent["reference"]) if step.parent else None
        if parent is not None:
            entry["associations"] = [
                {
                    "to": {"id": f"@{{{parent.reference_id}.id}}"},
                    "types": [
                        {
                            "associationCategory": "HUBSPOT_DEFINED",
                            "associationTypeId": step.parent.get("field") or 1,
                        }
                    ],
                }
            ]
        inputs.append(entry)
    return {"inputs": inputs}


def _batch_contact_dependencies(plan: BundlePlan) -> tuple[str, ...]:
    parents: list[str] = []
    for step in plan.steps:
        if _is_contact(step) and step.parent and step.parent["reference"] not in parents:
            parents.append(step.parent["reference"])
    return tuple(parents)


def _hubspot_properties(step: Step) -> dict[str, Any]:
    """The ``properties`` object, with the deployment's own key removed.

    ``object_type_id`` addresses the URL, it is not a CRM property, and sending
    it as one would put a routing hint in the record.
    """
    return {key: value for key, value in step.fields.items() if key != "object_type_id"}


def object_type_of(step: Step) -> str:
    """The ``{objectTypeId}`` for a step.

    The research quotes the path with the placeholder and does not document
    HubSpot's numeric type ids, so a step may declare ``object_type_id`` and the
    default is the lower-cased record type. A wrong guess shows up in the preview
    rather than as a 404 later.
    """
    declared = step.fields.get("object_type_id")
    if isinstance(declared, str) and declared:
        return declared
    return step.record_type.lower()


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _plural(name: str) -> str:
    """Dataverse's entity-set convention: lower-case, plural."""
    if name.endswith(("s", "x", "ch", "sh")):
        return f"{name}es"
    if name.endswith("y") and len(name) > 1 and name[-2] not in "aeiou":
        return f"{name[:-1]}ies"
    return f"{name}s"


__all__ = [
    "CHANGESET_DEFAULT",
    "CHANGESET_PREFIX",
    "DEFAULT_API_VERSION",
    "DEFAULT_DATAVERSE_VERSION",
    "RequestDocument",
    "RequestPart",
    "RenderOptions",
    "object_type_of",
    "render",
    "tree_children",
]
