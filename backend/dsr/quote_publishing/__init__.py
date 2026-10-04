"""WF-094: publish and share a quote as a hosted link or an email.

The researched specification for this workflow is
``docs/research/digital-sales-room-workflows/wf/WF-094.md``, built from section 9
of ``docs/research/raw/quoting-proposals.md``. Three modules carry it, and none
of them knows about HTTP:

``vocabulary``
    The quote statuses, the unlock targets, the two size ceilings and the
    locale set, each traced to the sentence in the research that states it.
``rules``
    The pure decisions: whether a quote may be published, what its public URL
    resolves to, how large a PDF may be before the email drops it, and who a
    send may address.
``publishing``
    The engine that applies those decisions. It moves the quote's state, freezes
    its total, computes the read-only link properties, and records the share and
    email events.

Nothing here imports ``dsr.api`` and nothing here opens SQLite. Every read and
write goes through the :class:`~dsr.store.RecordStore` the HTTP layer hands in,
so the audit row is written in the same transaction as the change.

The state model was put to Jev before it was written (audit
``jev-20261004T215156-29936-16381``, verdict ``pass`` at confidence 1.00). The
question was where a published quote's public URL and frozen total should live.
The answer was the single quote record, because the research says
``hs_quote_link`` is a read-only property *of the quote object* that "cannot be
set through the API after publishing", and because ``hs_status`` is what
unlocking moves. Storing the link on a separate record would have answered a
question no reader asks, which is where that record's link belongs.
"""
