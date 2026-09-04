## Root cause

**Service `payments/payments-gateway` has a label selector that matches no pods.** The Service selects `app=payments-gateway`, but the only pods in that namespace are produced by Deployment `payments/payments-gateway-api`, whose pods carry `app=payments-gateway-api`. With zero backing endpoints, kube-proxy REJECTs traffic to the Service's ClusterIP `10.96.215.202`, so every checkout-api health call to the payment gateway gets "Connection refused". `checkout-api`'s startup script holds checkout submissions and never creates `/tmp/ready`, so its readiness probe (`test -f /tmp/ready`) fails, both replicas stay `0/1 Ready`, the Deployment reports `0/2`, the storefront Service has no endpoints, and completed-order volume is flat at zero.

Verdict: **confirmed**.

## Evidence chain

- **Symptom, checkout side** — `describe pod/checkout-api-7db48f7c7b-6fb2v -n storefront`: `Ready: False`, probe `exec [sh -c test -f /tmp/ready]`, event `Warning Unhealthy ... Readiness probe failed:` (x11). Same for `-g7299`. `describe deployment.apps/checkout-api`: `2 desired | ... | 0 available | 2 unavailable`, `Available False MinimumReplicasUnavailable`.
- **Why the probe never passes** — log line: `checkout-api starting; payment gateway endpoint http://payments-gateway:8080/health` followed repeatedly by `wget: can't connect to remote host (10.96.215.202): Connection refused` and `payment gateway UNREACHABLE at http://payments-gateway:8080/health - holding checkout submissions`. The app gates readiness on the gateway health check.
- **Name resolution is fine; the target is empty** — `kubectl get all -A` services: `storefront service/payments-gateway ExternalName -> payments-gateway.payments.svc.cluster.local`, and `payments service/payments-gateway ClusterIP 10.96.215.202`. The IP in the error log (`10.96.215.202`) is exactly that ClusterIP, so DNS resolved end-to-end; the failure is at connect time.
- **The selector mismatch** — `kubectl get all -A` services column: `payments service/payments-gateway ... SELECTOR app=payments-gateway`. But the pods/workload in that namespace are `deployment.apps/payments-gateway-api ... SELECTOR app=payments-gateway-api` and `replicaset.apps/payments-gateway-api-9c78bc7b ... app=payments-gateway-api,pod-template-hash=9c78bc7b`. No pod carries `app=payments-gateway`, therefore the Service has no endpoints. A ClusterIP with zero endpoints produces immediate `Connection refused` — exactly the log line observed.
- **Backend is otherwise healthy** — `payments/payments-gateway-api-9c78bc7b-qcwwf` and `-twfm4` are `1/1 Running`, `deployment payments-gateway-api 2/2`. So the gateway process is up; only the Service routing to it is broken.
- **No release today is consistent** — `describe deployment.apps/checkout-api`: `deployment.kubernetes.io/revision: 1`, single ReplicaSet `checkout-api-7db48f7c7b (2/2 replicas created)`, no rollout events. The change was on the payments Service side, not a storefront ship.

## Investigation ledger

- **Bad checkout-api release / image regression** — ruled out: `describe deployment.apps/checkout-api` shows `revision: 1`, `OldReplicaSets: <none>`, one ReplicaSet, no rolling-update events; image is the same `busybox:1.36` used everywhere. Matches "team shipped no release today".
- **Checkout pods crashing / OOM / bad image** — ruled out: both pods are `Running` with `Restart Count: 0`, `Pulled ... already present on machine`; only the readiness probe fails.
- **Insufficient cluster CPU starving checkout** — ruled out: `describe pod/model-trainer-...` shows `FailedScheduling ... 1 Insufficient cpu` caused by its own absurd request (`cpu: 512` cores) in `batch-compute`. Checkout pods are `PodScheduled: True`, already running on `incident-lab-control-plane`, QoS `BestEffort` — they were scheduled fine. `model-trainer` is an unrelated decoy in another namespace.
- **CronJob `report-exports/nightly-export` failure** — ruled out: its log is `nightly-export: /export/destination.conf is missing` against an `EmptyDir` volume; separate namespace, no network or data path to storefront checkout.
- **`release-canary/canary-runner` restart loop (`RESTARTS 2`)** — ruled out: separate namespace, no Service, checkout never references it; checkout logs name exactly one dependency.
- **Cluster DNS broken (CoreDNS / `internal-dns/dns-forwarder`)** — ruled out: `coredns 2/2 Running`; checkout's error already contains the resolved ClusterIP `10.96.215.202`, proving both the ExternalName CNAME and the A-record lookup succeeded. A DNS failure would show a resolve error, not `Connection refused`.
- **The `storefront/payments-gateway` ExternalName Service is misconfigured** — ruled out: it points at `payments-gateway.payments.svc.cluster.local`, which is a real Service and resolved to that Service's ClusterIP. The alias is correct; the aliased Service is the empty one.
- **NetworkPolicy blocking storefront → payments** — ruled out as the likely mechanism: no NetworkPolicy objects appear anywhere in the cluster dump, and a policy drop typically yields a timeout, not an immediate `Connection refused`. (`kubectl get netpol -A` in the recipe closes this out definitively.)
- **Payments backend down / listening on the wrong port** — ruled out: both `payments-gateway-api` pods are `1/1 Running` and the Deployment is `2/2`. A wrong container port behind a matching selector would still yield refused connections, but here the selector cannot match any pod at all, so the connection never reaches a pod — the endpoint list is empty by construction.

## Verification recipe

```bash
# 1. The smoking gun: Service selector vs. actual pod labels (should not intersect)
kubectl get svc payments-gateway -n payments -o jsonpath='{.spec.selector}{"\n"}'
kubectl get pods -n payments --show-labels

# 2. Confirm the Service has zero endpoints (expect "<none>" / no addresses)
kubectl get endpoints,endpointslices -n payments payments-gateway
kubectl get netpol -A   # expect: No resources found

# 3. Prove the fix path: after patching the selector, endpoints populate and checkout goes Ready
kubectl patch svc payments-gateway -n payments -p '{"spec":{"selector":{"app":"payments-gateway-api"}}}'
kubectl get endpoints payments-gateway -n payments
kubectl rollout status deploy/checkout-api -n storefront --timeout=90s
```

Remediation: patch `Service payments/payments-gateway` selector to `app: payments-gateway-api` (command above). Equivalent alternative if the Service contract is authoritative: relabel the Deployment's pod template to `app: payments-gateway`, which forces a pod replacement — the Service patch is the faster, non-disruptive fix. Follow-ups: add an alert on `kube_endpoint_address_available == 0` for tier-1 Services, and make `checkout-api` degrade gracefully (serve reads / queue orders) rather than failing readiness on a downstream dependency, which currently converts a dependency outage into a total checkout blackout.

```json
{
  "case_id": "t3-crossns-decoys",
  "failing_resource": {"kind": "Service", "namespace": "payments", "name": "payments-gateway"},
  "mechanism": "Service payments/payments-gateway selects pods with label app=payments-gateway, but the backing Deployment's pods are labeled app=payments-gateway-api, so the Service has zero endpoints and kube-proxy refuses connections to its ClusterIP 10.96.215.202. checkout-api's dependency health check to http://payments-gateway:8080/health therefore gets 'Connection refused', so it never writes /tmp/ready, its readiness probe fails, and both replicas stay NotReady with the storefront Service taking no traffic.",
  "verdict": "confirmed"
}
```