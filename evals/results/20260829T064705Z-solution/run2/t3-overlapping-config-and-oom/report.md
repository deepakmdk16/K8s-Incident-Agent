## Root cause

Deployment orders/orders-api asks for the key "db_url" from ConfigMap orders/orders-config for its DATABASE_URL environment variable, but that ConfigMap only contains the key "database_url". The kubelet cannot build the container environment, so both orders-api pods sit in Pending with CreateContainerConfigError and never become ready. Because no pod backing Service orders/orders-api is ready, that Service has zero endpoint addresses and the gateway has no healthy upstream for POST /api/orders, producing the 502/503s. The peer workload orders-audit reads the same ConfigMap with key "database_url" and is 1/1 ready, confirming the ConfigMap is fine and the orders-api key reference is what is wrong.

Remediation: edit Deployment orders/orders-api, field `spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key`: `db_url` -> `database_url`.

## Evidence chain

1. [symptom] The paged Deployment is 0/2 ready.
   source: namespace_overview(orders) — verified
   > deployment/orders-api ready=0/2 podLabels={app=orders-api}
2. [symptom] Service orders-api has zero endpoint addresses.
   source: namespace_overview(orders) — verified
   > service/orders-api selector={app=orders-api} endpointAddresses=0
3. [defect] kubelet cannot create the container because the requested ConfigMap key does not exist.
   source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
   > Warning  Failed     3s (x6 over 51s)  kubelet            spec.containers{api}: Error: couldn't find key db_url in ConfigMap orders/orders-config
4. [defect] The deployment pod template requests key db_url.
   source: get_object({"kind": "deployments", "name": "orders-api", "namespace": "orders"}) — verified
   > "configMapKeyRef": {
   >                     "key": "db_url",
   >                     "name": "orders-config"
   >                   }
5. [link] The referenced ConfigMap contains only the key database_url.
   source: get_object({"kind": "configmaps", "name": "orders-config", "namespace": "orders"}) — verified
   > "dataKeys": [
   >     "database_url"
   >   ],
6. [link] The working peer workload reads the same ConfigMap with the correct key name.
   source: find_consumers({"kind": "configmap", "name": "orders-config", "namespace": "orders"}) — verified
   > deployment/orders-audit ready=1/1 references via env[DATABASE_URL].configMapKeyRef.key=database_url

## Investigation ledger

- The OOMKilled report worker is the cause of the checkout 5xx — ruled out: That pod carries labels app=orders-report-worker, which do not match the orders-api Service selector app=orders-api, so it never backs the order-submission service.
  source: namespace_overview(orders) — verified
  > pod/orders-report-worker-5645b7fccf-zhjzb phase=Running labels={app=orders-report-worker, pod-template-hash=5645b7fccf} node=incident-lab-control-plane worker(ready=False,restarts=3,lastExit=OOMKilled)
- Service orders-api has a wrong .spec.selector that matches no pods — ruled out: The selector app=orders-api exactly matches the labels on the orders-api pods; the pods are simply not ready, so they cannot be endpoints.
  source: namespace_overview(orders) — verified
  > pod/orders-api-6c64874687-8c47z phase=Pending labels={app=orders-api, pod-template-hash=6c64874687}
- Scheduling or capacity problem keeping the pods Pending — ruled out: The pod is scheduled onto a node; it is Pending only because container creation config fails.
  source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
  > Normal   Scheduled  51s               default-scheduler  Successfully assigned orders/orders-api-6c64874687-8c47z to incident-lab-control-plane

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
  "mechanism": "In deployment orders/orders-api, .spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key is \"db_url\" while the referenced ConfigMap only holds the key \"database_url\"; the kubelet rejects container creation with \"couldn't find key db_url in ConfigMap orders/orders-config\", leaving both replicas stuck in CreateContainerConfigError and never ready to serve as endpoints of Service orders/orders-api.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
