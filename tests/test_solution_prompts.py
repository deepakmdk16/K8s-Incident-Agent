"""The prompt is the anti-leak surface: nothing here may hint at a fault taxonomy."""

from __future__ import annotations

from common.report_contract import OUTPUT_CONTRACT
from solution import prompts, tools

# Spelling variants live HERE, not under solution/, because naming them in the
# arm would itself be the leak. The repo tripwire only matches exact slugs; this
# is the semantic half of the rule: no CANDIDATE FAULT TYPE in any spelling.
#
# Two things are deliberately NOT on this list, because banning them would be a
# misreading of the rule rather than an enforcement of it:
#   * Kubernetes API kind names ("resourcequotas", "networkpolicies", ...). The
#     get_object description lists the kinds it can serve, which is exactly what
#     `kubectl api-resources` prints. Naming a queryable kind is capability, not
#     a hint about what went wrong.
#   * Ordinary English that happens to appear in the frozen shared output
#     contract — "forbidden" occurs there in "numeric self-confidence ... is
#     forbidden anywhere in the report". That text is embedded verbatim by BOTH
#     arms as a fairness invariant and is not ours to reword.
FAULT_TYPE_PHRASES = (
    "crashloop",
    "crash loop",
    "backoff",
    "oomkill",
    "oom kill",
    "out of memory",
    "imagepull",
    "image pull",
    "unschedulable",
    "insufficient cpu",
    "insufficient memory",
    "quota exceeded",
    "exceeded quota",
    "rbac denial",
    "permission denied",
    "selector mismatch",
    "selector drift",
    "selector typo",
    "readiness probe fail",
    "liveness probe fail",
    "init container fail",
    "unbound",
    "storageclass typo",
    "bad rollout",
    # v2 (2026-09-04): phrases that name the admission fault, not the kind —
    # "webhook" alone is a queryable kind's stem and stays allowed.
    "failed calling webhook",
    "admission webhook",
    "admission-webhook",
    "webhook admission",
    "webhook-admission",
    "admission block",
    "webhook block",
    "missing env",
    "dangling reference",
    "fault class",
    "fault type",
    "failure mode",
    "root cause class",
    "common causes",
    "usually means",
    "typically caused",
    "look for one of",
)


def test_system_prompt_embeds_the_shared_output_contract_verbatim() -> None:
    """A declared fairness invariant: both arms are asked for the same deliverable."""
    assert OUTPUT_CONTRACT in prompts.SYSTEM


def test_prompt_names_no_candidate_failure_types() -> None:
    lowered = prompts.SYSTEM.lower()
    named = [phrase for phrase in FAULT_TYPE_PHRASES if phrase in lowered]
    assert named == [], f"the system prompt hints at candidate failures: {named}"


def test_tool_descriptions_name_no_candidate_failure_types() -> None:
    """Tool schemas are prompt surface too — a fault-shaped description is multiple choice."""
    surface = " ".join(f"{spec.name} {spec.description}" for spec in tools.TOOL_SPECS).lower()
    named = [phrase for phrase in FAULT_TYPE_PHRASES if phrase in surface]
    assert named == [], f"a tool description hints at candidate failures: {named}"


def test_first_user_message_echoes_the_case_id() -> None:
    message = prompts.first_user_message(
        "t9-example", "[PAGE] SEV1 Thing — demo", "demo", "ns demo"
    )
    assert "t9-example" in message
    assert "[PAGE] SEV1 Thing" in message


def test_rejection_message_lists_every_violation() -> None:
    message = prompts.rejection_message(("V1 QUOTE: nope", "V3 SPEC-OWNER: nope"))
    assert "V1 QUOTE: nope" in message
    assert "V3 SPEC-OWNER: nope" in message
