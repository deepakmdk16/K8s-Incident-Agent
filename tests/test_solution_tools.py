"""Offline tests for the solution arm's tool layer, against real captured fixtures.

No network, no LLM. Every assertion is about a projection the agent will actually
receive, because a renderer that is only proven on synthetic input proves nothing
about the run that gets scored.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import cast

import pytest

from solution import fixture as fx
from solution import tools as tl

FIXTURES = Path(__file__).resolve().parents[1] / "evals" / "fixtures"
RBAC = FIXTURES / "t2-rbac-sync-forbidden"
QUIET = FIXTURES / "t3-quiet-selector-loud-crashloop"
INIT = FIXTURES / "t2-init-wait-for-migrations"
OVERLAP = FIXTURES / "t3-overlapping-config-and-oom"
CROSSNS = FIXTURES / "t2-crossns-externalname-selector"
WEBHOOK = FIXTURES / "t2-checkout-release-stalled"
POD = "inventory-sync-5cf949f7f9-czxsq"


def test_overview_shows_a_ready_pod_and_its_rbac_object_counts() -> None:
    """The blind spot, closed: a 1/1 Running pod and the objects `get all` never prints."""
    out = tl.render_namespace_overview(RBAC, "inventory")
    assert "inventory-sync-5cf949f7f9-czxsq" in out
    assert "restarts=0" in out
    assert "rolebindings=1" in out
    assert len(out) < 1000


def test_overview_puts_service_selector_next_to_endpoint_count() -> None:
    out = tl.render_namespace_overview(QUIET, "search")
    assert "selector={app=search-api}" in out
    assert "endpointAddresses=0" in out
    assert "podLabels={app=search}" in out


def test_overview_never_filters_on_readiness() -> None:
    """Readiness filtering is the baseline's structural blind spot; it must not reappear here."""
    out = tl.render_namespace_overview(RBAC, "inventory")
    for pod in fx.load_kind(RBAC, "pods", "inventory"):
        assert pod["metadata"]["name"] in out


def test_logs_of_a_ready_pod_return_the_denied_line() -> None:
    out = tl.dispatch(
        RBAC, "get_logs", {"namespace": "inventory", "pod": "inventory-sync-5cf949f7f9-czxsq"}
    )
    assert "403 Forbidden" in out


def test_absent_log_channel_returns_the_capture_reason_not_an_exception() -> None:
    """An absent log is evidence: this one says the container never started."""
    out = tl.dispatch(
        INIT,
        "get_logs",
        {"namespace": "billing", "pod": "billing-api-ccb44c44c-89dn7", "container": "api"},
    )
    assert out.startswith("ERROR:")
    assert "PodInitializing" in out


def test_find_consumers_pairs_the_referrer_with_the_dangling_subject() -> None:
    out = tl.render_consumers(RBAC, "inventory", "serviceaccount", "inventory-sync")
    assert "spec.serviceAccountName" in out
    assert "inventory-synk" in out
    assert "does not match" in out


def test_find_consumers_shows_a_healthy_peer_consumer() -> None:
    """The guard against tool access creating a NEW regression on a passing row."""
    out = tl.render_consumers(OVERLAP, "orders", "configmap", "orders-config")
    assert "db_url" in out
    assert "database_url" in out
    assert "ready=1/1" in out


def test_describe_resolves_the_colliding_search_basenames() -> None:
    service = tl.dispatch(
        QUIET, "describe", {"namespace": "search", "kind": "service", "name": "search"}
    )
    deployment = tl.dispatch(
        QUIET, "describe", {"namespace": "search", "kind": "deployment", "name": "search"}
    )
    assert service != deployment
    assert not service.startswith("ERROR:") and not deployment.startswith("ERROR:")


def test_events_are_projected_not_dumped() -> None:
    out = tl.dispatch(QUIET, "get_events", {})
    assert len(out) < 3000
    assert "Warning" in out


def test_cluster_scoped_rbac_is_unreachable() -> None:
    """150 KB in every fixture, needed by no case: one unguarded call could end a run."""
    out = tl.dispatch(RBAC, "get_object", {"namespace": "inventory", "kind": "clusterroles"})
    assert out.startswith("ERROR")
    assert tl.is_not_served(out)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        # every shape the 2026-09-05 review found could stand in for a read of an
        # object no tool serves (docs/failure-modes.md)
        ("get_object", {"namespace": "inventory", "kind": "clusterroles", "name": "y"}),
        ("describe", {"namespace": "inventory", "kind": "clusterrole", "name": "y"}),
        # the webhook kinds are served since 2026-11-07, but this is a schema-1
        # (frozen) fixture that never captured them: not captured, not empty
        ("get_object", {"kind": "validatingwebhookconfigurations"}),
        ("describe", {"kind": "validatingwebhookconfiguration", "name": "y"}),
        ("get_object", {"namespace": "inventory", "kind": "widgets"}),
        # the third oracle: find_consumers' "kinds that exist" trailer read the
        # cluster file for a cluster-scoped kind (review, 2026-09-05)
        (
            "find_consumers",
            {"namespace": "inventory", "kind": "validatingwebhookconfigurations", "name": "y"},
        ),
        ("find_consumers", {"namespace": "inventory", "kind": "clusterroles", "name": "y"}),
    ],
    ids=[
        "get_object-cluster-kind",
        "describe-clusterrole",
        "get_object-webhook-uncaptured",
        "describe-webhook-uncaptured",
        "unknown-kind",
        "find_consumers-webhook",
        "find_consumers-clusterrole",
    ],
)
def test_tool_limits_are_marked_not_served(name: str, arguments: dict[str, object]) -> None:
    out = tl.dispatch(RBAC, name, arguments)
    assert tl.is_not_served(out), out
    assert tl.is_error(out) and not tl.is_evidence(out)
    assert "no describe captured" not in out


@pytest.mark.parametrize(
    ("name", "arguments", "prefix"),
    [
        ("get_events", {"namespace": "inventory", "involved_name": "nothing-here"}, "0 events"),
        (
            "get_logs",
            {"namespace": "inventory", "pod": "inventory-sync-5cf949f7f9-czxsq", "contains": "zzz"},
            "0 log",
        ),
        (
            "find_consumers",
            {"namespace": "inventory", "kind": "configmap", "name": "zzz"},
            "no workload",
        ),
        ("get_object", {"namespace": "inventory", "kind": "networkpolicies"}, "0 objects"),
    ],
    ids=["events", "logs", "consumers", "objects"],
)
def test_empty_results_echo_arguments_and_are_not_evidence(
    name: str, arguments: dict[str, object], prefix: str
) -> None:
    """An empty result repeats the name the caller typed; it shows nothing about it."""
    out = tl.dispatch(RBAC, name, arguments)
    assert out.startswith(prefix), out
    assert not tl.is_error(out) and not tl.is_evidence(out)


def test_real_results_are_evidence() -> None:
    assert tl.is_evidence(tl.dispatch(QUIET, "get_events", {"namespace": "analytics-batch"}))
    assert tl.is_evidence(
        tl.dispatch(RBAC, "get_object", {"namespace": "inventory", "kind": "rolebindings"})
    )


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("get_object", {"namespace": "inventory", "kind": "configmaps", "name": "nope"}),
        ("namespace_overview", {"namespace": "platform-policy"}),
        ("get_object", {"namespace": "no-such-ns", "kind": "services"}),
    ],
    ids=["object-absent", "namespace-absent-overview", "namespace-absent-list"],
)
def test_cluster_state_errors_stay_ordinary_errors(name: str, arguments: dict[str, object]) -> None:
    """A missing referent is evidence — often the defect itself — and stays citable."""
    out = tl.dispatch(RBAC, name, arguments)
    assert out.startswith("ERROR: ")
    assert not tl.is_not_served(out)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("get_events", {"namespace": "inventory", "name": "workload-standards"}),
        ("list_namespaces", {"name": "workload-standards"}),
        ("cluster_capacity", {"object": "workload-standards"}),
        ("namespace_overview", {"namespace": "inventory", "kind": "vwc", "name": "x"}),
        ("get_logs", {"namespace": "inventory", "pod": POD, "configuration": "x"}),
    ],
    ids=["events", "list_namespaces", "cluster_capacity", "overview", "logs"],
)
def test_an_argument_no_tool_declares_is_refused_not_ignored(
    name: str, arguments: dict[str, object]
) -> None:
    """Red-team 2026-09-05: an ignored key carrying an object's name rode along in the
    recorded call and made a real result read as a read of that object."""
    out = tl.dispatch(RBAC, name, arguments)
    assert tl.is_not_served(out), out
    assert "takes no argument" in out


def test_declared_arguments_are_still_accepted() -> None:
    out = tl.dispatch(RBAC, "get_events", {"namespace": "inventory", "warnings_only": False})
    assert not tl.is_error(out)
    out = tl.dispatch(RBAC, "get_logs", {"namespace": "inventory", "pod": POD, "tail": 5})
    assert not tl.is_error(out)


def test_serves_kind_is_the_namespaced_roster_plus_the_two_webhook_kinds() -> None:
    assert tl.serves_kind("deployments") and tl.serves_kind("ConfigMap")
    assert tl.serves_kind("validatingwebhookconfigurations")
    assert tl.serves_kind("MutatingWebhookConfiguration")
    for kind in fx.CLUSTER_KINDS:
        assert tl.serves_kind(kind) == (kind in tl.SERVED_CLUSTER_KINDS), kind
    # A kind no reader knows (a custom resource) is not served — an answer, not
    # an error: the validator asks this of whatever kind the agent submits.
    assert tl.serves_kind("routeprofile") is False


def test_configmap_values_are_never_served_only_keys() -> None:
    out = tl.dispatch(
        OVERLAP, "get_object", {"namespace": "orders", "kind": "configmap", "name": "orders-config"}
    )
    assert "dataKeys" in out
    assert '"data"' not in out


def test_unnamed_get_object_lists_names_when_the_kind_is_crowded() -> None:
    out = tl.dispatch(RBAC, "get_object", {"namespace": "kube-system", "kind": "pods"})
    assert "kube-system" in out
    assert len(out) < tl.TOOL_RESULT_CHAR_CAP


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("list_namespaces", {}),
        ("cluster_capacity", {}),
        ("namespace_overview", {"namespace": "inventory"}),
        ("get_events", {"namespace": "inventory", "warnings_only": False}),
        (
            "find_consumers",
            {"namespace": "inventory", "kind": "serviceaccount", "name": "inventory-sync"},
        ),
        ("get_object", {"namespace": "inventory", "kind": "rolebindings"}),
        ("describe", {"namespace": "inventory", "kind": "deployment", "name": "inventory-sync"}),
        ("get_logs", {"namespace": "inventory", "pod": "inventory-sync-5cf949f7f9-czxsq"}),
    ],
)
def test_every_tool_result_is_capped_and_path_free(name: str, arguments: dict[str, object]) -> None:
    out = tl.dispatch(RBAC, name, arguments)
    assert len(out) <= tl.TOOL_RESULT_CHAR_CAP + 120
    assert not re.search(r"/(Users|home)/[A-Za-z0-9._-]+", out)


def test_truncation_is_announced_never_silent() -> None:
    capped = tl.truncate("x" * (tl.TOOL_RESULT_CHAR_CAP + 500))
    assert "truncated" in capped
    assert "500 chars omitted" in capped


@pytest.mark.parametrize("hostile", ["../scenarios", "..", "a/b"])
def test_path_traversal_is_refused(hostile: str) -> None:
    assert tl.dispatch(RBAC, "namespace_overview", {"namespace": hostile}).startswith("ERROR:")


def test_an_unknown_tool_name_is_an_error_string_not_a_crash() -> None:
    assert tl.dispatch(RBAC, "rm_rf", {}).startswith("ERROR:")


def test_a_wrongly_typed_argument_is_returned_to_the_agent() -> None:
    assert tl.dispatch(RBAC, "namespace_overview", {"namespace": 7}).startswith("ERROR:")


def test_namespaces_touched_marks_cluster_scope_with_the_empty_string() -> None:
    assert tl.namespaces_touched("cluster_capacity", {}) == frozenset({""})
    assert tl.namespaces_touched("get_events", {}) == frozenset({""})
    assert tl.namespaces_touched("namespace_overview", {"namespace": "inventory"}) == frozenset(
        {"inventory"}
    )


def test_tool_specs_are_unique_and_include_the_only_exit() -> None:
    names = [spec.name for spec in tl.TOOL_SPECS]
    assert len(names) == len(set(names))
    assert tl.SUBMIT_ANSWER in names
    assert frozenset(names) - {tl.SUBMIT_ANSWER} == tl.READ_TOOL_NAMES


def test_an_externalname_service_is_not_rendered_as_a_broken_one() -> None:
    """An alias has no selector and no Endpoints by design.

    Rendered with the ClusterIP fields it read `selector={} endpointAddresses=0`
    — identical to a Service whose selector matches nothing, and an invitation
    to diagnose the healthy alias instead of the fault it points at.
    """
    out = tl.render_namespace_overview(CROSSNS, "storefront")
    assert "service/payments-gateway type=ExternalName" in out
    assert "externalName=payments-gateway.payments.svc.cluster.local" in out
    assert "endpointAddresses" not in out


def test_find_consumers_crosses_the_namespace_boundary_through_an_alias() -> None:
    """The consumer of payments/payments-gateway lives in another namespace."""
    out = tl.render_consumers(CROSSNS, "payments", "services", "payments-gateway")
    assert "service/payments-gateway in namespace storefront aliases this Service" in out
    assert "spec.externalName=payments-gateway.payments.svc.cluster.local" in out


def test_a_service_nothing_aliases_reports_no_cross_namespace_consumer() -> None:
    """The edge must not fire on every Service that happens to share a name."""
    out = tl.render_consumers(CROSSNS, "storefront", "services", "payments-gateway")
    assert "aliases this Service" not in out


# --- the webhook kinds, served since 2026-11-07 (Stage 2 of the webhook experiment) ---


def test_webhook_kinds_are_listed_and_read_without_a_namespace() -> None:
    listed = tl.dispatch(WEBHOOK, "get_object", {"kind": "validatingwebhookconfigurations"})
    assert tl.is_evidence(listed) and "workload-standards" in listed
    empty = tl.dispatch(WEBHOOK, "get_object", {"kind": "mutatingwebhookconfigurations"})
    assert empty == "0 objects of kind mutatingwebhookconfigurations cluster-wide"
    assert not tl.is_error(empty) and not tl.is_evidence(empty)


def test_a_single_webhook_read_keeps_what_a_diagnoser_needs_and_drops_the_rest() -> None:
    out = tl.dispatch(
        WEBHOOK,
        "get_object",
        {"kind": "ValidatingWebhookConfiguration", "name": "workload-standards"},
    )
    assert tl.is_evidence(out)
    for needed in (
        '"name": "validate.policy-guard.platform.internal"',
        '"name": "policy-guard"',
        '"namespace": "platform-policy"',
        '"path": "/validate"',
        '"port": 443',
        '"failurePolicy": "Fail"',
        '"matchPolicy": "Equivalent"',
        '"sideEffects": "None"',
        '"timeoutSeconds": 10',
        '"rules"',
        '"namespaceSelector"',
        '"objectSelector"',
    ):
        assert needed in out, needed
    assert "admissionReviewVersions" not in out
    assert "managedFields" not in out


def test_describe_serves_the_captured_webhook_configuration() -> None:
    out = tl.dispatch(
        WEBHOOK,
        "describe",
        {"kind": "validatingwebhookconfiguration", "name": "workload-standards"},
    )
    assert tl.is_evidence(out)
    assert "Failure Policy:  Fail" in out
    assert "Name:        policy-guard" in out


def test_describe_matches_the_configuration_name_case_insensitively_like_get_object() -> None:
    """get_object matched `Workload-Standards`; describe with the same arguments said no
    such object existed — a cluster-state-shaped error about an object that is there
    (review of PR #8, 2026-11-07)."""
    kind = "validatingwebhookconfiguration"
    lower = tl.dispatch(WEBHOOK, "describe", {"kind": kind, "name": "workload-standards"})
    mixed = tl.dispatch(WEBHOOK, "describe", {"kind": kind, "name": "Workload-Standards"})
    assert tl.is_evidence(mixed) and mixed == lower


@pytest.mark.parametrize("name", ["get_object", "describe"])
def test_a_webhook_object_absent_from_the_snapshot_is_a_cluster_state_error(name: str) -> None:
    """The name every arm guessed: now an ordinary miss that lists what IS there,
    legitimately, because get_object can list the kind."""
    out = tl.dispatch(
        WEBHOOK, name, {"kind": "validatingwebhookconfiguration", "name": "policy-guard"}
    )
    assert out.startswith("ERROR: no validatingwebhookconfiguration named 'policy-guard'"), out
    assert not tl.is_not_served(out)
    assert "present: workload-standards" in out


@pytest.mark.parametrize("kind", sorted(tl.SERVED_CLUSTER_KINDS))
def test_webhook_kinds_read_as_not_captured_on_a_frozen_fixture(kind: str) -> None:
    """Schema 1 never held them; an empty list would claim the cluster has none."""
    listed = tl.dispatch(RBAC, "get_object", {"kind": kind})
    described = tl.dispatch(RBAC, "describe", {"kind": kind, "name": "workload-standards"})
    for out in (listed, described):
        assert tl.is_not_served(out), out
        assert "not captured" in out


@pytest.mark.parametrize("name", ["get_object", "describe"])
def test_a_namespace_on_a_cluster_scoped_kind_is_refused(name: str) -> None:
    """Refused rather than ignored, so the recorded call is tagged cluster-wide."""
    out = tl.dispatch(
        WEBHOOK,
        name,
        {"namespace": "checkout", "kind": "validatingwebhookconfigurations", "name": "x"},
    )
    assert out.startswith("ERROR: ") and "omit 'namespace'" in out
    assert not tl.is_not_served(out)


def test_namespaces_touched_tags_a_webhook_read_with_the_empty_string() -> None:
    for name in ("get_object", "describe"):
        arguments = {"kind": "validatingwebhookconfigurations", "name": "workload-standards"}
        assert tl.namespaces_touched(name, arguments) == frozenset({""})


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("get_object", {"kind": "clusterroles"}),
        ("get_object", {"kind": "clusterrolebindings", "name": "cluster-admin"}),
        ("describe", {"kind": "clusterrole", "name": "cluster-admin"}),
        (
            "find_consumers",
            {
                "namespace": "checkout",
                "kind": "validatingwebhookconfigurations",
                "name": "workload-standards",
            },
        ),
    ],
    ids=["clusterroles", "clusterrolebindings", "describe-clusterrole", "consumers-of-webhook"],
)
def test_no_other_cluster_kind_became_served(name: str, arguments: dict[str, object]) -> None:
    """This fixture holds every cluster kind; only the two webhook kinds may come out,
    and only through get_object/describe — find_consumers still takes no cluster subject."""
    out = tl.dispatch(WEBHOOK, name, arguments)
    assert tl.is_not_served(out), out


def _graft_namespace(tmp_path: Path, namespace: str, services: list[str]) -> Path:
    """The webhook fixture's cluster files plus one synthetic namespace.

    The real capture has no namespace a webhook points at — that absence IS the
    case — so the edge from a Service that exists is exercised on a grafted one.
    """
    fixture = tmp_path / "grafted"
    (fixture / "cluster").mkdir(parents=True)
    for kind in tl.SERVED_CLUSTER_KINDS:
        shutil.copy(WEBHOOK / "cluster" / f"{kind}.json", fixture / "cluster")
    shutil.copy(WEBHOOK / "scenario.yaml", fixture)
    ns = fixture / "ns" / namespace
    ns.mkdir(parents=True)
    for kind in ("deployments", "statefulsets", "daemonsets"):
        (ns / f"{kind}.json").write_text(json.dumps({"items": []}), encoding="utf-8")
    items = [{"metadata": {"name": service}} for service in services]
    (ns / "services.json").write_text(json.dumps({"items": items}), encoding="utf-8")
    return fixture


def test_find_consumers_reports_the_webhook_that_calls_a_service(tmp_path: Path) -> None:
    fixture = _graft_namespace(tmp_path, "platform-policy", ["policy-guard", "other"])
    out = tl.dispatch(
        fixture,
        "find_consumers",
        {"namespace": "platform-policy", "kind": "service", "name": "policy-guard"},
    )
    assert tl.is_evidence(out)
    assert "validatingwebhookconfiguration/workload-standards (cluster-scoped) calls this" in out
    assert "webhooks[validate.policy-guard.platform.internal].clientConfig.service" in out
    assert "failurePolicy=Fail" in out
    other = tl.render_consumers(fixture, "platform-policy", "service", "other")
    assert "calls this Service" not in other


def test_the_webhook_edge_needs_the_namespace_to_match_too(tmp_path: Path) -> None:
    fixture = _graft_namespace(tmp_path, "elsewhere", ["policy-guard"])
    assert "calls this Service" not in tl.render_consumers(
        fixture, "elsewhere", "service", "policy-guard"
    )
    assert "calls this Service" not in tl.render_consumers(
        WEBHOOK, "kube-system", "svc", "kube-dns"
    )


def test_read_tool_descriptions_name_the_served_webhook_kinds() -> None:
    specs = {spec.name: spec for spec in tl.TOOL_SPECS}
    for name in ("get_object", "describe", "find_consumers"):
        assert "validatingwebhookconfiguration" in specs[name].description, name
        assert "mutatingwebhookconfiguration" in specs[name].description, name
    for name in ("get_object", "describe"):
        required = cast(list[str], specs[name].input_schema["required"])
        assert "namespace" not in required, name


def test_name_listings_keep_capture_order_rather_than_re_sorting(tmp_path: Path) -> None:
    """The API already returns a kind in name order. Re-sorting here made every
    listing a function of how the names are spelled, which a consistent rename of
    a fixture exposed as a broken verbatim quote (docs/failure-modes.md 2026-11-07).
    A planted out-of-order capture proves the order now comes from the snapshot.
    """
    import json

    from solution import fixture as fx

    namespace = tmp_path / "ns" / "shop"
    namespace.mkdir(parents=True)
    names = ["zeta", "kube-root-ca.crt", "alpha", "mid", "beta", "omega"]
    items = [{"metadata": {"name": name}, "data": {}} for name in names]
    (namespace / "configmaps.json").write_text(json.dumps({"items": items}), encoding="utf-8")
    for kind in ("deployments", "statefulsets", "daemonsets"):
        (namespace / f"{kind}.json").write_text('{"items": []}', encoding="utf-8")
    listed = ", ".join(names)
    long_form = tl.render_object(tmp_path, "shop", "configmaps", None)
    assert long_form == f"6 configmaps in shop: {listed}"
    with pytest.raises(fx.FixtureError, match=f"present: {listed}"):
        tl.render_object(tmp_path, "shop", "configmaps", "ghost")
    trailer = tl.render_consumers(tmp_path, "shop", "configmap", "ghost").splitlines()[-1]
    assert trailer == f"configmaps that exist in shop: {listed}"
