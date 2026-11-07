"""Round-trip properties of evals/perturb.py on a real frozen fixture.

Everything here runs against a renamed copy written into tmp_path: the frozen
fixtures and golds are read, never written, and one test proves it.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from evals import perturb, scoring
from solution import fixture as fx
from solution import tools as tl

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals" / "fixtures"
SCENARIOS = ROOT / "evals" / "scenarios"
CASE = "t2-rbac-sync-forbidden"
FIXTURE = FIXTURES / CASE
GOLD = SCENARIOS / CASE / "gold.json"
SEED = 7

_TOKEN = re.compile(r"[a-z0-9][a-z0-9-]*")

Snapshot = set[tuple[str, int, int]]


def _tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _snapshot(root: Path) -> Snapshot:
    return {
        (p.relative_to(root).as_posix(), p.stat().st_size, p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }


@pytest.fixture(scope="module")
def perturbed(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, perturb.RenameMap, Snapshot, Snapshot]:
    before = _snapshot(ROOT / "evals")
    out = tmp_path_factory.mktemp("perturbed")
    rename_map = perturb.perturb(FIXTURE, GOLD, out, SEED)
    return out, rename_map, before, _snapshot(ROOT / "evals")


# --- the Renamer on its own ---------------------------------------------------


def test_renamer_matches_whole_runs_and_keeps_letter_case() -> None:
    renamer = perturb.Renamer({"prod": "xkqz", "web": "vnt"})
    assert renamer.text("web.prod.svc.cluster.local") == "vnt.xkqz.svc.cluster.local"
    assert renamer.text("production preprod Prod PROD") == "production preprod Xkqz XKQZ"
    # A derived name follows its stem: the hash, ordinal or suffix is kept.
    assert renamer.text("web-7d9f8-x2kq4 data-web-0 web-") == "vnt-7d9f8-x2kq4 data-vnt-0 vnt-"


def test_renamer_prefers_the_longest_entry_and_keeps_shields_verbatim() -> None:
    renamer = perturb.Renamer({"prod": "xkqz", "prod-eu": "xkqz-wp"}, shields=("t2-prod-stalled",))
    assert renamer.text("prod prod-eu t2-prod-stalled") == "xkqz xkqz-wp t2-prod-stalled"


def test_renamer_value_rewrites_nested_strings_and_nothing_else() -> None:
    renamer = perturb.Renamer({"prod": "xkqz"})
    before: dict[str, object] = {
        "namespace": "prod",
        "tail": 50,
        "previous": True,
        "nested": [{"pod": "prod-7d9f8-x2kq4"}],
        "prod": "a key is a field name, not a value",
    }
    assert renamer.arguments(before) == {
        "namespace": "xkqz",
        "tail": 50,
        "previous": True,
        "nested": [{"pod": "xkqz-7d9f8-x2kq4"}],
        "prod": "a key is a field name, not a value",
    }


# --- the renamed copy ---------------------------------------------------------


def test_every_old_name_is_absent_from_the_output_tree(
    perturbed: tuple[Path, perturb.RenameMap, Snapshot, Snapshot],
) -> None:
    """Stricter than the renamer's own match: an old name must not survive as a
    run anywhere — not in a file, not in a path, not inside a longer token."""
    out, rename_map, _, _ = perturbed
    olds = sorted(rename_map.renames, key=len, reverse=True)
    leak = re.compile(
        r"(?<![a-z0-9])(?:" + "|".join(map(re.escape, olds)) + r")(?![a-z0-9])", re.IGNORECASE
    )
    leaks = {
        f"{relative}: {match.group(0)}"
        for relative, text in _tree(out).items()
        if relative != "rename-map.json"
        for match in [*leak.finditer(relative), *leak.finditer(text)]
    }
    assert not leaks, sorted(leaks)[:20]


def test_the_copy_is_a_loadable_fixture(
    perturbed: tuple[Path, perturb.RenameMap, Snapshot, Snapshot],
) -> None:
    out, rename_map, _, _ = perturbed
    fixture = out / "fixture"
    for path in fixture.rglob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))
    namespace = rename_map.namespaces["inventory"]
    expected = sorted(set(fx.namespaces(FIXTURE)) - {"inventory"} | {namespace})
    assert fx.namespaces(fixture) == expected
    for kind in fx.NAMESPACED_KINDS:
        fx.load_kind(fixture, kind, namespace)
    for kind in fx.expected_cluster_kinds(fixture):
        fx.load_kind(fixture, kind)
    assert fx.events(fixture, namespace)
    assert fx.capture_schema(fixture) == fx.capture_schema(FIXTURE)
    assert namespace in fx.page(fixture)
    binding = rename_map.names["inventory-reader-binding"]
    assert binding in fx.describe(fixture, "rolebinding", binding, namespace)
    pod = rename_map.names["inventory-sync-5cf949f7f9-czxsq"]
    assert "403 Forbidden" in fx.logs(fixture, namespace, pod)


def test_the_gold_parses_and_names_the_mapped_resource(
    perturbed: tuple[Path, perturb.RenameMap, Snapshot, Snapshot],
) -> None:
    out, rename_map, _, _ = perturbed
    gold = scoring.load_gold(out / "gold.json")
    original = scoring.load_gold(GOLD)
    assert gold.case_id == original.case_id == CASE
    assert gold.tier == original.tier
    assert gold.fault_class == original.fault_class
    assert gold.failing_resource.kind == original.failing_resource.kind
    assert gold.failing_resource.namespace == rename_map.namespaces["inventory"]
    assert gold.failing_resource.name == rename_map.names["inventory-reader-binding"]
    assert rename_map.names["inventory-sync"] in gold.mechanism_summary


def test_new_names_are_same_length_dns_labels_new_to_the_snapshot(
    perturbed: tuple[Path, perturb.RenameMap, Snapshot, Snapshot],
) -> None:
    _, rename_map, _, _ = perturbed
    existing: set[str] = set()
    for text in _tree(FIXTURE).values():
        existing.update(_TOKEN.findall(text.lower()))
    renames = rename_map.renames
    assert len(set(renames.values())) == len(renames), "two names were renamed to one"
    for old, new in renames.items():
        assert len(new) == len(old), (old, new)
        assert perturb.DNS_LABEL.fullmatch(new), (old, new)
        assert [i for i, c in enumerate(old) if c == "-"] == [
            i for i, c in enumerate(new) if c == "-"
        ], (old, new)
        assert new not in existing, (old, new)


def test_derived_names_follow_their_controller(
    perturbed: tuple[Path, perturb.RenameMap, Snapshot, Snapshot],
) -> None:
    _, rename_map, _, _ = perturbed
    names = rename_map.names
    deployment = names["inventory-sync"]
    replicaset = names["inventory-sync-5cf949f7f9"]
    pod = names["inventory-sync-5cf949f7f9-czxsq"]
    assert replicaset == f"{deployment}-5cf949f7f9"
    assert pod == f"{replicaset}-czxsq"


def test_a_decoy_one_letter_off_the_real_name_stays_one_letter_off(
    perturbed: tuple[Path, perturb.RenameMap, Snapshot, Snapshot],
) -> None:
    """The fault in this case is a typo'd RoleBinding subject (inventory-synk for
    inventory-sync). A rename that broke that relation would change the case."""
    out, rename_map, _, _ = perturbed
    namespace = rename_map.namespaces["inventory"]
    bindings: list[dict[str, Any]] = fx.load_kind(out / "fixture", "rolebindings", namespace)
    subject = str(bindings[0]["subjects"][0]["name"])
    real = rename_map.names["inventory-sync"]
    assert subject != real
    assert len(subject) == len(real)
    assert sum(a != b for a, b in zip(subject, real, strict=True)) == 1


def test_system_namespaces_nodes_and_system_objects_are_untouched(
    perturbed: tuple[Path, perturb.RenameMap, Snapshot, Snapshot],
) -> None:
    out, rename_map, _, _ = perturbed
    fixture = out / "fixture"
    for system in perturb.SYSTEM_NAMESPACES:
        assert (fixture / "ns" / system).is_dir(), system
    nodes = [n["metadata"]["name"] for n in fx.load_kind(fixture, "nodes")]
    assert nodes == [n["metadata"]["name"] for n in fx.load_kind(FIXTURE, "nodes")]
    namespace = rename_map.namespaces["inventory"]
    configmaps = {c["metadata"]["name"] for c in fx.load_kind(fixture, "configmaps", namespace)}
    assert "kube-root-ca.crt" in configmaps
    accounts = {s["metadata"]["name"] for s in fx.load_kind(fixture, "serviceaccounts", namespace)}
    assert "default" in accounts

    def header(root: Path) -> str:
        return (root / "cluster" / "get-all.txt").read_text(encoding="utf-8").splitlines()[0]

    assert header(fixture) == header(FIXTURE)
    assert rename_map.kept == ()
    assert rename_map.shields == ()


def test_same_seed_gives_the_same_tree_and_another_seed_other_names(
    perturbed: tuple[Path, perturb.RenameMap, Snapshot, Snapshot], tmp_path: Path
) -> None:
    out, rename_map, _, _ = perturbed
    again = tmp_path / "again"
    assert perturb.perturb(FIXTURE, GOLD, again, SEED) == rename_map
    assert _tree(again) == _tree(out)
    other = perturb.build_rename_map(FIXTURE, GOLD, SEED + 1)
    assert set(other.renames) == set(rename_map.renames)
    assert other.renames != rename_map.renames


def test_nothing_was_written_under_evals(
    perturbed: tuple[Path, perturb.RenameMap, Snapshot, Snapshot],
) -> None:
    out, _, before, after = perturbed
    assert (ROOT / "evals").resolve() not in out.resolve().parents
    assert after == before


def test_a_user_name_equal_to_a_json_key_or_describe_label_is_kept(tmp_path: Path) -> None:
    """Renamed as text, a namespace called `webhooks` would rewrite the `"webhooks"`
    key of every copied configuration and one called `policy` the `Failure Policy:`
    label of every describe, so tools reading the copy would silently find nothing.
    Such names are kept and reported (review of PR #8, 2026-11-07)."""
    case = "t2-checkout-release-stalled"
    fixture = tmp_path / "fixture"
    shutil.copytree(FIXTURES / case, fixture)
    for namespace in ("webhooks", "policy"):
        (fixture / "ns" / namespace).mkdir()
        (fixture / "ns" / namespace / "pods.json").write_text('{"items": []}\n', encoding="utf-8")
    rename_map = perturb.build_rename_map(
        fixture, ROOT / "evals" / "scenarios-v2" / case / "gold.json", SEED
    )
    assert {"webhooks", "policy"} <= set(rename_map.kept)
    assert not {"webhooks", "policy"} & set(rename_map.namespaces)


def test_refuses_to_write_in_place_or_over_a_non_empty_directory(tmp_path: Path) -> None:
    with pytest.raises(perturb.PerturbError, match="in place"):
        perturb.perturb(FIXTURE, GOLD, FIXTURE / "perturbed", SEED)
    with pytest.raises(perturb.PerturbError, match="in place"):
        perturb.perturb(FIXTURE, GOLD, GOLD.parent, SEED)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "x").write_text("x", encoding="utf-8")
    with pytest.raises(perturb.PerturbError, match="not empty"):
        perturb.perturb(FIXTURE, GOLD, occupied, SEED)
    assert not (FIXTURE / "perturbed").exists()


def test_cli_writes_the_copy_and_the_map(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "cli"
    perturb.main(
        ["--fixture", str(FIXTURE), "--gold", str(GOLD), "--out", str(out), "--seed", str(SEED)]
    )
    written = json.loads((out / "rename-map.json").read_text(encoding="utf-8"))
    assert written["seed"] == SEED
    assert set(written["namespaces"]) == {"inventory"}
    assert set(written["names"]) == {
        "inventory-reader",
        "inventory-reader-binding",
        "inventory-sources",
        "inventory-sync",
        "inventory-sync-5cf949f7f9",
        "inventory-sync-5cf949f7f9-czxsq",
        "inventory-sync-scripts",
    }
    assert (out / "fixture" / "page.txt").is_file()
    assert (out / "gold.json").is_file()
    assert "renamed 1 namespace(s) and 7 object name(s)" in capsys.readouterr().out


def test_read_tools_commute_with_the_rename(
    perturbed: tuple[Path, perturb.RenameMap, Snapshot, Snapshot],
) -> None:
    """rename(tool(fixture, args)) == tool(rename(fixture), rename(args)) — what the
    replay in tests/test_perturb_replay.py rests on, checked tool by tool. The
    find_consumers trailer is the one that broke first: a re-sorted name list
    (docs/failure-modes.md 2026-11-07)."""
    out, rename_map, _, _ = perturbed
    renamer = rename_map.renamer()
    pod = "inventory-sync-5cf949f7f9-czxsq"
    binding = "inventory-reader-binding"
    calls: list[tuple[str, dict[str, object]]] = [
        ("namespace_overview", {"namespace": "inventory"}),
        ("get_object", {"namespace": "inventory", "kind": "configmaps"}),
        ("get_object", {"namespace": "inventory", "kind": "rolebinding", "name": binding}),
        ("describe", {"namespace": "inventory", "kind": "pod", "name": pod}),
        ("get_logs", {"namespace": "inventory", "pod": pod, "tail": 20}),
        ("get_events", {"namespace": "inventory"}),
        ("find_consumers", {"namespace": "inventory", "kind": "cm", "name": "inventory-sources"}),
        ("find_consumers", {"namespace": "inventory", "kind": "sa", "name": "inventory-sync"}),
        ("cluster_capacity", {}),
    ]
    for name, arguments in calls:
        expected = renamer.text(tl.dispatch(FIXTURE, name, arguments))
        actual = tl.dispatch(out / "fixture", name, renamer.arguments(arguments))
        assert actual == expected, name
