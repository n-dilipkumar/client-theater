"""The judgement calls WF-072 made, each recorded with the alternative it rejected.

The specification fixes the three controls and their AND and leaves several things open.
Each entry below is a place this build had to decide, and each records the option chosen,
the option rejected, and why. The register is served by the decisions route so a
reviewer reads the decision rather than reverse-engineering it from the code.
"""

from __future__ import annotations

from typing import Any

DECISIONS: dict[str, dict[str, Any]] = {
    "THREE_WAY_AND_FOR_ANY_DOWNLOAD": {
        "question": (
            "Is a single-item download gated by the room-level bulk setting, or only by the "
            "link and the item?"
        ),
        "left_open_by": (
            "The data flow says the viewer service 'checks all three before serving file "
            "bytes', which reads as every download, but the room setting is documented only "
            "as 'download every document as a zip', which reads as the bulk path alone."
        ),
        "options": {
            "three_way_and": (
                "Every download requires the room, the link and the item control to permit it."
            ),
            "bulk_only_room_gate": (
                "The room control gates the zip only; a single item needs the link and the "
                "item alone."
            ),
        },
        "chosen": "three_way_and",
        "rejected_because": (
            "The issue names this precisely: 'The AND is the feature, not three separate "
            "toggles', and the extension the specification quotes is decisive: 'Because "
            "download is a three-way AND, an open-source implementation can mirror it "
            "exactly.' The two-way reading would also leave the room control untested "
            "against a single fetch, which is where the research's own warning about "
            "'three separate toggles' bites."
        ),
        "cost_of_the_choice": (
            "A rep who wants a single download has to leave the room's bulk setting on. The "
            "page states the AND beside the controls rather than leaving the coupling to be "
            "discovered."
        ),
    },
    "READ_THE_ITEM_GRANT_AND_OWN_THE_TWO_POLICIES": {
        "question": (
            "Given WF-074 already owns a link's allow_download and the per-item can_download, "
            "what does this workflow own?"
        ),
        "left_open_by": (
            "The issue's What to build names the room and link controls as this workflow's, "
            "and separately says 'This issue reads and evaluates can_download; it does not "
            "own the permission set that grants it.' WF-074 merged first and already carries "
            "all three flags on its own rows."
        ),
        "options": {
            "own_room_and_link_read_items": (
                "Own the room policy and the link policy in wf072_* collections; read the "
                "per-item can_download from WF-074's permission rows read-only."
            ),
            "own_room_only_read_rest": (
                "Own only the room policy; read the link and item flags from WF-074."
            ),
            "own_all_three_duplicate": (
                "Store all three flags in wf072_* records, including a copy of can_download."
            ),
        },
        "chosen": "own_room_and_link_read_items",
        "rejected_because": (
            "Ownership follows the boundary and keeps the bit that is genuinely this "
            "workflow's. Copying can_download would duplicate the per-item permission set "
            "the issue says not to own and would let two sources of truth disagree; reading "
            "the link flag from WF-074 would make this workflow depend on another ticket's "
            "private row shape and ids and would give it no link write surface the issue "
            "names. Jev selected this option over both at confidence 0.98, audit "
            "jev-20261013T150724-2296-44663."
        ),
        "cost_of_the_choice": (
            "The per-item grant lives in another workflow's rows, so a reader has to know "
            "which collection holds it. The response carries the read source on every call "
            "so the boundary is visible rather than implied."
        ),
    },
    "REVOKED_ROWS_ARE_NOT_IN_FORCE": {
        "question": "Does a permission row with a revocation stamp still grant a download?",
        "left_open_by": (
            "The OpenAPI schema fixes can_download as a boolean on a PermissionEntry but says "
            "nothing about a row that was withdrawn."
        ),
        "options": {
            "revoked_is_out_of_force": (
                "A row with revoked_at set grants nothing; a live row with can_download false "
                "is a live grant that grants no download."
            ),
            "revoked_is_live": "Read can_download off any row, whatever its stamp.",
        },
        "chosen": "revoked_is_out_of_force",
        "rejected_because": (
            "An audit trail whose grants cannot be withdrawn is not a permission set. The "
            "revocation stamp is the only thing that distinguishes 'somebody revoked it' "
            "from 'nobody granted it', and a download refused for the wrong reason is a "
            "defect a rep cannot debug."
        ),
        "cost_of_the_choice": (
            "A revoked row and a missing row both deny. The response names which of the two "
            "it read, so the difference survives the refusal."
        ),
    },
    "A_BLOCKED_DOWNLOAD_WRITES_NO_VIEW_ROW": {
        "question": (
            "Does a refused download write the view row the specification says records a download?"
        ),
        "left_open_by": (
            "The specification says a 'successful file fetch writes a View row with "
            "downloaded_at and download_type'. It does not say whether a refused fetch is "
            "recorded."
        ),
        "options": {
            "success_only": "Write the row only when the bytes are released.",
            "record_refusals_too": (
                "Write a row for a refused fetch as well, with a null download."
            ),
        },
        "chosen": "success_only",
        "rejected_because": (
            "'Auditable' is the claim the specification makes and it rests on the View row "
            "meaning a download happened. A row written for a refusal would make the "
            "download log describe requests rather than downloads, and the issue says to "
            "'enforce that every successful fetch writes the row in the same transaction as "
            "the bytes being released' - it scopes the row to a successful fetch."
        ),
        "cost_of_the_choice": (
            "A rep cannot see how many downloads were attempted and refused. The refusal "
            "itself is still an audited read of the decision, and the limitation sentence "
            "says the download log holds the downloads that happened."
        ),
    },
    "DOWNLOAD_TYPES_ADD_A_ZIP_KIND": {
        "question": "What values can download_type hold?",
        "left_open_by": (
            "The OpenAPI schema fixes download_type as a nullable string and names no values. "
            "WF-075 derives 'pdf' and 'original' for the same field."
        ),
        "options": {
            "original_pdf_zip": "original, pdf, and zip for the room-wide escalation.",
            "original_pdf_only": "original and pdf, leaving the bulk kind unnamed.",
        },
        "chosen": "original_pdf_zip",
        "rejected_because": (
            "The room-wide escalation is documented as 'download every document as a zip', "
            "which is a real kind the other two do not name. Clamping a bulk fetch to "
            "'original' would record a zip as a single document, which is a false row."
        ),
        "cost_of_the_choice": (
            "The kind set is broader than WF-075's for the same field. The two live in "
            "different packages; both are derived, and the page renders either."
        ),
    },
}


def describe() -> list[dict[str, Any]]:
    """Every recorded decision, in a stable order."""
    return [{"id": key, **value} for key, value in DECISIONS.items()]


def describe_one(decision_id: str) -> dict[str, Any]:
    """One decision by id, or an empty mapping the HTTP layer turns into a 404."""
    found = DECISIONS.get(decision_id)
    if found is None:
        return {}
    return {"id": decision_id, **found}


def count() -> int:
    return len(DECISIONS)
