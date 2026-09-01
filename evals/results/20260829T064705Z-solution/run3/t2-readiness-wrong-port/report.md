## Root cause

The orders-api Deployment's readiness probe addresses the wrong port. The container's process listens on :8080 (the port declared as containerPort http and targeted by the orders-api Service), but the readiness probe does an HTTP GET against port 8081, where nothing is listening. Every probe attempt gets connection refused, so both orders-api pods stay Ready=False even though they are Running and serving. Because only ready pods become Endpoints addresses, the orders-api Service has zero ready addresses and the checkout-gateway's calls to the orders backend fail, producing the 5xx at the gateway. Fix: change the readiness probe port in the Deployment pod template from 8081 to 8080.

Remediation: edit Deployment orders/orders-api, field `spec.template.spec.containers[api].readinessProbe.httpGet.port`: `8081` -> `8080`.

## Evidence chain

1. [symptom] Both orders-api pods are Running but not ready and the Service has no endpoint addresses.
   source: namespace_overview(orders) — verified
   > deployment/orders-api ready=0/2 podLabels={app=orders-api}
2. [symptom] The orders-api Service resolves to zero addresses.
   source: namespace_overview(orders) — verified
   > service/orders-api selector={app=orders-api} endpointAddresses=0
3. [defect] The Deployment pod template declares containerPort 8080 but the readiness probe targets port 8081.
   source: get_object({"kind": "deployment", "name": "orders-api", "namespace": "orders"}) — verified
   > "readinessProbe": {
   >               "failureThreshold": 3,
   >               "httpGet": {
   >                 "path": "/",
   >                 "port": 8081,
4. [link] The application actually listens on 8080.
   source: get_logs({"namespace": "orders", "pod": "orders-api-7cc5bcf4c7-lst42"}) — verified
   > orders-api: serving on :8080
5. [link] The kubelet readiness probe to 8081 is refused on the pod IP.
   source: describe({"kind": "pod", "name": "orders-api-7cc5bcf4c7-lst42", "namespace": "orders"}) — verified
   > Readiness probe failed: Get "http://10.244.0.122:8081/": dial tcp 10.244.0.122:8081: connect: connection refused
6. [link] Both pod IPs sit in notReadyAddresses of the orders-api Endpoints, so the Service has no routable backend.
   source: get_object({"kind": "endpoints", "name": "orders-api", "namespace": "orders"}) — verified
   > "notReadyAddresses": [
7. [symptom] The gateway's calls to the orders backend do not complete.
   source: get_logs({"namespace": "orders", "pod": "checkout-gateway-7b867bfc46-fgfqx", "tail": 15}) — verified
   > gateway: orders fetch FAILED (timeout or connection refused)

## Investigation ledger

- Service selector or targetPort mismatch keeping endpoints empty — ruled out: The Service selector app=orders-api matches the pod labels and its targetPort 8080 matches the container's listening port, so the Service definition is correct; the pods are excluded only because they are not ready.
  source: get_object({"kind": "service", "name": "orders-api", "namespace": "orders"}) — verified
  > "port": 8080,
  >         "protocol": "TCP",
  >         "targetPort": 8080
- The application crashed or failed to start (bad image, missing configmap script, restarts) — ruled out: The container started, has zero restarts and logged that it is serving, so the process itself is healthy.
  source: describe({"kind": "pod", "name": "orders-api-7cc5bcf4c7-lst42", "namespace": "orders"}) — verified
  > State:          Running
  >       Started:      Sat, 29 Aug 2026 07:40:38 +0530
  >     Ready:          False
  >     Restart Count:  0
- The checkout-gateway workload is itself broken — ruled out: The gateway pod is Running and Ready with no restarts; its failures are outbound calls to the orders backend.
  source: namespace_overview(orders) — verified
  > pod/checkout-gateway-7b867bfc46-fgfqx phase=Running labels={app=checkout-gateway, pod-template-hash=7b867bfc46} node=incident-lab-control-plane gateway(ready=True,restarts=0)

## Verification recipe

1. `kubectl -n orders get deployment orders-api -o jsonpath='{.spec.template.spec.containers[0].readinessProbe.httpGet.port}'` — expect to see: "port": 8081  [PRESENT]
2. `kubectl -n orders describe pod orders-api-7cc5bcf4c7-lst42` — expect to see: connect: connection refused  [PRESENT]
3. `kubectl -n orders logs orders-api-7cc5bcf4c7-lst42` — expect to see: serving on :8080  [PRESENT]
(Each command was re-executed against the captured cluster state; PRESENT/ABSENT is the measured result, not a prediction.)

Verdict: confirmed. Every quote below was re-checked against the tool output it cites, a defect observation names the failing object, an alternative was ruled out with evidence, and every verification command was re-run and found present.

```json
{
  "case_id": "t2-readiness-wrong-port",
  "failing_resource": {
    "kind": "Deployment",
    "namespace": "orders",
    "name": "orders-api"
  },
  "mechanism": "In Deployment orders/orders-api, .spec.template.spec.containers[api].readinessProbe.httpGet.port is 8081 while the container listens on 8080 (the declared containerPort http), so the kubelet's readiness HTTP GET to 8081 is refused (\"connect: connection refused\") and both pods report Ready=False, keeping them out of the orders-api Service endpoints.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
