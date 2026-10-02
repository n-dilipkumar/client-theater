"""The per-transaction buffer, and the commit rule the research states once.

"On each event the room checks ``changeType``, buffers the change under its
``transactionKey``, and only commits to the room's local replica when the key
changes."

That is the whole rule, and it is unusual enough to be worth spelling out: a
change is *not* applied when it arrives. It is parked under its transaction key
and stays parked until an event arrives under a **different** key, at which point
the parked transaction - and only that one - is applied to the room's replica.

Two consequences follow, and both are implemented here rather than left to the
caller:

* the **last** transaction in a stream has no successor to trigger its commit, so
  it stays buffered until the subscription is closed, at which point it is
  flushed. Dropping it instead would silently lose a change, which is the one
  outcome "near real time" must never produce.
* events **inside** a transaction are ordered by ``sequenceNumber``, because that
  is the field the research gives for exactly that purpose. A gap in the sequence
  is *reported* and not reconciled: recovering from a dropped stream is section  18
  of the same research file, which is a different workflow with its own brief.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Sequence

from dsr.change_stream.errors import MalformedEvent


def _bytes_of(event: Mapping[str, Any]) -> int:
    """The on-the-wire size of an event, for the buffer high-water mark.

    Measured on the normalised record rather than on the raw request, because
    the normalised record is what the room actually holds while the event is
    parked. Compared against the researched 3 MB recommendation, so the number
    has to be the one the buffer is really using.
    """
    try:
        return len(json.dumps(event, default=str).encode("utf-8"))
    except (TypeError, ValueError):  # pragma: no cover - default=str covers this
        return 0


def sequence_gaps(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Where a transaction's sequence numbers are not contiguous.

    Reported, never repaired. The research names ``sequenceNumber`` and nothing
    about what a missing one means, and section 18 of the same file - "Reconcile
    gaps and overflows after a dropped change stream" - is a separate workflow.
    Building a repair here would implement another ticket's specification inside
    this one, and would make this feature's audit log name recoveries that the
    page above it never offered.
    """
    numbers = sorted({int(event["sequence_number"]) for event in events})
    if len(numbers) < 2:
        return []
    gaps: list[dict[str, Any]] = []
    for previous, current in zip(numbers, numbers[1:], strict=False):
        if current > previous + 1:
            gaps.append({"after": previous, "before": current, "missing": current - previous - 1})
    return gaps


class TransactionBuffer:
    """One parked transaction, under the key that parked it.

    Held in memory for the life of a subscription because the commit rule is
    about the *stream*, not about a row: a buffer that survived a restart would
    have to survive a restart mid-transaction, and the research says nothing
    about what a reconnect should do with a half-parked transaction. So the
    buffer is per-process, and :func:`dsr.change_stream.inferences` says so.
    """

    __slots__ = ("key", "events", "_seen")

    def __init__(self, key: str) -> None:
        self.key = key
        self.events: list[dict[str, Any]] = []
        self._seen: set[int] = set()

    def __len__(self) -> int:
        return len(self.events)

    @property
    def empty(self) -> bool:
        return not self.events

    def add(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Park one event, or refuse a sequence number already parked.

        A duplicate sequence number inside one transaction means the same event
        arrived twice, and applying it twice would write a change twice. The
        research says nothing about at-least-once delivery, so this reports the
        duplicate rather than deciding whether it was a redelivery.
        """
        sequence = int(event["sequence_number"])
        if sequence in self._seen:
            return {
                "added": False,
                "reason": "duplicate_sequence_number",
                "sequence_number": sequence,
            }
        self._seen.add(sequence)
        self.events.append(dict(event))
        return {"added": True, "sequence_number": sequence}

    def ordered(self) -> list[dict[str, Any]]:
        """The parked events in sequence order."""
        return sorted(self.events, key=lambda event: int(event["sequence_number"]))

    def describe(self) -> dict[str, Any]:
        """What a reader of the buffer needs, and nothing derived from state."""
        ordered = self.ordered()
        return {
            "transaction_key": self.key,
            "event_count": len(ordered),
            "sequence_numbers": [int(event["sequence_number"]) for event in ordered],
            "sequence_range": (
                [int(ordered[0]["sequence_number"]), int(ordered[-1]["sequence_number"])]
                if ordered
                else []
            ),
            "buffer_bytes": sum(_bytes_of(event) for event in ordered),
            "change_types": [str(event["change_type"]) for event in ordered],
            "entities": sorted({str(event.get("entity") or "") for event in ordered} - {""}),
            "first_commit_timestamp": str(ordered[0]["commit_timestamp"]) if ordered else "",
            "last_commit_timestamp": str(ordered[-1]["commit_timestamp"]) if ordered else "",
            "sequence_gaps": sequence_gaps(ordered),
            "gap_policy": (
                "reported, not reconciled: recovering a dropped stream is a different "
                "researched workflow"
            ),
        }


class BufferSet:
    """Every parked transaction for one stream, and the commit rule that drains it.

    Keyed by transaction key rather than being a single slot, because a
    subscriber on a channel with several entities can legitimately have two
    transactions in flight, and the research's rule is about *a* transaction
    completing. A single-slot buffer would commit a transaction that is still
    receiving events.

    Ordering across transactions is by first-seen, so the commit order is the
    order the stream delivered them in - which is the only order the research
    gives any hint about, via ``commitTimestamp``.
    """

    #: The process-wide parked transactions, one set per subscription.
    #:
    #: Module-level on purpose, and the reason is in the ``buffer-is-per-process``
    #: entry in :mod:`dsr.change_stream.inferences`: a parked transaction lives in
    #: memory for the life of the *stream*, not the life of a request handler.
    #: The engine is built per request from ``StoreDep``, so a buffer held on the
    #: engine would be discarded between the event that parked a change and the
    #: event that closes its transaction - which is two HTTP requests apart and
    #: would mean the commit rule never fired at all.
    _registry: dict[str, "BufferSet"] = {}

    def __init__(self) -> None:
        self._buffers: dict[str, TransactionBuffer] = {}
        self._order: list[str] = []

    @classmethod
    def for_subscription(cls, subscription_id: str) -> "BufferSet":
        """The parked transactions for one subscription, shared process-wide."""
        found = cls._registry.get(subscription_id)
        if found is None:
            found = cls()
            cls._registry[subscription_id] = found
        return found

    @classmethod
    def release(cls, subscription_id: str) -> None:
        """Forget one subscription's parked transactions. Called on close."""
        cls._registry.pop(subscription_id, None)

    @classmethod
    def reset(cls) -> None:
        """Forget every parked transaction. For tests and for a fresh process."""
        cls._registry.clear()

    # -- inspection --------------------------------------------------------- #

    def __len__(self) -> int:
        return len(self._order)

    def keys(self) -> list[str]:
        """Parked transaction keys, oldest first."""
        return list(self._order)

    def get(self, key: str) -> TransactionBuffer | None:
        return self._buffers.get(key)

    def describe(self) -> list[dict[str, Any]]:
        """Every parked transaction, oldest first, for the buffer read route."""
        return [self._buffers[key].describe() for key in self._order]

    def current(self) -> TransactionBuffer | None:
        """The transaction the stream is currently filling, if any."""
        return self._buffers[self._order[-1]] if self._order else None

    def event_count(self) -> int:
        return sum(len(buffer) for buffer in self._buffers.values())

    def buffer_bytes(self) -> int:
        return sum(
            sum(_bytes_of(event) for event in buffer.events) for buffer in self._buffers.values()
        )

    # -- mutation ----------------------------------------------------------- #

    def accept(
        self, event: Mapping[str, Any]
    ) -> tuple[TransactionBuffer, list[TransactionBuffer], dict[str, Any]]:
        """Park one event and report which transactions that completed.

        Returns the buffer now filling, the list of buffers whose commit was
        triggered, and what happened to this event. The caller applies the
        committed ones, because applying one is a write to the room's replica and
        this class has no store.

        A different key is the commit trigger, which is the research's rule
        stated exactly. A repeat of the same key is not a trigger, and a sequence
        number already parked is refused as a duplicate.
        """
        key = str(event["transaction_key"])
        # Anything parked before this key can receive nothing further, so this
        # event is what closed it. Done before the buffer is opened so a
        # duplicate of the key's *first* event cannot close anything twice.
        ready = self.complete_before(key)
        target = self._buffers.get(key)
        if target is None:
            target = TransactionBuffer(key)
            self._buffers[key] = target
            self._order.append(key)
        return target, ready, target.add(event)

    def complete_before(self, key: str) -> list[TransactionBuffer]:
        """Every transaction parked before ``key``, which ``key`` has now closed.

        This is the commit rule, in one function. An event under a new key means
        the transactions before it cannot receive anything else, so they are ready
        to commit - oldest first, so a replica sees changes in the order the
        stream delivered them.

        A key already in flight closes only the transactions ahead of it. A key
        that is not in flight yet closes all of them, because it is about to
        become the newest and everything parked is older than it.
        """
        if key in self._order:
            position = self._order.index(key)
        else:
            position = len(self._order)
        if position == 0:
            return []
        ready = [self._buffers[other] for other in self._order[:position]]
        del self._order[:position]
        for other in ready:
            self._buffers.pop(other.key, None)
        return ready

    def restore_front(self, buffers: Sequence[TransactionBuffer]) -> None:
        """Put transactions back at the front of the queue, oldest first.

        The rollback for a commit that could not be applied. The commit rule
        removes a transaction from the queue when a new key closes it, and a
        transaction that then fails to apply must not vanish - the whole point of
        the transaction key is that the change is applied as a unit or not at
        all, and "not at all" has to mean "still parked", not "gone".

        The events stay in the room's change log either way, so a reviewer can see
        what arrived; this is what makes them still committable once the room has
        fixed whatever stopped them.
        """
        for buffer in buffers:
            if buffer.key not in self._buffers:
                self._buffers[buffer.key] = buffer
                self._order.append(buffer.key)
        for key in list(self._order):
            if key not in self._buffers:
                self._order.remove(key)
        restored = [buffer.key for buffer in buffers if buffer.key in self._buffers]
        self._order = restored + [key for key in self._order if key not in restored]

    def take(self, key: str) -> TransactionBuffer | None:
        """Remove one parked transaction without committing it."""
        buffer = self._buffers.pop(key, None)
        if buffer is not None:
            self._order.remove(key)
        return buffer

    def flush(self) -> list[TransactionBuffer]:
        """Every parked transaction, oldest first, and an empty buffer set.

        The drain the commit rule leaves to the caller. A stream that stops with
        a transaction still parked has a change that will never commit under the
        "when the key changes" rule alone, so closing the subscription flushes it.
        """
        ready = [self._buffers[key] for key in self._order]
        self._buffers.clear()
        self._order.clear()
        return ready

    # -- invariant ---------------------------------------------------------- #

    def assert_orderable(self, buffers: Iterable[TransactionBuffer]) -> None:
        """Refuse to apply a set of transactions whose timestamps go backwards.

        The commit rule orders by arrival, and arrival is the only order the
        research gives. When two transactions carry commit timestamps that
        contradict that order the stream has been re-delivered, and applying them
        as arrived would write an older change over a newer one - so this is
        checked rather than assumed, and the caller is told which pair.
        """
        previous: TransactionBuffer | None = None
        for buffer in buffers:
            described = buffer.describe()
            if not described["last_commit_timestamp"]:
                continue
            if previous is not None:
                prior = previous.describe()["last_commit_timestamp"]
                if str(described["first_commit_timestamp"]) < prior:
                    raise MalformedEvent(
                        f"transaction {described['transaction_key']!r} commits at "
                        f"{described['first_commit_timestamp']}, before {previous.key!r} at {prior}. "
                        "Transactions are applied in the order the stream delivered them, and "
                        "applying these in that order would write an older change over a newer one."
                    )
            previous = buffer
