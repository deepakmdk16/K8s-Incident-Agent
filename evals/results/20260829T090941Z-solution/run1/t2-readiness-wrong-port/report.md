## Root cause

Deployment orders/orders-api was released with a readiness probe pointed at the wrong port. The container serves HTTP on 8080 (log line "orders-api: serving on :8080", and the container declares containerPort 8080 named http), but .spec.template.spec.containers[api].readinessProbe.httpGet.port is set to 8081, where nothing listens. Every probe attempt is refused ("dial tcp 10.244.0.122:8081: connect: connection refused"), so both pods stay Ready=False and the Deployment reports ready=0/2. Because only ready pods become Endpoints addresses, Service orders/orders-api has endpointAddresses=0, and checkout-gateway's calls to the orders backend fail ("gateway: orders fetch FAILED (timeout or connection refused)"), which is the 5xx the gateway monitor paged on.

Remediation: edit Deployment orders/orders-api, field `spec.template.spec.containers[api].readinessProbe.httpGet.port`: `8081` -> `8080`.

## Evidence chain

1. [symptom] The paged gateway 5xx correspond to failing calls to the orders backend.
   source: get_logs({"namespace": "orders", "pod": "checkout-gateway-7b867bfc46-fgfqx", "tail": 15}) — verified
   > gateway: orders fetch FAILED (timeout or connection refused)
2. [symptom] Both orders-api pods run but are not ready and the Service has no endpoints.
   source: namespace_overview(orders) — verified
   > service/orders-api selector={app=orders-api} endpointAddresses=0
3. [defect] The Deployment pod template probes port 8081 while declaring containerPort 8080.
   source: get_object({"kind": "deployment", "name": "orders-api", "namespace": "orders"}) — verified
   > "readinessProbe": {
   >               "failureThreshold": 3,
   >               "httpGet": {
   >                 "path": "/",
   >                 "port": 8081,
4. [link] The application actually listens on 8080, not 8081.
   source: get_logs({"namespace": "orders", "pod": "orders-api-7cc5bcf4c7-lst42", "tail": 30}) — verified
   > orders-api: serving on :8080
5. [link] The kubelet's readiness probe to 8081 is refused, keeping the container Ready=False.
   source: describe({"kind": "pod", "name": "orders-api-7cc5bcf4c7-lst42", "namespace": "orders"}) — verified
   > Readiness probe failed: Get "http://10.244.0.122:8081/": dial tcp 10.244.0.122:8081: connect: connection refused

## Investigation ledger

- Service selector does not match the pod labels, so nothing is selected. — ruled out: The Service selector app=orders-api matches the pods' labels; the pods are excluded only because they are not ready.
  source: get_object({"kind": "service", "name": "orders-api", "namespace": "orders"}) — verified
  > "selector": {
  >       "app": "orders-api"
  >     },
- The Service targets the wrong port on the pods. — ruled out: Service orders/orders-api targetPort 8080 matches the port the container serves on, so the Service port mapping is correct.
  source: get_object({"kind": "service", "name": "orders-api", "namespace": "orders"}) — verified
  > "port": 8080,
  >         "protocol": "TCP",
  >         "targetPort": 8080
- The container is crashing or failing to start (bad image, missing configmap/volume). — ruled out: The container started successfully and stays Running with zero restarts; only readiness fails.
  source: describe({"kind": "pod", "name": "orders-api-7cc5bcf4c7-lst42", "namespace": "orders"}) — verified
  > State:          Running
  >       Started:      Sat, 29 Aug 2026 07:40:38 +0530
  >     Ready:          False
  >     Restart Count:  0

## Verification recipe

1. `kubectl -n orders get deploy orders-api -o jsonpath='{.spec.template.spec.containers[0].readinessProbe.httpGet.port}'` — expect to see: 8081  [PRESENT]
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
  "mechanism": "Deployment orders/orders-api sets .spec.template.spec.containers[api].readinessProbe.httpGet.port to 8081 while the api container listens on 8080 (\"orders-api: serving on :8080\", declared as containerPort 8080 named http), so the kubelet's readiness HTTP GET is rejected with `Readiness probe failed: Get \"http://10.244.0.122:8081/\": dial tcp 10.244.0.122:8081: connect: connection refused`. The kubelet therefore keeps both replicas marked Ready=False (restarts=0, State Running) and the Deployment stays at ready=0/2.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
