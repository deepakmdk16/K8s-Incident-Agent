## Root cause

Deployment orders/orders-api asks for the key "db_url" of ConfigMap orders/orders-config for its DATABASE_URL environment variable, but that ConfigMap contains only the key "database_url". Because the configMapKeyRef is not optional, kubelet refuses to create the "api" container and both replicas sit in Pending with CreateContainerConfigError ("couldn't find key db_url in ConfigMap orders/orders-config"), so the Deployment stays 0/2 Ready. With no ready pod carrying label app=orders-api, Service orders/orders-api has zero endpoint addresses and the gateway has no healthy upstream for order submission, which is the 502/503 on POST /api/orders. The working peer Deployment orders/orders-audit reads the same ConfigMap with key "database_url" and is 1/1 Ready, confirming the key name in the orders-api pod template is the wrong one.

Remediation: edit Deployment orders/orders-api, field `spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key`: `db_url` -> `database_url`.

## Evidence chain

1. [symptom] The paged Deployment is 0/2 Ready.
   source: namespace_overview(orders) — verified
   > deployment/orders-api ready=0/2 podLabels={app=orders-api}
2. [symptom] Both orders-api pods are stuck in CreateContainerConfigError.
   source: namespace_overview(orders) — verified
   > pod/orders-api-6c64874687-8c47z phase=Pending labels={app=orders-api, pod-template-hash=6c64874687} node=incident-lab-control-plane api(ready=False,restarts=0,waiting=CreateContainerConfigError)
3. [symptom] Service orders-api has zero endpoint addresses.
   source: namespace_overview(orders) — verified
   > service/orders-api selector={app=orders-api} endpointAddresses=0
4. [link] kubelet reports the missing key by name for the orders-api pod.
   source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
   > Warning  Failed     3s (x6 over 51s)  kubelet            spec.containers{api}: Error: couldn't find key db_url in ConfigMap orders/orders-config
5. [defect] The Deployment pod template requests key db_url from ConfigMap orders-config.
   source: get_object({"kind": "deployments", "name": "orders-api", "namespace": "orders"}) — verified
   > "configMapKeyRef": {
   >                     "key": "db_url",
   >                     "name": "orders-config"
   >                   }
6. [defect] ConfigMap orders-config contains only the key database_url.
   source: get_object({"kind": "configmaps", "namespace": "orders"}) — verified
   > "dataKeys": [
   >       "database_url"
   >     ],
7. [link] The healthy peer workload reads the same ConfigMap with the correct key.
   source: find_consumers({"kind": "configmap", "name": "orders-config", "namespace": "orders"}) — verified
   > deployment/orders-audit ready=1/1 references via env[DATABASE_URL].configMapKeyRef.key=database_url

## Investigation ledger

- The OOMKilled report worker is the cause of the checkout 5xx — ruled out: That pod carries label app=orders-report-worker, which does not match the Service orders-api selector {app=orders-api}, so it can never be an endpoint of the order-submission Service; it is a separate, unrelated failure.
  source: namespace_overview(orders) — verified
  > pod/orders-report-worker-5645b7fccf-zhjzb phase=Running labels={app=orders-report-worker, pod-template-hash=5645b7fccf} node=incident-lab-control-plane worker(ready=False,restarts=3,lastExit=OOMKilled)
- Service selector mismatch or wrong pod labels are why the Service has no endpoints — ruled out: The Service selector {app=orders-api} matches the pod labels exactly; the pods are simply not ready because their container was never created.
  source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
  > Labels:           app=orders-api
- A missing ConfigMap object (for example the mounted app volume source) is blocking container creation — ruled out: Every ConfigMap the pod template names exists in the namespace; only a key inside one of them is absent.
  source: find_consumers({"kind": "configmap", "name": "orders-config", "namespace": "orders"}) — verified
  > configmaps that exist in orders: kube-root-ca.crt, orders-api-app, orders-audit-app, orders-config, orders-report-worker-app
- Image pull failure or node scheduling pressure — ruled out: The pod was scheduled and the image was pulled successfully; the failure occurs at container config time.
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
  "mechanism": "Deployment orders/orders-api sets .spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key to \"db_url\" while ConfigMap orders/orders-config only holds the key \"database_url\"; since the reference is not optional, kubelet rejects container creation with CreateContainerConfigError (\"couldn't find key db_url in ConfigMap orders/orders-config\") and both replicas of the Deployment stay Pending at 0/2 Ready while the kubelet retries the create-container cycle.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
