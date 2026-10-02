"""WF-079: projecting the audited write path into exportable evidence.

Everything here reads the product's existing audit trail through ``store.audit()``
and ``store.db.audit_count()`` - the two read paths the host already serves at
``/api/audit`` and ``/api/audit/{seq}``. It does not open the SQLite file, and it
does not keep a second copy of the events. That is not a stylistic preference: the
product's promise is that the audit log is the single append-only trail whose rows
commit in the same transaction as the changes they describe, and a parallel export
log would be a copy that can drift from the thing it claims to describe.

The one thing the audited trail does not carry is a client address. ``audit_log``
has no IP column and ``dsr.db.audited`` is a shared file this workflow may not
edit, so an address cannot be attached by the write path. :func:`observe_ip` is how
one gets attached, and the resolution below is explicit about which rows have one
and which do not - a row with no address is reported as
``ip_status: "not_captured"``, never as an empty string, never as ``"0.0.0.0"``,
and never dropped.

Researched shape
----------------
The entry layout follows the research's ``{id, user, action, reason,
date_created, ip_address}`` tuple, with the rest of the host's fields carried
alongside rather than replacing them:

* ``id`` and ``date_created`` come straight from ``seq`` and ``ts``.
* ``user.id`` is the audit row's ``actor``. ``user.email`` is resolved from that
  actor when the actor is itself an address, and is otherwise reported as
  ``null`` with a status - the host has no user directory to resolve against, and
  inventing one would be inventing a requirement.
* ``action`` is the integer code plus its description. See
  :mod:`dsr.audit_export.vocabulary` for how a row gets a code and why an
  unrecognised one is reported verbatim rather than rewritten.
* ``reason`` prefers a ``reason`` the writing feature declared on the record and
  falls back to the audit row's own summary, so a workflow that says why is
  exported saying why.
* ``ip_address`` is the observed address, masked in sandbox mode.

Sandbox masking
---------------
The research is exact about this: "The IP address from which the action was
performed. If a sandbox API key is used, this will be ``\"hidden\"``." A sandbox
key stands in here for the ``DSR_AUDIT_EXPORT_SANDBOX`` environment variable, read
at call time rather than at import time so a test can flip it. The masked value is
what the export carries *and* what the digest hashes, so a digest stays
reproducible from the export alone; see :mod:`dsr.audit_export.integrity`.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from dsr.audit_export import integrity, vocabulary
from dsr.store import RecordStore

#: Where observed client addresses live. A record, in the ordinary schema-flexible
#: store, audited like everything else - not a side table bolted onto SQLite, and
#: emphatically not a second event log: it holds an address and the audit
#: ``seq`` it belongs to, and no events, no actors, no before/after state.
IP_OBSERVATION_COLLECTION = "audit_ip_observation"

#: Collections whose audited changes are document lifecycle events.
#:
#: The research scopes the trail to documents ("Every document action is appended
#: to an append-only audit store"), and ``document`` is the collection this product
#: stores them in. A room's other collections - activities, bookings, CRM rows -
#: are reported with the fallback code rather than being force-fitted into a
#: document lifecycle they are not part of.
DOCUMENT_COLLECTIONS = frozenset({"document"})

#: Batch size for the ordered scan described on :func:`room_rows`.
SCAN_BATCH = 500

#: Page size for reading observation records, which have no room-scoped
#: ``count`` shortcut worth a second round trip.
OBSERVATION_BATCH = 500

IP_OBSERVED = "observed"
IP_HIDDEN = "hidden"
IP_NOT_CAPTURED = "not_captured"

EMAIL_FROM_ACTOR = "actor_is_an_address"
EMAIL_UNRESOLVED = "unresolved"


class ObservationRefused(ValueError):
    """An address attribution this workflow refuses.

    Its own type rather than a bare ``ValueError`` so the router can map it to a
    409 without also catching every other ``ValueError`` in the product, and so
    the refusal carries a code a caller can branch on.
    """

    code = "observation_refused"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "detail": self.detail,
            "remediation": (
                "An address is attributed once and never rewritten. The audit row "
                "it belongs to is identified by seq, or by request_id before the "
                "seq is known."
            ),
        }


# --------------------------------------------------------------------------- #
# Client addresses
# --------------------------------------------------------------------------- #


def peer_address(request: Any) -> str | None:
    """The socket peer of the request, or ``None`` when there is no client.

    The peer's address and nothing else. ``X-Forwarded-For`` is deliberately not
    consulted: it is a request header, so any caller can set it, and an audit
    export that let a caller choose its own recorded address would be worthless as
    evidence. A deployment behind a proxy that needs the forwarded address needs a
    trusted-proxy list, which belongs in the shared write path rather than in a
    feature.
    """
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client is not None else None
    text = str(host).strip() if host else ""
    return text or None


def observe_ip(
    store: RecordStore,
    *,
    ip_address: str | None,
    seq: int | None = None,
    request_id: str | None = None,
    user_agent: str | None = None,
    room_id: str | None = None,
    attributed_by: str | None = None,
    source: str,
) -> dict[str, Any]:
    """Attribute a client address to an audited change.

    First writer wins. A second observation for the same ``seq`` is refused rather
    than overwriting, because an audit export whose address can be rewritten after
    the fact is not evidence of anything - the point of the record is that the
    address it holds is the one that was observed.
    """
    if not ip_address:
        raise ObservationRefused("ip_address is required to attribute an address")
    if seq is None and not request_id:
        raise ObservationRefused("either seq or request_id is required to attribute an address")
    existing = _observation_by_seq(store, seq) if seq is not None else None
    if existing is not None:
        raise ObservationRefused(f"audit entry {seq} already has an attributed address")
    return store.create(
        IP_OBSERVATION_COLLECTION,
        {
            "seq": seq,
            "request_id": request_id,
            "ip_address": str(ip_address),
            "user_agent": user_agent or None,
            "attributed_by": attributed_by or None,
        },
        room_id=room_id,
        actor=attributed_by or None,
        source=source,
    )


def _observation_by_seq(store: RecordStore, seq: int | None) -> dict[str, Any] | None:
    if seq is None:
        return None
    found = store.find(IP_OBSERVATION_COLLECTION, {"seq": seq}, limit=1)
    return found[0] if found else None


def load_observations(store: RecordStore, room_id: str | None) -> dict[int, dict[str, Any]]:
    """Every address observed in a room, keyed by the audit entry it belongs to.

    One paged read rather than a lookup per entry: an export of a few hundred rows
    would otherwise be a few hundred queries, and the row count is the thing a
    compliance review pages through.
    """
    by_seq: dict[int, dict[str, Any]] = {}
    offset = 0
    while True:
        batch = store.list(
            IP_OBSERVATION_COLLECTION, room_id=room_id, limit=OBSERVATION_BATCH, offset=offset
        )
        if not batch:
            break
        for record in batch:
            data = record.get("data") or {}
            try:
                seq = int(data.get("seq"))
            except (TypeError, ValueError):
                continue
            by_seq.setdefault(seq, data)
        if len(batch) < OBSERVATION_BATCH:
            break
        offset += OBSERVATION_BATCH
    return by_seq


# --------------------------------------------------------------------------- #
# Reading the trail
# --------------------------------------------------------------------------- #


def room_rows(
    store: RecordStore,
    room_id: str,
    *,
    since: str | None = None,
    until: str | None = None,
    record_id: str | None = None,
    upto_seq: int | None = None,
) -> list[dict[str, Any]]:
    """Every audit row scoped to one room, ascending by ``seq``.

    Why the scan, and not a filter: ``AuditedDatabase.audit`` filters on
    ``collection``, ``record_id``, ``actor``, ``action``, ``request_id`` and
    ``since``. It has no ``room_id`` predicate, and that method lives in a shared
    file this workflow is not allowed to edit. Rather than guess at the room's
    collections - which would miss any collection invented after this line was
    written - the log is walked once and filtered here.

    That is O(rows in the audit log) per export request, which is fine for the
    volume this product writes and is *stated* rather than hidden. The fix belongs
    in the audited read path: one more equality predicate on a column that already
    exists, added to ``db.audit`` and ``db.audit_count`` alongside ``since``.
    Until then the cost is on this feature and no other, which is the right way
    round for a shared file four other agents are working next to.

    The ``record_id`` filter is the exception to all of that, and it is not
    applied here: it is pushed down to ``audit()`` instead, which has an index
    for it. :func:`document_rows` is the reader that does so.

    ``upto_seq`` exists for the CSV report. A generated report is a snapshot, and
    a snapshot has to be reproducible: without an upper bound on ``seq``, the same
    report regenerated tomorrow would pick up rows written since, and the digest
    recorded at generation would not match the file the download link served. The
    bound is recorded on the report and replayed on every read of it.

    Rows are returned oldest-first. ``audit()`` reads newest-first, so the scan
    accumulates in reverse and sorts; ordering by ``seq`` ascending matters because
    the export's chain is defined over that order.
    """
    collected: list[dict[str, Any]] = []
    offset = 0
    while True:
        batch = store.audit(since=since, limit=SCAN_BATCH, offset=offset)
        if not batch:
            break
        collected.extend(batch)
        if len(batch) < SCAN_BATCH:
            break
        offset += SCAN_BATCH

    matched = [row for row in collected if row.get("room_id") == room_id]
    if record_id is not None:
        matched = [row for row in matched if row.get("record_id") == record_id]
    if upto_seq is not None:
        matched = [row for row in matched if int(row.get("seq") or 0) <= int(upto_seq)]
    if until:
        matched = [row for row in matched if str(row.get("ts") or "") <= until]
    matched.sort(key=lambda row: int(row.get("seq") or 0))
    return matched


def document_rows(
    store: RecordStore,
    record_id: str,
    *,
    room_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[dict[str, Any]]:
    """Every audit row for one record, ascending by ``seq``.

    The researched route is ``GET /public/v2/documents/{document_id}/audit-trail``,
    keyed by document rather than by room, and this is its read path. Unlike
    :func:`room_rows` it pushes ``record_id`` down into ``audit()``, which filters
    on that column and has ``idx_audit_record`` behind it - so this read costs the
    rows for one document rather than a walk of the whole log.

    ``room_id`` stays available as a scope check rather than as a filter. The
    audit row already carries the room the change belonged to, so refusing a
    document trail for a room that does not own the record is a two-line guard
    instead of a second query, and it is the guard that stops one room's compliance
    lead reading another room's trail by guessing an id.
    """
    collected: list[dict[str, Any]] = []
    offset = 0
    while True:
        batch = store.audit(record_id=record_id, since=since, limit=SCAN_BATCH, offset=offset)
        if not batch:
            break
        collected.extend(batch)
        if len(batch) < SCAN_BATCH:
            break
        offset += SCAN_BATCH

    matched = collected
    if room_id is not None:
        matched = [row for row in matched if row.get("room_id") == room_id]
    if until:
        matched = [row for row in matched if str(row.get("ts") or "") <= until]
    matched.sort(key=lambda row: int(row.get("seq") or 0))
    return matched


# --------------------------------------------------------------------------- #
# One row -> one export entry
# --------------------------------------------------------------------------- #


def actor_email(actor: Any) -> tuple[str | None, str]:
    """Resolve an audit actor to an address, or say why it could not be.

    The host stores actors as short names (``dana``, ``sam``, ``system``), so there
    is nothing to resolve them against. When a caller has been writing its own
    address as the actor - which some features do - that address *is* the identity
    and is used directly. Otherwise the export reports ``null`` with
    ``email_status: "unresolved"``, because a guessed address is worse than an
    absent one in a compliance artefact.
    """
    text = str(actor or "").strip()
    if "@" in text:
        local, _, domain = text.partition("@")
        if local and domain and "." in domain:
            return text, EMAIL_FROM_ACTOR
    return None, EMAIL_UNRESOLVED


def derive_action_code(row: Mapping[str, Any]) -> tuple[int, str]:
    """The integer code for an audited row, and how it was arrived at.

    Four steps, in order, and the order is the contract:

    1. **Declared.** A writing feature stamped ``event_code`` on the record. This
       is the additive extension point the research asks for, and it needs no
       migration and no coordination.
    2. **Verification.** The change carries a pass/fail outcome. Resolved to a code
       when the method has a slot in the 47-54 band.
    3. **Lifecycle.** A document insert is code 1; a document update whose new
       status is one the research names is that code.
    4. **Fallback.** ``UNCLASSIFIED``. The row is still exported.

    A rejected verification attempt whose method has no slot is reported at step 2
    as ``verification_unallocable`` rather than falling through to step 4, so the
    export says *why* it has no code instead of leaving a reviewer to guess.
    """
    before = row.get("before_state")
    after = row.get("after_state")

    declared = vocabulary.declared_event_code(before, after)
    if declared is not None:
        return declared, "declared"

    facts = vocabulary.verification_facts(before, after)
    if facts["reported"]:
        code = vocabulary.verification_code(facts["method"], facts["outcome"])
        if code is not None:
            return code, "derived"
        return vocabulary.UNCLASSIFIED, "verification_unallocable"

    if row.get("collection") in DOCUMENT_COLLECTIONS:
        verb = str(row.get("action") or "")
        if verb == "insert":
            return vocabulary.DOCUMENT_CREATED, "derived"
        status = after.get("status") if isinstance(after, dict) else None
        code = vocabulary.LIFECYCLE_STATUS_CODES.get(str(status or "").strip().lower())
        if code is not None:
            return code, "derived"

    return vocabulary.UNCLASSIFIED, "fallback"


def declared_reason(row: Mapping[str, Any]) -> tuple[str, str]:
    """Why the change happened, preferring a declared reason over the summary."""
    for payload in (row.get("after_state"), row.get("before_state")):
        if isinstance(payload, dict):
            reason = payload.get("reason")
            if reason:
                return str(reason), "declared"
    return str(row.get("summary") or ""), "summary"


def resolve_ip(
    row: Mapping[str, Any],
    observations: Mapping[int, Mapping[str, Any]],
    *,
    sandbox: bool,
) -> tuple[str | None, str, Mapping[str, Any] | None]:
    """The address for one audit row: observed, masked, or honestly absent."""
    observation = observations.get(int(row.get("seq") or -1))
    if observation is None:
        return None, IP_NOT_CAPTURED, None
    if sandbox:
        return "hidden", IP_HIDDEN, observation
    address = observation.get("ip_address")
    if not address:
        return None, IP_NOT_CAPTURED, observation
    return str(address), IP_OBSERVED, observation


def project(
    row: Mapping[str, Any],
    *,
    observations: Mapping[int, Mapping[str, Any]],
    sandbox: bool,
) -> dict[str, Any]:
    """One audit row as one export entry, before the chain is stamped."""
    seq = int(row.get("seq") or 0)
    code, code_source = derive_action_code(row)
    described = vocabulary.describe_code(code)
    reason, reason_source = declared_reason(row)
    actor = row.get("actor")
    email, email_status = actor_email(actor)
    ip_address, ip_status, observation = resolve_ip(row, observations, sandbox=sandbox)
    facts = vocabulary.verification_facts(row.get("before_state"), row.get("after_state"))

    verification: dict[str, Any] | None = None
    if facts["reported"]:
        verification = {
            "outcome": facts["outcome"],
            "method": facts["method"],
            "code": code if described.category == vocabulary.CATEGORY_VERIFICATION else None,
            "unallocable": code_source == "verification_unallocable",
        }

    return {
        # The research's tuple first, in its order.
        "id": seq,
        "user": {"id": actor, "email": email, "email_status": email_status},
        "action": {
            "code": described.code,
            "name": described.name,
            "category": described.category,
            "origin": described.origin,
            "description": described.description,
            "method": described.method,
            "outcome": described.outcome,
            "source": code_source,
        },
        "reason": reason,
        "date_created": row.get("ts"),
        "ip_address": ip_address,
        # Then the host's own columns, which a reviewer needs in order to chase a
        # row back to the record it describes.
        "seq": seq,
        "actor": actor,
        "reason_source": reason_source,
        "ip_status": ip_status,
        "ip_attributed_by": (observation or {}).get("attributed_by"),
        "user_agent": (observation or {}).get("user_agent"),
        "verification": verification,
        "collection": row.get("collection"),
        "record_id": row.get("record_id"),
        "room_id": row.get("room_id"),
        "request_id": row.get("request_id"),
        "source": row.get("source"),
        "duration_ms": row.get("duration_ms"),
    }


def matches_filters(
    entry: Mapping[str, Any],
    *,
    action: int | None = None,
    actor: str | None = None,
    collection: str | None = None,
) -> bool:
    """Whether an exported entry survives the caller's own filters.

    ``code`` and ``actor`` are pushed down to ``audit()``, which filters on its own
    ``action`` column - the record verb, not the integer code. These are the
    remaining filters, applied to the projected entry because that is where the
    codes live.
    """
    if action is not None and entry.get("action", {}).get("code") != action:
        return False
    if actor is not None and str(entry.get("actor") or "") != actor:
        return False
    if collection is not None and str(entry.get("collection") or "") != collection:
        return False
    return True


def _seal(
    rows: Sequence[Mapping[str, Any]],
    store: RecordStore,
    *,
    room_id: str | None,
    sandbox: bool,
    action: int | None,
    actor: str | None,
    collection: str | None,
) -> list[dict[str, Any]]:
    """Project, filter and stamp one ordered batch of audit rows.

    The single place the chain is applied. Two readers - the room trail and the
    document trail - share it so that a page row and a document row cannot drift
    into two different projections of the same audit row, which is the kind of
    difference a reviewer only finds by reading both code paths.

    The chain is sealed over the *filtered* entries, because the chain's job is to
    prove that *this* artefact is intact: a chain folded over rows the caller did
    not ask for would not be reproducible from what they were handed.
    """
    observations = load_observations(store, room_id)
    entries = [project(row, observations=observations, sandbox=sandbox) for row in rows]
    entries = [
        entry
        for entry in entries
        if matches_filters(entry, action=action, actor=actor, collection=collection)
    ]
    chains = integrity.seal(entries)
    for entry, chain in zip(entries, chains, strict=True):
        entry["digest"] = integrity.entry_digest(entry)
        entry["chain"] = chain
    return entries


def build_trail(
    store: RecordStore,
    room_id: str,
    *,
    sandbox: bool = False,
    since: str | None = None,
    until: str | None = None,
    action: int | None = None,
    actor: str | None = None,
    collection: str | None = None,
    record_id: str | None = None,
    upto_seq: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """The whole room-scoped trail, ascending by ``seq``, with the chain stamped.

    Returns the entries and the total that survived filtering.
    """
    rows = room_rows(
        store,
        room_id,
        since=since,
        until=until,
        record_id=record_id,
        upto_seq=upto_seq,
    )
    entries = _seal(
        rows,
        store,
        room_id=room_id,
        sandbox=sandbox,
        action=action,
        actor=actor,
        collection=collection,
    )
    return entries, len(entries)


def document_trail(
    store: RecordStore,
    record_id: str,
    *,
    room_id: str | None = None,
    sandbox: bool = False,
    since: str | None = None,
    until: str | None = None,
    action: int | None = None,
    actor: str | None = None,
    collection: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """One document's trail - the researched ``audit-trail`` route, keyed by document.

    "Retrieves the full audit trail for a **specified document**." Same projection,
    same chain and the same six hashed fields as the room trail; only the read
    differs, because ``audit()`` indexes ``record_id``.
    """
    rows = document_rows(store, record_id, room_id=room_id, since=since, until=until)
    entries = _seal(
        rows,
        store,
        room_id=room_id,
        sandbox=sandbox,
        action=action,
        actor=actor,
        collection=collection,
    )
    return entries, len(entries)


def signer_interactions(entries: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The verification events in a trail, with the address each one came from.

    "Captures signer IP addresses at each interaction" - so this is one row per
    verification attempt rather than one row per document, and a *failed* attempt
    appears here exactly as a passed one does. The research is explicit that
    "both success and failure produce audit actions, so a rejected attempt is as
    visible as a successful one"; filtering this list to the passes would be the
    exact failure it warns about, so nothing here filters.
    """
    interactions: list[dict[str, Any]] = []
    for entry in entries:
        facts = entry.get("verification")
        if not isinstance(facts, Mapping):
            continue
        action = entry.get("action") if isinstance(entry.get("action"), Mapping) else {}
        interactions.append(
            {
                "id": entry.get("id"),
                "seq": entry.get("seq"),
                "code": action.get("code") if action else None,
                "method": facts.get("method"),
                "outcome": facts.get("outcome"),
                "date_created": entry.get("date_created"),
                "ip_address": entry.get("ip_address"),
                "ip_status": entry.get("ip_status"),
                "user_agent": entry.get("user_agent"),
                "actor": entry.get("actor"),
            }
        )
    return interactions


def evidence_pack(
    store: RecordStore,
    record_id: str,
    *,
    room_id: str | None = None,
    sandbox: bool = False,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    """Everything the e-signature evidence asserts about one document, in one body.

    The researched user flow has four steps at the API and a fifth for e-signature
    flows: "the same evidence is also embedded in the final PDF as an audit-trail
    section, so it travels with the document". What travels here is a digest over
    the trail, the document hash and every signer interaction, so a pack handed to
    somebody outside this process carries one value that changes if any part of it
    is altered.

    It is **not** a PDF, and this build does not pretend otherwise - there is no
    byte stream here to merge pages into. What it does carry is the falsifiable
    half of that requirement: one digest a recipient can recompute, over evidence
    that travels with the document rather than living only inside the process that
    wrote it. See :func:`dsr.audit_export.integrity.pack_basis`.
    """
    rows = document_rows(store, record_id, room_id=room_id, since=since, until=until)
    entries = _seal(
        rows,
        store,
        room_id=room_id,
        sandbox=sandbox,
        action=None,
        actor=None,
        collection=None,
    )
    interactions = signer_interactions(entries)
    head = str(entries[-1]["chain"]) if entries else integrity.chain_seed()
    return {
        "record_id": record_id,
        "room_id": room_id,
        "entries": entries,
        "signer_interactions": interactions,
        "digest": integrity.pack_digest(
            room_id=room_id,
            record_id=record_id,
            entries=entries,
            head=head,
            interactions=interactions,
        ),
        "basis": integrity.pack_basis(),
    }


def summarise(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts a compliance lead reads first: who acted, from where, and how it went."""
    actors: set[str] = set()
    addresses: set[str] = set()
    codes: dict[int, int] = {}
    verification_pass = 0
    verification_fail = 0
    unclassified = 0
    first_at: str | None = None
    last_at: str | None = None

    for entry in entries:
        actors.add(str(entry.get("actor") or ""))
        address = entry.get("ip_address")
        if address:
            addresses.add(str(address))
        code = entry.get("action", {}).get("code", vocabulary.UNCLASSIFIED)
        codes[code] = codes.get(code, 0) + 1
        if code == vocabulary.UNCLASSIFIED:
            unclassified += 1
        facts = entry.get("verification")
        if isinstance(facts, dict):
            if facts.get("outcome") == vocabulary.PASS:
                verification_pass += 1
            elif facts.get("outcome") == vocabulary.FAIL:
                verification_fail += 1
        stamp = entry.get("date_created")
        if stamp:
            first_at = stamp if first_at is None else min(first_at, str(stamp))
            last_at = stamp if last_at is None else max(last_at, str(stamp))

    not_captured = sum(1 for entry in entries if entry.get("ip_status") == IP_NOT_CAPTURED)
    hidden = sum(1 for entry in entries if entry.get("ip_status") == IP_HIDDEN)
    return {
        "entries": len(entries),
        "distinct_actors": len({actor for actor in actors if actor}),
        "distinct_addresses": len(addresses),
        "addresses_not_captured": not_captured,
        "addresses_masked": hidden,
        "verification_pass": verification_pass,
        "verification_fail": verification_fail,
        "unclassified": unclassified,
        "by_code": dict(sorted(codes.items())),
        "first_event": first_at,
        "last_event": last_at,
    }


def audit_total(store: RecordStore, *, action: str | None = None) -> int:
    """Total rows in the whole audit log, read through the audited wrapper.

    Used to show a room's trail against the log it is a window into, so a reviewer
    can see the trail is scoped rather than empty. ``RecordStore`` does not re-export
    ``audit_count``, so this reaches the audited database through ``store.db`` -
    still the one write path and its own read API, never a raw connection.
    """
    return int(store.db.audit_count(action=action))
