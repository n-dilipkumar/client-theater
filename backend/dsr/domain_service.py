"""Storage-facing orchestration for custom domains and room branding.

This is the only module in the feature that writes. It goes through
:class:`dsr.store.RecordStore`, which is a thin façade over
:class:`dsr.db.audited.AuditedDatabase`, so every mutation here lands in the
audit log inside the same transaction as the change -- which is the guarantee
the product is built on.

Schema flexibility
------------------
No new column and no migration. The domain, its verification state, the link
secret, the collaborator token, and the brand tokens are all fields inside
``records.data``:

===========================  =============================================
Field                        Meaning
===========================  =============================================
``domain``                   the custom domain, or absent
``domain_status``            ``verified`` | ``unverified`` | ``failed``
``domain_cname_target``      target the CNAME was checked against
``domain_observed``          what DNS actually returned, for debugging
``domain_last_checked_at``   ISO timestamp of the last verification
``link_secret``              non-removable share-link secret
``collaborator_token``       internal-only token, bypasses the custom domain
``branding``                 open object: colours, fonts, logo, anything
===========================  =============================================

Because they are ordinary JSON fields they are reachable through the existing
dynamic index, so a caller can ask "which rooms are on this domain?" with
``store.find("room", {"domain": "proposals.acme.com"})`` and a team can add
``branding.email_footer`` without coordinating with anyone.

Security note
-------------
The research documents *that* the vendor verifies a domain is available to use,
but never *how*. This module checks two things:

1. **DNS**: the hostname resolves to the deployment's canonical CNAME target.
   That is necessary but not sufficient -- it shows the CNAME exists, not that
   the account controls it. A production deployment should additionally require
   a DNS TXT challenge proving control. That is called out as future work
   rather than silently claimed as secure.
2. **Availability**: no *other* live room already claims the domain. The
   research names this exact hazard ("changing the custom domain again ... the
   links will appear broken"), and the multi-tenant case where two customers
   fight over one domain is the more serious half of it.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from dsr.db.audited import AuditError, utcnow
from dsr.domains import (
    CnameResolver,
    DomainError,
    build_collaborator_url,
    build_share_url,
    cname_target,
    default_base_url,
    generate_collaborator_token,
    generate_link_secret,
    is_valid_colour,
    is_valid_font_family,
    link_path,
    normalise_domain,
    recognised_host,
    resolver_from_env,
    room_slug,
)
from dsr.store import RecordStore

__all__ = ["DomainConflict", "DomainService"]

# The reserved fields the API owns. A team can put anything else it likes in a
# room's data; these three are the product's own vocabulary.
_RESERVED_ROOM_FIELDS = frozenset({"domain", "link_secret", "collaborator_token"})

# Brand tokens the API validates. Anything else under `branding` is passed
# through untouched, so a team can add its own without coordination.
_VALIDATED_BRANDING = {
    "primary": is_valid_colour,
    "secondary": is_valid_colour,
    "accent": is_valid_colour,
    "background": is_valid_colour,
    "foreground": is_valid_colour,
    "heading_font": is_valid_font_family,
    "body_font": is_valid_font_family,
}


class DomainConflict(AuditError):
    """Raised when a domain cannot be claimed because it is already in use."""


class DomainService:
    """Custom domains, link secrets, and brand tokens for rooms."""

    def __init__(self, store: RecordStore, resolver: CnameResolver | None = None) -> None:
        self.store = store
        self.resolver = resolver or resolver_from_env()

    # -- reads -------------------------------------------------------------- #

    def get_room(self, room_id: str) -> dict[str, Any]:
        room = self.store.get(room_id)
        if room is None or room.get("collection") != "room":
            raise KeyError(room_id)
        return room

    def claimed_domains(self, *, exclude_room: str | None = None) -> list[str]:
        """Every domain currently attached to a live room.

        Read from the live room records rather than kept in a side index, so it
        cannot drift out of step with them. A deleted or unverified room
        contributes nothing, which is what lets a domain be released and reused.
        """
        domains: list[str] = []
        for room in self.store.list("room", limit=1000):
            if exclude_room is not None and room["id"] == exclude_room:
                continue
            data = room.get("data") or {}
            domain = data.get("domain")
            if domain and data.get("domain_status") == "verified":
                domains.append(domain)
        return domains

    def domain_holder(self, domain: str, *, exclude_room: str | None = None) -> dict[str, Any] | None:
        """The live room already holding ``domain``, if any.

        Uses ``find`` on the dynamic index rather than a scan, which is the
        schema-flexible query path: ``domain`` is not a declared column anywhere.

        ``exclude_room`` is ignored when deciding whether a room holds its own
        domain, so re-saving a domain a room already has is not a conflict.
        """
        matches = self.store.find("room", {"domain": domain}, limit=5)
        for room in matches:
            if exclude_room is not None and room["id"] == exclude_room:
                continue
            if (room.get("data") or {}).get("domain_status") == "verified":
                return room
        return None

    def describe(self, room: dict[str, Any]) -> dict[str, Any]:
        """Everything the UI needs about a room's public identity."""
        data = room.get("data") or {}
        secret = data.get("link_secret")
        base = default_base_url()

        result: dict[str, Any] = {
            "room_id": room["id"],
            "name": data.get("name"),
            "revision": room.get("revision"),
            "base_url": base,
            "cname_target": cname_target(),
            "domain": data.get("domain"),
            "domain_status": data.get("domain_status", "unverified"),
            "domain_cname_target": data.get("domain_cname_target"),
            "domain_observed": data.get("domain_observed", []),
            "domain_last_checked_at": data.get("domain_last_checked_at"),
            "domain_activated_at": data.get("domain_activated_at"),
            "domain_history": data.get("domain_history") or [],
            "has_link_secret": bool(secret),
            "has_collaborator_token": bool(data.get("collaborator_token")),
            "branding": data.get("branding") or {},
        }

        if secret:
            result["slug"] = room_slug(data.get("name"), secret)
            result["share_url"] = build_share_url(room, base_url=base)
            result["default_host_share_url"] = build_share_url(
                room, base_url=base, use_custom_domain=False
            )
            result["path"] = link_path(data.get("name"), secret)

        if data.get("collaborator_token"):
            result["collaborator_url"] = build_collaborator_url(room, base_url=base)

        return result

    # -- link secrets -------------------------------------------------------- #

    def ensure_link_secret(self, room_id: str, *, actor: str | None = None) -> dict[str, Any]:
        """Mint the room's share-link secret if it does not have one.

        Sourced rule: the secret is a "unique, non-removable identifier ... for
        security purposes". So it is minted once and there is deliberately no
        revoke or clear path -- a caller that sends ``link_secret: null`` in a
        patch has it stripped rather than honoured. Rotating it would invalidate
        every link the customer has already shared, which is the same breakage
        the research warns about for domains.
        """
        room = self.get_room(room_id)
        data = room.get("data") or {}
        if data.get("link_secret"):
            return room

        patch = {"link_secret": generate_link_secret()}
        if not data.get("collaborator_token"):
            patch["collaborator_token"] = generate_collaborator_token()

        return self.store.update(
            room_id,
            patch,
            actor=actor,
            source=f"mint link secret for {room_id}",
        )

    # -- verification -------------------------------------------------------- #

    def verify(
        self,
        domain: str,
        *,
        for_room: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Check a candidate domain without claiming it.

        Deliberately side-effect free: nothing is written, and no audit row is
        written either, because a check is a read. The operator needs to see
        "your CNAME points somewhere else, here is what we saw" before
        committing to anything.

        ``for_room`` names the room the check is being run for. The domain must
        read as available *to that room*, so a room verifying a domain it
        already holds is not told it is taken by itself. Omitting it -- as the
        public ``/api/white-label/verify`` endpoint does -- reports the strict
        "claimed by someone" answer, which is the more useful one for an
        operator deciding whether a domain is up for grabs.
        """
        normalised = normalise_domain(domain)
        target = cname_target()
        matched, observed = self.resolver.resolves_to(normalised, target)

        holder = self.domain_holder(normalised, exclude_room=for_room)
        available = holder is None

        checks = [
            {
                "name": "cname",
                "label": f"CNAME points at {target}",
                "ok": matched,
                "detail": (
                    f"{normalised} resolves to {' , '.join(observed)}"
                    if observed
                    else f"{normalised} does not resolve yet"
                ),
            },
            {
                "name": "format",
                "label": "Subdomain format",
                "ok": True,
                "detail": "The domain is in subdomain format and is well formed.",
            },
            {
                "name": "available",
                "label": "Not already in use",
                "ok": available,
                "detail": (
                    "No other room is using this domain."
                    if available
                    else f"Already used by room {holder['id']}."
                ),
            },
        ]

        ready = all(check["ok"] for check in checks)
        return {
            "domain": normalised,
            "cname_target": target,
            "ready": ready,
            "status": "verified" if ready else "failed",
            "observed": observed,
            "checked_at": utcnow(),
            "checks": checks,
            "cloudflare_note": (
                "If this domain is proxied by Cloudflare, set the CNAME to DNS only "
                "(proxy off). A proxied record will not verify and slows page loads."
            ),
        }

    # -- claim / release ----------------------------------------------------- #

    def claim(
        self,
        room_id: str,
        domain: str,
        *,
        force: bool = False,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Attach a custom domain to a room.

        Ordering matters and follows the researched flow: the operator enters the
        domain, the service verifies it, and only then is it saved. So
        :meth:`verify` runs first and a failing check refuses the write, unless
        ``force`` is set -- the escape hatch for the case the research names,
        where the operator is mid-propagation and wants the binding in place
        before the CNAME has finished spreading.
        """
        room = self.get_room(room_id)
        normalised = normalise_domain(domain)
        report = self.verify(normalised, for_room=room_id, actor=actor)

        if not report["ready"]:
            failing = [check for check in report["checks"] if not check["ok"]]
            if not force:
                raise DomainConflict(
                    f"{normalised} is not ready: "
                    + "; ".join(check["label"] for check in failing)
                    + ". Re-run verification once the CNAME has propagated, or save anyway."
                )
            # `force` past an availability clash would hand one customer's domain
            # to another, so that one check is never overridable.
            clash = next((c for c in failing if c["name"] == "available"), None)
            if clash is not None:
                raise DomainConflict(
                    f"{normalised} is already in use by another room: {clash['detail']}"
                )

        holder = self.domain_holder(normalised)
        if holder is not None and holder["id"] != room_id:
            raise DomainConflict(f"{normalised} is already in use by room {holder['id']}")

        data = room.get("data") or {}
        previous = data.get("domain")

        patch: dict[str, Any] = {
            "domain": normalised,
            "domain_status": "verified" if report["ready"] else "unverified",
            "domain_cname_target": report["cname_target"],
            "domain_observed": report["observed"],
            "domain_last_checked_at": report["checked_at"],
        }
        if previous and previous != normalised:
            # Recorded for transparency, not for resolution. Because the link
            # secret is the room's identity, retiring a domain does not break
            # links; the history is here so an operator can see what moved.
            history = list(data.get("domain_history") or [])
            history.append({"domain": previous, "retired_at": utcnow(), "room_id": room_id})
            patch["domain_history"] = history[-20:]

        if not data.get("link_secret"):
            patch["link_secret"] = generate_link_secret()
        if not data.get("collaborator_token"):
            patch["collaborator_token"] = generate_collaborator_token()

        return self.store.update(
            room_id,
            patch,
            actor=actor,
            source=f"claim custom domain {normalised} for {room_id}",
        )

    def release(self, room_id: str, *, actor: str | None = None) -> dict[str, Any]:
        """Detach the custom domain, returning the room to its default host.

        Links do not break: the secret is the identity, so a link already shared
        resolves on the default host. That is the whole point of the
        ``secret_is_identity`` decision, and it is the hazard the research
        attributes to the vendor that we do not inherit.
        """
        room = self.get_room(room_id)
        data = room.get("data") or {}
        if not data.get("domain"):
            raise DomainError(f"room {room_id} has no custom domain to release")

        previous = data.get("domain")
        history = list(data.get("domain_history") or [])
        history.append({"domain": previous, "retired_at": utcnow(), "room_id": room_id})

        return self.store.update(
            room_id,
            {
                "domain": None,
                "domain_status": "unverified",
                "domain_history": history[-20:],
            },
            actor=actor,
            source=f"release custom domain {previous} from {room_id}",
        )

    def recheck(self, room_id: str, *, actor: str | None = None) -> dict[str, Any]:
        """Re-run verification for the domain a room already holds.

        This is how a customer recovers from a failed check without re-entering
        the domain, which matters because propagation "may take up to 24 hours"
        and the operator should be able to poll rather than retype.
        """
        room = self.get_room(room_id)
        data = room.get("data") or {}
        if not data.get("domain"):
            raise DomainError(f"room {room_id} has no custom domain to check")

        report = self.verify(data["domain"], for_room=room_id, actor=actor)

        patch: dict[str, Any] = {
            "domain_status": report["status"],
            "domain_observed": report["observed"],
            "domain_last_checked_at": report["checked_at"],
        }
        # A forced save left the domain recorded but unverified. Once the CNAME
        # propagates the room should be able to come up on it without the
        # operator retyping anything, so a clean recheck clears the flag.
        if report["ready"] and data.get("domain_status") != "verified":
            patch["domain_activated_at"] = report["checked_at"]

        return self.store.update(
            room_id,
            patch,
            actor=actor,
            source=f"recheck custom domain for {room_id}",
        )

    # -- branding ------------------------------------------------------------ #

    def update_branding(
        self,
        room_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Merge brand tokens into a room's open ``branding`` object.

        Merged one level deep rather than replaced, because ``branding`` is a
        team-owned object: writing ``{"accent": ...}`` must not delete a logo URL
        another team stored. Unknown keys pass through unvalidated, which is the
        schema-flexibility promise -- this method validates only the tokens it
        itself will render.
        """
        room = self.get_room(room_id)
        data = room.get("data") or {}
        current = dict(data.get("branding") or {})

        incoming = dict(patch or {})
        for key, rule in _VALIDATED_BRANDING.items():
            if key in incoming and incoming[key] is not None and not rule(incoming[key]):
                raise DomainError(f"branding.{key} is not a valid {key.replace('_', ' ')} token")

        merged = {**current, **incoming}
        return self.store.update(
            room_id,
            {"branding": merged},
            actor=actor,
            source=f"update branding for {room_id}",
        )

    # -- resolution ---------------------------------------------------------- #

    def _host_is_served(self, host: str, room_data: Mapping[str, Any]) -> bool:
        """True when ``host`` is routed to this deployment.

        Checked in increasing order of cost, because this runs on every
        share-link request:

        1. the deployment's own host, which is a pure string compare;
        2. the domain this room already holds, also a string compare, and the
           overwhelmingly common case for a white-labelled link;
        3. any other claimed domain, which costs one lookup on the dynamic
           index rather than a scan of every room.

        Falling back to a full ``claimed_domains()`` scan here would mean
        hydrating every room on every buyer page view.
        """
        if recognised_host(host, base_url=default_base_url()):
            return True
        if _host_matches(host, room_data.get("domain") or ""):
            return True
        try:
            candidate = normalise_domain(host.strip().lower().rsplit(":", 1)[0] if host.count(":") == 1 else host)
        except DomainError:
            return False
        return self.domain_holder(candidate) is not None

    def resolve(self, secret: str, *, host: str | None = None) -> dict[str, Any]:
        """Resolve a share-link secret to a room.

        The read side of ``secret_is_identity``. The secret is the identity, so
        the room is found by secret alone; the host is then checked only to
        confirm the request arrived on a host this deployment actually serves.
        A link shared on the default host therefore keeps working after a custom
        domain takes effect, and a link shared on a custom domain keeps working
        after that domain is released.
        """
        if not secret:
            raise DomainError("a link secret is required")

        matches = self.store.find("room", {"link_secret": secret}, limit=2)
        if not matches:
            raise KeyError(secret)

        room = matches[0]
        data = room.get("data") or {}

        if host is not None and not self._host_is_served(host, data):
            # The secret is valid but this host is not ours. Reported separately
            # from "not found" because the two mean different things to whoever
            # is debugging: one is a bad link, the other is bad routing.
            raise HostNotServed(host)

        return {
            "room": room,
            "served_on_custom_domain": bool(
                data.get("domain") and data.get("domain_status") == "verified" and host
                and _host_matches(host, data["domain"])
            ),
        }

    # -- guard rails used by the API layer ----------------------------------- #

    @staticmethod
    def strip_reserved(patch: Mapping[str, Any]) -> dict[str, Any]:
        """Drop the fields the product owns from a caller-supplied patch.

        Without this, any generic record update could overwrite ``link_secret``
        with null, which is exactly the removal the research says is not
        possible. Reserved fields are changed only through the dedicated
        endpoints on this service.
        """
        return {key: value for key, value in patch.items() if key not in _RESERVED_ROOM_FIELDS}


class HostNotServed(LookupError):
    """Raised when a valid secret is requested on a host this app does not serve."""

    def __init__(self, host: str) -> None:
        super().__init__(host)
        self.host = host


def _host_matches(host: str, domain: str) -> bool:
    """True when ``host`` is exactly the domain a room has claimed."""
    candidate = (host or "").strip().lower()
    # A single colon is a port. More than one would be IPv6, which is never a
    # claimed custom domain, so the raw comparison simply fails and that is the
    # correct answer.
    if candidate.count(":") == 1:
        candidate = candidate.rsplit(":", 1)[0]
    try:
        return candidate == normalise_domain(domain)
    except DomainError:
        return False
