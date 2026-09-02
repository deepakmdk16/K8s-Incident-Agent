"""The kubectl-shaped tool layer the agent investigates through.

Every tool is a projection of the captured snapshot, never a raw file dump: a
1.6 MB fixture has to arrive as a few hundred readable characters or the loop
drowns in its own context. Two projections carry the design:

`namespace_overview` renders controllers, pods AND Services at equal weight,
with each Service's `.spec.selector` printed beside its endpoint address count,
and closes with a count of every other captured object kind. Nothing is filtered
on readiness — filtering on readiness is precisely the structural blind spot
that costs the one-shot baseline the rows it loses.

`find_consumers` answers the attribution question the scored answer turns on:
given an object, which workloads reference it, through which field, and what do
the objects of that kind actually look like. A referrer that disagrees with its
healthy peers is the spec a human has to edit.

Tool names and descriptions are prompt surface, so they describe graph edges and
kubectl verbs only — never what tends to go wrong.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from common.llm import ToolSpec
from solution import fixture as fx

TOOL_RESULT_CHAR_CAP = 12_000
DEFAULT_LOG_TAIL = 200
EVENT_MESSAGE_CAP = 200

# Kinds `namespace_overview` renders in full, and the derived kinds it folds
# into those lines; everything else is counted on the `other objects:` line.
_OVERVIEW_KINDS = ("deployments", "statefulsets", "daemonsets", "pods", "services")
_DERIVED_KINDS = ("replicasets", "endpoints", "endpointslices")


@dataclass(frozen=True)
class ToolInvocation:
    """One executed tool call, recorded so its output can be re-executed later."""

    id: str
    name: str
    arguments: dict[str, object]
    output: str
    namespaces_touched: frozenset[str]


class ToolLedger:
    """Every tool call this run made, keyed by the model's tool_use id."""

    def __init__(self) -> None:
        self._calls: dict[str, ToolInvocation] = {}

    def record(self, invocation: ToolInvocation) -> None:
        self._calls[invocation.id] = invocation

    def get(self, call_id: str) -> ToolInvocation | None:
        return self._calls.get(call_id)

    def ids(self) -> tuple[str, ...]:
        return tuple(self._calls)

    def __len__(self) -> int:
        return len(self._calls)


# --- small typed readers over the loosely-typed captured JSON -----------------


def _obj(node: dict[str, Any], *path: str) -> dict[str, Any]:
    current: dict[str, Any] = node
    for key in path:
        nxt = current.get(key)
        if not isinstance(nxt, dict):
            return {}
        current = cast(dict[str, Any], nxt)
    return current


def _items(node: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = node.get(key)
    return cast(list[dict[str, Any]], value) if isinstance(value, list) else []


def _text(node: dict[str, Any], key: str, default: str = "") -> str:
    value = node.get(key)
    return value if isinstance(value, str) else default


def _count(node: dict[str, Any], key: str) -> int:
    value = node.get(key)
    return value if isinstance(value, int) else 0


def _name(node: dict[str, Any]) -> str:
    return _text(_obj(node, "metadata"), "name", "<unnamed>")


def _labels(node: dict[str, Any], *path: str) -> str:
    labels = _obj(node, *path)
    return (
        "{}" if not labels else "{" + ", ".join(f"{k}={v}" for k, v in sorted(labels.items())) + "}"
    )


def _pod_templates(fixture: Path, namespace: str) -> list[tuple[str, dict[str, Any]]]:
    """(kind/name, workload object) for every controller in the namespace."""
    found: list[tuple[str, dict[str, Any]]] = []
    for kind in ("deployments", "statefulsets", "daemonsets"):
        for item in fx.load_kind(fixture, kind, namespace):
            found.append((f"{kind.rstrip('s')}/{_name(item)}", item))
    return found


def _ready(kind: str, item: dict[str, Any]) -> str:
    status, spec = _obj(item, "status"), _obj(item, "spec")
    if kind == "daemonsets":
        return f"{_count(status, 'numberReady')}/{_count(status, 'desiredNumberScheduled')}"
    return f"{_count(status, 'readyReplicas')}/{_count(spec, 'replicas')}"


# --- renderers ---------------------------------------------------------------


def render_namespace_list(fixture: Path) -> str:
    lines: list[str] = []
    for ns in fx.namespaces(fixture):
        pods = fx.load_kind(fixture, "pods", ns)
        not_ready = sum(
            1
            for pod in pods
            if not all(
                bool(cs.get("ready")) for cs in _items(_obj(pod, "status"), "containerStatuses")
            )
            or _text(_obj(pod, "status"), "phase") not in ("Running", "Succeeded")
        )
        workloads = len(_pod_templates(fixture, ns))
        lines.append(f"{ns} workloads={workloads} pods={len(pods)} notReady={not_ready}")
    return "\n".join(lines)


def render_namespace_overview(fixture: Path, namespace: str) -> str:
    lines = [f"namespace {namespace}"]
    for kind in ("deployments", "statefulsets", "daemonsets"):
        for item in fx.load_kind(fixture, kind, namespace):
            labels = _labels(item, "spec", "selector", "matchLabels")
            lines.append(
                f"  {kind.rstrip('s')}/{_name(item)} ready={_ready(kind, item)} podLabels={labels}"
            )
    for pod in fx.load_kind(fixture, "pods", namespace):
        status = _obj(pod, "status")
        parts = [
            f"  pod/{_name(pod)}",
            f"phase={_text(status, 'phase', '?')}",
            f"labels={_labels(pod, 'metadata', 'labels')}",
            f"node={_text(_obj(pod, 'spec'), 'nodeName', '<unscheduled>')}",
        ]
        for prefix, key in (("init:", "initContainerStatuses"), ("", "containerStatuses")):
            for cs in _items(status, key):
                detail = [
                    f"ready={bool(cs.get('ready'))}",
                    f"restarts={_count(cs, 'restartCount')}",
                ]
                waiting = _text(_obj(cs, "state", "waiting"), "reason")
                if waiting:
                    detail.append(f"waiting={waiting}")
                last_exit = _text(_obj(cs, "lastState", "terminated"), "reason")
                if last_exit:
                    detail.append(f"lastExit={last_exit}")
                parts.append(f"{prefix}{_text(cs, 'name', '?')}({','.join(detail)})")
        lines.append(" ".join(parts))
    endpoints = {_name(e): e for e in fx.load_kind(fixture, "endpoints", namespace)}
    for svc in fx.load_kind(fixture, "services", namespace):
        name = _name(svc)
        addresses = sum(
            len(_items(subset, "addresses"))
            for subset in _items(endpoints.get(name, {}), "subsets")
        )
        # An ExternalName Service is a DNS alias: it has no selector and no
        # Endpoints BY DESIGN, so rendering it with the ClusterIP fields makes
        # a healthy alias look exactly like a service whose selector matches
        # nothing. Show what it actually is — the target is also the only
        # reference edge that leaves this namespace.
        external = _text(_obj(svc, "spec"), "externalName")
        if external:
            lines.append(f"  service/{name} type=ExternalName externalName={external}")
            continue
        lines.append(
            f"  service/{name} selector={_labels(svc, 'spec', 'selector')} "
            f"endpointAddresses={addresses}"
        )
    others = [
        f"{kind}={len(fx.load_kind(fixture, kind, namespace))}"
        for kind in fx.NAMESPACED_KINDS
        if kind not in _OVERVIEW_KINDS
        and kind not in _DERIVED_KINDS
        and fx.load_kind(fixture, kind, namespace)
    ]
    if others:
        lines.append("  other objects: " + " ".join(others))
    return "\n".join(lines)


def render_events(
    fixture: Path,
    namespace: str | None,
    involved_name: str | None,
    warnings_only: bool,
) -> str:
    lines: list[str] = []
    for event in fx.events(fixture, namespace):
        if warnings_only and _text(event, "type") != "Warning":
            continue
        involved = _obj(event, "involvedObject")
        name = _text(involved, "name")
        if involved_name is not None and involved_name not in name:
            continue
        ns = _text(involved, "namespace") or _text(_obj(event, "metadata"), "namespace", "-")
        message = " ".join(_text(event, "message").split())[:EVENT_MESSAGE_CAP]
        lines.append(
            f"{ns} {_text(event, 'type')} {_text(event, 'reason')} "
            f"{_text(involved, 'kind').lower()}/{name} x{_count(event, 'count')} {message}"
        )
    return "\n".join(lines) if lines else "0 events matched"


def _project(kind: str, item: dict[str, Any]) -> dict[str, Any]:
    """Drop the noise kubectl also hides, and never emit ConfigMap values."""
    metadata = dict(_obj(item, "metadata"))
    metadata.pop("managedFields", None)
    annotations = dict(cast(dict[str, Any], metadata.get("annotations") or {}))
    annotations.pop("kubectl.kubernetes.io/last-applied-configuration", None)
    if annotations:
        metadata["annotations"] = annotations
    else:
        metadata.pop("annotations", None)
    projected: dict[str, Any] = {"metadata": metadata}
    for key, value in item.items():
        if key in ("metadata", "status", "apiVersion"):
            continue
        if kind == "configmaps" and key == "data":
            projected["dataKeys"] = sorted(cast(dict[str, Any], value or {}))
            continue
        projected[key] = value
    if kind == "pods":
        projected["status"] = _obj(item, "status")
    return projected


def render_object(fixture: Path, namespace: str | None, kind: str, name: str | None) -> str:
    resolved = fx.normalize_kind(kind)
    if resolved in fx.CLUSTER_KINDS:
        raise fx.FixtureError(
            f"{resolved} is cluster-scoped and not served here; "
            "use list_namespaces, cluster_capacity or get_events"
        )
    if namespace is None:
        raise fx.FixtureError(f"{resolved} is namespaced — a namespace is required")
    items = fx.load_kind(fixture, resolved, namespace)
    if name is not None:
        wanted = name.strip().lower()
        matches = [i for i in items if _name(i).lower() == wanted]
        if not matches:
            available = ", ".join(sorted(_name(i) for i in items)) or "none"
            raise fx.FixtureError(
                f"no {resolved[:-1]} named {name!r} in namespace {namespace}; present: {available}"
            )
        return json.dumps(_project(resolved, matches[0]), indent=2, sort_keys=True)
    if not items:
        return f"0 objects of kind {resolved} in namespace {namespace}"
    if len(items) > 5:
        return f"{len(items)} {resolved} in {namespace}: " + ", ".join(
            sorted(_name(i) for i in items)
        )
    return json.dumps([_project(resolved, i) for i in items], indent=2, sort_keys=True)


def render_describe(fixture: Path, namespace: str | None, kind: str, name: str) -> str:
    return fx.describe(fixture, kind, name, namespace)


def render_logs(
    fixture: Path,
    namespace: str,
    pod: str,
    container: str | None,
    previous: bool,
    contains: str | None,
    tail: int,
) -> str:
    try:
        text = fx.logs(fixture, namespace, pod, container, previous=previous)
    except fx.FixtureError as exc:
        if container is not None:
            reason = fx.log_channel_reason(fixture, namespace, pod, container, previous=previous)
            if reason is not None:
                raise fx.FixtureError(f"{exc} — capture recorded: {reason}") from exc
        raise
    lines = text.splitlines()
    if contains:
        lines = [line for line in lines if contains.lower() in line.lower()]
        if not lines:
            return f"0 log lines contain {contains!r}"
    kept = lines[-max(tail, 1) :]
    omitted = len(lines) - len(kept)
    prefix = f"[{omitted} earlier lines omitted; raise tail to see them]\n" if omitted else ""
    return prefix + "\n".join(kept)


def _reference_paths(spec: dict[str, Any], kind: str, name: str) -> list[str]:
    """Every place this pod template names the target object. Graph edges only."""
    paths: list[str] = []
    if kind == "serviceaccounts" and _text(spec, "serviceAccountName") == name:
        paths.append("spec.serviceAccountName")
    ref_key = {"configmaps": "configMapKeyRef", "secrets": "secretKeyRef"}.get(kind)
    from_key = {"configmaps": "configMapRef", "secrets": "secretRef"}.get(kind)
    containers = _items(spec, "initContainers") + _items(spec, "containers")
    for container in containers:
        for env in _items(container, "env"):
            ref = _obj(env, "valueFrom", ref_key) if ref_key else {}
            if ref and _text(ref, "name") == name:
                paths.append(f"env[{_text(env, 'name')}].{ref_key}.key={_text(ref, 'key')}")
        for env_from in _items(container, "envFrom"):
            ref = _obj(env_from, from_key) if from_key else {}
            if ref and _text(ref, "name") == name:
                paths.append(f"envFrom.{from_key}")
    volume_field = {
        "configmaps": "configMap",
        "secrets": "secret",
        "persistentvolumeclaims": "persistentVolumeClaim",
    }.get(kind)
    for volume in _items(spec, "volumes"):
        source = _obj(volume, volume_field) if volume_field else {}
        claim = _text(source, "claimName") or _text(source, "name")
        if source and claim == name:
            paths.append(f"volume {_text(volume, 'name')}")
    return paths


def _externalname_aliases(fixture: Path, namespace: str, name: str) -> list[str]:
    """Services in any namespace whose ExternalName target is this Service.

    The alias is the only reference edge in a captured snapshot that crosses a
    namespace boundary, and it is directional — the consumer names the target,
    never the reverse. Without this, a Service an entire other namespace
    depends on reports no consumers at all, which reads as "nothing uses it".
    """
    hits: list[str] = []
    for other in fx.namespaces(fixture):
        for svc in fx.load_kind(fixture, "services", other):
            target = _text(_obj(svc, "spec"), "externalName")
            segments = target.rstrip(".").split(".")
            if len(segments) >= 2 and segments[0] == name and segments[1] == namespace:
                hits.append(
                    f"service/{_name(svc)} in namespace {other} aliases this Service via "
                    f"spec.externalName={target}"
                )
    return hits


def render_consumers(fixture: Path, namespace: str, kind: str, name: str) -> str:
    resolved = fx.normalize_kind(kind)
    lines: list[str] = []
    for label, workload in _pod_templates(fixture, namespace):
        spec = _obj(workload, "spec", "template", "spec")
        paths = _reference_paths(spec, resolved, name)
        if paths:
            workload_kind = label.split("/", 1)[0] + "s"
            lines.append(
                f"{label} ready={_ready(workload_kind, workload)} references via {', '.join(paths)}"
            )
    if resolved == "serviceaccounts":
        for binding in fx.load_kind(fixture, "rolebindings", namespace):
            for subject in _items(binding, "subjects"):
                subject_name = _text(subject, "name")
                verdict = "MATCHES" if subject_name == name else "does not match"
                role = _obj(binding, "roleRef")
                lines.append(
                    f"rolebinding/{_name(binding)} subjects[].name='{subject_name}' "
                    f"roleRef={_text(role, 'kind')}/{_text(role, 'name')} ({verdict})"
                )
    if resolved == "services":
        lines += _externalname_aliases(fixture, namespace, name)
    existing = sorted(_name(i) for i in fx.load_kind(fixture, resolved, namespace))
    if not lines:
        lines.append(f"no workload in {namespace} references {resolved[:-1]}/{name}")
    lines.append(f"{resolved} that exist in {namespace}: {', '.join(existing) or 'none'}")
    if resolved == "configmaps":
        for item in fx.load_kind(fixture, "configmaps", namespace):
            if _name(item) == name:
                lines.append(f"keys in configmap/{name}: {sorted(_obj(item, 'data'))}")
    return "\n".join(lines)


def render_cluster_capacity(fixture: Path) -> str:
    lines: list[str] = []
    for node in fx.load_kind(fixture, "nodes"):
        status = _obj(node, "status")
        capacity, allocatable = _obj(status, "capacity"), _obj(status, "allocatable")
        lines.append(
            f"node/{_name(node)} cpu={_text(capacity, 'cpu')}/{_text(allocatable, 'cpu')} "
            f"memory={_text(capacity, 'memory')}/{_text(allocatable, 'memory')} "
            f"pods={_text(capacity, 'pods')} (capacity/allocatable)"
        )
        for taint in _items(_obj(node, "spec"), "taints"):
            lines.append(
                f"  taint {_text(taint, 'key')}={_text(taint, 'value')}:{_text(taint, 'effect')}"
            )
        for condition in _items(status, "conditions"):
            if _text(condition, "status") != "False" or _text(condition, "type") == "Ready":
                lines.append(f"  condition {_text(condition, 'type')}={_text(condition, 'status')}")
    for storage_class in fx.load_kind(fixture, "storageclasses"):
        annotations = _obj(storage_class, "metadata", "annotations")
        default = annotations.get("storageclass.kubernetes.io/is-default-class") == "true"
        lines.append(
            f"storageclass/{_name(storage_class)} provisioner={_text(storage_class, 'provisioner')}"
            + (" (default)" if default else "")
        )
    return "\n".join(lines)


# --- argument handling and dispatch -------------------------------------------


def _arg_str(arguments: Mapping[str, object], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise fx.FixtureError(f"argument {key!r} must be a string")
    return value.strip() or None


def _need_str(arguments: Mapping[str, object], key: str) -> str:
    value = _arg_str(arguments, key)
    if value is None:
        raise fx.FixtureError(f"argument {key!r} is required")
    return value


def _arg_bool(arguments: Mapping[str, object], key: str, default: bool) -> bool:
    value = arguments.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise fx.FixtureError(f"argument {key!r} must be true or false")
    return value


def _arg_int(arguments: Mapping[str, object], key: str, default: int) -> int:
    value = arguments.get(key)
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool):
        raise fx.FixtureError(f"argument {key!r} must be a whole number")
    return value


def truncate(text: str) -> str:
    """Cap a tool result loudly — a silent cut would hide evidence from the agent."""
    if len(text) <= TOOL_RESULT_CHAR_CAP:
        return text
    omitted = len(text) - TOOL_RESULT_CHAR_CAP
    return (
        text[:TOOL_RESULT_CHAR_CAP] + f"\n… [truncated, {omitted} chars omitted; narrow the query]"
    )


def namespaces_touched(name: str, arguments: Mapping[str, object]) -> frozenset[str]:
    """Namespaces a call reached into; the empty string marks a cluster-scoped read."""
    if name in ("list_namespaces", "cluster_capacity"):
        return frozenset({""})
    namespace = arguments.get("namespace")
    if name == "get_events" and not isinstance(namespace, str):
        return frozenset({""})
    return frozenset({namespace}) if isinstance(namespace, str) and namespace else frozenset({""})


def dispatch(fixture: Path, name: str, arguments: Mapping[str, object]) -> str:
    """Execute one read tool against the snapshot. Returns text for a tool_result.

    A FixtureError — bad kind, unknown namespace, absent log channel — comes back
    as `ERROR: <message>` rather than raising: a wrong argument is information the
    agent can act on, and the absence of a log is itself evidence. Anything else
    propagates untouched so that real bugs and the harness's billing abort both
    stay visible.
    """
    try:
        if name == "list_namespaces":
            return truncate(render_namespace_list(fixture))
        if name == "namespace_overview":
            return truncate(render_namespace_overview(fixture, _need_str(arguments, "namespace")))
        if name == "get_object":
            return truncate(
                render_object(
                    fixture,
                    _arg_str(arguments, "namespace"),
                    _need_str(arguments, "kind"),
                    _arg_str(arguments, "name"),
                )
            )
        if name == "describe":
            return truncate(
                render_describe(
                    fixture,
                    _arg_str(arguments, "namespace"),
                    _need_str(arguments, "kind"),
                    _need_str(arguments, "name"),
                )
            )
        if name == "get_logs":
            return truncate(
                render_logs(
                    fixture,
                    _need_str(arguments, "namespace"),
                    _need_str(arguments, "pod"),
                    _arg_str(arguments, "container"),
                    _arg_bool(arguments, "previous", False),
                    _arg_str(arguments, "contains"),
                    _arg_int(arguments, "tail", DEFAULT_LOG_TAIL),
                )
            )
        if name == "get_events":
            return truncate(
                render_events(
                    fixture,
                    _arg_str(arguments, "namespace"),
                    _arg_str(arguments, "involved_name"),
                    _arg_bool(arguments, "warnings_only", True),
                )
            )
        if name == "find_consumers":
            return truncate(
                render_consumers(
                    fixture,
                    _need_str(arguments, "namespace"),
                    _need_str(arguments, "kind"),
                    _need_str(arguments, "name"),
                )
            )
        if name == "cluster_capacity":
            return truncate(render_cluster_capacity(fixture))
    except fx.FixtureError as exc:
        return f"ERROR: {exc}"
    return f"ERROR: no tool named {name!r}"


_RESOURCE_REF = {
    "type": "object",
    "properties": {
        "kind": {"type": "string"},
        "namespace": {"type": "string"},
        "name": {"type": "string"},
    },
    "required": ["kind", "namespace", "name"],
    "additionalProperties": False,
}

SUBMIT_ANSWER = "submit_answer"

TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="list_namespaces",
        description=(
            "List every namespace in the snapshot with its workload, pod and not-ready pod "
            "counts. Cluster-scoped; no arguments."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        name="namespace_overview",
        description=(
            "Controllers (ready/desired plus pod-template labels), every pod (phase, labels, "
            "node, per-container ready/restarts/waiting/last-exit, init containers included), "
            "every Service with its .spec.selector beside its endpoint address count, and a "
            "count of every other captured object kind in the namespace. Shows all resources, "
            "not only unhealthy ones."
        ),
        input_schema={
            "type": "object",
            "properties": {"namespace": {"type": "string"}},
            "required": ["namespace"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="get_object",
        description=(
            "The projected spec of one object, or the names of every object of a kind when "
            "'name' is omitted. Namespaced kinds only: pods, deployments, replicasets, "
            "statefulsets, daemonsets, services, endpoints, endpointslices, configmaps, "
            "secrets, serviceaccounts, roles, rolebindings, persistentvolumeclaims, "
            "resourcequotas, networkpolicies, limitranges, poddisruptionbudgets, jobs, "
            "cronjobs, horizontalpodautoscalers, ingresses. ConfigMap keys are shown, values "
            "are not; Secret values are redacted in the capture."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "kind": {"type": "string"},
                "name": {"type": "string"},
            },
            "required": ["namespace", "kind"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="describe",
        description="kubectl describe for one object, including its per-object event list.",
        input_schema={
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "kind": {"type": "string"},
                "name": {"type": "string"},
            },
            "required": ["namespace", "kind", "name"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="get_logs",
        description=(
            "Container logs for one pod from the captured snapshot, regardless of whether the "
            "pod is ready. Set previous=true for the prior container instance. If a channel "
            "was not captured, the recorded reason is returned."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "pod": {"type": "string"},
                "container": {"type": "string"},
                "previous": {"type": "boolean"},
                "contains": {"type": "string"},
                "tail": {"type": "integer"},
            },
            "required": ["namespace", "pod"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="get_events",
        description=(
            "Cluster events, projected to type/reason/involved object/count/message. Omit "
            "'namespace' for a cluster-wide view; warnings_only defaults to true."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "involved_name": {"type": "string"},
                "warnings_only": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="find_consumers",
        description=(
            "Every workload in the namespace whose pod template references the named object, "
            "with the exact reference path and key it uses and that workload's ready count; "
            "plus the objects of that kind that actually exist in the namespace. For a service "
            "account, also every RoleBinding subject that names it or fails to."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "kind": {"type": "string"},
                "name": {"type": "string"},
            },
            "required": ["namespace", "kind", "name"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="cluster_capacity",
        description=(
            "Node capacity, allocatable, taints and conditions, plus the StorageClasses that "
            "exist and which is default. Cluster-scoped; no arguments."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        name=SUBMIT_ANSWER,
        description=(
            "Finish the incident. Every quote is re-checked against the tool output it cites "
            "and every verification check is re-run against the snapshot; if anything does not "
            "hold up you are told exactly what and you call this again."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "failing_resource": _RESOURCE_REF,
                "remediation": {
                    "type": "object",
                    "description": (
                        "The single edit that fixes the incident. Must target the same object "
                        "as failing_resource."
                    ),
                    "properties": {
                        "kind": {"type": "string"},
                        "namespace": {"type": "string"},
                        "name": {"type": "string"},
                        "field_path": {"type": "string"},
                        "current_value": {"type": "string"},
                        "required_value": {"type": "string"},
                    },
                    "required": [
                        "kind",
                        "namespace",
                        "name",
                        "field_path",
                        "current_value",
                        "required_value",
                    ],
                    "additionalProperties": False,
                },
                "root_cause_statement": {
                    "type": "string",
                    "description": "The Root cause section, in plain sentences.",
                },
                "mechanism": {
                    "type": "string",
                    "description": (
                        "1-3 sentences, the causal mechanism of the paged symptom only. Name "
                        "the object, the field by its API path, the wrong value against the "
                        "right one, and what fails."
                    ),
                },
                "evidence": {
                    "type": "array",
                    "minItems": 2,
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string", "enum": ["symptom", "link", "defect"]},
                            "claim": {"type": "string"},
                            "tool_call_id": {
                                "type": "string",
                                "description": (
                                    "The [call_id: cN] printed at the top of the tool result "
                                    'whose output shows this. Use "page" for the alert text and '
                                    '"overview" for the namespace overview you were given.'
                                ),
                            },
                            "quote": {
                                "type": "string",
                                "description": "Text copied literally from that tool's output.",
                            },
                        },
                        "required": ["role", "claim", "tool_call_id", "quote"],
                        "additionalProperties": False,
                    },
                },
                "ruled_out": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "alternative": {"type": "string"},
                            "entity_names": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "The object names this alternative was about. They are "
                                    "banned from the mechanism sentence."
                                ),
                            },
                            "ruling_claim": {"type": "string"},
                            "tool_call_id": {"type": "string"},
                            "quote": {"type": "string"},
                        },
                        "required": [
                            "alternative",
                            "entity_names",
                            "ruling_claim",
                            "tool_call_id",
                            "quote",
                        ],
                        "additionalProperties": False,
                    },
                },
                "verification": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 3,
                    "items": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "The exact kubectl command a human would type.",
                            },
                            "tool": {
                                "type": "string",
                                "description": "The tool that reproduces that command here.",
                            },
                            "arguments": {"type": "object", "additionalProperties": True},
                            "must_contain": {
                                "type": "string",
                                "description": "The text that must appear in the result.",
                            },
                        },
                        "required": ["command", "tool", "arguments", "must_contain"],
                        "additionalProperties": False,
                    },
                },
                "verdict": {
                    "type": "string",
                    "enum": ["confirmed", "probable", "inconclusive"],
                },
                "missing_evidence": {
                    "type": "string",
                    "description": (
                        "Required and non-empty only when the verdict is inconclusive."
                    ),
                },
            },
            "required": [
                "failing_resource",
                "remediation",
                "root_cause_statement",
                "mechanism",
                "evidence",
                "ruled_out",
                "verification",
                "verdict",
            ],
            "additionalProperties": False,
        },
    ),
)

READ_TOOL_NAMES: frozenset[str] = frozenset(
    spec.name for spec in TOOL_SPECS if spec.name != SUBMIT_ANSWER
)
