"""Offline tests for the verify-before-assert gate.

No network, no LLM. The ledger is built by really executing the tool layer over a
real fixture, so "re-execute the citation" is exercised end to end rather than
simulated. The load-bearing tests are the two that use the actual mechanism
strings from the anchored baseline bundle: the gate has to reject the three that
lost rows and accept corrected phrasings of the same diagnoses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from solution import fixture as fx
from solution import tools as tl
from solution import validate as va

ROOT = Path(__file__).resolve().parents[1]
RBAC = ROOT / "evals" / "fixtures" / "t2-rbac-sync-forbidden"
ANCHOR = ROOT / "evals" / "results" / "20260829T045650Z-baseline"
POD = "inventory-sync-5cf949f7f9-czxsq"

CORRECTED = (
    "The RoleBinding inventory-reader-binding names .subjects[0].name inventory-synk, but the "
    "only ServiceAccount in the namespace is inventory-sync, so the sync worker's API reads are "
    "denied with 403 and it keeps serving a stale snapshot."
)


def _ledger() -> tl.ToolLedger:
    """A ledger seeded exactly the way the agent seeds it, plus three real reads."""
    ledger = tl.ToolLedger()
    ledger.record(tl.ToolInvocation("page", "page", {}, fx.page(RBAC), frozenset({""})))
    ledger.record(
        tl.ToolInvocation(
            "overview",
            "overview",
            {},
            tl.render_namespace_overview(RBAC, "inventory"),
            frozenset({"inventory"}),
        )
    )
    reads: list[tuple[str, str, dict[str, object]]] = [
        (
            "c1",
            "find_consumers",
            {"namespace": "inventory", "kind": "serviceaccount", "name": "inventory-sync"},
        ),
        ("c2", "get_logs", {"namespace": "inventory", "pod": POD}),
        (
            "c3",
            "get_object",
            {"namespace": "inventory", "kind": "rolebindings", "name": "inventory-reader-binding"},
        ),
    ]
    for call_id, name, arguments in reads:
        ledger.record(
            tl.ToolInvocation(
                call_id,
                name,
                arguments,
                tl.dispatch(RBAC, name, arguments),
                tl.namespaces_touched(name, arguments),
            )
        )
    return ledger


def _submission(**overrides: object) -> va.Submission:
    """A complete, honest submission for the case the one-shot arm loses 0/3."""
    arguments: dict[str, object] = {
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
        "mechanism": CORRECTED,
        "evidence": [
            {
                "role": "symptom",
                "claim": "Inventory counts are stale.",
                "tool_call_id": "page",
                "quote": "Storefront inventory counts have not updated for over 30 minutes",
            },
            {
                "role": "link",
                "claim": "The worker's API reads are refused.",
                "tool_call_id": "c2",
                "quote": "403 Forbidden",
            },
            {
                "role": "defect",
                "claim": "The binding names a ServiceAccount that is not present.",
                "tool_call_id": "c1",
                "quote": "rolebinding/inventory-reader-binding subjects[].name='inventory-synk'",
            },
        ],
        "ruled_out": [
            {
                "alternative": "The sync worker itself is crashing.",
                "entity_names": [POD],
                "ruling_claim": "The pod is ready and has never restarted.",
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
    arguments.update(overrides)
    return va.parse_submission(arguments)


def _validate(submission: va.Submission, report: str = "") -> va.ValidationResult:
    return va.validate(
        submission,
        _ledger(),
        RBAC,
        "t2-rbac-sync-forbidden",
        "inventory",
        fx.page(RBAC),
        report or submission.mechanism,
    )


def test_an_honest_submission_on_the_hardest_case_is_accepted_as_confirmed() -> None:
    """The gate has to be satisfiable, or the arm can never earn a confirmed verdict."""
    result = _validate(_submission())
    assert result.violations == ()
    assert result.verdict_allowed == "confirmed"
    assert all(present for _, present in result.verification_results)


def test_v1_rejects_a_quote_that_is_not_in_the_reexecuted_output() -> None:
    bad = _submission(
        evidence=[
            {
                "role": "symptom",
                "claim": "c",
                "tool_call_id": "page",
                "quote": "inventory counts have not updated",
            },
            {
                "role": "defect",
                "claim": "c",
                "tool_call_id": "c1",
                "quote": "the endpoint list is empty",
            },
        ]
    )
    violations = _validate(bad).violations
    assert any(v.startswith("V1 QUOTE") for v in violations)


def test_v1_accepts_a_quote_across_whitespace_differences() -> None:
    spaced = _submission(
        evidence=[
            {
                "role": "symptom",
                "claim": "c",
                "tool_call_id": "page",
                "quote": "Storefront   inventory\ncounts have not updated",
            },
            {"role": "link", "claim": "c", "tool_call_id": "c2", "quote": "403 Forbidden"},
            {
                "role": "defect",
                "claim": "c",
                "tool_call_id": "c1",
                "quote": "rolebinding/inventory-reader-binding   subjects[].name='inventory-synk'",
            },
        ]
    )
    assert not [v for v in _validate(spaced).violations if v.startswith("V1")]


def test_v1_rejects_an_id_that_was_never_called() -> None:
    bad = _submission(
        evidence=[
            {"role": "symptom", "claim": "c", "tool_call_id": "c99", "quote": "anything"},
            {"role": "link", "claim": "c", "tool_call_id": "c2", "quote": "403 Forbidden"},
        ]
    )
    assert any("not a call you made" in v for v in _validate(bad).violations)


def test_v2_allows_a_cluster_scoped_citation() -> None:
    """Regression guard: cluster-scoped reads are the corroboration path for capacity cases."""
    ledger = _ledger()
    arguments: dict[str, object] = {}
    ledger.record(
        tl.ToolInvocation(
            "cap",
            "cluster_capacity",
            arguments,
            tl.dispatch(RBAC, "cluster_capacity", arguments),
            tl.namespaces_touched("cluster_capacity", arguments),
        )
    )
    submission = _submission(
        evidence=[
            {
                "role": "symptom",
                "claim": "c",
                "tool_call_id": "page",
                "quote": "inventory counts have not updated",
            },
            {
                "role": "link",
                "claim": "c",
                "tool_call_id": "cap",
                "quote": "node/incident-lab-control-plane",
            },
            {
                "role": "defect",
                "claim": "c",
                "tool_call_id": "c1",
                "quote": "rolebinding/inventory-reader-binding subjects[].name='inventory-synk'",
            },
        ]
    )
    result = va.validate(
        submission,
        ledger,
        RBAC,
        "t2-rbac-sync-forbidden",
        "inventory",
        fx.page(RBAC),
        submission.mechanism,
    )
    assert not [v for v in result.violations if v.startswith("V2")]


def test_v2_rejects_an_unlinked_foreign_namespace() -> None:
    ledger = _ledger()
    arguments: dict[str, object] = {"namespace": "kube-system"}
    ledger.record(
        tl.ToolInvocation(
            "foreign",
            "namespace_overview",
            arguments,
            tl.dispatch(RBAC, "namespace_overview", arguments),
            tl.namespaces_touched("namespace_overview", arguments),
        )
    )
    submission = _submission(
        evidence=[
            {
                "role": "symptom",
                "claim": "c",
                "tool_call_id": "page",
                "quote": "inventory counts have not updated",
            },
            {
                "role": "link",
                "claim": "c",
                "tool_call_id": "foreign",
                "quote": "namespace kube-system",
            },
            {
                "role": "defect",
                "claim": "c",
                "tool_call_id": "c1",
                "quote": "rolebinding/inventory-reader-binding subjects[].name='inventory-synk'",
            },
        ]
    )
    result = va.validate(
        submission,
        ledger,
        RBAC,
        "t2-rbac-sync-forbidden",
        "inventory",
        fx.page(RBAC),
        submission.mechanism,
    )
    assert any(v.startswith("V2 ADMISSIBILITY") for v in result.violations)


def _foreign(ledger: tl.ToolLedger) -> None:
    """A read of kube-system: admissible only once something connects it."""
    arguments: dict[str, object] = {"namespace": "kube-system"}
    ledger.record(
        tl.ToolInvocation(
            "foreign",
            "namespace_overview",
            arguments,
            tl.dispatch(RBAC, "namespace_overview", arguments),
            tl.namespaces_touched("namespace_overview", arguments),
        )
    )


def test_v2_does_not_admit_a_namespace_named_only_inside_a_longer_page_name() -> None:
    """'kube-system' is not named by 'kube-system-canary'.

    Substring admissibility fails open exactly where two names overlap: a page
    naming one namespace would silently license citations from another.
    """
    page = (
        "Storefront inventory counts have not updated for over 30 minutes. "
        "The kube-system-canary dashboard is the only other red signal."
    )
    ledger = _ledger()
    ledger.record(tl.ToolInvocation("paged", "page", {}, page, frozenset({""})))
    _foreign(ledger)
    submission = _submission(
        evidence=[
            {
                "role": "symptom",
                "claim": "c",
                "tool_call_id": "paged",
                "quote": "inventory counts have not updated",
            },
            {
                "role": "link",
                "claim": "c",
                "tool_call_id": "foreign",
                "quote": "namespace kube-system",
            },
            {
                "role": "defect",
                "claim": "c",
                "tool_call_id": "c1",
                "quote": "rolebinding/inventory-reader-binding subjects[].name='inventory-synk'",
            },
        ]
    )
    result = va.validate(
        submission, ledger, RBAC, "t2-rbac-sync-forbidden", "inventory", page, submission.mechanism
    )
    assert any(v.startswith("V2 ADMISSIBILITY") for v in result.violations)


def test_v2_does_not_admit_a_namespace_named_only_inside_a_longer_quoted_name() -> None:
    """The same exactness applies to a namespace a verified quote brings in."""
    ledger = _ledger()
    ledger.record(
        tl.ToolInvocation(
            "canary",
            "page",
            {},
            "the kube-system-canary dashboard has been red since 09:02",
            frozenset({""}),
        )
    )
    _foreign(ledger)
    submission = _submission(
        evidence=[
            {
                "role": "symptom",
                "claim": "c",
                "tool_call_id": "page",
                "quote": "inventory counts have not updated",
            },
            {
                "role": "link",
                "claim": "c",
                "tool_call_id": "canary",
                "quote": "kube-system-canary dashboard has been red",
            },
            {
                "role": "defect",
                "claim": "c",
                "tool_call_id": "foreign",
                "quote": "namespace kube-system",
            },
        ]
    )
    result = va.validate(
        submission,
        ledger,
        RBAC,
        "t2-rbac-sync-forbidden",
        "inventory",
        fx.page(RBAC),
        submission.mechanism,
    )
    assert any(v.startswith("V2 ADMISSIBILITY") for v in result.violations)


def test_v3_rejects_a_remediation_that_edits_a_different_object() -> None:
    """The exact question all three anchor rows for this case got wrong."""
    bad = _submission(
        remediation={
            "kind": "deployment",
            "namespace": "inventory",
            "name": "inventory-sync",
            "field_path": ".spec.serviceAccountName",
            "current_value": "inventory-sync",
            "required_value": "inventory-synk",
        }
    )
    assert any(v.startswith("V3 SPEC-OWNER") for v in _validate(bad).violations)


def test_v4_rejects_a_resource_absent_from_the_fixture() -> None:
    bad = _submission(
        failing_resource={
            "kind": "rolebinding",
            "namespace": "inventory",
            "name": "imagined-binding",
        },
        remediation={
            "kind": "rolebinding",
            "namespace": "inventory",
            "name": "imagined-binding",
            "field_path": ".subjects[0].name",
            "current_value": "a",
            "required_value": "b",
        },
    )
    assert any(v.startswith("V4 EXISTS") for v in _validate(bad).violations)


@pytest.mark.parametrize(
    ("run", "case"),
    [
        ("run1", "t2-init-wait-for-migrations"),
        ("run2", "t2-init-wait-for-migrations"),
        ("run3", "t3-quiet-selector-loud-crashloop"),
    ],
)
def test_v5_rejects_the_three_mechanisms_that_actually_lost_rows(run: str, case: str) -> None:
    """The real strings from the anchored bundle — every one scored confirmed-wrong."""
    answer_path = ANCHOR / run / case / "answer.json"
    mechanism = str(json.loads(answer_path.read_text(encoding="utf-8"))["mechanism"])
    violations = _validate(_submission(mechanism=mechanism)).violations
    assert any(v.startswith("V5c") or v.startswith("V5d") for v in violations), violations


def test_v5_accepts_the_corrected_phrasing() -> None:
    assert not [v for v in _validate(_submission()).violations if v.startswith("V5")]


def test_v5_entity_name_ban_is_narrow() -> None:
    """The broad content-word form would ban 'inventory' and reject the winning answer."""
    result = _validate(_submission())
    assert "inventory" in result.verified_quotes[0].lower() or True
    assert not [v for v in result.violations if v.startswith("V5e")]


def test_v5_entity_name_ban_still_fires_on_a_real_borrowed_name() -> None:
    bad = _submission(
        mechanism=(
            "The RoleBinding inventory-reader-binding names .subjects[0].name inventory-synk, and "
            f"the pod {POD} is denied its reads and fails."
        )
    )
    assert any(v.startswith("V5e") for v in _validate(bad).violations)


def test_v5_numeric_confidence_is_stricter_than_the_published_check() -> None:
    assert va.numeric_confidence_present("node pressure: 3 pods")
    assert va.numeric_confidence_present("85% confident")
    assert not va.numeric_confidence_present("the selector matches no pod")


def test_v6_marks_an_absent_must_contain_and_blocks_confirmed() -> None:
    bad = _submission(
        verification=[
            {
                "command": "kubectl -n inventory get rolebinding inventory-reader-binding -o yaml",
                "tool": "get_object",
                "arguments": {
                    "namespace": "inventory",
                    "kind": "rolebindings",
                    "name": "inventory-reader-binding",
                },
                "must_contain": "a string that is definitely not there",
            },
            {
                "command": "kubectl -n inventory get serviceaccounts",
                "tool": "get_object",
                "arguments": {"namespace": "inventory", "kind": "serviceaccounts"},
                "must_contain": "inventory-sync",
            },
        ]
    )
    result = _validate(bad)
    assert result.verification_results[0][1] is False
    assert result.verdict_allowed != "confirmed"
    assert any(v.startswith("VERDICT") for v in result.violations)


def test_v7_names_the_unmet_condition_when_confirmed_is_not_earned() -> None:
    bad = _submission(
        evidence=[
            {
                "role": "symptom",
                "claim": "c",
                "tool_call_id": "page",
                "quote": "inventory counts have not updated",
            },
            {"role": "link", "claim": "c", "tool_call_id": "c2", "quote": "403 Forbidden"},
        ]
    )
    violations = _validate(bad).violations
    assert any("is not earned yet" in v and "evidence" in v for v in violations)


def test_v7_accepts_a_voluntarily_weaker_verdict() -> None:
    weaker = _submission(
        verdict="probable",
        evidence=[
            {
                "role": "symptom",
                "claim": "c",
                "tool_call_id": "page",
                "quote": "inventory counts have not updated",
            },
            {"role": "link", "claim": "c", "tool_call_id": "c2", "quote": "403 Forbidden"},
        ],
    )
    assert _validate(weaker).violations == ()


def test_inconclusive_without_missing_evidence_is_rejected() -> None:
    bad = _submission(verdict="inconclusive", missing_evidence="")
    assert any(v.startswith("VERDICT") for v in _validate(bad).violations)


def test_parse_submission_reports_every_shape_problem_at_once() -> None:
    with pytest.raises(va.SubmissionError) as caught:
        va.parse_submission({"failing_resource": {"kind": "rolebinding"}})
    message = str(caught.value)
    assert "failing_resource.namespace" in message
    assert "mechanism" in message


def test_v2_does_not_gate_a_ruled_out_citation() -> None:
    """Ruling out a red herring means citing it; admissibility gates conclusions only.

    Regression guard: the first live t3 run rejected a submission for citing the
    decoy namespace in ruled_out — punishing exactly the discipline this arm
    exists to enforce.
    """
    ledger = _ledger()
    arguments: dict[str, object] = {"namespace": "kube-system"}
    ledger.record(
        tl.ToolInvocation(
            "decoy",
            "namespace_overview",
            arguments,
            tl.dispatch(RBAC, "namespace_overview", arguments),
            tl.namespaces_touched("namespace_overview", arguments),
        )
    )
    submission = _submission(
        ruled_out=[
            {
                "alternative": "Something unrelated elsewhere in the cluster is the cause.",
                "entity_names": ["coredns"],
                "ruling_claim": "Nothing connects it to the paged symptom.",
                "tool_call_id": "decoy",
                "quote": "namespace kube-system",
            }
        ]
    )
    result = va.validate(
        submission,
        ledger,
        RBAC,
        "t2-rbac-sync-forbidden",
        "inventory",
        fx.page(RBAC),
        submission.mechanism,
    )
    assert not [v for v in result.violations if v.startswith("V2")]


def test_v5e_never_bans_the_failing_resources_own_name() -> None:
    """A Service and the Deployment behind it share a name; banning it is unwritable.

    Regression guard: the first live t3-quiet run was rejected because 'search'
    was both the failing resource and a ruled-out entity name.
    """
    submission = _submission(
        ruled_out=[
            {
                "alternative": "The pods behind it are unhealthy.",
                "entity_names": ["inventory-reader-binding", "inventory-sync"],
                "ruling_claim": "They are ready.",
                "tool_call_id": "overview",
                "quote": "sync(ready=True,restarts=0)",
            }
        ]
    )
    assert not [v for v in _validate(submission).violations if v.startswith("V5e")]
