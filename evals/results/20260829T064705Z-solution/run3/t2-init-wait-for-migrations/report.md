## Root cause

Deployment billing/billing-api gates startup on an init container "wait-for-db" that polls the host given in DB_HOST. That env var is set to "db-primary", but the only database Service in the namespace is "postgres-primary" (ClusterIP 10.96.16.233, port 5432, one ready endpoint backed by the healthy postgres pod). The name db-primary resolves to nothing, so the init container's TCP probe never succeeds, the pod stays Pending with Initialized=False and the app container stuck in PodInitializing, and the Deployment reports 0/2 Ready while invoices queue. The fix is to point the init container at the Service that actually exists.

Remediation: edit Deployment billing/billing-api, field `spec.template.spec.initContainers[wait-for-db].env[DB_HOST].value`: `db-primary` -> `postgres-primary`.

## Evidence chain

1. [symptom] Deployment billing-api reports 0/2 ready and both pods are Pending with the init container not ready and the app container in PodInitializing
   source: namespace_overview(billing) — verified
   > deployment/billing-api ready=0/2 podLabels={app=billing-api}
2. [defect] The init container's DB_HOST is set to db-primary
   source: get_object({"kind": "deployment", "name": "billing-api", "namespace": "billing"}) — verified
   > "name": "DB_HOST",
   >                 "value": "db-primary"
3. [link] The init container is blocking on that hostname and never completes
   source: get_logs({"container": "wait-for-db", "namespace": "billing", "pod": "billing-api-ccb44c44c-89dn7", "tail": 50}) — verified
   > wait-for-db: waiting for db-primary:5432 before starting billing-api
4. [link] The only Service in namespace billing is postgres-primary on port 5432; no Service named db-primary exists
   source: get_object({"kind": "services", "namespace": "billing"}) — verified
   > "name": "postgres-primary",
   >       "namespace": "billing"
5. [link] The pod is stuck at Initialized=False with the app container waiting on PodInitializing, and the env shown on the pod is db-primary
   source: describe({"kind": "pod", "name": "billing-api-ccb44c44c-89dn7", "namespace": "billing"}) — verified
   > DB_HOST:  db-primary

## Investigation ledger

- The database tier is down, so the wait is legitimate — ruled out: The postgres Service has one ready endpoint address pointing at the running postgres pod, so the database is reachable under its real name
  source: get_object({"kind": "endpoints", "name": "postgres-primary", "namespace": "billing"}) — verified
  > "ip": "10.244.0.126",
  >           "nodeName": "incident-lab-control-plane",
  >           "targetRef": {
  >             "kind": "Pod",
  >             "name": "postgres-primary-65b8877bbf-9z42x"
- The mounted script ConfigMap is missing the key the init container runs — ruled out: ConfigMap billing-api-scripts contains both wait-for-db.sh and run.sh, so the referenced script resolves
  source: get_object({"kind": "configmaps", "namespace": "billing"}) — verified
  > "dataKeys": [
  >       "run.sh",
  >       "wait-for-db.sh"
  >     ]
- The pods cannot be scheduled or the image cannot be pulled — ruled out: The pod was scheduled and the init container image pulled and started normally; no scheduling or pull warnings exist
  source: describe({"kind": "pod", "name": "billing-api-ccb44c44c-89dn7", "namespace": "billing"}) — verified
  > Normal  Started    21s   kubelet            spec.initContainers{wait-for-db}: Container started

## Verification recipe

1. `kubectl -n billing get deploy billing-api -o jsonpath='{.spec.template.spec.initContainers[0].env}'` — expect to see: db-primary  [PRESENT]
2. `kubectl -n billing get svc` — expect to see: postgres-primary  [PRESENT]
3. `kubectl -n billing logs billing-api-ccb44c44c-89dn7 -c wait-for-db` — expect to see: waiting for db-primary:5432  [PRESENT]
(Each command was re-executed against the captured cluster state; PRESENT/ABSENT is the measured result, not a prediction.)

Verdict: confirmed. Every quote below was re-checked against the tool output it cites, a defect observation names the failing object, an alternative was ruled out with evidence, and every verification command was re-run and found present.

```json
{
  "case_id": "t2-init-wait-for-migrations",
  "failing_resource": {
    "kind": "Deployment",
    "namespace": "billing",
    "name": "billing-api"
  },
  "mechanism": "In Deployment billing/billing-api, .spec.template.spec.initContainers[wait-for-db].env[DB_HOST].value is \"db-primary\", but the only database Service in namespace billing is \"postgres-primary\"; the init container's script polls db-primary:5432, that name has no Service to resolve to, so every connection attempt fails and the init container never exits 0, leaving each pod with Initialized=False.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
