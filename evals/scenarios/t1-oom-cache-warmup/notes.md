# t1-oom-cache-warmup — scenario notes

**Tier:** T1 (textbook single fault). **Status:** authored 2026-08-29;
captured and scored in the frozen case set (fixtures:
`evals/fixtures/t1-oom-cache-warmup/`).

**Namespaces owned:** `recs` (authoring contract rule 4).

**Provenance:** authored from roster row #3; applied via `inject.sh --id
t1-oom-cache-warmup` against the live kind cluster at capture.

**Ground truth (informal — the formal, scored version is `gold.json` in this
directory, per `evals/scoring.md`):**

- Failing resource: `recs/deployment/recommendations`
- Fault class: OOM kill — the warmup working set exceeds the memory limit
- Mechanism: the entrypoint's warmup step materializes a ~200MiB in-memory
  catalog cache (`dd` 200×1MiB of zeros → `tr` → command substitution held
  in the foreground shell's `CACHE` variable) under
  `resources.limits.memory: 64Mi`; the cgroup limit is crossed mid-warmup
  and the kernel OOM-kills the container (OOMKilled, exit 137) on every
  start.
- Decisive evidence: `lastState.terminated.reason: OOMKilled` / exit code
  137 with climbing restartCount in containerStatuses; the 64Mi limit in
  the container spec; logs show `warming catalog cache` but never
  `catalog cache ready`.
- Remediation: raise the container memory limit (gold: 1Gi limit, 256Mi
  requests via `kubectl set resources`) and let the rollout complete.

**Wait condition:** pod with label `app=recommendations` in `recs` reaches
`restartCount >= 2` with `lastState.terminated.reason == OOMKilled` (or
lastState exit code 137). `wait.sh` gates on exactly this via jsonpath —
never on a coarse waiting reason (e.g. CrashLoopBackOff alone) that a
different failure could also produce.

**Why deterministic (host-independent in kind):** the allocation is fixed
(200×1MiB from `/dev/zero`) and needs no network, disk, or image pull
(`busybox:1.36` is node-cached — nothing pulls from Docker Hub). The 64Mi
cgroup limit is crossed during the command substitution on every start; at
that moment the shell's substitution buffer is by far the largest RSS in the
container's cgroup, so the OOM kill lands on the container's PID 1 (and on
cgroup v2 the kubelet's group-kill takes the whole cgroup regardless),
yielding OOMKilled/137 each time. The fault manifests within seconds of
each container start; restartCount >= 2 arrives well inside wait.sh's 300s
budget (first backoffs are 0s/10s).

**Counterfactual design (rule 1):** the allocation IS what the OOM kill
hits, and the same foreground shell that holds `CACHE` then serves. With a
corrected limit the warmup completes, `catalog cache ready` logs, and the
`while` serve loop keeps the container Running (and Ready — no probes
defined). An allocate-then-exit script was deliberately avoided: it would
exit 0 and crash-loop even when healthy, violating rule 1. Remediation
headroom: peak usage during substitution + variable assignment is roughly
2× the 200MiB payload (~400-600MiB transiently), hence gold's 1Gi limit —
256Mi requests need not cover the transient peak, the limit does.

**Gold-side asymmetry:** none — single workload; the deployment's own
resources block is the only defensible spec change.

**Counterfactual-verification record (rule 6):** **2026-08-29 ~02:05 IST** —
`inject.sh --no-capture` manifested the fault (wait.sh: `restartCount=2
lastState.terminated.reason=OOMKilled exitCode=137`); applied gold's
remediation (`kubectl -n recs set resources deployment/recommendations
--limits=memory=1Gi --requests=memory=256Mi`); recovery observed:
`deployment "recommendations" successfully rolled out`, logs showed
`catalog cache ready` then two `serving recommendations` iterations 30s
apart, containerStatuses `0 restarts, ready=true` — the transient
substitution peak fits the 1Gi limit as designed. Wiped and re-injected
cleanly for the pristine capture. Adversarial verification substituted by
operator review (the verifier agent run was cut short by an API usage
limit — disclosed).
