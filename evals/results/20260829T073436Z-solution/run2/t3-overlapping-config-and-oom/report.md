## Root cause

Deployment orders/orders-api asks for the environment variable DATABASE_URL from key "db_url" of ConfigMap orders/orders-config, but that ConfigMap contains only the key "database_url". Because the reference is not optional, kubelet refuses to create the container and both orders-api pods sit in Pending with CreateContainerConfigError ("couldn't find key db_url in ConfigMap orders/orders-config"). With no container ever starting, neither pod becomes Ready, so Service orders/orders-api (selector app=orders-api) has zero endpoint addresses and the gateway has no healthy upstream for order submission, producing the 502/503 on POST /api/orders. The peer Deployment orders/orders-audit reads the same ConfigMap with key "database_url" and is 1/1 Ready, which shows the correct key name.

Remediation: edit Deployment orders/orders-api, field `spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key`: `db_url` -> `database_url`.

## Evidence chain

1. [symptom] Both orders-api pods are Pending in CreateContainerConfigError and the Service has no endpoints.
   source: namespace_overview(orders) — verified
   > service/orders-api selector={app=orders-api} endpointAddresses=0
2. [defect] The Deployment pod template requests key db_url from ConfigMap orders-config.
   source: get_object({"kind": "deployment", "name": "orders-api", "namespace": "orders"}) — verified
   > "key": "db_url",
   >                     "name": "orders-config"
3. [link] kubelet fails container creation because the key does not exist.
   source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
   > Error: couldn't find key db_url in ConfigMap orders/orders-config
4. [link] ConfigMap orders-config contains only the key database_url.
   source: get_object({"kind": "configmap", "name": "orders-config", "namespace": "orders"}) — verified
   > "dataKeys": [
   >     "database_url"
   >   ],
5. [link] The working peer Deployment orders-audit reads the same ConfigMap using key database_url.
   source: find_consumers({"kind": "configmap", "name": "orders-config", "namespace": "orders"}) — verified
   > deployment/orders-audit ready=1/1 references via env[DATABASE_URL].configMapKeyRef.key=database_url

## Investigation ledger

- The OOMKilled report worker in the same namespace is causing the checkout 5xx — ruled out: That pod carries labels app=orders-report-worker, which does not match the orders-api Service selector app=orders-api, so it never served order submission traffic; the paged Service's emptiness is explained by the orders-api pods alone.
  source: namespace_overview(orders) — verified
  > pod/orders-report-worker-5645b7fccf-zhjzb phase=Running labels={app=orders-report-worker, pod-template-hash=5645b7fccf} node=incident-lab-control-plane worker(ready=False,restarts=3,lastExit=OOMKilled)
- The Service selector is wrong / label mismatch keeps pods out of the Endpoints — ruled out: The Service selector app=orders-api matches the labels the orders-api pods actually carry; they are excluded only because their containers never start and they are not Ready.
  source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
  > Labels:           app=orders-api
- Missing volume ConfigMap orders-api-app or an image pull failure blocks the pods — ruled out: The ConfigMap orders-api-app exists with key run.sh and the image was pulled successfully; only the env key lookup fails.
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
  "mechanism": "Deployment orders/orders-api sets .spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key to \"db_url\" while ConfigMap orders/orders-config holds only the key \"database_url\"; the mandatory (Optional: false) key lookup fails, so kubelet rejects container creation with CreateContainerConfigError and both replicas stay Pending with 0/2 Ready, the kubelet retrying the pull-and-fail cycle indefinitely.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
