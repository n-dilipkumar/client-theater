WF-046: land the throttle package once, and retire the duplicate

Audit: `jev-20261004T023057-22468-57435`, `land_the_package_once_and_reconcile_the_other_side`
at confidence 0.93, margin 0.95 over the runner-up. `follow_the_issue` scored 0.00.

## The conflict this settles

Two merged records disagreed. `WF-046-DISPOSITION.md` recorded that the recovered
throttle branch was deleted as a second copy of shipped behaviour. Issue #190
instructed an agent to build `dsr/throttle/` as its own plugin. I created the
issues after merging the disposition and did not check them against it, so the
contradiction was mine.

Jev was given both records, the measured overlap, the six pieces that are
genuinely new, and the cost of each error. It chose the option that resolves
the duplication rather than picking a side of it.

## What to build

**One** throttle package, at `backend/dsr/throttle/`, owning rate limiting.

The pre-emptive token bucket, the HubSpot 423 two-second floor,
`Sforce-Limit-Info` parsing, `Retry-After` honoured on an outbound retry,
deterministic jitter, and per-row idempotency keys all belong here, because this
is the module whose subject is rate limiting.

## What to retire

`dsr/partial_failures` currently classifies 429 as throttled and 403
`REQUEST_LIMIT_EXCEEDED` as throttled, and computes backoff from 30s doubling to
a 30 minute cap with an attempt bound of 5. That logic must delegate to
`dsr/throttle` rather than keep its own copy.

`dsr/integ_monitor` parses `X-HubSpot-RateLimit-*` headers and classifies 429.
It may keep its header parsing, which is HubSpot-specific, but the classification
and any backoff it owns come from the throttle module.

The audit log records the shape of a bug fix here, so changing the retry
behaviour is the riskiest thing in this workflow. **Its tests must still pass
after the refactor, unchanged in count and meaning.** If a test has to be edited
to accommodate the move, stop and report instead: that means the behaviour
changed, which is not what this issue asks for.

## Why not simply close the issue

Closing it would have delivered no pre-emptive token bucket at all, and the
`Retry-After` header and idempotency keys are behaviours no shipped code has.
The disposition's objection was duplication. Removing the duplication resolves
the objection and keeps the new behaviour.

## Scope

- `backend/dsr/throttle/` new
- `backend/dsr/partial_failures/` delegate, do not duplicate
- `backend/dsr/integ_monitor/` delegate classification, keep header parsing
- `backend/dsr/features/wf046_throttle_and_retry_under_vendor_api_ra.py`
- `backend/tests/test_wf046.py` and `test_wf046_http.py`
- `frontend/src/features/wf-046-throttle-and-retry-under-vendor-api-r/`