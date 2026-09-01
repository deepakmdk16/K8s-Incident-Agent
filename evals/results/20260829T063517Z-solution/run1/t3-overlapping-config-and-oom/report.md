## Root cause

Deployment orders/orders-api asks for the key "db_url" from ConfigMap orders/orders-config to populate the DATABASE_URL environment variable, but that ConfigMap contains only the key "database_url". Kubelet cannot build the container environment, so both orders-api pods stay Pending in CreateContainerConfigError, never become Ready, and therefore never appear as addresses in Service orders/orders-api (selector app=orders-api), which is why the gateway has no healthy upstream and POST /api/orders returns 502/503. The sibling Deployment orders-audit reads the same ConfigMap with the correct key "database_url" and is 1/1 Ready, confirming the key reference in orders-api is what is wrong.

Remediation: edit Deployment orders/orders-api, field `spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key`: `db_url` -> `database_url`.

## Evidence chain

1. [symptom] Both orders-api pods are not ready and the Service has no endpoints.
   source: namespace_overview(orders) — verified
   > service/orders-api selector={app=orders-api} endpointAddresses=0
2. [symptom] orders-api pods are stuck in CreateContainerConfigError.
   source: namespace_overview(orders) — verified
   > pod/orders-api-6c64874687-8c47z phase=Pending labels={app=orders-api, pod-template-hash=6c64874687} node=incident-lab-control-plane api(ready=False,restarts=0,waiting=CreateContainerConfigError)
3. [link] Kubelet reports the missing key as the reason containers cannot be created.
   source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
   > couldn't find key db_url in ConfigMap orders/orders-config
4. [defect] The deployment pod template requests key db_url from orders-config.
   source: get_object({"kind": "deployments", "name": "orders-api", "namespace": "orders"}) — verified
   > "key": "db_url",
5. [defect] ConfigMap orders-config only contains the key database_url.
   source: find_consumers({"kind": "configmap", "name": "orders-config", "namespace": "orders"}) — verified
   > keys in configmap/orders-config: ['database_url']
6. [link] The healthy peer workload reads the same ConfigMap with the correct key and is Ready.
   source: find_consumers({"kind": "configmap", "name": "orders-config", "namespace": "orders"}) — verified
   > deployment/orders-audit ready=1/1 references via env[DATABASE_URL].configMapKeyRef.key=database_url

## Investigation ledger

- The OOMKilled report worker is the cause of the checkout 5xx. — ruled out: The report worker carries labels app=orders-report-worker, which do not match Service orders-api's selector app=orders-api, so it never served the order-submission path.
  source: namespace_overview(orders) — verified
  > pod/orders-report-worker-5645b7fccf-zhjzb phase=Running labels={app=orders-report-worker, pod-template-hash=5645b7fccf} node=incident-lab-control-plane worker(ready=False,restarts=3,lastExit=OOMKilled)
- Service orders-api has a wrong selector or the pods are mislabeled. — ruled out: The Service selector app=orders-api matches the pod labels exactly; the pods simply never reach Ready because their containers are never created.
  source: namespace_overview(orders) — verified
  > pod/orders-api-6c64874687-t6d22 phase=Pending labels={app=orders-api, pod-template-hash=6c64874687}
- Image pull failure or scheduling/capacity problem blocking orders-api pods. — ruled out: The pods were scheduled successfully and the image was already present on the node, so neither pull nor scheduling blocked them.
  source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
  > Container image "busybox:1.36" already present on machine and can be accessed by the pod

## Verification recipe

1. `kubectl get configmap orders-config -n orders -o jsonpath='{.data}'` — expect to see: database_url  [PRESENT]
2. `kubectl describe pod orders-api-6c64874687-8c47z -n orders` — expect to see: couldn't find key db_url in ConfigMap orders/orders-config  [PRESENT]
3. `kubectl get deployment orders-api -n orders -o jsonpath='{.spec.template.spec.containers[0].env}'` — expect to see: "key": "db_url"  [PRESENT]
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
  "mechanism": "Deployment orders/orders-api sets .spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key to \"db_url\", while ConfigMap orders/orders-config defines only the key \"database_url\". Kubelet fails to construct the container environment with \"couldn't find key db_url in ConfigMap orders/orders-config\", so both replicas sit in CreateContainerConfigError and never pass readiness, leaving Service orders/orders-api with zero endpoint addresses for the checkout path.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
