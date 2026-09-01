## Root cause

Service search/search selects pods with the label app=search-api, but the pods produced by Deployment search/search carry the label app=search (its .spec.template.metadata.labels is {app=search}). Nothing in the namespace carries app=search-api, so the endpoint controller populates no addresses for the Service — its Endpoints object has no subsets and endpointAddresses=0. The two search pods are Running, ready, zero restarts and logging "search: serving on :8080", so the backend itself is healthy; the gateway's calls to the search ClusterIP have no backend to be routed to and log "gateway: search fetch FAILED (timeout or connection refused)", which surfaces as storewide search timeouts. Fixing this means editing the Service selector back to app=search.

Remediation: edit Service search/search, field `.spec.selector.app`: `search-api` -> `search`.

## Evidence chain

1. [symptom] The Service has no endpoint addresses even though both search pods are Running and ready.
   source: namespace_overview(search) — verified
   > service/search selector={app=search-api} endpointAddresses=0
2. [symptom] The gateway's calls to the search backend never complete.
   source: get_logs({"namespace": "search", "pod": "web-gateway-557b9db57b-65gxl", "tail": 30}) — verified
   > gateway: search fetch FAILED (timeout or connection refused)
3. [defect] The Service selector asks for app=search-api.
   source: get_object({"kind": "service", "name": "search", "namespace": "search"}) — verified
   > "selector": {
   >       "app": "search-api"
   >     }
4. [link] The Deployment's pod template labels pods app=search, which does not match the Service selector.
   source: get_object({"kind": "deployment", "name": "search", "namespace": "search"}) — verified
   > "template": {
   >       "metadata": {
   >         "labels": {
   >           "app": "search"
   >         }
5. [link] The Endpoints object for the Service contains no subsets at all.
   source: get_object({"kind": "endpoints", "name": "search", "namespace": "search"}) — verified
   > "name": "search",
   >     "namespace": "search",

## Investigation ledger

- The search backend pods are themselves broken or not listening on 8080. — ruled out: The search container logs that it is serving on :8080 and both pods report ready with zero restarts.
  source: get_logs({"namespace": "search", "pod": "search-5478989674-6rxcp", "tail": 10}) — verified
  > search: serving on :8080
- The web-gateway workload is misconfigured (wrong image, missing mount) and is the thing to edit. — ruled out: The web-gateway Deployment is intact, ready 1/1, and its only failure is the outbound call to the search Service.
  source: get_object({"kind": "deployment", "name": "web-gateway", "namespace": "search"}) — verified
  > "name": "web-gateway-scripts"
- A noisier crashlooping workload elsewhere in the cluster (analytics-batch has a not-ready pod) is the cause. — ruled out: That workload is in a different namespace with no reference from the search Service or the gateway; the search Service's own selector fully explains the empty endpoints.
  source: namespace_overview(search) — verified
  > service/search selector={app=search-api} endpointAddresses=0

## Verification recipe

1. `kubectl -n search get service search -o jsonpath='{.spec.selector}'` — expect to see: search-api  [PRESENT]
2. `kubectl -n search get deployment search -o jsonpath='{.spec.template.metadata.labels}'` — expect to see: "app": "search"  [PRESENT]
3. `kubectl -n search logs deploy/web-gateway --tail=5` — expect to see: search fetch FAILED  [PRESENT]
(Each command was re-executed against the captured cluster state; PRESENT/ABSENT is the measured result, not a prediction.)

Verdict: confirmed. Every quote below was re-checked against the tool output it cites, a defect observation names the failing object, an alternative was ruled out with evidence, and every verification command was re-run and found present.

```json
{
  "case_id": "t3-quiet-selector-loud-crashloop",
  "failing_resource": {
    "kind": "Service",
    "namespace": "search",
    "name": "search"
  },
  "mechanism": "Service search/search has .spec.selector set to {\"app\": \"search-api\"}, but the pods it is meant to front are labelled app=search, so no pod matches and the endpoint controller writes an Endpoints object search/search with no subsets (endpointAddresses=0). Traffic sent to the Service's ClusterIP 10.96.24.225:8080 has zero backend addresses and is refused/dropped rather than delivered to the ready search pods.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
