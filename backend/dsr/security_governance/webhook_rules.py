"""WF-082: does an address fall inside a published set of IP ranges?

The specification's second data source is a file the provider publishes and updates:
"We have made a JSON file containing the full list of IP addresses that webhook
events may come from available for download... We recommend checking this list
periodically to ensure your callback handler is secure." The URL is
:data:`~dsr.security_governance.webhook_vocabulary.IP_RANGES_URL`, and the evidence
adds that it is "automatically updated if the IP addresses change".

That sentence sets the shape of this module. The list is not configuration somebody
typed and forgot. It is a document that moves under the reader's feet, so this
module holds a **snapshot with its age**, and the age is part of every answer. A
handler that checks an allowlist but cannot say how old it is cannot answer the only
question an operator will ask at three in the morning: is the refusal real, or is my
copy of the list stale? So :func:`evaluate_source_ip` returns the age alongside the
verdict, and ``stale_after_seconds`` decides when the age starts being a warning.

Why this module fetches nothing
-------------------------------

A verification handler that reached for the network during verification is a handler
whose answer depends on a third party's uptime, inside a thirty second budget, on the
critical path of every delivery. :func:`refresh_ranges` is a separate, explicit call,
and the module says why in its own docstring. Fetching is not forbidden by the
evidence; it is simply not something that should happen by surprise while someone
else's signature is waiting to be checked.

The two shapes of a range
-------------------------

:func:`classify_ranges` accepts both shapes the evidence implies and reports which
one it found, because silently assuming the wrong one is how an allowlist ends up
empty and every delivery refused:

* a list of objects, which is what the published file contains: ``[{"ip": "3.0.0.0/
  8", "description": "..."}, ...]``;
* a list of strings, which is what a person types into a settings page: ``["3.0.0.0/
  8", "192.0.2.10", ...]``.

IPv4 is an integer range. IPv6 is an integer range too, and :func:`normalise` maps
either family onto one integer plus its width, so containment is the same one
comparison in both families. A prefix longer than the family's width is refused rather
than clamped, because a clamped prefix silently widens an allowlist and that is the
one direction this module must never fail in.
"""

from __future__ import annotations

import ipaddress
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from dsr.security_governance import webhook_vocabulary as vocab

__all__ = [
    "DEFAULT_STALE_AFTER_SECONDS",
    "RANGE_KEYS",
    "IpRangeError",
    "classify_ranges",
    "describe_range",
    "evaluate_source_ip",
    "freshness",
    "normalise",
    "parse_ranges",
    "refresh_ranges",
    "staleness_note",
]

#: How old a snapshot may be before the answer carries a warning beside it.
#:
#: Derived from the specification's own instruction rather than chosen. It says to
#: check the list "periodically" and gives no period, so this is an inference and it
#: is named as one. One day is long enough that a fresh install is never noisy and
#: short enough that a weekend-old file is called out during an incident.
DEFAULT_STALE_AFTER_SECONDS = 86_400


class IpRangeError(ValueError):
    """A range entry this build will not interpret.

    Raised rather than skipped. A malformed entry in a range list is either a typo or
    a probe, and both deserve to stop the call that met them. The caller catches it,
    records the refusal by name, and answers rather than crashing.
    """


def normalise(value: str) -> tuple[int, int, int]:
    """One address as ``(lo, hi, width)``: the integer span it covers.

    An address and a network are both normalised, so a caller can mix them freely.
    ``/32`` and ``/128`` are a single address; a bare address is treated as a network
    of the family's full width. The width is returned alongside the span because a
    refusal to read a malformed prefix has to be able to say which family it was
    reading.
    """
    text = str(value or "").strip()
    if not text:
        raise IpRangeError("an IP range is empty")
    try:
        if "/" in text:
            network = ipaddress.ip_network(text, strict=False)
        else:
            network = ipaddress.ip_network(f"{text}/{_max_prefix(text)}", strict=False)
    except ValueError as exc:
        raise IpRangeError(f"{text!r} is not an IP address or network: {exc}") from exc
    if not isinstance(network, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
        # ip_network returns one of exactly those two types. The guard is here so a
        # future change to the stdlib cannot turn a wrong type into a wrong answer.
        raise IpRangeError(f"{text!r} is not an IPv4 or IPv6 value")
    return int(network.network_address), int(network.broadcast_address), network.max_prefixlen


def _max_prefix(text: str) -> int:
    """The full prefix length of the family ``text`` belongs to."""
    try:
        return ipaddress.ip_address(text).max_prefixlen
    except ValueError as exc:
        raise IpRangeError(f"{text!r} is not an IP address or network: {exc}") from exc


def describe_range(value: str) -> dict[str, Any]:
    """One range as data: the text, the family, the span and the address count."""
    lo, hi, width = normalise(value)
    return {
        "range": str(value).strip(),
        "family": "ipv6" if width == 128 else "ipv4",
        "prefix_length": width,
        "first_address": str(ipaddress.ip_address(lo)),
        "last_address": str(ipaddress.ip_address(hi)),
        "address_count": hi - lo + 1,
    }


def classify_ranges(payload: Any) -> list[dict[str, Any]]:
    """Normalise a published range file into one row per range.

    Accepts the two shapes the evidence implies and refuses anything else with a
    message that names what it found. The refusal matters more than the acceptance: a
    range file whose shape changed must stop the caller, because the alternative is an
    allowlist of zero ranges and a handler that refuses every delivery with a message
    about a signature.
    """
    if isinstance(payload, Mapping):
        # A published file sometimes wraps the list in a named key. Any key holding a
        # list is accepted, and the key it was found under is reported, because
        # "which key did the list come from" is the question an operator asks next.
        for key, value in payload.items():
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                return [{**row, "source_key": str(key)} for row in classify_ranges(value)]
        raise IpRangeError(
            "the range file holds no list; its keys are "
            + ", ".join(sorted(str(key) for key in payload))
        )
    if isinstance(payload, str):
        return classify_ranges(_split_text(payload))
    if not isinstance(payload, Sequence):
        raise IpRangeError(
            f"a range file must be a list or an object, not {type(payload).__name__}"
        )

    rows: list[dict[str, Any]] = []
    for entry in payload:
        if isinstance(entry, str):
            text, description = entry.strip(), ""
        elif isinstance(entry, Mapping):
            text = _entry_text(entry)
            description = str(entry.get("description") or "")
        else:
            raise IpRangeError(
                f"a range entry must be a string or an object, not {type(entry).__name__}"
            )
        if not text:
            continue
        row = describe_range(text)
        row["description"] = description
        rows.append(row)
    return rows


#: The keys a range object may carry its address under. Read in this order, and the one
#: that matched is recorded, so a file using a sixth name produces a refusal rather than an
#: empty allowlist. The refusal below names every key in this tuple.
RANGE_KEYS = ("ip", "cidr", "range", "prefix", "network")


def _entry_text(entry: Mapping[str, Any]) -> str:
    """The address out of a range object, whichever key the publisher used.

    The evidence quotes a file this workflow does not have, so the key name inside it is not
    known from the specification. Five plausible names are read and the key that matched is
    recorded. A file that uses a sixth name produces a refusal, because the alternative is an
    allowlist that silently holds no ranges at all.
    """
    for key in RANGE_KEYS:
        value = entry.get(key)
        if value:
            return str(value).strip()
    raise IpRangeError(
        "a range object carries none of the keys "
        + ", ".join(RANGE_KEYS)
        + f"; it carries {sorted(str(k) for k in entry)}"
    )


def _split_text(text: str) -> list[str]:
    """A range list typed as text, split on the separators people actually use."""
    body = text.strip()
    if body.startswith("["):
        body = body[1:]
    if body.endswith("]"):
        body = body[:-1]
    return [part.strip().strip('"').strip("'") for part in body.replace("\n", ",").split(",") if part.strip()]


def parse_ranges(rows: Iterable[Mapping[str, Any]]) -> list[tuple[int, int, int]]:
    """The stored rows as comparable spans.

    The rows are re-normalised here rather than trusting a span someone may have
    stored next to them. A span is three integers and reading it back is cheap; a
    span that disagrees with its own ``range`` text is a row somebody edited by hand.
    """
    spans: list[tuple[int, int, int]] = []
    for row in rows:
        text = row.get("range")
        if not text:
            raise IpRangeError("a stored range row carries no range text")
        spans.append(normalise(str(text)))
    return spans


def evaluate_source_ip(
    source_ip: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    snapshot_at: str | None,
    now: datetime,
    stale_after_seconds: int | None = None,
) -> dict[str, Any]:
    """Decide one source address against a range snapshot.

    Returns a dict rather than a bool because a bare bool answers the wrong question.
    The caller needs to know which range matched, how old the snapshot was, and which
    value in the registration makes that snapshot stale. All three are facts about the
    answer, and a delivery log that carries only ``allowed: true`` cannot answer an
    incident question a month later.

    Refusals name a range rather than a row, because a person debugging needs the
    address they should add and not the internal id of the row it was not in.

    ``error_name`` and ``reason`` are kept apart on purpose, because they answer two
    different questions and a consumer has to be able to tell which is which.
    ``error_name`` is ``None`` on the happy path and otherwise a key this build publishes in
    :data:`~dsr.security_governance.webhook_vocabulary.ERROR_CODES`, so a client can look the
    answer up. ``reason`` is a sentence to display, and it is a sentence on **every** path
    including the successful one. Returning a slug in ``reason`` on one path and prose on
    another is what makes a field unusable: the reader has to know which path ran before they
    can decide whether to display it or index it.
    """
    if not str(source_ip or "").strip():
        return {
            "allowed": False,
            # ``passed`` is present alongside ``allowed`` so all three checks in
            # CHECK_ORDER answer the same question under the same key. The two digest checks
            # report ``passed``; without this the allowlist check was the odd one out, and a
            # consumer reading the list could not test one field across all three.
            "passed": False,
            "check": vocab.IP_ALLOWLIST,
            "error_name": "no_source_ip",
            "allowed_range": None,
            "range_count": len(rows),
            "snapshot_at": snapshot_at,
            "staleness": "unknown",
            "stale": True,
            "stale_after_seconds": _stale_window(stale_after_seconds),
            "reason": "no source address was supplied",
        }

    spans = parse_ranges(rows)
    width = _address_width(source_ip)
    matched: str | None = None
    for row, (lo, hi, row_width) in zip(rows, spans, strict=True):
        if row_width != width:
            # An IPv4 address is never inside an IPv6 range and the reverse. Comparing
            # the widths first is what makes that true, and skipping the check would
            # make an IPv4 range containing an IPv6 address whose integer value happens
            # to be small.
            continue
        try:
            address = int(ipaddress.ip_address(str(source_ip).strip()))
        except ValueError as exc:
            raise IpRangeError(f"{source_ip!r} is not an IP address: {exc}") from exc
        if lo <= address <= hi:
            matched = str(row.get("range"))
            break

    age = staleness_note(snapshot_at, now=now, stale_after_seconds=stale_after_seconds)
    allowed = matched is not None
    return {
        "allowed": allowed,
        # See the note on the empty-address path: ``passed`` and ``allowed`` always agree.
        "passed": allowed,
        "check": vocab.IP_ALLOWLIST,
        "error_name": None if allowed else "source_ip_not_allowed",
        "allowed_range": matched,
        "range_count": len(rows),
        "snapshot_at": snapshot_at,
        "staleness": age["note"],
        "stale": bool(age["stale"]),
        "stale_after_seconds": age["stale_after_seconds"],
        "age_seconds": age["age_seconds"],
        "reason": (
            "the source address is inside the published range file"
            if allowed
            else "the source address is not in the published range file"
        ),
    }


def _address_width(source_ip: str) -> int:
    """The full prefix length of the family a source address belongs to."""
    try:
        return ipaddress.ip_address(str(source_ip).strip()).max_prefixlen
    except ValueError as exc:
        raise IpRangeError(f"{source_ip!r} is not an IP address: {exc}") from exc


def _stale_window(value: int | None) -> int:
    return DEFAULT_STALE_AFTER_SECONDS if value is None else max(0, int(value))


def staleness_note(
    snapshot_at: str | None,
    *,
    now: datetime,
    stale_after_seconds: int | None = None,
) -> dict[str, Any]:
    """How old a snapshot is, and whether that age is now a warning.

    A snapshot with no timestamp reads as stale, and that is deliberate: an allowlist
    of unknown vintage is exactly the case where a person needs to be told, and the
    alternative - treating an unknown age as fresh - is the failure this whole module
    exists to avoid.
    """
    window = _stale_window(stale_after_seconds)
    parsed = _parse_timestamp(snapshot_at)
    if parsed is None:
        return {
            "snapshot_at": snapshot_at,
            "age_seconds": None,
            "stale": True,
            "stale_after_seconds": window,
            "note": f"the range snapshot carries no timestamp, so its age is unknown; refresh it from {vocab.IP_RANGES_URL}",
        }
    reference = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    age = int((reference - parsed).total_seconds())
    # A snapshot stamped in the future is a clock problem, not a very fresh file. It
    # is reported as stale rather than as negative-age-fresh, because treating it as
    # fresh would be the one answer that is certainly wrong.
    stale = age < 0 or age > window
    note = (
        f"the range snapshot is {age} s old, past the {window} s staleness window; "
        f"refresh it from {vocab.IP_RANGES_URL}"
        if age >= 0
        else f"the range snapshot is stamped {abs(age)} s in the future; check this host's clock"
    )
    return {
        "snapshot_at": snapshot_at,
        "age_seconds": age,
        "stale": stale,
        "stale_after_seconds": window,
        "note": (
            f"the range snapshot is {age} s old and inside the {window} s staleness window"
            if not stale
            else note
        ),
    }


#: The public spelling, so a caller reading the engine does not have to remember which
#: of the two names the same function has.
freshness = staleness_note


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def refresh_ranges(*, source_url: str, fetcher, now: datetime) -> dict[str, Any]:
    """Fetch and store a new range snapshot.

    The fetch is a callable rather than a hard-coded HTTP client so three things are
    possible, and each of them matters:

    * a test can hand over bytes without a network;
    * a deployment can put its own client, with its own proxy and its own timeouts,
      in front of the call;
    * the source URL is an argument, so the published host is a default in the engine
      and not a constant buried in a request.

    The whole call is bounded by :data:`vocab.PROVIDER_TIMEOUT_SECONDS`-sized
    expectations rather than being allowed to run open-ended, and a fetch that raises
    leaves the previous snapshot in place. Refusing to overwrite a good allowlist with
    a failed refresh is the only safe behaviour: the alternative is a verification
    handler that stops verifying because a vendor had a bad minute.
    """
    document = fetcher(source_url)
    rows = classify_ranges(document)
    if not rows:
        raise IpRangeError("the fetched range file holds no ranges")
    return {
        "source_url": source_url,
        "fetched_at": now.astimezone(timezone.utc).isoformat(timespec="milliseconds"),
        "range_count": len(rows),
        "ranges": rows,
    }


def next_stale_moment(snapshot_at: str, *, now: datetime, stale_after_seconds: int) -> datetime:
    """When a snapshot taken at ``snapshot_at`` goes stale. Used by the engine."""
    parsed = _parse_timestamp(snapshot_at)
    if parsed is None:
        return now + timedelta(seconds=max(0, int(stale_after_seconds)))
    return parsed + timedelta(seconds=max(0, int(stale_after_seconds)))