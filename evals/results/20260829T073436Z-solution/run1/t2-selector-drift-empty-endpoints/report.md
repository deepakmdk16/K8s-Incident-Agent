## Root cause

The Service shop/catalog selects pods with the label app=catalog-api, but the only pods serving the catalog backend are produced by Deployment shop/catalog, whose pod template labels pods app=catalog. No pod in the namespace carries app=catalog-api, so the endpoint controller populates no addresses for the Service: its Endpoints object shop/catalog has no subsets and the overview reports endpointAddresses=0. The catalog pods themselves are healthy and Ready, which is why the Deployment reports 2/2, but traffic sent to the Service ClusterIP has nowhere to go, and the storefront gateway's polls of the catalog backend fail, producing the storewide product-page timeouts. Fix: set .spec.selector.app on Service shop/catalog back to "catalog" so it matches the Deployment's pod template labels.

Remediation: edit Service shop/catalog, field `.spec.selector.app`: `catalog-api` -> `catalog`.

## Evidence chain

1. [symptom] The paged Service has no endpoint addresses even though the catalog pods are Ready.
   source: namespace_overview(shop) — verified
   > service/catalog selector={app=catalog-api} endpointAddresses=0
2. [symptom] The storefront gateway's calls to the catalog backend fail repeatedly.
   source: get_logs({"namespace": "shop", "pod": "storefront-gateway-6785fd7b5d-cq9bt", "tail": 30}) — verified
   > gateway: catalog fetch FAILED (timeout or connection refused)
3. [defect] The Service selector names a label value no pod carries.
   source: get_object({"kind": "services", "name": "catalog", "namespace": "shop"}) — verified
   > "selector": {
   >       "app": "catalog-api"
   >     },
4. [link] The Deployment's pod template labels pods app=catalog, not app=catalog-api.
   source: get_object({"kind": "deployments", "name": "catalog", "namespace": "shop"}) — verified
   > "template": {
   >       "metadata": {
   >         "labels": {
   >           "app": "catalog"
   >         }
5. [link] The Endpoints object for the Service contains no subsets/addresses at all.
   source: get_object({"kind": "endpoints", "name": "catalog", "namespace": "shop"}) — verified
   > "name": "catalog",
   >     "namespace": "shop",
6. [link] Both catalog pods are Running and Ready with the label app=catalog.
   source: namespace_overview(shop) — verified
   > pod/catalog-65498fccb-g52gl phase=Running labels={app=catalog, pod-template-hash=65498fccb} node=incident-lab-control-plane api(ready=True,restarts=0)

## Investigation ledger

- The catalog pods are unhealthy or failing their readiness probe, so they were removed from the Endpoints. — ruled out: Both catalog pods are Running with ready=True and zero restarts, and the Deployment reports 2/2 ready, so readiness is not the reason the Endpoints is empty.
  source: namespace_overview(shop) — verified
  > deployment/catalog ready=2/2 podLabels={app=catalog}
- The storefront gateway itself is broken or crashing. — ruled out: The gateway pod is Running and Ready with no restarts and is actively polling; its failures are on the outbound catalog call, not in its own startup.
  source: namespace_overview(shop) — verified
  > pod/storefront-gateway-6785fd7b5d-cq9bt phase=Running labels={app=storefront-gateway, pod-template-hash=6785fd7b5d} node=incident-lab-control-plane gateway(ready=True,restarts=0)
- A second, differently-labelled catalog Service or workload exists that should be backing the selector app=catalog-api. — ruled out: catalog is the only Service in the namespace and no other workload exists that could carry app=catalog-api.
  source: find_consumers({"kind": "service", "name": "catalog", "namespace": "shop"}) — verified
  > services that exist in shop: catalog

## Verification recipe

1. `kubectl -n shop get service catalog -o jsonpath='{.spec.selector}'` — expect to see: catalog-api  [PRESENT]
2. `kubectl -n shop get deployment catalog -o jsonpath='{.spec.template.metadata.labels}'` — expect to see: "app": "catalog"  [PRESENT]
3. `kubectl -n shop logs deploy/storefront-gateway --tail=20` — expect to see: catalog fetch FAILED  [PRESENT]
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
  "mechanism": "The Service shop/catalog has .spec.selector set to {app: catalog-api}, while the catalog pods carry the label app=catalog, so no pod matches the selector and the endpoint controller writes an Endpoints object shop/catalog with zero addresses; connections to the Service ClusterIP 10.96.96.22:8080 have no backend and are refused/time out.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
