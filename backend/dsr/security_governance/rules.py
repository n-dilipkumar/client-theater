"""The two booleans on a link, and what they mean for a viewer.

Every rule here is the researched specification for WF-073 made executable. The
specification is ``docs/research/digital-sales-room-workflows/wf/WF-073.md``, quoted
in full in issue 176, and the docstring on each rule names the evidence it came from.

The three rules the rest of the product leans on
------------------------------------------------

**A flag is a boolean, and a missing flag is false.** The OpenAPI evidence is explicit
about both halves: "`enable_screenshot_protection` (boolean, default `false`),
`enable_confidential_view` (boolean, default `false`)". So a link created without
either flag has both off, and a link whose stored value is not a boolean is a
validation failure rather than something coerced into one. Coercing `"false"` to
true is the defect this rule prevents, and a control silently the wrong way round is
the worst outcome this workflow can produce.

**Confidential view is a rendering transformation, not a permission.** The data flow
says it plainly: "Confidential view is a *rendering* transformation applied in the
viewer: the page band in focus is delivered sharp, the remainder is delivered
blurred". Nothing here decides who may open a link. Gating a link is WF-069's domain,
and this package never refuses a viewer. A control that blurred the page for an
unauthorised reader would be a second, weaker gate wearing a rendering control's name.

**A toggle is in place and does not disturb the URL or an open viewer.** The user
flow's fourth step says the update is "``links update --confidential-view on|off`` /
``--screenshot-protection on|off`` - the URL and existing viewers are unaffected", and
the API evidence says "``PATCH /v1/links/{id}`` - toggle either flag in place." So the
update changes the stored flag and nothing else: no new link id, no new URL, no
revocation, and no state a viewer is holding discarded. A viewer already looking at
the link sees the new setting on their next request, which is what "unaffected" means
for a link whose controls are read per request.

What this module does not decide
--------------------------------

Whether a capture was actually prevented. The specification's criticality note says
screenshot blocking "is largely unenforceable from a browser", so the honest shape is
a report: the viewer's browser tells this product what it saw, and this product
records that as what it saw. Nothing in this package asserts that a page can stop a
camera. See :mod:`dsr.security_governance.vocabulary` for the wording that every
response carries as a result.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from dsr.security_governance import vocabulary as vocab

#: The data key this workflow stores a room reference under.
#:
#: Not ``room_id``, and that is not a style preference. ``room_id`` is part of the
#: record *envelope*, so ``AuditedDatabase._insert_record`` strips it out of ``data``
#: before the dynamic index is built. A link that stored its room there would be
#: unfilterable by ``find()`` - and a "filter links by room" that silently returns
#: nothing is the kind of defect that ships. The envelope still carries ``room_id``;
#: this is the payload-side twin of it, and every response projects it back to
#: ``room_id``.
ROOM_REF = "room_ref"


def room_ref_of(data: Mapping[str, Any], record: Mapping[str, Any] | None = None) -> Any:
    """The room a payload belongs to: the payload's own key, else the envelope's."""
    value = data.get(ROOM_REF)
    if value:
        return value
    return (record or {}).get("room_id")


class ConfidentialError(ValueError):
    """A confidentiality setting this workflow will not accept.

    Carries a field-keyed map, because a seller filling in a form needs the message
    next to the input that caused it and not one combined sentence. The HTTP layer
    renders it as ``errors``.
    """

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})


class LinkNotFound(LookupError):
    """No such link, or it was never a link this workflow owns.

    Its own type rather than the store's ``RecordNotFound``, because a feature may
    only map error types it raises itself: registering a handler for a shared type
    would intercept that exception across the whole product.
    """


class PresetNotFound(LookupError):
    """No such baseline. Same reasoning as :class:`LinkNotFound`."""


class UnknownShortcut(ValueError):
    """A capture shortcut this workflow does not have a record for.

    The research enumerates no shortcut list, so the list in
    :mod:`dsr.security_governance.vocabulary` is derived. An attempt naming one that
    is not in it is refused rather than recorded as a row with a blank name, because
    a blank name in a governance log is worse than a refusal.
    """


# --------------------------------------------------------------------------- #
# The two booleans
# --------------------------------------------------------------------------- #


def coerce_flag(value: Any, field: str = "flag") -> bool | None:
    """Read one flag's stored or supplied value.

    Returns ``True``, ``False``, or ``None`` for "the caller did not say".

    ``None`` is a real answer and not an error, because the update is tri-state: a
    field the body omits is left alone, and ``{"enable_confidential_view": null}`` is
    a request to clear a link back to the documented default rather than a request
    to set it. The distinction is the same one WF-069 draws between an absent field
    and an explicit null, and it is the only way one link can be rotated and then
    un-rotated through a single endpoint.

    The CLI's tri-state spelling is accepted here too, because the specification
    names it: ``links update --confidential-view on|off``. ``on`` and ``off`` are the
    command line's words for the same two booleans, and refusing them would force a
    caller using the documented CLI to translate before it could call the API.
    """

    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("on", "true", "yes", "enabled"):
            return True
        if lowered in ("off", "false", "no", "disabled"):
            return False
    if isinstance(value, int) and value in (0, 1):
        # 0 and 1 are what a JSON round trip through some clients produces for a
        # boolean. Accepting only 0 and 1 rather than any integer keeps "2" a
        # validation failure instead of a truthy value nobody intended.
        return bool(value)
    raise ConfidentialError(
        "A confidentiality control is a boolean.",
        {field: "Use true or false, or the CLI spelling on or off."},
    )


def apply_flags(
    current: Mapping[str, Any], changes: Mapping[str, Any], *, strict: bool = True
) -> dict[str, bool]:
    """The two flags after applying ``changes`` on top of ``current``.

    Three states a field can be in, and each has its own answer:

    * ``changes`` omits it. The value from ``current`` stands. That is what "toggle
      either flag in place" means on a patch, and it is how a preset baseline reaches a
      new link: the baseline is ``current`` and the request only mentions what it wants
      to change.
    * ``changes`` sets it to a boolean. That value wins over ``current``.
    * ``changes`` sets it to ``null``. It returns to the documented default, which is
      ``False`` for both flags. This is how a link is taken back off a baseline.

    ``strict`` decides what happens when neither side has the field. On create it is
    on, and a flag nobody mentioned lands as the documented default rather than as an
    absent field, so a link created with no settings at all is unambiguously both-off.
    On a patch it is off, because a field absent from a stored link is already being
    read as ``False`` by :func:`enabled_flags` and by every projection.

    The two readings are correct for their own call, and conflating them is how a
    preset-seeded link loses the baseline it was supposed to inherit: the baseline goes
    in as ``current``, so an omitted flag must take the baseline's value rather than
    the default.
    """

    result: dict[str, bool] = {}
    for field in vocab.FLAGS:
        base = current.get(field)
        if base is None and not strict:
            # An absent field on a stored row means the documented default, which is
            # what the OpenAPI evidence says a link with no value for it has.
            base = vocab.DEFAULTS[field]
        if field in changes:
            coerced = coerce_flag(changes[field], field)
            result[field] = vocab.DEFAULTS[field] if coerced is None else coerced
        elif base is None:
            result[field] = vocab.DEFAULTS[field]
        else:
            result[field] = bool(base)
    return result


def enabled_flags(data: Mapping[str, Any]) -> list[str]:
    """Which of the two controls this link has on, in the specification's order."""
    return [field for field in vocab.FLAGS if bool(data.get(field, vocab.DEFAULTS[field]))]


def controls_summary(data: Mapping[str, Any]) -> dict[str, Any]:
    """The link's two controls, each with the wording the specification gives it.

    Every control carries ``effect`` and the limitation sentence, so a response
    cannot be read without the caveat. That is not decoration: the specification's
    criticality note forbids selling these controls as protection, and the cheapest
    way to honour that is to make the caveat part of the data rather than a line of
    UI copy somebody can delete.

    The two controls come back under ``panel_controls`` rather than ``controls``,
    because the engine's projection already uses ``controls`` for the list of which
    controls are switched on. Two different answers under one key in a single response
    would leave a reader guessing which one the page read.
    """

    return {
        "panel_controls": [
            {
                "field": control["field"],
                "label": control["label"],
                "on": bool(data.get(control["field"], control["default"])),
                "effect": control["effect"],
                "summary": control["summary"],
            }
            for control in vocab.PANEL_CONTROLS
        ],
        vocab.EFFECT_FIELD: vocab.EFFECT_VALUE,
        vocab.LIMITATION_FIELD: vocab.LIMITATION_VALUE,
    }


# --------------------------------------------------------------------------- #
# Confidential view: the focus band
# --------------------------------------------------------------------------- #


def _as_int(value: Any, name: str) -> int:
    """A viewport or page dimension as a whole number of pixels.

    Rounded rather than refused, because a browser reports a fractional CSS pixel
    (from a zoom level or a fractional device pixel ratio) and the band arithmetic is
    measured in whole pixels anyway. A dimension that is not a number at all is a
    validation failure, not a silent zero: a page height of zero would compute a
    band of zero and hand back a viewer with nothing sharp anywhere.
    """

    try:
        return int(round(float(value)))
    except (TypeError, ValueError) as exc:
        raise ConfidentialError(
            f"{name} must be a number of pixels.",
            {name: f"{name} must be a number of pixels."},
        ) from exc


def band_geometry(
    page_height: Any,
    viewport_height: Any,
    *,
    viewport_top: Any = 0,
    fraction: float = vocab.BAND_FRACTION,
    overlap: float = vocab.BAND_OVERLAP,
) -> dict[str, Any]:
    """Where the sharp band sits on one page, for one viewport.

    A page is delivered as a vertical stack of overlapping bands. The band whose
    range contains ``viewport_top`` is sharp; every other band is delivered blurred.
    That is the specification's own claim made arithmetic: "content only resolves
    inside the focus band, so a single screenshot cannot capture a whole page."

    The page's full-resolution bytes are never part of a response. A blurred band
    carries its geometry and its text length, not its glyphs, so the reader cannot
    reassemble a whole page from what the API sent. ``resolve`` below is what the
    viewer asks, and it answers with exactly one band.

    ``viewport_top`` is how far the page is scrolled, in pixels from the page's top.
    It is clamped into the page: a scroll past the last band resolves the last band
    rather than nothing, because the bottom of a document is a place a reader reaches
    and a viewer that went blank there would read as a broken product.
    """

    page = max(_as_int(page_height, "page_height"), 0)
    viewport = max(_as_int(viewport_height, "viewport_height"), 0)
    top = max(_as_int(viewport_top, "viewport_top"), 0)

    if page <= vocab.SMALL_PAGE_PIXELS:
        # Derived, and recorded as SMALL_PAGE_PIXELS in the vocabulary: a page
        # shorter than the smallest band has no out-of-band region, so the whole
        # page is sharp and there is nothing for confidential view to hide.
        return {
            "shape": vocab.RENDER_SHAPE_VIEWPORT_BANDS,
            "page_height": page,
            "viewport_height": viewport,
            "viewport_top": 0,
            "bands": [
                {
                    "index": 0,
                    "top": 0,
                    "bottom": page,
                    "sharp": True,
                    "reason": "page_shorter_than_one_band",
                }
            ],
            "sharp_index": 0,
            "sharp_top": 0,
            "sharp_bottom": page,
            "sharp_fraction": 1.0,
            "blurred_count": 0,
        }

    height = max(int(round(page * fraction)), 1)
    step = max(int(round(height * (1.0 - overlap))), 1)

    bands: list[dict[str, Any]] = []
    index = 0
    cursor = 0
    while cursor < page:
        top_edge = cursor
        bottom_edge = min(cursor + height, page)
        bands.append({"index": index, "top": top_edge, "bottom": bottom_edge})
        if bottom_edge >= page:
            break
        cursor += step
        index += 1

    position = min(top, max(0, page - 1))
    sharp_index = 0
    for band in bands:
        if band["top"] <= position < band["bottom"]:
            sharp_index = band["index"]
            break
    else:
        # The scrolled position sat in a gap the overlap did not cover, which can
        # only happen on the final band. Resolve the nearest band rather than
        # nothing.
        sharp_index = bands[-1]["index"]

    for band in bands:
        band["sharp"] = band["index"] == sharp_index

    sharp = bands[sharp_index]
    return {
        "shape": vocab.RENDER_SHAPE_VIEWPORT_BANDS,
        "page_height": page,
        "viewport_height": viewport,
        "viewport_top": position,
        "bands": bands,
        "sharp_index": sharp["index"],
        "sharp_top": sharp["top"],
        "sharp_bottom": sharp["bottom"],
        "sharp_fraction": round((sharp["bottom"] - sharp["top"]) / page, 4),
        "blurred_count": sum(1 for band in bands if not band["sharp"]),
    }


def resolve_band(geometry: Mapping[str, Any], band_index: int) -> dict[str, Any]:
    """One band, sharp or blurred, as the viewer receives it.

    A blurred band carries its geometry and its ``text_length`` and nothing else. It
    does not carry text, and it never carries the page's full-resolution bytes. That
    is the property the specification states as the reason this control is a
    rendering transformation rather than a client-side blur: "so the full-resolution
    page never reaches the client for out-of-band regions." A blur applied in the
    browser after the whole page arrived would satisfy the look and none of the
    guarantee, so the API never assembles that whole page.
    """

    bands = list(geometry.get("bands") or [])
    match = next((band for band in bands if band["index"] == band_index), None)
    if match is None:
        raise ConfidentialError(
            "No such band on this page.",
            {"band_index": f"This page has {len(bands)} band(s), numbered 0 to {len(bands) - 1}."},
        )
    sharp = bool(match.get("sharp"))
    resolved = {
        "index": match["index"],
        "top": match["top"],
        "bottom": match["bottom"],
        "sharp": sharp,
        "shape": geometry.get("shape", vocab.CHOSEN_RENDER_SHAPE),
        # Text length only. The count is what lets the viewer reserve the right amount
        # of space without learning a word.
        "text_length": match.get("text_length", 0),
        "text": match.get("text") if sharp else None,
    }
    # The geometry's own per-band reason, carried through rather than recomputed.
    #
    # This is the one band that can explain itself: a page shorter than a single band
    # is delivered whole because there is no out-of-band region on it, and that is a
    # different situation from a band that is sharp because the reader scrolled to it.
    # A viewer that cannot tell those two apart concludes the control is broken when it
    # is in fact declining to apply to a page it has nothing to withhold from.
    if match.get("reason"):
        resolved["reason"] = match["reason"]
    return resolved


# --------------------------------------------------------------------------- #
# Screenshot protection: the shortcut list
# --------------------------------------------------------------------------- #


def shortcut_for(keys: Any) -> dict[str, Any] | None:
    """The researched shortcut a key sequence matches, or ``None``.

    Matching is order-independent on the modifiers. A browser reports modifiers in
    ``KeyboardEvent`` flags and the character separately, and a caller that assembled
    the sequence in a different order than this table lists it would silently match
    nothing - which for a deterrence control means the buyer believes nothing is
    being watched.
    """

    if not isinstance(keys, (list, tuple)):
        return None
    wanted = {str(key).strip().lower() for key in keys if str(key).strip()}
    if not wanted:
        return None
    for shortcut in vocab.SHORTCUT_KEYS:
        if wanted == {key.lower() for key in shortcut["keys"]}:
            return dict(shortcut)
    return None


def blockable_shortcuts() -> list[dict[str, Any]]:
    """The shortcuts a web page can actually intercept before the capture path.

    The list is the honest scope. ``Print Screen`` is excluded because no web page
    can intercept it, and a viewer told that a control blocks screenshots has to be
    able to see the one it cannot. :func:`unblockable_shortcuts` names it instead of
    hiding it.
    """

    return [dict(shortcut) for shortcut in vocab.SHORTCUT_KEYS if shortcut.get("blockable", True)]


def unblockable_shortcuts() -> list[dict[str, Any]]:
    """The named capture shortcuts this product cannot intercept from a page."""
    return [
        dict(shortcut) for shortcut in vocab.SHORTCUT_KEYS if not shortcut.get("blockable", True)
    ]


# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime | None = None) -> str:
    """An ISO 8601 instant, always in UTC.

    The milliseconds are kept. A focus band is measured in pixels and a viewer's
    scroll is measured in pixels, so a stamp that rounded to the second would make
    two adjacent captures inside one second indistinguishable in the log.
    """

    return (moment or utcnow()).astimezone(timezone.utc).isoformat(timespec="milliseconds")


# --------------------------------------------------------------------------- #
# Presets
# --------------------------------------------------------------------------- #


def preset_baseline(payload: Mapping[str, Any]) -> dict[str, bool]:
    """The two flags a governed baseline seeds.

    Only the two flags this workflow owns are read. A baseline may carry other
    fields, and they are stored untouched, because the store is schema-flexible and
    a preset that seeds a governance set is only useful if it survives the trip. But
    only these two are interpreted here; the other sixteen preset-covered fields
    belong to other workflows' domains and enforcing them here would mean
    implementing rules nobody researched.
    """

    fields = payload.get("fields")
    if not isinstance(fields, Mapping):
        fields = payload
    return apply_flags({}, fields, strict=False)


def describe_preset(data: Mapping[str, Any]) -> dict[str, Any]:
    """A baseline as the page renders it: its id, its name and its two flags.

    ``id`` is read from the record envelope rather than from ``data``, because that is
    where the store keeps it. Reading it out of the payload returns ``None``, and a
    baseline whose id is ``None`` cannot be attached to a link - so the one number a
    caller needs in order to seed a link is the one number that would be missing.
    """

    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "fields": apply_flags({}, data.get("fields") or {}, strict=False),
        vocab.EFFECT_FIELD: vocab.EFFECT_VALUE,
        vocab.LIMITATION_FIELD: vocab.LIMITATION_VALUE,
    }


def project_preset(record: Mapping[str, Any]) -> dict[str, Any]:
    """The same projection, given the whole stored record.

    The store keeps the id in the envelope and the payload under ``data``, so a caller
    that has the record projects it through here rather than reaching into ``data`` for
    a key that is not there.
    """

    payload = dict(record.get("data") or {})
    payload["id"] = record.get("id")
    return describe_preset(payload)
