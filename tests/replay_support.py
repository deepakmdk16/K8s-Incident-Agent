"""Shared replay machinery: rebuild a frozen run's tool ledger from its transcript.

Used by tests/test_frozen_replay.py (the frozen fixture, names as recorded) and
tests/test_perturb_replay.py (a consistently renamed copy of it). One helper,
so the two replays cannot drift apart in what they rebuild.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

from solution import agent, render
from solution import fixture as fx
from solution import tools as tl
from solution import validate as va

ROOT = Path(__file__).resolve().parents[1]
FROZEN_BUNDLE = ROOT / "evals" / "results" / "20260829T090941Z-solution"
FIXTURES = ROOT / "evals" / "fixtures"
SCENARIOS = ROOT / "evals" / "scenarios"

RUNS = sorted(FROZEN_BUNDLE.glob("run*/*/transcript.jsonl"))

Arguments = dict[str, object]


def run_id(transcript: Path) -> str:
    return f"{transcript.parts[-3]}/{transcript.parts[-2]}"


def _as_recorded(arguments: Arguments) -> Arguments:
    return arguments


def _recorded_calls(transcript: Path) -> list[tuple[str, Arguments]]:
    calls: list[tuple[str, Arguments]] = []
    for line in transcript.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = cast(dict[str, object], json.loads(line))
        for call in cast(list[dict[str, object]], record.get("calls", [])):
            calls.append((str(call["name"]), cast(Arguments, call["arguments"])))
    return calls


def recorded_submission(transcript: Path) -> Arguments:
    """The arguments of the accepted submit_answer call: the last one in a 36/36 bundle."""
    submits = [
        arguments for name, arguments in _recorded_calls(transcript) if name == tl.SUBMIT_ANSWER
    ]
    assert submits, f"{transcript}: no submit_answer call"
    return submits[-1]


def accepted_submission_and_ledger(
    transcript: Path,
    fixture: Path,
    rewrite: Callable[[Arguments], Arguments] = _as_recorded,
) -> tuple[va.Submission, tl.ToolLedger, str, str, dict[str, str]]:
    """The ledger (and its source labels) as the agent had them at its accepted submit,
    and that submission.

    `rewrite` is applied to every recorded call's arguments and to the submission
    before anything is re-executed against `fixture`: the identity for a replay
    on the recorded names, a rename map for a replay on a renamed copy.
    """
    page_text = fx.page(fixture)
    namespace = agent.paged_namespace(fixture, page_text)
    overview = tl.render_namespace_overview(fixture, namespace)
    ledger = tl.ToolLedger()
    ledger.record(tl.ToolInvocation("page", "page", {}, page_text, frozenset({""})))
    ledger.record(tl.ToolInvocation("overview", "overview", {}, overview, frozenset({namespace})))
    ledger_names = {"page": "the page", "overview": f"namespace_overview({namespace})"}
    accepted: Arguments | None = None
    seq = 0
    for name, recorded in _recorded_calls(transcript):
        arguments = rewrite(recorded)
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
