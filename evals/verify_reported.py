"""Re-derive every reported number from the committed evidence bundles.

This is the offline gate behind `evals/run.sh`. It never calls an API: it reads
the `answer.json` each arm actually produced, re-scores it with the frozen
scorer against the frozen gold, and checks the result against the declared bar
in `evals/reported.json`. If a README or CHANGELOG number ever drifts from the
bundle it cites, or a bundle is edited, this fails.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals import scoring

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evals" / "results"
SCENARIOS = ROOT / "evals" / "scenarios"


@dataclass(frozen=True)
class ArmReport:
    """One arm's re-derived numbers."""

    name: str
    pooled: scoring.Summary
    resource_correct: int
    matched_nothing: int
    rows: int
    failing_cases: frozenset[str]
    mean_case_usd: float
    mean_case_duration_s: float


def rescore(bundle: str) -> ArmReport:
    """Re-score every committed answer in one bundle with the frozen scorer."""
    root = RESULTS / bundle
    if not root.is_dir():
        raise SystemExit(f"reported bundle missing: evals/results/{bundle}")
    scores: list[scoring.CaseScore] = []
    resource_correct = matched_nothing = rows = 0
    per_case: dict[str, list[bool]] = {}
    spend: list[float] = []
    latency: list[float] = []
    for answer_path in sorted(root.glob("run*/*/answer.json")):
        case_id = answer_path.parent.name
        metrics_path = answer_path.parent / "metrics.json"
        if not metrics_path.is_file():
            raise SystemExit(f"no metrics.json beside {answer_path.relative_to(ROOT)}")
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        spend.append(float(metrics["cost_usd"]))
        latency.append(float(metrics["duration_s"]))
        gold = scoring.load_gold(SCENARIOS / case_id / "gold.json")
        score = scoring.score_case(
            scoring.parse_answer(answer_path.read_text(encoding="utf-8")), gold
        )
        scores.append(score)
        rows += 1
        resource_correct += int(score.resource_correct)
        per_case.setdefault(case_id, []).append(score.root_cause_correct)
        if score.resource_correct and not score.matched_classes:
            matched_nothing += 1
    if not rows:
        raise SystemExit(f"no scorable answers in evals/results/{bundle}")
    # "demonstrably fails" = wrong in EVERY replicate run, never a flaky miss.
    failing = frozenset(case for case, results in per_case.items() if not any(results))
    return ArmReport(
        bundle,
        scoring.aggregate(scores),
        resource_correct,
        matched_nothing,
        rows,
        failing,
        sum(spend) / len(spend),
        sum(latency) / len(latency),
    )


def _cell(summary: scoring.Summary, tier: str) -> str:
    cell = summary.by_tier.get(tier)
    return "—" if cell is None else f"{cell.correct}/{cell.cases}"


def check_bar(
    bar: Mapping[str, Any], rules: ArmReport, baseline: ArmReport, solution: ArmReport
) -> list[str]:
    """Every way the committed evidence can miss the declared bar, named.

    Pure and total: the callers pass re-derived reports, so the bar itself is
    testable without a bundle on disk.
    """
    failures: list[str] = []
    if int(bar["cases"]) * int(bar["runs"]) != solution.rows:
        failures.append(
            f"solution bundle has {solution.rows} rows, expected {bar['cases']}x{bar['runs']}"
        )
    if not baseline.rows == solution.rows == rules.rows:
        failures.append(
            f"arms disagree on row count: rules {rules.rows}, baseline "
            f"{baseline.rows}, solution {solution.rows}"
        )
    if bar["solution_beats_rules_pooled"] and (
        solution.pooled.overall.correct <= rules.pooled.overall.correct
    ):
        failures.append(
            f"solution {solution.pooled.overall.correct} does not beat rules "
            f"{rules.pooled.overall.correct} on the primary metric"
        )
    # Design requirement 8: the ablation must name >=3 cases rules cannot do.
    if len(rules.failing_cases) < int(bar["rules_failing_cases_min"]):
        failures.append(
            f"rules arm fails on only {len(rules.failing_cases)} case(s), "
            f"design req 8 requires {bar['rules_failing_cases_min']}"
        )
    if bar["solution_beats_baseline_pooled"] and (
        solution.pooled.overall.correct <= baseline.pooled.overall.correct
    ):
        failures.append(
            f"solution {solution.pooled.overall.correct} does not beat baseline "
            f"{baseline.pooled.overall.correct} on the primary metric"
        )
    if solution.resource_correct < int(bar["solution_resource_correct_min"]):
        failures.append(
            f"solution resource_correct {solution.resource_correct} below declared bar "
            f"{bar['solution_resource_correct_min']}"
        )
    if solution.pooled.overall.correct < int(bar.get("solution_pooled_min", 0)):
        failures.append(
            f"solution pooled {solution.pooled.overall.correct} below declared bar "
            f"{bar['solution_pooled_min']}"
        )
    if solution.pooled.confirmed_wrong > int(bar.get("solution_confirmed_wrong_max", 99)):
        failures.append(
            f"solution confirmed-wrong {solution.pooled.confirmed_wrong} above declared bar "
            f"{bar['solution_confirmed_wrong_max']}"
        )
    # Cost and latency are asserted on the POOLED MEAN, never a per-case max:
    # the same case varies up to 2.4x across replicate runs, so a per-case
    # ceiling would fire on noise, while a 12-case run mean moved only +-15%
    # across the three committed runs. A per-case cost max would also be
    # toothless, since agent.MAX_CASE_USD already truncates it.
    if solution.mean_case_usd > float(bar["solution_mean_case_usd_max"]):
        failures.append(
            f"solution mean cost ${solution.mean_case_usd:.4f}/case above declared bar "
            f"${float(bar['solution_mean_case_usd_max']):.4f}"
        )
    if solution.mean_case_duration_s > float(bar["solution_mean_case_duration_s_max"]):
        failures.append(
            f"solution mean duration {solution.mean_case_duration_s:.1f}s/case above declared "
            f"bar {float(bar['solution_mean_case_duration_s_max']):.1f}s"
        )
    return failures


def main() -> None:
    spec = json.loads((ROOT / "evals" / "reported.json").read_text(encoding="utf-8"))
    rules = rescore(str(spec["rules"]))
    baseline = rescore(str(spec["baseline"]))
    solution = rescore(str(spec["solution"]))
    bar = spec["bar"]

    print("Re-derived from committed evidence (offline, frozen scorer):\n")
    rows: list[tuple[str, str, str, str]] = [
        ("metric", "rules", "baseline", "solution"),
        ("---", "---", "---", "---"),
        (
            "root-cause identification",
            f"{rules.pooled.overall.correct}/{rules.pooled.overall.cases}",
            f"{baseline.pooled.overall.correct}/{baseline.pooled.overall.cases}",
            f"{solution.pooled.overall.correct}/{solution.pooled.overall.cases}",
        ),
    ]
    rows += [
        (
            tier,
            _cell(rules.pooled, tier),
            _cell(baseline.pooled, tier),
            _cell(solution.pooled, tier),
        )
        for tier in ("T1", "T2", "T3")
    ]
    rows += [
        (
            "resource identification",
            f"{rules.resource_correct}/{rules.rows}",
            f"{baseline.resource_correct}/{baseline.rows}",
            f"{solution.resource_correct}/{solution.rows}",
        ),
        (
            "right object, sentence unmatched",
            str(rules.matched_nothing),
            str(baseline.matched_nothing),
            str(solution.matched_nothing),
        ),
        (
            "mean cost / case",
            f"${rules.mean_case_usd:.4f}",
            f"${baseline.mean_case_usd:.4f}",
            f"${solution.mean_case_usd:.4f}",
        ),
        (
            "mean duration / case",
            f"{rules.mean_case_duration_s:.1f}s",
            f"{baseline.mean_case_duration_s:.1f}s",
            f"{solution.mean_case_duration_s:.1f}s",
        ),
        (
            "confirmed-wrong",
            str(rules.pooled.confirmed_wrong),
            str(baseline.pooled.confirmed_wrong),
            str(solution.pooled.confirmed_wrong),
        ),
    ]
    for label, left, mid, right in rows:
        print(f"| {label} | {left} | {mid} | {right} |")
    print(
        "\nrules arm fails in every run on: "
        + (", ".join(sorted(rules.failing_cases)) or "no case")
    )

    failures = check_bar(bar, rules, baseline, solution)
    if failures:
        print("\nFAIL:")
        for failure in failures:
            print(f"  - {failure}")
        sys.exit(1)
    print(
        f"\nOK — solution {solution.pooled.overall.correct}/{solution.pooled.overall.cases} vs "
        f"baseline {baseline.pooled.overall.correct}/{baseline.pooled.overall.cases} vs "
        f"rules {rules.pooled.overall.correct}/{rules.pooled.overall.cases}, "
        f"resource identification {solution.resource_correct}/{solution.rows}, "
        f"{len(rules.failing_cases)} case(s) rules cannot do, "
        f"${solution.mean_case_usd:.4f} and {solution.mean_case_duration_s:.1f}s per case."
    )


if __name__ == "__main__":
    main()
