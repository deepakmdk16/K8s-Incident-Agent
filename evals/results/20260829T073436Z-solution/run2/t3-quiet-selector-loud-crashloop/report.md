## Root cause

The Service search in namespace search selects pods with the label app=search-api, but the Deployment search stamps its pods with app=search. No pod in the namespace carries app=search-api, so the endpoint controller populates no addresses for the Service and its Endpoints object has no subsets. Both search pods are Running, Ready and logging "search: serving on :8080", so the backend itself is healthy; the traffic simply never reaches it, and the web-gateway pod logs a continuous stream of "gateway: search fetch FAILED (timeout or connection refused)", which is the storewide search timeout the page describes. Fixing it is a one-line edit: set the Service's .spec.selector.app to search, matching the Deployment pod template labels.

Remediation: edit Service search/search, field `.spec.selector.app`: `search-api` -> `search`.

## Evidence chain

1. [symptom] The paged Service has no endpoint addresses even though the search Deployment is fully ready.
   source: namespace_overview(search) — verified
   > service/search selector={app=search-api} endpointAddresses=0
2. [symptom] The web gateway in the same namespace cannot complete calls to the search backend.
   source: get_logs({"namespace": "search", "pod": "web-gateway-557b9db57b-65gxl", "tail": 30}) — verified
   > gateway: search fetch FAILED (timeout or connection refused)
3. [defect] The Service selector asks for app=search-api.
   source: get_object({"kind": "service", "name": "search", "namespace": "search"}) — verified
   > "selector": {
   >       "app": "search-api"
   >     }
4. [link] The Deployment's pod template labels the pods app=search, which does not match the Service selector.
   source: get_object({"kind": "deployment", "name": "search", "namespace": "search"}) — verified
   > "template": {
   >       "metadata": {
   >         "labels": {
   >           "app": "search"
   >         }
5. [link] The Endpoints object search has no subsets/addresses at all.
   source: get_object({"kind": "endpoints", "name": "search", "namespace": "search"}) — verified
   > "name": "search",
   >     "namespace": "search",

## Investigation ledger

- The search backend pods are themselves broken or not serving. — ruled out: A search pod logs that it is serving on port 8080 and both replicas are Ready with zero restarts.
  source: get_logs({"namespace": "search", "pod": "search-5478989674-6rxcp", "tail": 10}) — verified
  > search: serving on :8080
- The loud CrashLoopBackOff workload report-generator in analytics-batch is the cause of the search outage. — ruled out: That crashing pod lives in a different namespace and is referenced by nothing in the search path; the search Service selector alone explains the empty Endpoints.
  source: namespace_overview({"namespace": "analytics-batch"}) — verified
  > pod/report-generator-646bc6f94f-sgdm2 phase=Running labels={app=report-generator, pod-template-hash=646bc6f94f} node=incident-lab-control-plane generator(ready=False,restarts=5,waiting=CrashLoopBackOff,lastExit=Error)
- Readiness probe failures kept pods out of the Endpoints list. — ruled out: Both search pods report ready=True, so readiness is not what removed them from the endpoint set.
  source: namespace_overview(search) — verified
  > pod/search-5478989674-pswt8 phase=Running labels={app=search, pod-template-hash=5478989674} node=incident-lab-control-plane api(ready=True,restarts=0)

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
  "mechanism": "The Service search in namespace search has .spec.selector set to {app: search-api} while the pods it is meant to front are labelled app=search, so the selector matches zero pods and the endpoint controller writes an Endpoints object search with no addresses. With an empty backend set, kube-proxy has no destination for the Service's ClusterIP 10.96.24.225:8080 and every TCP connection to it is dropped or refused rather than being load-balanced to a pod.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
