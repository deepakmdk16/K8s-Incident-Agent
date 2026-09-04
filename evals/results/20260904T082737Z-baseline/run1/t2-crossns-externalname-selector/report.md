## Root cause

**Verdict: confirmed.**

The Service `payments/payments-gateway` has the label selector `app=payments-gateway`, but the pods it is meant to front are labelled `app=payments-gateway-api` (that is the label the `payments-gateway-api` Deployment/ReplicaSet stamps on them). The selector therefore matches zero pods, the Service has an empty EndpointSlice, and kube-proxy installs a REJECT rule for its ClusterIP `10.96.191.39` — every connection to it is instantly refused.

`storefront/checkout-api`'s startup script polls `http://payments-gateway:8080/health` (resolved via the storefront `ExternalName` alias → `payments-gateway.payments.svc.cluster.local` → `10.96.191.39`), never gets a healthy response, and so never creates `/tmp/ready`. The exec readiness probe `test -f /tmp/ready` fails forever, both replicas stay `0/1 Ready`, the Deployment reports `0/2 available`, its Service endpoints stay empty, and checkout submissions error out — which is the paged symptom.

The resource whose spec must change is the `payments/payments-gateway` Service (fix the selector to `app=payments-gateway-api`). Nothing in `storefront` is misconfigured.

## Evidence chain

- **Service selector vs. pod labels (the defect).**
  From `kubectl get all -A`:
  `payments service/payments-gateway ClusterIP 10.96.191.39 ... 8080/TCP ... SELECTOR app=payments-gateway`
  but `payments deployment.apps/payments-gateway-api ... SELECTOR app=payments-gateway-api` and
  `payments replicaset.apps/payments-gateway-api-9c78bc7b ... SELECTOR app=payments-gateway-api,pod-template-hash=9c78bc7b`.
  The pods created by that ReplicaSet therefore carry `app=payments-gateway-api`, which the Service selector `app=payments-gateway` does not match. Empty endpoint set.

- **The backends are healthy — it is purely a wiring fault.**
  `payments pod/payments-gateway-api-9c78bc7b-k65l8 1/1 Running 0 11s` and `...-mhxwf 1/1 Running 0 11s`. Two ready pods exist; they are simply not selected.

- **The refusal is against the ClusterIP of that exact Service.**
  Log line (both checkout pods): `wget: can't connect to remote host (10.96.191.39): Connection refused` — `10.96.191.39` is `payments/payments-gateway`'s ClusterIP from `kubectl get all -A`. "Connection refused" (immediate, not a timeout) is the signature of a ClusterIP with no endpoints behind it.

- **Name resolution worked; only the backend selection failed.**
  `storefront service/payments-gateway ExternalName <none> payments-gateway.payments.svc.cluster.local` and the log resolved the target to `10.96.191.39`. DNS did its job, so the cross-namespace alias is correct.

- **Failed dependency check → probe never satisfied → 0/2 Ready.**
  Log line: `checkout-api starting; payment gateway endpoint http://payments-gateway:8080/health`, then repeatedly `payment gateway UNREACHABLE at http://payments-gateway:8080/health - holding checkout submissions`.
  `describe pod/checkout-api-7db48f7c7b-6jjnr`: `Readiness: exec [sh -c test -f /tmp/ready] ...`, `Ready: False`, `Restart Count: 0`, event `Warning Unhealthy ... Readiness probe failed:` (x6). The container is up (`State: Running`, `Started`), it is only gating readiness on the gateway check — the sentinel file is never written.

- **Symptom rollup matches the page.**
  `describe deployment.apps/checkout-api`: `Replicas: 2 desired | 2 updated | 2 total | 0 available | 2 unavailable`, `Available False MinimumReplicasUnavailable`. With 0 Ready pods, the storefront Service has no endpoints and takes no traffic → order volume flat at zero.

## Investigation ledger

- **Bad storefront release / image regression** — ruled out. `describe deployment.apps/checkout-api` shows `deployment.kubernetes.io/revision: 1`, `OldReplicaSets: <none>`, and a single ReplicaSet; the only Deployment event is the initial `ScalingReplicaSet ... from 0 to 2`. No rollout occurred, matching "the storefront team shipped no release today."

- **Checkout container crashing / OOM / bad image** — ruled out. `Image: busybox:1.36`, `Pulled ... already present on machine`, `State: Running`, `Restart Count: 0`, `Pods Status: 2 Running / 0 Waiting / 0 Succeeded / 0 Failed` on the ReplicaSet. The process is alive and logging; it just refuses to declare readiness.

- **Scheduling/capacity/node problems** — ruled out. `PodScheduled True`, both pods placed on `incident-lab-control-plane`, `QoS Class: BestEffort`, no `FailedScheduling` events; the node also runs all kube-system pods `1/1 Running`.

- **Missing ConfigMap / volume mount failure** — ruled out. `scripts` volume is `ConfigMap ... Name: checkout-scripts, Optional: false` and the container reached `Started` and executed `/app/run.sh` (its log output proves the script ran).

- **Misconfigured readiness probe on checkout-api (probe itself is the bug)** — ruled out as root cause. The probe is a faithful mirror of the app's own dependency check: the log shows the app deliberately "holding checkout submissions" because the gateway is unreachable. Loosening the probe would mark pods Ready while orders still fail; it treats the symptom, not the cause.

- **Broken cross-namespace `ExternalName` alias in `storefront`** — ruled out. It points at `payments-gateway.payments.svc.cluster.local` and the client actually resolved to `10.96.191.39`, the correct ClusterIP. Had the alias been wrong we would see an NXDOMAIN/"bad address" error, not a TCP refusal from the right IP.

- **DNS/CoreDNS outage** — ruled out. `coredns 2/2` Ready, and resolution demonstrably succeeded (an IP appears in the error message).

- **NetworkPolicy blocking storefront→payments** — ruled out as the mechanism. Policy drops produce timeouts, not immediate `Connection refused`; no NetworkPolicy appears in `kubectl get all -A` (though that verb does not list policies, the refusal signature plus the visible selector mismatch already explains the failure).

- **Payments gateway listening on a different port than the Service's `targetPort`** — considered; the selector mismatch is the demonstrated defect visible in the output, and an empty endpoint set produces exactly this refusal. The verification below distinguishes the two conclusively in seconds (`get endpointslices` empty ⇒ selector, non-empty ⇒ port).

## Verification recipe

```bash
# 1. The smoking gun: the Service selects nothing, while ready pods sit right there with a different label.
kubectl -n payments get svc payments-gateway -o jsonpath='{.spec.selector}{"\n"}'
kubectl -n payments get pods --show-labels
kubectl -n payments get endpointslices -l kubernetes.io/service-name=payments-gateway -o wide
#   expect: selector app=payments-gateway ; pods labelled app=payments-gateway-api ; NO endpoints listed

# 2. Reproduce the refusal from inside the cluster against the ClusterIP the checkout pods hit.
kubectl -n storefront run probe --rm -it --restart=Never --image=busybox:1.36 -- \
  wget -qO- --timeout=3 http://payments-gateway.payments.svc.cluster.local:8080/health
#   expect: "can't connect to remote host (10.96.191.39): Connection refused"

# 3. Apply the fix and watch checkout-api go Ready on its own (no storefront change needed).
kubectl -n payments patch svc payments-gateway --type=merge \
  -p '{"spec":{"selector":{"app":"payments-gateway-api"}}}'
kubectl -n payments get endpointslices -l kubernetes.io/service-name=payments-gateway -o wide
kubectl -n storefront rollout status deploy/checkout-api --timeout=90s
#   expect: 2 endpoints appear, then "deployment "checkout-api" successfully rolled out" (2/2 Ready)
```

Remediation: correct the `payments/payments-gateway` Service selector to `app=payments-gateway-api` (equivalently, add the `app=payments-gateway` label to the Deployment's pod template — but changing the Service is the minimal, non-restarting fix). Follow-ups: add an alert on `kube_endpoint_address_available == 0` for tier-1 Services so a selector typo pages directly instead of surfacing as a downstream checkout outage, and pin Service selectors to the Deployment's pod-template labels in the same manifest/chart so they cannot drift apart.

```json
{
  "case_id": "t2-crossns-externalname-selector",
  "failing_resource": {"kind": "Service", "namespace": "payments", "name": "payments-gateway"},
  "mechanism": "The payments-gateway Service selects app=payments-gateway, but its backing pods are labelled app=payments-gateway-api, so the Service has zero endpoints and kube-proxy immediately refuses every connection to its ClusterIP 10.96.191.39. The checkout-api containers poll that address for gateway health, never write their /tmp/ready sentinel, so the exec readiness probe fails permanently and the Deployment stays at 0/2 available, taking no traffic and blocking order submission.",
  "verdict": "confirmed"
}
```