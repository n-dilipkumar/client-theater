"""WF-012: generate a personalised room programmatically from a template.

The workflow
------------
A **template** is a shell: a named set of blocks carrying ``{{ variable }}``
references, plus a declaration of the variables it expects. It is never itself
a room and is never published. Generating means handing the template a map of
substitution values and getting back a real room record, personalised, audited,
and either a draft or published.

The research this follows is
``docs/research/digital-sales-room-workflows/wf/WF-012.md`` (Qwilr's
``POST /v1/pages`` and the variables model). What follows from a primary source
is marked **[sourced]**; what is a design inference is marked
**[inference]** so nobody downstream mistakes one for the other.

Rules taken from the sources
----------------------------

**[sourced]** Exactly one of a template or a saved-blocks list authorises a
generation. Here a template is mandatory and blocks are not accepted as an
alternative input, because saved-block composition is a different workflow.

**[sourced]** Publication is opt-in per call: ``published`` defaults to false
and nothing is ever published implicitly.

**[sourced]** Expiry is set at creation, and "the count of days starts when you
publish the page. A draft page keeps the setting until you publish it." The
stored ``expiry`` is therefore retained on a draft and the deadline is derived
from ``published_at``, never from ``created_at``.

**[sourced]** ``metadata`` is arbitrary caller data. It is stored verbatim and
echoed back untouched, because the point of it is to survive a round trip
through a system that knows nothing about the caller's domain.

**[sourced]** ``tags`` are case-sensitive, so they are stored verbatim.

**[sourced]** Caller-defined identifiers are a first-class extension seam. The
research quotes the vendor's own external-id rule for taxes: "You can also
supply your own ``id`` ... You cannot change the id after you create the tax. To
use a different ID, delete the tax and create a new one." A generated room
therefore takes a caller-supplied ``external_id`` that is unique across live
rooms, and the way to move a room onto a different one is to delete it and
generate again.

**[sourced]** Substitutions are hierarchical. Page-level substitutions "can be
overwritten if the same keys are defined in the block-level substitutions", so
a block-level definition wins for that block. Chosen with Jev
(``block_level_wins``, confidence 0.97) over the intuitive inverse.

Design inferences, not specifications
-------------------------------------

**[inference] Repeating keys.** The sources document the *data* shape of a
repeating key -- a list of objects -- but nothing documents how a template
marks where a list repeats or what binds inside an iteration. A block therefore
declares ``repeat`` (the key) and ``item`` (the per-row template) and each
element is bound to ``item``. Chosen with Jev
(``block_repeats_with_item_binding``, confidence 0.80; Jev's own
"is the evidence decisive" answer was only 0.19, so this is the shakiest part of
the design and the cheapest to change).

**[inference] Missing values are marked, not blanked.** A reference with no
value renders as ``[unresolved: key]`` and is listed in ``unresolved_variables``.
Silently blanking is how a half-personalised room reaches a buyer.

**[inference] Declared variables are advisory.** A template's ``variables``
list drives the UI form and the docs. It is not a validation gate: a value the
template never declared is rendered if a block references it, and a value no
block references is kept and reported as unused. Rejecting either would mean a
team adding a field has to coordinate with whoever owns the template, which is
exactly what the schema-flexibility rule forbids.

**[inference] Status is derived, never stored.** ``status`` is computed from
``published``, ``published_at`` and ``expiry`` on every read, so a room that
has silently run past its expiry reads as ``declined`` without a background
job, and a stored status can never drift from the facts it is derived from.
The vendor reaches the same state by having the UI change it; the research gap
note for this domain records that expiry-driven decline is only observable by
polling.

**[inference] A batch is all-or-nothing.** One request, one transaction, one
audit row. ``MAX_BATCH`` borrows the vendor's documented ceiling of 50
generated pages from a single CSV, which is documented for that CSV path and
not for an API, so the number is a borrowed default rather than a sourced
limit. It is a single named constant.

Deliberately not built here
---------------------------

* **Serving the public link.** Publication state is modelled because it is
  documented, but the unauthenticated share view is a separate workflow (this
  domain's WF-011) and the research records that the vendor's access-policy
  controls are UI-only with no API (gap 8). No link or token is invented here.
* **Conditional content** (WF-013) and **webhooks** (gap 10) are out of scope.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from dsr.db.audited import RecordNotFound, utcnow
from dsr.store import RecordStore

TEMPLATE_COLLECTION = "template"
ROOM_COLLECTION = "room"

#: **[inference]** The vendor documents a ceiling of 50 generated pages from a
#: single CSV upload. That ceiling is documented for the CSV path, not for an
#: API batch; it is reused here because generating an unbounded list inside one
#: transaction is the failure mode worth avoiding. Change this one constant.
MAX_BATCH = 50

#: ``{{ key }}`` with optional inner whitespace. Dotted paths are allowed so a
#: nested value, or an element bound during a repeat, can be referenced.
_TOKEN = re.compile(r"\{\{\s*([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)\s*\}\}")

#: What a reference with no supplied value renders as. Loud on purpose.
UNRESOLVED = "[unresolved: {key}]"

_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off"}


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class GenerationError(ValueError):
    """The request is malformed. Surfaces as HTTP 400."""


class UnknownTemplate(LookupError):
    """The named template does not exist. Surfaces as HTTP 404."""


class GenerationConflict(RuntimeError):
    """The request collides with current state. Surfaces as HTTP 409."""


# --------------------------------------------------------------------------- #
# Result shapes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Rendered:
    """The result of applying substitutions to a template. Pure, no I/O."""

    content: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    unused: list[str] = field(default_factory=list)
    problems: list[dict[str, Any]] = field(default_factory=list)

    def as_payload(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "unresolved_variables": self.unresolved,
            "unused_substitutions": self.unused,
            "problems": self.problems,
        }

    @property
    def clean(self) -> bool:
        return not self.problems and not self.unresolved


@dataclass(frozen=True)
class GenerationRequest:
    """One generation call. Every field beyond the template is optional."""

    template_id: str
    name: str
    substitutions: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    tags: Sequence[str] = ()
    owner_id: str | None = None
    account: str | None = None
    expiry: Mapping[str, Any] | None = None
    published: bool = False
    external_id: str | None = None


# --------------------------------------------------------------------------- #
# Coercion helpers. Lenient where a team should be free, strict where silence
# would ship a broken room.
# --------------------------------------------------------------------------- #


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise GenerationError(f"{label} must be an object")
    return dict(value)


def _optional_str(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise GenerationError(f"{label} must be a non-empty string")
    return value.strip()


def _as_bool(value: Any, label: str) -> bool:
    """Accept a real bool, or a string/number spelling of one.

    Deliberately not ``bool(value)``: the string ``"false"`` is truthy in
    Python, and a room published because someone posted ``"false"`` is a
    room the buyer sees that nobody meant to publish.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
    raise GenerationError(f"{label} must be true or false")


def normalise_expiry(spec: Any) -> dict[str, Any] | None:
    """Validate the ``expiry`` object and freeze its meaning.

    ``[sourced]`` The vendor's object is an enable flag plus a day count, and
    the day count only begins at publication. Both are preserved verbatim in
    the stored record so the setting the caller supplied stays auditable.
    """
    if spec is None:
        return None
    if not isinstance(spec, Mapping):
        raise GenerationError("expiry must be an object")

    days = spec.get("days")
    if days is not None and (isinstance(days, bool) or not isinstance(days, int)):
        raise GenerationError("expiry.days must be a whole number of days")
    if isinstance(days, int) and days <= 0:
        raise GenerationError("expiry.days must be greater than zero")

    # Absent an explicit flag, a stated day count is the enable.
    if "enabled" in spec:
        enabled = _as_bool(spec["enabled"], "expiry.enabled")
    else:
        enabled = days is not None

    if enabled and days is None:
        raise GenerationError("expiry.days is required when expiry is enabled")

    frozen: dict[str, Any] = {"enabled": enabled, "starts_on": "publish"}
    if days is not None:
        frozen["days"] = days
    return frozen


def parse_request(payload: Mapping[str, Any]) -> GenerationRequest:
    """Turn a caller's JSON body into a :class:`GenerationRequest`."""
    if not isinstance(payload, Mapping):
        raise GenerationError("request body must be an object")

    template_id = _optional_str(payload.get("template_id"), "template_id")
    if not template_id:
        raise GenerationError("template_id is required")

    name = _optional_str(payload.get("name"), "name")
    if not name:
        raise GenerationError("name is required")

    raw_tags = payload.get("tags") or []
    if isinstance(raw_tags, str) or not isinstance(raw_tags, Sequence):
        raise GenerationError("tags must be an array of strings")
    tags: list[str] = []
    for tag in raw_tags:
        # Case-sensitive per the source, so no case folding; only blanks go.
        text = _optional_str(tag, "tags[]")
        if text and text not in tags:
            tags.append(text)

    return GenerationRequest(
        template_id=template_id,
        name=name,
        substitutions=_require_mapping(payload.get("substitutions"), "substitutions"),
        metadata=_require_mapping(payload.get("metadata"), "metadata"),
        tags=tuple(tags),
        owner_id=_optional_str(payload.get("owner_id"), "owner_id"),
        account=_optional_str(payload.get("account"), "account"),
        expiry=normalise_expiry(payload.get("expiry")),
        published=_as_bool(payload.get("published"), "published"),
        external_id=_optional_str(payload.get("external_id"), "external_id"),
    )


def normalise_key(label: str) -> str:
    """Derive an API reference key from a human label.

    **[sourced]** The editor shows a label and the API takes a reference key:
    a variable created as ``Hello World`` is referenced as ``hello_world``.
    Only used to *derive* a suggestion; the server accepts whatever key the
    caller actually uses, because refusing keys would make the template owner a
    gate on a team's field names.
    """
    cleaned = re.sub(r"[^0-9a-zA-Z]+", "_", str(label).strip().lower())
    cleaned = cleaned.strip("_")
    return cleaned or "variable"


# --------------------------------------------------------------------------- #
# Substitution
# --------------------------------------------------------------------------- #


def _lookup(values: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    """Resolve ``a.b.c`` against a values map. Returns (found, value)."""
    if path in values:
        return True, values[path]
    cursor: Any = values
    for part in path.split("."):
        if isinstance(cursor, Mapping) and part in cursor:
            cursor = cursor[part]
        else:
            return False, None
    return True, cursor


def _as_text(value: Any) -> str:
    """Render a supplied value as text.

    A structured value in a scalar position is serialised rather than dropped,
    so the operator sees exactly what would have been personalised.
    """
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def render_text(source: str, values: Mapping[str, Any]) -> tuple[str, list[str]]:
    """Substitute ``{{ key }}`` references. Returns (text, missing keys).

    Unknown keys become a visible marker rather than an empty string, and are
    reported so the caller can see which value they forgot.
    """
    missing: list[str] = []

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        found, value = _lookup(values, key)
        if not found or value is None:
            if key not in missing:
                missing.append(key)
            return UNRESOLVED.format(key=key)
        return _as_text(value)

    return _TOKEN.sub(substitute, source), missing


def _referenced_roots(source: str) -> set[str]:
    return {match.group(1).split(".")[0] for match in _TOKEN.finditer(source)}


#: The name a repeating block's element is bound to inside its item template.
ITEM_BINDING = "item"


def _is_actionable(key: str) -> bool:
    """Can the caller actually supply this key?

    Inside a repeating block, ``{{item.x}}`` refers to the element currently
    being rendered. When it does not resolve, the thing the caller forgot is the
    repeating key, not ``item.x`` -- so reporting the latter would fill
    ``unresolved_variables`` with names nobody can pass. The actionable key is
    already reported as ``unresolved_repeat``.
    """
    return key.split(".")[0] != ITEM_BINDING


def _as_lines(text: str) -> list[str]:
    """A block's rendered text, as lines.

    Uniform shape for every block so a viewer never has to branch on whether a
    block repeated. A scalar block is one line, or several if its text contains
    newlines; a repeating block contributes one line per element.
    """
    return [line for line in text.split("\n")] if text else [""]


def render(
    template: Mapping[str, Any],
    substitutions: Mapping[str, Any],
    *,
    label: str = "template",
) -> Rendered:
    """Apply substitutions to a template's blocks. Pure, no I/O.

    Precedence is ``{**page_level, **block_level}``: a block-level definition
    wins for that block. **[sourced]** and confirmed with Jev
    (``block_level_wins``, 0.97).
    """
    blocks = template.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        # Reachable even though `declare` rejects a blockless template: the
        # generic records API can write a `template` record directly, so the
        # generation path cannot assume the shape was checked.
        raise GenerationError(
            f"{label} has no blocks; a template with no blocks cannot be personalised"
        )

    page_level = dict(substitutions)
    content: list[dict[str, Any]] = []
    unresolved: list[str] = []
    referenced: set[str] = set()
    problems: list[dict[str, Any]] = []

    for index, raw in enumerate(blocks):
        block = dict(raw) if isinstance(raw, Mapping) else {}
        block_level = block.get("substitutions")
        block_level = dict(block_level) if isinstance(block_level, Mapping) else {}
        # [sourced] block-level definitions overwrite page-level values.
        values = {**page_level, **block_level}

        repeat_key = block.get("repeat")
        if isinstance(repeat_key, str) and repeat_key.strip():
            key = repeat_key.strip()
            # Track the root, not the dotted path, so `unused` compares like
            # with like against the keys the caller actually supplied.
            referenced.add(key.split(".")[0])
            item_template = block.get("item")
            item_template = item_template if isinstance(item_template, str) else ""
            if not item_template:
                problems.append(
                    {
                        "code": "repeat_without_item",
                        "key": key,
                        "block": block.get("id") or f"block[{index}]",
                        "message": f"block repeats on {key!r} but declares no item template",
                    }
                )
            found, elements = _lookup(values, key)
            if not found or elements is None:
                if key not in unresolved:
                    unresolved.append(key)
                problems.append(
                    {
                        "code": "unresolved_repeat",
                        "key": key,
                        "block": block.get("id") or f"block[{index}]",
                        "message": f"no value supplied for repeating key {key!r}",
                    }
                )
                # [inference] One placeholder line, not zero lines: an empty
                # section reads as "there is nothing here" rather than "we
                # forgot to pass this".
                placeholder, _ = render_text(item_template, {})
                lines = _as_lines(placeholder)
            elif not isinstance(elements, list):
                problems.append(
                    {
                        "code": "repeat_type",
                        "key": key,
                        "block": block.get("id") or f"block[{index}]",
                        "message": (
                            f"repeating key {key!r} was given a "
                            f"{type(elements).__name__}, expected an array"
                        ),
                    }
                )
                lines = [UNRESOLVED.format(key=key)]
            else:
                lines = []
                for element in elements:
                    row, missing = render_text(
                        item_template, {**values, ITEM_BINDING: element}
                    )
                    # `item.x` misses are not the caller's problem here: the
                    # actionable key is the repeating key, already reported
                    # above if it was missing. A page-level key that an item
                    # template also references is still worth surfacing.
                    for key_name in missing:
                        root = key_name.split(".")[0]
                        if _is_actionable(key_name) and root not in unresolved:
                            unresolved.append(root)
                    lines.extend(_as_lines(row))
        else:
            text = block.get("text")
            text = text if isinstance(text, str) else ""
            referenced |= _referenced_roots(text)
            rendered, missing = render_text(text, values)
            unresolved.extend(k for k in missing if k not in unresolved)
            lines = _as_lines(rendered)

        entry: dict[str, Any] = {
            "index": index,
            "id": str(block.get("id") or f"block-{index}"),
            "kind": str(block.get("kind") or "text"),
            "lines": lines,
        }
        if isinstance(repeat_key, str) and repeat_key.strip():
            entry["repeat"] = repeat_key.strip()
        content.append(entry)

    unused = [key for key in page_level if key not in referenced]
    return Rendered(content=content, unresolved=unresolved, unused=unused, problems=problems)


# --------------------------------------------------------------------------- #
# Publication and expiry
# --------------------------------------------------------------------------- #


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def derive_state(data: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Derive publication state from the stored facts. Never stored, so never drifts.

    **[sourced]** The link "stops working after the number of days that you set,
    and the status of the page becomes ``declined``", and the count starts at
    publication. **[inference]** The exact instant is ``published_at`` plus
    ``days`` x 24h: the sources say "after the number of days that you set" but
    never say whether the boundary is midnight or a rolling window, so a rolling
    window from the publication instant is the only reading that behaves
    correctly for a room published at 23:50.
    """
    now = now or datetime.now(timezone.utc)
    published = bool(data.get("published"))
    published_at = data.get("published_at")

    expiry = data.get("expiry")
    expiry = dict(expiry) if isinstance(expiry, Mapping) else {}
    days = expiry.get("days") if expiry.get("enabled") else None
    if not isinstance(days, int) or isinstance(days, bool) or days <= 0:
        days = None

    deadline: datetime | None = None
    started = _parse_iso(published_at)
    if published and started is not None and days is not None:
        deadline = started + timedelta(days=days)
    expires_at = deadline.isoformat(timespec="milliseconds") if deadline else None

    if not published:
        status = "draft"
    elif deadline is not None and now >= deadline:
        status = "declined"
    else:
        status = "published"

    return {
        "published": published,
        "status": status,
        "published_at": published_at if published else None,
        "expires_at": expires_at,
        "expiry": expiry or None,
    }


# --------------------------------------------------------------------------- #
# The workflow
# --------------------------------------------------------------------------- #


class TemplateGenerator:
    """Generate rooms from templates, through the audited store only.

    Every write goes to :class:`~dsr.db.audited.AuditedDatabase` via
    :class:`~dsr.store.RecordStore`, so a generation is a normal audited
    record: one audit row per single generation, one row per batch.
    """

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    # -- templates ---------------------------------------------------------- #

    def declare(self, payload: Mapping[str, Any], *, actor: str | None = None,
                source: str = "POST /api/templates") -> dict[str, Any]:
        """Register or update a template shell.

        A template is an ordinary schema-flexible record, so it can equally be
        written through the generic records API. This method exists so the
        template's own invariants (it has blocks; it is not a room) are checked
        in one place.
        """
        data = _require_mapping(payload, "template")
        name = _optional_str(data.get("name"), "name")
        if not name:
            raise GenerationError("name is required")

        blocks = data.get("blocks")
        if not isinstance(blocks, list) or not blocks:
            raise GenerationError("blocks must be a non-empty array")
        for index, block in enumerate(blocks):
            if not isinstance(block, Mapping):
                # Silently skipping this would produce a room with a blank
                # section the buyer sees, so name the offending entry instead.
                raise GenerationError(
                    f"blocks[{index}] must be an object, got {type(block).__name__}"
                )

        record_data = {
            **data,
            "name": name,
            "blocks": blocks,
            # Advisory: drives the generator's form and the docs. See the
            # module docstring on why this is not a validation gate.
            "variables": data.get("variables") if isinstance(data.get("variables"), list) else [],
            "template_kind": "template",
        }

        existing = _optional_str(data.get("template_id"), "template_id")
        if existing:
            current = self.store.get(existing)
            if current is None:
                raise UnknownTemplate(existing)
            if current["collection"] != TEMPLATE_COLLECTION:
                raise GenerationConflict(f"{existing} is not a template")
            return self.store.update(
                existing, record_data, actor=actor, source=source
            )
        return self.store.create(
            TEMPLATE_COLLECTION, record_data, actor=actor, source=source
        )

    def templates(self, *, where: Mapping[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if where:
            return self.store.find(TEMPLATE_COLLECTION, where, limit=limit)
        return self.store.list(TEMPLATE_COLLECTION, limit=limit)

    def template(self, template_id: str) -> dict[str, Any]:
        record = self.store.get(template_id)
        if record is None or record["collection"] != TEMPLATE_COLLECTION:
            raise UnknownTemplate(template_id)
        return record

    # -- rendering ---------------------------------------------------------- #

    def preview(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Render a generation request without writing anything.

        The operator's safety net: see exactly what the buyer would see,
        including which values are missing, before committing a room.
        """
        request = parse_request(payload)
        record, rendered = self._render_request(request)
        # What the deadline would be if published right now, so the setting can
        # be checked before it is committed to a room.
        hypothetical = derive_state(
            {"published": True, "published_at": utcnow(), "expiry": request.expiry}
        )
        return {
            "template": {
                "id": record["id"],
                "name": record["data"].get("name"),
                "revision": record["revision"],
                "variables": record["data"].get("variables") or [],
            },
            "request": {
                "name": request.name,
                "tags": list(request.tags),
                "owner_id": request.owner_id,
                "external_id": request.external_id,
                "expiry": request.expiry,
            },
            "if_published_now": {
                "expires_at": hypothetical["expires_at"],
                "status": hypothetical["status"],
            },
            **rendered.as_payload(),
        }

    # -- generation --------------------------------------------------------- #

    def _render_request(self, request: GenerationRequest) -> tuple[dict[str, Any], Rendered]:
        """Fetch the template and render it, naming the template if it is unusable."""
        record = self.template(request.template_id)
        return record, render(
            record["data"], request.substitutions, label=f"template {record['id']}"
        )

    def _prepare(self, request: GenerationRequest, *, seen: dict[str, str] | None = None) -> dict[str, Any]:
        """Build the room payload, without writing. Raises on any problem."""
        record, rendered = self._render_request(request)
        template_data = record["data"]

        if request.external_id:
            key = request.external_id
            if seen is not None and key in seen:
                raise GenerationConflict(
                    f"external_id {key!r} appears twice in this batch"
                )
            clash = self.store.find(ROOM_COLLECTION, {"external_id": key}, limit=1)
            if clash:
                raise GenerationConflict(
                    f"external_id {key!r} is already used by room {clash[0]['id']}; "
                    "delete that room to reuse the id"
                )
            if seen is not None:
                seen[key] = request.name

        payload: dict[str, Any] = {
            "name": request.name,
            "template_id": record["id"],
            # Provenance: which exact revision of the shell produced this room.
            "template_name": template_data.get("name"),
            "template_revision": record["revision"],
            "substitutions": dict(request.substitutions),
            "metadata": dict(request.metadata),
            "tags": list(request.tags),
            "published": request.published,
            "published_at": utcnow() if request.published else None,
            "expiry": request.expiry,
            "generated": True,
            **rendered.as_payload(),
        }
        if request.owner_id:
            payload["owner_id"] = request.owner_id
        if request.account:
            payload["account"] = request.account
        if request.external_id:
            payload["external_id"] = request.external_id
        return payload

    def generate(self, request: GenerationRequest, *, actor: str | None = None,
                 source: str = "POST /api/generations") -> dict[str, Any]:
        """Generate one room. One write, one audit row."""
        payload = self._prepare(request)
        record = self.store.create(
            ROOM_COLLECTION, payload, actor=actor, source=source
        )
        return self._present(record)

    def generate_many(self, requests: Sequence[GenerationRequest], *, actor: str | None = None,
                      source: str = "POST /api/generations/bulk") -> dict[str, Any]:
        """Generate a batch of rooms. One transaction, one audit row.

        All or nothing: every item is rendered and every external id is checked
        before the single write, so a bad item in item 40 cannot leave items 1
        to 39 committed.
        """
        if not requests:
            raise GenerationError("a batch needs at least one item")
        if len(requests) > MAX_BATCH:
            raise GenerationError(
                f"a batch may contain at most {MAX_BATCH} items, got {len(requests)}"
            )

        seen: dict[str, str] = {}
        payloads = [self._prepare(request, seen=seen) for request in requests]
        created = self.store.bulk_create(
            ROOM_COLLECTION, payloads, actor=actor, source=source
        )
        return {"count": len(created), "rooms": [self._present(record) for record in created]}

    def publish(self, room_id: str, *, actor: str | None = None,
                source: str = "POST /api/generations/{room_id}/publish") -> dict[str, Any]:
        """Publish a generated room, which is when its expiry clock starts.

        **[sourced]** Publication is opt-in and the expiry count "starts when
        you publish the page. A draft page keeps the setting until you publish
        it", so this is the only place ``published_at`` is set for a room
        generated as a draft.
        """
        record = self.store.get(room_id)
        if record is None or record["collection"] != ROOM_COLLECTION:
            raise RecordNotFound(room_id)
        data = record["data"]
        if not data.get("template_id"):
            raise GenerationConflict(
                f"room {room_id} was not generated from a template and has nothing to publish"
            )
        if data.get("published"):
            raise GenerationConflict(f"room {room_id} is already published")

        updated = self.store.update(
            room_id,
            {"published": True, "published_at": utcnow()},
            actor=actor,
            source=source,
            expected_revision=record["revision"],
        )
        return self._present(updated)

    # -- reads -------------------------------------------------------------- #

    def generated(self, *, where: Mapping[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """List generated rooms, newest first.

        ``where`` filters on arbitrary JSON paths, so a team can ask for
        ``{"external_id": "sf-op-0001"}`` or ``{"published": true}`` without
        this method knowing either field exists.
        """
        records = (
            self.store.find(ROOM_COLLECTION, where, limit=limit)
            if where
            else self.store.list(ROOM_COLLECTION, limit=limit)
        )
        return [self._present(record) for record in records if record["data"].get("generated")]

    def get_generated(self, room_id: str) -> dict[str, Any]:
        record = self.store.get(room_id)
        if record is None or record["collection"] != ROOM_COLLECTION:
            raise RecordNotFound(room_id)
        if not record["data"].get("generated"):
            raise GenerationConflict(
                f"room {room_id} was not generated from a template; it is a plain room record"
            )
        return self._present(record)

    # -- presentation ------------------------------------------------------- #

    @staticmethod
    def _present(record: Mapping[str, Any]) -> dict[str, Any]:
        """The record envelope plus the state the workflow derived from it.

        The two are kept separate on purpose: ``data`` is exactly what the store
        holds, and ``generation`` holds only what is computed on read. A caller
        can therefore tell a stored fact from a derived one.
        """
        return {**record, "generation": derive_state(record["data"])}
