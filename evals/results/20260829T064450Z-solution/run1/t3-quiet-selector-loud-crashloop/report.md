## Root cause

Service search in namespace search selects pods with label app=search-api, but the search Deployment labels its pods app=search. No pod matches the Service selector, so the endpoint controller populated no addresses for the Service and its Endpoints object has no subsets. Traffic the web-gateway sends to the search ClusterIP therefore has no backend to be routed to, and the gateway's calls fail, which is what the gateway latency monitor paged on. The search pods themselves are healthy and serving on :8080; only the Service's selector needs to change to app=search.

Remediation: edit Service search/search, field `.spec.selector.app`: `search-api` -> `search`.

## Evidence chain

1. [symptom] The gateway's calls to the search backend do not complete
   source: get_logs({"namespace": "search", "pod": "web-gateway-557b9db57b-65gxl", "tail": 30}) — verified
   > gateway: search fetch FAILED (timeout or connection refused)
2. [symptom] The search Service has zero endpoint addresses even though both search pods are Ready
   source: namespace_overview(search) — verified
   > service/search selector={app=search-api} endpointAddresses=0
3. [defect] The Service selector asks for app=search-api
   source: get_object({"kind": "service", "name": "search", "namespace": "search"}) — verified
   > "selector": {
   >       "app": "search-api"
   >     }
4. [link] The deployment's pod template labels pods app=search, which does not match the selector
   source: get_object({"kind": "deployment", "name": "search", "namespace": "search"}) — verified
   > "template": {
   >       "metadata": {
   >         "labels": {
   >           "app": "search"
   >         }
5. [link] The Endpoints object for the Service has no subsets/addresses at all
   source: get_object({"kind": "endpoints", "name": "search", "namespace": "search"}) — verified
   > "name": "search",
   >     "namespace": "search",
6. [link] The search backend pods are healthy and serving on port 8080, so the failure is not in the backend
   source: get_logs({"namespace": "search", "pod": "search-5478989674-6rxcp", "tail": 10}) — verified
   > search: serving on :8080

## Investigation ledger

- The search backend pods are themselves broken or not listening — ruled out: The search container logs show it started and is serving on port 8080, and both pods report ready with zero restarts
  source: get_logs({"namespace": "search", "pod": "search-5478989674-6rxcp", "tail": 10}) — verified
  > search: serving on :8080
- The loud CrashLoopBackOff in analytics-batch is the cause of the search outage — ruled out: The crashing workload is in a different namespace with no reference from the search Service or the gateway path; it is unrelated to the search Service's empty endpoints
  source: namespace_overview({"namespace": "analytics-batch"}) — verified
  > pod/report-generator-646bc6f94f-sgdm2 phase=Running labels={app=report-generator, pod-template-hash=646bc6f94f} node=incident-lab-control-plane generator(ready=False,restarts=5,waiting=CrashLoopBackOff,lastExit=Error)
- Wrong Service port/targetPort so traffic hits a closed port — ruled out: The Service targets port 8080, matching the container's declared containerPort 8080; the mismatch is in the label selector, not the port
  source: get_object({"kind": "service", "name": "search", "namespace": "search"}) — verified
  > "port": 8080,
  >         "protocol": "TCP",
  >         "targetPort": 8080

## Verification recipe

1. `kubectl -n search get service search -o jsonpath='{.spec.selector}'` — expect to see: search-api  [PRESENT]
2. `kubectl -n search get endpoints search -o yaml` — expect to see: "name": "search"  [PRESENT]
3. `kubectl -n search get deployment search -o jsonpath='{.spec.template.metadata.labels}'` — expect to see: "app": "search"  [PRESENT]
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
  "mechanism": "The Service search in namespace search has .spec.selector set to {app: search-api}, while the pods produced by deployment/search carry the label app=search, so no pod matches and the Service's Endpoints object contains no addresses; connections to the search ClusterIP:8080 have no backend and are refused/never answered.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
