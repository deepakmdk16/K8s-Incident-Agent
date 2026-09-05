"""Offline tests for the scored-run harness. Fake arms only — no LLM calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals import scoring, scoring_v2
from evals.run_eval import (
    FROZEN_ROOT,
    SCENARIO_ROOTS,
    ArmFn,
    Case,
    InfrastructureError,
    aggregate,
    discover_cases,
    find_gold,
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


def test_the_frozen_case_set_keeps_its_identity() -> None:
    """A new case must not silently join the set every reported number is about.

    The frozen set's identity IS its count — evals/reported.json asserts 12
    cases x 3 runs — so an additive root is the only place a 13th case can
    land. This is the check that catches a v2 case authored into the wrong
    directory, which no other gate would notice until the bar failed.
    """
    frozen = discover_cases()
    assert len(frozen) == 12
    assert all((ROOT / FROZEN_ROOT / case.case_id).is_dir() for case in frozen)


def test_an_additive_root_is_discovered_separately() -> None:
    for root in SCENARIO_ROOTS:
        if root == FROZEN_ROOT:
            continue
        if not (ROOT / root).is_dir():
            continue
        extra = discover_cases(root=root)
        assert extra, f"{root} exists but discovers no case"
        assert not {c.case_id for c in extra} & {c.case_id for c in discover_cases()}


def test_find_gold_locates_a_case_in_either_root() -> None:
    assert find_gold("t1-crashloop-missing-env") == GOLD
    assert find_gold("no-such-case") is None


# --- scorer selection by root (2026-09-04) ------------------------------------


def test_each_root_is_scored_by_its_own_scorer() -> None:
    """The frozen set is never scored by anything but the frozen module."""
    for case in discover_cases():
        assert isinstance(case.gold, scoring.Gold)
        assert case.scorer == "evals.scoring"
    for case in discover_cases(root="evals/scenarios-v2"):
        assert isinstance(case.gold, scoring_v2.Gold)
        assert case.scorer == "evals.scoring_v2"


def test_aggregate_matches_the_frozen_aggregate_on_frozen_rows() -> None:
    def row(tier: str, verdict: str, ok: bool) -> scoring.CaseScore:
        return scoring.CaseScore(
            case_id=f"c-{tier}-{verdict}-{ok}",
            tier=tier,
            verdict=verdict,
            resource_correct=ok,
            matched_classes=frozenset(),
            class_correct=ok,
        )

    rows = [
        row("T1", "confirmed", ok=True),
        row("T1", "confirmed", ok=False),
        row("T2", "probable", ok=True),
        row("T3", "inconclusive", ok=False),
    ]
    ours, frozen = aggregate(list(rows)), scoring.aggregate(rows)
    assert (ours.overall.cases, ours.overall.correct) == (
        frozen.overall.cases,
        frozen.overall.correct,
    )
    assert {t: (c.cases, c.correct) for t, c in ours.by_tier.items()} == {
        t: (c.cases, c.correct) for t, c in frozen.by_tier.items()
    }
    assert {v: (c.cases, c.correct) for v, c in ours.by_verdict.items()} == {
        v: (c.cases, c.correct) for v, c in frozen.by_verdict.items()
    }
    assert ours.confirmed_wrong == frozen.confirmed_wrong == 1


def test_a_v2_case_scores_a_cluster_scoped_answer_through_the_v2_scorer(tmp_path: Path) -> None:
    gold = scoring_v2.Gold(
        case_id="t9-webhook",
        tier="T2",
        failing_resource=scoring.FailingResource(
            "validatingwebhookconfiguration", "", "workload-standards"
        ),
        fault_class=scoring_v2.FaultClass.WEBHOOK_ADMISSION_BLOCK,
        mechanism_summary="x",
        decisive_evidence="y",
        remediation_summary="z",
    )
    case = Case(case_id="t9-webhook", fixture=GOLD.parent, gold=gold)
    answer = {
        "case_id": "t9-webhook",
        "failing_resource": {
            "kind": "ValidatingWebhookConfiguration",
            "namespace": "cluster-scoped",
            "name": "workload-standards",
        },
        "mechanism": (
            "The validating webhook's service does not exist, so with failurePolicy Fail the "
            "API server refuses every pod create with 'failed calling webhook'."
        ),
        "verdict": "probable",
    }
    outcome = run_case(_arm_writing(answer, REPORT), case, 1, tmp_path / "w")
    assert outcome.error is None
    assert outcome.score is not None and outcome.score.root_cause_correct
    assert outcome.row()["matched_classes"] == ["webhook-admission-block"]
    write_outputs(tmp_path, "rules", 1, [outcome], "20260904T000000Z", "evals.scoring_v2")
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["scorer"] == "evals.scoring_v2"
    assert "- scorer: evals.scoring_v2" in (tmp_path / "summary.md").read_text()
