"""Tests for the additive v3 fix-scoring column (evals/scoring_v3.py).

Four guarantees: the normalization and matching rules do what the gold-v3
README says; arms whose contract lacks the fix fields score n/a, never 0;
every case in every scenario root has exactly one gold-v3 file and it
parses; and a replay over the committed bundles reproduces the numbers
recorded in docs/experiments/2026-11-07-scoring-v3.md, including the
decomposition of every frozen-bundle miss into one spelling.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from evals import scoring_v3 as v3
from evals.run_eval import SCENARIO_ROOTS, discover_cases

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "evals" / "results"
FROZEN_BUNDLE = RESULTS / "20260829T090941Z-solution"
LATER_BUNDLES = (
    RESULTS / "20260904T083020Z-solution",
    RESULTS / "20260904T124832Z-solution",
    RESULTS / "20260905T044853Z-solution",
)

CANON = "spec.template.spec.containers[0].readinessprobe.httpget.port"


# --- normalization ---------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "spec.template.spec.containers[0].readinessProbe.httpGet.port",
        "  Spec.Template.Spec.Containers[0].ReadinessProbe.HttpGet.Port  ",
        "`spec.template.spec.containers[0].readinessProbe.httpGet.port`",
        "/spec/template/spec/containers/0/readinessProbe/httpGet/port",
        ".spec.template.spec.containers.0.readinessProbe.httpGet.port",
        "$.spec.template.spec.containers.[0].readinessProbe.httpGet.port",
    ],
)
def test_field_path_spellings_canonicalize_to_the_dotted_indexed_form(raw: str) -> None:
    assert v3.normalize_field_path(raw) == CANON


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('containers[name="api"].image', "containers[name=api].image"),
        ("containers[name='api'].image", "containers[name=api].image"),
        ("containers[name:api].image", "containers[name=api].image"),
        ('spec.selector["app"]', "spec.selector[app]"),
        # The bare-name bracket form is left as written here; path_readings reads
        # it both ways at match time (v3.1, README "Matching").
        ("containers[api].image", "containers[api].image"),
    ],
)
def test_name_form_quotes_and_colons_fold_but_bare_names_stay(raw: str, expected: str) -> None:
    assert v3.normalize_field_path(raw) == expected


WHOLE_OBJECT_SPELLINGS = ("", ".", "<object>", "(object)", "object", "whole object", "the object")


@pytest.mark.parametrize("raw", [*WHOLE_OBJECT_SPELLINGS, "Entire object"])
def test_whole_object_spellings_canonicalize(raw: str) -> None:
    assert v3.normalize_field_path(raw) == v3.OBJECT_PATH


@pytest.mark.parametrize(
    ("normalized", "expected"),
    [
        ("containers[0].image", {"containers[0].image"}),
        ("containers[name=api].image", {"containers[name=api].image"}),
        ("containers[api].image", {"containers[name=api].image", "containers.api.image"}),
        ("spec.hard[pods]", {"spec.hard[name=pods]", "spec.hard.pods"}),
        (
            "initcontainers[wait-for-db].env[db_host].value",
            {
                "initcontainers[name=wait-for-db].env[name=db_host].value",
                "initcontainers[name=wait-for-db].env.db_host.value",
                "initcontainers.wait-for-db.env[name=db_host].value",
                "initcontainers.wait-for-db.env.db_host.value",
            },
        ),
    ],
)
def test_path_readings_expand_bare_brackets_and_nothing_else(
    normalized: str, expected: set[str]
) -> None:
    """v3.1 (2026-11-07): a bare bracket is a list element's name or a map key; the
    scorer has no schema, so both readings are tried. Indexes and key=value stay."""
    assert set(v3.path_readings(normalized)) == expected


def test_a_field_named_object_dot_something_is_not_the_whole_object() -> None:
    assert v3.normalize_field_path("metadata") == "metadata"
    assert v3.normalize_field_path("object.spec") == "object.spec"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"app": "catalog"}', "app:catalog"),
        ("app=catalog", "app:catalog"),
        ("app: catalog", "app:catalog"),
        ('"8080"', "8080"),
        ("`postgres-primary`", "postgres-primary"),
        ("Ignore", "ignore"),
        ("8080 (the containerPort)", "8080(thecontainerport)"),
    ],
)
def test_value_normalization(raw: str, expected: str) -> None:
    assert v3.normalize_value(raw) == expected


# --- matching ---------------------------------------------------------------------


def _gold(required_value: str | None, accepted_values: tuple[str, ...] = ()) -> v3.GoldFix:
    return v3.GoldFix(
        case_id="x",
        field_path="spec.template.spec.containers[0].image",
        accepted_field_paths=("spec.template.spec.containers[name=api].image",),
        required_value=required_value,
        accepted_required_values=accepted_values,
        source="test",
    )


ROW: dict[str, object] = {
    "run": 1,
    "case_id": "x",
    "tier": "T1",
    "root_cause_correct": True,
    "class_correct": True,
}


def _node(path: str, value: str) -> dict[str, object]:
    return {"field_path": path, "required_value": value}


def test_both_correct_via_the_name_alias_and_a_quoted_value() -> None:
    row = v3.score_submission(
        ROW,
        _node('spec.template.spec.containers[name="api"].image', '"nginx:1.27"'),
        _gold("nginx:1.27"),
    )
    assert (row.fix_path_correct, row.fix_value_correct, row.fix_both_correct) == (True, True, True)
    assert row.miss is None and row.status == v3.STATUS_SCORED


def test_path_wrong_value_right_is_a_path_miss() -> None:
    row = v3.score_submission(
        ROW, _node("spec.template.spec.containers[0].command", "nginx:1.27"), _gold("nginx:1.27")
    )
    assert (row.fix_path_correct, row.fix_value_correct, row.fix_both_correct) == (
        False,
        True,
        False,
    )
    assert row.miss == "path"


def test_path_right_value_wrong_is_a_value_miss() -> None:
    row = v3.score_submission(
        ROW,
        _node("spec.template.spec.containers[0].image", "nginx:1.27 (latest stable)"),
        _gold("nginx:1.27"),
    )
    assert (row.fix_path_correct, row.fix_value_correct, row.fix_both_correct) == (
        True,
        False,
        False,
    )
    assert row.miss == "value"


def test_both_wrong_is_a_path_plus_value_miss() -> None:
    row = v3.score_submission(ROW, _node("spec.replicas", "3"), _gold("nginx:1.27"))
    assert row.miss == "path+value"


def test_open_value_scores_the_path_only() -> None:
    row = v3.score_submission(
        ROW, _node("spec.template.spec.containers[0].image", "anything"), _gold(None)
    )
    assert row.fix_path_correct is True
    assert row.fix_value_correct is None and row.fix_both_correct is None
    assert row.value_open and row.miss is None


def test_no_accepted_submission_is_a_miss_not_an_na() -> None:
    row = v3.score_submission(ROW, None, _gold("nginx:1.27"))
    assert row.status == v3.STATUS_NO_SUBMISSION
    assert (row.fix_path_correct, row.fix_value_correct, row.fix_both_correct) == (
        False,
        False,
        False,
    )
    assert row.miss == "no_submission"
    open_row = v3.score_submission(ROW, None, _gold(None))
    assert open_row.fix_value_correct is None and open_row.miss == "no_submission"


def test_an_errored_row_scores_without_class_correct_and_a_malformed_row_says_so() -> None:
    """run_eval writes an errored row with no score block (Outcome.row): the column
    reads it as a no-submission miss instead of crashing on the missing key, and a
    row that lacks the key for any other reason is refused (review of PR #8)."""
    errored: dict[str, object] = {
        "run": 1,
        "case_id": "x",
        "tier": "T1",
        "error": "boom",
        "verdict": "invalid",
        "root_cause_correct": False,
    }
    row = v3.score_submission(errored, None, _gold("nginx:1.27"))
    assert row.class_correct is False and row.status == v3.STATUS_NO_SUBMISSION
    malformed = {k: v for k, v in errored.items() if k != "error"}
    with pytest.raises(KeyError, match="no class_correct and no error"):
        v3.score_submission(malformed, None, _gold("nginx:1.27"))


def test_a_missing_transcript_for_an_accepted_row_is_an_error_not_a_miss(tmp_path: Path) -> None:
    row: dict[str, object] = {"run": 1, "case_id": "c", "error": None, "metrics": {}}
    with pytest.raises(FileNotFoundError, match="no transcript for an accepted row"):
        v3.accepted_remediation(tmp_path, row)


def test_accepted_submission_is_the_last_submit_unless_salvaged(tmp_path: Path) -> None:
    case = tmp_path / "run1" / "c"
    case.mkdir(parents=True)

    def submit(turn: int, path: str) -> dict[str, object]:
        node = {"remediation": {"field_path": path}}
        return {"turn": turn, "calls": [{"name": "submit_answer", "arguments": node}]}

    records: list[dict[str, object]] = [
        submit(1, "a"),
        {"turn": 2, "calls": [{"name": "get_object", "arguments": {}}]},
        submit(3, "b"),
    ]
    (case / "transcript.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")

    def row(error: str | None, salvaged: bool) -> dict[str, object]:
        return {"run": 1, "case_id": "c", "error": error, "metrics": {"salvaged": salvaged}}

    node = v3.accepted_remediation(tmp_path, row(None, salvaged=False))
    assert node is not None and node["field_path"] == "b"
    assert v3.accepted_remediation(tmp_path, row(None, salvaged=True)) is None
    assert v3.accepted_remediation(tmp_path, row("boom", salvaged=False)) is None


# --- arms without the fields -------------------------------------------------------


@pytest.mark.parametrize("bundle", ["20260905T044850Z-baseline", "20260905T044845Z-rules"])
def test_arms_without_fix_fields_report_na_never_zero(bundle: str) -> None:
    result = v3.score_bundle(RESULTS / bundle)
    assert not result.emits_fix_fields
    assert result.rows and all(r.status == v3.STATUS_NOT_EMITTED for r in result.rows)
    assert all(r.fix_path_correct is None and r.fix_value_correct is None for r in result.rows)
    table = v3.render_markdown(result)
    assert "| n/a | n/a | n/a | n/a | n/a |" in table
    for line in table.splitlines():
        if line.startswith("| T") or line.startswith("| all"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            assert cells[3:8] == ["n/a"] * 5, line


# --- gold tree ---------------------------------------------------------------------


def _scored_cases() -> set[str]:
    """Every case evals/run_eval.py scores, found by its own discovery — one
    authority, so a new root or case cannot be scored without its fix gold
    (2026-11-11: the held-out root had none until CHANGELOG [28])."""
    return {case.case_id for root in SCENARIO_ROOTS for case in discover_cases(root=root)}


def test_every_case_has_exactly_one_gold_v3_file_and_it_parses() -> None:
    golds = v3.load_gold_dir()
    assert set(golds) == _scored_cases()
    assert len(list(v3.GOLD_DIR.glob("*.json"))) == len(golds)


def test_gold_v3_pins_a_value_except_the_documented_open_cases() -> None:
    golds = v3.load_gold_dir()
    open_cases = {cid for cid, g in golds.items() if g.required_value is None}
    assert open_cases == {
        "t1-crashloop-missing-env",
        "t1-imagepull-bogus-tag",
        "t1-oom-cache-warmup",
        "t1-reefer-telemetry-offline",
        "t1-unschedulable-cpu-requests",
        "t2-quota-blocks-scale",
    }
    for g in golds.values():
        # Every spelling the gold lists must score correct when submitted
        # verbatim: membership in path_forms is true by construction, so the
        # check is the matcher's own path (review of PR #11).
        for spelling in (g.field_path, *g.accepted_field_paths):
            readings = v3.path_readings(v3.normalize_field_path(spelling))
            assert readings & g.path_forms(), (g.case_id, spelling)
        assert g.source.strip()
        if g.required_value is None:
            assert g.accepted_required_values == ()


def test_gold_v3_loader_rejects_a_malformed_file(tmp_path: Path) -> None:
    bad = tmp_path / "t1-x.json"
    bad.write_text(
        json.dumps(
            {
                "case_id": "other",
                "field_path": "",
                "required_value": None,
                "accepted_required_values": ["a"],
            }
        )
    )
    with pytest.raises(ValueError) as excinfo:
        v3.load_gold(bad)
    message = str(excinfo.value)
    for fragment in (
        "case_id must equal",
        "field_path must be",
        "accepted_field_paths",
        "accepted_required_values must be empty",
        "source",
    ):
        assert fragment in message, message


# --- replay of the committed bundles (the numbers in the experiment doc) --------------


def _cells(t: v3.TierTotals) -> tuple[int, int, int, int, int, int, int]:
    return (
        t.rows,
        t.path_correct,
        t.value_scorable,
        t.value_correct,
        t.both_correct,
        t.no_submission,
        t.value_open,
    )


BARE_BRACKET_NAME = re.compile(r"\[(?!\d+\])(?![a-z]+=)[a-z0-9_.-]+\]")


def test_frozen_bundle_replays_the_recorded_v3_numbers() -> None:
    result = v3.score_bundle(FROZEN_BUNDLE)
    assert result.emits_fix_fields and result.arm == "solution"
    # (rows, path_correct, value_scorable, value_correct, both_correct, no_submission, value_open)
    # v3.1 numbers (2026-11-07); v3.0 measured (15, 5, …), (15, 8, …), (6, 3, …),
    # (36, 16, 21, 21, 12, 0, 15) — docs/experiments/2026-11-07-scoring-v3.md.
    assert _cells(result.by_tier["T1"]) == (15, 15, 3, 3, 3, 0, 12)
    assert _cells(result.by_tier["T2"]) == (15, 15, 12, 12, 12, 0, 3)
    assert _cells(result.by_tier["T3"]) == (6, 6, 6, 6, 6, 0, 0)
    assert _cells(result.overall) == (36, 36, 21, 21, 21, 0, 15)
    assert result.overall.root_cause_correct == 36


V3_0_PATH_MISSES = {
    (1, "t1-imagepull-bogus-tag"),
    (1, "t1-oom-cache-warmup"),
    (1, "t1-unschedulable-cpu-requests"),
    (1, "t2-init-wait-for-migrations"),
    (1, "t2-readiness-wrong-port"),
    (1, "t3-overlapping-config-and-oom"),
    (2, "t1-crashloop-missing-env"),
    (2, "t1-imagepull-bogus-tag"),
    (2, "t1-oom-cache-warmup"),
    (2, "t2-init-wait-for-migrations"),
    (2, "t2-quota-blocks-scale"),
    (2, "t2-readiness-wrong-port"),
    (2, "t3-overlapping-config-and-oom"),
    (3, "t1-crashloop-missing-env"),
    (3, "t1-imagepull-bogus-tag"),
    (3, "t1-oom-cache-warmup"),
    (3, "t1-unschedulable-cpu-requests"),
    (3, "t2-init-wait-for-migrations"),
    (3, "t2-readiness-wrong-port"),
    (3, "t3-overlapping-config-and-oom"),
}


def test_the_twenty_v3_0_misses_are_path_correct_only_through_a_v3_1_reading() -> None:
    """The regression corpus for the v3.1 rule: every row v3.0 refused spells a list
    element or map key as a bare name in brackets. Each still fails the literal
    match (so the rule, not a gold edit, is what changed) and passes through one of
    its readings; the frozen bundle has no miss left."""
    result = v3.score_bundle(FROZEN_BUNDLE)
    assert result.misses() == []
    rows = {(r.run, r.case_id): r for r in result.rows}
    for key in V3_0_PATH_MISSES:
        row = rows[key]
        assert row.field_path is not None and row.fix_path_correct, key
        normalized = v3.normalize_field_path(row.field_path)
        assert BARE_BRACKET_NAME.search(normalized), row.field_path
        forms = v3.load_gold(REPO / "evals" / "gold-v3" / f"{row.case_id}.json").path_forms()
        assert normalized not in forms, (key, normalized)
        assert v3.path_readings(normalized) & forms, (key, normalized)


def test_later_bundles_replay_the_recorded_v3_numbers() -> None:
    crossns, decoys, webhook = (v3.score_bundle(b) for b in LATER_BUNDLES)
    assert _cells(crossns.overall) == (3, 3, 3, 3, 3, 0, 0) and set(crossns.by_tier) == {"T2"}
    assert _cells(decoys.overall) == (3, 3, 3, 3, 3, 0, 0) and set(decoys.by_tier) == {"T3"}
    assert _cells(webhook.overall) == (3, 0, 3, 0, 0, 2, 0) and set(webhook.by_tier) == {"T2"}
    kinds = sorted((m.run, m.miss) for m in webhook.misses())
    assert kinds == [(1, "no_submission"), (2, "no_submission"), (3, "path+value")]


@pytest.mark.parametrize("bundle", [FROZEN_BUNDLE, *LATER_BUNDLES], ids=lambda p: p.name)
def test_committed_scoring_v3_json_matches_a_fresh_run(bundle: Path) -> None:
    committed = json.loads((bundle / "scoring_v3.json").read_text(encoding="utf-8"))
    assert committed == v3.to_json(v3.score_bundle(bundle))
