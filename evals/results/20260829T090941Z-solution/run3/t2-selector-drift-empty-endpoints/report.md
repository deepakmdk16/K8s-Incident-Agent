## Root cause

Service shop/catalog selects pods with the label app=catalog-api, but the pods produced by Deployment shop/catalog carry the label app=catalog (the deployment's pod template sets metadata.labels app=catalog). No pod in the namespace carries app=catalog-api, so the endpoint controller populated no addresses: Endpoints shop/catalog has no subsets and the overview shows endpointAddresses=0. Because Service ClusterIP 10.96.96.22 has no backends, calls from Deployment shop/storefront-gateway to the catalog backend die at the service VIP and the gateway logs "gateway: catalog fetch FAILED (timeout or connection refused)" on every poll, which is what the storefront sees as spinner hangs and empty product grids. The catalog pods themselves are healthy and serving on :8080, so the fix is to correct the Service selector back to app=catalog.

Remediation: edit Service shop/catalog, field `.spec.selector`: `{"app": "catalog-api"}` -> `{"app": "catalog"}`.

## Evidence chain

1. [symptom] The paged Service has zero endpoint addresses although both catalog pods are Running and Ready.
   source: namespace_overview(shop) — verified
   > service/catalog selector={app=catalog-api} endpointAddresses=0
2. [symptom] The storefront gateway's calls to the catalog backend do not complete.
   source: get_logs({"namespace": "shop", "pod": "storefront-gateway-6785fd7b5d-cq9bt", "tail": 30}) — verified
   > gateway: catalog fetch FAILED (timeout or connection refused)
3. [defect] Service shop/catalog selects app=catalog-api.
   source: get_object({"kind": "service", "name": "catalog", "namespace": "shop"}) — verified
   > "selector": {
   >       "app": "catalog-api"
   >     }
4. [link] Deployment shop/catalog labels its pods app=catalog, which does not match the Service selector.
   source: get_object({"kind": "deployment", "name": "catalog", "namespace": "shop"}) — verified
   > "template": {
   >       "metadata": {
   >         "labels": {
   >           "app": "catalog"
   >         }
   >       }
5. [link] Endpoints shop/catalog contains no subsets at all.
   source: get_object({"kind": "endpoints", "name": "catalog", "namespace": "shop"}) — verified
   > "name": "catalog",
   >     "namespace": "shop",
6. [link] The catalog container is actually listening on port 8080, so the backend itself is not the failure.
   source: get_logs({"namespace": "shop", "pod": "catalog-65498fccb-g52gl", "tail": 15}) — verified
   > catalog: serving on :8080

## Investigation ledger

- The catalog pods are unhealthy or not serving, so no ready backends exist. — ruled out: Both catalog pods are Running with the api container ready and zero restarts, and the container logs show it listening on 8080.
  source: get_logs({"namespace": "shop", "pod": "catalog-65498fccb-g52gl", "tail": 15}) — verified
  > catalog: serving on :8080
- The gateway is pointed at a different or non-existent Service name. — ruled out: Only one Service exists in namespace shop, catalog on port 8080, so the gateway's catalog calls can only target it.
  source: get_object({"kind": "services", "namespace": "shop"}) — verified
  > "name": "catalog",
  >       "namespace": "shop",
- The Service port or targetPort is wrong (8080 mismatch with the container port). — ruled out: The Service targets port 8080 and the catalog container declares containerPort 8080, so the port mapping is correct.
  source: get_object({"kind": "deployment", "name": "catalog", "namespace": "shop"}) — verified
  > "containerPort": 8080,
  >                 "name": "http",

## Verification recipe

1. `kubectl -n shop get service catalog -o jsonpath='{.spec.selector}'` — expect to see: catalog-api  [PRESENT]
2. `kubectl -n shop get endpoints catalog -o yaml` — expect to see: "name": "catalog"  [PRESENT]
3. `kubectl -n shop get deployment catalog -o jsonpath='{.spec.template.metadata.labels}'` — expect to see: "app": "catalog"  [PRESENT]
(Each command was re-executed against the captured cluster state; PRESENT/ABSENT is the measured result, not a prediction.)

Verdict: confirmed. Every quote below was re-checked against the tool output it cites, a defect observation names the failing object, an alternative was ruled out with evidence, and every verification command was re-run and found present.

```json
{
  "case_id": "t2-selector-drift-empty-endpoints",
  "failing_resource": {
    "kind": "Service",
    "namespace": "shop",
    "name": "catalog"
  },
  "mechanism": "Service shop/catalog has .spec.selector set to {app: catalog-api} while the pods it is meant to front are labelled {app: catalog}, so the selector matches zero pods and the endpoint controller writes Endpoints shop/catalog with no subsets (endpointAddresses=0). With no backend address to DNAT to, TCP connections to its ClusterIP 10.96.96.22:8080 are rejected or dropped \u2014 \"timeout or connection refused\" \u2014 instead of being load-balanced to the catalog pods.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
