"""CRM sync: push room events out to a CRM over webhooks and automations.

The public surface is :class:`CRMSync`. Everything else in the package is an
implementation detail of it. The HTTP surface is not in this package: the
feature host mounts ``dsr/features/wf016_crm_sync.py``, which owns the routes
under ``/api/wf-016`` and the mapping of :class:`CrmError` to a response.

Implemented from ``docs/research/digital-sales-room-workflows/wf/WF-016.md``,
ported from ``feature/WF-016-sync-room-events-to-the-crm-via-webhooks``.
Sourced behaviour and design inferences are marked in the modules that hold
them; the inferences are the retry policy, the HMAC signature, the delivery
envelope's field names, and the fact that an automation run resolves and logs
a CRM payload rather than calling a CRM API, for which the research explicitly
makes no endpoint claims.
"""

from __future__ import annotations

from dsr.crm.automations import AutomationError
from dsr.crm.delivery import DeliveryResult, Transport, UrllibTransport
from dsr.crm.errors import CrmError
from dsr.crm.subscriptions import SubscriptionError
from dsr.crm.sync import CRMSync, FieldRegistryError
from dsr.crm.vocabulary import VocabularyError

__all__ = [
    "CRMSync",
    "CrmError",
    "AutomationError",
    "SubscriptionError",
    "FieldRegistryError",
    "VocabularyError",
    "DeliveryResult",
    "Transport",
    "UrllibTransport",
]
