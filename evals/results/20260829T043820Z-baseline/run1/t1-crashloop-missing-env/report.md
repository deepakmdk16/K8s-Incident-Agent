# Incident Report — t1-crashloop-missing-env

## Root cause

Deployment `payments/checkout-worker` ships a container command that **requires** the environment variable `AMQP_URL`, but the pod template defines **no environment variables at all**. On every start the container's guard clause `[ -n "${AMQP_URL:-}" ] || { echo "FATAL: AMQP_URL not set"; exit 1; }` evaluates false, prints the fatal message, and exits 1. The kubelet restarts it, it fails identically, and the pod settles into `CrashLoopBackOff` — so the Deployment never reaches 1 Ready replica, no consumer attaches to the checkout queue, and merchant checkouts time out at the payment step.

Verdict: **confirmed**. The container's own log line names the exact missing variable, and the Deployment spec shows that variable is absent.

## Evidence chain

1. **The paged symptom is real and belongs to this workload.** From `kubectl get all -A`:
   `deployment.apps/checkout-worker   0/1   1   0   4m17s` — 1 desired, 0 available.
   And `kubectl describe deployment.apps/checkout-worker -n payments`:
   `Replicas: 1 desired | 1 updated | 1 total | 0 available | 1 unavailable`
   `Available   False   MinimumReplicasUnavailable`

2. **The single pod is crash-looping, not pending/unschedulable.** From `get all -A`:
   `pod/checkout-worker-66bfcdfc47-d9gdj   0/1   CrashLoopBackOff   5 (74s ago)   4m17s`
   From `describe pod`: `PodScheduled True`, `Initialized True`, and a node is assigned (`Node: incident-lab-control-plane/172.18.0.2`).

3. **The container exits immediately with a fatal error, not OOM/signal.** From `describe pod`:
   ```
   Last State:     Terminated
     Reason:       Error
     Exit Code:    1
     Started:      Sat, 29 Aug 2026 07:52:54 +0530
     Finished:     Sat, 29 Aug 2026 07:52:54 +0530
   ```
   Started and Finished share the same second — sub-second lifetime, consistent with failing a startup guard before doing any work.

4. **The application itself names the cause.** `kubectl logs ... --tail=50`:
   `FATAL: AMQP_URL not set`
   The identical line appears in `--previous`, proving this is the repeating failure mode across restarts, not a one-off.

5. **The log line maps directly to the container command.** From `describe pod` → `Command`:
   `[ -n "${AMQP_URL:-}" ] || { echo "FATAL: AMQP_URL not set"; exit 1; }; echo "connected to queue at ${AMQP_URL}"; ...`
   The `exit 1` in that guard is the only path producing exit code 1 with that message — matching evidence item 3 exactly.

6. **The variable is genuinely absent from the spec, at the owning workload level.** All three renderings agree:
   - `describe pod` → `Environment:    <none>`
   - `describe replicaset.apps/checkout-worker-66bfcdfc47` → `Environment:   <none>`
   - `describe deployment.apps/checkout-worker` → `Environment:   <none>`

   Because the Deployment's own pod template carries `Environment: <none>`, the defect is in the Deployment spec — the ReplicaSet and pod merely inherit it. That is the resource that must change.

7. **Nothing was going to inject it at runtime either.** `describe pod` → `Volumes:` lists only `kube-api-access-shsf7` (the projected service-account token); there is no ConfigMap, Secret, `envFrom`, or file mount that could supply `AMQP_URL`. `Mounts:` on the container shows only `/var/run/secrets/kubernetes.io/serviceaccount`.

8. **The restart loop is the kubelet backing off a repeatedly failing container, confirming the loop is self-sustaining.** `describe pod` Events:
   `Normal Pulled 84s (x6 over 4m27s) ... Container image "busybox:1.36" already present on machine`
   `Normal Created / Started (x6 over 4m27s)`
   `Warning BackOff 12s (x7 over 4m25s) ... Back-off restarting failed container worker`
   Six start attempts, seven backoffs, zero successes.

## Investigation ledger

- **Image pull failure / bad tag (`ImagePullBackOff`, `ErrImagePull`).** Ruled out. Event: `Pulled ... Container image "busybox:1.36" already present on machine and can be accessed by the pod`, and the pod reached `Running` with a real `Container ID: containerd://496f355b...` and resolved `Image ID: docker.io/library/busybox@sha256:73aaf...`. The image is fine; the process inside it exits.

- **Scheduling failure — no capacity, taints, node selectors, affinity.** Ruled out. `PodScheduled True`, the pod is bound to `incident-lab-control-plane`, and the spec has `Node-Selectors: <none>` with only the two default not-ready/unreachable tolerations. A pod that never scheduled would be `Pending`, not `CrashLoopBackOff`.

- **OOMKill or resource starvation.** Ruled out. `Last State: Terminated / Reason: Error / Exit Code: 1` — an OOMKill reports `Reason: OOMKilled` with exit code 137. Also `QoS Class: BestEffort` with no `Limits` shown, so no memory cap was being enforced against it.

- **Liveness/readiness probe killing a healthy container.** Ruled out. The `describe pod` container block lists no `Liveness:` or `Readiness:` fields, and there are no `Unhealthy` probe-failure events. The container terminates on its own after well under one second.

- **Downstream dependency outage — the AMQP broker itself being down, or DNS failing to resolve it.** Ruled out as the cause of *this* symptom. The guard clause exits *before* any connection attempt is made; the log never reaches `connected to queue at ...`. Additionally, `kube-dns` and both `coredns` pods are `1/1 Running` with 0 restarts, and no broker Service or Pod exists anywhere in `get all -A` — so no network call was even attempted. A broker outage would surface as connection-refused/timeout logs, not `AMQP_URL not set`.

- **A missing ConfigMap or Secret referenced by `envFrom`, causing `CreateContainerConfigError`.** Ruled out. That failure mode blocks the container from ever starting and shows `Reason: CreateContainerConfigError` with a `Failed` event naming the missing object. Here the container starts successfully six times (`Created`/`Started`, `x6`) and there is no such event. The spec shows `Environment: <none>` — nothing is referenced at all, so nothing can be missing-by-reference.

- **A bad rollout that could be fixed by rolling back to a prior good revision.** Ruled out as a remedy path. `describe deployment` shows `deployment.kubernetes.io/revision: 1`, `OldReplicaSets: <none>`, and a single `ScalingReplicaSet ... from 0 to 1` event. This is the first and only revision — there is no earlier working configuration to roll back to; the config was never correct.

- **Cluster-level or control-plane fault.** Ruled out. Every `kube-system` pod (etcd, apiserver, scheduler, controller-manager, kube-proxy, kindnet, coredns) and `local-path-provisioner` are `1/1 Running` with `0` restarts at 10h age. The only unhealthy object in the entire cluster is this one pod.

## Verification recipe

```bash
# 1. Confirm the Deployment pod template defines no env vars (expect: null / empty)
kubectl get deploy checkout-worker -n payments \
  -o jsonpath='{.spec.template.spec.containers[0].env}{"\n"}'

# 2. Confirm the crash message and exit code come from the missing-var guard
kubectl logs -n payments -l app=checkout-worker --tail=5 --previous
kubectl get pod -n payments -l app=checkout-worker \
  -o jsonpath='{.items[0].status.containerStatuses[0].lastState.terminated.reason}{" exit="}{.items[0].status.containerStatuses[0].lastState.terminated.exitCode}{"\n"}'

# 3. Prove causality: inject the var and watch it go Ready
kubectl set env deploy/checkout-worker -n payments AMQP_URL='amqp://<user>:<pass>@<broker-host>:5672/'
kubectl rollout status deploy/checkout-worker -n payments --timeout=90s
```

Step 1 should print an empty result, step 2 should print `FATAL: AMQP_URL not set` and `Error exit=1`, and step 3 should flip the Deployment to `1/1` with the log line `connected to queue at ...` — closing the loop from mechanism to symptom.

**Remediation note:** `kubectl set env` is the fast mitigation, but the durable fix is to add `AMQP_URL` to the Deployment manifest in source control — preferably as a `secretKeyRef` (the value is a credentialed broker URI and should not sit in plain text in the pod spec, where it is visible via `kubectl describe` and echoed into logs by the container's `echo "connected to queue at ${AMQP_URL}"` line). Otherwise the next GitOps sync or redeploy will reintroduce the outage. Worth filing a follow-up to remove the credential-echoing log statement.

```json
{
  "case_id": "t1-crashloop-missing-env",
  "failing_resource": {"kind": "Deployment", "namespace": "payments", "name": "checkout-worker"},
  "mechanism": "The checkout-worker Deployment's pod template defines no environment variables (Environment: <none>), but its container command begins with a guard that exits 1 with 'FATAL: AMQP_URL not set' when AMQP_URL is unset. The container therefore terminates within the same second it starts on every attempt, driving the pod into CrashLoopBackOff so the Deployment holds 0/1 available replicas and no consumer drains the checkout queue.",
  "verdict": "confirmed"
}
```