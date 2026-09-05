"""Offline tests for the solution arm's fixture reader.

No network, no LLM. Every assertion runs against real captured fixtures, so
the reader is proven on the data the scored run will actually serve — a path
helper that only works on synthetic input is worth nothing at eval time.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from solution import fixture as fx

FIXTURES = Path(__file__).resolve().parents[1] / "evals" / "fixtures"
RBAC = FIXTURES / "t2-rbac-sync-forbidden"


def test_every_fixture_has_the_same_captured_kinds() -> None:
    """The tool surface is uniform: a kind that resolves on one case resolves on all.

    Cluster kinds are checked against what the fixture's capture schema
    guarantees: the frozen schema-1 snapshots are byte-identical forever and
    cannot gain the kinds capture schema 2 added, so on them those kinds read as
    "not captured" (in-band error) rather than as present-and-empty.
    """
    for case in sorted(p for p in FIXTURES.iterdir() if p.is_dir()):
        for ns in fx.namespaces(case):
            present = {p.stem for p in (case / "ns" / ns).glob("*.json")}
            assert present == set(fx.NAMESPACED_KINDS), f"{case.name}/{ns}"
        cluster = {p.stem for p in (case / "cluster").glob("*.json")}
        assert fx.expected_cluster_kinds(case) <= cluster, case.name


def test_capture_schema_is_read_from_the_ledger() -> None:
    assert fx.capture_schema(RBAC) == 1


def test_capture_schema_missing_line_is_an_error_not_schema_one(tmp_path: Path) -> None:
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "scenario.yaml").write_text("id: broken\nmode: captured\n", encoding="utf-8")
    with pytest.raises(fx.FixtureError, match="no capture schema"):
        fx.capture_schema(broken)


def test_schema_two_kinds_resolve_and_read_as_not_captured_on_a_frozen_fixture() -> None:
    """The lockstep pair moved together: the reader knows the kind, the frozen
    snapshot honestly does not have it."""
    assert fx.normalize_kind("ValidatingWebhookConfiguration") == "validatingwebhookconfigurations"
    assert fx.normalize_kind("mutatingwebhookconfigurations") == "mutatingwebhookconfigurations"
    assert fx.CLUSTER_KINDS_SINCE_SCHEMA_2.issubset(fx.CLUSTER_KINDS)
    assert fx.expected_cluster_kinds(RBAC).isdisjoint(fx.CLUSTER_KINDS_SINCE_SCHEMA_2)
    with pytest.raises(fx.FixtureError, match="not captured in this snapshot"):
        fx.load_kind(RBAC, "validatingwebhookconfigurations")


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [
        ("po", "pods"),
        ("Pod", "pods"),
        ("pods", "pods"),
        ("deploy", "deployments"),
        ("rolebinding", "rolebindings"),
        ("RoleBindings", "rolebindings"),
        ("sa", "serviceaccounts"),
        ("netpol", "networkpolicies"),
        ("pvc", "persistentvolumeclaims"),
        ("endpoints", "endpoints"),
    ],
)
def test_kind_aliases_resolve_the_way_kubectl_accepts_them(spelling: str, expected: str) -> None:
    assert fx.normalize_kind(spelling) == expected


def test_unknown_kind_names_the_captured_kinds_instead_of_returning_empty() -> None:
    with pytest.raises(fx.FixtureError, match="unknown or uncaptured kind"):
        fx.normalize_kind("widgets")


def test_describe_matches_api_group_qualified_capture_filenames() -> None:
    """Capture names describes from `get -o name`, so the file is `deployment.apps_x.txt`."""
    assert "inventory-sync" in fx.describe(RBAC, "deployment", "inventory-sync", "inventory")
    assert "inventory-reader-binding" in fx.describe(
        RBAC, "rolebinding", "inventory-reader-binding", "inventory"
    )


def test_logs_of_a_ready_pod_are_reachable() -> None:
    """The baseline's blind spot: this pod is 1/1 Running, and its log holds the fault."""
    pod = "inventory-sync-5cf949f7f9-czxsq"
    assert fx.containers(RBAC, "inventory", pod) == ["sync"]
    assert "403 Forbidden" in fx.logs(RBAC, "inventory", pod)


def test_unavailable_log_channel_is_an_error_not_an_empty_string() -> None:
    with pytest.raises(fx.FixtureError, match="previous log channel"):
        fx.logs(RBAC, "inventory", "inventory-sync-5cf949f7f9-czxsq", "sync", previous=True)


def test_rbac_objects_and_serviceaccounts_are_queryable() -> None:
    bindings = fx.load_kind(RBAC, "rolebindings", "inventory")
    subjects = [s["name"] for b in bindings for s in b["subjects"]]
    accounts = [s["metadata"]["name"] for s in fx.load_kind(RBAC, "sa", "inventory")]
    assert subjects == ["inventory-synk"]
    assert "inventory-sync" in accounts and "inventory-synk" not in accounts


def test_cluster_scoped_kinds_need_no_namespace() -> None:
    assert [n["metadata"]["name"] for n in fx.load_kind(RBAC, "nodes")]
    assert fx.load_kind(RBAC, "clusterroles")


def test_events_narrow_to_one_namespace() -> None:
    everything = fx.events(RBAC)
    scoped = fx.events(RBAC, "inventory")
    assert 0 < len(scoped) < len(everything)
    assert all(e["metadata"]["namespace"] == "inventory" for e in scoped)


@pytest.mark.parametrize("hostile", ["../../etc/passwd", "..", "a/b", "", "-flag"])
def test_names_that_could_escape_the_fixture_are_refused(hostile: str) -> None:
    with pytest.raises(fx.FixtureError, match="invalid"):
        fx.describe(RBAC, "pod", hostile, "inventory")


def test_unknown_namespace_lists_the_real_ones() -> None:
    with pytest.raises(fx.FixtureError, match="no namespace"):
        fx.load_kind(RBAC, "pods", "nope")


def test_no_tool_can_serve_a_real_secret_value() -> None:
    """Design req 2: capture keeps Secret objects but redacts every value.

    Objects are kept on purpose — a bad-secret-ref fault has to stay
    diagnosable — so the invariant to guard is the values, not the items.
    """
    seen = 0
    for case in sorted(p for p in FIXTURES.iterdir() if p.is_dir()):
        for ns in fx.namespaces(case):
            for secret in fx.load_kind(case, "secrets", ns):
                for field in ("data", "stringData"):
                    values = cast(dict[str, str], secret.get(field) or {})
                    for key, value in values.items():
                        seen += 1
                        assert value == "REDACTED-BY-CAPTURE", (
                            f"{case.name}/{ns}/{secret['metadata']['name']}.{field}.{key}"
                        )
    assert seen > 0, "no secret values examined — the guard would pass vacuously"


def test_log_channel_reason_explains_an_absent_current_channel() -> None:
    """An absent log is evidence: this container has none because it never started."""
    init = FIXTURES / "t2-init-wait-for-migrations"
    reason = fx.log_channel_reason(init, "billing", "billing-api-ccb44c44c-89dn7", "api")
    assert reason is not None
    assert "PodInitializing" in reason


def test_log_channel_reason_is_none_for_a_channel_that_was_captured() -> None:
    init = FIXTURES / "t2-init-wait-for-migrations"
    assert (
        fx.log_channel_reason(init, "billing", "billing-api-ccb44c44c-89dn7", "wait-for-db") is None
    )


def test_log_channel_reason_reads_the_previous_channel_separately() -> None:
    pod = "inventory-sync-5cf949f7f9-czxsq"
    assert fx.log_channel_reason(RBAC, "inventory", pod, "sync") is None
    previous = fx.log_channel_reason(RBAC, "inventory", pod, "sync", previous=True)
    assert previous is not None and "not found" in previous


def test_log_channel_reason_is_none_for_an_unknown_pod() -> None:
    assert fx.log_channel_reason(RBAC, "inventory", "no-such-pod", "sync") is None
