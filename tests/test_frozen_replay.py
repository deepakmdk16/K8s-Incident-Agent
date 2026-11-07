"""Replay every accepted frozen-set submission through today's validator.

The 36 rows in the committed frozen bundle were all accepted as `confirmed` by
the validator as it stood on 2026-08-29. Every later tightening of the gate has
to leave them accepted, or the headline number would rest on a gate that no
longer exists. This test rebuilds each run's tool ledger from its transcript by
re-executing the same calls against the frozen fixture, feeds the accepted
submission through `validate`, and requires the same outcome. Offline, no LLM.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from solution import render
from solution import validate as va
from tests.replay_support import FIXTURES, RUNS, accepted_submission_and_ledger, run_id


def test_the_frozen_bundle_exists_and_is_complete() -> None:
    assert len(RUNS) == 36, f"expected 36 frozen rows, found {len(RUNS)}"


@pytest.mark.parametrize("transcript", RUNS, ids=run_id)
def test_every_accepted_frozen_submission_is_still_accepted_as_confirmed(transcript: Path) -> None:
    case_id = transcript.parent.name
    fixture = FIXTURES / case_id
    submission, ledger, namespace, page_text, names = accepted_submission_and_ledger(
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
