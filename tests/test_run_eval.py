"""Offline tests for the scored-run harness. Fake arms only — no LLM calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.run_eval import (
    ArmFn,
    Case,
    InfrastructureError,
    discover_cases,
    run_case,
    summarize,
    write_outputs,
)
from evals.scoring import load_gold

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "evals" / "scenarios" / "t1-crashloop-missing-env" / "gold.json"

CORRECT_ANSWER = {
    "case_id": "t1-crashloop-missing-env",
    "failing_resource": {"kind": "deployment", "namespace": "payments", "name": "checkout-worker"},
    "mechanism": (
        "The worker container requires AMQP_URL which is unset, so it exits fatally "
        "at startup and kubelet restarts it in crash-loop backoff."
    ),
    "verdict": "confirmed",
}

REPORT = (
    "## Root cause\nx\n## Evidence chain\nx\n## Investigation ledger\nx\n"
    "## Verification recipe\nx\n"
)


def _case() -> Case:
    return Case(case_id="t1-crashloop-missing-env", fixture=GOLD.parent, gold=load_gold(GOLD))


def _arm_writing(answer: object, report: str) -> ArmFn:
    def arm(fixture: Path, case_id: str, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "answer.json").write_text(json.dumps(answer), encoding="utf-8")
        (out_dir / "report.md").write_text(report, encoding="utf-8")
        (out_dir / "metrics.json").write_text(
            json.dumps({"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.01}),
            encoding="utf-8",
        )
        return out_dir / "answer.json"

    return arm


def test_discover_finds_the_first_case() -> None:
    cases = discover_cases()
    assert any(c.case_id == "t1-crashloop-missing-env" for c in cases)


def test_run_case_scores_a_correct_answer(tmp_path: Path) -> None:
    outcome = run_case(_arm_writing(CORRECT_ANSWER, REPORT), _case(), 1, tmp_path)
    assert outcome.error is None
    assert outcome.score is not None and outcome.score.root_cause_correct
    assert outcome.contract_violations == ()
    assert outcome.metrics["cost_usd"] == 0.01


def test_run_case_aborts_on_billing_or_limit_failure(tmp_path: Path) -> None:
    # Infrastructure failures poison every later call: abort loudly instead of
    # recording rows that blend model outcomes with billing refusals
    # (docs/failure-modes.md 2026-08-29).
    def broke_arm(fixture: Path, case_id: str, out: Path) -> Path:
        raise RuntimeError("Error code: 400 - Your credit balance is too low to access the API")

    def capped_arm(fixture: Path, case_id: str, out: Path) -> Path:
        raise RuntimeError("400 - You have reached your specified API usage limits.")

    with pytest.raises(InfrastructureError):
        run_case(broke_arm, _case(), 1, tmp_path / "a")
    with pytest.raises(InfrastructureError):
        run_case(capped_arm, _case(), 1, tmp_path / "b")


def test_run_case_records_invalid_answer_as_failure(tmp_path: Path) -> None:
    outcome = run_case(_arm_writing({"nope": True}, REPORT), _case(), 1, tmp_path)
    assert outcome.score is None
    assert outcome.error is not None and "invalid answer" in outcome.error
    assert outcome.row()["root_cause_correct"] is False


def test_run_case_failure_after_scoring_discards_the_score(tmp_path: Path) -> None:
    """A correct answer whose report is missing must count wrong, not half-succeed."""

    def arm(fixture: Path, case_id: str, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "answer.json").write_text(json.dumps(CORRECT_ANSWER), encoding="utf-8")
        return out_dir / "answer.json"  # no report.md

    outcome = run_case(arm, _case(), 1, tmp_path)
    assert outcome.score is None
    assert outcome.error is not None and "report.md" in outcome.error
    assert outcome.row()["root_cause_correct"] is False


def test_summarize_folds_invalid_cases_into_denominators(tmp_path: Path) -> None:
    good = run_case(_arm_writing(CORRECT_ANSWER, REPORT), _case(), 1, tmp_path / "a")
    bad = run_case(_arm_writing({"nope": True}, REPORT), _case(), 1, tmp_path / "b")
    summary = summarize([good, bad])
    assert summary.overall.cases == 2 and summary.overall.correct == 1
    assert summary.by_verdict["invalid"].cases == 1


def test_write_outputs_emits_rows_and_summaries(tmp_path: Path) -> None:
    outcome = run_case(_arm_writing(CORRECT_ANSWER, REPORT), _case(), 1, tmp_path / "c")
    write_outputs(tmp_path, "baseline", 1, [outcome], "20260828T000000Z")
    rows = [json.loads(line) for line in (tmp_path / "rows.jsonl").read_text().splitlines()]
    assert rows[0]["root_cause_correct"] is True
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["pooled"]["overall"] == {"correct": 1, "cases": 1}
    assert summary["totals"]["cost_usd"] == 0.01
    assert "| pooled | 1/1 |" in (tmp_path / "summary.md").read_text()
