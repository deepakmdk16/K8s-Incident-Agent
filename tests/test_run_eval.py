"""Offline tests for the scored-run harness. Fake arms only — no LLM calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals import hedging, scoring, scoring_v2
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
        # No guard on the directory: a registered root that is missing raises
        # inside discover_cases, so the test fails instead of passing vacuously.
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
    for root in SCENARIO_ROOTS:
        if root == FROZEN_ROOT:
            continue
        # No guard on the directory: every registered root exists, and a missing
        # one raises inside discover_cases — this must never pass vacuously.
        for case in discover_cases(root=root):
            assert isinstance(case.gold, scoring_v2.Gold), root
            assert case.scorer == "evals.scoring_v2"


def test_the_held_out_root_is_registered_as_additive() -> None:
    """STATUS 3.2: the held-out set is an additive root after the frozen one, so it
    is scored by the v2 scorer and never joins the frozen count."""
    assert SCENARIO_ROOTS[0] == FROZEN_ROOT
    assert "evals/scenarios-heldout" in SCENARIO_ROOTS[1:]


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


# --- hedging report: price every verdict, report only, no bar (STATUS 2.3) ---


def test_hedging_prices_each_verdict_with_coverage_and_accuracy() -> None:
    """Every verdict value present gets its row count, its share of all rows and
    the root-cause rate among its rows; a row with no verdict lands in an
    explicit "none" bucket rather than vanishing."""
    rows = [
        hedging.Row("confirmed", True, "deployment"),
        hedging.Row("confirmed", True, "deployment"),
        hedging.Row("confirmed", False, "deployment"),
        hedging.Row("probable", False, "service"),  # a verdict with zero correct rows
        hedging.Row(None, False, "service"),
    ]
    report = hedging.Report.from_rows(rows)
    assert report.rows == 5
    assert list(report.by_verdict) == ["confirmed", "probable", "none"]
    cells = {v: (c.cases, c.correct) for v, c in report.by_verdict.items()}
    assert cells == {"confirmed": (3, 2), "probable": (1, 0), "none": (1, 0)}
    assert report.as_dict()["by_verdict"] == {
        "confirmed": {"rows": 3, "correct": 2, "coverage": 0.6, "accuracy": 0.6667},
        "probable": {"rows": 1, "correct": 0, "coverage": 0.2, "accuracy": 0.0},
        "none": {"rows": 1, "correct": 0, "coverage": 0.2, "accuracy": 0.0},
    }
    assert "| probable | 1/5 (20.0%) | 0/1 (0.0%) |" in report.markdown_lines()


def test_hedging_splits_inconclusives_by_what_the_solution_tools_can_read() -> None:
    """The readability notion is the solution's tool layer (tools.serves_kind) on
    the gold failing-resource kind, applied to any arm's rows. A kind the layer
    does not know is unreadable, not a crash; a non-hedge is not counted."""
    rows = [
        hedging.Row("inconclusive", False, "deployment"),
        hedging.Row("inconclusive", True, "RoleBinding"),  # kubectl spelling, still read
        hedging.Row("inconclusive", False, "clusterrole"),  # cluster-scoped, no tool serves it
        hedging.Row("inconclusive", False, "no-such-kind"),
        hedging.Row("confirmed", False, "clusterrole"),
    ]
    report = hedging.Report.from_rows(rows)
    assert (report.inconclusive_readable, report.inconclusive_unreadable) == (2, 2)
    payload = report.as_dict()
    assert payload["inconclusive_on_readable_kind"] == 2
    assert payload["inconclusive_on_unreadable_kind"] == 2
    assert "serves_kind" in str(payload["readability"])
    assert hedging.Report.from_rows([]).as_dict()["by_verdict"] == {}


def test_write_outputs_adds_the_hedging_report_beside_the_existing_keys(tmp_path: Path) -> None:
    """New keys are added; nothing existing is renamed, removed or re-shaped."""
    hedged_answer = CORRECT_ANSWER | {"verdict": "inconclusive", "missing_evidence": "logs"}
    good = run_case(_arm_writing(CORRECT_ANSWER, REPORT), _case(), 1, tmp_path / "a")
    hedged = run_case(_arm_writing(hedged_answer, REPORT), _case(), 1, tmp_path / "b")
    bad = run_case(_arm_writing({"nope": True}, REPORT), _case(), 1, tmp_path / "c")
    write_outputs(tmp_path, "baseline", 1, [good, hedged, bad], "20260828T000000Z")

    summary = json.loads((tmp_path / "summary.json").read_text())
    assert set(summary) == {
        "arm",
        "runs",
        "started_utc",
        "scorer",
        "model_pinned",
        "sampling",
        "pooled",
        "per_run",
        "totals",
        "hedging",
    }
    assert set(summary["pooled"]) == {"overall", "by_tier", "by_verdict", "confirmed_wrong"}
    assert summary["pooled"]["by_verdict"]["inconclusive"] == {"correct": 1, "cases": 1}
    assert summary["hedging"]["by_verdict"] == {
        "confirmed": {"rows": 1, "correct": 1, "coverage": 0.3333, "accuracy": 1.0},
        "inconclusive": {"rows": 1, "correct": 1, "coverage": 0.3333, "accuracy": 1.0},
        "invalid": {"rows": 1, "correct": 0, "coverage": 0.3333, "accuracy": 0.0},
    }
    # the gold object is a Deployment, which the solution's tools read
    assert summary["hedging"]["inconclusive_on_readable_kind"] == 1
    assert summary["hedging"]["inconclusive_on_unreadable_kind"] == 0

    rows = [json.loads(line) for line in (tmp_path / "rows.jsonl").read_text().splitlines()]
    assert set(rows[0]) == {
        "run",
        "case_id",
        "tier",
        "error",
        "contract_violations",
        "metrics",
        "verdict",
        "resource_correct",
        "matched_classes",
        "class_correct",
        "root_cause_correct",
    }
    assert rows[2]["verdict"] == "invalid"

    md = (tmp_path / "summary.md").read_text()
    assert "| pooled | 2/3 |" in md
    assert "| inconclusive | 1/3 (33.3%) | 1/1 (100.0%) |" in md
    assert "| invalid | 1/3 (33.3%) | 0/1 (0.0%) |" in md
    assert "inconclusive on a readable kind: 1; on an unreadable kind: 0" in md
