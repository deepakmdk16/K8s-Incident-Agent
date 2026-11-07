"""Validator invariance under renaming: the 36 accepted frozen submissions, replayed
on a consistently renamed copy of each fixture.

A gate that accepts an argument on the names it was tuned on and refuses the
same argument under other names is reading the names, not the argument. Each
frozen fixture and its gold are renamed with evals/perturb.py under one fixed
seed; the same rename map is applied to the recorded tool-call arguments and to
the submission; the ledger is re-executed against the renamed copy; and the
validator has to accept all 36 as `confirmed`, exactly as it does on the
originals (tests/test_frozen_replay.py). Offline, no LLM.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from evals import perturb
from solution import render
from solution import validate as va
from tests.replay_support import (
    FIXTURES,
    RUNS,
    SCENARIOS,
    accepted_submission_and_ledger,
    recorded_submission,
    run_id,
)

SEED = 7
CASES = sorted({transcript.parent.name for transcript in RUNS})

Perturbed = tuple[Path, perturb.RenameMap]


@pytest.fixture(scope="module")
def perturbed(tmp_path_factory: pytest.TempPathFactory) -> Callable[[str], Perturbed]:
    """Each case's fixture and gold, renamed once for the whole module."""
    cache: dict[str, Perturbed] = {}

    def for_case(case_id: str) -> Perturbed:
        if case_id not in cache:
            out = tmp_path_factory.mktemp(case_id)
            rename_map = perturb.perturb(
                FIXTURES / case_id, SCENARIOS / case_id / "gold.json", out, SEED
            )
            cache[case_id] = (out / "fixture", rename_map)
        return cache[case_id]

    return for_case


def test_every_frozen_case_is_renamed_without_a_kept_name(
    perturbed: Callable[[str], Perturbed],
) -> None:
    """A name the perturbation had to leave alone would make the replay below
    weaker than it claims; on the frozen set there is none."""
    assert len(CASES) == 12
    for case_id in CASES:
        _, rename_map = perturbed(case_id)
        assert rename_map.namespaces, case_id
        assert rename_map.kept == (), (case_id, rename_map.kept)


@pytest.mark.parametrize("transcript", RUNS, ids=run_id)
def test_every_accepted_frozen_submission_is_still_accepted_under_renaming(
    transcript: Path, perturbed: Callable[[str], Perturbed]
) -> None:
    case_id = transcript.parent.name
    fixture, rename_map = perturbed(case_id)
    renamer = rename_map.renamer()
    submission, ledger, namespace, page_text, names = accepted_submission_and_ledger(
        transcript, fixture, renamer.arguments
    )
    # Not vacuous: the rename reached the submission's own failing resource.
    recorded = va.parse_submission(recorded_submission(transcript))
    assert submission.failing_resource.name == renamer.text(recorded.failing_resource.name)
    assert submission.failing_resource.name != recorded.failing_resource.name
    preview = render.render_report(
        case_id, submission, va.ValidationResult((), "probable", (), ()), names
    )
    result = va.validate(submission, ledger, fixture, case_id, namespace, page_text, preview)
    assert result.violations == (), result.violations
    assert result.verdict_allowed == "confirmed"
    assert all(present for _, present in result.verification_results)
