"""Deterministic rendering of one ACCEPTED submission into the final artifacts.

The model supplies every word of content through `submit_answer`; this module
only guarantees the shape. That is why the four required report sections cannot
be missing and the answer JSON cannot disagree with the report — both are
rendered from the same accepted submission, once, after it passed the gate.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from solution.validate import (
    EvidenceItem,
    ResourceRef,
    RuledOut,
    Submission,
    ValidationResult,
    VerificationCheck,
)

# A fenced block inside a quoted log line would otherwise be able to impersonate
# the machine-read answer block, which is defined as the LAST fenced json block.
# Neutralising the fence marker keeps the quote readable and the answer findable.
_FENCE = "```"
_FENCE_SAFE = "'''"

_PAGE_RESOURCE = re.compile(r"\b([A-Z][A-Za-z]+)\s+([a-z0-9][a-z0-9-]*)/([a-z0-9][a-z0-9.-]*)")

_CRITERION = {
    "confirmed": (
        "Every quote below was re-checked against the tool output it cites, a defect observation "
        "names the failing object, an alternative was ruled out with evidence, and every "
        "verification command was re-run and found present."
    ),
    "probable": (
        "Every quote below was re-checked against the tool output it cites, but the evidence is "
        "indirect: at least one condition for a confirmed verdict was not met."
    ),
    "inconclusive": (
        "The investigation did not close. What would settle it is named under Root cause."
    ),
}


def _safe(text: str) -> str:
    return text.replace(_FENCE, _FENCE_SAFE)


def source_label(tool_name: str, arguments: object) -> str:
    """How a citation's origin is printed in the report: the call, not a file path."""
    return f"{tool_name}({json.dumps(arguments, sort_keys=True)})"


def _evidence_lines(items: Sequence[EvidenceItem], ledger_names: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        source = ledger_names.get(item.tool_call_id, item.tool_call_id)
        lines.append(f"{index}. [{item.role}] {_safe(item.claim)}")
        lines.append(f"   source: {source} — verified")
        lines += [f"   > {_safe(line)}" for line in item.quote.splitlines() or [""]]
    return lines


def _ruled_out_lines(items: Sequence[RuledOut], ledger_names: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for item in items:
        source = ledger_names.get(item.tool_call_id, item.tool_call_id)
        lines.append(f"- {_safe(item.alternative)} — ruled out: {_safe(item.ruling_claim)}")
        lines.append(f"  source: {source} — verified")
        lines += [f"  > {_safe(line)}" for line in item.quote.splitlines() or [""]]
    if not lines:
        lines.append("- No alternative survived long enough to be recorded.")
    return lines


def _verification_lines(
    checks: Sequence[VerificationCheck], results: Sequence[tuple[str, bool]]
) -> list[str]:
    lines: list[str] = []
    for index, check in enumerate(checks, start=1):
        present = next((ok for command, ok in results if command == check.command), False)
        lines.append(
            f"{index}. `{_safe(check.command)}` — expect to see: {_safe(check.must_contain)}  "
            f"[{'PRESENT' if present else 'ABSENT'}]"
        )
    if not lines:
        lines.append("1. (no verification recipe was produced)")
    lines.append(
        "(Each command was re-executed against the captured cluster state; PRESENT/ABSENT is "
        "the measured result, not a prediction.)"
    )
    return lines


def answer_json(case_id: str, submission: Submission, verdict: str) -> str:
    """The five-field answer document. `case_id` comes from the harness, never the model."""
    return json.dumps(
        {
            "case_id": case_id,
            "failing_resource": {
                "kind": submission.failing_resource.kind,
                "namespace": submission.failing_resource.namespace,
                "name": submission.failing_resource.name,
            },
            "mechanism": submission.mechanism,
            "verdict": verdict,
            "missing_evidence": submission.missing_evidence,
        },
        indent=2,
    )


def render_report(
    case_id: str,
    submission: Submission,
    result: ValidationResult,
    ledger_names: dict[str, str],
) -> str:
    """The four contract sections, then the answer JSON as the LAST fenced block."""
    verdict = submission.verdict_proposed
    resource = submission.failing_resource
    lines = [
        "## Root cause",
        "",
        _safe(submission.root_cause_statement),
        "",
        f"Remediation: edit {resource.kind} {resource.namespace}/{resource.name}, field "
        f"`{_safe(submission.field_path)}`: `{_safe(submission.current_value)}` -> "
        f"`{_safe(submission.required_value)}`.",
        "",
        "## Evidence chain",
        "",
        *_evidence_lines(submission.evidence, ledger_names),
        "",
        "## Investigation ledger",
        "",
        *_ruled_out_lines(submission.ruled_out, ledger_names),
        "",
        "## Verification recipe",
        "",
        *_verification_lines(submission.verification, result.verification_results),
        "",
        f"Verdict: {verdict}. {_CRITERION.get(verdict, '')}",
        "",
        "```json",
        answer_json(case_id, submission, verdict),
        "```",
        "",
    ]
    return "\n".join(lines)


def salvage_submission(
    case_id: str,
    page_text: str,
    paged_namespace: str,
    last_rejected: Submission | None,
    violations: Sequence[str],
) -> Submission:
    """A schema-valid inconclusive answer for a run that could not close.

    The row is lost either way; what this prevents is losing it as an unparseable
    answer, which reads as a broken harness rather than an honest non-answer and
    scores exactly the same.
    """
    resource = _salvage_resource(page_text, paged_namespace, last_rejected)
    unmet = "; ".join(violations) or "the investigation did not reach a verifiable conclusion"
    mechanism = (
        f"The investigation of {resource.kind} {resource.namespace}/{resource.name} did not "
        f"close: the evidence gathered could not be verified against the snapshot."
    )
    return Submission(
        failing_resource=resource,
        remediation=resource,
        field_path="(not established)",
        current_value="(not established)",
        required_value="(not established)",
        root_cause_statement=(
            f"Not established for case {case_id}. The investigation stopped before a claim could "
            f"be verified. Outstanding: {unmet}"
        ),
        mechanism=mechanism,
        evidence=(),
        ruled_out=(),
        verification=(),
        verdict_proposed="inconclusive",
        missing_evidence=unmet,
    )


def _salvage_resource(
    page_text: str, paged_namespace: str, last_rejected: Submission | None
) -> ResourceRef:
    if last_rejected is not None:
        reference = last_rejected.failing_resource
        if all((reference.kind, reference.namespace, reference.name)):
            return reference
    match = _PAGE_RESOURCE.search(page_text)
    if match is not None:
        return ResourceRef(
            kind=match.group(1).lower(), namespace=match.group(2), name=match.group(3)
        )
    return ResourceRef(kind="namespace", namespace=paged_namespace, name=paged_namespace)
