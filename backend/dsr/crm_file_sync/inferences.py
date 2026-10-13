"""Every judgement call WF-047 rests on, and how to change each one.

This is the module the design system of this repository asks for: the line
between what the research says and what this build chose, kept in one readable
list rather than scattered through comments. It is served at
``GET /api/wf-047/inferences``, so a page can render the decisions rather than a
reader reconstructing them from a diff.

Every entry carries four things:

``id``       a stable name, so a later change names what it is changing.
``topic``    the question the research left open.
``basis``    what the research actually says, quoted.
``value``    what this build chose.
``why``      the reasoning, including the alternative that was rejected.
``change_it`` the function or constant that carries the choice.
``blast_radius`` what a reader would have to re-check if the choice were wrong.

The gaps are entries too. ``unsourced-vendors-are-refused`` is the one a reviewer
should read first, because it is the boundary between a sourced workflow and a
guess.
"""

from __future__ import annotations

from typing import Any

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "unsourced-vendors-are-refused",
        "topic": "what this workflow does for the two systems the research could not source",
        "basis": (
            "The gaps block says: 'Salesforce Files (ContentVersion / ContentDocument / "
            "ContentDocumentLink) could not be sourced - every candidate resources-content* URL "
            "returned 404. Microsoft Dataverse file/image columns and annotation/attachment tables "
            "could not be sourced - the corresponding doc pages returned 404. Only HubSpot's Files "
            "API is fully documented for this workflow.' And on the Salesforce blob page: '(The blob "
            "page was readable only as a link index; no payload example is quoted here.)'"
        ),
        "value": {
            "file_capable_systems": ["hubspot"],
            "salesforce": "refused with the gap text, unless a deployment registers its own connector",
            "dataverse": "refused with the gap text, unless a deployment registers its own connector",
            "invented_payloads": "none",
        },
        "why": (
            "The issue's own instruction is 'Do not invent a payload.' A build that wrote a "
            "Salesforce sObject body from two sentences that name a collection operation and say a "
            "blob insert needs 'more values in the attributes map' without naming them would be "
            "reporting a vendor request it never sourced, and a tenant would find out at the vendor. "
            "A refusal that names the gap is more useful than a payload that is wrong: the connector "
            "interface is exactly the spec's own extensibility point, so a deployment with a real "
            "Salesforce connector registers one and nothing in this package changes."
        ),
        "change_it": "vocabulary.FILE_CAPABLE_SYSTEMS and vocabulary.UNSOURCED_SYSTEMS, and connectors.connector_for.",
        "blast_radius": "Which systems can receive a mirror at all, and what a tenant on the other two sees.",
    },
    {
        "id": "revision-detected-by-content-digest",
        "topic": "how this workflow decides that a room asset is a new revision",
        "basis": (
            "User-flow step four says: 'The room stores crm_file_id on the asset row, so re-uploads "
            "are skipped and revisions create a new file.' It does not say how a revision is detected."
        ),
        "value": {
            "detector": "sha256 digest of the asset bytes, stored on the asset row",
            "digest_of": "the bytes alone, not the filename",
            "unchanged_digest": "skipped_unchanged, with no vendor call",
            "changed_digest": "a new CRM file id; the old one stays where the CRM has it",
        },
        "why": (
            "Two alternatives were considered. The store's envelope revision counter was rejected "
            "because it measures writes, not content: renaming a document or moving it between "
            "folders bumps it, so a rep who tidied a filename would get a second copy in the CRM that "
            "nobody asked for. An explicit revision flag was rejected because a generated asset that "
            "forgets to set it is never pushed again, and that failure is silent until a "
            "reconciliation notices it. A digest of the bytes answers the only question the research "
            "actually asks, which is whether the file the CRM holds is the file the room holds now. "
            "The filename is deliberately excluded: a renamed document is the same document."
        ),
        "change_it": "assets.content_digest, assets.needs_mirror and assets.is_revision.",
        "blast_radius": "Which mirrors are skipped, which create a new CRM file, and what the Documents chip says.",
    },
    {
        "id": "reconcile-is-routed-not-scheduled",
        "topic": "how the reconciliation pass exists in a process with no background threads",
        "basis": (
            "The automations field says: 'Mirror is event-driven (asset created/updated) and can be a "
            "scheduled reconciliation pass'. The rule is quoted: 'find room assets with no crm_file_id "
            "and push them'. No cadence is given anywhere in the research."
        ),
        "value": {
            "mechanism": "a method, exposed as a route, that an external scheduler calls",
            "thread": "none. This package starts no thread and no timer.",
            "interval_seconds": 300,
            "before_due": "answered not_due, and no vendor is asked for anything",
            "force": "skips the floor, because a room that restarted has not been respecting it",
            "derivation_of_the_number": (
                "300 seconds is the same floor dsr.crm_backfill.plan derives for this product's other "
                "polled vendor loops (normalise_poll_interval). One number governing every polled "
                "vendor workflow here is worth more than a locally tuned one, and a reader who wants a "
                "different floor sets poll_interval_seconds on the connection."
            ),
        },
        "why": (
            "A thread that fires on a timer inside a web process is not a scheduler, it is a source of "
            "double-writes the moment two workers exist, and it cannot be paused by an operator. "
            "Making the pass a route means the scheduler decides, the interval is a number an operator "
            "can read, and a run that was not asked is not charged against a vendor limit the research "
            "describes as per-day."
        ),
        "change_it": "vocabulary.DEFAULT_POLL_INTERVAL_SECONDS and CrmFileSyncEngine._poll_interval.",
        "blast_radius": "Every reconciliation run, and the number of vendor calls this workflow makes.",
    },
    {
        "id": "event-driven-is-the-default-automation",
        "topic": "which of the research's two automation modes is the default",
        "basis": "'Mirror is event-driven (asset created/updated) and can be a scheduled reconciliation pass.'",
        "value": {
            "default": "event-driven. A registered asset with push_to_crm on is mirrored when the caller asks.",
            "reconciliation": "available as a route, never on by default",
            "connection_switch": "mirror_all mirrors every room document the connection is given",
        },
        "why": (
            "The research calls the second mode a reconciliation, and a reconciliation repairs a gap "
            "rather than being the primary path. A workflow that uploads every unmirrored asset on a "
            "timer surprises a tenant whose room holds documents the rep never meant to publish, and "
            "running both at once means the same asset can be pushed twice in the window where an "
            "event and a pass overlap. The idempotency rule then becomes the only thing standing "
            "between a tenant and duplicate files, which is a poor place to leave the guarantee."
        ),
        "change_it": "assets.push_enabled and CrmFileSyncEngine.reconcile.",
        "blast_radius": "When a document reaches the CRM without a rep asking for it.",
    },
    {
        "id": "folder-id-is-optional-per-connection",
        "topic": "how the CRM folder id is configured",
        "basis": (
            "'While a specific folder ID is not required at upload, it's recommended to upload files "
            "into a folder and not the root directory.' and 'Folder requirements at upload are subject "
            "to change.'"
        ),
        "value": {
            "folder_id": "optional on a connection, resolved at upload time",
            "no_folder": "uploads to the root and records folder_recommendation_followed: false",
            "rejected_folder": "a named folder_refused failure on the mirror row, never a silent fall back to the root",
        },
        "why": (
            "The vendor says a folder is not required, so refusing a connection without one would "
            "refuse a configuration the vendor accepts. The vendor also warns that folder requirements "
            "change, which is the reason a rejected folder is a named failure rather than a retry "
            "against the root: a silent fall back would hide the change that just happened, and the "
            "rep would see files in the root with nothing in the log to explain it."
        ),
        "change_it": "CrmFileSyncEngine.save_connection and connectors.SimulatedConnector.upload.",
        "blast_radius": "Where every mirrored file lands, and what a mirror row says about it.",
    },
    {
        "id": "attach-mode-defaults-to-note",
        "topic": "how the returned file id is written onto the record that owns it",
        "basis": (
            "The data_flow says 'room writes that id into a file-type property (or attaches it to a "
            "note)'. The vendor's files guide says 'Attach files to records using the notes APIs'. The "
            "properties guide says a file property 'Stores a file ID'."
        ),
        "value": {
            "modes": ["note", "file_property"],
            "default": "note",
            "file_property_requires": "a property name on the connection",
            "invented_note_body": "none. The note is recorded as an attachment, not as a vendor payload.",
        },
        "why": (
            "Both modes are sourced, so this build refuses to drop either: a tenant that created a "
            "file-type property gets use from it. The note is the default because the vendor's own "
            "guide names the notes APIs for attaching a file to a record and does not say the same "
            "about a property, and because a note appears on the record's timeline, which is the "
            "reps' fifth step. The note body is not written, because the research quotes no note "
            "payload and the same rule that keeps the Salesforce body out keeps this one out."
        ),
        "change_it": "vocabulary.ATTACH_MODES and CrmFileSyncEngine._attempt.",
        "blast_radius": "Where a rep sees the attachment, and whether a property the tenant created is used.",
    },
    {
        "id": "the-default-connector-is-simulated-and-says-so",
        "topic": "what the connector does when there is no CRM credential",
        "basis": (
            "This product holds no OAuth connection to a vendor org. The research's response content is "
            "thin: the data_flow says only that the CRM 'stores the file and returns a file id'."
        ),
        "value": {
            "default": "SimulatedConnector, holding vendor file state in the audited store",
            "collection": "crm_remote_file, prefixed crm_remote_ because it stands in for the vendor",
            "recorded_on_every_row": "simulated: true, and the connector's class name",
            "returns": "a file id and the folder the file landed in, and nothing else",
        },
        "why": (
            "The alternative - a build that only records what it would send - could assert idempotency "
            "in a comment and prove nothing. Simulating the effect means the second mirror of an "
            "unchanged asset really does find the file already there, really does create nothing, and "
            "really does write no remote row, which is a property a test can hold it to. The response "
            "carries only the id and the folder because those are the only two things the research "
            "quotes; a real HubSpot upload response has more fields and this simulation does not "
            "invent the rest."
        ),
        "change_it": "connectors.SimulatedConnector and connectors.connector_for.",
        "blast_radius": "Every mirror row's simulated flag, and whether a reviewer can mistake this for a vendor confirmation.",
    },
    {
        "id": "no-size-limit-because-none-is-sourced",
        "topic": "whether this workflow refuses a file that is too large",
        "basis": "'Uploaded files are subject to the limits and supported formats' - and the research quotes no number.",
        "value": {
            "size_limit": "none applied by this build",
            "what_is_refused": "a missing filename, a missing content type, and zero bytes",
        },
        "why": (
            "A limit this build chose would be a number the research does not carry, and a tenant would "
            "meet it as an unexplained refusal with no source to check it against. The vendor's own "
            "limits apply at the vendor, and a deployment's real connector is where a real number "
            "belongs."
        ),
        "change_it": "assets.normalise_asset.",
        "blast_radius": "What a caller may register, and where a tenant first discovers the vendor's real limit.",
    },
    {
        "id": "the-owner-record-is-read-from-the-connection",
        "topic": "how this workflow finds the CRM record the file id is written onto",
        "basis": (
            "The issue names the dependency on WF-042: it 'provisions the room's CRM deal, account and "
            "contact records. Without it, this workflow has no contact or deal record to own the "
            "attachment, so the returned file id has nowhere to be written and the rep never sees the "
            "file.'"
        ),
        "value": {
            "owner_object": "deal, contact or account, named on the connection",
            "crm_record_id": "carried on this feature's own connection row",
            "reads": "nothing from dsr.crm_integration",
        },
        "why": (
            "The dependency has to stay directional. Reading the owning id out of WF-042's tables would "
            "couple this feature to another feature's storage, and the brief forbids the domain module "
            "importing anything but the store in any case. The connection carries the id, so a swap of "
            "how WF-042 stores its records changes nothing here. A connection with no record is not a "
            "refusal at save time: it is a named mirror outcome, so the rep can see which document is "
            "waiting for a record rather than losing it at registration."
        ),
        "change_it": "CrmFileSyncEngine.save_connection and CrmFileSyncEngine._attempt.",
        "blast_radius": "Whether a returned file id has anywhere to be written, and what a tenant with no CRM record sees.",
    },
)


def describe() -> dict[str, Any]:
    """The whole register as data, for a page or a log line."""
    return {"count": len(INFERENCES), "inferences": [dict(entry) for entry in INFERENCES]}


__all__ = ["INFERENCES", "describe"]
