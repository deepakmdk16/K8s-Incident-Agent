"""The solution arm: a symptom-anchored backward walk over the object graph.

One agent, scoped read-only tools, and a loop whose only clean exit is a
submission that survived `solution.validate`. The contrast with the frozen
baseline is deliberate and total: that arm decides what is worth looking at
BEFORE it looks (a curated dump of whatever is not Ready) and then gets one
guess; this arm starts at the paged resource and decides what to read next from
what it has already read, then has to prove every claim before it is allowed to
finish.

`converse_fn` is the same production-signature injection seam the baseline uses
for its model call, which is what keeps the whole arm testable offline. It is
never wrapped in try/except: the harness distinguishes "the model was wrong"
from "billing refused the request" by matching on the exception text, and
swallowing an API error would turn an outage into a page of fake wrong answers.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from anthropic.types import ContentBlockParam, MessageParam, ToolResultBlockParam

from common.llm import ConverseFn, Turn, converse, load_env_file
from common.runlog import get_logger, new_run_id
from solution import fixture as fx
from solution import prompts, render
from solution import tools as tl
from solution import validate as va

MAX_TURNS = 14
MAX_TOOL_CALLS = 40
MAX_SUBMIT_REJECTS = 3
MAX_NUDGES = 2
MAX_CASE_USD = 0.60

_PAGE_NAMESPACE = re.compile(r"[—-]\s*([a-z0-9][a-z0-9-]*)\s*$")


class _Usage:
    """Running totals, written to disk after every turn so spend always survives."""

    def __init__(self) -> None:
        self.uncached = 0
        self.cache_read = 0
        self.cache_write = 0
        self.output = 0
        self.cost = 0.0
        self.model = ""

    def add(self, turn: Turn) -> None:
        self.uncached += turn.input_tokens
        self.cache_read += turn.cache_read_tokens
        self.cache_write += turn.cache_write_tokens
        self.output += turn.output_tokens
        self.cost += turn.cost_usd or 0.0
        self.model = turn.model

    @property
    def input_total(self) -> int:
        """Every token fed to the model — the like-for-like number against an uncached arm."""
        return self.uncached + self.cache_read + self.cache_write


def paged_namespace(fixture: Path, page_text: str) -> str:
    """The namespace the page is about, validated against the snapshot."""
    known = fx.namespaces(fixture)
    first_line = page_text.strip().splitlines()[0] if page_text.strip() else ""
    match = _PAGE_NAMESPACE.search(first_line)
    if match is not None and match.group(1) in known:
        return match.group(1)
    named = fx.namespaces_named_in(page_text, set(known))
    for namespace in sorted(known, key=len, reverse=True):
        if namespace in named:
            return namespace
    return known[0] if known else ""


def _seed_ledger(fixture: Path, namespace: str, page_text: str, overview: str) -> tl.ToolLedger:
    """Make the page and the anchor overview citable, since the agent was handed both."""
    ledger = tl.ToolLedger()
    ledger.record(tl.ToolInvocation("page", "page", {}, page_text, frozenset({""})))
    ledger.record(tl.ToolInvocation("overview", "overview", {}, overview, frozenset({namespace})))
    return ledger


def _tool_result(
    call_id: str, text: str, is_error: bool = False, citation_id: str | None = None
) -> ToolResultBlockParam:
    """One tool result. `citation_id` is stamped on the output the model reads.

    The API's own tool_use ids are opaque and are never rendered anywhere the
    model can copy them from, so asking it to cite one produced invented ids and
    a wall of rejections. The ledger is keyed by this short visible id instead;
    the stamp is not part of the recorded output, so a quote lifted from it
    cannot verify.
    """
    body = f"[call_id: {citation_id}]\n{text}" if citation_id else text
    return {
        "type": "tool_result",
        "tool_use_id": call_id,
        "content": body,
        "is_error": is_error,
    }


def _user(blocks: Sequence[ContentBlockParam]) -> MessageParam:
    return {"role": "user", "content": list(blocks)}


def _text_user(text: str) -> MessageParam:
    return {"role": "user", "content": text}


def _metrics(
    case_id: str,
    run_id: str,
    usage: _Usage,
    started: float,
    turns: int,
    tool_calls: int,
    submit_attempts: int,
    submission: va.Submission | None,
    result: va.ValidationResult | None,
    salvaged: bool,
) -> dict[str, object]:
    total_checks = len(result.verification_results) if result else 0
    passing_checks = sum(1 for _, ok in result.verification_results if ok) if result else 0
    return {
        "case_id": case_id,
        "arm": "solution",
        "run_id": run_id,
        "model": usage.model,
        # Every token fed to the model. With prompt caching on, the API's own
        # input_tokens counts only the uncached remainder, so reporting that
        # figure against an uncached baseline would flatter this arm.
        "input_tokens": usage.input_total,
        "output_tokens": usage.output,
        "cost_usd": round(usage.cost, 6),
        "uncached_input_tokens": usage.uncached,
        "cache_read_tokens": usage.cache_read,
        "cache_write_tokens": usage.cache_write,
        "duration_s": round(time.monotonic() - started, 2),
        "turns": turns,
        "tool_calls": tool_calls,
        "submit_attempts": submit_attempts,
        "verdict_proposed": submission.verdict_proposed if submission else "",
        "verdict_final": submission.verdict_proposed
        if submission and not salvaged
        else ("inconclusive" if salvaged else ""),
        "salvaged": salvaged,
        "evidence_items": len(submission.evidence) if submission else 0,
        "ruled_out_items": len(submission.ruled_out) if submission else 0,
        "verification_present": passing_checks,
        "verification_total": total_checks,
    }


def diagnose(
    fixture: Path,
    case_id: str,
    out_dir: Path,
    converse_fn: ConverseFn = converse,
) -> Path:
    """Work one case; write the evidence bundle; return the answer path."""
    run_id = new_run_id()
    log = get_logger(run_id, name="solution")
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()

    page_text = fx.page(fixture)
    namespace = paged_namespace(fixture, page_text)
    overview = tl.render_namespace_overview(fixture, namespace)
    ledger = _seed_ledger(fixture, namespace, page_text, overview)
    ledger_names = {"page": "the page", "overview": f"namespace_overview({namespace})"}

    first_user = prompts.first_user_message(
        case_id, page_text, tl.render_namespace_list(fixture), overview
    )
    (out_dir / "system.txt").write_text(prompts.SYSTEM, encoding="utf-8")
    (out_dir / "prompt.txt").write_text(
        prompts.SYSTEM + "\n\n=== FIRST USER MESSAGE ===\n\n" + first_user, encoding="utf-8"
    )
    log.info("case %s anchored on namespace %s", case_id, namespace)

    messages: list[MessageParam] = [_text_user(first_user)]
    transcript: list[dict[str, object]] = []
    rejections: list[dict[str, object]] = []
    usage = _Usage()
    accepted: va.Submission | None = None
    accepted_result: va.ValidationResult | None = None
    last_rejected: va.Submission | None = None
    last_violations: tuple[str, ...] = ()
    tool_calls = submit_attempts = nudges = rejects = turns = 0
    citation_seq = 0
    forced = False

    while turns < MAX_TURNS and accepted is None:
        turn = converse_fn(messages, prompts.SYSTEM, tl.TOOL_SPECS)
        turns += 1
        usage.add(turn)
        (out_dir / "metrics.json").write_text(
            json.dumps(
                _metrics(
                    case_id,
                    run_id,
                    usage,
                    started,
                    turns,
                    tool_calls,
                    submit_attempts,
                    last_rejected,
                    None,
                    False,
                ),
                indent=2,
            ),
            encoding="utf-8",
        )
        messages.append(
            cast(MessageParam, {"role": "assistant", "content": turn.assistant_content})
        )
        turn_record: dict[str, object] = {
            "turn": turns,
            "text": turn.text,
            "calls": [{"name": c.name, "arguments": c.arguments} for c in turn.tool_calls],
        }

        results: list[ContentBlockParam] = []
        for call in turn.tool_calls:
            if call.name == tl.SUBMIT_ANSWER:
                submit_attempts += 1
                try:
                    submission = va.parse_submission(call.arguments)
                except va.SubmissionError as exc:
                    rejects += 1
                    results.append(
                        _tool_result(call.id, f"Your submission was malformed: {exc}", True)
                    )
                    continue
                preview = render.render_report(
                    case_id, submission, va.ValidationResult((), "probable", (), ()), ledger_names
                )
                result = va.validate(
                    submission, ledger, fixture, case_id, namespace, page_text, preview
                )
                if result.accepted:
                    accepted, accepted_result = submission, result
                    results.append(_tool_result(call.id, "Accepted. The incident is closed."))
                    break
                rejects += 1
                last_rejected, last_violations = submission, result.violations
                rejections.append({"turn": turns, "violations": list(result.violations)})
                results.append(
                    _tool_result(call.id, prompts.rejection_message(result.violations), True)
                )
                continue
            tool_calls += 1
            citation_seq += 1
            citation_id = f"c{citation_seq}"
            output = tl.dispatch(fixture, call.name, call.arguments)
            ledger.record(
                tl.ToolInvocation(
                    citation_id,
                    call.name,
                    dict(call.arguments),
                    output,
                    tl.namespaces_touched(call.name, call.arguments),
                )
            )
            ledger_names[citation_id] = render.source_label(call.name, call.arguments)
            results.append(_tool_result(call.id, output, tl.is_error(output), citation_id))

        turn_record["rejections"] = [r for r in rejections if r["turn"] == turns]
        turn_record["results"] = [
            {"tool_use_id": r.get("tool_use_id"), "chars": len(str(r.get("content", "")))}
            for r in cast(list[ToolResultBlockParam], results)
        ]
        transcript.append(turn_record)

        if accepted is not None:
            break
        if results:
            messages.append(_user(results))
        elif turn.stop_reason == "end_turn":
            nudges += 1
            if nudges > MAX_NUDGES:
                break
            messages.append(_text_user(prompts.NUDGE))

        if forced:
            break
        over_budget = usage.cost >= MAX_CASE_USD
        if over_budget or tool_calls >= MAX_TOOL_CALLS or rejects >= MAX_SUBMIT_REJECTS:
            log.info(
                "case %s forcing a finish: cost=$%.4f tool_calls=%d rejects=%d",
                case_id,
                usage.cost,
                tool_calls,
                rejects,
            )
            messages.append(_text_user(prompts.FORCED_SUBMIT))
            forced = True

    salvaged = accepted is None
    if salvaged:
        log.info("case %s salvaged after %d turn(s); %d rejection(s)", case_id, turns, rejects)
        accepted = render.salvage_submission(
            case_id, page_text, namespace, last_rejected, last_violations
        )
        accepted_result = va.ValidationResult((), "inconclusive", (), ())

    result = accepted_result or va.ValidationResult((), "inconclusive", (), ())
    (out_dir / "metrics.json").write_text(
        json.dumps(
            _metrics(
                case_id,
                run_id,
                usage,
                started,
                turns,
                tool_calls,
                submit_attempts,
                accepted,
                result,
                salvaged,
            ),
            indent=2,
        ),
        encoding="utf-8",
    )
    with (out_dir / "transcript.jsonl").open("w", encoding="utf-8") as handle:
        for record in transcript:
            handle.write(json.dumps(record) + "\n")
    report = render.render_report(case_id, accepted, result, ledger_names)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    (out_dir / "answer.json").write_text(
        render.answer_json(case_id, accepted, accepted.verdict_proposed), encoding="utf-8"
    )
    log.info(
        "case %s done: verdict=%s turns=%d tools=%d %d in / %d out tokens, $%.4f",
        case_id,
        accepted.verdict_proposed,
        turns,
        tool_calls,
        usage.input_total,
        usage.output,
        usage.cost,
    )
    return out_dir / "answer.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Backward-walk incident diagnoser")
    parser.add_argument("--fixture", type=Path, required=True, help="fixture directory")
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument("--case-id", default=None, help="defaults to the fixture dir name")
    args = parser.parse_args()
    load_env_file()
    diagnose(args.fixture, args.case_id or args.fixture.name, args.out)


if __name__ == "__main__":
    main()
