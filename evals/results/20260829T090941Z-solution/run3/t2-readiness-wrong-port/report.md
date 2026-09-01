## Root cause

Deployment orders/orders-api was released with its readiness probe pointed at the wrong port. The container listens on 8080 (its own log says "orders-api: serving on :8080", and the container port declared in the template is 8080), but .spec.template.spec.containers[api].readinessProbe.httpGet.port is 8081. Nothing is bound to 8081 in the pod, so every readiness probe gets "connect: connection refused" and both pods stay Ready=False. Because Endpoints only publish ready pods, Service orders/orders-api holds both pod IPs under notReadyAddresses and has zero endpoint addresses, so checkout-gateway's calls to the orders backend have nowhere to land ("gateway: orders fetch FAILED (timeout or connection refused)"), which surfaces as 5xx at the checkout gateway. Fix: set the readiness probe port to 8080 in the Deployment template.

Remediation: edit Deployment orders/orders-api, field `spec.template.spec.containers[api].readinessProbe.httpGet.port`: `8081` -> `8080`.

## Evidence chain

1. [symptom] Both orders-api pods run but are not ready and the Service has no endpoint addresses.
   source: namespace_overview(orders) — verified
   > deployment/orders-api ready=0/2 podLabels={app=orders-api}
2. [symptom] Service orders-api has zero endpoint addresses.
   source: namespace_overview(orders) — verified
   > service/orders-api selector={app=orders-api} endpointAddresses=0
3. [defect] The Deployment's readiness probe targets port 8081 while the declared container port is 8080.
   source: get_object({"kind": "deployment", "name": "orders-api", "namespace": "orders"}) — verified
   > "readinessProbe": {
   >               "failureThreshold": 3,
   >               "httpGet": {
   >                 "path": "/",
   >                 "port": 8081,
4. [link] The probe is refused at 8081 on the pod IP.
   source: describe({"kind": "pod", "name": "orders-api-7cc5bcf4c7-lst42", "namespace": "orders"}) — verified
   > Readiness probe failed: Get "http://10.244.0.122:8081/": dial tcp 10.244.0.122:8081: connect: connection refused
5. [link] The application actually listens on 8080.
   source: get_logs({"namespace": "orders", "pod": "orders-api-7cc5bcf4c7-lst42", "tail": 20}) — verified
   > orders-api: serving on :8080
6. [link] Both pod IPs are held as notReadyAddresses, so no traffic is routed to them.
   source: get_object({"kind": "endpoints", "name": "orders-api", "namespace": "orders"}) — verified
   > "notReadyAddresses": [
7. [symptom] The gateway's calls to the orders backend do not complete.
   source: get_logs({"namespace": "orders", "pod": "checkout-gateway-7b867bfc46-fgfqx", "tail": 10}) — verified
   > gateway: orders fetch FAILED (timeout or connection refused)

## Investigation ledger

- Service selector does not match the pod labels — ruled out: The Service selector app=orders-api matches the pod labels exactly; the endpoint controller does select both pods, it just lists them as not ready.
  source: get_object({"kind": "endpoints", "name": "orders-api", "namespace": "orders"}) — verified
  > "name": "orders-api-7cc5bcf4c7-lst42",
- Service targetPort points at the wrong container port — ruled out: Service orders-api targets port 8080, which is the port the container actually serves on, so the Service port mapping is correct.
  source: get_object({"kind": "service", "name": "orders-api", "namespace": "orders"}) — verified
  > "port": 8080,
  >         "protocol": "TCP",
  >         "targetPort": 8080
- The application is crashing or failing to start (bad image, missing configmap volume) — ruled out: The container started, has zero restarts and is Running with its script volume mounted; only readiness is failing.
  source: describe({"kind": "pod", "name": "orders-api-7cc5bcf4c7-lst42", "namespace": "orders"}) — verified
  > State:          Running

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
  "mechanism": "Deployment orders/orders-api sets .spec.template.spec.containers[api].readinessProbe.httpGet.port to 8081 while the api container listens on 8080 (\"orders-api: serving on :8080\", containerPort 8080), so the kubelet's readiness HTTP GET is refused \u2014 'Readiness probe failed: Get \"http://10.244.0.122:8081/\": dial tcp 10.244.0.122:8081: connect: connection refused' \u2014 and the kubelet keeps both pods marked Ready=False (deployment ready=0/2) as the probe repeats every 5s.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
