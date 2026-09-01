# t1-unschedulable-cpu-requests — scenario notes

**Tier:** T1 (textbook single fault). **Status:** authored; captured and
scored in the frozen case set (fixtures:
`evals/fixtures/t1-unschedulable-cpu-requests/`).

**Provenance:** authored from `fault.yaml` per the scenario contract; applied
only via `evals/inject.sh --id t1-unschedulable-cpu-requests`.

**Namespaces owned:** `fraud` (the only namespace this scenario creates).

**Ground truth (informal — the formal, scored version is `gold.json` in this
directory, per `evals/scoring.md`):**

- Failing resource: `fraud/deployment/fraud-scoring`
- Fault class: pod unschedulable — cpu request no node can satisfy
- Mechanism: the scorer container requests `cpu: "512"` (512 whole cores);
  the scheduler's fit predicate fails on every node, the pod never leaves
  Pending, and the scheduler emits FailedScheduling `Insufficient cpu` on
  each attempt.
- Decisive evidence: PodScheduled condition `False/Unschedulable` on the
  fraud-scoring pod plus the FailedScheduling event message
  `0/1 nodes are available: 1 Insufficient cpu` (single-node kind cluster,
  allocatable cpu 6).
- Remediation: lower the cpu request to a schedulable value (pod template
  resources are mutable on a Deployment; see `gold.json`).

**Wait condition:** pod with label `app=fraud-scoring` in `fraud` has
condition PodScheduled `False` with reason `Unschedulable` AND a
FailedScheduling event for that pod whose message contains
`Insufficient cpu` (`wait.sh`; 5s poll, 300s cap).

**Why deterministic (host-independent by construction):**

- The request is `cpu: "512"` — 512 whole cores, not millicores. No real
  machine this could run on satisfies it (the largest cloud hosts top out
  well below 512 allocatable cores; the kind node has 6), so the outcome
  cannot flip on a bigger workstation — the red-team rule that bans values
  like 32.
- Scheduling is a pure predicate (sum of requests vs node allocatable):
  no timing, no races, no admission ordering. There is no quota or
  LimitRange in the namespace; nothing else competes for the decision.
- The image is never pulled (the pod never reaches a node), so registry
  behavior cannot leak into the fixture; `busybox:1.36` is node-cached
  anyway, so the counterfactual recovery pulls nothing either.
- The pod stays Pending indefinitely and the scheduler re-emits
  FailedScheduling on retry, so the decisive evidence is present whenever
  capture runs after `wait.sh` manifests — no capture-timing window.

**Gold-side asymmetry:** none needed — a single object (the deployment's own
pod template request) is the only spec that can defensibly change.

**Counterfactual verification (contract rule 1 + 6):** the container command
is an unconditional serve loop; with the request corrected it schedules and
runs, so the fault lives entirely in `resources.requests`. Live rehearsal
record: **2026-08-29 ~01:55 IST** — ran `inject.sh --no-capture`; wait.sh
confirmed `PodScheduled=False/Unschedulable` + FailedScheduling event
mentioning `Insufficient cpu`; applied the `gold.json` remediation
(`kubectl -n fraud set resources deployment fraud-scoring
--requests=cpu=250m`); recovery observed: `deployment "fraud-scoring"
successfully rolled out` and the pod logged `score server starting` /
`scored transaction batch`; then wiped and re-injected cleanly for the real
capture. Adversarial verification of this scenario was substituted by
operator review (its verifier agent run was cut short by an API usage
limit — disclosed).
