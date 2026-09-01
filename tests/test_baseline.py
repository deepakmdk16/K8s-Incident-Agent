"""Offline tests for the baseline arm: curation policy and diagnose plumbing.

No network, no live LLM — the model call is a fake. The curation tests run
against the real first fixture so the policy is proven on captured data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from baseline.curate import LOG_TAIL_LINES, curate
from baseline.diagnose import build_prompt, diagnose
from common.llm import LLMResult
from common.report_contract import extract_answer

FIXTURE = Path(__file__).resolve().parents[1] / "evals" / "fixtures" / "t1-crashloop-missing-env"


def test_curate_selects_the_failing_workload() -> None:
    dump = curate(FIXTURE)
    titles = [s.title for s in dump.sections]
    # Pod/ReplicaSet hashes change on every (disclosed) re-capture of the
    # fixture; assert the failing workload's ownership chain by prefix, never
    # by a pinned hash (regression: the t1 --force re-capture broke the
    # original literal-name assertion).
    assert any(
        t.startswith("kubectl describe pod/checkout-worker-") and t.endswith("-n payments")
        for t in titles
    )
    assert "kubectl describe deployment.apps/checkout-worker -n payments" in titles
    assert any(
        t.startswith("kubectl describe replicaset.apps/checkout-worker-")
        and t.endswith("-n payments")
        for t in titles
    )


def test_curate_includes_both_log_channels() -> None:
    dump = curate(FIXTURE)
    log_titles = [s.title for s in dump.sections if s.title.startswith("kubectl logs")]
    assert len(log_titles) == 2
    assert any("--previous" in t for t in log_titles)
    assert all(f"--tail={LOG_TAIL_LINES}" in t for t in log_titles)


def test_curate_excludes_healthy_namespaces() -> None:
    dump = curate(FIXTURE)
    assert all("-n payments" in s.title for s in dump.sections)


def test_prompt_carries_page_dump_and_contract() -> None:
    dump = curate(FIXTURE)
    prompt = build_prompt("t1-crashloop-missing-env", dump)
    assert "CheckoutWorkerDown" in prompt  # the page
    assert "$ kubectl get all -A" in prompt
    assert '"failing_resource"' in prompt  # the shared output contract
    assert "## Investigation ledger" in prompt


def test_extract_answer_takes_the_last_json_block() -> None:
    text = '```json\n{"first": 1}\n```\ntext\n```json\n{"second": 2}\n```\n'
    assert json.loads(extract_answer(text)) == {"second": 2}
    with pytest.raises(ValueError, match="no fenced json"):
        extract_answer("no blocks here")


def _fake_response(text: str) -> LLMResult:
    return LLMResult(
        text=text, model="fake", input_tokens=10, output_tokens=5, stop_reason="end_turn"
    )


def test_diagnose_writes_all_artifacts(tmp_path: Path) -> None:
    canned = (
        "## Root cause\nx\n## Evidence chain\nx\n## Investigation ledger\nx\n"
        '## Verification recipe\nx\n```json\n{"case_id": "t1-crashloop-missing-env"}\n```\n'
    )
    answer_path = diagnose(
        FIXTURE, "t1-crashloop-missing-env", tmp_path, complete_fn=lambda _p: _fake_response(canned)
    )
    assert json.loads(answer_path.read_text()) == {"case_id": "t1-crashloop-missing-env"}
    metrics = json.loads((tmp_path / "metrics.json").read_text())
    assert metrics["input_tokens"] == 10
    assert (tmp_path / "prompt.txt").is_file()
    assert (tmp_path / "report.md").read_text() == canned


def test_diagnose_keeps_metrics_when_answer_block_is_missing(tmp_path: Path) -> None:
    """A response with no answer block still spent tokens; the spend must survive."""
    with pytest.raises(ValueError, match="no fenced json"):
        diagnose(
            FIXTURE,
            "t1-crashloop-missing-env",
            tmp_path,
            complete_fn=lambda _p: _fake_response("report text, no answer block"),
        )
    assert json.loads((tmp_path / "metrics.json").read_text())["output_tokens"] == 5
    assert (tmp_path / "report.md").is_file()
    assert not (tmp_path / "answer.json").exists()
