#!/usr/bin/env python3
"""Parse the raw research corpus into a structured workflow catalogue.

Each domain file uses `## N. Workflow name` sections with a consistent set of
labelled fields, plus appendices that are explicitly NOT workflows. This
extracts the workflows, keeps their provenance, and reports what field coverage
looks like so gaps in the corpus are visible rather than assumed away.

    python tools/consolidate.py --list
    python tools/consolidate.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "docs" / "research" / "raw"
OUT = ROOT / "docs" / "research" / "digital-sales-room-workflows"

WORKFLOW_HEADING = re.compile(r"^(\d+)\.\s+(.+?)\s*$")
APPENDIX_HEADING = re.compile(r"^Appendix", re.IGNORECASE)
ANY_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
BOLD_FIELD = re.compile(r"^\*\*([A-Za-z_ /]+?)\*\*\s*:?\s*(.*)$")
BULLET_FIELD = re.compile(r"^[-*]\s+\*\*([A-Za-z_ /]+?)\*\*\s*:?\s*(.*)$")
URL_RE = re.compile(r"https?://[^\s)\]<>\"']+")


@dataclass
class Workflow:
    """One researched workflow with its provenance and evidence."""

    uid: str
    domain: str
    number: int
    name: str
    fields: dict[str, str] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    body: str = ""
    """Raw section text.

    The research agents used several different layouts (labelled fields, prose,
    tables), so labelled-field parsing alone only recovers some domains. The
    body is the format-agnostic fallback and is what Jev actually reads.
    """

    @property
    def slug(self) -> str:
        words = re.findall(r"[a-z0-9]+", self.name.lower())
        return "-".join(words[:8])

    def summary_for_jev(self, limit: int = 1800) -> str:
        """Condensed evidence bundle handed to Jev for a distinctness judgment."""
        head = f"WORKFLOW {self.uid}: {self.name}\nDOMAIN: {self.domain}"

        labelled = [
            f"{key.upper()}: {value}"
            for key, value in self.fields.items()
            if key not in ("name", "sources") and value
        ]
        if labelled:
            return (head + "\n" + "\n".join(labelled))[:limit]

        prose = re.sub(r"\n{3,}", "\n\n", self.body).strip()
        return (head + "\n" + prose)[:limit]


def _clean(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^`+|`+$", "", value)
    return value.strip()


def parse_file(path: Path) -> list[Workflow]:
    """Extract workflows from one domain file, ignoring its appendices."""
    domain = path.stem
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Map each line number to its enclosing section so appendices can be cut.
    sections: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        match = ANY_HEADING.match(line)
        if match and len(match.group(1)) == 2:
            sections.append((index, len(match.group(1)), match.group(2)))

    workflows: list[Workflow] = []
    for position, (start, _level, heading) in enumerate(sections):
        if APPENDIX_HEADING.match(heading):
            continue
        numbered = WORKFLOW_HEADING.match(heading)
        if not numbered:
            continue
        end = sections[position + 1][0] if position + 1 < len(sections) else len(lines)
        body = lines[start + 1 : end]

        fields: dict[str, str] = {}
        buffer_key: str | None = None
        for line in body:
            field_match = BOLD_FIELD.match(line) or BULLET_FIELD.match(line)
            if field_match:
                buffer_key = _clean(field_match.group(1).lower().replace(" ", "_").replace("/", "_"))
                fields[buffer_key] = _clean(field_match.group(2))
            elif buffer_key and line.strip() and not line.startswith("#"):
                fields[buffer_key] = (fields[buffer_key] + " " + line.strip()).strip()

        number = int(numbered.group(1))
        name = numbered.group(2).strip()
        sources_blob = "\n".join(body)
        sources = sorted({u.rstrip(".,;") for u in URL_RE.findall(sources_blob)})
        if "sources" in fields:
            sources = sorted({u.rstrip(".,;") for u in URL_RE.findall(fields["sources"])} | set(sources))

        workflows.append(
            Workflow(
                uid=f"{domain}#{number:02d}",
                domain=domain,
                number=number,
                name=name,
                fields=fields,
                sources=dedupe(sources),
                body="\n".join(body).strip(),
            )
        )
    return workflows


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


# Research documents that describe the landscape rather than enumerating
# workflows. Their numbered sections are prose, not workflows, so parsing them
# as workflows produces false positives.
NON_WORKFLOW_DOCS = {"opensource-landscape"}


def load_all() -> list[Workflow]:
    workflows: list[Workflow] = []
    for path in sorted(RAW.glob("*.md")):
        if path.stem in NON_WORKFLOW_DOCS:
            continue
        workflows.extend(parse_file(path))
    return workflows


FIELD_COVERAGE = (
    "user_flow",
    "data_flow",
    "data_sources",
    "apis_hit",
    "automations",
    "features_tools",
    "extensibility",
    "sources",
    "evidence",
)


def main() -> int:
    # Research files contain arrows, em dashes and curly quotes. Windows consoles
    # default to cp1252, which cannot encode them, so force UTF-8 output rather
    # than mangling or crashing on real content.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="print a compact index")
    parser.add_argument("--json", action="store_true", help="write the catalogue as JSON")
    args = parser.parse_args()

    workflows = load_all()
    if not workflows:
        print("no workflows parsed; is docs/research/raw/ populated?")
        return 1

    by_domain: dict[str, int] = {}
    for workflow in workflows:
        by_domain[workflow.domain] = by_domain.get(workflow.domain, 0) + 1

    print(f"parsed {len(workflows)} workflows across {len(by_domain)} domains\n")
    for domain, count in sorted(by_domain.items()):
        print(f"  {domain:26s} {count:3d}")

    # The agents used several different section layouts, so per-field parsing is
    # only reliable for some domains. Report what actually matters: does each
    # workflow carry enough prose to judge, and does it cite a real source?
    thin = [w for w in workflows if len(w.body) < 400 and len(w.fields) < 4]
    print(f"\nworkflows with thin evidence (<400 chars and <4 parsed fields): {len(thin)}")
    for workflow in thin:
        print(f"  {workflow.uid} {workflow.name} ({len(workflow.body)} chars)")

    labelled = [w for w in workflows if len(w.fields) >= 4]
    print(f"workflows with the full labelled-field layout: {len(labelled)}")

    unsourced = [w for w in workflows if not w.sources]
    print(f"workflows with no source URL: {len(unsourced)}")
    for workflow in unsourced:
        print(f"  {workflow.uid} {workflow.name}")

    total_sources = len({url for w in workflows for url in w.sources})
    print(f"distinct source URLs across corpus: {total_sources}")

    if args.list:
        print("\n--- index ---")
        for workflow in workflows:
            print(f"{workflow.uid:32s} {workflow.name}")

    if args.json:
        OUT.mkdir(parents=True, exist_ok=True)
        target = OUT / "catalogue.json"
        target.write_text(
            json.dumps([asdict(w) for w in workflows], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nwrote {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
