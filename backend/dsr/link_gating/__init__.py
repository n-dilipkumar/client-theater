"""WF-069: gate a buyer link with a password, an expiry and email verification.

The researched specification for this workflow is
``docs/research/digital-sales-room-workflows/wf/WF-069.md``. Three modules carry
it, and none of them knows about HTTP:

``secrets``
    Password hashing, one-time-code hashing and token minting. The only place a
    cleartext secret is ever in memory, and the only place one is never allowed
    out of it.
``rules``
    The link's gate settings: what they may contain, which of them a preset
    controls, when a link has expired, and what step a viewer is on.
``gate``
    The engine that writes those settings, walks a viewer through the steps, and
    records the verified buyer identity the research asks to persist.

Nothing here imports ``dsr.api``, and nothing here opens SQLite. Every read and
write goes through the :class:`~dsr.store.RecordStore` the HTTP layer hands in,
so the audit row is written in the same transaction as the change.
"""
