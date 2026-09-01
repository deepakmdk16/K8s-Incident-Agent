## Root cause

The Deployment orders-api ships a readiness probe pointed at a port the container does not listen on. The container's only listener is on :8080 (its own log says "orders-api: serving on :8080") and its declared containerPort is 8080/TCP named http, but .spec.template.spec.containers[api].readinessProbe.httpGet.port is 8081. Every probe attempt is refused ("dial tcp 10.244.0.122:8081: connect: connection refused"), so both replicas stay Ready=False and the Deployment stays ready=0/2. Because the Service orders-api only publishes ready pods, its Endpoints has zero addresses, which is why the checkout gateway's calls to the orders backend fail and shoppers see 5xx at submission. Fix: set the readiness probe port to 8080 (or the named port http) in the Deployment's pod template.

Remediation: edit Deployment orders/orders-api, field `spec.template.spec.containers[api].readinessProbe.httpGet.port`: `8081` -> `8080 (or the named port "http")`.

## Evidence chain

1. [symptom] Both orders-api pods run but are not ready and the Service has no endpoint addresses.
   source: namespace_overview(orders) — verified
   > service/orders-api selector={app=orders-api} endpointAddresses=0
2. [defect] The Deployment pod template's readiness probe targets port 8081 while the container port is 8080.
   source: get_object({"kind": "deployments", "name": "orders-api", "namespace": "orders"}) — verified
   > "readinessProbe": {
   >               "failureThreshold": 3,
   >               "httpGet": {
   >                 "path": "/",
   >                 "port": 8081,
3. [link] The application actually listens on 8080.
   source: get_logs({"namespace": "orders", "pod": "orders-api-7cc5bcf4c7-lst42", "tail": 30}) — verified
   > orders-api: serving on :8080
4. [link] The kubelet's readiness probe to 8081 is refused, keeping the pod Ready=False.
   source: describe({"kind": "pod", "name": "orders-api-7cc5bcf4c7-lst42", "namespace": "orders"}) — verified
   > Readiness probe failed: Get "http://10.244.0.122:8081/": dial tcp 10.244.0.122:8081: connect: connection refused
5. [link] The container declares port 8080 and is Ready=False with no restarts.
   source: describe({"kind": "pod", "name": "orders-api-7cc5bcf4c7-lst42", "namespace": "orders"}) — verified
   > Port:          8080/TCP (http)

## Investigation ledger

- Service selector does not match the pod labels, so nothing is selected. — ruled out: The Service selector app=orders-api matches the pods' labels exactly; selection is not the problem, readiness is.
  source: get_object({"kind": "service", "name": "orders-api", "namespace": "orders"}) — verified
  > "selector": {
  >       "app": "orders-api"
  >     },
- Service targetPort points at the wrong container port. — ruled out: The Service forwards to targetPort 8080, which is the port the container actually serves on.
  source: get_object({"kind": "service", "name": "orders-api", "namespace": "orders"}) — verified
  > "port": 8080,
  >         "protocol": "TCP",
  >         "targetPort": 8080
- The container is crashing or failing to start (bad image, missing ConfigMap volume). — ruled out: The container started and stays Running with zero restarts, and its script mounted from the ConfigMap ran successfully.
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
  "mechanism": "In the Deployment orders-api, .spec.template.spec.containers[api].readinessProbe.httpGet.port is 8081 while the container listens only on 8080 (its declared containerPort http). The probe's TCP connection to 8081 is refused on every attempt, so the kubelet keeps marking both replicas Ready=False and the Deployment remains stuck at ready=0/2 with no pod ever passing readiness.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
