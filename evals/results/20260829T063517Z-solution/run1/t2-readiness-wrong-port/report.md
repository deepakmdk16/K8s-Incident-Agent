## Root cause

The orders-api Deployment's readiness probe points at the wrong port. The container serves HTTP on :8080 (the containerPort named http, and the Service targetPort), but .spec.template.spec.containers[api].readinessProbe.httpGet.port is 8081, where nothing is listening. Every probe is refused, so both orders-api pods stay Ready=False, the endpoint controller keeps their IPs in notReadyAddresses, and the orders-api Service has zero ready addresses. The checkout gateway's calls to the orders backend therefore reach no backend and checkout returns 5xx.

Remediation: edit Deployment orders/orders-api, field `spec.template.spec.containers[api].readinessProbe.httpGet.port`: `8081` -> `8080`.

## Evidence chain

1. [symptom] Both orders-api pods are Running but not ready and the Service has no endpoint addresses.
   source: namespace_overview(orders) — verified
   > service/orders-api selector={app=orders-api} endpointAddresses=0
2. [symptom] The gateway's calls to the orders backend are failing.
   source: get_logs({"namespace": "orders", "pod": "checkout-gateway-7b867bfc46-fgfqx", "tail": 15}) — verified
   > gateway: orders fetch FAILED (timeout or connection refused)
3. [defect] The readiness probe in the deployment pod template targets port 8081 while the declared container port is 8080.
   source: get_object({"kind": "deployment", "name": "orders-api", "namespace": "orders"}) — verified
   > "port": 8081,
4. [link] The application actually listens on 8080.
   source: get_logs({"namespace": "orders", "pod": "orders-api-7cc5bcf4c7-lst42", "tail": 20}) — verified
   > orders-api: serving on :8080
5. [link] The probe against 8081 is refused, keeping the pod not ready.
   source: describe({"kind": "pod", "name": "orders-api-7cc5bcf4c7-lst42", "namespace": "orders"}) — verified
   > Readiness probe failed: Get "http://10.244.0.122:8081/": dial tcp 10.244.0.122:8081: connect: connection refused
6. [link] Because no pod is ready, both pod IPs sit in notReadyAddresses so the Service has no servable backends.
   source: get_object({"kind": "endpoints", "name": "orders-api", "namespace": "orders"}) — verified
   > "notReadyAddresses": [

## Investigation ledger

- Service selector does not match the pod labels — ruled out: The Service selector app=orders-api does select the pods: both pod IPs appear in the Endpoints object, just as not-ready addresses.
  source: get_object({"kind": "endpoints", "name": "orders-api", "namespace": "orders"}) — verified
  > "name": "orders-api-7cc5bcf4c7-lst42",
- Service targetPort points at the wrong container port — ruled out: The Service targets 8080, the port the application logs say it serves on, so the Service port mapping is correct.
  source: get_object({"kind": "service", "name": "orders-api", "namespace": "orders"}) — verified
  > "targetPort": 8080
- The application crashed or failed to start (bad image, missing ConfigMap volume) — ruled out: The container is Running with zero restarts and the scripts ConfigMap mounted; only the readiness check fails.
  source: describe({"kind": "pod", "name": "orders-api-7cc5bcf4c7-lst42", "namespace": "orders"}) — verified
  > State:          Running

## Verification recipe

1. `kubectl -n orders get deploy orders-api -o jsonpath='{.spec.template.spec.containers[0].readinessProbe.httpGet.port}'` — expect to see: "port": 8081,  [PRESENT]
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
  "mechanism": "Deployment orders-api sets .spec.template.spec.containers[api].readinessProbe.httpGet.port to 8081 while the container listens on 8080, so the kubelet's readiness HTTP GET is refused (\"dial tcp 10.244.0.122:8081: connect: connection refused\") and both pods report Ready=False. With no ready pods, the orders-api Endpoints object holds only notReadyAddresses and the Service publishes zero addresses, so gateway requests to the orders backend have no backend to reach and fail.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
