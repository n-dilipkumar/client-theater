# A live security defect on main: the share-link secret is removable

Found by the forensics agent that was sent to audit five orphaned worktrees. It
was not looking for this. It measured main instead, because a port it was
auditing could only be judged against what main actually does.

## The defect

`PATCH /api/records/room/{id}` accepts an arbitrary JSON payload and merges it
into the record. Three consequences, all measured against `origin/main`:

    PATCH /api/records/room/{id}  {"link_secret": null, "name": "Renamed"}
      -> clears link_secret and still honours name

    PATCH /api/records/room/{id}  {"collaborator_token": "..."}
      -> writes the reserved collaborator_token field

    PATCH /api/records/room/{id}  {"branding": {"accent": "url(https://evil.example.net/x)"}}
      -> stores a hostile CSS value

Three probe tests were written, each asserting the opposite of main's own
expectation, so a pass proves the defect. Three of three passed.

## Why the share-link secret matters

The research states the share-link secret is a unique non-removable identifier
for security purposes. On main it is removable by any caller who can reach the
generic record route. A room whose share link has been stripped stops resolving,
and nothing in the audit trail distinguishes that from an ordinary rename.

## Why it is still open

`backend/dsr/features/wf_017_white_label.py` line 188 defines
`guard_room_payload()`, which delegates to `DomainService.strip_reserved` and
drops `domain`, `link_secret` and `collaborator_token`. On main it is called
from nowhere. Every occurrence in the tree:

    backend/dsr/features/wf_017_white_label.py:54   docstring reference
    backend/dsr/features/wf_017_white_label.py:68   docstring reference
    backend/dsr/features/wf_017_white_label.py:188  the definition
    backend/tests/test_wf017.py:292                  a test calls it directly
    backend/tests/test_wf017.py:306                  asserting it works in isolation

And `backend/dsr/api.py` line 193 reads:

    @app.patch("/api/records/{collection}/{record_id}", tags=["records"])
    def update_record(...):
        """Merge a partial payload into ``data`` and audit the change."""
        existing = store.get(record_id)
        ...
        return store.update(record_id, payload, ...)

No filter. The guard was written and exported, and the one-line wiring was left
for someone who was allowed to edit a shared file. Nobody was.

## main already knows, and has for a week

`backend/tests/test_wf017.py` line 309 marks the covering test
`@pytest.mark.xfail(strict=True)`, and its comment says the test exists so that
"the day the guard is wired in, this test fails and asks to be un-xfailed."

Two earlier attempts at this fix are recorded in
`orchestration/decisions/jev-audit.jsonl`:

    jev-20260927T064150-15976-10355  uncertain, confidence 0.20
    jev-20260927T064317-24548-97948  uncertain, confidence 0.70

Both were uncertain about how to slice the change, not about whether the defect
was real. The gate is now asking the sharper question, and the answer is that
the fix is a defect in main rather than a gap in an orphan, at 0.97.

## The fix, and why it needs an integrator

One filter call inside `update_record` before `store.update`. That file is on the
shared list, so the feature guard refuses any branch that edits it, which is
correct and is why this needs an integrator and a `platform-change` label rather
than a feature branch.

The hostile-CSS third of it is the same back door twice: `branding` is written
with no validation through the same route that skips `is_valid_colour`. The
feature's own route returns 422 for an identical value, so the generic route
demonstrably bypasses a check the product already has.

## What to do

Do not widen the scope. The third probe is a real finding but it is a
vulnerability class, not this defect, and bundling a CSS-validation change into
a secret-guard fix is how a one-line fix becomes an unreviewable diff.

Fix the secret. Wire `guard_room_payload` into `update_record`. Un-xfail
`test_the_generic_record_route_cannot_clear_the_link_secret`. Re-run the two
`collaborator_token` and `branding` probes and record whether they still pass.
Report the branding one either way, as its own item with its own severity, so
it is neither lost nor smuggled into this change.