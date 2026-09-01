## Root cause

The Deployment orders-api ships a readiness probe pointed at the wrong port. Its pod template declares containerPort 8080 (name http) and the container itself logs "orders-api: serving on :8080", but .spec.template.spec.containers[api].readinessProbe.httpGet.port is 8081. Nothing listens on 8081, so every probe attempt is refused ("dial tcp 10.244.0.122:8081: connect: connection refused") and both replicas stay Ready=False, leaving deployment orders-api at ready=0/2. Because only ready pods become Endpoints addresses, the Service orders-api holds both pod IPs as notReadyAddresses and zero endpoint addresses, so the checkout gateway's calls to the orders backend fail ("gateway: orders fetch FAILED (timeout or connection refused)") and checkout returns 5xx. Fix: set the readiness probe port back to 8080 (or the named port http) in the Deployment's pod template.

Remediation: edit Deployment orders/orders-api, field `spec.template.spec.containers[api].readinessProbe.httpGet.port`: `8081` -> `8080`.

## Evidence chain

1. [symptom] Deployment orders-api has no ready replicas and both pods are Running but not ready
   source: namespace_overview(orders) — verified
   > deployment/orders-api ready=0/2 podLabels={app=orders-api}
2. [symptom] The Service orders-api has zero endpoint addresses
   source: namespace_overview(orders) — verified
   > service/orders-api selector={app=orders-api} endpointAddresses=0
3. [defect] The pod template declares containerPort 8080 but the readiness probe targets port 8081
   source: get_object({"kind": "deployments", "name": "orders-api", "namespace": "orders"}) — verified
   > "readinessProbe": {
   >               "failureThreshold": 3,
   >               "httpGet": {
   >                 "path": "/",
   >                 "port": 8081,
4. [link] The container actually listens on 8080
   source: get_logs({"namespace": "orders", "pod": "orders-api-7cc5bcf4c7-lst42", "tail": 30}) — verified
   > orders-api: serving on :8080
5. [link] The readiness probe to 8081 is refused, keeping the pod not ready
   source: describe({"kind": "pod", "name": "orders-api-7cc5bcf4c7-lst42", "namespace": "orders"}) — verified
   > Readiness probe failed: Get "http://10.244.0.122:8081/": dial tcp 10.244.0.122:8081: connect: connection refused
6. [link] Both pod IPs sit in notReadyAddresses of the Endpoints, so the Service routes nothing
   source: get_object({"kind": "endpoints", "name": "orders-api", "namespace": "orders"}) — verified
   > "notReadyAddresses": [
7. [symptom] The checkout gateway cannot complete calls to the orders backend
   source: get_logs({"namespace": "orders", "pod": "checkout-gateway-7b867bfc46-fgfqx", "tail": 15}) — verified
   > gateway: orders fetch FAILED (timeout or connection refused)

## Investigation ledger

- Service selector mismatch (labels not matching pods) — ruled out: The Service selector app=orders-api matches the pod labels, and the endpoint controller did select both pods — they are listed as notReadyAddresses, not absent.
  source: get_object({"kind": "endpoints", "name": "orders-api", "namespace": "orders"}) — verified
  > "name": "orders-api-7cc5bcf4c7-lst42",
- Application crash / bad image / missing ConfigMap script preventing the server from starting — ruled out: The container is Running with zero restarts and logged that it is serving, so the process started fine and the volume-mounted script executed.
  source: describe({"kind": "pod", "name": "orders-api-7cc5bcf4c7-lst42", "namespace": "orders"}) — verified
  > State:          Running
  >       Started:      Sat, 29 Aug 2026 07:40:38 +0530
- Service port/targetPort misconfiguration sending traffic to a wrong container port — ruled out: The Service targets port 8080, which is the port the container actually serves on.
  source: get_object({"kind": "services", "name": "orders-api", "namespace": "orders"}) — verified
  > "port": 8080,
  >         "protocol": "TCP",
  >         "targetPort": 8080
- The checkout-gateway Deployment is itself broken — ruled out: The gateway pod is Running and ready with no restarts; it only reports failures on its outbound orders fetch.
  source: namespace_overview(orders) — verified
  > pod/checkout-gateway-7b867bfc46-fgfqx phase=Running labels={app=checkout-gateway, pod-template-hash=7b867bfc46} node=incident-lab-control-plane gateway(ready=True,restarts=0)

## Verification recipe

1. `kubectl -n orders get deployment orders-api -o jsonpath='{.spec.template.spec.containers[0].readinessProbe.httpGet.port}'` — expect to see: "port": 8081  [PRESENT]
2. `kubectl -n orders describe pod orders-api-7cc5bcf4c7-lst42` — expect to see: dial tcp 10.244.0.122:8081: connect: connection refused  [PRESENT]
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
  "mechanism": "The Deployment orders-api has .spec.template.spec.containers[api].readinessProbe.httpGet.port set to 8081 while the container's only listener is on 8080 (declared as containerPort 8080 name http, and the process logs \"serving on :8080\"). Each probe to port 8081 is refused with \"connect: connection refused\", so the kubelet keeps marking both replicas Ready=False and the Deployment stays stuck at ready=0/2 with no pod ever passing readiness.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
