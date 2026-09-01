## Root cause

Deployment orders/orders-api injects DATABASE_URL from ConfigMap orders/orders-config using the key "db_url", but that ConfigMap contains only the key "database_url". The kubelet cannot build the container environment, so every orders-api pod stays Pending with its "api" container waiting in CreateContainerConfigError and never becomes ready. Because no pod carrying the label app=orders-api ever reaches Ready, the Service orders/orders-api has zero endpoint addresses and the gateway has no healthy upstream for POST /api/orders, producing the 502/503s. The sibling Deployment orders/orders-audit reads the same ConfigMap with the correct key "database_url" and is 1/1 Ready, which shows the ConfigMap itself is intact and the orders-api key reference is the defect. Fix: change the configMapKeyRef key in the orders-api pod template from "db_url" to "database_url".

Remediation: edit Deployment orders/orders-api, field `spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key`: `db_url` -> `database_url`.

## Evidence chain

1. [symptom] Both orders-api pods are Pending with CreateContainerConfigError and the Service has no endpoints.
   source: namespace_overview(orders) — verified
   > service/orders-api selector={app=orders-api} endpointAddresses=0
2. [symptom] The paged deployment reports 0/2 ready.
   source: namespace_overview(orders) — verified
   > deployment/orders-api ready=0/2 podLabels={app=orders-api}
3. [defect] kubelet rejects container creation because the referenced ConfigMap key does not exist.
   source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
   > Error: couldn't find key db_url in ConfigMap orders/orders-config
4. [defect] The Deployment pod template asks for key db_url.
   source: get_object({"kind": "deployments", "name": "orders-api", "namespace": "orders"}) — verified
   > "configMapKeyRef": {
   >                     "key": "db_url",
   >                     "name": "orders-config"
   >                   }
5. [link] The referenced ConfigMap contains only the key database_url, not db_url.
   source: find_consumers({"kind": "configmap", "name": "orders-config", "namespace": "orders"}) — verified
   > keys in configmap/orders-config: ['database_url']
6. [link] The working peer deployment reads the same ConfigMap with the correct key and is ready.
   source: find_consumers({"kind": "configmap", "name": "orders-config", "namespace": "orders"}) — verified
   > deployment/orders-audit ready=1/1 references via env[DATABASE_URL].configMapKeyRef.key=database_url

## Investigation ledger

- The OOMKilled report worker is the cause of the checkout 5xx — ruled out: That pod belongs to a different deployment whose labels (app=orders-report-worker) do not match the Service orders-api selector app=orders-api, so it can never be an upstream for POST /api/orders.
  source: namespace_overview(orders) — verified
  > pod/orders-report-worker-5645b7fccf-zhjzb phase=Running labels={app=orders-report-worker, pod-template-hash=5645b7fccf} node=incident-lab-control-plane worker(ready=False,restarts=3,lastExit=OOMKilled)
- The shared ConfigMap is missing or emptied, which would break all of its consumers — ruled out: Its key database_url is consumed successfully by the sibling deployment orders-audit, which is 1/1 Ready, so the shared config object is intact.
  source: find_consumers({"kind": "configmap", "name": "orders-config", "namespace": "orders"}) — verified
  > deployment/orders-audit ready=1/1 references via env[DATABASE_URL].configMapKeyRef.key=database_url
- Scheduling, image pull, or Service selector mismatch is blocking readiness — ruled out: The pod is scheduled and the image is already present on the node; the only failure reported is the missing environment key.
  source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
  > Normal   Pulled     3s (x6 over 51s)  kubelet            spec.containers{api}: Container image "busybox:1.36" already present on machine and can be accessed by the pod

## Verification recipe

1. `kubectl -n orders get configmap orders-config -o jsonpath='{.data}'` — expect to see: database_url  [PRESENT]
2. `kubectl -n orders describe pod orders-api-6c64874687-8c47z` — expect to see: couldn't find key db_url in ConfigMap orders/orders-config  [PRESENT]
3. `kubectl -n orders get deploy orders-api -o jsonpath='{.spec.template.spec.containers[0].env}'` — expect to see: "key": "db_url"  [PRESENT]
(Each command was re-executed against the captured cluster state; PRESENT/ABSENT is the measured result, not a prediction.)

Verdict: confirmed. Every quote below was re-checked against the tool output it cites, a defect observation names the failing object, an alternative was ruled out with evidence, and every verification command was re-run and found present.

```json
{
  "case_id": "t3-overlapping-config-and-oom",
  "failing_resource": {
    "kind": "Deployment",
    "namespace": "orders",
    "name": "orders-api"
  },
  "mechanism": "In Deployment orders/orders-api, .spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key is set to \"db_url\" while the referenced ConfigMap holds the key \"database_url\". The kubelet cannot resolve that key, rejects container creation with \"couldn't find key db_url in ConfigMap orders/orders-config\", and both replicas sit Pending with the api container stuck in CreateContainerConfigError at restarts=0, never starting.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
