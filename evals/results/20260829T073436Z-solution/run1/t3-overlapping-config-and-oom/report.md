## Root cause

Deployment orders/orders-api injects DATABASE_URL from ConfigMap orders/orders-config using key "db_url", but the only key that ConfigMap contains is "database_url". Because the env reference is not optional, kubelet cannot build the container config and both orders-api pods sit in Pending with CreateContainerConfigError ("couldn't find key db_url in ConfigMap orders/orders-config"), so the Deployment stays at 0/2 Ready. With no ready pod carrying label app=orders-api, Service orders/orders-api has zero endpoint addresses and the gateway has no healthy upstream for order submission, which is the 502/503 on POST /api/orders. The peer Deployment orders/orders-audit reads the same ConfigMap with key "database_url" and is 1/1 Ready, confirming the ConfigMap content is correct and the key spelled in orders-api is the defect.

Remediation: edit Deployment orders/orders-api, field `spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key`: `db_url` -> `database_url`.

## Evidence chain

1. [symptom] Deployment orders-api is 0/2 Ready and both its pods are Pending with CreateContainerConfigError
   source: namespace_overview(orders) — verified
   > deployment/orders-api ready=0/2 podLabels={app=orders-api}
2. [symptom] Service orders-api has no endpoint addresses, matching the gateway's 'no healthy upstream'
   source: namespace_overview(orders) — verified
   > service/orders-api selector={app=orders-api} endpointAddresses=0
3. [link] kubelet reports the missing key by name for the paged pod
   source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
   > Error: couldn't find key db_url in ConfigMap orders/orders-config
4. [defect] The Deployment pod template asks for key db_url
   source: get_object({"kind": "deployments", "name": "orders-api", "namespace": "orders"}) — verified
   > "configMapKeyRef": {
   >                     "key": "db_url",
   >                     "name": "orders-config"
   >                   }
5. [defect] The working peer deployment consumes the key that actually exists
   source: find_consumers({"kind": "configmap", "name": "orders-config", "namespace": "orders"}) — verified
   > deployment/orders-audit ready=1/1 references via env[DATABASE_URL].configMapKeyRef.key=database_url
6. [defect] The only data key present is database_url
   source: get_object({"kind": "configmaps", "namespace": "orders"}) — verified
   > "dataKeys": [
   >       "database_url"
   >     ],

## Investigation ledger

- The OOMKilled report worker is the cause of checkout 5xx — ruled out: That pod carries label app=orders-report-worker, which does not match the order-submission Service selector app=orders-api, so its restarts cannot remove endpoints from the paged Service.
  source: namespace_overview(orders) — verified
  > pod/orders-report-worker-5645b7fccf-zhjzb phase=Running labels={app=orders-report-worker, pod-template-hash=5645b7fccf} node=incident-lab-control-plane worker(ready=False,restarts=3,lastExit=OOMKilled)
- Scheduling, node capacity or image pull is keeping the paged pods from starting — ruled out: The pod was scheduled successfully and its image was already present on the node; the only failure event is the config key lookup.
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
  "mechanism": "Deployment orders/orders-api sets .spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key to \"db_url\" instead of the existing key \"database_url\"; the required (non-optional) key lookup fails, so kubelet rejects the container configuration and every replica the Deployment creates is stuck Pending with CreateContainerConfigError, leaving the Deployment at 0/2 Ready.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
