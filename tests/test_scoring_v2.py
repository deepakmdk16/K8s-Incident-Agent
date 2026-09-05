"""Tests for the additive v2 scorer (evals/scoring_v2.py).

Three guarantees: parity — every phrasing the frozen rubric classifies scores
identically under v2; the one new class classifies its natural phrasings
uniquely and never co-matches a frozen class's phrasing; and the cluster-scoped
namespace convention accepts every "no namespace" spelling for a cluster-scoped
kind while a namespaced kind keeps its namespace as part of its identity.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from evals import scoring as v1
from evals import scoring_v2 as v2

REPO = Path(__file__).resolve().parents[1]
FROZEN_GOLD = sorted((REPO / "evals" / "scenarios").glob("*/gold.json"))
V2_GOLD = sorted((REPO / "evals" / "scenarios-v2").glob("*/gold.json"))

# Phrasings the frozen test suite pins, plus the undercall and shotgun shapes:
# v2 must return exactly what v1 returns on every one of them.
FROZEN_PHRASINGS: tuple[str, ...] = (
    "The search Service's selector does not match the search pods' labels, so its "
    "Endpoints object is empty; the crashlooping batch job is unrelated.",
    "Pods are Ready and readiness probes pass, yet Endpoints is empty because the "
    "Service selector does not match the pod labels.",
    "The readiness probe targets port 8081 but the container listens on 8080, so "
    "probes fail and pods never become Ready, leaving Endpoints empty.",
    "The deployment references a renamed ConfigMap key (couldn't find key db_url), so "
    "container creation fails; the report worker's restarts and OOM state are unrelated.",
    "The RoleBinding subject names a nonexistent ServiceAccount, so the sync worker's "
    "API requests return 403 Forbidden.",
    "The cache warmup exceeds the container's 64Mi memory limit and the kernel "
    "OOM-kills it (exit code 137) on every start.",
    "The service selector does not match the pod labels, and the namespace resource "
    "quota is exceeded.",
    "Something is broken somewhere.",
    "The worker container requires AMQP_URL which is unset, so it exits fatally at "
    "startup and kubelet restarts it in crash-loop backoff.",
)


def _values(classes: frozenset[v1.FaultClass] | frozenset[v2.FaultClass]) -> set[str]:
    return {cls.value for cls in classes}


# --- parity with the frozen rubric -------------------------------------------


def test_v2_enum_is_a_superset_of_the_frozen_enum_by_value() -> None:
    assert {cls.value for cls in v1.FaultClass} < {cls.value for cls in v2.FaultClass}
    assert {cls.value for cls in v2.FaultClass} - {cls.value for cls in v1.FaultClass} == {
        "webhook-admission-block"
    }


@pytest.mark.parametrize("mechanism", FROZEN_PHRASINGS)
def test_frozen_phrasings_classify_identically_under_v2(mechanism: str) -> None:
    assert _values(v2.classify_mechanism(mechanism)) == _values(v1.classify_mechanism(mechanism))


@pytest.mark.parametrize("path", FROZEN_GOLD, ids=lambda p: p.parent.name)
def test_frozen_gold_summaries_classify_identically_under_v2(path: Path) -> None:
    gold = v1.load_gold(path)
    assert _values(v2.classify_mechanism(gold.mechanism_summary)) == {gold.fault_class.value}
    v2_gold = v2.load_gold(path)
    assert v2_gold.fault_class.value == gold.fault_class.value
    assert v2.normalized_resource(v2_gold.failing_resource) == gold.failing_resource.normalized()


@pytest.mark.parametrize("path", V2_GOLD, ids=lambda p: p.parent.name)
def test_v2_gold_validates_and_self_classifies(path: Path) -> None:
    gold = v2.load_gold(path)
    assert gold.case_id == path.parent.name
    assert v2.classify_mechanism(gold.mechanism_summary) == frozenset({gold.fault_class})


# --- the new class ------------------------------------------------------------

WEBHOOK = v2.FaultClass.WEBHOOK_ADMISSION_BLOCK


@pytest.mark.parametrize(
    "mechanism",
    [
        # gold form: object, field by API path, observed error text, what fails
        "The ValidatingWebhookConfiguration workload-standards has .webhooks[0]"
        ".clientConfig.service pointing at Service policy-guard in namespace platform-policy, "
        "neither of which exists, and with failurePolicy Fail the API server refuses every "
        "pod create with 'failed calling webhook ... service \"policy-guard\" not found', so "
        "the checkout-api ReplicaSet cannot create pods.",
        # terse SRE
        "Orphaned validating webhook: its backing service is gone and failurePolicy is Fail, "
        "so all pod creates error out.",
        # names the rollout symptom too: dominance must collapse rollout-stuck
        "The admission webhook validate.policy-guard.platform.internal fails to be called "
        "(service not found), which blocks the new ReplicaSet from creating pods and leaves "
        "the rollout stuck at 0 updated replicas.",
        # 'denied' wording without any rbac noun
        "Pod creation is denied by a validating webhook whose endpoint the API server cannot "
        "reach.",
    ],
    ids=["gold-form", "terse", "with-rollout-symptom", "denied-wording"],
)
def test_webhook_phrasings_classify_exactly(mechanism: str) -> None:
    assert v2.classify_mechanism(mechanism) == frozenset({WEBHOOK})


@pytest.mark.parametrize(
    ("mechanism", "expected"),
    [
        (
            "The ResourceQuota checkout-quota caps pods at 2, so the quota admission "
            "controller rejects every further pod create with 'exceeded quota'.",
            "resource-quota-exceeded",
        ),
        (
            "The RoleBinding's subjects[0].name is inventory-synk while the ServiceAccount is "
            "inventory-sync, so every namespaced read is denied with 403 Forbidden.",
            "rbac-denial",
        ),
        (
            "The Service selector does not match the pod labels so Endpoints is empty; the "
            "cluster's admission webhooks are unrelated and were ruled out.",
            "service-selector-mismatch",
        ),
    ],
    ids=["quota-admission-wording", "rbac-denied", "selector-negated-webhook"],
)
def test_other_classes_do_not_co_match_webhook(mechanism: str, expected: str) -> None:
    assert _values(v2.classify_mechanism(mechanism)) == {expected}


@pytest.mark.parametrize(
    "mechanism",
    [
        # every generic failure verb the solution's mechanism audit accepts
        "The webhook's Service does not exist, so the API server is unable to admit pods.",
        "The webhook can't be reached; pod creation timed out at admission.",
        "There is no such Service behind the webhook, so pods are refused before scheduling "
        "and every request is turned away.",
        # the scheduling bucket is dominated, like the rollout bucket
        "Because the webhook is unreachable, pods are rejected before scheduling and the "
        "ReplicaSet's create requests keep failing.",
    ],
    ids=["does-not-exist+unable", "cant+timed-out", "no-such+scheduling", "scheduling-bucket"],
)
def test_generic_failure_verbs_and_dominated_buckets_still_classify_webhook(
    mechanism: str,
) -> None:
    assert v2.classify_mechanism(mechanism) == frozenset({WEBHOOK})


# Disclosed collisions (scoring_v2 module comment). These rows pin the KNOWN
# ambiguous behaviour so it is discovered here, not in a scored bundle.
@pytest.mark.parametrize(
    ("mechanism", "expected"),
    [
        (
            # a decoy webhook named INSIDE a failing sentence co-matches — decoys
            # belong in ruled_out, exactly as for every frozen class
            "The RoleBinding subject names a nonexistent ServiceAccount so reads are denied "
            "with 403; the policy webhook in the cluster is unrelated.",
            {"rbac-denial", "webhook-admission-block"},
        ),
        (
            # prose "namespace selector" carries the selector class's vocabulary
            "The webhook's namespace selector does not match the paged namespace, so the "
            "API server fails every create there.",
            {"service-selector-mismatch", "webhook-admission-block"},
        ),
        (
            # the API-path spelling of the same fact does not
            "The webhook's .namespaceSelector admits the paged namespace, and its service is "
            "not found, so the API server fails every create there.",
            {"webhook-admission-block"},
        ),
    ],
    ids=["decoy-webhook-in-rbac-sentence", "namespace-selector-prose", "namespace-selector-path"],
)
def test_disclosed_collisions_behave_as_documented(mechanism: str, expected: set[str]) -> None:
    assert _values(v2.classify_mechanism(mechanism)) == expected


# --- what the arms are shown must carry no class vocabulary ------------------------

# Pre-existing v2 ids that encode their mechanism, kept as they are (renaming a
# captured case is a re-capture); the rule applies to every case after them.
_MECHANISM_NAMED_IDS = frozenset({"t2-crossns-externalname-selector", "t3-crossns-decoys"})


def _group_one_hits(text: str) -> set[str]:
    lowered = text.lower()
    return {
        cls.value
        for cls, groups in v2.signature_table().items()
        if any(re.search(pattern, lowered) for pattern in groups[0])
    }


@pytest.mark.parametrize("path", V2_GOLD, ids=lambda p: p.parent.name)
def test_v2_page_and_id_name_no_class(path: Path) -> None:
    """The page is the only scenario text an arm sees (README rule 5), and the case id
    is echoed to both arms: neither may hand over a class's object noun."""
    page = (path.parent / "page.txt").read_text(encoding="utf-8")
    assert v2.classify_mechanism(page) == frozenset(), path.parent.name
    assert _group_one_hits(page) == set(), path.parent.name
    if path.parent.name not in _MECHANISM_NAMED_IDS:
        assert _group_one_hits(path.parent.name.replace("-", " ")) == set()


# --- cluster-scoped namespace convention ---------------------------------------


@pytest.mark.parametrize(
    "spelling", [*sorted(v2.NO_NAMESPACE_SPELLINGS), "Cluster-Scoped", "  none  "]
)
def test_every_no_namespace_spelling_normalizes_for_a_cluster_scoped_kind(spelling: str) -> None:
    answered = v1.FailingResource("ValidatingWebhookConfiguration", spelling, "Policy-Guard")
    expected = ("validatingwebhookconfiguration", "", "policy-guard")
    assert v2.normalized_resource(answered) == expected


@pytest.mark.parametrize("kind", ["validatingwebhookconfigurations", "vwc", "mwc", "pv", "sc"])
def test_cluster_scoped_kind_aliases_resolve(kind: str) -> None:
    assert v2.canonical_kind(kind) in v2.CLUSTER_SCOPED_KINDS


def test_a_namespaced_kind_keeps_its_namespace_as_identity() -> None:
    assert v2.normalized_resource(v1.FailingResource("deployment", "-", "web")) == (
        "deployment",
        "-",
        "web",
    )
    assert v2.normalized_resource(v1.FailingResource("deploy", "Shop", "web")) == (
        "deployment",
        "shop",
        "web",
    )


def test_parse_answer_allows_no_namespace_only_for_cluster_scoped_kinds() -> None:
    base = {"case_id": "c", "mechanism": "m", "verdict": "confirmed"}
    accepted = v2.parse_answer(
        json.dumps(base | {"failing_resource": {"kind": "clusterrole", "name": "x"}})
    )
    assert accepted.failing_resource.namespace == ""
    with pytest.raises(ValueError, match="namespace"):
        v2.parse_answer(
            json.dumps(
                base | {"failing_resource": {"kind": "deployment", "namespace": "", "name": "x"}}
            )
        )
    # everything the frozen parser rejects, v2 rejects too
    with pytest.raises(ValueError):
        v2.parse_answer(json.dumps(base | {"failing_resource": {"kind": "deployment"}}))
    with pytest.raises(ValueError, match="inconclusive"):
        v2.parse_answer(
            json.dumps(
                base
                | {
                    "failing_resource": {"kind": "node", "name": "n"},
                    "verdict": "inconclusive",
                }
            )
        )


def test_score_case_matches_a_cluster_scoped_gold_across_spellings(tmp_path: Path) -> None:
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "case_id": "t9-example",
                "tier": "T2",
                "failing_resource": {
                    "kind": "validatingwebhookconfiguration",
                    "namespace": "",
                    "name": "workload-standards",
                },
                "fault_class": "webhook-admission-block",
                "mechanism_summary": "The webhook cannot be called so pod creates fail.",
                "decisive_evidence": "x",
                "remediation_summary": "y",
            }
        ),
        encoding="utf-8",
    )
    gold = v2.load_gold(gold_path)
    assert gold.fault_class is WEBHOOK
    answer = v1.Answer(
        case_id="t9-example",
        failing_resource=v1.FailingResource(
            "ValidatingWebhookConfiguration", "cluster-scoped", "workload-standards"
        ),
        mechanism="The validating webhook fails to be called, so pod creation is refused.",
        verdict="probable",
    )
    score = v2.score_case(answer, gold)
    assert score.resource_correct and score.class_correct and score.root_cause_correct
    wrong_ns = v1.Answer(
        case_id="t9-example",
        failing_resource=v1.FailingResource("deployment", "", "workload-standards"),
        mechanism=answer.mechanism,
        verdict="probable",
    )
    assert not v2.score_case(wrong_ns, gold).resource_correct


def test_v2_gold_rejects_unknown_class_and_empty_namespace_on_namespaced_kind(
    tmp_path: Path,
) -> None:
    good = json.loads(FROZEN_GOLD[0].read_text(encoding="utf-8"))
    for corruption in (
        {"fault_class": "meteor-strike"},
        {"failing_resource": {"kind": "deployment", "namespace": "", "name": "x"}},
    ):
        bad = tmp_path / "gold.json"
        bad.write_text(json.dumps(good | corruption), encoding="utf-8")
        with pytest.raises(ValueError):
            v2.load_gold(bad)


# --- anti-leak tripwire, extended to v2 -----------------------------------------


def test_v2_class_and_module_never_leak_into_agent_dirs() -> None:
    banned = [cls.value for cls in v2.FaultClass] + ["scoring_v2", "evals/scoring_v2"]
    hits: list[str] = []
    for agent_dir in (REPO / "baseline", REPO / "solution"):
        for path in sorted(agent_dir.rglob("*")):
            if not path.is_file() or path.name == ".gitkeep":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            hits.extend(f"{path.relative_to(REPO)}: {token}" for token in banned if token in text)
    assert not hits, f"v2 fault-class vocabulary leaked into agent code/prompts: {hits}"
