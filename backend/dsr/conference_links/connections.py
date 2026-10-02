"""The Integrations tab: one provider connection per host, and what needs one.

This is researched user_flow step 2, and the research is unusually blunt about
it: "Connecting Zoom on the Integrations tab is mandatory for this one to work".
automations adds that the same is true of Google Meet and Gong. So a connection
is not a setting a Location *may* use - it is a prerequisite a booking
*cannot* be provisioned without, and :func:`require_connected` is how that is
enforced rather than suggested.

What is and is not stored
-------------------------
"OAuth tokens per host" is a data source, and a token is a secret. This package
stores the *shape* of a connection - which provider, which host, whether it is
live, when it was last refreshed, what scopes it carries - and never the token
itself. The researched scope for the swap is named
(:data:`~dsr.conference_links.vocabulary.CAL_BOOKING_WRITE_SCOPE`) and is stored
because a reviewer needs to see what was asked for; a caller that wants to
demonstrate it was granted passes ``token_present``, not the token. Storing a
credential in a record this product writes an audit row for every time would
put it in the audit log, in the JSONL mirror, and in every export - which is
why ``token`` is not merely discouraged here but refused.

The thirty-provider question
----------------------------
extensibility says "Cal exposes ~30 video integrations plus a ``link`` escape
hatch", and the research lists the thirty by name. All thirty are *addressable*
here - :func:`describe_provider` will describe any of them - but only three are
*provisionable*, because only three are Location options the researched picker
offers. The difference is kept explicit in the data rather than blurred: a
provider carries ``picker: true`` or ``picker: false``, and a client that renders
the checklist can show the other twenty-seven as "available in Cal, not
offered by this Meeting Type" instead of pretending this build mints a
Whereby link.

Gong is the odd one. It is a picker option, it mints a conference, and it is
*not* one of the thirty - it is a Chili Piper provider whose link redirects to
Zoom. :func:`describe_provider` says so in ``in_cal_integration_enum`` and
carries the quote, so a reader sees the gap rather than a tidy lie.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dsr.conference_links import locations, vocabulary as vocab
from dsr.conference_links.errors import (
    ConnectionError,
    ProviderNotConnected,
    UnknownProvider,
)

#: What a connection must carry before it can be called live.
#:
#: The research says only "Connecting Zoom on the Integrations tab is
#: mandatory" and "OAuth tokens per host". It does not enumerate what makes a
#: connection usable, so these three are this build's reading: named, here,
#: and recorded as the ``connection-readiness`` inference.
REQUIRED_FIELDS: tuple[str, ...] = ("provider", "host")

#: The states a connection is reported in, which are not the same as the
#: researched Location states and must not be confused with them.
CONNECTION_STATES: tuple[str, ...] = ("connected", "needs-reauth", "revoked")

#: Gong is a Location option that is not a Cal integration. Named here because
#: :mod:`dsr.conference_links.locations` has to reach for it, and because the
#: gap is worth one constant rather than a string in three places.
GONG = "gong"


def describe_provider(provider: str) -> dict[str, Any]:
    """Everything known about one provider, in one object.

    Answers three questions a client cannot answer for itself: can this Meeting
    Type's picker offer it, does provisioning through it need a connection, and
    is it one of the thirty Cal documents.
    """
    name = normalise_provider(provider)

    if name in vocab.PICKER_PROVIDERS:
        kind = name
        picker = True
        one_time = True
        outcome = "conference-provisioned"
    else:
        # Every other member of the enum is addressable and provisionable in
        # principle, but no researched Location option reaches it, so the picker
        # says no and the outcome is the honest blank.
        kind = None
        picker = False
        one_time = True
        outcome = None

    return {
        "provider": name,
        "label": vocab.LOCATION_LABELS.get(name, name),
        "on_picker": picker,
        "location_kind": kind,
        "mints_one_time_conference": one_time,
        "connection_required": picker,
        "provision_outcome": outcome,
        "in_cal_integration_enum": name in vocab.CAL_INTEGRATION_ENUM,
        "enum_position": (
            vocab.CAL_INTEGRATION_ENUM.index(name) if name in vocab.CAL_INTEGRATION_ENUM else None
        ),
        "gong_redirects_to_zoom": name == GONG,
        "researched": _provider_quote(name),
    }


def _provider_quote(provider: str) -> str:
    if provider == GONG:
        return vocab.GONG_REDIRECT_QUOTE
    if provider in ("google-meet", "zoom"):
        return {
            "google-meet": "This option generates a one-time Google Meet link to be displayed in the Location.",
            "zoom": "This one generates a one-time Zoom link.",
        }[provider]
    return "A member of Cal's documented integration enum; not one of the Location options this workflow's picker offers."


def provider_catalogue() -> dict[str, Any]:
    """Every addressable provider: the three, then the rest of the enum.

    ``picker_count`` and ``enum_count`` are carried separately on purpose. A
    page that shows "30 providers" when only three can actually be provisioned
    is the failure this split exists to prevent, and a reviewer reading the
    numbers should be able to tell at a glance which is which.
    """
    providers = [describe_provider(name) for name in vocab.PICKER_PROVIDERS]
    others = [
        describe_provider(name)
        for name in vocab.CAL_INTEGRATION_ENUM
        if name not in vocab.PICKER_PROVIDERS
    ]
    return {
        "picker_count": len(providers),
        "enum_count": len(vocab.CAL_INTEGRATION_ENUM),
        "in_cal_enum_count": len(others),
        "escape_hatch": {
            "location_type": "link",
            "why": vocab.STATIC_LINK_QUOTE,
        },
        "providers": providers,
        "other_cal_integrations": others,
    }


def normalise_provider(raw: Any) -> str:
    """Resolve a provider name, refusing anything this workflow does not offer.

    The offerable set is the picker - the three researched Location options -
    plus the rest of Cal's enum, so a team that wants to record a connection for
    ``whereby-video`` before this build grows a Location option for it has
    somewhere to put it. A name outside *that* is refused by name, because a
    silent pass would store a connection nothing can ever use.
    """
    if raw is None:
        raise UnknownProvider(f"a connection must name a provider; known providers: {_known()}")
    text = str(raw).strip().lower()
    if not text:
        raise UnknownProvider(f"a connection must name a provider; known providers: {_known()}")
    if text in vocab.PICKER_PROVIDERS or text in vocab.CAL_INTEGRATION_ENUM:
        return text
    raise UnknownProvider(f"{text!r} is not a known provider; known providers: {_known()}")


def _known() -> str:
    return ", ".join(sorted(set(vocab.PICKER_PROVIDERS) | set(vocab.CAL_INTEGRATION_ENUM)))


def normalise(
    payload: Mapping[str, Any], *, existing: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Resolve a connection body into the shape this package stores.

    Merges over ``existing`` so a PATCH validates the *result*, not the patch -
    the same rule WF-041's connection endpoint follows, and the reason a patch
    that removes the provider from a live connection is refused rather than
    applied.

    ``token`` is refused outright. See this module's docstring for why a secret
    cannot be a field on a record this product writes an audit row for.
    """
    body = dict(existing or {})
    body.update({key: value for key, value in dict(payload).items() if value is not None})

    if "token" in payload or (existing or {}).get("token"):
        raise ConnectionError(
            "a connection's credential is not stored on the record; record token_present "
            "instead, so the secret cannot reach the audit log or its JSONL mirror"
        )

    provider = normalise_provider(body.get("provider"))

    host = str(body.get("host") or body.get("host_id") or body.get("organizer") or "").strip()
    if not host:
        raise ConnectionError(
            f"a connection must name a host; OAuth tokens are per host, so {provider!r} "
            "without one cannot be resolved to a credential"
        )

    token_present = body.get("token_present")
    if token_present is None:
        token_present = True  # a connection is created because a credential exists

    state = str(body.get("state") or ("connected" if token_present else "needs-reauth"))
    if state not in CONNECTION_STATES:
        raise ConnectionError(
            f"{state!r} is not a connection state; choose one of {', '.join(CONNECTION_STATES)}"
        )

    scopes = body.get("scopes")
    if scopes is None:
        scopes = [vocab.CAL_BOOKING_WRITE_SCOPE] if provider in vocab.PICKER_PROVIDERS else []
    elif isinstance(scopes, str):
        scopes = [part.strip() for part in scopes.split(",") if part.strip()]
    else:
        scopes = [str(scope).strip() for scope in scopes if str(scope).strip()]

    return {
        "provider": provider,
        "host": host,
        "state": state,
        "token_present": bool(token_present),
        "scopes": scopes,
        "calendar_id": str(body.get("calendar_id") or "").strip() or None,
        # The domain a guest's link is built on. Separate from `calendar_id`
        # because those are different things and conflating them produces a
        # nonsense URL: the researched example is
        # `https://example.zoom.us/j/1234567890`, and a Workspace account id is
        # not a host. A deployment that knows its real conferencing domain sets
        # this once here and every link follows.
        "link_host": str(body.get("link_host") or body.get("domain") or "").strip() or None,
        "account_email": str(body.get("account_email") or "").strip() or None,
        "note": str(body.get("note") or "").strip() or None,
        "picker_provider": provider in vocab.PICKER_PROVIDERS,
    }


def _unwrap(connection: Mapping[str, Any] | None) -> dict[str, Any]:
    """The payload of a connection, whichever shape the caller has to hand.

    Two callers, two shapes, and they must not disagree: the engine holds a
    *record* off the store, whose payload lives under ``data``, while a test and
    a client hold the *presented* connection, whose fields are already
    flattened. Accepting both here is what stops a readiness check from quietly
    reading a top-level ``provider`` that is not there and reporting every
    connection as missing its provider and its host.
    """
    if not connection:
        return {}
    if isinstance(connection.get("data"), Mapping):
        return dict(connection["data"])
    return dict(connection)


def readiness(connection: Mapping[str, Any] | None) -> dict[str, Any]:
    """What is still missing before this connection can provision a conference.

    Named per missing field rather than as a boolean, because the fix for each
    is a different action on a different screen: connect it, re-authorise it, or
    accept that it is gone. ``ready`` is the conjunction, so a caller that only
    wants a flag does not have to read the list.
    """
    if not connection:
        return {
            "ready": False,
            "missing": ["connection"],
            "state": "absent",
            "provider": None,
        }

    data = _unwrap(connection)
    provider = str(data.get("provider") or "")
    host = str(data.get("host") or "").strip()
    state = str(data.get("state") or "")
    token_present = bool(data.get("token_present"))

    missing: list[str] = []
    if not provider:
        missing.append("provider")
    if not host:
        missing.append("host")
    if not token_present:
        missing.append("token")
    if state != "connected":
        missing.append(f"state:{state or 'unknown'}")

    return {
        "ready": not missing,
        "missing": missing,
        "state": state or "unknown",
        "provider": provider or None,
        "host": host or None,
    }


def require_connected(
    kind: str,
    connection: Mapping[str, Any] | None,
    *,
    location: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Enforce the researched mandatory connection, or explain what is missing.

    Called before anything is provisioned. It raises
    :class:`~dsr.conference_links.errors.ProviderNotConnected` for the three
    one-time kinds and returns the readiness report unchanged for the other
    four, which need no connection and must not be asked for one.

    The refusal message names the *tab* as well as the provider, because the
    research's sentence is about where the fix lives: "Connecting Zoom on the
    Integrations tab is mandatory for this one to work". A seller who sees the
    gate needs to be told which screen to open.

    The message also distinguishes *which* is wrong, because the two send an
    admin to different screens and the default wording sends both to "connect
    it", which is advice for one of them and a dead end for the other:

    * a connection exists and is named - it is not ready, so re-authorise it;
    * several connections exist and this build will not choose between them, so
      name one;
    * nothing exists at all, so connect the provider.
    """
    resolved = locations.normalise_kind(kind)
    if not locations.needs_conference(resolved):
        return readiness(None) | {"applicable": False, "kind": resolved}

    provider = locations.provider_for(resolved)
    report = readiness(connection)
    report["applicable"] = True
    report["kind"] = resolved

    if report["ready"]:
        return report

    label = vocab.LOCATION_LABELS.get(resolved, resolved)
    prefix = f"connecting {provider} on the Integrations tab is mandatory for {label} to work"

    named = (
        str((location or {}).get("connection_id") or "").strip()
        or str((connection or {}).get("id") or "").strip()
    )
    if named:
        raise ProviderNotConnected(
            f"{prefix}; connection {named!r} is not ready ({', '.join(report['missing'])})"
        )

    candidates = [str(entry) for entry in (location or {}).get("provider_candidates") or []]
    if candidates:
        raise ProviderNotConnected(
            f"{prefix}; {len(candidates)} connections for {provider} are ready and this "
            f"booking does not say which one to use, so name connection_id "
            f"({', '.join(candidates)}) rather than have one picked for you"
        )

    raise ProviderNotConnected(f"{prefix}; no connection is configured for {provider}")


def find_duplicate(
    records: list[Mapping[str, Any]], provider: str, host: str
) -> Mapping[str, Any] | None:
    """The connection this host already has for that provider, if any.

    "provider OAuth connection" is one per host per provider. A second one
    leaves the Location picker choosing between two credentials with nothing to
    choose on, so this returns the row to refuse against rather than letting
    the caller compare ids after the fact.

    Both shapes are accepted for the same reason :func:`_unwrap` accepts both:
    the engine walks raw records off the store, whose payload is under ``data``,
    and a client holds already-presented connections.
    """
    for record in records:
        data = _unwrap(record)
        if str(data.get("provider") or "") == provider and str(data.get("host") or "") == host:
            return record
    return None


__all__ = [
    "CONNECTION_STATES",
    "GONG",
    "REQUIRED_FIELDS",
    "describe_provider",
    "find_duplicate",
    "normalise",
    "normalise_provider",
    "provider_catalogue",
    "readiness",
    "require_connected",
]
