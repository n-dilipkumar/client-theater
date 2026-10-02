"""Subscription channels: the multi-tenant extension point the research names.

"A subscription channel is a stream of change events that correspond to one or
more entities. Change Data Capture provides predefined standard channels and you
can create your own custom channels. ... The channel name is case-sensitive."

Two rules live here and both are load-bearing.

**The name is case-sensitive, so two names differing only in case are two
channels.** That is not pedantry: the standard channel is
``/data/ChangeEvents`` and the enrichment rule below is about the standard
channel specifically, so a lookup that folded case would let a room create a
channel that reads as standard and behave as custom.

**Enrichment is refused on the standard channel.** The research recommends
against it, in a sentence whose reason is about *other* subscribers: "other
subscribers that receive change events on the standard channel don't receive
unchanged fields that they don't expect." This build refuses rather than warns,
and the reason is in that clause - the parties who would be harmed are not in
this room and cannot be enumerated, so nobody here can weigh the cost of the
warning against the harm of ignoring it. Refusing is the only answer available
to a party that cannot see the other parties.

The custom-channel answer is the researched one: "A room deployment can create
its own channel and add enrichment without disturbing other consumers."
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.change_stream import vocabulary
from dsr.change_stream.errors import (
    ChannelError,
    FieldMapError,
    StandardChannelEnrichmentRefused,
    UnsupportedEnrichmentTransport,
)
from dsr.change_stream.fieldmap import normalise_field_map


def normalise_channel(
    raw: Mapping[str, Any],
    *,
    org: Mapping[str, Any],
    transport: str,
) -> dict[str, Any]:
    """A channel record in the shape this package stores.

    The standard channel is a fixed fact, not something a caller chooses: naming
    ``/data/ChangeEvents`` is what makes a channel the standard one. Everything
    else - which entities, which field map, how big the buffer - is the caller's.
    """
    if not isinstance(raw, Mapping):
        raise ChannelError(f"a channel must be a JSON object; got {type(raw).__name__}")

    name = str(raw.get("name") or "").strip()
    if not name:
        raise ChannelError("a channel needs a name; the research makes it the identity")
    if transport not in vocabulary.TRANSPORTS:
        raise ChannelError(
            f"transport must be one of {', '.join(vocabulary.TRANSPORTS)}; got {transport!r}"
        )

    entities_raw = raw.get("entities") or []
    if not isinstance(entities_raw, (list, tuple)) or not entities_raw:
        raise ChannelError(
            "a channel needs at least one entity: it is 'a stream of change events that "
            "correspond to one or more entities'"
        )
    entities = [str(name) for name in entities_raw if str(name).strip()]
    if len(set(entities)) != len(entities):
        raise ChannelError("entities must be unique within a channel")

    kind = "standard" if vocabulary.is_standard_channel(name) else "custom"
    declared_kind = str(raw.get("kind") or "").strip()
    if declared_kind and declared_kind != kind:
        raise ChannelError(
            f"channel {name!r} is the {kind} channel by name; kind={declared_kind!r} "
            "contradicts it. The standard channel is identified by its name, so a caller "
            "cannot relabel it."
        )

    field_map = normalise_field_map(raw.get("field_map"))

    buffer_bytes = raw.get("buffer_bytes")
    if buffer_bytes is None:
        buffer = vocabulary.RECOMMENDED_BUFFER_BYTES
    else:
        try:
            buffer = int(buffer_bytes)
        except (TypeError, ValueError) as exc:
            raise ChannelError(
                f"buffer_bytes must be a whole number of bytes; got {buffer_bytes!r}"
            ) from exc
        if buffer <= 0:
            raise ChannelError("buffer_bytes must be greater than zero")

    return {
        "name": name,
        "kind": kind,
        "entities": entities,
        "org_id": org.get("id"),
        "org_system": org.get("system"),
        "field_map": field_map,
        "enriched_fields": [],
        "buffer_bytes": buffer,
        # The research says "Pub/Sub buffer sizing is also tunable", so a deviation
        # is allowed and reported rather than refused.
        "buffer_matches_recommendation": buffer == vocabulary.RECOMMENDED_BUFFER_BYTES,
        "transport": transport,
    }


def channel_name_taken(existing: list[Mapping[str, Any]], name: str, *, org_id: str | None) -> bool:
    """Whether this org already has a channel with *exactly* this name.

    Case-sensitive on purpose, because "The channel name is case-sensitive." Two
    channels named ``/data/ChangeEvents`` and ``/data/changeevents`` are two
    channels, and this function is what makes that true rather than an accident.
    """
    return any(
        str(other.get("name")) == name
        and (org_id is None or str(other.get("org_id")) == str(org_id))
        for other in existing
    )


def names_in_use(existing: list[Mapping[str, Any]], *, org_id: str | None) -> list[str]:
    """Every channel name this org holds, for the collision message."""
    return sorted(
        str(other.get("name"))
        for other in existing
        if org_id is None or str(other.get("org_id")) == str(org_id)
    )


def add_enrichment(
    channel: Mapping[str, Any],
    fields: Any,
    *,
    transport: str,
) -> dict[str, Any]:
    """Add enriched fields to a channel, or refuse with the researched reason.

    Both refusals are the research's, not this build's taste:

    * the standard channel, because the harm named in the documentation falls on
      other subscribers;
    * a transport outside {Pub/Sub, CometD, event relays}, because "Event
      enrichment is supported for subscribers that use Pub/Sub API, CometD
      (Streaming API), or event relays" is a closed list.
    """
    if not vocabulary.supports_enrichment(transport):
        raise UnsupportedEnrichmentTransport(
            f"event enrichment is not supported on the {transport!r} transport. It is "
            "supported for subscribers that use Pub/Sub API, CometD (Streaming API), or "
            "event relays, so a delta-link poll or a workflow webhook has no enriched fields "
            "to ask for."
        )
    if str(channel.get("kind")) == "standard":
        raise StandardChannelEnrichmentRefused(
            f"channel {channel.get('name')!r} is the standard channel, and enrichment on it "
            "would send unchanged fields to every other subscriber of the standard channel "
            "that they do not expect. Create a custom channel and add the fields there: a "
            "room deployment can create its own channel and add enrichment without "
            "disturbing other consumers."
        )

    if isinstance(fields, str):
        requested = [fields]
    elif isinstance(fields, (list, tuple)):
        requested = [str(name) for name in fields if str(name).strip()]
    else:
        raise ChannelError(
            f"enrichment.fields must be a string or a list of strings; got {type(fields).__name__}"
        )
    if not requested:
        raise ChannelError("enrichment.fields must name at least one CRM field")

    existing = [str(name) for name in (channel.get("enriched_fields") or [])]
    added = [name for name in requested if name not in existing]
    return {"enriched_fields": sorted(set(existing) | set(requested)), "added": added}


def remove_enrichment(channel: Mapping[str, Any], field: str) -> dict[str, Any]:
    """Drop one enriched field.

    Additive-only is the researched direction for the *channel* ("configure event
    enrichment on a custom channel"), and removing a field here is a room giving
    up the ability to resolve a record rather than an API removing something
    somebody else depends on, so it is allowed. What it may not do is leave the
    sync key unresolved while the channel still claims to stream update events -
    and that is checked by refusing at commit time, naming the field, rather than
    by inspecting the map here.
    """
    name = str(field).strip()
    existing = [str(entry) for entry in (channel.get("enriched_fields") or [])]
    if name not in existing:
        raise ChannelError(
            f"{name!r} is not enriched on channel {channel.get('name')!r}; it enriches "
            f"{existing or ['nothing']}"
        )
    return {"enriched_fields": [entry for entry in existing if entry != name], "removed": [name]}


def field_map_findings_for(channel: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Findings for a stored channel, so a page can show them without recomputing."""
    from dsr.change_stream.fieldmap import field_map_findings

    return field_map_findings(channel.get("field_map"))


def require_field_map(raw: Any) -> dict[str, Any]:
    """Re-exported so the engine has one import for the map's entry point."""
    return normalise_field_map(raw)


__all__ = [
    "ChannelError",
    "FieldMapError",
    "add_enrichment",
    "channel_name_taken",
    "field_map_findings_for",
    "names_in_use",
    "normalise_channel",
    "remove_enrichment",
    "require_field_map",
]
