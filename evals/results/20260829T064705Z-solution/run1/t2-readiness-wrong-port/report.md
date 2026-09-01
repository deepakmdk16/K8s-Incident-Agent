## Root cause

The orders-api Deployment's readiness probe targets the wrong port. Its pod template declares containerPort 8080 and the application logs "serving on :8080", but the readinessProbe httpGet port is 8081. Nothing listens on 8081, so the kubelet's readiness GET is refused on every attempt and both orders-api pods stay Ready=False even though their containers are Running with zero restarts. Because Endpoints only lists ready pods, the orders-api Service holds both pod IPs as notReadyAddresses and has zero endpoint addresses, so the checkout-gateway's calls to the orders backend never reach a backend and the gateway returns 5xx at checkout.

Remediation: edit Deployment orders/orders-api, field `spec.template.spec.containers[api].readinessProbe.httpGet.port`: `8081` -> `8080 (the containerPort named http, the port the process listens on)`.

## Evidence chain

1. [symptom] Both orders-api pods run but are not ready and the Service has no endpoint addresses.
   source: namespace_overview(orders) — verified
   > service/orders-api selector={app=orders-api} endpointAddresses=0
2. [symptom] The checkout gateway cannot complete calls to the orders backend.
   source: get_logs({"namespace": "orders", "pod": "checkout-gateway-7b867bfc46-fgfqx", "tail": 15}) — verified
   > gateway: orders fetch FAILED (timeout or connection refused)
3. [defect] The deployment's readiness probe points at port 8081 while the container port is 8080.
   source: get_object({"kind": "deployment", "name": "orders-api", "namespace": "orders"}) — verified
   > "readinessProbe": {
   >               "failureThreshold": 3,
   >               "httpGet": {
   >                 "path": "/",
   >                 "port": 8081,
4. [link] The application actually listens on 8080.
   source: get_logs({"namespace": "orders", "pod": "orders-api-7cc5bcf4c7-lst42", "tail": 30}) — verified
   > orders-api: serving on :8080
5. [link] The kubelet readiness probe to 8081 is refused, keeping the pod not ready.
   source: describe({"kind": "pod", "name": "orders-api-7cc5bcf4c7-lst42", "namespace": "orders"}) — verified
   > Readiness probe failed: Get "http://10.244.0.122:8081/": dial tcp 10.244.0.122:8081: connect: connection refused
6. [link] Both pod IPs are present but classified as not-ready addresses, so the Service routes to nothing.
   source: get_object({"kind": "endpoints", "name": "orders-api", "namespace": "orders"}) — verified
   > "notReadyAddresses": [

## Investigation ledger

- The Service selector or targetPort is wrong (label/port mismatch would also empty the endpoints). — ruled out: The Service selects app=orders-api, which both pods carry, and its targetPort 8080 matches the container's listening port; the pod IPs do appear in Endpoints, only as notReadyAddresses.
  source: get_object({"kind": "service", "name": "orders-api", "namespace": "orders"}) — verified
  > "port": 8080,
  >         "protocol": "TCP",
  >         "targetPort": 8080
- The container is crashing or failing to start (bad image, bad command, missing configmap volume). — ruled out: The container started once, is Running with zero restarts, and the configmap-backed /app volume mounted fine.
  source: describe({"kind": "pod", "name": "orders-api-7cc5bcf4c7-lst42", "namespace": "orders"}) — verified
  > State:          Running
  >       Started:      Sat, 29 Aug 2026 07:40:38 +0530
  >     Ready:          False
  >     Restart Count:  0
- The checkout-gateway deployment itself is broken. — ruled out: checkout-gateway is fully ready and its only failure is the outbound call to the orders backend.
  source: namespace_overview(orders) — verified
  > deployment/checkout-gateway ready=1/1 podLabels={app=checkout-gateway}

## Verification recipe

1. `kubectl -n orders get deploy orders-api -o jsonpath='{.spec.template.spec.containers[0].readinessProbe.httpGet.port}'` — expect to see: "port": 8081  [PRESENT]
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
  "mechanism": "In deployment/orders-api the container \"api\" listens on 8080 (containerPort http = 8080, log \"serving on :8080\") but .spec.template.spec.containers[api].readinessProbe.httpGet.port is set to 8081 instead of 8080; the kubelet's readiness GET to that port is answered with \"connection refused\", so the probe fails continuously and both pods are held Ready=False.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
