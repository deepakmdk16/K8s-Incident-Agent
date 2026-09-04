## Root cause

**Verdict: confirmed.**

The `payments/payments-gateway` Service is mis-selected. Its selector is `app=payments-gateway`, but the pods it is meant to front are labelled `app=payments-gateway-api` (that is the label the `payments-gateway-api` Deployment/ReplicaSet stamps on its pods). The label sets do not match, so the Service selects zero pods and has an empty EndpointSlice. kube-proxy programs a REJECT rule for a ClusterIP with no backends, which is why every connection to `10.96.191.39:8080` returns *Connection refused* immediately rather than timing out.

`checkout-api` resolves `payments-gateway` in `storefront` → the ExternalName alias → `payments-gateway.payments.svc.cluster.local` → `10.96.191.39`, gets refused on every health poll, therefore never writes its `/tmp/ready` sentinel, therefore fails its exec readiness probe forever. Deployment `storefront/checkout-api` stays 0/2 Ready, its Service takes it out of rotation, and order submissions fail. No storefront release was needed for this — the break is on the payments Service object.

## Evidence chain

- **Selector mismatch (the core fact)** — `kubectl get all -A`, Services block:
  `payments   service/payments-gateway   ClusterIP   10.96.191.39   ...   8080/TCP   ...   app=payments-gateway`
  vs. the workload that is supposed to back it:
  `payments   deployment.apps/payments-gateway-api ... SELECTOR app=payments-gateway-api` and
  `payments   replicaset.apps/payments-gateway-api-9c78bc7b ... SELECTOR app=payments-gateway-api,pod-template-hash=9c78bc7b`.
  The ReplicaSet selector is authoritative for the labels its pods carry, so the pods are `app=payments-gateway-api` — which the Service selector `app=payments-gateway` does not match.
- **The backends are healthy, so this is not an outage of the gateway itself** — `kubectl get all -A`: `payments/payments-gateway-api-9c78bc7b-k65l8   1/1   Running` and `...-mhxwf   1/1   Running`, and `deployment.apps/payments-gateway-api   2/2   2   2`.
- **Empty-endpoints signature in the traffic** — log line from both checkout pods: `wget: can't connect to remote host (10.96.191.39): Connection refused`. The refusal is against the ClusterIP itself (`10.96.191.39`, exactly the `payments-gateway` ClusterIP from `get all -A`), instantaneous and reproducible every 6 s — the classic kube-proxy REJECT-on-no-endpoints behaviour, not a pod-level refusal (a pod-level refusal would show a `10.244.0.x` peer).
- **DNS/alias path is working** — the storefront-side alias exists and points at the right FQDN: `storefront   service/payments-gateway   ExternalName   <none>   payments-gateway.payments.svc.cluster.local`. The client resolved the name to the correct ClusterIP (`10.96.191.39` appears in the log), so resolution succeeded and only the connection failed.
- **Symptom linkage to readiness** — `describe pod/checkout-api-7db48f7c7b-6jjnr`: `Readiness: exec [sh -c test -f /tmp/ready] ...`, `Ready: False`, `Warning Unhealthy ... Readiness probe failed:`; and the app's own statement of intent, log line: `payment gateway UNREACHABLE at http://payments-gateway:8080/health - holding checkout submissions`. Both replicas show the identical log (`...-d9ktf` and `...-6jjnr`).
- **Deployment-level consequence** — `describe deployment.apps/checkout-api`: `Replicas: 2 desired | ... | 0 available | 2 unavailable`, `Available False MinimumReplicasUnavailable`.

## Investigation ledger

- **Bad storefront release / image regression** — ruled out. `describe deployment.apps/checkout-api` shows `deployment.kubernetes.io/revision: 1`, `OldReplicaSets: <none>`, single event `Scaled up replica set checkout-api-7db48f7c7b from 0 to 2`. There is no second revision, consistent with "no release today".
- **checkout-api crash-looping or bad command/configmap** — ruled out. `State: Running`, `Restart Count: 0`, the ConfigMap volume `checkout-scripts` mounted with `Optional: false` and no `FailedMount`/`CreateContainerConfigError` events; the script is clearly executing, since it emits structured startup and poll logs.
- **The payments gateway is down / not listening on 8080** — ruled out as the primary cause. Both gateway pods are `1/1 Running` with 0 restarts. If the pods were listening on the wrong port you would still expect connections to be *routed* to a pod IP; here the refusal is at the ClusterIP with no endpoints behind it. (If, after fixing the selector, connections still refuse — this time from a `10.244.0.x` peer — the Service `targetPort` would become the next thing to check.)
- **Wrong ExternalName target / cross-namespace DNS broken** — ruled out. The ExternalName points at the exact FQDN `payments-gateway.payments.svc.cluster.local`, and the client's error already contains the resolved IP `10.96.191.39`, which matches the payments ClusterIP. DNS itself is healthy: both `coredns` pods are `1/1 Running`.
- **NetworkPolicy blocking storefront → payments** — ruled out by behaviour, not by object listing. A default-deny policy drops packets (timeout/`wget: download timed out`); the observed failure is an immediate `Connection refused`, which is a REJECT generated by kube-proxy for a backend-less Service. No NetworkPolicy objects appear in the cluster dump either.
- **Node pressure / scheduling / resource starvation** — ruled out. All pods scheduled to `incident-lab-control-plane`, `PodScheduled True`, no eviction or `FailedScheduling` events; QoS `BestEffort` with no OOM or throttling events.
- **Readiness probe itself misconfigured (probe never satisfiable)** — considered and rejected as root cause. The probe (`test -f /tmp/ready`) is a sentinel the app itself writes once the gateway health check passes; the log shows the app deliberately withholding readiness (`holding checkout submissions`). The probe is reporting a real dependency failure, not manufacturing one.

## Verification recipe

```bash
# 1. The smoking gun: the Service selects nothing.
kubectl get endpointslices -n payments -l kubernetes.io/service-name=payments-gateway -o wide
kubectl get endpoints -n payments payments-gateway          # expect ENDPOINTS: <none>

# 2. Prove the label mismatch side by side.
kubectl get svc payments-gateway -n payments -o jsonpath='{.spec.selector}{"\n"}'   # app=payments-gateway
kubectl get pods -n payments --show-labels                                          # pods carry app=payments-gateway-api

# 3. Confirm the fix restores reachability and readiness.
kubectl patch svc payments-gateway -n payments \
  -p '{"spec":{"selector":{"app":"payments-gateway-api"}}}'
kubectl get endpoints -n payments payments-gateway          # expect 2 pod IPs:8080
kubectl rollout status deploy/checkout-api -n storefront --timeout=90s
```

**Remediation:** correct the selector on Service `payments/payments-gateway` to `app: payments-gateway-api` (in the source manifest/Helm values, not just the live patch). Once endpoints populate, checkout-api's health poll succeeds, the pods write `/tmp/ready`, readiness passes, and the Deployment returns to 2/2 without any storefront change or restart. Follow-ups: add an alert on `kube_endpoint_address_available == 0` for tier-1 Services, and a CI check that every Service selector matches at least one workload's pod labels — this class of typo (`-api` suffix present on the workload, absent on the Service) is invisible to `kubectl apply`.

```json
{
  "case_id": "t2-crossns-externalname-selector",
  "failing_resource": {"kind": "Service", "namespace": "payments", "name": "payments-gateway"},
  "mechanism": "The Service's selector is app=payments-gateway, but the backing gateway pods are labelled app=payments-gateway-api, so the Service matches zero pods and has an empty EndpointSlice. kube-proxy therefore REJECTs every connection to its ClusterIP 10.96.191.39:8080 with an immediate 'Connection refused', which is exactly what the dependent checkout health poll records. That dependency check never succeeds, so the checkout replicas never become Ready and completed-order volume sits at zero.",
  "verdict": "confirmed"
}
```