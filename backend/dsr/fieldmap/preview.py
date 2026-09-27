"""The mapping evaluated against one record, in both directions.

The research's ``automations`` line is the design constraint: "None required at
write time - the mapping is evaluated per record on every sync cycle." So this is a
pure function over a record and the stored mapping, and it is exposed over HTTP so
an admin can see what a sync cycle *would* send before any cycle runs.

Outbound, the direction a write travels, the transforms apply to a sales-room value
and the result is keyed by the CRM property name. Inbound, the same rows run in
reverse: the record arrives keyed by CRM property, and the result is keyed by the
sales-room field name. Both halves are produced from one pass so a row that is
``both`` is applied once per direction and reported twice, which is what it is.

The sync key is injected, not mapped
-----------------------------------

The research says the key "carries the sales-room's own row id". So the outbound
payload always carries it, taken from the record's own id, and the trace says
``injected`` so a reader can see that no row produced it. A row *may* also target
the key property - the shipped HubSpot default does, with the ``identity``
transform - and when it does and the record carries a value, the row wins and the
trace says so. What never happens is a silent disagreement: if a row targets the
key and produces a different value, the payload carries the row's value and the
trace names the key property twice rather than hiding one.

Enumeration values are re-checked here, at the value
---------------------------------------------------

The grid checks the *table* - that every value the picklist transform can produce is
an internal option value. That cannot be the last word, because the table is data
and a record can carry anything. So the outbound value is checked against the
property's internal values too, and a value the CRM would refuse is reported on the
row rather than written. This is the same cited rule applied at the only moment the
actual value exists.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.fieldmap.metadata import Metadata
from dsr.fieldmap.transforms import REGISTRY, TransformUnavailable
from dsr.fieldmap.vocabulary import ENUMERATION_HINT, sends_in, sends_out

#: Per-row statuses. ``ok`` is the only one that reaches the CRM payload.
PREVIEW_STATUSES = ("ok", "skipped", "error")


def _trace(
    row: Mapping[str, Any],
    *,
    direction: str,
    status: str,
    before: Any = None,
    after: Any = None,
    target: str = "",
    reason: str = "",
    detail: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "row_id": row.get("id"),
        "source_field": str(row.get("source_field") or ""),
        "direction": direction,
        "target_property": target,
        "transform": str(row.get("transform") or ""),
        "transform_version": row.get("transform_version"),
        "status": status,
        "before": before,
        "after": after,
        "reason": reason,
        "detail": dict(detail or {}),
    }


def _apply(row: Mapping[str, Any], value: Any, direction: str) -> Any:
    """Run a row's transform, turning an unresolvable transform into a refusal.

    The registry raises :class:`~dsr.fieldmap.transforms.TransformUnavailable` and a
    bad value raises ``ValueError``; both are reported against the row rather than
    escaping, because a preview that dies on the first bad row tells an admin
    nothing about the other nine.
    """
    transform = REGISTRY.require(
        str(row.get("transform") or ""),
        int(row["transform_version"]) if row.get("transform_version") is not None else None,
    )
    return transform.fn(value, dict(row.get("transform_config") or {}), direction)


def _check_outbound_value(value: Any, prop: Any) -> str:
    """Why an outbound value the CRM would refuse is refused, or the empty string."""
    if prop is None or not prop.is_enumeration or value is None:
        return ""
    if str(value) in prop.internal_values:
        return ""
    return "unsupported_option"


def preview(
    mapping: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    metadata: Metadata,
    record: Mapping[str, Any],
    *,
    directions: Sequence[str] = ("out", "in"),
) -> dict[str, Any]:
    """What one sync cycle would send, and what it would read back.

    ``mapping`` is the mapping's ``data``; ``rows`` its stored grid records. Nothing
    is written and nothing is stored, which is what makes this safe to call from a
    page on every keystroke.
    """
    want_out = "out" in directions
    want_in = "in" in directions
    payload: dict[str, Any] = {}
    inbound: dict[str, Any] = {}
    trace: list[dict[str, Any]] = []
    errors = 0
    skipped = 0

    for row in rows:
        # A row arrives either as a stored record (envelope plus `data`) or as the
        # flat projection the grid renders, and both are accepted so a caller
        # previewing from a page does not have to reshape what it already has.
        envelope_id = row.get("id") if row.get("collection") else None
        row_id = envelope_id or row.get("id")
        # The envelope's id is folded back into the flat row so the trace can name
        # the row it came from. `id` is a reserved key, so the store never keeps it
        # inside `data` - which is why it has to be restored here rather than read.
        row_data = {**dict(row.get("data") or row), "id": row_id}
        source_field = str(row_data.get("source_field") or "")
        direction = str(row_data.get("direction") or "out")
        target = str(row_data.get("target_property") or "")
        prop = metadata.property(target) if target else None

        if want_out and sends_out(direction) and target:
            if source_field not in record:
                skipped += 1
                trace.append(
                    _trace(
                        row_data,
                        direction="out",
                        status="skipped",
                        target=target,
                        reason="source_field_absent",
                        detail={"source_field": source_field},
                    )
                )
                continue
            before = record[source_field]
            try:
                after = _apply(row_data, before, "out")
            except TransformUnavailable as exc:
                errors += 1
                trace.append(
                    _trace(
                        row_data,
                        direction="out",
                        status="error",
                        before=before,
                        target=target,
                        reason="transform_unavailable",
                        detail={"error": str(exc)},
                    )
                )
                continue
            except ValueError as exc:
                errors += 1
                trace.append(
                    _trace(
                        row_data,
                        direction="out",
                        status="error",
                        before=before,
                        target=target,
                        reason="transform_failed",
                        detail={"error": str(exc)},
                    )
                )
                continue
            refusal = _check_outbound_value(after, prop)
            if refusal:
                errors += 1
                trace.append(
                    _trace(
                        row_data,
                        direction="out",
                        status="error",
                        before=before,
                        after=after,
                        target=target,
                        reason=refusal,
                        detail={
                            "internal_values": list(prop.internal_values) if prop else [],
                            "matched_a_label": bool(
                                prop and prop.option_for_label(str(after)) is not None
                            ),
                            "hint": ENUMERATION_HINT,
                        },
                    )
                )
                continue
            payload[target] = after
            trace.append(
                _trace(row_data, direction="out", status="ok", before=before, after=after, target=target)
            )

        if want_in and sends_in(direction) and target:
            if target not in record:
                skipped += 1
                trace.append(
                    _trace(
                        row_data,
                        direction="in",
                        status="skipped",
                        target=target,
                        reason="target_property_absent",
                        detail={"target_property": target},
                    )
                )
                continue
            before = record[target]
            if prop is not None and prop.is_enumeration and str(before) not in prop.internal_values:
                option = prop.option_for_label(str(before))
                errors += 1
                trace.append(
                    _trace(
                        row_data,
                        direction="in",
                        status="error",
                        before=before,
                        target=target,
                        reason="unsupported_option",
                        detail={
                            "internal_values": list(prop.internal_values),
                            "matched_a_label": option is not None,
                            "internal_name": option.value if option is not None else "",
                            "hint": ENUMERATION_HINT,
                        },
                    )
                )
                continue
            try:
                after = _apply(row_data, before, "in")
            except TransformUnavailable as exc:
                errors += 1
                trace.append(
                    _trace(
                        row_data,
                        direction="in",
                        status="error",
                        before=before,
                        target=target,
                        reason="transform_unavailable",
                        detail={"error": str(exc)},
                    )
                )
                continue
            except ValueError as exc:
                errors += 1
                trace.append(
                    _trace(
                        row_data,
                        direction="in",
                        status="error",
                        before=before,
                        target=target,
                        reason="transform_failed",
                        detail={"error": str(exc)},
                    )
                )
                continue
            inbound[source_field] = after
            trace.append(
                _trace(row_data, direction="in", status="ok", before=before, after=after, target=target)
            )

    # -- the sync key --------------------------------------------------------- #
    sync_key = dict(mapping.get("sync_key") or {})
    key_property = str((sync_key.get("properties") or [""])[0] or "")
    key_trace: dict[str, Any] | None = None
    if key_property and want_out:
        record_id = str(record.get("id") or "")
        injected = key_property not in payload
        if injected:
            payload[key_property] = record_id
        key_trace = {
            "property": key_property,
            "value": payload.get(key_property),
            "injected": injected,
            "reason": (
                "the sales room's own row id, which is what the key carries"
                if injected
                else "a mapped row already targets the key property, and its value was used"
            ),
            "unique": bool(sync_key.get("unique", True)),
        }

    return {
        "mapping_id": mapping.get("mapping_id") or mapping.get("id"),
        "connection_id": mapping.get("connection_id") or "",
        "crm_object": mapping.get("crm_object") or "",
        "provider": mapping.get("provider") or metadata.provider,
        "directions": [name for name in directions if name in ("out", "in")],
        "record_id": str(record.get("id") or ""),
        "out": payload,
        "in": inbound,
        "sync_key": key_trace,
        "trace": trace,
        "counts": {
            "out": len(payload),
            "in": len(inbound),
            "ok": len([item for item in trace if item["status"] == "ok"]),
            "skipped": skipped,
            "error": errors,
        },
        "writable": errors == 0,
        "wrote_anything": False,
        "note": (
            "None required at write time: the mapping is evaluated per record on every sync cycle. "
            "This is that evaluation, and it writes nothing."
        ),
    }
