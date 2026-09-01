# Incident Report — `t1-crashloop-missing-env`

## Root cause

**Deployment `payments/checkout-worker` ships a pod template with an empty environment block.** The container's start command hard-fails unless `AMQP_URL` is set:

```
[ -n "${AMQP_URL:-}" ] || { echo "FATAL: AMQP_URL not set"; exit 1; }; ...
```

No `env`, `envFrom`, ConfigMap, or Secret reference exists in the template, so `AMQP_URL` is undefined at runtime. The container exits 1 within milliseconds of start, kubelet restarts it, and the pod settles into `CrashLoopBackOff`. With `replicas: 1` and zero Ready pods, the Deployment reports `0/1 Available` — the exact condition the availability monitor paged on — and no checkout jobs are consumed from the queue.

Verdict: **confirmed**. The application itself names the missing variable in its fatal log line, and the same output shows the variable absent from the workload spec.

## Evidence chain

1. **Symptom matches the page.** `kubectl get all -A`: `deployment.apps/checkout-worker  0/1  1  0  4m17s` — 1 desired, 0 available. `describe deployment` corroborates: `Replicas: 1 desired | 1 updated | 1 total | 0 available | 1 unavailable` with condition `Available  False  MinimumReplicasUnavailable`.
2. **The single pod is crash-looping, not pending or unschedulable.** `kubectl get all -A`: `pod/checkout-worker-66bfcdfc47-d9gdj  0/1  CrashLoopBackOff  5 (74s ago)  4m17s`.
3. **The container fails instantly, at startup, with a nonzero exit.** `describe pod`, Last State: `Terminated / Reason: Error / Exit Code: 1 / Started: 07:52:54 / Finished: 07:52:54` — start and finish in the same second, i.e. it dies before doing any work.
4. **The application states the cause itself.** Current and previous logs are identical and consist of exactly one line: `FATAL: AMQP_URL not set`. Both `--tail=50` and `--previous --tail=50` return only this, so no other failure precedes it.
5. **That log line is emitted by the guard in the pod's own command.** `describe pod`, Command: `[ -n "${AMQP_URL:-}" ] || { echo "FATAL: AMQP_URL not set"; exit 1; }` — the `${AMQP_URL:-}` expansion means empty/unset both trigger `exit 1`, matching Exit Code 1 exactly.
6. **The variable is genuinely absent from the pod, at every level of ownership.** `describe pod` → `Environment:  <none>`. `describe replicaset checkout-worker-66bfcdfc47` pod template → `Environment:  <none>`. `describe deployment checkout-worker` pod template → `Environment:  <none>`. The defect originates in the Deployment spec and is faithfully propagated down; the ReplicaSet and pod are correct renderings of a broken template.
7. **Nothing else could have supplied it.** The pod mounts only `kube-api-access-shsf7` (`Mounts:` lists no config/secret volume), and `Volumes:` on the Deployment and ReplicaSet templates is `<none>` — so no ConfigMap/Secret is wired in via `envFrom` or file.
8. **Only one revision has ever existed** — `deployment.kubernetes.io/revision: 1`, `OldReplicaSets: <none>`, a single `ScalingReplicaSet ... from 0 to 1` event. This is a broken-from-birth deployment (age 4m17s), not a regression from a previously working config.

## Investigation ledger

- **Image pull failure / wrong image tag** — ruled out. `describe pod` events show `Pulled: Container image "busybox:1.36" already present on machine`, six times, with no `ErrImagePull`/`ImagePullBackOff`, and the container reaches `Created` → `Started` each cycle.
- **Scheduling problem (resources, taints, node selector, capacity)** — ruled out. `PodScheduled: True`, `Successfully assigned payments/checkout-worker-66bfcdfc47-d9gdj to incident-lab-control-plane`, and the pod holds an IP (`10.244.0.153`). Nothing is Pending.
- **OOMKill or resource limits** — ruled out. Termination reason is `Error` with exit code 1, not `OOMKilled`; `QoS Class: BestEffort` means no limits are set to be exceeded.
- **Failing liveness/readiness probe restarting the container** — ruled out. No probes appear in the container spec, and there are no `Unhealthy`/probe-failed events; the container terminates itself with exit 1 and prints its own fatal message.
- **Broker/queue unreachable — RabbitMQ down, DNS failure, or NetworkPolicy blocking egress** — ruled out. The guard aborts *before* any connection is attempted; the success path (`echo "connected to queue at ${AMQP_URL}"`) never prints. Separately, CoreDNS is `2/2` Running and kube-proxy/kindnet are healthy, and no broker workload exists anywhere in `get all -A` — but that is beside the point, since the process never gets far enough to dial out.
- **Missing ConfigMap/Secret causing the pod to fail to start** — ruled out as the *mechanism*. That failure mode presents as `CreateContainerConfigError` and the pod never starting; here the container starts cleanly six times (`Created`/`Started` x6). The reference is not broken — it is simply absent (`Environment: <none>` at all three levels).
- **RBAC / ServiceAccount token issue** — ruled out. `Service Account: default` with the projected token mounted, and `PodReadyToStartContainers: True`; the failure occurs in the shell guard, not in an API call.
- **Bad rollout that should be rolled back** — ruled out as a remedy. `revision: 1` and `OldReplicaSets: <none>` mean there is no prior good revision to roll back to; the fix must be a forward change.
- **Control-plane or node-level fault** — ruled out. All `kube-system` pods are `1/1 Running` with 0 restarts and 10h uptime; only the `payments` pod is unhealthy.

## Verification recipe

```bash
# 1. Confirm the env var is absent from the Deployment's pod template (expect: null / empty)
kubectl get deploy checkout-worker -n payments \
  -o jsonpath='{.spec.template.spec.containers[0].env}{"\n"}{.spec.template.spec.containers[0].envFrom}{"\n"}'

# 2. Confirm the container's own fatal message and its exit code
kubectl logs -n payments -l app=checkout-worker -c worker --previous --tail=5
kubectl get pod -n payments -l app=checkout-worker \
  -o jsonpath='{.items[0].status.containerStatuses[0].lastState.terminated.reason}{" exit="}{.items[0].status.containerStatuses[0].lastState.terminated.exitCode}{"\n"}'

# 3. Prove causality: inject the variable and watch the pod go Ready
kubectl set env deployment/checkout-worker -n payments \
  AMQP_URL='amqp://<user>:<pass>@<broker-host>:5672/'
kubectl rollout status deployment/checkout-worker -n payments --timeout=90s
```

**Remediation.** Supply `AMQP_URL` to the Deployment — preferably from a Secret, since the URL carries credentials:

```yaml
# payments/checkout-worker, spec.template.spec.containers[0]
envFrom:
  - secretRef:
      name: checkout-worker-amqp   # must contain key AMQP_URL
```

Create the Secret first (`kubectl create secret generic checkout-worker-amqp -n payments --from-literal=AMQP_URL='amqp://...'`), then apply. Step 3 above is the fast mitigation; fold the same change into the source manifest/Helm values so the next deploy does not regress. Follow-ups: add a readiness probe so a bad config surfaces as a failed rollout rather than a silent availability alert, and add a CI check that required env keys referenced by container commands are present in the template.

```json
{
  "case_id": "t1-crashloop-missing-env",
  "failing_resource": {"kind": "Deployment", "namespace": "payments", "name": "checkout-worker"},
  "mechanism": "The checkout-worker Deployment's pod template defines no env or envFrom entries, so the required AMQP_URL variable is unset in the container. The container's start command guards on that variable and exits 1 immediately, printing 'FATAL: AMQP_URL not set', which drives the pod into CrashLoopBackOff and leaves the Deployment at 0/1 available so no checkout jobs are consumed.",
  "verdict": "confirmed"
}
```