## Root cause

Service shop/catalog selects pods with the label app=catalog-api, but Deployment shop/catalog stamps its pods with app=catalog. No pod in the namespace carries app=catalog-api, so the endpoint controller writes an Endpoints object for shop/catalog with no addresses at all. Both catalog pods are Running and Ready and the container logs "catalog: serving on :8080", so the backend itself is healthy; connections to the ClusterIP 10.96.96.22 have no backend to be forwarded to, which the storefront gateway records as "gateway: catalog fetch FAILED (timeout or connection refused)" and shoppers see as hanging product pages. Fixing the incident means editing the Service selector back to app=catalog to match the Deployment pod template labels.

Remediation: edit Service shop/catalog, field `.spec.selector`: `{"app": "catalog-api"}` -> `{"app": "catalog"}`.

## Evidence chain

1. [symptom] The paged symptom is the gateway's calls to the catalog backend not completing while catalog reports all replicas Ready.
   source: the page — verified
   > The storefront gateway reports its calls to the catalog backend are not completing. Catalog deployment shop/catalog reports all replicas Ready.
2. [symptom] Service shop/catalog has zero endpoint addresses despite two Ready catalog pods.
   source: namespace_overview(shop) — verified
   > service/catalog selector={app=catalog-api} endpointAddresses=0
3. [defect] The Service selector asks for app=catalog-api.
   source: get_object({"kind": "services", "name": "catalog", "namespace": "shop"}) — verified
   > "selector": {
   >       "app": "catalog-api"
   >     },
4. [link] The Deployment pod template labels the pods app=catalog, not app=catalog-api.
   source: get_object({"kind": "deployments", "name": "catalog", "namespace": "shop"}) — verified
   > "template": {
   >       "metadata": {
   >         "labels": {
   >           "app": "catalog"
   >         }
   >       },
5. [link] The Endpoints object for the Service contains no subsets/addresses at all.
   source: get_object({"kind": "endpoints", "name": "catalog", "namespace": "shop"}) — verified
   > "name": "catalog",
   >     "namespace": "shop",
   >     "resourceVersion": "57553",
6. [link] The gateway's calls to the catalog Service fail repeatedly.
   source: get_logs({"namespace": "shop", "pod": "storefront-gateway-6785fd7b5d-cq9bt", "tail": 30}) — verified
   > gateway: catalog fetch FAILED (timeout or connection refused)
7. [link] The catalog backend itself is serving on the port the Service targets.
   source: get_logs({"namespace": "shop", "pod": "catalog-65498fccb-g52gl", "tail": 10}) — verified
   > catalog: serving on :8080

## Investigation ledger

- The catalog pods are crashed, not ready, or not listening on 8080. — ruled out: Both catalog pods are Running with ready=True and zero restarts, and the container logs show it bound port 8080.
  source: get_logs({"namespace": "shop", "pod": "catalog-65498fccb-g52gl", "tail": 10}) — verified
  > catalog: serving on :8080
- The Service port/targetPort is wrong (pointing at a port nothing listens on). — ruled out: The Service targets port 8080, exactly the containerPort the catalog container serves on, so the port mapping is not the defect.
  source: get_object({"kind": "services", "name": "catalog", "namespace": "shop"}) — verified
  > "port": 8080,
  >         "protocol": "TCP",
  >         "targetPort": 8080
- The storefront gateway is misconfigured to call a Service name that does not exist in the namespace. — ruled out: Only one Service exists in shop and it is named catalog, the name the gateway poller targets; the Service resolves, it simply has no backends.
  source: find_consumers({"kind": "service", "name": "catalog", "namespace": "shop"}) — verified
  > services that exist in shop: catalog

## Verification recipe

1. `kubectl -n shop get service catalog -o jsonpath='{.spec.selector}'` — expect to see: catalog-api  [PRESENT]
2. `kubectl -n shop get endpoints catalog -o yaml` — expect to see: endpoint-controller  [PRESENT]
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
  "mechanism": "Service shop/catalog has .spec.selector set to {\"app\": \"catalog-api\"} while the pods produced by Deployment shop/catalog carry the label app=catalog, so the selector matches zero pods and the endpoint controller writes Endpoints shop/catalog with no addresses (\"endpointAddresses=0\"). With no backend addresses, kube-proxy has no destination for the ClusterIP 10.96.96.22:8080, so every connection to that Service address is dropped or refused instead of reaching a catalog pod.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
