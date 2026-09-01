## Root cause

Deployment orders/orders-api asks for the environment variable DATABASE_URL from ConfigMap orders-config using key "db_url", but that ConfigMap only contains the key "database_url". Because the reference is not optional, kubelet refuses to create the container and both orders-api pods sit in Waiting with CreateContainerConfigError ("couldn't find key db_url in ConfigMap orders/orders-config"). Since no orders-api pod ever becomes ready, Service orders/orders-api has zero endpoint addresses and the gateway has no healthy upstream for POST /api/orders, producing the 502/503s. The peer Deployment orders-audit reads the same ConfigMap with key "database_url" and is 1/1 ready, which shows the ConfigMap is correct and the orders-api spec is the object that is wrong.

Remediation: edit Deployment orders/orders-api, field `spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key`: `db_url` -> `database_url`.

## Evidence chain

1. [symptom] Both orders-api pods are stuck in CreateContainerConfigError and the Service has no endpoints
   source: namespace_overview(orders) — verified
   > service/orders-api selector={app=orders-api} endpointAddresses=0
2. [symptom] Pod orders-api-6c64874687-8c47z cannot start its container
   source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
   > Reason:       CreateContainerConfigError
3. [defect] kubelet reports the exact missing key
   source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
   > Error: couldn't find key db_url in ConfigMap orders/orders-config
4. [defect] The deployment pod template requests key db_url from orders-config
   source: get_object({"kind": "deployment", "name": "orders-api", "namespace": "orders"}) — verified
   > "configMapKeyRef": {
   >                     "key": "db_url",
   >                     "name": "orders-config"
   >                   }
5. [link] The referenced ConfigMap only has the key database_url
   source: find_consumers({"kind": "configmap", "name": "orders-config", "namespace": "orders"}) — verified
   > keys in configmap/orders-config: ['database_url']
6. [link] The working peer deployment reads the same ConfigMap with the correct key
   source: find_consumers({"kind": "configmap", "name": "orders-config", "namespace": "orders"}) — verified
   > deployment/orders-audit ready=1/1 references via env[DATABASE_URL].configMapKeyRef.key=database_url

## Investigation ledger

- The OOMKilled report worker is the cause of the checkout 5xx — ruled out: That pod belongs to a different deployment with labels app=orders-report-worker, which the orders-api Service selector {app=orders-api} does not match, so it never backed the order-submission endpoint
  source: namespace_overview(orders) — verified
  > pod/orders-report-worker-5645b7fccf-zhjzb phase=Running labels={app=orders-report-worker, pod-template-hash=5645b7fccf} node=incident-lab-control-plane worker(ready=False,restarts=3,lastExit=OOMKilled)
- Service orders-api has a wrong selector / label mismatch keeping endpoints empty — ruled out: The Service selector app=orders-api matches the labels actually carried by the orders-api pods; the pods simply never reach Ready, so no address is published
  source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
  > Labels:           app=orders-api
- The pods fail because the mounted app ConfigMap volume is missing — ruled out: ConfigMap orders-api-app exists in the namespace with its run.sh key, so the volume reference resolves
  source: find_consumers({"kind": "configmap", "name": "orders-config", "namespace": "orders"}) — verified
  > configmaps that exist in orders: kube-root-ca.crt, orders-api-app, orders-audit-app, orders-config, orders-report-worker-app

## Verification recipe

1. `kubectl -n orders get configmap orders-config -o jsonpath='{.data}'` — expect to see: keys in configmap/orders-config: ['database_url']  [PRESENT]
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
  "mechanism": "Deployment orders/orders-api sets .spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key to \"db_url\" while the referenced ConfigMap holds only the key \"database_url\"; the required key does not resolve, so kubelet aborts container creation with CreateContainerConfigError: \"couldn't find key db_url in ConfigMap orders/orders-config\".",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
