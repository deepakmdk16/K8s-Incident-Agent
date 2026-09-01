## Root cause

Service shop/catalog selects pods with label app=catalog-api, but the catalog Deployment stamps its pods with app=catalog. No pod in the namespace carries app=catalog-api, so the endpoint controller populates no addresses for the Service and its Endpoints object has no subsets. The catalog pods themselves are healthy and serving on :8080, but nothing routes to them: the storefront gateway's catalog fetches fail with timeout/connection refused and product pages hang. Fixing it is a one-field edit: set the Service selector back to app=catalog.

Remediation: edit Service shop/catalog, field `.spec.selector.app`: `catalog-api` -> `catalog`.

## Evidence chain

1. [symptom] Service catalog has zero endpoint addresses even though both catalog pods are Running and Ready
   source: namespace_overview(shop) — verified
   > service/catalog selector={app=catalog-api} endpointAddresses=0
2. [defect] The Service selector asks for app=catalog-api
   source: get_object({"kind": "service", "name": "catalog", "namespace": "shop"}) — verified
   > "selector": {
   >       "app": "catalog-api"
   >     },
3. [link] The catalog Deployment pod template labels pods app=catalog, which does not match the Service selector
   source: get_object({"kind": "deployment", "name": "catalog", "namespace": "shop"}) — verified
   > "template": {
   >       "metadata": {
   >         "labels": {
   >           "app": "catalog"
   >         }
   >       },
4. [link] The Endpoints object for catalog exists but carries no subsets/addresses
   source: get_object({"kind": "endpoints", "name": "catalog", "namespace": "shop"}) — verified
   > "name": "catalog",
   >     "namespace": "shop",
5. [symptom] The storefront gateway's calls to the catalog service do not complete
   source: get_logs({"namespace": "shop", "pod": "storefront-gateway-6785fd7b5d-cq9bt", "tail": 30}) — verified
   > gateway: catalog fetch FAILED (timeout or connection refused)

## Investigation ledger

- The catalog application itself is down or not listening on 8080 — ruled out: The catalog container logs show it bound and is serving on port 8080, and both pods report ready with zero restarts
  source: get_logs({"namespace": "shop", "pod": "catalog-65498fccb-g52gl", "tail": 20}) — verified
  > catalog: serving on :8080
- The storefront gateway is misconfigured or crashing — ruled out: The gateway pod is Running and Ready with no restarts and is actively polling; its failures are connection-level to the catalog Service
  source: namespace_overview(shop) — verified
  > pod/storefront-gateway-6785fd7b5d-cq9bt phase=Running labels={app=storefront-gateway, pod-template-hash=6785fd7b5d} node=incident-lab-control-plane gateway(ready=True,restarts=0)
- Readiness gating is removing catalog pods from the Endpoints list — ruled out: Both catalog pods report ready=True, so readiness is not what empties the endpoint list
  source: namespace_overview(shop) — verified
  > pod/catalog-65498fccb-lbrwn phase=Running labels={app=catalog, pod-template-hash=65498fccb} node=incident-lab-control-plane api(ready=True,restarts=0)

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
  "mechanism": "Service shop/catalog has .spec.selector.app = \"catalog-api\" while the pods produced by deployment/catalog are labelled app=catalog, so the selector matches no pod and the endpoint controller writes an Endpoints object with zero addresses; every connection to the catalog ClusterIP on port 8080 therefore has no backend and is rejected with connection refused / timeout.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
