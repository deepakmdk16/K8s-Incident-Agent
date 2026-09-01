# Incident Report — `t1-crashloop-missing-env`

## Root cause

**Deployment `payments/checkout-worker` is missing the `AMQP_URL` environment variable in its pod template.** The container's startup command begins with a guard — `[ -n "${AMQP_URL:-}" ] || { echo "FATAL: AMQP_URL not set"; exit 1; }` — and since the pod template declares `Environment: <none>` (no `env`, no `envFrom`, no secret/configMap reference), the guard fails immediately, the container exits 1 within the same second it starts, and the kubelet drops it into `CrashLoopBackOff`. The pod never becomes Ready, so the Deployment stays at `0/1 available` and no checkout jobs are consumed from the queue.

Verdict: **confirmed**.

## Evidence chain

- **Symptom matches the page.** `kubectl get all -A`: `deployment.apps/checkout-worker 0/1 1 0 4m17s` and `pod/checkout-worker-66bfcdfc47-d9gdj 0/1 CrashLoopBackOff 5 (74s ago)`. The Deployment describe confirms: `Replicas: 1 desired | 1 updated | 1 total | 0 available | 1 unavailable` with condition `Available False MinimumReplicasUnavailable`.
- **The container itself declares the failure reason.** Log line (both current and `--previous`): `FATAL: AMQP_URL not set`. This is verbatim the string emitted by the guard clause in the container command.
- **The guard exists and exits non-zero.** From `describe pod/checkout-worker-66bfcdfc47-d9gdj`, Command: `sh -c [ -n "${AMQP_URL:-}" ] || { echo "FATAL: AMQP_URL not set"; exit 1; }; echo "connected to queue at ${AMQP_URL}"; ...`. Note the success path (`connected to queue at ...`) never appears in the logs — the process dies at the guard.
- **The variable is genuinely absent, and absent at the source of truth.** `describe pod` shows `Environment: <none>` for container `worker`; `describe replicaset.apps/checkout-worker-66bfcdfc47` shows `Environment: <none>`; `describe deployment.apps/checkout-worker` pod template shows `Environment: <none>`. The omission is baked into the Deployment spec, not lost in translation — so the Deployment is the resource that must change.
- **Failure is instantaneous, i.e. pre-connection, not a runtime/network fault.** `describe pod` Last State: `Terminated, Reason: Error, Exit Code: 1, Started: 07:52:54, Finished: 07:52:54` — zero-second lifetime. A broker connection attempt would take measurable time and produce a different message.
- **Restart loop confirmed by kubelet.** Events: `Started 84s (x6 over 4m27s)` paired with `Warning BackOff 12s (x7 over 4m25s) ... Back-off restarting failed container worker`.
- **No secret/configMap plumbing was even attempted.** Pod `Volumes:` contains only `kube-api-access-shsf7` (the default service-account projection); `Mounts:` only `/var/run/secrets/kubernetes.io/serviceaccount`. So the value is not being supplied by file either.

## Investigation ledger

- **Image pull / bad image tag** — ruled out. Event: `Pulled ... Container image "busybox:1.36" already present on machine and can be accessed by the pod`, plus `Created` and `Started` fired six times. The container runs; it just exits.
- **Scheduling, capacity, or node pressure** — ruled out. `PodScheduled True`, event `Successfully assigned payments/checkout-worker-66bfcdfc47-d9gdj to incident-lab-control-plane`, and pod `Status: Running` on a healthy node. No `Unschedulable` or `FailedScheduling` events.
- **OOMKill / resource limits** — ruled out. `QoS Class: BestEffort` (no requests/limits set) and Last State `Reason: Error, Exit Code: 1`, not `OOMKilled` (137).
- **Missing Secret or ConfigMap referenced by the pod template (`CreateContainerConfigError`)** — ruled out. That failure mode blocks container creation and never produces logs; here containers are `Created`/`Started` and emit application output. Also `Environment: <none>` means nothing is referenced at all — the problem is omission, not a dangling reference.
- **Broker/queue outage or DNS failure reaching the AMQP endpoint** — ruled out. The log stops at the guard; `connected to queue at ...` was never printed, so no connection was ever attempted. Additionally the container lifetime is `Started 07:52:54 / Finished 07:52:54`, too short for any network I/O. CoreDNS is `2/2` Running regardless.
- **Failed rollout / bad new revision to roll back to** — ruled out. `deployment.kubernetes.io/revision: 1`, `OldReplicaSets: <none>`, and only one ScalingReplicaSet event (`from 0 to 1`). There is no prior good revision; the workload has never been healthy.
- **Readiness/liveness probe misconfiguration** — ruled out. `describe pod` lists no `Liveness`/`Readiness` probe lines, and no `Unhealthy` events. Non-readiness is a consequence of the container not running, not of a probe.
- **Control-plane or cluster-wide fault** — ruled out. All `kube-system` and `local-path-storage` pods are `1/1 Running` with `0` restarts; only the `payments` pod is affected.

## Verification recipe

```bash
# 1. Confirm the Deployment template supplies no env at all (expect null / empty).
kubectl get deploy checkout-worker -n payments \
  -o jsonpath='{.spec.template.spec.containers[0].env}{"\n"}{.spec.template.spec.containers[0].envFrom}{"\n"}'

# 2. Confirm the container dies at the guard, exit 1, with no "connected to queue" line.
kubectl logs -n payments -l app=checkout-worker --previous --tail=20
kubectl get pod -n payments -l app=checkout-worker \
  -o jsonpath='{.items[0].status.containerStatuses[0].lastState.terminated.exitCode}{"\n"}'

# 3. Prove causality: inject the variable and watch it go Ready.
kubectl set env deploy/checkout-worker -n payments AMQP_URL='amqp://<user>:<pass>@<broker-host>:5672/<vhost>'
kubectl rollout status deploy/checkout-worker -n payments --timeout=90s
```

**Remediation:** add `AMQP_URL` to the `checkout-worker` pod template, sourced from a Secret rather than a literal — e.g. create/locate the broker credential Secret in `payments` and reference it via `env[].valueFrom.secretKeyRef` (or `envFrom.secretRef`), then re-apply the Deployment through the normal GitOps/manifest path so the fix is not lost on the next deploy. Step 3 above is a valid emergency mitigation but leaves the credential in plaintext on the Deployment object and drifts from source control.

```json
{
  "case_id": "t1-crashloop-missing-env",
  "failing_resource": {"kind": "Deployment", "namespace": "payments", "name": "checkout-worker"},
  "mechanism": "The checkout-worker Deployment's pod template declares no environment variables (Environment: <none>, no env or envFrom), so the required AMQP_URL is never injected into the worker container. The container's startup guard '[ -n \"${AMQP_URL:-}\" ] || { echo \"FATAL: AMQP_URL not set\"; exit 1; }' therefore fails instantly and exits 1, driving CrashLoopBackOff. The pod never reaches Ready, leaving the Deployment at 0/1 available so no checkout jobs are consumed from the queue.",
  "verdict": "confirmed"
}
```