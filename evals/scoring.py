"""Deterministic scorer for the frozen case set.

This module IS the normative mechanism->class rubric: evals/scoring.md explains
the same tables in prose, but where they could ever disagree, this file wins.
Frozen together with the case set at the pre-final-run tag; any earlier
tightening is CHANGELOG'd (design req 5).

Scoring is mechanical end to end: no LLM judge, no partial credit. A case is
root-cause-correct iff the normalized failing resource matches gold exactly AND
the freeform mechanism classifies to exactly the gold fault class.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import cast


class FaultClass(StrEnum):
    """The deterministic fault atoms (decision doc, design req 3).

    ANTI-LEAK INVARIANT: these values, this class name, and this module must
    never be referenced anywhere under baseline/ or solution/ — the enum in an
    agent prompt turns open diagnosis into multiple choice. Enforced by
    tests/test_scoring.py::test_fault_enum_never_leaks_into_agent_dirs.
    """

    SERVICE_SELECTOR_MISMATCH = "service-selector-mismatch"
    RBAC_DENIAL = "rbac-denial"
    RESOURCE_QUOTA_EXCEEDED = "resource-quota-exceeded"
    UNBOUND_PVC = "unbound-pvc"
    BAD_CONFIG_REF = "bad-config-ref"
    INIT_CONTAINER_FAILURE = "init-container-failure"
    IMAGE_PULL_BACKOFF = "image-pull-backoff"
    POD_UNSCHEDULABLE = "pod-unschedulable"
    READINESS_PROBE_FAILING = "readiness-probe-failing"
    OOM_KILLED = "oom-killed"
    APP_CRASHLOOP = "app-crashloop"
    ROLLOUT_STUCK = "rollout-stuck"


VERDICTS: tuple[str, ...] = ("confirmed", "probable", "inconclusive")
TIERS: tuple[str, ...] = ("T1", "T2", "T3")

# Signature = groups of case-insensitive regex alternatives. A mechanism text
# matches a class iff EVERY group has at least one matching alternative.
_SIGNATURES: dict[FaultClass, tuple[tuple[str, ...], ...]] = {
    FaultClass.SERVICE_SELECTOR_MISMATCH: (
        (r"\bselector\b",),
        (r"\blabels?\b", r"\bmatch", r"\bendpoints?\b"),
    ),
    FaultClass.RBAC_DENIAL: (
        (
            r"\brbac\b",
            r"\brole\b",
            r"\brole ?-?binding",
            r"\bclusterrole\b",
            r"service ?account",
            r"\bpermission",
        ),
        (
            r"\bforbidden\b",
            r"\bden(y|ied|ies)\b",
            r"\bcannot\b",
            r"\bunauthorized\b",
            r"\bmissing\b",
            r"not (allowed|bound|granted)",
            r"\b403\b",
        ),
    ),
    FaultClass.RESOURCE_QUOTA_EXCEEDED: (
        (r"\bquota\b",),
        (
            r"\bexceed",
            r"\bexhaust",
            r"\bfull\b",
            r"\bden(y|ied|ies)\b",
            r"\bblock",
            r"\breject",
            r"\binsufficient\b",
            r"\blimit",
        ),
    ),
    FaultClass.UNBOUND_PVC: (
        (r"\bpvc\b", r"persistent ?volume ?claim", r"volume claim"),
        (
            r"\bunbound\b",
            r"\bpending\b",
            r"storage ?class",
            r"\bprovision",
            r"no (matching )?persistent ?volume",
        ),
    ),
    # Group 2 deliberately excludes bare "missing": a correct app-crashloop
    # answer often says "the env var is missing" while mentioning ConfigMap
    # sourcing, and would be falsely dominated. Reference-failure phrasings
    # only (kubelet events say `configmap "x" not found`).
    FaultClass.BAD_CONFIG_REF: (
        (r"config ?map\b", r"\bsecret\b"),
        (
            r"not found",
            r"does not exist",
            r"\bnonexistent\b",
            r"createcontainerconfigerror",
            r"invalid key",
            r"\bno such\b",
            r"\brenamed\b",
            r"couldn'?t find",
            r"no longer exists",
        ),
    ),
    FaultClass.INIT_CONTAINER_FAILURE: (
        (r"init[- ]?container",),
        (r"\bfail", r"\berror", r"\bexit", r"\bcrash", r"\bstuck\b", r"\bblock"),
    ),
    FaultClass.IMAGE_PULL_BACKOFF: (
        (r"\bimage\b", r"\btag\b", r"\bregistry\b"),
        (
            r"\bpull",
            r"not found",
            r"does not exist",
            r"\binvalid\b",
            r"errimagepull",
            r"manifest unknown",
            r"\bunknown\b",
            r"\bbogus\b",
        ),
    ),
    FaultClass.POD_UNSCHEDULABLE: (
        (r"schedul", r"no nodes? available"),
        (
            r"\binsufficient\b",
            r"\brequests?\b",
            r"\ballocatable\b",
            r"\bcapacity\b",
            r"\bfit\b",
            r"\bresources\b",
        ),
    ),
    # Group 2 holds failure evidence only (no bare probe/endpoints/503): the
    # natural selector-mismatch mechanism says "readiness probes pass, yet
    # Endpoints is empty" and must not co-match this class (roster red-team,
    # 2026-08-29).
    FaultClass.READINESS_PROBE_FAILING: (
        (r"readiness",),
        (r"\bfail", r"\bunready\b", r"not ready", r"connection refused", r"wrong port"),
    ),
    # No alternative may appear in both groups: a single token ("OOM", "137",
    # "crashloop") satisfying a class by itself makes any answer that merely
    # MENTIONS a decoy — even to rule it out — multi-match and score wrong
    # (roster red-team, 2026-08-29). Same reason \bcrash left crashloop g2.
    FaultClass.OOM_KILLED: (
        (r"\boom", r"out of memory", r"memory limit", r"\b137\b"),
        (r"\bkill", r"\bexceed", r"\blimit"),
    ),
    FaultClass.APP_CRASHLOOP: (
        (r"crash ?-?loop", r"\brestart", r"back ?-?off"),
        (r"\bexit", r"\bfatal\b", r"\bpanic", r"\babort", r"at startup"),
    ),
    FaultClass.ROLLOUT_STUCK: (
        (r"\brollout\b", r"rolling update", r"replica ?set\b", r"progress ?deadline"),
        (
            r"\bstuck\b",
            r"\bpaused\b",
            r"\bdeadlock",
            r"not progress",
            r"progressdeadlineexceeded",
            r"max ?unavailable",
            r"\bblock",
            r"\bhalt",
        ),
    ),
}

# Root-over-symptom dominance: when a specific mechanism co-matches with the
# symptom bucket it produces, the specific class wins and the bucket is
# dropped. Only listed pairs are collapsed; any other multi-match stays
# ambiguous and scores wrong (disclosed in evals/scoring.md).
_DOMINATED_BY: dict[FaultClass, frozenset[FaultClass]] = {
    FaultClass.APP_CRASHLOOP: frozenset(
        {
            FaultClass.OOM_KILLED,
            FaultClass.BAD_CONFIG_REF,
            FaultClass.INIT_CONTAINER_FAILURE,
            FaultClass.IMAGE_PULL_BACKOFF,
        }
    ),
    FaultClass.POD_UNSCHEDULABLE: frozenset(
        {FaultClass.RESOURCE_QUOTA_EXCEEDED, FaultClass.UNBOUND_PVC}
    ),
    FaultClass.ROLLOUT_STUCK: frozenset(
        {
            FaultClass.RESOURCE_QUOTA_EXCEEDED,
            FaultClass.POD_UNSCHEDULABLE,
            FaultClass.IMAGE_PULL_BACKOFF,
            FaultClass.READINESS_PROBE_FAILING,
            FaultClass.OOM_KILLED,
            FaultClass.APP_CRASHLOOP,
            FaultClass.BAD_CONFIG_REF,
            FaultClass.INIT_CONTAINER_FAILURE,
            FaultClass.UNBOUND_PVC,
        }
    ),
}

# kubectl-style kind aliases -> canonical kind (all compared lowercase).
_KIND_ALIASES: dict[str, str] = {
    "deploy": "deployment",
    "deployments": "deployment",
    "po": "pod",
    "pods": "pod",
    "svc": "service",
    "services": "service",
    "sts": "statefulset",
    "statefulsets": "statefulset",
    "ds": "daemonset",
    "daemonsets": "daemonset",
    "rs": "replicaset",
    "replicasets": "replicaset",
    "cm": "configmap",
    "configmaps": "configmap",
    "secrets": "secret",
    "ing": "ingress",
    "ingresses": "ingress",
    "netpol": "networkpolicy",
    "networkpolicies": "networkpolicy",
    "pvc": "persistentvolumeclaim",
    "persistentvolumeclaims": "persistentvolumeclaim",
    "quota": "resourcequota",
    "resourcequotas": "resourcequota",
    "hpa": "horizontalpodautoscaler",
    "horizontalpodautoscalers": "horizontalpodautoscaler",
    "pdb": "poddisruptionbudget",
    "poddisruptionbudgets": "poddisruptionbudget",
    "sa": "serviceaccount",
    "serviceaccounts": "serviceaccount",
    "roles": "role",
    "rolebindings": "rolebinding",
    "clusterroles": "clusterrole",
    "clusterrolebindings": "clusterrolebinding",
    "jobs": "job",
    "cronjobs": "cronjob",
    "cj": "cronjob",
    "ep": "endpoints",
    "no": "node",
    "nodes": "node",
    "ns": "namespace",
    "namespaces": "namespace",
}

# Banned: a number ATTACHED to confidence language ("85% confident",
# "confidence: 0.9"). Adjacency is required both ways — a count or exit code
# that merely shares a sentence with qualitative confidence wording ("after 3
# restarts we are certain") is legitimate and must not trip this.
_CONF_WORDS = r"(?:confiden(?:ce|t)|certain(?:ty)?|probability|likelihood|sure)"
_NUMERIC_CONFIDENCE = re.compile(
    rf"{_CONF_WORDS}\b[^.\n\d]{{0,15}}\d|\d[\d.]*\s?%?\s*{_CONF_WORDS}\b",
    re.IGNORECASE,
)

# The report contract's four required sections (decision doc, "Report
# contract"), matched as markdown headings case-insensitively.
_REPORT_SECTIONS: tuple[str, ...] = (
    "root cause",
    "evidence chain",
    "investigation ledger",
    "verification recipe",
)


@dataclass(frozen=True)
class FailingResource:
    """The resource whose spec must change, normalized for comparison."""

    kind: str
    namespace: str
    name: str

    def normalized(self) -> tuple[str, str, str]:
        kind = self.kind.strip().lower()
        kind = _KIND_ALIASES.get(kind, kind)
        return (kind, self.namespace.strip().lower(), self.name.strip().lower())


@dataclass(frozen=True)
class Answer:
    """The scored extract every arm must emit per case (schema in scoring.md)."""

    case_id: str
    failing_resource: FailingResource
    mechanism: str
    verdict: str
    missing_evidence: str = ""


@dataclass(frozen=True)
class Gold:
    """Ground truth for one case; lives in evals/scenarios/<id>/gold.json."""

    case_id: str
    tier: str
    failing_resource: FailingResource
    fault_class: FaultClass
    mechanism_summary: str
    decisive_evidence: str
    remediation_summary: str


@dataclass(frozen=True)
class CaseScore:
    """One scored case; the per-case rows checked into evals/results/."""

    case_id: str
    tier: str
    verdict: str
    resource_correct: bool
    matched_classes: frozenset[FaultClass]
    class_correct: bool

    @property
    def root_cause_correct(self) -> bool:
        return self.resource_correct and self.class_correct


@dataclass
class RateCell:
    """Count pair backing every reported rate."""

    cases: int = 0
    correct: int = 0

    def rate(self) -> float | None:
        return None if self.cases == 0 else self.correct / self.cases


@dataclass
class Summary:
    """Aggregate over one invocation of the frozen set for one arm."""

    overall: RateCell = field(default_factory=RateCell)
    by_tier: dict[str, RateCell] = field(default_factory=dict[str, RateCell])
    by_verdict: dict[str, RateCell] = field(default_factory=dict[str, RateCell])
    confirmed_wrong: int = 0


def classify_mechanism(mechanism: str) -> frozenset[FaultClass]:
    """All classes whose signature fully matches, after dominance collapse."""
    text = mechanism.lower()
    matched = {
        cls
        for cls, groups in _SIGNATURES.items()
        if all(any(re.search(pat, text) for pat in group) for group in groups)
    }
    return frozenset(cls for cls in matched if not (_DOMINATED_BY.get(cls, frozenset()) & matched))


def _require_str(obj: dict[str, object], key: str, errors: list[str]) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"missing or empty string field: {key}")
        return ""
    return value


def _parse_resource(obj: object, errors: list[str]) -> FailingResource:
    if not isinstance(obj, dict):
        errors.append("failing_resource must be an object with kind/namespace/name")
        return FailingResource(kind="", namespace="", name="")
    res = cast(dict[str, object], obj)
    return FailingResource(
        kind=_require_str(res, "kind", errors),
        namespace=_require_str(res, "namespace", errors),
        name=_require_str(res, "name", errors),
    )


def parse_answer(raw: str) -> Answer:
    """Parse and validate one answer JSON document. Raises ValueError; never defaults."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"answer is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("answer JSON must be an object")
    obj = cast(dict[str, object], data)
    errors: list[str] = []
    case_id = _require_str(obj, "case_id", errors)
    resource = _parse_resource(obj.get("failing_resource"), errors)
    mechanism = _require_str(obj, "mechanism", errors)
    verdict = _require_str(obj, "verdict", errors)
    if verdict and verdict not in VERDICTS:
        errors.append(f"verdict must be one of {VERDICTS}, got {verdict!r}")
    missing = obj.get("missing_evidence", "")
    if not isinstance(missing, str):
        errors.append("missing_evidence must be a string")
        missing = ""
    if verdict == "inconclusive" and not missing.strip():
        errors.append("verdict 'inconclusive' requires a non-empty missing_evidence")
    if errors:
        raise ValueError("invalid answer: " + "; ".join(errors))
    return Answer(
        case_id=case_id,
        failing_resource=resource,
        mechanism=mechanism,
        verdict=verdict,
        missing_evidence=missing,
    )


def load_gold(path: Path) -> Gold:
    """Load and validate one gold.json. Raises ValueError; never defaults."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path}: gold JSON must be an object")
    obj = cast(dict[str, object], data)
    errors: list[str] = []
    case_id = _require_str(obj, "case_id", errors)
    tier = _require_str(obj, "tier", errors)
    if tier and tier not in TIERS:
        errors.append(f"tier must be one of {TIERS}, got {tier!r}")
    resource = _parse_resource(obj.get("failing_resource"), errors)
    raw_class = _require_str(obj, "fault_class", errors)
    fault_class = FaultClass.SERVICE_SELECTOR_MISMATCH
    if raw_class:
        try:
            fault_class = FaultClass(raw_class)
        except ValueError:
            errors.append(f"unknown fault_class: {raw_class!r}")
    mechanism = _require_str(obj, "mechanism_summary", errors)
    evidence = _require_str(obj, "decisive_evidence", errors)
    remediation = _require_str(obj, "remediation_summary", errors)
    if errors:
        raise ValueError(f"{path}: invalid gold: " + "; ".join(errors))
    return Gold(
        case_id=case_id,
        tier=tier,
        failing_resource=resource,
        fault_class=fault_class,
        mechanism_summary=mechanism,
        decisive_evidence=evidence,
        remediation_summary=remediation,
    )


def score_case(answer: Answer, gold: Gold) -> CaseScore:
    """Mechanically score one case: exact resource match AND unique class match."""
    if answer.case_id != gold.case_id:
        raise ValueError(f"case_id mismatch: answer {answer.case_id!r} vs gold {gold.case_id!r}")
    matched = classify_mechanism(answer.mechanism)
    return CaseScore(
        case_id=gold.case_id,
        tier=gold.tier,
        verdict=answer.verdict,
        resource_correct=(
            answer.failing_resource.normalized() == gold.failing_resource.normalized()
        ),
        matched_classes=matched,
        class_correct=(matched == frozenset({gold.fault_class})),
    )


def aggregate(scores: list[CaseScore]) -> Summary:
    """Aggregate case rows into the reported table (overall / per tier / calibration)."""
    summary = Summary()
    for score in scores:
        cells = (
            summary.overall,
            summary.by_tier.setdefault(score.tier, RateCell()),
            summary.by_verdict.setdefault(score.verdict, RateCell()),
        )
        for cell in cells:
            cell.cases += 1
            cell.correct += int(score.root_cause_correct)
        if score.verdict == "confirmed" and not score.root_cause_correct:
            summary.confirmed_wrong += 1
    return summary


def numeric_confidence_present(report_text: str) -> bool:
    """True on banned numeric self-confidence ('85% confident', 'confidence: 0.9')."""
    return _NUMERIC_CONFIDENCE.search(report_text) is not None


def report_contract_violations(report_text: str) -> list[str]:
    """Mechanical report-contract checks; empty list means compliant."""
    lowered = report_text.lower()
    headings = {
        match.group(1).strip() for match in re.finditer(r"^#{1,6}\s+(.+)$", lowered, re.MULTILINE)
    }
    violations = [
        f"missing required section heading: {section}"
        for section in _REPORT_SECTIONS
        if not any(section in heading for heading in headings)
    ]
    if numeric_confidence_present(report_text):
        violations.append("numeric self-confidence present (banned; use the discrete verdict)")
    return violations
