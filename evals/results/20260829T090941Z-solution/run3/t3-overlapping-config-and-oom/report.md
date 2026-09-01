## Root cause

Deployment orders/orders-api asks for a ConfigMap key that does not exist. Its pod template sets env DATABASE_URL from configMapKeyRef name orders-config, key db_url, but ConfigMap orders/orders-config contains only the key database_url. kubelet therefore refuses to create the container and both pods sit in Waiting with reason CreateContainerConfigError and the event "couldn't find key db_url in ConfigMap orders/orders-config", so the Deployment stays 0/2 Ready. Because no orders-api pod ever becomes ready, Service orders/orders-api (selector app=orders-api) has endpointAddresses=0 and the gateway sees no healthy upstream for POST /api/orders, returning 502/503. The peer Deployment orders/orders-audit reads the same ConfigMap with key database_url and is 1/1 Ready, which shows the correct key spelling.

Remediation: edit Deployment orders/orders-api, field `spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key`: `db_url` -> `database_url`.

## Evidence chain

1. [symptom] Paged deployment is 0/2 Ready.
   source: namespace_overview(orders) — verified
   > deployment/orders-api ready=0/2 podLabels={app=orders-api}
2. [symptom] Service orders-api selects app=orders-api and has zero addresses.
   source: namespace_overview(orders) — verified
   > service/orders-api selector={app=orders-api} endpointAddresses=0
3. [defect] kubelet rejects container creation because the referenced key is absent.
   source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
   > Warning  Failed     3s (x6 over 51s)  kubelet            spec.containers{api}: Error: couldn't find key db_url in ConfigMap orders/orders-config
4. [defect] The Deployment pod template names key db_url in ConfigMap orders-config.
   source: get_object({"kind": "deployments", "name": "orders-api", "namespace": "orders"}) — verified
   > "configMapKeyRef": {
   >                     "key": "db_url",
   >                     "name": "orders-config"
   >                   }
5. [link] ConfigMap orders/orders-config only has the key database_url.
   source: get_object({"kind": "configmaps", "namespace": "orders"}) — verified
   > "dataKeys": [
   >       "database_url"
   >     ],
6. [link] The working peer reads the same ConfigMap with key database_url and is Ready.
   source: find_consumers({"kind": "configmap", "name": "orders-config", "namespace": "orders"}) — verified
   > deployment/orders-audit ready=1/1 references via env[DATABASE_URL].configMapKeyRef.key=database_url
7. [symptom] Both orders-api pods are stuck in CreateContainerConfigError.
   source: namespace_overview(orders) — verified
   > pod/orders-api-6c64874687-t6d22 phase=Pending labels={app=orders-api, pod-template-hash=6c64874687} node=incident-lab-control-plane api(ready=False,restarts=0,waiting=CreateContainerConfigError)

## Investigation ledger

- The OOMKilled report worker is causing the checkout 5xx — ruled out: That pod carries labels app=orders-report-worker, which do not match Service orders/orders-api's selector app=orders-api, so it never served order-submission traffic; the api pods fail before their container is created, which is not a memory condition.
  source: namespace_overview(orders) — verified
  > pod/orders-report-worker-5645b7fccf-zhjzb phase=Running labels={app=orders-report-worker, pod-template-hash=5645b7fccf} node=incident-lab-control-plane worker(ready=False,restarts=3,lastExit=OOMKilled)
- The mounted app ConfigMap for the api container is missing, which would also block container creation — ruled out: orders-api-app exists in the namespace, so the volume reference resolves.
  source: find_consumers({"kind": "configmap", "name": "orders-config", "namespace": "orders"}) — verified
  > configmaps that exist in orders: kube-root-ca.crt, orders-api-app, orders-audit-app, orders-config, orders-report-worker-app
- Service selector mismatch or wrong targetPort keeps endpoints empty — ruled out: Service orders/orders-api selects app=orders-api and targets port 8080, exactly the label and containerPort of the api pod template; endpoints are empty only because no pod is ready.
  source: get_object({"kind": "services", "name": "orders-api", "namespace": "orders"}) — verified
  > "selector": {
  >       "app": "orders-api"
  >     }
- Scheduling or node capacity is blocking the pods — ruled out: The pod is scheduled onto a node and PodScheduled is True; it fails later at container creation.
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
  "mechanism": "Deployment orders/orders-api's pod template sets .spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key to \"db_url\", while ConfigMap orders/orders-config holds only the key \"database_url\". kubelet cannot populate that environment variable and rejects container creation with \"Error: couldn't find key db_url in ConfigMap orders/orders-config\", so both replicas stay Pending in waiting state CreateContainerConfigError with the Deployment at ready=0/2, and kubelet keeps retrying the create cycle.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
