"""The readings this build took where WF-076's research is silent.

Every entry here is a point where the researched document does not say what to
do. The rule this module exists to enforce is the corpus's own: a workflow
marked as sourced is a specification, and a workflow marked as inferred is a
hypothesis - so the line has to be visible, not buried in the code.

Each entry carries four things a reviewer can act on:

``id``
    Stable, so a comment or a test can name the reading rather than paraphrase
    it.
``claim``
    What the research actually says, quoted. Where there is no quote, ``claim``
    says "the research is silent", which is the honest form of the same thing.
``reading``
    What this build does about it.
``basis``
    Why that reading, and what would have to be true for a different one to be
    better.
``change_it``
    The concrete lever, so the choice is data or a constant rather than a
    rewrite.
``blast_radius``
    What moves if a reviewer disagrees.

None of these are requirements the research states. Each is the reading it
supports best, and each is reachable from ``GET /api/wf-076/inferences`` so the
page can show them rather than hide them.
"""

from __future__ import annotations

from typing import Any

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "cascade-keeps-rows",
        "claim": (
            'Group delete "matches the dashboard\'s behavior and cannot be undone" and dataroom '
            'delete "is unrecoverable", but neither says the rows are destroyed.'
        ),
        "reading": (
            "Every revocation in this feature is a soft delete, including the two cascades. The "
            "records are marked deleted and stay queryable with include_deleted, and the cascade's "
            "own audit row carries the full before_state of every record it removed."
        ),
        "basis": (
            "The product guarantee is that the audit log is complete and that no write path can "
            "bypass it; a hard delete in the workflow whose subject is 'keep the audit row' would "
            "be the one place the guarantee did not hold. 'Cannot be undone' is honoured at the "
            "product level instead: there is no restore route for a revoked link, a deleted group "
            "or a purged room, and IRREVERSIBLE is reported on every such response. So a caller "
            "cannot undo it, while a reviewer can still read exactly what was removed and when."
        ),
        "change_it": "HARD_DELETE in dsr/revocation/__init__.py, and the bulk_delete(hard=...) call.",
        "blast_radius": "Every cascade. It is the only reason the purge is inspectable at all.",
    },
    {
        "id": "cascade-does-not-rename-slugs",
        "claim": (
            'The slug rename is documented only for the single-link delete: "If the link used a '
            'custom-domain slug, the slug is renamed so the original can be reused."'
        ),
        "reading": (
            "A single revoke renames the slug when the link has a custom domain. A group delete or "
            "a dataroom purge does not rename anything: the links are removed, so no live record "
            "holds the slug, and the slug each cascade freed is readable in that cascade's audit "
            "row under before_state."
        ),
        "basis": (
            "Renaming during a cascade needs an update on a row the same transaction is about to "
            "delete, and AuditedWriter has no delete, so the rename would be a second transaction "
            "that could land without the delete - leaving a live link whose slug had already been "
            "tombstoned. That is the wrong direction to fail in: access stays on, but its URL is "
            "gone. The research asks for the rename in exactly the case where it is safe."
        ),
        "change_it": "Revocation.revoke_link versus Revocation.delete_group, in engine.py.",
        "blast_radius": "The slug a cascade frees, and the audit row that records it.",
    },
    {
        "id": "membership-gates-resolution",
        "claim": (
            'The flow says "To stop one buyer, rep removes just that group membership", but never '
            "states the rule resolution applies."
        ),
        "reading": (
            "resolve(slug, viewer) resolves a group link only when the requester is a live member "
            "of that group. Removing a membership therefore stops that buyer on their next request "
            "without touching the link or anyone else's access."
        ),
        "basis": (
            "This is the only reading under which step 4 of the researched user flow has an effect. "
            "If membership did not gate resolution, removing one would be indistinguishable from "
            "doing nothing, and the research lists it as a way to stop an individual."
        ),
        "change_it": "Revocation.resolve, in engine.py.",
        "blast_radius": "Every group link. Room-wide links are ungated by design.",
    },
    {
        "id": "download-requires-view",
        "claim": (
            'The permissions endpoint is documented as "hide an item (view off, download off)" but '
            "the relationship between the two flags is not stated."
        ),
        "reading": (
            "download=True with view=False is refused as incoherent. Hiding an item means turning "
            "both flags off; turning one on is only meaningful when the other allows it."
        ),
        "basis": (
            "The documented state that hides an item sets both flags, and a document a buyer cannot "
            "see is not one they can download. Accepting the incoherent pair would let a caller "
            "believe they had hidden something while leaving a download right open, which is the "
            "failure this whole workflow exists to prevent."
        ),
        "change_it": "Revocation.set_permissions, in engine.py.",
        "blast_radius": "Only the incoherent combination. The documented pair is unaffected.",
    },
    {
        "id": "frozen-governs-structure-not-access",
        "claim": (
            'Freeze is documented three times, all structural: "Frozen datarooms refuse new '
            'attachments", repeated for detach and for move. It is never mentioned on a revoke.'
        ),
        "reading": (
            "A frozen dataroom refuses attach and detach. It can still have a link revoked, a group "
            "deleted, a member removed, permissions flipped and its access graph purged."
        ),
        "basis": (
            "The research enumerates the three operations a freeze refuses and revocation is not "
            "one of them. Reading the freeze as a general write lock would make the freeze a way to "
            "keep stale links alive, which is the opposite of what the workflow is for."
        ),
        "change_it": "FROZEN_REFUSALS in dsr/revocation/__init__.py.",
        "blast_radius": "Two routes. Everything else on a frozen room keeps working.",
    },
    {
        "id": "irreversible-actions-echo-their-target",
        "claim": (
            "The extensibility note asks the UI to gate the irreversible operations: group delete "
            '"cannot be undone", dataroom delete "is unrecoverable". No confirmation shape is given.'
        ),
        "reading": (
            "Both cascades require confirm to equal the id of the thing being destroyed - the group "
            "id for a group delete, the room id for a purge - and refuse with 400 otherwise."
        ),
        "basis": (
            "A gate the research asks for has to exist somewhere, and the API is where it can be "
            "enforced rather than merely rendered. Echoing the id means a confirmation dialog that "
            "hard-codes true cannot cause the delete, and a client that batch-calls cannot delete "
            "the wrong room by passing the same flag twice."
        ),
        "change_it": "The confirm check in Revocation.delete_group and Revocation.purge.",
        "blast_radius": "Two routes, and the frontend's two delete buttons.",
    },
    {
        "id": "document-team-defaults-to-the-dataroom",
        "claim": (
            'The attach endpoint requires that "the document must belong to the same team as the '
            'dataroom; cross-team attaches are refused", but the product has no team field on a '
            "document."
        ),
        "reading": (
            "A document's team is document.data.team when present. A document with no team is "
            "treated as belonging to the dataroom's own team, so the refusal applies only to a "
            "document that names a different team."
        ),
        "basis": (
            "Failing closed on a missing field would refuse every attach the demo dataset can "
            "express, and the core documents carry no team. Defaulting to same-team keeps the "
            "documented refusal exact - it still fires on a real mismatch - without inventing a "
            "team assignment for records that never had one."
        ),
        "change_it": "Revocation.attach's team comparison, in engine.py.",
        "blast_radius": "Attach only. The demo seeds one cross-team document to keep it reachable.",
    },
    {
        "id": "purge-stops-at-the-access-graph",
        "claim": (
            'Dataroom delete "cascades to every link and folder and is unrecoverable; documents '
            'stay in the team library." The research never says the room record itself goes.'
        ),
        "reading": (
            "A purge removes every revocation record in the room - links, groups, memberships, "
            "permissions and join rows - and leaves the core room record alone."
        ),
        "basis": (
            "The room row belongs to WF-001 and eleven other features read it; deleting it from "
            "here would be one feature reaching across the boundary the plugin host exists to keep. "
            "The researched outcome this workflow owns is the access graph, and documents surviving "
            "is asserted by test rather than assumed."
        ),
        "change_it": "Revocation.purge, in engine.py.",
        "blast_radius": "The purge route's scope. Documents and the room row are untouched either way.",
    },
    {
        "id": "a-room-flag-gates-the-freeze",
        "claim": "The research names a frozen dataroom but never says how one is frozen.",
        "reading": (
            "The room payload's own boolean frozen field, defaulting to False when absent."
        ),
        "basis": (
            "The freeze is a property of the dataroom, and a team setting it should not have to "
            "call a second feature to record it. Reading it from the core payload is what schema "
            "flexibility is for: the field is ordinary JSON, so any writer can set it and no "
            "migration is involved."
        ),
        "change_it": "Revocation.is_frozen, in engine.py.",
        "blast_radius": "Two routes. Every other room behaves as an ordinary unfrozen room.",
    },
)


def describe() -> dict[str, Any]:
    """The whole list, plus the sourced quotes it is answering."""
    return {
        "count": len(INFERENCES),
        "rule": (
            "Each entry is a point where the researched document for WF-076 does not say what to "
            "do. The claim is what it does say, the reading is what this build does about the gap, "
            "and change_it names the lever. A reviewer who disagrees should be able to change one "
            "constant rather than rewrite a route."
        ),
        "inferences": [dict(entry) for entry in INFERENCES],
    }


def by_id(entry_id: str) -> dict[str, Any] | None:
    """One reading by its id, or None."""
    for entry in INFERENCES:
        if entry["id"] == entry_id:
            return dict(entry)
    return None


__all__ = ["INFERENCES", "by_id", "describe"]
