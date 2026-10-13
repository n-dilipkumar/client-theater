"""The pure rules WF-072 enforces: the three controls and the AND that decides a download.

Nothing in this module reads or writes. Every function is a pure transformation of its
arguments, so the whole workflow's decision-making can be exercised without a database and
without HTTP. The reads and the writes are in :mod:`dsr.view_only_access.engine`.

The three controls and the AND
------------------------------

The specification's data flow names the three controls and then the rule that composes
them: the viewer service "checks all three before serving file bytes". The CLI states the
same rule for one of them: an item's ``--download`` also needs ``--allow-download`` on the
link. So :func:`decide_download` is the AND of the three, and it is the function the tests
drive to prove that one control alone does not permit a download.

Why the AND is three-way and not two-way
----------------------------------------

The room-level ``bulkDownload`` could be read as gating bulk only, leaving a single item to
be gated by the link and the item alone. This build implements the three-way AND for any
download, because the specification writes it that way and the issue names it: "The AND is
the feature, not three separate toggles. The extension the issue quotes is decisive:
'Because download is a three-way AND, an open-source implementation can mirror it exactly.'
The two-way reading is recorded as rejected in :mod:`dsr.view_only_access.inferences`.

The defaults are the safe ones
------------------------------

:func:`coerce_flag` maps an absent field to ``False`` and never to a wildcard. The CLI
documents the default as "view-only", so a record written without the field permits no
download.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from dsr.view_only_access import vocabulary as vocab

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# Every error type below is declared here and raised by nothing else in the product. That is
# what makes it safe for the feature module to map them to responses: the host refuses a
# second feature registering a handler for the same type, and a handler for ValueError or
# PermissionError would intercept those exceptions across the whole product.


class ViewOnlyAccessError(Exception):
    """Base for every error this workflow raises.

    Carries an optional field-keyed ``errors`` map, so the HTTP layer can put each message
    beside the input that caused it instead of in one sentence the seller has to decode.
    """

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})


class PolicyRefused(ViewOnlyAccessError):
    """A policy payload this workflow will not accept. Sender error; the sender can fix it."""


class RoomNotFound(ViewOnlyAccessError):
    """No such room, so no policy can hang on it."""


class LinkNotFound(ViewOnlyAccessError):
    """No such governed link."""


class DownloadRefused(ViewOnlyAccessError):
    """The AND withheld the file.

    Not a malformed request and not a missing resource: the request is well formed, the
    link exists, and the answer is no. The decision that produced the refusal rides on the
    error so the response can name which of the three controls withheld the file.
    """

    def __init__(self, message: str, decision: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.decision: dict[str, Any] = dict(decision or {})


# --------------------------------------------------------------------------- #
# The flagged booleans
# --------------------------------------------------------------------------- #


def coerce_flag(value: Any, field: str) -> bool:
    """The boolean a control field carries, with the safe default.

    Accepts the JSON booleans, the CLI's ``on``/``off`` spelling, and their unambiguous
    string forms. An absent field is ``False``: the documented default is view-only, so
    missing must mean no download. Anything else is refused rather than guessed at, because
    a control that silently accepts a typo is a control that silently does nothing.
    """

    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value in (0, 1):
            return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in (vocab.FLAG_ON, "true", "1", "yes"):
            return True
        if token in (vocab.FLAG_OFF, "false", "0", "no", ""):
            return False
    raise PolicyRefused(
        f"{field} must be a boolean, or one of {vocab.FLAG_ON!r} / {vocab.FLAG_OFF!r}.",
        {
            field: (
                f"Send true or false, or the CLI spelling {vocab.FLAG_ON!r} / "
                f"{vocab.FLAG_OFF!r}. An absent field reads as off."
            )
        },
    )


def _validate_owned_fields(
    payload: Mapping[str, Any], fields: Sequence[str], error: str
) -> dict[str, Any]:
    """The owned control fields out of a payload, plus the rest carried through untouched.

    A room settings payload also holds ``conversations`` and ``agents`` (the
    specification's own "settings triple"), and those belong to other workflows. So the
    owned fields are validated and the others are stored as given: a schema-flexible store
    must not drop a field just because this workflow does not interpret it.
    """

    if not isinstance(payload, Mapping):
        raise PolicyRefused(error, {"body": "Send a JSON object."})
    out: dict[str, Any] = {}
    for field in fields:
        if field in payload:
            out[field] = coerce_flag(payload[field], field)
    for key, value in payload.items():
        if key not in out:
            out[key] = value
    return out


def validate_room_policy(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The room policy a caller may set.

    Only ``bulk_download`` is interpreted. A payload that carries ``conversations`` or
    ``agents`` keeps them: only bulk download is in scope here, and the other two settings
    are carried through rather than dropped.
    """

    return _validate_owned_fields(
        payload,
        vocab.ROOM_POLICY_FIELDS,
        "A room policy must be a JSON object; only bulk_download is interpreted.",
    )


def validate_link_policy(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The link policy a caller may set. Only ``allow_download`` is interpreted."""

    return _validate_owned_fields(
        payload,
        vocab.LINK_POLICY_FIELDS,
        "A link policy must be a JSON object; only allow_download is interpreted.",
    )


# --------------------------------------------------------------------------- #
# The per-item grant, read from WF-074's rows
# --------------------------------------------------------------------------- #


def grant_from_rows(records: Sequence[Mapping[str, Any]], item_id: str) -> dict[str, Any]:
    """This item's grant state out of WF-074's permission rows.

    The rows are WF-074's and this function reads them as data, never as an import. Three
    states are distinguished, because a grid that collapsed them would make the most
    important question unanswerable:

    * ``live`` - an in-force row says ``can_download``; the item may be downloaded.
    * ``revoked`` - a row exists but its ``revoked_at`` is set, so it is out of force.
    * ``no_row`` - nobody ever granted the item; the document default.

    A live row with ``can_download: false`` is a grant that grants no download: the field is
    the answer and its absence of force is what the boolean says, so it is ``live`` with
    ``can_download`` false rather than ``no_row``.
    """

    live: list[dict[str, Any]] = []
    revoked: list[dict[str, Any]] = []
    for record in records:
        data = dict(record.get("data") or {})
        if str(data.get(vocab.ITEM_ID) or "") != str(item_id):
            continue
        row = {
            "record_id": record.get("id"),
            "collection": record.get("collection"),
            vocab.CAN_DOWNLOAD: coerce_flag(data.get(vocab.CAN_DOWNLOAD), vocab.CAN_DOWNLOAD),
            vocab.REVOKED_AT: data.get(vocab.REVOKED_AT),
        }
        if data.get(vocab.REVOKED_AT):
            revoked.append(row)
        else:
            live.append(row)

    if live:
        # The most recent live row wins. The rows are returned newest-first by the store;
        # taking the first keeps the answer stable and matches "later changes apply
        # immediately, no re-sharing".
        chosen = live[0]
        return {
            vocab.ROW_PRESENT_FIELD: True,
            vocab.GRANT_FIELD: vocab.GRANT_LIVE,
            vocab.CAN_DOWNLOAD: chosen[vocab.CAN_DOWNLOAD],
            "record_id": chosen["record_id"],
            "collection": chosen["collection"],
        }
    if revoked:
        return {
            vocab.ROW_PRESENT_FIELD: True,
            vocab.GRANT_FIELD: vocab.GRANT_REVOKED,
            vocab.CAN_DOWNLOAD: False,
            "record_id": revoked[0]["record_id"],
            "collection": revoked[0]["collection"],
        }
    return {
        vocab.ROW_PRESENT_FIELD: False,
        vocab.GRANT_FIELD: vocab.GRANT_MISSING,
        vocab.CAN_DOWNLOAD: False,
        "record_id": None,
        "collection": None,
    }


# --------------------------------------------------------------------------- #
# The AND
# --------------------------------------------------------------------------- #


def decide_download(room_bulk: Any, link_allow: Any, item_can: Any) -> dict[str, Any]:
    """The three-way AND, as the specification states it.

    "The viewer service checks all three before serving file bytes." The verdict is
    ``download`` only when every gate permits; otherwise it is ``view_only`` and
    ``blocked_by`` names the gates that did not. Each gate is reported separately so a page
    can say which control withheld the file rather than only that it was withheld.
    """

    gates = {
        vocab.GATE_ROOM: coerce_flag(room_bulk, vocab.BULK_DOWNLOAD),
        vocab.GATE_LINK: coerce_flag(link_allow, vocab.ALLOW_DOWNLOAD),
        vocab.GATE_ITEM: coerce_flag(item_can, vocab.CAN_DOWNLOAD),
    }
    blocked = [name for name in vocab.GATES if not gates[name]]
    permitted = not blocked
    return {
        vocab.DOWNLOAD_FIELD: permitted,
        vocab.STATE_FIELD: vocab.STATE_DOWNLOAD if permitted else vocab.STATE_VIEW_ONLY,
        vocab.GATES_FIELD: gates,
        vocab.BLOCKED_BY_FIELD: blocked,
        vocab.REASON_FIELD: _decision_reason(blocked),
    }


def _decision_reason(blocked: Sequence[str]) -> str:
    if not blocked:
        return "All three controls permit this download."
    names = ", ".join(vocab.GATE_LABELS[name] for name in blocked)
    if len(blocked) == 1:
        return f"{names} did not permit this download."
    return f"{names} did not permit this download."


def decide_bulk(
    room_bulk: Any, link_allow: Any, items: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    """The room-wide escalation: download every document as a zip.

    A bulk download is gated by the room and the link controls, and every item it would
    carry has to permit download too. So the zip is permitted only when the room's
    ``bulk_download`` is on, the link's ``allow_download`` is on, and no item in the set is
    withheld. ``blocked_items`` names the ones that were, so a page can say which document
    kept the zip from being built rather than only that it was not.
    """

    room = coerce_flag(room_bulk, vocab.BULK_DOWNLOAD)
    link = coerce_flag(link_allow, vocab.ALLOW_DOWNLOAD)
    blocked_items = [
        str(item.get(vocab.ITEM_ID) or "")
        for item in items
        if not coerce_flag(item.get(vocab.CAN_DOWNLOAD), vocab.CAN_DOWNLOAD)
    ]
    gates = {
        vocab.GATE_ROOM: room,
        vocab.GATE_LINK: link,
        vocab.GATE_ITEM: not blocked_items,
    }
    blocked = [name for name in vocab.GATES if not gates[name]]
    permitted = not blocked
    return {
        vocab.DOWNLOAD_FIELD: permitted,
        vocab.STATE_FIELD: vocab.STATE_DOWNLOAD if permitted else vocab.STATE_VIEW_ONLY,
        vocab.GATES_FIELD: gates,
        vocab.BLOCKED_BY_FIELD: blocked,
        "blocked_items": blocked_items,
        vocab.REASON_FIELD: _decision_reason(blocked),
    }


def coerce_download_type(value: Any) -> str:
    """The download kind, clamped to the derived set.

    A kind outside :data:`~dsr.view_only_access.vocabulary.DOWNLOAD_TYPES` is refused
    rather than stored, because a ``download_type`` the page cannot name would render a real
    download as a blank cell.
    """

    if value is None or value == "":
        return vocab.DOWNLOAD_TYPE_ORIGINAL
    token = str(value).strip().lower()
    if token not in vocab.DOWNLOAD_TYPES:
        raise PolicyRefused(
            f"download_type must be one of {', '.join(vocab.DOWNLOAD_TYPES)}.",
            {
                vocab.DOWNLOAD_TYPE: (
                    f"Send one of {', '.join(vocab.DOWNLOAD_TYPES)}; got {value!r}."
                )
            },
        )
    return token
