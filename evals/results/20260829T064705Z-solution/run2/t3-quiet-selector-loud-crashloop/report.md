## Root cause

Service search in namespace search selects pods with label app=search-api, but the only pods that back it — the two Ready pods produced by deployment/search — carry the label app=search (the deployment's template labels and its own matchLabels both say app=search). No pod in the namespace carries app=search-api, so the endpoint controller populates zero addresses for the Service and its Endpoints object has no subsets. The web-gateway pod resolves the search Service ClusterIP but there is nothing behind it, so every search call fails ("gateway: search fetch FAILED (timeout or connection refused)") and shoppers get the "search is unavailable" fallback, while deployment/search still reports 2/2 Ready because the pods themselves are healthy and serving on :8080. The fix is a one-line edit to the Service selector; the deployment's selector is immutable and its pods are healthy.

Remediation: edit Service search/search, field `.spec.selector`: `{"app": "search-api"}` -> `{"app": "search"}`.

## Evidence chain

1. [symptom] The search Service has zero endpoint addresses even though both search pods are Running and Ready.
   source: namespace_overview(search) — verified
   > service/search selector={app=search-api} endpointAddresses=0
2. [symptom] The web gateway's calls to the search backend never complete.
   source: get_logs({"namespace": "search", "pod": "web-gateway-557b9db57b-65gxl", "tail": 30}) — verified
   > gateway: search fetch FAILED (timeout or connection refused)
3. [defect] The Service selector asks for app=search-api.
   source: get_object({"kind": "services", "name": "search", "namespace": "search"}) — verified
   > "selector": {
   >       "app": "search-api"
   >     }
4. [link] The deployment that backs the Service labels its pods app=search, not app=search-api.
   source: get_object({"kind": "deployments", "name": "search", "namespace": "search"}) — verified
   > "template": {
   >       "metadata": {
   >         "labels": {
   >           "app": "search"
   >         }
   >       }
5. [link] The Endpoints object for the Service contains no subsets/addresses at all.
   source: get_object({"kind": "endpoints", "name": "search", "namespace": "search"}) — verified
   > "name": "search",
   >     "namespace": "search",
6. [link] The search pods themselves are healthy and listening on the Service's target port 8080.
   source: get_logs({"namespace": "search", "pod": "search-5478989674-6rxcp", "tail": 10}) — verified
   > search: serving on :8080

## Investigation ledger

- The search pods are broken or not serving, so the Service has no ready backends. — ruled out: Both search pods are Running with ready=True and zero restarts, and the container logs show it listening on the Service's target port.
  source: get_logs({"namespace": "search", "pod": "search-5478989674-6rxcp", "tail": 10}) — verified
  > search: serving on :8080
- The loud CrashLoopBackOff in analytics-batch (report-generator) is the cause of the search outage. — ruled out: That workload lives in a different namespace and nothing in the search namespace references it; the search Service's emptiness is fully explained by its own label selector.
  source: namespace_overview({"namespace": "analytics-batch"}) — verified
  > pod/report-generator-646bc6f94f-sgdm2 phase=Running labels={app=report-generator, pod-template-hash=646bc6f94f} node=incident-lab-control-plane generator(ready=False,restarts=5,waiting=CrashLoopBackOff,lastExit=Error)
- A wrong Service port/targetPort keeps traffic from reaching the pods. — ruled out: The Service targets port 8080, which is exactly the containerPort the search container exposes and serves on, so the port mapping is correct.
  source: get_object({"kind": "services", "name": "search", "namespace": "search"}) — verified
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
  "mechanism": "Service search in namespace search has .spec.selector set to {app: search-api} while the pods of deployment/search are labelled {app: search}, so no pod matches and the endpoint controller writes an Endpoints object with zero addresses. Traffic sent to the search ClusterIP on port 8080 therefore has no backend to be forwarded to and the connection is dropped as refused or timed out.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
