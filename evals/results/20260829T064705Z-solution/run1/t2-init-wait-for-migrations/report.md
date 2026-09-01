## Root cause

The billing-api Deployment's init container "wait-for-db" is told to poll the database at the hostname given in env DB_HOST, which is set to "db-primary". No Service by that name exists in namespace billing; the only database Service is "postgres-primary", which is healthy and has one endpoint address (the running postgres-primary pod, which logs "accepting connections on :5432"). Because the hostname resolves to nothing, every TCP probe from the init container fails, so the pods stay Initialized=False with the "api" container held in PodInitializing and the Deployment reports 0/2 Ready, which is why the release never went live and invoices queue unprocessed.

Remediation: edit Deployment billing/billing-api, field `spec.template.spec.initContainers[wait-for-db].env[DB_HOST].value`: `db-primary` -> `postgres-primary`.

## Evidence chain

1. [symptom] The paged Deployment reports 0/2 Ready with both pods Pending and blocked in the wait-for-db init container.
   source: namespace_overview(billing) — verified
   > deployment/billing-api ready=0/2 podLabels={app=billing-api}
2. [defect] The Deployment pod template sets DB_HOST to db-primary for the init container.
   source: get_object({"kind": "deployments", "name": "billing-api", "namespace": "billing"}) — verified
   > "name": "DB_HOST",
   >                 "value": "db-primary"
3. [link] The init container is repeatedly probing db-primary:5432 without success.
   source: get_logs({"container": "wait-for-db", "namespace": "billing", "pod": "billing-api-ccb44c44c-89dn7", "tail": 50}) — verified
   > wait-for-db: waiting for db-primary:5432 before starting billing-api
4. [link] The only Service in namespace billing is postgres-primary; no Service named db-primary exists.
   source: get_object({"kind": "services", "namespace": "billing"}) — verified
   > "name": "postgres-primary",
   >       "namespace": "billing"
5. [link] The pod remains Initialized=False with the app container waiting on PodInitializing, and the init container env shows DB_HOST db-primary.
   source: describe({"kind": "pod", "name": "billing-api-ccb44c44c-89dn7", "namespace": "billing"}) — verified
   > DB_HOST:  db-primary

## Investigation ledger

- The database tier is down, so the wait loop is legitimately waiting. — ruled out: The postgres pod is running and logs that it accepts connections on port 5432.
  source: get_logs({"namespace": "billing", "pod": "postgres-primary-65b8877bbf-9z42x", "tail": 20}) — verified
  > postgres-primary: accepting connections on :5432
- The database Service has no endpoints (selector mismatch), so nothing is reachable at 5432. — ruled out: Endpoints for postgres-primary contain the running database pod address on port 5432.
  source: get_object({"kind": "endpoints", "name": "postgres-primary", "namespace": "billing"}) — verified
  > "ip": "10.244.0.126"
- The pods cannot be scheduled or their configmap/script volume is missing. — ruled out: The pod was scheduled, the image was already present and the init container started; the referenced ConfigMap billing-api-scripts exists with both script keys.
  source: get_object({"kind": "configmaps", "namespace": "billing"}) — verified
  > "dataKeys": [
  >       "run.sh",
  >       "wait-for-db.sh"
  >     ]

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
  "mechanism": "Deployment billing/billing-api sets .spec.template.spec.initContainers[wait-for-db].env[DB_HOST].value to \"db-primary\" instead of the actual database Service name \"postgres-primary\", so each probe to db-primary:5432 fails on unresolvable DNS and the init container's exit-zero condition is never met, leaving the pod Initialized=False.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
