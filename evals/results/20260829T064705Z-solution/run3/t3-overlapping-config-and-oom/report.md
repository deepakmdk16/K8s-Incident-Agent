## Root cause

Deployment orders/orders-api asks for the key "db_url" from ConfigMap orders/orders-config, but that ConfigMap only contains the key "database_url". The kubelet cannot build the container environment, so both orders-api pods sit in Pending with CreateContainerConfigError and the api container never starts. Because no orders-api pod ever becomes ready, Service orders/orders-api has zero endpoint addresses and the gateway has no healthy upstream for POST /api/orders, producing the 502/503s. The peer workload orders-audit reads the same ConfigMap with key "database_url" and is 1/1 Ready, which shows the ConfigMap is fine and the orders-api key reference is the wrong value. Fix by editing the key in the orders-api pod template to "database_url".

Remediation: edit Deployment orders/orders-api, field `spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key`: `db_url` -> `database_url`.

## Evidence chain

1. [symptom] Both orders-api pods are Pending with CreateContainerConfigError and the Service has no endpoints.
   source: namespace_overview(orders) — verified
   > service/orders-api selector={app=orders-api} endpointAddresses=0
2. [symptom] The api container never starts.
   source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
   > State:          Waiting
   >       Reason:       CreateContainerConfigError
3. [defect] kubelet reports the referenced key does not exist.
   source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
   > Error: couldn't find key db_url in ConfigMap orders/orders-config
4. [defect] The deployment pod template names key db_url.
   source: get_object({"kind": "deployments", "name": "orders-api", "namespace": "orders"}) — verified
   > "configMapKeyRef": {
   >                     "key": "db_url",
   >                     "name": "orders-config"
   >                   }
5. [link] The healthy peer orders-audit reads the same ConfigMap with key database_url.
   source: find_consumers({"kind": "configmap", "name": "orders-config", "namespace": "orders"}) — verified
   > deployment/orders-audit ready=1/1 references via env[DATABASE_URL].configMapKeyRef.key=database_url
6. [link] The ConfigMap's only data key is database_url.
   source: get_object({"kind": "configmaps", "namespace": "orders"}) — verified
   > "dataKeys": [
   >       "database_url"
   >     ],

## Investigation ledger

- The OOMKilled report worker is the cause of the order-submission 5xx — ruled out: The Service that fronts order submission selects app=orders-api, so the report worker's pods were never eligible endpoints for it; its OOM restarts cannot empty that Service.
  source: namespace_overview(orders) — verified
  > service/orders-api selector={app=orders-api} endpointAddresses=0
- Scheduling, image pull, or node capacity is blocking orders-api pods — ruled out: The pod was scheduled onto a node and the image was already present locally; only the environment key lookup failed.
  source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
  > Container image "busybox:1.36" already present on machine and can be accessed by the pod

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
  "mechanism": "Deployment orders/orders-api sets .spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key to \"db_url\" while the referenced ConfigMap holds only the key \"database_url\"; the kubelet cannot populate the environment variable and rejects container creation with \"couldn't find key db_url\", leaving the api container in CreateContainerConfigError.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
