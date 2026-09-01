"""The one-prompt baseline: curated dump in, report + answer JSON out.

One LLM call per case, no tools, no retries, no follow-ups — this arm is the
frozen floor the solution is measured against. It never sees gold data or the
scenario ledger; its only inputs are the fixture's page.txt and cluster state,
curated per baseline/curate.py.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from baseline.curate import CuratedDump, curate
from common.llm import CompleteFn, complete, load_env_file
from common.report_contract import OUTPUT_CONTRACT, extract_answer
from common.runlog import get_logger, new_run_id


def build_prompt(case_id: str, dump: CuratedDump) -> str:
    """The single baseline prompt: page, pasted dump, shared output contract."""
    parts: list[str] = [
        "You are an on-call Kubernetes engineer. You just got paged:",
        "",
        dump.page.strip(),
        "",
        "You grabbed the following output from the cluster. Diagnose the root",
        "cause of the paged symptom from this output alone.",
        "",
        "$ kubectl get all -A",
        dump.get_all.rstrip(),
    ]
    for section in dump.sections:
        parts += ["", f"$ {section.title}", section.body.rstrip()]
    parts += ["", f'The case id for your answer is "{case_id}".', "", OUTPUT_CONTRACT]
    return "\n".join(parts)


def diagnose(
    fixture: Path,
    case_id: str,
    out_dir: Path,
    complete_fn: CompleteFn = complete,
) -> Path:
    """Run the baseline on one fixture; write artifacts; return the answer path."""
    run_id = new_run_id()
    log = get_logger(run_id, name="baseline")
    out_dir.mkdir(parents=True, exist_ok=True)

    dump = curate(fixture)
    prompt = build_prompt(case_id, dump)
    (out_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    log.info(
        "curated %d sections, %d chars (~%d tokens) for case %s",
        len(dump.sections),
        dump.total_chars,
        dump.approx_tokens,
        case_id,
    )

    started = time.monotonic()
    result = complete_fn(prompt)
    duration_s = time.monotonic() - started

    # report + metrics land BEFORE answer extraction: a response with no valid
    # answer block still hit the API, and its measured spend must survive.
    (out_dir / "report.md").write_text(result.text, encoding="utf-8")
    metrics = {
        "case_id": case_id,
        "arm": "baseline",
        "run_id": run_id,
        "model": result.model,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cost_usd": result.cost_usd,
        "duration_s": round(duration_s, 2),
        "prompt_chars": dump.total_chars,
        "prompt_sections": len(dump.sections),
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (out_dir / "answer.json").write_text(extract_answer(result.text), encoding="utf-8")
    log.info(
        "case %s done: %d in / %d out tokens, $%.4f, %.1fs",
        case_id,
        result.input_tokens,
        result.output_tokens,
        result.cost_usd or 0.0,
        duration_s,
    )
    return out_dir / "answer.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="One-prompt baseline diagnoser")
    parser.add_argument("--fixture", type=Path, required=True, help="fixture directory")
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument("--case-id", default=None, help="defaults to the fixture dir name")
    args = parser.parse_args()
    load_env_file()
    diagnose(args.fixture, args.case_id or args.fixture.name, args.out)


if __name__ == "__main__":
    main()
