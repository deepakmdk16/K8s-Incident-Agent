"""Verify-before-assert: the gate a submission has to survive to end the run.

The agent cannot finish by writing prose. It finishes by calling `submit_answer`,
and everything it claims there is checked mechanically against the snapshot
before the answer is allowed to exist:

* every quote is re-checked by RE-EXECUTING the tool call it cites, so a claim
  can only be made about output the run actually produced;
* the remediation target has to be the same object as the failing resource, which
  is the question — "whose spec does a human edit" — that the one-shot arm gets
  wrong even when it identifies the fault correctly;
* the 2-3 verification commands the report promises are RUN, and marked
  PRESENT/ABSENT from their real results;
* the verdict word is then *earned* from those outcomes rather than chosen. A
  submission claiming more than its evidence supports is rejected with the unmet
  condition named, and the agent resubmits.

Because every tool is a pure file read, all of that costs no API spend and no
extra turn. This module knows nothing about any taxonomy of faults — every rule
here is about the shape and provenance of an argument, never its subject.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from solution import fixture as fx
from solution import tools as tl

# Name the field by its API path or quote the key — never paraphrase it. Three
# accepted shapes: a dotted path (".spec.selector"), an indexed field
# ("env[DB_HOST]", "subjects[0].name"), or a backtick-quoted key.
FIELD_PATH = re.compile(
    r"\.[a-zA-Z][A-Za-z0-9_]*(\[[^\]]*\])?(\.[a-zA-Z][A-Za-z0-9_]*)*"
    r"|[a-zA-Z][A-Za-z0-9_]*\[[^\]]+\]"
    r"|`[^`]+`"
)

# Plain-English failure verbs. Fault-shaped nouns are deliberately absent: a list
# of them would be a hint list in a different spelling, which the anti-leak rule
# forbids however it is worded. This requirement is additive — it never bans
# domain vocabulary, it only insists the sentence say what actually failed.
FAILURE_VERBS: tuple[str, ...] = (
    "fail",
    "error",
    "exit",
    "crash",
    "block",
    "stuck",
    "reject",
    "cannot",
    "can't",
    "unable",
    "deny",
    "denied",
    "refuse",
    "abort",
    "halt",
    "kill",
    "invalid",
    "timeout",
    "timed out",
    "not found",
    "does not exist",
    "no such",
)

# Stricter than the published check, which false-positives on the "sure" tail of
# pressure/ensure/measure. Being stricter costs nothing and guarantees the
# recorded contract-violation count stays zero.
_CONFIDENCE_WORD = re.compile(
    r"(confiden|certain|probabilit|likelihood|sure|pressure|ensure|measure)", re.IGNORECASE
)

_KIND_WORD_FORMS: dict[str, tuple[str, ...]] = {
    "persistentvolumeclaim": ("pvc",),
    "serviceaccount": ("sa",),
    "poddisruptionbudget": ("pdb",),
    "horizontalpodautoscaler": ("hpa",),
    "resourcequota": ("quota",),
}

MAX_MECHANISM_SENTENCES = 3


class SubmissionError(ValueError):
    """The submit_answer arguments are the wrong shape to even validate."""


@dataclass(frozen=True)
class ResourceRef:
    kind: str
    namespace: str
    name: str

    def normalized(self) -> tuple[str, str, str]:
        return (
            self.kind.strip().lower(),
            self.namespace.strip().lower(),
            self.name.strip().lower(),
        )


@dataclass(frozen=True)
class EvidenceItem:
    role: str
    claim: str
    tool_call_id: str
    quote: str


@dataclass(frozen=True)
class RuledOut:
    alternative: str
    entity_names: tuple[str, ...]
    ruling_claim: str
    tool_call_id: str
    quote: str


@dataclass(frozen=True)
class VerificationCheck:
    command: str
    tool: str
    arguments: dict[str, object]
    must_contain: str


@dataclass(frozen=True)
class Submission:
    failing_resource: ResourceRef
    remediation: ResourceRef
    field_path: str
    current_value: str
    required_value: str
    root_cause_statement: str
    mechanism: str
    evidence: tuple[EvidenceItem, ...]
    ruled_out: tuple[RuledOut, ...]
    verification: tuple[VerificationCheck, ...]
    verdict_proposed: str
    missing_evidence: str


@dataclass(frozen=True)
class ValidationResult:
    violations: tuple[str, ...]
    verdict_allowed: str
    verification_results: tuple[tuple[str, bool], ...]
    verified_quotes: tuple[str, ...]

    @property
    def accepted(self) -> bool:
        return not self.violations


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _normalize(text: str) -> str:
    return " ".join(text.split())


# --- parsing ------------------------------------------------------------------


def _dict_at(node: Mapping[str, object], key: str, errors: list[str]) -> dict[str, object]:
    value = node.get(key)
    if not isinstance(value, dict):
        errors.append(f"{key} must be an object")
        return {}
    return cast(dict[str, object], value)


def _str_at(node: Mapping[str, object], key: str, errors: list[str], where: str = "") -> str:
    value = node.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{where}{key} must be a non-empty string")
        return ""
    return value.strip()


def _list_at(node: Mapping[str, object], key: str, errors: list[str]) -> list[dict[str, object]]:
    value = node.get(key)
    if not isinstance(value, list):
        errors.append(f"{key} must be an array")
        return []
    out: list[dict[str, object]] = []
    for index, entry in enumerate(cast(list[object], value)):
        if not isinstance(entry, dict):
            errors.append(f"{key}[{index}] must be an object")
            continue
        out.append(cast(dict[str, object], entry))
    return out


def _ref(node: Mapping[str, object], errors: list[str], where: str) -> ResourceRef:
    return ResourceRef(
        kind=_str_at(node, "kind", errors, where),
        namespace=_str_at(node, "namespace", errors, where),
        name=_str_at(node, "name", errors, where),
    )


def parse_submission(arguments: Mapping[str, object]) -> Submission:
    """Shape-check the submit_answer arguments. Raises listing every problem at once."""
    errors: list[str] = []
    failing = _ref(_dict_at(arguments, "failing_resource", errors), errors, "failing_resource.")
    remediation_node = _dict_at(arguments, "remediation", errors)
    remediation = _ref(remediation_node, errors, "remediation.")

    evidence = tuple(
        EvidenceItem(
            role=_str_at(node, "role", errors, f"evidence[{i}]."),
            claim=_str_at(node, "claim", errors, f"evidence[{i}]."),
            tool_call_id=_str_at(node, "tool_call_id", errors, f"evidence[{i}]."),
            quote=_str_at(node, "quote", errors, f"evidence[{i}]."),
        )
        for i, node in enumerate(_list_at(arguments, "evidence", errors))
    )
    ruled_out: list[RuledOut] = []
    for i, node in enumerate(_list_at(arguments, "ruled_out", errors)):
        raw_names = node.get("entity_names")
        names = (
            tuple(
                n.strip() for n in cast(list[object], raw_names) if isinstance(n, str) and n.strip()
            )
            if isinstance(raw_names, list)
            else ()
        )
        ruled_out.append(
            RuledOut(
                alternative=_str_at(node, "alternative", errors, f"ruled_out[{i}]."),
                entity_names=names,
                ruling_claim=_str_at(node, "ruling_claim", errors, f"ruled_out[{i}]."),
                tool_call_id=_str_at(node, "tool_call_id", errors, f"ruled_out[{i}]."),
                quote=_str_at(node, "quote", errors, f"ruled_out[{i}]."),
            )
        )
    verification: list[VerificationCheck] = []
    for i, node in enumerate(_list_at(arguments, "verification", errors)):
        raw_args = node.get("arguments")
        verification.append(
            VerificationCheck(
                command=_str_at(node, "command", errors, f"verification[{i}]."),
                tool=_str_at(node, "tool", errors, f"verification[{i}]."),
                arguments=cast(dict[str, object], raw_args) if isinstance(raw_args, dict) else {},
                must_contain=_str_at(node, "must_contain", errors, f"verification[{i}]."),
            )
        )
    verdict = _str_at(arguments, "verdict", errors)
    missing = arguments.get("missing_evidence")
    # Every field is collected BEFORE the raise: a shape error reported one at a
    # time costs the model a whole turn per problem, and a field read after the
    # raise would silently default to "" instead of being reported at all.
    field_path = _str_at(remediation_node, "field_path", errors, "remediation.")
    current_value = _str_at(remediation_node, "current_value", errors, "remediation.")
    required_value = _str_at(remediation_node, "required_value", errors, "remediation.")
    root_cause = _str_at(arguments, "root_cause_statement", errors)
    mechanism = _str_at(arguments, "mechanism", errors)
    if errors:
        raise SubmissionError("; ".join(errors))
    return Submission(
        failing_resource=failing,
        remediation=remediation,
        field_path=field_path,
        current_value=current_value,
        required_value=required_value,
        root_cause_statement=root_cause,
        mechanism=mechanism,
        evidence=evidence,
        ruled_out=tuple(ruled_out),
        verification=tuple(verification),
        verdict_proposed=verdict,
        missing_evidence=missing.strip() if isinstance(missing, str) else "",
    )


# --- the seven checks ---------------------------------------------------------

_VERDICT_STRENGTH = {"inconclusive": 0, "probable": 1, "confirmed": 2}


def numeric_confidence_present(text: str) -> bool:
    """A digit near any confidence word. Stricter than the published contract check."""
    for match in _CONFIDENCE_WORD.finditer(text):
        window = text[max(0, match.start() - 15) : match.end() + 15]
        if any(character.isdigit() for character in window):
            return True
    return False


def _reexecute(invocation: tl.ToolInvocation, fixture: Path) -> str:
    """Fresh output for a citation. Synthetic entries replay their recorded text."""
    if invocation.name in tl.READ_TOOL_NAMES:
        return tl.dispatch(fixture, invocation.name, invocation.arguments)
    return invocation.output


def _kind_word_present(kind: str, mechanism: str) -> bool:
    squashed_kind = _squash(kind).rstrip("s")
    squashed_mechanism = _squash(mechanism)
    if squashed_kind and squashed_kind in squashed_mechanism:
        return True
    return any(form in mechanism.lower() for form in _KIND_WORD_FORMS.get(squashed_kind, ()))


def _sentence_count(text: str) -> int:
    """Sentences, counting only terminators followed by a space or the end.

    An API path is the point of V5c, so ".spec.selector" and "10.96.24.225"
    must not read as sentence breaks.
    """
    return len([part for part in re.split(r"[.!?]+(?=\s|$)", text) if part.strip()])


# The argument keys that identify the object a read is about. `namespace`,
# `contains` and `container` are deliberately absent: a name appearing there
# scopes or filters a read, it does not make the read about that object.
_OBJECT_ARGUMENT_KEYS: tuple[str, ...] = ("name", "pod", "involved_name")


def _arguments_name(arguments: Mapping[str, object], name: str) -> bool:
    lowered = name.lower()
    return any(
        isinstance(value := arguments.get(key), str) and lowered in value.lower()
        for key in _OBJECT_ARGUMENT_KEYS
    )


# A pod is the product of its controller: reading the pod (describe, logs) is
# reading the controller's behaviour, and pod names carry the controller's name.
# That relationship, and same-kind, are the only two the anchoring rule accepts.
_CONTROLLER_KINDS: frozenset[str] = frozenset(
    {"cronjobs", "daemonsets", "deployments", "jobs", "replicasets", "statefulsets"}
)


def _same_kind(arguments: Mapping[str, object], reference: ResourceRef) -> bool:
    """A call with a kind argument is about the failing object only if the kinds agree,
    or the call reads a pod and the failing object is the controller that produced it.

    Without this, asking a served tool about a DIFFERENT kind that happens to
    carry the failing object's name ("describe configmap/<name>") counted as a
    read of the object (review, 2026-09-05). The pod→controller allowance is what
    the frozen bundle's accepted submissions rely on (tests/test_frozen_replay.py).
    """
    kind = arguments.get("kind")
    if not isinstance(kind, str):
        return True
    try:
        called, failing = fx.normalize_kind(kind), fx.normalize_kind(reference.kind)
    except fx.FixtureError:
        return False
    return called == failing or (called == "pods" and failing in _CONTROLLER_KINDS)


def _about_failing_object(
    output: str, quote: str, arguments: Mapping[str, object], reference: ResourceRef
) -> bool:
    """Does this verified citation actually show the failing object?

    Two ways: the quote names it, or the call's arguments name it and the call was
    about its kind. Either way the result has to be real cluster state — an error
    or an empty echo of the arguments shows nothing about any object, however
    literally it re-verifies.
    """
    if not tl.is_evidence(output):
        return False
    if reference.name.lower() in quote.lower():
        return True
    return _same_kind(arguments, reference) and _arguments_name(arguments, reference.name)


def _check_mechanism(submission: Submission, report_preview: str) -> list[str]:
    violations: list[str] = []
    mechanism = submission.mechanism
    if not 1 <= _sentence_count(mechanism) <= MAX_MECHANISM_SENTENCES:
        violations.append(
            f"V5a MECHANISM: use 1-{MAX_MECHANISM_SENTENCES} sentences; "
            f"yours has {_sentence_count(mechanism)}"
        )
    if submission.failing_resource.name.lower() not in mechanism.lower():
        violations.append(
            f"V5b MECHANISM: name the failing object '{submission.failing_resource.name}' in the "
            "mechanism sentence"
        )
    if not _kind_word_present(submission.failing_resource.kind, mechanism):
        violations.append(
            f"V5b MECHANISM: say what kind of object it is ('{submission.failing_resource.kind}')"
        )
    if not FIELD_PATH.search(mechanism):
        violations.append(
            "V5c MECHANISM: name the wrong field by its API path (e.g. '.spec.selector') or quote "
            "the key in backticks — a paraphrase such as 'selects pods with label' does not count"
        )
    if not any(verb in mechanism.lower() for verb in FAILURE_VERBS):
        violations.append(
            "V5d MECHANISM: say what FAILS, in failure words. Describing an absence of success "
            "('loops forever', 'never becomes ready', 'stalled') does not say what failed"
        )
    # V5e exists to stop the mechanism sentence dragging in a SECOND failing
    # thing, which reads as a second diagnosis. Two classes of name are exempt
    # because banning them makes a correct sentence unwritable:
    #   * the failing resource's own name — a Service and the Deployment behind
    #     it routinely share one, so a ruled-out sibling would ban the subject;
    #   * anything named in the remediation's current/required values — the
    #     value that SHOULD be there is part of the mechanism by definition,
    #     even when a ruled-out alternative was about that same object.
    own = submission.failing_resource.name.lower()
    exempt = {own} | {
        word
        for value in (submission.current_value, submission.required_value)
        for word in re.findall(r"[a-z0-9][a-z0-9._-]*", value.lower())
    }
    banned = {
        name.lower()
        for item in submission.ruled_out
        for name in item.entity_names
        if name.lower() not in exempt and name.lower() not in own
    }
    tokens = set(re.findall(r"[a-z0-9][a-z0-9._-]*", mechanism.lower()))
    for name in sorted(banned & tokens):
        violations.append(
            f"V5e MECHANISM: '{name}' belongs to an alternative you ruled out; the mechanism "
            "sentence must describe only the failing mechanism"
        )
    if numeric_confidence_present(report_preview):
        violations.append(
            "V5f REPORT: a number appears next to a word about confidence — the verdict is a word, "
            "never a number"
        )
    return violations


def validate(
    submission: Submission,
    ledger: tl.ToolLedger,
    fixture: Path,
    case_id: str,
    paged_namespace: str,
    page_text: str,
    report_preview: str,
) -> ValidationResult:
    """Run every check. An empty `violations` tuple is the only way the run ends."""
    del case_id  # echoed by the renderer; never taken from the model
    violations: list[str] = []
    verified_quotes: list[str] = []

    known = set(fx.namespaces(fixture))
    admissible = {paged_namespace, ""} | fx.namespaces_named_in(page_text, known)

    # V1 QUOTE (re-executed) and V2 ADMISSIBILITY, in citation order so that a
    # verified quote can itself bring a further namespace into the story.
    cited: list[tuple[str, str, str]] = [
        (f"evidence[{i}]", item.tool_call_id, item.quote)
        for i, item in enumerate(submission.evidence)
    ] + [
        (f"ruled_out[{i}]", item.tool_call_id, item.quote)
        for i, item in enumerate(submission.ruled_out)
    ]
    verified_by_label: dict[str, bool] = {}
    fresh_by_label: dict[str, str] = {}
    for label, call_id, quote in cited:
        invocation = ledger.get(call_id)
        if invocation is None:
            violations.append(
                f"V1 QUOTE: {label} cites tool_call_id '{call_id}', which is not a call you made. "
                f"Available ids: {', '.join(ledger.ids())}"
            )
            verified_by_label[label] = False
            continue
        fresh = _reexecute(invocation, fixture)
        if tl.is_not_served(fresh):
            # A "not served" result is a limit of the tools. It re-verifies
            # literally, and it may even name the object — which is exactly why
            # it must not count: it says nothing about the cluster
            # (docs/failure-modes.md 2026-09-05).
            violations.append(
                f"V1 QUOTE: {label} cites a {invocation.name} result that says the snapshot "
                "has no view of what was asked. That is a limit of the tools, not evidence "
                "about the cluster — cite cluster state, or drop the item"
            )
            verified_by_label[label] = False
            continue
        if _normalize(quote) not in _normalize(fresh):
            violations.append(
                f"V1 QUOTE: {label}'s quote is not present in the output of "
                f"{invocation.name}. Copy the text literally from what the tool returned"
            )
            verified_by_label[label] = False
            continue
        verified_by_label[label] = True
        fresh_by_label[label] = fresh
        verified_quotes.append(quote)
        # Admissibility gates what a conclusion RESTS ON, never what it
        # eliminates: ruling out a red herring necessarily means citing the red
        # herring, and penalising that would punish the exact discipline this
        # arm exists to enforce.
        if label.startswith("evidence") and not invocation.namespaces_touched & admissible:
            violations.append(
                f"V2 ADMISSIBILITY: {label} cites "
                f"{', '.join(sorted(invocation.namespaces_touched))}, which nothing has yet "
                "connected to the paged symptom. Cite the reference that links it first"
            )
            continue
        admissible |= fx.namespaces_named_in(quote, known)

    # V3 SPEC-OWNER
    if submission.remediation.normalized() != submission.failing_resource.normalized():
        violations.append(
            "V3 SPEC-OWNER: the remediation edits "
            f"{submission.remediation.kind}/{submission.remediation.name} but failing_resource is "
            f"{submission.failing_resource.kind}/{submission.failing_resource.name}. The failing "
            "resource is the object whose spec must change"
        )
    for field, value in (
        ("field_path", submission.field_path),
        ("current_value", submission.current_value),
        ("required_value", submission.required_value),
    ):
        if not value.strip():
            violations.append(f"V3 SPEC-OWNER: remediation.{field} must say what changes")

    # V4 EXISTS
    reference = submission.failing_resource
    try:
        present = fx.load_kind(fixture, reference.kind, reference.namespace)
        names = {
            str(cast(dict[str, object], item.get("metadata", {})).get("name", "")).lower()
            for item in present
        }
        if reference.name.lower() not in names:
            # The names present are listed only for a kind the agent could have
            # listed itself. For any other kind the list would be an oracle: the
            # snapshot holds the object, no tool serves it, and this message was
            # how the agent learned its name (docs/failure-modes.md 2026-09-05).
            if tl.serves_kind(reference.kind):
                listed = ", ".join(sorted(n for n in names if n)) or "none"
                detail = f"Present: {listed}"
            else:
                detail = (
                    f"No read tool serves {reference.kind}, so its names cannot be listed "
                    "here; name only an object you have read"
                )
            violations.append(
                f"V4 EXISTS: no {reference.kind} named '{reference.name}' in namespace "
                f"'{reference.namespace}'. {detail}"
            )
    except fx.FixtureError as exc:
        violations.append(f"V4 EXISTS: failing_resource does not resolve — {exc}")

    # V5 MECHANISM AUDIT
    violations.extend(_check_mechanism(submission, report_preview))

    # V6 VERIFICATION EXECUTED
    verification_results: list[tuple[str, bool]] = []
    check_outputs: list[str] = []
    if not 2 <= len(submission.verification) <= 3:
        violations.append("V6 VERIFICATION: give 2-3 checks a human could run")
    for check in submission.verification:
        if check.tool not in tl.READ_TOOL_NAMES:
            verification_results.append((check.command, False))
            check_outputs.append("")
            violations.append(
                f"V6 VERIFICATION: '{check.tool}' is not one of the tools you can run "
                f"({', '.join(sorted(tl.READ_TOOL_NAMES))})"
            )
            continue
        output = tl.dispatch(fixture, check.tool, check.arguments)
        # A check whose command the snapshot cannot serve has not been run; it
        # is ABSENT even if its error text happens to contain must_contain.
        present_now = not tl.is_not_served(output) and (
            _normalize(check.must_contain).lower() in _normalize(output).lower()
        )
        verification_results.append((check.command, present_now))
        check_outputs.append(output)

    # V7 VERDICT CRITERIA — mechanical, never numeric
    unmet: list[str] = []
    verified_evidence = [
        item
        for i, item in enumerate(submission.evidence)
        if verified_by_label.get(f"evidence[{i}]", False)
    ]
    if len(verified_evidence) < 3:
        unmet.append("at least 3 verified evidence items")
    if not any(item.role == "symptom" for item in verified_evidence):
        unmet.append("one verified item with role 'symptom'")
    defect_on_target = any(
        item.role == "defect"
        and (invocation := ledger.get(item.tool_call_id)) is not None
        and _about_failing_object(
            fresh_by_label[f"evidence[{i}]"], item.quote, invocation.arguments, reference
        )
        for i, item in enumerate(submission.evidence)
        if verified_by_label.get(f"evidence[{i}]", False)
    )
    if not defect_on_target:
        unmet.append(
            f"one verified item with role 'defect' whose citation is about {reference.name}"
        )
    if not any(
        verified_by_label.get(f"ruled_out[{i}]", False) for i in range(len(submission.ruled_out))
    ):
        unmet.append("one ruled-out alternative with a verified quote")
    present_checks = [command for command, ok in verification_results if ok]
    if len(verification_results) < 2 or len(present_checks) != len(verification_results):
        unmet.append("2-3 verification checks that are all PRESENT")
    elif not any(
        _about_failing_object(output, check.must_contain, check.arguments, reference)
        for check, (_, ok), output in zip(
            submission.verification, verification_results, check_outputs, strict=True
        )
        if ok
    ):
        unmet.append(f"one PRESENT verification check that names {reference.name}")

    verdict_allowed = "confirmed" if not violations and not unmet else "probable"
    if violations:
        verdict_allowed = "inconclusive"
    proposed = submission.verdict_proposed
    if proposed == "inconclusive" and not submission.missing_evidence:
        violations.append(
            "VERDICT: 'inconclusive' requires missing_evidence naming what would settle it"
        )
    elif _VERDICT_STRENGTH.get(proposed, 0) > _VERDICT_STRENGTH[verdict_allowed] and not violations:
        violations.append(
            f"VERDICT: '{proposed}' is not earned yet — still missing {'; '.join(unmet)}. "
            f"Either supply that, or submit '{verdict_allowed}'"
        )
    return ValidationResult(
        violations=tuple(violations),
        verdict_allowed=verdict_allowed,
        verification_results=tuple(verification_results),
        verified_quotes=tuple(verified_quotes),
    )
