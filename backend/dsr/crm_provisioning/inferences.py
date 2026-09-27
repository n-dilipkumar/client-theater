"""Every judgement call in this package, in one inspectable place.

The research for WF-036 is unusually specific about what it does *not* carry. Its
own gaps paragraph says: "The Salesforce half (Metadata API ``CustomObject`` /
``CustomField`` deploy) could not be sourced - the Metadata API guide returns a
cookie banner only. HubSpot and Dataverse carry this workflow in the evidence."
And the brief that this build works from asks for the sourced/inferred line to be
kept rather than blurred.

So this module is that line, as data:

* **named**, so it can be argued with by name;
* **traceable** - ``basis`` quotes what the research does and does not say;
* **bounded** - ``value`` is what this build chose, and ``change_it`` says how to
  change it without editing a function body;
* **visible** - :func:`describe` is served at the feature's ``/inferences`` route.

The entries with the most weight are the three that decide what a running system
does:

``simulated-vendor-state``  there is no CRM account, so the vendor's schema is
    held in this product's own audited store. That is what makes idempotency a
    property rather than a claim, and it is also the largest thing a reviewer
    would want to disagree with.
``blocking-vs-property-findings``  a manifest that is partly unreadable installs
    what it can and reports the rest, rather than refusing the whole run.
``key-length-must-be-declared``  the 900-byte limit is sourced; the widths it is
    measured in are not, so a string key column must declare its own length rather
    than inherit a default nobody sourced.

Nothing here is a migration, a typed column, or a new required field. It is a list
of ordinary JSON, exactly like everything else this package stores, and it is a
*record* of a judgement rather than a mechanism that enforces one.
"""

from __future__ import annotations

from typing import Any

from dsr.crm_provisioning.vocabulary import (
    KEY_MAX_BYTES,
    KEY_MAX_COLUMNS,
    KEY_STATUSES,
    PROPERTY_ACTIONS,
    UNSUPPORTED_VENDORS,
    VENDORS,
)

#: The sentence that governs the largest judgement in this package.
SOURCED_QUOTE = (
    "The Salesforce half (Metadata API CustomObject / CustomField deploy) could not be "
    "sourced - the Metadata API guide returns a cookie banner only. HubSpot and Dataverse "
    "carry this workflow in the evidence."
)

#: The sentence that governs idempotency, and the reason this package simulates
#: the vendor's schema rather than only recording the request it would send.
IDEMPOTENCY_QUOTE = (
    "Creation is idempotent: re-running the installer is a no-op for already-present "
    "properties. [...] The installer is idempotent by construction so it can be run on "
    "every deploy and on every new tenant without a human."
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "simulated-vendor-state",
        "topic": "where the CRM's live schema lives, and what a run actually does to it",
        "basis": (
            "The research describes real vendor calls and names real endpoints. It does "
            "not say this product has a CRM account to call: the workflow opens at an "
            "admin pressing Install on a connection that already exists. A build cannot "
            "create a HubSpot account, so the vendor's schema is held in this product's "
            "own audited store."
        ),
        "value": {
            "remote_collections": ["crm_remote_object", "crm_remote_property", "crm_remote_key"],
            "own_collections": [
                "crm_connection",
                "crm_manifest",
                "crm_object",
                "crm_property",
                "crm_sync_key",
                "crm_installation",
            ],
            "the_effect_is_stored": True,
            "opens_no_socket": True,
        },
        "why": (
            "The research's central rule is that a re-run is a no-op. A build that only "
            "recorded the requests it would send could assert that in a comment and hold "
            "nothing to it. Storing the effect means the second run really does find the "
            "properties already there, really does create nothing, and really does write "
            "no create row to the audit log - which is a property a test can enforce."
        ),
        "change_it": (
            "Give SimulatedCrm in backend/dsr/crm_provisioning/gateway.py real methods and "
            "pass an instance to ProvisioningEngine(crm=...). Every other module takes the "
            "vendor's answer as an argument and would not change."
        ),
        "blast_radius": (
            "The whole workflow. The recorded requests carry the researched paths and "
            "bodies, so a real transport would send exactly what the run record shows."
        ),
    },
    {
        "id": "unsupported-vendor-is-a-refusal",
        "topic": "what happens on a connection whose vendor the research could not source",
        "basis": (
            "The research names the gap and names the two vendors that carry the workflow. "
            "It does not say what a build should do about the third."
        ),
        "value": {
            "vendors_installable": list(VENDORS),
            "unsupported": dict(UNSUPPORTED_VENDORS),
            "status": 422,
            "code": "vendor_not_supported",
            "connection_is_still_registrable": True,
        },
        "why": (
            "A CI job that installs packages would otherwise report success for a tenant "
            "whose CRM was never touched, and nobody would find out until an engagement "
            "row failed to write. The connection is still registrable, so the account can "
            "be recorded; the refusal comes from the install a human actually runs, and "
            "its message quotes the gap."
        ),
        "change_it": (
            "Add a VendorAdapter to ADAPTERS in vendors.py and a name to VENDORS. That is "
            "the whole extension point, and it is deliberately a small one: a vendor with "
            "no cited provisioning page has no adapter."
        ),
        "blast_radius": "Only the install and plan routes for that vendor's connections.",
    },
    {
        "id": "blocking-vs-property-findings",
        "topic": "which manifest problems refuse the run and which are reported",
        "basis": (
            "The research requires the installer to be runnable 'on every deploy and on "
            "every new tenant without a human' and to create 'only what is missing'. It "
            "says nothing about a manifest that is partly unreadable."
        ),
        "value": {
            "blocking": "the object has no name, a property is declared twice",
            "property": "a type has no vendor mapping, or a vendor-required field is missing",
            "advisory": "no option set, a repeated option, a string with no length, a key this vendor cannot build",
            "skipped_is_never_silent": [
                "counted on the run record",
                "visible in the diff view",
                "the object is left marked complete=false",
            ],
        },
        "why": (
            "A deploy pipeline that fails on one field nobody has needed yet is a "
            "pipeline a human ends up running, which is the outcome the research rules "
            "out. Equally, a skipped field that nobody is told about is a broken object. "
            "So the two are separated, and a skip is always counted, always listed, and "
            "always leaves the object marked incomplete so a CI job can assert on it."
        ),
        "change_it": "_finding() severities and validate() in manifest.py.",
        "blast_radius": "Which installs are refused, and what a run record reports.",
    },
    {
        "id": "existing-property-is-never-changed",
        "topic": "what happens to a CRM property that exists but differs from the manifest",
        "basis": (
            "Sourced verbatim: 'The sales room creates only what is missing - the "
            "engagement object plus each property - never destructively renaming or "
            "dropping existing fields.'"
        ),
        "value": {
            "match": "unchanged, and no request is built",
            "differ": "conflict, reported on the run, never written",
            "narrower_column": "advisory, because no code path can widen one either",
            "no_update_path_exists": True,
        },
        "why": (
            "The sentence says 'never destructively', and the only way to honour it "
            "honestly is for no code path to reach an update at all. A tenant who widened "
            "a column by hand must not have this workflow narrow it back on the next "
            "deploy, and a tenant who hand-made a field the manifest also declares must "
            "get a row in the diff saying so rather than a silent overwrite."
        ),
        "change_it": (
            "compute() in diff.py. There is deliberately no update branch to remove, and "
            "add_property() in engine.py raises PropertyConflict rather than reconciling."
        ),
        "blast_radius": "Every install. This is the rule the whole diff exists to serve.",
    },
    {
        "id": "a-removed-field-is-left-in-place",
        "topic": "what happens to a CRM property the manifest no longer declares",
        "basis": (
            "The same sourced sentence. A manifest edit that removes a field is the most "
            "likely way to destroy a tenant's data by accident, and the research is "
            "explicit that the installer does not drop fields."
        ),
        "value": {
            "action": "left_in_place",
            "dropped": False,
            "renamed_field": "the new name is created; the old name is reported and kept",
            "reported_as": "left_in_place entries on the plan and the run",
        },
        "why": (
            "A deploy that removes a field from the manifest is exactly the event a person "
            "should see, not one a pipeline should resolve. The symmetric rename case is "
            "the same thing seen from the other side: the new name is genuinely missing "
            "and is created, so the diff names both halves and a human can decide whether "
            "the old column should ever go."
        ),
        "change_it": "compute() in diff.py. There is no delete branch to remove.",
        "blast_radius": "Every install against a manifest that has been edited.",
    },
    {
        "id": "key-length-must-be-declared",
        "topic": "how the 900-byte key limit is measured",
        "basis": (
            "Sourced: 'The system validates the key, including that the total key size "
            "doesn't violate SQL-based index constraints like 900 bytes per key and 16 "
            "columns per key.' The limit is the vendor's. The widths it is measured in are "
            "not recorded anywhere in this research."
        ),
        "value": {
            "max_bytes": KEY_MAX_BYTES,
            "max_columns": KEY_MAX_COLUMNS,
            "string": "the column's declared length",
            "number": 8,
            "datetime": 8,
            "bool": 1,
            "picklist": 4,
            "undeclared_string_length": "refused, naming the column",
        },
        "why": (
            "A guess here is wrong in both directions: too small and the installer refuses "
            "a key the vendor would have accepted, too large and it half-provisions an "
            "object and leaves a key request the vendor rejects minutes later on a new "
            "tenant. Refusing an undeclared length is the only answer that cannot be wrong, "
            "and the message names the column so the manifest is a one-line fix."
        ),
        "change_it": "key_bytes() in manifest.py.",
        "blast_radius": "Only manifests that declare a sync key.",
    },
    {
        "id": "key-index-advances-on-poll",
        "topic": "how far along a background index build is between two looks",
        "basis": (
            "Sourced: 'If a table has many existing records, creating an index can take a "
            "long time. To make the customization UI and solution import more responsive, "
            "create the index in a background process. ... EntityKeyMetadata.AsyncJob ... "
            "EntityKeyMetadata.EntityKeyIndexStatus ... Pending / In Progress / Active / "
            "Failed.' The four names and the progression are the vendor's. The cadence is "
            "not."
        ),
        "value": {
            "statuses": list(KEY_STATUSES),
            "polls_to_active": 2,
            "failed_at": "the first look, and it stays failed until reactivated",
            "reactivate": "re-arms to Pending with a fresh AsyncJob",
            "reactivate_on_active": "a no-op, because a working key is not half-provisioned",
        },
        "why": (
            "A synchronous build would have been the easy choice and would have hidden the "
            "one state the workflow exists to surface. The cadence is a simulation choice: "
            "there is no vendor here to build an index, so 'two looks' is chosen to make "
            "the intermediate state observable rather than to imitate a real duration."
        ),
        "change_it": "DEFAULT_INDEX_POLLS and advance_key() in gateway.py.",
        "blast_radius": "The key status a caller sees, and how many polls it takes to see Active.",
    },
    {
        "id": "key-requested-only-where-sourced",
        "topic": "which vendor gets an alternate key request",
        "basis": (
            "Sourced, and only for Dataverse: 'EntityKeyMetadata + CreateEntityKey to "
            "create the sync key on the new table', and both the alternate-keys page and "
            "the index-status quotes are Dataverse documentation. The research does not "
            "say how HubSpot's uniqueness is set."
        ),
        "value": {
            "dataverse": "CreateEntityKey with an EntityKeyMetadata body",
            "hubspot": "unsupported; the request is reported, not sent",
        },
        "why": (
            "The sibling workflow in the same research document creates a HubSpot unique "
            "property with hasUniqueValue, and it would have been easy to borrow. That is "
            "its sourced claim, not this workflow's, and borrowing it would put an "
            "unsourced rule into a build whose point is that it is not one. A manifest "
            "declaring a key against HubSpot is installed without one and says so."
        ),
        "change_it": "key_create and key_reactivate on the HUBSPOT adapter in vendors.py.",
        "blast_radius": "HubSpot connections whose manifest declares a sync key.",
    },
    {
        "id": "connection-registry-is-added-here",
        "topic": "the connection the installer provisions into",
        "basis": (
            "The researched flow starts at 'Admin runs Integrations -> <connection> -> "
            "Install integration package (or CI job calls the same endpoint)'. A "
            "connection is assumed to exist. This product has no connection registry, and "
            "the install has to name something."
        ),
        "value": {
            "record": "crm_connection",
            "fields": ["name", "vendor", "environment", "room_id", "enabled"],
            "bound_to_a_room": "optional, and explicit rather than inferred from the room's integrations list",
        },
        "why": (
            "A room record in this product carries a list of vendor names under "
            "'integrations', which is not the same thing as an account: two rooms can "
            "share one account, and one account can serve several rooms. Binding "
            "explicitly means the room-scoped reads can answer honestly about which "
            "connection installed what."
        ),
        "change_it": "register_connection() in engine.py, and the crm_connection collection.",
        "blast_radius": "Everything the install needs to name a target. Nothing else reads it.",
    },
    {
        "id": "provisioning-is-scoped-to-the-connection",
        "topic": "whether provisioning is per-room or per-connection",
        "basis": (
            "The research never mentions a room in this workflow. Its result is 'the room "
            "now has a first-class, CRM-native object to write engagement rows into', "
            "which is a statement about what a room can do afterwards, not about how the "
            "object is installed."
        ),
        "value": {
            "installed_against": "a connection",
            "room_scoped": "the reads under /rooms/{room_id}/",
            "reason": "one tenant-level object, and a room in the key would install it twice",
        },
        "why": (
            "The engagement object is one artefact per CRM account. Making the room part "
            "of the key would let two rooms in the same account install it twice under two "
            "ids, and every subsequent sync would have to pick one - which is the same trap "
            "the intent-signal registry hit when a registration key included the room. The "
            "room appears on the read, which is where the research actually puts it."
        ),
        "change_it": "The room_id argument on install(), and the /rooms/{room_id}/ routes.",
        "blast_radius": "Route shape, and nothing about what an install does.",
    },
    {
        "id": "manifest-versions-are-immutable",
        "topic": "what happens when a manifest version is registered again",
        "basis": (
            "The research calls the descriptor 'a sales-room package manifest (versioned)' "
            "and says a deployment ships its own. It does not say what a re-registration "
            "of the same version means."
        ),
        "value": {
            "same_id_same_version_same_body": "accepted, reported as unchanged",
            "same_id_same_version_different_body": "409",
            "new_version": "a new record, installable, with the old one still readable",
        },
        "why": (
            "An install that ran against version 1.0.0 must still be able to say later "
            "that the manifest it installed was this body. Overwriting a published version "
            "would make that sentence false, and a run record naming a version is the only "
            "way a reviewer can tell what a deployment actually shipped. Re-registering an "
            "unchanged manifest is accepted, because a pipeline that runs on every deploy "
            "must not fail on a file that has not moved."
        ),
        "change_it": "register_manifest() in engine.py.",
        "blast_radius": "Manifest registration, and nothing else.",
    },
    {
        "id": "the-room-object-id-defaults-to-the-manifest-id",
        "topic": "what the room-object half of the mapping is",
        "basis": (
            "The data flow ends with 'sales room records the mapping of room-object-id -> "
            "CRM-object-id for subsequent syncs', but the descriptor it names is 'name, "
            "labels, field types, option sets' - no id. So the room's own id for the "
            "object is something the room assigns."
        ),
        "value": {
            "declared_as": "room_object_id on the manifest",
            "default": "the manifest's own manifest_id",
        },
        "why": (
            "The default is the only id a manifest always has, and a mapping with a wrong "
            "room half is worse than a mapping with the obvious one. A deployment that has "
            "its own object registry declares room_object_id and is believed."
        ),
        "change_it": "normalise_manifest() in manifest.py.",
        "blast_radius": "The room_object_id column on every object and run record.",
    },
    {
        "id": "property-actions-are-a-fixed-vocabulary",
        "topic": "the set of decisions the diff can make about one property",
        "basis": (
            "Not sourced. The research says the installer creates only what is missing and "
            "never renames or drops; which buckets that splits into, and what each is "
            "called, is this build's."
        ),
        "value": {"actions": list(PROPERTY_ACTIONS)},
        "why": (
            "A client needs a closed set to render, and a closed set that matches the code "
            "means a bucket cannot be added without every renderer learning about it. The "
            "five are exactly the states the researched flow can be in."
        ),
        "change_it": "PROPERTY_ACTIONS in vocabulary.py, and compute() in diff.py.",
        "blast_radius": "The diff view's rendering and the run record's counts.",
    },
    {
        "id": "dry-run-stores-a-run-record",
        "topic": "whether a preview leaves a trace",
        "basis": (
            "The research lists 'sales-room package-installer surface with a dry-run diff "
            "view'. It does not say whether a preview is recorded."
        ),
        "value": {
            "writes": "one crm_installation row with dry_run=true and created=0",
            "never_writes": ["crm_object", "crm_property", "crm_sync_key", "crm_remote_*"],
            "outcome": "dry_run",
        },
        "why": (
            "The preview is a read of the vendor's live schema, and the interesting question "
            "about a preview is what it would have done on a given deploy. Recording the "
            "answer and nothing else means a pipeline that previews on every deploy leaves "
            "a history of what each deploy would have changed - and the guarantee that "
            "matters, that a preview created nothing, is checkable because the row says "
            "created=0 and the other collections are untouched."
        ),
        "change_it": "The dry_run branch in install() in engine.py.",
        "blast_radius": "One audit row per preview.",
    },
)


def by_id(inference_id: str) -> dict[str, Any] | None:
    for entry in INFERENCES:
        if entry["id"] == inference_id:
            return dict(entry)
    return None


def describe() -> dict[str, Any]:
    """The whole registry, alongside the half of the workflow that is sourced.

    Both halves in one payload on purpose. The point of this endpoint is that a
    reader can see where the line falls, and that means showing the researched
    vocabulary and the two sourced quotes next to the inferred behaviour.
    """
    return {
        "count": len(INFERENCES),
        "sourced_quotes": {"gap": SOURCED_QUOTE, "idempotency": IDEMPOTENCY_QUOTE},
        "sourced": {
            "vendors": list(VENDORS),
            "unsupported_vendors": dict(UNSUPPORTED_VENDORS),
            "key_statuses": list(KEY_STATUSES),
            "key_limits": {"max_bytes": KEY_MAX_BYTES, "max_columns": KEY_MAX_COLUMNS},
            "property_actions": list(PROPERTY_ACTIONS),
        },
        "inferences": [dict(entry) for entry in INFERENCES],
    }
