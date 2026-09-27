"""Domain errors for WF-038, the batch engagement upsert.

Each type is this feature's own, which matters: the plugin host refuses two
features mapping the same exception type, and a global handler for a shared type
would intercept an error raised anywhere else in the product. Nothing here
subclasses a core error, so registering these handlers cannot capture a
``RecordNotFound`` from another feature.

The hierarchy is the researched shape of the failure, not a convenience:

``UpsertError``
    A well-formed request asking this connector to do something it will not do.
    Every instance is the caller's to fix, so all of them answer 422.
``UnknownConnection`` / ``UnknownRun``
    The thing named does not exist. 404, and deliberately distinct from an empty
    success so a client cannot mistake "you asked for the wrong id" for "there is
    nothing here".
``BatchTooLarge``
    The connection asks for more rows per request than the vendor accepts. The
    research states the caps as hard limits ("up to 200 objects", "Batch
    operations are limited to 100 records at a time"), so this is refused rather
    than silently clamped - a silent clamp would send batches the vendor is
    documented to reject, and the run would look like a vendor failure.
"""

from __future__ import annotations


class UpsertError(ValueError):
    """A well-formed request this connector will not act on. Answers 422."""


class UnknownConnection(LookupError):
    """No such CRM upsert connection. Answers 404."""

    def __init__(self, connection_id: str) -> None:
        super().__init__(connection_id)
        self.connection_id = connection_id

    def __str__(self) -> str:  # pragma: no cover - exercised through HTTP
        return f"connection {self.connection_id} not found"


class UnknownRun(LookupError):
    """No such upsert run. Answers 404."""

    def __init__(self, run_id: str) -> None:
        super().__init__(run_id)
        self.run_id = run_id

    def __str__(self) -> str:  # pragma: no cover - exercised through HTTP
        return f"run {self.run_id} not found"


class BatchTooLarge(UpsertError):
    """The requested batch size exceeds what the vendor accepts."""


class UnsupportedKey(UpsertError):
    """The key field cannot key an upsert.

    Two sourced reasons, both refused rather than attempted:

    * a key that is a CRM record id. "Only external ids are supported. Don't use
      record ids." - the record id is the *output* of an upsert, so keying on it
      cannot both create and update.
    * a key type the vendor's capability does not declare. The capability
      registry is the researched extensibility seam, and a key type a vendor does
      not support is a configuration error, not a runtime surprise.
    """


class NoUpsertPath(UpsertError):
    """Neither a bulk nor a single upsert is available for this table.

    The research's fallback rule is one direction only - "it auto-falls back
    from ``UpsertMultiple`` to per-row ``PATCH`` for tables that don't support
    bulk upsert" - so a table that supports neither has nowhere to go, and the run
    is refused up front rather than sending requests that cannot work.
    """


class MixedObjectTypes(UpsertError):
    """Rows in one request would carry more than one object type.

    Sourced: "The list can contain objects only of the type indicated in the
    request URI." and the data flow says "single object type". A connection is
    bound to one object, so a row asking for another is refused rather than
    quietly relabelled into the connection's object.
    """
