#!/usr/bin/env python3
"""Produce the final indexed workflow corpus, with Jev's dedupe decisions applied.

Output goes to docs/research/digital-sales-room-workflows/:

  INDEX.md              the navigable table, one row per shipped workflow
  workflows.json        machine-readable catalogue for the Orchestrator
  wf/WF-XXX.md          one page per workflow, carrying its research evidence

Ticket ids (`WF-001`...) are assigned here and are the ids used by branches
(`feature/WF-001-<slug>`) and by the build batches, so a workflow keeps the same
identity from research through implementation.

    python tools/build_corpus.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.consolidate import load_all  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "research" / "digital-sales-room-workflows"
RAW = ROOT / "docs" / "research" / "raw"

DOMAIN_TITLES = {
    "room-experience": "Room experience and content",
    "analytics-intent": "Buyer engagement analytics and intent",
    "crm-integration": "CRM integration and synchronisation",
    "scheduling-meetings": "Scheduling and meetings",
    "security-governance": "Security, access and governance",
    "quoting-proposals": "Pricing, quoting and proposals",
    "automation-engagement": "Automations and engagement",
    "competitive-baseline": "Competitive baseline",
}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    workflows = {w.uid: w for w in load_all()}
    decisions_path = OUT / "dedupe-decisions.json"
    if not decisions_path.is_file():
        print("run tools/dedupe.py first: no dedupe-decisions.json")
        return 1
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))

    # Apply merges: a cluster judged "same_workflow" keeps only its canonical entry.
    merged_away: dict[str, str] = {}  # dropped uid -> canonical uid
    merge_notes: list[str] = []
    for decision in decisions:
        if decision["verdict"] != "same_workflow":
            continue
        canonical = decision["canonical"]
        for uid in decision["members"]:
            if uid != canonical and canonical in workflows:
                merged_away[uid] = canonical
        merge_notes.append(
            f"- `{canonical}` absorbs {len(decision['members']) - 1} duplicate(s): "
            + ", ".join(f"`{u}`" for u in decision["members"] if u != canonical)
        )

    kept = [w for uid, w in workflows.items() if uid not in merged_away]

    # Stable ordering: domain in a deliberate reading order, then research number.
    domain_order = list(DOMAIN_TITLES)
    kept.sort(key=lambda w: (domain_order.index(w.domain) if w.domain in domain_order else 99, w.number))

    OUT.mkdir(parents=True, exist_ok=True)
    wf_dir = OUT / "wf"
    wf_dir.mkdir(exist_ok=True)

    entries = []
    for index, workflow in enumerate(kept, start=1):
        ticket = f"WF-{index:03d}"
        aliases = [u for u, c in merged_away.items() if c == workflow.uid]
        entry = {
            "ticket": ticket,
            "slug": workflow.slug,
            "name": workflow.name,
            "domain": workflow.domain,
            "domain_title": DOMAIN_TITLES.get(workflow.domain, workflow.domain),
            "source_uid": workflow.uid,
            "merged_duplicates": aliases,
            "source_count": len(workflow.sources),
            "sources": workflow.sources,
            "path": f"wf/{ticket}.md",
        }
        entries.append(entry)

        # One page per workflow, carrying the research evidence forward.
        body = workflow.body.strip() or "_No prose captured; see the raw domain file._"
        page = "\n".join(
            [
                f"# {ticket} - {workflow.name}",
                "",
                f"- **Domain:** {entry['domain_title']} (`{workflow.domain}`)",
                f"- **Research source:** `docs/research/raw/{workflow.domain}.md` section {workflow.number}",
                f"- **Distinct sources cited:** {len(workflow.sources)}",
                f"- **Branch:** `feature/{ticket}-{workflow.slug}`",
            ]
            + ([f"- **Merged duplicates:** {', '.join(aliases)}"] if aliases else [])
            + [
                "",
                "## Research evidence",
                "",
                body,
                "",
                "## Sources",
                "",
            ]
            + [f"- <{url}>" for url in workflow.sources]
            + [
                "",
                "---",
                "",
                "## Build status",
                "",
                "- [ ] Technical design doc written and Jev-validated",
                "- [ ] Implemented",
                "- [ ] Tests written and passing",
                "- [ ] Reviewer bot passed",
                "- [ ] Jev merge gate passed",
                "- [ ] Verified in localhost browser",
                "",
            ]
        )
        (wf_dir / f"{ticket}.md").write_text(page, encoding="utf-8")

    # ---- INDEX.md ------------------------------------------------------- #
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        by_domain[entry["domain"]].append(entry)

    total_sources = len({u for e in entries for u in e["sources"]})
    lines = [
        "# Digital Sales Room - workflow corpus",
        "",
        "Every workflow below was researched against primary vendor or open-source",
        "sources before being admitted. Each row links to a page carrying that evidence.",
        "",
        "## Summary",
        "",
        f"- **Distinct workflows:** {len(entries)}",
        f"- **Domains:** {len(by_domain)}",
        f"- **Distinct source URLs:** {total_sources}",
        f"- **Duplicates merged after Jev review:** {len(merged_away)}",
        "",
        "Duplicate handling is recorded rather than silent: Jev was asked, per candidate",
        "cluster, whether the entries were one capability or several. Merges and the",
        "clusters judged distinct are both listed in `dedupe-decisions.json`.",
        "",
    ]
    if merge_notes:
        lines += ["## Merged duplicates", ""] + merge_notes + [""]

    for domain, domain_entries in by_domain.items():
        title = DOMAIN_TITLES.get(domain, domain)
        lines += [
            f"## {title}",
            "",
            f"_({len(domain_entries)} workflows, domain `{domain}`)_",
            "",
            "| Ticket | Workflow | Sources | Merged |",
            "| --- | --- | --- | --- |",
        ]
        for entry in domain_entries:
            merged = ", ".join(entry["merged_duplicates"]) if entry["merged_duplicates"] else ""
            lines.append(
                f"| [`{entry['ticket']}`]({entry['path']}) | {entry['name']} | "
                f"{entry['source_count']} | {merged} |"
            )
        lines.append("")

    (OUT / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "workflows.json").write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"distinct workflows : {len(entries)}")
    print(f"duplicates merged : {len(merged_away)}")
    print(f"source URLs       : {total_sources}")
    for domain, domain_entries in by_domain.items():
        print(f"  {domain:24s} {len(domain_entries):3d}")
    print(f"\nwrote {(OUT / 'INDEX.md').relative_to(ROOT)}")
    print(f"wrote {(OUT / 'workflows.json').relative_to(ROOT)}")
    print(f"wrote {len(entries)} pages under {(wf_dir).relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
