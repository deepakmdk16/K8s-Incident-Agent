# Incident report — t1-crashloop-missing-env

## Root cause

**Verdict: confirmed.**

The Deployment `payments/checkout-worker` ships a pod template whose container command hard-requires the environment variable `AMQP_URL`, but the template defines **no environment variables at all** (`Environment: <none>`). On every container start the guard `[ -n "${AMQP_URL:-}" ] || { echo "FATAL: AMQP_URL not set"; exit 1; }` evaluates false, the shell exits 1 before entering the consume loop, and the kubelet restarts it under an ever-growing backoff. The Deployment therefore never gets a Ready pod (`0/1`), the queue consumer never runs, and checkout jobs are never drained — which is exactly the paged symptom.

The failing resource is the **Deployment** (its pod template spec must change), not the pod it produced — deleting the pod just yields an identical crashing replacement.

## Evidence chain

- **Symptom, `kubectl get all -A`:** `payments pod/checkout-worker-66bfcdfc47-d9gdj 0/1 CrashLoopBackOff 5 (74s ago) 4m17s` and `deployment.apps/checkout-worker 0/1 1 0` — 0 Ready replicas, matching the alert.
- **Exit mechanism, `kubectl logs ... -c worker`:** the single log line is `FATAL: AMQP_URL not set`. The `--previous` invocation prints the *same* line, so this is not a one-off — every container incarnation dies the same way.
- **Exit status, describe of pod `checkout-worker-66bfcdfc47-d9gdj`:** `Last State: Terminated / Reason: Error / Exit Code: 1`, with `Started` and `Finished` both at `07:52:54` — the process lives well under a second, i.e. it dies at the guard clause, before `sleep 10`.
- **The guard itself, describe of pod (Command):** `[ -n "${AMQP_URL:-}" ] || { echo "FATAL: AMQP_URL not set"; exit 1; }; echo "connected to queue at ${AMQP_URL}"; ...` — the string in the log is emitted only on that branch, so the log line uniquely identifies which condition failed.
- **The missing input, describe of pod:** `Environment:    <none>` in the container spec. No env vars, no `envFrom`, and `Volumes:` lists only `kube-api-access-shsf7` (the projected SA token) — so there is no ConfigMap/Secret mount that could supply it either.
- **Defect lives in the template, not the pod, describe of deployment `checkout-worker`:** the Pod Template shows the identical `Command` and identical `Environment:   <none>`. Confirmed again in describe of ReplicaSet `checkout-worker-66bfcdfc47`, same template. So any replacement pod inherits the same defect.
- **Deployment-level consequence, describe of deployment:** `Available  False  MinimumReplicasUnavailable` and `1 desired | 1 updated | 1 total | 0 available | 1 unavailable`.
- **Restart loop, describe of pod Events:** `Pulled/Created/Started (x6 over 4m27s)` followed by `Warning BackOff ... Back-off restarting failed container worker` — repeated successful starts, repeated immediate failures.

## Investigation ledger

- **Image pull / bad image (ImagePullBackOff, wrong tag):** ruled out — event `Container image "busybox:1.36" already present on machine and can be accessed by the pod`, and the container reaches `Started` six times with a resolved `Image ID` digest.
- **Scheduling failure (resources, taints, nodeSelector):** ruled out — `PodScheduled True`, `Successfully assigned payments/checkout-worker-66bfcdfc47-d9gdj to incident-lab-control-plane`, pod has an IP `10.244.0.153`, and `Node-Selectors: <none>`.
- **OOMKill / resource limits:** ruled out — `QoS Class: BestEffort` (no requests/limits set) and `Last State: Terminated / Reason: Error`, not `OOMKilled`.
- **Failing readiness/liveness probe restarting a healthy process:** ruled out — the describe output declares no probes, and the container self-terminates with exit code 1 within the same second it starts; a probe kill would show `Reason: Error` only after the probe period and would not produce that log line.
- **Dependency outage (broker/DNS unreachable, so the worker can't connect):** ruled out — the process never attempts a connection. It exits at the variable check *before* `echo "connected to queue at ${AMQP_URL}"`, and that "connected" line never appears in the logs. Supporting: both `coredns` pods are `1/1 Running`, `kindnet` and `kube-proxy` DaemonSets are `1/1`, and the control plane pods are all Running with 0 restarts — the cluster substrate is healthy.
- **A missing Secret/ConfigMap reference that fails to mount:** ruled out — a broken `envFrom`/`secretKeyRef` would surface as `CreateContainerConfigError` with a `FailedMount`/`Error: secret not found` event, not a running-then-exiting container. Events show clean `Created`/`Started`, and the template references no such source at all (`Environment: <none>`, no extra volumes). The variable is simply absent, never "failed to resolve".
- **Stale/rolled-back ReplicaSet serving old config:** ruled out — `OldReplicaSets: <none>`, `deployment.kubernetes.io/revision: 1`, single ReplicaSet `checkout-worker-66bfcdfc47`. This is the first and only revision; the Deployment was created broken (age `4m17s`).
- **Node-level pressure / kubelet fault:** ruled out — every other pod on `incident-lab-control-plane` is `Running` with `0` restarts over 10h; only this workload is affected.

## Verification recipe

```bash
# 1. Prove the template carries no env var (expect empty / null output):
kubectl get deploy checkout-worker -n payments \
  -o jsonpath='{.spec.template.spec.containers[0].env}{"\n"}{.spec.template.spec.containers[0].envFrom}{"\n"}'

# 2. Prove the crash is the guard clause, and that it repeats identically:
kubectl logs -n payments -l app=checkout-worker --previous --tail=5
kubectl get pod -n payments -l app=checkout-worker \
  -o jsonpath='{.items[0].status.containerStatuses[0].lastState.terminated.reason}{" exit="}{.items[0].status.containerStatuses[0].lastState.terminated.exitCode}{"\n"}'

# 3. Confirm the fix hypothesis in-place (proves causation, then roll forward properly via GitOps):
kubectl set env deployment/checkout-worker -n payments AMQP_URL='amqp://<user>:<pass>@<broker-host>:5672/<vhost>'
kubectl rollout status deployment/checkout-worker -n payments --timeout=60s
kubectl logs -n payments -l app=checkout-worker --tail=5   # expect "connected to queue at ..." then "consuming checkout jobs"
```

**Remediation.** Short term: inject `AMQP_URL` into the Deployment's pod template. Do not hardcode credentials — reference a Secret:

```yaml
        env:
          - name: AMQP_URL
            valueFrom:
              secretKeyRef:
                name: checkout-broker-credentials
                key: amqp-url
```

Create/confirm that Secret exists in `payments` first (`kubectl get secret -n payments`), then apply the manifest change through the normal source-of-truth pipeline so the `kubectl set env` hotfix isn't reverted by the next reconcile. Follow-ups: add a readiness probe and CI validation that every env var referenced by a container command is declared in the template, so a config-only regression can't ship as a first revision again.

```json
{
  "case_id": "t1-crashloop-missing-env",
  "failing_resource": {"kind": "Deployment", "namespace": "payments", "name": "checkout-worker"},
  "mechanism": "The checkout-worker Deployment's pod template declares no environment variables (Environment: <none>) while its container command requires AMQP_URL, so the startup guard '[ -n \"${AMQP_URL:-}\" ] || { echo \"FATAL: AMQP_URL not set\"; exit 1; }' fails and the shell exits 1 within a second of every start. The kubelet loops the container into CrashLoopBackOff, so the Deployment never has a Ready replica and no consumer ever attaches to the checkout queue. Because the defect is in the template itself, every replacement pod fails identically.",
  "verdict": "confirmed"
}
```