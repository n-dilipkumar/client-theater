"""Tests for the Jev gate logic.

The gate is the only thing standing between "an agent said it was done" and
"the project agreed it was done", so its own behaviour is pinned here rather
than trusted. These are pure unit tests: no network, no audit-log writes.

Run from the repo root:

    ./.venv/Scripts/python -m pytest tools/test_jev_gate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jev import Answer, Jev, _is_gate_name  # noqa: E402

MERGE_LEGEND = {"merge": "Ready to merge", "fix": "Needs a fix-up pass", "reject": "Off-spec"}


def choice(name, value, confidence, probabilities, legend=None):
    return Answer(
        name=name,
        type="choice",
        value=value,
        confidence=confidence,
        probabilities=probabilities,
        legend=legend if legend is not None else MERGE_LEGEND,
    )


def gate(answer, pass_option="verdict", fail_options=("reject",), threshold=0.75):
    return Jev._apply_gate(answer, pass_option, fail_options, threshold)


# -- a gate named after its own question ------------------------------------- #


def test_question_name_gate_passes_on_a_confident_approval():
    """The convention every high-level helper uses: pass_option is the question name."""
    answer = choice("verdict", "merge", 0.91, {"merge": 0.94, "fix": 0.05, "reject": 0.01})
    verdict, passed, reason, selected = gate(answer)
    assert (verdict, passed) == ("pass", True)
    assert selected == "merge"
    assert "0.91" in reason


def test_question_name_gate_fails_on_an_explicit_failure_option():
    answer = choice("verdict", "reject", 0.9, {"merge": 0.05, "reject": 0.9})
    verdict, passed, _, _ = gate(answer)
    assert (verdict, passed) == ("fail", False)


def test_question_name_gate_treats_a_non_failure_middle_option_as_a_pass():
    """"fix" is not in fail_options, so a confident fix verdict passes the gate.

    The gate question asked whether the branch is ready; the answer is that a
    fix-up pass is needed. That is a genuine pass of the *gate*, and the reason
    string records what was chosen so nobody mistakes it for a clean merge.
    """
    answer = choice("verdict", "fix", 0.9, {"merge": 0.05, "fix": 0.9, "reject": 0.01})
    verdict, passed, reason, selected = gate(answer)
    assert (verdict, passed) == ("pass", True)
    assert selected == "fix"
    assert "fix" in reason


def test_question_name_gate_is_uncertain_when_confidence_is_too_low():
    answer = choice("verdict", "merge", 0.40, {"merge": 0.45, "reject": 0.45})
    verdict, passed, _, _ = gate(answer)
    assert (verdict, passed) == ("uncertain", False)


def test_an_uncertain_verdict_is_not_acted_on():
    """A near-tie must escalate, never resolve silently in either direction."""
    answer = choice("verdict", "merge", 0.60, {"merge": 0.55, "reject": 0.40})
    assert gate(answer)[1] is False


# -- a gate named after the winning criteria key ----------------------------- #


def test_criteria_key_gate_passes_on_that_key():
    answer = choice("verdict", "accept", 0.9, {"accept": 0.9, "revise": 0.1},
                    legend={"accept": "ready", "revise": "thin"})
    verdict, passed, _, selected = gate(answer, pass_option="accept", fail_options=("reject",))
    assert (verdict, passed) == ("pass", True)
    assert selected == "accept"


def test_criteria_key_gate_fails_on_a_different_key():
    answer = choice("verdict", "revise", 0.9, {"accept": 0.1, "revise": 0.9},
                    legend={"accept": "ready", "revise": "thin"})
    verdict, passed, _, _ = gate(answer, pass_option="accept", fail_options=("reject",))
    assert (verdict, passed) == ("fail", False)
    assert "rather than" in gate(answer, pass_option="accept", fail_options=("reject",))[2]


def test_criteria_key_gate_still_honours_an_explicit_failure():
    answer = choice("verdict", "reject", 0.9, {"accept": 0.1, "reject": 0.9},
                    legend={"accept": "ready", "reject": "off-spec"})
    verdict, passed, _, _ = gate(answer, pass_option="accept", fail_options=("reject",))
    assert (verdict, passed) == ("fail", False)


# -- numeric gates ------------------------------------------------------------ #


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.9, ("pass", True)), (0.75, ("pass", True)), (0.74, ("fail", False))],
)
def test_a_score_gate_passes_at_or_above_the_threshold(value, expected):
    answer = Answer(name="meets_bar", type="score", value=value, confidence=None, probabilities={})
    assert Jev._apply_gate(answer, "meets_bar", (), 0.75)[:2] == expected


def test_a_non_numeric_gate_fails_rather_than_crashing():
    answer = Answer(name="verdict", type="score", value="yes", confidence=None, probabilities={})
    verdict, passed, reason, _ = Jev._apply_gate(answer, "meets_bar", (), 0.75)
    assert (verdict, passed) == ("fail", False)
    assert "not numeric" in reason


# -- confidence-gate mode ----------------------------------------------------- #


def test_confidence_mode_passes_a_confident_selection():
    answer = choice("best", "option_a", 0.85, {"option_a": 0.9, "option_b": 0.1})
    verdict, passed, _, selected = Jev._apply_gate(answer, "best", (), 0.75, mode="confidence")
    assert (verdict, passed) == ("pass", True)
    assert selected == "option_a"


def test_confidence_mode_is_uncertain_on_a_near_tie():
    """Any option may be legitimate here, so a low-confidence pick escalates."""
    answer = choice("best", "option_a", 0.5, {"option_a": 0.52, "option_b": 0.44})
    verdict, passed, _, _ = Jev._apply_gate(answer, "best", (), 0.75, mode="confidence")
    assert (verdict, passed) == ("uncertain", False)


def test_confidence_mode_rejects_a_non_choice_gate():
    answer = Answer(name="best", type="score", value=0.9, confidence=0.9, probabilities={})
    verdict, passed, reason, _ = Jev._apply_gate(answer, "best", (), 0.75, mode="confidence")
    assert (verdict, passed) == ("fail", False)
    assert "must be a choice" in reason


def test_confidence_mode_ignores_fail_options():
    """In confidence mode nothing is an explicit failure: any option is allowed."""
    answer = choice("best", "option_c", 0.9, {"option_c": 0.9, "option_a": 0.1})
    verdict, passed, _, _ = Jev._apply_gate(answer, "best", ("option_c",), 0.75, mode="confidence")
    assert (verdict, passed) == ("pass", True)


# -- the gate-name disambiguation helper -------------------------------------- #


def test_gate_name_helper_distinguishes_a_name_from_a_criteria_key():
    answer = choice("verdict", "merge", 0.9, {"merge": 0.9, "fix": 0.1})
    assert _is_gate_name(answer, "merge") is False
    assert _is_gate_name(answer, "verdict") is True
