## Root cause

The Service search in namespace search selects pods with the label app=search-api, but the pods produced by deployment/search carry the label app=search (the deployment's pod template sets labels app=search). No pod in the namespace matches the Service selector, so the Endpoints object for the Service has no addresses at all and every connection the web gateway makes to search:8080 is refused/dropped at the ClusterIP. The search pods themselves are healthy and serving on :8080, and the deployment correctly reports 2/2 Ready, which is why the outage is invisible from the workload's status. The fix is to edit the Service selector to app=search so it matches the pod template labels.

Remediation: edit Service search/search, field `.spec.selector.app`: `search-api` -> `search`.

## Evidence chain

1. [symptom] The gateway's calls to the search backend fail with connection errors.
   source: get_logs({"namespace": "search", "pod": "web-gateway-557b9db57b-65gxl", "tail": 30}) — verified
   > gateway: search fetch FAILED (timeout or connection refused)
2. [symptom] The search Service has no endpoint addresses even though the deployment is fully ready.
   source: namespace_overview(search) — verified
   > service/search selector={app=search-api} endpointAddresses=0
3. [defect] The Service selector asks for label app=search-api.
   source: get_object({"kind": "services", "name": "search", "namespace": "search"}) — verified
   > "selector": {
   >       "app": "search-api"
   >     },
4. [link] The deployment's pod template labels the pods app=search, which cannot match app=search-api.
   source: get_object({"kind": "deployments", "name": "search", "namespace": "search"}) — verified
   > "template": {
   >       "metadata": {
   >         "labels": {
   >           "app": "search"
   >         }
   >       },
5. [link] The Endpoints object for the Service is empty - it has no subsets/addresses at all.
   source: get_object({"kind": "endpoints", "name": "search", "namespace": "search"}) — verified
   > "name": "search",
   >     "namespace": "search",
6. [link] The search pods are healthy and actually listening on port 8080, so the backend itself is not the failure.
   source: get_logs({"namespace": "search", "pod": "search-5478989674-6rxcp", "tail": 10}) — verified
   > search: serving on :8080

## Investigation ledger

- The search backend pods are unhealthy or not listening, so the gateway's calls fail at the application layer. — ruled out: Both search pods are Running and Ready with zero restarts and the container logs show the server bound to its port.
  source: get_logs({"namespace": "search", "pod": "search-5478989674-6rxcp", "tail": 10}) — verified
  > search: serving on :8080
- The web-gateway pod itself is broken or crashing. — ruled out: The gateway pod is Running and Ready with no restarts; it fails only on its outbound call to the search Service.
  source: namespace_overview(search) — verified
  > pod/web-gateway-557b9db57b-65gxl phase=Running labels={app=web-gateway, pod-template-hash=557b9db57b} node=incident-lab-control-plane gateway(ready=True,restarts=0)
- The loud CrashLoopBackOff in analytics-batch is the cause of the search outage. — ruled out: That failing workload lives in a different namespace and is not referenced by the search Service, its Endpoints, or the gateway's call path.
  source: namespace_overview({"namespace": "analytics-batch"}) — verified
  > pod/report-generator-646bc6f94f-sgdm2 phase=Running labels={app=report-generator, pod-template-hash=646bc6f94f} node=incident-lab-control-plane generator(ready=False,restarts=5,waiting=CrashLoopBackOff,lastExit=Error)

## Verification recipe

1. `kubectl -n search get service search -o jsonpath='{.spec.selector}'` — expect to see: search-api  [PRESENT]
2. `kubectl -n search get deployment search -o jsonpath='{.spec.template.metadata.labels}'` — expect to see: "app": "search"  [PRESENT]
3. `kubectl -n search get endpoints search -o yaml` — expect to see: "name": "search"  [PRESENT]
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
  "mechanism": "Service search in namespace search has .spec.selector = {\"app\": \"search-api\"}, while the pods created by deployment/search are labelled app=search, so the selector matches zero pods and the endpoint controller writes an Endpoints object with no addresses; connections to the search ClusterIP:8080 therefore hit a service with no backend and are refused/black-holed by kube-proxy.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
