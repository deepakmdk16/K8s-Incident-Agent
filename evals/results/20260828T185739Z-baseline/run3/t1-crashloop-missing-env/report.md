## Root cause

**Deployment `payments/checkout-worker` ships a pod template with no environment configuration**, so its container starts, discovers that `AMQP_URL` is unset, logs `FATAL: AMQP_URL not set` and exits with code 1 roughly 2 seconds after start. The kubelet restarts it, it fails identically, and the pod is held in `CrashLoopBackOff`/`Error` — never becoming Ready. With the single replica never Ready, the Deployment reports `0/1 available`, no worker consumes the checkout queue, and merchant checkouts time out at the payment step.

This is a **configuration defect in the Deployment spec**, not an infrastructure, image, scheduling, or resource fault. Verdict: **confirmed**.

## Evidence chain

- **The symptom is the crash, not scheduling or image pull.** From `kubectl get all -A`: `payments pod/checkout-worker-56d848b6d-tpzjs 0/1 Error 14 (5m49s ago) 47m 10.244.0.5 incident-lab-control-plane` — the pod is assigned to a node, has an IP, and has restarted 14 times.
- **The container exits nonzero every run.** From `describe pod/checkout-worker-56d848b6d-tpzjs`:
  - `State: Terminated / Reason: Error / Exit Code: 1 / Started: ... 22:08:59 / Finished: ... 22:09:01` (2-second lifetime)
  - `Last State: Terminated / Reason: Error / Exit Code: 1 / Started: ... 22:03:49 / Finished: ... 22:03:51` — identical 2-second lifetime on the prior attempt, i.e. deterministic, not intermittent.
  - `Restart Count: 14`
- **The application itself names the missing configuration.** Log line: `2026-08-28T16:39:01.239203062Z FATAL: AMQP_URL not set`, preceded by `2026-08-28T16:38:59.235921950Z connecting to queue`. The 2-second gap between the two log lines matches the 2-second container lifetime exactly, confirming the log is from the terminating run.
- **The pod is given no environment at all.** From `describe pod/...`: `Environment: <none>` — no `env:` entries and no `envFrom:` (no ConfigMap or Secret projection). The only mount is `kube-api-access-hpf64` (`/var/run/secrets/kubernetes.io/serviceaccount`), the default service-account token; `Volumes:` contains nothing else.
- **The defect lives in the Deployment, not just the pod.** From `describe deployment.apps/checkout-worker`, the Pod Template contains the same `Environment: <none>`, `Mounts: <none>`, `Volumes: <none>` and the same command:
  `sh -c echo 'connecting to queue'; sleep 2; echo 'FATAL: AMQP_URL not set'; exit 1`.
  `describe replicaset.apps/checkout-worker-56d848b6d` shows the identical template. So any pod this Deployment creates will fail the same way — deleting the pod cannot fix it.
- **There is no Secret or ConfigMap in the cluster to supply the value.** `kubectl get all -A` lists no `payments` resources other than the Deployment/ReplicaSet/Pod, and the pod template references none. (Note: `get all` does not enumerate Secrets/ConfigMaps, so this is corroborating rather than decisive — but the template references none regardless, which is the point that matters.)
- **The kubelet's behaviour is the expected consequence, not a separate fault.** Events: `Warning BackOff 94s (x44 over 47m) ... Back-off restarting failed container worker` and `Normal Pulled 52s (x14 over 47m) ... image "busybox:1.36" already present on machine`.
- **Readiness reporting is a downstream effect.** Pod conditions: `Ready False`, `ContainersReady False`, while `PodScheduled True` and `Initialized True`. Deployment conditions: `Available False MinimumReplicasUnavailable`, `Progressing True NewReplicaSetAvailable` — the rollout completed; the replica just never becomes available. This is exactly what the "workload readiness monitor" paged on.

## Investigation ledger

- **Image pull failure / bad tag** — ruled out. `Successfully pulled image "busybox:1.36" in 4.523s ... Image size: 1906887 bytes` and later `Container image "busybox:1.36" already present on machine`. Container ID `containerd://604043c1...` exists, so the image ran.
- **Scheduling failure / insufficient resources / taints** — ruled out. `PodScheduled True`, `Successfully assigned payments/checkout-worker-56d848b6d-tpzjs to incident-lab-control-plane`, pod has IP `10.244.0.5`. No `FailedScheduling` events, no `Unschedulable` condition.
- **OOMKill or resource-limit eviction** — ruled out. `Reason: Error`, `Exit Code: 1` — not `OOMKilled` (137). `QoS Class: BestEffort` means no limits are set to breach anyway.
- **Failing readiness/liveness probe restarting a healthy container** — ruled out. The Deployment and pod templates define no probes at all, and no `Unhealthy` events appear. The restarts are driven by container exit, evidenced by `BackOff ... restarting failed container`.
- **Cluster-wide / control-plane outage** — ruled out. Every `kube-system` pod (etcd, apiserver, scheduler, controller-manager, kube-proxy, kindnet, both coredns) is `1/1 Running` with `0` restarts, as is `local-path-provisioner`. Only the `payments` pod is unhealthy.
- **DNS failure preventing the broker lookup** — ruled out as the *cause*. Both `coredns` pods are `1/1 Running 0 restarts` and `service/kube-dns` exists at `10.96.0.10`. More decisively, the process fails before any network attempt: it dies on a missing variable, not a resolution or connection error.
- **The broker/queue service itself being down** — ruled out as the paged cause. The failure message is `AMQP_URL not set`, a configuration check, not a connectivity error; and no broker Service appears in `get all -A`. (A broker may separately need to exist — see remediation — but it is not what is crashing this pod.)
- **Missing Secret/ConfigMap referenced by the pod** — ruled out as the mechanism. That failure mode would surface as `CreateContainerConfigError` with a `Failed ... couldn't find key/secret` event and the container would never start. Here the container starts and runs (`Container created`, `Container started`, x9), and `Environment: <none>` shows nothing was *referenced* in the first place. The defect is an omission, not a dangling reference.
- **Transient/flaky startup that self-heals** — ruled out. Two recorded terminations 5 minutes apart have byte-identical 2-second durations and exit code 1, across 14 restarts over 47 minutes.
- **Node pressure / kubelet malfunction** — ruled out. `PodReadyToStartContainers True`, images pull and containers start normally, and every other pod on the same node `incident-lab-control-plane` is healthy.
- **The `--previous` log fetch failing indicates a logging/runtime problem** — ruled out as irrelevant. `unable to retrieve container logs for containerd://9442e001...` is simply garbage-collected log state for an older container generation; the current log and both `Terminated` states already give a consistent, sufficient picture.
- **Caveat worth stating plainly:** the container's command is a hardcoded script that prints the FATAL line and runs `exit 1` *unconditionally* — it does not actually test `$AMQP_URL`. So this is a placeholder/stub workload standing in for the real worker. Either way the conclusion is the same and the fix is in the same place: **the Deployment's pod template is the resource that must change** — it must supply the queue configuration *and* run a real worker command instead of a script that always exits 1. If the intent were a one-shot task rather than a long-running consumer, the correct object would be a Job/CronJob, since a `Deployment` with `restartPolicy: Always` will crash-loop any command that exits.

**Remediation.** Patch the Deployment's pod template to inject the broker URL from a Secret and to run the real worker entrypoint:

```bash
kubectl -n payments create secret generic checkout-worker-secrets \
  --from-literal=AMQP_URL='amqp://user:pass@rabbitmq.payments.svc.cluster.local:5672/'

kubectl -n payments set env deployment/checkout-worker \
  --from=secret/checkout-worker-secrets
```

Then remove the stub command so the container runs the actual consumer, and add a readiness probe reflecting queue connectivity so this monitor fails fast and accurately next time. Longer term, gate deploys on a required-env check in CI so a template with `Environment: <none>` cannot reach production, and confirm the AMQP broker Service actually exists in `payments` before declaring the incident closed.

## Verification recipe

```bash
# 1. Confirm the Deployment template carries no env/envFrom (expect null/empty).
kubectl -n payments get deploy checkout-worker \
  -o jsonpath='{.spec.template.spec.containers[0].env}{"|"}{.spec.template.spec.containers[0].envFrom}{"\n"}'

# 2. Confirm the container dies on the config check with exit 1, deterministically.
kubectl -n payments logs deploy/checkout-worker --tail=5
kubectl -n payments get pod -l app=checkout-worker \
  -o jsonpath='{.items[0].status.containerStatuses[0].lastState.terminated.exitCode}{" "}{.items[0].status.containerStatuses[0].lastState.terminated.reason}{"\n"}'

# 3. Prove the fix direction: same image WITH the var set does not emit FATAL.
kubectl -n payments run envtest --rm -it --restart=Never --image=busybox:1.36 \
  --env=AMQP_URL=amqp://test -- sh -c 'test -n "$AMQP_URL" && echo OK: AMQP_URL present'
```

Expect: (1) empty/`null` for both env fields, (2) `1 Error`, (3) `OK: AMQP_URL present` — together confirming the omission in the Deployment spec is what starves the worker.

```json
{
  "case_id": "t1-crashloop-missing-env",
  "failing_resource": {"kind": "Deployment", "namespace": "payments", "name": "checkout-worker"},
  "mechanism": "The checkout-worker Deployment's pod template defines no env or envFrom entries (Environment: <none>), so the worker container starts without AMQP_URL, logs 'FATAL: AMQP_URL not set' and exits 1 about two seconds into every run. The kubelet restarts it into CrashLoopBackOff (14 restarts, two recorded terminations both exit code 1 with identical 2-second lifetimes), so the container is never Ready and the Deployment reports Available=False/MinimumReplicasUnavailable, leaving the checkout queue unconsumed.",
  "verdict": "confirmed"
}
```