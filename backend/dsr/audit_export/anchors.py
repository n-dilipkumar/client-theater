"""WF-079: pinning a trail's chain head so a later read can falsify it.

Why this module exists, and what it is careful not to claim
-----------------------------------------------------------
Every export already reports a chain head, and recomputing it on a fresh read
looks like verification. It is not, on its own. The entries a fresh read produces
are sealed as they are produced, so re-sealing them necessarily agrees - the check
passes because the two sides were computed from the same rows microseconds apart.
It proves the artefact is internally consistent and nothing more.

An anchor is what turns that into a falsifiable claim. It is a record of the head
digest that existed *at the moment somebody pinned it*, together with the scope
it was computed over and the per-entry chains behind it. A later verification
recomputes the same scope from the current database and compares:

    head_now = sha256-fold(scope truncated at the anchor's upto_seq)

``head_now == anchor.head`` means the rows behind that anchor have not moved. If
they have, the answer names the first entry whose chain diverged rather than only
saying that something did.

**What this is not.** An anchor lives in the same database as the rows it
describes, so an attacker with write access can edit the anchor as well as the
trail. This detects divergence - a corrupted or partially restored database, a
defect in the write path, a row rewritten without the chain - and it is *not* a
defence against somebody who can recompute a digest. That defence is the export
carried out of the process: :func:`dsr.audit_export.integrity.integrity_payload`
publishes the head and the exact recipe, and the emailed download link in
:mod:`dsr.audit_export.reports` is a copy of it that this process does not keep.

Anchors are written as records through the audited store, so pinning one is
itself an event in the trail it describes.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.audit_export import integrity
from dsr.store import RecordStore

#: Where anchors live. A record in the ordinary schema-flexible store, audited like
#: everything else, so "a compliance lead pinned this trail" is a row in the log.
ANCHOR_COLLECTION = "audit_export_anchor"

#: Ceiling on how many per-entry chains an anchor records. Large enough that a
#: realistic room trail fits whole, and bounded because an anchor that can grow
#: without limit is a denial of service against the database it is protecting.
MAX_ANCHORED_ENTRIES = 5_000


def scope_of(
    room_id: str | None,
    *,
    record_id: str | None = None,
    action: int | None = None,
    actor: str | None = None,
    collection: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    """The filter an anchor was taken over, recorded so it can be reproduced.

    An anchor without its scope is a bare digest: the trail it describes is not
    reproducible because the caller cannot tell which rows were in it, and a
    digest nobody can reproduce is not evidence. Every filter the reader accepts
    goes in, including the ones that came out as ``None``.
    """
    return {
        "room_id": room_id,
        "record_id": record_id,
        "action": action,
        "actor": actor,
        "collection": collection,
        "since": since,
        "until": until,
    }


def pin(
    store: RecordStore,
    entries: Sequence[Mapping[str, Any]],
    *,
    scope: Mapping[str, Any],
    pinned_at: str,
    actor: str | None,
    source: str,
    room_id: str | None = None,
) -> dict[str, Any]:
    """Record the current head of a trail, with the chains behind it.

    Refuses to pin a trail larger than :data:`MAX_ANCHORED_ENTRIES` rather than
    truncating: a partial anchor that reads as a whole one is worse than no
    anchor, because it would report a divergence that is really a truncation.
    """
    if len(entries) > MAX_ANCHORED_ENTRIES:
        raise ValueError(
            f"a trail of {len(entries)} entries exceeds the {MAX_ANCHORED_ENTRIES} "
            "entries an anchor records; narrow the scope rather than anchoring a "
            "truncated trail"
        )
    head = str(entries[-1]["chain"]) if entries else integrity.chain_seed()
    return store.create(
        ANCHOR_COLLECTION,
        {
            "head": head,
            "entries": len(entries),
            "upto_seq": entries[-1]["seq"] if entries else None,
            "scope": dict(scope),
            "chains": [{"seq": entry["seq"], "chain": entry["chain"]} for entry in entries],
            "pinned_at": pinned_at,
            "pinned_by": actor or None,
            "document_hash": integrity.document_hash(scope.get("room_id"), entries),
        },
        room_id=room_id,
        actor=actor,
        source=source,
    )


def listing(
    store: RecordStore,
    *,
    room_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Every anchor pinned in a room, newest first.

    Kept separate from ``entries.load_observations`` rather than folded into it,
    because the two answer different questions. An observation says an address was
    seen for this audit row; an anchor says this chain head existed at this moment,
    over this scope. Merging them would let a reader take a network fact for a
    tamper-evidence one.
    """
    return store.list(ANCHOR_COLLECTION, room_id=room_id, limit=max(1, min(int(limit), 1000)))


def check(
    anchor: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare a recomputed trail against a pinned anchor.

    ``entries`` must be the trail rebuilt with the anchor's own recorded scope and
    truncated at the anchor's ``upto_seq`` - see :func:`dsr.audit_export.entries`
    for the readers that produce it. The comparison is over the pinned prefix
    only, because rows written *after* the anchor is a normal event and reporting
    it as divergence would make every anchor fail from the moment it was taken.
    """
    data = dict(anchor.get("data") or {})
    scope = dict(data.get("scope") or {})
    upto = data.get("upto_seq")
    recorded_chains = {row.get("seq"): row.get("chain") for row in (data.get("chains") or [])}

    if upto is None:
        rebuilt = list(entries)
    else:
        rebuilt = [entry for entry in entries if int(entry.get("seq") or 0) <= int(upto)]

    recomputed = integrity.seal(rebuilt)
    head_now = recomputed[-1] if recomputed else integrity.chain_seed()

    first_divergent: dict[str, Any] | None = None
    for entry, chain in zip(rebuilt, recomputed, strict=True):
        recorded = recorded_chains.get(entry.get("seq"))
        if recorded is None:
            first_divergent = {
                "seq": entry.get("seq"),
                "reason": "this entry was not in the anchor",
                "recomputed_chain": chain,
                "recorded_chain": None,
            }
            break
        if recorded != chain:
            first_divergent = {
                "seq": entry.get("seq"),
                "reason": "this entry's chain no longer matches the anchor",
                "recomputed_chain": chain,
                "recorded_chain": recorded,
            }
            break

    return {
        "anchor_id": anchor.get("id"),
        "pinned_at": data.get("pinned_at"),
        "pinned_by": data.get("pinned_by"),
        "scope": scope,
        "upto_seq": upto,
        "anchored_entries": data.get("entries"),
        "entries_recomputed": len(rebuilt),
        "head_at_anchor": data.get("head"),
        "head_now": head_now,
        "intact": first_divergent is None and head_now == data.get("head"),
        "first_divergent": first_divergent,
        "meaning": (
            "the rows behind this anchor have not moved since it was taken. It "
            "does not prove the trail is complete, and it is not a defence "
            "against a writer who can recompute a digest: the anchor lives in "
            "the same database as the trail. The export carried out of this "
            "process is the external witness."
        ),
    }
