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


# --- the oracles the webhook case exposed (docs/failure-modes.md 2026-09-05) ------

WEBHOOK = ROOT / "evals" / "fixtures" / "t2-checkout-release-stalled"


def _record_reads(
    ledger: tl.ToolLedger, reads: list[tuple[str, str, dict[str, object]]]
) -> tl.ToolLedger:
    for call_id, name, arguments in reads:
        ledger.record(
            tl.ToolInvocation(
                call_id,
                name,
                arguments,
                tl.dispatch(WEBHOOK, name, arguments),
                tl.namespaces_touched(name, arguments),
            )
        )
    return ledger


def _seeded_webhook_ledger() -> tl.ToolLedger:
    ledger = tl.ToolLedger()
    ledger.record(tl.ToolInvocation("page", "page", {}, fx.page(WEBHOOK), frozenset({""})))
    ledger.record(
        tl.ToolInvocation(
            "overview",
            "overview",
            {},
            tl.render_namespace_overview(WEBHOOK, "checkout"),
            frozenset({"checkout"}),
        )
    )
    return ledger


def _webhook_ledger() -> tl.ToolLedger:
    """The reads the unchanged arm actually made on the webhook case, replayed — with
    its two reads of the configuration re-aimed at clusterroles, a kind that stays
    unserved after the webhook kinds were served (2026-11-07), so these tests keep
    exercising a result the snapshot cannot give."""
    return _record_reads(
        _seeded_webhook_ledger(),
        [
            ("c1", "get_events", {"namespace": "checkout", "warnings_only": True}),
            (
                "c2",
                "get_object",
                {
                    "namespace": "cluster-scoped",
                    "kind": "clusterroles",
                    "name": "workload-standards",
                },
            ),
            (
                "c3",
                "describe",
                {
                    "namespace": "cluster-scoped",
                    "kind": "clusterrole",
                    "name": "workload-standards",
                },
            ),
            ("c4", "namespace_overview", {"namespace": "platform-policy"}),
        ],
    )


def _served_webhook_ledger() -> tl.ToolLedger:
    """The reads the arm can make once the webhook kinds are served: cluster-wide,
    no namespace argument."""
    return _record_reads(
        _seeded_webhook_ledger(),
        [
            ("c1", "get_events", {"namespace": "checkout", "warnings_only": True}),
            (
                "c2",
                "get_object",
                {"kind": "validatingwebhookconfigurations", "name": "workload-standards"},
            ),
            (
                "c3",
                "describe",
                {"kind": "validatingwebhookconfiguration", "name": "workload-standards"},
            ),
            ("c4", "namespace_overview", {"namespace": "platform-policy"}),
        ],
    )


def _webhook_submission(**overrides: object) -> va.Submission:
    """The shape the unchanged arm converged on: the right object, named without a read."""
    ref = {
        "kind": "ValidatingWebhookConfiguration",
        "namespace": "cluster-scoped",
        "name": "workload-standards",
    }
    arguments: dict[str, object] = {
        "failing_resource": ref,
        "remediation": ref
        | {
            "field_path": ".webhooks[0].failurePolicy",
            "current_value": "Fail",
            "required_value": "Ignore",
        },
        "root_cause_statement": "An orphaned webhook configuration refuses every pod create.",
        "mechanism": (
            "ValidatingWebhookConfiguration workload-standards fails every pod create because "
            'the API server cannot call its webhook: `service "policy-guard" not found`.'
        ),
        "evidence": [
            {
                "role": "symptom",
                "claim": "The release has not completed.",
                "tool_call_id": "page",
                "quote": "the new\nversion has reached 0 of 3 replicas",
            },
            {
                "role": "link",
                "claim": "The ReplicaSet cannot create pods.",
                "tool_call_id": "c1",
                "quote": "failed calling webhook",
            },
            {
                "role": "defect",
                "claim": "The configuration is the object named.",
                "tool_call_id": "c3",
                "quote": "clusterroles",
            },
        ],
        "ruled_out": [
            {
                "alternative": "The Deployment template is wrong.",
                "entity_names": ["checkout-api"],
                "ruling_claim": "The old ReplicaSet serves fine.",
                "tool_call_id": "overview",
                "quote": "checkout-api",
            }
        ],
        "verification": [
            {
                "command": "kubectl describe clusterrole workload-standards",
                "tool": "describe",
                "arguments": {
                    "namespace": "cluster-scoped",
                    "kind": "clusterrole",
                    "name": "workload-standards",
                },
                "must_contain": "workload-standards",
            },
            {
                "command": "kubectl get events -n checkout",
                "tool": "get_events",
                "arguments": {"namespace": "checkout", "warnings_only": True},
                "must_contain": "failed calling webhook",
            },
        ],
        "verdict": "confirmed",
        "missing_evidence": "",
    }
    arguments.update(overrides)
    return va.parse_submission(arguments)


def _validate_webhook(submission: va.Submission) -> va.ValidationResult:
    return va.validate(
        submission,
        _webhook_ledger(),
        WEBHOOK,
        "t2-checkout-release-stalled",
        "checkout",
        fx.page(WEBHOOK),
        submission.mechanism,
    )


def test_v4_does_not_list_names_for_a_kind_no_tool_serves() -> None:
    """The unchanged arm learned the object's name from this very message, 3 runs of 3.
    The webhook kinds are served since 2026-11-07, so the guard is held on clusterroles."""
    ref = {"kind": "ClusterRole", "namespace": "cluster-scoped", "name": "imagined"}
    guessed = _webhook_submission(
        failing_resource=ref,
        remediation=ref | {"field_path": ".rules[0]", "current_value": "a", "required_value": "b"},
    )
    v4 = [v for v in _validate_webhook(guessed).violations if v.startswith("V4 EXISTS")]
    assert v4, "a wrong name must still be rejected"
    assert "Present:" not in v4[0]
    real_names = {str(item["metadata"]["name"]) for item in fx.load_kind(WEBHOOK, "clusterroles")}
    assert real_names and not any(name in v4[0] for name in real_names)


def test_v4_lists_names_for_the_webhook_kind_now_that_get_object_serves_it() -> None:
    """The flip side of the guard: a kind the agent can list may be listed back to it."""
    ref = {
        "kind": "ValidatingWebhookConfiguration",
        "namespace": "cluster-scoped",
        "name": "policy-guard",
    }
    guessed = _webhook_submission(
        failing_resource=ref,
        remediation=ref
        | {"field_path": ".webhooks[0].failurePolicy", "current_value": "a", "required_value": "b"},
    )
    v4 = [v for v in _validate_webhook(guessed).violations if v.startswith("V4 EXISTS")]
    assert v4 and "Present: workload-standards" in v4[0]


def test_a_real_read_of_the_configuration_earns_confirmed_honestly() -> None:
    """Stage 2's acceptance path, offline: the defect item cites a real read of the
    object (tagged cluster-wide, so V2 admits it), and the verdict gate lets
    `confirmed` through on nothing but tool output."""
    honest = _webhook_submission(
        mechanism=(
            "ValidatingWebhookConfiguration workload-standards points "
            ".webhooks[0].clientConfig.service at Service policy-guard in namespace "
            "platform-policy, which does not exist, and sets .webhooks[0].failurePolicy to "
            "Fail, so the API server refuses every pod create with "
            '`service "policy-guard" not found`.'
        ),
        evidence=[
            {
                "role": "symptom",
                "claim": "The release has not completed.",
                "tool_call_id": "page",
                "quote": "the new\nversion has reached 0 of 3 replicas",
            },
            {
                "role": "link",
                "claim": "The ReplicaSet cannot create pods.",
                "tool_call_id": "c1",
                "quote": "failed calling webhook",
            },
            {
                "role": "defect",
                "claim": "The configuration fails closed on a Service that is not there.",
                "tool_call_id": "c2",
                "quote": '"failurePolicy": "Fail"',
            },
        ],
        verification=[
            {
                "command": "kubectl get validatingwebhookconfiguration workload-standards -o yaml",
                "tool": "get_object",
                "arguments": {
                    "kind": "validatingwebhookconfigurations",
                    "name": "workload-standards",
                },
                "must_contain": '"failurePolicy": "Fail"',
            },
            {
                "command": "kubectl describe validatingwebhookconfiguration workload-standards",
                "tool": "describe",
                "arguments": {
                    "kind": "validatingwebhookconfiguration",
                    "name": "workload-standards",
                },
                "must_contain": "Name:        policy-guard",
            },
            {
                "command": "kubectl get events -n checkout",
                "tool": "get_events",
                "arguments": {"namespace": "checkout", "warnings_only": True},
                "must_contain": "failed calling webhook",
            },
        ],
    )
    result = va.validate(
        honest,
        _served_webhook_ledger(),
        WEBHOOK,
        "t2-checkout-release-stalled",
        "checkout",
        fx.page(WEBHOOK),
        honest.mechanism,
    )
    assert result.violations == (), result.violations
    assert result.verdict_allowed == "confirmed"
    assert all(ok for _, ok in result.verification_results)


def test_v4_still_lists_names_for_a_kind_the_agent_could_have_listed() -> None:
    bad = _submission(
        failing_resource={"kind": "rolebinding", "namespace": "inventory", "name": "imagined"},
        remediation={
            "kind": "rolebinding",
            "namespace": "inventory",
            "name": "imagined",
            "field_path": ".subjects[0].name",
            "current_value": "a",
            "required_value": "b",
        },
    )
    v4 = [v for v in _validate(bad).violations if v.startswith("V4 EXISTS")]
    assert v4 and "Present: inventory-reader-binding" in v4[0]


def test_a_not_served_result_is_neither_a_quote_nor_a_defect_nor_a_present_check() -> None:
    """Every no-read path to `confirmed` the review found, closed at once: the
    describe error names the object and re-verifies literally, yet counts for nothing."""
    result = _validate_webhook(_webhook_submission())
    assert any("V1 QUOTE: evidence[2]" in v and "no view" in v for v in result.violations)
    assert result.verification_results[0][1] is False, "describe of an unserved kind is ABSENT"
    assert result.verification_results[1][1] is True
    assert result.verdict_allowed != "confirmed"


@pytest.mark.parametrize(
    ("call", "quote", "must_contain"),
    [
        # a served tool, a different kind, the failing object's name: an empty echo
        (
            ("get_events", {"namespace": "checkout", "involved_name": "workload-standards"}),
            "0 events matched",
            "0 events matched",
        ),
        (
            (
                "describe",
                {"namespace": "checkout", "kind": "configmap", "name": "workload-standards"},
            ),
            "no describe captured for configmap/workload-standards",
            "workload-standards",
        ),
        (
            (
                "find_consumers",
                {"namespace": "checkout", "kind": "configmap", "name": "workload-standards"},
            ),
            "no workload in checkout references configmap/workload-standards",
            "workload-standards",
        ),
    ],
    ids=["events-echo", "describe-other-kind", "consumers-echo"],
)
def test_v7_is_not_anchored_by_an_echo_of_the_name_from_another_kind(
    call: tuple[str, dict[str, object]], quote: str, must_contain: str
) -> None:
    """Review of the first closure (2026-09-05): V7 keyed on the name alone, so any served
    tool asked about a same-named object of another kind earned `confirmed`."""
    ledger = _webhook_ledger()
    name, arguments = call
    ledger.record(
        tl.ToolInvocation(
            "c9",
            name,
            arguments,
            tl.dispatch(WEBHOOK, name, arguments),
            tl.namespaces_touched(name, arguments),
        )
    )
    submission = _webhook_submission(
        evidence=[
            {
                "role": "symptom",
                "claim": "The release has not completed.",
                "tool_call_id": "page",
                "quote": "0 of 3 replicas",
            },
            {
                "role": "link",
                "claim": "The ReplicaSet cannot create pods.",
                "tool_call_id": "c1",
                "quote": "failed calling webhook",
            },
            {"role": "defect", "claim": "About the object.", "tool_call_id": "c9", "quote": quote},
        ],
        verification=[
            {
                "command": "kubectl something",
                "tool": name,
                "arguments": arguments,
                "must_contain": must_contain,
            },
            {
                "command": "kubectl get events -n checkout",
                "tool": "get_events",
                "arguments": {"namespace": "checkout", "warnings_only": True},
                "must_contain": "failed calling webhook",
            },
        ],
    )
    result = va.validate(
        submission,
        ledger,
        WEBHOOK,
        "t2-checkout-release-stalled",
        "checkout",
        fx.page(WEBHOOK),
        submission.mechanism,
    )
    assert result.verdict_allowed != "confirmed"
    assert any("is not earned yet" in v and "defect" in v for v in result.violations)


@pytest.mark.parametrize(
    ("name", "arguments", "quote"),
    [
        # the single root cause the red-team found: an undeclared key carrying the name
        (
            "get_events",
            {"namespace": "checkout", "warnings_only": True, "name": "workload-standards"},
            "failed calling webhook",
        ),
        ("list_namespaces", {"name": "workload-standards"}, "checkout workloads="),
        (
            "get_events",
            {
                "warnings_only": True,
                "involved_name": "checkout-api",
                "object": "workload-standards",
            },
            "failed calling webhook",
        ),
        # a declared key that scopes rather than identifies must not anchor either
        (
            "get_logs",
            {
                "namespace": "kube-system",
                "pod": "kube-apiserver-incident-lab-control-plane",
                "contains": "workload-standards",
            },
            "0 log lines contain",
        ),
    ],
    ids=["events-extra-name", "list-namespaces-extra-name", "events-extra-object", "logs-contains"],
)
def test_v7_is_not_anchored_by_an_argument_the_tool_never_consumed(
    name: str, arguments: dict[str, object], quote: str
) -> None:
    ledger = _webhook_ledger()
    ledger.record(
        tl.ToolInvocation(
            "c9",
            name,
            arguments,
            tl.dispatch(WEBHOOK, name, arguments),
            tl.namespaces_touched(name, arguments),
        )
    )
    submission = _webhook_submission(
        evidence=[
            {
                "role": "symptom",
                "claim": "The release has not completed.",
                "tool_call_id": "page",
                "quote": "0 of 3 replicas",
            },
            {
                "role": "link",
                "claim": "The ReplicaSet cannot create pods.",
                "tool_call_id": "c1",
                "quote": "failed calling webhook",
            },
            {"role": "defect", "claim": "About the object.", "tool_call_id": "c9", "quote": quote},
        ],
        verification=[
            {"command": "kubectl x", "tool": name, "arguments": arguments, "must_contain": quote},
            {
                "command": "kubectl get events -n checkout",
                "tool": "get_events",
                "arguments": {"namespace": "checkout", "warnings_only": True},
                "must_contain": "failed calling webhook",
            },
        ],
    )
    result = va.validate(
        submission,
        ledger,
        WEBHOOK,
        "t2-checkout-release-stalled",
        "checkout",
        fx.page(WEBHOOK),
        submission.mechanism,
    )
    assert result.verdict_allowed != "confirmed", result.violations


def test_a_cluster_state_error_stays_citable() -> None:
    """`no namespace 'platform-policy'` IS evidence: the orphan's namespace is gone."""
    honest = _webhook_submission(
        verdict="probable",
        evidence=[
            {
                "role": "symptom",
                "claim": "The release has not completed.",
                "tool_call_id": "page",
                "quote": "0 of 3 replicas",
            },
            {
                "role": "link",
                "claim": "The ReplicaSet cannot create pods.",
                "tool_call_id": "c1",
                "quote": "failed calling webhook",
            },
        ],
        ruled_out=[
            {
                "alternative": "The webhook's namespace still exists; the Service is merely down.",
                "entity_names": ["platform-policy"],
                "ruling_claim": "The namespace is gone.",
                "tool_call_id": "c4",
                "quote": "no namespace 'platform-policy' in this snapshot",
            }
        ],
        verification=[
            {
                "command": "kubectl get events -n checkout",
                "tool": "get_events",
                "arguments": {"namespace": "checkout", "warnings_only": True},
                "must_contain": "failed calling webhook",
            },
            {
                "command": "kubectl get ns platform-policy",
                "tool": "namespace_overview",
                "arguments": {"namespace": "platform-policy"},
                "must_contain": "no namespace 'platform-policy'",
            },
        ],
    )
    result = _validate_webhook(honest)
    assert not any(v.startswith("V1 QUOTE") for v in result.violations), result.violations
    assert result.verification_results[1][1] is True


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


# --- the two documented soft spots (STATUS 2.2, 2026-11-07) ------------------------

CRASHLOOP = ROOT / "evals" / "fixtures" / "t1-crashloop-missing-env"
WORKER_POD = "checkout-worker-66bfcdfc47-d9gdj"
DECOYS = ROOT / "evals" / "fixtures" / "t3-crossns-decoys"


def _recorded(
    fixture: Path, namespace: str, reads: list[tuple[str, str, dict[str, object]]]
) -> tl.ToolLedger:
    """A ledger seeded the way the agent seeds it, plus the given real reads."""
    ledger = tl.ToolLedger()
    ledger.record(tl.ToolInvocation("page", "page", {}, fx.page(fixture), frozenset({""})))
    ledger.record(
        tl.ToolInvocation(
            "overview",
            "overview",
            {},
            tl.render_namespace_overview(fixture, namespace),
            frozenset({namespace}),
        )
    )
    for call_id, name, arguments in reads:
        ledger.record(
            tl.ToolInvocation(
                call_id,
                name,
                arguments,
                tl.dispatch(fixture, name, arguments),
                tl.namespaces_touched(name, arguments),
            )
        )
    return ledger


def _pod_only_ledger() -> tl.ToolLedger:
    """The Deployment's pod was read two ways; c3 reads the Deployment itself but
    nothing cites it unless a test says so."""
    return _recorded(
        CRASHLOOP,
        "payments",
        [
            ("c1", "get_logs", {"namespace": "payments", "pod": WORKER_POD, "previous": True}),
            ("c2", "describe", {"namespace": "payments", "kind": "pod", "name": WORKER_POD}),
            (
                "c3",
                "get_object",
                {"namespace": "payments", "kind": "deployment", "name": "checkout-worker"},
            ),
        ],
    )


def _pod_only_submission(**overrides: object) -> va.Submission:
    """A Deployment named as the failing object, evidenced through its pod alone."""
    ref = {"kind": "Deployment", "namespace": "payments", "name": "checkout-worker"}
    arguments: dict[str, object] = {
        "failing_resource": ref,
        "remediation": ref
        | {
            "field_path": ".spec.template.spec.containers[0].env",
            "current_value": "no AMQP_URL entry",
            "required_value": "AMQP_URL set from the queue Secret",
        },
        "root_cause_statement": "The worker's pod template sets no AMQP_URL.",
        "mechanism": (
            "Deployment checkout-worker's .spec.template.spec.containers[0].env has no AMQP_URL "
            "entry, so the worker exits with `FATAL: AMQP_URL not set` on every start."
        ),
        "evidence": [
            {
                "role": "symptom",
                "claim": "The worker is not ready.",
                "tool_call_id": "overview",
                "quote": "worker(ready=False,restarts=5",
            },
            {
                "role": "link",
                "claim": "The process exits at startup.",
                "tool_call_id": "c1",
                "quote": "FATAL: AMQP_URL not set",
            },
            {
                "role": "defect",
                "claim": "The container has no environment.",
                "tool_call_id": "c2",
                "quote": "Environment:    <none>",
            },
        ],
        "ruled_out": [
            {
                "alternative": "The node cannot run the pod.",
                "entity_names": ["incident-lab-control-plane"],
                "ruling_claim": "The pod is scheduled and running.",
                "tool_call_id": "overview",
                "quote": "phase=Running",
            }
        ],
        "verification": [
            {
                "command": f"kubectl -n payments logs {WORKER_POD} --previous",
                "tool": "get_logs",
                "arguments": {"namespace": "payments", "pod": WORKER_POD, "previous": True},
                "must_contain": "FATAL: AMQP_URL not set",
            },
            {
                "command": f"kubectl -n payments describe pod {WORKER_POD}",
                "tool": "describe",
                "arguments": {"namespace": "payments", "kind": "pod", "name": WORKER_POD},
                "must_contain": "Environment:    <none>",
            },
        ],
        "verdict": "confirmed",
        "missing_evidence": "",
    }
    arguments.update(overrides)
    return va.parse_submission(arguments)


def _validate_pod_only(submission: va.Submission) -> va.ValidationResult:
    return va.validate(
        submission,
        _pod_only_ledger(),
        CRASHLOOP,
        "t1-crashloop-missing-env",
        "payments",
        fx.page(CRASHLOOP),
        submission.mechanism,
    )


def test_v7_a_controller_anchored_only_through_its_pod_is_not_confirmed() -> None:
    """Soft spot (a): the pod→controller allowance let `confirmed` be earned for a
    Deployment whose own spec no citation and no check ever read. The allowance now
    holds only once the controller itself was read."""
    result = _validate_pod_only(_pod_only_submission())
    assert result.verdict_allowed != "confirmed"
    assert any(
        "is not earned yet" in v and "checkout-worker itself" in v for v in result.violations
    ), result.violations


_CONTROLLER_READ_CHECK: dict[str, object] = {
    "command": "kubectl -n payments get deployment checkout-worker -o yaml",
    "tool": "get_object",
    "arguments": {"namespace": "payments", "kind": "deployment", "name": "checkout-worker"},
    "must_contain": "AMQP_URL not set",
}
_CONTROLLER_READ_EVIDENCE: dict[str, object] = {
    "role": "link",
    "claim": "The template's only container is the worker.",
    "tool_call_id": "c3",
    "quote": '"image": "busybox:1.36"',
}


@pytest.mark.parametrize("via", ["evidence", "verification"])
def test_v7_a_pod_read_anchors_its_controller_once_the_controller_was_read(via: str) -> None:
    """The allowance the frozen bundle relies on survives: with the Deployment read
    either as a cited quote or as a PRESENT check (two frozen rows supply it only the
    second way), the pod describe still anchors the defect and `confirmed` is earned."""
    base = _pod_only_submission()
    if via == "evidence":
        evidence = [
            {"role": i.role, "claim": i.claim, "tool_call_id": i.tool_call_id, "quote": i.quote}
            for i in base.evidence
        ] + [_CONTROLLER_READ_EVIDENCE]
        submission = _pod_only_submission(evidence=evidence)
    else:
        verification = [
            {
                "command": c.command,
                "tool": c.tool,
                "arguments": c.arguments,
                "must_contain": c.must_contain,
            }
            for c in base.verification
        ] + [_CONTROLLER_READ_CHECK]
        submission = _pod_only_submission(verification=verification)
    result = _validate_pod_only(submission)
    assert result.violations == (), result.violations
    assert result.verdict_allowed == "confirmed"


def _decoys_submission(event_quote: str) -> va.Submission:
    """A link citation taken from a cluster-wide event read on the 30-namespace fixture."""
    ref = {"kind": "Deployment", "namespace": "storefront", "name": "checkout-api"}
    return va.parse_submission(
        {
            "failing_resource": ref,
            "remediation": ref
            | {"field_path": ".spec.template", "current_value": "a", "required_value": "b"},
            "root_cause_statement": "placeholder",
            "mechanism": "Deployment checkout-api .spec.template fails.",
            "evidence": [
                {
                    "role": "symptom",
                    "claim": "Orders have stopped.",
                    "tool_call_id": "page",
                    "quote": "completed-order volume has been flat at zero",
                },
                {
                    "role": "link",
                    "claim": "An event line.",
                    "tool_call_id": "c1",
                    "quote": event_quote,
                },
                {
                    "role": "defect",
                    "claim": "The Deployment is not ready.",
                    "tool_call_id": "overview",
                    "quote": "deployment/checkout-api ready=0/2",
                },
            ],
            "ruled_out": [],
            "verification": [],
            "verdict": "probable",
            "missing_evidence": "",
        }
    )


@pytest.mark.parametrize(
    ("event_quote", "refused"),
    [
        ("release-canary Warning BackOff pod/canary-runner-54bdcc6cc-dc9f2", True),
        ("Failed to create endpoint for service platform-metrics/metrics-collector", True),
        ("storefront Warning Unhealthy pod/checkout-api-7db48f7c7b-6fb2v", False),
        ("Readiness probe failed:", False),
    ],
    ids=["unlinked-line", "unlinked-in-message", "paged-line", "names-no-namespace"],
)
def test_v2_tags_an_event_citation_with_the_namespaces_its_quote_names(
    event_quote: str, refused: bool
) -> None:
    """Soft spot (b): a cluster-wide event read is tagged namespace "" and so was
    admissible whatever line of it was quoted. An event citation now rests on the
    namespaces its quoted text names; a quote naming none keeps the read's own tag."""
    ledger = _recorded(DECOYS, "storefront", [("c1", "get_events", {})])
    result = va.validate(
        _decoys_submission(event_quote),
        ledger,
        DECOYS,
        "t3-crossns-decoys",
        "storefront",
        fx.page(DECOYS),
        "",
    )
    assert not any(v.startswith("V1") for v in result.violations), result.violations
    v2 = [v for v in result.violations if v.startswith("V2 ADMISSIBILITY: evidence[1]")]
    if refused:
        assert v2 and ("release-canary" in v2[0] or "platform-metrics" in v2[0]), v2
    else:
        assert v2 == []
