## Root cause

Service shop/catalog selects pods with label app=catalog-api, but the catalog Deployment's pod template stamps its pods with app=catalog. No pod in the namespace carries app=catalog-api, so the endpoint controller populates no addresses for the Service and its Endpoints object has no subsets. The catalog Deployment itself is healthy (2/2 Ready), which is why the page says all replicas are Ready, but every storefront-gateway call to the catalog ClusterIP has no backend to reach and fails, producing the timing-out product pages. Fixing it is a single edit to the Service selector back to app=catalog.

Remediation: edit Service shop/catalog, field `.spec.selector.app`: `catalog-api` -> `catalog`.

## Evidence chain

1. [symptom] The catalog Service has no endpoint addresses even though both catalog pods are Running and Ready.
   source: namespace_overview(shop) — verified
   > service/catalog selector={app=catalog-api} endpointAddresses=0
2. [symptom] The storefront gateway's calls to the catalog backend fail continuously.
   source: get_logs({"namespace": "shop", "pod": "storefront-gateway-6785fd7b5d-cq9bt", "tail": 30}) — verified
   > gateway: catalog fetch FAILED (timeout or connection refused)
3. [defect] The Service selector asks for app=catalog-api.
   source: get_object({"kind": "services", "name": "catalog", "namespace": "shop"}) — verified
   > "selector": {
   >       "app": "catalog-api"
   >     }
4. [link] The catalog Deployment stamps its pods with app=catalog, which does not match the Service selector.
   source: get_object({"kind": "deployments", "name": "catalog", "namespace": "shop"}) — verified
   > "template": {
   >       "metadata": {
   >         "labels": {
   >           "app": "catalog"
   >         }
5. [link] The Endpoints object for catalog exists but contains no subsets/addresses.
   source: get_object({"kind": "endpoints", "name": "catalog", "namespace": "shop"}) — verified
   > "name": "catalog",
   >     "namespace": "shop",
6. [link] Both catalog pods are Ready, so readiness is not the reason for empty endpoints.
   source: namespace_overview(shop) — verified
   > pod/catalog-65498fccb-g52gl phase=Running labels={app=catalog, pod-template-hash=65498fccb} node=incident-lab-control-plane api(ready=True,restarts=0)

## Investigation ledger

- Catalog pods are crashing or failing their readiness probe, which would empty the endpoints. — ruled out: Both catalog pods are Running with ready=True and zero restarts, and the Deployment reports 2/2.
  source: namespace_overview(shop) — verified
  > deployment/catalog ready=2/2 podLabels={app=catalog}
- The gateway itself is broken or mis-scheduled. — ruled out: The gateway pod is Running and Ready with no restarts and is actively polling; its failures are outbound connection failures to the catalog service, not a fault in its own startup.
  source: namespace_overview(shop) — verified
  > pod/storefront-gateway-6785fd7b5d-cq9bt phase=Running labels={app=storefront-gateway, pod-template-hash=6785fd7b5d} node=incident-lab-control-plane gateway(ready=True,restarts=0)
- The Service port/targetPort is wrong so traffic hits a closed port on the pods. — ruled out: The Service targets port 8080 and the container declares containerPort 8080, so the port mapping is consistent; the mismatch is in the label selector.
  source: get_object({"kind": "services", "name": "catalog", "namespace": "shop"}) — verified
  > "port": 8080,
  >         "protocol": "TCP",
  >         "targetPort": 8080

## Verification recipe

1. `kubectl -n shop get service catalog -o jsonpath='{.spec.selector}'` — expect to see: catalog-api  [PRESENT]
2. `kubectl -n shop get endpoints catalog -o yaml` — expect to see: "name": "catalog"  [PRESENT]
3. `kubectl -n shop get pods -l app=catalog --show-labels` — expect to see: labels={app=catalog, pod-template-hash=65498fccb}  [PRESENT]
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
  "mechanism": "Service shop/catalog has .spec.selector = {app: catalog-api}, while the pods produced by deployment/catalog are labelled app=catalog; no pod matches the selector, so the endpoint controller writes an Endpoints object for catalog with no subsets and traffic to the catalog ClusterIP:8080 has zero backends and is refused/dropped.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
