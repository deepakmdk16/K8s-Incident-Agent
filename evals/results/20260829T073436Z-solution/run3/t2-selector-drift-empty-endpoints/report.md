## Root cause

The Service shop/catalog selects pods with label app=catalog-api, but the Deployment shop/catalog stamps its pods with app=catalog. No pod in the namespace carries app=catalog-api, so the endpoint controller populates no addresses for the Service and its Endpoints object has no subsets. Both catalog pods are Running and Ready and the container logs show the API serving on :8080, which is why the Deployment reports 2/2 while every connection the storefront gateway makes to the catalog ClusterIP has nothing behind it and fails ("gateway: catalog fetch FAILED (timeout or connection refused)"). Fixing the incident means editing the Service selector back to app=catalog so it matches the pod template labels.

Remediation: edit Service shop/catalog, field `.spec.selector.app`: `catalog-api` -> `catalog`.

## Evidence chain

1. [symptom] The paged Service has zero endpoint addresses while its pods are all Ready.
   source: namespace_overview(shop) — verified
   > service/catalog selector={app=catalog-api} endpointAddresses=0
2. [defect] The Service selector asks for app=catalog-api.
   source: get_object({"kind": "services", "name": "catalog", "namespace": "shop"}) — verified
   > "selector": {
   >       "app": "catalog-api"
   >     },
3. [link] The Deployment's pod template labels the pods app=catalog, not catalog-api.
   source: get_object({"kind": "deployments", "name": "catalog", "namespace": "shop"}) — verified
   > "template": {
   >       "metadata": {
   >         "labels": {
   >           "app": "catalog"
   >         }
   >       },
4. [link] The Endpoints object for catalog contains no subsets/addresses.
   source: get_object({"kind": "endpoints", "name": "catalog", "namespace": "shop"}) — verified
   > "name": "catalog",
   >     "namespace": "shop",
5. [symptom] The storefront gateway's calls to the catalog Service do not complete.
   source: get_logs({"namespace": "shop", "pod": "storefront-gateway-6785fd7b5d-cq9bt", "tail": 30}) — verified
   > gateway: catalog fetch FAILED (timeout or connection refused)

## Investigation ledger

- The catalog pods themselves are broken or not serving on port 8080. — ruled out: The catalog container logs show the API listening on :8080 and both pods report ready with no restarts.
  source: get_logs({"namespace": "shop", "pod": "catalog-65498fccb-g52gl", "tail": 10}) — verified
  > catalog: serving on :8080
- Readiness failures are removing otherwise matching pods from the Endpoints list. — ruled out: Both pods are Running with the api container ready=True and zero restarts, so readiness is not what emptied the endpoint list.
  source: namespace_overview(shop) — verified
  > pod/catalog-65498fccb-g52gl phase=Running labels={app=catalog, pod-template-hash=65498fccb} node=incident-lab-control-plane api(ready=True,restarts=0)
- A NetworkPolicy is blocking gateway-to-backend traffic in the namespace. — ruled out: Only ConfigMaps and a ServiceAccount exist besides the workloads and the Service; no NetworkPolicy objects were captured.
  source: namespace_overview(shop) — verified
  > other objects: configmaps=3 serviceaccounts=1

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
  "mechanism": "The Service catalog has .spec.selector set to {app: catalog-api}, while the pods that back it are labelled app=catalog, so the selector matches zero pods and the endpoint controller writes an Endpoints object for catalog with no addresses; traffic sent to that Service's ClusterIP 10.96.96.22:8080 has no backend to be routed to and the connection fails.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
