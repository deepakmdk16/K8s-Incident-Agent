## Root cause

The Service shop/catalog selects pods with the label app=catalog-api, but the Deployment shop/catalog stamps its pods with app=catalog (.spec.template.metadata.labels). No pod in the namespace carries app=catalog-api, so the endpoint controller writes an Endpoints object for the Service with no addresses at all. The catalog Deployment itself is healthy (2/2 Ready, no restarts), which is why the deployment reports all replicas Ready while traffic sent to the Service ClusterIP has nowhere to land; the storefront gateway's polls of the catalog Service log repeated "catalog fetch FAILED (timeout or connection refused)". Fixing the Service selector to app=catalog restores endpoints.

Remediation: edit Service shop/catalog, field `.spec.selector.app`: `catalog-api` -> `catalog`.

## Evidence chain

1. [symptom] The paged Service has no endpoint addresses even though the catalog Deployment is fully Ready.
   source: namespace_overview(shop) — verified
   > service/catalog selector={app=catalog-api} endpointAddresses=0
2. [symptom] The storefront gateway's calls to the catalog Service never complete.
   source: get_logs({"namespace": "shop", "pod": "storefront-gateway-6785fd7b5d-cq9bt", "tail": 30}) — verified
   > gateway: catalog fetch FAILED (timeout or connection refused)
3. [defect] The Service selector names a label value no pod carries.
   source: get_object({"kind": "services", "name": "catalog", "namespace": "shop"}) — verified
   > "selector": {
   >       "app": "catalog-api"
   >     },
4. [link] The catalog Deployment labels its pods app=catalog, not app=catalog-api.
   source: get_object({"kind": "deployments", "name": "catalog", "namespace": "shop"}) — verified
   > "template": {
   >       "metadata": {
   >         "labels": {
   >           "app": "catalog"
   >         }
5. [link] The endpoint controller produced an Endpoints object for catalog with no subsets/addresses.
   source: get_object({"kind": "endpoints", "name": "catalog", "namespace": "shop"}) — verified
   > "endpoints.kubernetes.io/managed-by": "endpoint-controller"
6. [link] Both catalog pods are Running and Ready with no restarts, so readiness is not what removed them from the Endpoints.
   source: namespace_overview(shop) — verified
   > pod/catalog-65498fccb-g52gl phase=Running labels={app=catalog, pod-template-hash=65498fccb} node=incident-lab-control-plane api(ready=True,restarts=0)

## Investigation ledger

- The catalog pods are unhealthy or failing their readiness probe, so they were dropped from the Endpoints. — ruled out: Both catalog pods are Running with ready=True and zero restarts, and the Deployment reports 2/2 ready.
  source: namespace_overview(shop) — verified
  > deployment/catalog ready=2/2 podLabels={app=catalog}
- The Service port or targetPort is misconfigured so traffic reaches the pods on the wrong port. — ruled out: The Service listens on 8080 with targetPort 8080, which matches the container's named http port 8080 in the Deployment template; the port mapping is consistent.
  source: get_object({"kind": "services", "name": "catalog", "namespace": "shop"}) — verified
  > "port": 8080,
  >         "protocol": "TCP",
  >         "targetPort": 8080
- The storefront-gateway Deployment itself is broken or crash-looping. — ruled out: The gateway pod is Running and Ready with no restarts and is actively polling; it only reports failures against the catalog backend.
  source: namespace_overview(shop) — verified
  > pod/storefront-gateway-6785fd7b5d-cq9bt phase=Running labels={app=storefront-gateway, pod-template-hash=6785fd7b5d} node=incident-lab-control-plane gateway(ready=True,restarts=0)

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
  "mechanism": "The Service shop/catalog has .spec.selector set to {app: catalog-api}, while the pods that back the catalog workload carry the label app=catalog, so nothing in the namespace matches the selector and the endpoint controller populates the Service's Endpoints object with zero addresses; connections to the Service ClusterIP:8080 therefore have no backend to be routed to and fail.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
