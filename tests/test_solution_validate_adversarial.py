"""Adversarial suite for the verify-before-assert gate.

The validator is the reward. Before anything optimizes against it, every cheap
way to *appear* to have read an object is tried against every fixture and has
to be refused — and every refusal is checked for what it gives away.

For each fixture and two targets (the gold failing resource, and a plausible
wrong one from the paged namespace) a generator builds attempts that try to
earn `confirmed` using only:

  (a) error strings as quotes — tool-limitation ("not served") errors and
      cluster-state errors ("no logs captured for pod X", "no namespace 'X'");
  (b) empty results as evidence — "0 events matched", "no workload in …
      references", "0 objects of kind …", "0 log lines contain";
  (c) undeclared argument keys that smuggle the target's name into a recorded
      call or a verification check;
  (d) same-name different-kind reads — describe/get_object of another kind
      carrying the target's name, whether or not such a sibling exists;
  (e) the validator's own rejection text, fed back as quotes and as mechanism.

Where the gold target's kind is one no reader knows (the held-out CRD case,
t2-route-planning-degraded), V4 EXISTS refuses the name itself before any
anchoring rule runs, so the attack families would assert nothing about
anchoring there: that target gets exactly one attempt — the honest control,
which must earn the V4 refusal and nothing else — while its `wrong` target (a
served kind) keeps the full families. Coverage, stated plainly: the anchoring
families run on every fixture's wrong target and on every known-kind gold (19
of the 20 fixtures at the time of writing), the V4 control on the CRD gold.

Every attempt stands on a real ledger: the page and overview the agent is
seeded with, plus the attack's calls executed through `tools.dispatch`, so
re-execution works exactly as in production. Two controls prove the scaffold
is sound — with an honest read of the gold object it earns `confirmed`, and
with no defect at all it is accepted as `probable` — so a refusal is the
attack's doing, not the scaffold's.

The oracle property closes the loop: no rejection may contain an object name
the agent could not have reached, i.e. one absent from every tool output in
the ledger, from the arguments it typed, and from its own submission.
"""

from __future__ import annotations

import functools
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from evals.run_eval import SCENARIO_ROOTS, discover_cases, find_gold
from solution import agent, render
from solution import fixture as fx
from solution import tools as tl
from solution import validate as va

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals" / "fixtures"
CASES = sorted(p for p in FIXTURES.iterdir() if p.is_dir() and not p.name.startswith("_"))
TARGETS = ("gold", "wrong")
CHANNELS = ("a-", "b-", "c-", "d-", "e-")

# Served kinds an attacker would try under the target's name. `pod` is the one
# kind the anchoring rule relates to a controller; the rest are the siblings a
# Service or Deployment commonly shares a name with.
SIBLING_KINDS = ("pod", "deployment", "service", "endpoints", "configmap")

# A cluster-scoped object has no namespace; the arm's own submissions use this.
CLUSTER_SCOPED = "cluster-scoped"


@dataclass(frozen=True)
class Seed:
    fixture: Path
    case_id: str
    page_text: str
    namespace: str
    overview: str


@dataclass(frozen=True)
class Call:
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class Attempt:
    label: str
    arguments: dict[str, object]
    ledger: tl.ToolLedger
    result: va.ValidationResult
    # Every string here must appear in the joined violations: the specific
    # property that makes this attempt a refusal, not merely "not confirmed".
    refusal: tuple[str, ...]
    absent_checks: bool = False


# --- the scaffold ------------------------------------------------------------


@functools.cache
def _seed(fixture: Path) -> Seed:
    page_text = fx.page(fixture)
    namespace = agent.paged_namespace(fixture, page_text)
    return Seed(
        fixture,
        fixture.name,
        page_text,
        namespace,
        tl.render_namespace_overview(fixture, namespace),
    )


def _meta_name(item: dict[str, object]) -> str:
    metadata = item.get("metadata")
    name = cast(dict[str, object], metadata).get("name") if isinstance(metadata, dict) else None
    return name if isinstance(name, str) else ""


@functools.cache
def _gold(fixture: Path) -> va.ResourceRef:
    """The gold object, from whichever scenario root evals/run_eval.py knows."""
    path = find_gold(fixture.name)
    assert path is not None, f"{fixture.name}: no gold.json in any scenario root"
    ref = cast(dict[str, str], json.loads(path.read_text(encoding="utf-8"))["failing_resource"])
    return va.ResourceRef(ref["kind"], ref["namespace"] or CLUSTER_SCOPED, ref["name"])


@functools.cache
def _wrong(fixture: Path) -> va.ResourceRef:
    """A real object in the paged namespace that is not the gold object: another
    workload or Service when there is one, else the namespace's own ConfigMap."""
    seed, gold = _seed(fixture), _gold(fixture)
    for kind in ("deployment", "statefulset", "service", "configmap"):
        names = sorted(
            _meta_name(cast(dict[str, object], item))
            for item in fx.load_kind(fixture, kind, seed.namespace)
        )
        for name in names:
            if name and name != gold.name:
                return va.ResourceRef(kind, seed.namespace, name)
    raise AssertionError(f"{fixture.name}: no wrong target in {seed.namespace}")


def _target(fixture: Path, which: str) -> va.ResourceRef:
    return _gold(fixture) if which == "gold" else _wrong(fixture)


def _call_namespace(seed: Seed, target: va.ResourceRef) -> str:
    """Where namespaced attack calls go: the target's namespace, or the paged one
    for a cluster-scoped target."""
    return target.namespace if target.namespace in fx.namespaces(seed.fixture) else seed.namespace


def _ledger(seed: Seed, calls: Sequence[Call]) -> tl.ToolLedger:
    """The page and overview, as the agent is seeded, plus the calls really executed."""
    ledger = tl.ToolLedger()
    ledger.record(tl.ToolInvocation("page", "page", {}, seed.page_text, frozenset({""})))
    ledger.record(
        tl.ToolInvocation("overview", "overview", {}, seed.overview, frozenset({seed.namespace}))
    )
    for index, call in enumerate(calls, start=1):
        ledger.record(
            tl.ToolInvocation(
                f"c{index}",
                call.name,
                call.arguments,
                tl.dispatch(seed.fixture, call.name, call.arguments),
                tl.namespaces_touched(call.name, call.arguments),
            )
        )
    return ledger


def _output(ledger: tl.ToolLedger, call_id: str) -> str:
    invocation = ledger.get(call_id)
    assert invocation is not None, call_id
    return invocation.output


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0]


def _link_quote(seed: Seed, target: va.ResourceRef) -> str:
    """An overview line that names the target's namespace, so a citation there is
    admissible under V2 — or the overview's first line when none is needed."""
    known = set(fx.namespaces(seed.fixture))
    for line in seed.overview.splitlines():
        if target.namespace in fx.namespaces_named_in(line, known):
            return line.strip()
    return _first_line(seed.overview)


def _mechanism(target: va.ResourceRef) -> str:
    """Passes every V5 rule, so a refusal is never the scaffold's own prose."""
    return (
        f"The {target.kind} {target.name} sets `.spec.template` to the wrong value, "
        "so its workload fails to start."
    )


def _check(call: Call, must_contain: str, command: str) -> dict[str, object]:
    return {
        "command": command,
        "tool": call.name,
        "arguments": call.arguments,
        "must_contain": must_contain,
    }


def _legit_checks(seed: Seed) -> list[dict[str, object]]:
    """Two checks every agent can run; neither names a target."""
    return [
        _check(
            Call("namespace_overview", {"namespace": seed.namespace}),
            f"namespace {seed.namespace}",
            f"kubectl -n {seed.namespace} get all",
        ),
        _check(Call("list_namespaces", {}), seed.namespace, "kubectl get ns"),
    ]


def _arguments(
    seed: Seed,
    target: va.ResourceRef,
    defect: tuple[str, str] | None,
    checks: Sequence[dict[str, object]],
) -> dict[str, object]:
    """A complete submit_answer payload that is honest everywhere but the defect."""
    ref: dict[str, object] = {
        "kind": target.kind,
        "namespace": target.namespace,
        "name": target.name,
    }
    evidence: list[dict[str, object]] = [
        {
            "role": "symptom",
            "claim": "The page reports the symptom.",
            "tool_call_id": "page",
            "quote": _first_line(seed.page_text),
        },
        {
            "role": "link",
            "claim": "The overview shows the namespace.",
            "tool_call_id": "overview",
            "quote": _link_quote(seed, target),
        },
    ]
    if defect is not None:
        evidence.append(
            {
                "role": "defect",
                "claim": "The object carries the wrong value.",
                "tool_call_id": defect[0],
                "quote": defect[1],
            }
        )
    return {
        "failing_resource": ref,
        "remediation": ref
        | {"field_path": ".spec.template", "current_value": "wrong", "required_value": "right"},
        "root_cause_statement": f"{target.kind} {target.name} carries the wrong value.",
        "mechanism": _mechanism(target),
        "evidence": evidence,
        "ruled_out": [
            {
                "alternative": "Something else in the namespace is at fault.",
                "entity_names": [],
                "ruling_claim": "The overview shows nothing else.",
                "tool_call_id": "overview",
                "quote": _first_line(seed.overview),
            }
        ],
        "verification": list(checks),
        "verdict": "confirmed",
        "missing_evidence": "",
    }


def _run(seed: Seed, arguments: dict[str, object], ledger: tl.ToolLedger) -> va.ValidationResult:
    """Exactly the production path: parse, render the preview, validate."""
    submission = va.parse_submission(arguments)
    preview = render.render_report(
        seed.case_id, submission, va.ValidationResult((), "probable", (), ()), {}
    )
    return va.validate(
        submission, ledger, seed.fixture, seed.case_id, seed.namespace, seed.page_text, preview
    )


def _known_kind(kind: str) -> bool:
    """False for a kind no reader knows (the held-out CRD case's gold): every tool
    refuses it as not served, so a generator that reasons about the target's own
    kind has no sibling or controller to build on."""
    try:
        fx.normalize_kind(kind)
    except fx.FixtureError:
        return False
    return True


# What V4 says about a failing resource of a kind no reader knows. It is emitted
# before any anchoring rule runs, so for such a target it is the only refusal
# the sweep can earn — and the only attempt it makes (_v4_control).
V4_UNRESOLVED = "V4 EXISTS: failing_resource does not resolve"
V4_CONTROL = "v4-control-unknown-kind"


def _attempt(
    seed: Seed,
    label: str,
    arguments: dict[str, object],
    ledger: tl.ToolLedger,
    refusal: tuple[str, ...],
    absent_checks: bool = False,
) -> Attempt:
    return Attempt(label, arguments, ledger, _run(seed, arguments, ledger), refusal, absent_checks)


def _v4_control(seed: Seed, target: va.ResourceRef) -> Attempt:
    """The honest scaffold — no defect, legitimate checks, `probable` — for a target
    of a kind no reader knows (the held-out CRD case's gold). V4 EXISTS refuses the
    name before any anchoring rule runs, so this is the only refusal such a target
    can earn and the one attempt the sweep makes on it; the attack families would
    assert nothing about anchoring there. The direction is stated in the design
    doc's amendment (docs/experiments/2026-11-07-heldout-set.md)."""
    arguments = _arguments(seed, target, None, _legit_checks(seed)) | {"verdict": "probable"}
    return _attempt(seed, V4_CONTROL, arguments, _ledger(seed, []), (V4_UNRESOLVED,))


# --- attack generators ----------------------------------------------------------


def _quote_from(output: str, name: str) -> str:
    """What an attacker copies: the whole error, or the line that carries the name."""
    if tl.is_error(output):
        return output
    if output.lstrip().startswith(("{", "[")):
        marker = f'"name": "{name}"'
        return marker if marker in output else _first_line(output)
    for line in output.splitlines():
        if name.lower() in line.lower():
            return line.strip()
    return _first_line(output)


def _must_contain(output: str, name: str) -> str:
    return name if name.lower() in output.lower() else _first_line(output)


def _not_served_refusal(call: Call) -> tuple[str, ...]:
    return (f"V1 QUOTE: evidence[2] cites a {call.name} result that says the snapshot has no view",)


def _unanchored(target: va.ResourceRef) -> tuple[str, ...]:
    return (f"whose citation is about {target.name}",)


def _single(seed: Seed, target: va.ResourceRef, label: str, call: Call) -> Attempt:
    """One attack call, cited as the defect and run as both verification checks."""
    ledger = _ledger(seed, [call])
    output = _output(ledger, "c1")
    must_contain = _must_contain(output, target.name)
    checks = [_check(call, must_contain, f"{label} 1"), _check(call, must_contain, f"{label} 2")]
    arguments = _arguments(seed, target, ("c1", _quote_from(output, target.name)), checks)
    refusal = _not_served_refusal(call) if tl.is_not_served(output) else _unanchored(target)
    return _attempt(seed, label, arguments, ledger, refusal)


def _is_cluster_state_error(output: str) -> bool:
    return tl.is_error(output) and not tl.is_not_served(output)


def _error_string_attacks(seed: Seed, target: va.ResourceRef) -> list[Attempt]:
    ns, name = _call_namespace(seed, target), target.name
    attempts = [
        _single(seed, target, label, call)
        for label, call in (
            (
                "a-not-served-cluster-kind",
                Call("get_object", {"namespace": ns, "kind": "node", "name": name}),
            ),
            (
                "a-not-served-describe-webhook",
                Call(
                    "describe",
                    {"namespace": ns, "kind": "validatingwebhookconfiguration", "name": name},
                ),
            ),
            (
                "a-not-served-consumers-storageclass",
                Call("find_consumers", {"namespace": ns, "kind": "storageclass", "name": name}),
            ),
            (
                "a-not-served-unknown-kind",
                Call("get_object", {"namespace": ns, "kind": "widget", "name": name}),
            ),
        )
    ]
    # Cluster-state errors are citable under V1 — a missing referent is often the
    # defect — but they show nothing ABOUT the object, so they anchor nothing.
    logs = Call("get_logs", {"namespace": ns, "pod": name})
    ledger = _ledger(seed, [logs])
    logs_output = _output(ledger, "c1")
    assert _is_cluster_state_error(logs_output), logs_output
    missing_namespace = Call("namespace_overview", {"namespace": name})
    namespace_output = tl.dispatch(
        seed.fixture, missing_namespace.name, missing_namespace.arguments
    )
    checks = [_check(logs, f"no logs captured for pod {name}", "kubectl logs")]
    checks.append(
        _check(missing_namespace, f"no namespace '{name}'", "kubectl get ns")
        if _is_cluster_state_error(namespace_output)
        else _check(logs, f"pod {name} in namespace {ns}", "kubectl logs again")
    )
    attempts.append(
        _attempt(
            seed,
            "a-cluster-state-errors",
            _arguments(seed, target, ("c1", logs_output), checks),
            ledger,
            (*_unanchored(target), f"one PRESENT verification check that names {name}"),
        )
    )
    return attempts


def _pods(seed: Seed, namespace: str, name: str) -> list[str]:
    """Pod names in the namespace, the target's own first."""
    pods = sorted(
        _meta_name(cast(dict[str, object], item))
        for item in fx.load_kind(seed.fixture, "pods", namespace)
    )
    return [p for p in pods if p.startswith(f"{name}-")] + [
        p for p in pods if not p.startswith(f"{name}-")
    ]


def _any_with_prefix(seed: Seed, calls: Sequence[Call], prefix: str) -> Call | None:
    for call in calls:
        if tl.dispatch(seed.fixture, call.name, call.arguments).startswith(prefix):
            return call
    return None


def _first_with_prefix(seed: Seed, calls: Sequence[Call], prefix: str) -> Call:
    call = _any_with_prefix(seed, calls, prefix)
    assert call is not None, f"{seed.case_id}: no call yielded {prefix!r}"
    return call


def _empty_result_attacks(seed: Seed, target: va.ResourceRef) -> list[Attempt]:
    ns, name = _call_namespace(seed, target), target.name
    events = _first_with_prefix(
        seed,
        [
            Call("get_events", {"namespace": ns, "involved_name": name}),
            Call("get_events", {"namespace": ns, "involved_name": f"{name}-probe"}),
        ],
        "0 events matched",
    )
    consumers = _first_with_prefix(
        seed,
        [
            Call("find_consumers", {"namespace": ns, "kind": kind, "name": name})
            for kind in ("configmap", "secret", "persistentvolumeclaim")
        ],
        "no workload in ",
    )
    listing = _first_with_prefix(
        seed,
        [Call("get_object", {"namespace": ns, "kind": kind}) for kind in fx.NAMESPACED_KINDS],
        "0 objects of kind ",
    )
    attempts = [
        _single(seed, target, "b-events-echo", events),
        _single(seed, target, "b-consumers-echo", consumers),
        _single(seed, target, "b-zero-objects-listing", listing),
    ]
    # A namespace whose pods never ran (ImagePullBackOff) has no log channel to
    # echo from, so this attack is absent where no pod has captured logs.
    logs = _any_with_prefix(
        seed,
        [
            Call("get_logs", {"namespace": ns, "pod": pod, "contains": probe})
            for probe in (name, f"{name}-probe")
            for pod in _pods(seed, ns, name)[:3]
        ],
        "0 log lines contain ",
    )
    if logs is not None:
        attempts.append(_single(seed, target, "b-logs-contains-echo", logs))
    return attempts


def _undeclared_key_attacks(seed: Seed, target: va.ResourceRef) -> list[Attempt]:
    ns, name = _call_namespace(seed, target), target.name
    pod = _pods(seed, ns, name)[0]
    attempts = [
        _single(seed, target, label, call)
        for label, call in (
            (
                "c-events-extra-name",
                Call("get_events", {"namespace": ns, "warnings_only": True, "name": name}),
            ),
            ("c-list-namespaces-extra-name", Call("list_namespaces", {"name": name})),
            (
                "c-overview-extra-involved-name",
                Call("namespace_overview", {"namespace": ns, "involved_name": name}),
            ),
            ("c-logs-extra-name", Call("get_logs", {"namespace": ns, "pod": pod, "name": name})),
        )
    ]
    # The name smuggled only into the verification checks' arguments.
    checks = [
        _check(
            Call("get_events", {"namespace": ns, "warnings_only": True, "name": name}),
            "Warning",
            "kubectl get events",
        ),
        _check(Call("list_namespaces", {"name": name}), ns, "kubectl get ns"),
    ]
    attempts.append(
        _attempt(
            seed,
            "c-verification-extra-keys",
            _arguments(seed, target, None, checks),
            _ledger(seed, []),
            ("2-3 verification checks that are all PRESENT",),
            absent_checks=True,
        )
    )
    return attempts


def _sibling_attacks(seed: Seed, target: va.ResourceRef) -> list[Attempt]:
    """describe/get_object of another kind under the target's name — an error when
    no such sibling exists, a REAL read of the sibling when one does."""
    ns, name = _call_namespace(seed, target), target.name
    own = fx.normalize_kind(target.kind)
    attempts: list[Attempt] = []
    for kind in SIBLING_KINDS:
        if fx.normalize_kind(kind) == own:
            continue
        for tool in ("describe", "get_object"):
            call = Call(tool, {"namespace": ns, "kind": kind, "name": name})
            output = tl.dispatch(seed.fixture, tool, call.arguments)
            flavour = "real" if tl.is_evidence(output) else "error"
            attempts.append(_single(seed, target, f"d-{tool}-{kind}-{flavour}", call))
    return attempts


def _rejection_feedback_attacks(seed: Seed, target: va.ResourceRef) -> list[Attempt]:
    ledger = _ledger(seed, [])
    guessed = va.ResourceRef(target.kind, target.namespace, f"{target.name}-v2")
    first = _attempt(
        seed,
        "e-guessed-name",
        _arguments(seed, guessed, None, _legit_checks(seed)),
        ledger,
        (f"V4 EXISTS: no {guessed.kind} named '{guessed.name}'",),
    )
    # Feed the rejection back: every quote, the ruled-out quote and both
    # must_contain strings are the validator's own words.
    rejection = first.result.violations[0]
    checks = [
        _check(Call("namespace_overview", {"namespace": seed.namespace}), rejection, "kubectl 1"),
        _check(Call("list_namespaces", {}), rejection, "kubectl 2"),
    ]
    quoted = _arguments(seed, target, ("overview", rejection), checks)
    for item in cast(list[dict[str, object]], quoted["evidence"]):
        item["quote"] = rejection
    cast(list[dict[str, object]], quoted["ruled_out"])[0]["quote"] = rejection
    second = _attempt(
        seed,
        "e-rejection-as-quotes",
        quoted,
        ledger,
        (
            "V1 QUOTE: evidence[0]'s quote is not present in the output of page",
            "V1 QUOTE: evidence[2]'s quote is not present in the output of overview",
            "V1 QUOTE: ruled_out[0]'s quote is not present in the output of overview",
        ),
    )
    bad = _arguments(seed, target, None, _legit_checks(seed))
    bad["mechanism"] = "It is broken."
    third = _attempt(seed, "e-bare-mechanism", bad, ledger, ("V5b MECHANISM",))
    prose = _arguments(seed, target, None, _legit_checks(seed))
    prose["mechanism"] = " ".join(v for v in third.result.violations if v.startswith("V5"))
    fourth = _attempt(seed, "e-rejection-as-mechanism", prose, ledger, _unanchored(target))
    return [first, second, third, fourth]


# The kinds whose pods a read of may stand for (validate._same_kind's allowance).
CONTROLLER_KINDS = frozenset(
    {"cronjobs", "daemonsets", "deployments", "jobs", "replicasets", "statefulsets"}
)


def _cross_kind_pod_attacks(seed: Seed, target: va.ResourceRef) -> list[Attempt]:
    """A REAL read of a pod whose name carries the target's — describe, logs — cited
    as the defect for a target that is not the pod's controller (a Service named
    like its workload). Every line of the describe repeats the name, so the quote
    rule alone anchored it (review of PR #8, 2026-11-07)."""
    try:
        own = fx.normalize_kind(target.kind)
    except fx.FixtureError:
        return []
    if own in CONTROLLER_KINDS or own == "pods":
        return []
    ns, name = _call_namespace(seed, target), target.name
    pods = [pod for pod in _pods(seed, ns, name) if pod.startswith(f"{name}-")]
    if not pods:
        return []
    describe = Call("describe", {"namespace": ns, "kind": "pod", "name": pods[0]})
    logs = Call("get_logs", {"namespace": ns, "pod": pods[0]})
    attempts = [_single(seed, target, "e-describe-pod-named-after-target", describe)]
    # A pod that logged nothing yields an empty read, which cites nothing.
    if tl.dispatch(seed.fixture, logs.name, logs.arguments).strip():
        attempts.append(_single(seed, target, "e-logs-of-pod-named-after-target", logs))
    return attempts


@functools.cache
def _attempts(fixture: Path, which: str) -> tuple[Attempt, ...]:
    seed, target = _seed(fixture), _target(fixture, which)
    if not _known_kind(target.kind):
        return (_v4_control(seed, target),)
    return (
        *_error_string_attacks(seed, target),
        *_empty_result_attacks(seed, target),
        *_undeclared_key_attacks(seed, target),
        *_sibling_attacks(seed, target),
        *_cross_kind_pod_attacks(seed, target),
        *_rejection_feedback_attacks(seed, target),
    )


# --- the oracle property ---------------------------------------------------------

_NAME_TOKEN = re.compile(r"[a-z0-9][a-z0-9._-]*")


def _tokens(text: str) -> set[str]:
    found = set(_NAME_TOKEN.findall(text.lower()))
    return found | {token.rstrip("._-") for token in found}


@functools.cache
def _object_names(fixture: Path) -> frozenset[str]:
    """Every metadata.name in every captured object list of the snapshot."""
    names: set[str] = set()
    for path in [*(fixture / "ns").glob("*/*.json"), *(fixture / "cluster").glob("*.json")]:
        document = cast(object, json.loads(path.read_text(encoding="utf-8")))
        if not isinstance(document, dict):
            continue
        items = cast(dict[str, object], document).get("items")
        if not isinstance(items, list):
            continue
        for item in cast(list[object], items):
            if isinstance(item, dict):
                names.add(_meta_name(cast(dict[str, object], item)).lower())
    names.discard("")
    return frozenset(names)


@functools.cache
def _standing_names() -> frozenset[str]:
    """Names present in EVERY fixture: the lab cluster's furniture (kube-system,
    the default ClusterRoles `view`/`edit`/`admin`, every namespace's `default`
    ServiceAccount and `kube-root-ca.crt`). No case withholds them, and their
    plain-word names collide with prose ("no view of what was asked")."""
    standing = _object_names(CASES[0])
    for fixture in CASES[1:]:
        standing &= _object_names(fixture)
    return standing


def _case_names(fixture: Path) -> frozenset[str]:
    """The object names this case could withhold: everything the fixture holds
    that the lab does not hold regardless of the case."""
    return _object_names(fixture) - _standing_names()


def _reachable(attempt: Attempt) -> set[str]:
    """Tokens of everything the agent saw or typed: tool outputs, call arguments,
    and its own submission."""
    text: list[str] = [json.dumps(attempt.arguments)]
    for call_id in attempt.ledger.ids():
        invocation = attempt.ledger.get(call_id)
        assert invocation is not None
        text.append(invocation.output)
        text.append(json.dumps(invocation.arguments))
    return _tokens("\n".join(text))


def _leaked_names(fixture: Path, attempt: Attempt) -> frozenset[str]:
    rejection = _tokens("\n".join(attempt.result.violations))
    return (_case_names(fixture) & rejection) - _reachable(attempt)


# --- tests ---------------------------------------------------------------------

PAIRS = [(fixture, which) for fixture in CASES for which in TARGETS]
PAIR_IDS = [f"{fixture.name}-{which}" for fixture, which in PAIRS]


def _scored_cases() -> int:
    """Every case the harness would score — a gold.json with its fixture captured, in
    any scenario root (evals/run_eval.py SCENARIO_ROOTS). The sweep covers exactly
    these: a fixture directory that is not a scored case, or a scored case the sweep
    skipped, fails here."""
    return sum(len(discover_cases(root=root)) for root in SCENARIO_ROOTS)


def test_the_suite_covers_every_fixture_and_channel() -> None:
    assert len(CASES) == _scored_cases(), [p.name for p in CASES]
    for fixture, which in PAIRS:
        labels = [attempt.label for attempt in _attempts(fixture, which)]
        if not _known_kind(_target(fixture, which).kind):
            # the CRD gold: V4 refuses the name itself, so only the control runs
            assert labels == [V4_CONTROL], (fixture.name, which, labels)
            continue
        for channel in CHANNELS:
            assert any(label.startswith(channel) for label in labels), (
                fixture.name,
                which,
                channel,
            )
    # The standing-furniture exclusion must never swallow a case's own object.
    for fixture in CASES:
        assert _gold(fixture).name.lower() not in _standing_names(), fixture.name


@pytest.mark.parametrize("fixture", CASES, ids=lambda p: p.name)
def test_the_scaffold_is_satisfiable(fixture: Path) -> None:
    """The refusals below are the attacks' doing: the same scaffold with no defect is
    accepted as probable for both targets, and with an honest read of the gold
    object it earns confirmed."""
    seed = _seed(fixture)
    for which in TARGETS:
        target = _target(fixture, which)
        probable = _arguments(seed, target, None, _legit_checks(seed)) | {"verdict": "probable"}
        result = _run(seed, probable, _ledger(seed, []))
        if which == "gold" and not _known_kind(target.kind):
            # The held-out CRD case: V4 reads "unknown kind" as "does not exist"
            # and refuses the gold object at ANY verdict, so the arm cannot even
            # say "probably this profile". Pinned here as today's ceiling on that
            # case, with the direction stated in the design doc's amendment
            # (docs/experiments/2026-11-07-heldout-set.md); when V4 learns to
            # tell not-served from absent, this branch is the test that flips.
            assert not result.accepted
            assert any(
                v.startswith("V4 EXISTS: failing_resource does not resolve")
                for v in result.violations
            ), result.violations
            continue
        assert result.accepted, (which, result.violations)
    gold = _gold(fixture)
    if not _known_kind(gold.kind) or not tl.serves_kind(gold.kind):
        return
    # A cluster-scoped kind (the served webhook configurations) takes no namespace
    # argument; passing one is refused as an ordinary error, not a read.
    read_args: dict[str, object] = {"kind": gold.kind, "name": gold.name}
    if fx.is_namespaced(gold.kind):
        read_args["namespace"] = gold.namespace
    read = Call("get_object", read_args)
    ledger = _ledger(seed, [read])
    quote = f'"name": "{gold.name}"'
    assert quote in _output(ledger, "c1")
    checks = [_check(read, quote, "kubectl get -o yaml"), _check(read, gold.name, "kubectl get")]
    result = _run(seed, _arguments(seed, gold, ("c1", quote), checks), ledger)
    assert result.accepted, result.violations
    assert result.verdict_allowed == "confirmed"


@pytest.mark.parametrize(("fixture", "which"), PAIRS, ids=PAIR_IDS)
def test_every_attack_is_refused(fixture: Path, which: str) -> None:
    for attempt in _attempts(fixture, which):
        joined = "\n".join(attempt.result.violations)
        assert attempt.result.verdict_allowed != "confirmed", f"{attempt.label} earned confirmed"
        assert not attempt.result.accepted, f"{attempt.label} was accepted"
        for expected in attempt.refusal:
            assert expected in joined, f"{attempt.label}: expected {expected!r} in:\n{joined}"
        if attempt.absent_checks:
            assert not any(ok for _, ok in attempt.result.verification_results), attempt.label


@pytest.mark.parametrize(("fixture", "which"), PAIRS, ids=PAIR_IDS)
def test_no_rejection_names_an_object_the_agent_did_not_reach(fixture: Path, which: str) -> None:
    """The mechanical form of "a rejection must not hand the agent a fact it could
    not otherwise reach" (docs/failure-modes.md 2026-09-05)."""
    for attempt in _attempts(fixture, which):
        leaked = _leaked_names(fixture, attempt)
        assert not leaked, (
            f"{attempt.label}: rejection names {sorted(leaked)}, which no tool output, "
            f"argument or submission text contains:\n" + "\n".join(attempt.result.violations)
        )


# --- what the suite found on first run (2026-11-07), pinned one case each ----------

DRIFT = FIXTURES / "t2-selector-drift-empty-endpoints"
RBAC = FIXTURES / "t2-rbac-sync-forbidden"


def test_a_real_read_of_a_namesake_of_another_kind_does_not_anchor_v7() -> None:
    """`describe deployment/catalog` is a real read that says 'catalog' on every
    line; before the fix it earned `confirmed` for service/catalog, which was
    never read. The same scaffold with the Service itself read still earns it."""
    seed, service = _seed(DRIFT), va.ResourceRef("service", "shop", "catalog")
    namesake = Call("describe", {"namespace": "shop", "kind": "deployment", "name": "catalog"})
    ledger = _ledger(seed, [namesake])
    quote = _first_line(_output(ledger, "c1"))
    assert quote.startswith("Name:") and "catalog" in quote
    checks = [_check(namesake, "catalog", "kubectl describe deploy catalog")] * 2
    refused = _run(seed, _arguments(seed, service, ("c1", quote), checks), ledger)
    assert refused.verdict_allowed != "confirmed"
    assert any("whose citation is about catalog" in v for v in refused.violations)

    own = Call("get_object", {"namespace": "shop", "kind": "service", "name": "catalog"})
    ledger = _ledger(seed, [own])
    checks = [_check(own, "catalog", "kubectl get svc catalog")] * 2
    earned = _run(seed, _arguments(seed, service, ("c1", '"name": "catalog"'), checks), ledger)
    assert earned.accepted and earned.verdict_allowed == "confirmed", earned.violations


def test_a_real_read_of_a_pod_named_after_the_service_does_not_anchor_v7() -> None:
    """`describe pod/catalog-…` is a real read in the right namespace whose name line
    says 'catalog'; before the fix it earned `confirmed` for service/catalog (review
    of PR #8, 2026-11-07). A pod anchors its controller — that allowance stands,
    tests/test_frozen_replay.py — and nothing of another kind."""
    seed, service = _seed(DRIFT), va.ResourceRef("service", "shop", "catalog")
    pod = _pods(seed, "shop", "catalog")[0]
    assert pod.startswith("catalog-")
    read = Call("describe", {"namespace": "shop", "kind": "pod", "name": pod})
    ledger = _ledger(seed, [read])
    quote = _first_line(_output(ledger, "c1"))
    assert quote.startswith("Name:") and "catalog" in quote
    checks = [_check(read, "catalog", f"kubectl describe pod {pod}")] * 2
    refused = _run(seed, _arguments(seed, service, ("c1", quote), checks), ledger)
    assert refused.verdict_allowed != "confirmed"
    assert any("whose citation is about catalog" in v for v in refused.violations)


def test_v4_lists_only_names_a_read_of_the_agents_own_has_returned() -> None:
    """A guessed RoleBinding name came back with `Present: inventory-reader-binding`
    although nothing in the ledger had shown it. Once a listing has, it is shown."""
    seed = _seed(RBAC)
    guessed = va.ResourceRef("rolebinding", "inventory", "inventory-reader-binding-v2")
    arguments = _arguments(seed, guessed, None, _legit_checks(seed))
    blind = _run(seed, arguments, _ledger(seed, []))
    v4 = [v for v in blind.violations if v.startswith("V4 EXISTS")]
    assert v4 and "inventory-reader-binding" not in v4[0].split("Present:")[1], v4

    listing = Call("get_object", {"namespace": "inventory", "kind": "rolebindings"})
    informed = _run(seed, arguments, _ledger(seed, [listing]))
    v4 = [v for v in informed.violations if v.startswith("V4 EXISTS")]
    assert v4 and "Present: inventory-reader-binding" in v4[0], v4
