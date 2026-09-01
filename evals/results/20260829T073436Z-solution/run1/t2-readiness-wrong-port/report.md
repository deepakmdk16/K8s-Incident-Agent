## Root cause

The orders-api Deployment's pod template declares a readiness probe against HTTP port 8081, but the container process listens on port 8080 (the same port declared as containerPort "http" and targeted by the orders-api Service). Every readiness probe therefore gets a TCP connection refused, both orders-api pods stay Ready=False forever, and because a Service only publishes ready pods, the orders-api Service ends up with zero endpoint addresses. The checkout-gateway's calls to the orders backend then fail with connection refused, which is the 5xx storm at the gateway. The application itself is healthy; only the probe port in the Deployment template is wrong.

Remediation: edit Deployment orders/orders-api, field `spec.template.spec.containers[api].readinessProbe.httpGet.port`: `8081` -> `8080`.

## Evidence chain

1. [symptom] Both orders-api pods run but are not ready and the Service has no endpoints.
   source: namespace_overview(orders) — verified
   > deployment/orders-api ready=0/2 podLabels={app=orders-api}
2. [symptom] The orders-api Service has zero endpoint addresses despite a matching selector.
   source: namespace_overview(orders) — verified
   > service/orders-api selector={app=orders-api} endpointAddresses=0
3. [defect] The Deployment pod template probes port 8081 while declaring containerPort 8080.
   source: get_object({"kind": "deployments", "name": "orders-api", "namespace": "orders"}) — verified
   > "readinessProbe": {
   >               "failureThreshold": 3,
   >               "httpGet": {
   >                 "path": "/",
   >                 "port": 8081,
4. [link] The readiness probe is refused at 8081 on the pod IP.
   source: describe({"kind": "pod", "name": "orders-api-7cc5bcf4c7-lst42", "namespace": "orders"}) — verified
   > Readiness probe failed: Get "http://10.244.0.122:8081/": dial tcp 10.244.0.122:8081: connect: connection refused
5. [link] The application actually listens on 8080, not 8081.
   source: get_logs({"namespace": "orders", "pod": "orders-api-7cc5bcf4c7-lst42", "tail": 30}) — verified
   > orders-api: serving on :8080
6. [link] The gateway's calls to the orders backend fail, matching the paged 5xx symptom.
   source: get_logs({"namespace": "orders", "pod": "checkout-gateway-7b867bfc46-fgfqx", "tail": 15}) — verified
   > gateway: orders fetch FAILED (timeout or connection refused)

## Investigation ledger

- The Service selector does not match the pod labels, so nothing is selected. — ruled out: The Service selector app=orders-api matches the pods' labels exactly; the pods are excluded only because they are not ready.
  source: get_object({"kind": "service", "name": "orders-api", "namespace": "orders"}) — verified
  > "selector": {
  >       "app": "orders-api"
  >     },
- The Service targets the wrong port on the pods. — ruled out: The Service targetPort 8080 matches the port the application logs it is serving on.
  source: get_object({"kind": "service", "name": "orders-api", "namespace": "orders"}) — verified
  > "port": 8080,
  >         "protocol": "TCP",
  >         "targetPort": 8080
- The container is crashing, failing to pull its image, or failing to mount the orders-api-scripts ConfigMap. — ruled out: The container started cleanly with zero restarts and stays Running; only the readiness probe fails.
  source: describe({"kind": "pod", "name": "orders-api-7cc5bcf4c7-lst42", "namespace": "orders"}) — verified
  > State:          Running
  >       Started:      Sat, 29 Aug 2026 07:40:38 +0530
  >     Ready:          False
  >     Restart Count:  0
- The checkout-gateway deployment is itself broken and is the source of the 5xx. — ruled out: The gateway pod is Running and Ready with no restarts; its failures are outbound calls to the orders backend.
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
  "mechanism": "In the Deployment orders-api, .spec.template.spec.containers[api].readinessProbe.httpGet.port is 8081 while the container serves on 8080 (its declared containerPort \"http\"), so the kubelet's readiness HTTP GET is refused at the TCP level on every attempt and both replicas produced by this Deployment hold Ready=False, leaving it at ready=0/2 indefinitely.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
