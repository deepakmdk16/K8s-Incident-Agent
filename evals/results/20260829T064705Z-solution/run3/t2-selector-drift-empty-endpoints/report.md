## Root cause

Service shop/catalog selects pods with label app=catalog-api, but the catalog Deployment's pod template stamps app=catalog on its pods. No pod in the namespace carries app=catalog-api, so the endpoint controller populates no addresses for the Service and its Endpoints object has no subsets. The storefront gateway resolves the catalog Service ClusterIP but every connection has no backend to land on, which is why its catalog fetches fail while the catalog Deployment itself reports 2/2 Ready and its containers log that they are serving on :8080. Fixing the incident means changing the Service selector back to app=catalog.

Remediation: edit Service shop/catalog, field `.spec.selector.app`: `catalog-api` -> `catalog`.

## Evidence chain

1. [symptom] The paged Service has zero endpoint addresses even though catalog reports all replicas ready.
   source: namespace_overview(shop) — verified
   > service/catalog selector={app=catalog-api} endpointAddresses=0
2. [symptom] The gateway's calls to the catalog backend do not complete.
   source: get_logs({"namespace": "shop", "pod": "storefront-gateway-6785fd7b5d-cq9bt", "tail": 30}) — verified
   > gateway: catalog fetch FAILED (timeout or connection refused)
3. [defect] The Service selector asks for app=catalog-api.
   source: get_object({"kind": "service", "name": "catalog", "namespace": "shop"}) — verified
   > "selector": {
   >       "app": "catalog-api"
   >     }
4. [link] The catalog Deployment pod template labels pods app=catalog, which the selector does not match.
   source: get_object({"kind": "deployment", "name": "catalog", "namespace": "shop"}) — verified
   > "template": {
   >       "metadata": {
   >         "labels": {
   >           "app": "catalog"
   >         }
5. [link] The Endpoints object for catalog contains no subsets/addresses at all.
   source: get_object({"kind": "endpoints", "name": "catalog", "namespace": "shop"}) — verified
   > "name": "catalog",
   >     "namespace": "shop",

## Investigation ledger

- The catalog pods themselves are unhealthy or failing their readiness probe, so they are excluded from endpoints. — ruled out: Both catalog pods are Running and their api container is ready with no restarts.
  source: namespace_overview(shop) — verified
  > pod/catalog-65498fccb-g52gl phase=Running labels={app=catalog, pod-template-hash=65498fccb} node=incident-lab-control-plane api(ready=True,restarts=0)
- The catalog application is not actually listening on port 8080, so the Service port/targetPort would be wrong. — ruled out: The catalog container logs that it is serving on :8080, matching the Service targetPort 8080.
  source: get_logs({"namespace": "shop", "pod": "catalog-65498fccb-g52gl", "tail": 10}) — verified
  > catalog: serving on :8080
- The storefront gateway pod is itself broken rather than being starved of a backend. — ruled out: The gateway pod is Running and Ready with no restarts; it only reports failures on its outbound catalog calls.
  source: namespace_overview(shop) — verified
  > pod/storefront-gateway-6785fd7b5d-cq9bt phase=Running labels={app=storefront-gateway, pod-template-hash=6785fd7b5d} node=incident-lab-control-plane gateway(ready=True,restarts=0)

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
  "mechanism": "Service shop/catalog has .spec.selector set to {app: catalog-api} while the pods produced by deployment/catalog carry the label app=catalog, so no pod matches the selector and the endpoint controller writes an Endpoints object with no addresses; traffic sent to the catalog ClusterIP has zero backends and the connection fails.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
