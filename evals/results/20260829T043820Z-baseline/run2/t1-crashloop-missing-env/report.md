# Incident Report — `t1-crashloop-missing-env`

## Root cause

**Verdict: confirmed.**

The Deployment `payments/checkout-worker` ships a container command that requires the environment variable `AMQP_URL`, but the pod template defines no environment variables at all (`Environment: <none>`). The container's guard clause therefore fails on every start, prints `FATAL: AMQP_URL not set`, and exits 1 immediately. The kubelet restarts it, it fails again, and it settles into `CrashLoopBackOff`. Since the Deployment has a single replica and that replica never becomes Ready, the Deployment reports `0/1` and `Available=False / MinimumReplicasUnavailable` — which is exactly the paged symptom. No checkout jobs are consumed because the consumer process never gets past its first line.

The fix must be applied to the Deployment's pod template (the source of truth), not to the crashing pod.

## Evidence chain

1. **The workload is down, one replica, none ready** — `kubectl get all -A`: `deployment.apps/checkout-worker   0/1   1   0   4m17s`, and `replicaset.apps/checkout-worker-66bfcdfc47   1 desired / 1 current / 0 ready`.
2. **The pod is crash-looping, not pending/unschedulable** — `kubectl get all -A`: `pod/checkout-worker-66bfcdfc47-d9gdj   0/1   CrashLoopBackOff   5 (74s ago)   4m17s`, already assigned `IP 10.244.0.153` on node `incident-lab-control-plane`.
3. **The container's own log states the failure verbatim** — `kubectl logs ... --tail=50`: `FATAL: AMQP_URL not set`. The `--previous` invocation returns the identical line, showing the failure is deterministic across restarts, not intermittent.
4. **The command contains the guard that produces that exact string** — from `describe pod`, `Command: sh -c [ -n "${AMQP_URL:-}" ] || { echo "FATAL: AMQP_URL not set"; exit 1; }; echo "connected to queue at ${AMQP_URL}"; ...`. The `|| { ...; exit 1; }` branch is the only code path that emits this message, so the observed log line proves `AMQP_URL` was empty/unset at process start.
5. **The variable is genuinely absent from the pod** — `describe pod`, container `worker`: `Environment:    <none>`. No `env`, no `envFrom`, no ConfigMap/Secret reference. The only mount is the default service-account projection (`kube-api-access-shsf7`), which supplies no application config.
6. **The absence originates in the Deployment spec, not in pod-level drift** — `describe deployment.apps/checkout-worker` pod template shows the same `Environment:   <none>` and the same guard command, and `describe replicaset.apps/checkout-worker-66bfcdfc47` shows the identical template. The defect is baked into the Deployment, so deleting the pod will reproduce it.
7. **Exit path is an application exit, not a kill** — `describe pod`: `Last State: Terminated, Reason: Error, Exit Code: 1`, with `Started` and `Finished` at the same second (`07:52:54` → `07:52:54`). Exit code 1 is the `exit 1` from the guard.
8. **Kubelet is throttling restarts, confirming the loop** — `describe pod` events: `Warning BackOff ... Back-off restarting failed container worker`, alongside six successful `Pulled`/`Created`/`Started` cycles (`x6 over 4m27s`).
9. **Deployment-level symptom ties back to the alert** — `describe deployment`: `Available  False  MinimumReplicasUnavailable`, `1 unavailable`. This is the condition the workload availability monitor paged on.

## Investigation ledger

- **Image pull failure / wrong image tag** — ruled out. `describe pod` events show `Pulled: Container image "busybox:1.36" already present on machine and can be accessed by the pod` six times, and the container reached `Started` and produced logs. A pull problem would surface as `ErrImagePull`/`ImagePullBackOff`, not `CrashLoopBackOff` with application output.
- **Scheduling failure (resources, taints, node selector, affinity)** — ruled out. `PodScheduled: True`, `Scheduled: Successfully assigned payments/checkout-worker-66bfcdfc47-d9gdj to incident-lab-control-plane`. Pod template has `Node-Selectors: <none>` and only the default not-ready/unreachable tolerations.
- **OOMKill or resource-limit eviction** — ruled out. `Last State: Terminated, Reason: Error, Exit Code: 1` — an OOMKill would report `Reason: OOMKilled` and exit code 137. `QoS Class: BestEffort` also means no memory limit was set to be exceeded.
- **Failing readiness/liveness probe restarting a healthy container** — ruled out. `describe pod` lists no `Liveness`/`Readiness`/`Startup` probe on the `worker` container, and there are no `Unhealthy` probe-failure events. The container is failing on its own before any probe could matter.
- **Missing ConfigMap or Secret blocking startup** — ruled out as the mechanism. A missing referenced ConfigMap/Secret produces `CreateContainerConfigError` and a `Failed`/`FailedMount` event, and the container never starts. Here the container started six times and ran. Moreover, the spec references no ConfigMap or Secret at all (`Environment: <none>`, `Volumes: <none>` in the deployment template) — that absence *is* the bug, not a broken reference.
- **Broker/queue outage (AMQP endpoint unreachable) causing the worker to exit** — ruled out. The code path that would touch the broker (`echo "connected to queue at ${AMQP_URL}"` and the consume loop) is never reached; the process dies on the preceding guard. The log contains only `FATAL: AMQP_URL not set`, with no connection-related output.
- **DNS/networking breakage in the cluster** — ruled out. Both `coredns` pods are `1/1 Running`, `kube-dns` Service is present, `kindnet` and `kube-proxy` DaemonSets are `1/1` ready, and the pod itself has a routable IP and `PodReadyToStartContainers: True`. Regardless, the failure occurs before any network call.
- **Bad rollout / stuck old ReplicaSet during an update** — ruled out. `OldReplicaSets: <none>`, `deployment.kubernetes.io/revision: 1`, `Progressing: True (NewReplicaSetAvailable)`. There is one ReplicaSet at revision 1; this workload has never been healthy, it is not a regression from a previous good revision.
- **Environment variable present but empty (e.g. set to `""` from a Secret key)** — considered and folded into the same fix. `[ -n "${AMQP_URL:-}" ]` fails identically for unset and empty. `describe` shows `Environment: <none>`, so it is unset here; the remediation must still supply a non-empty value.
- **Pod-level manual edit / drift, fixable by deleting the pod** — ruled out. The Deployment and ReplicaSet templates both show `Environment: <none>`, so any replacement pod inherits the same defect.

**Proposed remediation:** patch the Deployment's pod template to inject a valid `AMQP_URL`, sourced from a Secret rather than a literal (it is a broker URL and typically carries credentials):

```bash
# 1. create/verify the secret holding the broker URL
kubectl -n payments create secret generic checkout-worker-amqp \
  --from-literal=AMQP_URL='amqp://<user>:<pass>@<broker-host>:5672/<vhost>'

# 2. wire it into the deployment pod template
kubectl -n payments set env deployment/checkout-worker \
  --from=secret/checkout-worker-amqp

kubectl -n payments rollout status deployment/checkout-worker --timeout=120s
```

Follow-ups worth filing: add a readiness probe so "process alive" is not mistaken for "consuming", and add an admission/CI check rejecting workloads whose entrypoint requires env vars the template does not provide — this class of failure should be caught before it reaches a SEV2 page.

## Verification recipe

```bash
# 1. Confirm the Deployment's own pod template carries no env vars (the defect at its source).
kubectl -n payments get deployment checkout-worker \
  -o jsonpath='{.spec.template.spec.containers[0].env}{"\n"}{.spec.template.spec.containers[0].envFrom}{"\n"}'
#    Expect: two empty lines — nothing is injected.

# 2. Confirm the running container dies on the guard with exit code 1, deterministically.
kubectl -n payments logs deploy/checkout-worker --previous --tail=5
kubectl -n payments get pod -l app=checkout-worker \
  -o jsonpath='{.items[0].status.containerStatuses[0].lastState.terminated.reason}{" "}{.items[0].status.containerStatuses[0].lastState.terminated.exitCode}{"\n"}'
#    Expect: "FATAL: AMQP_URL not set" and "Error 1".

# 3. Positive control — same image + command WITH the variable set stays up.
kubectl -n payments run amqp-probe --rm -it --restart=Never --image=busybox:1.36 \
  --env=AMQP_URL='amqp://probe' -- \
  sh -c '[ -n "${AMQP_URL:-}" ] || { echo "FATAL: AMQP_URL not set"; exit 1; }; echo "connected to queue at ${AMQP_URL}"'
#    Expect: "connected to queue at amqp://probe" and exit 0 — proves the env var is the sole discriminator.
```

```json
{
  "case_id": "t1-crashloop-missing-env",
  "failing_resource": {"kind": "Deployment", "namespace": "payments", "name": "checkout-worker"},
  "mechanism": "The checkout-worker Deployment's pod template declares no environment variables (Environment: <none>), but its container command guards on AMQP_URL and exits 1 when that variable is unset or empty. Every container start therefore prints 'FATAL: AMQP_URL not set' and terminates immediately, driving the pod into CrashLoopBackOff so the single replica never becomes Ready and the Deployment reports 0/1 Available with MinimumReplicasUnavailable.",
  "verdict": "confirmed"
}
```