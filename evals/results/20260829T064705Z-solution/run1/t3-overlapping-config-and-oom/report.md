## Root cause

Deployment orders/orders-api asks for the environment variable DATABASE_URL from ConfigMap orders-config using key "db_url", but that ConfigMap only contains the key "database_url". Because the reference is required (Optional: false), the kubelet cannot build the container config and both orders-api pods sit in Pending with CreateContainerConfigError, never starting or becoming ready. With no ready pods carrying label app=orders-api, Service orders-api has zero endpoint addresses, so the gateway has no healthy upstream and POST /api/orders returns 502/503. The peer workload orders-audit reads the same ConfigMap with key "database_url" and is 1/1 Ready, which shows the correct key name.

Remediation: edit Deployment orders/orders-api, field `spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key`: `db_url` -> `database_url`.

## Evidence chain

1. [symptom] Both orders-api pods are Pending with CreateContainerConfigError and the Service has no endpoints.
   source: namespace_overview(orders) — verified
   > service/orders-api selector={app=orders-api} endpointAddresses=0
2. [link] The kubelet names the exact missing key when building the container.
   source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
   > Error: couldn't find key db_url in ConfigMap orders/orders-config
3. [defect] The deployment pod template requests key db_url from orders-config.
   source: get_object({"kind": "deployment", "name": "orders-api", "namespace": "orders"}) — verified
   > "configMapKeyRef": {
   >                     "key": "db_url",
   >                     "name": "orders-config"
   >                   }
4. [defect] ConfigMap orders-config only has the key database_url.
   source: get_object({"kind": "configmaps", "name": "orders-config", "namespace": "orders"}) — verified
   > "dataKeys": [
   >     "database_url"
   >   ],
5. [link] The working peer orders-audit reads the same ConfigMap with the correct key and is Ready.
   source: find_consumers({"kind": "configmap", "name": "orders-config", "namespace": "orders"}) — verified
   > deployment/orders-audit ready=1/1 references via env[DATABASE_URL].configMapKeyRef.key=database_url

## Investigation ledger

- The OOMKilled report worker is what broke order submission — ruled out: That pod carries label app=orders-report-worker, which does not match Service orders-api's selector app=orders-api, so it never served order submission traffic.
  source: namespace_overview(orders) — verified
  > pod/orders-report-worker-5645b7fccf-zhjzb phase=Running labels={app=orders-report-worker, pod-template-hash=5645b7fccf} node=incident-lab-control-plane worker(ready=False,restarts=3,lastExit=OOMKilled)
- The mounted app ConfigMap orders-api-app is missing, so the command sh /app/run.sh cannot run — ruled out: ConfigMap orders-api-app exists in the namespace with key run.sh, so the volume reference resolves.
  source: get_object({"kind": "configmaps", "namespace": "orders"}) — verified
  > "dataKeys": [
  >       "run.sh"
  >     ],
  >     "kind": "ConfigMap",
  >     "metadata": {
  >       "creationTimestamp": "2026-08-29T01:34:01Z",
  >       "name": "orders-api-app",
- Pods cannot be scheduled (node capacity/taints) — ruled out: Both pods are scheduled onto the node and PodScheduled is True; the failure is at container config creation.
  source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
  > Successfully assigned orders/orders-api-6c64874687-8c47z to incident-lab-control-plane

## Verification recipe

1. `kubectl -n orders get configmap orders-config -o jsonpath='{.data}'` — expect to see: database_url  [PRESENT]
2. `kubectl -n orders get deploy orders-api -o jsonpath='{.spec.template.spec.containers[0].env}'` — expect to see: db_url  [PRESENT]
3. `kubectl -n orders describe pod orders-api-6c64874687-8c47z` — expect to see: couldn't find key db_url in ConfigMap orders/orders-config  [PRESENT]
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
  "mechanism": "In Deployment orders/orders-api, .spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key is \"db_url\", but ConfigMap orders/orders-config contains only the key \"database_url\". The kubelet rejects the container configuration with \"couldn't find key db_url in ConfigMap orders/orders-config\", so both replicas stay in CreateContainerConfigError and no pod with label app=orders-api ever becomes an endpoint of Service orders-api.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
