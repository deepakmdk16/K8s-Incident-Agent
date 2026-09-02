"""Scored-run harness: one arm x the case set x N replicate runs.

Discovers cases (a scenario with gold.json + its recorded fixture), invokes the
arm offline against the fixture, parses and scores every answer with the frozen
evals/scoring.py, and writes the evidence bundle into evals/results/<run>/:
per-case artifacts (prompt where the arm builds one, report, answer, metrics),
rows.jsonl (per-case
score rows incl. matched_classes for audit), summary.json and summary.md. The
committed bundle is what README/CHANGELOG numbers cite (design req 7).

A case whose answer fails to produce or validate is scored wrong and its error
recorded — never skipped, never defaulted (scoring spec: nothing defaults).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ablation.rules import diagnose as rules_diagnose
from baseline.diagnose import diagnose as baseline_diagnose
from common.llm import PINNED_MODEL, load_env_file
from common.runlog import get_logger, new_run_id
from evals import scoring
from solution.agent import diagnose as solution_diagnose

ROOT = Path(__file__).resolve().parents[1]


class InfrastructureError(RuntimeError):
    """A failure that dooms every subsequent API call (billing / usage limits).

    Distinct from an agent failure (which is scored wrong, never skipped): an
    infrastructure failure poisons the whole run, so the harness aborts and
    marks the bundle disclosed-partial instead of writing rows that blend
    "model was wrong" with "billing refused the request"
    (docs/failure-modes.md 2026-08-29, both entries).
    """


_INFRA_MARKERS = ("credit balance is too low", "reached your specified api usage limits")

ArmFn = Callable[[Path, str, Path], Path]
ARMS: dict[str, ArmFn] = {
    "baseline": lambda fixture, case_id, out: baseline_diagnose(fixture, case_id, out).parent,
    "solution": lambda fixture, case_id, out: solution_diagnose(fixture, case_id, out).parent,
    "rules": lambda fixture, case_id, out: rules_diagnose(fixture, case_id, out).parent,
}

# The rules arm calls no model, so a pinned-model field in its bundle would be a
# false claim about how its answers were produced.
NO_MODEL_ARMS = frozenset({"rules"})


@dataclass(frozen=True)
class Case:
    case_id: str
    fixture: Path
    gold: scoring.Gold


@dataclass(frozen=True)
class Outcome:
    """One arm invocation on one case in one replicate run."""

    run: int
    case_id: str
    tier: str
    score: scoring.CaseScore | None
    error: str | None
    contract_violations: tuple[str, ...]
    metrics: dict[str, object]

    def row(self) -> dict[str, object]:
        base: dict[str, object] = {
            "run": self.run,
            "case_id": self.case_id,
            "tier": self.tier,
            "error": self.error,
            "contract_violations": list(self.contract_violations),
            "metrics": self.metrics,
        }
        if self.score is not None:
            base |= {
                "verdict": self.score.verdict,
                "resource_correct": self.score.resource_correct,
                "matched_classes": sorted(str(c) for c in self.score.matched_classes),
                "class_correct": self.score.class_correct,
                "root_cause_correct": self.score.root_cause_correct,
            }
        else:
            base |= {"verdict": "invalid", "root_cause_correct": False}
        return base


# The frozen 12-case set lives in evals/scenarios/ and keeps that root to
# itself: its identity IS the reported case count, so a 13th directory there
# would silently change what "the frozen set" means. New cases land in an
# additive root and are run by pointing --scenarios-root at it.
FROZEN_ROOT = "evals/scenarios"
SCENARIO_ROOTS = (FROZEN_ROOT, "evals/scenarios-v2")


def find_gold(case_id: str) -> Path | None:
    """The gold.json for a case, in whichever scenario root defines it."""
    for root in SCENARIO_ROOTS:
        candidate = ROOT / root / case_id / "gold.json"
        if candidate.is_file():
            return candidate
    return None


def discover_cases(only: set[str] | None = None, root: str = FROZEN_ROOT) -> list[Case]:
    """Every scenario with a gold.json whose fixture exists, optionally filtered.

    A scenario authored ahead of its capture (gold.json present, fixture not
    yet recorded) is skipped with a loud warning rather than an error: case
    production is gold-first by design (evals/scenarios/README.md), and the
    freeze-time protection is the reported case COUNT in the final table, plus
    the explicit warning here. The reverse direction — a fixture without gold —
    stays a hard failure in checkpoints.sh and test_every_fixture_has_gold.
    """
    cases: list[Case] = []
    for gold_path in sorted(ROOT.glob(f"{root}/*/gold.json")):
        case_id = gold_path.parent.name
        if only is not None and case_id not in only:
            continue
        fixture = ROOT / "evals" / "fixtures" / case_id
        if not fixture.is_dir():
            print(
                f"WARNING: skipping {case_id} — gold.json present, no fixture captured yet",
                file=sys.stderr,
            )
            continue
        cases.append(Case(case_id=case_id, fixture=fixture, gold=scoring.load_gold(gold_path)))
    if not cases:
        raise FileNotFoundError(f"no scorable cases found under {root}/")
    return cases


def run_case(arm: ArmFn, case: Case, run_idx: int, case_dir: Path) -> Outcome:
    """Invoke the arm, then score its artifacts. Failures score wrong, loudly."""
    score: scoring.CaseScore | None = None
    error: str | None = None
    violations: tuple[str, ...] = ()
    metrics: dict[str, object] = {}
    try:
        arm(case.fixture, case.case_id, case_dir)
        answer = scoring.parse_answer((case_dir / "answer.json").read_text(encoding="utf-8"))
        score = scoring.score_case(answer, case.gold)
        violations = tuple(
            scoring.report_contract_violations((case_dir / "report.md").read_text(encoding="utf-8"))
        )
    except Exception as exc:  # recorded as a failed case row — never re-raised mid-run
        lowered = str(exc).lower()
        if any(marker in lowered for marker in _INFRA_MARKERS):
            raise InfrastructureError(
                f"billing/limit failure on {case.case_id} (run {run_idx}): {exc}"
            ) from exc
        error = f"{type(exc).__name__}: {exc}"
        score = None  # a case that failed anywhere is scored wrong, never half-counted
        violations = ()
    metrics_path = case_dir / "metrics.json"
    if metrics_path.is_file():
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    return Outcome(
        run=run_idx,
        case_id=case.case_id,
        tier=case.gold.tier,
        score=score,
        error=error,
        contract_violations=violations,
        metrics=metrics,
    )


def summarize(outcomes: list[Outcome]) -> scoring.Summary:
    """Frozen-scorer aggregation, with invalid (unscorable) cases folded in as wrong."""
    summary = scoring.aggregate([o.score for o in outcomes if o.score is not None])
    for outcome in outcomes:
        if outcome.score is None:
            for cell in (
                summary.overall,
                summary.by_tier.setdefault(outcome.tier, scoring.RateCell()),
                summary.by_verdict.setdefault("invalid", scoring.RateCell()),
            ):
                cell.cases += 1
    return summary


def _fmt(cell: scoring.RateCell | None) -> str:
    if cell is None or cell.cases == 0:
        return "—"
    return f"{cell.correct}/{cell.cases}"


def _summary_dict(summary: scoring.Summary) -> dict[str, object]:
    return {
        "overall": {"correct": summary.overall.correct, "cases": summary.overall.cases},
        "by_tier": {
            t: {"correct": c.correct, "cases": c.cases} for t, c in summary.by_tier.items()
        },
        "by_verdict": {
            v: {"correct": c.correct, "cases": c.cases} for v, c in summary.by_verdict.items()
        },
        "confirmed_wrong": summary.confirmed_wrong,
    }


def write_outputs(
    out_dir: Path, arm_name: str, runs: int, outcomes: list[Outcome], started_utc: str
) -> None:
    with (out_dir / "rows.jsonl").open("w", encoding="utf-8") as fh:
        for outcome in outcomes:
            fh.write(json.dumps(outcome.row()) + "\n")

    pooled = summarize(outcomes)
    per_run = {r: summarize([o for o in outcomes if o.run == r]) for r in range(1, runs + 1)}
    # cost_usd is None for an unpriced model and absent when the arm died before
    # metrics — count those instead of folding them to $0 (masked spend).
    cost, unmeasured, tokens_in, tokens_out = 0.0, 0, 0, 0
    for outcome in outcomes:
        case_cost = outcome.metrics.get("cost_usd")
        if isinstance(case_cost, int | float):
            cost += float(case_cost)
        else:
            unmeasured += 1
        case_in = outcome.metrics.get("input_tokens")
        if isinstance(case_in, int):
            tokens_in += case_in
        case_out = outcome.metrics.get("output_tokens")
        if isinstance(case_out, int):
            tokens_out += case_out

    summary_json: dict[str, object] = {
        "arm": arm_name,
        "runs": runs,
        "started_utc": started_utc,
        "model_pinned": None if arm_name in NO_MODEL_ARMS else PINNED_MODEL,
        "sampling": (
            "deterministic: no model is called"
            if arm_name in NO_MODEL_ARMS
            else "no sampling parameters (removed on the pinned model); replicate runs instead"
        ),
        "pooled": _summary_dict(pooled),
        "per_run": {str(r): _summary_dict(s) for r, s in per_run.items()},
        "totals": {
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "cost_usd": round(cost, 4),
            "cases_without_measured_cost": unmeasured,
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary_json, indent=2), encoding="utf-8")

    tiers = sorted({o.tier for o in outcomes})
    verdicts = ["confirmed", "probable", "inconclusive", "invalid"]
    lines = [
        f"# Scored run — arm: {arm_name}",
        "",
        f"- started: {started_utc}  |  runs: {runs}  |  cases/run: {len(outcomes) // runs}",
        (
            "- model: none — deterministic rules engine, no API call"
            if arm_name in NO_MODEL_ARMS
            else f"- model: {PINNED_MODEL} (pinned); no sampling parameters "
            "(removed on this model) — determinism reported over replicate runs"
        ),
        f"- totals: {tokens_in} in / {tokens_out} out tokens, ${cost:.4f}"
        + (f" — WARNING: {unmeasured} case(s) without measured cost" if unmeasured else ""),
        "",
        "| run | overall | " + " | ".join(tiers + verdicts) + " | confirmed-wrong |",
        "|" + "---|" * (len(tiers) + len(verdicts) + 3),
    ]
    for r, s in per_run.items():
        cells = [_fmt(s.by_tier.get(t)) for t in tiers] + [
            _fmt(s.by_verdict.get(v)) for v in verdicts
        ]
        lines.append(
            f"| {r} | {_fmt(s.overall)} | " + " | ".join(cells) + f" | {s.confirmed_wrong} |"
        )
    cells = [_fmt(pooled.by_tier.get(t)) for t in tiers] + [
        _fmt(pooled.by_verdict.get(v)) for v in verdicts
    ]
    lines.append(
        f"| pooled | {_fmt(pooled.overall)} | "
        + " | ".join(cells)
        + f" | {pooled.confirmed_wrong} |"
    )
    errors = [o for o in outcomes if o.error is not None]
    if errors:
        lines += ["", "## Failed cases", ""]
        lines += [f"- run {o.run} {o.case_id}: {o.error}" for o in errors]
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run and score one arm over the case set")
    parser.add_argument("--arm", choices=sorted(ARMS), required=True)
    parser.add_argument("--runs", type=int, default=3, help="replicate runs (default 3)")
    parser.add_argument("--cases", default=None, help="comma-separated case ids (default: all)")
    parser.add_argument(
        "--scenarios-root",
        default=FROZEN_ROOT,
        choices=SCENARIO_ROOTS,
        help=f"scenario root to score (default {FROZEN_ROOT}, the frozen set)",
    )
    parser.add_argument("--out", type=Path, default=None, help="results dir (default: timestamped)")
    args = parser.parse_args()

    load_env_file(ROOT / ".env")
    started_utc = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir: Path = args.out or ROOT / "evals" / "results" / f"{started_utc}-{args.arm}"
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = new_run_id()
    log = get_logger(run_id, name="run_eval")

    only = {c.strip() for c in args.cases.split(",")} if args.cases else None
    cases = discover_cases(only, args.scenarios_root)
    log.info("arm=%s cases=%d runs=%d out=%s", args.arm, len(cases), args.runs, out_dir)

    outcomes: list[Outcome] = []
    aborted: str | None = None
    try:
        for run_idx in range(1, args.runs + 1):
            for case in cases:
                case_dir = out_dir / f"run{run_idx}" / case.case_id
                outcome = run_case(ARMS[args.arm], case, run_idx, case_dir)
                outcomes.append(outcome)
                log.info(
                    "run %d %s: %s",
                    run_idx,
                    case.case_id,
                    outcome.error or f"root_cause_correct={outcome.row()['root_cause_correct']}",
                )
    except InfrastructureError as exc:
        aborted = str(exc)
        log.error("RUN ABORTED, no further API calls made: %s", aborted)

    write_outputs(out_dir, args.arm, args.runs, outcomes, started_utc)
    log.info("wrote %s", out_dir / "summary.md")
    print((out_dir / "summary.md").read_text(encoding="utf-8"))
    if aborted:
        (out_dir / "README.md").write_text(
            "# DISCLOSED-PARTIAL RUN — not a valid scored bundle\n\n"
            "The harness ABORTED on an infrastructure failure (billing/usage\n"
            "limit); no further API calls were made and no failure rows were\n"
            "recorded for the unreached case-runs. Completed rows (rows.jsonl)\n"
            "are genuine model outcomes; the summary covers only what ran.\n"
            "Do not quote this bundle as the baseline number — re-run the full\n"
            f"matrix after resolving:\n\n    {aborted}\n",
            encoding="utf-8",
        )
        raise SystemExit(f"aborted: {aborted} — bundle at {out_dir} marked DISCLOSED-PARTIAL")


if __name__ == "__main__":
    main()
