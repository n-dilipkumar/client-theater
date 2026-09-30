# The design floor is a scripted CI gate, not a manual review

`MASTER.md`'s obligations — 44px touch targets, visible focus, a text label
beside every icon, no emoji as icons, `prefers-reduced-motion` respected — are
checked by one script that runs in CI and fails the build. They are not
hand-audited, and they are not a warning.

A check that only reports is a check that decays. `check_feature_diff.py` passed
vacuously on an uncommitted branch, printing `OK: 0 changed file(s), none
shared`, and a gate record then quoted that string as proof of contract
compliance. The design floor was heading the same way: measured by whoever
remembered, on pages nobody had opened.

**Considered options.** Auditing all pages by hand, and relying on the build to
catch regressions. Rejected on both counts: a manual pass does not survive
contact with a hundred pages, and compiling says nothing about whether anyone
can tap a button.

**Consequences.** The script keeps a per-feature allowlist so one legacy page
cannot block an unrelated feature. The allowlist starts empty, and every entry
in it is visible debt. Adding to it is a deliberate act, which is why it is
worth noticing when it happens.

**Outcome.** The first run of the job was red: 14 findings across the 43 shipped
pages. Reading each before changing anything showed the checker was wrong about
six of them — a static centring offset reported as motion, a 2px chart column
reported as an undersized target, and six features that replaced the global
focus outline with a ring, which is the standard accessible substitution. The
other eight were real and were fixed rather than allowlisted. Forty-four pages
now check clean with an empty allowlist.

The temptation was to allowlist the six false positives to get green in one
move. That is what an allowlist invites people to do when they are tired, and it
would have left the next feature author inheriting six lies. Fixing the checker
costs one more commit and keeps the check trustworthy.
