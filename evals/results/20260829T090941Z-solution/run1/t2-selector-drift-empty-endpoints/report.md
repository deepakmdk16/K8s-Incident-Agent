## Root cause

Service shop/catalog selects pods with the label app=catalog-api, but the pods produced by Deployment shop/catalog are labelled app=catalog (its .spec.template.metadata.labels and .spec.selector.matchLabels both say app=catalog). No pod in the namespace carries app=catalog-api, so the endpoint controller writes an Endpoints object for shop/catalog with no addresses at all. The catalog pods themselves are healthy and Ready, which is why the Deployment reports 2/2, but every connection the storefront gateway makes to the catalog Service has nowhere to go, and its poller logs "gateway: catalog fetch FAILED (timeout or connection refused)" — the timeouts and empty product grids shoppers see. Fixing it is a one-line edit of the Service selector back to app=catalog.

Remediation: edit Service shop/catalog, field `.spec.selector.app`: `catalog-api` -> `catalog`.

## Evidence chain

1. [symptom] The page reports catalog calls not completing while the Deployment is fully Ready
   source: the page — verified
   > The storefront gateway reports
   > its calls to the catalog backend are not completing. Catalog deployment
   > shop/catalog reports all replicas Ready.
2. [symptom] Service catalog has no endpoint addresses despite ready catalog pods
   source: namespace_overview(shop) — verified
   > service/catalog selector={app=catalog-api} endpointAddresses=0
3. [defect] The Service selector asks for app=catalog-api
   source: get_object({"kind": "service", "name": "catalog", "namespace": "shop"}) — verified
   > "selector": {
   >       "app": "catalog-api"
   >     },
4. [link] The Deployment's pod template labels pods app=catalog, not catalog-api
   source: get_object({"kind": "deployment", "name": "catalog", "namespace": "shop"}) — verified
   > "template": {
   >       "metadata": {
   >         "labels": {
   >           "app": "catalog"
   >         }
   >       },
5. [link] The Endpoints object for catalog contains no subsets/addresses
   source: get_object({"kind": "endpoints", "name": "catalog", "namespace": "shop"}) — verified
   > "name": "catalog",
   >     "namespace": "shop",
6. [symptom] The gateway's calls to the catalog Service fail
   source: get_logs({"namespace": "shop", "pod": "storefront-gateway-6785fd7b5d-cq9bt", "tail": 30}) — verified
   > gateway: catalog fetch FAILED (timeout or connection refused)

## Investigation ledger

- The catalog pods are crashing or failing their readiness probe, so they were removed from Endpoints — ruled out: Both catalog pods are Running with ready=True and zero restarts, so readiness is not what emptied the Endpoints
  source: namespace_overview(shop) — verified
  > pod/catalog-65498fccb-g52gl phase=Running labels={app=catalog, pod-template-hash=65498fccb} node=incident-lab-control-plane api(ready=True,restarts=0)
- The storefront gateway itself is unhealthy or misconfigured — ruled out: The gateway pod is Running and Ready with no restarts and its poller starts normally before every fetch fails, so the failure is on the catalog Service side
  source: get_logs({"namespace": "shop", "pod": "storefront-gateway-6785fd7b5d-cq9bt", "tail": 30}) — verified
  > gateway: starting catalog poller
- The Service targets the wrong port, so connections reach pods but the wrong listener — ruled out: The Service targetPort 8080 matches the container port 8080 named http in the Deployment template, so port mapping is not the defect
  source: get_object({"kind": "deployment", "name": "catalog", "namespace": "shop"}) — verified
  > "containerPort": 8080,
  >                 "name": "http",

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
  "mechanism": "Service shop/catalog has .spec.selector set to {\"app\": \"catalog-api\"}, while the pods created by Deployment shop/catalog carry the label app=catalog, so no pod matches the selector and the endpoint controller leaves the Endpoints object shop/catalog with zero addresses (endpointAddresses=0); traffic sent to ClusterIP 10.96.96.22 has no backend to be routed to and the connection fails.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
