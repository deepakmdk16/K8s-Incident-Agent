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

from evals import hedging, scoring
from evals import verify_reported as vr

ROOT = Path(__file__).resolve().parents[1]
REPORTED = json.loads((ROOT / "evals" / "reported.json").read_text(encoding="utf-8"))
BAR: dict[str, Any] = REPORTED["bar"]
NO_HEDGES = hedging.Report.from_rows([])


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
    hedges: hedging.Report = NO_HEDGES,
) -> vr.ArmReport:
    """One arm's re-derived numbers, at the shape rescore() returns."""
    summary = scoring.Summary(
        overall=scoring.RateCell(cases=rows, correct=correct), confirmed_wrong=confirmed_wrong
    )
    return vr.ArmReport(name, summary, resource_correct, 0, rows, failing, usd, seconds, hedges)


def _arms(
    correct: int = 36,
    *,
    rows: int = 36,
    resource_correct: int = 36,
    confirmed_wrong: int = 0,
    usd: float = 0.18,
    seconds: float = 44.0,
    hedges: hedging.Report = NO_HEDGES,
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
            hedges=hedges,
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


# --- hedging report: price every verdict, report only, no bar (STATUS 2.3) ---


def test_hedging_is_report_only_and_never_moves_the_bar() -> None:
    """A bar needs a pre-registered threshold and a second scoring (STATUS 2.3):
    a solution that hedged every readable case still passes check_bar unchanged."""
    all_hedged = hedging.Report.from_rows([hedging.Row("inconclusive", True, "deployment")] * 36)
    assert all_hedged.inconclusive_readable == 36
    assert _arms(hedges=all_hedged) == []


def test_rescore_prices_the_verdicts_of_the_reported_bundles() -> None:
    """Re-derived from the committed evidence. The rules arm's three inconclusive
    rows are all t2-rbac-sync-forbidden, a RoleBinding the solution's tools read."""
    rules = vr.rescore(str(REPORTED["rules"])).hedges
    assert {v: (c.cases, c.correct) for v, c in rules.by_verdict.items()} == {
        "confirmed": (18, 15),
        "probable": (15, 12),
        "inconclusive": (3, 0),
    }
    assert (rules.inconclusive_readable, rules.inconclusive_unreadable) == (3, 0)
    baseline = vr.rescore(str(REPORTED["baseline"])).hedges
    assert {v: (c.cases, c.correct) for v, c in baseline.by_verdict.items()} == {
        "confirmed": (33, 30),
        "probable": (3, 0),
    }
    solution = vr.rescore(str(REPORTED["solution"])).hedges
    assert {v: (c.cases, c.correct) for v, c in solution.by_verdict.items()} == {
        "confirmed": (36, 36)
    }
    assert (solution.inconclusive_readable, solution.inconclusive_unreadable) == (0, 0)


def test_hedging_rows_cover_every_verdict_any_arm_reports() -> None:
    """One cross-arm table: a verdict only one arm used still gets its rows, the
    other arms show a dash, and the readable/unreadable split closes the table."""
    rules = hedging.Report.from_rows([hedging.Row("inconclusive", False, "deployment")])
    baseline = hedging.Report.from_rows([hedging.Row("probable", False, "service")])
    solution = hedging.Report.from_rows(
        [hedging.Row("confirmed", True, "service"), hedging.Row(None, False, "clusterrole")]
    )
    rows = vr.hedging_rows(rules, baseline, solution)
    assert [row[0] for row in rows] == [
        "confirmed: coverage",
        "confirmed: accuracy",
        "probable: coverage",
        "probable: accuracy",
        "inconclusive: coverage",
        "inconclusive: accuracy",
        "none: coverage",
        "none: accuracy",
        "inconclusive on a readable kind",
        "inconclusive on an unreadable kind",
    ]
    assert rows[0] == ("confirmed: coverage", "—", "—", "1/2 (50.0%)")
    assert rows[1] == ("confirmed: accuracy", "—", "—", "1/1 (100.0%)")
    assert rows[5] == ("inconclusive: accuracy", "0/1 (0.0%)", "—", "—")
    assert rows[-2] == ("inconclusive on a readable kind", "1", "0", "0")
    assert rows[-1] == ("inconclusive on an unreadable kind", "0", "0", "0")
