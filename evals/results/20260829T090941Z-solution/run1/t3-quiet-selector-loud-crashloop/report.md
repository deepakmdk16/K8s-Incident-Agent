## Root cause

Service search/search selects pods with label app=search-api, but the only pods backing the search backend are produced by Deployment search/search whose pod template labels them app=search. No pod in the namespace carries app=search-api, so the endpoint controller writes an Endpoints object with no addresses and the ClusterIP 10.96.24.225:8080 has nothing to route to. The search pods themselves are healthy and serving ("search: serving on :8080", both Ready with 0 restarts), which is why the Deployment reports 2/2 Ready while the gateway's calls fail: pod web-gateway-557b9db57b-65gxl logs "gateway: search fetch FAILED (timeout or connection refused)" every six seconds, surfacing storewide as the "search is unavailable" fallback.

Remediation: edit Service search/search, field `.spec.selector.app`: `search-api` -> `search`.

## Evidence chain

1. [symptom] Service search/search has zero endpoint addresses even though both search pods are Running and Ready
   source: namespace_overview(search) — verified
   > service/search selector={app=search-api} endpointAddresses=0
2. [defect] The Service selector asks for app=search-api
   source: get_object({"kind": "service", "name": "search", "namespace": "search"}) — verified
   > "selector": {
   >       "app": "search-api"
   >     }
3. [link] The Deployment's pod template labels pods app=search, which cannot match app=search-api
   source: get_object({"kind": "deployment", "name": "search", "namespace": "search"}) — verified
   > "template": {
   >       "metadata": {
   >         "labels": {
   >           "app": "search"
   >         }
   >       }
4. [link] Endpoints search/search contains no subsets/addresses
   source: get_object({"kind": "endpoints", "name": "search", "namespace": "search"}) — verified
   > "name": "search",
   >     "namespace": "search",
5. [symptom] The gateway's calls to the search Service fail continuously
   source: get_logs({"namespace": "search", "pod": "web-gateway-557b9db57b-65gxl", "tail": 30}) — verified
   > gateway: search fetch FAILED (timeout or connection refused)
6. [link] The search backend itself is healthy and listening on 8080
   source: get_logs({"namespace": "search", "pod": "search-5478989674-6rxcp", "tail": 10}) — verified
   > search: serving on :8080

## Investigation ledger

- The search backend pods are crashed or not serving — ruled out: Both search pods are Running with their container Ready and zero restarts, and the container logs show it listening on port 8080
  source: get_logs({"namespace": "search", "pod": "search-5478989674-6rxcp", "tail": 10}) — verified
  > search: serving on :8080
- The web-gateway deployment is itself unhealthy and generating the failures locally — ruled out: The gateway pod is Running and Ready with no restarts; its only complaint is that the outbound search call does not complete
  source: namespace_overview(search) — verified
  > pod/web-gateway-557b9db57b-65gxl phase=Running labels={app=web-gateway, pod-template-hash=557b9db57b} node=incident-lab-control-plane gateway(ready=True,restarts=0)
- The Service port/targetPort is wrong so traffic hits a closed port — ruled out: The Service targets port 8080 which is exactly the containerPort named http that the search container exposes and serves on
  source: get_object({"kind": "service", "name": "search", "namespace": "search"}) — verified
  > "port": 8080,
  >         "protocol": "TCP",
  >         "targetPort": 8080

## Verification recipe

1. `kubectl -n search get service search -o jsonpath='{.spec.selector}'` — expect to see: search-api  [PRESENT]
2. `kubectl -n search get endpoints search -o yaml` — expect to see: "name": "search"  [PRESENT]
3. `kubectl -n search get pods --show-labels` — expect to see: app=search  [PRESENT]
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
  "mechanism": "Service search/search has .spec.selector set to {app: search-api} while the pods created by Deployment search/search carry the label app=search, so no pod matches and Endpoints search/search is written with no addresses subset at all; every connection to the Service's ClusterIP 10.96.24.225:8080 has no backend to be routed to and is refused or hangs.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
