# Digital Sales Room

A buyer-facing sales workspace built from a seller's own content, where every
change to data is recorded in the same transaction as the change. This file is
the glossary for that context: the words the project uses deliberately, and the
words it avoids.

## Landing and validation

**Landed**:
Present on `main`. Nothing less counts as landed.
_Avoid_: merged, shipped, live, done

**Release bar**:
The criteria a change must clear before it lands: the suite green, the contract
guard clean on a real diff, the design floor met, and a `pass` from the
release-bar judgment — or a recorded human override.

**Release-bar judgment**:
The typed decision, with a recorded probability, that gates one landing. Its
outcomes are `pass`, `fail`, and `uncertain`; it is not an opinion asked of a
chat model.

**Human override**:
A named person's recorded decision to land something the judgment did not clear.
An override is only real once it is recorded, and it names who decided and why.

**Uncertain**:
A judgment whose evidence was too close to decide. An escalation to a person,
never a soft pass and never a soft fail.
_Avoid_: maybe, borderline

**Claimed-ready**:
Built and reported as ready, but never verified against the bar. This is a
claim about a report, not a statement about the code.
_Avoid_: ready to merge, ready to verify and merge

**Design floor**:
The accessibility obligations a page must meet — touch targets, visible focus,
contrast, reduced-motion — verified by one scripted pass rather than by eye.

## Workflows and their bookkeeping

**Workflow**:
One countable unit of product behaviour in the program, which targets one
hundred of them.
_Avoid_: feature, ticket, capability

**Spec**:
A researched description of a workflow in the corpus. A workflow is only
genuinely specified when its spec cites primary sources; an unsourced spec is a
hypothesis.
_Avoid_: brief, requirement

**In flight**:
Built but not landed. Says where the code is, not whether it is good.

**Recovered build**:
A built-but-unlanded change found outside the repository's tracked refs, in a
workspace nothing guarded. Recovery precedes review: the work is made durable
before it is judged.

**Empty shell**:
A build workspace that never committed anything of its own, usually a retry that
never ran. Counting shells as work is how a stalled programme looks busy.

## The audited store

**Envelope**:
The fixed columns every record carries: `id`, `collection`, `room_id`,
`revision`, `created_at`, `updated_at`, `deleted_at`. It is the only vocabulary
the store fixes.

**Record**:
One stored thing: the envelope, plus arbitrary JSON payload whose fields are
discovered at runtime rather than declared.

**Audit row**:
The record of one change, written in the same transaction as the change itself,
so that the log cannot drift from the data.
