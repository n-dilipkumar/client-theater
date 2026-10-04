"""The key a retry reuses, and the rule that makes a retry safe.

The research's promise: "retry with the same idempotency key (so retries never
duplicate CRM rows)". That promise has two halves and both are load-bearing:

* **The key must be stable across attempts.** It is derived from the room, the
  connection and the row's own external identifier, and from nothing that changes
  when the batch is retried. A key built from an attempt number, a timestamp or a
  uuid minted at submit time would be a *different* key on every retry, which is
  precisely how a retry duplicates the row it was supposed to replace.
* **The key must be checkable by a reader.** It is a plain string with the parts
  visible, so an operator comparing two attempts of one batch sees the same value
  in both rows and does not have to trust the room.

**Derived, never random.** ``hashlib.blake2b`` rather than :func:`hash` because
``hash`` of a string is salted per process, which would give the same row a
different key on every restart - and a key that changes when the room restarts
cannot deduplicate anything across a restart. The digest is truncated to 32 hex
characters because that is the length Salesforce ids use and the vendor will not
look further.

**Where the external id comes from is the caller's business, not this module's.**
:func:`for_row` takes whatever identifier the connector already keys the row on,
which is the researched idempotency layer's own field (external ID / cursor), and
refuses an empty one: a key derived from nothing is the same key for every row in
the batch, and a batch whose rows all share a key is a batch where one row's
success silences the rest.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping

from dsr.throttle.errors import InvalidPayload

#: How many hex characters of the digest are kept.
DIGEST_LENGTH = 32

#: The prefix, so a key from this room is recognisable in a vendor's logs.
PREFIX = "dsr"


def for_row(
    *,
    vendor: str,
    connection_id: str,
    external_id: Any,
    object_name: str = "",
    room_id: str = "",
) -> str:
    """The idempotency key one row is sent under.

    The parts are joined with ``|`` and then digested, so the key itself does not
    leak a buyer's external id into a vendor's request log, and two rooms sending
    the same external id through the same connection cannot collide.
    """
    key = str(external_id or "").strip()
    if not key:
        raise InvalidPayload(
            "a row needs an external id before the room can key its retry; an idempotency key "
            "derived from nothing is the same key for every row in the batch, and one row's "
            "success would then silence the rest"
        )
    material = "|".join(
        (
            PREFIX,
            str(vendor or "").strip().lower(),
            str(connection_id or "").strip(),
            str(object_name or "").strip().lower(),
            str(room_id or "").strip(),
            key,
        )
    )
    return digest(material)


def digest(material: str) -> str:
    """The stable digest :func:`for_row` produces, for a caller that has its own parts."""
    raw = hashlib.blake2b(str(material).encode("utf-8"), digest_size=16).hexdigest()
    return raw[:DIGEST_LENGTH]


def for_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    vendor: str,
    connection_id: str,
    object_name: str = "",
    room_id: str = "",
    key_field: str = "external_id",
) -> list[dict[str, Any]]:
    """One entry per row, each with its key and the identifier it came from.

    Duplicates inside one batch are refused rather than keyed identically: a batch
    that names the same external id twice is asking the vendor to write the same
    row twice, and the room will not hand it a key that makes the second write a
    silent no-op without saying so.
    """
    entries: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for index, row in enumerate(rows or []):
        if not isinstance(row, Mapping):
            raise InvalidPayload(f"row {index} must be a JSON object; got {type(row).__name__}")
        external = row.get(key_field)
        if external in (None, ""):
            external = row.get("external_id")
        key = for_row(
            vendor=vendor,
            connection_id=connection_id,
            external_id=external,
            object_name=object_name,
            room_id=room_id,
        )
        if key in seen:
            raise InvalidPayload(
                f"rows {seen[key]} and {index} share the external id {external!r}, so they share "
                f"the idempotency key {key}. Send them as one row or key them differently: a key "
                "that two rows share means the first success silences the second."
            )
        seen[key] = index
        entries.append({"index": index, "external_id": external, "idempotency_key": key})
    return entries


def same(a: Any, b: Any) -> bool:
    """Whether two attempts carried the *same* key.

    Two absences are **not** a match. ``same(None, None)`` and ``same("", "")``
    answer ``False``, and that is the honest answer rather than a pedantic one:
    the question this function exists to answer is "did the retry keep the key the
    first attempt used", and an attempt with no key kept nothing. Reporting
    ``True`` would let a caller record "keys reused" on a batch that never had
    any, which is the one thing the researched promise must never be able to say.

    Values are compared as trimmed text, because a key that arrived with a
    trailing space and the same key without it are the same key.
    """
    left = str(a or "").strip()
    right = str(b or "").strip()
    return bool(left) and left == right


__all__ = ["DIGEST_LENGTH", "PREFIX", "for_row", "digest", "for_rows", "same"]
