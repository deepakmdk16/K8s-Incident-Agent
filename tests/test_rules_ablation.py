"""Tests for the rules-only ablation arm (design req 8).

The arm's value depends entirely on its method being honest, so most of these
tests police the method rather than the behaviour: it must stay deterministic,
must not learn the case set, and must keep failing on the cases the changelog
claims it cannot do.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from ablation import rules
from common.report_contract import extract_answer
from evals import scoring

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "evals" / "fixtures"
ABLATION = REPO / "ablation"
CASES = sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())


def _fixture_ids() -> list[str]:
    return CASES


def test_case_set_is_present() -> None:
    assert len(CASES) >= 12


@pytest.mark.parametrize("case_id", _fixture_ids())
def test_arm_produces_a_scorable_answer(case_id: str, tmp_path: Path) -> None:
    """Contract with evals/run_eval.py: answer.json parses under the frozen scorer."""
    answer_path = rules.diagnose(FIXTURES / case_id, case_id, tmp_path / case_id)
    answer = scoring.parse_answer(answer_path.read_text(encoding="utf-8"))
    assert answer.case_id == case_id
    assert answer.verdict in scoring.VERDICTS


@pytest.mark.parametrize("case_id", _fixture_ids())
def test_report_satisfies_the_shared_contract(case_id: str, tmp_path: Path) -> None:
    """Same four sections and no numeric confidence, exactly as the LLM arms."""
    rules.diagnose(FIXTURES / case_id, case_id, tmp_path / case_id)
    report = (tmp_path / case_id / "report.md").read_text(encoding="utf-8")
    assert not list(scoring.report_contract_violations(report))
    # the report's trailing json block must be the answer, as for every arm
    assert json.loads(extract_answer(report))["case_id"] == case_id


@pytest.mark.parametrize("case_id", _fixture_ids())
def test_arm_is_deterministic(case_id: str, tmp_path: Path) -> None:
    """No model, no clock, no randomness: identical bytes on repeat runs."""
    first = rules.diagnose(FIXTURES / case_id, case_id, tmp_path / "a").read_text(encoding="utf-8")
    second = rules.diagnose(FIXTURES / case_id, case_id, tmp_path / "b").read_text(encoding="utf-8")
    assert first == second


@pytest.mark.parametrize("case_id", _fixture_ids())
def test_arm_costs_nothing(case_id: str, tmp_path: Path) -> None:
    metrics = json.loads(
        (
            rules.diagnose(FIXTURES / case_id, case_id, tmp_path / case_id).parent / "metrics.json"
        ).read_text(encoding="utf-8")
    )
    assert metrics["cost_usd"] == 0.0
    assert metrics["model"] is None


def test_ablation_never_learns_the_case_set() -> None:
    """The methodology constraint, mechanised.

    An analyzer that keys on a case id, a scenario namespace or a fixture
    workload name is a lookup table, and a lookup table proves nothing about
    what a rules engine can do (docs/experiments/2026-08-29-rules-ablation.md).
    """
    banned = set(CASES)
    for case_id in CASES:
        banned |= set(rules.fx.namespaces(FIXTURES / case_id)) - {"default"}
        gold = json.loads(
            (REPO / "evals" / "scenarios" / case_id / "gold.json").read_text(encoding="utf-8")
        )
        banned.add(str(gold["failing_resource"]["name"]))
    # namespaces every real cluster has are not case knowledge
    banned -= {"kube-system", "kube-public", "kube-node-lease", "local-path-storage"}

    # Only string literals count: that is where a lookup table would keep case
    # data. Bare identifiers collide with ordinary code (a namespace named
    # "search" against `re.search`) and would make this tripwire noise.
    hits: list[str] = []
    for path in sorted(ABLATION.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        literals = [
            node.value.lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        for token in banned:
            pattern = rf"\b{re.escape(token.lower())}\b"
            hits += [f"{path.name}: {token}" for literal in literals if re.search(pattern, literal)]
    assert not hits, f"ablation arm references the evaluation case set: {sorted(set(hits))}"


def test_ablation_makes_no_network_or_model_import() -> None:
    """A 'rules-only' arm that could call a model would invalidate the comparison.

    AST-based, not substring: `from evals import scoring` and
    `from common.llm import complete` contain no "import evals.scoring"
    substring, so a text check holds nothing (the first version of this test
    was exactly that hollow). Prefix-matching the resolved module path closes
    every from-import spelling; `evals` is banned wholesale — importing the
    scorer would let the arm answer to the grader.
    """
    banned = ("anthropic", "requests", "httpx", "urllib", "socket", "common.llm", "evals")

    def is_banned(module: str) -> bool:
        return any(module == b or module.startswith(f"{b}.") for b in banned)

    for path in sorted(ABLATION.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hits: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                hits += [a.name for a in node.names if is_banned(a.name)]
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and is_banned(node.module)
            ):
                hits.append(node.module)
        assert not hits, f"{path.name} imports banned module(s): {hits}"


@pytest.mark.parametrize(
    "case_id",
    ["t1-pvc-storageclass-typo", "t2-quota-blocks-scale", "t2-rbac-sync-forbidden"],
)
def test_cases_the_ablation_demonstrably_cannot_do(case_id: str, tmp_path: Path) -> None:
    """The >=3 failing cases design req 8 requires, pinned as a regression test.

    These are quoted in README/CHANGELOG as what pattern-matching cannot do; if
    a later change makes one pass, the claim must be rewritten, not silently
    left stale.
    """
    answer_path = rules.diagnose(FIXTURES / case_id, case_id, tmp_path / case_id)
    gold = scoring.load_gold(REPO / "evals" / "scenarios" / case_id / "gold.json")
    score = scoring.score_case(scoring.parse_answer(answer_path.read_text(encoding="utf-8")), gold)
    assert not score.root_cause_correct


def test_ambiguity_is_recorded_not_hidden(tmp_path: Path) -> None:
    """Precedence-resolved cases must disclose the analyzers they discarded."""
    case_id = "t3-overlapping-config-and-oom"
    rules.diagnose(FIXTURES / case_id, case_id, tmp_path / case_id)
    metrics = json.loads((tmp_path / case_id / "metrics.json").read_text(encoding="utf-8"))
    report = (tmp_path / case_id / "report.md").read_text(encoding="utf-8")
    assert metrics["analyzers_fired"] > 1
    assert "on no evidence" in report
