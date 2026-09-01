"""Offline, read-only access to one captured cluster snapshot.

Every solution tool reads the cluster through this module and nothing else. It
is the single place that knows the capture layout (evals/capture.sh writes
`cluster/<kind>.json`, `ns/<ns>/<kind>.json`, `ns/<ns>/describe/<obj>.txt` and
`ns/<ns>/logs/<pod>__<container>[.previous].log`), so a tool can neither invent
a path nor read outside the fixture directory.

Two capture facts drive the awkward parts here. Describe files are named from
`kubectl get -o name`, which is api-group qualified — `deployment.apps_web.txt`,
`role.rbac.authorization.k8s.io_reader.txt` — so a describe lookup has to match
on a kind PREFIX, not an exact filename. And a kind that the capture could not
read is written in-band as `{"capture_error": true, ...}`, which is surfaced as
an error rather than silently read as an empty list.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

# Namespaced kinds, exactly as evals/capture.sh names the files.
NAMESPACED_KINDS: tuple[str, ...] = (
    "configmaps",
    "cronjobs",
    "daemonsets",
    "deployments",
    "endpoints",
    "endpointslices",
    "horizontalpodautoscalers",
    "ingresses",
    "jobs",
    "limitranges",
    "networkpolicies",
    "persistentvolumeclaims",
    "poddisruptionbudgets",
    "pods",
    "replicasets",
    "resourcequotas",
    "rolebindings",
    "roles",
    "secrets",
    "serviceaccounts",
    "services",
    "statefulsets",
)

# Cluster-scoped kinds, likewise from the capture script.
CLUSTER_KINDS: tuple[str, ...] = (
    "clusterrolebindings",
    "clusterroles",
    "events",
    "namespaces",
    "nodes",
    "pv",
    "storageclasses",
)

# kubectl's own singulars and short names, so the model's natural phrasing
# resolves instead of 404-ing on a plural it never uses in practice.
_ALIASES: dict[str, str] = {
    "cj": "cronjobs",
    "cm": "configmaps",
    "crb": "clusterrolebindings",
    "deploy": "deployments",
    "ds": "daemonsets",
    "ep": "endpoints",
    "hpa": "horizontalpodautoscalers",
    "ing": "ingresses",
    "limits": "limitranges",
    "netpol": "networkpolicies",
    "no": "nodes",
    "ns": "namespaces",
    "pdb": "poddisruptionbudgets",
    "persistentvolume": "pv",
    "persistentvolumes": "pv",
    "po": "pods",
    "pvc": "persistentvolumeclaims",
    "quota": "resourcequotas",
    "rb": "rolebindings",
    "rq": "resourcequotas",
    "rs": "replicasets",
    "sa": "serviceaccounts",
    "sc": "storageclasses",
    "sts": "statefulsets",
    "svc": "services",
}

_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class FixtureError(RuntimeError):
    """A tool asked the snapshot for something it cannot serve."""


def _known_kinds() -> dict[str, str]:
    kinds = {kind: kind for kind in NAMESPACED_KINDS + CLUSTER_KINDS}
    kinds |= {kind.rstrip("s"): kind for kind in NAMESPACED_KINDS + CLUSTER_KINDS}
    kinds |= {"endpointslice": "endpointslices", "ingress": "ingresses"}
    kinds |= _ALIASES
    return kinds


_KINDS = _known_kinds()


def normalize_kind(kind: str) -> str:
    """Resolve any kubectl spelling of a kind to its captured file stem."""
    resolved = _KINDS.get(kind.strip().lower())
    if resolved is None:
        raise FixtureError(
            f"unknown or uncaptured kind {kind!r}; captured kinds are: "
            + ", ".join(sorted(set(NAMESPACED_KINDS + CLUSTER_KINDS)))
        )
    return resolved


def is_namespaced(kind: str) -> bool:
    return normalize_kind(kind) in NAMESPACED_KINDS


def _safe(value: str, label: str) -> str:
    """Reject any name that could escape the fixture directory."""
    cleaned = value.strip()
    if not _SAFE_NAME.fullmatch(cleaned):
        raise FixtureError(f"invalid {label} {value!r}")
    return cleaned


def namespaces(fixture: Path) -> list[str]:
    """Every namespace present in the snapshot."""
    return sorted(p.name for p in (fixture / "ns").iterdir() if p.is_dir())


def page(fixture: Path) -> str:
    """The alert the on-call engineer was paged with."""
    return (fixture / "page.txt").read_text(encoding="utf-8")


def get_all(fixture: Path) -> str:
    """`kubectl get all -A -o wide`, exactly as captured."""
    return (fixture / "cluster" / "get-all.txt").read_text(encoding="utf-8")


def _read_items(path: Path, what: str) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FixtureError(f"{what} was not captured in this snapshot")
    document = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    if document.get("capture_error"):
        raise FixtureError(f"{what} failed to capture: {document.get('error', 'unknown error')}")
    return cast(list[dict[str, Any]], document.get("items", []))


def load_kind(fixture: Path, kind: str, namespace: str | None = None) -> list[dict[str, Any]]:
    """The captured objects of one kind, in one namespace or cluster-wide."""
    resolved = normalize_kind(kind)
    if resolved in CLUSTER_KINDS:
        return _read_items(fixture / "cluster" / f"{resolved}.json", f"cluster {resolved}")
    if namespace is None:
        items: list[dict[str, Any]] = []
        for ns in namespaces(fixture):
            items.extend(
                _read_items(fixture / "ns" / ns / f"{resolved}.json", f"{resolved} in {ns}")
            )
        return items
    ns = _safe(namespace, "namespace")
    if not (fixture / "ns" / ns).is_dir():
        raise FixtureError(
            f"no namespace {ns!r} in this snapshot; have: {', '.join(namespaces(fixture))}"
        )
    return _read_items(fixture / "ns" / ns / f"{resolved}.json", f"{resolved} in {ns}")


def describe(fixture: Path, kind: str, name: str, namespace: str | None = None) -> str:
    """`kubectl describe`, matching the api-group-qualified capture filename."""
    resolved = normalize_kind(kind)
    safe_name = _safe(name, "name")
    if resolved == "nodes":
        return (fixture / "cluster" / "nodes.describe.txt").read_text(encoding="utf-8")
    if namespace is None:
        raise FixtureError(f"describe of namespaced kind {kind!r} requires a namespace")
    ns = _safe(namespace, "namespace")
    singular = resolved.rstrip("s") if resolved != "endpoints" else "endpoints"
    directory = fixture / "ns" / ns / "describe"
    matches = sorted(directory.glob(f"{singular}*_{safe_name}.txt")) if directory.is_dir() else []
    if not matches:
        raise FixtureError(f"no describe captured for {singular}/{safe_name} in namespace {ns}")
    return matches[0].read_text(encoding="utf-8")


def containers(fixture: Path, namespace: str, pod: str) -> list[str]:
    """Container names that have a captured log channel for this pod."""
    ns, safe_pod = _safe(namespace, "namespace"), _safe(pod, "pod")
    directory = fixture / "ns" / ns / "logs"
    if not directory.is_dir():
        return []
    names = {
        p.name[len(safe_pod) + 2 :].removesuffix(".log").removesuffix(".previous")
        for p in directory.glob(f"{safe_pod}__*.log")
    }
    return sorted(names)


def logs(
    fixture: Path,
    namespace: str,
    pod: str,
    container: str | None = None,
    *,
    previous: bool = False,
) -> str:
    """Captured logs for one pod container, current or previous channel."""
    ns, safe_pod = _safe(namespace, "namespace"), _safe(pod, "pod")
    available = containers(fixture, ns, safe_pod)
    if not available:
        raise FixtureError(f"no logs captured for pod {safe_pod} in namespace {ns}")
    chosen = _safe(container, "container") if container is not None else available[0]
    if chosen not in available:
        raise FixtureError(
            f"pod {safe_pod} has no captured container {chosen!r}; have: {', '.join(available)}"
        )
    suffix = ".previous.log" if previous else ".log"
    path = fixture / "ns" / ns / "logs" / f"{safe_pod}__{chosen}{suffix}"
    if not path.is_file():
        channel = "previous" if previous else "current"
        raise FixtureError(
            f"the {channel} log channel of {safe_pod}/{chosen} was unavailable at capture time"
        )
    return path.read_text(encoding="utf-8")


def events(fixture: Path, namespace: str | None = None) -> list[dict[str, Any]]:
    """Cluster events, optionally narrowed to one namespace."""
    items = _read_items(fixture / "cluster" / "events.json", "cluster events")
    if namespace is None:
        return items
    ns = _safe(namespace, "namespace")
    return [e for e in items if cast(dict[str, Any], e.get("metadata", {})).get("namespace") == ns]


def log_channel_reason(
    fixture: Path,
    namespace: str,
    pod: str,
    container: str,
    *,
    previous: bool = False,
) -> str | None:
    """Why a log channel is absent, from the capture ledger in scenario.yaml.

    A missing log is evidence, not a dead end: a container whose current channel
    was unavailable because the pod had not finished initialising says something
    a present log never could. capture.sh records that reason per channel, so it
    is read back here rather than reported as "no logs".

    Returns the ledger's reason text, or None when the channel was captured fine
    or the ledger has no entry for it. Hand-scanned rather than parsed — no YAML
    library is a declared dependency, and the block shape is fixed by capture.sh.
    """
    ledger = fixture / "scenario.yaml"
    if not ledger.is_file():
        return None
    wanted_pod = f"{_safe(namespace, 'namespace')}/{_safe(pod, 'pod')}"
    wanted_container = _safe(container, "container")
    channel = "previous" if previous else "current"
    in_pod = in_container = False
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if line.startswith("  - pod:"):
            in_pod = line.split(":", 1)[1].strip() == wanted_pod
            in_container = False
        elif in_pod and line.startswith("    container:"):
            in_container = line.split(":", 1)[1].strip() == wanted_container
        elif in_pod and in_container and line.startswith(f"    {channel}:"):
            _, _, comment = line.partition("#")
            return comment.strip() or None
    return None
