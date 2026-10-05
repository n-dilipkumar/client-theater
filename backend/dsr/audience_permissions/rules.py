"""The rules WF-074 enforces: the default deny, the two scopes, and membership.

This is the researched specification for WF-074 made executable. The specification is
``docs/research/digital-sales-room-workflows/wf/WF-074.md``, quoted in full in issue 149,
and the docstring on each rule names the evidence it came from.

Nothing in this module touches the store. Every function here is a pure function of its
arguments, so the rules can be tested without a database and the engine can be tested
without a request. That is the split the package is built on:
:mod:`~dsr.audience_permissions.vocabulary` holds the researched terms,
this module holds the decisions, :mod:`~dsr.audience_permissions.inferences` holds the
judgement calls, and :mod:`~dsr.audience_permissions.engine` holds the reads and writes.

The seven rules the rest of the workflow leans on
-------------------------------------------------

**The default is deny, and an absent row is not a wildcard.** The guide says "A new
group sees **nothing** until you grant permissions." So :func:`entry_for` returns ``None``
for an item with no row, and :func:`decide_item` turns that into
:data:`~dsr.audience_permissions.vocabulary.HIDDEN_NO_PERMISSION`. There is no code path
in which a missing row grants access, and :func:`decide_item` has no "inherit" argument
that could be set to one.

**The two scopes write differently, and neither shares code with the other.** Group
permissions are delta: "entries you send are upserted, entries you omit keep their current
state". Link permissions are full-replace: "the payload is the complete desired state,
items not listed lose their override, and an empty array clears everything". So
:func:`apply_delta` only ever touches the items in the payload and
:func:`apply_full_replace` returns the items to write and the ids to drop. Building one
generic writer and a flag beside it is the failure this avoids: a flag is one boolean away
from defaulting to the wrong semantics, and the wrong semantics here is over-granting.

**A group link refuses link overrides.** The rule is a refusal, not a precedence order:
"Rejected with ``422`` on links with ``audience_type: \\"group\\"`` - their group
determines visibility; switch the link to ``audience_type: \\"general\\"`` first." So
:func:`require_link_scope` raises rather than picking a winner. An implementation that
resolved the conflict by precedence would silently show one scope's answer while the rep
believed they were reading the other, which is the exact defect a refusal prevents.

**Membership is three steps in a fixed order.** The data flow says the email is matched
"explicit email, then domain, then ``allow_all``". :func:`resolve_membership` evaluates
those three and returns the step that matched, so the page can say which rule admitted the
viewer. The order is load-bearing: an explicit member is reported as an explicit member
even when their domain is also on the group, so a rep reading the answer is told the
narrowest true thing.

**``allow_all`` short-circuits the two checks before it.** Quoted: "When true, anyone who
passes the link's other access gates is treated as a group member - the email/domain
membership check is skipped." :func:`resolve_membership` returns
:data:`~dsr.audience_permissions.vocabulary.MEMBERSHIP_ALLOW_ALL` before it looks at the
member rows at all.

**Ancestor folders are a write, not a read-time filter.** Quoted: "Ancestor folders of any
item made visible are automatically set to ``can_view: true`` so the folder tree stays
navigable." :func:`plan_ancestor_grants` returns the entries to write, and the engine
writes them. Filtering them in at read time instead would make the same tree navigable on
one request and broken on the next, and would leave the grid lying about what is stored.

**Download never implies view, and view never implies download.** The entry has two
independent booleans because the specification's own example gives an audience a folder it
may browse but not download. :func:`decide_item` therefore returns
:data:`~dsr.audience_permissions.vocabulary.VIEW_ONLY` rather than collapsing the two into
one "allowed" word.

What this module does not decide
--------------------------------

Whether an item still exists. The ``item_id`` targets belong to WF-003's library, and this
workflow only reads them. A permission entry for a document that has since been deleted is
stored as given and reported as dangling by the engine, which is a fact about the room
rather than an error in the grant.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from dsr.audience_permissions import vocabulary as vocab

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+$")


class AudienceRuleError(ValueError):
    """A payload this workflow will not accept.

    Carries a field-keyed map, because the page puts each message beside the input that
    caused it rather than in one combined sentence. Same reasoning as every other
    workflow's validation error: it is this workflow's own type, so mapping it to a
    response here cannot intercept anything anywhere else in the product.
    """

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})


class GroupNotFound(LookupError):
    """No such group, or it belongs to a different room.

    Its own type rather than the store's ``RecordNotFound``, because a feature may only
    map error types it raises itself. A handler for a shared type would intercept that
    exception across the whole product.
    """


class MemberNotFound(LookupError):
    """No such member of that group. Same reasoning as :class:`GroupNotFound`."""


class LinkNotFound(LookupError):
    """No such link, or it belongs to a different room."""


class ScopeConflict(AudienceRuleError):
    """A link override was attempted on a link that belongs to a group.

    The researched status for this is ``422``, and it is a refusal rather than a
    precedence order. Raised by :func:`require_link_scope` and mapped by the HTTP layer
    alone: a second feature mapping the same type would be a host error, so this type is
    declared here and nowhere else.
    """


# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #


def utcnow() -> datetime:
    """The clock, as one function, so a test can move it by hand."""
    return datetime.now(timezone.utc)


def stamp(moment: datetime) -> str:
    """An instant as the sortable string the store already writes."""
    return moment.isoformat(timespec="milliseconds")


# --------------------------------------------------------------------------- #
# Emails and domains
# --------------------------------------------------------------------------- #


def normalise_email(value: Any, *, field: str = "email") -> str:
    """One address, lowercased and trimmed, or a validation failure.

    Membership is keyed by email, so two spellings of one address are one member or a
    duplicate. The address is lowercased because the domain half of an address is not
    case sensitive and the local half in practice is treated the same way by every mail
    provider the research describes.

    A value with no ``@``, or with more than one, is refused rather than repaired. A
    repaired address is an address the rep did not type.
    """

    address = str(value or "").strip().lower()
    if not address:
        raise AudienceRuleError("A member needs an email address.", {field: "Email is required."})
    if not _EMAIL.match(address):
        raise AudienceRuleError(
            f"{address!r} is not an email address.",
            {field: "Enter one address, for example jane@acme.com."},
        )
    return address


def normalise_domain(value: Any, *, field: str = vocab.DOMAINS_FIELD) -> str:
    """One domain as ``@name``, lowercased, or a validation failure.

    Quoted: "Accepts bare (``acme.com``) or ``@``-prefixed (``@acme.com``) domains; both
    are lowercased and normalized to ``@acme.com``." So both spellings land on the same
    stored string and a domain list cannot hold the same domain twice under two forms.
    """

    raw = str(value or "").strip().lower()
    trimmed = raw[1:] if raw.startswith(vocab.DOMAIN_PREFIX) else raw
    if not trimmed or "@" in trimmed or "." not in trimmed or " " in trimmed:
        raise AudienceRuleError(
            f"{raw!r} is not an email domain.",
            {field: "Give a domain as acme.com or as @acme.com."},
        )
    return f"{vocab.DOMAIN_PREFIX}{trimmed}"


def normalise_domains(values: Any) -> list[str]:
    """The group's domain list, normalised and de-duplicated in first-seen order.

    "Duplicates are removed", quoted, and order is preserved rather than sorted because
    a rep who typed two domains expects to see them back in the order they typed them.

    The ``max 100`` cap is checked here rather than in the HTTP layer, so a caller that
    goes through the engine cannot bypass it by calling the rule directly. That matters
    because the cap is the vendor's own request bound: it protects the caller's account,
    so the check belongs where the list is built.
    """

    if values is None:
        return []
    if isinstance(values, str) or not isinstance(values, Iterable):
        raise AudienceRuleError(
            "domains must be a list.",
            {vocab.DOMAINS_FIELD: "Send a list of domains."},
        )
    ordered: list[str] = []
    for raw in values:
        domain = normalise_domain(raw)
        if domain not in ordered:
            ordered.append(domain)
    if len(ordered) > vocab.MAX_DOMAINS:
        raise AudienceRuleError(
            f"A group takes at most {vocab.MAX_DOMAINS} domains.",
            {
                vocab.DOMAINS_FIELD: (
                    f"You sent {len(ordered)}. The cap is {vocab.MAX_DOMAINS}. "
                    "Remove some or split them across groups."
                )
            },
        )
    return ordered


def domain_of(email: Any) -> str:
    """The ``@name`` half of an address, or an empty string when there is no ``@``.

    Deliberately tolerant where :func:`normalise_email` is strict. A malformed address
    reaching the membership check must not be a crash: it simply matches no domain, and
    :func:`resolve_membership` reports the address as not a member.
    """

    address = str(email or "").strip().lower()
    if "@" not in address:
        return ""
    return f"{vocab.DOMAIN_PREFIX}{address.rsplit('@', 1)[1]}"


def normalise_member_emails(values: Any) -> list[str]:
    """The addresses to add, normalised, de-duplicated and order-preserving.

    "already-present members are skipped, so the call is idempotent" is a property of the
    write, but de-duplicating inside one call is what makes it true for the caller's own
    input too: a list containing the same address twice must not produce two rows to skip.

    The ``max 500`` cap is the vendor's per-request bound and is checked here, for the
    same reason the domain cap is: it is checked where the list is built.
    """

    if values is None:
        return []
    if isinstance(values, str) or not isinstance(values, Iterable):
        raise AudienceRuleError("emails must be a list.", {"emails": "Send a list of addresses."})
    ordered: list[str] = []
    for raw in values:
        address = normalise_email(raw, field="emails")
        if address not in ordered:
            ordered.append(address)
    if len(ordered) > vocab.MAX_MEMBERS_PER_CALL:
        raise AudienceRuleError(
            f"A call takes at most {vocab.MAX_MEMBERS_PER_CALL} member addresses.",
            {
                "emails": (
                    f"You sent {len(ordered)}. The cap is {vocab.MAX_MEMBERS_PER_CALL}. "
                    "Send the rest in another call."
                )
            },
        )
    return ordered


# --------------------------------------------------------------------------- #
# The closed permission entry
# --------------------------------------------------------------------------- #


def build_permission(entry: Any, *, field: str = "permission") -> dict[str, Any]:
    """One entry, validated and reduced to the four researched keys.

    ``PermissionEntry`` "declares ``additionalProperties: false``", quoted. So a spare key
    is refused rather than dropped: the vendor's own validator would reject the request,
    and a store that accepted the row would then hold a row the API cannot read back. The
    returned mapping carries exactly :data:`~dsr.audience_permissions.vocabulary.PERMISSION_ENTRY_FIELDS`.

    Both flags are required and both must be booleans. A flag with a default would hide
    the case that matters most, which is an audience that may browse a folder but not
    download it.
    """

    if not isinstance(entry, Mapping):
        raise AudienceRuleError(
            "A permission entry must be an object.",
            {field: "Send an object with item_id, item_type, can_view and can_download."},
        )

    unknown = sorted(set(entry) - set(vocab.PERMISSION_ENTRY_FIELDS))
    if unknown:
        raise AudienceRuleError(
            f"A permission entry carries no key named {unknown[0]}.",
            {
                field: (
                    f"The entry is closed. Remove {', '.join(unknown)}. It accepts "
                    f"{', '.join(vocab.PERMISSION_ENTRY_FIELDS)} and nothing else."
                )
            },
        )

    missing = [name for name in vocab.PERMISSION_ENTRY_FIELDS if name not in entry]
    if missing:
        raise AudienceRuleError(
            f"A permission entry needs {missing[0]}.",
            {field: f"{missing[0]} is required. The entry accepts exactly four keys."},
        )

    item_id = str(entry["item_id"] or "").strip()
    if not item_id:
        raise AudienceRuleError(
            "A permission entry needs an item_id.",
            {field: "item_id is required and cannot be empty."},
        )

    item_type = str(entry["item_type"] or "").strip()
    if item_type not in vocab.ITEM_TYPES:
        raise AudienceRuleError(
            f"{item_type!r} is not an item type.",
            {
                field: (
                    f"item_type must be {vocab.ITEM_TYPE_DOCUMENT} or "
                    f"{vocab.ITEM_TYPE_FOLDER}."
                )
            },
        )

    flags: dict[str, bool] = {}
    for name in (vocab.CAN_VIEW, vocab.CAN_DOWNLOAD):
        value = entry[name]
        if not isinstance(value, bool):
            raise AudienceRuleError(
                f"{name} must be true or false.",
                {field: f"{name} must be the boolean true or false, not {value!r}."},
            )
        flags[name] = value

    return {
        "item_id": item_id,
        "item_type": item_type,
        vocab.CAN_VIEW: flags[vocab.CAN_VIEW],
        vocab.CAN_DOWNLOAD: flags[vocab.CAN_DOWNLOAD],
    }


def build_permissions(entries: Any) -> list[dict[str, Any]]:
    """The whole payload, validated, with ``max 1000 entries`` enforced.

    Duplicates inside one payload are refused rather than resolved. The vendor's request
    is a list of upserts, and two rows for one item in the same call have no defensible
    winner: the second would silently overwrite the first, and which one the rep meant is
    not knowable from the payload.
    """

    if entries is None:
        return []
    if isinstance(entries, Mapping) or not isinstance(entries, Sequence):
        raise AudienceRuleError(
            "permissions must be a list of entries.",
            {"permissions": "Send a list, not a single object."},
        )
    built = [build_permission(entry, field=f"permissions[{index}]") for index, entry in enumerate(entries)]
    if len(built) > vocab.MAX_PERMISSIONS_PER_CALL:
        raise AudienceRuleError(
            f"A call takes at most {vocab.MAX_PERMISSIONS_PER_CALL} permission entries.",
            {
                "permissions": (
                    f"You sent {len(built)}. The cap is {vocab.MAX_PERMISSIONS_PER_CALL}."
                )
            },
        )
    seen: set[tuple[str, str]] = set()
    for entry in built:
        key = (entry["item_type"], entry["item_id"])
        if key in seen:
            raise AudienceRuleError(
                f"Two entries in this call both name {entry['item_id']}.",
                {
                    "permissions": (
                        f"{entry['item_id']} appears twice. Send one entry per item so the "
                        "outcome does not depend on list order."
                    )
                },
            )
        seen.add(key)
    return built


def entry_key(entry: Mapping[str, Any]) -> str:
    """One entry's identity as a single string, used to key a permission map.

    The item type is part of the key, not decoration: ``item_id`` is opaque and this
    repository's own ids are collection-prefixed, but the vendor's ids are not promised
    to be. Keying on the id alone would merge a document and a folder that happened to
    share a string.
    """

    return f"{entry['item_type']}:{entry['item_id']}"


# --------------------------------------------------------------------------- #
# Scope semantics: the two writers, and neither of them is the other
# --------------------------------------------------------------------------- #


def apply_delta(
    existing: Mapping[str, Mapping[str, Any]], entries: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Group semantics: upsert the entries sent, leave every other item alone.

    "Items not listed keep their current state (delta semantics - matching the
    dashboard)", quoted. So the result is the existing map with the payload written over
    it, and the keys the payload does not name are returned untouched under
    :data:`UNTOUCHED`.

    The return value names what it did, because a delta write's most useful answer is
    "which items did this call actually change". A rep who grants one document and turns
    off another in the same call needs to know both happened, and a response that returned
    only the whole resulting map would force them to diff it themselves.
    """

    merged: dict[str, dict[str, Any]] = {key: dict(value) for key, value in existing.items()}
    touched: list[str] = []
    for entry in entries:
        key = entry_key(entry)
        before = existing.get(key)
        merged[key] = dict(entry)
        if before != dict(entry):
            touched.append(key)
    untouched = sorted(key for key in existing if key not in touched)
    return {
        "semantics": vocab.SCOPE_SEMANTICS[vocab.SCOPE_GROUP],
        "entries": [merged[key] for key in sorted(merged)],
        vocab.TOUCHED_KEY: touched,
        vocab.UNTOUCHED_KEY: untouched,
    }


def apply_full_replace(
    existing: Mapping[str, Mapping[str, Any]], entries: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Link semantics: the payload is the whole truth, and anything absent is removed.

    "The complete desired permission state for this link (full-replace semantics...) An
    empty array clears all overrides", quoted. So the result is exactly the payload, the
    rows to drop are the keys that existed and are not in the payload, and an empty payload
    clears everything.

    This is the difference from :func:`apply_delta` in one sentence, and it is why the two
    are separate functions rather than one with a flag: for the same two payloads in the
    same order, one keeps an omitted item and the other removes it. Only one of those can
    be right, and which one is decided by the scope, not by the caller.
    """

    wanted = {entry_key(entry): dict(entry) for entry in entries}
    dropped = sorted(key for key in existing if key not in wanted)
    return {
        "semantics": vocab.SCOPE_SEMANTICS[vocab.SCOPE_LINK],
        "entries": [wanted[key] for key in sorted(wanted)],
        vocab.TOUCHED_KEY: sorted(wanted),
        vocab.DROPPED_KEY: dropped,
    }


def require_link_scope(audience_type: Any) -> str:
    """A link's audience type, or a refusal when the link belongs to a group.

    Quoted: "Rejected with ``422`` on links with ``audience_type: \\"group\\"`` - their
    group determines visibility; switch the link to ``audience_type: \\"general\\"``
    first."

    A refusal rather than a precedence order, deliberately. Precedence would answer the
    request and silently pick a winner between two scopes the specification says are
    deliberately distinct, and the rep would read one scope's answer while believing they
    were reading the other. Nothing here invents a third audience type either: the
    research names two, so a third is a bad request.
    """

    kind = str(audience_type or "").strip().lower()
    if kind not in vocab.AUDIENCE_TYPES:
        raise AudienceRuleError(
            f"{kind or 'an empty value'!r} is not an audience type.",
            {
                "audience_type": (
                    f"audience_type must be {vocab.AUDIENCE_GENERAL} or {vocab.AUDIENCE_GROUP}."
                )
            },
        )
    if kind == vocab.AUDIENCE_GROUP:
        raise ScopeConflict(vocab.SCOPE_CONFLICT_MESSAGE, {"audience_type": vocab.SCOPE_CONFLICT_MESSAGE})
    return kind


# --------------------------------------------------------------------------- #
# Membership
# --------------------------------------------------------------------------- #


def resolve_membership(
    group: Mapping[str, Any] | None, members: Sequence[Mapping[str, Any]], email: Any
) -> dict[str, Any]:
    """Which of the three steps admitted this address, and what the group lets it see.

    The three steps in the fixed order the data flow names: "explicit email, then domain,
    then ``allow_all``". Each returns the step that matched rather than a boolean, so the
    page can tell a rep which rule admitted the viewer. A boolean would lose that, and the
    difference is what a rep needs when a domain admits someone they did not expect.

    ``allow_all`` is checked first even though it is listed third, because it is a
    short-circuit and not a fallback: quoted, "anyone who passes the link's other access
    gates is treated as a group member - the email/domain membership check is skipped".
    Checking the narrow rules first would report an explicit member as an open-group
    member whenever both were true, which is the wrong way round for a rule whose whole
    purpose is to admit people the narrow rules would not.

    The result carries ``allow_all`` and the group's domains whatever the outcome, because
    "why was this address refused" is a question a rep asks after every refusal and the
    answer is in those two fields.
    """

    group = dict(group or {})
    allow_all = bool(group.get(vocab.ALLOW_ALL))
    domains = [str(value) for value in (group.get(vocab.DOMAINS_FIELD) or [])]
    address = str(email or "").strip().lower()

    result: dict[str, Any] = {
        vocab.EMAIL_FIELD: address or None,
        vocab.ALLOW_ALL: allow_all,
        vocab.DOMAINS_FIELD: domains,
        vocab.MEMBERSHIP_STEP_FIELD: vocab.MEMBERSHIP_NONE,
        vocab.MEMBER_OF_FIELD: bool(group.get(vocab.GROUP_ID_FIELD)),
        vocab.MATCHED_DOMAIN_FIELD: None,
    }

    if not address:
        return result

    if allow_all:
        result[vocab.MEMBERSHIP_STEP_FIELD] = vocab.MEMBERSHIP_ALLOW_ALL
        return result

    for member in members:
        candidate = str((member.get("data") or {}).get(vocab.EMAIL_FIELD) or "").strip().lower()
        if candidate and candidate == address:
            result[vocab.MEMBERSHIP_STEP_FIELD] = vocab.MEMBERSHIP_BY_EMAIL
            result[vocab.MEMBER_ID_FIELD] = member.get("id")
            return result

    domain = domain_of(address)
    if domain and domain in domains:
        result[vocab.MEMBERSHIP_STEP_FIELD] = vocab.MEMBERSHIP_BY_DOMAIN
        result[vocab.MATCHED_DOMAIN_FIELD] = domain
    return result


def is_member(membership: Mapping[str, Any]) -> bool:
    """Whether the resolved membership admits the viewer at all.

    Separate from the mapping on purpose. The step and the answer are two questions: the
    step says which rule fired, and this says whether any rule did. Collapsing them is how
    a page ends up rendering "matched by domain" for a refusal.
    """

    return membership.get(vocab.MEMBERSHIP_STEP_FIELD) != vocab.MEMBERSHIP_NONE


# --------------------------------------------------------------------------- #
# Deciding one item
# --------------------------------------------------------------------------- #


def entry_for(
    permissions: Mapping[str, Mapping[str, Any]], item_id: str, item_type: str
) -> Mapping[str, Any] | None:
    """The row granting this item, or ``None`` when there is none.

    ``None`` is the load-bearing answer. It is what "A new group sees **nothing** until you
    grant permissions" looks like in code, and :func:`decide_item` turns it into a refusal.
    There is no default entry, no wildcard and no inheritance for a caller to reach.
    """

    return permissions.get(f"{item_type}:{item_id}")


def decide_item(entry: Mapping[str, Any] | None) -> dict[str, Any]:
    """One item's outcome, from its row alone.

    Four answers, all of them refusals except the first:

    * ``visible`` - ``can_view`` and ``can_download`` are both true.
    * ``view_only`` - ``can_view`` is true and ``can_download`` is false. The researched
      shape, and the reason the two flags are separate.
    * ``hidden_no_permission`` - no row at all. The default-deny sentence.
    * ``hidden_can_view_false`` - a row exists and says no.

    The distinction between the last two is not cosmetic. A rep who sees a document as
    hidden needs to know whether nobody granted it or somebody revoked it, because the
    first is the state the workflow ships in and the second is an edit somebody made.
    """

    if entry is None:
        return {
            "state": vocab.HIDDEN_NO_PERMISSION,
            vocab.DENY_REASON_FIELD: vocab.DENY_NO_PERMISSION_ROW,
            vocab.CAN_VIEW: False,
            vocab.CAN_DOWNLOAD: False,
            vocab.ROW_PRESENT_FIELD: False,
        }

    can_view = bool(entry.get(vocab.CAN_VIEW))
    can_download = bool(entry.get(vocab.CAN_DOWNLOAD))
    if not can_view:
        return {
            "state": vocab.HIDDEN_NO_PERMISSION,
            vocab.DENY_REASON_FIELD: vocab.DENY_CAN_VIEW_FALSE,
            vocab.CAN_VIEW: False,
            vocab.CAN_DOWNLOAD: False,
            vocab.ROW_PRESENT_FIELD: True,
        }
    return {
        "state": vocab.VISIBLE if can_download else vocab.VIEW_ONLY,
        vocab.DENY_REASON_FIELD: None,
        vocab.CAN_VIEW: True,
        vocab.CAN_DOWNLOAD: can_download,
        vocab.ROW_PRESENT_FIELD: True,
    }


# --------------------------------------------------------------------------- #
# Ancestor auto-visibility
# --------------------------------------------------------------------------- #


def ancestors_of(
    item_id: str, item_type: str, folders: Mapping[str, Mapping[str, Any]]
) -> list[tuple[str, str]]:
    """The folder chain above one item, nearest parent first, ending at the room root.

    ``folders`` maps a folder record id to a mapping carrying
    :data:`~dsr.audience_permissions.vocabulary.PARENT_FOLDER_FIELD`. The walk stops at
    the root sentinel, at a folder whose parent is not in the map, and at a cycle.

    The cycle guard is not defensive coding for its own sake. ``parentFolderId`` lives in
    an open JSON payload, so a team may well point a folder at its own parent, and a walk
    without a guard turns one bad row into a request that never returns. A malformed tree
    must answer with the chain it can prove, not hang.
    """

    chain: list[tuple[str, str]] = []
    if item_type != vocab.ITEM_TYPE_DOCUMENT:
        return chain

    seen: set[str] = set()
    current = str((folders.get(item_id) or {}).get(vocab.PARENT_FOLDER_FIELD) or vocab.ROOT_FOLDER)
    while current and current != vocab.ROOT_FOLDER:
        if current in seen:
            break
        seen.add(current)
        chain.append((current, vocab.ITEM_TYPE_FOLDER))
        current = str((folders.get(current) or {}).get(vocab.PARENT_FOLDER_FIELD) or vocab.ROOT_FOLDER)
    return chain


def plan_ancestor_grants(
    entries: Sequence[Mapping[str, Any]],
    permissions: Mapping[str, Mapping[str, Any]],
    folders: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """The folder rows to write so every newly visible item is reachable in the tree.

    Quoted: "Ancestor folders of any item made visible are automatically set to
    ``can_view: true`` so the folder tree stays navigable."

    Two rules, and both are in the source rather than invented here:

    * **Only ancestors of items that are visible in the result.** An item whose row keeps
      ``can_view`` false contributes no ancestors, because it is not going to be shown and
      opening its parent would reveal a folder with nothing in it.
    * **Only rows that need it.** An ancestor already at ``can_view: true`` is not
      rewritten, so a grant that touches one document does not bump a hundred folders'
      revisions.

    Download is not granted to an ancestor. The source says ``can_view: true``, and
    granting download to a parent folder the rep never chose would hand out bytes that
    were not granted. The row it writes carries ``can_download: false``, and
    :func:`decide_item` reports those folders as ``view_only`` rather than visible.
    """

    wanted: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not entry.get(vocab.CAN_VIEW):
            continue
        for folder_id, folder_type in ancestors_of(entry["item_id"], entry["item_type"], folders):
            key = entry_key({"item_type": folder_type, "item_id": folder_id})
            current = permissions.get(key)
            if current is not None and bool(current.get(vocab.CAN_VIEW)):
                continue
            wanted[key] = {
                "item_id": folder_id,
                "item_type": folder_type,
                vocab.CAN_VIEW: True,
                vocab.CAN_DOWNLOAD: False,
                vocab.AUTO_OPENED_FIELD: True,
            }

    return [wanted[key] for key in sorted(wanted)]


# --------------------------------------------------------------------------- #
# Summary counts
# --------------------------------------------------------------------------- #


def group_counts(
    member_rows: Sequence[Mapping[str, Any]],
    link_rows: Sequence[Mapping[str, Any]],
    permission_rows: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    """The three counts on a group card, counted rather than stored.

    ``member_count`` and ``link_count`` are the specification's own field names on
    ``DataroomGroup``. Counting them here rather than keeping a counter column means a
    count cannot drift from the rows it describes, and a member removed by another route
    cannot leave a group claiming a member who is gone.
    """

    return {
        vocab.MEMBER_COUNT_FIELD: len(member_rows),
        vocab.LINK_COUNT_FIELD: len(link_rows),
        vocab.PERMISSION_COUNT_FIELD: len(permission_rows),
    }