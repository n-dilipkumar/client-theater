"""WF-036: provision the sales-room engagement object and its fields into the CRM.

The engagement-object domain for the Digital Sales Room, kept in its own package so
no two features claim one path. The researched workflow is an *installer*: read the
CRM's live schema, work out what is missing, create only that, and record the
mapping from this room's object id to the CRM's own id so later syncs can write
into a first-class object instead of free text.

The module layout, and why each piece is separate:

``vocabulary``   the values the research fixes by name, served as data
``vendors``      the two researched APIs, as request-body and path builders
``manifest``     the versioned sales-room object descriptor, and what is wrong with it
``gateway``      the vendor side of the seam, held in this product's audited store
``diff``         the schema diff, pure: read state in, return a plan
``inferences``   every judgement call, named and served
``engine``       the façade the HTTP layer calls; owns the six collections

``diff.compute`` writes nothing. There is exactly one writer,
``engine.ProvisioningEngine.install``, and it acts on a plan computed from state
read at that moment - which is what makes the researched "dry-run diff view" a
read rather than a flag, and a flag is one boolean away from a bug.

Two rules run through everything here, both from the research's own words:

* **"creates only what is missing ... never destructively renaming or dropping
  existing fields."** So there is no update path and no delete path. A property
  that exists and differs is reported as a ``conflict``; a property the manifest
  no longer declares is reported as ``left_in_place``. A second install of the
  same manifest creates nothing and writes no create row to the audit log.
* **"The installer is idempotent by construction so it can be run on every deploy
  and on every new tenant without a human."** So the vendor's schema is *stored*
  rather than only described: the second run really does find what the first one
  created, which is the only way that sentence can be a test rather than a comment.

:func:`dsr.crm_provisioning.inferences.describe` is served at the feature's
``/inferences`` route, so a reviewer can see which parts are sourced and which are
this build's judgement without reading the diff.
"""

from __future__ import annotations

from dsr.crm_provisioning.diff import KEY_ACTIONS, OBJECT_ACTIONS, compute
from dsr.crm_provisioning.engine import (
    CONNECTIONS,
    KEYS,
    MANIFESTS,
    OBJECTS,
    PROPERTIES,
    RUNS,
    ProvisioningEngine,
)
from dsr.crm_provisioning.errors import (
    DuplicateManifestVersion,
    KeyConstraintError,
    ManifestError,
    PropertyConflict,
    ProvisioningError,
    UnsupportedVendor,
)
from dsr.crm_provisioning.gateway import (
    KEYS as REMOTE_KEYS,
    OBJECTS as REMOTE_OBJECTS,
    PROPERTIES as REMOTE_PROPERTIES,
    SimulatedCrm,
)
from dsr.crm_provisioning.inferences import INFERENCES, by_id, describe
from dsr.crm_provisioning.manifest import (
    check_key,
    findings_for,
    key_bytes,
    normalise_manifest,
    validate,
)
from dsr.crm_provisioning.vendors import (
    ADAPTERS,
    HUBSPOT,
    DATAVERSE,
    VendorAdapter,
    adapter_for,
    describe_adapter,
)
from dsr.crm_provisioning.vocabulary import (
    KEY_MAX_BYTES,
    KEY_MAX_COLUMNS,
    KEY_STATUSES,
    PROPERTY_ACTIONS,
    PROPERTY_TYPES,
    UNSUPPORTED_VENDORS,
    VENDORS,
    vocabulary,
)

__all__ = [
    "ADAPTERS",
    "CONNECTIONS",
    "DATAVERSE",
    "DuplicateManifestVersion",
    "HUBSPOT",
    "INFERENCES",
    "KEYS",
    "KEY_ACTIONS",
    "KEY_MAX_BYTES",
    "KEY_MAX_COLUMNS",
    "KEY_STATUSES",
    "KeyConstraintError",
    "MANIFESTS",
    "ManifestError",
    "OBJECTS",
    "OBJECT_ACTIONS",
    "PROPERTIES",
    "PROPERTY_ACTIONS",
    "PROPERTY_TYPES",
    "PropertyConflict",
    "ProvisioningEngine",
    "ProvisioningError",
    "REMOTE_KEYS",
    "REMOTE_OBJECTS",
    "REMOTE_PROPERTIES",
    "RUNS",
    "SimulatedCrm",
    "UNSUPPORTED_VENDORS",
    "UnsupportedVendor",
    "VENDORS",
    "VendorAdapter",
    "adapter_for",
    "by_id",
    "check_key",
    "compute",
    "describe",
    "describe_adapter",
    "findings_for",
    "key_bytes",
    "normalise_manifest",
    "validate",
    "vocabulary",
]
