## Root cause

The billing-api Deployment's init container "wait-for-db" is told to probe the database at the hostname given in DB_HOST, and that value is "db-primary". The only database Service in the billing namespace is named postgres-primary (ClusterIP 10.96.16.233, port 5432), so "db-primary" resolves to nothing and the init container's TCP check never succeeds. Because the init container never exits, neither replica gets past Initialized=False, the "api" container stays in PodInitializing, and the Deployment sits at 0/2 Ready so the release never goes live. The database tier itself is healthy: the postgres-primary Endpoints holds one ready address and the db container logs "accepting connections on :5432". Fixing the env value to postgres-primary lets the init check connect and the pods proceed.

Remediation: edit Deployment billing/billing-api, field `spec.template.spec.initContainers[wait-for-db].env[DB_HOST].value`: `db-primary` -> `postgres-primary`.

## Evidence chain

1. [symptom] Both billing-api pods are Pending with the wait-for-db init container not ready and the app container stuck in PodInitializing.
   source: namespace_overview(billing) — verified
   > pod/billing-api-ccb44c44c-89dn7 phase=Pending labels={app=billing-api, pod-template-hash=ccb44c44c} node=incident-lab-control-plane init:wait-for-db(ready=False,restarts=0) api(ready=False,restarts=0,waiting=PodInitializing)
2. [defect] The Deployment pod template sets DB_HOST to db-primary for the init container.
   source: get_object({"kind": "deployment", "name": "billing-api", "namespace": "billing"}) — verified
   > "name": "DB_HOST",
   >                 "value": "db-primary"
3. [link] The init container is looping on db-primary:5432 and never succeeding.
   source: get_logs({"container": "wait-for-db", "namespace": "billing", "pod": "billing-api-ccb44c44c-89dn7", "tail": 40}) — verified
   > wait-for-db: waiting for db-primary:5432 before starting billing-api
4. [link] The only Service in the billing namespace is postgres-primary, listening on port 5432; no Service named db-primary exists.
   source: get_object({"kind": "services", "namespace": "billing"}) — verified
   > "name": "postgres-primary",
   >       "namespace": "billing",
5. [link] The pod as admitted carries the wrong hostname in its environment while the init container is still Running and Initialized is False.
   source: describe({"kind": "pod", "name": "billing-api-ccb44c44c-89dn7", "namespace": "billing"}) — verified
   > Environment:
   >       DB_HOST:  db-primary

## Investigation ledger

- The database tier is down or not serving, so the wait is legitimate. — ruled out: The postgres pod logs show it accepting connections on 5432.
  source: get_logs({"namespace": "billing", "pod": "postgres-primary-65b8877bbf-9z42x", "tail": 20}) — verified
  > postgres-primary: accepting connections on :5432
- The postgres-primary Service selector matches nothing, so there is no address to connect to. — ruled out: The Endpoints object for postgres-primary holds one ready pod address on port 5432.
  source: get_object({"kind": "endpoints", "name": "postgres-primary", "namespace": "billing"}) — verified
  > "ip": "10.244.0.126",
- The pods are failing to schedule, pull the image, or mount the scripts ConfigMap. — ruled out: The pod scheduled, the image was already present, and the init container started cleanly with no warning events.
  source: describe({"kind": "pod", "name": "billing-api-ccb44c44c-89dn7", "namespace": "billing"}) — verified
  > Normal  Started    21s   kubelet            spec.initContainers{wait-for-db}: Container started
- The billing-api-scripts ConfigMap is missing the wait-for-db.sh key the command references. — ruled out: Both run.sh and wait-for-db.sh keys exist in the ConfigMap.
  source: get_object({"kind": "configmaps", "namespace": "billing"}) — verified
  > "dataKeys": [
  >       "run.sh",
  >       "wait-for-db.sh"
  >     ],

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
  "mechanism": "Deployment billing/billing-api sets .spec.template.spec.initContainers[wait-for-db].env[DB_HOST].value to \"db-primary\", but the only database Service in the namespace is \"postgres-primary\", so the init container's connect attempt to db-primary:5432 fails on every iteration and the init container never completes, leaving both pods with Initialized=False.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
