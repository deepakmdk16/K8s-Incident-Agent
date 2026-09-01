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
from dataclasses import dataclass
from pathlib import Path

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


def rescore(bundle: str) -> ArmReport:
    """Re-score every committed answer in one bundle with the frozen scorer."""
    root = RESULTS / bundle
    if not root.is_dir():
        raise SystemExit(f"reported bundle missing: evals/results/{bundle}")
    scores: list[scoring.CaseScore] = []
    resource_correct = matched_nothing = rows = 0
    per_case: dict[str, list[bool]] = {}
    for answer_path in sorted(root.glob("run*/*/answer.json")):
        case_id = answer_path.parent.name
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
        bundle, scoring.aggregate(scores), resource_correct, matched_nothing, rows, failing
    )


def _cell(summary: scoring.Summary, tier: str) -> str:
    cell = summary.by_tier.get(tier)
    return "—" if cell is None else f"{cell.correct}/{cell.cases}"


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
        f"{len(rules.failing_cases)} case(s) rules cannot do."
    )


if __name__ == "__main__":
    main()
