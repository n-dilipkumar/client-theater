"""Ask Jev how to resolve the workflow-count shortfall.

126 researched workflows must become 100 shipped ones, and the research shows
real overlap between domains: reminders and nudges appear in both scheduling and
automation, e-signature in four domains, CRM activity writeback in both CRM
integration and analytics. The honest distinct count is likely below 100.

Rather than pad the number with near-duplicates, ask which policy to apply.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.jev import Jev  # noqa: E402

client = Jev()

result = client.choose_approach(
    problem=(
        "An open-source Digital Sales Room has 126 researched workflows across 7 domains, with "
        "genuine cross-domain overlap: reminders/nudges appear in both scheduling and automation; "
        "e-signature appears in four domains; CRM activity writeback appears in both CRM "
        "integration and analytics. A requirement says 'at least 100 workflows must be built'. "
        "Which policy should be applied to reach the target honestly?"
    ),
    options={
        "ship_distinct_and_report": (
            "Merge genuine duplicates, ship only distinct workflows, and report the final count "
            "honestly even if it lands between 85 and 95. Add coverage by deepening each workflow "
            "rather than by inventing new ones. A reviewer can trust every shipped item."
        ),
        "pad_with_near_duplicates": (
            "Split merged workflows back out per domain so the count reaches 100, accepting that "
            "some shipped features overlap heavily. Hits the number but a reviewer will see the "
            "padding."
        ),
        "broaden_scope_to_reach_100": (
            "Keep all distinct workflows and add genuinely new ones from the documented gaps in "
            "the research (mutual action plans, per-slide deck tracking, native video, "
            "geofencing, multi-touch attribution), which vendors do not document but which are "
            "real product requirements. Reaches 100 with real work, but those are design "
            "inferences rather than sourced workflows."
        ),
    },
    context={
        "audience": "The user reviews the work and will reject padding.",
        "hard_requirement": "Minimum 100 workflows built, tested, documented.",
        "constraint": "Every workflow must be implementable and verifiable on localhost.",
        "observation": (
            "The research corpus contains 395 distinct source URLs and explicitly records which "
            "features vendors do NOT document, so the gap list is real and specific."
        ),
    },
    threshold=0.7,
)
print(result.summary())
print()
print("verdict :", result.verdict)
print("selected:", result.selected)
