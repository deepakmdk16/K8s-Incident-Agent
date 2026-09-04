## Root cause

**Verdict: confirmed.**

`Service payments/payments-gateway` has the label selector `app=payments-gateway`, but the only pods backing that gateway are produced by `Deployment payments/payments-gateway-api`, whose pods carry `app=payments-gateway-api`. The selector therefore matches zero pods, the Service has no endpoints, and kube-proxy programs a REJECT rule for its ClusterIP `10.96.191.39` — every connection to it fails instantly with "Connection refused".

`checkout-api` resolves `payments-gateway` through the storefront `ExternalName` alias to that same ClusterIP, gets refused on every health poll, never writes `/tmp/ready`, so its exec readiness probe (`test -f /tmp/ready`) fails, both replicas stay `0/1 Ready`, the Deployment reports `0/2` and receives no traffic — the paged CheckoutUnavailable symptom. The spec that must change is the payments `payments-gateway` Service's selector (label-only change; no storefront release involved, consistent with "the storefront team shipped no release today").

## Evidence chain

- **Selector mismatch (the defect).** From `kubectl get all -A`, services table:
  `payments  service/payments-gateway  ClusterIP  10.96.191.39 ... SELECTOR app=payments-gateway`.
  From the same output, deployments table:
  `payments  deployment.apps/payments-gateway-api ... SELECTOR app=payments-gateway-api`, and replicaset
  `payments  replicaset.apps/payments-gateway-api-9c78bc7b ... SELECTOR app=payments-gateway-api,pod-template-hash=9c78bc7b`.
  The Service selects `app=payments-gateway`; the only candidate pods are labelled `app=payments-gateway-api`. No pod in the cluster listing carries the Service's label.
- **The backend pods are healthy, so this is not a backend outage.** `payments  pod/payments-gateway-api-9c78bc7b-k65l8  1/1  Running  0  11s` and `...-mhxwf  1/1  Running`; the Deployment reads `2/2 ... 2 AVAILABLE`. Healthy pods that the Service simply cannot see.
- **Symptom of an endpoint-less ClusterIP.** Log line from both checkout pods: `wget: can't connect to remote host (10.96.191.39): Connection refused`. The IP `10.96.191.39` is exactly the `CLUSTER-IP` of `payments/payments-gateway` in the services table. An immediate refusal at the ClusterIP (rather than a timeout or DNS error) is the kube-proxy REJECT behaviour for a Service with an empty endpoint set.
- **DNS/aliasing worked, isolating the fault to endpoints.** `storefront service/payments-gateway ExternalName ... payments-gateway.payments.svc.cluster.local`. The client log shows it starting at `payment gateway endpoint http://payments-gateway:8080/health` and then failing against the *resolved IP* `10.96.191.39` — resolution succeeded through the ExternalName chain; only the connection failed.
- **Link from refused connection to un-Ready pods.** Log line: `payment gateway UNREACHABLE at http://payments-gateway:8080/health - holding checkout submissions`, repeating every ~6s in both `checkout-api-7db48f7c7b-6jjnr` and `-d9ktf`. The app "holds" instead of marking itself ready.
- **Link from un-Ready pods to the page.** `describe pod/checkout-api-7db48f7c7b-6jjnr`: `Readiness: exec [sh -c test -f /tmp/ready]`, `Ready: False`, `Restart Count: 0`, `State: Running`, and event `Warning Unhealthy 0s (x6 over 110s) ... Readiness probe failed:`. Identical in `describe pod/...-d9ktf`. `describe deployment.apps/checkout-api`: `0 available | 2 unavailable`, `Available False MinimumReplicasUnavailable` — matching "0 of 2 replicas Ready and taking no traffic".
- **Failure is dependency-driven, not crash-driven.** Both checkout containers are `Running` with `Restart Count: 0` and were `Started` normally (`Normal Started 116s`); they are alive but self-declaring not-ready.

## Investigation ledger

- **A bad storefront release / broken checkout image.** Ruled out: `describe deployment.apps/checkout-api` shows `deployment.kubernetes.io/revision: 1`, `OldReplicaSets: <none>`, and a single event `Scaled up replica set checkout-api-7db48f7c7b from 0 to 2`. No rollout occurred, matching the reporter's "no release today".
- **Checkout container crashing / OOM / bad command.** Ruled out: both pods are `State: Running`, `Restart Count: 0`, image `busybox:1.36` already present and pulled fine (`Normal Pulled ... already present on machine`). Logs show orderly, repeated health-check output, not a crash.
- **Broken readiness probe (probe itself misconfigured, e.g. wrong path/port).** Ruled out as root cause: the probe is `test -f /tmp/ready`, purely local, and the logs explain exactly why the flag file is absent — the app is "holding checkout submissions" pending the gateway. Fixing the probe would mask a genuinely dependent-down service, not restore checkout.
- **payments-gateway backend down / not listening on 8080.** Ruled out: both gateway pods are `1/1 Running` and the Deployment is `2/2 ... 2 AVAILABLE`. Also, a live-but-not-listening backend would produce refusals from a *pod* IP (10.244.0.11/.12); the refusal is at the ClusterIP `10.96.191.39`, i.e. before any backend is ever selected. (`kubectl get endpoints` would make this decisive — see verification.)
- **DNS failure / CoreDNS outage / wrong ExternalName target.** Ruled out: `coredns` is `2/2` Running and `deployment.apps/coredns 2/2 2 2` available. The client error is a *connect* error naming a resolved IP, not `wget: bad address`; and that IP equals the ClusterIP the ExternalName chain is supposed to reach, so resolution landed on the correct Service.
- **NetworkPolicy blocking storefront→payments cross-namespace traffic.** Ruled out as best explanation: no NetworkPolicy objects appear anywhere in `kubectl get all -A`'s namespaces (and a policy drop typically yields a timeout, not an instant `Connection refused`). The observed refusal is the signature of an endpoint-less ClusterIP, and the selector mismatch is directly visible in the output.
- **Wrong Service port / gateway listening on a different port.** Ruled out: `payments service/payments-gateway ... 8080/TCP` matches the client's `http://payments-gateway:8080/health`. Port mismatch at the Service level would still require endpoints to exist; here there are none.
- **Scheduling/capacity/node problems.** Ruled out: all checkout pods are `Scheduled` and `Running` on `incident-lab-control-plane`, `PodScheduled True`, and control-plane components are all `1/1 Running`.

## Verification recipe

```bash
# 1. The smoking gun: the Service selects nothing -> no endpoints -> ClusterIP REJECTs.
kubectl get endpointslice -n payments -l kubernetes.io/service-name=payments-gateway -o wide
kubectl get endpoints payments-gateway -n payments        # expect ENDPOINTS: <none>

# 2. Confirm the label mismatch that causes it.
kubectl get svc payments-gateway -n payments -o jsonpath='{.spec.selector}{"\n"}'   # app=payments-gateway
kubectl get pods -n payments --show-labels                                          # pods carry app=payments-gateway-api

# 3. Prove the fix restores the paged symptom (safe, reversible label-selector patch).
kubectl patch svc payments-gateway -n payments -p '{"spec":{"selector":{"app":"payments-gateway-api"}}}'
kubectl get endpoints payments-gateway -n payments        # now lists 10.244.0.11:8080, 10.244.0.12:8080
kubectl wait --for=condition=Available deploy/checkout-api -n storefront --timeout=90s
```

Remediation: correct the `payments/payments-gateway` Service selector to `app: payments-gateway-api` (or, if the Service contract is canonical, add the label `app: payments-gateway` to the Deployment's pod template — a rollout, so the selector patch is the faster mitigation). Follow-ups: add an alert on `kube_endpoint_address_available == 0` for tier-1 Services so an empty-endpoint Service pages directly instead of surfacing as a downstream checkout outage, and make the checkout readiness probe distinguish "dependency down" from "not started" so pages point at the true owner.

```json
{
  "case_id": "t2-crossns-externalname-selector",
  "failing_resource": {"kind": "Service", "namespace": "payments", "name": "payments-gateway"},
  "mechanism": "The payments-gateway Service's selector 'app=payments-gateway' matches no pods, because the gateway pods are labelled 'app=payments-gateway-api', leaving the Service with an empty endpoint set. kube-proxy therefore REJECTs all traffic to its ClusterIP 10.96.191.39, which is what the storefront ExternalName alias resolves to, so checkout-api's gateway health poll gets an immediate 'Connection refused'. Checkout-api consequently never writes /tmp/ready, its exec readiness probe fails, and both replicas stay un-Ready so the Deployment reports 0/2 and receives no traffic.",
  "verdict": "confirmed"
}
```