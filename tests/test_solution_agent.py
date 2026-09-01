"""Offline tests for the agent loop, driven by a scripted fake model.

No network, no LLM: `converse_fn` is the production-signature injection seam, so
every terminal path of the loop — accept, reject-then-resubmit, salvage, budget
cap, and a billing outage — is exercised against real fixture data.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path

import pytest
from anthropic.types import MessageParam

from common.llm import ToolCall, ToolSpec, Turn
from solution import agent

ROOT = Path(__file__).resolve().parents[1]
RBAC = ROOT / "evals" / "fixtures" / "t2-rbac-sync-forbidden"
CASE = "t2-rbac-sync-forbidden"
POD = "inventory-sync-5cf949f7f9-czxsq"


def _turn(*calls: ToolCall, text: str = "", output_tokens: int = 120, cost_tokens: int = 0) -> Turn:
    return Turn(
        text=text or ("working" if calls else "done"),
        tool_calls=calls,
        assistant_content=[],
        stop_reason="tool_use" if calls else "end_turn",
        model="claude-opus-5",
        input_tokens=cost_tokens or 500,
        output_tokens=output_tokens,
        cache_write_tokens=0,
        cache_read_tokens=200,
    )


class Script:
    """Replays scripted turns and records what the loop sent."""

    def __init__(self, turns: Sequence[Turn | Exception]) -> None:
        self.turns = list(turns)
        self.seen: list[list[MessageParam]] = []

    def __call__(
        self, messages: Sequence[MessageParam], system: str, tools: Sequence[ToolSpec]
    ) -> Turn:
        del system, tools
        self.seen.append(list(messages))
        if not self.turns:
            raise AssertionError("the loop called the model more times than the script allows")
        nxt = self.turns.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _submit_args(**over: object) -> dict[str, object]:
    args: dict[str, object] = {
        "failing_resource": {
            "kind": "rolebinding",
            "namespace": "inventory",
            "name": "inventory-reader-binding",
        },
        "remediation": {
            "kind": "rolebinding",
            "namespace": "inventory",
            "name": "inventory-reader-binding",
            "field_path": ".subjects[0].name",
            "current_value": "inventory-synk",
            "required_value": "inventory-sync",
        },
        "root_cause_statement": "The RoleBinding binds a ServiceAccount that does not exist.",
        "mechanism": (
            "The RoleBinding inventory-reader-binding names .subjects[0].name inventory-synk, but "
            "the only ServiceAccount in the namespace is inventory-sync, so the worker's API "
            "reads are denied with 403 and it keeps serving stale data."
        ),
        "evidence": [
            {
                "role": "symptom",
                "claim": "Counts are stale.",
                "tool_call_id": "page",
                "quote": "Storefront inventory counts have not updated for over 30 minutes",
            },
            {
                "role": "link",
                "claim": "Reads are refused.",
                "tool_call_id": "c2",
                "quote": "403 Forbidden",
            },
            {
                "role": "defect",
                "claim": "The subject does not resolve.",
                "tool_call_id": "c1",
                "quote": "rolebinding/inventory-reader-binding subjects[].name='inventory-synk'",
            },
        ],
        "ruled_out": [
            {
                "alternative": "The worker itself is crashing.",
                "entity_names": [POD],
                "ruling_claim": "It is ready and has never restarted.",
                "tool_call_id": "overview",
                "quote": "sync(ready=True,restarts=0)",
            }
        ],
        "verification": [
            {
                "command": "kubectl -n inventory get rolebinding inventory-reader-binding -o yaml",
                "tool": "get_object",
                "arguments": {
                    "namespace": "inventory",
                    "kind": "rolebindings",
                    "name": "inventory-reader-binding",
                },
                "must_contain": "inventory-synk",
            },
            {
                "command": "kubectl -n inventory get serviceaccounts",
                "tool": "get_object",
                "arguments": {"namespace": "inventory", "kind": "serviceaccounts"},
                "must_contain": "inventory-sync",
            },
        ],
        "verdict": "confirmed",
        "missing_evidence": "",
    }
    args.update(over)
    return args


def _reads() -> tuple[ToolCall, ToolCall]:
    return (
        ToolCall(
            "t1",
            "find_consumers",
            {"namespace": "inventory", "kind": "serviceaccount", "name": "inventory-sync"},
        ),
        ToolCall("t2", "get_logs", {"namespace": "inventory", "pod": POD}),
    )


def test_happy_path_writes_all_artifacts(tmp_path: Path) -> None:
    find, logs = _reads()
    script = Script(
        [_turn(find), _turn(logs), _turn(ToolCall("s1", "submit_answer", _submit_args()))]
    )
    answer_path = agent.diagnose(RBAC, CASE, tmp_path, script)

    for name in ("answer.json", "report.md", "metrics.json", "transcript.jsonl", "prompt.txt"):
        assert (tmp_path / name).is_file(), name
    answer = json.loads(answer_path.read_text(encoding="utf-8"))
    assert answer["case_id"] == CASE
    assert answer["failing_resource"]["name"] == "inventory-reader-binding"
    assert answer["verdict"] == "confirmed"
    metrics = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["salvaged"] is False
    assert metrics["submit_attempts"] == 1
    assert metrics["tool_calls"] == 2
    assert (
        metrics["input_tokens"] == metrics["uncached_input_tokens"] + metrics["cache_read_tokens"]
    )


def test_metrics_survive_an_exception_after_the_first_turn(tmp_path: Path) -> None:
    """Measured spend must land on disk even when the run dies mid-flight."""
    find, _ = _reads()
    script = Script([_turn(find), RuntimeError("connection reset")])
    with pytest.raises(RuntimeError, match="connection reset"):
        agent.diagnose(RBAC, CASE, tmp_path, script)
    metrics = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["output_tokens"] > 0
    assert metrics["turns"] == 1


def test_a_rejected_submission_is_returned_and_a_resubmit_is_accepted(tmp_path: Path) -> None:
    find, logs = _reads()
    bad = _submit_args(
        remediation={
            "kind": "deployment",
            "namespace": "inventory",
            "name": "inventory-sync",
            "field_path": ".spec.serviceAccountName",
            "current_value": "a",
            "required_value": "b",
        }
    )
    script = Script(
        [
            _turn(find),
            _turn(logs),
            _turn(ToolCall("s1", "submit_answer", bad)),
            _turn(ToolCall("s2", "submit_answer", _submit_args())),
        ]
    )
    agent.diagnose(RBAC, CASE, tmp_path, script)
    metrics = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["submit_attempts"] == 2
    assert metrics["salvaged"] is False
    rejection = json.dumps(script.seen[-1])
    assert "V3 SPEC-OWNER" in rejection


def test_reject_cap_falls_through_to_a_schema_valid_salvage(tmp_path: Path) -> None:
    bad = _submit_args(
        failing_resource={
            "kind": "rolebinding",
            "namespace": "inventory",
            "name": "imagined-binding",
        }
    )
    script = Script([_turn(ToolCall(f"s{i}", "submit_answer", bad)) for i in range(1, 6)])
    agent.diagnose(RBAC, CASE, tmp_path, script)
    metrics = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["salvaged"] is True
    answer = json.loads((tmp_path / "answer.json").read_text(encoding="utf-8"))
    assert answer["verdict"] == "inconclusive"
    assert answer["missing_evidence"].strip()
    assert all(answer["failing_resource"][f].strip() for f in ("kind", "namespace", "name"))
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    for heading in (
        "## Root cause",
        "## Evidence chain",
        "## Investigation ledger",
        "## Verification recipe",
    ):
        assert heading in report


def test_cost_cap_forces_exactly_one_final_turn(tmp_path: Path) -> None:
    find, _ = _reads()
    expensive = _turn(find, output_tokens=40_000)
    script = Script([expensive, _turn(ToolCall("s1", "submit_answer", _submit_args()))])
    agent.diagnose(RBAC, CASE, tmp_path, script)
    forced = [m for m in script.seen[-1] if m.get("content") == agent.prompts.FORCED_SUBMIT]
    assert len(forced) == 1
    assert script.turns == [], "the loop should have used exactly the scripted turns"


def test_a_billing_error_propagates_verbatim(tmp_path: Path) -> None:
    """The harness tells a billing outage from a wrong answer by matching this text."""
    marker = "Your credit balance is too low to access the API"
    script = Script([RuntimeError(f"Error code: 400 - {marker}")])
    with pytest.raises(RuntimeError, match="credit balance is too low"):
        agent.diagnose(RBAC, CASE, tmp_path, script)


def test_no_artifact_contains_an_absolute_home_path(tmp_path: Path) -> None:
    find, logs = _reads()
    script = Script(
        [_turn(find), _turn(logs), _turn(ToolCall("s1", "submit_answer", _submit_args()))]
    )
    agent.diagnose(RBAC, CASE, tmp_path, script)
    for path in tmp_path.iterdir():
        assert not re.search(r"/(Users|home)/[A-Za-z0-9._-]+", path.read_text(encoding="utf-8")), (
            path.name
        )


def test_the_arm_never_raises_system_exit_and_always_writes_a_bundle(tmp_path: Path) -> None:
    """run_case catches Exception; a SystemExit would kill the matrix with no outputs."""
    bad = _submit_args(
        failing_resource={
            "kind": "rolebinding",
            "namespace": "inventory",
            "name": "imagined-binding",
        }
    )
    script = Script([_turn(ToolCall(f"s{i}", "submit_answer", bad)) for i in range(1, 6)])
    try:
        agent.diagnose(RBAC, CASE, tmp_path, script)
    except SystemExit:  # pragma: no cover - the failure this test exists to catch
        pytest.fail("the arm raised SystemExit")
    for name in ("answer.json", "report.md", "metrics.json"):
        assert (tmp_path / name).is_file(), name


def test_a_model_that_only_talks_is_nudged_then_salvaged(tmp_path: Path) -> None:
    script = Script([_turn() for _ in range(agent.MAX_NUDGES + 1)])
    agent.diagnose(RBAC, CASE, tmp_path, script)
    metrics = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["salvaged"] is True
    nudges = [m for m in script.seen[-1] if m.get("content") == agent.prompts.NUDGE]
    assert nudges


def test_paged_namespace_resolves_for_every_fixture() -> None:
    from solution import fixture as fx

    for case in sorted(p for p in (ROOT / "evals" / "fixtures").iterdir() if p.is_dir()):
        namespace = agent.paged_namespace(case, fx.page(case))
        assert namespace in fx.namespaces(case), case.name


def test_paged_namespace_ignores_a_name_that_is_only_part_of_a_longer_one() -> None:
    """'kube-system-canary' does not page kube-system.

    The fallback matched by substring, so any longer name containing a real
    namespace resolved to it — and the paged namespace is what seeds V2
    admissibility, so a wrong one licenses citations from the wrong place.
    """
    assert agent.paged_namespace(RBAC, "The kube-system-canary dashboard is red.") != "kube-system"
    assert agent.paged_namespace(RBAC, "Errors across kube-system since 09:02.") == "kube-system"


def test_tool_results_carry_the_citation_id_the_model_is_told_to_use(tmp_path: Path) -> None:
    """Regression guard: the API's own tool_use ids are opaque and unciteable.

    Asking the model to cite one produced invented ids and a wall of V1
    rejections on a case it had already diagnosed correctly. The ledger is keyed
    by the short id stamped on the result the model actually reads.
    """
    find, logs = _reads()
    script = Script(
        [_turn(find), _turn(logs), _turn(ToolCall("s1", "submit_answer", _submit_args()))]
    )
    agent.diagnose(RBAC, CASE, tmp_path, script)
    sent = json.dumps(script.seen[-1])
    assert "[call_id: c1]" in sent
    assert "[call_id: c2]" in sent


def test_the_citation_stamp_is_not_part_of_the_quotable_output(tmp_path: Path) -> None:
    """A quote lifted from the stamp must not verify — it is not tool output."""
    find, logs = _reads()
    stamped = _submit_args()
    evidence = list(stamped["evidence"])  # type: ignore[arg-type]
    evidence[1] = {
        "role": "link",
        "claim": "c",
        "tool_call_id": "c2",
        "quote": "[call_id: c2]",
    }
    stamped["evidence"] = evidence
    script = Script(
        [
            _turn(find),
            _turn(logs),
            _turn(ToolCall("s1", "submit_answer", stamped)),
            _turn(ToolCall("s2", "submit_answer", _submit_args())),
        ]
    )
    agent.diagnose(RBAC, CASE, tmp_path, script)
    metrics = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["submit_attempts"] == 2
    assert metrics["salvaged"] is False


def test_rejections_are_recorded_in_the_transcript(tmp_path: Path) -> None:
    """Reconstructing why a submission was rejected once required replaying it offline."""
    bad = _submit_args(
        remediation={
            "kind": "deployment",
            "namespace": "inventory",
            "name": "inventory-sync",
            "field_path": ".spec.serviceAccountName",
            "current_value": "a",
            "required_value": "b",
        }
    )
    find, logs = _reads()
    script = Script(
        [
            _turn(find),
            _turn(logs),
            _turn(ToolCall("s1", "submit_answer", bad)),
            _turn(ToolCall("s2", "submit_answer", _submit_args())),
        ]
    )
    agent.diagnose(RBAC, CASE, tmp_path, script)
    lines = (tmp_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    recorded = [r for line in lines for r in json.loads(line)["rejections"]]
    assert recorded
    assert any("V3 SPEC-OWNER" in v for r in recorded for v in r["violations"])
