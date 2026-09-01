# Incident Report — t1-crashloop-missing-env

## Root cause

The Deployment `payments/checkout-worker` ships a container command that **requires the `AMQP_URL` environment variable**, but the pod template defines **no environment variables at all** (`Environment: <none>`). The container's guard clause fails immediately, prints `FATAL: AMQP_URL not set`, and exits 1. The kubelet restarts it, it fails again in under a second, and the pod settles into `CrashLoopBackOff`. Because the single replica never becomes Ready, the Deployment stays at `0/1 Available`, no process consumes the checkout queue, and merchant checkouts time out at the payment step.

Verdict: **confirmed** — the container's own log line names the exact missing variable, and the workload spec confirms it is absent.

## Evidence chain

1. **The paged symptom is real and is a crash loop, not a scheduling/image problem.**
   - `kubectl get all -A`: `payments pod/checkout-worker-66bfcdfc47-d9gdj 0/1 CrashLoopBackOff 5 (74s ago) 4m17s` — pod is scheduled onto `incident-lab-control-plane` with IP `10.244.0.153`, so it placed and pulled fine.
   - `kubectl get all -A`: `deployment.apps/checkout-worker 0/1 1 0` — zero available replicas, matching the alert text "0/1 Ready".

2. **The container exits non-zero immediately after start.**
   - describe of pod `checkout-worker-66bfcdfc47-d9gdj`:
     ```
     Last State:  Terminated
       Reason:    Error
       Exit Code: 1
       Started:   Sat, 29 Aug 2026 07:52:54 +0530
       Finished:  Sat, 29 Aug 2026 07:52:54 +0530
     ```
     Started and Finished are the **same second** — the process dies before doing any work.

3. **The container itself names the cause.**
   - log line (current): `2026-08-29T02:22:54.975656626Z FATAL: AMQP_URL not set`
   - log line (`--previous`): identical — `FATAL: AMQP_URL not set`. The failure is deterministic and repeated across restarts, not a transient.

4. **The failing branch is exactly the guard in the command.**
   - describe of pod, `Command:`
     ```
     sh -c [ -n "${AMQP_URL:-}" ] || { echo "FATAL: AMQP_URL not set"; exit 1; }; echo "connected to queue at ${AMQP_URL}"; ...
     ```
     `exit 1` matches the observed `Exit Code: 1`, and the echoed string matches the log line verbatim.

5. **The variable is genuinely absent from the spec — this is a workload defect, not a runtime accident.**
   - describe of pod: `Environment:    <none>` and `Mounts:` show only the service-account projection (no `envFrom`, no ConfigMap/Secret volume).
   - describe of **deployment** `checkout-worker`, Pod Template: `Environment:   <none>`, `Volumes:         <none>`.
   - describe of **replicaset** `checkout-worker-66bfcdfc47`, Pod Template: `Environment:   <none>`.
   - The defect exists at the Deployment level, so deleting the pod cannot fix it — the ReplicaSet will recreate an identically broken pod.

6. **Kubelet is behaving normally; nothing else is wrong.**
   - describe of pod, Events: `Pulled ... "busybox:1.36" already present on machine`, `Created`, `Started` (x6), then `Warning BackOff ... Back-off restarting failed container worker`. The only Warning is the back-off, which is a *consequence* of the exits.

7. **Blast radius is confined to this workload.**
   - `kubectl get all -A`: every other pod (`coredns` x2, `etcd`, `kindnet`, `kube-apiserver`, `kube-controller-manager`, `kube-proxy`, `kube-scheduler`, `local-path-provisioner`) is `1/1 Running` with `0` restarts.

## Investigation ledger

| Alternative considered | Ruled out by |
|---|---|
| **Image pull failure / bad tag** | Event `Pulled ... Container image "busybox:1.36" already present on machine and can be accessed by the pod` and a populated `Container ID: containerd://496f355b...`. Status would be `ImagePullBackOff`, not `CrashLoopBackOff`. |
| **Scheduling problem (taints, node selector, resource pressure)** | `PodScheduled True`, event `Successfully assigned payments/checkout-worker-66bfcdfc47-d9gdj to incident-lab-control-plane`, and `Node-Selectors: <none>`. The pod is running on a node. |
| **OOMKill / memory limit too low** | `Last State: Terminated, Reason: Error, Exit Code: 1`. OOM would show `Reason: OOMKilled` and exit code 137. Also `QoS Class: BestEffort` — no limits are set at all. |
| **Failing liveness/readiness probe restarting the container** | No probes appear anywhere in the pod, ReplicaSet, or Deployment templates, and no `Unhealthy` events are present. Restarts are driven by the container's own `exit 1`. |
| **Missing ConfigMap/Secret blocking startup** | A missing referenced ConfigMap/Secret produces `CreateContainerConfigError` and a `Failed` event, and the pod would never reach `Started`. Here the container starts six times. Additionally the spec references no ConfigMap or Secret (`Environment: <none>`, `Volumes: <none>`) — the variable is simply not wired up, from any source. |
| **Broker/queue outage — worker can't reach AMQP and dies** | The command's guard runs *before* any network call; the log never reaches `connected to queue at ...`. `Started` and `Finished` are the same second, too fast for a connection attempt or DNS timeout. Both CoreDNS pods are `1/1 Running` with 0 restarts. |
| **Bad rollout — a previously-working revision was replaced** | `deployment.kubernetes.io/revision: 1`, `OldReplicaSets: <none>`, and the only Deployment event is `Scaled up replica set checkout-worker-66bfcdfc47 from 0 to 1`. This workload has never had a healthy revision; there is nothing to roll back to. |
| **Cluster-wide control-plane or CNI degradation** | All `kube-system` and `local-path-storage` pods are `1/1 Running` with `0` restarts and 10h age; only the `payments` pod is unhealthy. |
| **RBAC / service-account token problem** | `PodReadyToStartContainers True`, `Initialized True`, and the `kube-api-access-shsf7` projected volume mounted successfully. The command makes no API calls regardless. |

## Verification recipe

```bash
# 1. Confirm the workload spec has no env wired up (expect: null / empty)
kubectl get deploy checkout-worker -n payments \
  -o jsonpath='{.spec.template.spec.containers[0].env}{"\n"}{.spec.template.spec.containers[0].envFrom}{"\n"}'

# 2. Confirm the container's own last words and exit code
kubectl logs -n payments -l app=checkout-worker --previous --tail=5
kubectl get pod -n payments -l app=checkout-worker \
  -o jsonpath='{.items[0].status.containerStatuses[0].lastState.terminated.reason}{" exit="}{.items[0].status.containerStatuses[0].lastState.terminated.exitCode}{"\n"}'

# 3. Prove causality: inject the variable and watch it go Ready
kubectl set env deploy/checkout-worker -n payments \
  AMQP_URL='amqp://<user>:<pass>@<broker-host>:5672/<vhost>'
kubectl rollout status deploy/checkout-worker -n payments --timeout=90s
```

Expected: step 1 prints empty for both `env` and `envFrom`; step 2 prints `FATAL: AMQP_URL not set` and `Error exit=1`; step 3 rolls out to `1/1` with the log advancing to `connected to queue at ...` followed by `consuming checkout jobs`.

**Remediation.** Short term, `kubectl set env` as above to stop the bleeding. Durably, fix the Deployment manifest in source control: store the broker URL in a Secret (it carries credentials — do not inline it) and reference it, e.g.

```yaml
env:
  - name: AMQP_URL
    valueFrom:
      secretKeyRef: {name: checkout-broker, key: amqp-url}
```

Follow-ups worth filing: this Deployment has no resource requests/limits (`QoS Class: BestEffort`) and no readiness probe, so the availability monitor had to wait on `Ready` from the crash loop rather than a real health signal. Adding a readiness probe and requests would tighten detection and scheduling for the next incident.

```json
{
  "case_id": "t1-crashloop-missing-env",
  "failing_resource": {"kind": "Deployment", "namespace": "payments", "name": "checkout-worker"},
  "mechanism": "The checkout-worker Deployment's pod template declares no environment variables (Environment: <none>), but its container command begins with a guard requiring AMQP_URL. The guard fails instantly, logs 'FATAL: AMQP_URL not set' and exits 1 in the same second it starts, so the kubelet back-off puts the pod in CrashLoopBackOff. With the sole replica never Ready, the Deployment stays 0/1 Available and no consumer drains the checkout queue.",
  "verdict": "confirmed"
}
```