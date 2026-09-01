## Root cause

Deployment orders/orders-api asks for the key db_url from ConfigMap orders/orders-config, but that ConfigMap only contains the key database_url. kubelet therefore cannot build the container environment and both orders-api pods stay in Pending with waiting reason CreateContainerConfigError and the event "couldn't find key db_url in ConfigMap orders/orders-config". Because no orders-api pod ever becomes ready, Service orders/orders-api has 0 endpoint addresses, which is why the gateway reports no healthy upstream and POST /api/orders returns 502/503. The peer workload Deployment orders/orders-audit reads the same ConfigMap with key database_url and is 1/1 Ready, confirming the key name in the orders-api template is the wrong one.

Remediation: edit Deployment orders/orders-api, field `spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key`: `db_url` -> `database_url`.

## Evidence chain

1. [symptom] Both orders-api pods are stuck in CreateContainerConfigError and the Service has no endpoints
   source: namespace_overview(orders) — verified
   > service/orders-api selector={app=orders-api} endpointAddresses=0
2. [symptom] Deployment orders-api is 0/2 Ready
   source: namespace_overview(orders) — verified
   > deployment/orders-api ready=0/2 podLabels={app=orders-api}
3. [defect] kubelet reports the missing key by name
   source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
   > Warning  Failed     3s (x6 over 51s)  kubelet            spec.containers{api}: Error: couldn't find key db_url in ConfigMap orders/orders-config
4. [link] The deployment template requests key db_url from orders-config
   source: get_object({"kind": "deployment", "name": "orders-api", "namespace": "orders"}) — verified
   > "configMapKeyRef": {
   >                     "key": "db_url",
   >                     "name": "orders-config"
   >                   }
5. [defect] ConfigMap orders-config contains only database_url
   source: get_object({"kind": "configmaps", "namespace": "orders"}) — verified
   > "dataKeys": [
   >       "database_url"
   >     ],
6. [link] The working peer reads the same ConfigMap with the correct key
   source: find_consumers({"kind": "configmap", "name": "orders-config", "namespace": "orders"}) — verified
   > deployment/orders-audit ready=1/1 references via env[DATABASE_URL].configMapKeyRef.key=database_url

## Investigation ledger

- The OOMKilled report worker is the cause of the checkout 5xx — ruled out: That pod carries labels app=orders-report-worker, which do not match the orders-api Service selector app=orders-api, so it never served order submission traffic.
  source: namespace_overview(orders) — verified
  > pod/orders-report-worker-5645b7fccf-zhjzb phase=Running labels={app=orders-report-worker, pod-template-hash=5645b7fccf} node=incident-lab-control-plane worker(ready=False,restarts=3,lastExit=OOMKilled)
- Service orders/orders-api has a wrong selector or label mismatch — ruled out: The Service selector app=orders-api exactly matches the orders-api pod labels; the pods simply never become ready because their containers are never created.
  source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
  > Labels:           app=orders-api
- Missing ConfigMap or bad volume reference (orders-api-app) blocking startup — ruled out: ConfigMap orders/orders-api-app exists with its run.sh key, so the mounted volume resolves; only the env key lookup fails.
  source: get_object({"kind": "configmaps", "namespace": "orders"}) — verified
  > "name": "orders-api-app",

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
  "mechanism": "Deployment orders/orders-api sets .spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key to \"db_url\", but ConfigMap orders/orders-config holds only the key \"database_url\". kubelet fails to populate the environment and rejects container creation with \"Error: couldn't find key db_url in ConfigMap orders/orders-config\", so every replica sits in Pending with waiting=CreateContainerConfigError and the kubelet retries the pull-and-create cycle indefinitely (x6 over 51s), leaving the Deployment at 0/2 Ready.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
