## Root cause

The Deployment orders-api ships a readiness probe that points at the wrong port. Its pod template declares containerPort 8080 (name http) and the container itself logs "orders-api: serving on :8080", but the readiness probe is configured as httpGet on port 8081. Nothing listens on 8081 inside the pod, so every probe attempt is refused ("dial tcp 10.244.0.122:8081: connect: connection refused") and both replicas stay Ready=False. Because the EndpointSlice for Service orders-api only admits ready pods, the Service (whose selector app=orders-api does match the pod labels) carries zero endpoint addresses, and the checkout-gateway's calls to the orders backend fail, which is the 5xx the gateway monitor paged on.

Remediation: edit Deployment orders/orders-api, field `spec.template.spec.containers[api].readinessProbe.httpGet.port`: `8081` -> `8080`.

## Evidence chain

1. [symptom] The orders-api Deployment has no ready replicas and its Service has no endpoint addresses.
   source: namespace_overview(orders) — verified
   > deployment/orders-api ready=0/2 podLabels={app=orders-api}
2. [symptom] The Service orders-api has zero endpoint addresses despite a matching selector.
   source: namespace_overview(orders) — verified
   > service/orders-api selector={app=orders-api} endpointAddresses=0
3. [defect] The Deployment pod template sets the readiness probe to port 8081 while declaring containerPort 8080.
   source: get_object({"kind": "deployments", "name": "orders-api", "namespace": "orders"}) — verified
   > "readinessProbe": {
   >               "failureThreshold": 3,
   >               "httpGet": {
   >                 "path": "/",
   >                 "port": 8081,
4. [link] The container port declared in the same template is 8080.
   source: get_object({"kind": "deployments", "name": "orders-api", "namespace": "orders"}) — verified
   > "containerPort": 8080,
   >                 "name": "http",
5. [link] The application actually listens on 8080.
   source: get_logs({"namespace": "orders", "pod": "orders-api-7cc5bcf4c7-lst42", "tail": 30}) — verified
   > orders-api: serving on :8080
6. [link] The kubelet readiness probe to 8081 is refused, so the container stays not ready.
   source: describe({"kind": "pod", "name": "orders-api-7cc5bcf4c7-lst42", "namespace": "orders"}) — verified
   > Readiness probe failed: Get "http://10.244.0.122:8081/": dial tcp 10.244.0.122:8081: connect: connection refused
7. [symptom] The gateway's calls to the orders backend do not complete.
   source: get_logs({"namespace": "orders", "pod": "checkout-gateway-7b867bfc46-fgfqx", "tail": 15}) — verified
   > gateway: orders fetch FAILED (timeout or connection refused)

## Investigation ledger

- Service selector mismatch (Service selecting labels no pod carries) — ruled out: The Service selector app=orders-api exactly matches the pod labels, so selection is not the problem; the pods are simply not ready.
  source: get_object({"kind": "services", "name": "orders-api", "namespace": "orders"}) — verified
  > "selector": {
  >       "app": "orders-api"
  >     },
- Service targetPort pointing at the wrong container port — ruled out: The Service targets port 8080, which is exactly where the container listens, so traffic routing configuration is correct.
  source: get_object({"kind": "services", "name": "orders-api", "namespace": "orders"}) — verified
  > "port": 8080,
  >         "protocol": "TCP",
  >         "targetPort": 8080
- Application crash-looping or failing to start (bad image, missing ConfigMap script) — ruled out: The container started once, is Running with zero restarts, and logs a successful bind, so the process itself is healthy.
  source: describe({"kind": "pod", "name": "orders-api-7cc5bcf4c7-lst42", "namespace": "orders"}) — verified
  > State:          Running
  >       Started:      Sat, 29 Aug 2026 07:40:38 +0530
  >     Ready:          False
  >     Restart Count:  0
- The checkout-gateway Deployment is itself broken — ruled out: The gateway pod is Running and Ready with no restarts; it only reports failures reaching the orders backend.
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
  "mechanism": "In the Deployment orders-api, .spec.template.spec.containers[api].readinessProbe.httpGet.port is 8081 while the container listens on 8080 (the same value declared in .spec.template.spec.containers[api].ports[http].containerPort), so the kubelet's HTTP readiness check is refused on every attempt and both replicas produced by the Deployment hold Ready=False, leaving it at ready=0/2 indefinitely.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
