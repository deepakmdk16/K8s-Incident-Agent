## Root cause

**Deployment `payments/checkout-worker` (its pod template spec) is the failing resource.** The pod template runs a container whose command unconditionally logs `FATAL: AMQP_URL not set` and exits with status 1, and the template supplies **no environment variables at all** (`Environment: <none>`) — so the worker's queue connection config (`AMQP_URL`) is absent. The container therefore terminates ~2 seconds after every start, the kubelet restarts it, and after repeated failures the pod sits in CrashLoopBackOff (`Error` / `BackOff`) with `Ready: False`. Because the single replica never becomes Ready, the Deployment reports `0/1` and `Available=False (MinimumReplicasUnavailable)`, and no checkout jobs are consumed from the queue.

Verdict: **confirmed** — the crash mechanism, the missing config, and the readiness symptom are all directly visible in the provided output.

One important nuance for remediation: the command as written is a hardcoded failure path (`echo 'FATAL: AMQP_URL not set'; exit 1`) — it never actually reads an env var. Injecting `AMQP_URL` alone will *not* fix it; the pod template's `command` must also be replaced with the real worker entrypoint that consumes that variable.

## Evidence chain

- **Symptom, workload level** — `kubectl get all -A`: `deployment.apps/checkout-worker  0/1   1   0   47m` and `replicaset.apps/checkout-worker-56d848b6d  1 desired / 1 current / 0 ready`. Matches the page ("0/1 Ready for over 15 minutes"; pod AGE 47m).
- **Symptom, pod level** — `kubectl get all -A`: `pod/checkout-worker-56d848b6d-tpzjs  0/1  Error  14 (5m49s ago)  47m`. Fourteen restarts, container not Ready.
- **Crash mechanism** — describe of pod `checkout-worker-56d848b6d-tpzjs`, container `worker`:
  ```
  Command:
    sh
    -c
    echo 'connecting to queue'; sleep 2; echo 'FATAL: AMQP_URL not set'; exit 1
  ```
  The command's terminal statement is `exit 1`; there is no long-running process.
- **Crash confirmed at runtime** — same describe:
  ```
  State:          Terminated
    Reason:       Error
    Exit Code:    1
    Started:      Fri, 28 Aug 2026 22:08:59 +0530
    Finished:     Fri, 28 Aug 2026 22:09:01 +0530
  ```
  Two-second lifetime, matching the `sleep 2` in the command. `Last State` shows the identical pattern (`22:03:49` → `22:03:51`), i.e. a deterministic, repeating failure — not a transient one.
- **Missing configuration** — describe of pod, and describe of deployment `checkout-worker`, and describe of replicaset `checkout-worker-56d848b6d`, all show `Environment:   <none>` for container `worker`. No `env`, no `envFrom`, no Secret/ConfigMap reference. `Volumes: <none>` in the deployment/replicaset templates and only the default `kube-api-access-hpf64` projected token mounted in the pod — so config is not arriving via a mounted file either.
- **Application's own statement of the cause** — `kubectl logs ... --tail=50`:
  ```
  2026-08-28T16:38:59.235921950Z connecting to queue
  2026-08-28T16:39:01.239203062Z FATAL: AMQP_URL not set
  ```
  The only log content is the failure message about the missing queue URL.
- **Restart-loop backoff** — describe of pod, Events: `Warning  BackOff  94s (x44 over 47m)  kubelet  Back-off restarting failed container worker in pod checkout-worker-56d848b6d-tpzjs_payments`.
- **Fault is in the spec, not a bad rollout** — describe of deployment: `deployment.kubernetes.io/revision: 1`, `OldReplicaSets: <none>`, and the only event is `Normal ScalingReplicaSet 48m ... Scaled up replica set checkout-worker-56d848b6d from 0 to 1`. The workload has never had a working revision; it was born broken. `Conditions: Available False MinimumReplicasUnavailable` ties the pod failure to the paged Deployment-level symptom.

## Investigation ledger

- **Image pull failure / wrong image tag** — ruled out. Describe of pod Events: `Normal Pulled 47m ... Successfully pulled image "busybox:1.36" in 4.523s` and `Normal Pulled 52s (x14 over 47m) ... image "busybox:1.36" already present on machine`. The image resolves and the container reaches `Started` each time; status is `Error`, not `ImagePullBackOff`.
- **Scheduling failure / insufficient capacity / taints** — ruled out. Describe of pod: `Node: incident-lab-control-plane/172.18.0.2`, `PodScheduled True`, `Normal Scheduled 48m Successfully assigned payments/checkout-worker-56d848b6d-tpzjs`. No `FailedScheduling` events, `Node-Selectors: <none>`.
- **OOMKill or resource limit eviction** — ruled out. Terminated `Reason: Error` with `Exit Code: 1`, not `OOMKilled`/137. `QoS Class: BestEffort` with no limits set in the template, so no cgroup limit to hit.
- **Failing readiness/liveness probe restarting a healthy process** — ruled out. No probes appear anywhere in the pod, deployment, or replicaset templates, and no `Unhealthy` events. The container genuinely exits on its own after ~2s.
- **Broken Secret/ConfigMap reference (config exists but can't be mounted)** — ruled out. That failure mode presents as `CreateContainerConfigError` and a `Failed ... not found` event; instead we see `Environment: <none>` with no references at all, and containers `Created`/`Started` successfully (`Normal Created 31m (x9 over 47m)`). The config is *absent from the spec*, not *unresolvable*.
- **Cluster/control-plane or networking outage (e.g. worker can't reach the broker/DNS)** — ruled out as the cause of this crash. All of `kube-apiserver`, `etcd`, `kube-scheduler`, `kube-controller-manager`, `kube-proxy`, `kindnet`, both `coredns` pods and `local-path-provisioner` are `1/1 Running` with `0` restarts. The pod has an IP (`10.244.0.5`). More decisively, the container never attempts a network call — it prints and exits per its own hardcoded command. Nothing in the output speaks to the health of the actual AMQP broker, but that is downstream of a worker that never runs.
- **Bad new rollout that needs a rollback** — ruled out. `revision: 1` and `OldReplicaSets: <none>` in describe of deployment: there is no previous good revision to roll back to. Fix-forward is the only option.
- **Missing Service / selector mismatch breaking traffic** — ruled out as the paged cause. The workload is a queue consumer with `Port: <none>`; no Service is expected. Labels/selectors are consistent (`app=checkout-worker` on deployment, replicaset, and pod), and the replicaset did create its pod (`Normal SuccessfulCreate`).
- **Note on an unresolved side detail** — `kubectl logs --previous` returned `unable to retrieve container logs for containerd://9442e001...`, i.e. the older container's logs were already garbage-collected after 14 restarts. This does not weaken the conclusion: the current and last-state terminations are identical in reason, exit code, and duration.

## Verification recipe

```bash
# 1. Confirm the pod template ships no env/envFrom and hardcodes the failing command.
kubectl get deploy checkout-worker -n payments \
  -o jsonpath='{range .spec.template.spec.containers[*]}{.name}{"\ncommand: "}{.command}{"\nenv: "}{.env}{"\nenvFrom: "}{.envFrom}{"\n"}{end}'
# Expect: env and envFrom empty/absent; command ends with "exit 1".

# 2. Confirm the container dies with exit code 1 on every restart (not OOM, not a probe).
kubectl get pod -n payments -l app=checkout-worker \
  -o jsonpath='{.items[0].status.containerStatuses[0]}{"\n"}' | tr ',' '\n' | grep -Ei 'reason|exitCode|restartCount|ready'

# 3. Watch one full crash cycle and see the application state the missing variable itself.
kubectl logs -n payments -l app=checkout-worker --tail=20 --timestamps
# Expect: "connecting to queue" then "FATAL: AMQP_URL not set", ~2s apart.
```

**Remediation:** patch the Deployment's pod template to (a) replace the placeholder `command` with the real worker entrypoint, and (b) inject the broker URL, e.g. create/reference a Secret and add
`envFrom: [{secretRef: {name: checkout-worker-amqp}}]` or `env: [{name: AMQP_URL, valueFrom: {secretKeyRef: {name: checkout-worker-amqp, key: url}}}]`.
Roll out with `kubectl rollout restart deploy/checkout-worker -n payments` and confirm `1/1 Ready`. Follow-ups: add a readiness probe so this Deployment fails loudly at deploy time rather than 15 minutes into a SEV2, and validate required env at container start with a clear non-zero exit.

```json
{
  "case_id": "t1-crashloop-missing-env",
  "failing_resource": {"kind": "Deployment", "namespace": "payments", "name": "checkout-worker"},
  "mechanism": "The checkout-worker Deployment's pod template defines no environment variables (Environment: <none>, no env/envFrom/volumes), so the required AMQP_URL queue configuration is absent, and its container command unconditionally logs 'FATAL: AMQP_URL not set' and runs 'exit 1' about two seconds after start. The kubelet restarts the container repeatedly (14 restarts, BackOff x44) and it never reaches Ready, so the single-replica Deployment stays 0/1 with Available=False (MinimumReplicasUnavailable) and no checkout jobs are consumed from the queue.",
  "verdict": "confirmed"
}
```