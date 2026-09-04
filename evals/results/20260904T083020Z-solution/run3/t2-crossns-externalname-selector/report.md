## Root cause

Service payments/payments-gateway selects pods with label app=payments-gateway, but the only pods that back it are produced by Deployment payments/payments-gateway-api whose pod template labels them app=payments-gateway-api. Nothing matches the selector, so Endpoints payments/payments-gateway has no subsets and the Service's ClusterIP 10.96.191.39 accepts no backend. Storefront's ExternalName Service storefront/payments-gateway aliases the checkout pods' requests to payments-gateway.payments.svc.cluster.local, so the checkout container's health poll of http://payments-gateway:8080/health lands on that empty ClusterIP and gets "Connection refused"; it therefore never writes /tmp/ready, its exec readiness probe keeps failing, Deployment storefront/checkout-api stays at 0/2 Ready and takes no traffic, and order submissions are held.

Remediation: edit Service payments/payments-gateway, field `.spec.selector.app`: `payments-gateway` -> `payments-gateway-api`.

## Evidence chain

1. [symptom] Deployment storefront/checkout-api has both pods Running but not Ready.
   source: namespace_overview(storefront) — verified
   > deployment/checkout-api ready=0/2 podLabels={app=checkout-api}
2. [symptom] The checkout container's readiness probe is an exec test for /tmp/ready and it keeps failing.
   source: describe({"kind": "pod", "name": "checkout-api-7db48f7c7b-6jjnr", "namespace": "storefront"}) — verified
   > Readiness:      exec [sh -c test -f /tmp/ready] delay=5s timeout=1s period=5s successThreshold=1 failureThreshold=2
3. [link] The checkout container cannot reach the payment gateway and holds checkout submissions; the refused address is the payments Service ClusterIP.
   source: get_logs({"namespace": "storefront", "pod": "checkout-api-7db48f7c7b-6jjnr", "tail": 40}) — verified
   > wget: can't connect to remote host (10.96.191.39): Connection refused
4. [link] Storefront reaches the payments gateway through an ExternalName alias to the payments namespace Service.
   source: get_object({"kind": "services", "name": "payments-gateway", "namespace": "storefront"}) — verified
   > "externalName": "payments-gateway.payments.svc.cluster.local"
5. [defect] Service payments/payments-gateway selects app=payments-gateway and owns ClusterIP 10.96.191.39.
   source: get_object({"kind": "services", "name": "payments-gateway", "namespace": "payments"}) — verified
   > "selector": {
   >       "app": "payments-gateway"
   >     },
6. [defect] The backing pods are labelled app=payments-gateway-api, so the Service has zero endpoint addresses even though both pods are Ready.
   source: namespace_overview({"namespace": "payments"}) — verified
   > service/payments-gateway selector={app=payments-gateway} endpointAddresses=0
7. [defect] Endpoints payments/payments-gateway contains no subsets at all.
   source: get_object({"kind": "endpoints", "name": "payments-gateway", "namespace": "payments"}) — verified
   > "name": "payments-gateway",
   >     "namespace": "payments",
8. [link] Deployment payments/payments-gateway-api labels its pods app=payments-gateway-api, which is what the Service selector must match.
   source: get_object({"kind": "deployments", "name": "payments-gateway-api", "namespace": "payments"}) — verified
   > "labels": {
   >           "app": "payments-gateway-api"
   >         }

## Investigation ledger

- The gateway workload itself is down or its container is crashing. — ruled out: Both gateway pods are Running and Ready with no restarts, so the backend is healthy; only the Service enrollment is broken.
  source: namespace_overview({"namespace": "payments"}) — verified
  > deployment/payments-gateway-api ready=2/2 podLabels={app=payments-gateway-api}
- The storefront ExternalName Service points at the wrong DNS name, causing resolution failure. — ruled out: The alias resolved successfully to the payments Service ClusterIP 10.96.191.39 and the failure was a TCP refusal, not a name-resolution error.
  source: get_logs({"namespace": "storefront", "pod": "checkout-api-7db48f7c7b-6jjnr", "tail": 40}) — verified
  > wget: can't connect to remote host (10.96.191.39): Connection refused
- The checkout pod is broken on its own (bad image, missing ConfigMap/volume, crash loop). — ruled out: The container pulled, started, is Running with zero restarts and all mounts resolved; the only warning is the readiness probe.
  source: describe({"kind": "pod", "name": "checkout-api-7db48f7c7b-6jjnr", "namespace": "storefront"}) — verified
  > State:          Running
  >       Started:      Wed, 02 Sep 2026 08:40:15 +0530
  >     Ready:          False
  >     Restart Count:  0

## Verification recipe

1. `kubectl -n payments get endpoints payments-gateway -o yaml` — expect to see: payments-gateway  [PRESENT]
2. `kubectl -n payments get svc payments-gateway -o jsonpath='{.spec.selector}'` — expect to see: "app": "payments-gateway"  [PRESENT]
3. `kubectl -n storefront logs deploy/checkout-api --tail=20` — expect to see: Connection refused  [PRESENT]
(Each command was re-executed against the captured cluster state; PRESENT/ABSENT is the measured result, not a prediction.)

Verdict: confirmed. Every quote below was re-checked against the tool output it cites, a defect observation names the failing object, an alternative was ruled out with evidence, and every verification command was re-run and found present.

```json
{
  "case_id": "t2-crossns-externalname-selector",
  "failing_resource": {
    "kind": "Service",
    "namespace": "payments",
    "name": "payments-gateway"
  },
  "mechanism": "Service payments/payments-gateway has .spec.selector {\"app\":\"payments-gateway\"}, while the pods created by Deployment payments/payments-gateway-api carry the label app=payments-gateway-api, so the selector matches nothing and Endpoints payments/payments-gateway is written with no subsets. Its ClusterIP 10.96.191.39 consequently has no backend, and connections to port 8080 are rejected with \"wget: can't connect to remote host (10.96.191.39): Connection refused\". The selector value should be app=payments-gateway-api so the two ready gateway pods are enrolled as addresses.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
