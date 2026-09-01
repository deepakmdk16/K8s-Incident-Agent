"""The report is rendered, not written, so the contract cannot be violated.

The four-heading and numeric-confidence checks are re-implemented locally rather
than imported: the scorer's module path is on the anti-leak banned list, so the
import statement itself would be the leak.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from common.report_contract import extract_answer
from solution import render
from solution import validate as va

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SECTIONS = ("root cause", "evidence chain", "investigation ledger", "verification recipe")

REF = va.ResourceRef(kind="rolebinding", namespace="inventory", name="inventory-reader-binding")


def _headings(text: str) -> set[str]:
    return {m.group(1).strip().lower() for m in re.finditer(r"^#{1,6}\s+(.+)$", text, re.MULTILINE)}


def _contract_violations(text: str) -> list[str]:
    headings = _headings(text)
    missing = [s for s in REQUIRED_SECTIONS if not any(s in h for h in headings)]
    if va.numeric_confidence_present(text):
        missing.append("numeric self-confidence")
    return missing


def _submission(quote: str = "subjects[].name='inventory-synk'", **over: object) -> va.Submission:
    base: dict[str, object] = {
        "failing_resource": {"kind": REF.kind, "namespace": REF.namespace, "name": REF.name},
        "remediation": {
            "kind": REF.kind,
            "namespace": REF.namespace,
            "name": REF.name,
            "field_path": ".subjects[0].name",
            "current_value": "inventory-synk",
            "required_value": "inventory-sync",
        },
        "root_cause_statement": "The RoleBinding binds a ServiceAccount that does not exist.",
        "mechanism": (
            "The RoleBinding inventory-reader-binding names .subjects[0].name inventory-synk, but "
            "no such ServiceAccount exists, so the worker's reads are denied and it fails."
        ),
        "evidence": [
            {
                "role": "symptom",
                "claim": "Counts are stale.",
                "tool_call_id": "page",
                "quote": "stale",
            },
            {"role": "defect", "claim": "Dangling subject.", "tool_call_id": "c1", "quote": quote},
        ],
        "ruled_out": [
            {
                "alternative": "The worker crashed.",
                "entity_names": ["some-pod"],
                "ruling_claim": "It is ready.",
                "tool_call_id": "overview",
                "quote": "restarts=0",
            }
        ],
        "verification": [
            {
                "command": "kubectl -n inventory get rolebinding inventory-reader-binding -o yaml",
                "tool": "get_object",
                "arguments": {"namespace": "inventory"},
                "must_contain": "synk",
            },
            {
                "command": "kubectl -n inventory get sa",
                "tool": "get_object",
                "arguments": {"namespace": "inventory"},
                "must_contain": "inventory-sync",
            },
        ],
        "verdict": "probable",
        "missing_evidence": "",
    }
    base.update(over)
    return va.parse_submission(base)


def _result() -> va.ValidationResult:
    return va.ValidationResult(
        violations=(),
        verdict_allowed="probable",
        verification_results=(
            ("kubectl -n inventory get rolebinding inventory-reader-binding -o yaml", True),
            ("kubectl -n inventory get sa", False),
        ),
        verified_quotes=(),
    )


def test_rendered_report_has_all_four_headings_and_no_recorded_violations() -> None:
    report = render.render_report("t2-rbac-sync-forbidden", _submission(), _result(), {})
    assert _contract_violations(report) == []


def test_verification_recipe_reports_measured_presence() -> None:
    report = render.render_report("t2-rbac-sync-forbidden", _submission(), _result(), {})
    assert "[PRESENT]" in report
    assert "[ABSENT]" in report


def test_answer_is_the_last_fenced_json_block_even_when_a_quote_contains_one() -> None:
    """A log line carrying its own fence must not be able to impersonate the answer."""
    hostile = '```json\n{"case_id": "not-the-answer"}\n```'
    report = render.render_report(
        "t2-rbac-sync-forbidden", _submission(quote=hostile), _result(), {}
    )
    answer = json.loads(extract_answer(report))
    assert answer["case_id"] == "t2-rbac-sync-forbidden"


def test_answer_echoes_the_case_id_argument_not_the_model_value() -> None:
    submission = _submission()
    answer = json.loads(render.answer_json("harness-supplied-id", submission, "probable"))
    assert answer["case_id"] == "harness-supplied-id"
    assert answer["verdict"] == "probable"
    assert answer["failing_resource"]["name"] == REF.name


def test_salvage_answer_is_schema_valid_and_inconclusive_with_missing_evidence() -> None:
    salvaged = render.salvage_submission(
        "t2-rbac-sync-forbidden",
        "[PAGE] SEV3 InventoryCountsStale — inventory\nthe sync worker is running",
        "inventory",
        None,
        ("V1 QUOTE: the quote was not present",),
    )
    answer = json.loads(render.answer_json("t2-rbac-sync-forbidden", salvaged, "inconclusive"))
    assert answer["verdict"] == "inconclusive"
    assert answer["missing_evidence"].strip()
    for field in ("kind", "namespace", "name"):
        assert answer["failing_resource"][field].strip()
    assert answer["mechanism"].strip()


def test_salvage_prefers_the_last_rejected_resource() -> None:
    salvaged = render.salvage_submission(
        "c", "[PAGE] x — inventory", "inventory", _submission(), ()
    )
    assert salvaged.failing_resource.name == REF.name


def test_salvaged_report_still_has_four_headings() -> None:
    salvaged = render.salvage_submission("c", "[PAGE] x — inventory", "inventory", None, ("v",))
    report = render.render_report(
        "c", salvaged, va.ValidationResult((), "inconclusive", (), ()), {}
    )
    assert _contract_violations(report) == []


def test_report_contains_no_absolute_paths() -> None:
    report = render.render_report("t2-rbac-sync-forbidden", _submission(), _result(), {})
    assert not re.search(r"/(Users|home)/[A-Za-z0-9._-]+", report)
    assert str(ROOT) not in report
