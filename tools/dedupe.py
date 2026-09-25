#!/usr/bin/env python3
"""Find candidate duplicate clusters across the research domains, then let Jev adjudicate.

The research was done per domain, so the same product capability was documented
independently in several places: e-signature appears in four domains, CRM
activity writeback in two, reminders in two. Shipping all of those as separate
"workflows" would inflate the count with the same feature wearing different
names, so duplicates have to be identified deliberately rather than by accident.

Stage 1 clusters candidates locally on token overlap plus a shared-noun list.
Stage 2 asks Jev, in one batched request, whether each cluster is genuinely one
workflow or genuinely distinct, and which entry should be canonical.

    python tools/dedupe.py            # cluster, adjudicate, report
    python tools/dedupe.py --dry      # show clusters without calling Jev
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.consolidate import load_all  # noqa: E402
from tools.jev import Jev  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "research" / "digital-sales-room-workflows"

STOPWORDS = {
    "a", "an", "and", "the", "to", "of", "in", "on", "for", "from", "with", "into",
    "by", "at", "via", "per", "each", "its", "that", "this", "then", "or", "as",
    "is", "are", "be", "it", "up", "out", "over", "under", "own", "via",
}

# Capability nouns that mean two workflows probably do the same thing even when
# their names read differently.
SHARED_NOUNS = {
    "e-signature": {"e-signature", "esignature", "signature", "sign-off", "signoff", "sign"},
    "reminder": {"reminder", "reminders", "nudge", "nudges"},
    "engagement review": {"engagement"},
    "crm writeback": {"crm", "activity", "timeline", "feed"},
    "webhook stream": {"webhook", "stream", "streaming", "events"},
    "access control": {"access", "expiry", "expires", "expire", "link", "invite"},
    "identity": {"identity", "verification", "verify", "domain"},
    "expiry": {"expire", "expiry", "expires"},
    "dwell": {"dwell", "time-per-page", "drop-off"},
    "approval": {"approval", "approve", "approvals"},
}

# Nouns generic enough that two workflows sharing one are usually the same
# feature. Others need name-token overlap before they are even compared.
HIGH_COLLISION = {"e-signature", "reminder", "crm writeback"}

# Above this size a connected component is treated as over-merged and split.
MAX_CLUSTER = 4

# Candidate overlap clusters, curated by reading all 126 workflow names.
#
# The research ran per domain, so the same product capability was documented
# independently in several places. These are the clusters where that looks
# likely. Jev decides each one; nothing is merged on the strength of this list
# alone. Every entry cites why the overlap is plausible.
CANDIDATE_CLUSTERS: list[tuple[str, ...]] = [
    # Engagement review is described from both the analytics and the room side.
    ("analytics-intent#01", "room-experience#06"),
    ("analytics-intent#02", "room-experience#06"),
    # Per-page dwell inside a document appears in both analytics and the
    # security engagement review.
    ("analytics-intent#03", "security-governance#08"),
    # Writing room engagement into an external activity timeline.
    ("analytics-intent#11", "crm-integration#15"),
    ("analytics-intent#10", "room-experience#17"),
    # Single-event write versus batch upsert of the same engagement rows.
    ("crm-integration#04", "crm-integration#05"),
    # E-signature capture, described from the quote, the room, and the mutual
    # action plan angle.
    ("quoting-proposals#10", "room-experience#16"),
    ("quoting-proposals#10", "scheduling-meetings#17"),
    # Reminders and nudges, described per domain.
    ("automation-engagement#08", "scheduling-meetings#11"),
    ("automation-engagement#08", "quoting-proposals#14"),
    ("quoting-proposals#14", "security-governance#14"),
    ("automation-engagement#01", "automation-engagement#08"),
    # Gating a buyer link with password, expiry and verification.
    ("room-experience#04", "security-governance#01"),
    ("quoting-proposals#12", "security-governance#01"),
    ("room-experience#14", "security-governance#01"),
    # Buyer identity and email-domain restriction.
    ("room-experience#15", "security-governance#02"),
    ("room-experience#15", "security-governance#11"),
    # Routing something for human approval before it takes effect.
    ("quoting-proposals#06", "scheduling-meetings#12"),
    # Stale-deal sweeping versus engagement-weighted pipeline triage.
    ("analytics-intent#08", "automation-engagement#14"),
    # Intent signal emission versus acting on a buyer signal.
    ("analytics-intent#12", "automation-engagement#09"),
]


def tokens(name: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", name.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


def shared_nouns(name: str) -> set[str]:
    lowered = name.lower()
    return {label for label, needles in SHARED_NOUNS.items() if any(n in lowered for n in needles)}


def cluster_candidates(workflows) -> list[list]:
    """Return the curated set of candidate overlap clusters.

    An earlier version clustered on name-token overlap. That does not work:
    generic tokens produce nonsense pairs (a workflow that merely says "CRM"
    gets paired with every other CRM workflow), and transitive merging chained
    67 unrelated workflows into one group. Semantic distinctness is not a
    lexical property, so the candidate set is curated by reading all 126
    workflow names, and Jev decides each cluster in Stage 2.
    """
    by_uid = {w.uid: w for w in workflows}
    clusters: list[list] = []
    for members in CANDIDATE_CLUSTERS:
        present = [by_uid[uid] for uid in members if uid in by_uid]
        if len(present) > 1:
            clusters.append(present)
    return clusters


def adjudicate(clusters: list[list], client: Jev) -> list[dict]:
    """Ask Jev, in one batched request, whether each cluster is one workflow or several."""
    questions: dict[str, dict] = {}
    for index, cluster in enumerate(clusters):
        label = f"c{index:02d}"
        listing = "\n".join(
            f"  {w.uid}: {w.name}\n    domain={w.domain}, sources={len(w.sources)}"
            for w in cluster
        )
        questions[f"{label}_verdict"] = {
            "type": "choice",
            "instructions": (
                "Do these documented workflows implement the SAME underlying product capability "
                "(a user would see one feature), or are they genuinely different features that "
                "happen to share vocabulary? Judge by the effect on the user and the system, not "
                "by wording.\n" + listing
            ),
            "criteria": {
                "same_workflow": (
                    "These are the same capability documented in different domains. Ship one "
                    "workflow; the others are duplicate descriptions of it."
                ),
                "distinct_workflows": (
                    "These are genuinely different capabilities. A user would encounter each as a "
                    "separate feature and they should ship separately."
                ),
                "partial_overlap": (
                    "Substantially the same capability but each has a distinct sub-case worth "
                    "shipping separately."
                ),
            },
        }
        questions[f"{label}_canonical"] = {
            "type": "choice",
            "instructions": (
                "If these describe one capability, which entry is the most complete and "
                "best-evidenced version to keep as the canonical workflow?\n" + listing
            ),
            "criteria": {w.uid: w.name for w in cluster} | {"none": "Not applicable; they are distinct"},
        }

    state = {
        "task": "Deduplicate a Digital Sales Room workflow corpus harvested per domain.",
        "clusters": [
            {"label": f"c{i:02d}", "members": [{"uid": w.uid, "name": w.name, "domain": w.domain} for w in c]}
            for i, c in enumerate(clusters)
        ],
    }
    answers = client.ask(state=state, questions=questions)

    results = []
    for index, cluster in enumerate(clusters):
        label = f"c{index:02d}"
        verdict = answers[f"{label}_verdict"]
        canonical = answers[f"{label}_canonical"]
        results.append(
            {
                "label": label,
                "members": [w.uid for w in cluster],
                "names": {w.uid: w.name for w in cluster},
                "verdict": verdict.value,
                "confidence": verdict.confidence,
                "probabilities": verdict.probabilities,
                "canonical": canonical.value,
                "canonical_confidence": canonical.confidence,
            }
        )
    return results


def main() -> int:
    # Workflow names contain arrows and em dashes; Windows consoles default to
    # cp1252 and cannot encode them.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry", action="store_true", help="show clusters without calling Jev")
    args = parser.parse_args()

    workflows = load_all()
    clusters = cluster_candidates(workflows)
    covered = {w.uid for cluster in clusters for w in cluster}

    print(f"{len(workflows)} workflows parsed")
    print(f"{len(clusters)} candidate overlap clusters covering {len(covered)} workflows\n")

    for index, cluster in enumerate(clusters):
        print(f"c{index:02d}")
        for workflow in cluster:
            print(f"    {workflow.uid:30s} {workflow.name}")
        print()

    if args.dry:
        return 0

    client = Jev()
    results = adjudicate(clusters, client)
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "dedupe-decisions.json"
    target.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    same = [r for r in results if r["verdict"] == "same_workflow"]
    partial = [r for r in results if r["verdict"] == "partial_overlap"]
    distinct = [r for r in results if r["verdict"] == "distinct_workflows"]
    lost = sum(len(r["members"]) - 1 for r in same)

    print("=" * 70)
    print(f"same workflow (merge): {len(same)} clusters, removes {lost} duplicate entries")
    print(f"partial overlap (keep both): {len(partial)} clusters")
    print(f"genuinely distinct (keep all): {len(distinct)} clusters")
    print(f"\ndistinct workflow count: {len(workflows) - lost}")
    print(f"wrote {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
