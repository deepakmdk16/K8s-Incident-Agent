"""Additive scorer for the cases under evals/scenarios-v2/ and evals/scenarios-heldout/.

The frozen rubric (evals/scoring.py) is a measurement instrument tied to the
12-case set at tag case-set-freeze, and it does not change. Cases in the
additive roots need things it lacks: a class for admission-webhook faults, a
class for Pod Security admission refusals, a spelling rule for custom kinds
the alias table does not know, and a rule for naming a failing resource that
has no namespace. This module is that instrument's superset. It re-keys the
frozen signature, dominance and alias tables BY VALUE — never retypes them —
so every phrasing the frozen scorer classifies scores identically here
(tests/test_scoring_v2.py pins the parity), and adds exactly two classes and
two conventions on top.

Frozen-root cases are still scored by evals/scoring.py. evals/run_eval.py picks
the scorer by scenario root and records which one scored a bundle.

Anti-leak: like the frozen enum, nothing here may be referenced under baseline/
or solution/ (tests/test_scoring_v2.py extends the tripwire to this module).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast

from evals import scoring as v1

# The frozen module keeps its tables private because nothing was meant to
# extend them; this module is that extension, and it reads them here — once,
# by name — rather than copying a hundred lines of regex that could then drift.
# The tables are frozen with the case set, so the only thing that can ever
# change on this side is v2's own additions.
_FROZEN_SIGNATURES = v1._SIGNATURES  # pyright: ignore[reportPrivateUsage]
_FROZEN_DOMINATED_BY = v1._DOMINATED_BY  # pyright: ignore[reportPrivateUsage]
_FROZEN_KIND_ALIASES = v1._KIND_ALIASES  # pyright: ignore[reportPrivateUsage]
_require_str = v1._require_str  # pyright: ignore[reportPrivateUsage]


class FaultClass(StrEnum):
    """The frozen 12 atoms, values verbatim, plus the v2 additions."""

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
    # v2 (2026-09-04): an admission webhook the API server cannot call, with
    # failurePolicy Fail, refuses the object create it intercepts. The failing
    # resource is the cluster-scoped webhook configuration.
    WEBHOOK_ADMISSION_BLOCK = "webhook-admission-block"
    # v2 (2026-11-07, held-out set): Pod Security admission refuses a pod for
    # the enforce level its namespace's labels set. The failing resource is the
    # Namespace (cluster-scoped).
    POD_SECURITY_VIOLATION = "pod-security-violation"


def _lift(cls: v1.FaultClass) -> FaultClass:
    return FaultClass(cls.value)


# Group 1 is the bare stem on purpose: the kind name the arms will write —
# "ValidatingWebhookConfiguration" — has no word boundary before "webhook", and
# no other class's vocabulary contains the stem. Group 2 holds failure words
# only, the same discipline as the frozen table (no alternative appears in
# both groups), and covers every generic failure verb the solution arm's own
# mechanism audit accepts, so a sentence that passes that audit cannot
# zero-match here on the verb alone. Red-teamed 2026-09-04 (40-sentence corpus,
# docs/experiments/2026-09-04-webhook-outage.md) against rbac-denial's
# "denied/cannot" group, the quota class's "reject/block" group, rollout-stuck
# and pod-unschedulable (both co-match naturally and are dominated below).
#
# Disclosed collisions, in the spirit of the frozen table's comments:
#   - group 2 is broad ("error", "fail", "cannot"), so a webhook MENTIONED
#     inside any failing sentence co-matches this class. A decoy webhook belongs
#     in ruled_out, never in the mechanism sentence — the same rule the frozen
#     scorer already imposes on every other decoy;
#   - "namespace selector" prose co-matches service-selector-mismatch
#     (`\bselector\b` + `\bmatch`); the API-path spelling (.namespaceSelector)
#     does not. Not a dominance pair: neither is the other's symptom;
#   - "image"/"tag" + "not found" co-matches image-pull-backoff, which is why
#     the authored release changes an env value rather than an image.
_WEBHOOK_SIGNATURE: tuple[tuple[str, ...], ...] = (
    (r"webhook",),
    (
        r"failed calling",
        r"\bfail",
        r"\breject",
        r"\bden(y|ied|ies)\b",
        r"\bblock",
        r"not found",
        r"does not exist",
        r"\bno such\b",
        r"\bmissing\b",
        r"\bunavailable\b",
        r"no endpoints",
        r"\brefus",
        r"\btimeout",
        r"timed out",
        r"\bunreachable\b",
        r"\bcannot\b",
        r"can'?t\b",
        r"\bunable\b",
        r"\berror",
    ),
)

# Group 1 is the admission plugin's own name in every spelling an arm writes:
# "PodSecurity" in the API server's message ('violates PodSecurity
# "restricted:latest"', the text the yard fixture's events carry),
# "pod-security.kubernetes.io" in the label key, "Pod Security admission",
# "Pod Security Standards", and the abbreviation "PSS" — all under one stem
# plus the abbreviation; no other class's vocabulary contains either, and no
# frozen phrasing does. Group 2 holds failure words only, the same discipline
# as the frozen table. "forbidden" is shared with rbac-denial's second group,
# but that class also needs an rbac noun in the sentence, which a Pod Security
# sentence has no reason to carry. A refused pod create leaves the ReplicaSet
# stuck and the rollout blocked, so the rollout bucket is dominated, and a pod
# refused "before scheduling" co-matches the scheduling bucket, likewise
# dominated — the two pairs the webhook class already added, for the same
# reason (docs/experiments/2026-11-07-heldout-set.md).
#
# Amended 2026-11-08, before any LLM-arm run (review of PR #9): group 1 also
# had `security standards?` and group 2 had `\benforc`, neither specific to
# this plugin, so a webhook that "enforces the company's security standards"
# co-matched this class and scored class-wrong. Both dropped;
# tests/test_scoring_v2.py pins that sentence to the webhook class alone.
_POD_SECURITY_SIGNATURE: tuple[tuple[str, ...], ...] = (
    (r"pod[ -]?security", r"\bpss\b"),
    (
        r"violat",
        r"\bforbidden\b",
        r"\breject",
        r"\bden(y|ied|ies)\b",
        r"\brefus",
        r"\bblock",
        r"not allowed",
    ),
)

_SIGNATURES: dict[FaultClass, tuple[tuple[str, ...], ...]] = {
    _lift(cls): groups for cls, groups in _FROZEN_SIGNATURES.items()
} | {
    FaultClass.WEBHOOK_ADMISSION_BLOCK: _WEBHOOK_SIGNATURE,
    FaultClass.POD_SECURITY_VIOLATION: _POD_SECURITY_SIGNATURE,
}

_DOMINATED_BY: dict[FaultClass, frozenset[FaultClass]] = {
    _lift(cls): frozenset(_lift(d) for d in dominators)
    for cls, dominators in _FROZEN_DOMINATED_BY.items()
}
# A refused pod create leaves the ReplicaSet stuck and the rollout blocked;
# that is the symptom bucket, and the webhook is its root. Likewise a pod that
# is refused "before scheduling" for a "request" the webhook never answered
# co-matches the scheduling bucket, whose root the webhook also is. Both pairs
# are score-monotone: they only ever turn an already-wrong two-class match into
# the webhook class, and no frozen text contains the stem "webhook".
_DOMINATED_BY[FaultClass.ROLLOUT_STUCK] |= {
    FaultClass.WEBHOOK_ADMISSION_BLOCK,
    FaultClass.POD_SECURITY_VIOLATION,
}
_DOMINATED_BY[FaultClass.POD_UNSCHEDULABLE] |= {
    FaultClass.WEBHOOK_ADMISSION_BLOCK,
    FaultClass.POD_SECURITY_VIOLATION,
}

# --- cluster-scoped failing resources -----------------------------------------

# Canonical kinds that have no namespace. For a failing resource of one of
# these kinds the answer's namespace field is not part of the identity being
# scored: kind + name decide. A namespaced kind gets no such leniency — its
# namespace is part of the identity being scored.
#
# Amended 2026-11-08, before any LLM-arm run (review of PR #9): until then
# only a fixed set of "no namespace" spellings ("", "-", "cluster-scoped",
# "none", …) normalized to empty, so an arm that named the Namespace yard-ops
# and wrote the paged namespace — yard-ops — in the namespace field scored
# resource-wrong on the one pod-security case: a trap set by the rubric's
# vocabulary, not a wrong answer (CLAUDE.md invariant).
CLUSTER_SCOPED_KINDS: frozenset[str] = frozenset(
    {
        "clusterrole",
        "clusterrolebinding",
        "mutatingwebhookconfiguration",
        "namespace",
        "node",
        "persistentvolume",
        "storageclass",
        "validatingwebhookconfiguration",
    }
)
_KIND_ALIASES: dict[str, str] = _FROZEN_KIND_ALIASES | {
    "mutatingwebhookconfigurations": "mutatingwebhookconfiguration",
    "mwc": "mutatingwebhookconfiguration",
    "persistentvolumes": "persistentvolume",
    "pv": "persistentvolume",
    "sc": "storageclass",
    "storageclasses": "storageclass",
    "validatingwebhookconfigurations": "validatingwebhookconfiguration",
    "vwc": "validatingwebhookconfiguration",
}
# Kinds the table maps TO: known canonical spellings, which the custom-kind
# rule in canonical_kind must never touch (`endpoints` keeps its trailing s).
_CANONICAL_KINDS: frozenset[str] = frozenset(_KIND_ALIASES.values())


def signature_table() -> dict[FaultClass, tuple[tuple[str, ...], ...]]:
    """The v2 signatures, read-only, for tests that audit agent-visible text against them."""
    return dict(_SIGNATURES)


def kind_alias_table() -> dict[str, str]:
    """The v2 kind aliases, read-only, for tests that pin the custom-kind rule around them."""
    return dict(_KIND_ALIASES)


def canonical_kind(kind: str) -> str:
    """One spelling per kind. The alias table decides for every kind it knows, as
    alias or as canonical value. A kind it does not know is a custom kind, and the
    spellings `kubectl` prints for one collapse by rule — lowercase, the `.group`
    qualifier dropped (everything from the first dot), a trailing plural `s`
    dropped — so `RouteProfile`, `routeprofiles` and
    `routeprofiles.fleet.northbound.io` name one object (2026-11-08, replacing
    the per-kind aliases). An `-es` plural (`ingressclasses`) is not collapsed; a
    case that needs one adds its alias to the table.
    """
    lowered = kind.strip().lower()
    if lowered in _KIND_ALIASES:
        return _KIND_ALIASES[lowered]
    if lowered in _CANONICAL_KINDS:
        return lowered
    return lowered.split(".", 1)[0].removesuffix("s")


def normalized_resource(resource: v1.FailingResource) -> tuple[str, str, str]:
    """(kind, namespace, name) for comparison; a cluster-scoped kind ignores its namespace field."""
    kind = canonical_kind(resource.kind)
    namespace = "" if kind in CLUSTER_SCOPED_KINDS else resource.namespace.strip().lower()
    return (kind, namespace, resource.name.strip().lower())


def _parse_resource(obj: object, errors: list[str]) -> v1.FailingResource:
    """Like the frozen parser, except a cluster-scoped kind may leave namespace empty."""
    if not isinstance(obj, dict):
        errors.append("failing_resource must be an object with kind/namespace/name")
        return v1.FailingResource(kind="", namespace="", name="")
    res = cast(dict[str, object], obj)
    kind = _require_str(res, "kind", errors)
    name = _require_str(res, "name", errors)
    raw_namespace = res.get("namespace", "")
    if not isinstance(raw_namespace, str):
        errors.append("failing_resource.namespace must be a string")
        raw_namespace = ""
    if not raw_namespace.strip() and canonical_kind(kind) not in CLUSTER_SCOPED_KINDS:
        errors.append("missing or empty string field: namespace")
    return v1.FailingResource(kind=kind, namespace=raw_namespace, name=name)


# --- gold, answers, scores ------------------------------------------------------


@dataclass(frozen=True)
class Gold:
    """Ground truth for one v2 case; lives in evals/scenarios-v2/<id>/gold.json."""

    case_id: str
    tier: str
    failing_resource: v1.FailingResource
    fault_class: FaultClass
    mechanism_summary: str
    decisive_evidence: str
    remediation_summary: str


@dataclass(frozen=True)
class CaseScore:
    """One scored v2 case; same row shape as the frozen scorer's."""

    case_id: str
    tier: str
    verdict: str
    resource_correct: bool
    matched_classes: frozenset[FaultClass]
    class_correct: bool

    @property
    def root_cause_correct(self) -> bool:
        return self.resource_correct and self.class_correct


def classify_mechanism(mechanism: str) -> frozenset[FaultClass]:
    """All classes whose signature fully matches, after dominance collapse.

    The same algorithm as the frozen scorer's, over the superset tables; the
    parity test runs both over the frozen phrasings and requires equal results.
    """
    text = mechanism.lower()
    matched = {
        cls
        for cls, groups in _SIGNATURES.items()
        if all(any(re.search(pat, text) for pat in group) for group in groups)
    }
    return frozenset(cls for cls in matched if not (_DOMINATED_BY.get(cls, frozenset()) & matched))


def parse_answer(raw: str) -> v1.Answer:
    """The frozen answer validation, with the cluster-scoped namespace convention."""
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
    if verdict and verdict not in v1.VERDICTS:
        errors.append(f"verdict must be one of {v1.VERDICTS}, got {verdict!r}")
    missing = obj.get("missing_evidence", "")
    if not isinstance(missing, str):
        errors.append("missing_evidence must be a string")
        missing = ""
    if verdict == "inconclusive" and not missing.strip():
        errors.append("verdict 'inconclusive' requires a non-empty missing_evidence")
    if errors:
        raise ValueError("invalid answer: " + "; ".join(errors))
    return v1.Answer(
        case_id=case_id,
        failing_resource=resource,
        mechanism=mechanism,
        verdict=verdict,
        missing_evidence=missing,
    )


def load_gold(path: Path) -> Gold:
    """Load and validate one v2 gold.json. Raises ValueError; never defaults."""
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
    if tier and tier not in v1.TIERS:
        errors.append(f"tier must be one of {v1.TIERS}, got {tier!r}")
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


def score_case(answer: v1.Answer, gold: Gold) -> CaseScore:
    """Mechanically score one case: exact resource match AND unique class match."""
    if answer.case_id != gold.case_id:
        raise ValueError(f"case_id mismatch: answer {answer.case_id!r} vs gold {gold.case_id!r}")
    matched = classify_mechanism(answer.mechanism)
    answered = normalized_resource(answer.failing_resource)
    return CaseScore(
        case_id=gold.case_id,
        tier=gold.tier,
        verdict=answer.verdict,
        resource_correct=(answered == normalized_resource(gold.failing_resource)),
        matched_classes=matched,
        class_correct=(matched == frozenset({gold.fault_class})),
    )
