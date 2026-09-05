"""Replay every accepted frozen-set submission through today's validator.

The 36 rows in the committed frozen bundle were all accepted as `confirmed` by
the validator as it stood on 2026-08-29. Every later tightening of the gate has
to leave them accepted, or the headline number would rest on a gate that no
longer exists. This test rebuilds each run's tool ledger from its transcript by
re-executing the same calls against the frozen fixture, feeds the accepted
submission through `validate`, and requires the same outcome. Offline, no LLM.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from solution import agent, render
from solution import fixture as fx
from solution import tools as tl
from solution import validate as va

ROOT = Path(__file__).resolve().parents[1]
FROZEN_BUNDLE = ROOT / "evals" / "results" / "20260829T090941Z-solution"
FIXTURES = ROOT / "evals" / "fixtures"

RUNS = sorted(FROZEN_BUNDLE.glob("run*/*/transcript.jsonl"))


def _accepted_submission_and_ledger(
    transcript: Path, fixture: Path
) -> tuple[va.Submission, tl.ToolLedger, str, str, dict[str, str]]:
    """The ledger (and its source labels) as the agent had them at its accepted submit,
    and that submission."""
    page_text = fx.page(fixture)
    namespace = agent.paged_namespace(fixture, page_text)
    overview = tl.render_namespace_overview(fixture, namespace)
    ledger = tl.ToolLedger()
    ledger.record(tl.ToolInvocation("page", "page", {}, page_text, frozenset({""})))
    ledger.record(tl.ToolInvocation("overview", "overview", {}, overview, frozenset({namespace})))
    ledger_names = {"page": "the page", "overview": f"namespace_overview({namespace})"}
    accepted: dict[str, object] | None = None
    seq = 0
    for line in transcript.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = cast(dict[str, object], json.loads(line))
        for call in cast(list[dict[str, object]], record.get("calls", [])):
            name = str(call["name"])
            arguments = cast(dict[str, object], call["arguments"])
            if name == tl.SUBMIT_ANSWER:
                accepted = arguments  # the last submit in a 36/36 bundle is the accepted one
                continue
            seq += 1
            ledger.record(
                tl.ToolInvocation(
                    f"c{seq}",
                    name,
                    arguments,
                    tl.dispatch(fixture, name, arguments),
                    tl.namespaces_touched(name, arguments),
                )
            )
            ledger_names[f"c{seq}"] = render.source_label(name, arguments)
    assert accepted is not None, f"{transcript}: no submit_answer call"
    return va.parse_submission(accepted), ledger, namespace, page_text, ledger_names


def test_the_frozen_bundle_exists_and_is_complete() -> None:
    assert len(RUNS) == 36, f"expected 36 frozen rows, found {len(RUNS)}"


@pytest.mark.parametrize("transcript", RUNS, ids=lambda p: f"{p.parts[-3]}/{p.parts[-2]}")
def test_every_accepted_frozen_submission_is_still_accepted_as_confirmed(transcript: Path) -> None:
    case_id = transcript.parent.name
    fixture = FIXTURES / case_id
    submission, ledger, namespace, page_text, names = _accepted_submission_and_ledger(
        transcript, fixture
    )
    # The preview is rendered exactly as agent.py renders it before validating.
    preview = render.render_report(
        case_id, submission, va.ValidationResult((), "probable", (), ()), names
    )
    result = va.validate(submission, ledger, fixture, case_id, namespace, page_text, preview)
    assert result.violations == (), result.violations
    assert result.verdict_allowed == "confirmed"
    assert all(present for _, present in result.verification_results)
