## Root cause

The orders-api Deployment's readiness probe points at the wrong port. The container serves HTTP on 8080 (its only declared containerPort, and the log line says "serving on :8080"), but the readiness probe is configured as httpGet on port 8081, where nothing listens. The kubelet's probe is refused on every attempt, so both orders-api pods stay Ready=False even though the process is running normally. Because Endpoints only contain ready pods, service/orders-api has zero endpoint addresses, and checkout-gateway's calls to the orders backend get connection refused, which surfaces as 5xx at checkout.

Remediation: edit Deployment orders/orders-api, field `spec.template.spec.containers[api].readinessProbe.httpGet.port`: `8081` -> `8080`.

## Evidence chain

1. [symptom] Both orders-api pods run but are not ready and the Service has no endpoints.
   source: namespace_overview(orders) — verified
   > service/orders-api selector={app=orders-api} endpointAddresses=0
2. [defect] The readiness probe targets port 8081 while the container port is 8080.
   source: get_object({"kind": "deployment", "name": "orders-api", "namespace": "orders"}) — verified
   > "readinessProbe": {
   >               "failureThreshold": 3,
   >               "httpGet": {
   >                 "path": "/",
   >                 "port": 8081,
3. [link] The kubelet's readiness probe on 8081 is refused, keeping the container Ready=False.
   source: describe({"kind": "pod", "name": "orders-api-7cc5bcf4c7-lst42", "namespace": "orders"}) — verified
   > Readiness probe failed: Get "http://10.244.0.122:8081/": dial tcp 10.244.0.122:8081: connect: connection refused
4. [link] The application actually listens on 8080, so the probe port is the wrong one.
   source: get_logs({"namespace": "orders", "pod": "orders-api-7cc5bcf4c7-lst42", "tail": 30}) — verified
   > orders-api: serving on :8080
5. [symptom] The gateway's calls to orders fail, which is the paged 5xx symptom.
   source: get_logs({"namespace": "orders", "pod": "checkout-gateway-7b867bfc46-fgfqx", "tail": 15}) — verified
   > gateway: orders fetch FAILED (timeout or connection refused)

## Investigation ledger

- Service selector does not match the pod labels — ruled out: The Service selector app=orders-api matches the pods' labels exactly; the endpoints are empty only because no pod is ready.
  source: get_object({"kind": "service", "name": "orders-api", "namespace": "orders"}) — verified
  > "selector": {
  >       "app": "orders-api"
  >     },
- Service targetPort points at the wrong container port — ruled out: The Service targets port 8080, which is exactly where the container listens, so the Service port mapping is correct.
  source: get_object({"kind": "service", "name": "orders-api", "namespace": "orders"}) — verified
  > "port": 8080,
  >         "protocol": "TCP",
  >         "targetPort": 8080
- The orders-api container is crashing, failing to pull its image, or missing its configmap volume — ruled out: The container started cleanly from a present image with zero restarts and is still Running; only the readiness probe fails.
  source: describe({"kind": "pod", "name": "orders-api-7cc5bcf4c7-lst42", "namespace": "orders"}) — verified
  > State:          Running
  >       Started:      Sat, 29 Aug 2026 07:40:38 +0530
  >     Ready:          False
  >     Restart Count:  0
- The checkout-gateway workload itself is broken — ruled out: The gateway pod is Running and Ready with no restarts; its failures are outbound calls to the orders backend.
  source: namespace_overview(orders) — verified
  > pod/checkout-gateway-7b867bfc46-fgfqx phase=Running labels={app=checkout-gateway, pod-template-hash=7b867bfc46} node=incident-lab-control-plane gateway(ready=True,restarts=0)

## Verification recipe

1. `kubectl -n orders get deploy orders-api -o jsonpath='{.spec.template.spec.containers[0].readinessProbe.httpGet.port}'` — expect to see: "port": 8081  [PRESENT]
2. `kubectl -n orders describe pod -l app=orders-api` — expect to see: dial tcp 10.244.0.122:8081: connect: connection refused  [PRESENT]
3. `kubectl -n orders logs -l app=orders-api --tail=5` — expect to see: serving on :8080  [PRESENT]
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
  "mechanism": "In deployment/orders-api the container \"api\" declares containerPort 8080 and the process logs \"serving on :8080\", but .spec.template.spec.containers[api].readinessProbe.httpGet.port is set to 8081 instead of 8080; the kubelet's readiness GET to that port is refused (\"dial tcp 10.244.0.122:8081: connect: connection refused\"), so the probe fails and the pods are held Ready=False.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
