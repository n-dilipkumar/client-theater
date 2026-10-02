#!/usr/bin/env python3
"""Check the design floor over every feature page, and fail when a page misses it.

The floor is written in design-system/digital-sales-room/MASTER.md and is now a
gate rather than a habit, per docs/adr/0002-design-floor-is-a-ci-gate.md. It is
checked by one script so that it survives a hundred pages; it is not checked by
a person reading diffs.

What is mechanically checkable from source:

  emoji-as-icon    an emoji used where an icon belongs
  motion-not-gated vestibular motion (translate/rotate/scale/spin) on a page
                   that never consults prefers-reduced-motion
  focus-suppressed a page that removes the global :focus-visible outline
  tap-target-under-44px  an explicit height or width below 44px

What is NOT checkable here, and is deliberately not claimed: contrast ratios
(needs computed styles from a render), responsive breakpoints at 375/768/1024/
1440, occlusion behind a fixed navbar, and whether a control is actually
reachable by keyboard. Those need a browser, and the release bar lists them as
unverified rather than quietly passing.

    python tools/check_design_floor.py                 # every feature page
    python tools/check_design_floor.py --feature wf-033
    python tools/check_design_floor.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "src" / "features"
ALLOWLIST = Path(__file__).resolve().parent / "design_floor_allowlist.json"

# The floor in MASTER.md, as source patterns. Each rule is (id, severity, why).
RULES = (
    (
        "emoji-as-icon",
        "fail",
        "Emoji used as an icon. MASTER.md: use SVG.",
    ),
    (
        "motion-not-gated",
        "fail",
        "Vestibular motion (translate/rotate/scale/spin) with no reduced-motion opt-out.",
    ),
    (
        "focus-suppressed",
        "fail",
        "Removes the global :focus-visible outline with no ring, shadow or border to replace it.",
    ),
    (
        "tap-target-under-44px",
        "warn",
        "Explicit height or width below the 44px minimum touch target.",
    ),
)

# Emoji are pictographs, not text symbols. The arrow/dingbat blocks
# (U+2190-21FF, U+2600-27BF) are excluded on purpose: a "->" between two fields
# is typography, and flagging it is the kind of noise that trains people to
# ignore the check.
EMOJI = re.compile(
    "[\U0001f300-\U0001f5ff\U0001f600-\U0001f64f\U0001f680-\U0001f6ff"
    r"\U0001f700-\U0001f77f\U0001f780-\U0001f7ff\U0001f800-\U0001f8ff"
    r"\U0001f900-\U0001f9ff\U0001fa00-\U0001faff\U00002600-\U000026ff"
    r"\U00002700-\U000027bf\U0000fe0f]"
)

# The floor's focus rule is satisfied globally: frontend/src/index.css sets a
# :focus-visible outline for every interactive element, which is the correct
# place for it. So the finding is not "this feature has no focus class" -- it is
# "this feature switches the global focus state off".
FOCUS_SUPPRESSED = re.compile(
    r"focus:outline-none|outline-none|focus:ring-0|focus-visible:outline-none"
)

# Classes that imply "this is interactive".
INTERACTIVE = re.compile(
    r"\bonClick\b|\bonKeyDown\b|\bonKeyUp\b|\bonSubmit\b|<button\b|<a\b\b|\bButton\b|\bLink\b"
)

# Focus styling: Tailwind focus/focus-visible variants, or a :focus rule.
FOCUS_STYLE = re.compile(
    r"focus:ring|focus-visible:|focus:border|:focus\b|outline-(none|ring|offset)"
)

# The floor's focus rule is satisfied globally: frontend/src/index.css sets a
# :focus-visible outline on every interactive element, which is the right place
# for it. Suppressing that outline is only a defect when nothing replaces it.
# `focus-visible:outline-none` next to `focus-visible:ring-2` is the standard
# accessible substitution -- the ring is the visible focus state, and a doubled
# outline around a rounded control reads worse. Only a bare suppression with no
# replacement is reported.
FOCUS_RING = re.compile(
    r"focus-visible:ring-|focus:ring-|focus-visible:shadow-|focus:shadow-|ring-\d"
)
FOCUS_SUPPRESSED = re.compile(r"focus:outline-none|focus-visible:outline-none|outline-none")

# An animation or transition that moves things. Class-based or inline.
MOTION = re.compile(
    r"\banimate-\S+|\btransition-\S+|\bmotion-safe:|\bduration-\d+|\banimate\b"
    r"|\btransition\b|@keyframes|requestAnimationFrame"
)
REDUCED_MOTION = re.compile(r"prefers-reduced-motion|motion-reduce|motion-safe")

# Not every transition is an accessibility hazard. Animating a bar's width or a
# colour on hover is not vestibular motion, and gating it on reduced-motion
# would be noise. The hazards are the ones that move a large area, zoom, slide,
# or spin: those are what makes someone ill.
#
# A translate used as a static centring offset is not motion at all, because it
# never changes: `-translate-y-1/2` puts a search icon in the middle of its
# input and stays there. It is removed from the pattern rather than allowed
# through by hand, so the exemption cannot drift.
STATIC_CENTRE_OFFSET = re.compile(r"-\s*translate-[xy]-1/2")
HAZARDOUS_MOTION = re.compile(
    r"translate-[xy]-|rotate-|scale-[xy]|zoom-"
    r"|animate-(?:spin|ping|pulse|bounce|wobble|accordion|elastic)"
    r"|parallax|slide-in|fly-in"
)
# Tailwind's named height scale. Anything at or above h-11 (2.75rem = 44px) is
# compliant, so only the steps below it are findings.
TAP_OK_SCALE = re.compile(r"\b(?:min-)?(?:h|w)-(?:11|12|14|16|20|24|28|32|40|48|56|64|72|80|96)\b")
TAP_SMALL_SCALE = re.compile(r"\b(?:min-)?(?:h|w)-\[(\d+)px\]")
# A decorative element is not a tap target. A 2px-wide chart column and a 1px
# divider are sized to be seen, not to be hit, and reporting them as
# undersized targets is the kind of noise that trains people to ignore a check.
DECORATIVE_SIZE = re.compile(r"\b(?:min-)?[hw]-\[(?:0|1|2|3|4)px\]")


SMALL_PX = 44


def feature_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        (p for p in root.iterdir() if p.is_dir() and any(p.glob("index.*"))), key=lambda p: p.name
    )


def check_feature(feat: Path) -> list[dict]:
    findings: list[dict] = []
    for src in sorted(feat.glob("*.jsx")) + sorted(feat.glob("*.js")):
        text = src.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped:
                continue

            for match in EMOJI.finditer(line):
                findings.append(
                    {
                        "rule": "emoji-as-icon",
                        "severity": "fail",
                        "file": f"{feat.name}/{src.name}",
                        "line": i,
                        "detail": f"emoji {match.group(0)!r}",
                    }
                )

            # A static centring offset never changes, so it is not motion.
            stripped_of_offsets = STATIC_CENTRE_OFFSET.sub(" ", line)
            if HAZARDOUS_MOTION.search(stripped_of_offsets) and not REDUCED_MOTION.search(line):
                # Only flag a motion line inside a feature that never mentions
                # reduced motion anywhere; a file-level opt-in is enough.
                if not REDUCED_MOTION.search(text):
                    findings.append(
                        {
                            "rule": "motion-not-gated",
                            "severity": "fail",
                            "file": f"{feat.name}/{src.name}",
                            "line": i,
                            "detail": stripped[:90],
                        }
                    )

            # Only an explicit under-44px value is a finding, and a decorative
            # element is not a target at all: a 2px chart column and a 1px
            # divider are sized to be seen.
            for match in TAP_SMALL_SCALE.finditer(line):
                if DECORATIVE_SIZE.search(match.group(0)):
                    continue
                value = next((g for g in match.groups() if g), None)
                if value and int(value) < SMALL_PX:
                    findings.append(
                        {
                            "rule": "tap-target-under-44px",
                            "severity": "warn",
                            "file": f"{feat.name}/{src.name}",
                            "line": i,
                            "detail": f"{value}px in {match.group(0)!r}",
                        }
                    )

        # Suppressing the global focus outline is a finding only where nothing
        # replaces it. A line that drops the outline and adds a ring is the
        # accessible substitution, so the two are read together.
        for i, ln in enumerate(lines, 1):
            if not FOCUS_SUPPRESSED.search(ln):
                continue
            if FOCUS_RING.search(ln):
                continue
            findings.append(
                {
                    "rule": "focus-suppressed",
                    "severity": "fail",
                    "file": feat.name,
                    "line": i,
                    "detail": "removes the global focus-visible outline with nothing in its place",
                }
            )
    return findings


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--feature", help="check one feature directory by name")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--fail-on-warn", action="store_true", help="treat warnings as failures")
    args = parser.parse_args(argv)

    if args.feature:
        targets = [FRONTEND / args.feature]
        if not targets[0].is_dir():
            print(f"No such feature: {targets[0]}", file=sys.stderr)
            return 2
    else:
        targets = feature_dirs(FRONTEND)

    if not targets:
        print(f"No feature pages found under {FRONTEND}", file=sys.stderr)
        return 2

    allow = set()
    if ALLOWLIST.is_file():
        allow = set(json.loads(ALLOWLIST.read_text(encoding="utf-8")))

    results: dict[str, list[dict]] = {}
    for feat in targets:
        if feat.name in allow:
            continue
        found = check_feature(feat)
        if found:
            results[feat.name] = found

    failures = {k: [f for f in v if f["severity"] == "fail"] for k, v in results.items()}
    failures = {k: v for k, v in failures.items() if v}
    warns = {k: [f for f in v if f["severity"] == "warn"] for k, v in results.items()}
    warns = {k: v for k, v in warns.items() if v}

    if args.json:
        print(
            json.dumps({"checked": len(targets), "failures": failures, "warnings": warns}, indent=2)
        )
    else:
        checked = len([t for t in targets if t.name not in allow])
        print(
            f"design floor: {checked} feature page(s) checked, {len(failures)} failing, {len(warns)} with warnings"
        )
        for name, items in failures.items():
            print(f"\nFAIL {name}")
            for f in items:
                loc = f"line {f['line']}" if f["line"] else "file"
                print(f"  {f['rule']}: {loc}: {f['detail']}")
        for name, items in warns.items():
            print(f"\nWARN {name}")
            for f in items:
                print(f"  {f['rule']}: line {f['line']}: {f['detail']}")
        if not failures and not warns:
            print("OK: every checked feature page meets the mechanically checkable floor.")

    if failures:
        return 1
    if warns and args.fail_on_warn:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
