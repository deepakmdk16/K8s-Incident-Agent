"""Offline tests for the declared bar.

`evals/verify_reported.py` is the gate that stands between a scored bundle and
the numbers README.md quotes, and until these tests it had none of its own: a
bar that cannot be shown to fail is decoration. Every check gets a case that
trips it, built from constructed reports rather than bundles on disk.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals import scoring
from evals import verify_reported as vr

ROOT = Path(__file__).resolve().parents[1]
REPORTED = json.loads((ROOT / "evals" / "reported.json").read_text(encoding="utf-8"))
BAR: dict[str, Any] = REPORTED["bar"]


def _report(
    name: str,
    correct: int,
    *,
    rows: int = 36,
    resource_correct: int = 36,
    confirmed_wrong: int = 0,
    failing: frozenset[str] = frozenset({"a", "b", "c"}),
    usd: float = 0.18,
    seconds: float = 44.0,
) -> vr.ArmReport:
    """One arm's re-derived numbers, at the shape rescore() returns."""
    summary = scoring.Summary(
        overall=scoring.RateCell(cases=rows, correct=correct), confirmed_wrong=confirmed_wrong
    )
    return vr.ArmReport(name, summary, resource_correct, 0, rows, failing, usd, seconds)


def _arms(
    correct: int = 36,
    *,
    rows: int = 36,
    resource_correct: int = 36,
    confirmed_wrong: int = 0,
    usd: float = 0.18,
    seconds: float = 44.0,
) -> list[str]:
    """The bar's verdict on a passing trio, with the solution arm overridden."""
    return vr.check_bar(
        BAR,
        _report("rules", 27, rows=rows, resource_correct=33, confirmed_wrong=3),
        _report("baseline", 30, rows=rows, resource_correct=33, confirmed_wrong=3),
        _report(
            "solution",
            correct,
            rows=rows,
            resource_correct=resource_correct,
            confirmed_wrong=confirmed_wrong,
            usd=usd,
            seconds=seconds,
        ),
    )


def test_the_committed_evidence_passes_its_own_bar() -> None:
    assert _arms() == []


def test_a_costlier_agent_fails_the_bar_even_at_a_perfect_score() -> None:
    """Roadmap 1.6: 36/36 bought at 10x the spend is not a pass."""
    failures = _arms(usd=BAR["solution_mean_case_usd_max"] + 0.01)
    assert len(failures) == 1
    assert "mean cost" in failures[0]
    assert _arms(usd=BAR["solution_mean_case_usd_max"]) == []


def test_a_slower_agent_fails_the_bar_even_at_a_perfect_score() -> None:
    failures = _arms(seconds=BAR["solution_mean_case_duration_s_max"] + 0.1)
    assert len(failures) == 1
    assert "mean duration" in failures[0]
    assert _arms(seconds=BAR["solution_mean_case_duration_s_max"]) == []


def test_the_bar_the_committed_numbers_already_claimed_still_fires() -> None:
    """The pre-existing checks had no test either; each one gets a trip case."""
    assert any("below declared bar" in f for f in _arms(correct=35))
    assert any("resource_correct" in f for f in _arms(resource_correct=35))
    assert any("confirmed-wrong" in f for f in _arms(confirmed_wrong=1))
    assert any("expected 12x3" in f for f in _arms(rows=24))


def test_a_solution_that_only_ties_an_arm_does_not_beat_it() -> None:
    assert any("does not beat baseline" in f for f in _arms(correct=30))


def test_design_requirement_8_fails_when_rules_can_do_every_case() -> None:
    failures = vr.check_bar(
        BAR,
        _report("rules", 27, resource_correct=33, confirmed_wrong=3, failing=frozenset({"a"})),
        _report("baseline", 30, resource_correct=33, confirmed_wrong=3),
        _report("solution", 36),
    )
    assert any("design req 8" in f for f in failures)


def test_rescore_reads_the_cost_and_latency_of_the_reported_solution_bundle() -> None:
    """The means the bar is set from, re-derived from the committed metrics."""
    solution = vr.rescore(str(REPORTED["solution"]))
    assert solution.rows == 36
    assert round(solution.mean_case_usd, 4) == 0.1807
    assert round(solution.mean_case_duration_s, 1) == 44.1
