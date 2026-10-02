"""WF-079: the tamper-evidence. What is hashed, in what order, and how to check it.

The research's claim for this workflow is the strongest one in the programme, and
it is also the easiest to fake:

    "Every Signature Request generates an Audit Trail... Automatically generated
    and cannot be modified."
    "**Tamper-evident: any modification invalidates the audit trail**"
    "Includes SHA-256 document hash proving the document wasn't altered after
    signing"
    -- Dropbox Sign, via docs/research/digital-sales-room-workflows/wf/WF-079.md

"Tamper-evident" is a claim about a *verifier*, not about a hash. A SHA-256
digest printed next to an export proves nothing unless somebody outside this
process can recompute it and get the same answer. So the specification below is
written to be reproducible by a third party from the export alone, and this module
is deliberately boring: no salt, no key, no secret, no clock, no randomness. Every
input is either a field of the row being hashed or a constant in this file. A
verifier needs the export and this file and nothing else - not the database, not
the API, not this application.

The specification, exactly
-------------------------
Let the entries of one export be ``e(1) .. e(n)``, ordered **ascending by
``seq``**. ``seq`` is the audit log's autoincrementing sequence number, so it is
the insertion order and is identical on every machine; see the note on
``rowid`` tie-breaking in ``dsr.db.audited`` for why an ordering on a random
``uuid4`` would not do.

**1. The canonical entry.** For each entry take exactly these six fields, in this
set, and serialise them::

    json.dumps(
        {
            "seq":         int,
            "date_created": str,
            "action_code": int,
            "actor":       str,
            "ip_address":  str,
            "reason":      str,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

A field that is absent or null is the empty string. Nothing else about the entry
is hashed, and the two derived fields ``digest`` and ``chain`` are deliberately
*not* among the six: they are outputs of this algorithm, and hashing them would
make the digest depend on itself.

**2. The entry digest.** ``digest(k) = sha256(canonical_entry(k)).hexdigest()``,
lowercase hex.

**3. The chain.** A seed, then one step per entry::

    chain(0) = sha256(b"wf-079-audit-chain-v1").hexdigest()
    chain(k) = sha256(b"wf-079-audit-chain-v1\\n" + chain(k-1) + b"\\n" + digest(k)).hexdigest()

``integrity.head`` is ``chain(n)``. Because each step consumes the previous step's
digest, a verifier that recomputes the chain learns not just *that* the export was
altered but *which entry* was the first one that no longer matches - the position
where the recomputed chain and the stored chain diverge.

**4. The document hash.** Dropbox Sign's SHA-256 is over the PDF bytes, "proving
the document wasn't altered after signing". This application has no PDF bytes, so
it does not claim that. What it hashes instead is the identity of what the trail
describes - which records, at which revisions, in which order::

    sha256(
        json.dumps(
            {
                "algorithm":  "sha-256",
                "kind":       "wf-079-document-hash-v1",
                "room_id":    str | None,
                "entries": [
                    {"seq", "date_created", "action_code", "collection",
                     "record_id", "room_id"},
                    ...
                ],
            },
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

That is a different and narrower claim than the vendor's, and
:func:`document_hash_basis` says so in the export itself rather than letting the
word "tamper-evident" imply a guarantee this build cannot make.

**``revision`` is not among the fields, and that is a finding rather than an
omission.** An earlier draft of this file hashed ``revision`` alongside the
others, and it always hashed ``null``: ``audit_log`` has no revision column (see
``backend/dsr/db/schema.sql``) and a projected entry therefore never carries one.
A field that is always null is worse than an absent field, because
:func:`document_hash_basis` was claiming coverage it did not have.

It could be made real - ``records`` does have a ``revision`` column - but only by
reading each record's *current* revision at export time, which would make the
digest depend on state the export does not carry and that has since moved on. A
hash a holder of the export cannot recompute is the failure this module exists to
avoid, so the field is out.

What this does and does not prove
---------------------------------
It proves that the entries this export lists are internally consistent and that
none of them was altered between the moment the export was built and the moment it
is verified, *provided* the verifier obtained the original export and its stored
digests together.

It does not prove the trail is complete, and no hash over a list can: a row that
was never recorded leaves no trace in the digest. Completeness is the audited
write path's claim - "the audit row is written in the same transaction as the
change" - not this module's. Nor does it prove the log has not been rewritten
from the top, because an attacker with write access can recompute the whole chain
after editing it; that needs an external witness, which is what the emailed
download link in :mod:`dsr.audit_export.reports` is a small step towards.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

#: Domain separator. Prefixing every digest with it means a digest from this
#: algorithm can never be mistaken for one taken over the same bytes by some other
#: scheme, and it is what lets the chain seed be a constant rather than a magic
#: empty string.
CHAIN_PREFIX = "wf-079-audit-chain-v1"

#: The document-hash envelope's discriminator, kept distinct from the chain prefix
#: so the two claims cannot be confused for one another.
DOCUMENT_HASH_KIND = "wf-079-document-hash-v1"

ALGORITHM = "sha-256"

#: The six fields hashed per entry, in the order the specification lists them.
#:
#: A tuple rather than a dict because the order is part of the published contract
#: and a reviewer should be able to read it top to bottom. ``sort_keys=True``
#: makes the serialisation independent of this order anyway, which is deliberate:
#: the order documents the contract, the sort guarantees it.
HASHED_FIELDS: tuple[str, ...] = (
    "seq",
    "date_created",
    "action_code",
    "actor",
    "ip_address",
    "reason",
)

#: The fields the document hash covers, per entry.
#:
#: Exactly the fields :func:`dsr.audit_export.entries.project` emits for an audit
#: row. A field listed here that the projection does not supply would hash as
#: ``null`` on every entry, which is why this tuple is not allowed to grow on its
#: own: see the note on ``revision`` in the module docstring.
DOCUMENT_HASH_ENTRY_FIELDS: tuple[str, ...] = (
    "seq",
    "date_created",
    "action_code",
    "collection",
    "record_id",
    "room_id",
)


def canonical_entry(
    *,
    seq: Any,
    date_created: Any,
    action_code: Any,
    actor: Any = None,
    ip_address: Any = None,
    reason: Any = None,
) -> bytes:
    """The exact bytes hashed for one entry.

    Public because it is the specification: a verifier outside this application
    must be able to produce these bytes, so the function is not private and the
    serialisation arguments are not configurable.
    """
    try:
        normalised_seq = int(seq)
    except (TypeError, ValueError):
        normalised_seq = 0
    try:
        normalised_code = int(action_code)
    except (TypeError, ValueError):
        normalised_code = 0
    payload = {
        "seq": normalised_seq,
        "date_created": "" if date_created is None else str(date_created),
        "action_code": normalised_code,
        "actor": "" if actor is None else str(actor),
        "ip_address": "" if ip_address is None else str(ip_address),
        "reason": "" if reason is None else str(reason),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def canonical_json(value: Any) -> bytes:
    """The one serialisation this module uses, exposed so a verifier can match it."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def entry_digest(entry: Mapping[str, Any]) -> str:
    """SHA-256 over one export entry's six canonical fields."""
    return hashlib.sha256(
        canonical_entry(
            seq=entry.get("seq"),
            date_created=entry.get("date_created"),
            action_code=(entry.get("action") or {}).get("code")
            if isinstance(entry.get("action"), Mapping)
            else entry.get("action_code"),
            actor=entry.get("actor"),
            ip_address=entry.get("ip_address"),
            reason=entry.get("reason"),
        )
    ).hexdigest()


def chain_seed() -> str:
    """``chain(0)``: the constant the first entry's chain step starts from."""
    return hashlib.sha256(CHAIN_PREFIX.encode("utf-8")).hexdigest()


def chain_step(previous: str, digest: str) -> str:
    """One chain step. Exposed so the fold can be re-implemented independently."""
    return hashlib.sha256(f"{CHAIN_PREFIX}\n{previous}\n{digest}".encode("utf-8")).hexdigest()


def seal(entries: Sequence[Mapping[str, Any]]) -> list[str]:
    """The running chain digest for every entry, in the order given.

    The returned list is parallel to ``entries``. Callers must pass entries
    ascending by ``seq``; :func:`dsr.audit_export.entries.build_trail` is what
    guarantees the order.
    """
    running = chain_seed()
    chains: list[str] = []
    for entry in entries:
        running = chain_step(running, entry_digest(entry))
        chains.append(running)
    return chains


def document_hash(room_id: str | None, entries: Sequence[Mapping[str, Any]]) -> str:
    """SHA-256 over the identity of what the trail describes.

    Deliberately narrower than the vendor's document hash; see the module
    docstring and :func:`document_hash_basis`.
    """
    payload = {
        "algorithm": ALGORITHM,
        "kind": DOCUMENT_HASH_KIND,
        "room_id": str(room_id) if room_id is not None else None,
        "entries": [
            {field: entry.get(field) for field in DOCUMENT_HASH_ENTRY_FIELDS} for entry in entries
        ],
    }
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def document_hash_basis() -> dict[str, str]:
    """The claim this document hash makes, and the one it does not.

    Carried in every export. "Tamper-evident" is a strong phrase and a reviewer is
    entitled to know exactly which part of it is in force.
    """
    return {
        "covers": (
            "the identity of every record the trail describes - its seq, "
            "collection, record id, action code and room - in seq order"
        ),
        "does_not_cover": (
            "document bytes. The cited vendor hashes the signed PDF; this "
            "application has no PDF to hash, so it does not claim the document "
            "was unaltered, only that this set of audited rows is unchanged. "
            "Record revisions are not covered either: audit_log records no "
            "revision, so there is nothing to hash that a holder of the export "
            "could reproduce."
        ),
        "completeness": (
            "not proven by any hash over a list: an event that was never recorded "
            "leaves no trace in the digest. Completeness rests on the audited "
            "write path, where the audit row commits in the same transaction as "
            "the change."
        ),
    }


#: The evidence bundle's discriminator, distinct from both the chain prefix and the
#: document hash so the three claims can never be mistaken for one another.
PACK_KIND = "wf-079-evidence-pack-v1"

#: The fields hashed per signer interaction in the pack. A per-interaction address
#: is what "captures signer IP addresses at each interaction" asks for, so the
#: address is inside the bundle digest rather than beside it: a pack whose
#: interactions list could be edited without changing its digest would be
#: asserting the opposite of tamper-evident.
PACK_INTERACTION_FIELDS: tuple[str, ...] = (
    "seq",
    "code",
    "method",
    "outcome",
    "date_created",
    "ip_address",
    "user_agent",
    "actor",
)


def pack_digest(
    *,
    room_id: str | None,
    record_id: str | None,
    entries: Sequence[Mapping[str, Any]],
    head: str,
    interactions: Sequence[Mapping[str, Any]],
) -> str:
    """SHA-256 over everything the evidence pack asserts.

    The researched claim this lands is that the same evidence "is embedded in the
    final PDF as an audit-trail section, so it travels with the document". What
    travels here is a digest over the trail, the document hash and every signer
    interaction, so that a pack handed to somebody outside the process carries one
    value that changes if any part of it is altered.

    It is not a PDF and this build does not pretend otherwise: there is no byte
    stream to merge pages into. The bundle is the part of that requirement that is
    falsifiable here - one digest over the whole pack - and
    :func:`pack_basis` says what it covers.
    """
    payload = {
        "algorithm": ALGORITHM,
        "kind": PACK_KIND,
        "room_id": str(room_id) if room_id is not None else None,
        "record_id": str(record_id) if record_id is not None else None,
        "entries": len(entries),
        "entries_head": str(head),
        "document_hash": document_hash(room_id, entries),
        "interactions": [
            {field: interaction.get(field) for field in PACK_INTERACTION_FIELDS}
            for interaction in interactions
        ],
    }
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def pack_basis() -> dict[str, str]:
    """What the bundle digest covers, said in the bundle itself."""
    return {
        "covers": (
            "the room and record it names, every entry in the trail and that "
            "trail's chain head, the document hash, and every signer interaction "
            "including its address and user agent"
        ),
        "is_not": (
            "a PDF. The cited vendor merges the audit trail into the signed "
            "document's bytes; this build produces a digest over the same "
            "evidence rather than a file, because a digest is the part that can be "
            "checked by whoever receives it."
        ),
    }


def verify(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Recompute the chain and report the first entry that no longer matches.

    Runs the same three steps a third party would, in the same order, and reports
    where they diverge rather than only whether they do. A boolean is the answer
    to "is this intact"; the position is the answer to "what happened to it".
    """
    running = chain_seed()
    first_mismatch: dict[str, Any] | None = None
    for position, entry in enumerate(entries, start=1):
        digest = entry_digest(entry)
        running = chain_step(running, digest)
        stored_digest = entry.get("digest")
        stored_chain = entry.get("chain")
        if first_mismatch is None:
            # A missing digest is a mismatch, not an absence of one. Checking only
            # when a value was present meant an entry appended to a genuine export
            # and stripped of its two derived fields verified clean - and a
            # hand-edited file is precisely what a verifier is handed. Every entry a
            # real export carries has both fields, so requiring them costs nothing
            # and closes the hole.
            if stored_digest is None:
                first_mismatch = {
                    "position": position,
                    "seq": entry.get("seq"),
                    "expected_digest": digest,
                    "recorded_digest": None,
                    "expected_chain": running,
                    "recorded_chain": stored_chain,
                    "field": "digest",
                    "reason": "this entry carries no recorded digest, so it was not part of any sealed set",
                }
            elif stored_digest != digest:
                first_mismatch = {
                    "position": position,
                    "seq": entry.get("seq"),
                    "expected_digest": digest,
                    "recorded_digest": stored_digest,
                    "expected_chain": running,
                    "recorded_chain": stored_chain,
                    "field": "digest",
                    "reason": "this entry's digest does not match its own contents",
                }
            elif stored_chain is None:
                first_mismatch = {
                    "position": position,
                    "seq": entry.get("seq"),
                    "expected_digest": digest,
                    "recorded_digest": stored_digest,
                    "expected_chain": running,
                    "recorded_chain": None,
                    "field": "chain",
                    "reason": "this entry carries no recorded chain, so it was not part of any sealed set",
                }
            elif stored_chain != running:
                first_mismatch = {
                    "position": position,
                    "seq": entry.get("seq"),
                    "expected_digest": digest,
                    "recorded_digest": stored_digest,
                    "expected_chain": running,
                    "recorded_chain": stored_chain,
                    "field": "chain",
                    "reason": "this entry's chain does not follow from the entries before it",
                }
    head = running if entries else chain_seed()
    return {
        "verified": first_mismatch is None,
        "entries_checked": len(entries),
        "head": head,
        "first_mismatch": first_mismatch,
    }


def integrity_payload(
    *,
    entries: Sequence[Mapping[str, Any]],
    room_id: str | None,
    head: str,
    verified: bool,
    first_mismatch: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The integrity block carried by an export, including its own specification."""
    return {
        "algorithm": ALGORITHM,
        "domain_separator": CHAIN_PREFIX,
        "hashed_fields": list(HASHED_FIELDS),
        "canonical_form": (
            'json.dumps({"seq": int, "date_created": str, "action_code": int, '
            '"actor": str, "ip_address": str, "reason": str}, sort_keys=True, '
            'separators=(",", ":"), ensure_ascii=False).encode("utf-8")'
        ),
        "entry_digest": "sha256(canonical_entry).hexdigest()",
        "chain_seed": 'sha256("wf-079-audit-chain-v1").hexdigest()',
        "chain_step": (
            'sha256("wf-079-audit-chain-v1\\n" + chain(k-1) + "\\n" + digest(k)).hexdigest()'
        ),
        "order": "entries ascending by seq, the audit log's insertion sequence",
        "hashed_values": (
            "the values as exported. A sandbox-masked ip_address is hashed as "
            '"hidden", and a row with no observed address is hashed as the empty '
            "string, so a digest is reproducible from the export alone and does not "
            "depend on data the export withholds."
        ),
        "excluded_by_design": ["digest", "chain"],
        "entries": len(entries),
        "head": head,
        "document_hash": document_hash(room_id, entries),
        "document_hash_kind": DOCUMENT_HASH_KIND,
        "document_hash_basis": document_hash_basis(),
        "verified": verified,
        "first_mismatch": dict(first_mismatch) if first_mismatch else None,
    }


def reseal(entries: Iterable[Mapping[str, Any]]) -> list[str]:
    """Alias kept for readability at the call site that stamps a trail."""
    return seal(list(entries))
