## Root cause

**Failing resource: `Deployment payments/checkout-worker` (its pod template).**

The worker container terminates itself on every start because the queue connection string `AMQP_URL` is not present in its environment. The deployment's pod template declares `Environment: <none>` — no env vars, no `envFrom`, no secret/configMap projection, and no volume mounts other than the default service-account token. The container's own startup logic detects the missing variable and exits non-zero (`echo 'FATAL: AMQP_URL not set'; exit 1`) roughly 2 seconds after start. The kubelet restarts it, it fails identically, and the pod enters CrashLoopBackOff (14 restarts). Since the pod never reaches Ready, the Deployment stays `0/1 available` (`MinimumReplicasUnavailable`), no replica consumes the checkout queue, and merchant checkouts time out at the payment step — exactly the paged symptom.

This is a **workload configuration defect, not an infrastructure fault**: image pulls succeed, scheduling succeeds, the node and all control-plane components are healthy.

Verdict: **confirmed**.

One important caveat for the fix (see remediation note at the end of Evidence chain): the running container's command is a hardcoded stub that prints the FATAL line and exits **unconditionally**, so injecting `AMQP_URL` alone will not make this exact pod template succeed — the template's `command` must also be replaced with the real worker entrypoint.

## Root cause

*(section retained per required structure — see above)*

## Evidence chain

1. **Symptom, workload level** — `kubectl get all -A`:
   `payments deployment.apps/checkout-worker  0/1  1  0  47m` → 0 ready replicas, matching the page ("0/1 Ready for over 15 minutes").
2. **Symptom, pod level** — same output:
   `pod/checkout-worker-56d848b6d-tpzjs  0/1  Error  14 (5m49s ago)  47m` → the single pod is repeatedly failing, 14 restarts.
3. **Deployment confirms unavailability is due to the pod, not scaling** — `describe deployment checkout-worker`:
   `Replicas: 1 desired | 1 updated | 1 total | 0 available | 1 unavailable` and condition `Available  False  MinimumReplicasUnavailable`. `Progressing True NewReplicaSetAvailable` shows the rollout itself completed — the ReplicaSet exists and created its pod.
4. **The container exits by its own choice, code 1** — `describe pod`:
   ```
   State:      Terminated
     Reason:   Error
     Exit Code: 1
     Started:  22:08:59   Finished: 22:09:01
   ```
   A 2-second lifetime, matching the `sleep 2` in the command — i.e. it ran to its own failure branch, it was not killed by the platform (no OOMKilled, no `Reason: OOMKilled`, no probe-failure events).
5. **The application states the reason explicitly** — `kubectl logs ... --tail=50`:
   ```
   connecting to queue
   FATAL: AMQP_URL not set
   ```
   This is direct causal evidence: the process names the missing configuration and then exits.
6. **The missing configuration is missing in the pod template** — `describe pod` container `worker`: `Environment:    <none>`, and `Mounts:` lists only `kube-api-access-hpf64` (the default SA token). No `AMQP_URL` is supplied by any mechanism.
7. **The defect lives in the Deployment, not just the pod** — the identical `Environment: <none>` and identical `Command` appear in the pod template of both `describe deployment.apps/checkout-worker` and `describe replicaset.apps/checkout-worker-56d848b6d`. Deleting the pod would reproduce the same failure; the Deployment spec is what must change.
8. **Crash loop is the restart mechanism** — `describe pod` events:
   `Warning BackOff 94s (x44 over 47m) ... Back-off restarting failed container worker`.
9. **Not an image/registry problem** — `Normal Pulled ... Successfully pulled image "busybox:1.36" in 4.523s` and `Container image "busybox:1.36" already present on machine`. No `ErrImagePull` / `ImagePullBackOff`.
10. **Not a scheduling/node problem** — `Normal Scheduled ... Successfully assigned payments/checkout-worker-56d848b6d-tpzjs to incident-lab-control-plane`, `PodScheduled True`, and every kube-system pod plus `kindnet`/`kube-proxy` DaemonSets are `1/1 Running` / fully ready with 0 restarts.
11. **Remediation caveat evidence** — the pod template `Command` is
    `sh -c "echo 'connecting to queue'; sleep 2; echo 'FATAL: AMQP_URL not set'; exit 1"`.
    There is no conditional test of `$AMQP_URL` in that command, so it exits 1 regardless of the environment. A complete fix must (a) supply `AMQP_URL` (ideally `envFrom`/`valueFrom` a Secret in `payments`) **and** (b) restore the real worker image/entrypoint in place of this stub.

**Proposed remediation:** patch `deployment/checkout-worker` in `payments` to inject the queue URL from a Secret and run the real worker binary, e.g.
`kubectl -n payments create secret generic checkout-worker-amqp --from-literal=AMQP_URL='amqp://...'`
then add to the container spec:
```yaml
envFrom:
  - secretRef: {name: checkout-worker-amqp}
```
and replace the placeholder `command` with the production entrypoint. Roll out and confirm `1/1 available`. Follow-up: add a readiness/liveness gate and an alert on `kube_deployment_status_replicas_unavailable` so config regressions are caught before the queue backs up.

## Investigation ledger

| Alternative explanation | Ruled out by |
|---|---|
| **Image pull failure / bad tag** | `Successfully pulled image "busybox:1.36" in 4.523s` and `Container image ... already present on machine`; no `ImagePullBackOff`, status is `Error`, not `Waiting`. |
| **Unschedulable pod (resources, taints, nodeSelector)** | `PodScheduled True`, `Successfully assigned ... to incident-lab-control-plane`; `Node-Selectors: <none>`, QoS `BestEffort` with no resource requests to fail against. |
| **OOMKill / memory limit** | Terminated `Reason: Error`, `Exit Code: 1` — an OOMKill reports `Reason: OOMKilled` with exit code 137. No limits are set (`BestEffort`). |
| **Liveness/readiness probe killing the container** | No probes are defined in the pod template, and there are no `Unhealthy`/`Killing` events. The container's `Started`→`Finished` gap is 2s, matching its own `sleep 2`, not a probe period. |
| **Missing Service / broken networking to the broker** | The failure occurs before any network attempt is even possible — the process aborts on a configuration check (`AMQP_URL not set`) rather than a connection error. CoreDNS is `2/2 Running`, `kindnet` and `kube-proxy` DaemonSets are fully ready, so cluster DNS/networking is healthy. A worker consuming a queue also needs no inbound Service, so its absence from `get all` is expected. |
| **Missing Secret/ConfigMap referenced by the pod (mount would block startup)** | If a referenced ConfigMap/Secret were missing, the pod would sit in `CreateContainerConfigError`/`ContainerCreating` and never run. Here the container *runs* and exits, and `Environment: <none>` / `Mounts:` show nothing is referenced at all — the reference itself was never written into the spec. |
| **Node pressure / kubelet or control-plane fault** | Every kube-system pod (`etcd`, `kube-apiserver`, `kube-controller-manager`, `kube-scheduler`, `kube-proxy`, `kindnet`, both CoreDNS) and `local-path-provisioner` are `1/1 Running` with `0` restarts at 48m; the crash is isolated to one container in one namespace. |
| **Bad/stuck rollout or an older ReplicaSet fighting the new one** | `OldReplicaSets: <none>`, single `revision: 1`, `NewReplicaSetAvailable`, and only one ReplicaSet exists for the app in `get all`. |
| **Transient failure that will self-heal** | 14 restarts over 47m with `BackOff ... (x44 over 47m)` and two recorded terminations (22:03:49→51 and 22:08:59→22:09:01) that are byte-identical in duration and exit code — deterministic, not transient. |
| **Missing `--previous` logs hide a different earlier cause** | `unable to retrieve container logs for containerd://9442e...` is only garbage-collected log data for an old container; the *current* container's logs already carry the explicit FATAL message, and the pod template proves the same command has run since revision 1. This does not weaken the conclusion. |

## Verification recipe

```bash
# 1. Confirm the Deployment's pod template supplies no AMQP_URL (expect empty/null output)
kubectl -n payments get deploy checkout-worker \
  -o jsonpath='{.spec.template.spec.containers[0].env}{"\n"}{.spec.template.spec.containers[0].envFrom}{"\n"}'

# 2. Confirm the container self-terminates with exit code 1 and the config error
kubectl -n payments logs deploy/checkout-worker --tail=5
kubectl -n payments get pod -l app=checkout-worker \
  -o jsonpath='{.items[0].status.containerStatuses[0].lastState.terminated.reason}{" exit="}{.items[0].status.containerStatuses[0].lastState.terminated.exitCode}{"\n"}'

# 3. Confirm no Secret/ConfigMap carrying the queue URL exists to be wired in
kubectl -n payments get secrets,configmaps
```

Expect: (1) prints nothing/`null` for both env fields, (2) `FATAL: AMQP_URL not set` and `Error exit=1`, (3) no AMQP-bearing Secret/ConfigMap in the namespace — together confirming the Deployment spec is the resource that must change.

```json
{
  "case_id": "t1-crashloop-missing-env",
  "failing_resource": {"kind": "Deployment", "namespace": "payments", "name": "checkout-worker"},
  "mechanism": "The checkout-worker Deployment's pod template supplies no environment configuration (Environment: <none>, no envFrom, no secret/configMap mounts), so the worker container cannot find AMQP_URL, prints 'FATAL: AMQP_URL not set' and exits with code 1 about two seconds after each start. The kubelet restarts it into CrashLoopBackOff (14 restarts, BackOff x44), so the pod never becomes Ready and the Deployment reports 0/1 available with MinimumReplicasUnavailable, leaving the checkout queue unconsumed. Note that the template's command is a hardcoded stub that exits 1 unconditionally, so the fix must both inject AMQP_URL (e.g. envFrom a Secret) and restore the real worker entrypoint.",
  "verdict": "confirmed"
}
```