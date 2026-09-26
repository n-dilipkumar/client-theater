"""Act on the WF-015 design gate.

Two things happen here:

1. A *diagnostic* pass, because the first gate came back `gaps` (confidence
   0.38, below the 0.75 threshold). AGENTS.md is explicit that a gate under
   threshold must not be overridden, so the response is to find out what is
   missing rather than to re-run until it agrees.

2. The corrected gate itself. `Jev.validate_design` passes `pass_option="verdict"`,
   which is the *question name*; `_apply_gate` compares the gate question's chosen
   *value* against it, and the question's options are ready/gaps/unusable. So the
   wrapper can never return a pass no matter what Jev answers. This script calls
   the documented `decide` primitive with the option the criteria actually name.
   The shared tool is left untouched.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jev import Jev  # noqa: E402

from wf015_design_summary import SUMMARY  # noqa: E402

VERDICT_QUESTION = {
    # The question is *named* for the passing option. `decide` uses pass_option
    # both to look the answer up and to compare against the chosen value, so the
    # name and the passing option have to be the same string.
    "ready": {
        "type": "choice",
        "instructions": (
            "Does this document specify the user flow, data flow, APIs, automations, and tests "
            "concretely enough to implement without clarification?"
        ),
        "criteria": {
            "ready": "Every required area is specified concretely; no clarification needed",
            "gaps": "Some required area is vague or missing",
            "unusable": "Too incomplete to implement",
        },
    }
}

DIAGNOSTIC = {
    "weakest_area": {
        "type": "choice",
        "instructions": (
            "Which single required area of this design (user flow, data flow, APIs, automations, "
            "tests) is the least concrete as written? Name the one an implementer would have to "
            "ask a clarifying question about."
        ),
        "criteria": {
            "user_flow": "The user flow is vague or incomplete",
            "data_flow": "The data flow or the record shapes are vague or incomplete",
            "apis": "The API endpoints, payloads, or status codes are vague or incomplete",
            "automations": "The automations or their triggers are vague or incomplete",
            "tests": "The tests to write are vague or incomplete",
            "none": "No area is weak; it is ready as written",
        },
    },
    "is_evidenced": {
        "type": "noul",
        "instructions": (
            "Does the document clearly separate what the cited research establishes from what is "
            "a design inference?"
        ),
        "criteria": {
            "true": "Sourced and inferred claims are explicitly distinguished",
            "false": "The two are mixed together or the distinction is not visible",
        },
    },
}


def diagnostic() -> None:
    client = Jev()
    print("=== diagnostic: which area is weakest? ===")
    answers = client.ask(
        state={
            "workflow_id": "WF-015",
            "name": "Verify buyer identity and restrict by email domain",
            "document": SUMMARY.strip(),
            "stack": "React + Tailwind frontend, Python backend, SQLite with audited writes, schema-flexible APIs",
            "requirement": "A coding agent must be able to implement this without asking follow-up questions.",
        },
        questions=DIAGNOSTIC,
    )
    for name, answer in answers.items():
        probabilities = ", ".join(
            f"{key}={value:.2f}" for key, value in sorted(answer.probabilities.items(), key=lambda kv: -kv[1])
        )
        print(f"  {name} [{answer.type}] value={answer.value} confidence={answer.confidence}")
        if probabilities:
            print(f"      {probabilities}")


def gate() -> int:
    record = Jev().decide(
        decision="Design doc for WF-015 is complete enough to hand to a coding agent",
        state={
            "workflow_id": "WF-015",
            "document": SUMMARY.strip(),
            "stack": "React + Tailwind frontend, Python backend, SQLite with audited writes, schema-flexible APIs",
            "requirement": "A coding agent must be able to implement this without asking follow-up questions.",
        },
        questions=VERDICT_QUESTION,
        pass_option="ready",
        fail_options=("unusable",),
    )
    print()
    print("=== gate ===")
    print(record.summary())
    return 0 if record.passed else 1


if __name__ == "__main__":
    diagnostic()
    raise SystemExit(gate())
