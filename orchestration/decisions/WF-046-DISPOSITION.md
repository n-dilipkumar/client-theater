Record why WF-046 was deleted rather than landed

The `wf046` agent did its work and escalated rather than deciding. Its summary:

- 13 files staged, nothing committed, nothing pushed
- Jev returned **uncertain at 0.60** against 0.39 for the alternative
- 36 ruff findings across those 13 files, including one real bug: `engine.py`
  exports `ThrottleEngine` in `__all__`, but the class is named `Engine`
- those 13 files were the only lint debt in the repository. The other 516
  backend files were clean.

Its finding about overlap is the part that decides it:

> Already in the product, from WF-040 (`dsr/partial_failures/`): 429 to
> throttled, 403 `REQUEST_LIMIT_EXCEEDED` to throttled, "locked" as a retryable
> class, backoff from 30s doubling capped at 30m, attempt bound 5, from the same
> researched sentence WF-046 step three implements. Also from WF-049
> (`dsr/integ_monitor/`): HubSpot `X-HubSpot-RateLimit-*` parsing and 429
> throttle classification.
>
> Genuinely new: a pre-emptive token bucket, `Retry-After` on an outbound
> retry, the HubSpot 423 two-second floor, Sforce-Limit-Info parsing,
> deterministic jitter, per-row idempotency keys.
>
> Partly a second copy, partly new. That split is why it landed at 0.60 against
> 0.39.

## Why the split is the answer

A branch that is roughly 60 percent a second copy of shipped behaviour and 40
percent new is worse than either alternative. Landing it puts two
implementations of one idea in the tree, and the 40 percent that is new has to
be unpicked from the copy before it is worth anything. That is not a mechanical
cleanup. It is a rewrite with a gauge attached.

The verdict was uncertain, and an uncertain verdict is neither permission to land
nor a reason to keep the branch. The evidence behind it is clear even where the
verdict is not:

- the retry half is already shipped, in two features
- landing it would make this the only lint debt in the repository
- `__all__` naming a class that does not exist is a defect the moment anything
  imports it

## Decision

**Deleted.** The genuinely new pieces are recorded below so they are not lost
with the branch. Each is mapped to the module that already owns that behaviour,
because that is where the change belongs.

## What is lost, and where it is written down

| Piece | Belongs in | Why there |
|---|---|---|
| HubSpot `X-HubSpot-RateLimit-*` parsing | `dsr/integ_monitor/` | already parses these headers, shipped in WF-049 |
| the 423 two-second floor | `dsr/integ_monitor/` | the same HubSpot limit model |
| `Sforce-Limit-Info` parsing | a Salesforce surface; `dsr/throttle/` if that package is ever created | the branch invented the package name |
| pre-emptive token bucket | `dsr/throttle/`, if created | new, and the one piece worth writing first |
| `Retry-After` on an outbound retry | `dsr/partial_failures/` | already classifies 429 and computes backoff |
| deterministic jitter | whichever module owns the backoff | the shipped backoff has no jitter |
| per-row idempotency keys | `dsr/partial_failures/` | same |

Nothing is lost. All of it is written above with its intended owner, which is
more useful than a branch nobody opens.

## The lint finding is worth keeping regardless

`__all__` exporting a name that does not exist is a defect that appears only when
something imports it, and a recovered branch is exactly where that hides. It is
recorded here because the branch is gone and the pattern is worth recognising: a
recovered module that exports a class it does not define.