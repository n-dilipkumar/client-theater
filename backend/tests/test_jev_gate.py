"""Regression tests for the Jev gate arithmetic in tools/jev.py.

These exist because of a specific bug: ``pass_option`` was used both as the name
of the gate question and as the answer that counts as success. Since every
high-level gate names its question ``verdict`` and its passing answer
``accept`` / ``ready`` / ``merge``, the comparison could never succeed and every
approval gate in the project returned ``fail`` no matter how confident Jev was.
A merge gate that cannot pass is worse than no gate, because it looks like a
review and is actually a wall.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

JEV_PATH = Path(__file__).resolve().parents[2] / "tools" / "jev.py"

_spec = importlib.util.spec_from_file_location("jev_gate", JEV_PATH)
jev = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
# @dataclass resolves annotations through sys.modules[cls.__module__], so the
# module has to be registered before it is executed.
sys.modules.setdefault("jev_gate", jev)
_spec.loader.exec_module(jev)

Answer = jev.Answer
_apply_gate = jev.Jev._apply_gate


def choice(value, confidence, probabilities=None):
    return Answer(
        name="verdict",
        type="choice",
        value=value,
        confidence=confidence,
        probabilities=probabilities or {value: confidence or 0.0},
    )


def noul(value):
    return Answer(name="meets_bar", type="noul", value=value, confidence=None, probabilities={})


# -- the bug this file was written for --------------------------------------- #


def test_named_pass_value_is_honoured():
    """Jev says 'merge'; the gate must report pass, not 'chose merge rather than verdict'."""
    verdict, passed, reason, selected = _apply_gate(
        choice("merge", 0.97),
        pass_option="verdict",
        fail_options=("reject",),
        threshold=0.7,
        pass_values=("merge",),
    )
    assert (verdict, passed, selected) == ("pass", True, "merge"), reason


def test_high_level_gates_declare_their_pass_values():
    """Each gate's passing answer must be wired, or it can never pass."""
    import inspect

    for method in ("validate_workflow", "validate_design", "score_implementation"):
        source = inspect.getsource(getattr(jev.Jev, method))
        assert "pass_values=" in source, f"{method} never passes pass_values"


# -- option gate ------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value,fail_options,expected",
    [
        ("ready", ("unusable",), "pass"),
        ("gaps", ("unusable",), "fail"),
        ("unusable", ("unusable",), "fail"),
    ],
)
def test_option_gate_routes_on_the_value(value, fail_options, expected):
    verdict, passed, _, _ = _apply_gate(
        choice(value, 0.9),
        pass_option="verdict",
        fail_options=fail_options,
        threshold=0.7,
        pass_values=("ready",),
    )
    assert verdict == expected
    assert passed is (expected == "pass")


def test_low_confidence_pass_value_is_uncertain_not_passed():
    """Right answer on thin evidence must escalate, per the agent contract."""
    verdict, passed, reason, _ = _apply_gate(
        choice("merge", 0.4),
        pass_option="verdict",
        fail_options=("reject",),
        threshold=0.75,
        pass_values=("merge",),
    )
    assert verdict == "uncertain"
    assert passed is False
    assert "insufficient evidence" in reason


def test_empty_pass_values_falls_back_to_the_option_name():
    """Documented fallback for callers whose question and answer share a name."""
    verdict, passed, _, _ = _apply_gate(
        choice("approve", 0.95),
        pass_option="approve",
        fail_options=(),
        threshold=0.7,
    )
    assert (verdict, passed) == ("pass", True)


# -- noul / score gate ------------------------------------------------------- #


def test_noul_gate_passes_at_or_above_threshold():
    verdict, passed, _, _ = _apply_gate(
        noul(0.91), pass_option="meets_bar", fail_options=(), threshold=0.7, pass_values=("merge",)
    )
    assert (verdict, passed) == ("pass", True)


def test_noul_gate_fails_below_threshold():
    verdict, passed, _, _ = _apply_gate(
        noul(0.46), pass_option="meets_bar", fail_options=(), threshold=0.7, pass_values=()
    )
    assert (verdict, passed) == ("fail", False)


# -- confidence gate --------------------------------------------------------- #


def test_confidence_gate_passes_on_a_clear_margin():
    answer = choice("single_writer_wal", 1.0, {"single_writer_wal": 1.0, "other": 0.0})
    verdict, passed, reason, selected = _apply_gate(
        answer,
        pass_option="best_approach",
        fail_options=(),
        threshold=0.75,
        mode="confidence",
    )
    assert (verdict, passed, selected) == ("pass", True, "single_writer_wal")


def test_confidence_gate_is_uncertain_on_a_near_tie():
    answer = choice("a", 0.25, {"a": 0.5, "b": 0.25, "c": 0.25})
    verdict, passed, reason, _ = _apply_gate(
        answer,
        pass_option="best_approach",
        fail_options=(),
        threshold=0.75,
        mode="confidence",
    )
    assert verdict == "uncertain"
    assert passed is False
    assert "too close to decide" in reason
