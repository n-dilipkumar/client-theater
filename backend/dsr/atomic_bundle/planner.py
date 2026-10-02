"""The dependency-ordered plan: the researched flow, made into data.

Step two of the researched user flow is "The connector builds a single request
containing Account → Contact → Opportunity subrequests in dependency order", and
the research's extensibility note is the reason this is a module rather than a
string builder:

    The dependency graph is declared as data (ordered subrequests with id
    references), so a deployment can add a 4th record type without touching the
    transport.

So the graph *is* the bundle. A step declares its type, its fields, and - if it
is not a root - the step it hangs from, by ``referenceId``. The transport reads
the plan; it does not know what an Account is. Adding a fourth record type is
adding an entry to a list, which is the claim the research makes and the shape
this module has to honour to make it true.

The other thing the research is explicit about is the *order*. "in dependency
order" plus "later subrequests reference earlier ones by id" means the declared
order is the dependency order, so a step may only depend on a step that came
before it. A forward reference is a contradiction, and it is refused here with a
message that says so - rather than being reordered, because a bundle that has to
be reordered to be valid is a bundle whose preview lied.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

from dsr.atomic_bundle.errors import (
    BundleShapeError,
    LimitExceeded,
    PolicyError,
    ReferenceError,
)
from dsr.atomic_bundle.references import sf_references
from dsr.atomic_bundle.vocabulary import (
    ATOMICITY,
    DATAVERSE_BATCH,
    DV_BATCH_MAX_REQUESTS,
    HUBSPOT_ASSOCIATIONS,
    LIMITS,
    POLICIES,
    POLICY_PARTIAL,
    POLICY_STRICT,
    SALESFORCE_COMPOSITE,
    SALESFORCE_TREE,
    SF_COMPOSITE_MAX_COLLECTIONS,
    SF_COMPOSITE_MAX_SUBREQUESTS,
    SF_TREE_MAX_DEPTH,
    SF_TREE_MAX_RECORDS,
    SF_TREE_MAX_TYPES,
)

#: A ``referenceId`` is a name a later step will point at, so it has to be an
#: identifier: no spaces, no punctuation that would make ``@{ref.path}``
#: ambiguous. Strict on purpose - the error message quotes the shape.
REFERENCE_SHAPE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")

#: Warning codes the preview renders. Named so a client can branch on them and a
#: reviewer can look one up.
WARN_IMPLICIT_DEPENDENCY = "implicit_dependency_unordered"
WARN_COLLATION_NOT_APPLICABLE = "collation_flag_not_applicable"
WARN_COMPENSATION_NOT_TRANSACTION = "compensation_not_transaction"
WARN_TREE_HAS_NO_ORDERING_FLAG = "tree_order_is_structural"

WARNINGS: dict[str, str] = {
    WARN_IMPLICIT_DEPENDENCY: (
        "This bundle declares an implicit dependency - a trigger that reads an "
        "earlier record without a reference to it. With collateSubrequests on, "
        "subrequests of the same type may be grouped together, so the trigger can "
        "run before the record it reads exists. Set collateSubrequests to false to "
        "guarantee the declared order."
    ),
    WARN_COLLATION_NOT_APPLICABLE: (
        "This dialect has no collation flag, so collateSubrequests has no effect on "
        "it. The declared order is the execution order either way."
    ),
    WARN_COMPENSATION_NOT_TRANSACTION: (
        "This dialect has no documented cross-object transaction, so a strict "
        "policy is honoured by deleting what was created. That is a compensation, "
        "not a rollback: if a delete is itself refused, the rows stay."
    ),
    WARN_TREE_HAS_NO_ORDERING_FLAG: (
        "The sObject Tree endpoint takes one nested tree, so the order is the "
        "nesting. The research documents no collation flag for it."
    ),
}

#: The field name a step's dependency is written to. [sourced] the research's data
#: flow says the CRM "resolves $1/@{refAccount.id} into real record URIs as it
#: creates each row → related rows linked"; the field the id lands in is a
#: deployment fact, so it is declared per step and defaults to
#: ``<ParentType>Id`` when the declaration omits it.
DEFAULT_PARENT_FIELD = "Id"


@dataclass(frozen=True)
class Step:
    """One record in the bundle, with the step it depends on, if any."""

    reference_id: str
    record_type: str
    fields: dict[str, Any]
    position: int
    parent: dict[str, str] | None = None
    implicit_depends_on: tuple[str, ...] = ()
    collection: dict[str, Any] | None = None

    @property
    def is_collection(self) -> bool:
        return self.collection is not None

    @property
    def is_root(self) -> bool:
        return self.parent is None

    @property
    def record_count(self) -> int:
        """How many CRM records this step creates."""
        return len(self.collection["records"]) if self.is_collection else 1

    @property
    def depth(self) -> int:
        """1 for a root, one more than its parent."""
        return 1

    def dependencies(self) -> tuple[str, ...]:
        """Every earlier step this one must run after."""
        declared = (self.parent["reference"],) if self.parent else ()
        return declared + tuple(self.implicit_depends_on)

    def explicit_dependencies(self) -> tuple[str, ...]:
        """Dependencies expressed as an id reference, which *is* an ordering edge."""
        return (self.parent["reference"],) if self.parent else ()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "reference_id": self.reference_id,
            "record_type": self.record_type,
            "position": self.position,
            "fields": dict(self.fields),
            "is_root": self.is_root,
            "is_collection": self.is_collection,
            "record_count": self.record_count,
            "parent": dict(self.parent) if self.parent else None,
            "explicit_dependencies": list(self.explicit_dependencies()),
            "implicit_dependencies": list(self.implicit_depends_on),
        }
        if self.is_collection:
            payload["collection"] = dict(self.collection)
        return payload


@dataclass(frozen=True)
class BundlePlan:
    """A bundle, validated and ordered, ready to render into a request."""

    name: str
    dialect: str
    policy: str
    collate_subrequests: bool
    steps: tuple[Step, ...]
    room_id: str = ""
    bundle_id: str = ""
    field_map: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[dict[str, str], ...] = ()

    # -- shape ------------------------------------------------------------- #

    @property
    def subrequests(self) -> int:
        """How many subrequests this plan becomes."""
        return len(self.steps)

    @property
    def collections(self) -> int:
        return sum(1 for step in self.steps if step.is_collection)

    @property
    def record_count(self) -> int:
        return sum(step.record_count for step in self.steps)

    @property
    def distinct_types(self) -> tuple[str, ...]:
        seen: list[str] = []
        for step in self.steps:
            if step.record_type not in seen:
                seen.append(step.record_type)
        return tuple(seen)

    @property
    def depth(self) -> int:
        """The longest chain of declared parents, counting the root as 1."""
        by_reference = {step.reference_id: step for step in self.steps}
        deepest = 0
        for step in self.steps:
            level = 1
            cursor = step
            seen: set[str] = set()
            while cursor.parent is not None:
                if cursor.reference_id in seen:  # pragma: no cover - refused earlier
                    break
                seen.add(cursor.reference_id)
                level += 1
                nxt = by_reference.get(cursor.parent["reference"])
                if nxt is None:  # pragma: no cover - refused earlier
                    break
                cursor = nxt
            deepest = max(deepest, level)
        return deepest

    @property
    def order(self) -> tuple[str, ...]:
        """``referenceId``s in declared order - the dependency order."""
        return tuple(step.reference_id for step in self.steps)

    @property
    def atomicity(self) -> dict[str, Any]:
        return dict(ATOMICITY[self.dialect])

    @property
    def atomic(self) -> bool:
        return bool(self.atomicity["atomic"])

    def step(self, reference_id: str) -> Step | None:
        return next((step for step in self.steps if step.reference_id == reference_id), None)

    def by_position(self) -> dict[str, int]:
        """``{referenceId: 1-based position}`` - the Content-ID numbering."""
        return {step.reference_id: index + 1 for index, step in enumerate(self.steps)}

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "room_id": self.room_id,
            "bundle_id": self.bundle_id,
            "dialect": self.dialect,
            "policy": self.policy,
            "collate_subrequests": self.collate_subrequests,
            "order": list(self.order),
            "subrequests": self.subrequests,
            "collections": self.collections,
            "record_count": self.record_count,
            "distinct_types": list(self.distinct_types),
            "depth": self.depth,
            "atomicity": self.atomicity,
            "field_map": dict(self.field_map),
            "steps": [step.to_dict() for step in self.steps],
            "warnings": [dict(entry) for entry in self.warnings],
        }


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #


def plan_bundle(
    bundle: Mapping[str, Any],
    *,
    dialect: str | None = None,
    policy: str | None = None,
    collate_subrequests: bool | None = None,
    room_id: str = "",
    bundle_id: str = "",
) -> BundlePlan:
    """Validate a declared bundle and return the ordered plan.

    Everything this refuses, it refuses *before* anything is rendered, so a
    bundle that cannot be sent never becomes a request that has to be withdrawn.
    """
    chosen_dialect = str(dialect or bundle.get("dialect") or SALESFORCE_COMPOSITE)
    if chosen_dialect not in ATOMICITY:
        raise BundleShapeError(
            f"unknown dialect {chosen_dialect!r}; expected one of {sorted(ATOMICITY)}"
        )

    chosen_policy = str(policy or bundle.get("policy") or POLICY_STRICT)
    if chosen_policy not in POLICIES:
        raise PolicyError(
            f"unknown rollback policy {chosen_policy!r}; expected one of {list(POLICIES)}"
        )
    if not ATOMICITY[chosen_dialect]["honours_partial"] and chosen_policy == POLICY_PARTIAL:
        raise PolicyError(
            f"{chosen_dialect} has no partial mode. {ATOMICITY[chosen_dialect]['basis']} "
            f'[sourced] "{ATOMICITY[chosen_dialect]["quote"]}"'
        )

    collate = (
        bool(bundle.get("collate_subrequests", True))
        if collate_subrequests is None
        else bool(collate_subrequests)
    )

    steps = _steps(bundle, chosen_dialect)
    _check_limits(steps, chosen_dialect)

    warnings = _warnings(steps, chosen_dialect, chosen_policy, collate)

    return BundlePlan(
        name=str(bundle.get("name") or "Untitled bundle"),
        dialect=chosen_dialect,
        policy=chosen_policy,
        collate_subrequests=collate,
        steps=steps,
        room_id=room_id or str(bundle.get("room_id") or ""),
        bundle_id=bundle_id or str(bundle.get("id") or ""),
        field_map=dict(bundle.get("field_map") or {}),
        warnings=warnings,
    )


def _steps(bundle: Mapping[str, Any], dialect: str) -> tuple[Step, ...]:
    records = bundle.get("records")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)) or not records:
        raise BundleShapeError(
            "records is required and must be a non-empty list of the subrequests to send"
        )

    steps: list[Step] = []
    seen: dict[str, int] = {}

    for position, entry in enumerate(records):
        if not isinstance(entry, Mapping):
            raise BundleShapeError(f"record {position} is not an object")

        reference_id = entry.get("reference_id") or entry.get("referenceId")
        if not isinstance(reference_id, str) or not reference_id.strip():
            raise BundleShapeError(
                f"record {position} has no reference_id; every subrequest needs one so a "
                "later subrequest can point at it"
            )
        reference_id = reference_id.strip()
        if not REFERENCE_SHAPE.fullmatch(reference_id):
            raise BundleShapeError(
                f"reference_id {reference_id!r} is not an identifier "
                f"(expected {REFERENCE_SHAPE.pattern})"
            )
        if reference_id in seen:
            raise BundleShapeError(
                f"reference_id {reference_id!r} is used twice (records {seen[reference_id]} "
                f"and {position}); a reference has to name exactly one subrequest"
            )

        record_type = entry.get("type") or entry.get("record_type") or entry.get("sobject")
        if not isinstance(record_type, str) or not record_type.strip():
            raise BundleShapeError(
                f"record {position} ({reference_id}) has no type; the transport has to know "
                "which CRM object to create"
            )

        fields = entry.get("fields") or {}
        if not isinstance(fields, Mapping):
            raise BundleShapeError(f"record {position} ({reference_id}) has non-object fields")
        if not fields and not entry.get("collection"):
            raise BundleShapeError(
                f"record {position} ({reference_id}) has no fields; there is nothing to create"
            )

        collection = _collection(entry, position, reference_id, dialect)
        # A collection's own field map is filled in *after* the declared fields
        # have been checked, because the placeholder it needs points at the
        # step's own result and would otherwise read as a self-reference. See
        # :func:`_collection_field_map`.
        steps.append(
            Step(
                reference_id=reference_id,
                record_type=record_type.strip(),
                fields=dict(fields),
                position=position,
                parent=_parent(entry, position, reference_id),
                implicit_depends_on=_implicit(entry, position),
                collection=collection,
            )
        )
        seen[reference_id] = position

    for step in steps:
        for reference in step.dependencies():
            if reference not in seen:
                raise ReferenceError(
                    f"{step.reference_id!r} depends on {reference!r}, which is not in the bundle"
                )
            if seen[reference] >= step.position:
                where = (
                    "itself" if seen[reference] == step.position else f"record {seen[reference]}"
                )
                raise ReferenceError(
                    f"{step.reference_id!r} depends on {reference!r}, declared at {where} - "
                    "subrequests must be in dependency order, so a step may only reference "
                    "an earlier one"
                )

    _check_declared_parent_fields(steps)
    _check_field_references(steps, seen)
    return tuple(replace(step, fields=_collection_field_map(step)) for step in steps)


def _collection_field_map(step: Step) -> dict[str, Any]:
    """A collection's field map, when the bundle did not declare one.

    A collection's result is a *list*, so a later step's reference has to index
    into it: ``@{ref.Name}`` would resolve to nothing, and the documented index
    form ``@{ref.Name[0]}`` is the one that does. A collection that *did* declare
    fields keeps them - a team that has its own field map has already said what
    the reference should be.
    """
    if not step.is_collection or step.fields:
        return dict(step.fields)
    key = step.collection["field"]
    return {key: f"@{{{step.reference_id}.{key}[0]}}"}


def _check_declared_parent_fields(steps: Sequence[Step]) -> None:
    """The parent link is declared once: by the ``parent`` block, or not at all.

    Each dialect writes that link in its own way - a ``@{ref.id}`` placeholder in
    the subrequest body, a bind property in a changeset part, nesting in a tree,
    an association in HubSpot - so which representation ends up on the wire is the
    dialect's business, not the bundle's. A bundle that *also* spells the link out
    in ``fields`` would produce two representations of one relationship, and on
    Dataverse that meant both ``AccountId`` and ``AccountId@odata.bind`` going out
    with the same meaning. One declaration, one link.
    """
    for step in steps:
        if not step.parent or not step.parent.get("field"):
            continue
        if step.parent["field"] in step.fields:
            raise BundleShapeError(
                f"{step.reference_id!r} declares parent {step.parent['reference']!r} on field "
                f"{step.parent['field']!r} *and* sets that field in `fields`. Declare the link "
                "once: the `parent` block is enough, and the dialect writes it in the form its "
                "API expects."
            )


def _check_field_references(steps: Sequence[Step], seen: Mapping[str, int]) -> None:
    """Every ``@{ref.path}`` in a body must name an earlier subrequest.

    [sourced] "later subrequests reference earlier ones by id". A body that points
    at a later one, at itself, or at nothing is a reference the CRM cannot
    resolve, and finding it here is what keeps it out of the request.

    A ``$n`` is checked by position rather than by name, and a plan is rendered
    before it is sent, so there is nothing to check for it here - the binder adds
    it in the renderer.
    """
    for step in steps:
        for reference, path in sf_references(step.fields):
            if reference not in seen:
                raise ReferenceError(
                    f"{step.reference_id!r} has the placeholder @{{{reference}.{path}}}, and no "
                    "subrequest in this bundle has that referenceId"
                )
            if seen[reference] >= step.position:
                where = (
                    "itself" if seen[reference] == step.position else f"record {seen[reference]}"
                )
                raise ReferenceError(
                    f"{step.reference_id!r} has the placeholder @{{{reference}.{path}}}, which "
                    f"names {where} - subrequests must be in dependency order, so a body may "
                    "only reference an earlier one"
                )


def _parent(entry: Mapping[str, Any], position: int, reference_id: str) -> dict[str, str] | None:
    parent = entry.get("parent")
    if parent is None:
        return None
    if not isinstance(parent, Mapping):
        raise BundleShapeError(f"record {position} ({reference_id}) has a non-object parent")
    target = parent.get("reference") or parent.get("referenceId")
    if not isinstance(target, str) or not target.strip():
        raise BundleShapeError(
            f"record {position} ({reference_id}) has a parent with no reference to point at"
        )
    field = parent.get("field")
    if field is not None and (not isinstance(field, str) or not field.strip()):
        raise BundleShapeError(
            f"record {position} ({reference_id}) has a parent field that is not a name"
        )
    return {"reference": target.strip(), "field": str(field or "").strip() or None}  # type: ignore[dict-item]


def _implicit(entry: Mapping[str, Any], position: int) -> tuple[str, ...]:
    declared = entry.get("implicit_depends_on") or entry.get("implicitDependsOn") or ()
    if isinstance(declared, str):
        declared = (declared,)
    if not isinstance(declared, (list, tuple)):
        raise BundleShapeError(f"record {position} has a non-list implicit_depends_on")
    seen: list[str] = []
    for reference in declared:
        if not isinstance(reference, str) or not reference.strip():
            raise BundleShapeError(
                f"record {position} has an implicit dependency that is not a name"
            )
        if reference.strip() not in seen:
            seen.append(reference.strip())
    return tuple(seen)


def _collection(
    entry: Mapping[str, Any], position: int, reference_id: str, dialect: str
) -> dict[str, Any] | None:
    collection = entry.get("collection")
    if collection is None:
        return None
    if not isinstance(collection, Mapping):
        raise BundleShapeError(f"record {position} ({reference_id}) has a non-object collection")
    if dialect == SALESFORCE_TREE:
        # The tree endpoint takes a nested tree, not an sObject Collection, and
        # the research documents the two as separate endpoints. Rendering a
        # collection as a tree would invent a shape nothing sourced supports.
        raise BundleShapeError(
            f"{dialect} takes a nested tree, not an sObject Collection; "
            f"{reference_id!r} declares one"
        )
    records = collection.get("records")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)) or not records:
        raise BundleShapeError(
            f"record {position} ({reference_id}) declares a collection with no records"
        )
    for index, item in enumerate(records):
        if not isinstance(item, Mapping):
            raise BundleShapeError(
                f"record {position} ({reference_id}) collection entry {index} is not an object"
            )
    key_field = collection.get("field")
    if not isinstance(key_field, str) or not key_field.strip():
        raise BundleShapeError(
            f"record {position} ({reference_id}) declares a collection with no field to key on"
        )
    return {"field": key_field.strip(), "records": [dict(item) for item in records]}


# --------------------------------------------------------------------------- #
# Limits
# --------------------------------------------------------------------------- #


def _check_limits(steps: Sequence[Step], dialect: str) -> None:
    """Refuse a bundle the documented limits already say will be rejected.

    Doing it here rather than letting the CRM refuse it means the reason reaches
    the room before anything is sent, and no half-written request is on the wire
    when the answer is no.
    """
    subrequests = len(steps)
    collections = sum(1 for step in steps if step.is_collection)
    records = sum(step.record_count for step in steps)
    types = len({step.record_type for step in steps})
    depth = max((_depth_of(step, steps) for step in steps), default=0)

    checks: tuple[tuple[str, int, int, str], ...] = (
        (
            "salesforce_composite.subrequests",
            subrequests,
            SF_COMPOSITE_MAX_SUBREQUESTS,
            "subrequests in a single composite call",
        ),
        (
            "salesforce_composite.collections",
            collections,
            SF_COMPOSITE_MAX_COLLECTIONS,
            "sObject Collections in a single composite call",
        ),
        (
            "salesforce_sobject_tree.records",
            records,
            SF_TREE_MAX_RECORDS,
            "records across all trees",
        ),
        (
            "salesforce_sobject_tree.types",
            types,
            SF_TREE_MAX_TYPES,
            "records of different types in a tree",
        ),
        (
            "salesforce_sobject_tree.depth",
            depth,
            SF_TREE_MAX_DEPTH,
            "levels of tree nesting",
        ),
        (
            "dataverse_batch.requests",
            subrequests,
            DV_BATCH_MAX_REQUESTS,
            "individual requests in a $batch",
        ),
    )

    for key, value, maximum, noun in checks:
        if not key.startswith(dialect):
            continue
        if value > maximum:
            quoted = LIMITS[key]["quote"]
            raise LimitExceeded(
                f'{value} {noun} exceeds the documented maximum of {maximum}. [sourced] "{quoted}"'
            )


def _depth_of(step: Step, steps: Sequence[Step]) -> int:
    by_reference = {entry.reference_id: entry for entry in steps}
    level = 1
    cursor = step
    while cursor.parent is not None:
        nxt = by_reference.get(cursor.parent["reference"])
        if nxt is None:  # pragma: no cover - refused by _steps
            break
        level += 1
        cursor = nxt
    return level


# --------------------------------------------------------------------------- #
# Warnings: the tenant's speed-for-ordering knob
# --------------------------------------------------------------------------- #


def _warnings(
    steps: Sequence[Step], dialect: str, policy: str, collate: bool
) -> tuple[dict[str, str], ...]:
    found: list[dict[str, str]] = []

    implicit = [step.reference_id for step in steps if step.implicit_depends_on]
    if dialect == SALESFORCE_COMPOSITE and collate and implicit:
        found.append(
            {
                "code": WARN_IMPLICIT_DEPENDENCY,
                "steps": ", ".join(implicit),
                "message": WARNINGS[WARN_IMPLICIT_DEPENDENCY],
            }
        )
    if dialect == SALESFORCE_TREE:
        found.append(
            {
                "code": WARN_TREE_HAS_NO_ORDERING_FLAG,
                "steps": "",
                "message": WARNINGS[WARN_TREE_HAS_NO_ORDERING_FLAG],
            }
        )
    elif dialect in (DATAVERSE_BATCH, HUBSPOT_ASSOCIATIONS) and collate is not None:
        found.append(
            {
                "code": WARN_COLLATION_NOT_APPLICABLE,
                "steps": "",
                "message": WARNINGS[WARN_COLLATION_NOT_APPLICABLE],
            }
        )

    if policy == POLICY_STRICT and not ATOMICITY[dialect]["atomic"]:
        found.append(
            {
                "code": WARN_COMPENSATION_NOT_TRANSACTION,
                "steps": "",
                "message": WARNINGS[WARN_COMPENSATION_NOT_TRANSACTION],
            }
        )

    return tuple(found)
