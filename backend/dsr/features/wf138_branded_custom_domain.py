"""WF-138: a branded room on the customer's own domain.

The rules live in :mod:`dsr.competitive_baseline.branded_domain` and are not
restated here. This module is the three things a feature contributes, and the
three things it must never contribute.

What this module contributes
----------------------------

* The route table, under a prefix this feature owns.
* The mapping from this workflow's own error types to responses, exported as
  :data:`EXCEPTION_HANDLERS` because FastAPI accepts handlers on the app object
  only and this feature may not edit the app.
* The demo rows, as ``seed(db, context)`` rather than as an edit to the shared
  ``backend/seed.py``.

What it must never contribute
-----------------------------

* No shared file. ``dsr/api.py``, ``dsr/deps.py``, ``dsr/store.py``,
  ``dsr/db/audited.py``, ``backend/seed.py``, ``App.jsx``, ``main.jsx``,
  ``lib/api.js``, ``lib/features.js``, ``components/ui.jsx``, ``vite.config.js``.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``.
* No hand-written ``source=`` string. :func:`_source` builds every one from
  :data:`router`, so the audit row names the route that actually served the
  write.

Why the hostname rule is called from here and not from the domain module
-----------------------------------------------------------------------
``backend/tests/test_wf122.py`` globs every module in
``dsr/competitive_baseline/`` and refuses any ``dsr`` import other than
``dsr.store``. That test ships on ``main`` and this workflow does not own it, so
:mod:`dsr.competitive_baseline.branded_domain` imports no ``dsr`` module at all.
The hostname rule is WF-017's ``dsr.domains.normalise_domain``, and it is called
*here*, at the feature seam, so the rule has exactly one implementation and this
workflow still holds no second set of domain records. That is the answer to the
hazard the issue names: two domain tables would let one host be claimed twice.

Two route-parameter names are chosen for the route verifier
-----------------------------------------------------------
``tools/verify_all_routes.py`` substitutes a value for every path placeholder it
knows and leaves an unknown one in the URL, which then fails as a request that
never answered. So this feature uses ``account_id`` and ``room_id``, both of
which the verifier knows, rather than inventing a name it would leave in place.
``account_id`` is the id of the account's brand record, which is the resource
these routes address.

A brand and a room are different things
---------------------------------------
A brand belongs to an account: it holds the owner identity, the white-label
switch and the tenant token overrides. A room joins a brand, inherits the
brand's current tokens, and may refine them with its own override. Propagation
is eager, so the moment a brand's tokens change the change is written to every
room of that brand, and a new room inherits the current tokens rather than the
product defaults.

The revision field is ``brand_revision``, not ``revision``
---------------------------------------------------------
``revision`` is one of the store's own envelope keys, and the audited wrapper
strips it out of ``data`` on the way in. A payload that stored it there lost it
silently, and every room then looked stale forever. The field is named
``brand_revision`` so the two cannot be confused.
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Any, Mapping

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.competitive_baseline.branded_domain import (
    BRAND_COLLECTION,
    CLAIM_COLLECTION,
    CLAIM_EXPECTATIONS,
    CLAIM_RELEASED,
    CLAIM_UNVERIFIED,
    CLAIM_VERIFIED,
    EVENT_BRAND_CREATED,
    EVENT_COLLECTION,
    EVENT_DOMAIN_CLAIMED,
    EVENT_DOMAIN_RELEASED,
    EVENT_PROPAGATED,
    EVENT_ROOM_ATTACHED,
    EVENT_TOKENS_SET,
    EVENT_WHITE_LABELLED,
    ROOM_THEME_COLLECTION,
    UNVERIFIABLE_HERE,
    BrandConflict,
    BrandedDomainError,
    adopt_room,
    claim_is_serving,
    claim_next_state,
    claimable,
    effective_tokens,
    presented_owner,
    propagation_plan,
    resolve_host,
    token_css_variables,
    validate_brand,
    validate_token_map,
    verify_report,
    vocabulary,
)
from dsr.db.audited import utcnow
from dsr.deps import StoreDep
from dsr.domains import (
    DomainError,
    cname_target,
    default_base_url,
    normalise_domain,
    resolver_from_env,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-138-branded-domain",
    "ticket": "WF-138",
    "name": "Branded room on your own domain",
    "description": (
        "A brand belongs to an account and carries the owner identity, the "
        "white-label switch and the tenant token overrides. A room joins a brand "
        "and inherits the brand's current tokens; a brand change is propagated to "
        "every room at once. In white-label mode the client's brand is presented "
        "and the agency's name is withheld."
    ),
    "nav": [{"id": "wf-138-branded", "label": "Branded domain"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several
#: workflows already share. Every path below sits under it.
router = APIRouter(prefix="/api/wf-138", tags=["WF-138"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal. A hardcoded path is the
    defect hard rule 4 names: the audit row keeps naming a route the app stopped
    serving, and nothing fails when it does.
    """
    return f"{method} {router.prefix}{path}"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# Both types are this workflow's own, declared by
# ``dsr.competitive_baseline.branded_domain`` and raised by nothing else in the
# product. That is what makes it safe to map them here: the host refuses two
# features mapping the same type, and a handler for ``ValueError`` -- which
# ``BrandedDomainError`` subclasses -- would intercept exceptions product-wide.


def _branded_error(request: Request, exc: BrandedDomainError) -> JSONResponse:
    """422 with a field-keyed map, so each message lands beside its input."""
    return JSONResponse(
        status_code=422,
        content={"error": "branded_domain_invalid", "detail": str(exc), "errors": exc.errors},
    )


def _brand_conflict(request: Request, exc: BrandConflict) -> JSONResponse:
    """409 for a change this workflow's state refuses.

    409 rather than 422 because nothing about the request is malformed: another
    brand holds the host, or the checks did not pass. A client that retries the
    same call gets the same answer, and ``reason`` is a stable token the page
    branches on.
    """
    return JSONResponse(
        status_code=409,
        content={"error": "brand_conflict", "reason": exc.reason, "detail": str(exc)},
    )


EXCEPTION_HANDLERS = {
    BrandedDomainError: _branded_error,
    BrandConflict: _brand_conflict,
}


# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #

_RESOLVER_CACHE: dict[tuple[str, str], Any] = {}


def _resolver() -> Any:
    """The DNS resolver the deployment configured, memoised on its environment.

    Memoised on the *configuration* rather than on the first call, so a
    deployment that changes ``DSR_CNAME_FIXTURES`` gets a fresh resolver and a
    test that points the fixtures somewhere else still sees the change. Built by
    WF-017's ``resolver_from_env``, so verification reads DNS the same way in
    both workflows.
    """
    key = (os.environ.get("DSR_CNAME_FIXTURES", ""), os.environ.get("DSR_EDGE_ADDRESSES", ""))
    cached = _RESOLVER_CACHE.get(key)
    if cached is None:
        cached = resolver_from_env()
        _RESOLVER_CACHE[key] = cached
    return cached


def _deployment_host() -> str:
    """The host this deployment answers on by default, without scheme or port."""
    host = default_base_url().split("//", 1)[-1].split("/", 1)[0]
    if host.count(":") == 1:
        host = host.rsplit(":", 1)[0]
    return host.lower().rstrip(".")


def _normalise_host(raw: object) -> str:
    """Normalise a host with WF-017's rule, as this workflow's own error.

    The rule itself is not restated: it is the same "it needs to be in a
    subdomain format" rule WF-017 records, and it lives in one place. Only the
    error type is converted, so this workflow's routes answer with this
    workflow's error vocabulary.
    """
    try:
        return normalise_domain(raw)
    except DomainError as exc:
        raise BrandedDomainError("The host could not be read.", {"host": str(exc)}) from exc


def _claim_row(record: Mapping[str, Any]) -> dict[str, Any]:
    """One stored claim as a flat row, for the pure routing rules."""
    data = record.get("data") or {}
    return {
        "id": record.get("id"),
        "host": data.get("host"),
        "state": data.get("state", CLAIM_UNVERIFIED),
        "brand_id": data.get("brand_id"),
    }


def _theme_row(record: Mapping[str, Any]) -> dict[str, Any]:
    """One stored room theme as a flat row, for the pure propagation rule."""
    data = record.get("data") or {}
    return {
        "theme_id": record.get("id"),
        "room_ref": data.get("room_ref"),
        "brand_id": data.get("brand_id"),
        "tokens": dict(data.get("tokens") or {}),
        "override": dict(data.get("override") or {}),
        "brand_revision": data.get("brand_revision") or 0,
    }


class BrandingService:
    """Brands, tenant token overrides, hostname claims and propagation.

    The only writer of this workflow. Every mutation goes through
    :class:`dsr.store.RecordStore`, a thin façade over
    :class:`dsr.db.audited.AuditedDatabase`, so the audit row is written in the
    same transaction as the change. No new column, no migration: a brand, its
    hostname claims and a room's theme are ordinary JSON in ``records.data``.
    """

    def __init__(self, store: RecordStore, resolver: Any | None = None) -> None:
        self.store = store
        self.resolver = resolver if resolver is not None else _resolver()

    # -- reads -------------------------------------------------------------- #

    def brands(self) -> list[dict[str, Any]]:
        return self.store.list(BRAND_COLLECTION, limit=1000)

    def all_claims(self) -> list[dict[str, Any]]:
        return self.store.list(CLAIM_COLLECTION, limit=1000)

    def claims_for(self, account_id: str) -> list[dict[str, Any]]:
        records = self.store.find(CLAIM_COLLECTION, {"brand_id": account_id}, limit=1000)
        return sorted(records, key=lambda r: str((r.get("data") or {}).get("host") or ""))

    def themes_for(self, account_id: str) -> list[dict[str, Any]]:
        """Every room on this brand, with the tokens it carries.

        Read through the dynamic index on ``brand_id``, so it costs one lookup
        rather than a scan of every room.
        """
        records = self.store.find(ROOM_THEME_COLLECTION, {"brand_id": account_id}, limit=1000)
        return sorted(records, key=lambda r: str((r.get("data") or {}).get("room_ref") or ""))

    def require_brand(self, account_id: str) -> dict[str, Any]:
        """A brand record, or ``KeyError``. The route turns that into a 404."""
        record = self.store.get(account_id)
        if record is None or record.get("collection") != BRAND_COLLECTION:
            raise KeyError(account_id)
        return record

    def _live_claim(self, account_id: str, host: str) -> dict[str, Any] | None:
        for claim in self.claims_for(account_id):
            data = claim.get("data") or {}
            if data.get("host") == host and data.get("state") != CLAIM_RELEASED:
                return claim
        return None

    def describe_claim(self, claim: Mapping[str, Any]) -> dict[str, Any]:
        data = claim.get("data") or {}
        state = str(data.get("state") or CLAIM_UNVERIFIED)
        return {
            "id": claim.get("id"),
            "host": data.get("host"),
            "brand_id": data.get("brand_id"),
            "state": state,
            "serving": claim_is_serving(state),
            "expectation": CLAIM_EXPECTATIONS.get(state),
            "cname_target": data.get("cname_target"),
            "observed": list(data.get("observed") or []),
            "last_checked_at": data.get("last_checked_at"),
            "verified_at": data.get("verified_at"),
            "released_at": data.get("released_at"),
        }

    def describe_brand(self, brand: Mapping[str, Any]) -> dict[str, Any]:
        data = brand.get("data") or {}
        tokens = dict(data.get("tokens") or {})
        return {
            "account_id": brand.get("id"),
            "name": data.get("name"),
            "account_ref": data.get("account_ref"),
            "owner_name": data.get("owner_name"),
            "client_name": data.get("client_name"),
            "white_label": bool(data.get("white_label")),
            "brand_revision": int(data.get("brand_revision") or 0),
            "presented": presented_owner(data),
            "tokens": tokens,
            "css_variables": token_css_variables(tokens),
            "hosts": [
                self.describe_claim(claim) for claim in self.claims_for(str(brand.get("id")))
            ],
            "cannot_verify": list(UNVERIFIABLE_HERE),
        }

    def describe_theme(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = record.get("data") or {}
        tokens = dict(data.get("tokens") or {})
        return {
            "theme_id": record.get("id"),
            "room_ref": data.get("room_ref"),
            "brand_id": data.get("brand_id"),
            "tokens": tokens,
            "override": dict(data.get("override") or {}),
            "css_variables": token_css_variables(tokens),
            "brand_revision": int(data.get("brand_revision") or 0),
            "inherited": bool(data.get("inherited")),
        }

    def events_of(self, account_id: str) -> list[dict[str, Any]]:
        records = self.store.find(EVENT_COLLECTION, {"brand_id": account_id}, limit=200)
        ordered = sorted(records, key=lambda r: str(r.get("created_at") or ""))
        return [
            {
                "id": record.get("id"),
                "kind": (record.get("data") or {}).get("kind"),
                "at": (record.get("data") or {}).get("at"),
                "detail": (record.get("data") or {}).get("detail"),
                "host": (record.get("data") or {}).get("host"),
            }
            for record in ordered
        ]

    def summary(self) -> dict[str, Any]:
        """The headline numbers: brands, host states, rooms themed, ambiguity."""
        brands = self.brands()
        claims = self.all_claims()
        themes = self.store.list(ROOM_THEME_COLLECTION, limit=1000)

        states = Counter(_claim_row(claim)["state"] for claim in claims)
        verified_hosts = Counter(
            _claim_row(claim)["host"]
            for claim in claims
            if _claim_row(claim)["state"] == CLAIM_VERIFIED
        )
        ambiguous = sorted(host for host, count in verified_hosts.items() if host and count > 1)

        return {
            "brands": len(brands),
            "white_labelled": sum(
                1 for brand in brands if (brand.get("data") or {}).get("white_label")
            ),
            "claims": len(claims),
            "verified": states.get(CLAIM_VERIFIED, 0),
            "unverified": states.get(CLAIM_UNVERIFIED, 0),
            "failed": states.get("failed", 0),
            "released": states.get(CLAIM_RELEASED, 0),
            "ambiguous_hosts": ambiguous,
            "rooms_themed": len(themes),
            "rooms_inheriting": sum(
                1 for theme in themes if (theme.get("data") or {}).get("inherited")
            ),
            "token_overrides": sum(
                len((brand.get("data") or {}).get("tokens") or {}) for brand in brands
            ),
            "events": len(self.store.list(EVENT_COLLECTION, limit=1000)),
            "deployment_host": _deployment_host(),
        }

    # -- writes: brands and tokens ------------------------------------------ #

    def _event(
        self,
        brand_id: str,
        kind: str,
        *,
        actor: str | None,
        source: str,
        detail: str = "",
        host: str | None = None,
    ) -> None:
        """Record one brand event. Every event is an audited write."""
        self.store.create(
            EVENT_COLLECTION,
            {"brand_id": brand_id, "kind": kind, "at": utcnow(), "detail": detail, "host": host},
            actor=actor,
            source=source,
        )

    def create_brand(
        self, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Create one account's brand."""
        fields = validate_brand(payload)
        tokens = validate_token_map(payload.get("tokens")) if "tokens" in payload else {}
        record = self.store.create(
            BRAND_COLLECTION,
            {**fields, "tokens": tokens, "brand_revision": 1},
            actor=actor,
            source=source,
        )
        self._event(
            str(record["id"]),
            EVENT_BRAND_CREATED,
            actor=actor,
            source=source,
            detail=str(fields.get("name") or ""),
        )
        return self.describe_brand(record)

    def update_brand(
        self, account_id: str, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Set a brand's identity and its white-label switch.

        Merged onto what is stored, because a form that sends only the field the
        operator touched must not blank the rest. Turning white-label mode on
        requires the client's name: the switch swaps an identity, so a swap with
        nothing to swap in is refused rather than silently hiding the brand.
        """
        brand = self.require_brand(account_id)
        data = dict(brand.get("data") or {})
        incoming = {key: value for key, value in dict(payload or {}).items() if key != "tokens"}
        fields = validate_brand({**data, **incoming})
        was_white = bool(data.get("white_label"))

        updated = self.store.update(
            account_id,
            {
                "name": fields["name"],
                "owner_name": fields["owner_name"],
                "client_name": fields["client_name"],
                "white_label": fields["white_label"],
            },
            actor=actor,
            source=source,
        )
        if fields["white_label"] and not was_white:
            self._event(
                account_id,
                EVENT_WHITE_LABELLED,
                actor=actor,
                source=source,
                detail=str(fields.get("client_name") or ""),
            )
        return self.describe_brand(updated)

    def set_tokens(
        self, account_id: str, payload: Any, *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Replace a brand's tenant token override and propagate it.

        The resource is the map itself: the body of this call *is* the
        token-to-token mapping. The revision is bumped so propagation is
        idempotent, and the new tokens are written to every room of the brand in
        the same request, which is the researched "auto-propagate a brand change
        to every tenant".
        """
        brand = self.require_brand(account_id)
        tokens = validate_token_map(payload)
        revision = int((brand.get("data") or {}).get("brand_revision") or 0) + 1

        updated = self.store.update(
            account_id, {"tokens": tokens, "brand_revision": revision}, actor=actor, source=source
        )
        self._event(
            account_id,
            EVENT_TOKENS_SET,
            actor=actor,
            source=source,
            detail=f"{len(tokens)} token override(s)",
        )
        propagation = self.propagate(
            account_id, tokens=tokens, revision=revision, actor=actor, source=source
        )
        return {"brand": self.describe_brand(updated), "propagation": propagation}

    def propagate(
        self,
        account_id: str,
        *,
        tokens: Mapping[str, str] | None = None,
        revision: int | None = None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Write the brand's current tokens to every room of the brand.

        Idempotent by revision: a room that already carries the current revision
        is reported ``changed: False`` and nothing is written for it. A room's
        own override wins, so such a room is marked ``inherited: False``; a room
        of another brand is not touched at all.
        """
        brand = self.require_brand(account_id)
        data = brand.get("data") or {}
        current = dict(data.get("tokens") or {}) if tokens is None else dict(tokens)
        target_revision = (
            int(data.get("brand_revision") or 0) if revision is None else int(revision)
        )

        rooms = [
            _theme_row(record)
            for record in self.store.find(
                ROOM_THEME_COLLECTION, {"brand_id": account_id}, limit=1000
            )
        ]
        plan = propagation_plan(account_id, current, target_revision, rooms)

        updated = 0
        for row in plan:
            if not row["changed"]:
                continue
            self.store.update(
                str(row["theme_id"]),
                {
                    "tokens": row["tokens"],
                    "override": row["override"],
                    "brand_revision": row["brand_revision"],
                    "inherited": row["inherited"],
                },
                actor=actor,
                source=source,
            )
            updated += 1

        if plan:
            self._event(
                account_id,
                EVENT_PROPAGATED,
                actor=actor,
                source=source,
                detail=f"{updated} of {len(plan)} room(s) updated",
            )
        return {
            "brand_id": account_id,
            "brand_revision": target_revision,
            "rooms": len(plan),
            "updated": updated,
            "rows": plan,
        }

    # -- writes: hostname claims -------------------------------------------- #

    def verify(self, account_id: str, host: object) -> dict[str, Any]:
        """Check a candidate host without claiming it. Writes nothing.

        Returns every check with what it saw, so the operator can see what is
        wrong before committing. The account is *not* required to exist:
        checking whether a host is up for grabs is a read, and the researched
        flow is verify-then-save.
        """
        normalised = _normalise_host(host)
        target = cname_target()
        matched, observed = self.resolver.resolves_to(normalised, target)
        holder = claimable(
            normalised, [_claim_row(claim) for claim in self.all_claims()], brand_id=account_id
        )
        report = verify_report(
            normalised, matched=matched, observed=observed, cname_target=target, holder=holder
        )
        report["brand_id"] = account_id
        return report

    def claim(
        self,
        account_id: str,
        host: object,
        *,
        force: bool = False,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Attach a host to a brand, after verification.

        The researched order is verify-then-save, so a failing check refuses the
        write unless ``force`` is set -- the escape hatch for the customer
        mid-propagation, whose CNAME "may take up to 24 hours" to spread. A host
        another brand holds, verified, is never overridable: that would hand one
        customer's host to another.
        """
        self.require_brand(account_id)
        normalised = _normalise_host(host)
        report = self.verify(account_id, normalised)

        if not report["ready"]:
            failing = [check for check in report["checks"] if not check["ok"]]
            clash = next((check for check in failing if check["name"] == "available"), None)
            if clash is not None:
                raise BrandConflict(
                    BrandConflict.REASON_HOST_TAKEN,
                    f"{normalised} is already held by another brand: {clash['detail']}",
                )
            if not force:
                labels = "; ".join(check["label"] for check in failing)
                raise BrandConflict(
                    BrandConflict.REASON_NOT_READY,
                    f"{normalised} is not ready: {labels}. Re-run verification once the CNAME has "
                    "propagated, or save anyway.",
                )

        state = CLAIM_VERIFIED if report["ready"] else CLAIM_UNVERIFIED
        checked_at = utcnow()
        patch: dict[str, Any] = {
            "host": normalised,
            "brand_id": account_id,
            "state": state,
            "cname_target": report["cname_target"],
            "observed": report["observed"],
            "last_checked_at": checked_at,
            "verified_at": checked_at if state == CLAIM_VERIFIED else None,
            "released_at": None,
        }

        existing = self._live_claim(account_id, normalised)
        if existing is None:
            record = self.store.create(CLAIM_COLLECTION, patch, actor=actor, source=source)
        else:
            record = self.store.update(str(existing["id"]), patch, actor=actor, source=source)

        self._event(
            account_id,
            EVENT_DOMAIN_CLAIMED,
            actor=actor,
            source=source,
            detail=state,
            host=normalised,
        )
        brand = self.require_brand(account_id)
        return {
            "brand": self.describe_brand(brand),
            "claim": self.describe_claim(record),
            "report": report,
        }

    def release(
        self, account_id: str, host: object, *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Give a host back. The host is then free for another brand.

        The brand's rooms do not move: a room is attached to a brand, not to a
        host, so releasing a host never strands a room.
        """
        self.require_brand(account_id)
        normalised = _normalise_host(host)
        existing = self._live_claim(account_id, normalised)
        if existing is None:
            raise BrandConflict(
                BrandConflict.REASON_NO_HOST, f"brand {account_id} holds no host {normalised}"
            )

        record = self.store.update(
            str(existing["id"]),
            {"state": CLAIM_RELEASED, "released_at": utcnow(), "verified_at": None},
            actor=actor,
            source=source,
        )
        self._event(account_id, EVENT_DOMAIN_RELEASED, actor=actor, source=source, host=normalised)
        return {
            "brand": self.describe_brand(self.require_brand(account_id)),
            "claim": self.describe_claim(record),
        }

    def recheck(self, account_id: str, *, actor: str | None, source: str) -> dict[str, Any]:
        """Re-run verification for every host the brand holds.

        The route an operator polls during the researched propagation wait, so
        they never have to retype a host to find out whether it has landed.
        """
        self.require_brand(account_id)
        live = [
            claim
            for claim in self.claims_for(account_id)
            if (claim.get("data") or {}).get("state") != CLAIM_RELEASED
        ]
        if not live:
            raise BrandConflict(
                BrandConflict.REASON_NO_HOST, f"brand {account_id} holds no host to check"
            )

        checked: list[dict[str, Any]] = []
        for claim in live:
            data = claim.get("data") or {}
            report = self.verify(account_id, data.get("host"))
            state = claim_next_state(
                str(data.get("state") or CLAIM_UNVERIFIED),
                "verify_ok" if report["ready"] else "verify_fail",
            )
            record = self.store.update(
                str(claim["id"]),
                {
                    "state": state,
                    "observed": report["observed"],
                    "last_checked_at": utcnow(),
                    "verified_at": utcnow() if state == CLAIM_VERIFIED else None,
                },
                actor=actor,
                source=source,
            )
            checked.append({"claim": self.describe_claim(record), "report": report})

        return {"brand": self.describe_brand(self.require_brand(account_id)), "checked": checked}

    # -- writes: rooms ------------------------------------------------------- #

    def attach_room(
        self,
        account_id: str,
        room_id: str,
        *,
        override: Any = None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Put a room on a brand, inheriting the brand's current tokens.

        A new room inherits the tokens the brand has *now*, not the product
        defaults, which is the researched "auto-apply brand tokens to new
        rooms". A room that already carries an override keeps it and only the
        brand reference moves.
        """
        self.require_brand(account_id)
        room = self.store.get(room_id)
        if room is None or room.get("collection") != "room":
            raise KeyError(room_id)

        brand = self.require_brand(account_id)
        data = brand.get("data") or {}
        existing_override = {}
        previous = self.store.find(ROOM_THEME_COLLECTION, {"room_ref": room_id}, limit=2)
        if previous:
            existing_override = dict((previous[0].get("data") or {}).get("override") or {})

        merged = dict(existing_override)
        merged.update(validate_token_map(override))
        theme = adopt_room(
            account_id,
            data.get("tokens") or {},
            int(data.get("brand_revision") or 0),
            room_id,
        )
        theme["override"] = merged
        theme["tokens"] = effective_tokens(data.get("tokens"), merged)
        theme["inherited"] = not merged

        if previous:
            record = self.store.update(str(previous[0]["id"]), theme, actor=actor, source=source)
        else:
            record = self.store.create(
                ROOM_THEME_COLLECTION, theme, room_id=room_id, actor=actor, source=source
            )

        self._event(account_id, EVENT_ROOM_ATTACHED, actor=actor, source=source, detail=room_id)
        return {
            "theme": self.describe_theme(record),
            "brand": self.describe_brand(self.require_brand(account_id)),
        }

    def theme_for_room(self, room_id: str) -> dict[str, Any]:
        """The theme a room renders with, and the brand behind it."""
        found = self.store.find(ROOM_THEME_COLLECTION, {"room_ref": room_id}, limit=1)
        if not found:
            return {
                "room_ref": room_id,
                "branded": False,
                "tokens": {},
                "css_variables": {},
                "detail": "This room has not joined a brand, so it renders the product defaults.",
            }
        theme = self.describe_theme(found[0])
        brand = self.store.get(str(theme["brand_id"]))
        return {
            "room_ref": room_id,
            "branded": True,
            "presented": presented_owner(brand.get("data") or {}) if brand else None,
            **theme,
        }

    # -- resolution by host -------------------------------------------------- #

    def resolve(self, host: object) -> dict[str, Any]:
        """Which brand serves ``host``, and the theme it renders with.

        A host held by two verified claims is refused rather than resolved,
        because the issue names "two possible answers" for host resolution as
        the specific hazard. A host no brand holds is reported as unclaimed. The
        deployment's own host is named separately: the app serves it, but it is
        not branded, and it is not a claimable name either.
        """
        raw_host = str(host or "").strip().lower().rstrip(".")
        if raw_host.count(":") == 1:
            raw_host = raw_host.rsplit(":", 1)[0]
        deployment = _deployment_host()

        result: dict[str, Any] = {
            "host": raw_host,
            "served": False,
            "reason": "unclaimed",
            "detail": "",
            "deployment_host": deployment,
            "is_deployment_host": raw_host == deployment,
            "claim": None,
            "brand": None,
            "cannot_verify": list(UNVERIFIABLE_HERE),
        }

        if result["is_deployment_host"]:
            result["detail"] = (
                f"{deployment} is the deployment's own host. The app serves it, and no brand "
                "is attached, so it is not a branded host."
            )
            return result

        normalised = _normalise_host(host)
        result["host"] = normalised
        decision = resolve_host(normalised, [_claim_row(claim) for claim in self.all_claims()])
        result["reason"] = decision["reason"]
        result["detail"] = decision["detail"]
        result["served"] = decision["reason"] == "ok"

        claim_row = decision["claim"]
        if claim_row is None:
            return result

        claim = self.store.get(str(claim_row["id"]))
        brand = self.store.get(str(claim_row["brand_id"]))
        result["claim"] = self.describe_claim(claim) if claim else None
        if brand is None:
            return result

        data = brand.get("data") or {}
        tokens = dict(data.get("tokens") or {})
        result["brand"] = {
            "account_id": brand.get("id"),
            "name": data.get("name"),
            "presented": presented_owner(data),
            "tokens": tokens,
            "css_variables": token_css_variables(tokens),
            "brand_revision": int(data.get("brand_revision") or 0),
        }
        return result


def get_branding_service(store: RecordStore = StoreDep) -> BrandingService:
    """The store plus the configured resolver, for one request."""
    return BrandingService(store, resolver=_resolver())


ServiceDep = Depends(get_branding_service)


def _account_or_404(account_id: str, service: BrandingService) -> dict[str, Any]:
    try:
        return service.require_brand(account_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"account {account_id} not found") from None


# --------------------------------------------------------------------------- #
# The board
# --------------------------------------------------------------------------- #


@router.get("/summary", summary="Brands, host states, rooms themed")
def summary(service: BrandingService = ServiceDep) -> dict[str, Any]:
    """The headline numbers. Reads only."""
    return service.summary()


@router.get("/vocabulary", summary="The token names, states and decisions")
def vocabulary_route() -> dict[str, Any]:
    """The vocabulary this workflow enforces, and every decision behind it.

    Served from the domain module, so the page reads its token names and claim
    states from here rather than hard-coding them, and a reviewer reads each
    decision without opening the source.
    """
    return vocabulary()


@router.get("/config", summary="What this deployment asks a customer to point at")
def config() -> dict[str, Any]:
    """The facts the setup screen needs, read from the server.

    The CNAME target is the deployment's own edge hostname. Surfacing it from
    here rather than hard-coding it in the UI is what stops a customer being told
    to point DNS at a vendor's host.
    """
    return {
        "cname_target": cname_target(),
        "base_url": default_base_url(),
        "deployment_host": _deployment_host(),
        "record_type": "CNAME",
        "propagation_note": (
            "A new CNAME can take up to 24 hours to propagate. Until it does, the host is saved "
            "and not serving."
        ),
        "cloudflare_note": (
            "If this domain is proxied by Cloudflare, set the CNAME to DNS only (proxy off). "
            "A proxied record will not verify and slows page loads."
        ),
        "format_note": "The host must be in subdomain format, for example deals.acme.com.",
        "token_note": (
            "A tenant override names another token, never a colour value, so the product's "
            "palette and its contrast floor survive a later theme change."
        ),
        "cannot_verify": list(UNVERIFIABLE_HERE),
    }


# --------------------------------------------------------------------------- #
# Accounts and brands
# --------------------------------------------------------------------------- #


@router.get("/accounts", summary="Every account's brand")
def list_accounts(service: BrandingService = ServiceDep) -> dict[str, Any]:
    """Every brand, so the page can offer one to work on."""
    brands = service.brands()
    return {
        "count": len(brands),
        "accounts": [service.describe_brand(brand) for brand in brands],
    }


@router.post("/accounts", summary="Create an account's brand")
def create_account(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    service: BrandingService = ServiceDep,
) -> dict[str, Any]:
    """Create one brand: its owner identity and, optionally, its token map."""
    return service.create_brand(payload, actor=actor, source=_source("POST", "/accounts"))


@router.get("/accounts/{account_id}/brand", summary="One brand")
def read_brand(account_id: str, service: BrandingService = ServiceDep) -> dict[str, Any]:
    """A brand's identity, its presented owner, its tokens and its hosts."""
    return service.describe_brand(_account_or_404(account_id, service))


@router.put("/accounts/{account_id}/brand", summary="Set a brand's identity")
def update_brand(
    account_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    service: BrandingService = ServiceDep,
) -> dict[str, Any]:
    """Set the brand name, the owner identity and the white-label switch.

    Turning white-label mode on requires the client's name, because the switch
    swaps the identity presented rather than adding a second one.
    """
    _account_or_404(account_id, service)
    return service.update_brand(
        account_id, payload, actor=actor, source=_source("PUT", "/accounts/{account_id}/brand")
    )


@router.put("/accounts/{account_id}/tokens", summary="Replace the tenant token override")
def set_tokens(
    account_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    service: BrandingService = ServiceDep,
) -> dict[str, Any]:
    """Replace the brand's token map and propagate it to every room.

    The body is the map itself: ``{"accent": "accent-bright"}``. A colour value
    is refused, because both sides of a pair must be one of this product's tokens.
    """
    _account_or_404(account_id, service)
    return service.set_tokens(
        account_id, payload, actor=actor, source=_source("PUT", "/accounts/{account_id}/tokens")
    )


@router.post("/accounts/{account_id}/propagate", summary="Apply the brand to every room")
def propagate(
    account_id: str,
    actor: str | None = Query(default=None),
    service: BrandingService = ServiceDep,
) -> dict[str, Any]:
    """Write the brand's current tokens to every room of the brand.

    Idempotent by revision, so a second call writes nothing. The control exists
    because a room attached while an earlier version of the tokens was current
    would otherwise keep the older map until the next token change.
    """
    _account_or_404(account_id, service)
    return service.propagate(
        account_id, actor=actor, source=_source("POST", "/accounts/{account_id}/propagate")
    )


@router.get("/accounts/{account_id}/theme", summary="The theme this brand renders with")
def account_theme(account_id: str, service: BrandingService = ServiceDep) -> dict[str, Any]:
    """The brand's token map, as scoped custom properties."""
    brand = _account_or_404(account_id, service)
    described = service.describe_brand(brand)
    return {
        "account_id": account_id,
        "brand_revision": described["brand_revision"],
        "presented": described["presented"],
        "tokens": described["tokens"],
        "css_variables": described["css_variables"],
    }


@router.get("/accounts/{account_id}/events", summary="The brand's change log")
def list_events(account_id: str, service: BrandingService = ServiceDep) -> dict[str, Any]:
    """Every brand change, host claim, release, propagation and attachment."""
    _account_or_404(account_id, service)
    return {"account_id": account_id, "events": service.events_of(account_id)}


# --------------------------------------------------------------------------- #
# Hostname claims
# --------------------------------------------------------------------------- #


@router.get("/accounts/{account_id}/domains", summary="Every host this brand holds")
def list_domains(account_id: str, service: BrandingService = ServiceDep) -> dict[str, Any]:
    """The brand's hosts, with the state that decides whether they serve."""
    _account_or_404(account_id, service)
    return {
        "account_id": account_id,
        "state_expectations": dict(CLAIM_EXPECTATIONS),
        "hosts": [service.describe_claim(claim) for claim in service.claims_for(account_id)],
    }


@router.post("/accounts/{account_id}/domains/verify", summary="Check a host, claiming nothing")
def verify_domain(
    account_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    service: BrandingService = ServiceDep,
) -> dict[str, Any]:
    """Check a candidate host and report every check with what it saw.

    A separate route from claiming, because the researched flow is verify-then-
    save and the operator has to be able to see what is wrong first. Writes
    nothing, so it writes no audit row either.
    """
    host = payload.get("host")
    if host is None:
        raise BrandedDomainError("The host could not be read.", {"host": "Name the host to check."})
    return service.verify(account_id, host)


@router.post("/accounts/{account_id}/domains", summary="Attach a host to a brand")
def claim_domain(
    account_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    force: bool = Query(default=False, description="Save even if the CNAME has not propagated yet"),
    actor: str | None = Query(default=None),
    service: BrandingService = ServiceDep,
) -> dict[str, Any]:
    """Attach a verified host to a brand.

    ``force`` is the researched escape hatch for the propagation window. It
    cannot force past a host another brand holds, verified.
    """
    _account_or_404(account_id, service)
    host = payload.get("host")
    if host is None:
        raise BrandedDomainError(
            "The host could not be read.", {"host": "Name the host to attach."}
        )
    return service.claim(
        account_id,
        host,
        force=force,
        actor=actor,
        source=_source("POST", "/accounts/{account_id}/domains"),
    )


@router.delete("/accounts/{account_id}/domains", summary="Give a host back")
def release_domain(
    account_id: str,
    host: str = Query(description="The host to release"),
    actor: str | None = Query(default=None),
    service: BrandingService = ServiceDep,
) -> dict[str, Any]:
    """Release a host so another brand can claim it."""
    _account_or_404(account_id, service)
    return service.release(
        account_id, host, actor=actor, source=_source("DELETE", "/accounts/{account_id}/domains")
    )


@router.post("/accounts/{account_id}/domains/recheck", summary="Re-run DNS verification")
def recheck_domain(
    account_id: str,
    actor: str | None = Query(default=None),
    service: BrandingService = ServiceDep,
) -> dict[str, Any]:
    """Re-check every host the brand holds, without retyping one."""
    _account_or_404(account_id, service)
    return service.recheck(
        account_id, actor=actor, source=_source("POST", "/accounts/{account_id}/domains/recheck")
    )


# --------------------------------------------------------------------------- #
# Rooms
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/brand", summary="Put a room on a brand")
def attach_room(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    service: BrandingService = ServiceDep,
) -> dict[str, Any]:
    """Attach a room to a brand and inherit the brand's current tokens.

    An optional ``tokens`` object is the room's own refine: a room override beats
    the brand value for the tokens it names and inherits the rest.
    """
    account_id = str(payload.get("account_id") or "").strip()
    if not account_id:
        raise BrandedDomainError(
            "The attachment could not be read.", {"account_id": "Name the brand this room joins."}
        )
    _account_or_404(account_id, service)
    try:
        return service.attach_room(
            account_id,
            room_id,
            override=payload.get("tokens"),
            actor=actor,
            source=_source("POST", "/rooms/{room_id}/brand"),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found") from None


@router.get("/rooms/{room_id}/theme", summary="The theme a room renders with")
def room_theme(room_id: str, service: BrandingService = ServiceDep) -> dict[str, Any]:
    """A room's tokens and the brand behind them."""
    return service.theme_for_room(room_id)


@router.get("/accounts/{account_id}/rooms", summary="Every room on this brand")
def list_rooms(account_id: str, service: BrandingService = ServiceDep) -> dict[str, Any]:
    """Every room that has joined this brand, with the tokens it carries.

    What the researched "auto-propagate a brand change to every tenant" is
    observable through: a room appears here the moment it joins, and its tokens
    and brand revision move with the brand.
    """
    _account_or_404(account_id, service)
    return {
        "account_id": account_id,
        "rooms": [service.describe_theme(record) for record in service.themes_for(account_id)],
    }


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


@router.get("/resolve", summary="Resolve a Host header to its brand")
def resolve(
    host: str = Query(default="", description="The Host header the request arrived on"),
    service: BrandingService = ServiceDep,
) -> dict[str, Any]:
    """Which brand serves ``host``, and the theme it renders with.

    This is the first step of the researched data flow: the room tenant is
    resolved by host. A host held by two verified claims is refused rather than
    resolved, and a host no brand holds is reported as unclaimed.
    """
    if not host.strip():
        raise BrandedDomainError(
            "The host could not be read.", {"host": "Name the host to resolve."}
        )
    return service.resolve(host)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #
#
# A feature whose page is empty in the demo is a feature nobody can review, so
# this seeds the states the research describes:
#
#   * a brand live on a verified host, with tenant tokens and a room attached;
#   * a brand with white-label mode on, so the client's identity is presented and
#     the agency's is withheld;
#   * a brand mid-propagation, with a host saved and unverified, so the "awaiting
#     DNS" state and the re-check control are visible without clicking;
#   * a host whose last check failed, so the failed state is visible too;
#   * a room carrying its own override, so a room refine beside a brand value is
#     visible rather than only the inherited case.
#
# The brands, the tokens and the attachments are written through the real
# service, so the demo cannot show a token map or a propagation the HTTP routes
# would not produce. The hostname claims are written directly, because a seed has
# no DNS to verify against and each patch is exactly the shape `claim` and
# `recheck` leave behind.
#
# The audit `source` is "seed", the value every other feature's `seed()` uses,
# and deliberately not a route path: no route served this write, so it names no
# route.

#: Reserved demo hosts. ``.example`` is RFC 2606 reserved, so a seeded host can
#: never resolve and can never be a real customer's domain.
SEED_HOSTS = {
    "live": "deals.northwind.example",
    "white_label": "rooms.fabrikam.example",
    "awaiting": "renewals.adventure.example",
    "failed": "pilot.contoso.example",
}

#: The four states the seed creates, in the order they are applied.
SEED_STATES = ("live", "white_label", "awaiting", "failed")


def _seed_claim(
    db: Any, state: str, brand_id: str, host: str, now: str, source: str, actor: str
) -> None:
    """Write one hostname claim in the state the state's name says.

    One write, not two: the state is chosen here rather than created and then
    corrected, so the audit trail of the demo is the trail the product leaves.
    """
    if state in ("live", "white_label"):
        claim_state, observed = CLAIM_VERIFIED, [cname_target()]
    elif state == "failed":
        claim_state, observed = "failed", ["elsewhere.example"]
    else:
        claim_state, observed = CLAIM_UNVERIFIED, []

    db.create(
        CLAIM_COLLECTION,
        {
            "host": host,
            "brand_id": brand_id,
            "state": claim_state,
            "cname_target": cname_target(),
            "observed": observed,
            "last_checked_at": now,
            "verified_at": now if claim_state == CLAIM_VERIFIED else None,
            "released_at": None,
        },
        actor=actor,
        source=source,
    )


def seed(db, context: dict[str, Any]) -> str:
    """Put the researched branded-domain states into the demo accounts.

    Every character of the returned string is ASCII, so the seeder can print it
    on a Windows console. A single character outside cp1252 in one feature's
    return value once broke the whole seeder.
    """
    store = RecordStore(db)
    now = context["now"].isoformat()
    room_ids = list(context.get("room_ids") or [])
    if not room_ids:
        return "no rooms to attach to"

    service = BrandingService(store, resolver=_resolver())
    source, actor = "seed", "dana"
    rows = [(room_id, account) for room_id, account in room_ids]

    spec = [
        (
            "live",
            "Northwind Studio",
            False,
            None,
            {"accent": "accent-bright", "destructive": "warning"},
        ),
        ("white_label", "Fabrikam Advisory", True, "Fabrikam Logistics", {"accent": "info"}),
        ("awaiting", "Adventure Works", False, None, {}),
        ("failed", "Contoso Health", False, None, {"success": "accent-bright"}),
    ]

    for index, (state, name, white_label, client_name, tokens) in enumerate(spec):
        room_id, account = rows[index % len(rows)]
        brand = service.create_brand(
            {
                "name": name,
                "account_ref": account,
                "owner_name": "Fabrikam Advisory" if white_label else name,
                "white_label": white_label,
                "client_name": client_name,
                "tokens": tokens,
            },
            actor=actor,
            source=source,
        )
        # Tokens are set before the room joins, so the room inherits the current
        # map rather than the defaults. That is the researched "auto-apply brand
        # tokens to new rooms", and the seed proves it rather than describing it.
        if tokens:
            service.set_tokens(str(brand["account_id"]), tokens, actor=actor, source=source)
        service.attach_room(
            str(brand["account_id"]),
            room_id,
            # One room refines the brand's accent, so a room override beside an
            # inherited map is visible in the demo.
            override={"accent": "accent-soft"} if index == 0 else None,
            actor=actor,
            source=source,
        )
        _seed_claim(db, state, str(brand["account_id"]), SEED_HOSTS[state], now, source, actor)

    counts = service.summary()
    return (
        f"{counts['brands']} brands ({counts['white_labelled']} white-labelled), "
        f"{counts['verified']} host(s) live, {counts['unverified']} awaiting DNS, "
        f"{counts['failed']} check failed, {counts['rooms_themed']} rooms themed "
        f"({counts['rooms_inheriting']} inheriting the brand)"
    )
