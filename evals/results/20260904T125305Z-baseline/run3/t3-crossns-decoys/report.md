## Root cause

**Service `payments/payments-gateway` has a selector that matches no pods**, so it has zero endpoints and every connection to its ClusterIP is refused by kube-proxy. The Service selects `app=payments-gateway`, but the pods produced by `payments/payments-gateway-api` carry the label `app=payments-gateway-api`. `storefront/checkout-api` gates its readiness file on a successful health check to that gateway (via the `storefront/payments-gateway` ExternalName alias), so both replicas stay `0/1 Ready`, the Deployment reports `0/2 available`, the Service backing checkout has no ready endpoints, and order submissions fail — order volume flat at zero with no storefront release today.

Verdict: **confirmed**.

## Evidence chain

- **Symptom, in the app's own words** — log line from `checkout-api-7db48f7c7b-6fb2v` (and identically from `-g7299`):
  `checkout-api starting; payment gateway endpoint http://payments-gateway:8080/health`
  `wget: can't connect to remote host (10.96.215.202): Connection refused`
  `payment gateway UNREACHABLE at http://payments-gateway:8080/health - holding checkout submissions`
  The app explicitly *holds* checkout submissions — that is the "submitting an order returns an error" symptom.
- **Readiness is coupled to that check** — `describe pod/checkout-api-7db48f7c7b-6fb2v`:
  `Readiness: exec [sh -c test -f /tmp/ready] delay=5s ... failureThreshold=2` and
  `Warning Unhealthy ... Readiness probe failed:`. The startup script (`sh /app/run.sh` from ConfigMap `checkout-scripts`) never creates `/tmp/ready` while the gateway is unreachable, hence `Ready: False`, `Status: Running` but `0/1`.
- **Name resolution path** — `kubectl get all -A` services:
  `storefront service/payments-gateway ExternalName <none> payments-gateway.payments.svc.cluster.local`. So the in-namespace name `payments-gateway` aliases to the payments-namespace Service.
- **The IP in the error is that Service** — `payments service/payments-gateway ClusterIP 10.96.215.202 ... 8080/TCP SELECTOR app=payments-gateway`. The refused IP `10.96.215.202` in the logs matches exactly. DNS worked; TCP was refused — the classic signature of a ClusterIP with an empty endpoint set.
- **Why the endpoint set is empty (the selector mismatch)** — same output:
  - Service selector: `app=payments-gateway`
  - `deployment.apps/payments-gateway-api ... SELECTOR app=payments-gateway-api`
  - `replicaset.apps/payments-gateway-api-9c78bc7b ... SELECTOR app=payments-gateway-api,pod-template-hash=9c78bc7b`
  The pods therefore carry `app=payments-gateway-api`, which the Service's `app=payments-gateway` selector cannot match. `payments-gateway` ≠ `payments-gateway-api`.
- **The backend itself is healthy** — `pod/payments-gateway-api-9c78bc7b-qcwwf` and `-twfm4` are `1/1 Running`, `0` restarts; `deployment.apps/payments-gateway-api 2/2 2 2`. So this is a routing/label defect, not a crashed backend.
- **No storefront release** — `describe deployment.apps/checkout-api` shows `deployment.kubernetes.io/revision: 1`, `NewReplicaSet: checkout-api-7db48f7c7b (2/2 replicas created)`, single `ScalingReplicaSet` event, no old ReplicaSets. Consistent with "the storefront team shipped no release today."

## Investigation ledger

- **checkout-api image/config bug or bad deploy** — ruled out. `describe deployment.apps/checkout-api` shows `revision: 1` with a single ReplicaSet and no rollout events; both pods pulled `busybox:1.36` successfully, `Container created` / `Container started`, `Restart Count: 0`. The container is running fine; only its dependency check fails.
- **ConfigMap `checkout-scripts` missing / volume failure** — ruled out. The volume is declared `Optional: false`, and the pods reached `Initialized True`, `PodReadyToStartContainers True`, and are executing `sh /app/run.sh` (it is emitting log lines). A missing ConfigMap would have held the pods in `ContainerCreating`.
- **Readiness probe misconfigured (too aggressive / too short)** — ruled out as root cause. The probe has failed continuously for the pod's whole life (`x11 over 51s`), and the logs give an independent, specific reason (gateway refused) rather than a timing artifact. Relaxing the probe would only hide a genuinely non-functional checkout.
- **DNS broken (the ExternalName alias is wrong)** — ruled out. `coredns 2/2` Running, `kube-dns` Service present, and the client resolved the name all the way to a concrete IP (`10.96.215.202`) that exactly matches `payments/payments-gateway`. A DNS failure would produce "bad address", not "Connection refused" against a correct IP. The ExternalName chain itself is correct.
- **payments-gateway backend down / crashlooping / wrong port** — ruled out for the endpoint-refusal mechanism. Both gateway pods are `1/1 Running` with `0` restarts and their Deployment is `2/2`. Had a live pod been in the endpoint set but not listening on 8080, we would still see a refusal — but no pod can be in the endpoint set at all, because no pod carries the Service's selector label.
- **NetworkPolicy blocking storefront → payments** — ruled out on the available evidence. No NetworkPolicy objects appear anywhere in `kubectl get all -A`-adjacent output, and a policy drop typically manifests as a timeout, not an immediate `Connection refused`.
- **`batch-compute/model-trainer` Pending (decoy)** — unrelated. `describe pod/model-trainer-...`: `0/1 nodes are available: 1 Insufficient cpu`, caused by its own absurd `Requests: cpu: 512`. It has no Service, no relationship to storefront, and checkout pods are already scheduled and running on the node.
- **`report-exports/nightly-export` CronJob failing (decoy)** — unrelated. Log line: `nightly-export: /export/destination.conf is missing`; its `export-target` volume is an `EmptyDir`, so the file can never exist. A batch export job is not in the checkout request path.
- **`release-canary/canary-runner` restarting (decoy)** — unrelated. `RESTARTS 2 (13s ago)` but it is `1/1 Running`, has no Service, and is not referenced by checkout.
- **Node/control-plane degradation** — ruled out. All `kube-system` components (etcd, apiserver, scheduler, controller-manager, kube-proxy, kindnet, coredns) are `1/1`/`2/2` Running with `0` restarts and 2d9h uptime.

## Verification recipe

```bash
# 1. The smoking gun: the Service has no endpoints at all.
kubectl get endpointslice -n payments -l kubernetes.io/service-name=payments-gateway -o wide
kubectl get endpoints -n payments payments-gateway

# 2. Prove the label mismatch side by side.
kubectl get svc -n payments payments-gateway -o jsonpath='{.spec.selector}{"\n"}'
kubectl get pods -n payments --show-labels

# 3. Confirm the pods are actually serving, i.e. only routing is broken.
kubectl run -n storefront netcheck --rm -it --restart=Never --image=busybox:1.36 -- \
  wget -qO- --timeout=3 "http://$(kubectl get pod -n payments -l app=payments-gateway-api -o jsonpath='{.items[0].status.podIP}'):8080/health"
```

Expected: step 1 returns `<none>` / no addresses; step 2 shows selector `{"app":"payments-gateway"}` against pod labels `app=payments-gateway-api`; step 3 succeeds against the pod IP, proving the backend is healthy and only the Service selector is wrong.

**Remediation** — repoint the Service selector at the real pod label (single-field, no pod restarts, no storefront change):

```bash
kubectl patch svc -n payments payments-gateway \
  --type merge -p '{"spec":{"selector":{"app":"payments-gateway-api"}}}'
```

Then watch `kubectl get endpoints -n payments payments-gateway` populate and `kubectl get pods -n storefront -w` flip to `1/1` within roughly two probe periods (~10s). Fix the same drift in the source manifest/chart so the next apply does not revert it. Follow-ups: add an alert on `kube_endpoint_address_available == 0` for tier-1 Services, and add a CI check that every Service selector matches at least one workload's pod template labels.

```json
{
  "case_id": "t3-crossns-decoys",
  "failing_resource": {"kind": "Service", "namespace": "payments", "name": "payments-gateway"},
  "mechanism": "The Service payments/payments-gateway selects pods with label app=payments-gateway, but the backing workload's pods are labelled app=payments-gateway-api, so the Service has an empty endpoint set and kube-proxy refuses every connection to ClusterIP 10.96.215.202. storefront/checkout-api aliases that Service via an ExternalName and only writes its /tmp/ready readiness file after a successful gateway health check, so both replicas stay unready, the Deployment reports 0/2 available and takes no traffic, and order submissions fail.",
  "verdict": "confirmed"
}
```