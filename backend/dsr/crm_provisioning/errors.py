"""One error hierarchy for the CRM provisioning package.

Every refusal this package makes is either the caller's mistake or a fact about
the manifest the caller sent, so the types share a base and the feature module
registers a single handler for it. Anything that is *not* a
:class:`ProvisioningError` is a bug and must propagate.

``code`` and ``status`` ride on the exception rather than being decided inside the
handler. A manifest that names no object and a manifest that asks for a vendor
the research could not source are both this package's domain errors, but one is
the caller's typo and the other is a gap in the evidence, and a handler that
answered 400 for both would be lying about the second. FastAPI only accepts
exception handlers on the app object, so the feature module exports this mapping
as ``EXCEPTION_HANDLERS``; two features may not map the same type, which is why
the whole hierarchy hangs off one base class.

``RecordNotFound`` is deliberately *not* in this hierarchy. The core app already
maps it to 404, and two handlers for one type is a collision the plugin host
refuses, so a lookup for a connection, manifest, object or key that does not
exist raises the core type and the feature claims nothing.
"""

from __future__ import annotations


class ProvisioningError(ValueError):
    """A provisioning request cannot be honoured as written.

    A ``ValueError`` because every subclass is caused by something the caller
    sent. Nothing in this package raises for a fault of its own.
    """

    code = "provisioning_error"
    status = 400


class ManifestError(ProvisioningError):
    """A manifest cannot be read as a sales-room object descriptor."""

    code = "manifest_invalid"


class DuplicateManifestVersion(ManifestError):
    """This manifest version is already on file with a different body.

    409 rather than 400: the request is well formed, and what it conflicts with
    is state that already exists. The research calls the descriptor "a
    sales-room package manifest (versioned)", so a version is a published
    artefact rather than a draft - an install that ran against version 1.0.0
    must be able to say later that the manifest it installed was version 1.0.0
    of *this* body, and overwriting it would make that sentence false.
    """

    code = "manifest_version_already_registered"
    status = 409


class UnsupportedVendor(ProvisioningError):
    """The connection's vendor is not one this workflow has evidence for.

    The research states the gap in its own words: "The Salesforce half
    (Metadata API ``CustomObject`` / ``CustomField`` deploy) could not be
    sourced - the Metadata API guide returns a cookie banner only. HubSpot and
    Dataverse carry this workflow in the evidence." A vendor outside that set is
    therefore refused with 422 and a pointer, rather than being silently
    skipped: a CI job that installs packages would otherwise report success for
    a tenant whose CRM was never touched.
    """

    code = "vendor_not_supported"
    status = 422


class KeyConstraintError(ProvisioningError):
    """The declared sync key violates a limit the vendor enforces.

    "The system validates the key, including that the total key size doesn't
    violate SQL-based index constraints like 900 bytes per key and 16 columns
    per key." An installer that runs on every deploy without a human has to
    refuse an impossible key itself, or it half-provisions an object and leaves
    a key request that the vendor will reject minutes later.
    """

    code = "sync_key_rejected"
    status = 422


class PropertyConflict(ProvisioningError):
    """A property already exists on the object and this request would change it.

    "The sales room creates only what is missing - the engagement object plus
    each property - never destructively renaming or dropping existing fields."
    There is no code path in this package that updates a property, so this
    error can only be raised by the one route that accepts a property body
    against an installed object, and it exists to make the refusal explicit
    rather than to be caught and ignored.
    """

    code = "property_already_exists"
    status = 409
