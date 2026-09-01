# Incident Report — `t1-crashloop-missing-env`

## Root cause

**Deployment `payments/checkout-worker` ships a pod template with no environment variables defined, but its container command hard-requires `AMQP_URL`.** The container's first statement is a guard — `[ -n "${AMQP_URL:-}" ] || { echo "FATAL: AMQP_URL not set"; exit 1; }` — so on every start the shell finds the variable unset, prints the fatal message, and exits 1. The kubelet restarts it, it fails identically, and the pod settles into `CrashLoopBackOff`. Because the single replica never becomes Ready, the Deployment reports `0/1 Available` and no worker process is alive to consume checkout jobs from the queue — which is exactly the paged symptom.

Verdict: **confirmed**. The container's own log line names the missing variable, the pod spec confirms it is absent, and the Deployment template — the source of truth that would have to change — also shows `Environment: <none>`.

## Evidence chain

1. **The paged workload is down, and it is the only unhealthy thing in the cluster.**
   From `kubectl get all -A`:
   `payments   pod/checkout-worker-66bfcdfc47-d9gdj   0/1   CrashLoopBackOff   5 (74s ago)   4m17s`
   and `deployment.apps/checkout-worker   0/1   1   0   4m17s`.
   Every other pod in `kube-system` and `local-path-storage` is `1/1 Running` with `0` restarts.

2. **The container tells us exactly why it dies.**
   Log line (current and `--previous` are byte-identical, i.e. every restart fails the same way):
   `2026-08-29T02:22:54.975656626Z FATAL: AMQP_URL not set`

3. **That log line is emitted by the container's own guard clause.**
   From `describe pod/checkout-worker-66bfcdfc47-d9gdj`, `Command:`
   `[ -n "${AMQP_URL:-}" ] || { echo "FATAL: AMQP_URL not set"; exit 1; }; echo "connected to queue at ${AMQP_URL}"; while :; do echo "consuming checkout jobs"; sleep 10; done`
   The `${AMQP_URL:-}` form cannot itself fail on an unset variable, so reaching the `echo`/`exit 1` branch proves the variable was empty or unset at exec time. Note the "connected to queue" line never appears in the logs — the process dies before the queue loop is ever entered.

4. **The variable is genuinely absent from the running pod.**
   `describe pod ...` → `Environment:    <none>`. The only mount is the default `kube-api-access-shsf7` projected service-account token; there is no envFrom ConfigMap/Secret, no `Volumes:` other than that token, and no injected config of any kind.

5. **The absence originates in the Deployment's pod template, not in a mutated pod.**
   `describe deployment.apps/checkout-worker` → under `Pod Template` → `Containers: worker:` → `Environment:   <none>`, with the identical `Command:` block.
   `describe replicaset.apps/checkout-worker-66bfcdfc47` → same `Environment:   <none>`.
   Deployment → ReplicaSet → Pod all agree, so the fix must be applied to the Deployment spec.

6. **Termination semantics match a guard-clause abort, not a slow crash.**
   `describe pod ...`, `Last State: Terminated / Reason: Error / Exit Code: 1`, with `Started: 07:52:54` and `Finished: 07:52:54` — the same second. `exit 1` is the literal exit code in the guard clause.

7. **The kubelet is doing its job; the failure is purely in-container.**
   Events show `Pulled` / `Created` / `Started` each `(x6 over 4m27s)` and
   `Warning BackOff ... Back-off restarting failed container worker`.
   The image is resolved locally (`Container image "busybox:1.36" already present on machine`), so this is not an image or registry problem.

8. **Symptom linkage to the page.** `describe deployment` → `Available   False   MinimumReplicasUnavailable` and `1 desired | ... | 0 available | 1 unavailable`. With `replicas: 1` and that one replica never Ready, zero consumers exist for the checkout queue — matching "checkout jobs are not being consumed" and the 15-minute availability alert.

## Investigation ledger

- **Image pull / bad tag (`ImagePullBackOff`, typo'd tag)** — Ruled out. Event: `Container image "busybox:1.36" already present on machine and can be accessed by the pod`, and `Pulled`/`Created`/`Started` fire on every one of the 6 attempts. The container reaches RUNNING and emits application output before dying.

- **Failing readiness/liveness probe killing a healthy process** — Ruled out. `describe pod` lists no `Liveness:` or `Readiness:` lines at all, and there are no `Unhealthy` probe events. `Ready: False` is a consequence of the container not running, not of a probe verdict. Termination `Reason: Error, Exit Code: 1` is a self-inflicted exit, not a probe-triggered `Killed`.

- **OOMKill / resource limits** — Ruled out. `Last State: Terminated / Reason: Error`, not `OOMKilled`, and `QoS Class: BestEffort` with no `Limits:`/`Requests:` stanza means no memory ceiling was in play. The container also lived under one second, far too short to accumulate memory pressure.

- **Scheduling failure (taints, nodeSelector, insufficient capacity)** — Ruled out. `PodScheduled: True`, event `Successfully assigned payments/checkout-worker-66bfcdfc47-d9gdj to incident-lab-control-plane`, and the pod holds an IP `10.244.0.153`. It is scheduled and running, just crashing.

- **Missing Secret/ConfigMap referenced by the spec (would give `CreateContainerConfigError`)** — Ruled out as the *mechanism*. If the template referenced an absent Secret via `envFrom`/`secretKeyRef`, the pod would sit in `CreateContainerConfigError` with a `Failed`/`FailedMount` event and the container would never start. Instead the status is `CrashLoopBackOff`, the container starts cleanly 6 times, and `Environment: <none>` shows *no reference exists at all*. The wiring is simply missing from the spec, not broken. (This does inform remediation: the value most likely belongs in a Secret that then needs to be referenced.)

- **DNS / broker unreachable — worker can't connect to RabbitMQ** — Ruled out. Both `coredns` pods are `1/1 Running` with 0 restarts and `kube-dns` Service is intact. More decisively, the worker exits *before* any network call: the `echo "connected to queue at ${AMQP_URL}"` line never appears in the logs, so the guard clause aborts prior to any connection attempt.

- **A bad recent rollout — previous revision was healthy** — Ruled out. `deployment.kubernetes.io/revision: 1`, `OldReplicaSets: <none>`, and a single event `Scaled up replica set checkout-worker-66bfcdfc47 from 0 to 1`. This workload has never been healthy; there is no good revision to roll back to. Remediation must be a forward fix.

- **Node-level / control-plane degradation** — Ruled out. All `kube-system` components (`etcd`, `kube-apiserver`, `kube-scheduler`, `kube-controller-manager`, `kube-proxy`, `kindnet`) and `local-path-provisioner` are `1/1 Running`, `0` restarts, `10h` uptime. The blast radius is exactly one Deployment.

- **Service/selector misrouting hiding a healthy pod** — Ruled out as irrelevant to the symptom. No Service exists in the `payments` namespace, and this is a queue-consuming worker, not an inbound-traffic service; label `app=checkout-worker` matches the ReplicaSet selector correctly regardless.

## Verification recipe

```bash
# 1. Confirm the Deployment template carries no env vars (expect: empty / null)
kubectl get deployment checkout-worker -n payments \
  -o jsonpath='{.spec.template.spec.containers[0].env}'; echo

# 2. Confirm the running container sees no AMQP_URL and dies on the guard clause
kubectl logs -n payments -l app=checkout-worker --tail=5 --previous
# expect: FATAL: AMQP_URL not set   (and NO "connected to queue at ..." line)

# 3. Prove causality in 10s — inject the var into a throwaway pod with the same command
kubectl run amqp-probe -n payments --rm -it --restart=Never \
  --image=busybox:1.36 --env=AMQP_URL=amqp://probe/ -- \
  sh -c '[ -n "${AMQP_URL:-}" ] || { echo "FATAL: AMQP_URL not set"; exit 1; }; echo "connected to queue at ${AMQP_URL}"'
# expect: "connected to queue at amqp://probe/" and exit 0 -> the env var is the sole blocker
```

**Remediation:** store the real broker URL in a Secret and reference it from the Deployment's pod template, e.g. `kubectl create secret generic checkout-worker-amqp -n payments --from-literal=AMQP_URL='<real-broker-url>'`, then patch the container with an `env` entry using `secretKeyRef` (name `checkout-worker-amqp`, key `AMQP_URL`). Do **not** pass the credential via `kubectl set env --value=...`, which would write the URL in plaintext into the Deployment spec and shell history. The rollout will replace the crashing pod and the Deployment should report `1/1` Available. Follow-ups: add a required-env check to the workload's CI/admission policy so a template missing `AMQP_URL` can't ship, and consider a readiness probe so a half-initialized worker is not counted Ready.

```json
{
  "case_id": "t1-crashloop-missing-env",
  "failing_resource": {"kind": "Deployment", "namespace": "payments", "name": "checkout-worker"},
  "mechanism": "The checkout-worker Deployment's pod template defines no environment variables (Environment: <none>), but the container's command begins with a guard that exits 1 when AMQP_URL is unset. Every start therefore logs 'FATAL: AMQP_URL not set' and terminates immediately, driving the pod into CrashLoopBackOff so the sole replica never becomes Ready and the Deployment stays 0/1 Available with no process consuming checkout jobs.",
  "verdict": "confirmed"
}
```