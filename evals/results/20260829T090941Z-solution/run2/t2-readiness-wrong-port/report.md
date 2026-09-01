## Root cause

Deployment orders/orders-api ships a readiness probe pointed at a port the container does not listen on. The container process logs "orders-api: serving on :8080" and the pod template declares containerPort 8080 named http, but .spec.template.spec.containers[api].readinessProbe.httpGet.port is 8081. The kubelet's probe gets `dial tcp 10.244.0.122:8081: connect: connection refused`, so both replicas stay Ready=False and the endpoint controller keeps their IPs in notReadyAddresses of Endpoints orders/orders-api, leaving Service orders/orders-api with zero usable addresses. Deployment orders/checkout-gateway therefore cannot reach the backend and logs "gateway: orders fetch FAILED (timeout or connection refused)", which surfaces as the 5xx rate at checkout. Fix: set the readiness probe port to 8080 (or the named port http) in the orders-api pod template.

Remediation: edit Deployment orders/orders-api, field `spec.template.spec.containers[api].readinessProbe.httpGet.port`: `8081` -> `8080`.

## Evidence chain

1. [symptom] Both orders-api pods are Running but not ready and the Service has no endpoint addresses.
   source: namespace_overview(orders) — verified
   > service/orders-api selector={app=orders-api} endpointAddresses=0
2. [symptom] The checkout gateway cannot complete calls to the orders backend.
   source: get_logs({"namespace": "orders", "pod": "checkout-gateway-7b867bfc46-fgfqx", "tail": 10}) — verified
   > gateway: orders fetch FAILED (timeout or connection refused)
3. [defect] The deployment's readiness probe targets port 8081 while the container port is 8080.
   source: get_object({"kind": "deployment", "name": "orders-api", "namespace": "orders"}) — verified
   > "readinessProbe": {
   >               "failureThreshold": 3,
   >               "httpGet": {
   >                 "path": "/",
   >                 "port": 8081,
4. [link] The application actually listens on 8080.
   source: get_logs({"namespace": "orders", "pod": "orders-api-7cc5bcf4c7-lst42", "tail": 20}) — verified
   > orders-api: serving on :8080
5. [link] The kubelet probe to 8081 is refused, keeping the pod not ready.
   source: describe({"kind": "pod", "name": "orders-api-7cc5bcf4c7-lst42", "namespace": "orders"}) — verified
   > Readiness probe failed: Get "http://10.244.0.122:8081/": dial tcp 10.244.0.122:8081: connect: connection refused
6. [link] Endpoints for the Service list both pod IPs only as notReadyAddresses.
   source: get_object({"kind": "endpoints", "name": "orders-api", "namespace": "orders"}) — verified
   > "notReadyAddresses": [

## Investigation ledger

- Service selector mismatch (labels not matching pods) — ruled out: The Service selector app=orders-api matches the pod labels, and both pods appear in the Endpoints object, just as not-ready addresses.
  source: get_object({"kind": "service", "name": "orders-api", "namespace": "orders"}) — verified
  > "selector": {
  >       "app": "orders-api"
  >     }
- Service targetPort pointing at the wrong container port — ruled out: The Service forwards to targetPort 8080, which is exactly where the container serves, so the Service port mapping is correct.
  source: get_object({"kind": "service", "name": "orders-api", "namespace": "orders"}) — verified
  > "port": 8080,
  >         "protocol": "TCP",
  >         "targetPort": 8080
- Container crashing / image or configmap mount failure — ruled out: The container is Running with zero restarts and its scripts ConfigMap volume mounted; only the readiness check fails.
  source: describe({"kind": "pod", "name": "orders-api-7cc5bcf4c7-lst42", "namespace": "orders"}) — verified
  > State:          Running
  >       Started:      Sat, 29 Aug 2026 07:40:38 +0530
  >     Ready:          False
  >     Restart Count:  0

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
  "mechanism": "Deployment orders/orders-api sets .spec.template.spec.containers[api].readinessProbe.httpGet.port to 8081 while the container serves on 8080 (the same template declares containerPort 8080 named http and the container logs \"orders-api: serving on :8080\"), so every readiness probe fails with `Readiness probe failed: Get \"http://10.244.0.122:8081/\": dial tcp 10.244.0.122:8081: connect: connection refused`. Both replicas therefore hold Ready=False and are published only as notReadyAddresses, so Service orders/orders-api has endpointAddresses=0 and no request to the orders backend is ever routed.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
