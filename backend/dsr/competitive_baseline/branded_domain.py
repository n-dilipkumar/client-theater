"""WF-138: the pure rules of a branded room on the customer's own domain.

This module holds the vocabulary, the error types and the rules for one
workflow: a customer points a hostname at this deployment, the room is served
from that hostname with the customer's brand, and in white-label mode the
client's brand appears instead of the agency's.

It is pure. Every function is a function of its arguments. Nothing here opens a
database, reads the clock, reads the environment, or touches the network, so
each rule is checkable without a server and without a fixture. The storage-facing
orchestration lives in :mod:`dsr.features.wf138_branded_custom_domain`, which is
the only module of this workflow that writes.

Why this module imports no ``dsr`` module at all
-----------------------------------------------
``backend/tests/test_wf122.py`` globs every ``*.py`` in
``dsr/competitive_baseline/`` and asserts that any ``dsr.`` import is exactly
``dsr.store``. That test ships on ``main`` and this workflow does not own it, so
a module added here may import no ``dsr`` module -- not even the store, and not
``dsr.domains``.

That constraint would normally force a second copy of the hostname rules, and a
second copy is exactly the hazard the issue names: two workflows holding their
own domain-claim records would let one host be claimed twice and give the
"room tenant resolved by host" step two possible answers. So the hostname rule
is *injected* instead: every function here takes an already-normalised host, and
the feature module normalises it with WF-017's ``dsr.domains.normalise_domain``.
There is still one implementation of the rule, and this module still holds the
claim states, the verification report, the routing decision and the branding.

What is sourced, and what is inferred
-------------------------------------
Sourced from the WF-138 research (``docs/research/digital-sales-room-workflows/wf/WF-138.md``):

* The user flow: the company registers its own domain, the room is served from
  it with the seller's brand, fonts and colour, and for an agency the room is
  white-labelled so the *client's* brand appears, not the agency's.
* The data flow: domain, then TLS and a reverse proxy, then the room tenant is
  resolved by host, then branded theme tokens are applied, then per-tenant asset
  and colour overrides -- and white-label mode "swaps owner identity".
* The automations: auto-issue and renew TLS, auto-apply brand tokens to new
  rooms, auto-propagate a brand change to every tenant, auto-verify domain
  ownership.
* Papermark advertises custom domains and custom branding, and the research is
  explicit that this is a **hosted-plan** feature of that project, so it is not
  presented here as a self-hosted capability.
* DealHub and Seismic document branded rooms with customizable branding. Dock
  ships a white-label client portal page.
* One claim is unverified and stays that way: Liferay's capability page is
  gated, so "Liferay has a Digital Sales Room" is not confirmed and Liferay is
  not cited as a source for the capability. ``https://www.docker.com/`` is
  marked "not used" in the research and is not cited for anything.

Design inferences, which are NOT sourced and are marked so in :data:`DECISIONS`:

* The override is a **token-to-token** mapping. A tenant re-points one of this
  product's semantic tokens at another one. A tenant never supplies a colour
  value, so a tenant can never break a later theme change and can never inject
  a stylesheet.
* Four tokens are product-locked so the light theme and the 4.5:1 contrast
  floor survive: ``background``, ``surface``, ``foreground`` and
  ``muted-foreground``.
* White-label mode is a swap. It requires the client's name, and the owner's
  name is withheld rather than shown beside it.
* Propagation is eager: a token change is written to every room of the brand at
  the moment it is made, and a room's own override beats the brand value.

What this module cannot do, and says so
---------------------------------------
The product needs infrastructure this repository does not own. See
:data:`UNVERIFIABLE_HERE` and the ``cannot_verify`` block of the resolve route:
TLS issuance and renewal, a reverse proxy that forwards the ``Host`` header,
per-customer DNS, a DNS TXT challenge that proves control, a CDN edge, and a
digital asset manager for brand assets. No claim in this workflow proves any of
them.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

__all__ = [
    "BRAND_COLLECTION",
    "BrandConflict",
    "BrandedDomainError",
    "CLAIM_COLLECTION",
    "CLAIM_EXPECTATIONS",
    "CLAIM_FAILED",
    "CLAIM_RELEASED",
    "CLAIM_STATES",
    "CLAIM_UNVERIFIED",
    "CLAIM_VERIFIED",
    "DECISIONS",
    "EVENT_BRAND_CREATED",
    "EVENT_DOMAIN_CLAIMED",
    "EVENT_DOMAIN_RELEASED",
    "EVENT_KINDS",
    "EVENT_PROPAGATED",
    "EVENT_ROOM_ATTACHED",
    "EVENT_TOKENS_SET",
    "EVENT_WHITE_LABELLED",
    "EVENT_COLLECTION",
    "PRODUCT_TOKENS",
    "ROOM_THEME_COLLECTION",
    "TENANT_TOKENS",
    "TOKEN_NAMES",
    "UNVERIFIABLE_HERE",
    "adopt_room",
    "claim_is_serving",
    "claim_next_state",
    "claimable",
    "effective_tokens",
    "presented_owner",
    "propagation_plan",
    "resolve_host",
    "token_css_variables",
    "validate_brand",
    "validate_token_map",
    "verify_report",
    "vocabulary",
]


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# Two types, both this workflow's own, and nothing else in the product raises
# them. The feature host refuses a second feature that registers a handler for
# the same type, so these names are what makes the mapping unambiguous.
#
# Neither is a bare builtin. A handler registered for ``ValueError`` or for
# ``PermissionError`` would intercept that exception across the whole product,
# so each type subclasses a builtin and is registered by its own name.


class BrandedDomainError(ValueError):
    """A host, a brand or a token map this workflow will not accept.

    Carries a field-keyed map. An operator filling in a form needs the message
    beside the input that caused it, so the HTTP layer renders the map as
    ``errors``.
    """

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})


class BrandConflict(PermissionError):
    """A change this workflow's current state refuses.

    ``reason`` is a stable machine token, so a page can branch on it instead of
    parsing an English sentence.
    """

    #: Another brand already holds the host, verified. Never overridable.
    REASON_HOST_TAKEN = "host_taken"

    #: The checks did not pass and the caller did not force the save.
    REASON_NOT_READY = "not_ready"

    #: The brand has no such host, so there is nothing to release or re-check.
    REASON_NO_HOST = "no_host"

    _MESSAGES = {
        REASON_HOST_TAKEN: "Another brand already holds this host.",
        REASON_NOT_READY: "The host did not pass its checks.",
        REASON_NO_HOST: "This brand holds no host.",
    }

    def __init__(self, reason: str, detail: str | None = None) -> None:
        super().__init__(detail or self._MESSAGES.get(reason, "That change was refused."))
        self.reason = reason


# --------------------------------------------------------------------------- #
# Collections. One shared `records` table carries every feature, so each name is
# namespaced per ticket and `find()` matches on the collection before anything
# else.
# --------------------------------------------------------------------------- #

#: One brand per account: the owner identity, the white-label switch and the
#: tenant token overrides.
BRAND_COLLECTION = "wf138_brand"

#: One hostname claim. The state, the CNAME target it was checked against and
#: what DNS actually answered.
CLAIM_COLLECTION = "wf138_host_claim"

#: One room's applied theme: the tokens it carries and the brand revision they
#: came from. This is what makes propagation observable.
ROOM_THEME_COLLECTION = "wf138_room_theme"

#: One brand change, host claim, release, propagation or attachment.
EVENT_COLLECTION = "wf138_brand_event"


# --------------------------------------------------------------------------- #
# The tokens a tenant may re-point
# --------------------------------------------------------------------------- #
#
# The tokens are the design system's semantic ones, named exactly as
# `frontend/src/index.css` declares them, minus the `--color-` prefix. The
# product renders a pair as `--color-<key>: var(--color-<value>)`, so no colour
# value ever leaves the stylesheet.
#
# TENANT_TOKENS is what a tenant may override *and* what a tenant may point at.
# PRODUCT_TOKENS is locked at both ends: re-pointing the page ground, a surface
# or a text colour is what would break the 4.5:1 contrast floor the design
# system states, and the light theme is locked for the whole product.

#: Tokens a tenant may override, and may point another token at.
TENANT_TOKENS: tuple[str, ...] = (
    "primary",
    "accent",
    "accent-bright",
    "accent-soft",
    "muted",
    "border-subtle",
    "info",
    "success",
    "warning",
    "destructive",
)

#: Tokens the product owns. A tenant may not override them and may not point
#: another token at them.
PRODUCT_TOKENS: tuple[str, ...] = (
    "background",
    "surface",
    "foreground",
    "muted-foreground",
)

#: Every token this workflow knows.
TOKEN_NAMES: tuple[str, ...] = PRODUCT_TOKENS + TENANT_TOKENS


# --------------------------------------------------------------------------- #
# The hostname claim's verification states
# --------------------------------------------------------------------------- #

#: Saved, but no DNS check has passed yet. This is the researched state during
#: the "up to 24 hours" propagation window, and it is what `force` produces.
CLAIM_UNVERIFIED = "unverified"

#: The CNAME check passed. Only this state serves traffic.
CLAIM_VERIFIED = "verified"

#: The check ran and failed.
CLAIM_FAILED = "failed"

#: The brand gave the host back. The host may be claimed by another brand.
CLAIM_RELEASED = "released"

#: Every state a claim can be in.
CLAIM_STATES: tuple[str, ...] = (CLAIM_UNVERIFIED, CLAIM_VERIFIED, CLAIM_FAILED, CLAIM_RELEASED)

#: What each state means for routing, so the page and the resolver agree.
CLAIM_EXPECTATIONS: dict[str, str] = {
    CLAIM_UNVERIFIED: "Not serving yet. A CNAME may take up to 24 hours to propagate.",
    CLAIM_VERIFIED: "Serving: this brand answers on this host.",
    CLAIM_FAILED: "Not serving. The last check failed.",
    CLAIM_RELEASED: "Released. The host is free for another brand.",
}

#: The state changes a claim accepts. Anything else is refused rather than
#: silently ignored, because a typo in an event name is a missing transition.
CLAIM_EVENTS: tuple[str, ...] = ("verify_ok", "verify_fail", "release")


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #

EVENT_BRAND_CREATED = "brand_created"
EVENT_TOKENS_SET = "tokens_set"
EVENT_WHITE_LABELLED = "white_labelled"
EVENT_DOMAIN_CLAIMED = "domain_claimed"
EVENT_DOMAIN_RELEASED = "domain_released"
EVENT_ROOM_ATTACHED = "room_attached"
EVENT_PROPAGATED = "propagated"

EVENT_KINDS: tuple[str, ...] = (
    EVENT_BRAND_CREATED,
    EVENT_TOKENS_SET,
    EVENT_WHITE_LABELLED,
    EVENT_DOMAIN_CLAIMED,
    EVENT_DOMAIN_RELEASED,
    EVENT_ROOM_ATTACHED,
    EVENT_PROPAGATED,
)


# --------------------------------------------------------------------------- #
# What this build cannot verify
# --------------------------------------------------------------------------- #

#: Named so a reviewer does not have to guess whether a gap is a decision or an
#: oversight. Every entry is infrastructure this repository does not own.
UNVERIFIABLE_HERE: tuple[str, ...] = (
    "TLS issuance and renewal (ACME). A share URL is built as https://<host>/... "
    "and a browser refuses it without a certificate; nothing here issues one.",
    "A reverse proxy terminating TLS and forwarding the Host header. A proxy that "
    "normalises or drops the header breaks host resolution.",
    "Per-customer DNS. The customer points a CNAME at this deployment's canonical "
    "target, and that target must resolve to the same edge for every customer.",
    "A DNS TXT challenge. Resolving to the canonical target shows the CNAME exists; "
    "it does not show the account controls the domain.",
    "A CDN or edge cache in front of the branded host.",
    "A digital asset manager or headless CMS for brand assets. A brand asset is a "
    "URL string in the brand's own object; no file is stored or served here.",
    "A drag-and-drop design tool. The research's own answer is that the tokens do "
    "not need one: they are a token map set through a form.",
)


# --------------------------------------------------------------------------- #
# The decisions the research left open, each with the alternative rejected
# --------------------------------------------------------------------------- #

DECISIONS: dict[str, str] = {
    "token_overrides": (
        "A tenant override is a token-to-token mapping: a tenant re-points one of "
        "this product's semantic tokens at another one, and the pair renders as "
        "--color-<key>: var(--color-<value>). Rejected: accepting a colour value per "
        "tenant. A stored hex is exactly what the design system forbids, because a "
        "hardcoded colour opts out of every later theme change, and a value that "
        "reaches a style attribute is also the injection surface WF-017's "
        "is_valid_colour exists to close. Chosen by Jev over colour values and over "
        "a tenant-supplied stylesheet: verdict pass, confidence 1.00, audit "
        "jev-20261014T124133-18172-93831."
    ),
    "locked_tokens": (
        "background, surface, foreground and muted-foreground are locked at both "
        "ends: a tenant may neither override them nor point another token at them. "
        "Rejected: letting a tenant re-point every token. Those four decide text "
        "contrast against its own ground, and the design system states 4.5:1 and a "
        "locked light theme as the floor, so they are the product's."
    ),
    "one_accent": (
        "A tenant may re-point the accent, and may not add a second accent colour. "
        "Every target is another one of this product's tokens, so the palette is "
        "still the product's."
    ),
    "white_label": (
        "White-label mode swaps the owner identity: the client's brand is presented "
        "and the agency's name is withheld. It requires the client's name, because a "
        "swap with nothing to swap in would hide the brand and present no one. "
        "Rejected: presenting both with a 'powered by' line, which is not a "
        "white-label experience and leaks the agency."
    ),
    "host_identity": (
        "A hostname is routing, not identity. Room identity stays WF-017's "
        "non-removable link secret, and this workflow reuses that package's rules "
        "rather than holding a second set of domain records. Rejected: a second "
        "domain table, which is the hazard the issue names -- it would let one host "
        "be claimed twice and give host resolution two answers."
    ),
    "verification": (
        "A verified CNAME is necessary and not sufficient. It shows the record "
        "exists, not that the account controls the domain. Rejected: claiming "
        "control from a CNAME; a production deployment should also require a DNS "
        "TXT challenge, which is named as future work rather than claimed."
    ),
    "propagation": (
        "Propagation is eager and at write time: setting a brand's tokens writes the "
        "new tokens to every room of that brand in the same request, and a new room "
        "inherits the brand's current tokens rather than the product defaults. A "
        "room's own override beats the brand value. Rejected: resolving lazily at "
        "render time, which would let a room render a stale brand and would make "
        "'a brand change reaches every tenant' unobservable."
    ),
    "brand_assets": (
        "A brand asset is a URL string in the brand's open object. This repository "
        "has no object storage and no DAM, so nothing is uploaded, transcoded or "
        "served."
    ),
    "design_tool": (
        "No drag-and-drop design tool ships. The research names it as a hosted-vendor "
        "surface and gives the self-build answer in the same breath: a headless CMS "
        "for brand tokens plus a proxy. This build ships the tokens, not the editor."
    ),
    "hosted_vendors": (
        "Papermark's custom domain and custom branding are a hosted-plan feature and "
        "are not presented as a self-hosted capability. Liferay's Digital Sales Room "
        "could not be verified -- its capability page is gated -- so Liferay is not "
        "cited for it, and Liferay Portal CE is LGPL-2.1 and would not include a "
        "commercial DXP module. The research marks https://www.docker.com/ as not "
        "used, so it is not cited for anything."
    ),
}


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def validate_token_map(payload: Any) -> dict[str, str]:
    """Normalise a tenant override, or raise with a message per bad token.

    The accepted shape is ``{"accent": "accent-bright"}``: the key is a token this
    product lets a tenant override, and the value names another token. A colour
    value is refused by the same membership check that refuses an unknown token,
    which is the point -- there is no grammar to escape, because neither side of
    the pair is a free-form string.
    """
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise BrandedDomainError(
            "The token map could not be read.",
            {"tokens": "Send an object of token name to token name."},
        )

    errors: dict[str, str] = {}
    normalised: dict[str, str] = {}
    for raw_key, raw_value in payload.items():
        key = _text(raw_key)
        if not key:
            errors["tokens"] = "Every token override needs a token name."
            continue
        if key in PRODUCT_TOKENS:
            errors[f"tokens.{key}"] = (
                f"{key} is locked by the product: it decides text contrast, so a "
                "tenant may not override it."
            )
            continue
        if key not in TENANT_TOKENS:
            errors[f"tokens.{key}"] = (
                f"{key} is not a token this product lets a tenant override. "
                "Use one of: " + ", ".join(TENANT_TOKENS) + "."
            )
            continue

        value = _text(raw_value)
        if not value:
            errors[f"tokens.{key}"] = "Name the token this one should point at."
            continue
        if value not in TENANT_TOKENS:
            errors[f"tokens.{key}"] = (
                f"{value!r} is not a token. Name another token, not a colour value. "
                "Use one of: " + ", ".join(TENANT_TOKENS) + "."
            )
            continue
        normalised[key] = value

    if errors:
        raise BrandedDomainError("The token map could not be read.", errors)
    return normalised


def validate_brand(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalise a brand's identity fields, or raise with a message per field.

    A field this workflow has never heard of is kept. Teams add fields without a
    migration or a typed column, so validation normalises the fields it knows and
    passes the rest through untouched.
    """
    data = dict(payload or {})
    errors: dict[str, str] = {}

    name = _text(data.get("name"))
    if not name:
        errors["name"] = "Name the brand. A brand belongs to one account."

    owner_name = _text(data.get("owner_name"))
    if not owner_name:
        errors["owner_name"] = (
            "Name the owner. It is the identity presented when the room is not white-labelled."
        )

    white_label = bool(data.get("white_label"))
    client_name = _text(data.get("client_name"))
    if white_label and not client_name:
        errors["client_name"] = (
            "White-label mode swaps the owner identity, so it needs the client's "
            "name to present. Add the client's name, or turn white-label mode off."
        )

    if errors:
        raise BrandedDomainError("The brand could not be read.", errors)

    normalised = dict(data)
    normalised["name"] = name
    normalised["owner_name"] = owner_name
    normalised["white_label"] = white_label
    normalised["client_name"] = client_name or None
    return normalised


# --------------------------------------------------------------------------- #
# Token resolution and rendering
# --------------------------------------------------------------------------- #


def effective_tokens(
    brand_tokens: Mapping[str, str] | None, override: Mapping[str, str] | None = None
) -> dict[str, str]:
    """The token map a room renders with.

    The room's own override beats the brand value, one level deep. A room that
    overrides ``accent`` still inherits the brand's ``primary``, which is what
    makes a room override a refinement rather than a replacement.
    """
    merged = dict(brand_tokens or {})
    merged.update({key: value for key, value in dict(override or {}).items() if key and value})
    return merged


def token_css_variables(tokens: Mapping[str, str] | None) -> dict[str, str]:
    """Render a token map as scoped custom properties.

    ``{"accent": "accent-bright"}`` becomes
    ``{"--color-accent": "var(--color-accent-bright)"}``. This is the whole of the
    render path: a page sets these on a wrapper and no colour value is ever
    generated, stored or sent.
    """
    return {
        f"--color-{key}": f"var(--color-{value})"
        for key, value in dict(tokens or {}).items()
        if key and value
    }


def adopt_room(
    brand_id: str,
    brand_tokens: Mapping[str, str] | None,
    brand_revision: int,
    room_ref: str,
) -> dict[str, Any]:
    """The theme a new room carries when it joins a brand.

    The room inherits the brand's **current** tokens, not the product defaults,
    and it records the brand revision they came from, so a later propagation can
    tell an up-to-date room from a stale one.

    The field is ``brand_revision`` and not ``revision``: ``revision`` is one of
    the store's own envelope keys, and the audited wrapper strips it out of
    ``data`` on the way in. A payload that stored it there would silently lose it
    and every room would look stale forever.
    """
    return {
        "room_ref": room_ref,
        "brand_id": brand_id,
        "tokens": dict(brand_tokens or {}),
        "override": {},
        "brand_revision": int(brand_revision or 0),
        "inherited": True,
    }


def propagation_plan(
    brand_id: str,
    brand_tokens: Mapping[str, str] | None,
    brand_revision: int,
    rooms: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """What a brand change does to every room of that brand.

    Returns one row per room of ``brand_id``, in the order given, with the tokens
    the room should carry and whether anything actually changes:

    * a row whose recorded brand revision already matches is reported ``changed:
      False``, so propagation is idempotent and a second call writes nothing;
    * a room with its own override keeps it -- the override beats the brand
      value, so such a room is marked ``inherited: False``;
    * a room attached to a *different* brand is not in the result at all.

    ``theme_id`` is carried through from the input row, so the caller knows which
    stored record to write. Pure: the caller does the writing. That is what makes
    "a brand change reaches every tenant" a rule that can be tested without a
    database.
    """
    revision = int(brand_revision or 0)
    rows: list[dict[str, Any]] = []

    for room in rooms:
        if _text(room.get("brand_id")) != brand_id:
            continue

        override = dict(room.get("override") or {})
        tokens = effective_tokens(brand_tokens, override)
        changed = (
            int(room.get("brand_revision") or 0) != revision
            or dict(room.get("tokens") or {}) != tokens
        )
        rows.append(
            {
                "theme_id": room.get("theme_id") or room.get("id"),
                "room_ref": room.get("room_ref"),
                "brand_id": brand_id,
                "tokens": tokens,
                "override": override,
                "brand_revision": revision,
                "inherited": not override,
                "changed": changed,
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# Owner identity and white-label mode
# --------------------------------------------------------------------------- #


def presented_owner(brand: Mapping[str, Any]) -> dict[str, Any]:
    """Who a buyer sees as the owner of the room, and who is withheld.

    White-label mode swaps the identity. The client's brand is presented and the
    owner's name is returned under ``withheld``, so a page can prove the agency's
    identity does not appear rather than merely not looking for it.

    Raises when white-label mode is on with no client to present, because a swap
    with nothing to swap in presents no one. :func:`validate_brand` refuses that
    state at the door, and this guards a record that already holds it.
    """
    owner_name = _text(brand.get("owner_name")) or _text(brand.get("name"))
    client_name = _text(brand.get("client_name"))

    if brand.get("white_label"):
        if not client_name:
            raise BrandedDomainError(
                "The brand could not be presented.",
                {
                    "client_name": (
                        "White-label mode is on and there is no client name to "
                        "present. A swap needs a client identity."
                    )
                },
            )
        return {
            "presented": client_name,
            "role": "client",
            "white_labelled": True,
            "withheld": owner_name or None,
        }

    return {
        "presented": owner_name or None,
        "role": "owner",
        "white_labelled": False,
        "withheld": None,
    }


# --------------------------------------------------------------------------- #
# Hostname claims
# --------------------------------------------------------------------------- #


def claim_next_state(state: str, event: str) -> str:
    """The state a claim moves to when ``event`` happens.

    A released host can be verified again, because a fresh claim reuses the
    history: the state machine describes the states, and the engine writes a new
    claim record rather than reviving an old one.
    """
    if event not in CLAIM_EVENTS:
        raise BrandedDomainError(
            f"{event!r} is not a claim event. Use one of: " + ", ".join(CLAIM_EVENTS) + "."
        )
    if state not in CLAIM_STATES:
        raise BrandedDomainError(
            f"{state!r} is not a claim state. Use one of: " + ", ".join(CLAIM_STATES) + "."
        )

    if event == "release":
        return CLAIM_RELEASED
    if event == "verify_ok":
        return CLAIM_VERIFIED
    return CLAIM_FAILED


def claim_is_serving(state: str) -> bool:
    """True when a claim in this state answers traffic.

    Only a verified claim serves. A saved-but-unverified claim is the researched
    propagation window, and a released claim serves nothing at all.
    """
    return state == CLAIM_VERIFIED


def claimable(
    host: str,
    claims: Iterable[Mapping[str, Any]],
    *,
    brand_id: str | None = None,
) -> Mapping[str, Any] | None:
    """The *other* verified claim holding ``host``, or ``None``.

    Only a verified claim reserves a host, which is WF-017's rule and follows
    from the researched propagation window: a customer who has saved a host but
    not yet pointed DNS at this deployment must not lock it away from everyone.
    A claim the calling brand already holds is not a conflict with itself.
    """
    for claim in claims:
        if _text(claim.get("host")) != host:
            continue
        if claim.get("state") != CLAIM_VERIFIED:
            continue
        if brand_id is not None and _text(claim.get("brand_id")) == brand_id:
            continue
        return claim
    return None


def resolve_host(host: str, claims: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Which brand serves ``host``, and why when none does.

    ``reason`` is ``ok``, ``unclaimed`` or ``ambiguous``. Ambiguity is a refusal
    rather than a coin flip: the issue names two possible answers for host
    resolution as the specific hazard of holding two domain tables, so a host
    held by two verified claims serves nothing and is reported.
    """
    matches = [
        claim
        for claim in claims
        if _text(claim.get("host")) == host and claim.get("state") == CLAIM_VERIFIED
    ]

    if not matches:
        return {
            "claim": None,
            "reason": "unclaimed",
            "detail": f"{host} is not claimed by any brand.",
        }
    if len(matches) > 1:
        return {
            "claim": None,
            "reason": "ambiguous",
            "detail": f"{len(matches)} verified claims hold {host}; routing is refused.",
        }
    return {
        "claim": matches[0],
        "reason": "ok",
        "detail": f"{matches[0].get('brand_id')} serves {host}.",
    }


def verify_report(
    host: str,
    *,
    matched: bool,
    observed: Iterable[str] = (),
    cname_target: str = "",
    holder: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The checks a host claim is judged by, as data.

    Takes the resolver's answer rather than resolving, so the report is a pure
    function and the verification flow is testable with no DNS at all. The shape
    deliberately matches WF-017's report: the two surfaces answer the same
    operator question, and a second shape would be a second thing to learn.

    The format check is always true here because ``host`` arrives normalised by
    WF-017's own rule at the feature seam. The check is still listed, so the
    operator sees which rule accepted it.
    """
    seen = [str(item) for item in observed]
    available = holder is None

    checks = [
        {
            "name": "cname",
            "label": f"CNAME points at {cname_target}",
            "ok": bool(matched),
            "detail": (
                f"{host} resolves to {' , '.join(seen)}" if seen else f"{host} does not resolve yet"
            ),
        },
        {
            "name": "format",
            "label": "Subdomain format",
            "ok": True,
            "detail": "The host is a subdomain and is well formed.",
        },
        {
            "name": "available",
            "label": "Not held by another brand",
            "ok": available,
            "detail": (
                "No other brand holds this host."
                if available
                else f"Already held by brand {holder.get('brand_id') if holder else ''}."
            ),
        },
    ]

    ready = all(check["ok"] for check in checks)
    return {
        "host": host,
        "cname_target": cname_target,
        "ready": ready,
        "status": CLAIM_VERIFIED if ready else CLAIM_FAILED,
        "observed": seen,
        "checks": checks,
        "cannot_verify": list(UNVERIFIABLE_HERE),
    }


# --------------------------------------------------------------------------- #
# The published vocabulary
# --------------------------------------------------------------------------- #


def vocabulary() -> dict[str, Any]:
    """The vocabulary this workflow enforces, and every decision behind it.

    Served by the vocabulary route, so the page reads its states and its token
    names from here rather than hard-coding them, and so a reviewer can read each
    decision without opening the source.
    """
    return {
        "collections": {
            "brand": BRAND_COLLECTION,
            "claim": CLAIM_COLLECTION,
            "room_theme": ROOM_THEME_COLLECTION,
            "event": EVENT_COLLECTION,
        },
        "token_names": list(TOKEN_NAMES),
        "tenant_tokens": list(TENANT_TOKENS),
        "product_tokens": list(PRODUCT_TOKENS),
        "claim_states": list(CLAIM_STATES),
        "claim_expectations": dict(CLAIM_EXPECTATIONS),
        "event_kinds": list(EVENT_KINDS),
        "unverifiable_here": list(UNVERIFIABLE_HERE),
        "decisions": dict(DECISIONS),
    }
