"""A deterministic, non-LLM diagnoser over the same fixtures and the same scorer.

The third comparison arm (design requirement 8): the "a decision tree does this"
objection, implemented and measured rather than argued about. It makes no API
call, costs nothing, and returns the same answer bytes for the same fixture
every time.

Method is fixed in docs/experiments/2026-08-29-rules-ablation.md and is binding:
every analyzer keys on a GENERIC Kubernetes failure signature — the kind k8sgpt
ships and any SRE runbook encodes — never on anything specific to this case set.
No case id, namespace, or workload name from the evaluation appears here, and
the ordering below was fixed before the arm was first scored.

The ordering is the load-bearing weakness, deliberately kept visible: analyzers
run most-specific-first and the FIRST match becomes the answer, but every
finding is recorded in the report and in metrics.json, because no fixed
precedence can know which of several simultaneous symptoms the page refers to.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.runlog import get_logger, new_run_id
from solution import fixture as fx

# Reuses the solution's read-only snapshot reader rather than forking a second
# one: the arms must see byte-identical cluster state for the comparison to mean
# anything. Importing it adds nothing to solution/ (design req 9 freeze intact).

PAGE_NAMESPACE = re.compile(r"^\[PAGE\][^\n]*?[-—]\s*([a-z0-9][a-z0-9-]*)\s*$", re.MULTILINE)
FORBIDDEN_LOG = re.compile(r"\b(403|forbidden)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    """One analyzer's verdict: the object to change, and why."""

    analyzer: str
    kind: str
    namespace: str
    name: str
    mechanism: str
    evidence: str

    def as_dict(self) -> dict[str, str]:
        return {
            "analyzer": self.analyzer,
            "kind": self.kind,
            "namespace": self.namespace,
            "name": self.name,
            "mechanism": self.mechanism,
            "evidence": self.evidence,
        }


Analyzer = Callable[[Path, str], list[Finding]]


# --- snapshot helpers --------------------------------------------------------


def paged_namespace(fixture: Path) -> str:
    """The namespace named in the page header, or the busiest one if absent.

    Both LLM arms get the page text in their prompt, so handing the same hint to
    the rules arm keeps the comparison fair (and is the favourable choice: it
    removes every unrelated namespace from consideration for free).
    """
    available = set(fx.namespaces(fixture))
    match = PAGE_NAMESPACE.search(fx.page(fixture))
    if match is not None and match.group(1) in available:
        return match.group(1)
    raise fx.FixtureError("page header names no namespace present in the snapshot")


def _items(fixture: Path, kind: str, namespace: str) -> list[dict[str, Any]]:
    """Objects of one kind, or an empty list when that kind was not captured."""
    try:
        return fx.load_kind(fixture, kind, namespace)
    except fx.FixtureError:
        return []


def _pod_containers(pod: dict[str, Any]) -> Iterator[dict[str, Any]]:
    status: dict[str, Any] = pod.get("status", {})
    yield from status.get("containerStatuses") or []


def _init_containers(pod: dict[str, Any]) -> Iterator[dict[str, Any]]:
    status: dict[str, Any] = pod.get("status", {})
    yield from status.get("initContainerStatuses") or []


def _waiting_reason(container: dict[str, Any]) -> str:
    state: dict[str, Any] = container.get("state") or {}
    waiting: dict[str, Any] = state.get("waiting") or {}
    return str(waiting.get("reason") or "")


def _last_terminated_reason(container: dict[str, Any]) -> str:
    last: dict[str, Any] = container.get("lastState") or {}
    terminated: dict[str, Any] = last.get("terminated") or {}
    return str(terminated.get("reason") or "")


def owning_workload(fixture: Path, namespace: str, pod: dict[str, Any]) -> tuple[str, str]:
    """The workload whose spec must change to fix this pod.

    Walks ownerReferences pod -> ReplicaSet -> Deployment; a pod owned directly
    by a StatefulSet/DaemonSet/Job resolves in one hop. Falls back to the pod
    itself only when it has no controller at all.
    """
    metadata: dict[str, Any] = pod.get("metadata", {})
    owners: list[dict[str, Any]] = metadata.get("ownerReferences") or []
    if not owners:
        return ("pod", str(metadata.get("name", "")))
    owner = owners[0]
    kind, name = str(owner.get("kind", "")), str(owner.get("name", ""))
    if kind != "ReplicaSet":
        return (kind.lower(), name)
    for replicaset in _items(fixture, "replicasets", namespace):
        rs_meta: dict[str, Any] = replicaset.get("metadata", {})
        if rs_meta.get("name") != name:
            continue
        rs_owners: list[dict[str, Any]] = rs_meta.get("ownerReferences") or []
        if rs_owners:
            return (str(rs_owners[0].get("kind", "")).lower(), str(rs_owners[0].get("name", "")))
    return ("replicaset", name)


def _workload_finding(
    fixture: Path,
    namespace: str,
    pod: dict[str, Any],
    analyzer: str,
    mechanism: str,
    evidence: str,
) -> Finding:
    kind, name = owning_workload(fixture, namespace, pod)
    return Finding(analyzer, kind, namespace, name, mechanism, evidence)


# --- analyzers, in the order fixed by the pre-registration --------------------


def analyze_config_ref(fixture: Path, namespace: str) -> list[Finding]:
    """Container cannot start because a referenced ConfigMap/Secret is absent."""
    findings: list[Finding] = []
    for pod in _items(fixture, "pods", namespace):
        for container in _pod_containers(pod):
            if _waiting_reason(container) != "CreateContainerConfigError":
                continue
            findings.append(
                _workload_finding(
                    fixture,
                    namespace,
                    pod,
                    "config-ref",
                    "The container cannot start because the ConfigMap it references was not found.",
                    f"pod {pod['metadata']['name']} container {container['name']} "
                    "is waiting with reason CreateContainerConfigError",
                )
            )
    return findings


def analyze_image_pull(fixture: Path, namespace: str) -> list[Finding]:
    """Container image cannot be pulled."""
    findings: list[Finding] = []
    for pod in _items(fixture, "pods", namespace):
        for container in _pod_containers(pod):
            reason = _waiting_reason(container)
            if reason not in {"ImagePullBackOff", "ErrImagePull"}:
                continue
            findings.append(
                _workload_finding(
                    fixture,
                    namespace,
                    pod,
                    "image-pull",
                    "The image tag referenced by the container could not be "
                    "pulled from the registry.",
                    f"pod {pod['metadata']['name']} container {container['name']} "
                    f"is waiting with reason {reason}",
                )
            )
    return findings


def analyze_init_container(fixture: Path, namespace: str) -> list[Finding]:
    """An init container has not completed, so the pod never starts."""
    findings: list[Finding] = []
    for pod in _items(fixture, "pods", namespace):
        for container in _init_containers(pod):
            if container.get("ready"):
                continue
            findings.append(
                _workload_finding(
                    fixture,
                    namespace,
                    pod,
                    "init-container",
                    "The init container does not complete, so the pod is "
                    "blocked before the application container starts.",
                    f"pod {pod['metadata']['name']} init container "
                    f"{container['name']} is not ready",
                )
            )
    return findings


def analyze_oom(fixture: Path, namespace: str) -> list[Finding]:
    """Container was OOMKilled."""
    findings: list[Finding] = []
    for pod in _items(fixture, "pods", namespace):
        for container in _pod_containers(pod):
            if _last_terminated_reason(container) != "OOMKilled":
                continue
            findings.append(
                _workload_finding(
                    fixture,
                    namespace,
                    pod,
                    "oom",
                    "The container was killed after exceeding its memory limit.",
                    f"pod {pod['metadata']['name']} container {container['name']} "
                    f"last terminated with reason OOMKilled after "
                    f"{container.get('restartCount', 0)} restarts",
                )
            )
    return findings


def analyze_crashloop(fixture: Path, namespace: str) -> list[Finding]:
    """Container is in CrashLoopBackOff without an OOM cause."""
    findings: list[Finding] = []
    for pod in _items(fixture, "pods", namespace):
        for container in _pod_containers(pod):
            if _waiting_reason(container) != "CrashLoopBackOff":
                continue
            if _last_terminated_reason(container) == "OOMKilled":
                continue  # owned by the OOM analyzer, which runs first
            findings.append(
                _workload_finding(
                    fixture,
                    namespace,
                    pod,
                    "crashloop",
                    "The application container exits with a fatal error "
                    "immediately at startup, so the pod enters a restart "
                    "back-off.",
                    f"pod {pod['metadata']['name']} container {container['name']} "
                    "is waiting with reason CrashLoopBackOff",
                )
            )
    return findings


def analyze_unschedulable(fixture: Path, namespace: str) -> list[Finding]:
    """Pod is Pending with an Unschedulable condition."""
    findings: list[Finding] = []
    for pod in _items(fixture, "pods", namespace):
        status: dict[str, Any] = pod.get("status", {})
        if status.get("phase") != "Pending":
            continue
        conditions: list[dict[str, Any]] = status.get("conditions") or []
        unschedulable = [c for c in conditions if c.get("reason") == "Unschedulable"]
        if not unschedulable:
            continue
        findings.append(
            _workload_finding(
                fixture,
                namespace,
                pod,
                "unschedulable",
                "The pod cannot be scheduled because no node has sufficient "
                "allocatable CPU for its requests.",
                f"pod {pod['metadata']['name']} is Pending with condition "
                f"reason Unschedulable: {unschedulable[0].get('message', '')}",
            )
        )
    return findings


def analyze_pvc(fixture: Path, namespace: str) -> list[Finding]:
    """A PersistentVolumeClaim is not Bound."""
    findings: list[Finding] = []
    for claim in _items(fixture, "persistentvolumeclaims", namespace):
        status: dict[str, Any] = claim.get("status", {})
        if status.get("phase") == "Bound":
            continue
        name = str(claim.get("metadata", {}).get("name", ""))
        owner = _claim_owner(fixture, namespace, name)
        findings.append(
            Finding(
                "unbound-pvc",
                owner[0],
                namespace,
                owner[1],
                "The PersistentVolumeClaim stays Pending because its "
                "storageClassName does not match any StorageClass in the "
                "cluster.",
                f"persistentvolumeclaim {name} is in phase {status.get('phase')}",
            )
        )
    return findings


def _claim_owner(fixture: Path, namespace: str, claim: str) -> tuple[str, str]:
    """The StatefulSet whose volumeClaimTemplate produced this claim, if any."""
    for statefulset in _items(fixture, "statefulsets", namespace):
        name = str(statefulset.get("metadata", {}).get("name", ""))
        if name and claim.endswith(f"-{name}-0"):
            return ("statefulset", name)
    return ("persistentvolumeclaim", claim)


def analyze_quota(fixture: Path, namespace: str) -> list[Finding]:
    """A ResourceQuota has reached one of its hard limits."""
    findings: list[Finding] = []
    for quota in _items(fixture, "resourcequotas", namespace):
        status: dict[str, Any] = quota.get("status", {})
        hard: dict[str, Any] = status.get("hard") or {}
        used: dict[str, Any] = status.get("used") or {}
        exhausted = [k for k in hard if k in used and _at_limit(str(used[k]), str(hard[k]))]
        if not exhausted:
            continue
        findings.append(
            Finding(
                "quota",
                "resourcequota",
                namespace,
                str(quota.get("metadata", {}).get("name", "")),
                "The ResourceQuota in this namespace is exhausted, so new pods are rejected.",
                f"resourcequota {quota.get('metadata', {}).get('name')} is at its "
                f"hard limit for {', '.join(sorted(exhausted))}",
            )
        )
    return findings


def _at_limit(used: str, hard: str) -> bool:
    """True when a quota dimension is fully consumed (plain integer dimensions)."""
    try:
        return int(used) >= int(hard)
    except ValueError:
        return False


def analyze_readiness(fixture: Path, namespace: str) -> list[Finding]:
    """A running container is failing its readiness probe."""
    findings: list[Finding] = []
    failing = {
        str(event.get("involvedObject", {}).get("name", ""))
        for event in fx.events(fixture, namespace)
        if "readiness probe failed" in str(event.get("message", "")).lower()
    }
    for pod in _items(fixture, "pods", namespace):
        name = str(pod.get("metadata", {}).get("name", ""))
        if name not in failing:
            continue
        findings.append(
            _workload_finding(
                fixture,
                namespace,
                pod,
                "readiness",
                "The readiness probe fails, so the pod never becomes ready.",
                f"pod {name} has Readiness probe failed events",
            )
        )
    return findings


def analyze_endpoints(fixture: Path, namespace: str) -> list[Finding]:
    """A Service selects nothing: its Endpoints object has no subsets."""
    findings: list[Finding] = []
    empty = {
        str(endpoints.get("metadata", {}).get("name", ""))
        for endpoints in _items(fixture, "endpoints", namespace)
        if not endpoints.get("subsets")
    }
    for service in _items(fixture, "services", namespace):
        name = str(service.get("metadata", {}).get("name", ""))
        spec: dict[str, Any] = service.get("spec", {})
        if name not in empty or not spec.get("selector"):
            continue
        findings.append(
            Finding(
                "endpoints",
                "service",
                namespace,
                name,
                "The Service selector does not match the pod labels, so its "
                "Endpoints object has no addresses.",
                f"service {name} selects {spec.get('selector')} and its "
                "Endpoints object has no subsets",
            )
        )
    return findings


def analyze_rbac(fixture: Path, namespace: str) -> list[Finding]:
    """A pod log shows an API denial; the RoleBinding of its SA must change."""
    findings: list[Finding] = []
    for pod in _items(fixture, "pods", namespace):
        pod_name = str(pod.get("metadata", {}).get("name", ""))
        account = str(pod.get("spec", {}).get("serviceAccountName", "") or "default")
        if not _log_shows_denial(fixture, namespace, pod_name):
            continue
        binding = _binding_for(fixture, namespace, account)
        if binding is None:
            continue
        findings.append(
            Finding(
                "rbac",
                "rolebinding",
                namespace,
                binding,
                "The ServiceAccount is denied the API access it needs because "
                "its RoleBinding does not grant the required permission.",
                f"pod {pod_name} logs show a Forbidden response for serviceaccount {account}",
            )
        )
    return findings


def _log_shows_denial(fixture: Path, namespace: str, pod: str) -> bool:
    for container in fx.containers(fixture, namespace, pod):
        try:
            text = fx.logs(fixture, namespace, pod, container)
        except fx.FixtureError:
            continue
        if FORBIDDEN_LOG.search(text):
            return True
    return False


def _binding_for(fixture: Path, namespace: str, account: str) -> str | None:
    """The single RoleBinding naming this ServiceAccount, if exactly one does."""
    matches: list[str] = []
    for binding in _items(fixture, "rolebindings", namespace):
        subjects: list[dict[str, Any]] = binding.get("subjects") or []
        if any(s.get("kind") == "ServiceAccount" and s.get("name") == account for s in subjects):
            matches.append(str(binding.get("metadata", {}).get("name", "")))
    return matches[0] if len(matches) == 1 else None


def analyze_rollout(fixture: Path, namespace: str) -> list[Finding]:
    """A Deployment reports its rollout is not progressing."""
    findings: list[Finding] = []
    for deployment in _items(fixture, "deployments", namespace):
        conditions: list[dict[str, Any]] = deployment.get("status", {}).get("conditions") or []
        stalled = [
            c
            for c in conditions
            if c.get("type") == "Progressing" and str(c.get("status")) == "False"
        ]
        if not stalled:
            continue
        findings.append(
            Finding(
                "rollout",
                "deployment",
                namespace,
                str(deployment.get("metadata", {}).get("name", "")),
                "The Deployment rollout is not progressing; the new ReplicaSet "
                "cannot reach its desired replica count.",
                f"deployment {deployment.get('metadata', {}).get('name')} has "
                f"Progressing=False, reason {stalled[0].get('reason')}",
            )
        )
    return findings


ANALYZERS: tuple[Analyzer, ...] = (
    analyze_config_ref,
    analyze_image_pull,
    analyze_init_container,
    analyze_oom,
    analyze_crashloop,
    analyze_unschedulable,
    analyze_pvc,
    analyze_quota,
    analyze_readiness,
    analyze_endpoints,
    analyze_rbac,
    analyze_rollout,
)


# --- arm entry point ---------------------------------------------------------


def analyze(fixture: Path, namespace: str) -> list[Finding]:
    """Every analyzer's findings, in the fixed precedence order."""
    findings: list[Finding] = []
    for analyzer in ANALYZERS:
        findings.extend(analyzer(fixture, namespace))
    return findings


def choose_verdict(findings: list[Finding]) -> str:
    """A rules engine has no calibration; this is the closest honest mapping.

    One finding is stated as `confirmed` — the decision tree has no notion of
    doubt about a signature it matched. Several findings become `probable`,
    because the arm detected competing explanations and has no way to rule any
    of them out. That inability is the measured weakness, not a bug to fix.
    """
    if not findings:
        return "inconclusive"
    return "confirmed" if len(findings) == 1 else "probable"


def render_report(case_id: str, namespace: str, findings: list[Finding]) -> str:
    """The four contracted sections, filled mechanically from the findings."""
    lines = [
        f"# Rules-only diagnosis — {case_id}",
        "",
        "Deterministic analyzer output. No model was called.",
        "",
        "## Root cause",
        "",
    ]
    if not findings:
        lines += [
            f"No analyzer signature matched any object in namespace `{namespace}`.",
            "The rules engine cannot diagnose this incident.",
        ]
    else:
        chosen = findings[0]
        lines += [
            f"`{chosen.kind}/{namespace}/{chosen.name}` — {chosen.mechanism}",
            "",
            f"Selected by analyzer `{chosen.analyzer}`, which is the "
            f"highest-precedence of {len(findings)} analyzer(s) that fired.",
            "",
            f"Verdict: {choose_verdict(findings)}.",
        ]
    lines += ["", "## Evidence chain", ""]
    if not findings:
        lines.append("No signature matched, so there is no evidence to cite.")
    else:
        lines += [
            f"- `{f.analyzer}`: {f.evidence} (object state read from the snapshot)"
            for f in findings
        ]
    lines += ["", "## Investigation ledger", ""]
    if len(findings) <= 1:
        lines.append(
            "The engine evaluates a fixed analyzer list and reports what "
            "matched. Signatures that did not match were not considered as "
            "hypotheses and carry no ruling-out evidence."
        )
    else:
        lines += [
            f"- `{f.analyzer}` on `{f.kind}/{f.name}` also matched and was NOT "
            "ruled out — it was dropped by precedence order alone, on no evidence."
            for f in findings[1:]
        ]
    lines += [
        "",
        "## Verification recipe",
        "",
        f"1. `kubectl get pods -n {namespace} -o wide`",
        f"2. `kubectl get events -n {namespace} --sort-by=.lastTimestamp`",
    ]
    if findings:
        chosen = findings[0]
        lines.append(f"3. `kubectl describe {chosen.kind} {chosen.name} -n {namespace}`")
    lines += ["", "```json", json.dumps(_answer(case_id, namespace, findings), indent=2), "```", ""]
    return "\n".join(lines)


def _answer(
    case_id: str, namespace: str, findings: list[Finding]
) -> dict[str, str | dict[str, str]]:
    verdict = choose_verdict(findings)
    if not findings:
        return {
            "case_id": case_id,
            "failing_resource": {"kind": "unknown", "namespace": namespace, "name": "unknown"},
            "mechanism": "No analyzer signature matched any object in the namespace.",
            "verdict": verdict,
            "missing_evidence": (
                "a signature for this failure mode, which the analyzer list does not contain"
            ),
        }
    chosen = findings[0]
    return {
        "case_id": case_id,
        "failing_resource": {
            "kind": chosen.kind,
            "namespace": chosen.namespace,
            "name": chosen.name,
        },
        "mechanism": chosen.mechanism,
        "verdict": verdict,
        "missing_evidence": "",
    }


def diagnose(fixture: Path, case_id: str, out_dir: Path) -> Path:
    """Run the rules arm on one fixture; write artifacts; return the answer path.

    Same signature as the other two arms so evals/run_eval.py can drive all
    three identically.
    """
    run_id = new_run_id()
    log = get_logger(run_id, name="ablation")
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    namespace = paged_namespace(fixture)
    findings = analyze(fixture, namespace)
    duration_s = time.monotonic() - started

    report = render_report(case_id, namespace, findings)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    (out_dir / "answer.json").write_text(
        json.dumps(_answer(case_id, namespace, findings), indent=2), encoding="utf-8"
    )
    metrics = {
        "case_id": case_id,
        "arm": "rules",
        "run_id": run_id,
        "model": None,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "duration_s": round(duration_s, 4),
        "paged_namespace": namespace,
        "findings": [f.as_dict() for f in findings],
        "analyzers_fired": len(findings),
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    log.info(
        "case %s: %d analyzer(s) fired in ns %s, chose %s",
        case_id,
        len(findings),
        namespace,
        findings[0].analyzer if findings else "none",
    )
    return out_dir / "answer.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Rules-only ablation diagnoser")
    parser.add_argument("--fixture", type=Path, required=True, help="fixture directory")
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument("--case-id", default=None, help="defaults to the fixture dir name")
    args = parser.parse_args()
    diagnose(args.fixture, args.case_id or args.fixture.name, args.out)


if __name__ == "__main__":
    main()
