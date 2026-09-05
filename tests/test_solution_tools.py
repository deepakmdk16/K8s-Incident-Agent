"""Offline tests for the solution arm's tool layer, against real captured fixtures.

No network, no LLM. Every assertion is about a projection the agent will actually
receive, because a renderer that is only proven on synthetic input proves nothing
about the run that gets scored.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from solution import tools as tl

FIXTURES = Path(__file__).resolve().parents[1] / "evals" / "fixtures"
RBAC = FIXTURES / "t2-rbac-sync-forbidden"
QUIET = FIXTURES / "t3-quiet-selector-loud-crashloop"
INIT = FIXTURES / "t2-init-wait-for-migrations"
OVERLAP = FIXTURES / "t3-overlapping-config-and-oom"
CROSSNS = FIXTURES / "t2-crossns-externalname-selector"
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
    from solution import fixture as fx

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
        ("get_object", {"namespace": "x", "kind": "validatingwebhookconfigurations", "name": "y"}),
        ("describe", {"namespace": "x", "kind": "validatingwebhookconfiguration", "name": "y"}),
        ("describe", {"namespace": "inventory", "kind": "clusterrole", "name": "y"}),
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
        "describe-webhook",
        "describe-clusterrole",
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


def test_serves_kind_is_the_namespaced_roster_today() -> None:
    assert tl.serves_kind("deployments") and tl.serves_kind("ConfigMap")
    assert not tl.serves_kind("validatingwebhookconfigurations")
    assert not tl.serves_kind("clusterroles")


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
