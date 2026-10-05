"""WF-082: the writes, and the order the three checks run in.

The four modules this workflow recovered carry vocabulary, signing arithmetic, allowlist
arithmetic and the derivation register. None of them knows about storage. This one does, and
it is the only one that reads or writes.

What the specification fixes, and where each half lives
-------------------------------------------------------

**Three checks, in this order, and "only then does the handler act".**
:func:`WebhookVerifier.inspect` runs :data:`~webhook_vocabulary.CHECK_ORDER` in order and
stops at the first failure, so a delivery is recorded against the check that actually refused
it rather than against whichever check happened to run first in a rewritten loop.

**A magic string, not a status code.** The evidence says the callback URL "must return an
HTTP ``200`` with a response body that contains the string ``Hello API Event Received``", and
:func:`WebhookVerifier.inspect` puts that string in ``acknowledgement`` on every accepted
delivery including a duplicate, because a duplicate the provider reads as a failure costs a
retry and a retry walks a ladder that reaches twenty hours and fifteen minutes.

**De-duplicate on event id.** :meth:`WebhookVerifier.inspect` records the id only after all
three checks pass, and a repeat is reported as :data:`~webhook_vocabulary.DUPLICATE`. The key
is the registration as well as the id, so two registrations for one provider de-duplicate
independently; see ``INFERRED_DUPLICATE_IS_ACKNOWLEDGED``.

**Thirty seconds.** The provider's timeout is :data:`~webhook_vocabulary.PROVIDER_TIMEOUT_SECONDS`
and this module does no network I/O on that path at all, which is why the budget is never at
risk rather than merely comfortable.

Where the API key lives
-----------------------

The specification names the account's API key as the HMAC secret and says nothing about
storing it, which ``INFERRED_INBOUND_KEY_VAULT`` records. The secret is sealed at rest rather
than kept in the clear: a registration is readable through the core records API, and a secret an
operator can re-read is not being used as a secret. Hashing it was rejected because HMAC needs the
key itself - there is no re-hash here, the product has to *produce* a digest on every delivery.

The sealing lives in :mod:`dsr.security_governance.webhook_sealing`, which is a module of its own
because an enforced test forbids this package from importing another workflow's. That module's
docstring gives the construction and states the cost of not reusing the CRM vault.

The key that sealed a row is named beside it as a public fingerprint, so a surface can say
which key a registration needs without revealing the key, and a row sealed under another
process's key is recognisable as such rather than as corruption.

Schema flexibility
------------------

Every field below is ordinary JSON in ``records.data``. There is no migration and no typed
column, so a team adding a field to a registration needs no coordination with anyone. Room
scoping uses ``room_ref`` rather than ``room_id`` because ``room_id`` is part of the record
envelope and the store strips it out of ``data`` before the dynamic index is built, so a row
that stored its room there would be unfilterable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from dsr.security_governance import (
    webhook_rules as rules,
    webhook_sealing as sealing,
    webhook_signing as signing,
    webhook_vocabulary as vocab,
)
from dsr.store import RecordStore

__all__ = [
    "HONESTY",
    "WebhookRegistrationInvalid",
    "WebhookRegistrationNotFound",
    "WebhookVerifier",
    "delivery_record",
]


#: The four fields every JSON response from this feature carries.
#:
#: A verifier that says what it checked without saying what it did not check is the failure
#: this workflow most needs to avoid, so the scope is stated in words and sent with every
#: response rather than left to a reader's assumption.
HONESTY: dict[str, str] = {
    "effect": "verified_then_recorded",
    # Named ``verification_scope`` and not ``checks`` on purpose. ``checks`` is the list of
    # the three check results in every delivery, summary and vocabulary payload, and a
    # ``**HONESTY`` spread that carried the key ``checks`` overwrote that list with a
    # sentence. The collision is silent: every response still had a ``checks`` field, and
    # the field was the wrong shape. One word apart is exactly how that happens.
    "verification_scope": (
        "A delivery is acted on only after the source IP, the Content-Sha256 payload digest "
        "and the event_hash HMAC have all passed. This handler checks the source address it "
        "was given and the two digests the request carried. It cannot prove the request was "
        "not replayed from a captured body, because that would need a nonce the provider does "
        "not send."
    ),
    "no_fetches_on_delivery": (
        "Verifying a delivery opens no socket. The allowlist is a stored snapshot with the age "
        "it had when the answer was given, and refreshing it is a separate explicit call."
    ),
    "declared_source_ip": (
        "When the peer address is overridden from a request header, the delivery records that "
        "it was. A deployment that accepts a declared source address has turned the IP check "
        "off, and the row is the only evidence that it did."
    ),
}


class WebhookRegistrationInvalid(ValueError):
    """A registration this build will not accept. Maps to 400."""

    status = 400
    code = "registration_invalid"

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors = dict(errors or {})


class WebhookRegistrationNotFound(LookupError):
    """No registration answers for that request. Maps to 404."""

    status = 404
    code = "registration_not_found"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WebhookVerifier:
    """The reads and the writes, over an audited store.

    Built per request by the HTTP layer rather than hung off ``app.state``, so a test can
    substitute a clock and a vault key without editing a shared file.

    ``now`` is a callable, not a value, for the same reason
    :func:`~dsr.deps.db_path` reads the environment at call time: a module-level constant is
    captured on first import, and a test that sets the clock afterwards would silently keep
    using the old one.
    """

    def __init__(
        self,
        store: RecordStore,
        *,
        now: Any = None,
        vault_key: sealing.VaultKey | None = None,
    ) -> None:
        self.store = store
        self.now = now or _now
        self.vault_key = vault_key or sealing.resolve_key()

    # -- clock --------------------------------------------------------------- #

    def clock(self) -> datetime:
        value = self.now()
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    def _stamp(self) -> str:
        return self.clock().isoformat(timespec="milliseconds")

    # -- the vault ----------------------------------------------------------- #

    @property
    def key_origin(self) -> str:
        return self.vault_key.origin

    @property
    def key_is_default(self) -> bool:
        return self.vault_key.is_default

    def key_warning(self) -> str:
        """The sentence a surface shows when the sealing key is the published default.

        Empty when an operator supplied one, so the caller can render it unconditionally
        without showing a warning about a deployment that is correctly configured.
        """
        if not self.vault_key.is_default:
            return ""
        return (
            f"No {vocab.KEY_ENV} is set, so callback registrations are sealed with the "
            "published demo key, which is in the source. Set it to a real secret before "
            "storing a real provider API key."
        )

    def _seal_api_key(self, api_key: str) -> str:
        return sealing.seal({"api_key": api_key}, self.vault_key.material)

    def _open_api_key(self, row: Mapping[str, Any]) -> str:
        """The API key behind a registration, or a refusal naming what to do.

        A row sealed under another process's key raises rather than returning an empty
        string, because an empty key would make every digest wrong and the refusal would
        name a signature rather than a configuration fault.
        """
        opened = sealing.open_sealed(
            str(row.get(vocab.SEALED_API_KEY) or ""), self.vault_key.material
        )
        value = opened.get("api_key")
        if not isinstance(value, str) or not value:
            raise WebhookRegistrationInvalid(
                "the sealed API key on this registration is empty, so no delivery can be "
                "authenticated. Register the callback again with a real API key."
            )
        return value

    # -- registrations ------------------------------------------------------- #

    def register_callback(
        self,
        room_ref: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Record a callback registration for a room.

        ``callback_url`` is required and must be HTTPS. The evidence is not a preference
        about this: "we will require callback URLs to use **HTTPS** starting **November 30,
        2024**" and "Any callback URL not using HTTPS on December 1, 2024, will stop receiving
        Sign callback events". A plain-HTTP callback is refused here rather than registered
        and then silently starved of events.

        ``api_key`` is required for the same reason the HMAC checks exist at all: without the
        key nothing this handler receives can be authenticated.
        """
        errors: dict[str, str] = {}
        callback_url = str(payload.get("callback_url") or "").strip()
        if not callback_url:
            errors["callback_url"] = "a callback URL is required."
        elif not callback_url.lower().startswith(f"{vocab.REQUIRED_SCHEME}://"):
            errors["callback_url"] = (
                f"the callback URL must use {vocab.REQUIRED_SCHEME}. Plain HTTP callbacks "
                "receive no events."
            )

        api_key = str(payload.get("api_key") or "").strip()
        if not api_key:
            errors["api_key"] = "the account API key is required. It is the HMAC secret."

        scope = str(payload.get("scope") or "").strip()
        if scope and scope not in vocab.CALLBACK_SCOPES:
            errors["scope"] = f"scope must be one of {', '.join(vocab.CALLBACK_SCOPES)}."

        if errors:
            raise WebhookRegistrationInvalid(
                "This registration cannot be accepted. Each message names the field it belongs to.",
                errors,
            )

        client_id = str(payload.get("client_id") or "").strip() or None
        body = {
            vocab.ROOM_REF: str(room_ref),
            "callback_url": callback_url,
            "scope": scope or vocab.CALLBACK_SCOPES[0],
            "client_id": client_id,
            "stale_after_seconds": _optional_int(payload.get("stale_after_seconds")),
            vocab.SEALED_API_KEY: self._seal_api_key(api_key),
            vocab.KEY_FINGERPRINT: self.vault_key.key_id,
            vocab.KEY_ORIGIN: self.vault_key.origin,
            "key_warning": self.key_warning(),
        }
        row = self.store.create(
            vocab.COLLECTION_CALLBACKS,
            body,
            actor=actor,
            source=source,
        )
        return self.read_callback(row["id"])

    def update_callback(
        self,
        callback_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Change a registration. Rejects ``api_key`` with a message that says to re-register.

        Replacing a sealed secret is a new value rather than a patch, and pretending otherwise
        would leave a row whose stored fingerprint and sealed body disagree. The caller is
        told which route does it.
        """
        if "api_key" in patch:
            raise WebhookRegistrationInvalid(
                "api_key cannot be patched. Register the callback again to store a new key.",
                {"api_key": "Register the callback again to replace the sealed key."},
            )
        row = self.store.get(callback_id)
        if row is None or row["collection"] != vocab.COLLECTION_CALLBACKS:
            raise WebhookRegistrationNotFound(f"No callback registration with id {callback_id}.")
        body = dict(row["data"])
        for key in ("callback_url", "client_id", "scope"):
            if key in patch and patch[key] is not None:
                body[key] = str(patch[key]).strip() or None
        if "stale_after_seconds" in patch:
            body["stale_after_seconds"] = _optional_int(patch["stale_after_seconds"])

        candidate = str(body.get("callback_url") or "")
        if not candidate.lower().startswith(f"{vocab.REQUIRED_SCHEME}://"):
            raise WebhookRegistrationInvalid(
                "the callback URL must use HTTPS. Plain HTTP callbacks receive no events.",
                {"callback_url": "The URL must use https."},
            )

        self.store.update(callback_id, body, actor=actor, source=source)
        return self.read_callback(callback_id)

    def callbacks(self, room_ref: str | None = None) -> list[dict[str, Any]]:
        """Every registration, never the sealed key.

        The projection carries the fingerprint and the origin and never the sealed string, so
        a list route cannot become the reason a secret is exposed. The sealed blob is still
        in the record, because the core records API can read any collection; what this route
        does is not widen that.
        """
        where = {vocab.ROOM_REF: room_ref} if room_ref else {}
        rows = self.store.find(vocab.COLLECTION_CALLBACKS, where, limit=200)
        return [self._callback_summary(row) for row in rows]

    def read_callback(self, callback_id: str) -> dict[str, Any]:
        row = self.store.get(callback_id)
        if row is None or row["collection"] != vocab.COLLECTION_CALLBACKS:
            raise WebhookRegistrationNotFound(f"No callback registration with id {callback_id}.")
        summary = self._callback_summary(row)
        # The row this registration verifies against, so a page can show whether the room
        # could hear a delivery at all. A range list that is empty refuses every delivery.
        snapshot = self._snapshot_for(str(row["data"].get(vocab.ROOM_REF) or ""))
        summary["snapshot"] = snapshot["summary"]
        return summary

    def _callback_summary(self, row: Mapping[str, Any]) -> dict[str, Any]:
        data = row["data"]
        return {
            "id": row["id"],
            vocab.ROOM_REF: data.get(vocab.ROOM_REF),
            "callback_url": data.get("callback_url"),
            "scope": data.get("scope"),
            "client_id": data.get("client_id"),
            "stale_after_seconds": data.get("stale_after_seconds"),
            "sealed": True,
            vocab.KEY_FINGERPRINT: data.get(vocab.KEY_FINGERPRINT),
            vocab.KEY_ORIGIN: data.get(vocab.KEY_ORIGIN),
            "key_is_published_default": data.get(vocab.KEY_ORIGIN) == "default",
            "key_warning": data.get("key_warning") or self.key_warning(),
            "updated_at": row.get("updated_at"),
        }

    # -- the allowlist ------------------------------------------------------- #

    def _snapshot_rows(self, room_ref: str) -> list[dict[str, Any]]:
        """The ranges a room holds, in the shape :func:`~webhook_rules.evaluate_source_ip` reads."""
        return [
            {"range": entry["range"], "description": entry.get("description")}
            for entry in self._snapshot_for(room_ref)["ranges"]
            if entry.get("range")
        ]

    def ranges(self, room_ref: str) -> dict[str, Any]:
        """The stored allowlist for a room, with the age that decides whether to trust it.

        The evidence says to check the list "periodically", so an answer that cannot say how
        old its own allowlist is cannot answer the only question an operator asks during an
        incident: is this refusal real, or is my copy of the list stale?
        """
        snapshot = self._snapshot_for(str(room_ref))
        return {
            vocab.ROOM_REF: str(room_ref),
            "source_url": vocab.IP_RANGES_URL,
            "recheck": vocab.IP_RANGES_RECHECK,
            "ranges": snapshot["ranges"],
            **snapshot["summary"],
        }

    def _snapshot_for(self, room_ref: str) -> dict[str, Any]:
        """The stored ranges for a room, with the newest snapshot's age beside them.

        One snapshot per room rather than one per process, because two rooms in one demo may
        hold different range files and a per-process snapshot would let one room's allowlist
        answer for another's deliveries.
        """
        rows = self.store.find(vocab.COLLECTION_RANGES, {vocab.ROOM_REF: room_ref}, limit=500)
        ranges = [
            {
                "range": row["data"].get("range"),
                "description": row["data"].get("description"),
                "family": row["data"].get("family"),
                "address_count": row["data"].get("address_count"),
            }
            for row in rows
            if row["data"].get("range")
        ]
        snapshot_at = max(
            (str(row["data"].get("snapshot_at") or "") for row in rows),
            default="",
        )
        age = rules.staleness_note(snapshot_at or None, now=self.clock())
        return {
            "ranges": ranges,
            "summary": {
                "range_count": len(ranges),
                "snapshot_at": snapshot_at or None,
                "source_url": vocab.IP_RANGES_URL,
                "stale": age["stale"],
                "age_seconds": age["age_seconds"],
                "staleness": age["note"],
            },
        }

    def refresh_ranges(
        self,
        room_ref: str,
        *,
        fetcher,
        source_url: str = vocab.IP_RANGES_URL,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Fetch the published range file and store it, keeping the previous one on failure.

        The fetch is an argument rather than a hard-coded client so three things are possible
        and each matters: a test hands over bytes without a network, a deployment puts its
        own client in front of the call, and the published host is a default rather than a
        constant buried in a request.

        A fetch that raises leaves the stored allowlist untouched. Overwriting a good
        allowlist with a failed refresh would turn a vendor's bad minute into a verification
        handler that stops verifying, so the failure propagates and the caller records it.
        """
        document = rules.refresh_ranges(source_url=source_url, fetcher=fetcher, now=self.clock())
        rows = document["ranges"]

        existing = self.store.find(
            vocab.COLLECTION_RANGES,
            {vocab.ROOM_REF: str(room_ref)},
            limit=500,
        )
        for row in existing:
            self.store.delete(row["id"], actor=actor, source=source)
        created = self.store.bulk_create(
            vocab.COLLECTION_RANGES,
            [
                {
                    vocab.ROOM_REF: str(room_ref),
                    "range": entry["range"],
                    "description": entry.get("description") or "",
                    "family": entry.get("family"),
                    "address_count": entry.get("address_count"),
                    "source_url": source_url,
                    "snapshot_at": document["fetched_at"],
                }
                for entry in rows
            ],
            actor=actor,
            source=source,
        )
        return {
            "room_ref": str(room_ref),
            "source_url": source_url,
            "snapshot_at": document["fetched_at"],
            "range_count": len(created),
            "snapshot": self._snapshot_for(str(room_ref))["summary"],
        }

    # -- the delivery -------------------------------------------------------- #

    def _registration_for(
        self,
        room_ref: str,
        callback_id: str | None,
    ) -> dict[str, Any]:
        """The one registration that verifies this delivery, or a refusal naming the fix.

        One, and named. ``INFERRED_REGISTRATION_MATCHES_ONE_KEY`` records why: trying every
        registration turns the number of secrets a room holds into a verification oracle, and
        taking the newest silently changes which secret a delivery must be signed with.
        Refusing an ambiguous delivery fails closed instead of guessing.
        """
        candidates = [
            row
            for row in self.store.find(
                vocab.COLLECTION_CALLBACKS, {vocab.ROOM_REF: str(room_ref)}, limit=200
            )
        ]
        if callback_id:
            chosen = [row for row in candidates if row["id"] == callback_id]
            if not chosen:
                raise WebhookRegistrationNotFound(
                    f"No callback registration with id {callback_id} is bound to this room."
                )
            return chosen[0]
        if not candidates:
            raise WebhookRegistrationNotFound(
                "No callback registration is bound to this room, so no delivery can be "
                "authenticated. Register one first."
            )
        if len(candidates) > 1:
            raise WebhookRegistrationInvalid(
                "More than one callback registration is bound to this room, so this delivery "
                "does not say which key signed it. Name one in the "
                f"{vocab.CREDENTIAL_HEADER} header.",
                {
                    vocab.CREDENTIAL_HEADER: (
                        "This header is not part of the provider's protocol. A deployment "
                        "with more than one registration has to set it in front of the "
                        "delivery."
                    )
                },
            )
        return candidates[0]

    def inspect(
        self,
        room_ref: str,
        *,
        source_ip: str,
        content_sha256: str | None,
        payload_bytes: bytes,
        payload: Mapping[str, Any],
        callback_id: str | None = None,
        callback_url: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Run the three checks in order and record the delivery.

        Returns a report rather than raising, because a refused delivery is an outcome this
        product records and serves, not an exception: the provider reads a non-200 as a
        failure and retries, and a handler that raised would record nothing about which check
        refused it.

        The order is :data:`~webhook_vocabulary.CHECK_ORDER`. The IP check runs first because
        it is the cheapest and the only one that does not need the secret, and a handler that
        spent its thirty second budget on an HMAC for a request the IP check would have
        refused for free has spent it on an address that was never going to be accepted.
        """
        row = self._registration_for(room_ref, callback_id)
        data = row["data"]

        registered = str(data.get("callback_url") or "")
        target = str(callback_url or registered).strip()
        if target and target != registered:
            raise WebhookRegistrationInvalid(
                "The callback URL on this request does not match the registration. A provider "
                "posts to the registered URL, so a mismatch means the request is aimed at the "
                "wrong registration.",
                {"callback_url": f"This registration is registered for {registered}."},
            )

        api_key = self._open_api_key(data)

        snapshot = self._snapshot_for(str(room_ref))
        snapshot_rows = self._snapshot_rows(str(room_ref))

        checks: list[dict[str, Any]] = []

        # -- one: the source IP against the published range file -------------- #
        try:
            verdict = rules.evaluate_source_ip(
                source_ip,
                snapshot_rows,
                snapshot_at=str(snapshot["summary"]["snapshot_at"] or "") or None,
                now=self.clock(),
                stale_after_seconds=data.get("stale_after_seconds"),
            )
        except rules.IpRangeError as exc:
            # A range list this build cannot interpret refuses the delivery rather than
            # checking nothing. The alternative is an empty allowlist presented as a policy.
            verdict = {
                "allowed": False,
                "passed": False,
                "check": vocab.IP_ALLOWLIST,
                "error_name": "source_ip_not_allowed",
                "allowed_range": None,
                "range_count": len(snapshot_rows),
                "snapshot_at": snapshot["summary"]["snapshot_at"],
                "staleness": "the stored range list could not be read",
                "stale": True,
                "stale_after_seconds": rules.DEFAULT_STALE_AFTER_SECONDS,
                "age_seconds": snapshot["summary"]["age_seconds"],
                "reason": str(exc),
            }
        checks.append(verdict)

        # -- two: the base64 digest of the payload as received --------------- #
        payload_check = signing.verify_content_sha256(api_key, payload_bytes, content_sha256)
        checks.append(payload_check)

        # -- three: the HMAC over event_time concatenated with event_type --- #
        event = payload.get("event") if isinstance(payload.get("event"), Mapping) else {}
        event_hash_check = signing.verify_event_hash(api_key, event)
        checks.append(event_hash_check)

        first_failure = next(
            (check for check in checks if not _passed(check)),
            None,
        )

        body = {
            vocab.ROOM_REF: str(room_ref),
            "callback_ref": row["id"],
            "source_ip": str(source_ip or ""),
            "event_id": event.get(vocab.EVENT_ID_FIELD),
            "event_type": event.get(vocab.EVENT_TYPE_FIELD),
            "event_time": event.get("event_time"),
            "signature_request_ref": _ref(payload.get("signature_request")),
            "content_sha256": content_sha256,
            "checks": checks,
            "snapshot_at": snapshot["summary"]["snapshot_at"],
            "snapshot_stale": snapshot["summary"]["stale"],
        }

        if first_failure is not None:
            error_name = _error_name(first_failure)
            catalogue = vocab.error_code(error_name)
            body["state"] = vocab.REJECTED
            body["error_name"] = error_name
            body["http_status"] = catalogue["http_status"]
            body["failed_check"] = first_failure.get("check")
            record = self.store.create(
                vocab.COLLECTION_DELIVERIES, body, actor=actor, source=source
            )
            return {
                "state": vocab.REJECTED,
                "acknowledgement": None,
                "error_name": error_name,
                "http_status": catalogue["http_status"],
                "catalogue": catalogue,
                "failed_check": first_failure.get("check"),
                "checks": checks,
                "delivery": delivery_record(record),
                **HONESTY,
            }

        event_id = event.get(vocab.EVENT_ID_FIELD)
        if not str(event_id or "").strip():
            # Reached only after all three checks passed, which cannot happen for a payload
            # with no event_hash, but the id is not covered by any of the three and an
            # unrecognisable event would be recorded for ever.
            error_name = "event_id_missing"
            catalogue = vocab.error_code(error_name)
            body["state"] = vocab.REJECTED
            body["error_name"] = error_name
            body["http_status"] = catalogue["http_status"]
            body["failed_check"] = "event_id"
            record = self.store.create(
                vocab.COLLECTION_DELIVERIES, body, actor=actor, source=source
            )
            return {
                "state": vocab.REJECTED,
                "acknowledgement": None,
                "error_name": error_name,
                "http_status": catalogue["http_status"],
                "catalogue": catalogue,
                "failed_check": "event_id",
                "checks": checks,
                "delivery": delivery_record(record),
                **HONESTY,
            }

        dedupe_key = f"{row['id']}:{event_id}"
        seen = self.store.find(
            vocab.COLLECTION_DEDUPE,
            {"dedupe_key": dedupe_key},
            limit=5,
        )
        body["state"] = vocab.DUPLICATE if seen else vocab.VERIFIED
        body["dedupe_key"] = dedupe_key
        record = self.store.create(vocab.COLLECTION_DELIVERIES, body, actor=actor, source=source)

        if not seen:
            self.store.create(
                vocab.COLLECTION_DEDUPE,
                {
                    "dedupe_key": dedupe_key,
                    "event_id": str(event_id),
                    "callback_ref": row["id"],
                    vocab.ROOM_REF: str(room_ref),
                    "first_seen_at": self._stamp(),
                    "delivery_ref": record["id"],
                },
                actor=actor,
                source=source,
            )

        return {
            "state": body["state"],
            # The magic string, on a duplicate as well as on a first delivery. A duplicate
            # the provider reads as a failure costs a retry, and a retry walks the ladder.
            "acknowledgement": vocab.ACKNOWLEDGEMENT_BODY,
            "http_status": vocab.ACKNOWLEDGEMENT_STATUS,
            "event_id": str(event_id),
            "event_type": event.get(vocab.EVENT_TYPE_FIELD),
            "checks": checks,
            "delivery": delivery_record(record),
            **HONESTY,
        }

    # -- reads --------------------------------------------------------------- #

    def deliveries(
        self,
        room_ref: str,
        *,
        state: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = self.store.find(
            vocab.COLLECTION_DELIVERIES,
            {vocab.ROOM_REF: str(room_ref)},
            limit=max(1, min(int(limit), 500)),
        )
        out = [delivery_record(row) for row in rows]
        if state:
            out = [row for row in out if row["state"] == state]
        return out

    def summary(self, room_ref: str | None = None) -> dict[str, Any]:
        """The board's headline numbers for one room.

        Reads only, so it writes no audit row. A route that wrote on every read would fill the
        guarantee's own log with rows describing nothing having happened.
        """
        where = {vocab.ROOM_REF: room_ref} if room_ref else {}
        callbacks = self.store.find(vocab.COLLECTION_CALLBACKS, where, limit=200)
        rows = [
            delivery_record(row)
            for row in self.store.find(vocab.COLLECTION_DELIVERIES, where, limit=500)
        ]
        by_state: dict[str, int] = {}
        by_error: dict[str, int] = {}
        for row in rows:
            by_state[row["state"]] = by_state.get(row["state"], 0) + 1
            if row.get("error_name"):
                by_error[row["error_name"]] = by_error.get(row["error_name"], 0) + 1

        snapshot = (
            self._snapshot_for(str(room_ref))["summary"]
            if room_ref
            else {"range_count": 0, "snapshot_at": None, "stale": False, "staleness": ""}
        )
        deduped = len(
            self.store.find(
                vocab.COLLECTION_DEDUPE,
                where,
                limit=1000,
            )
        )
        return {
            "ticket": "WF-082",
            "room_ref": room_ref,
            "callbacks": len(callbacks),
            "deliveries": len(rows),
            "by_state": by_state,
            "verified": by_state.get(vocab.VERIFIED, 0),
            "rejected": by_state.get(vocab.REJECTED, 0),
            "duplicates": by_state.get(vocab.DUPLICATE, 0),
            "by_error_name": by_error,
            "dedupe_ids": deduped,
            "ranges": snapshot["range_count"],
            "snapshot_at": snapshot["snapshot_at"],
            "snapshot_stale": snapshot["stale"],
            "staleness": snapshot["staleness"],
            "key_origin": self.vault_key.origin,
            "key_warning": self.key_warning(),
            "checks": list(vocab.CHECK_ORDER),
            **HONESTY,
        }

    def vocabulary(self) -> dict[str, Any]:
        """Every researched constant, served as data.

        A client renders its labels and its ladder from this rather than from a list compiled
        into the page, so the editor cannot disagree with the validator about what is legal.
        """
        return {
            "ticket": "WF-082",
            "user_agent": vocab.USER_AGENT,
            "content_sha256_header": vocab.CONTENT_SHA256_HEADER,
            "json_part": vocab.JSON_PART,
            "content_type": "multipart/form-data",
            "acknowledgement": {
                "status": vocab.ACKNOWLEDGEMENT_STATUS,
                "body": vocab.ACKNOWLEDGEMENT_BODY,
                "note": (
                    "The body is a magic string, not a status code. The provider checks for it, "
                    "so a 200 with an empty body is still read as a failed callback."
                ),
            },
            "check_order": list(vocab.CHECK_ORDER),
            "checks": [
                {
                    "check": vocab.IP_ALLOWLIST,
                    "covers": "the source address",
                    "reads": "a stored snapshot of the published range file",
                    "failure": "source_ip_not_allowed",
                },
                {
                    "check": vocab.CONTENT_SHA256,
                    "covers": "the whole JSON payload",
                    "reads": f"the {vocab.CONTENT_SHA256_HEADER} header",
                    "failure": "content_sha256_mismatch",
                },
                {
                    "check": vocab.EVENT_HASH,
                    "covers": "event_time concatenated with event_type",
                    "reads": "the event_hash field in the payload",
                    "failure": "event_hash_mismatch",
                },
            ],
            "delivery_states": list(vocab.DELIVERY_STATES),
            "event_hash": {
                "field": vocab.EVENT_HASH_FIELD,
                "input_fields": list(vocab.EVENT_HASH_INPUT_FIELDS),
                "separator": vocab.EVENT_HASH_SEPARATOR,
                "evidence": "echo -n $event_time$event_type | openssl dgst -sha256 -hmac $apikey",
                "note": (
                    "The two values are concatenated with no separator. The content digest "
                    "below is a different check over different bytes."
                ),
            },
            "content_sha256": {
                "header": vocab.CONTENT_SHA256_HEADER,
                "covers": "the whole JSON payload",
                "encoding": "base64",
                "evidence": "echo -n $json | openssl dgst -sha256 -hmac $apiKey",
                "note": "The two digests are not interchangeable.",
            },
            "event_types": {
                "all_signed": vocab.SIGNATURE_REQUEST_ALL_SIGNED,
                "downloadable": vocab.SIGNATURE_REQUEST_DOWNLOADABLE,
                "warning": (
                    "Final document generation lags signing. If you plan to download the "
                    f"final files, wait for {vocab.SIGNATURE_REQUEST_DOWNLOADABLE}."
                ),
            },
            "event_metadata_keys": list(vocab.EVENT_METADATA_KEYS),
            "callback_scopes": list(vocab.CALLBACK_SCOPES),
            "required_scheme": vocab.REQUIRED_SCHEME,
            "tls_enforcement_date": vocab.TLS_ENFORCEMENT_DATE,
            "provider_timeout_seconds": vocab.PROVIDER_TIMEOUT_SECONDS,
            "retry_ladder": vocab.retry_ladder(),
            "retry_multiplier": vocab.RETRY_MULTIPLIER,
            "consecutive_failure_limit": vocab.CONSECUTIVE_FAILURE_LIMIT,
            "self_disable": (
                f"After {vocab.CONSECUTIVE_FAILURE_LIMIT} consecutive failures the provider "
                "clears the callback URL. Nothing is reported on either side when that "
                "happens, so this handler answers inside "
                f"{vocab.PROVIDER_TIMEOUT_SECONDS}s and never fetches on the delivery path."
            ),
            "ip_ranges": {
                "url": vocab.IP_RANGES_URL,
                "recheck": vocab.IP_RANGES_RECHECK,
                "stale_after_seconds": rules.DEFAULT_STALE_AFTER_SECONDS,
            },
            "credential_header": vocab.CREDENTIAL_HEADER,
            "error_catalogue": {
                "codes_name": vocab.ERROR_CODES_CATALOGUE,
                "events_name": vocab.ERROR_EVENTS_CATALOGUE,
                "fields": list(vocab.ERROR_CODE_FIELDS),
                "codes": {
                    name: {"error_name": name, **entry}
                    for name, entry in sorted(vocab.ERROR_CODES.items())
                },
            },
            "key_env": vocab.KEY_ENV,
            "key_origin": self.vault_key.origin,
            "key_is_published_default": self.vault_key.is_default,
            "key_warning": self.key_warning(),
            "router_prefix": vocab.ROUTER_PREFIX,
            "transparency": {
                "wiring_risk": (
                    f"When more than one registration is bound to a room, a delivery must "
                    f"name one in {vocab.CREDENTIAL_HEADER}. This handler does not read a "
                    "forwarded header: it uses the socket peer address, so mounting this "
                    "route behind a proxy or a public load balancer will refuse every "
                    "delivery. The check cannot be skipped by a request, so it is not "
                    "silently defeated, but it does need correct network wiring."
                )
            },
            **HONESTY,
        }

    def inferences(self) -> dict[str, Any]:
        from dsr.security_governance import webhook_inferences

        return {
            "ticket": "WF-082",
            "count": webhook_inferences.count(),
            "decisions": webhook_inferences.describe(),
        }

    def read_inference(self, decision_id: str) -> dict[str, Any] | None:
        from dsr.security_governance import webhook_inferences

        return webhook_inferences.describe_one(decision_id)


def delivery_record(row: Mapping[str, Any]) -> dict[str, Any]:
    """One delivery as the API serves it.

    The checks are carried whole rather than collapsed to a boolean. A delivery log that
    recorded only ``passed`` cannot answer a question a month later, and the three checks
    each hold a different fact: which range matched, which digest was expected, whether the
    event id had been seen.
    """
    data = row["data"]
    return {
        "id": row["id"],
        vocab.ROOM_REF: data.get(vocab.ROOM_REF),
        "callback_ref": data.get("callback_ref"),
        "state": data.get("state"),
        "error_name": data.get("error_name"),
        "http_status": data.get("http_status"),
        "failed_check": data.get("failed_check"),
        "source_ip": data.get("source_ip"),
        "event_id": data.get("event_id"),
        "event_type": data.get("event_type"),
        "event_time": data.get("event_time"),
        "signature_request_ref": data.get("signature_request_ref"),
        "content_sha256": data.get("content_sha256"),
        "checks": data.get("checks") or [],
        "snapshot_at": data.get("snapshot_at"),
        "snapshot_stale": data.get("snapshot_stale"),
        "recorded_at": row.get("created_at"),
    }


def _passed(check: Mapping[str, Any]) -> bool:
    """Whether one of the three checks passed.

    Two shapes reach this function and both are legitimate. The two digest checks in
    :mod:`webhook_signing` report ``passed``. The allowlist check in :mod:`webhook_rules`
    reports ``allowed``, because its return value is a policy decision about an address
    rather than a comparison of two digests.

    Reading only ``passed`` would mark every allowed address as a failure, and every delivery
    would be refused at the first check with a message about a signature. The two field names
    are a disagreement between the modules rather than a defect in either, so the seam is named
    here in one place instead of being papered over by normalising a field upstream.
    """
    if "passed" in check:
        return bool(check["passed"])
    return bool(check.get("allowed"))


def _error_name(check: Mapping[str, Any]) -> str:
    """The catalogue key for one failed check, whichever shape it arrived in.

    The digest checks report their refusal as ``reason``, which is an ``error_name``. The
    allowlist check reports its refusal as ``error_name`` and its prose as ``reason``, so the
    two are read from different keys. Returning a prose sentence as a catalogue key is what
    made a refusal answer ``"This build has no catalogue entry named the source address is
    not in the published range file"``.
    """
    if "error_name" in check:
        return str(check.get("error_name") or "source_ip_not_allowed")
    return str(check.get("reason") or "source_ip_not_allowed")


def _ref(value: Any) -> str | None:
    """The id out of a sibling object, whichever key the payload used.

    The evidence quotes ``signature_request:{...}`` without naming its keys, so four plausible
    names are read. The reference is recorded rather than interpreted: this workflow
    authenticates the delivery and does not act on the agreement, which is WF-080's job.
    """
    if not isinstance(value, Mapping):
        return None
    for key in ("signature_request_id", "id", "request_id", "reference_id"):
        found = value.get(key)
        if found:
            return str(found)
    return None


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)
