"""Tests for the frozen scoring spec (evals/scoring.md; evals/scoring.py is normative)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals import run_eval
from evals.scoring import (
    Answer,
    CaseScore,
    FailingResource,
    FaultClass,
    aggregate,
    classify_mechanism,
    load_gold,
    numeric_confidence_present,
    parse_answer,
    report_contract_violations,
    score_case,
)

REPO = Path(__file__).resolve().parents[1]
GOLD_FILES = sorted((REPO / "evals" / "scenarios").glob("*/gold.json"))
T1_GOLD = REPO / "evals" / "scenarios" / "t1-crashloop-missing-env" / "gold.json"


def _answer(
    mechanism: str,
    kind: str = "deployment",
    name: str = "checkout-worker",
    verdict: str = "confirmed",
) -> Answer:
    return Answer(
        case_id="t1-crashloop-missing-env",
        failing_resource=FailingResource(kind=kind, namespace="payments", name=name),
        mechanism=mechanism,
        verdict=verdict,
    )


# --- gold ground truth ------------------------------------------------------


def test_at_least_one_gold_exists() -> None:
    # Guards the parametrized tests below from passing vacuously on an empty glob.
    assert GOLD_FILES, "no gold.json found under evals/scenarios/*/"


@pytest.mark.parametrize("path", GOLD_FILES, ids=lambda p: p.parent.name)
def test_gold_validates_and_matches_directory(path: Path) -> None:
    gold = load_gold(path)
    assert gold.case_id == path.parent.name


@pytest.mark.parametrize("path", GOLD_FILES, ids=lambda p: p.parent.name)
def test_gold_mechanism_self_classifies(path: Path) -> None:
    # The rubric must at minimum recognize its own ground truth (scoring.md).
    gold = load_gold(path)
    assert classify_mechanism(gold.mechanism_summary) == frozenset({gold.fault_class})


def test_every_fixture_has_gold() -> None:
    """A fixture nothing can score is a capture with no ground truth.

    Checked against every scenario root, not just the frozen one: a v2 case
    ships its fixture into the same evals/fixtures/ tree so the credential scan
    and schema gate reach it without being taught a second location.
    """
    fixtures = sorted(d.name for d in (REPO / "evals" / "fixtures").iterdir() if d.is_dir())
    assert fixtures, "no fixtures found under evals/fixtures/"
    missing = [fix for fix in fixtures if run_eval.find_gold(fix) is None]
    assert not missing, f"fixtures without gold.json in any scenario root: {missing}"


def test_gold_rejects_unknown_class_and_tier(tmp_path: Path) -> None:
    good = json.loads(T1_GOLD.read_text(encoding="utf-8"))
    for corruption in ({"fault_class": "meteor-strike"}, {"tier": "T9"}):
        bad = tmp_path / "gold.json"
        bad.write_text(json.dumps(good | corruption), encoding="utf-8")
        with pytest.raises(ValueError):
            load_gold(bad)


# --- answer schema ----------------------------------------------------------


def test_parse_answer_roundtrip() -> None:
    raw = json.dumps(
        {
            "case_id": "t1-crashloop-missing-env",
            "failing_resource": {
                "kind": "deploy",
                "namespace": "payments",
                "name": "checkout-worker",
            },
            "mechanism": "The worker exits at startup; kubelet restarts it.",
            "verdict": "probable",
        }
    )
    answer = parse_answer(raw)
    assert answer.verdict == "probable"
    assert answer.failing_resource.normalized() == ("deployment", "payments", "checkout-worker")


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        json.dumps({"case_id": "x"}),
        json.dumps(
            {
                "case_id": "x",
                "failing_resource": {"kind": "pod", "namespace": "a", "name": "b"},
                "mechanism": "m",
                "verdict": "certain",
            }
        ),
        json.dumps(
            {
                "case_id": "x",
                "failing_resource": {"kind": "pod", "namespace": "a", "name": "b"},
                "mechanism": "m",
                "verdict": "inconclusive",
            }
        ),
    ],
    ids=["not-json", "missing-fields", "bad-verdict", "inconclusive-without-missing-evidence"],
)
def test_parse_answer_rejects(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_answer(raw)


# --- mechanism -> class rubric ----------------------------------------------


def test_t1_correct_answer_scores_root_cause_correct() -> None:
    gold = load_gold(T1_GOLD)
    answer = _answer(
        "The container requires AMQP_URL which is unset; it logs a FATAL error and "
        "exits 1, so kubelet restarts it with backoff."
    )
    score = score_case(answer, gold)
    assert score.resource_correct
    assert score.matched_classes == frozenset({FaultClass.APP_CRASHLOOP})
    assert score.root_cause_correct


def test_symptom_pod_instead_of_owner_is_wrong() -> None:
    gold = load_gold(T1_GOLD)
    answer = _answer(
        "The container exits at startup and restarts in a crash loop.",
        kind="pod",
        name="checkout-worker-56d848b6d-tpzjs",
    )
    score = score_case(answer, gold)
    assert score.class_correct
    assert not score.resource_correct
    assert not score.root_cause_correct


def test_wrong_mechanism_class_is_wrong() -> None:
    gold = load_gold(T1_GOLD)
    answer = _answer("The image tag is bogus, so the kubelet cannot pull the image.")
    score = score_case(answer, gold)
    assert score.matched_classes == frozenset({FaultClass.IMAGE_PULL_BACKOFF})
    assert not score.class_correct


def test_configmap_sourcing_mention_does_not_dominate_crashloop() -> None:
    # Regression (code review 2026-08-28): a correct app-crashloop answer that
    # mentions where the env var SHOULD come from must not collapse to
    # bad-config-ref via bare "configmap" + "missing".
    matched = classify_mechanism(
        "The deployment should source AMQP_URL from a ConfigMap but the env var is "
        "missing; the container exits FATAL at startup and crashloops with backoff."
    )
    assert matched == frozenset({FaultClass.APP_CRASHLOOP})


def test_true_config_ref_failure_still_classifies() -> None:
    matched = classify_mechanism(
        'The pod references ConfigMap "app-config" which is not found, so the '
        "container stays in CreateContainerConfigError."
    )
    assert matched == frozenset({FaultClass.BAD_CONFIG_REF})


def test_dominance_collapses_oom_over_crashloop() -> None:
    matched = classify_mechanism(
        "The worker exceeded its memory limit and was OOMKilled (exit code 137); "
        "kubelet restarts it and the pod cycles through backoff."
    )
    assert matched == frozenset({FaultClass.OOM_KILLED})


def test_shotgun_mechanism_is_ambiguous() -> None:
    matched = classify_mechanism(
        "The service selector does not match the pod labels, and the namespace "
        "resource quota is exceeded."
    )
    assert len(matched) == 2


def test_empty_match_scores_wrong() -> None:
    gold = load_gold(T1_GOLD)
    score = score_case(answer=_answer("Something is broken somewhere."), gold=gold)
    assert score.matched_classes == frozenset()
    assert not score.class_correct


# Regression (roster red-team 2026-08-29, evals/out/20260829-roster-red-team.json):
# natural, correct mechanism prose for the roster cases must classify to exactly
# its gold class — including answers that name a decoy only to negate it, and
# phrasings the signatures previously zero-matched (renamed key) or co-matched
# (readiness vocabulary inside selector-mismatch prose).
@pytest.mark.parametrize(
    ("mechanism", "expected"),
    [
        (
            "The search Service's selector does not match the search pods' labels, so "
            "its Endpoints object is empty; the crashlooping batch job is unrelated.",
            FaultClass.SERVICE_SELECTOR_MISMATCH,
        ),
        (
            "Pods are Ready and readiness probes pass, yet Endpoints is empty because "
            "the Service selector does not match the pod labels.",
            FaultClass.SERVICE_SELECTOR_MISMATCH,
        ),
        (
            "The readiness probe targets port 8081 but the container listens on 8080, "
            "so probes fail and pods never become Ready, leaving Endpoints empty.",
            FaultClass.READINESS_PROBE_FAILING,
        ),
        (
            "The deployment references a renamed ConfigMap key (couldn't find key "
            "db_url), so container creation fails; the report worker's restarts and "
            "OOM state are unrelated.",
            FaultClass.BAD_CONFIG_REF,
        ),
        (
            "The RoleBinding subject names a nonexistent ServiceAccount, so the sync "
            "worker's API requests return 403 Forbidden.",
            FaultClass.RBAC_DENIAL,
        ),
        (
            "The cache warmup exceeds the container's 64Mi memory limit and the "
            "kernel OOM-kills it (exit code 137) on every start.",
            FaultClass.OOM_KILLED,
        ),
    ],
    ids=[
        "t3-selector-negated-decoy",
        "selector-probes-pass",
        "readiness-wrong-port",
        "renamed-key-negated-oom",
        "rolebinding-403",
        "oom-gold-form",
    ],
)
def test_roster_phrasings_classify_exactly(mechanism: str, expected: FaultClass) -> None:
    assert classify_mechanism(mechanism) == frozenset({expected})


# --- aggregation & calibration ----------------------------------------------


def test_aggregate_rates_and_confirmed_wrong() -> None:
    def row(tier: str, verdict: str, ok: bool) -> CaseScore:
        return CaseScore(
            case_id=f"c-{tier}-{verdict}-{ok}",
            tier=tier,
            verdict=verdict,
            resource_correct=ok,
            matched_classes=frozenset(),
            class_correct=ok,
        )

    summary = aggregate(
        [
            row("T1", "confirmed", ok=True),
            row("T1", "confirmed", ok=False),
            row("T2", "probable", ok=True),
            row("T2", "inconclusive", ok=False),
        ]
    )
    assert summary.overall.cases == 4
    assert summary.overall.correct == 2
    assert summary.by_tier["T1"].rate() == 0.5
    assert summary.by_verdict["confirmed"].rate() == 0.5
    assert summary.confirmed_wrong == 1
    assert aggregate([]).overall.rate() is None


# --- report contract ---------------------------------------------------------


def test_numeric_confidence_detection() -> None:
    assert numeric_confidence_present("I am 85% confident in this diagnosis.")
    assert numeric_confidence_present("Confidence: 0.9")
    assert not numeric_confidence_present(
        "5% of requests returned 503; exit code 137 in the last restart."
    )
    # Regression (code review 2026-08-28): a count or exit code that merely
    # shares a sentence with qualitative confidence wording is not banned.
    assert not numeric_confidence_present(
        "After 3 restarts we are certain the cause is the missing env var."
    )
    assert not numeric_confidence_present(
        "Exit code 137 confirms it; we are confident in the OOM diagnosis."
    )


def test_report_contract_checks() -> None:
    compliant = (
        "## Root cause\nx\n## Evidence chain\nx\n"
        "## Investigation ledger\nx\n## Verification recipe\nx\n"
    )
    assert report_contract_violations(compliant) == []
    violations = report_contract_violations("## Root cause\nI am 90% sure.\n")
    assert any("verification recipe" in v for v in violations)
    assert any("self-confidence" in v for v in violations)


# --- anti-leak tripwire ------------------------------------------------------


def test_fault_enum_never_leaks_into_agent_dirs() -> None:
    """The class enum in an agent prompt turns diagnosis into multiple choice.

    Mechanical tripwire for the exact enum forms; the semantic rule (no fault
    taxonomy in prompts, any spelling) is held by review (evals/scoring.md).
    """
    banned = [cls.value for cls in FaultClass] + ["FaultClass", "evals.scoring", "evals/scoring"]
    hits: list[str] = []
    for agent_dir in (REPO / "baseline", REPO / "solution"):
        for path in sorted(agent_dir.rglob("*")):
            if not path.is_file() or path.name == ".gitkeep":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            hits.extend(f"{path.relative_to(REPO)}: {token}" for token in banned if token in text)
    assert not hits, f"fault-class enum leaked into agent code/prompts: {hits}"
