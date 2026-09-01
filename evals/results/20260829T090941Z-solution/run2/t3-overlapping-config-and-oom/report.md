## Root cause

Deployment orders/orders-api asks for the key db_url from ConfigMap orders/orders-config, but that ConfigMap holds only the key database_url. Kubelet therefore refuses to create the container for every orders-api pod with "Error: couldn't find key db_url in ConfigMap orders/orders-config", leaving both pods stuck in CreateContainerConfigError and never Ready. Because no pod matching selector app=orders-api is ready, Service orders/orders-api has endpointAddresses=0 and the gateway sees no healthy upstream, producing the 502/503 on POST /api/orders. The peer Deployment orders/orders-audit reads the same ConfigMap with key database_url and is 1/1 Ready, which shows the correct spelling of the key.

Remediation: edit Deployment orders/orders-api, field `spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key`: `db_url` -> `database_url`.

## Evidence chain

1. [symptom] Both orders-api pods are Pending with CreateContainerConfigError and the Service has no endpoints.
   source: namespace_overview(orders) — verified
   > service/orders-api selector={app=orders-api} endpointAddresses=0
2. [defect] Kubelet cannot resolve the env key the pod spec asks for.
   source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
   > Error: couldn't find key db_url in ConfigMap orders/orders-config
3. [defect] The Deployment pod template is where the wrong key is written.
   source: get_object({"kind": "deployment", "name": "orders-api", "namespace": "orders"}) — verified
   > "configMapKeyRef": {
   >                     "key": "db_url",
   >                     "name": "orders-config"
   >                   }
4. [link] ConfigMap orders/orders-config only holds the key database_url.
   source: get_object({"kind": "configmaps", "namespace": "orders"}) — verified
   > "dataKeys": [
   >       "database_url"
   >     ],
5. [link] The healthy peer workload reads the same ConfigMap with the correct key.
   source: find_consumers({"kind": "configmap", "name": "orders-config", "namespace": "orders"}) — verified
   > deployment/orders-audit ready=1/1 references via env[DATABASE_URL].configMapKeyRef.key=database_url

## Investigation ledger

- The OOMKilled report worker is the cause of the order-submission outage — ruled out: That pod carries labels app=orders-report-worker, so it is never selected by Service orders/orders-api (selector app=orders-api), and its failure is a memory limit issue unrelated to the paged endpoint.
  source: describe({"kind": "pod", "name": "orders-report-worker-5645b7fccf-zhjzb", "namespace": "orders"}) — verified
  > Labels:           app=orders-report-worker
- The orders-api pods failed to schedule or to pull the image — ruled out: The pod scheduled successfully and the image was already present on the node; only container config resolution failed.
  source: describe({"kind": "pod", "name": "orders-api-6c64874687-8c47z", "namespace": "orders"}) — verified
  > Container image "busybox:1.36" already present on machine and can be accessed by the pod
- The Service selector is wrong / mismatched with pod labels — ruled out: The Service selector app=orders-api matches the pod labels exactly; the endpoints are empty only because no matching pod is ready.
  source: namespace_overview(orders) — verified
  > pod/orders-api-6c64874687-8c47z phase=Pending labels={app=orders-api, pod-template-hash=6c64874687}

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
  "mechanism": "Deployment orders/orders-api sets .spec.template.spec.containers[api].env[DATABASE_URL].valueFrom.configMapKeyRef.key to \"db_url\", but ConfigMap orders/orders-config contains only the key \"database_url\". Kubelet rejects container creation for each orders-api pod with \"Error: couldn't find key db_url in ConfigMap orders/orders-config\", so the pods sit in Waiting with Reason CreateContainerConfigError, are retried on every sync, and never become Ready.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
